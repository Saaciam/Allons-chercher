import contextlib
import io
import json
import os
import smtplib
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import check_smtp


class CheckSMTPTests(unittest.TestCase):
    def setUp(self):
        self.values = {"EMAIL_ADDRESS": "candidate@gmail.com",
                       "EMAIL_PASSWORD": "PRIVATE_APP_PASSWORD",
                       "SMTP_SERVER": "smtp.gmail.com", "SMTP_PORT": "587"}
        self.server = Mock()
        self.server.ehlo.return_value = (250, b"private greeting")
        self.server.starttls.return_value = (220, b"private tls response")
        self.server.login.return_value = (235, b"private authentication response")
        smtp = patch.object(check_smtp.smtplib, "SMTP", return_value=self.server)
        self.smtp = smtp.start()
        self.addCleanup(smtp.stop)

    def assert_no_message(self):
        for method in ("sendmail", "send_message", "mail", "rcpt", "data"):
            getattr(self.server, method).assert_not_called()

    def test_success_checks_tls_and_authentication_without_sending(self):
        result = check_smtp.check_connection(self.values)
        self.assertTrue(result["success"])
        self.assertTrue(result["authentication_accepted"])
        self.assertFalse(result["message_sent"])
        self.assertEqual(result["smtp_code"], 235)
        self.assertEqual(result["exit_code"], 0)
        self.smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=12)
        context = self.server.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual([c[0] for c in self.server.method_calls],
                         ["ehlo", "starttls", "ehlo", "login", "quit"])
        self.server.login.assert_called_once_with("candidate@gmail.com", "PRIVATE_APP_PASSWORD")
        self.server.quit.assert_called_once()
        self.assert_no_message()

    def test_authentication_rejected_is_safe_and_connection_is_closed(self):
        self.server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"PRIVATE_APP_PASSWORD candidate@gmail.com private response")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "authentication_rejected")
        self.assertEqual(result["smtp_code"], 535)
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn("PRIVATE_APP_PASSWORD", json.dumps(result))
        self.assertNotIn("candidate@gmail.com", json.dumps(result))
        self.server.quit.assert_called_once()
        self.assert_no_message()

    def test_network_timeout_has_distinct_exit_code(self):
        self.smtp.side_effect = socket.timeout("PRIVATE_APP_PASSWORD")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "network_timeout")
        self.assertEqual(result["exit_code"], 2)
        self.server.quit.assert_not_called()
        self.assert_no_message()

    def test_certificate_error_never_proceeds_to_authentication(self):
        self.server.starttls.side_effect = ssl.SSLCertVerificationError("private certificate info")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "tls_certificate_rejected")
        self.assertEqual(result["exit_code"], 2)
        self.server.login.assert_not_called()
        self.server.quit.assert_called_once()
        self.assert_no_message()

    def test_unsupported_tls_is_a_protocol_error_not_a_network_error(self):
        self.server.starttls.side_effect = smtplib.SMTPNotSupportedError("private server info")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "smtp_feature_unavailable")
        self.assertEqual(result["exit_code"], 1)
        self.server.login.assert_not_called()
        self.server.quit.assert_called_once()
        self.assert_no_message()

    def test_smtp_response_error_retains_only_numeric_code(self):
        self.server.ehlo.side_effect = smtplib.SMTPResponseException(421, b"PRIVATE_APP_PASSWORD")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "smtp_request_rejected")
        self.assertEqual(result["smtp_code"], 421)
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn("PRIVATE_APP_PASSWORD", json.dumps(result))
        self.assert_no_message()

    def test_dns_error_has_network_exit_code_without_server_cleanup(self):
        self.smtp.side_effect = socket.gaierror("private network info")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "network_connection_failed")
        self.assertEqual(result["exit_code"], 2)
        self.server.quit.assert_not_called()
        self.assert_no_message()

    def test_cleanup_falls_back_to_close_and_retains_success(self):
        self.server.quit.side_effect = OSError("PRIVATE_APP_PASSWORD")
        result = check_smtp.check_connection(self.values)
        self.assertTrue(result["success"])
        self.server.close.assert_called_once()
        self.assert_no_message()

    def test_invalid_configuration_never_connects(self):
        cases = [("EMAIL_ADDRESS", "bad\naddress", "invalid_email_address"),
                 ("EMAIL_PASSWORD", "", "missing_email_password"),
                 ("EMAIL_PASSWORD", "private\nsecret", "invalid_email_password"),
                 ("SMTP_SERVER", "attacker.example", "invalid_gmail_server"),
                 ("SMTP_PORT", "465", "invalid_gmail_port")]
        for key, value, code in cases:
            with self.subTest(key=key, code=code):
                result = check_smtp.check_connection({**self.values, key: value})
                self.assertEqual(result["code"], code)
                self.assertEqual(result["phase"], "configuration")
                self.assertEqual(result["exit_code"], 1)
        self.smtp.assert_not_called()

    def test_bad_ehlo_is_reported_without_authentication(self):
        self.server.ehlo.return_value = (550, b"PRIVATE_APP_PASSWORD")
        result = check_smtp.check_connection(self.values)
        self.assertEqual(result["code"], "smtp_greeting_rejected")
        self.assertEqual(result["smtp_code"], 550)
        self.server.login.assert_not_called()
        self.server.quit.assert_called_once()
        self.assert_no_message()

    def test_main_uses_existing_dotenv_loader_and_prints_only_safe_json(self):
        self.server.login.side_effect = RuntimeError("PRIVATE_APP_PASSWORD raw exception")
        output = io.StringIO()
        with patch.object(check_smtp.send_emails, "load_dotenv") as loader, \
                patch.dict(os.environ, self.values, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = check_smtp.main()
        loader.assert_called_once_with()
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["code"], "smtp_check_failed")
        self.assertNotIn("PRIVATE_APP_PASSWORD", output.getvalue())
        self.assertNotIn("raw exception", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        self.assert_no_message()

    def test_unreadable_configuration_does_not_leak_or_connect(self):
        output = io.StringIO()
        with patch.object(check_smtp.send_emails, "load_dotenv",
                          side_effect=OSError("PRIVATE_APP_PASSWORD")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = check_smtp.main()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "configuration_unreadable")
        self.assertNotIn("PRIVATE_APP_PASSWORD", output.getvalue())
        self.smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
