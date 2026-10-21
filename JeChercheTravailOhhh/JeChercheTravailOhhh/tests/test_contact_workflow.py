"""Rich contact search keeps legacy CSV exports and manual corrections intact."""

from copy import deepcopy
import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import script as hunter
from workflow_service import WorkflowService


class ContactWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        shutil.copyfile(PROJECT_DIR / "candidate_profile.json", self.base / "candidate_profile.json")
        self.service = WorkflowService(self.base)
        self.row = {"entreprise": "Entreprise test", "prenom": "Camille", "nom": "Martin",
                    "poste": "Talent Acquisition Manager", "email": "camille@example.fr",
                    "confiance": "98", "linkedin": "https://www.linkedin.com/in/contact-test/",
                    "source": "hunter", "department": "hr", "verification_status": "valid",
                    "verification_date": "2026-10-08", "resolved_domain": "example.fr",
                    "public_sources": [{"url": "https://example.fr/equipe", "last_seen_on": "2026-10-08",
                                        "still_on_page": True}],
                    "priority": 0, "priority_reasons": ["Recrutement et ressources humaines"]}
        self.csv_path = self.base / "contacts.interface.csv"
        self.metadata_path = self.base / "data" / "contact_metadata.json"
        smtp_patcher = patch("workflow_service.smtplib.SMTP")
        self.smtp = smtp_patcher.start()
        self.addCleanup(smtp_patcher.stop)
        self.addCleanup(self.smtp.assert_not_called)
        network_patcher = patch("contact_sources.requests.get", side_effect=AssertionError("Unexpected network call"))
        network_patcher.start()
        self.addCleanup(network_patcher.stop)

    def result(self, contacts=None, **changes):
        return {"contacts": deepcopy([self.row] if contacts is None else contacts),
                "company": self.row["entreprise"], "warnings": [], "queries": [],
                "resolved_domain": "example.fr", "from_cache": False, "cache_stale": False,
                "request_count": 1, "truncated": False,
                "checked_at": "2026-10-09T10:00:00+00:00", **changes}

    def search(self, result=None, company="Entreprise test", **kwargs):
        with patch("workflow_service.hunter.load_dotenv"), patch.dict("os.environ", {}, clear=True), \
                patch.object(self.service.contact_discovery, "search", return_value=self.result() if result is None else result):
            return self.service.search_contacts_detail(company, **kwargs)

    def test_explicit_search_forwards_domain_refresh_and_private_key(self):
        with patch("workflow_service.hunter.load_dotenv") as dotenv, \
                patch.dict("os.environ", {"HUNTER_API_KEY": "unit-test-key"}, clear=True), \
                patch.object(self.service.contact_discovery, "search", return_value=self.result()) as discover:
            result = self.service.search_contacts_detail("Entreprise test", domain="example.fr", force=True)
        dotenv.assert_called_once_with(self.base / ".env")
        discover.assert_called_once_with("Entreprise test", "hr", domain="example.fr", force=True, api_key="unit-test-key")
        self.assertEqual("example.fr", result["resolved_domain"])
        self.assertEqual(1, len(result["contacts"]))
        self.assertNotIn("unit-test-key", json.dumps(result))

    def test_legacy_csv_columns_and_public_metadata_survive_restart(self):
        result = self.search()
        with self.csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(hunter.CSV_FIELDS, rows[0])
        self.assertTrue(all(len(row) == 7 for row in rows))
        self.assertTrue(self.metadata_path.exists())
        found = WorkflowService(self.base).list_contacts()[0]
        for key in ("public_sources", "priority", "priority_reasons", "department", "verification_status",
                    "verification_date", "resolved_domain", "source"):
            with self.subTest(key=key):
                self.assertEqual(result["contacts"][0][key], found[key])

    def test_manual_names_position_and_profile_win_over_refreshed_results(self):
        self.search()
        corrected = self.service.upsert_contact({"entreprise": "Entreprise test", "email": self.row["email"],
                                                "prenom": "Camille corrigé", "nom": "Nom corrigé",
                                                "poste": "Responsable recrutement local",
                                                "linkedin": "https://www.linkedin.com/in/profil-corrige/"})
        updated = {**self.row, "prenom": "Ancien prénom", "poste": "Ancien poste",
                   "public_sources": [{"url": "https://example.fr/recrutement", "last_seen_on": "2026-10-09"}],
                   "verification_date": "2026-10-09"}
        result = self.search(self.result([updated]), force=True)
        refreshed = result["contacts"][0]
        self.assertEqual("manual", refreshed["source"])
        for key in hunter.CSV_FIELDS:
            self.assertEqual(corrected[key], refreshed[key])
        self.assertEqual(updated["public_sources"][0]["url"], refreshed["public_sources"][0]["url"])
        self.assertEqual("2026-10-09", refreshed["verification_date"])
        restarted = WorkflowService(self.base).list_contacts()[0]
        self.assertEqual(corrected["prenom"], restarted["prenom"])
        self.assertEqual(refreshed["public_sources"], restarted["public_sources"])

    def test_same_address_at_different_companies_keeps_separate_corrections(self):
        manual = self.service.upsert_contact({**self.row, "entreprise": "Autre entreprise", "prenom": "Contact autre"})
        self.search()
        contacts = WorkflowService(self.base).list_contacts()
        self.assertEqual(2, len(contacts))
        other = next(row for row in contacts if row["entreprise"] == "Autre entreprise")
        found = next(row for row in contacts if row["entreprise"] == "Entreprise test")
        self.assertEqual(manual["prenom"], other["prenom"])
        self.assertEqual("manual", other["source"])
        self.assertEqual("hunter", found["source"])

    def test_empty_or_quota_result_preserves_previous_files_exactly(self):
        self.search()
        previous_csv = self.csv_path.read_bytes()
        previous_metadata = self.metadata_path.read_bytes()
        for warnings in (["Aucun email public trouvé."], ["Le quota Hunter est épuisé."]):
            with self.subTest(warnings=warnings):
                result = self.search(self.result([], warnings=warnings))
                self.assertEqual([], result["contacts"])
                self.assertEqual(warnings, result["warnings"])
                self.assertEqual(previous_csv, self.csv_path.read_bytes())
                self.assertEqual(previous_metadata, self.metadata_path.read_bytes())
                self.assertEqual(1, len(self.service.list_contacts()))

    def test_discovery_exception_preserves_previous_contacts(self):
        self.search()
        previous = self.csv_path.read_bytes()
        with patch("workflow_service.hunter.load_dotenv"), \
                patch.object(self.service.contact_discovery, "search", side_effect=RuntimeError("Lookup unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Lookup unavailable"):
                self.service.search_contacts_detail("Entreprise test", force=True)
        self.assertEqual(previous, self.csv_path.read_bytes())

    def test_missing_key_can_return_links_and_warnings_without_losing_contacts(self):
        self.search()
        previous = self.csv_path.read_bytes()
        fallback = self.result([], warnings=["Hunter n'est pas configuré."],
                               queries=[{"label": "Profils LinkedIn", "url": "https://www.linkedin.com/search/results/people/"}],
                               request_count=0)
        with patch("workflow_service.hunter.load_dotenv"), patch.dict("os.environ", {}, clear=True), \
                patch.object(self.service.contact_discovery, "search", return_value=fallback) as discover:
            result = self.service.search_contacts_detail("Entreprise test")
        self.assertIsNone(discover.call_args.kwargs["api_key"])
        self.assertEqual(fallback["queries"], result["queries"])
        self.assertEqual(previous, self.csv_path.read_bytes())

    def test_invalid_public_or_profile_links_are_rejected_before_persistence(self):
        invalid_rows = [{**self.row, "linkedin": "javascript:alert(1)"},
                        {**self.row, "linkedin": "https://name:password@example.fr/profil"},
                        {**self.row, "public_sources": [{"url": "file:///private/contact.txt"}]},
                        {**self.row, "public_sources": [{"url": "https://example.fr/\r\nInjected"}]}]
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.search(self.result([row]))
        self.assertFalse(self.csv_path.exists())
        self.assertFalse(self.metadata_path.exists())

    def test_loading_contacts_and_manual_edit_never_trigger_discovery(self):
        with patch.object(self.service.contact_discovery, "search") as discover, \
                patch("workflow_service.hunter.load_dotenv") as dotenv:
            self.service.upsert_contact(self.row)
            contacts = self.service.list_contacts()
        discover.assert_not_called()
        dotenv.assert_not_called()
        self.assertEqual("manual", contacts[0]["source"])

    def test_old_csv_without_metadata_still_loads_and_accepts_manual_correction(self):
        old_path = self.base / "contacts.hr.csv"
        with old_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=hunter.CSV_FIELDS)
            writer.writeheader()
            writer.writerow({key: self.row[key] for key in hunter.CSV_FIELDS})
        contact = self.service.list_contacts()[0]
        self.assertEqual(self.row["email"], contact["email"])
        self.assertEqual([], contact["public_sources"])
        self.service.upsert_contact({**contact, "poste": "Poste corrigé"})
        contacts = WorkflowService(self.base).list_contacts()
        self.assertEqual(1, len(contacts))
        self.assertEqual("Poste corrigé", contacts[0]["poste"])

    def test_direct_csv_corrections_survive_restart_and_forced_refresh(self):
        self.search()
        with self.csv_path.open(encoding="utf-8", newline="") as handle:
            corrected = next(csv.DictReader(handle))
        corrected.update(prenom="Prénom retouché dans le CSV", poste="Responsable Data local",
                         linkedin="https://www.linkedin.com/in/profil-csv-corrige/")
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=hunter.CSV_FIELDS)
            writer.writeheader()
            writer.writerow(corrected)
        self.service = WorkflowService(self.base)
        fresh = {**self.row, "prenom": "Valeur Hunter à remplacer", "poste": "Poste Hunter",
                 "linkedin": "https://www.linkedin.com/in/ancien-profil/",
                 "verification_date": "2026-10-09",
                 "public_sources": [{"url": "https://example.fr/equipe-actualisee", "last_seen_on": "2026-10-09"}]}
        result = self.search(self.result([fresh]), force=True)
        contact = result["contacts"][0]
        self.assertEqual("manual", contact["source"])
        for key in hunter.CSV_FIELDS:
            with self.subTest(key=key):
                self.assertEqual(corrected[key], contact[key])
        self.assertEqual(fresh["public_sources"][0]["url"], contact["public_sources"][0]["url"])
        self.assertEqual(fresh["verification_date"], contact["verification_date"])
        restarted = WorkflowService(self.base).list_contacts()[0]
        self.assertEqual(corrected["prenom"], restarted["prenom"])
        self.assertEqual(corrected["poste"], restarted["poste"])
        self.assertEqual(contact["public_sources"], restarted["public_sources"])

    def test_legacy_csv_values_without_metadata_win_over_forced_discovery(self):
        old_path = self.base / "contacts.hr.csv"
        legacy = {key: self.row[key] for key in hunter.CSV_FIELDS}
        legacy.update(prenom="Contact déjà relu", nom="Nom déjà corrigé", poste="Responsable BI local",
                      linkedin="https://www.linkedin.com/in/profil-export-relu/", confiance="92")
        with old_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=hunter.CSV_FIELDS)
            writer.writeheader()
            writer.writerow(legacy)
        original_export = old_path.read_bytes()
        fresh = {**self.row, "public_sources": [{"url": "https://example.fr/source-recente",
                                                "last_seen_on": "2026-10-09"}],
                 "verification_date": "2026-10-09"}
        result = self.search(self.result([fresh]), force=True)
        contact = result["contacts"][0]
        self.assertEqual("manual", contact["source"])
        for key in hunter.CSV_FIELDS:
            with self.subTest(key=key):
                self.assertEqual(legacy[key], contact[key])
        self.assertEqual(fresh["public_sources"][0]["url"], contact["public_sources"][0]["url"])
        self.assertEqual(fresh["verification_date"], contact["verification_date"])
        self.assertEqual(original_export, old_path.read_bytes())
        restarted = WorkflowService(self.base).list_contacts()
        self.assertEqual(1, len(restarted))
        self.assertEqual(legacy["prenom"], restarted[0]["prenom"])
        self.assertEqual(contact["public_sources"], restarted[0]["public_sources"])

    def test_csv_correction_is_preserved_when_another_contact_is_added_before_refresh(self):
        self.search()
        with self.csv_path.open(encoding="utf-8", newline="") as handle:
            corrected = next(csv.DictReader(handle))
        corrected.update(prenom="Prénom modifié dans l'export", poste="Responsable recrutement corrigé",
                         linkedin="https://www.linkedin.com/in/correction-avant-ajout/")
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=hunter.CSV_FIELDS)
            writer.writeheader()
            writer.writerow(corrected)
        self.service.upsert_contact({"entreprise": "Entreprise test", "email": "autre@example.fr",
                                     "prenom": "Autre contact", "poste": "Responsable Data"})
        result = self.search(force=True)
        contact = result["contacts"][0]
        self.assertEqual("manual", contact["source"])
        for key in ("prenom", "poste", "linkedin"):
            self.assertEqual(corrected[key], contact[key])
        restarted = WorkflowService(self.base).list_contacts()
        self.assertEqual(2, len(restarted))
        corrected_contact = next(row for row in restarted if row["email"] == self.row["email"])
        self.assertEqual(corrected["prenom"], corrected_contact["prenom"])
        self.assertEqual("manual", corrected_contact["source"])


if __name__ == "__main__":
    unittest.main()
