#!/usr/bin/env python3
"""Préparer ou envoyer des candidatures avec leurs documents.

L'aperçu est le comportement par défaut : aucune connexion SMTP n'est ouverte.
L'envoi nécessite explicitement --send. Les commandes existantes restent valides.

    python send_emails.py
    python send_emails.py --contacts contacts.hr.csv --only recrutement@example.fr
    python send_emails.py --application candidatures/mon-offre
    python send_emails.py --application candidatures/mon-offre --send

Le profil candidat fournit la signature. EMAIL_ADDRESS et EMAIL_PASSWORD servent
à la connexion SMTP ; SMTP_SERVER et SMTP_PORT configurent le serveur.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONTACTS_FILE = BASE_DIR / "contacts.csv"
TEMPLATE_FILE = BASE_DIR / "email_template.txt"
CV_FILE = BASE_DIR / "CHRISTOPHER_KOUTOUA.pdf"
PROFILE_FILE = BASE_DIR / "candidate_profile.json"
DELAY_BETWEEN_EMAILS = 5
SMTP_TIMEOUT = 30
EMAIL_PATTERN = re.compile(r'[^@\s<>(),;:"]+@[^@\s<>(),;:"]+\.[^@\s<>(),;:"]+')
PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")
MONTH_NAMES = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
               "septembre", "octobre", "novembre", "décembre")


def load_dotenv(path=None) -> None:
    path = Path(path) if path is not None else BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
            os.environ.setdefault(key, value)


def validate_header(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\r" in value or "\n" in value:
        raise ValueError(f"{label} vide ou contenant un retour à la ligne")
    return value.strip()


def validate_email(value: str, label: str = "Email") -> str:
    value = validate_header(value, label)
    if not value.isascii() or not EMAIL_PATTERN.fullmatch(value):
        raise ValueError(f"{label} invalide : {value}")
    return value


def load_template(path):
    content = Path(path).read_text(encoding="utf-8-sig")
    subject_line, separator, body = content.partition("\n")
    if not separator or not subject_line.upper().startswith("SUBJECT:"):
        raise ValueError(f"{path} doit commencer par SUBJECT: suivi du corps du message")
    subject = subject_line[len("SUBJECT:"):].strip()
    validate_header(subject, "Objet du modèle")
    if not body.strip():
        raise ValueError(f"Corps du message vide dans {path}")
    return subject, body.strip("\n")


def render(text: str, ctx: dict) -> str:
    def replace(match):
        key = match.group(1)
        if key not in ctx:
            raise ValueError(f"Variable inconnue dans le modèle : {{{key}}}")
        return str(ctx[key] or "")
    return PLACEHOLDER_PATTERN.sub(replace, text)


def load_profile(path=None) -> dict:
    path = Path(path) if path is not None else Path(PROFILE_FILE)
    profile = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict) or not isinstance(profile.get("identity"), dict):
        raise ValueError(f"Profil candidat invalide : {path}")
    identity = profile["identity"]
    for key in ("first_name", "last_name"):
        validate_header(identity.get(key), f"Profil : {key}")
    validate_email(identity.get("email"), "Email du profil")
    if not isinstance(identity.get("phone", ""), str):
        raise ValueError("Le téléphone du profil doit être du texte")
    return profile


def profile_context(profile: dict) -> dict:
    identity = profile["identity"]
    internship = profile.get("internship")
    if not isinstance(internship, dict):
        raise ValueError("Durée et disponibilité du stage absentes du profil candidat")
    duration = internship.get("duration_months")
    if type(duration) is not int or duration <= 0:
        raise ValueError("Durée de stage invalide dans le profil candidat")
    try:
        availability = date.fromisoformat(internship.get("available_from", ""))
    except (TypeError, ValueError):
        raise ValueError("Disponibilité invalide dans le profil candidat (format YYYY-MM-DD attendu)") from None
    return {"prenom_expediteur": identity["first_name"], "nom_expediteur": identity["last_name"],
            "email_expediteur": identity["email"], "telephone_expediteur": identity.get("phone", ""),
            "duree_stage": str(duration), "periode_stage": f"{MONTH_NAMES[availability.month - 1]} {availability.year}"}


def load_contacts(path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"entreprise", "email"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} doit contenir les colonnes entreprise et email")
        contacts = []
        for line_number, row in enumerate(reader, 2):
            if None in row:
                raise ValueError(f"Ligne CSV {line_number} mal formée dans {path}")
            contact = {key: (value or "").strip() for key, value in row.items()}
            if not any(contact.values()):
                continue
            contact.setdefault("prenom", "")
            contact.setdefault("nom", "")
            contacts.append(contact)
    return contacts


def validate_contacts(contacts: list[dict]) -> list[dict]:
    validated, seen = [], set()
    for contact in contacts:
        contact = dict(contact)
        contact["email"] = validate_email(contact.get("email"), "Email du contact")
        contact["entreprise"] = validate_header(contact.get("entreprise"), "Entreprise du contact")
        contact.setdefault("prenom", "")
        contact.setdefault("nom", "")
        key = contact["email"].casefold()
        if key not in seen:
            validated.append(contact)
            seen.add(key)
    return validated


def application_file(folder: Path, filename, label: str) -> Path:
    if not isinstance(filename, str) or not filename:
        raise ValueError(f"Manifest : fichier {label} absent")
    path = (folder / filename).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise ValueError(f"Fichier {label} absent ou situé hors du dossier de candidature")
    return path


def read_pdf(file_path):
    file_path = Path(file_path)
    validate_header(file_path.name, "Nom de pièce jointe")
    content = file_path.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"Pièce jointe PDF invalide : {file_path}")
    return file_path, content


def print_review_notes(notes):
    if notes:
        print("Points à vérifier dans ce dossier :")
        for note in notes:
            print(f"  - {note}")
        print()


def load_application(folder, require_fresh=False):
    folder = Path(folder).resolve()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Manifest de candidature invalide ou version non prise en charge")
    for key in ("offer_id", "company", "title"):
        validate_header(manifest.get(key), f"Manifest : {key}")
    profile = load_profile()
    if validate_email(manifest.get("profile_email"), "Email du manifest").casefold() != profile["identity"]["email"].casefold():
        raise ValueError("Ce dossier appartient à un autre profil candidat ; régénérez la candidature")
    expected_hash = manifest.get("source_profile_sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ValueError("Empreinte du profil absente ou invalide ; régénérez la candidature")
    current_hash = hashlib.sha256(Path(PROFILE_FILE).read_bytes()).hexdigest()
    if expected_hash.lower() != current_hash:
        message = "Le profil a changé depuis la génération du dossier ; régénérez la candidature avant l'envoi"
        if require_fresh:
            raise ValueError(message)
        print(f"Attention : {message}.", file=sys.stderr)
    offer_path = application_file(folder, "offre.json", "offre")
    offer_hash = manifest.get("source_offer_sha256")
    if not isinstance(offer_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", offer_hash):
        raise ValueError("Empreinte de l'offre absente ou invalide ; régénérez la candidature")
    if offer_hash.lower() != hashlib.sha256(offer_path.read_bytes()).hexdigest():
        message = "L'offre a changé depuis la génération du dossier ; régénérez la candidature avant l'envoi"
        if require_fresh:
            raise ValueError(message)
        print(f"Attention : {message}.", file=sys.stderr)
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("Manifest : liste des fichiers absente")
    cv = application_file(folder, files.get("cv"), "CV")
    letter = application_file(folder, files.get("letter"), "lettre")
    email_path = application_file(folder, files.get("email"), "email")
    recipient = manifest.get("recipient") or {}
    if not isinstance(recipient, dict):
        raise ValueError("Manifest : destinataire invalide")
    if any(not isinstance(recipient.get(key, ""), str) for key in ("email", "prenom", "nom")):
        raise ValueError("Manifest : les informations du destinataire doivent être du texte")
    notes = manifest.get("review_notes", [])
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise ValueError("Manifest : les points à vérifier doivent être une liste de textes")
    return manifest, recipient, (cv, letter), load_template(email_path)


def build_message(contact: dict, subject_tpl: str, body_tpl: str, sender_email: str,
                  ctx: dict, attachments=None):
    row_ctx = {**ctx, "prenom": contact.get("prenom", ""), "nom": contact.get("nom", ""),
               "entreprise": contact["entreprise"]}
    subject = validate_header(render(subject_tpl, row_ctx), "Objet du message")
    body = render(body_tpl, row_ctx).replace("Bonjour ,", "Bonjour,")
    sender_email = validate_email(sender_email, "Email du compte expéditeur")
    recipient_email = validate_email(contact["email"], "Email du contact")
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = subject
    if ctx.get("email_expediteur"):
        msg["Reply-To"] = validate_email(ctx["email_expediteur"], "Email du profil")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for file_path in attachments if attachments is not None else (CV_FILE,):
        file_path, content = read_pdf(file_path)
        attachment = MIMEApplication(content, _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=file_path.name)
        msg.attach(attachment)
    return msg, subject, body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="envoie réellement les emails (sinon aperçu seul)")
    parser.add_argument("--dry-run", action="store_true", help="aperçu seul, comportement par défaut")
    parser.add_argument("--only", help="n'envoie/affiche qu'au contact ayant cet email")
    parser.add_argument("--contacts", help="CSV des contacts (défaut : contacts.csv à côté du script)")
    parser.add_argument("--application", help="dossier de candidature généré pour une offre ou une entreprise")
    parser.add_argument("--cv", help="CV PDF pour le mode sans --application")
    parser.add_argument("--template", help="modèle email pour le mode sans --application")
    args = parser.parse_args(argv)
    if args.send and args.dry_run:
        parser.error("--send et --dry-run ne peuvent pas être utilisés ensemble")
    if args.application and (args.cv or args.template):
        parser.error("--application fournit déjà son CV et son email ; utilisez --cv/--template sans --application")

    try:
        profile = load_profile()
        identity = profile["identity"]
        review_notes = []
        if args.application:
            manifest, recipient, attachments, (subject_tpl, body_tpl) = load_application(args.application, args.send)
            company = manifest["company"]
            review_notes = manifest.get("review_notes", [])
            if recipient.get("email", "").strip():
                contacts = [{"entreprise": company, "email": recipient["email"].strip(),
                             "prenom": recipient.get("prenom", ""), "nom": recipient.get("nom", "")}]
            else:
                contacts_path = Path(args.contacts) if args.contacts else Path(CONTACTS_FILE)
                contacts = load_contacts(contacts_path) if args.contacts or contacts_path.exists() else []
                contacts = [c for c in contacts if c["entreprise"].casefold() == company.casefold()]
                if not contacts and args.send:
                    raise ValueError(f"Aucun contact pour {company} ; renseignez recipient.email ou un CSV --contacts correspondant")
        else:
            subject_tpl, body_tpl = load_template(args.template or TEMPLATE_FILE)
            attachments = (Path(args.cv) if args.cv else CV_FILE,)
            contacts = load_contacts(args.contacts or CONTACTS_FILE)
        if args.only:
            only_email = validate_email(args.only, "Email --only")
            contacts = [c for c in contacts if c["email"].casefold() == only_email.casefold()]
            if not contacts:
                raise ValueError(f"Aucun contact avec l'email {only_email} dans la sélection")
        contacts = validate_contacts(contacts)
        if not contacts:
            if args.application:
                ctx = {**profile_context(profile), "prenom": "", "nom": "", "entreprise": company}
                subject = validate_header(render(subject_tpl, ctx), "Objet du message")
                body = render(body_tpl, ctx).replace("Bonjour ,", "Bonjour,")
                for path in attachments:
                    read_pdf(path)
                print_review_notes(review_notes)
                print(f"[BROUILLON] -> {company}")
                print(f"  Objet: {subject}\n  ---\n  " + body.replace("\n", "\n  "))
                print("  Pièces jointes : " + ", ".join(Path(path).name for path in attachments))
                print("  ---\n")
                print("Destinataire à renseigner dans l'offre puis dossier à régénérer, ou dans un CSV --contacts.")
                print("Aperçu du brouillon — aucun email n'a été envoyé.")
                return 0
            print("Aucun contact dans la sélection.")
            return 0

        load_dotenv()
        sender_email = os.environ.get("EMAIL_ADDRESS", "").strip() or identity["email"]
        sender_password = os.environ.get("EMAIL_PASSWORD", "")
        if args.send and (not os.environ.get("EMAIL_ADDRESS", "").strip() or not sender_password):
            raise ValueError("EMAIL_ADDRESS / EMAIL_PASSWORD non définis dans .env ou l'environnement")
        ctx = profile_context(profile)
        # Valider tous les messages avant toute connexion SMTP.
        messages = [(contact, *build_message(contact, subject_tpl, body_tpl, sender_email, ctx, attachments))
                    for contact in contacts]
        payloads = [message[1].as_string() for message in messages]
        smtp_server, smtp_port = None, None
        if args.send:
            smtp_server = validate_header(os.environ.get("SMTP_SERVER", "smtp.gmail.com"), "Serveur SMTP")
            try:
                smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            except ValueError:
                raise ValueError("SMTP_PORT doit être un nombre entier") from None
            if not 1 <= smtp_port <= 65535:
                raise ValueError("SMTP_PORT doit être compris entre 1 et 65535")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    server, sent = None, 0
    try:
        if not args.send:
            print_review_notes(review_notes)
        if args.send:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=SMTP_TIMEOUT)
            server.starttls(context=ssl.create_default_context())
            server.login(sender_email, sender_password)
        for i, (contact, msg, subject, body) in enumerate(messages, 1):
            print(f"[{i}/{len(messages)}] {'ENVOI' if args.send else 'APERÇU'} -> {contact['entreprise']} | "
                  f"{contact['prenom']} {contact['nom']} <{contact['email']}>")
            print(f"  Objet: {subject}\n  ---\n  " + body.replace("\n", "\n  "))
            print("  Pièces jointes : " + ", ".join(Path(path).name for path in attachments))
            print("  ---\n")
            if args.send:
                refused = server.sendmail(sender_email, contact["email"], payloads[i - 1])
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
                sent += 1
                if i < len(messages):
                    time.sleep(DELAY_BETWEEN_EMAILS)
    except (OSError, smtplib.SMTPException) as exc:
        # Ne pas afficher les détails SMTP susceptibles de contenir des identifiants.
        print(f"Erreur SMTP ({type(exc).__name__}) : connexion ou envoi impossible. "
              f"{sent}/{len(messages)} email(s) envoyé(s).", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            try:
                server.quit()
            except (OSError, smtplib.SMTPException):
                try:
                    server.close()
                except OSError:
                    pass
    if args.send:
        print(f"{sent} email(s) envoyé(s).")
    else:
        print(f"Aperçu de {len(messages)} email(s) — rien n'a été envoyé. Relance avec --send pour envoyer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
