"""Reusable application workflow for the local interface.

Reading, preparing and previewing are offline. Hunter and SMTP are contacted only
by their respective explicit methods; no command-line globals are changed.
"""

from __future__ import annotations

import csv
from datetime import datetime
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import threading
from urllib.parse import urlsplit

import applications
import script as hunter
import send_emails


class IncompleteApplicationError(ValueError):
    """An existing dossier needs explicit repair rather than silent replacement."""

    code = "application_incomplete"

    def __init__(self, application_id: str, *, can_regenerate=False):
        super().__init__(
            "Cette candidature est incomplète ou illisible. Utilisez « Régénérer les documents » "
            "pour la réparer après confirmation."
        )
        self.application_id = application_id
        self.can_regenerate = bool(can_regenerate)


class WorkflowService:
    def __init__(self, base_dir: Path = applications.BASE_DIR):
        self.base_dir = Path(base_dir).resolve()
        self.profile_path = self._local_path("candidate_profile.json")
        self.applications_dir = self._local_path("candidatures")
        self._lock = threading.RLock()
        from contact_sources import ContactDiscovery
        self.contact_discovery = ContactDiscovery(self.base_dir)

    def _local_path(self, name: str) -> Path:
        path = (self.base_dir / name).resolve()
        if not path.is_relative_to(self.base_dir):
            raise ValueError("Ce chemin est situé hors du projet.")
        return path

    @staticmethod
    def _header(value, label: str, *, optional=False, limit=500) -> str:
        if optional and value in (None, ""):
            return ""
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"{label} invalide : un texte sur une ligne est requis.")
        if len(value) > limit:
            raise ValueError(f"{label} trop long ({limit} caractères maximum).")
        return send_emails.validate_header(value, label)

    @classmethod
    def _email(cls, value, *, optional=False) -> str:
        if optional and value in (None, ""):
            return ""
        return send_emails.validate_email(cls._header(value, "Email"), "Email")

    @staticmethod
    def _text(value, label: str, limit=60000) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise ValueError(f"{label} vide, trop long ou invalide.")
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def _folder(self, application_id: str, *, must_exist=True) -> Path:
        if not isinstance(application_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", application_id):
            raise ValueError("Identifiant de candidature invalide.")
        root = self._local_path("candidatures")
        candidate = root / application_id
        if candidate.is_symlink():
            raise ValueError("Un dossier de candidature ne peut pas être un lien symbolique.")
        folder = candidate.resolve()
        if not folder.is_relative_to(root) or folder == root:
            raise ValueError("Dossier de candidature situé hors du projet.")
        if must_exist and not folder.is_dir():
            raise ValueError("Cette candidature n'existe pas.")
        return folder

    @staticmethod
    def _file(folder: Path, name, label: str) -> Path:
        return send_emails.application_file(folder, name, label)

    def _profile(self) -> dict:
        # Re-evaluate confinement in case a file was replaced by a symlink.
        path = self._local_path("candidate_profile.json")
        profile = applications.load_profile(path)
        self._email(profile["identity"]["email"])
        return profile

    def _load(self, application_id: str, *, require_fresh=False) -> dict:
        folder = self._folder(application_id)
        manifest_path = self._file(folder, "manifest.json", "manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ValueError("Manifest de candidature invalide.")
        for key in ("offer_id", "company", "title"):
            self._header(manifest.get(key), f"Manifest : {key}")
        if manifest["offer_id"] != application_id:
            raise ValueError("L'identifiant du dossier et du manifest ne correspondent pas.")
        profile = self._profile()
        warnings = []
        if self._email(manifest.get("profile_email")).casefold() != profile["identity"]["email"].casefold():
            message = "L’adresse du profil a changé ; régénérez la candidature avant l’envoi."
            if require_fresh:
                raise ValueError(message)
            warnings.append(message)
        offer_path = self._file(folder, "offre.json", "offre")
        offer = applications.validate_offer(json.loads(offer_path.read_text(encoding="utf-8-sig")))
        if offer["id"] != application_id:
            raise ValueError("L'identifiant de l'offre et du dossier ne correspondent pas.")
        for key, source, message in (
            ("source_profile_sha256", self._local_path("candidate_profile.json"), "Le profil a changé depuis la génération du dossier ; régénérez la candidature avant l'envoi."),
            ("source_offer_sha256", offer_path, "L'offre a changé depuis la génération du dossier ; régénérez la candidature avant l'envoi."),
        ):
            expected = manifest.get(key)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                raise ValueError("Empreinte de candidature absente ou invalide ; régénérez le dossier.")
            if expected.casefold() != applications.file_hash(source):
                if require_fresh:
                    raise ValueError(message)
                warnings.append(message)
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise ValueError("Manifest : liste des fichiers absente.")
        paths = {kind: self._file(folder, files.get(kind), kind) for kind in ("cv", "letter", "email")}
        for kind in ("cv", "letter"):
            send_emails.read_pdf(paths[kind])
        subject, body = send_emails.load_template(paths["email"])
        notes = manifest.get("review_notes", [])
        if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
            raise ValueError("Manifest : les points à vérifier doivent être une liste de textes.")
        recipient = self._recipient(manifest.get("recipient") or {}, manifest["company"], require_email=False)
        return {"folder": folder, "manifest": manifest, "profile": profile, "offer": offer,
                "paths": paths, "subject": subject, "body": body,
                "recipient": recipient, "notes": notes, "warnings": warnings}

    def _recipient(self, recipient: dict, company: str, *, require_email=True) -> dict:
        if not isinstance(recipient, dict):
            raise ValueError("Le destinataire doit être un objet.")
        supplied_company = recipient.get("entreprise") or company
        supplied_company = self._header(supplied_company, "Entreprise du contact", limit=200)
        if supplied_company.casefold() != company.casefold():
            raise ValueError("Le contact sélectionné doit appartenir à l'entreprise de la candidature.")
        return {"entreprise": company,
                "email": self._email(recipient.get("email", ""), optional=not require_email),
                "prenom": self._header(recipient.get("prenom", ""), "Prénom du contact", optional=True),
                "nom": self._header(recipient.get("nom", ""), "Nom du contact", optional=True)}

    @staticmethod
    def _urls(application_id: str) -> dict:
        return {kind: f"/api/applications/{application_id}/files/{kind}" for kind in ("cv", "letter")}

    def _public(self, application_id: str, loaded: dict, *, detail=True) -> dict:
        manifest = loaded["manifest"]
        result = {"id": application_id, "company": manifest["company"], "title": manifest["title"],
                  "created_at": manifest.get("created_at", ""), "notes": loaded["notes"],
                  "files": self._urls(application_id), "recipient": loaded["recipient"],
                  "stale": bool(loaded["warnings"]), "warnings": loaded["warnings"],
                  "spontaneous": bool(manifest.get("spontaneous"))}
        if detail:
            letter_path = loaded["folder"] / "lettre_motivation.txt"
            letter = self._file(loaded["folder"], letter_path.name, "texte de la lettre").read_text(encoding="utf-8-sig") if letter_path.exists() else ""
            result.update({"offer": loaded["offer"], "subject": loaded["subject"],
                           "body": loaded["body"], "letter_text": letter})
        return result

    def list_applications(self) -> list[dict]:
        with self._lock:
            root = self._local_path("candidatures")
            if not root.exists():
                return []
            result = []
            for folder in root.iterdir():
                if not folder.is_dir():
                    continue
                try:
                    loaded = self._load(folder.name)
                    result.append(self._public(folder.name, loaded, detail=False))
                except (OSError, ValueError, KeyError, TypeError):
                    # An unfinished or unrelated folder is not an application.
                    continue
            return sorted(result, key=lambda app: app["created_at"], reverse=True)

    def load_application(self, application_id: str) -> dict:
        with self._lock:
            return self._public(application_id, self._load_for_review(application_id))

    def _load_for_review(self, application_id: str) -> dict:
        self._folder(application_id)
        try:
            return self._load(application_id)
        except (OSError, ValueError, KeyError, TypeError):
            try:
                self.recover_offer(application_id)
                can_regenerate = True
            except (OSError, ValueError, KeyError, TypeError):
                can_regenerate = False
            raise IncompleteApplicationError(application_id, can_regenerate=can_regenerate) from None

    def recover_offer(self, application_id: str) -> dict:
        """Read the saved offer even when the dossier's manifest/PDFs are incomplete."""
        with self._lock:
            folder = self._folder(application_id)
            path = self._file(folder, "offre.json", "offre")
            offer = applications.validate_offer(json.loads(path.read_text(encoding="utf-8-sig")))
            if offer["id"] != application_id:
                raise ValueError("L'identifiant de l'offre et du dossier ne correspondent pas.")
            self._recipient(offer.get("contact", {}), offer["entreprise"], require_email=False)
            return offer

    def prepare(self, offer: dict, overwrite=False) -> dict:
        if type(overwrite) is not bool:
            raise ValueError("overwrite doit être un booléen.")
        validated = applications.validate_offer(offer)
        # Check email and single-line recipient values before writing documents.
        self._recipient(validated.get("contact", {}), validated["entreprise"], require_email=False)
        with self._lock:
            folder = self._folder(validated["id"], must_exist=False)
            if folder.exists():
                for filename in ("manifest.json", "offre.json", "analyse.json", "lettre_motivation.txt", "email.txt", "CV_CHRISTOPHER_KOUTOUA.pdf", "Lettre_motivation.pdf"):
                    candidate = folder / filename
                    if candidate.exists() or candidate.is_symlink():
                        self._file(folder, filename, filename)
                if not overwrite:
                    existing = self.load_application(validated["id"])
                    return {**existing, "reused": True}
            applications.prepare_application(self._profile(), self._local_path("candidate_profile.json"), validated,
                                             self._local_path("candidatures"), overwrite=overwrite)
            return {**self.load_application(validated["id"]), "reused": False}

    def preview(self, application_id: str, recipient: dict | None = None,
                subject: str | None = None, body: str | None = None) -> dict:
        with self._lock:
            loaded = self._load_for_review(application_id)
            contact = self._recipient(recipient if recipient is not None else loaded["recipient"],
                                      loaded["manifest"]["company"], require_email=False)
            context = {**send_emails.profile_context(loaded["profile"]), **contact}
            subject_template = self._header(subject if subject is not None else loaded["subject"], "Objet du message")
            final_subject = self._header(send_emails.render(subject_template, context), "Objet du message")
            final_body = send_emails.render(self._text(body if body is not None else loaded["body"], "Corps du message"), context).replace("Bonjour ,", "Bonjour,")
            urls = self._urls(application_id)
            return {"subject": final_subject, "body": final_body, "recipient": contact,
                    "attachments": [{"name": loaded["paths"][kind].name, "url": urls[kind]} for kind in ("cv", "letter")],
                    "notes": loaded["notes"], "warnings": loaded["warnings"], "stale": bool(loaded["warnings"]),
                    "letter_text": self._public(application_id, loaded)["letter_text"]}

    def get_document(self, application_id: str, kind: str) -> Path:
        if kind not in ("cv", "letter"):
            raise ValueError("Document inconnu : cv ou letter attendu.")
        with self._lock:
            return self._load(application_id)["paths"][kind]

    def update_texts(self, application_id: str, subject: str | None = None, body: str | None = None,
                     letter_text: str | None = None) -> dict:
        from pdf_documents import write_letter

        if subject is not None:
            subject = self._header(subject, "Objet du message")
        if body is not None:
            body = self._text(body, "Corps du message")
        if letter_text is not None:
            letter_text = self._text(letter_text, "Lettre de motivation")
        with self._lock:
            loaded = self._load(application_id)
            subject = loaded["subject"] if subject is None else subject
            body = loaded["body"] if body is None else body
            # Ensure templates can be rendered before persisting the edits.
            self.preview(application_id, subject=subject, body=body)
            if letter_text is not None:
                txt = loaded["folder"] / "lettre_motivation.txt"
                if txt.exists() or txt.is_symlink():
                    self._file(loaded["folder"], txt.name, "texte de la lettre")
                temp_letter = loaded["folder"] / ".letter.interface.tmp.pdf"
                if temp_letter.exists() or temp_letter.is_symlink():
                    self._file(loaded["folder"], temp_letter.name, "lettre temporaire")
                write_letter(temp_letter, loaded["profile"], letter_text)
                temp_letter.replace(loaded["paths"]["letter"])
                txt.write_text(letter_text, encoding="utf-8")
            loaded["paths"]["email"].write_text(f"SUBJECT: {subject}\n\n{body}\n", encoding="utf-8")
            return self.load_application(application_id)

    def send(self, application_id: str, recipient: dict, subject: str | None = None,
             body: str | None = None) -> dict:
        with self._lock:
            loaded = self._load(application_id, require_fresh=True)
            contact = self._recipient(recipient, loaded["manifest"]["company"])
            preview = self.preview(application_id, contact, subject, body)
            send_emails.load_dotenv(self.base_dir / ".env")
            sender = os.environ.get("EMAIL_ADDRESS", "").strip()
            password = os.environ.get("EMAIL_PASSWORD", "")
            if not sender or not password:
                raise ValueError("EMAIL_ADDRESS et EMAIL_PASSWORD doivent être définis dans .env ou l'environnement.")
            sender = self._email(sender)
            server_name = self._header(os.environ.get("SMTP_SERVER", "smtp.gmail.com"), "Serveur SMTP")
            if any(c.isspace() for c in server_name):
                raise ValueError("Serveur SMTP invalide.")
            try:
                port = int(os.environ.get("SMTP_PORT", "587"))
            except ValueError:
                raise ValueError("SMTP_PORT doit être un nombre entier.") from None
            if not 1 <= port <= 65535:
                raise ValueError("SMTP_PORT doit être compris entre 1 et 65535.")
            message, final_subject, _ = send_emails.build_message(contact, preview["subject"], preview["body"], sender,
                                                                 send_emails.profile_context(loaded["profile"]),
                                                                 (loaded["paths"]["cv"], loaded["paths"]["letter"]))
            # Materialize the complete message before opening a connection.
            payload = message.as_string()
            server = None
            try:
                server = smtplib.SMTP(server_name, port, timeout=send_emails.SMTP_TIMEOUT)
                server.starttls(context=ssl.create_default_context())
                server.login(sender, password)
                refused = server.sendmail(sender, contact["email"], payload)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
            except (OSError, smtplib.SMTPException):
                raise RuntimeError("Connexion SMTP ou envoi impossible. Vérifiez votre configuration avant de réessayer.") from None
            finally:
                if server is not None:
                    try:
                        server.quit()
                    except (OSError, smtplib.SMTPException):
                        try:
                            server.close()
                        except OSError:
                            pass
            return {"id": application_id, "message": f"Email envoyé à {contact['email']}.",
                    "recipient": contact, "subject": final_subject,
                    "sent_at": datetime.now().astimezone().isoformat(timespec="seconds")}

    def _contact(self, row: dict) -> dict:
        if not isinstance(row, dict):
            raise ValueError("Le contact doit être un objet.")
        result = {"entreprise": self._header(row.get("entreprise"), "Entreprise", limit=200),
                  "email": self._email(row.get("email"))}
        for key in hunter.CSV_FIELDS:
            if key in result:
                continue
            value = row.get(key, "")
            if key == "confiance" and isinstance(value, (int, float)):
                value = str(value)
            result[key] = self._header(value, key, optional=True)
        for key in ('department', 'verification_status', 'verification_date', 'source', 'resolved_domain'):
            result[key] = self._header(row.get(key, ''), key, optional=True)
        result['linkedin'] = self._contact_url(result['linkedin'])
        priority = row.get('priority', 2)
        if type(priority) is not int or priority not in (0, 1, 2):
            raise ValueError('Priorité du contact invalide.')
        result['priority'] = priority
        reasons = row.get('priority_reasons', [])
        if not isinstance(reasons, list) or len(reasons) > 20:
            raise ValueError('Les raisons du classement doivent être une liste.')
        result['priority_reasons'] = [self._header(reason, 'Raison du classement') for reason in reasons]
        sources = row.get('public_sources', [])
        if not isinstance(sources, list) or len(sources) > 30:
            raise ValueError('Les sources du contact doivent être une liste.')
        if row.get('source_url'):
            sources = [*sources, {'url': row['source_url']}]
        result['public_sources'] = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError('Source du contact invalide.')
            public = {'url': self._contact_url(source.get('url', source.get('uri', '')), required=True)}
            for field in ('last_seen_on', 'extracted_on'):
                public[field] = self._header(source.get(field, ''), field, optional=True)
            if source.get('still_on_page') is False:
                continue
            if isinstance(source.get('still_on_page'), bool):
                public['still_on_page'] = source['still_on_page']
            if public['url'] not in {entry['url'] for entry in result['public_sources']}:
                result['public_sources'].append(public)
        return result

    @staticmethod
    def _contact_key(row):
        return row['entreprise'].casefold() + '|' + row['email'].casefold()

    @classmethod
    def _contact_url(cls, value, *, required=False):
        value = cls._header(value, 'Lien professionnel', optional=not required, limit=2000)
        if not value:
            return ''
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            parsed.port
        except ValueError:
            raise ValueError('Le lien professionnel doit être une adresse HTTP ou HTTPS publique.') from None
        return value

    def _contact_metadata(self):
        path = self._local_path('data/contact_metadata.json')
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('contacts'), dict):
                return {}
            return data['contacts']
        except (OSError, ValueError):
            return {}

    def _save_contact_metadata(self, rows):
        metadata = self._contact_metadata()
        for row in rows:
            metadata[self._contact_key(row)] = row
        path = self._local_path('data/contact_metadata.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._local_path('data/.contact_metadata.tmp.json')
        try:
            temporary.write_bytes(applications.json_bytes({'schema_version': 1, 'contacts': metadata}))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def list_contacts(self) -> list[dict]:
        with self._lock:
            result = {}
            metadata = self._contact_metadata()
            # Manual changes take precedence over contacts from earlier exports.
            paths = sorted(self.base_dir.glob("contacts*.csv"), key=lambda path: (path.name == "contacts.interface.csv", path.name))
            for path in paths:
                path = self._local_path(path.name)
                if not path.is_file():
                    continue
                for raw in send_emails.load_contacts(path):
                    row = self._stored_contact(raw, metadata)
                    result[self._contact_key(row)] = row
            return list(result.values())

    def _stored_contact(self, raw: dict, metadata: dict) -> dict:
        row = self._contact(raw)
        saved = metadata.get(self._contact_key(row), {})
        if not isinstance(saved, dict):
            saved = {}
        extras = {key: value for key, value in saved.items() if key not in hunter.CSV_FIELDS}
        # Old CSVs have no reliable provenance. Preserve them, and detect edits
        # made directly to the CSV since its last recorded metadata snapshot.
        if not saved.get('source') or any(row[key] != saved.get(key, '') for key in hunter.CSV_FIELDS):
            extras['source'] = 'manual'
        return self._contact({**row, **extras})

    def _save_contacts(self, rows: list[dict]) -> None:
        path = self._local_path("contacts.interface.csv")
        temp_path = self._local_path(".contacts.interface.tmp.csv")
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=hunter.CSV_FIELDS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(path)

    def _merge_contacts(self, rows: list[dict]) -> None:
        path = self._local_path("contacts.interface.csv")
        metadata = self._contact_metadata()
        current = [self._stored_contact(row, metadata)
                   for row in send_emails.load_contacts(path)] if path.exists() else []
        merged = {(row["email"].casefold(), row["entreprise"].casefold()): row for row in current}
        for row in rows:
            merged[(row["email"].casefold(), row["entreprise"].casefold())] = row
        self._save_contacts(list(merged.values()))
        self._save_contact_metadata(list(merged.values()))

    def upsert_contact(self, row: dict) -> dict:
        with self._lock:
            key_row = {'entreprise': self._header(row.get('entreprise'), 'Entreprise', limit=200),
                       'email': self._email(row.get('email'))} if isinstance(row, dict) else {}
            prior = next((contact for contact in self.list_contacts()
                          if key_row and self._contact_key(contact) == self._contact_key(key_row)), {})
            contact = self._contact({**prior, **row, 'source': 'manual'}) if isinstance(row, dict) else self._contact(row)
            self._merge_contacts([contact])
        return contact

    def search_contacts_detail(self, company: str, department='hr', *, domain=None, force=False) -> dict:
        company = self._header(company, 'Entreprise', limit=200)
        if department not in hunter.DEPARTMENTS:
            raise ValueError('Département Hunter inconnu.')
        if type(force) is not bool:
            raise ValueError('Le choix d’actualisation doit être un booléen.')
        hunter.load_dotenv(self.base_dir / '.env')
        result = self.contact_discovery.search(company, department, domain=domain, force=force,
                                               api_key=os.environ.get('HUNTER_API_KEY'))
        contacts = [self._contact(row) for row in result['contacts']]
        with self._lock:
            existing = {self._contact_key(row): row for row in self.list_contacts()}
            # Les noms, postes et liens corrigés à la main gardent la priorité.
            contacts = [{**row, **{key: existing[self._contact_key(row)][key] for key in hunter.CSV_FIELDS},
                         'source': 'manual'}
                        if existing.get(self._contact_key(row), {}).get('source') == 'manual' else row
                        for row in contacts]
            if contacts:
                self._merge_contacts(contacts)
        return {**result, 'contacts': contacts}

    def search_contacts(self, company: str, department="hr") -> list[dict]:
        company = self._header(company, "Entreprise", limit=200)
        if department not in hunter.DEPARTMENTS:
            raise ValueError("Département Hunter inconnu.")
        hunter.load_dotenv(self.base_dir / ".env")
        results = hunter.search_company(company, department, os.environ.get("HUNTER_API_KEY"))
        contacts = [self._contact({"entreprise": company, "prenom": raw.get("first_name") or "",
                                   "nom": raw.get("last_name") or "", "poste": raw.get("position") or "",
                                   "email": raw.get("value") or "", "confiance": raw.get("confidence") if raw.get("confidence") is not None else "",
                                   "linkedin": raw.get("linkedin") or "", 'source': 'hunter'}) for raw in results]
        with self._lock:
            self._merge_contacts(contacts)
        return contacts
