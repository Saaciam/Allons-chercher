"""Offline tests for public-only, bounded contact lookup and persistent cache."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import contact_sources as discovery


def person(number=1, **values):
    row = {
        "value": f"contact{number}@example.fr", "first_name": f"Camille{number}",
        "last_name": "Test", "position": "Recruteur", "department": "hr", "confidence": 90,
        "linkedin": f"https://www.linkedin.com/in/camille-test-{number}/",
        "sources": [{"uri": f"https://example.fr/equipe/{number}", "last_seen_on": "2026-10-01", "still_on_page": True}],
        "verification": {"status": "valid", "date": "2026-10-02"},
    }
    row.update(values)
    return row


def response(rows=(), domain="example.fr", total=None, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = {"data": {"domain": domain, "emails": list(rows)}, "meta": {"results": len(rows) if total is None else total}}
    return result


class ContactSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.service = discovery.ContactDiscovery(self.base)

    def search(self, **values):
        return self.service.search("Entreprise de test", api_key="unit-secret", **values)

    def test_three_hr_contacts_stop_after_one_bounded_query_and_keep_metadata(self):
        with patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])) as get:
            result = self.search()
        self.assertEqual(1, result["request_count"])
        self.assertEqual(3, len(result["contacts"]))
        self.assertEqual("example.fr", result["resolved_domain"])
        self.assertEqual("hr", get.call_args.kwargs["params"]["department"])
        self.assertEqual(10, get.call_args.kwargs["params"]["limit"])
        self.assertEqual(0, get.call_args.kwargs["params"]["offset"])
        self.assertNotIn("api_key", get.call_args.kwargs["params"])
        self.assertEqual({"X-API-KEY": "unit-secret"}, get.call_args.kwargs["headers"])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual("valid", result["contacts"][0]["verification_status"])
        self.assertEqual("2026-10-02", result["contacts"][0]["verification_date"])
        self.assertEqual("2026-10-01", result["contacts"][0]["public_sources"][0]["last_seen_on"])
        self.assertIn("RH ou recrutement", result["contacts"][0]["priority_reasons"])
        self.assertNotIn("unit-secret", self.service.cache_path.read_text(encoding="utf-8"))

    def test_no_hr_results_uses_secondary_then_all_with_maximum_three_requests(self):
        responses = [response([]), response([person(1, position="Data Scientist", department="research")]), response([person(2, position="Consultant", department="consulting")])]
        with patch("contact_sources.requests.get", side_effect=responses) as get:
            result = self.search()
        self.assertEqual(3, get.call_count)
        self.assertEqual(2, len(result["contacts"]))
        self.assertEqual(["hr", discovery.SECONDARY_DEPARTMENTS, None], [call.kwargs["params"].get("department") for call in get.call_args_list])
        self.assertEqual("Entreprise de test", get.call_args_list[0].kwargs["params"]["company"])
        self.assertEqual("example.fr", get.call_args_list[1].kwargs["params"]["domain"])
        self.assertNotIn("company", get.call_args_list[1].kwargs["params"])

    def test_secondary_search_stops_when_three_usable_contacts_are_available(self):
        with patch("contact_sources.requests.get", side_effect=[response([person(1)]), response([person(2), person(3)])]) as get:
            result = self.search()
        self.assertEqual(2, get.call_count)
        self.assertEqual(3, len(result["contacts"]))

    def test_only_public_active_sources_are_retained_and_guesses_are_rejected(self):
        bad_sources = [None, [], [{"uri": "javascript:alert(1)"}], [{"uri": "https://example.fr/equipe", "still_on_page": False}],
                       [{"uri": "https://user:secret@example.fr/equipe"}], [{"uri": "http://127.0.0.1/internal"}],
                       [{"uri": "https://example.fr/equipe", "source_type": "inferred"}]]
        for sources in bad_sources:
            with self.subTest(sources=sources):
                self.assertIsNone(discovery.normalize_contact(person(sources=sources), "Entreprise"))
        for source_type in ("inferred", "generated", "guessed", "predicted", "deduced"):
            with self.subTest(source_type=source_type):
                self.assertIsNone(discovery.normalize_contact(person(source_type=source_type), "Entreprise"))
        mixed = person(sources=[{"uri": "https://example.fr/ancien", "still_on_page": False},
                               {"uri": "https://example.fr/equipe", "still_on_page": True},
                               {"uri": "https://example.fr/equipe", "still_on_page": True}])
        contact = discovery.normalize_contact(mixed, "Entreprise")
        self.assertEqual(["https://example.fr/equipe"], [source["url"] for source in contact["public_sources"]])
        self.assertEqual("", contact["public_sources"][0]["last_seen_on"])

    def test_rejected_rows_do_not_count_towards_fallback_threshold(self):
        with patch("contact_sources.requests.get", side_effect=[response([person(i, sources=[]) for i in range(10)]), response([]), response([person(25)])]) as get:
            result = self.search()
        self.assertEqual(3, get.call_count)
        self.assertEqual(["contact25@example.fr"], [row["email"] for row in result["contacts"]])

    def test_deduplication_is_case_insensitive_and_combines_source_links(self):
        one = person(1, value="CAMILLE@example.fr")
        duplicate = person(1, value="camille@example.fr", sources=[{"uri": "https://example.fr/publication", "still_on_page": True}])
        with patch("contact_sources.requests.get", side_effect=[response([one]), response([duplicate, person(2)]), response([])]):
            result = self.search()
        self.assertEqual(2, len(result["contacts"]))
        row = next(row for row in result["contacts"] if row["email"] == "camille@example.fr")
        self.assertEqual(2, len(row["public_sources"]))

    def test_ranking_prefers_hr_then_data_responsibles_then_other_collaborators(self):
        raw = [person(1, position="Commercial", department="sales", confidence=100),
               person(2, position="Responsable Data", department="it", confidence=80),
               person(3, position="Talent Acquisition", department="hr", confidence=65),
               person(4, position="Responsable Data", department="it", first_name="", last_name="", confidence=99)]
        contacts = discovery.sort_contacts([discovery.normalize_contact(row, "Entreprise") for row in raw])
        self.assertEqual([3, 2, 4, 1], [int(row["email"].split("@")[0][7:]) for row in contacts])
        self.assertEqual([0, 1, 1, 2], [row["priority"] for row in contacts])

    def test_verification_and_confidence_order_equal_roles_and_names(self):
        raw = [person(1, confidence=99, verification={"status": "unknown"}), person(2, confidence=70), person(3, confidence=90)]
        contacts = discovery.sort_contacts([discovery.normalize_contact(row, "Entreprise") for row in raw])
        self.assertEqual(["contact3@example.fr", "contact2@example.fr", "contact1@example.fr"], [row["email"] for row in contacts])

    def test_display_limit_and_provider_truncation_are_reported(self):
        with patch("contact_sources.requests.get", side_effect=[response([person(1), person(2)]), response([person(i) for i in range(3, 13)], total=100)]) as get:
            result = self.search()
        self.assertEqual(2, get.call_count)
        self.assertEqual(10, len(result["contacts"]))
        self.assertTrue(result["truncated"])

    def test_success_cache_is_persistent_for_twenty_four_hours(self):
        rows = [person(i) for i in range(3)]
        with patch("contact_sources.time.time", return_value=1000), patch("contact_sources.requests.get", return_value=response(rows)) as get:
            first = self.search()
            second = discovery.ContactDiscovery(self.base).search("Entreprise de test", api_key="unit-secret")
        self.assertEqual(1, get.call_count)
        self.assertFalse(first["from_cache"])
        self.assertTrue(second["from_cache"])
        self.assertFalse(second["cache_stale"])
        self.assertEqual(0, second["request_count"])
        state = json.loads(self.service.cache_path.read_text(encoding="utf-8"))
        entry = next(iter(state["entries"].values()))
        self.assertEqual(24 * 60 * 60, entry["expires_at"] - entry["checked_at"])

    def test_empty_results_are_cached_for_thirty_minutes(self):
        with patch("contact_sources.time.time", return_value=1000), patch("contact_sources.requests.get", return_value=response([])) as get:
            result = self.search()
        self.assertEqual(3, get.call_count)
        self.assertEqual([], result["contacts"])
        self.assertTrue(result["warnings"])
        state = json.loads(self.service.cache_path.read_text(encoding="utf-8"))
        entry = next(iter(state["entries"].values()))
        self.assertEqual(30 * 60, entry["expires_at"] - entry["checked_at"])
        with patch("contact_sources.time.time", return_value=2799), patch("contact_sources.requests.get") as get:
            cached = self.search()
        get.assert_not_called()
        self.assertTrue(cached["from_cache"])
        with patch("contact_sources.time.time", return_value=2801), patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])) as get:
            renewed = self.search()
        self.assertEqual(1, get.call_count)
        self.assertFalse(renewed["from_cache"])

    def test_explicit_refresh_bypasses_success_cache(self):
        with patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])) as get:
            self.search()
            refreshed = self.search(force=True)
        self.assertEqual(2, get.call_count)
        self.assertFalse(refreshed["from_cache"])

    def test_empty_refresh_preserves_contacts_after_restart_without_extending_freshness(self):
        with patch("contact_sources.time.time", return_value=1000), patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])):
            initial = self.search()
        with patch("contact_sources.time.time", return_value=1100), patch("contact_sources.requests.get", return_value=response([])) as get:
            refreshed = self.search(force=True)
        self.assertEqual(3, get.call_count)
        self.assertEqual(initial["contacts"], refreshed["contacts"])
        self.assertEqual(initial["checked_at"], refreshed["checked_at"])
        self.assertEqual(initial["expires_at"], refreshed["expires_at"])
        self.assertTrue(refreshed["from_cache"])
        self.assertTrue(refreshed["cache_stale"])
        self.assertIn("contacts précédents", " ".join(refreshed["warnings"]))
        state = json.loads(self.service.cache_path.read_text(encoding="utf-8"))
        entry = next(iter(state["entries"].values()))
        self.assertEqual(30 * 60, entry["expires_at"] - entry["checked_at"])
        with patch("contact_sources.time.time", return_value=2899), patch("contact_sources.requests.get") as get:
            restarted = discovery.ContactDiscovery(self.base).search("Entreprise de test", api_key="unit-secret")
        get.assert_not_called()
        self.assertEqual(initial["contacts"], restarted["contacts"])
        self.assertEqual(initial["checked_at"], restarted["checked_at"])
        self.assertEqual(initial["expires_at"], restarted["expires_at"])
        self.assertTrue(restarted["cache_stale"])
        with patch("contact_sources.time.time", return_value=2901), patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3, 6)])) as get:
            new_contacts = discovery.ContactDiscovery(self.base).search("Entreprise de test", api_key="unit-secret")
        self.assertEqual(1, get.call_count)
        self.assertFalse(new_contacts["cache_stale"])
        self.assertNotEqual(initial["contacts"], new_contacts["contacts"])

    def test_domain_override_is_normalized_and_separates_cache_entries(self):
        with patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])) as get:
            self.search(domain="https://www.example.fr/carrieres")
            again = self.search(domain="example.fr")
            self.search()
        self.assertEqual(2, get.call_count)
        self.assertEqual("example.fr", get.call_args_list[0].kwargs["params"]["domain"])
        self.assertTrue(again["from_cache"])

    def test_domain_shaped_company_is_sent_as_a_domain_without_guessing_from_name(self):
        for company in ("example.fr", "https://www.example.fr/carrieres"):
            with self.subTest(company=company), patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])) as get:
                result = self.service.search(company, api_key="unit-secret")
                self.assertEqual("example.fr", get.call_args.kwargs["params"]["domain"])
                self.assertNotIn("company", get.call_args.kwargs["params"])
                self.assertEqual(company, result["company"])

    def test_cache_path_and_temporary_file_are_checked_for_workspace_confinement(self):
        target = self.base / "data" / "contact_search.json"
        with patch.object(Path, "resolve", return_value=self.base.parent / "outside.json"):
            with self.assertRaisesRegex(ValueError, "dossier du projet"):
                self.service._read_cache()
        self.assertFalse(target.exists())

    def test_missing_key_does_not_call_provider_or_cache_a_failure(self):
        with patch("contact_sources.requests.get") as get:
            result = self.service.search("Entreprise de test")
        get.assert_not_called()
        self.assertFalse(self.service.cache_path.exists())
        self.assertEqual(0, result["request_count"])
        self.assertIn("HUNTER_API_KEY", " ".join(result["warnings"]))
        self.assertEqual(3, len(result["queries"]))

    def test_quota_failure_preserves_previous_cache_and_returns_its_contacts(self):
        with patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])):
            old = self.search()
        previous = self.service.cache_path.read_bytes()
        with patch("contact_sources.requests.get", return_value=response(status=429)) as get:
            result = self.search(force=True)
        self.assertEqual(1, get.call_count)
        self.assertEqual(previous, self.service.cache_path.read_bytes())
        self.assertEqual(old["contacts"], result["contacts"])
        self.assertTrue(result["from_cache"])
        self.assertTrue(result["cache_stale"])
        self.assertIn("quota", " ".join(result["warnings"]))

    def test_expired_cache_remains_available_on_failure_and_without_key(self):
        with patch("contact_sources.time.time", return_value=1000), patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])):
            self.search()
        with patch("contact_sources.time.time", return_value=100000), patch("contact_sources.requests.get", side_effect=requests.Timeout("unit-secret in URL")):
            failed = self.search()
            no_key = self.service.search("Entreprise de test")
        self.assertEqual(3, len(failed["contacts"]))
        self.assertEqual(3, len(no_key["contacts"]))
        self.assertTrue(failed["cache_stale"])
        self.assertNotIn("unit-secret", json.dumps(failed))

    def test_partial_failure_keeps_new_and_cached_contacts_without_overwriting_cache(self):
        with patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])):
            self.search()
        previous = self.service.cache_path.read_bytes()
        with patch("contact_sources.requests.get", side_effect=[response([person(20)]), response(status=429)]):
            result = self.search(force=True)
        self.assertEqual(4, len(result["contacts"]))
        self.assertIn("contact20@example.fr", [row["email"] for row in result["contacts"]])
        self.assertEqual(previous, self.service.cache_path.read_bytes())

    def test_first_partial_failure_returns_new_usable_results_without_negative_cache(self):
        with patch("contact_sources.requests.get", side_effect=[response([person(20)]), requests.ConnectionError("secret-url")]):
            result = self.search()
        self.assertEqual(1, len(result["contacts"]))
        self.assertEqual(2, result["request_count"])
        self.assertFalse(self.service.cache_path.exists())
        self.assertTrue(result["warnings"])
        self.assertNotIn("secret-url", json.dumps(result))

    def test_http_errors_are_sanitized_and_stop_further_queries(self):
        for status in (401, 403, 429, 500, 302):
            with self.subTest(status=status), patch("contact_sources.requests.get", return_value=response(status=status)) as get:
                result = self.search()
                self.assertEqual(1, get.call_count)
                self.assertTrue(result["warnings"])
                self.assertFalse(self.service.cache_path.exists())
                self.assertNotIn("unit-secret", json.dumps(result))

    def test_invalid_provider_payload_is_sanitized(self):
        invalid = response()
        invalid.json.side_effect = ValueError("unit-secret")
        with patch("contact_sources.requests.get", return_value=invalid):
            result = self.search()
        self.assertEqual(1, result["request_count"])
        self.assertIn("invalide", " ".join(result["warnings"]))
        self.assertNotIn("unit-secret", json.dumps(result))

    def test_concurrent_instances_share_single_flight_and_read_new_cache(self):
        def delayed(*args, **kwargs):
            time.sleep(0.03)
            return response([person(i) for i in range(3)])

        with patch("contact_sources.requests.get", side_effect=delayed) as get:
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda _: discovery.ContactDiscovery(self.base).search("Entreprise de test", api_key="unit-secret"), range(5)))
        self.assertEqual(1, get.call_count)
        self.assertEqual(4, sum(result["from_cache"] for result in results))

    def test_corrupt_cache_does_not_prevent_discovery(self):
        self.service.cache_path.parent.mkdir(parents=True)
        self.service.cache_path.write_text("{corrupt", encoding="utf-8")
        with patch("contact_sources.requests.get", return_value=response([person(i) for i in range(3)])):
            result = self.search()
        self.assertEqual(3, len(result["contacts"]))
        self.assertEqual(1, json.loads(self.service.cache_path.read_text(encoding="utf-8"))["schema_version"])

    def test_input_validation_rejects_invalid_domains_flags_and_control_characters(self):
        for values in ({"company": ""}, {"company": "Entreprise\nInjected"}, {"company": "Entreprise", "force": "true"},
                       {"company": "Entreprise", "department": "arbitrary"}, {"company": "Entreprise", "domain": "http://user:pass@example.fr"},
                       {"company": "Entreprise", "domain": "localhost"}, {"company": "Entreprise", "domain": "127.0.0.1"}):
            with self.subTest(values=values), patch("contact_sources.requests.get") as get:
                with self.assertRaises(ValueError):
                    self.service.search(**values)
                get.assert_not_called()

    def test_normalization_preserves_legacy_keys_and_safe_profile_links_only(self):
        raw = person(confidence=float("nan"), linkedin="https://linkedin.com.evil.fr/in/camille", verification={"date": "invalid", "status": "unknown"})
        row = discovery.normalize_contact(raw, "Entreprise", "example.fr")
        self.assertTrue(set(("entreprise", "prenom", "nom", "poste", "email", "confiance", "linkedin")).issubset(row))
        self.assertEqual("", row["linkedin"])
        self.assertEqual("0", row["confiance"])
        self.assertEqual("", row["verification_date"])
        self.assertEqual(row, discovery.normalize_contact(deepcopy(row), "Entreprise", "example.fr"))

    def test_safe_source_links_and_queries_use_explicit_public_urls(self):
        for value in ("file:///secret", "javascript:alert(1)", "https://user:pass@example.fr", "http://localhost/x", "http://192.168.1.1/x", "https://example.fr/%0aBcc:test", "https://example.fr\\evil.fr/x"):
            with self.subTest(value=value):
                self.assertEqual("", discovery.safe_url(value))
        queries = discovery.search_queries("Entreprise & fils", "example.fr")
        self.assertEqual(3, len(queries))
        self.assertTrue(all(query["url"].startswith("https://www.google.com/search?q=") for query in queries))
        self.assertIn("%26", queries[0]["url"])
        self.assertEqual("linkedin", queries[0]["kind"])


if __name__ == "__main__":
    unittest.main()
