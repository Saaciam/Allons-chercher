#!/usr/bin/env python3
"""Rechercher des contacts Hunter pour les entreprises de companies.txt.

La clé HUNTER_API_KEY vient de l'environnement ou du .env à côté du script.
Les options --department, --input et --output conservent leur comportement.
Les chemins fournis explicitement sont relatifs au répertoire de travail.
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "companies.txt"
OUTPUT_FILE = BASE_DIR / "contacts.csv"
API_URL = "https://api.hunter.io/v2/domain-search"
DEPARTMENTS = {
    "executive", "it", "finance", "management", "sales", "legal", "support",
    "hr", "marketing", "communication", "education", "design", "health", "operations",
}
PAGE_SIZE = 10
CSV_FIELDS = ["entreprise", "prenom", "nom", "poste", "email", "confiance", "linkedin"]


def load_dotenv(path=None) -> None:
    path = Path(path) if path is not None else BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
            os.environ.setdefault(key, value)


class HunterError(RuntimeError):
    """Erreur de recherche dont le message n'inclut jamais la clé API."""


def search_company(company: str, department=None, api_key=None) -> list[dict]:
    api_key = api_key or os.environ.get("HUNTER_API_KEY")
    if not api_key:
        raise HunterError("Variable d'environnement HUNTER_API_KEY non définie")
    emails, offset = [], 0
    while True:
        params = {"company": company, "api_key": api_key, "limit": PAGE_SIZE, "offset": offset}
        if department:
            params["department"] = department
        try:
            response = requests.get(API_URL, params=params, timeout=15)
        except requests.RequestException as exc:
            # L'exception peut inclure l'URL complète et donc la clé API.
            raise HunterError(f"Hunter inaccessible pour {company} ({type(exc).__name__})") from None
        if response.status_code != 200:
            raise HunterError(f"Erreur Hunter HTTP {response.status_code} pour {company}")
        try:
            payload = response.json()
            data, meta = payload.get("data", {}), payload.get("meta", {})
            page = data.get("emails", [])
            total = int(meta.get("results", 0))
            if not isinstance(page, list) or any(not isinstance(contact, dict) for contact in page):
                raise ValueError("Liste de contacts invalide")
        except (ValueError, TypeError, AttributeError):
            raise HunterError(f"Réponse Hunter invalide pour {company}") from None
        emails.extend(page)
        offset += PAGE_SIZE
        if not page or offset >= total:
            break
        time.sleep(1)
    return emails


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--department", choices=sorted(DEPARTMENTS),
                        help="filtre Hunter par département ; sortie contacts.<département>.csv")
    parser.add_argument("--output", help="fichier CSV de sortie")
    parser.add_argument("--input", help="fichier des entreprises (défaut : companies.txt à côté du script)")
    args = parser.parse_args(argv)
    input_file = Path(args.input) if args.input else INPUT_FILE
    output_file = Path(args.output) if args.output else (
        BASE_DIR / f"contacts.{args.department}.csv" if args.department else OUTPUT_FILE
    )
    try:
        load_dotenv()
        api_key = os.environ.get("HUNTER_API_KEY")
        if not api_key:
            raise HunterError("Variable d'environnement HUNTER_API_KEY non définie")
        companies = [line.strip() for line in input_file.read_text(encoding="utf-8-sig").splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
        rows = []
        for company in companies:
            print(f"Recherche: {company}")
            for contact in search_company(company, args.department, api_key):
                rows.append({
                    "entreprise": company,
                    "prenom": contact.get("first_name") or "",
                    "nom": contact.get("last_name") or "",
                    "poste": contact.get("position") or "",
                    "email": contact.get("value") or "",
                    "confiance": contact.get("confidence") if contact.get("confidence") is not None else "",
                    "linkedin": contact.get("linkedin") or "",
                })
            time.sleep(1)
        # Une recherche échouée ne remplace pas un précédent CSV par des résultats partiels.
        with output_file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    except (OSError, UnicodeError, HunterError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    print(f"\n{len(rows)} contacts écrits dans {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
