import contextlib
import hashlib
import io
import json
import os
import smtplib
import ssl
import tempfile
import unittest
from email import message_from_string
from pathlib import Path
from unittest.mock import Mock, patch

import send_emails


class SendEmailsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.profile = self.folder / "candidate_profile.json"
        self.profile.write_text(json.dumps({
            "identity": {"first_name": "Christopher Isaac William", "last_name": "KOUTOUA",
                         "email": "ckoutoua25@gmail.com", "phone": "+33 7 53 26 00 25"},
            "internship": {"duration_months": 6, "available_from": "2027-03-01"},
        }), encoding="utf-8")
        self.cv = self.folder / "CV_CHRISTOPHER_KOUTOUA.pdf"
        self.letter = self.folder / "Lettre_motivation.pdf"
        self.cv.write_bytes(b"%PDF-1.4\nCV specific to ACME")
        self.letter.write_bytes(b"%PDF-1.4\nLetter specific to ACME")
        self.email = self.folder / "email.txt"
        self.email.write_text(
            "SUBJECT: Candidature {entreprise} - {prenom_expediteur} {nom_expediteur}\n\n"
            "Bonjour {prenom},\nStage de {duree_stage} mois en {periode_stage}.\n"
            "{prenom_expediteur} {nom_expediteur}\n{email_expediteur}\n{telephone_expediteur}",
            encoding="utf-8")
        self.contacts = self.folder / "contacts.csv"
        self.contacts.write_text(
            "entreprise,prenom,nom,email\nACME,Marie,Dupont,marie@example.fr\n"
            "OTHER,Paul,Durand,paul@example.fr\nACME,Marie,Dupont,MARIE@example.fr\n",
            encoding="utf-8-sig")
        self.offer = self.folder / "offre.json"
        self.offer.write_text(json.dumps({"id": "acme-stage", "entreprise": "acme",
                                          "intitule": "Stage data"}), encoding="utf-8")
        self.manifest = {
            "schema_version": 1, "offer_id": "acme-stage", "company": "acme", "title": "Stage data",
            "profile_email": "ckoutoua25@gmail.com",
            "source_profile_sha256": hashlib.sha256(self.profile.read_bytes()).hexdigest(),
            "source_offer_sha256": hashlib.sha256(self.offer.read_bytes()).hexdigest(),
            "files": {"cv": self.cv.name, "letter": self.letter.name, "email": self.email.name},
            "recipient": {"email": "", "prenom": "", "nom": ""},
            "review_notes": ["Docker demandé : compétence non attestée dans le profil."],
        }
        self.write_manifest()
        self.server = Mock()
        self.server.sendmail.return_value = {}
        for patcher in (
            patch.object(send_emails, "PROFILE_FILE", self.profile),
            patch.object(send_emails, "CV_FILE", self.cv),
            patch.object(send_emails, "load_dotenv"),
            patch.dict(os.environ, {"EMAIL_ADDRESS": "smtp@example.fr", "EMAIL_PASSWORD": "fake-password",
                                    "SENDER_PRENOM": "Incorrect", "SENDER_NOM": "Identity"}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        smtp_patcher = patch.object(send_emails.smtplib, "SMTP", return_value=self.server)
        self.smtp = smtp_patcher.start()
        self.addCleanup(smtp_patcher.stop)
        sleep_patcher = patch.object(send_emails.time, "sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def write_manifest(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def run_command(self, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = send_emails.main(arguments)
        return code, output.getvalue()

    def legacy_arguments(self):
        return ["--contacts", str(self.contacts), "--cv", str(self.cv), "--template", str(self.email)]

    def test_legacy_preview_uses_profile_identity_and_deduplicates_selected_contact(self):
        code, output = self.run_command(self.legacy_arguments() + ["--only", "MARIE@example.fr"])
        self.assertEqual(code, 0, output)
        self.smtp.assert_not_called()
        self.assertIn("Aperçu de 1 email(s)", output)
        self.assertIn("Christopher Isaac William KOUTOUA", output)
        self.assertIn("Stage de 6 mois en mars 2027", output)
        self.assertNotIn("Incorrect Identity", output)

    def test_actual_legacy_template_renders_all_profile_fields(self):
        arguments = self.legacy_arguments()
        arguments[-1] = str(send_emails.TEMPLATE_FILE)
        code, output = self.run_command(arguments + ["--only", "marie@example.fr"])
        self.assertEqual(code, 0, output)
        self.assertIn("Christopher Isaac William KOUTOUA", output)
        self.assertIn("mars 2027", output)
        self.assertNotIn("{periode_stage}", output)
        self.assertNotIn("modèles de langage", output)
        self.smtp.assert_not_called()

    def test_preview_does_not_need_smtp_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            code, output = self.run_command(self.legacy_arguments())
        self.assertEqual(code, 0, output)
        self.smtp.assert_not_called()

    def test_application_selects_company_contacts_and_displays_review_notes(self):
        code, output = self.run_command(["--application", str(self.folder), "--contacts", str(self.contacts)])
        self.assertEqual(code, 0, output)
        self.assertIn("Aperçu de 1 email(s)", output)
        self.assertIn("Docker demandé", output)
        self.assertIn(f"{self.cv.name}, {self.letter.name}", output)
        self.assertNotIn("paul@example.fr", output)
        self.smtp.assert_not_called()

    def test_application_without_contacts_previews_a_draft_without_fake_recipient(self):
        with patch.object(send_emails, "CONTACTS_FILE", self.folder / "no-default-contacts.csv"):
            code, output = self.run_command(["--application", str(self.folder)])
        self.assertEqual(code, 0, output)
        self.assertIn("[BROUILLON]", output)
        self.assertIn("Bonjour,", output)
        self.assertIn("Destinataire à renseigner", output)
        self.assertIn("Docker demandé", output)
        self.assertIn(self.cv.name, output)
        self.assertIn(self.letter.name, output)
        self.assertNotIn("<", output)
        self.smtp.assert_not_called()

    def test_application_with_no_matching_company_also_previews_a_draft(self):
        self.contacts.write_text("entreprise,email\nOTHER,paul@example.fr\n", encoding="utf-8")
        code, output = self.run_command(["--application", str(self.folder), "--contacts", str(self.contacts)])
        self.assertEqual(code, 0, output)
        self.assertIn("[BROUILLON]", output)
        self.assertNotIn("paul@example.fr", output)
        self.smtp.assert_not_called()

    def test_send_and_only_require_real_application_recipient(self):
        with patch.object(send_emails, "CONTACTS_FILE", self.folder / "no-default-contacts.csv"):
            for flag in (["--send"], ["--only", "marie@example.fr"]):
                with self.subTest(arguments=flag):
                    code, output = self.run_command(["--application", str(self.folder)] + flag)
                    self.assertEqual(code, 1)
                    self.assertIn("Aucun contact", output)
                    self.smtp.assert_not_called()

    def test_explicit_missing_contacts_is_an_error_even_for_draft(self):
        code, output = self.run_command(["--application", str(self.folder),
                                         "--contacts", str(self.folder / "missing.csv")])
        self.assertEqual(code, 1)
        self.smtp.assert_not_called()

    def test_application_recipient_needs_no_csv_and_attaches_its_own_files(self):
        self.manifest["recipient"] = {"email": "recrutement@example.fr", "prenom": "Julie", "nom": "Martin"}
        self.write_manifest()
        code, output = self.run_command(["--application", str(self.folder), "--send",
                                         "--contacts", str(self.folder / "missing.csv")])
        self.assertEqual(code, 0, output)
        self.smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=send_emails.SMTP_TIMEOUT)
        self.server.starttls.assert_called_once()
        self.assertIsInstance(self.server.starttls.call_args.kwargs["context"], ssl.SSLContext)
        self.server.login.assert_called_once_with("smtp@example.fr", "fake-password")
        sender, recipient, payload = self.server.sendmail.call_args.args
        self.assertEqual((sender, recipient), ("smtp@example.fr", "recrutement@example.fr"))
        message = message_from_string(payload)
        self.assertEqual(message["Reply-To"], "ckoutoua25@gmail.com")
        parts = {part.get_filename(): part.get_payload(decode=True)
                 for part in message.walk() if part.get_filename()}
        self.assertEqual(parts, {self.cv.name: self.cv.read_bytes(), self.letter.name: self.letter.read_bytes()})
        body = message.get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("Bonjour Julie", body)
        self.server.quit.assert_called_once()

    def test_invalid_last_contact_blocks_all_sending_before_smtp(self):
        self.contacts.write_text("entreprise,email\nACME,ok@example.fr\nACME,invalid\n", encoding="utf-8")
        code, output = self.run_command(self.legacy_arguments() + ["--send"])
        self.assertEqual(code, 1)
        self.assertIn("Email du contact invalide", output)
        self.smtp.assert_not_called()

    def test_missing_application_letter_blocks_smtp(self):
        self.letter.unlink()
        code, output = self.run_command(["--application", str(self.folder), "--contacts", str(self.contacts), "--send"])
        self.assertEqual(code, 1)
        self.assertIn("lettre", output)
        self.smtp.assert_not_called()

    def test_stale_profile_and_offer_each_block_smtp(self):
        for path, marker in ((self.profile, "profil"), (self.offer, "offre")):
            with self.subTest(source=marker):
                previous = path.read_bytes()
                path.write_bytes(previous + b"\n")
                code, output = self.run_command(["--application", str(self.folder),
                                                 "--contacts", str(self.contacts), "--send"])
                self.assertEqual(code, 1)
                self.assertIn("changé", output)
                self.assertIn("régénérez", output)
                self.smtp.assert_not_called()
                path.write_bytes(previous)

    def test_modified_offer_is_visible_in_preview(self):
        self.offer.write_bytes(self.offer.read_bytes() + b"\n")
        code, output = self.run_command(["--application", str(self.folder), "--contacts", str(self.contacts)])
        self.assertEqual(code, 0, output)
        self.assertIn("L'offre a changé", output)
        self.smtp.assert_not_called()

    def test_unknown_template_variable_blocks_smtp(self):
        self.email.write_text("SUBJECT: Candidature\n\nBonjour {inconnue}", encoding="utf-8")
        code, output = self.run_command(self.legacy_arguments() + ["--send"])
        self.assertEqual(code, 1)
        self.assertIn("Variable inconnue", output)
        self.smtp.assert_not_called()

    def test_files_cannot_escape_application_directory(self):
        self.manifest["files"]["letter"] = "../outside.pdf"
        self.write_manifest()
        code, output = self.run_command(["--application", str(self.folder), "--contacts", str(self.contacts), "--send"])
        self.assertEqual(code, 1)
        self.assertIn("hors du dossier", output)
        self.smtp.assert_not_called()

    def test_tls_login_and_send_failures_close_connection_without_private_details(self):
        for method in ("starttls", "login", "sendmail"):
            with self.subTest(method=method):
                server = Mock()
                server.sendmail.return_value = {}
                getattr(server, method).side_effect = smtplib.SMTPException("private-credential-details")
                self.smtp.return_value = server
                code, output = self.run_command(self.legacy_arguments() + ["--only", "marie@example.fr", "--send"])
                self.assertEqual(code, 1)
                self.assertIn("Erreur SMTP", output)
                self.assertNotIn("private-credential-details", output)
                server.quit.assert_called_once()

    def test_quit_failure_falls_back_to_close(self):
        self.server.quit.side_effect = smtplib.SMTPServerDisconnected("connection already closed")
        code, output = self.run_command(self.legacy_arguments() + ["--only", "marie@example.fr", "--send"])
        self.assertEqual(code, 0, output)
        self.server.close.assert_called_once()

    def test_header_injection_is_rejected_before_smtp(self):
        self.contacts.write_text('entreprise,email\n"ACME\nBcc: intruder@example.fr",ok@example.fr\n', encoding="utf-8")
        code, output = self.run_command(self.legacy_arguments() + ["--send"])
        self.assertEqual(code, 1)
        self.assertIn("retour à la ligne", output)
        self.smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
