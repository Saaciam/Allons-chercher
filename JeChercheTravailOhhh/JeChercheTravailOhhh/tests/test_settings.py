"""Local source configuration: persist credentials without returning secrets."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from send_emails import load_dotenv
from web_app import AppServer, Dashboard


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        shutil.copyfile(PROJECT_DIR / "candidate_profile.json", self.base / "candidate_profile.json")
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.search = Mock(return_value={"offers": [], "warnings": []})
        self.dashboard = Dashboard(self.base, token="settings-test-token", search_provider=self.search)
        for name in ("job_sources.requests.get", "job_sources.requests.post", "workflow_service.smtplib.SMTP"):
            remote = patch(name, side_effect=AssertionError("Unexpected remote integration call"))
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

    def request(self, method="GET", value=None, *, path="/api/settings", token=True, headers=None):
        request_headers = {"Content-Type": "application/json"}
        if token:
            request_headers["X-CSRF-Token"] = self.dashboard.csrf_token
        request_headers.update(headers or {})
        payload = json.dumps(value).encode("utf-8") if value is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            status = response.status
            raw = response.read()
            return status, json.loads(raw)
        finally:
            connection.close()

    def configure(self, client_id="fake-private-client-id", secret="fake-private-client-secret"):
        status, result = self.request("POST", {"france_travail_client_id": client_id,
                                                "france_travail_client_secret": secret})
        self.assertEqual(200, status, result)
        return result

    def assert_secrets_hidden(self, result, *secrets):
        response = json.dumps(result, ensure_ascii=False)
        for secret in secrets:
            self.assertNotIn(secret, response)

    def test_settings_describe_real_sources_and_usable_initial_search_defaults(self):
        status, result = self.request()
        self.assertEqual(200, status, result)
        sources = {source["id"]: source for source in result["sources"]}
        self.assertTrue({"smartrecruiters", "arbeitnow", "france-travail"}.issubset(sources))
        for source in sources.values():
            self.assertIsInstance(source["label"], str)
            self.assertIsInstance(source["description"], str)
            self.assertIs(type(source["configured"]), bool)
            self.assertIs(type(source["requires_credentials"]), bool)
        self.assertTrue(sources["smartrecruiters"]["configured"])
        self.assertFalse(sources["smartrecruiters"]["requires_credentials"])
        self.assertFalse(sources["france-travail"]["configured"])
        self.assertTrue(sources["france-travail"]["requires_credentials"])
        self.assertFalse(result["france_travail"]["configured"])
        defaults = result["defaults"]
        self.assertTrue(defaults["keywords"].strip())
        self.assertTrue(defaults["location"].strip())
        self.assertEqual("stage", defaults["contract"])
        self.assertIs(type(defaults["radius_km"]), int)
        self.assertIs(type(defaults["remote"]), bool)
        self.assertIn(defaults["source"], {"all", "smartrecruiters", "arbeitnow", "france-travail"})
        self.search.assert_not_called()
        self.assertEqual([], self.dashboard.store.snapshot()["offers"])

    def test_saved_credentials_update_environment_and_survive_dotenv_reload(self):
        result = self.configure()
        self.assertTrue(result["france_travail"]["configured"])
        self.assertEqual("fake-private-client-id", os.environ["FRANCE_TRAVAIL_CLIENT_ID"])
        self.assertEqual("fake-private-client-secret", os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"])
        self.assert_secrets_hidden(result, "fake-private-client-id", "fake-private-client-secret")
        os.environ.pop("FRANCE_TRAVAIL_CLIENT_ID")
        os.environ.pop("FRANCE_TRAVAIL_CLIENT_SECRET")
        # Only this temporary .env is read; the project's real configuration is never loaded.
        load_dotenv(self.base / ".env")
        self.assertEqual("fake-private-client-id", os.environ["FRANCE_TRAVAIL_CLIENT_ID"])
        self.assertEqual("fake-private-client-secret", os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"])
        status, settings = self.request()
        self.assertEqual(200, status)
        self.assertTrue(settings["france_travail"]["configured"])

    def test_get_settings_and_state_never_include_saved_secrets(self):
        self.configure()
        for path in ("/api/settings", "/api/state"):
            status, data = self.request(path=path)
            self.assertEqual(200, status)
            self.assert_secrets_hidden(data, "fake-private-client-id", "fake-private-client-secret")

    def test_blank_fields_preserve_existing_credentials(self):
        self.configure()
        status, result = self.request("POST", {"france_travail_client_id": "", "france_travail_client_secret": ""})
        self.assertEqual(200, status, result)
        self.assertTrue(result["france_travail"]["configured"])
        self.assertEqual("fake-private-client-id", os.environ["FRANCE_TRAVAIL_CLIENT_ID"])
        self.assertEqual("fake-private-client-secret", os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"])
        self.assert_secrets_hidden(result, "fake-private-client-id", "fake-private-client-secret")

    def test_secret_can_be_updated_while_empty_client_id_is_preserved(self):
        self.configure()
        result = self.configure(client_id="", secret="second-private-secret")
        self.assertEqual("fake-private-client-id", os.environ["FRANCE_TRAVAIL_CLIENT_ID"])
        self.assertEqual("second-private-secret", os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"])
        self.assertTrue(result["france_travail"]["configured"])
        self.assert_secrets_hidden(result, "fake-private-client-id", "second-private-secret")

    def test_updating_credentials_preserves_comments_and_unrelated_configuration(self):
        original = "# Private test configuration\nSMTP_SERVER=smtp.example.fr\nEMAIL_ADDRESS=sender@example.fr\nEMAIL_PASSWORD=other-fake-secret\nHUNTER_API_KEY=fake-hunter-key\nCUSTOM_SETTING=keep-me\n"
        (self.base / ".env").write_text(original, encoding="utf-8")
        self.configure()
        saved = (self.base / ".env").read_text(encoding="utf-8")
        for line in original.splitlines():
            self.assertIn(line, saved)
        self.assertIn("FRANCE_TRAVAIL_CLIENT_ID", saved)
        self.assertIn("FRANCE_TRAVAIL_CLIENT_SECRET", saved)
        status, result = self.request()
        self.assertEqual(200, status)
        self.assert_secrets_hidden(result, "other-fake-secret", "fake-hunter-key", "fake-private-client-secret")

    def test_explicit_clear_removes_credentials_from_runtime_and_saved_environment(self):
        (self.base / ".env").write_text("HUNTER_API_KEY=keep-hunter\n", encoding="utf-8")
        self.configure()
        status, result = self.request("POST", {"clear_france_travail": True})
        self.assertEqual(200, status, result)
        self.assertFalse(result["france_travail"]["configured"])
        self.assertFalse(os.environ.get("FRANCE_TRAVAIL_CLIENT_ID"))
        self.assertFalse(os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET"))
        saved = (self.base / ".env").read_text(encoding="utf-8")
        self.assertIn("HUNTER_API_KEY=keep-hunter", saved)
        self.assertNotIn("fake-private-client-id", saved)
        self.assertNotIn("fake-private-client-secret", saved)
        load_dotenv(self.base / ".env")
        self.assertFalse(os.environ.get("FRANCE_TRAVAIL_CLIENT_ID"))
        self.assertFalse(os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET"))

    def test_invalid_credentials_do_not_partially_replace_configuration_or_leak_values(self):
        self.configure()
        original = (self.base / ".env").read_bytes()
        invalid_values = (["not-an-object"], {"france_travail_client_secret": 7},
                          {"france_travail_client_id": "bad-client\nEMAIL_PASSWORD=injected"},
                          {"france_travail_client_secret": "invalid-secret\x00"},
                          {"clear_france_travail": "yes"})
        for invalid in invalid_values:
            status, result = self.request("POST", invalid)
            with self.subTest(invalid=invalid):
                self.assertEqual(400, status, result)
                self.assertEqual(original, (self.base / ".env").read_bytes())
                self.assertEqual("fake-private-client-secret", os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"])
                self.assert_secrets_hidden(result, "fake-private-client-secret", "bad-client", "invalid-secret")

    def test_settings_save_requires_csrf_and_same_origin(self):
        values = {"france_travail_client_id": "blocked-id", "france_travail_client_secret": "blocked-secret"}
        self.assertEqual(403, self.request("POST", values, token=False)[0])
        self.assertEqual(403, self.request("POST", values, headers={"Origin": "https://hostile.example"})[0])
        self.assertFalse((self.base / ".env").exists())
        self.assertFalse(os.environ.get("FRANCE_TRAVAIL_CLIENT_ID"))
        self.assertFalse(os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET"))

    def test_file_replacement_failure_preserves_existing_credentials_and_configuration(self):
        self.configure()
        original = (self.base / ".env").read_bytes()
        with patch("settings_service.Path.replace", side_effect=PermissionError("simulated write failure")):
            status, result = self.request("POST", {"france_travail_client_id": "replacement-id",
                                                   "france_travail_client_secret": "replacement-secret"})
        self.assertNotEqual(200, status)
        self.assertEqual(original, (self.base / ".env").read_bytes())
        self.assertEqual("fake-private-client-id", os.environ["FRANCE_TRAVAIL_CLIENT_ID"])
        self.assertEqual("fake-private-client-secret", os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"])
        self.assertFalse((self.base / ".env.tmp").exists())
        self.assert_secrets_hidden(result, "replacement-id", "replacement-secret", "fake-private-client-secret")

    def test_settings_defaults_use_saved_localisation_and_configuration_has_no_search_side_effect(self):
        location = {"location": "Rennes", "city_code": "35238", "radius_km": 40,
                    "latitude": 48.11, "longitude": -1.68}
        self.dashboard.post("/api/preferences", location)
        self.configure()
        status, result = self.request()
        self.assertEqual(200, status)
        self.assertEqual("Rennes", result["defaults"]["location"])
        self.assertEqual(40, result["defaults"]["radius_km"])
        self.search.assert_not_called()
        self.assertEqual([], self.dashboard.store.snapshot()["offers"])


if __name__ == "__main__":
    unittest.main()
