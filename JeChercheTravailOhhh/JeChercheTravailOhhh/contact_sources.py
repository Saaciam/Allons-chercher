"""Bounded, public-source contact discovery for the local application interface.

This module never reads credentials from disk, infers email addresses, scrapes
LinkedIn, or sends messages. Callers explicitly provide the Hunter API key.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import quote_plus, unquote, urlsplit

import requests

API_URL = "https://api.hunter.io/v2/domain-search"
PAGE_SIZE = 10
MAX_REQUESTS = 3
MAX_CONTACTS = 10
SUCCESS_TTL = 24 * 60 * 60
EMPTY_TTL = 30 * 60
SECONDARY_DEPARTMENTS = "it,management,research,consulting"
DEPARTMENTS = {
    "executive", "it", "finance", "management", "sales", "legal", "support",
    "hr", "marketing", "communication", "education", "design", "health",
    "operations", "product", "research", "consulting", "administrative", "procurement",
}
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
_INFERRED_TYPES = {"inferred", "generated", "guessed", "guess", "predicted", "deduced"}
_HR = re.compile(r"\b(?:rh|hr|recruit\w*|recrut\w*|talent|human resources?|ressources? humaines?|people partner)\b")
_DATA = re.compile(r"\b(?:data|donnees|analytics?|intelligence artificielle|artificial intelligence|machine learning|ai|ia|bi|cto|chief technology officer)\b")
_MANAGER = re.compile(r"\b(?:head|lead|manager|responsable|directeur|directrice|director|chief)\b")


def _text(value, limit=300) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _fold(value) -> str:
    value = unicodedata.normalize("NFKD", _text(value, 1000).casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def normalize_domain(value) -> str:
    """Accept a domain or company website URL, never guess one from a name."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 2000 or re.search(r"[\s\x00-\x1f\x7f]", value.strip()):
        raise ValueError("Domaine d’entreprise invalide : indiquez par exemple entreprise.fr.")
    supplied = value.strip()
    try:
        parsed = urlsplit(supplied if "://" in supplied else "//" + supplied)
        if (parsed.scheme and parsed.scheme not in {"http", "https"}) or parsed.username or parsed.password or parsed.port:
            raise ValueError
        domain = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        raise ValueError("Domaine d’entreprise invalide : indiquez par exemple entreprise.fr.") from None
    if domain.startswith("www."):
        domain = domain[4:]
    if len(domain) > 253 or "." not in domain:
        raise ValueError("Domaine d’entreprise invalide : indiquez par exemple entreprise.fr.")
    labels = domain.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("Domaine d’entreprise invalide : indiquez par exemple entreprise.fr.")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("Indiquez le domaine public de l’entreprise, pas une adresse IP.")
    if labels[-1] in {"local", "localhost", "internal", "test", "invalid"}:
        raise ValueError("Indiquez le domaine public de l’entreprise.")
    return domain


def safe_url(value) -> str:
    """Keep an explicit public HTTP(S) URL suitable for a source or profile link."""
    if not isinstance(value, str) or not value or len(value) > 4000:
        return ""
    value = value.strip()
    if re.search(r"[\x00-\x20\x7f]", value) or re.search(r"[\x00-\x1f\x7f]", unquote(value)):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.rsplit(".", 1)[-1] in {"local", "localhost", "internal", "test", "invalid"}:
                return ""
            ascii_host = host.encode("idna").decode("ascii")
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.IGNORECASE) for label in ascii_host.split(".")):
                return ""
        else:
            if not address.is_global:
                return ""
    except (ValueError, UnicodeError):
        return ""
    return value


def _date(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def _number(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = float(value)
        return max(0, min(100, round(number))) if math.isfinite(number) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _priority(contact) -> tuple[int, list[str]]:
    role = _fold(contact.get("poste"))
    department = _fold(contact.get("department"))
    local_part = str(contact.get("email", "")).partition("@")[0].lower()
    if department == "hr" or _HR.search(role) or re.fullmatch(r"(?:rh|hr|recrutement|recruitment|recruiting|careers|talent)(?:[._-].*)?", local_part):
        return 0, ["RH ou recrutement"]
    if _DATA.search(role):
        reasons = ["Profil Data / IA / BI"]
        if _MANAGER.search(role):
            reasons.append("Responsabilité d’équipe")
        return 1, reasons
    if department in {"it", "management", "research", "consulting"}:
        return 1, ["Équipe informatique, management, recherche ou conseil"]
    return 2, ["Autre collaborateur de l’entreprise"]


def _contact_key(contact):
    priority, _ = _priority(contact)
    named = bool(contact.get("prenom") or contact.get("nom"))
    verification = str(contact.get("verification_status", "")).lower()
    verification_rank = {"valid": 0, "accept_all": 1, "unknown": 2, "": 2, "invalid": 4, "disposable": 4}.get(verification, 3)
    sources = contact.get("public_sources") or []
    latest = max((_date(source.get("last_seen_on")) for source in sources if isinstance(source, dict)), default="")
    # ISO dates sort naturally; an ordinal permits newest-first alongside ascending priorities.
    seen_rank = -datetime.strptime(latest, "%Y-%m-%d").toordinal() if latest else 0
    return priority, not named, verification_rank, -_number(contact.get("confiance")), seen_rank, str(contact.get("email", "")).casefold()


def sort_contacts(contacts: list[dict]) -> list[dict]:
    """Sort usable contacts consistently without discarding manually edited fields."""
    return sorted(contacts, key=_contact_key)


def normalize_contact(row, company: str, resolved_domain: str = "") -> dict | None:
    """Normalize Hunter or enriched rows, rejecting emails without active sources."""
    if not isinstance(row, dict) or str(row.get("source_type", "")).lower() in _INFERRED_TYPES:
        return None
    email = row.get("value") or row.get("email") or ""
    if not isinstance(email, str) or len(email) > 254 or not _EMAIL.fullmatch(email.strip()):
        return None
    raw_sources = row.get("sources") if "sources" in row else row.get("public_sources", [])
    if not isinstance(raw_sources, list):
        return None
    sources, seen = [], set()
    for source in raw_sources:
        if not isinstance(source, dict) or source.get("still_on_page") in (False, 0, "false", "0"):
            continue
        if str(source.get("source_type", "")).lower() in _INFERRED_TYPES:
            continue
        url = safe_url(source.get("uri") or source.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({
            "url": url,
            "domain": urlsplit(url).hostname or "",
            "last_seen_on": _date(source.get("last_seen_on")),
            "extracted_on": _date(source.get("extracted_on")),
            "still_on_page": source.get("still_on_page") if isinstance(source.get("still_on_page"), bool) else None,
        })
    if not sources:
        return None
    sources.sort(key=lambda item: item["last_seen_on"], reverse=True)
    verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    linkedin = safe_url(row.get("linkedin") or row.get("linkedin_url"))
    if linkedin:
        host = (urlsplit(linkedin).hostname or "").lower()
        if host != "linkedin.com" and not host.endswith(".linkedin.com"):
            linkedin = ""
    contact = {
        "entreprise": company,
        "prenom": _text(row.get("first_name") or row.get("prenom")),
        "nom": _text(row.get("last_name") or row.get("nom")),
        "poste": _text(row.get("position") or row.get("position_raw") or row.get("poste"), 500),
        "email": email.strip().lower(),
        "confiance": str(_number(row.get("confidence", row.get("confiance")))),
        "linkedin": linkedin,
        "department": _text(row.get("department"), 100),
        "verification_status": _text(verification.get("status") or row.get("verification_status"), 50).lower(),
        "verification_date": _date(verification.get("date") or row.get("verification_date")),
        "public_sources": sources,
        "source": "hunter",
        "resolved_domain": resolved_domain or _text(row.get("resolved_domain")),
    }
    contact["priority"], contact["priority_reasons"] = _priority(contact)
    return contact


def _deduplicate(contacts: list[dict]) -> list[dict]:
    selected = {}
    for contact in sort_contacts(contacts):
        email = contact["email"].casefold()
        if email not in selected:
            selected[email] = deepcopy(contact)
            continue
        previous = selected[email]
        source_urls = {source["url"] for source in previous["public_sources"]}
        previous["public_sources"].extend(source for source in contact["public_sources"] if source["url"] not in source_urls)
        previous["public_sources"].sort(key=lambda source: source["last_seen_on"], reverse=True)
        for field in ("prenom", "nom", "poste", "linkedin", "department", "verification_status", "verification_date", "resolved_domain"):
            if not previous.get(field) and contact.get(field):
                previous[field] = contact[field]
        previous["priority"], previous["priority_reasons"] = _priority(previous)
    return sort_contacts(list(selected.values()))


def search_queries(company: str, domain: str = "") -> list[dict]:
    """Offer manual searches; never perform or impersonate a LinkedIn login."""
    quoted_company = '"' + company.replace('"', " ") + '"'
    queries = [
        ("Profils LinkedIn — recrutement", f"site:linkedin.com/in/ {quoted_company} (recruteur OR recrutement OR talent)", "linkedin"),
        ("Profils LinkedIn — Data / IA", f"site:linkedin.com/in/ {quoted_company} (data OR intelligence artificielle) (manager OR responsable OR lead)", "linkedin"),
        ("Pages professionnelles et recrutement", f"{('site:' + domain + ' ') if domain else ''}{quoted_company} recrutement contact", "web"),
    ]
    return [{"label": label, "url": "https://www.google.com/search?q=" + quote_plus(query), "kind": kind} for label, query, kind in queries]


class _HunterFailure(RuntimeError):
    pass


class ContactDiscovery:
    """Serializes bounded discovery and keeps reusable public results on disk."""

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir).resolve()
        self.cache_path = self.base_dir / "data" / "contact_search.json"
        self._check_paths()
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.cache_path), threading.RLock())

    def _check_paths(self):
        for path in (self.cache_path, self.cache_path.with_suffix(".tmp")):
            if not path.resolve().is_relative_to(self.base_dir):
                raise ValueError("Le cache des contacts doit rester dans le dossier du projet.")

    def _read_cache(self):
        self._check_paths()
        try:
            state = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("entries"), dict):
                return {"schema_version": 1, "entries": {}}
            state["entries"] = {key: entry for key, entry in state["entries"].items()
                                if isinstance(key, str) and isinstance(entry, dict)
                                and isinstance(entry.get("checked_at"), (int, float))
                                and not isinstance(entry["checked_at"], bool)
                                and math.isfinite(entry["checked_at"])}
            return state
        except (OSError, ValueError, UnicodeError):
            return {"schema_version": 1, "entries": {}}

    def _write_cache(self, state):
        self._check_paths()
        entries = sorted(state["entries"].items(), key=lambda entry: entry[1].get("checked_at", 0), reverse=True)[:200]
        state = {"schema_version": 1, "entries": dict(entries)}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.cache_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _entry_result(entry, company, domain):
        if not isinstance(entry, dict) or not isinstance(entry.get("result"), dict):
            return None
        result = deepcopy(entry["result"])
        if not isinstance(result.get("contacts"), list):
            return None
        result["contacts"] = _deduplicate([
            contact for row in result["contacts"]
            if (contact := normalize_contact(row, company, result.get("resolved_domain") or domain)) is not None
        ])[:MAX_CONTACTS]
        result.update(company=company, from_cache=True, request_count=0, queries=search_queries(company, result.get("resolved_domain") or domain))
        return result

    @staticmethod
    def _fetch(company, domain, department, api_key):
        params = {"limit": PAGE_SIZE, "offset": 0}
        if domain:
            params["domain"] = domain
        else:
            params["company"] = company
        if department:
            params["department"] = department
        try:
            response = requests.get(API_URL, params=params, headers={"X-API-KEY": api_key}, timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise _HunterFailure("Hunter est momentanément inaccessible. Les contacts déjà enregistrés sont conservés.") from None
        status = response.status_code
        if status != 200:
            message = {
                401: "La clé Hunter est absente ou invalide. Vérifiez HUNTER_API_KEY dans la configuration locale.",
                403: "La limite de requêtes Hunter est atteinte. Réessayez plus tard.",
                429: "Le quota Hunter est épuisé. Les contacts déjà enregistrés sont conservés.",
            }.get(status, f"La recherche Hunter a échoué (HTTP {status}). Les contacts enregistrés sont conservés.")
            raise _HunterFailure(message)
        try:
            payload = response.json()
            data = payload["data"]
            emails = data["emails"]
            if not isinstance(data, dict) or not isinstance(emails, list) or any(not isinstance(row, dict) for row in emails):
                raise ValueError
            meta = payload.get("meta") or {}
            if not isinstance(meta, dict):
                raise ValueError
            total = int(meta.get("results") or 0)
            resolved_domain = normalize_domain(data.get("domain"))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _HunterFailure("Hunter a renvoyé une réponse invalide. Les contacts enregistrés sont conservés.") from None
        return emails[:PAGE_SIZE], resolved_domain, total > PAGE_SIZE or len(emails) > PAGE_SIZE

    def search(self, company, department="hr", domain=None, force=False, api_key=None):
        if not isinstance(company, str) or not company.strip() or len(company) > 200 or re.search(r"[\x00-\x1f\x7f]", company):
            raise ValueError("Indiquez le nom de l’entreprise (200 caractères maximum).")
        company = company.strip()
        department = department or "hr"
        if not isinstance(department, str) or department not in DEPARTMENTS:
            raise ValueError("Département de recherche invalide.")
        if not isinstance(force, bool):
            raise ValueError("L’actualisation doit être un booléen.")
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("Clé Hunter invalide.")
        domain = normalize_domain(domain)
        if not domain and ("://" in company or re.fullmatch(r"(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}/?", company)):
            domain = normalize_domain(company)
        cache_key = hashlib.sha256(json.dumps([_fold(company), domain, department, "public-only-v1"], ensure_ascii=True).encode("utf-8")).hexdigest()
        with self.lock:
            now = time.time()
            state = self._read_cache()
            entry = state["entries"].get(cache_key)
            cached = self._entry_result(entry, company, domain)
            if cached is not None and not force:
                try:
                    fresh = float(entry.get("expires_at", 0)) > now
                except (ValueError, TypeError):
                    fresh = False
                if fresh:
                    cached["cache_stale"] = bool(cached.get("cache_stale", False))
                    return cached
            result = {"company": company, "contacts": [], "warnings": [], "queries": search_queries(company, domain),
                      "from_cache": False, "resolved_domain": domain, "truncated": False, "cache_stale": False,
                      "request_count": 0, "checked_at": "", "expires_at": ""}
            failure = None
            if not api_key or not api_key.strip():
                failure = "Hunter n’est pas configuré : ajoutez HUNTER_API_KEY dans la configuration locale. Les liens de recherche restent disponibles."
            else:
                stages = [department, SECONDARY_DEPARTMENTS, None]
                contacts = []
                for selected_department in stages[:MAX_REQUESTS]:
                    if result["request_count"] and len(_deduplicate(contacts)) >= 3:
                        break
                    result["request_count"] += 1
                    try:
                        rows, found_domain, truncated = self._fetch(company, result["resolved_domain"], selected_department, api_key)
                    except _HunterFailure as exc:
                        failure = str(exc)
                        break
                    if found_domain:
                        result["resolved_domain"] = found_domain
                    result["truncated"] = result["truncated"] or truncated
                    contacts.extend(contact for row in rows if (contact := normalize_contact(row, company, result["resolved_domain"])) is not None)
                deduplicated = _deduplicate(contacts)
                result["truncated"] = result["truncated"] or len(deduplicated) > MAX_CONTACTS
                result["contacts"] = deduplicated[:MAX_CONTACTS]
            result["queries"] = search_queries(company, result["resolved_domain"])
            if failure:
                result["warnings"].append(failure)
                if cached is not None:
                    merged = _deduplicate(result["contacts"] + cached["contacts"])
                    result["contacts"] = merged[:MAX_CONTACTS]
                    result["truncated"] = result["truncated"] or cached.get("truncated", False) or len(merged) > MAX_CONTACTS
                    result["from_cache"] = True
                    result["cache_stale"] = True
                    result["checked_at"] = cached.get("checked_at", "")
                    result["expires_at"] = cached.get("expires_at", "")
                    result["resolved_domain"] = result["resolved_domain"] or cached.get("resolved_domain", "")
                    result["queries"] = search_queries(company, result["resolved_domain"])
                    result["warnings"].append("Résultats précédents conservés ; leur actualisation n’a pas pu être terminée.")
                # A failed lookup never turns a previously successful entry into an empty cache.
                return result
            found_contacts = bool(result["contacts"])
            ttl = SUCCESS_TTL if found_contacts else EMPTY_TTL
            result["checked_at"] = datetime.fromtimestamp(now, timezone.utc).isoformat()
            result["expires_at"] = datetime.fromtimestamp(now + ttl, timezone.utc).isoformat()
            if not found_contacts and cached is not None and cached["contacts"]:
                # A successful empty refresh is a negative lookup, not evidence that
                # earlier public addresses disappeared. Keep them visibly stale,
                # preserve their dates, and retry only after the negative-cache TTL.
                result.update(contacts=cached["contacts"], from_cache=True, cache_stale=True,
                              checked_at=cached.get("checked_at", ""), expires_at=cached.get("expires_at", ""),
                              truncated=cached.get("truncated", False),
                              resolved_domain=result["resolved_domain"] or cached.get("resolved_domain", ""))
                result["queries"] = search_queries(company, result["resolved_domain"])
                result["warnings"].append("La recherche n’a retrouvé aucun nouveau contact public. Les contacts précédents sont conservés avec leurs anciennes dates ; vérifiez leurs sources avant de les utiliser. Le cache de cette recherche expire dans 30 minutes.")
            elif not found_contacts:
                result["warnings"].append("Aucun email accompagné d’une source publique active trouvé. Consultez les liens de profils et les pages professionnelles.")
            state["entries"][cache_key] = {"checked_at": now, "expires_at": now + ttl, "result": deepcopy(result)}
            try:
                self._write_cache(state)
            except OSError:
                result["warnings"].append("Les résultats sont disponibles, mais leur cache local n’a pas pu être enregistré.")
            return result
