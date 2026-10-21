"""Sources d'offres et filtres partagés par la recherche et les alertes locales.

API publiques documentées :
https://www.arbeitnow.com/blog/job-board-api
https://documenter.getpostman.com/view/18545278/UVJbJdKh
https://francetravail.io/produits-partages/catalogue/offres-emploi/documentation
https://geo.api.gouv.fr/decoupage-administratif/communes
https://developers.smartrecruiters.com/reference/v1listpostings
https://developers.smartrecruiters.com/docs/objects

Les appels se limitent à ces services. Les liens des offres ne sont jamais visités.
Les identifiants France Travail proviennent de l'environnement chargé par l'application.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from html.parser import HTMLParser
import math
import os
import re
import threading
import time
import unicodedata
from urllib.parse import urlsplit

import requests

ARBEITNOW_URL = 'https://www.arbeitnow.com/api/job-board-api'
FRANCE_TRAVAIL_URL = 'https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search'
TOKEN_URL = 'https://entreprise.francetravail.fr/connexion/oauth2/access_token'
GEO_URL = 'https://geo.api.gouv.fr/communes'
SMARTRECRUITERS_URL = 'https://api.smartrecruiters.com/v1/companies'
# Comptes publics confirmés sur leurs pages carrière et API ; aucun compte privé.
SMARTRECRUITERS_EMPLOYERS = (
    ('SopraSteria1', 'Sopra Steria'), ('Devoteam', 'Devoteam'),
    ('Wavestone1', 'Wavestone'), ('alten', 'ALTEN'),
)
REQUEST_TIMEOUT = (5, 15)
_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.RLock()
_KEY_LOCKS: OrderedDict = OrderedDict()
_CACHE_LIMIT = 256


class SourceError(ValueError):
    """Message public, sans URL de requête, jeton ou détail d'authentification."""


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag in {'p', 'br', 'li', 'div', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'li', 'div'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value) -> str:
    parser = _PlainText()
    parser.feed(str(value or '')[:200_000])
    return re.sub(r'\n\s*\n+', '\n\n', ''.join(parser.parts)).strip().replace('\x00', '')


def _normal(value) -> str:
    text = str(value or '')
    return _normal_cached(text) if len(text) <= 50_000 else _normal_text(text)


@lru_cache(maxsize=256)
def _normal_cached(text: str) -> str:
    return _normal_text(text)


def _normal_text(text: str) -> str:
    decomposed = unicodedata.normalize('NFKD', text.casefold())
    return re.sub(r'\s+', ' ', ''.join(c for c in decomposed if not unicodedata.combining(c))).strip()


def _contains(text: str, phrase: str) -> bool:
    return bool(re.search(r'(?<!\w)' + re.escape(_normal(phrase)) + r'(?!\w)', _normal(text)))


_KEYWORD_ALIASES = {
    'data': ('data', 'donnees', 'donnee', 'daten', 'database', 'databases', 'dataops', 'dataviz'),
    'intelligence artificielle': ('intelligence artificielle', 'artificial intelligence', 'ia', 'ai',
                                'machine learning', 'deep learning', 'llm', 'llms', 'generative ai',
                                'ia generative', 'genai', 'mlops', 'nlp', 'computer vision',
                                'reinforcement learning', 'modele de langage', 'modeles de langage',
                                'traitement du langage naturel', 'apprentissage automatique'),
    'bi': ('bi', 'business intelligence', 'power bi', 'powerbi', 'decisionnel', 'decisionnelle'),
}


def matching_keywords(text: str, keywords: str) -> bool:
    """Groupes séparés par virgule : OU ; mots d'un groupe personnalisé : ET."""
    groups = [_normal(part) for part in str(keywords or '').split(',') if part.strip()]
    if not groups:
        return True
    for group in groups:
        canonical = next((key for key, values in _KEYWORD_ALIASES.items() if group in values), None)
        if canonical and any(_contains(text, word) for word in _KEYWORD_ALIASES[canonical]):
            return True
        if not canonical and all(_contains(text, word) for word in group.split()):
            return True
    return False


def _cached(key, ttl, loader):
    # Chaque clé regroupe ses rafraîchissements ; différentes sources restent parallèles.
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.monotonic() - entry[0] < ttl:
            _CACHE.move_to_end(key)
            return deepcopy(entry[1])
        lock = _KEY_LOCKS.setdefault(key, threading.RLock())
        _KEY_LOCKS.move_to_end(key)
        while len(_KEY_LOCKS) > 256:
            _KEY_LOCKS.popitem(last=False)
    with lock:
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry and time.monotonic() - entry[0] < ttl:
                return deepcopy(entry[1])
        value = loader()
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), deepcopy(value))
            _CACHE.move_to_end(key)
            while len(_CACHE) > _CACHE_LIMIT:
                _CACHE.popitem(last=False)
        return value


def _json_request(method, url, *, source, params=None, data=None, headers=None):
    try:
        response = getattr(requests, method)(
            url, params=params, data=data, headers=headers,
            timeout=REQUEST_TIMEOUT, allow_redirects=False,
        )
        if response.status_code == 204 and source != 'Authentification France Travail':
            return {}
        if response.status_code in {401, 403}:
            raise SourceError(f'{source} : accès refusé ; vérifier les identifiants et les droits API.')
        if response.status_code == 429:
            raise SourceError(f'{source} : limite de requêtes atteinte ; réessayer plus tard.')
        if not 200 <= response.status_code < 300:
            raise SourceError(f'{source} : service indisponible (HTTP {response.status_code}).')
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        if isinstance(exc, SourceError):
            raise
        # Ne jamais relayer une exception requests : elle peut contenir un token.
        raise SourceError(f'{source} : connexion impossible ou réponse non lisible.') from None


def _safe_link(value) -> str:
    if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username and not parsed.password:
            return value[:2000]
    except ValueError:
        pass
    return ''


def _line(value, fallback='') -> str:
    return re.sub(r'\s+', ' ', _plain(value)).strip()[:200] or fallback


def _offer_id(source: str, raw_id, title, company) -> str:
    digest = sha256(f'{raw_id}|{title}|{company}'.encode('utf-8')).hexdigest()[:24]
    return source + '-' + digest


def _contract(metadata: str, title: str) -> str:
    metadata = _normal(metadata)
    for contract, aliases in (
        ('stage', ('stage', 'stagiaire', 'internship', 'intern', 'praktikum', 'stg')),
        ('alternance', ('alternance', 'apprentissage', 'apprenticeship', 'apprentice', 'ausbildung', 'professionnalisation')),
        ('cdi', ('cdi', 'contrat a duree indeterminee', 'permanent', 'unbefristet', 'indefinite')),
        ('cdd', ('cdd', 'contrat a duree determinee', 'fixed term', 'fixed-term', 'befristet')),
    ):
        if any(_contains(metadata, alias) for alias in aliases):
            return contract
    # "Full-time" n'atteste aucun type de contrat. Une mention dans le titre, oui.
    for contract, aliases in (
        ('stage', ('stage', 'stagiaire', 'internship', 'intern', 'praktikum')),
        ('alternance', ('alternance', 'apprentissage', 'apprentice', 'apprenticeship', 'ausbildung')),
        ('cdi', ('cdi',)), ('cdd', ('cdd',)),
    ):
        if any(_contains(title, alias) for alias in aliases):
            return contract
    return 'unknown'


def _coordinates(latitude, longitude):
    try:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError
        lat, lon = float(latitude), float(longitude)
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError):
        pass
    raise ValueError('Coordonnées géographiques invalides.')


def _distance(first, second) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    arc = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, arc)))


def _city_name(label: str) -> str:
    name = re.sub(r'\([^)]*\)', '', _normal(label)).strip()
    return re.sub(r'^\d{2,5}\s*[-–]?\s*', '', name).strip()


_DATA_ROLES = ('data engineer', 'data engineering', 'data scientist', 'data science',
               'data analyst', 'data analytics', 'analytics engineer', 'big data',
               'analyste de donnees', 'analyse de donnees', 'ingenierie des donnees',
               'data visualisation', 'data visualization', 'business intelligence',
               'data warehouse', 'data lake', 'dataops', 'dataviz', 'etl', 'power bi', 'pandas', 'spark', 'dbt')
_DATA_OTHER_ROLES = ('data protection', 'protect data', 'privacy', 'rgpd',
                     'protection des donnees', 'delegue a la protection', 'dpo')


def _search_keyword_match(offer: dict, keywords: str) -> bool:
    title = str(offer.get('intitule', ''))
    # Les missions sont séparées de la présentation générale de l'employeur.
    body = str(offer.get('search_text', offer.get('description', '')))
    groups = [part.strip() for part in str(keywords or '').split(',') if part.strip()]
    if not groups:
        return True
    for group in groups:
        if _normal(group) in _KEYWORD_ALIASES['data']:
            if not matching_keywords(title + '\n' + body, group):
                continue
            if any(_contains(title, excluded) for excluded in _DATA_OTHER_ROLES):
                if not any(_contains(title + '\n' + body, primary) for primary in _DATA_ROLES):
                    continue
            # Une simple mention de "données" dans une mission RH ne suffit pas.
            if matching_keywords(title, group) or any(_contains(body, role) for role in _DATA_ROLES):
                return True
        elif matching_keywords(title + '\n' + body, group):
            return True
    return False


def rank_offer(offer: dict, criteria: dict) -> dict:
    """Classement explicable de recherche, sans score de recrutement ni de profil."""
    title = str(offer.get('intitule', ''))
    body = str(offer.get('search_text', offer.get('description', '')))
    groups = list(dict.fromkeys(part.strip() for part in criteria.get('keywords', '').split(',') if part.strip()))
    title_matches = [group for group in groups if matching_keywords(title, group)]
    body_matches = [group for group in groups if matching_keywords(body, group) and group not in title_matches]
    score = len(title_matches) * 25 + len(body_matches) * 10
    reasons = []
    if title_matches:
        reasons.append('Mots-clés dans l’intitulé : ' + ', '.join(title_matches) + '.')
    if body_matches:
        reasons.append('Mots-clés dans les missions : ' + ', '.join(body_matches) + '.')
    if criteria.get('contract', 'stage') == offer.get('contract'):
        score += 10
        reasons.append('Type de contrat recherché attesté.')
    try:
        distance = _distance(_coordinates(criteria.get('latitude'), criteria.get('longitude')),
                             _coordinates(offer.get('latitude'), offer.get('longitude')))
        radius = max(1, int(criteria.get('radius_km', 30)))
        score += max(0, round(5 * (1 - distance / radius)))
        reasons.append(f'À environ {distance:.0f} km du lieu recherché.')
    except (ValueError, TypeError):
        pass
    if offer.get('contract_warning'):
        score -= 5
        reasons.append('Métadonnées de contrat à vérifier sur l’offre originale.')
    return {'score': max(0, score), 'reasons': reasons,
            'matched_keywords': title_matches + body_matches}


def match_offer(offer: dict, criteria: dict) -> bool:
    if not _search_keyword_match(offer, criteria.get('keywords', '')):
        return False
    requested_contract = criteria.get('contract', 'stage')
    actual = _contract(str(offer.get('contract', '')), str(offer.get('intitule', '')))
    if requested_contract != 'all' and requested_contract != actual:
        return False
    if criteria.get('remote') and offer.get('remote') is not True:
        return False
    location = _normal(criteria.get('location', ''))
    if not location or location in {'all', 'partout', 'europe', 'world', 'monde'}:
        return True
    if location == 'france':
        # Un lieu importé ou distant ne suffit pas à attester un pays de résidence.
        country = _normal(offer.get('country') or offer.get('pays'))
        if country:
            return country in {'france', 'fr', '1', 'french', 'frankreich'}
        if offer.get('source') == 'France Travail':
            return True
        if re.fullmatch(r'[0-9AB]{5}', str(offer.get('city_code', ''))):
            return True
        return any(_contains(str(offer.get('lieu', '')), word) for word in ('france', 'frankreich'))
    try:
        center = _coordinates(criteria.get('latitude'), criteria.get('longitude'))
        coordinates = _coordinates(offer.get('latitude'), offer.get('longitude'))
    except ValueError:
        if criteria.get('city_code') and offer.get('city_code') == criteria['city_code']:
            return True
        city_name = _city_name(location)
        return bool(city_name) and _contains(_city_name(str(offer.get('lieu', ''))), city_name)
    return _distance(center, coordinates) <= int(criteria.get('radius_km', 30))


def _geo_locations(payload) -> list[dict]:
    if not isinstance(payload, list):
        return []
    locations = []
    for item in payload[:6]:
        if not isinstance(item, dict) or not isinstance(item.get('nom'), str) or not item.get('code'):
            continue
        coords = item.get('centre', {}).get('coordinates', []) if isinstance(item.get('centre'), dict) else []
        if not isinstance(coords, list) or len(coords) != 2:
            continue
        try:
            lat, lon = _coordinates(coords[1], coords[0])
        except ValueError:
            continue
        code = str(item['code'])
        department = item.get('departement', {}).get('code', '') if isinstance(item.get('departement'), dict) else ''
        locations.append({'label': f"{item['nom']} ({department})" if department else item['nom'],
                          'city_code': code, 'latitude': lat, 'longitude': lon})
    return locations


def resolve_location(query: str) -> list[dict]:
    query = str(query or '').strip()[:100]
    if len(query) < 2 or _normal(query) in {'france', 'europe', 'partout'}:
        return []
    def load():
        params = {'fields': 'nom,code,centre,departement', 'boost': 'population', 'limit': 6}
        if re.fullmatch(r'\d{5}', query):
            params['codePostal'] = query
        else:
            params['nom'] = _city_name(query)
        return _geo_locations(_json_request('get', GEO_URL, source='Localisation', params=params))
    return _cached(('geo', _normal(query)), 86400, load)


def reverse_location(latitude, longitude) -> dict:
    lat, lon = _coordinates(latitude, longitude)
    def load():
        results = _geo_locations(_json_request('get', GEO_URL, source='Localisation', params={
            'lat': lat, 'lon': lon, 'fields': 'nom,code,centre,departement', 'limit': 1,
        }))
        if not results:
            raise SourceError('Localisation : aucune commune française trouvée à ces coordonnées.')
        return {**results[0], 'latitude': lat, 'longitude': lon}
    return _cached(('reverse-geo', round(lat, 5), round(lon, 5)), 86400, load)


def _sr_custom_fields(raw: dict) -> list[dict]:
    fields = raw.get('customField', [])
    return [field for field in fields if isinstance(field, dict)] if isinstance(fields, list) else []


def _normalize_smartrecruiters(raw: dict, employer: str, label: str, *, detailed=True) -> dict | None:
    if not isinstance(raw, dict) or raw.get('active') is False:
        return None
    title = _line(raw.get('name'))
    raw_id = str(raw.get('id', ''))
    if not title or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', raw_id):
        return None
    company = raw.get('company', {})
    place = raw.get('location', {})
    employment = raw.get('typeOfEmployment', {})
    company = company if isinstance(company, dict) else {}
    place = place if isinstance(place, dict) else {}
    employment = employment if isinstance(employment, dict) else {}
    fields = _sr_custom_fields(raw)
    contract_fields = [field for field in fields if any(word in _normal(field.get('fieldLabel'))
                                                      for word in ('contract', 'contrat', 'employment'))]
    customs = ' '.join(str(field.get('valueLabel', '')) for field in contract_fields)
    title_contract = _contract('', title)
    custom_contract = _contract(customs, '')
    if any(str(field.get('valueId', '')).upper().startswith('STG')
           and 'nature' in _normal(field.get('fieldLabel')) for field in contract_fields):
        custom_contract = 'stage'
    employment_text = str(employment.get('id', '')) + ' ' + str(employment.get('label', ''))
    generic_contract = _contract(employment_text, '')
    # Des employeurs utilisent "permanent/Full-time" pour des stages publics :
    # l'intitulé explicite et les champs métier sont conservés comme preuves.
    actual_contract = title_contract if title_contract in {'stage', 'alternance'} else custom_contract
    if actual_contract == 'unknown':
        actual_contract = generic_contract
    sections = raw.get('jobAd', {}).get('sections', {}) if isinstance(raw.get('jobAd'), dict) else {}
    sections = sections if isinstance(sections, dict) else {}
    parts, search_parts = [], []
    for key in ('companyDescription', 'jobDescription', 'qualifications', 'additionalInformation'):
        section = sections.get(key, {})
        text = _plain(section.get('text')) if isinstance(section, dict) else ''
        if text:
            parts.append(text)
            if key in {'jobDescription', 'qualifications'}:
                search_parts.append(text)
    description = '\n\n'.join(parts)
    if detailed and not description:
        return None
    country = _normal(place.get('country'))
    company_name = _line(company.get('name'), label)
    location = _line(place.get('fullLocation')) or _line(', '.join(str(place.get(key, ''))
                                                                   for key in ('city', 'region') if place.get(key)))
    if country == 'fr' and not _contains(location, 'France'):
        location += ', France'
    offer = {'id': _offer_id('smartrecruiters', employer + '/' + raw_id, title, company_name),
             'entreprise': company_name, 'intitule': title, 'description': description or title,
             'search_text': '\n\n'.join(search_parts), 'lieu': location, 'country': country,
             'url': _safe_link(raw.get('postingUrl')) or f'https://jobs.smartrecruiters.com/{employer}/{raw_id}',
             'source': 'SmartRecruiters', 'source_employer': label,
             'remote': place.get('remote') is True and place.get('hybrid') is not True,
             'hybrid': place.get('hybrid') is True, 'contract': actual_contract, 'contact': {}}
    if actual_contract in {'stage', 'alternance'} and (
        generic_contract in {'cdi', 'cdd'} or custom_contract in {'cdi', 'cdd'}):
        offer['contract_warning'] = ('L’intitulé indique un ' + actual_contract
                                     + ', mais certaines métadonnées de l’employeur indiquent un autre contrat. '
                                     + 'Vérifier le contrat sur l’offre originale.')
    try:
        offer['latitude'], offer['longitude'] = _coordinates(place.get('latitude'), place.get('longitude'))
    except ValueError:
        pass
    if isinstance(raw.get('releasedDate'), str):
        offer['published_at'] = raw['releasedDate'][:50]
    for field in fields:
        if _normal(field.get('fieldLabel')) in {'duration (in months)', 'duree (en mois)', 'duration in months'}:
            duration = str(field.get('valueLabel', '')).strip()
            if duration.isdigit() and 1 <= int(duration) <= 36:
                offer['duree_mois'] = int(duration)
    return offer


def _smartrecruiters_list(employer: str, query: str) -> tuple[list[dict], bool]:
    def load():
        rows, truncated = [], False
        for offset in (0, 100, 200):
            params = {'country': 'fr', 'destination': 'PUBLIC', 'limit': 100, 'offset': offset}
            if query:
                params['q'] = query
            payload = _json_request('get', f'{SMARTRECRUITERS_URL}/{employer}/postings',
                                    source=f'SmartRecruiters ({employer})', params=params)
            if not isinstance(payload, dict) or not isinstance(payload.get('content'), list):
                raise SourceError(f'SmartRecruiters ({employer}) : format de réponse inattendu.')
            page = payload['content'][:100]
            rows.extend(row for row in page if isinstance(row, dict))
            try:
                total = int(payload.get('totalFound', len(rows)))
            except (ValueError, TypeError):
                total = len(rows)
            if len(page) < 100 or len(rows) >= total:
                break
            truncated = offset == 200
        return rows, truncated
    return _cached(('smartrecruiters-list', employer, query), 1800, load)


def _smartrecruiters(criteria: dict) -> tuple[list[dict], list[str], list[dict]]:
    contract = criteria.get('contract', 'stage')
    if contract == 'stage':
        queries = ['stage', 'intern']
    elif contract == 'alternance':
        queries = ['alternance', 'apprentice']
    else:
        queries = [part.strip()[:100] for part in criteria.get('keywords', '').split(',') if part.strip()][:3] or ['']
    offers, warnings, statuses = [], [], []

    def employer_search(pair):
        employer, label = pair
        candidates, source_warnings = {}, []
        try:
            for query in queries:
                rows, truncated = _smartrecruiters_list(employer, query)
                if truncated:
                    source_warnings.append(f'{label} : recherche limitée à 300 annonces par requête ; affiner les mots-clés.')
                for raw in rows:
                    preliminary = _normalize_smartrecruiters(raw, employer, label, detailed=False)
                    if preliminary and match_offer(preliminary, {**criteria, 'keywords': ''}):
                        candidates[str(raw['id'])] = (raw, preliminary)
            # Priorité aux titres recherchés ; descriptions complètes pour les annonces
            # restantes de la ville afin de reconnaître une mission Data sous un autre titre.
            ordered = sorted(candidates.values(),
                             key=lambda pair: -int(matching_keywords(pair[1]['intitule'], criteria.get('keywords', ''))))
            domain_search = any(_normal(group) in tuple(_KEYWORD_ALIASES['data']) + tuple(_KEYWORD_ALIASES['intelligence artificielle']) + tuple(_KEYWORD_ALIASES['bi'])
                                for group in criteria.get('keywords', '').split(','))
            if domain_search:
                ordered = [pair for pair in ordered if matching_keywords(pair[1]['intitule'], criteria.get('keywords', ''))
                           or any(_contains(pair[1]['intitule'], title) for title in (
                               'developpeur', 'developpeuse', 'ingenieur', 'ingenieure', 'consultant',
                               'consultante', 'informatique', 'research', 'innovation', 'r&d'))]
            if len(ordered) > 30:
                source_warnings.append(f'{label} : descriptions limitées à 30 annonces ; priorité aux titres correspondant aux mots-clés.')
            normalized = []
            errors = []

            def get_detail(pair):
                raw, preliminary = pair
                posting_id = str(raw['id'])
                def load():
                    # Les liens ref fournis par l'API ne sont jamais suivis.
                    return _json_request('get', f'{SMARTRECRUITERS_URL}/{employer}/postings/{posting_id}',
                                         source=f'SmartRecruiters ({label})')
                try:
                    detail = _cached(('smartrecruiters-detail', employer, posting_id), 1800, load)
                    if not isinstance(detail, dict):
                        raise SourceError(f'{label} : description non lisible.')
                    offer = _normalize_smartrecruiters({**raw, **detail}, employer, label)
                    return offer, None
                except SourceError as exc:
                    return None, str(exc)

            with ThreadPoolExecutor(max_workers=2) as pool:
                for offer, error in pool.map(get_detail, ordered[:30]):
                    if error:
                        errors.append(error)
                    if offer and match_offer(offer, criteria):
                        normalized.append(offer)
            if errors:
                source_warnings.append(f'{label} : {len(errors)} description(s) momentanément indisponible(s).')
            return normalized, source_warnings, {'id': employer, 'label': label,
                                                'status': 'partial' if errors else 'ok',
                                                'count': len(normalized),
                                                'message': 'Annonces publiques françaises consultées sans clé API.'}
        except SourceError as exc:
            return [], [str(exc)], {'id': employer, 'label': label, 'status': 'error',
                                    'count': 0, 'message': str(exc)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for normalized, source_warnings, status in pool.map(employer_search, SMARTRECRUITERS_EMPLOYERS):
            offers.extend(normalized)
            warnings.extend(source_warnings)
            statuses.append(status)
    warnings.append('Les sources employeurs couvrent Sopra Steria, Devoteam, Wavestone et ALTEN en France ; cette recherche ne couvre pas tous les employeurs.')
    return offers, warnings, statuses


def _arbeitnow_feed() -> tuple[list[dict], bool]:
    def load():
        offers = []
        truncated = False
        for page in range(1, 4):
            payload = _json_request('get', ARBEITNOW_URL, source='Arbeitnow', params={'page': page})
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                if payload == {}:
                    break
                raise SourceError('Arbeitnow : format de réponse inattendu.')
            for raw in payload['data'][:100]:
                if not isinstance(raw, dict):
                    continue
                company, title = _line(raw.get('company_name')), _line(raw.get('title'))
                description = _plain(raw.get('description'))
                if not company or not title or not description:
                    continue
                kinds = raw.get('job_types', [])
                kinds = kinds if isinstance(kinds, list) else []
                offer = {'id': _offer_id('arbeitnow', raw.get('slug'), title, company),
                         'entreprise': company, 'intitule': title, 'description': description,
                         'lieu': _line(raw.get('location')), 'url': _safe_link(raw.get('url')),
                         'source': 'Arbeitnow', 'remote': raw.get('remote') is True,
                         'contract': _contract(' '.join(str(v) for v in kinds), title), 'contact': {}}
                timestamp = raw.get('created_at')
                if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
                    try:
                        offer['published_at'] = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                    except (ValueError, OverflowError, OSError):
                        pass
                offers.append(offer)
            links = payload.get('links', {})
            has_next = isinstance(links, dict) and bool(links.get('next'))
            if not has_next:
                break
            truncated = page == 3
            # Le lien next est informatif ; seul le paramètre numérique page est utilisé.
        return offers, truncated
    return _cached(('arbeitnow-feed',), 1800, load)


def _ft_token(client_id, secret):
    credential_key = sha256((client_id + '\0' + secret).encode('utf-8')).hexdigest()
    def load():
        payload = _json_request('post', TOKEN_URL, source='Authentification France Travail',
                                params={'realm': '/partenaire'}, data={
                                    'grant_type': 'client_credentials', 'client_id': client_id,
                                    'client_secret': secret, 'scope': 'api_offresdemploiv2 o2dsoffre',
                                })
        token = payload.get('access_token') if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
            raise SourceError('France Travail : aucun jeton valide reçu.')
        try:
            expiry = max(1, min(300, int(payload.get('expires_in', 300)) - 30))
        except (ValueError, TypeError):
            expiry = 60
        return token, time.monotonic() + expiry
    token, deadline = _cached(('ft-token', credential_key), 270, load)
    if time.monotonic() >= deadline:
        with _CACHE_LOCK:
            _CACHE.pop(('ft-token', credential_key), None)
        token, _ = _cached(('ft-token', credential_key), 270, load)
    return token, credential_key


def _ft_remote(text: str) -> bool:
    normal = _normal(text)
    if re.search(r'(?:pas de|sans|aucun) teletravail|teletravail (?:non|impossible)|no remote', normal):
        return False
    return bool(re.search(r'(?:100\s*%\s*(?:en\s+)?(?:teletravail|remote)|(?:teletravail|remote)\s*(?:a\s*)?100\s*%)', normal)
                or any(_contains(normal, phrase) for phrase in (
                    'full remote', 'fully remote', 'entierement a distance', 'teletravail integral')))


def _normalize_ft(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    enterprise = raw.get('entreprise', {})
    place = raw.get('lieuTravail', {})
    enterprise = enterprise if isinstance(enterprise, dict) else {}
    place = place if isinstance(place, dict) else {}
    title = _line(raw.get('intitule'))
    description = _plain(raw.get('description'))
    if not title or not description:
        return None
    company = _line(enterprise.get('nom'), 'Entreprise non précisée')
    source_id = str(raw.get('id', ''))
    origin = raw.get('origineOffre', {})
    origin = origin if isinstance(origin, dict) else {}
    url = _safe_link(origin.get('urlOrigine'))
    if not url and re.fullmatch(r'[a-zA-Z0-9_-]{1,50}', source_id):
        url = 'https://candidat.francetravail.fr/offres/recherche/detail/' + source_id
    offer = {'id': _offer_id('france-travail', source_id, title, company),
             'entreprise': company, 'intitule': title, 'description': description,
             'lieu': _line(place.get('libelle')), 'url': url, 'source': 'France Travail',
             'remote': _ft_remote(title + '\n' + description),
             'hybrid': not _ft_remote(title + '\n' + description)
                       and any(_contains(description, word) for word in ('teletravail', 'hybride', 'hybrid'))
                       and not re.search(r'(?:pas de|sans|aucun) teletravail|teletravail (?:non|impossible)', _normal(description)),
             'contract': _contract(' '.join(str(raw.get(key, '')) for key in (
                 'natureContratLibelle', 'typeContrat', 'typeContratLibelle')), title),
             'city_code': str(place.get('commune', '')), 'contact': {}}
    if place.get('paysLibelle'):
        offer['country'] = _line(place['paysLibelle'])
    try:
        offer['latitude'], offer['longitude'] = _coordinates(place.get('latitude'), place.get('longitude'))
    except ValueError:
        pass
    if isinstance(raw.get('dateCreation'), str):
        offer['published_at'] = raw['dateCreation'][:50]
    # Dates de début et durées ne sont pas extrapolées de la date de publication.
    return offer


def _france_travail(criteria: dict, client_id, secret) -> tuple[list[dict], bool]:
    token, credential_key = _ft_token(client_id, secret)
    groups = [part.strip()[:100] for part in criteria.get('keywords', '').split(',') if part.strip()] or ['']
    if len(groups) > 5:
        raise ValueError('France Travail accepte au maximum 5 groupes de mots-clés par recherche.')
    offers, truncated = [], False
    for group in groups:
        params = {'range': '0-149', 'sort': 1}
        if group:
            params['motsCles'] = group
        if criteria.get('city_code'):
            params['commune'] = criteria['city_code']
            params['distance'] = criteria.get('radius_km', 30)
        if criteria.get('contract') in {'cdi', 'cdd'}:
            params['typeContrat'] = criteria['contract'].upper()
        # STG et alternance ne sont pas supposés disponibles dans le référentiel :
        # la preuve de contrat est contrôlée localement sur chaque offre reçue.
        key = ('ft-search', credential_key, tuple(sorted(params.items())))
        def load(params=params):
            payload = _json_request('get', FRANCE_TRAVAIL_URL, source='France Travail', params=params,
                                    headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
            if payload == {}:
                return []
            if not isinstance(payload, dict) or not isinstance(payload.get('resultats'), list):
                raise SourceError('France Travail : format de réponse inattendu.')
            return payload['resultats'][:150]
        rows = _cached(key, 600, load)
        truncated = truncated or len(rows) == 150
        offers.extend(offer for raw in rows if (offer := _normalize_ft(raw)) is not None)
    return offers, truncated


def search_jobs(criteria: dict) -> dict:
    """Une panne de source laisse disponibles les résultats des autres sources."""
    result = {'offers': [], 'warnings': [], 'sources': [], 'source_status': []}
    try:
        if not isinstance(criteria, dict):
            raise ValueError('Les critères de recherche doivent être un objet.')
        criteria = dict(criteria)
        source = criteria.get('source', 'all')
        if source not in {'all', 'smartrecruiters', 'france-travail', 'arbeitnow'}:
            raise ValueError('Source de recherche inconnue.')
        if criteria.get('contract', 'stage') not in {'all', 'stage', 'alternance', 'cdi', 'cdd'}:
            raise ValueError('Type de contrat inconnu.')
        if not isinstance(criteria.get('keywords', ''), str) or len(criteria.get('keywords', '')) > 500:
            raise ValueError('Les mots-clés doivent contenir au maximum 500 caractères.')
        radius = criteria.get('radius_km', 30)
        if isinstance(radius, bool) or int(radius) != float(radius) or not 0 <= int(radius) <= 200:
            raise ValueError('Le rayon doit être un entier entre 0 et 200 km.')
        criteria['radius_km'] = int(radius)
        if not isinstance(criteria.get('location', ''), str) or len(criteria.get('location', '')) > 200:
            raise ValueError('Localisation invalide.')
        if type(criteria.get('remote', False)) is not bool:
            raise ValueError('Le filtre télétravail doit être un booléen.')
        city_code = criteria.get('city_code', '')
        if city_code and (not isinstance(city_code, str) or not re.fullmatch(r'[0-9AB]{5}', city_code)):
            raise ValueError('Code commune invalide.')
        if criteria.get('latitude') is not None or criteria.get('longitude') is not None:
            criteria['latitude'], criteria['longitude'] = _coordinates(criteria.get('latitude'), criteria.get('longitude'))
    except (ValueError, TypeError, OverflowError) as exc:
        result['warnings'].append(str(exc) if isinstance(exc, ValueError) else 'Critères de recherche invalides.')
        return result

    location = _normal(criteria.get('location', ''))
    if location and location not in {'france', 'all', 'partout', 'europe', 'world', 'monde'} and not criteria.get('city_code'):
        try:
            locations = resolve_location(criteria['location'])
            if locations:
                criteria.update(locations[0])
                criteria['location'] = locations[0]['label']
                if len(locations) > 1:
                    result['warnings'].append('Plusieurs villes correspondent : la première commune a été retenue ; préciser la ville si nécessaire.')
            else:
                result['warnings'].append('Commune non résolue : filtre sur le nom de ville, sans calcul du rayon.')
        except SourceError as exc:
            result['warnings'].append(str(exc) + ' Filtre sur le nom de ville, sans calcul du rayon.')

    offers = []
    if source in {'all', 'smartrecruiters'}:
        rows, warnings, employer_statuses = _smartrecruiters(criteria)
        offers.extend(rows)
        result['warnings'].extend(warnings)
        successful = [status for status in employer_statuses if status['status'] in {'ok', 'partial'}]
        if successful:
            result['sources'].append('SmartRecruiters')
        result['source_status'].append({
            'id': 'smartrecruiters', 'label': 'Employeurs France',
            'status': 'ok' if all(status['status'] == 'ok' for status in employer_statuses) else 'partial' if successful else 'error',
            'count': len(rows), 'message': 'Annonces publiques françaises de 4 employeurs, sans clé API.',
            'employers': employer_statuses,
        })
    if source in {'all', 'france-travail'}:
        client_id = os.environ.get('FRANCE_TRAVAIL_CLIENT_ID', '').strip()
        secret = os.environ.get('FRANCE_TRAVAIL_CLIENT_SECRET', '').strip()
        if not client_id or not secret:
            message = ('France Travail non configuré : accès API optionnel, soumis à l’accord de France Travail. '
                       'Les offres des employeurs français fonctionnent sans cet accès.')
            result['source_status'].append({'id': 'france-travail', 'label': 'France Travail',
                                            'status': 'not_configured', 'count': 0, 'message': message})
            if source == 'france-travail':
                result['warnings'].append(message)
        else:
            try:
                rows, truncated = _france_travail(criteria, client_id, secret)
                offers.extend(rows)
                result['sources'].append('France Travail')
                result['source_status'].append({'id': 'france-travail', 'label': 'France Travail',
                                                'status': 'ok', 'count': 0,
                                                'message': 'Source complémentaire connectée.'})
                if truncated:
                    result['warnings'].append('France Travail : recherche limitée aux 150 premières offres par groupe de mots-clés ; affiner la recherche.')
                if criteria.get('contract', 'stage') in {'stage', 'alternance'}:
                    result['warnings'].append('France Travail ne couvre pas tous les stages et alternances ; seules les offres dont le contrat est attesté sont retenues.')
            except (SourceError, ValueError) as exc:
                result['warnings'].append(str(exc))
                result['source_status'].append({'id': 'france-travail', 'label': 'France Travail',
                                                'status': 'error', 'count': 0, 'message': str(exc)})
    french_search = location == 'france' or bool(criteria.get('city_code'))
    if source == 'all' and french_search:
        result['source_status'].append({'id': 'arbeitnow', 'label': 'Arbeitnow',
                                        'status': 'out_of_scope', 'count': 0,
                                        'message': 'Flux Allemagne/Europe écarté pour cette recherche française ; disponible en sélection explicite.'})
    elif source in {'all', 'arbeitnow'}:
        result['warnings'].append('Arbeitnow est un flux gratuit principalement orienté Allemagne/Europe. Il ne garantit pas des stages en France ; une offre à distance peut imposer un pays de résidence.')
        try:
            rows, truncated = _arbeitnow_feed()
            offers.extend(rows)
            result['sources'].append('Arbeitnow')
            result['source_status'].append({'id': 'arbeitnow', 'label': 'Arbeitnow',
                                            'status': 'ok', 'count': 0,
                                            'message': 'Flux européen gratuit ; couverture principalement allemande.'})
            if truncated:
                result['warnings'].append('Arbeitnow : recherche limitée aux 3 premières pages du flux ; la couverture est partielle.')
            if location and location not in {'france', 'all', 'partout', 'europe', 'world', 'monde'}:
                result['warnings'].append('Arbeitnow ne fournit pas de coordonnées : pour cette source, la ville est filtrée par son nom et le rayon ne peut pas être calculé.')
        except SourceError as exc:
            result['warnings'].append(str(exc))
            result['source_status'].append({'id': 'arbeitnow', 'label': 'Arbeitnow',
                                            'status': 'error', 'count': 0, 'message': str(exc)})
    unique = {offer['id']: offer for offer in offers if match_offer(offer, criteria)}
    for offer in unique.values():
        offer['search_relevance'] = rank_offer(offer, criteria)
    result['offers'] = sorted(unique.values(), key=lambda offer: (
        offer['search_relevance']['score'], offer.get('published_at', '')), reverse=True)[:500]
    labels = {'smartrecruiters': 'SmartRecruiters', 'france-travail': 'France Travail', 'arbeitnow': 'Arbeitnow'}
    for status in result['source_status']:
        status['count'] = sum(offer['source'] == labels.get(status['id']) for offer in result['offers'])
    return result
