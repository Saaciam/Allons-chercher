#!/usr/bin/env python3
"""Interface locale de recherche, alertes et candidatures. python web_app.py --open"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from applications import BASE_DIR, analyze_offer, json_bytes, load_profile, month_label, validate_offer
from dashboard_store import DashboardStore
from job_sources import match_offer, resolve_location, reverse_location, search_jobs
from send_emails import load_dotenv
from settings_service import settings, update_settings
from workflow_service import IncompleteApplicationError, WorkflowService


def criteria(values, *, alert=False):
    if not isinstance(values, dict):
        raise ValueError('Renseigne les critères de recherche.')
    result = {}
    for key in ('keywords', 'location', 'city_code'):
        value = values.get(key, '')
        if not isinstance(value, str) or len(value) > (300 if key == 'keywords' else 200):
            raise ValueError(f'Champ {key} invalide.')
        result[key] = value.strip()
    if not result['keywords']:
        raise ValueError('Renseigne le métier ou les mots-clés recherchés.')
    if isinstance(values.get('radius_km'), bool):
        raise ValueError('Le rayon doit être un nombre de kilomètres.')
    try:
        result['radius_km'] = int(values.get('radius_km', 30))
    except (ValueError, TypeError):
        raise ValueError('Le rayon doit être un nombre de kilomètres.') from None
    if not 0 <= result['radius_km'] <= 200:
        raise ValueError('Choisis un rayon entre 0 et 200 km.')
    result['contract'] = values.get('contract', 'stage')
    if result['contract'] not in ('stage', 'alternance', 'cdi', 'cdd', 'all'):
        raise ValueError('Type de contrat invalide.')
    for key in ('remote', 'enabled'):
        value = values.get(key, False if key == 'remote' else True)
        if not isinstance(value, bool):
            raise ValueError(f'Champ {key} invalide.')
        result[key] = value
    for key, limit in (('latitude', 90), ('longitude', 180)):
        value = values.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -limit <= value <= limit:
                raise ValueError('Coordonnées géographiques invalides.')
        result[key] = value
    if (result['latitude'] is None) != (result['longitude'] is None):
        raise ValueError('La latitude et la longitude doivent être renseignées ensemble.')
    result['source'] = values.get('source', 'all')
    if result['source'] not in ('all', 'france-travail', 'arbeitnow', 'smartrecruiters'):
        raise ValueError('Source d’offres invalide.')
    if alert:
        name = values.get('name', '')
        if not isinstance(name, str):
            raise ValueError('Le nom de l’alerte doit être du texte.')
        result['name'] = name.strip() or result['keywords']
        if len(result['name']) > 120:
            raise ValueError('Le nom de l’alerte est trop long.')
    return result


class Dashboard:
    def __init__(self, base_dir=BASE_DIR, *, token=None, search_provider=search_jobs):
        self.base_dir = Path(base_dir)
        self.profile_path = self.base_dir / 'candidate_profile.json'
        self.store = DashboardStore(self.base_dir / 'data' / 'dashboard.json')
        self.workflow = WorkflowService(self.base_dir)
        self.csrf_token = token or secrets.token_urlsafe(32)
        self.search_provider = search_provider
        self.scan_lock = threading.Lock()
        self.operation_lock = threading.RLock()
        self.sending = set()
        self.stop = threading.Event()

    def state(self):
        data = self.store.snapshot()
        apps = self.workflow.list_applications()
        for application in apps:
            stored = data['application_statuses'].get(application['id'], {})
            application['status'] = stored.get('status', 'ready')
            if stored:
                application['sent_at'] = stored.get('sent_at')
        return {
            'profile': load_profile(self.profile_path), 'preferences': data['preferences'],
            'alerts': data['alerts'], 'offers': list(reversed(data['offers'])),
            'notifications': data['notifications'], 'applications': apps,
            'contacts': self.workflow.list_contacts(), 'csrf_token': self.csrf_token,
            'checking': self.scan_lock.locked(),
            'search_defaults': settings(data['preferences'])['defaults'],
            'last_search': data.get('last_search'),
            'integrations': {
                'hunter': bool(os.environ.get('HUNTER_API_KEY')),
                'smtp': bool(os.environ.get('EMAIL_ADDRESS') and os.environ.get('EMAIL_PASSWORD')),
                'france_travail': bool(os.environ.get('FRANCE_TRAVAIL_CLIENT_ID') and os.environ.get('FRANCE_TRAVAIL_CLIENT_SECRET')),
            },
        }

    def settings(self):
        return settings(self.store.snapshot()['preferences'])

    def personalized_offers(self, entries):
        """Expliquer les correspondances avec les acquis réellement présents dans le CV."""
        profile = load_profile(self.profile_path)
        skill_names = {skill['id']: skill['label'] for skill in profile['skills']}
        offers = []
        for raw in entries:
            offer = validate_offer(raw)
            analysis = analyze_offer(profile, offer)
            offer['matching_skills'] = [skill_names[key] for key in analysis['ordered_skill_ids']
                                       if key in analysis['matched_skill_ids']]
            offer['profile_notes'] = analysis['review_notes']
            offer['unverified_technologies'] = analysis['unverified_technologies']
            offers.append(offer)
        return offers

    def check_alerts(self, *, force=True):
        if not self.scan_lock.acquire(blocking=False):
            return {'new_count': 0, 'warnings': ['Une vérification est déjà en cours.']}
        notices, warnings = 0, []
        try:
            now = datetime.now(timezone.utc)
            for alert in self.store.snapshot()['alerts']:
                if not alert.get('enabled'):
                    continue
                if not force and alert.get('last_checked_at'):
                    elapsed = (now - datetime.fromisoformat(alert['last_checked_at'])).total_seconds()
                    if elapsed < 30 * 60:
                        continue
                try:
                    result = self.search_provider(alert)
                    offers = self.personalized_offers(result.get('offers', []))
                    notice = self.store.add_offers(offers, match_offer)
                    notices += notice['new_count']
                    # Les offres importées ou vues par une autre recherche sont aussi examinées.
                    notices += self.store.add_offers(self.store.snapshot()['offers'], match_offer)['new_count']
                    alert_warnings = result.get('warnings', [])
                except Exception:
                    # Les exceptions requests peuvent inclure un jeton ou une URL confidentielle.
                    alert_warnings = ['La source d’offres est momentanément indisponible. Réessaie plus tard.']
                warnings.extend(alert_warnings)
                self.store.checked(alert['id'], alert_warnings)
            return {'new_count': notices, 'warnings': list(dict.fromkeys(warnings))}
        finally:
            self.scan_lock.release()

    def scheduler(self):
        while not self.stop.wait(15):
            self.check_alerts(force=False)

    def offer(self, offer_id):
        offer = next((o for o in self.store.snapshot()['offers'] if o['id'] == offer_id), None)
        if offer is None:
            raise ValueError('Offre introuvable.')
        return offer

    def update_profile(self, values):
        if not isinstance(values, dict):
            raise ValueError('Profil invalide.')
        with self.operation_lock:
            profile = load_profile(self.profile_path)
            previous_internship = dict(profile['internship'])
            previous_summary = profile.get('summary', '')
            allowed = {'identity': {'first_name', 'last_name', 'display_name', 'email', 'phone', 'linkedin', 'github'},
                       'internship': {'duration_months', 'available_from', 'mobility'}}
            for section, keys in allowed.items():
                if section in values:
                    if not isinstance(values[section], dict) or set(values[section]) - keys:
                        raise ValueError('Champs du profil invalides.')
                    profile[section].update(values[section])
            if 'summary' in values:
                if not isinstance(values['summary'], str):
                    raise ValueError('Le résumé doit être du texte.')
                profile['summary'] = values['summary']
            if profile.get('summary') == previous_summary:
                profile['summary'] = profile.get('summary', '').replace(
                    f"stage de {previous_internship['duration_months']} mois",
                    f"stage de {profile['internship']['duration_months']} mois",
                ).replace(month_label(previous_internship['available_from']),
                          month_label(profile['internship']['available_from']))
            # Les champs d’identité servent aussi aux en-têtes de courrier.
            from send_emails import validate_email, validate_header
            validate_email(profile['identity']['email'])
            for key in ('first_name', 'last_name', 'display_name'):
                validate_header(profile['identity'][key], key)
            temporary = self.base_dir / 'data' / 'profile.tmp.json'
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(json_bytes(profile))
            try:
                load_profile(temporary)
                temporary.replace(self.profile_path)
            finally:
                temporary.unlink(missing_ok=True)
            return {'profile': profile, 'message': 'Profil enregistré. Régénère les dossiers concernés pour actualiser les documents.'}

    def post(self, path, body):
        if not isinstance(body, dict):
            raise ValueError('Le formulaire doit être un objet JSON.')
        if path == '/api/settings':
            return update_settings(self.base_dir, body, self.store.snapshot()['preferences'])
        if path == '/api/preferences':
            checked = criteria({**body, 'keywords': 'préférences'})
            keys = ('location', 'city_code', 'radius_km', 'latitude', 'longitude', 'contract', 'remote', 'source')
            if 'keywords' in body:
                checked['keywords'] = criteria(body)['keywords']
                keys += ('keywords',)
            return {'preferences': self.store.preferences({key: checked[key] for key in keys})}
        if path == '/api/profile':
            return self.update_profile(body)
        if path == '/api/alerts':
            return {'alert': self.store.upsert_alert(criteria(body, alert=True))}
        if path.startswith('/api/alerts/') and path != '/api/alerts/check':
            alert_id = path.rsplit('/', 1)[-1]
            existing = next((a for a in self.store.snapshot()['alerts'] if a['id'] == alert_id), None)
            if not existing:
                raise ValueError('Alerte introuvable.')
            return {'alert': self.store.upsert_alert(criteria({**existing, **body}, alert=True), alert_id)}
        if path == '/api/alerts/check':
            return self.check_alerts()
        if path == '/api/search':
            checked = criteria(body)
            result = self.search_provider(checked)
            result['offers'] = self.personalized_offers(result.get('offers', []))
            self.store.add_offers(result['offers'], match_offer)
            self.store.record_search(checked, result)
            return result
        if path == '/api/offers':
            entries = body.get('offers', [body])
            if not isinstance(entries, list) or not entries:
                raise ValueError('Aucune offre à importer.')
            offers = [validate_offer(offer) for offer in entries]
            for offer in offers:
                offer.setdefault('source', 'manuel')
            return {**self.store.add_offers(offers, match_offer), 'offers': offers}
        if path == '/api/location/reverse':
            checked = criteria({**body, 'keywords': 'localisation'})
            if checked['latitude'] is None:
                raise ValueError('Coordonnées manquantes.')
            return {'location': reverse_location(checked['latitude'], checked['longitude'])}
        if path == '/api/notifications/read':
            self.store.mark_read()
            return {'message': 'Notifications lues.'}
        if path == '/api/contacts/search':
            return self.workflow.search_contacts_detail(body.get('company', ''), body.get('department', 'hr'),
                                                       domain=body.get('domain'), force=body.get('force', False))
        if path == '/api/contacts':
            return {'contact': self.workflow.upsert_contact(body)}
        offer_match = re.fullmatch(r'/api/offers/([a-z0-9_-]+)/prepare', path)
        if offer_match:
            with self.operation_lock:
                return {'application': self.workflow.prepare(self.offer(offer_match[1]))}
        app_match = re.fullmatch(r'/api/applications/([a-z0-9_-]+)/(preview|send|draft|letter|regenerate)', path)
        if app_match:
            application_id, action = app_match.groups()
            if 'recipient' in body:
                recipient = body['recipient']
            elif any(key in body for key in ('email', 'prenom', 'nom')):
                recipient = {key: body.get(key, '') for key in ('email', 'prenom', 'nom')}
            else:
                recipient = None
            if action == 'preview':
                return self.workflow.preview(application_id, recipient, body.get('subject'), body.get('body'))
            if action == 'send':
                if body.get('confirmed') is not True:
                    raise ValueError('Relis le message et confirme l’envoi dans l’interface.')
                with self.operation_lock:
                    status = self.store.snapshot()['application_statuses'].get(application_id, {})
                    if application_id in self.sending or status.get('status') == 'sent':
                        raise ValueError('Cette candidature a déjà été envoyée ou son envoi est en cours.')
                    self.sending.add(application_id)
                try:
                    result = self.workflow.send(application_id, recipient, body.get('subject'), body.get('body'))
                    self.store.sent(application_id, result.get('recipient') or recipient)
                    return result
                finally:
                    with self.operation_lock:
                        self.sending.discard(application_id)
            with self.operation_lock:
                if action == 'regenerate':
                    if body.get('confirmed') is not True:
                        raise ValueError('Confirme la régénération : les documents et les textes personnalisés seront remplacés.')
                    try:
                        offer = self.workflow.recover_offer(application_id)
                    except (OSError, ValueError):
                        offer = self.offer(application_id)
                    return {'application': self.workflow.prepare(offer, overwrite=True)}
                if action == 'letter':
                    return {'application': self.workflow.update_texts(application_id, letter_text=body.get('text'))}
                return {'application': self.workflow.update_texts(application_id, subject=body.get('subject'), body=body.get('body'))}
        raise ValueError('Action introuvable.')


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, dashboard):
        self.dashboard = dashboard
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'MonStage/1.0'

    def log_message(self, *args):
        # Éviter de copier les formulaires ou coordonnées personnelles dans le terminal.
        pass

    def allowed(self):
        host = self.headers.get('Host', '')
        port = self.server.server_address[1]
        return host in (f'127.0.0.1:{port}', f'localhost:{port}')

    def output(self, status, content, content_type='application/json; charset=utf-8', filename=None):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; frame-ancestors 'self'")
        if filename:
            self.send_header('Content-Disposition', f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def error(self, message, status=400, **details):
        self.output(status, {'error': message, **details})

    def reject_mutation(self, message, status=403):
        # Closing with an unread POST body can reset the TCP connection on
        # Windows before the client receives our error. Discard bounded bytes,
        # without interpreting the form or letting a slow sender hold a worker.
        try:
            remaining = min(max(int(self.headers.get('Content-Length', '0')), 0), 2_000_000)
        except (ValueError, TypeError):
            remaining = 0
        previous_timeout = self.connection.gettimeout()
        deadline = time.monotonic() + 1
        try:
            while remaining:
                available = deadline - time.monotonic()
                if available <= 0:
                    break
                self.connection.settimeout(available)
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous_timeout)
        self.error(message, status)

    def do_GET(self):
        if not self.allowed():
            return self.error('Accès local uniquement.', 403)
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        dashboard = self.server.dashboard
        try:
            if path == '/api/state':
                return self.output(200, dashboard.state())
            if path == '/api/settings':
                return self.output(200, dashboard.settings())
            if path == '/api/locations':
                query = parse_qs(parsed.query).get('q', [''])[0]
                return self.output(200, {'locations': resolve_location(query)})
            document = re.fullmatch(r'/api/applications/([a-z0-9_-]+)/files/(cv|letter)', path)
            if document:
                file_path = dashboard.workflow.get_document(document[1], document[2])
                return self.output(200, file_path.read_bytes(), 'application/pdf', file_path.name)
            filename = 'index.html' if path == '/' else (
                path.removeprefix('/static/') if path.startswith('/static/') else path.removeprefix('/')
            )
            static = (dashboard.base_dir / 'static').resolve()
            file_path = (static / filename).resolve()
            if not file_path.is_relative_to(static) or not file_path.is_file() or file_path.suffix not in ('.html', '.css', '.js', '.svg', '.ico'):
                return self.error('Page introuvable.', 404)
            content_type = mimetypes.guess_type(str(file_path))[0] or 'application/octet-stream'
            self.output(200, file_path.read_bytes(), content_type + '; charset=utf-8')
        except (ValueError, FileNotFoundError) as exc:
            self.error(str(exc))
        except Exception:
            self.error('Impossible de charger les données. Vérifie les fichiers du projet.', 500)

    def mutation(self, *, delete=False):
        if not self.allowed():
            return self.reject_mutation('Accès local uniquement.')
        dashboard = self.server.dashboard
        token = self.headers.get('X-CSRF-Token', '')
        if not secrets.compare_digest(token, dashboard.csrf_token):
            return self.reject_mutation('Recharge l’interface avant cette action.')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.reject_mutation('Origine de la demande invalide.')
        try:
            path = urlsplit(self.path).path
            if delete:
                match = re.fullmatch(r'/api/alerts/([a-z0-9]+)', path)
                if not match:
                    return self.error('Action introuvable.', 404)
                dashboard.store.delete_alert(match[1])
                return self.output(200, {'message': 'Alerte supprimée.'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 2_000_000:
                return self.error('Le formulaire est trop volumineux.', 413)
            body = json.loads(self.rfile.read(length) or b'{}')
            self.output(200, dashboard.post(path, body))
        except IncompleteApplicationError as exc:
            can_regenerate = exc.can_regenerate or any(offer['id'] == exc.application_id
                                                       for offer in dashboard.store.snapshot()['offers'])
            self.error(str(exc), 409, code=exc.code, application_id=exc.application_id,
                       can_regenerate=can_regenerate)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            self.error(str(exc))
        except Exception:
            self.error('L’action a échoué. Vérifie la connexion et la configuration du service.', 502)

    def do_POST(self):
        self.mutation()

    def do_DELETE(self):
        self.mutation(delete=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true', help='ouvre l’interface dans le navigateur')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Le port doit être compris entre 1 et 65535.')
    load_dotenv()
    dashboard = Dashboard()
    try:
        server = AppServer(('127.0.0.1', args.port), dashboard)
    except OSError:
        print(f'Le port {args.port} est indisponible. Essaie --port 8766 ou ferme l’autre instance.')
        return 1
    worker = threading.Thread(target=dashboard.scheduler, name='job-alerts', daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{args.port}'
    print(f'Interface disponible : {url}\nGarde ce terminal ouvert. Ctrl+C pour arrêter.')
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.stop.set()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
