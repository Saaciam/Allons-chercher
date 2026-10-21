"""Offline verification of the local interface's application adapter."""

from __future__ import annotations

from email import message_from_string
import json
from pathlib import Path
import shutil
import smtplib
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import applications
import script as hunter
from workflow_service import WorkflowService


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        shutil.copyfile(PROJECT_DIR / "candidate_profile.json", self.base / "candidate_profile.json")
        self.service = WorkflowService(self.base)
        self.offer = {"id": "test-stage", "entreprise": "Entreprise test", "intitule": "Stage Python Data",
                      "description": "Analyse de données en Python et SQL", "date_debut": "2027-03-01", "duree_mois": 6}
        self.contact = {"entreprise": self.offer["entreprise"], "prenom": "Camille", "nom": "Test", "email": "camille@example.fr"}

    def prepare(self):
        return self.service.prepare(self.offer)

    def test_prepare_reuses_candidate_facts_and_correct_documents(self):
        result = self.prepare()
        profile = applications.load_profile(self.base / "candidate_profile.json")
        self.assertEqual(self.offer["id"], result["id"])
        self.assertEqual(self.offer, result["offer"])
        manifest = json.loads((self.base / "candidatures" / self.offer["id"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["identity"]["email"], manifest["profile_email"])
        self.assertIn(profile["identity"]["display_name"], result["letter_text"])
        self.assertFalse(result["stale"])
        cv = self.service.get_document(result["id"], "cv")
        letter = self.service.get_document(result["id"], "letter")
        self.assertNotEqual(cv, letter)
        self.assertIn(self.offer["entreprise"], " ".join(page.extract_text() for page in PdfReader(letter).pages))
        self.assertEqual([result["id"]], [app["id"] for app in self.service.list_applications()])

    def test_preview_has_no_external_side_effect_and_allows_missing_email(self):
        self.prepare()
        with patch("workflow_service.smtplib.SMTP") as smtp, patch("workflow_service.hunter.search_company") as search, patch("workflow_service.send_emails.load_dotenv") as dotenv:
            result = self.service.preview(self.offer["id"])
            named = self.service.preview(self.offer["id"], self.contact)
        smtp.assert_not_called()
        search.assert_not_called()
        dotenv.assert_not_called()
        self.assertEqual("", result["recipient"]["email"])
        self.assertIn("Bonjour,", result["body"])
        self.assertIn("Bonjour Camille,", named["body"])
        self.assertEqual(2, len(result["attachments"]))
        self.assertEqual(result["letter_text"], self.service.load_application(self.offer["id"])["letter_text"])
        self.assertTrue(all(item["url"].startswith("/api/applications/test-stage/files/") for item in result["attachments"]))

    def test_explicit_send_uses_tls_profile_reply_to_and_two_pdf_attachments(self):
        self.prepare()
        profile = applications.load_profile(self.base / "candidate_profile.json")
        with patch.dict("os.environ", {"EMAIL_ADDRESS": "sender@example.fr", "EMAIL_PASSWORD": "test-secret", "SMTP_PORT": "587"}, clear=True), patch("workflow_service.send_emails.load_dotenv") as dotenv, patch("workflow_service.smtplib.SMTP") as smtp:
            smtp.return_value.sendmail.return_value = {}
            result = self.service.send(self.offer["id"], self.contact)
        dotenv.assert_called_once_with(self.base / ".env")
        smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
        smtp.return_value.starttls.assert_called_once()
        smtp.return_value.login.assert_called_once_with("sender@example.fr", "test-secret")
        smtp.return_value.quit.assert_called_once()
        payload = smtp.return_value.sendmail.call_args.args[2]
        message = message_from_string(payload)
        self.assertEqual(profile["identity"]["email"], message["Reply-To"])
        self.assertEqual(self.contact["email"], message["To"])
        documents = [part for part in message.walk() if part.get_content_type() == "application/pdf"]
        self.assertEqual(2, len(documents))
        self.assertTrue(all(part.get_payload(decode=True).startswith(b"%PDF-") for part in documents))
        self.assertEqual(self.contact["email"], result["recipient"]["email"])

    def test_profile_change_and_offer_change_block_send_before_dotenv(self):
        self.prepare()
        profile = self.base / "candidate_profile.json"
        profile.write_bytes(profile.read_bytes() + b"\n")
        self.assertTrue(self.service.preview(self.offer["id"])["stale"])
        with patch("workflow_service.send_emails.load_dotenv") as dotenv, patch("workflow_service.smtplib.SMTP") as smtp:
            with self.assertRaisesRegex(ValueError, "profil a changé"):
                self.service.send(self.offer["id"], self.contact)
        dotenv.assert_not_called()
        smtp.assert_not_called()
        self.service.prepare(self.offer, overwrite=True)
        path = self.base / "candidatures" / self.offer["id"] / "offre.json"
        edited = json.loads(path.read_text(encoding="utf-8"))
        edited["description"] += " Modification manuelle"
        path.write_text(json.dumps(edited), encoding="utf-8")
        with patch("workflow_service.smtplib.SMTP") as smtp:
            with self.assertRaisesRegex(ValueError, "offre a changé"):
                self.service.send(self.offer["id"], self.contact)
        smtp.assert_not_called()

    def test_headers_addresses_and_wrong_company_rejected(self):
        self.prepare()
        for contact in ({**self.contact, "email": "x@example.fr\r\nBcc: y@example.fr"},
                        {**self.contact, "prenom": "Camille\x00"},
                        {**self.contact, "entreprise": "Autre entreprise"}):
            with self.subTest(contact=contact), self.assertRaises(ValueError):
                self.service.preview(self.offer["id"], contact)
        with self.assertRaises(ValueError):
            self.service.preview(self.offer["id"], self.contact, subject="Objet\r\nBcc: x@example.fr")
        with self.assertRaises(ValueError):
            self.service.upsert_contact({**self.contact, "email": "pas-un-email"})

    def test_dossier_and_manifest_files_are_confined(self):
        self.prepare()
        for name in ("../candidate_profile.json", "..", "test-stage/../../", "TEST", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.load_application(name)
        manifest_path = self.base / "candidatures" / self.offer["id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["cv"] = "../../candidate_profile.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hors du dossier"):
            self.service.get_document(self.offer["id"], "cv")
        self.assertEqual([], self.service.list_applications())

    def test_manual_contacts_persist_merge_and_deduplicate_by_company_email(self):
        first = self.service.upsert_contact(self.contact)
        second = self.service.upsert_contact({**self.contact, "prenom": "Camille corrigé"})
        self.service.upsert_contact({**self.contact, "entreprise": "Autre entreprise"})
        reloaded = WorkflowService(self.base).list_contacts()
        self.assertEqual(2, len(reloaded))
        self.assertNotEqual(first["prenom"], second["prenom"])
        found = next(row for row in reloaded if row["entreprise"] == self.offer["entreprise"])
        self.assertEqual("Camille corrigé", found["prenom"])
        self.assertTrue((self.base / "contacts.interface.csv").exists())

    def test_hunter_runs_only_explicitly_and_preserves_csv_on_failure(self):
        self.service.upsert_contact(self.contact)
        csv_path = self.base / "contacts.interface.csv"
        previous = csv_path.read_bytes()
        with patch("workflow_service.hunter.load_dotenv") as dotenv, patch("workflow_service.hunter.search_company", side_effect=hunter.HunterError("Échec Hunter")) as search:
            with self.assertRaises(hunter.HunterError):
                self.service.search_contacts("Nouvelle entreprise")
        dotenv.assert_called_once_with(self.base / ".env")
        search.assert_called_once()
        self.assertEqual(previous, csv_path.read_bytes())
        with patch("workflow_service.hunter.load_dotenv"), patch.dict("os.environ", {"HUNTER_API_KEY": "mock-key"}, clear=True), patch("workflow_service.hunter.search_company", return_value=[{"first_name": "Jean", "last_name": "Test", "value": "jean@example.fr", "confidence": 90}]) as search:
            result = self.service.search_contacts("Nouvelle entreprise", "hr")
        search.assert_called_once_with("Nouvelle entreprise", "hr", "mock-key")
        self.assertEqual("90", result[0]["confiance"])
        self.assertEqual(2, len(self.service.list_contacts()))

    def test_draft_and_letter_edit_survive_restart_and_regenerate_pdf(self):
        prepared = self.prepare()
        original_cv = self.service.get_document(self.offer["id"], "cv").read_bytes()
        result = self.service.update_texts(self.offer["id"], subject="Ma candidature relue", body="Bonjour {prenom},\n\nVoici ma candidature.")
        self.assertEqual("Ma candidature relue", result["subject"])
        letter_text = "Madame, Monsieur,\n\nJe souhaite rejoindre votre équipe.\n\nChristopher KOUTOUA"
        self.service.update_texts(self.offer["id"], letter_text=letter_text)
        reloaded = WorkflowService(self.base).preview(self.offer["id"], self.contact)
        self.assertEqual("Ma candidature relue", reloaded["subject"])
        self.assertIn("Bonjour Camille", reloaded["body"])
        self.assertEqual(letter_text, reloaded["letter_text"])
        # Saving the same draft again must not append a newline to the letter.
        self.service.update_texts(self.offer["id"], letter_text=reloaded["letter_text"])
        self.assertEqual(letter_text, WorkflowService(self.base).load_application(self.offer["id"])["letter_text"])
        self.assertIn("Je souhaite rejoindre", " ".join(page.extract_text() for page in PdfReader(self.service.get_document(self.offer["id"], "letter")).pages))
        self.assertEqual(original_cv, self.service.get_document(self.offer["id"], "cv").read_bytes())
        self.assertFalse(reloaded["stale"])
        self.assertEqual(prepared["created_at"], self.service.load_application(self.offer["id"])["created_at"])

    def test_smtp_failure_is_sanitized_and_connection_closed(self):
        self.prepare()
        with patch.dict("os.environ", {"EMAIL_ADDRESS": "sender@example.fr", "EMAIL_PASSWORD": "secret"}, clear=True), patch("workflow_service.send_emails.load_dotenv"), patch("workflow_service.smtplib.SMTP") as smtp:
            smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"secret password")
            smtp.return_value.quit.side_effect = OSError("unavailable")
            with self.assertRaises(RuntimeError) as raised:
                self.service.send(self.offer["id"], self.contact)
        self.assertNotIn("secret", str(raised.exception))
        smtp.return_value.close.assert_called_once()
        smtp.return_value.sendmail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
