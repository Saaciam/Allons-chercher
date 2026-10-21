"""Dashboard persistence and HTTP checks with all remote integrations mocked."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import select
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from dashboard_store import DashboardStore
from job_sources import match_offer
from web_app import AppServer, Dashboard, criteria


def offer(offer_id="test-offer", *, title="Stage Data Python", location="Rennes"):
    return {"id": offer_id, "entreprise": "Entreprise de test", "intitule": title,
            "description": "Stage en analyse de données : Python et SQL.",
            "lieu": location, "contract": "stage", "date_debut": "2027-03-01", "duree_mois": 6,
            "source": "manuel", "country": "France"}


def alert_values(**changes):
    return {"name": "Data à Rennes", "keywords": "data", "location": "Rennes", "radius_km": 30,
            "contract": "stage", "enabled": True, "remote": False, **changes}


class DashboardStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "data" / "dashboard.json"
        self.store = DashboardStore(self.path)

    def test_preferences_alerts_and_read_notifications_survive_restart(self):
        preferences = {"location": "Rennes", "city_code": "35238", "latitude": 48.11,
                       "longitude": -1.68, "radius_km": 50}
        self.store.preferences(preferences)
        alert = self.store.upsert_alert(alert_values())
        self.store.add_offers([offer()], match_offer)
        self.store.mark_read()
        restarted = DashboardStore(self.path).snapshot()
        self.assertEqual(preferences, restarted["preferences"])
        self.assertEqual(alert["id"], restarted["alerts"][0]["id"])
        self.assertEqual("test-offer", restarted["notifications"][0]["offer_id"])
        self.assertTrue(restarted["notifications"][0]["read"])

    def test_notifications_match_contract_city_and_keywords_without_duplicates(self):
        self.store.upsert_alert(alert_values())
        batch = [offer(), offer("wrong-city", location="Paris"),
                 {**offer("wrong-contract"), "contract": "cdi", "intitule": "Data Analyst"},
                 {**offer("wrong-role"), "intitule": "Stage comptabilité", "description": "Gestion comptable"}]
        first = self.store.add_offers(batch, match_offer)
        second = self.store.add_offers(batch, match_offer)
        self.assertEqual({"added_count": 4, "new_count": 1}, first)
        self.assertEqual({"added_count": 0, "new_count": 0}, second)
        self.assertEqual(1, len(self.store.snapshot()["notifications"]))

    def test_paused_alert_stops_new_notifications_and_can_resume(self):
        alert = self.store.upsert_alert(alert_values(enabled=False))
        self.assertEqual(0, self.store.add_offers([offer()], match_offer)["new_count"])
        self.store.upsert_alert(alert_values(enabled=True), alert["id"])
        self.assertEqual(1, self.store.add_offers(self.store.snapshot()["offers"], match_offer)["new_count"])
        self.assertEqual(0, self.store.add_offers(self.store.snapshot()["offers"], match_offer)["new_count"])

    def test_snapshot_is_isolated_from_persistent_state(self):
        self.store.upsert_alert(alert_values())
        snapshot = self.store.snapshot()
        snapshot["alerts"][0]["keywords"] = "modification accidentelle"
        self.assertEqual("data", self.store.snapshot()["alerts"][0]["keywords"])

    def test_bad_state_json_schema_is_rejected(self):
        self.path.parent.mkdir(parents=True)
        for data in ([], {"schema_version": 99}):
            self.path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(data=data), self.assertRaises(ValueError):
                DashboardStore(self.path)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        shutil.copyfile(PROJECT_DIR / "candidate_profile.json", self.base / "candidate_profile.json")
        self.search = Mock(return_value={"offers": [], "warnings": []})
        self.dashboard = Dashboard(self.base, token="test-token", search_provider=self.search)
        for name in ("job_sources.requests.get", "job_sources.requests.post", "workflow_service.smtplib.SMTP"):
            remote = patch(name, side_effect=AssertionError("Unexpected external request"))
            remote.start()
            self.addCleanup(remote.stop)

    def create_alert(self, **changes):
        return self.dashboard.post("/api/alerts", alert_values(**changes))["alert"]

    def test_import_offers_triggers_matching_notifications_without_remote_services(self):
        self.create_alert()
        result = self.dashboard.post("/api/offers", {"offers": [offer()]})
        repeated = self.dashboard.post("/api/offers", {"offers": [offer()]})
        self.assertEqual(1, result["new_count"])
        self.assertEqual(0, repeated["new_count"])
        self.assertEqual(1, len(self.dashboard.state()["notifications"]))
        self.search.assert_not_called()

    def test_existing_import_can_notify_an_alert_created_later(self):
        self.dashboard.post("/api/offers", {"offers": [offer()]})
        self.create_alert()
        result = self.dashboard.check_alerts()
        self.assertEqual(1, result["new_count"])
        self.search.assert_called_once()

    def test_background_checks_wait_thirty_minutes_and_skip_paused_alerts(self):
        alert = self.create_alert()
        self.dashboard.check_alerts(force=False)
        self.dashboard.check_alerts(force=False)
        self.assertEqual(1, self.search.call_count)
        self.dashboard.store.data["alerts"][0]["last_checked_at"] = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        self.dashboard.check_alerts(force=False)
        self.assertEqual(2, self.search.call_count)
        self.dashboard.post(f"/api/alerts/{alert['id']}", {"enabled": False})
        self.dashboard.check_alerts(force=True)
        self.assertEqual(2, self.search.call_count)

    def test_failed_provider_does_not_invent_offers_or_expose_credentials(self):
        self.create_alert()
        self.search.side_effect = RuntimeError("https://private.invalid/?token=secret-credential")
        result = self.dashboard.check_alerts()
        state = self.dashboard.store.snapshot()
        self.assertEqual([], state["offers"])
        self.assertTrue(result["warnings"])
        self.assertNotIn("secret-credential", json.dumps(result))
        self.assertIn("indisponible", state["alerts"][0]["last_error"])

    def test_profile_edit_preserves_verified_skills_and_marks_documents_stale(self):
        prepared = self.dashboard.workflow.prepare(offer())
        original = deepcopy(self.dashboard.state()["profile"])
        self.dashboard.post("/api/profile", {"identity": {"phone": "+33 6 00 00 00 00"}})
        updated = self.dashboard.state()["profile"]
        self.assertEqual(original["skills"], updated["skills"])
        self.assertEqual(original["projects"], updated["projects"])
        self.assertEqual("+33 6 00 00 00 00", updated["identity"]["phone"])
        self.assertTrue(self.dashboard.workflow.load_application(prepared["id"])["stale"])
        with patch("workflow_service.send_emails.load_dotenv") as dotenv:
            with self.assertRaises(ValueError):
                self.dashboard.workflow.send(prepared["id"], {"email": "test@example.fr"})
        dotenv.assert_not_called()

    def test_changed_profile_email_keeps_application_visible_and_regenerable(self):
        prepared = self.dashboard.workflow.prepare(offer())
        self.dashboard.post("/api/profile", {"identity": {"email": "new-address@example.fr"}})
        applications = self.dashboard.state()["applications"]
        self.assertEqual(prepared["id"], applications[0]["id"])
        self.assertTrue(applications[0]["stale"])
        regenerated = self.dashboard.post(f"/api/applications/{prepared['id']}/regenerate", {'confirmed': True})["application"]
        self.assertFalse(regenerated["stale"])

    def test_invalid_profile_update_keeps_original_file(self):
        before = (self.base / "candidate_profile.json").read_bytes()
        with self.assertRaises(ValueError):
            self.dashboard.post("/api/profile", {"internship": {"duration_months": 0}})
        self.assertEqual(before, (self.base / "candidate_profile.json").read_bytes())

    def test_email_confirmation_is_required_and_duplicate_send_is_blocked(self):
        body = {"recipient": {"email": "recruitment@example.fr", "prenom": "Camille"}}
        result = {"message": "Envoi simulé", "recipient": body["recipient"]}
        with patch.object(self.dashboard.workflow, "send", return_value=result) as send:
            with self.assertRaisesRegex(ValueError, "confirme"):
                self.dashboard.post("/api/applications/test-offer/send", body)
            send.assert_not_called()
            self.dashboard.post("/api/applications/test-offer/send", {**body, "confirmed": True})
            with self.assertRaisesRegex(ValueError, "déjà"):
                self.dashboard.post("/api/applications/test-offer/send", {**body, "confirmed": True})
        send.assert_called_once()
        restarted = Dashboard(self.base, token="other-token", search_provider=self.search)
        self.assertEqual("sent", restarted.store.snapshot()["application_statuses"]["test-offer"]["status"])

    def test_failed_send_releases_in_progress_marker_and_does_not_mark_sent(self):
        body = {"recipient": {"email": "recruitment@example.fr"}, "confirmed": True}
        with patch.object(self.dashboard.workflow, "send", side_effect=RuntimeError("échec simulé")):
            with self.assertRaises(RuntimeError):
                self.dashboard.post("/api/applications/test-offer/send", body)
        self.assertEqual(set(), self.dashboard.sending)
        self.assertNotIn("test-offer", self.dashboard.store.snapshot()["application_statuses"])

    def test_json_form_and_search_criteria_are_validated(self):
        for invalid in ([], "texte", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.dashboard.post("/api/search", invalid)
        for invalid in ({"keywords": ""}, {"keywords": "data", "radius_km": 301},
                        {"keywords": "data", "latitude": float("nan"), "longitude": 0},
                        {"keywords": "data", "remote": "oui"}, {"keywords": "data", "source": "inconnu"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                criteria(invalid)
        self.search.assert_not_called()

    def test_search_explains_verified_skills_and_reports_unverified_tools_and_conditions(self):
        candidate_offer = {**offer(), "description": "Python et SQL pour des projets LLM et LangChain.",
                           "date_debut": "2027-01-01", "duree_mois": 3,
                           "matching_skills": ["LangChain", "Compétence inventée"]}
        self.search.return_value = {"offers": [candidate_offer], "warnings": []}
        result = self.dashboard.post("/api/search", alert_values())
        personalized = result["offers"][0]
        self.assertIn("Python", personalized["matching_skills"])
        self.assertIn("SQL", personalized["matching_skills"])
        self.assertNotIn("LangChain", personalized["matching_skills"])
        self.assertNotIn("Compétence inventée", personalized["matching_skills"])
        self.assertIn("LangChain", personalized["unverified_technologies"])
        self.assertTrue(any("LLM" in tool for tool in personalized["unverified_technologies"]))
        notes = " ".join(personalized["profile_notes"])
        self.assertIn("mars 2027", notes)
        self.assertIn("3 mois", notes)
        self.assertIn("6 mois", notes)
        persisted = self.dashboard.state()["offers"][0]
        self.assertEqual(personalized["matching_skills"], persisted["matching_skills"])

    def test_last_search_persists_current_result_ids_and_source_outcome_separately_from_saved_offers(self):
        self.dashboard.post("/api/offers", {"offers": [offer("older-import")]})
        source_status = [{"id": "smartrecruiters", "label": "Source de test", "status": "ok", "count": 1}]
        self.search.return_value = {"offers": [offer("current-search")], "warnings": ["Information de test"],
                                    "source_status": source_status}
        values = alert_values(source="smartrecruiters")
        self.dashboard.post("/api/search", values)
        last = self.dashboard.state()["last_search"]
        self.assertEqual(["current-search"], last["offer_ids"])
        self.assertEqual("smartrecruiters", last["criteria"]["source"])
        self.assertEqual("data", last["criteria"]["keywords"])
        self.assertEqual(["Information de test"], last["warnings"])
        self.assertEqual(source_status, last["source_status"])
        self.assertIsNotNone(datetime.fromisoformat(last["searched_at"]).tzinfo)
        restarted = Dashboard(self.base, search_provider=self.search).state()
        self.assertEqual(last, restarted["last_search"])
        self.assertEqual({"current-search", "older-import"}, {entry["id"] for entry in restarted["offers"]})

    def test_empty_search_clears_latest_result_ids_while_saved_offers_remain(self):
        self.search.return_value = {"offers": [offer()], "warnings": [], "source_status": []}
        self.dashboard.post("/api/search", alert_values())
        self.search.return_value = {"offers": [], "warnings": ["Aucune correspondance"], "source_status": []}
        self.dashboard.post("/api/search", alert_values(keywords="autre métier"))
        state = self.dashboard.state()
        self.assertEqual([], state["last_search"]["offer_ids"])
        self.assertEqual("autre métier", state["last_search"]["criteria"]["keywords"])
        self.assertEqual(["test-offer"], [entry["id"] for entry in state["offers"]])

    def test_alert_scan_does_not_replace_last_manual_search(self):
        self.search.return_value = {"offers": [offer("manual-result")], "warnings": ["Note manuelle"],
                                    "source_status": [{"id": "smartrecruiters", "status": "ok", "count": 1}]}
        self.dashboard.post("/api/search", alert_values())
        saved_search = deepcopy(self.dashboard.state()["last_search"])
        self.create_alert()
        self.search.return_value = {"offers": [offer("alert-result")], "warnings": [],
                                    "source_status": [{"id": "france-travail", "status": "ok", "count": 1}]}
        self.dashboard.check_alerts()
        state = self.dashboard.state()
        self.assertEqual(saved_search, state["last_search"])
        self.assertIn("alert-result", {entry["id"] for entry in state["offers"]})

    def test_search_preferences_preserve_job_contract_remote_and_source_after_restart(self):
        values = alert_values(keywords="Python, SQL", contract="alternance", remote=True,
                              source="smartrecruiters", radius_km=45)
        self.dashboard.post("/api/preferences", values)
        restored = Dashboard(self.base, search_provider=self.search).state()["preferences"]
        for key in ("keywords", "location", "contract", "remote", "source", "radius_km"):
            self.assertEqual(values[key], restored[key])


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        shutil.copyfile(PROJECT_DIR / "candidate_profile.json", self.base / "candidate_profile.json")
        (self.base / "static").mkdir()
        (self.base / "static" / "index.html").write_text("<!doctype html><title>Interface de test</title>", encoding="utf-8")
        (self.base / ".env").write_text("DO_NOT_EXPOSE=test-secret-value\n", encoding="utf-8")
        self.search = Mock(return_value={"offers": [offer()], "warnings": []})
        self.dashboard = Dashboard(self.base, token="test-http-token", search_provider=self.search)
        for name in ("job_sources.requests.get", "job_sources.requests.post", "workflow_service.smtplib.SMTP"):
            remote = patch(name, side_effect=AssertionError("Unexpected external request"))
            remote.start()
            self.addCleanup(remote.stop)
        self.server = AppServer(("127.0.0.1", 0), self.dashboard)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def request(self, method, path, value=None, *, token=True, extra_headers=None, raw=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-CSRF-Token"] = self.dashboard.csrf_token
        headers.update(extra_headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        payload = raw if raw is not None else json.dumps(value).encode("utf-8") if value is not None else None
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_state_and_static_page_load_without_remote_actions(self):
        status, headers, data = self.request("GET", "/api/state")
        self.assertEqual(200, status)
        state = json.loads(data)
        self.assertEqual("test-http-token", state["csrf_token"])
        self.assertIn("profile", state)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertNotIn(b"test-secret-value", data)
        self.assertEqual(200, self.request("GET", "/")[0])
        self.search.assert_not_called()

    def test_mutations_reject_missing_csrf_hostile_origin_and_host(self):
        self.assertEqual(403, self.request("POST", "/api/alerts", alert_values(), token=False)[0])
        self.assertEqual(403, self.request("POST", "/api/alerts", alert_values(), extra_headers={"Origin": "https://evil.example"})[0])
        self.assertEqual(403, self.request("POST", "/api/alerts", alert_values(), extra_headers={"Host": "evil.example"})[0])
        self.assertEqual(403, self.request("GET", "/api/state", extra_headers={"Host": "evil.example"})[0])
        self.assertEqual([], self.dashboard.store.snapshot()["alerts"])

    def test_rejected_large_post_returns_403_without_reset_or_mutation(self):
        body = {"padding": "x" * 1_000_000}
        cases = ({"token": False}, {"extra_headers": {"Origin": "https://evil.example"}},
                 {"extra_headers": {"Host": "evil.example"}})
        for case in cases:
            with self.subTest(case=case):
                status, _, response = self.request("POST", "/api/alerts", body, **case)
                self.assertEqual(403, status)
                self.assertIn("error", json.loads(response))
        self.assertEqual([], self.dashboard.store.snapshot()["alerts"])
        self.search.assert_not_called()

    def test_rejection_waits_for_fragmented_body_then_returns_403(self):
        payload = json.dumps({"padding": "x" * 65536}).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.putrequest("POST", "/api/alerts")
            connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders()
            connection.send(payload[:1024])
            # No response/connection close while the remaining body is in flight.
            self.assertFalse(select.select([connection.sock], [], [], .05)[0])
            connection.send(payload[1024:])
            response = connection.getresponse()
            self.assertEqual(403, response.status)
            self.assertIn("error", json.loads(response.read()))
        finally:
            connection.close()
        self.assertEqual([], self.dashboard.store.snapshot()["alerts"])

    def test_rejection_drain_has_a_deadline_for_client_that_never_sends_body(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.putrequest("POST", "/api/alerts")
            connection.putheader("Content-Length", "1000000")
            connection.endheaders()
            started = time.monotonic()
            response = connection.getresponse()
            self.assertEqual(403, response.status)
            self.assertIn("error", json.loads(response.read()))
            self.assertLess(time.monotonic() - started, 2.5)
        finally:
            connection.close()
        self.assertEqual([], self.dashboard.store.snapshot()["alerts"])
        self.search.assert_not_called()

    def test_alert_preferences_and_search_flow_over_http(self):
        location = {"location": "Rennes", "city_code": "35238", "radius_km": 40,
                    "latitude": 48.11, "longitude": -1.68}
        self.assertEqual(200, self.request("POST", "/api/preferences", location)[0])
        self.assertEqual(200, self.request("POST", "/api/alerts", alert_values())[0])
        status, _, body = self.request("POST", "/api/search", alert_values())
        self.assertEqual(200, status)
        self.assertEqual("test-offer", json.loads(body)["offers"][0]["id"])
        restarted = DashboardStore(self.base / "data" / "dashboard.json").snapshot()
        self.assertEqual(40, restarted["preferences"]["radius_km"])
        self.assertEqual(1, len(restarted["notifications"]))

    def test_invalid_json_and_non_object_forms_return_400_without_search(self):
        self.assertEqual(400, self.request("POST", "/api/search", raw=b"{not valid json}")[0])
        self.assertEqual(400, self.request("POST", "/api/search", ["data"])[0])
        self.assertEqual(400, self.request("POST", "/api/search", {"keywords": ""})[0])
        self.assertEqual(400, self.request("POST", "/api/offers", {"offers": "wrong"})[0])
        self.search.assert_not_called()

    def test_location_services_are_invoked_only_by_location_endpoints(self):
        location = {"label": "Rennes (35)", "city_code": "35238", "latitude": 48.11, "longitude": -1.68}
        with patch("web_app.resolve_location", return_value=[location]) as resolve, patch("web_app.reverse_location", return_value=location) as reverse:
            status, _, body = self.request("GET", "/api/locations?q=Rennes")
            self.assertEqual(200, status)
            self.assertEqual(location, json.loads(body)["locations"][0])
            self.assertEqual(200, self.request("POST", "/api/location/reverse", {"latitude": 48.11, "longitude": -1.68})[0])
        resolve.assert_called_once_with("Rennes")
        reverse.assert_called_once_with(48.11, -1.68)

    def test_static_paths_cannot_expose_env_profile_or_traversed_files(self):
        for path in ("/.env", "/candidate_profile.json", "/../.env", "/%2e%2e/.env", "/%2e%2e/candidate_profile.json", "/api/applications/../../.env/files/cv"):
            status, _, body = self.request("GET", path)
            with self.subTest(path=path):
                self.assertEqual(404, status)
                self.assertNotIn(b"test-secret-value", body)

    def test_pdf_draft_letter_and_preview_endpoints_use_application_documents(self):
        self.request("POST", "/api/offers", {"offers": [offer()]})
        status, _, body = self.request("POST", "/api/offers/test-offer/prepare", {})
        self.assertEqual(200, status)
        app = json.loads(body)["application"]
        self.assertEqual("test-offer", app["id"])
        for kind in ("cv", "letter"):
            status, headers, pdf = self.request("GET", app["files"][kind])
            self.assertEqual(200, status)
            self.assertEqual("application/pdf", headers["Content-Type"])
            self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(200, self.request("POST", "/api/applications/test-offer/draft", {"subject": "Objet relu", "body": "Bonjour {prenom},\nMa candidature."})[0])
        self.assertEqual(200, self.request("POST", "/api/applications/test-offer/letter", {"text": "Madame, Monsieur,\n\nLettre relue."})[0])
        status, _, body = self.request("POST", "/api/applications/test-offer/preview", {"recipient": {"email": "test@example.fr", "prenom": "Camille"}})
        preview = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual("Objet relu", preview["subject"])
        self.assertIn("Bonjour Camille", preview["body"])
        self.assertIn("Lettre relue", preview["letter_text"])
        self.assertEqual(2, len(preview["attachments"]))
        self.search.assert_not_called()

    def test_hunter_and_email_require_explicit_http_actions(self):
        contact = {"entreprise": "Entreprise de test", "email": "test@example.fr", "prenom": "Camille"}
        with patch.object(self.dashboard.workflow, "search_contacts_detail", return_value={'contacts': [contact], 'warnings': [], 'queries': []}) as hunter, patch.object(self.dashboard.workflow, "send", return_value={"message": "Simulation réussie"}) as send:
            self.request("GET", "/api/state")
            hunter.assert_not_called()
            send.assert_not_called()
            self.assertEqual(200, self.request("POST", "/api/contacts/search", {"company": contact["entreprise"]})[0])
            self.assertEqual(400, self.request("POST", "/api/applications/test-offer/send", {"recipient": contact})[0])
            send.assert_not_called()
            self.assertEqual(200, self.request("POST", "/api/applications/test-offer/send", {"recipient": contact, "confirmed": True})[0])
            self.assertEqual(400, self.request("POST", "/api/applications/test-offer/send", {"recipient": contact, "confirmed": True})[0])
        hunter.assert_called_once_with(contact["entreprise"], "hr", domain=None, force=False)
        send.assert_called_once()

    def test_http_search_exposes_personalized_matches_and_latest_search_in_state(self):
        self.search.return_value = {"offers": [{**offer(), "description": "Python SQL et modèles LLM demandés."}],
                                    "warnings": [], "source_status": [{"id": "smartrecruiters", "status": "ok", "count": 1}]}
        status, _, body = self.request("POST", "/api/search", alert_values())
        result = json.loads(body)
        self.assertEqual(200, status)
        self.assertIn("Python", result["offers"][0]["matching_skills"])
        self.assertIn("SQL", result["offers"][0]["matching_skills"])
        self.assertTrue(any("LLM" in item for item in result["offers"][0]["unverified_technologies"]))
        status, _, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual(["test-offer"], state["last_search"]["offer_ids"])
        self.assertEqual(result["source_status"], state["last_search"]["source_status"])

    def test_repeated_prepare_keeps_edited_documents_and_returns_reused_over_http(self):
        self.request('POST', '/api/offers', {'offers': [offer()]})
        status, _, body = self.request('POST', '/api/offers/test-offer/prepare', {})
        self.assertEqual(200, status)
        self.assertFalse(json.loads(body)['application']['reused'])
        self.request('POST', '/api/applications/test-offer/draft', {'subject': 'Mon objet corrigé', 'body': 'Mon message relu.'})
        folder = self.base / 'candidatures' / 'test-offer'
        before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        status, _, body = self.request('POST', '/api/offers/test-offer/prepare', {})
        result = json.loads(body)
        self.assertEqual(200, status)
        self.assertTrue(result['application']['reused'])
        self.assertEqual('Mon objet corrigé', result['application']['subject'])
        self.assertEqual(before, {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()})

    def test_incomplete_application_returns_repair_details_and_needs_confirmed_regeneration(self):
        self.request('POST', '/api/offers', {'offers': [offer()]})
        self.request('POST', '/api/offers/test-offer/prepare', {})
        folder = self.base / 'candidatures' / 'test-offer'
        (folder / 'manifest.json').unlink()
        (folder / 'offre.json').unlink()
        before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        status, _, body = self.request('POST', '/api/offers/test-offer/prepare', {})
        problem = json.loads(body)
        self.assertEqual(409, status)
        self.assertEqual('application_incomplete', problem['code'])
        self.assertTrue(problem['can_regenerate'])
        self.assertEqual('test-offer', problem['application_id'])
        self.assertNotIn('--overwrite', problem['error'])
        self.assertNotIn(str(self.base), problem['error'])
        # The dashboard may still hold a card for this now-incomplete dossier.
        status, _, body = self.request('POST', '/api/applications/test-offer/preview', {})
        self.assertEqual(409, status)
        self.assertEqual('application_incomplete', json.loads(body)['code'])
        self.assertTrue(json.loads(body)['can_regenerate'])
        self.assertEqual(400, self.request('POST', '/api/applications/test-offer/regenerate', {})[0])
        self.assertEqual(before, {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()})
        status, _, body = self.request('POST', '/api/applications/test-offer/regenerate', {'confirmed': True})
        self.assertEqual(200, status)
        self.assertEqual('test-offer', json.loads(body)['application']['id'])

    def test_contact_search_preserves_rich_results_and_forwards_domain_and_refresh(self):
        contact = {'entreprise': 'Entreprise de test', 'prenom': 'Camille', 'nom': 'Test',
                   'email': 'camille@example.fr', 'poste': 'Responsable recrutement', 'source': 'hunter',
                   'linkedin': 'https://www.linkedin.com/in/camille-test', 'department': 'hr',
                   'verification_status': 'valid', 'verification_date': '2026-10-09',
                   'public_sources': [{'url': 'https://example.fr/equipe', 'last_seen_on': '2026-10-08', 'still_on_page': True}],
                   'priority': 0, 'priority_reasons': ['RH ou recrutement']}
        result = {'company': 'Entreprise de test', 'contacts': [contact], 'warnings': [],
                  'queries': [{'label': 'Profils', 'url': 'https://www.google.com/search?q=recrutement'}],
                  'from_cache': True, 'resolved_domain': 'example.fr', 'truncated': False}
        with patch('workflow_service.hunter.load_dotenv'), patch.dict('os.environ', {'HUNTER_API_KEY': 'private-fixture-key'}), \
                patch.object(self.dashboard.workflow.contact_discovery, 'search', return_value=result) as discover:
            status, _, body = self.request('POST', '/api/contacts/search',
                                           {'company': 'Entreprise de test', 'domain': 'example.fr', 'force': True})
        self.assertEqual(200, status)
        self.assertNotIn(b'private-fixture-key', body)
        response = json.loads(body)
        self.assertEqual('valid', response['contacts'][0]['verification_status'])
        self.assertEqual('https://example.fr/equipe', response['contacts'][0]['public_sources'][0]['url'])
        self.assertTrue(response['from_cache'])
        self.assertEqual('example.fr', response['resolved_domain'])
        discover.assert_called_once_with('Entreprise de test', 'hr', domain='example.fr', force=True, api_key='private-fixture-key')
        state = json.loads(self.request('GET', '/api/state')[2])
        self.assertEqual('Responsable recrutement', state['contacts'][0]['poste'])
        self.assertEqual('hr', state['contacts'][0]['department'])


if __name__ == "__main__":
    unittest.main()
