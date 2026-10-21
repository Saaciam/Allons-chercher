import contextlib
import csv
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import script


class HunterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.companies = self.folder / "companies.txt"
        self.companies.write_text("ACME\nOTHER\n", encoding="utf-8-sig")
        self.output = self.folder / "contacts.csv"
        self.output.write_text("previous contacts must survive a failed search\n", encoding="utf-8")
        for patcher in (patch.object(script, "load_dotenv"),
                        patch.object(script.time, "sleep"),
                        patch.dict(os.environ, {"HUNTER_API_KEY": "fake-secret-key"}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def response(emails, total=None):
        response = Mock(status_code=200)
        response.json.return_value = {"data": {"emails": emails},
                                      "meta": {"results": len(emails) if total is None else total}}
        return response

    def run_command(self, extra=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = script.main(["--input", str(self.companies), "--output", str(self.output)] + (extra or []))
        return code, output.getvalue()

    def test_pagination_preserves_department_and_timeout(self):
        first = [{"value": f"contact{i}@example.fr"} for i in range(10)]
        with patch.object(script.requests, "get", side_effect=[self.response(first, 11),
                                                               self.response([{"value": "last@example.fr"}], 11)]) as getter:
            result = script.search_company("ACME", "hr", "fake-secret-key")
        self.assertEqual(len(result), 11)
        self.assertEqual([call.kwargs["params"]["offset"] for call in getter.call_args_list], [0, 10])
        self.assertTrue(all(call.kwargs["params"]["department"] == "hr" for call in getter.call_args_list))
        self.assertTrue(all(call.kwargs["timeout"] == 15 for call in getter.call_args_list))

    def test_http_failure_preserves_csv_and_never_prints_response_secrets(self):
        previous = self.output.read_bytes()
        rejected = Mock(status_code=429, text="fake-secret-key https://api.hunter.io/private")
        with patch.object(script.requests, "get", side_effect=[self.response([{"value": "one@example.fr"}]), rejected]):
            code, output = self.run_command()
        self.assertEqual(code, 1)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertIn("HTTP 429", output)
        self.assertNotIn("fake-secret-key", output)
        self.assertNotIn("https://api.hunter.io", output)

    def test_network_failure_never_prints_exception_url_or_key(self):
        previous = self.output.read_bytes()
        exception = script.requests.RequestException("https://api.hunter.io?api_key=fake-secret-key")
        with patch.object(script.requests, "get", side_effect=exception):
            code, output = self.run_command()
        self.assertEqual(code, 1)
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertIn("RequestException", output)
        self.assertNotIn("fake-secret-key", output)
        self.assertNotIn("https://api.hunter.io", output)

    def test_success_keeps_csv_columns_and_zero_confidence(self):
        contact = {"first_name": "Julie", "last_name": "Martin", "position": "RH",
                   "value": "julie@example.fr", "confidence": 0, "linkedin": "https://example.fr/julie"}
        with patch.object(script.requests, "get", side_effect=[self.response([contact]), self.response([])]):
            code, output = self.run_command(["--department", "hr"])
        self.assertEqual(code, 0, output)
        with self.output.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, script.CSV_FIELDS)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entreprise"], "ACME")
        self.assertEqual(rows[0]["confiance"], "0")

    def test_malformed_api_payload_is_handled_without_overwriting_csv(self):
        previous = self.output.read_bytes()
        bad = Mock(status_code=200)
        bad.json.return_value = {"data": {"emails": "not a list"}, "meta": {"results": 1}}
        with patch.object(script.requests, "get", return_value=bad):
            code, output = self.run_command()
        self.assertEqual(code, 1)
        self.assertIn("Réponse Hunter invalide", output)
        self.assertEqual(self.output.read_bytes(), previous)

    def test_import_does_not_read_dotenv_or_capture_credentials(self):
        # Reload after stopping the setUp loader patch so the import itself is exercised.
        with patch.object(Path, "read_text", side_effect=AssertionError("import read a file")), \
                patch.dict(os.environ, {}, clear=True):
            importlib.reload(script)
            self.assertNotIn("HUNTER_API_KEY", os.environ)
            self.assertFalse(hasattr(script, "API_KEY"))

    def test_dotenv_supports_bom_quotes_and_environment_precedence(self):
        path = self.folder / "sample.env"
        path.write_text('TASK_TEST_VALUE="quoted value"\nexport TASK_TEST_OTHER=\'other value\'\n'
                        'TASK_TEST_EXISTING="replace me"\n', encoding="utf-8-sig")
        # Reload restores the actual loader when another test has patched it.
        loader = importlib.reload(script).load_dotenv
        with patch.dict(os.environ, {"TASK_TEST_EXISTING": "keep me"}, clear=True):
            loader(path)
            self.assertEqual(os.environ["TASK_TEST_VALUE"], "quoted value")
            self.assertEqual(os.environ["TASK_TEST_OTHER"], "other value")
            self.assertEqual(os.environ["TASK_TEST_EXISTING"], "keep me")

    def test_default_paths_live_beside_script(self):
        self.assertEqual(Path(script.INPUT_FILE).parent, script.BASE_DIR)
        self.assertEqual(Path(script.OUTPUT_FILE).parent, script.BASE_DIR)


if __name__ == "__main__":
    unittest.main()
