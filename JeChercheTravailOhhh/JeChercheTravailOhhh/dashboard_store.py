"""État persistant de l’interface, séparé du profil et des documents."""

from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class DashboardStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {
            'schema_version': 1,
            'preferences': {'location': '', 'city_code': '', 'latitude': None,
                            'longitude': None, 'radius_km': 30},
            'alerts': [], 'offers': [], 'notifications': [], 'application_statuses': {},
        }
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(loaded, dict) or loaded.get('schema_version') != 1:
                raise ValueError('Les données de l’interface sont invalides.')
            self.data.update(loaded)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(self.path)

    def preferences(self, values):
        with self.lock:
            self.data['preferences'].update(values)
            self.save()
            return copy.deepcopy(self.data['preferences'])

    def upsert_alert(self, alert, alert_id=None):
        with self.lock:
            existing = next((a for a in self.data['alerts'] if a['id'] == alert_id), None)
            if alert_id and not existing:
                raise ValueError('Alerte introuvable.')
            if existing:
                existing.update(alert)
                # Une modification des critères doit pouvoir être vérifiée aussitôt.
                existing['last_checked_at'] = None
                existing['last_error'] = ''
                result = existing
            else:
                result = {**alert, 'id': uuid.uuid4().hex, 'created_at': timestamp(),
                          'last_checked_at': None, 'last_error': ''}
                self.data['alerts'].append(result)
            self.save()
            return copy.deepcopy(result)

    def delete_alert(self, alert_id):
        with self.lock:
            original = len(self.data['alerts'])
            self.data['alerts'] = [a for a in self.data['alerts'] if a['id'] != alert_id]
            if len(self.data['alerts']) == original:
                raise ValueError('Alerte introuvable.')
            self.save()

    def add_offers(self, offers, matcher):
        """Ajouter des offres et notifier une seule fois chaque couple alerte/offre."""
        with self.lock:
            existing = {o['id']: o for o in self.data['offers']}
            added, notices = 0, 0
            for offer in offers:
                previous = existing.get(offer['id'])
                if previous is None:
                    previous = {**offer, 'seen_at': timestamp(), 'alert_ids': []}
                    self.data['offers'].append(previous)
                    existing[offer['id']] = previous
                    added += 1
                else:
                    links = previous.get('alert_ids', [])
                    previous.update(offer)
                    previous['alert_ids'] = links
                for alert in self.data['alerts']:
                    if alert.get('enabled') and alert['id'] not in previous['alert_ids'] and matcher(previous, alert):
                        previous['alert_ids'].append(alert['id'])
                        self.data['notifications'].insert(0, {
                            'id': uuid.uuid4().hex, 'title': 'Nouvelle offre pour ' + alert['name'],
                            'message': f"{previous['intitule']} — {previous['entreprise']}",
                            'offer_id': previous['id'], 'alert_id': alert['id'],
                            'created_at': timestamp(), 'read': False,
                        })
                        notices += 1
            self.save()
            return {'added_count': added, 'new_count': notices}

    def checked(self, alert_id, warnings):
        with self.lock:
            alert = next((a for a in self.data['alerts'] if a['id'] == alert_id), None)
            if alert:
                alert['last_checked_at'] = timestamp()
                alert['last_error'] = '\n'.join(warnings)
                self.save()

    def record_search(self, criteria, result):
        with self.lock:
            self.data['last_search'] = {
                'criteria': copy.deepcopy(criteria),
                'offer_ids': [offer['id'] for offer in result.get('offers', [])],
                'warnings': list(result.get('warnings', [])),
                'source_status': copy.deepcopy(result.get('source_status', [])),
                'searched_at': timestamp(),
            }
            self.save()
            return copy.deepcopy(self.data['last_search'])

    def mark_read(self):
        with self.lock:
            for notification in self.data['notifications']:
                notification['read'] = True
            self.save()

    def sent(self, application_id, recipient):
        with self.lock:
            self.data['application_statuses'][application_id] = {
                'status': 'sent', 'sent_at': timestamp(), 'recipient_email': recipient['email'],
            }
            self.save()
