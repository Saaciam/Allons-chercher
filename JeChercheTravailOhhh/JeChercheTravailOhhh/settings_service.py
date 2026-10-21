"""Configuration locale des sources, sans exposer les identifiants au navigateur."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path


FT_KEYS = ('FRANCE_TRAVAIL_CLIENT_ID', 'FRANCE_TRAVAIL_CLIENT_SECRET')
_ENV_LOCK = threading.RLock()
DEFAULT_KEYWORDS = 'Data, intelligence artificielle, Business Intelligence'


def settings(preferences):
    configured = all(bool(os.environ.get(key)) for key in FT_KEYS)
    return {
        'sources': [
            {'id': 'smartrecruiters', 'label': 'Employeurs en France',
             'configured': True, 'requires_credentials': False,
             'description': 'Annonces publiques d’une sélection d’employeurs via SmartRecruiters, sans clé. La couverture dépend de leurs recrutements.'},
            {'id': 'france-travail', 'label': 'France Travail',
             'configured': configured, 'requires_credentials': True,
             'description': 'Source complémentaire à accès restreint : identifiants d’une application autorisée à utiliser l’API Offres d’emploi v2.'},
            {'id': 'arbeitnow', 'label': 'Arbeitnow — Europe',
             'configured': True, 'requires_credentials': False,
             'description': 'Flux européen principalement allemand, utilisé pour les recherches hors de France.'},
        ],
        'france_travail': {'configured': configured, 'portal_url': 'https://francetravail.io/',
                          'docs_url': 'https://www.data.gouv.fr/dataservices/api-offres-demploi',
                          'contact_url': 'https://francetravail.io/contact'},
        'defaults': {'keywords': DEFAULT_KEYWORDS, 'contract': 'stage', 'remote': False,
                     'source': 'all', **preferences, 'location': preferences.get('location') or 'France'},
    }


def update_settings(base_dir: Path, values: dict, preferences: dict):
    allowed = {'france_travail_client_id', 'france_travail_client_secret', 'clear_france_travail'}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError('Paramètres des sources invalides.')
    clear = values.get('clear_france_travail', False)
    if not isinstance(clear, bool):
        raise ValueError('Le choix de déconnexion doit être un booléen.')
    supplied = {}
    for field, key in zip(('france_travail_client_id', 'france_travail_client_secret'), FT_KEYS):
        value = values.get(field, '')
        if not isinstance(value, str) or (value and not re.fullmatch(r'[A-Za-z0-9._~:+/=-]{1,1024}', value)):
            raise ValueError('Identifiants invalides : colle uniquement la valeur fournie par France Travail, sans espace ni retour à la ligne.')
        if value:
            supplied[key] = value
    if clear and supplied:
        raise ValueError('Choisis entre enregistrer des identifiants et déconnecter France Travail.')
    with _ENV_LOCK:
        credentials = {key: supplied.get(key, os.environ.get(key, '')) for key in FT_KEYS}
        if not clear and supplied and not all(credentials.values()):
            raise ValueError('Renseigne le Client ID et le Client Secret pour la première configuration.')
        if clear or supplied:
            path = Path(base_dir) / '.env'
            previous = path.read_text(encoding='utf-8-sig') if path.exists() else ''
            # Garder les commentaires et les paramètres SMTP/Hunter tels quels.
            lines = []
            for line in previous.splitlines(keepends=True):
                declaration = line.strip().removeprefix('export ').lstrip()
                if declaration.partition('=')[0].strip() not in FT_KEYS:
                    lines.append(line)
            content = ''.join(lines)
            if not clear:
                if content and not content.endswith(('\n', '\r')):
                    content += '\n'
                content += ''.join(f'{key}={credentials[key]}\n' for key in FT_KEYS)
            temporary = path.with_name('.env.tmp')
            try:
                temporary.write_text(content, encoding='utf-8', newline='')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            for key in FT_KEYS:
                if clear:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = credentials[key]
    result = settings(preferences)
    result['message'] = ('France Travail déconnecté. Les sources gratuites restent disponibles.' if clear
                         else 'Paramètres enregistrés. Une recherche vérifiera l’accès à France Travail.' if supplied
                         else 'Les identifiants existants ont été conservés.')
    return result
