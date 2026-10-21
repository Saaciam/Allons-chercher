#!/usr/bin/env python3
"""Génère CV, lettre, mail et analyse ; n’envoie aucun email."""

import argparse
import sys
from pathlib import Path

from applications import BASE_DIR, iso_date, load_offers, load_profile, prepare_application, spontaneous_offers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--offers', type=Path, help='JSON contenant une offre ou une liste d’offres')
    source.add_argument('--companies', type=Path, help='liste d’entreprises pour candidatures spontanées')
    parser.add_argument('--profile', type=Path, default=BASE_DIR / 'candidate_profile.json')
    parser.add_argument('--output', type=Path, default=BASE_DIR / 'candidatures')
    parser.add_argument('--only', help='identifiant de l’offre à préparer')
    parser.add_argument('--overwrite', action='store_true', help='régénère les dossiers existants')
    parser.add_argument('--date', type=iso_date, help='date sur la lettre, au format AAAA-MM-JJ')
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        offers = load_offers(args.offers) if args.offers else spontaneous_offers(args.companies or BASE_DIR / 'companies.txt')
        if args.only:
            offers = [o for o in offers if o['id'] == args.only]
            if not offers:
                raise ValueError(f'Offre introuvable : {args.only}.')
        # Vérifier toutes les destinations avant d’écrire le premier dossier.
        if not args.overwrite:
            existing = [o['id'] for o in offers if (args.output / o['id']).exists()]
            if existing:
                raise ValueError('Dossiers déjà présents : ' + ', '.join(existing) + '. Utilise --overwrite pour régénérer.')
        for offer in offers:
            result = prepare_application(profile, args.profile, offer, args.output,
                                         overwrite=args.overwrite, application_date=args.date)
            print(f"Préparé : {offer['entreprise']} — {result}")
        print(f'{len(offers)} dossier(s) préparé(s). Relis les PDF et analyse.json avant l’envoi. Aucun email envoyé.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f'Erreur de préparation : {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
