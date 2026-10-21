"""Vérification visuelle facultative, dans une copie isolée et sans service externe.

À exécuter avec un Python disposant de Selenium et un navigateur Edge.
Les tests unitaires n’en ont pas besoin.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.edge.service import Service

BASE = Path(__file__).resolve().parents[1]


def run():
    os.environ['SE_CACHE_PATH'] = str(BASE / 'data' / 'selenium-cache')
    with tempfile.TemporaryDirectory(prefix='interface-test-', dir=BASE / 'data') as temp:
        fixture = Path(temp)
        shutil.copy2(BASE / 'candidate_profile.json', fixture / 'candidate_profile.json')
        shutil.copytree(BASE / 'static', fixture / 'static')
        shutil.copytree(BASE / 'candidatures', fixture / 'candidatures')
        fixture_contact = {
            'entreprise': 'Entreprise de test', 'prenom': 'Alice', 'nom': 'Martin',
            'poste': 'Talent Acquisition Manager', 'email': 'alice@entreprise-test.fr',
            'department': 'hr', 'confiance': 92, 'source': 'hunter',
            'linkedin': 'https://www.linkedin.com/in/alice-example/',
            'verification_status': 'valid', 'verification_date': '2026-10-07',
            'public_sources': [{'url': 'https://entreprise-test.fr/equipe/recrutement',
                                'last_seen_on': '2026-10-07'}],
            'priority': 0, 'priority_reasons': ['Recrutement et ressources humaines'],
        }
        bootstrap = fixture / 'fixture_server.py'
        bootstrap.write_text(
            'import sys, os, smtplib, requests\n'
            f'sys.path.insert(0, {str(BASE)!r})\n'
            'from pathlib import Path\n'
            'from web_app import Dashboard, AppServer\n'
            'import web_app\n'
            'def refuse_remote(*args, **kwargs):\n'
            '    raise AssertionError("External integrations disabled in browser fixture")\n'
            'requests.sessions.Session.request = refuse_remote\n'
            'smtplib.SMTP = refuse_remote\n'
            'web_app.resolve_location = lambda query: []\n'
            'for key in ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "HUNTER_API_KEY", "FRANCE_TRAVAIL_CLIENT_ID", "FRANCE_TRAVAIL_CLIENT_SECRET"):\n'
            '    os.environ.pop(key, None)\n'
            'offer = {"id":"test-stage-python","entreprise":"Entreprise de test",'
            '"intitule":"Stage Data Python", "description":"Stage de six mois. Python et SQL.",'
            '"lieu":"Rennes, France", "country":"France", "contract":"stage",'
            '"source":"Test isolé", "date_debut":"2027-03-01", "duree_mois":6}\n'
            'provider = lambda criteria: {"offers":[offer],"warnings":[],"sources":["Test isolé"],'
            '"source_status":[{"id":"smartrecruiters","label":"Test isolé","status":"ok","count":1}]}\n'
            'dashboard = Dashboard(Path(__file__).parent, search_provider=provider)\n'
            f'fixture_contact = {fixture_contact!r}\n'
            'contact_calls = []\n'
            'def fixture_search(company, department="hr", *, domain=None, force=False):\n'
            '    contact_calls.append({"company":company,"domain":domain,"force":force})\n'
            '    return {"company":company,"contacts":[fixture_contact],"warnings":[],\n'
            '            "queries":[{"label":"Profils LinkedIn de l’entreprise",'
            '"url":"https://www.google.com/search?q=site%3Alinkedin.com%2Fin+entreprise-test+recrutement",'
            '"kind":"linkedin"}],"from_cache":len(contact_calls)>1 and not force,\n'
            '            "resolved_domain":domain or "entreprise-test.fr","truncated":False}\n'
            'dashboard.workflow.search_contacts_detail = fixture_search\n'
            'dashboard.workflow.list_contacts = lambda: [fixture_contact] if contact_calls else []\n'
            'old_offer = {"id":"old-import","entreprise":"Ancienne entreprise",'
            '"intitule":"Ancienne offre indépendante", "description":"Administration du personnel.",'
            '"lieu":"France", "contract":"stage", "source":"manuel"}\n'
            'dashboard.post("/api/offers", {"offers":[old_offer]})\n'
            'AppServer(("127.0.0.1",8766), dashboard).serve_forever()\n',
            encoding='utf-8',
        )
        server = subprocess.Popen([str(BASE / '.venv' / 'Scripts' / 'python.exe'), str(bootstrap)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        driver = None
        try:
            options = webdriver.EdgeOptions()
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-first-run')
            options.add_argument('--disable-background-networking')
            options.add_argument('--window-size=1440,1080')
            options.add_argument('--user-data-dir=' + str(fixture / 'edge-profile'))
            options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
            # Les anciennes versions de Selenium Manager utilisent une adresse retirée.
            driver_dir = BASE / 'data' / 'edge-driver'
            driver_path = driver_dir / 'msedgedriver.exe'
            if not driver_path.exists():
                version = subprocess.check_output([
                    'powershell', '-NoProfile', '-Command',
                    "(Get-Item -LiteralPath 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe').VersionInfo.ProductVersion",
                ], text=True).strip()
                driver_dir.mkdir(parents=True, exist_ok=True)
                archive = driver_dir / 'driver.zip'
                url = f'https://msedgedriver.microsoft.com/{version}/edgedriver_win64.zip'
                with urllib.request.urlopen(url, timeout=30) as response:
                    archive.write_bytes(response.read())
                with zipfile.ZipFile(archive) as zipped:
                    driver_path.write_bytes(zipped.read('msedgedriver.exe'))
            driver = webdriver.Edge(service=Service(str(driver_path)), options=options)
            driver.get_log('browser')
            driver.get('http://127.0.0.1:8766/')
            wait = WebDriverWait(driver, 20)
            wait.until(lambda d: 'Votre espace se prépare' not in d.find_element(By.ID, 'page-content').text)
            assert 'Christopher' in driver.find_element(By.TAG_NAME, 'body').text
            (BASE / 'data').mkdir(exist_ok=True)
            driver.save_screenshot(str(BASE / 'data' / 'interface-bureau.png'))
            for page in ('offers', 'alerts', 'applications', 'contacts', 'profile'):
                driver.find_element(By.CSS_SELECTOR, f'a[href="#{page}"]').click()
                wait.until(lambda d: d.current_url.endswith('#' + page))
                assert driver.find_element(By.ID, 'page-content').text.strip()
                assert not driver.find_elements(By.CSS_SELECTOR, '#page-content .error-screen')
            driver.find_element(By.CSS_SELECTOR, 'a[href="#applications"]').click()
            wait.until(lambda d: 'Sopra Steria' in d.find_element(By.ID, 'page-content').text)
            assert 'Capgemini' in driver.find_element(By.ID, 'page-content').text
            driver.save_screenshot(str(BASE / 'data' / 'interface-candidatures.png'))
            # Les identifiants de démonstration sont enregistrés uniquement dans la copie isolée.
            driver.find_element(By.CSS_SELECTOR, 'a[href="#offers"]').click()
            driver.find_element(By.XPATH, '//button[normalize-space()="Configurer les sources"]').click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '#modal input[name="france_travail_client_id"]')))
            driver.find_element(By.CSS_SELECTOR, '#modal input[name="france_travail_client_id"]').send_keys('browser-fake-client-id')
            driver.find_element(By.CSS_SELECTOR, '#modal input[name="france_travail_client_secret"]').send_keys('browser-fake-client-secret')
            driver.find_element(By.XPATH, '//button[normalize-space()="Enregistrer la configuration"]').click()
            wait.until(lambda d: 'Configuré' in d.find_element(By.ID, 'modal').text)
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="france_travail_client_id"]').get_attribute('value') == ''
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="france_travail_client_secret"]').get_attribute('value') == ''
            assert 'browser-fake-client-secret' in (fixture / '.env').read_text(encoding='utf-8')
            settings = driver.execute_async_script('const done=arguments[0]; fetch("/api/settings").then(response=>response.json()).then(done);')
            assert settings['france_travail']['configured'] is True
            assert 'browser-fake-client-id' not in json.dumps(settings)
            assert 'browser-fake-client-secret' not in json.dumps(settings)
            driver.save_screenshot(str(BASE / 'data' / 'interface-sources.png'))
            driver.find_element(By.ID, 'modal-close').click()
            # Une recherche factice fournit une offre : les anciens imports restent dans leur vue.
            for name, value in (('keywords', 'Python'), ('location', 'France')):
                field = driver.find_element(By.CSS_SELECTOR, f'#page-content .search-form input[name="{name}"]')
                field.clear()
                field.send_keys(value)
            Select(driver.find_element(By.CSS_SELECTOR, '#page-content select[name="source"]')).select_by_value('smartrecruiters')
            driver.find_element(By.CSS_SELECTOR, '#page-content .search-form button[type="submit"]').click()
            wait.until(lambda d: bool(d.find_elements(By.CSS_SELECTOR, '#page-content .offer-card')) and 'Entreprise de test' in d.find_element(By.ID, 'page-content').text)
            assert len(driver.find_elements(By.CSS_SELECTOR, '#page-content .offer-card')) == 1
            assert 'Ancienne offre indépendante' not in driver.find_element(By.ID, 'page-content').text
            assert 'Python' in driver.find_element(By.CSS_SELECTOR, '#page-content .offer-matches').text
            assert 'SQL' in driver.find_element(By.CSS_SELECTOR, '#page-content .offer-matches').text
            driver.find_element(By.XPATH, '//button[@role="tab" and starts-with(normalize-space(),"Offres enregistrées") ]').click()
            wait.until(lambda d: 'Ancienne offre indépendante' in d.find_element(By.ID, 'page-content').text)
            assert len(driver.find_elements(By.CSS_SELECTOR, '#page-content .offer-card')) == 2
            filter_field = driver.find_element(By.CSS_SELECTOR, '#page-content input[name="offer_filter"]')
            filter_field.send_keys('Ancienne')
            assert len(driver.find_elements(By.CSS_SELECTOR, '#page-content .offer-card')) == 1
            assert 'Entreprise de test' not in driver.find_element(By.ID, 'page-content').text
            filter_field.clear()
            driver.find_element(By.XPATH, '//button[@role="tab" and starts-with(normalize-space(),"Dernière recherche") ]').click()
            assert 'Ancienne offre indépendante' not in driver.find_element(By.ID, 'page-content').text
            driver.refresh()
            wait.until(lambda d: bool(d.find_elements(By.CSS_SELECTOR, '#page-content .offer-card')))
            assert len(driver.find_elements(By.CSS_SELECTOR, '#page-content .offer-card')) == 1
            assert 'Ancienne offre indépendante' not in driver.find_element(By.ID, 'page-content').text
            driver.save_screenshot(str(BASE / 'data' / 'interface-recherche.png'))
            # La copie isolée utilise une source d’offres factice ; aucun service externe.
            driver.find_element(By.CSS_SELECTOR, 'a[href="#alerts"]').click()
            driver.find_element(By.XPATH, '//button[normalize-space()="Créer une alerte"]').click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '#modal input[name="keywords"]')))
            for name, value in (('name', 'Alerte Python de test'), ('keywords', 'Python'), ('location', 'France')):
                field = driver.find_element(By.CSS_SELECTOR, f'#modal input[name="{name}"]')
                field.clear()
                field.send_keys(value)
            driver.find_element(By.XPATH, '//button[normalize-space()="Créer mon alerte"]').click()
            wait.until(lambda d: not d.find_element(By.ID, 'modal').get_attribute('open'))
            wait.until(lambda d: 'Alerte Python de test' in d.find_element(By.ID, 'page-content').text)
            driver.find_element(By.XPATH, '//button[normalize-space()="Vérifier les offres"]').click()
            wait.until(lambda d: '1 correspondance(s)' in d.find_element(By.ID, 'page-content').text)
            driver.find_element(By.CSS_SELECTOR, 'a[href="#offers"]').click()
            wait.until(lambda d: 'Entreprise de test' in d.find_element(By.ID, 'page-content').text)
            driver.find_element(By.XPATH, '//button[normalize-space()="Préparer ma candidature"]').click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '#modal textarea[name="body"]')))
            assert 'Entreprise de test' in driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="body"]').get_attribute('value')
            assert len(driver.find_elements(By.CSS_SELECTOR, '#modal a[href*="/files/"]')) >= 2
            subject = driver.find_element(By.CSS_SELECTOR, '#modal input[name="subject"]')
            subject.clear()
            subject.send_keys('Objet retouché pendant le test')
            body = driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="body"]')
            body.send_keys('\nMail retouché sans enregistrement.')
            expected_body = body.get_attribute('value')
            driver.find_element(By.XPATH, '//button[@role="tab" and normalize-space()="Lettre de motivation"]').click()
            letter = driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="letter"]')
            letter.send_keys('\nTexte ajouté pendant le test isolé.')
            expected_letter = letter.get_attribute('value')
            # Recherche sans clé réelle, avec un résultat simulé et sourcé ; aucune sélection automatique.
            driver.find_element(By.XPATH, '//button[normalize-space()="Rechercher un contact RH"]').click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '.contact-results [data-contact-email="alice@entreprise-test.fr"]')))
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="email"]').get_attribute('value') == ''
            assert 'Talent Acquisition Manager' in driver.find_element(By.CSS_SELECTOR, '.contact-results').text
            assert 'Statut Hunter : Valide' in driver.find_element(By.CSS_SELECTOR, '.contact-results').text
            assert 'Domaine : entreprise-test.fr' in driver.find_element(By.CSS_SELECTOR, '.contact-search-status').text
            driver.find_element(By.CSS_SELECTOR, '.contact-sources summary').click()
            assert driver.find_element(By.CSS_SELECTOR, '.contact-sources a').get_attribute('href') == 'https://entreprise-test.fr/equipe/recrutement'
            assert 'Dernière observation' in driver.find_element(By.CSS_SELECTOR, '.contact-sources').text
            assert driver.find_element(By.CSS_SELECTOR, '.contact-evidence a').get_attribute('href').startswith('https://www.linkedin.com/in/')
            assert driver.find_element(By.CSS_SELECTOR, '.contact-query-links a').get_attribute('href').startswith('https://www.google.com/search')
            domain = driver.find_element(By.CSS_SELECTOR, '#modal input[name="contact_domain"]')
            domain.send_keys('entreprise-test.fr')
            driver.find_element(By.XPATH, '//button[normalize-space()="Actualiser la recherche"]').click()
            wait.until(lambda d: 'Recherche actualisée' in d.find_element(By.CSS_SELECTOR, '.contact-search-status').text and d.find_element(By.XPATH, '//button[normalize-space()="Rechercher les contacts"]').is_enabled())
            driver.find_element(By.XPATH, '//button[normalize-space()="Rechercher les contacts"]').click()
            wait.until(lambda d: 'Résultats conservés' in d.find_element(By.CSS_SELECTOR, '.contact-search-status').text)
            assert driver.find_element(By.CSS_SELECTOR, '#modal [role="tab"][aria-selected="true"]').text == 'Lettre de motivation'
            assert letter.get_attribute('value') == expected_letter
            driver.find_element(By.XPATH, '//button[normalize-space()="Fermer la recherche"]').click()
            assert not driver.find_element(By.CSS_SELECTOR, '.application-contact-panel').is_displayed()
            driver.find_element(By.XPATH, '//button[normalize-space()="Rechercher un contact RH"]').click()
            driver.find_element(By.XPATH, '//button[normalize-space()="Choisir ce contact"]').click()
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="email"]').get_attribute('value') == 'alice@entreprise-test.fr'
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="prenom"]').get_attribute('value') == 'Alice'
            assert letter.get_attribute('value') == expected_letter
            # Annuler une régénération restaure exactement le même éditeur et ses retouches.
            driver.find_element(By.XPATH, '//button[normalize-space()="Régénérer les documents"]').click()
            driver.find_element(By.XPATH, '//button[normalize-space()="Retour"]').click()
            assert driver.find_element(By.CSS_SELECTOR, '#modal [role="tab"][aria-selected="true"]').text == 'Lettre de motivation'
            assert driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="letter"]').get_attribute('value') == expected_letter
            driver.find_element(By.XPATH, '//button[normalize-space()="Enregistrer la lettre et son PDF"]').click()
            wait.until(lambda d: any('La lettre et son PDF ont été mis à jour.' in e.text for e in d.find_elements(By.CSS_SELECTOR, '.toast')))
            driver.find_element(By.XPATH, '//button[@role="tab" and normalize-space()="Email de candidature"]').click()
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="subject"]').get_attribute('value') == 'Objet retouché pendant le test'
            assert driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="body"]').get_attribute('value') == expected_body
            driver.find_element(By.XPATH, '//button[normalize-space()="Enregistrer le mail"]').click()
            wait.until(lambda d: any('Votre mail est enregistré.' in e.text for e in d.find_elements(By.CSS_SELECTOR, '.toast')))
            driver.save_screenshot(str(BASE / 'data' / 'interface-contacts-relecture.png'))
            driver.find_element(By.ID, 'modal-close').click()
            assert driver.find_elements(By.XPATH, '//button[normalize-space()="Ouvrir ma candidature"]')
            # Répéter la préparation via l’API réutilise les documents retouchés, même après rechargement.
            prepared = driver.execute_async_script('const done=arguments[0]; fetch("/api/state").then(r=>r.json()).then(s=>fetch("/api/offers/test-stage-python/prepare",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":s.csrf_token},body:"{}"})).then(r=>r.json()).then(done);')
            assert prepared['application']['reused'] is True, prepared
            driver.refresh()
            wait.until(EC.element_to_be_clickable((By.XPATH, '//button[normalize-space()="Ouvrir ma candidature"]'))).click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '#modal textarea[name="body"]')))
            assert driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="body"]').get_attribute('value') == expected_body
            assert driver.find_element(By.CSS_SELECTOR, '#modal input[name="subject"]').get_attribute('value') == 'Objet retouché pendant le test'
            driver.find_element(By.XPATH, '//button[@role="tab" and normalize-space()="Lettre de motivation"]').click()
            assert driver.find_element(By.CSS_SELECTOR, '#modal textarea[name="letter"]').get_attribute('value') == expected_letter
            driver.find_element(By.ID, 'modal-close').click()
            driver.find_element(By.CSS_SELECTOR, 'a[href="#contacts"]').click()
            wait.until(lambda d: 'Talent Acquisition Manager' in d.find_element(By.ID, 'page-content').text)
            assert 'Statut Hunter : Valide' in driver.find_element(By.ID, 'page-content').text
            driver.save_screenshot(str(BASE / 'data' / 'interface-contacts.png'))
            driver.find_element(By.CSS_SELECTOR, 'a[href="#applications"]').click()
            wait.until(lambda d: 'Entreprise de test' in d.find_element(By.ID, 'page-content').text)
            driver.set_window_size(390, 844)
            driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':390, 'height':844, 'deviceScaleFactor':1, 'mobile':True})
            driver.get('http://127.0.0.1:8766/')
            wait.until(lambda d: 'Votre espace se prépare' not in d.find_element(By.ID, 'page-content').text)
            width = driver.execute_script('return [document.documentElement.scrollWidth, window.innerWidth]')
            assert width[0] <= width[1] + 2, width
            driver.save_screenshot(str(BASE / 'data' / 'interface-mobile.png'))
            driver.find_element(By.CSS_SELECTOR, 'a[href="#contacts"]').click()
            wait.until(lambda d: 'Talent Acquisition Manager' in d.find_element(By.ID, 'page-content').text)
            width = driver.execute_script('return [document.documentElement.scrollWidth, window.innerWidth]')
            assert width[0] <= width[1] + 2, width
            driver.save_screenshot(str(BASE / 'data' / 'interface-contacts-mobile.png'))
            logs = driver.get_log('browser')
            severe = [log['message'] for log in logs if log['level'] == 'SEVERE' and '127.0.0.1:8766' in log['message']]
            assert not severe, severe
            print(json.dumps({'pages_verified':6,'existing_applications':8,'source_settings':True,
                              'latest_search_separated_from_saved_offers':True,'saved_offers_filter':True,
                              'search_persisted_after_reload':True,'alert_and_notification':True,
                              'application_generated':True,'letter_pdf_edited':True,
                              'preparation_reused_without_overwrite':True,'public_contact_sources_and_selection':True,
                              'contact_refresh_and_cache':True,'unsaved_drafts_preserved':True,
                              'responsive':True,'browser_errors':severe}))
        finally:
            if driver:
                driver.quit()
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    (BASE / 'data').mkdir(exist_ok=True)
    run()
