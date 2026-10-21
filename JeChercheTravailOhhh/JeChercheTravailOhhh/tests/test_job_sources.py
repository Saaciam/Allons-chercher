"""Contrôles hors réseau des sources, filtres et erreurs publiques."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_sources as jobs
from applications import validate_offer


def response(payload=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


ARBEITNOW_ROW = {
    'slug': 'data-intern-test', 'company_name': 'Example',
    'title': 'Data science internship',
    'description': '<p>Python et <b>Machine Learning</b>.</p><script>ne pas afficher</script>',
    'location': 'Paris, France', 'remote': True,
    'url': 'https://www.arbeitnow.com/jobs/test', 'created_at': 1791446400,
    'job_types': ['Full-time', 'Internship'],
}
FT_ROW = {
    'id': '123ABCD', 'intitule': 'Stage data engineer',
    'description': 'Python et SQL. Télétravail possible.',
    'entreprise': {'nom': 'Société Exemple'}, 'typeContrat': 'STG',
    'typeContratLibelle': 'Stage', 'dateCreation': '2026-10-08T10:00:00Z',
    'lieuTravail': {'commune': '35238', 'libelle': '35 - RENNES',
                    'latitude': 48.1173, 'longitude': -1.6778},
    'origineOffre': {'urlOrigine': 'https://candidat.francetravail.fr/offres/test'},
}
SR_ROW = {
    'id': '744000123456789', 'name': 'Stage - Data Engineer',
    'company': {'name': 'Exemple France'}, 'active': True,
    'releasedDate': '2026-10-08T10:00:00Z',
    'location': {'city': 'Courbevoie', 'country': 'fr', 'latitude': '48.895',
                 'longitude': '2.242', 'remote': False, 'hybrid': True},
    'typeOfEmployment': {'id': 'permanent', 'label': 'Full-time'},
    'customField': [{'fieldLabel': 'Nature of Contract', 'valueId': 'STG015', 'valueLabel': 'Training'},
                    {'fieldLabel': 'Duration (in Months)', 'valueLabel': '6'}],
    'postingUrl': 'https://jobs.smartrecruiters.com/Example/744000123456789',
    'ref': 'http://127.0.0.1/unsafe',
    'jobAd': {'sections': {
        'companyDescription': {'text': '<p>Groupe spécialisé en data et IA.</p>'},
        'jobDescription': {'text': '<p>Construire des pipelines de données Python et SQL.</p>'},
        'qualifications': {'text': '<p>Master 2, Python, ETL.</p>'},
    }},
}


class JobSourcesTests(unittest.TestCase):
    def setUp(self):
        jobs._CACHE.clear()
        self.env = patch.dict(os.environ, {
            'FRANCE_TRAVAIL_CLIENT_ID': '', 'FRANCE_TRAVAIL_CLIENT_SECRET': '',
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        jobs._CACHE.clear()

    def test_keywords_are_or_groups_and_aliases_have_word_boundaries(self):
        self.assertTrue(jobs.matching_keywords('LLM engineer', 'data, intelligence artificielle'))
        self.assertTrue(jobs.matching_keywords('Stage Ingénieure GenAI / NLP', 'intelligence artificielle'))
        self.assertTrue(jobs.matching_keywords('Business intelligence analyst', 'BI'))
        self.assertTrue(jobs.matching_keywords('Analyste de données', 'data'))
        self.assertTrue(jobs.matching_keywords('Ingénieur SQL utilisant Python', 'Python SQL'))
        self.assertFalse(jobs.matching_keywords('Ingénieur SQL', 'Python SQL'))
        self.assertFalse(jobs.matching_keywords('Compagnie régionale', 'IA'))
        self.assertFalse(jobs.matching_keywords('Database operations', 'BI'))

    def test_contract_evidence_not_full_time_or_description(self):
        offer = {'intitule': 'Data analyst', 'description': 'We also employ interns.',
                 'contract': 'Full-time', 'lieu': 'Paris', 'source': 'manual'}
        self.assertFalse(jobs.match_offer(offer, {'keywords': 'data', 'contract': 'stage'}))
        self.assertFalse(jobs.match_offer(offer, {'contract': 'cdi'}))
        offer['intitule'] = 'Stage Data analyst'
        self.assertTrue(jobs.match_offer(offer, {'contract': 'stage'}))
        offer['contract'] = 'CDI'
        self.assertFalse(jobs.match_offer(offer, {'contract': 'stage'}))

    def test_location_coordinates_apply_radius_including_neighbouring_city(self):
        criteria = {'location': 'Rennes (35)', 'latitude': 48.1173,
                    'longitude': -1.6778, 'radius_km': 30, 'contract': 'all'}
        nearby = {'lieu': 'Cesson-Sévigné', 'latitude': 48.1200, 'longitude': -1.6035}
        far = {'lieu': 'Paris', 'latitude': 48.8566, 'longitude': 2.3522}
        self.assertTrue(jobs.match_offer(nearby, criteria))
        self.assertFalse(jobs.match_offer(far, criteria))
        self.assertTrue(jobs.match_offer({'lieu': '35 - RENNES'}, criteria))
        self.assertFalse(jobs.match_offer({'lieu': 'Paris'}, criteria))

    def test_remote_does_not_remove_geographical_requirements(self):
        offer = {'intitule': 'Stage Data', 'description': 'Python',
                 'remote': True, 'lieu': 'Berlin, Germany', 'source': 'Arbeitnow'}
        self.assertFalse(jobs.match_offer(offer, {'location': 'France', 'remote': True}))
        self.assertTrue(jobs.match_offer(offer, {'location': 'Europe', 'remote': True}))
        offer['remote'] = False
        self.assertFalse(jobs.match_offer(offer, {'location': 'Europe', 'remote': True}))

    def test_imported_country_is_not_assumed_to_be_france(self):
        offer = {'intitule': 'Stage Data', 'description': 'Python',
                 'lieu': 'Berlin', 'source': 'manuel'}
        self.assertFalse(jobs.match_offer(offer, {'location': 'France'}))
        offer['lieu'] = 'Rennes'
        self.assertFalse(jobs.match_offer(offer, {'location': 'France'}))
        self.assertTrue(jobs.match_offer(offer, {'location': 'Rennes'}))
        offer['country'] = 'France'
        self.assertTrue(jobs.match_offer(offer, {'location': 'France'}))
        offer['country'] = 'Germany'
        self.assertFalse(jobs.match_offer(offer, {'location': 'France'}))

    @patch('job_sources.requests.get')
    def test_arbeitnow_normalizes_html_and_cache_is_shared(self, get):
        get.return_value = response({'data': [ARBEITNOW_ROW], 'links': {'next': None}})
        criteria = {'keywords': 'data', 'contract': 'stage', 'location': 'France', 'source': 'arbeitnow'}
        result = jobs.search_jobs(criteria)
        self.assertEqual(result['sources'], ['Arbeitnow'])
        offer = result['offers'][0]
        self.assertEqual(offer['description'], 'Python et Machine Learning.')
        self.assertEqual(offer['contract'], 'stage')
        self.assertEqual(validate_offer(offer), offer)
        self.assertNotIn('date_debut', offer)
        self.assertNotIn('duree_mois', offer)
        self.assertTrue(any(status['id'] == 'arbeitnow' and status['status'] == 'ok'
                            for status in result['source_status']))
        result['offers'][0]['description'] = 'mutated'
        other = jobs.search_jobs({**criteria, 'keywords': 'AI'})
        self.assertEqual(other['offers'][0]['description'], 'Python et Machine Learning.')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], jobs.ARBEITNOW_URL)
        self.assertEqual(get.call_args.kwargs['timeout'], jobs.REQUEST_TIMEOUT)

    @patch('job_sources.requests.get')
    def test_pagination_never_fetches_arbitrary_next_link(self, get):
        get.return_value = response({'data': [ARBEITNOW_ROW],
                                    'links': {'next': 'http://127.0.0.1/private'}})
        result = jobs.search_jobs({'source': 'arbeitnow', 'contract': 'all', 'location': 'Europe'})
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(result['offers']), 1)
        self.assertTrue(any('3 premières pages' in warning for warning in result['warnings']))
        self.assertTrue(all(call.args[0] == jobs.ARBEITNOW_URL for call in get.call_args_list))
        self.assertEqual([call.kwargs['params']['page'] for call in get.call_args_list], [1, 2, 3])

    @patch('job_sources.requests.post')
    @patch('job_sources.requests.get')
    def test_france_travail_uses_oauth_geo_parameters_and_strict_stage(self, get, post):
        with patch.dict(os.environ, {'FRANCE_TRAVAIL_CLIENT_ID': 'example-id',
                                    'FRANCE_TRAVAIL_CLIENT_SECRET': 'example-secret'}):
            post.return_value = response({'access_token': 'test-token', 'expires_in': 600})
            cdi = {**deepcopy(FT_ROW), 'id': 'other', 'typeContrat': 'CDI'}
            cdi['typeContratLibelle'] = 'Contrat à durée indéterminée'
            get.return_value = response({'resultats': [FT_ROW, cdi]})
            criteria = {'source': 'france-travail', 'location': 'Rennes (35)',
                        'city_code': '35238', 'latitude': 48.1173, 'longitude': -1.6778,
                        'radius_km': 30, 'keywords': 'data', 'contract': 'stage'}
            result = jobs.search_jobs(criteria)
            self.assertEqual(result['sources'], ['France Travail'])
            self.assertEqual(len(result['offers']), 1)
            offer = result['offers'][0]
            self.assertFalse(offer['remote'])
            self.assertTrue(offer['hybrid'])
            self.assertEqual(validate_offer(offer), offer)
            self.assertEqual(post.call_args.args[0], jobs.TOKEN_URL)
            self.assertEqual(post.call_args.kwargs['params'], {'realm': '/partenaire'})
            self.assertEqual(post.call_args.kwargs['data']['grant_type'], 'client_credentials')
            params = get.call_args.kwargs['params']
            self.assertEqual(params['commune'], '35238')
            self.assertEqual(params['distance'], 30)
            self.assertNotIn('typeContrat', params)
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')
            jobs.search_jobs(criteria)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(post.call_count, 1)

    @patch('job_sources.requests.post')
    @patch('job_sources.requests.get')
    @patch('job_sources._smartrecruiters', return_value=([], [], []))
    def test_one_source_failure_does_not_block_other_or_leak_secrets(self, smart, get, post):
        with patch.dict(os.environ, {'FRANCE_TRAVAIL_CLIENT_ID': 'private-id',
                                    'FRANCE_TRAVAIL_CLIENT_SECRET': 'private-secret'}):
            post.side_effect = requests.ConnectionError('private-secret appears in a diagnostic URL')
            get.return_value = response({'data': [ARBEITNOW_ROW], 'links': {}})
            result = jobs.search_jobs({'location': 'Europe', 'keywords': 'data'})
            self.assertEqual(len(result['offers']), 1)
            self.assertIn('Arbeitnow', result['sources'])
            self.assertNotIn('private-secret', str(result))
            self.assertNotIn('private-id', str(result))

    @patch('job_sources.requests.get')
    def test_invalid_filters_do_not_trigger_network(self, get):
        for invalid in ({'radius_km': -1}, {'source': 'localhost'}, {'latitude': float('nan'), 'longitude': 0},
                        {'city_code': '123456789'}, {'remote': 'true'}, {'keywords': ['Python']}):
            result = jobs.search_jobs(invalid)
            self.assertEqual(result['offers'], [])
            self.assertTrue(result['warnings'])
        get.assert_not_called()

    @patch('job_sources.requests.get')
    def test_geo_city_lookup_postal_lookup_cache_and_gps_reverse(self, get):
        get.return_value = response([{'nom': 'Rennes', 'code': '35238',
                                      'centre': {'coordinates': [-1.6778, 48.1173]},
                                      'departement': {'code': '35'}}])
        located = jobs.resolve_location('Rennes')
        self.assertEqual(located[0]['label'], 'Rennes (35)')
        self.assertEqual(located[0]['latitude'], 48.1173)
        self.assertEqual(get.call_args.kwargs['params']['nom'], 'rennes')
        jobs.resolve_location('Rennes')
        self.assertEqual(get.call_count, 1)
        jobs.resolve_location('35000')
        self.assertEqual(get.call_args.kwargs['params']['codePostal'], '35000')
        reverse = jobs.reverse_location(48.12, -1.68)
        self.assertEqual(reverse['city_code'], '35238')
        self.assertEqual(reverse['latitude'], 48.12)
        self.assertEqual(get.call_args.kwargs['params']['lat'], 48.12)
        self.assertTrue(all(call.args[0] == jobs.GEO_URL for call in get.call_args_list))
        with self.assertRaises(ValueError):
            jobs.reverse_location(100, 0)

    @patch('job_sources.requests.get')
    def test_errors_and_empty_http_response_are_distinguishable(self, get):
        get.return_value = response({}, status=429)
        result = jobs.search_jobs({'source': 'arbeitnow', 'location': 'Europe', 'contract': 'all'})
        self.assertTrue(any('limite de requêtes' in message for message in result['warnings']))
        self.assertEqual(result['sources'], [])
        get.return_value = response({}, status=204)
        result = jobs.search_jobs({'source': 'arbeitnow', 'location': 'Europe', 'contract': 'all'})
        self.assertEqual(result['offers'], [])
        self.assertEqual(result['sources'], ['Arbeitnow'])

    def test_unsafe_links_and_negated_remote_are_not_inferred(self):
        self.assertEqual(jobs._safe_link('javascript:alert(1)'), '')
        self.assertEqual(jobs._safe_link('https://secret:password@example.com/job'), '')
        offer = jobs._normalize_ft({**FT_ROW, 'description': 'Python, sans télétravail.'})
        self.assertFalse(offer['remote'])
        self.assertNotIn('email', offer['contact'])

    def test_smart_contract_evidence_duration_and_hybrid_are_preserved(self):
        offer = jobs._normalize_smartrecruiters(SR_ROW, 'Example', 'Exemple')
        self.assertEqual(offer['contract'], 'stage')
        self.assertIn('autre contrat', offer['contract_warning'])
        self.assertEqual(offer['duree_mois'], 6)
        self.assertNotIn('date_debut', offer)
        self.assertEqual(offer['country'], 'fr')
        self.assertTrue(offer['hybrid'])
        self.assertFalse(offer['remote'])
        self.assertNotIn('Groupe spécialisé', offer['search_text'])
        self.assertEqual(validate_offer(offer), offer)
        criteria = {'location': 'Paris', 'latitude': 48.8566, 'longitude': 2.3522,
                    'radius_km': 30, 'keywords': 'data, intelligence artificielle'}
        self.assertTrue(jobs.match_offer(offer, criteria))
        self.assertFalse(jobs.match_offer(offer, {**criteria, 'remote': True}))
        remote = {**deepcopy(SR_ROW), 'location': {**SR_ROW['location'], 'remote': True, 'hybrid': False}}
        self.assertTrue(jobs._normalize_smartrecruiters(remote, 'Example', 'Exemple')['remote'])

    def test_data_privacy_and_employer_boilerplate_are_not_data_jobs(self):
        privacy = {'intitule': 'Stage Data Protection Officer', 'contract': 'stage',
                   'description': 'Protection des données personnelles et RGPD.'}
        self.assertFalse(jobs.match_offer(privacy, {'keywords': 'Data'}))
        unrelated = deepcopy(SR_ROW)
        unrelated['name'] = 'Stage chargé de ressources humaines'
        unrelated['jobAd']['sections']['jobDescription']['text'] = 'Organiser les dossiers du personnel.'
        unrelated['jobAd']['sections']['qualifications']['text'] = 'Master RH.'
        offer = jobs._normalize_smartrecruiters(unrelated, 'Example', 'Exemple')
        self.assertFalse(jobs.match_offer(offer, {'keywords': 'data, intelligence artificielle'}))
        self.assertTrue(jobs.match_offer(offer, {'keywords': 'ressources humaines'}))

    def test_search_ranking_explains_keywords_without_profile_score(self):
        offer = jobs._normalize_smartrecruiters(SR_ROW, 'Example', 'Exemple')
        ranking = jobs.rank_offer(offer, {'keywords': 'Data, Python', 'contract': 'stage'})
        self.assertEqual(ranking['matched_keywords'], ['Data', 'Python'])
        self.assertTrue(any('intitulé' in reason for reason in ranking['reasons']))
        self.assertTrue(any('missions' in reason for reason in ranking['reasons']))
        self.assertNotIn('matching_skills', ranking)

    @patch('job_sources.SMARTRECRUITERS_EMPLOYERS', (('Example', 'Exemple'),))
    @patch('job_sources.requests.get')
    def test_smart_public_sources_get_full_descriptions_cache_and_safe_detail_url(self, get):
        summary = {key: value for key, value in SR_ROW.items() if key != 'jobAd'}
        def fake_get(url, **kwargs):
            if url.endswith('/postings'):
                return response({'content': [summary], 'totalFound': 1})
            self.assertEqual(url, jobs.SMARTRECRUITERS_URL + '/Example/postings/' + SR_ROW['id'])
            return response(SR_ROW)
        get.side_effect = fake_get
        criteria = {'source': 'smartrecruiters', 'location': 'Paris (75)', 'city_code': '75056',
                    'latitude': 48.8566, 'longitude': 2.3522, 'radius_km': 30,
                    'keywords': 'data, intelligence artificielle', 'contract': 'stage'}
        result = jobs.search_jobs(criteria)
        self.assertEqual(len(result['offers']), 1)
        self.assertEqual(result['sources'], ['SmartRecruiters'])
        self.assertEqual(result['source_status'][0]['status'], 'ok')
        self.assertEqual(result['source_status'][0]['count'], 1)
        self.assertEqual(result['offers'][0]['contract'], 'stage')
        self.assertIn('search_relevance', result['offers'][0])
        self.assertEqual(get.call_count, 3)
        list_calls = [call for call in get.call_args_list if call.args[0].endswith('/postings')]
        self.assertEqual({call.kwargs['params']['q'] for call in list_calls}, {'stage', 'intern'})
        self.assertTrue(all(call.kwargs['params']['destination'] == 'PUBLIC' for call in list_calls))
        self.assertTrue(all(call.kwargs['params']['country'] == 'fr' for call in list_calls))
        jobs.search_jobs(criteria)
        self.assertEqual(get.call_count, 3)

    @patch('job_sources._smartrecruiters', return_value=([], [], [
        {'id': 'Example', 'label': 'Exemple', 'status': 'ok', 'count': 0, 'message': 'Public'},
    ]))
    @patch('job_sources.requests.get')
    @patch('job_sources.requests.post')
    def test_france_all_skips_arbeitnow_and_does_not_warn_about_optional_keys(self, post, get, smart):
        result = jobs.search_jobs({'location': 'France', 'source': 'all', 'keywords': 'data'})
        get.assert_not_called()
        post.assert_not_called()
        self.assertEqual(result['sources'], ['SmartRecruiters'])
        self.assertFalse(any('non configuré' in warning for warning in result['warnings']))
        statuses = {status['id']: status['status'] for status in result['source_status']}
        self.assertEqual(statuses['france-travail'], 'not_configured')
        self.assertEqual(statuses['arbeitnow'], 'out_of_scope')

    @patch('job_sources.requests.get')
    def test_smart_pagination_has_a_bound_and_does_not_visit_ref_links(self, get):
        get.return_value = response({'content': [deepcopy(SR_ROW) for _ in range(100)], 'totalFound': 1000})
        rows, truncated = jobs._smartrecruiters_list('Example', 'stage')
        self.assertEqual(len(rows), 300)
        self.assertTrue(truncated)
        self.assertEqual([call.kwargs['params']['offset'] for call in get.call_args_list], [0, 100, 200])
        self.assertTrue(all(call.args[0] == jobs.SMARTRECRUITERS_URL + '/Example/postings'
                            for call in get.call_args_list))

    def test_france_travail_only_explicit_full_remote_counts(self):
        self.assertFalse(jobs._ft_remote('Télétravail possible 2 jours par semaine.'))
        self.assertTrue(jobs._ft_remote('Poste en full remote.'))
        self.assertTrue(jobs._ft_remote('100% télétravail en France.'))
        self.assertFalse(jobs._ft_remote('Pas de télétravail.'))


if __name__ == '__main__':
    unittest.main()
