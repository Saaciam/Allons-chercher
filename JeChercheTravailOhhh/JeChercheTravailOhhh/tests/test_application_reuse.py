"""Existing dossiers are reused without losing the user's document edits."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import applications
from workflow_service import IncompleteApplicationError, WorkflowService


class ApplicationReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        shutil.copyfile(PROJECT_DIR / "candidate_profile.json", self.base / "candidate_profile.json")
        self.service = WorkflowService(self.base)
        self.offer = {"id": "stage-reuse", "entreprise": "Entreprise de test",
                      "intitule": "Stage Python Data", "description": "Analyse Python et SQL",
                      "date_debut": "2027-03-01", "duree_mois": 6}
        self.folder = self.base / "candidatures" / self.offer["id"]

    def dossier_bytes(self):
        return {path.name: path.read_bytes() for path in self.folder.iterdir() if path.is_file()}

    def test_repeated_preparation_preserves_all_edited_files_after_restart(self):
        first = self.service.prepare(self.offer)
        self.assertFalse(first["reused"])
        self.service.update_texts(self.offer["id"], subject="Objet relu",
                                  body="Bonjour {prenom},\n\nMon message personnel.",
                                  letter_text="Madame, Monsieur,\n\nMa lettre retouchée.\n\nChristopher KOUTOUA")
        before = self.dossier_bytes()
        with patch("workflow_service.applications.prepare_application") as generate:
            reopened = WorkflowService(self.base).prepare(self.offer)
        generate.assert_not_called()
        self.assertTrue(reopened["reused"])
        self.assertEqual("Objet relu", reopened["subject"])
        self.assertIn("Mon message personnel", reopened["body"])
        self.assertIn("Ma lettre retouchée", reopened["letter_text"])
        self.assertEqual(first["created_at"], reopened["created_at"])
        self.assertEqual(before, self.dossier_bytes())

    def test_profile_change_warns_without_regenerating_or_changing_documents(self):
        self.service.prepare(self.offer)
        before = self.dossier_bytes()
        path = self.base / "candidate_profile.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["identity"]["email"] = "new-profile@example.fr"
        path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        reopened = self.service.prepare(self.offer)
        self.assertTrue(reopened["reused"])
        self.assertTrue(reopened["stale"])
        self.assertTrue(any("profil a changé" in warning for warning in reopened["warnings"]))
        self.assertEqual(before, self.dossier_bytes())

    def test_simultaneous_preparations_generate_once(self):
        original = applications.prepare_application
        with patch("workflow_service.applications.prepare_application", wraps=original) as generate:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: self.service.prepare(self.offer), range(8)))
        self.assertEqual(1, generate.call_count)
        self.assertEqual(1, sum(not result["reused"] for result in results))
        self.assertEqual(7, sum(result["reused"] for result in results))
        self.assertEqual({self.offer["id"]}, {result["id"] for result in results})
        self.assertEqual(1, len(self.service.list_applications()))

    def test_missing_manifest_reports_recoverable_error_without_overwriting(self):
        self.folder.mkdir(parents=True)
        (self.folder / "offre.json").write_bytes(applications.json_bytes(self.offer))
        (self.folder / "email.txt").write_text("Ma retouche à préserver", encoding="utf-8")
        before = self.dossier_bytes()
        with self.assertRaises(IncompleteApplicationError) as raised:
            self.service.prepare(self.offer)
        error = raised.exception
        self.assertEqual("application_incomplete", error.code)
        self.assertEqual(self.offer["id"], error.application_id)
        self.assertTrue(error.can_regenerate)
        self.assertIn("Régénérer les documents", str(error))
        self.assertNotIn(str(self.base), str(error))
        self.assertNotIn("--overwrite", str(error))
        self.assertEqual(self.offer, self.service.recover_offer(self.offer["id"]))
        self.assertEqual(before, self.dossier_bytes())

    def test_missing_pdf_and_malformed_manifest_do_not_silently_regenerate(self):
        self.service.prepare(self.offer)
        (self.folder / "Lettre_motivation.pdf").unlink()
        before = self.dossier_bytes()
        with self.assertRaises(IncompleteApplicationError) as raised:
            self.service.prepare(self.offer)
        self.assertTrue(raised.exception.can_regenerate)
        self.assertEqual(before, self.dossier_bytes())
        (self.folder / "manifest.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(IncompleteApplicationError):
            self.service.prepare(self.offer)
        self.assertEqual(self.offer, self.service.recover_offer(self.offer["id"]))

    def test_empty_existing_folder_cannot_recover_offer(self):
        self.folder.mkdir(parents=True)
        with self.assertRaises(IncompleteApplicationError) as raised:
            self.service.prepare(self.offer)
        self.assertFalse(raised.exception.can_regenerate)
        self.assertEqual([], list(self.folder.iterdir()))
        with self.assertRaises(ValueError):
            self.service.recover_offer(self.offer["id"])

    def test_explicit_overwrite_repairs_incomplete_dossier(self):
        self.folder.mkdir(parents=True)
        (self.folder / "offre.json").write_bytes(applications.json_bytes(self.offer))
        result = self.service.prepare(self.service.recover_offer(self.offer["id"]), overwrite=True)
        self.assertFalse(result["reused"])
        self.assertFalse(result["stale"])
        self.assertTrue(self.service.get_document(self.offer["id"], "letter").is_file())

    def test_recovery_requires_safe_id_and_matching_saved_offer(self):
        self.folder.mkdir(parents=True)
        path = self.folder / "offre.json"
        for application_id in ("../candidate_profile.json", "..", "", "STAGE"):
            with self.subTest(application_id=application_id), self.assertRaises(ValueError):
                self.service.recover_offer(application_id)
        path.write_bytes(applications.json_bytes({**self.offer, "id": "another-stage"}))
        with self.assertRaisesRegex(ValueError, "identifiant"):
            self.service.recover_offer(self.offer["id"])
        path.write_bytes(applications.json_bytes({**self.offer, "entreprise": ""}))
        with self.assertRaisesRegex(ValueError, "entreprise"):
            self.service.recover_offer(self.offer["id"])

    def test_recovery_and_overwrite_reject_external_offer_symlink(self):
        self.folder.mkdir(parents=True)
        outside = self.base / "external-offer.json"
        outside.write_bytes(applications.json_bytes(self.offer))
        try:
            (self.folder / "offre.json").symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symbolic links unavailable: {error}")
        before = outside.read_bytes()
        with self.assertRaises(ValueError):
            self.service.recover_offer(self.offer["id"])
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite), self.assertRaises(ValueError):
                self.service.prepare(self.offer, overwrite=overwrite)
        self.assertEqual(before, outside.read_bytes())


if __name__ == "__main__":
    unittest.main()
