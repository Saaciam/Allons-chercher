#!/usr/bin/env python3
"""Vérifier l'authentification Gmail SMTP, sans envoyer de message.

La sortie JSON ne contient ni mot de passe ni réponse brute du serveur.
Codes de sortie : 0 connexion validée, 1 configuration/authentification,
2 connexion réseau ou TLS impossible.
"""

import json
import os
import smtplib
import socket
import ssl
from dataclasses import dataclass, field

import send_emails


SMTP_TIMEOUT = 12


@dataclass(frozen=True)
class Settings:
    address: str
    password: str = field(repr=False)
    host: str = "smtp.gmail.com"
    port: int = 587


class InvalidConfiguration(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_configuration(values):
    """Validation locale uniquement ; ne lit aucun fichier."""
    try:
        address = send_emails.validate_email(values.get("EMAIL_ADDRESS", ""))
    except (ValueError, TypeError):
        raise InvalidConfiguration("invalid_email_address") from None
    password = values.get("EMAIL_PASSWORD", "")
    if not isinstance(password, str) or not password.strip():
        raise InvalidConfiguration("missing_email_password")
    if "\r" in password or "\n" in password:
        raise InvalidConfiguration("invalid_email_password")
    host = values.get("SMTP_SERVER", "smtp.gmail.com")
    if not isinstance(host, str) or host.strip().lower() != "smtp.gmail.com":
        raise InvalidConfiguration("invalid_gmail_server")
    port = values.get("SMTP_PORT", "587")
    if str(port).strip() != "587":
        raise InvalidConfiguration("invalid_gmail_port")
    return Settings(address, password)


def _result(success, phase, code, exit_code, smtp_code=None):
    result = {"success": success, "phase": phase, "code": code,
              "exit_code": exit_code, "authentication_accepted": success,
              "message_sent": False}
    if type(smtp_code) is int and 100 <= smtp_code <= 599:
        result["smtp_code"] = smtp_code
    return result


def check_connection(values):
    """Ouvre SMTP, valide TLS puis LOGIN ; aucun destinataire ni message."""
    try:
        settings = validate_configuration(values)
    except InvalidConfiguration as error:
        return _result(False, "configuration", error.code, 1)
    server = None
    phase = "connection"
    try:
        server = smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT)
        phase = "ehlo"
        code, _ = server.ehlo()
        if code != 250:
            return _result(False, phase, "smtp_greeting_rejected", 1, code)
        phase = "tls"
        context = ssl.create_default_context()
        code, _ = server.starttls(context=context)
        if code != 220:
            return _result(False, phase, "smtp_tls_rejected", 1, code)
        phase = "ehlo_tls"
        code, _ = server.ehlo()
        if code != 250:
            return _result(False, phase, "smtp_greeting_rejected", 1, code)
        phase = "authentication"
        code, _ = server.login(settings.address, settings.password)
        if code not in (235, 503):
            return _result(False, phase, "authentication_rejected", 1, code)
        return _result(True, "authentication", "smtp_authentication_accepted", 0, code)
    except smtplib.SMTPAuthenticationError as error:
        return _result(False, phase, "authentication_rejected", 1, error.smtp_code)
    except (socket.timeout, TimeoutError):
        return _result(False, phase, "network_timeout", 2)
    except ssl.SSLCertVerificationError:
        return _result(False, phase, "tls_certificate_rejected", 2)
    except ssl.SSLError:
        return _result(False, phase, "tls_connection_failed", 2)
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
        return _result(False, phase, "network_connection_failed", 2)
    except smtplib.SMTPNotSupportedError:
        return _result(False, phase, "smtp_feature_unavailable", 1)
    except smtplib.SMTPResponseException as error:
        return _result(False, phase, "smtp_request_rejected", 1, error.smtp_code)
    except smtplib.SMTPException:
        return _result(False, phase, "smtp_protocol_error", 1)
    except OSError:
        return _result(False, phase, "network_connection_failed", 2)
    except Exception:
        # Ne jamais transmettre une exception pouvant contenir les identifiants.
        return _result(False, phase, "smtp_check_failed", 1)
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def main():
    try:
        send_emails.load_dotenv()
        result = check_connection(os.environ)
    except Exception:
        result = _result(False, "configuration", "configuration_unreadable", 1)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
