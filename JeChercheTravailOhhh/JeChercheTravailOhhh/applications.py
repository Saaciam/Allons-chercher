"""Préparation locale de candidatures à partir des seuls faits du profil."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent
MONTHS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
          'août', 'septembre', 'octobre', 'novembre', 'décembre')

# Ce lexique signale des outils cités dans l'offre sans les attribuer au candidat.
REVIEW_TECHNOLOGIES = {
    'Pandas': ['pandas'], 'NumPy': ['numpy'], 'scikit-learn': ['scikit-learn', 'sklearn'],
    'TensorFlow': ['tensorflow'], 'PyTorch': ['pytorch'], 'Spark': ['spark', 'pyspark'],
    'Kafka': ['kafka'], 'Airflow': ['airflow'], 'dbt': ['dbt'], 'Docker': ['docker'],
    'Kubernetes': ['kubernetes', 'k8s'], 'Azure': ['azure'], 'GCP': ['gcp', 'google cloud'],
    'Tableau': ['tableau software', 'tableau desktop'],
    'LLM / modèles de langage': ['llm', 'llms', 'modèles de langage', 'language models'],
    'IA générative': ['ia générative', 'generative ai'],
    'RAG': ['rag', 'retrieval augmented generation'], 'LangChain': ['langchain'],
    'Databricks': ['databricks'], 'Hadoop': ['hadoop'], 'Scala': ['scala'],
}


def normalized(value: str) -> str:
    text = unicodedata.normalize('NFKD', value.casefold().replace('’', "'").replace('‑', '-'))
    return re.sub(r'\s+', ' ', ''.join(c for c in text if not unicodedata.combining(c)))


def mentions(text: str, alias: str) -> bool:
    # Frontières de mot : « R » ne doit pas correspondre à « entreprise ».
    return bool(re.search(r'(?<!\w)' + re.escape(normalized(alias)) + r'(?!\w)', normalized(text)))


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', normalized(value)).strip('-')[:80]


def iso_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('La date doit être au format AAAA-MM-JJ.')
    return date.fromisoformat(value)


def month_label(value: str) -> str:
    parsed = iso_date(value)
    return f'{MONTHS[parsed.month - 1]} {parsed.year}'


def today_in_paris() -> date:
    try:
        return datetime.now(ZoneInfo('Europe/Paris')).date()
    except ZoneInfoNotFoundError:
        # Windows peut ne pas installer la base IANA ; le poste de l'utilisateur est à Paris.
        return date.today()


def load_profile(path: Path) -> dict:
    profile = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(profile, dict):
        raise ValueError('Le profil doit être un objet JSON.')
    if type(profile.get('schema_version')) is not int or profile['schema_version'] != 1:
        raise ValueError('Version de profil non prise en charge.')
    identity = profile.get('identity', {})
    if not isinstance(identity, dict):
        raise ValueError('identity doit être un objet JSON.')
    for key in ('first_name', 'last_name', 'display_name', 'email', 'phone'):
        if not isinstance(identity.get(key), str) or not identity[key].strip():
            raise ValueError(f'Profil incomplet : identity.{key}.')
    internship = profile.get('internship', {})
    if not isinstance(internship, dict):
        raise ValueError('internship doit être un objet JSON.')
    iso_date(internship.get('available_from'))
    if not isinstance(internship.get('mobility'), str) or not internship['mobility'].strip():
        raise ValueError('Mobilité manquante dans le profil.')
    if type(internship.get('duration_months')) is not int or internship['duration_months'] <= 0:
        raise ValueError('Durée de stage invalide dans le profil.')
    for collection in ('skills', 'projects', 'education', 'languages'):
        if not isinstance(profile.get(collection), list) or not profile[collection]:
            raise ValueError(f'Profil incomplet : {collection}.')
        if any(not isinstance(entry, dict) for entry in profile[collection]):
            raise ValueError(f'{collection} doit contenir des objets JSON.')
    for collection, required in (
        ('skills', ('id', 'label', 'category')),
        ('projects', ('id', 'title', 'description')),
        ('education', ('title', 'institution', 'location', 'period')),
        ('languages', ('name', 'level')),
    ):
        for entry in profile[collection]:
            if any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in required):
                raise ValueError(f'{collection} : champs requis {", ".join(required)}.')
    for skill in profile['skills']:
        if not isinstance(skill.get('aliases', []), list) or any(not isinstance(alias, str) or not alias.strip() for alias in skill.get('aliases', [])):
            raise ValueError(f'Alias invalides pour {skill["id"]}.')
    for collection in ('skills', 'projects'):
        ids = [entry['id'] for entry in profile[collection]]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Identifiants dupliqués : {collection}.')
    known_ids = {skill['id'] for skill in profile['skills']}
    for project in profile['projects']:
        if not isinstance(project.get('skills', []), list) or any(not isinstance(item, str) for item in project.get('skills', [])):
            raise ValueError(f'Liste de compétences invalide pour {project["id"]}.')
        if set(project.get('skills', [])) - known_ids:
            raise ValueError(f'Compétence inconnue dans le projet {project["id"]}.')
    return profile


def validate_offer(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('Chaque offre doit être un objet JSON.')
    offer = dict(raw)
    for key in ('entreprise', 'intitule', 'description'):
        if not isinstance(offer.get(key), str) or not offer[key].strip():
            raise ValueError(f'Offre incomplète : {key}.')
        offer[key] = offer[key].strip()
    for key in ('entreprise', 'intitule'):
        if any(c in offer[key] for c in '\r\n\x00') or len(offer[key]) > 200:
            raise ValueError(f'Champ invalide : {key} (une ligne, 200 caractères maximum).')
    offer.setdefault('id', slug(offer['entreprise'] + '-' + offer['intitule']))
    if not isinstance(offer['id'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', offer['id']):
        raise ValueError('id doit contenir 1 à 80 lettres minuscules, chiffres, - ou _.')
    if offer.get('date_debut') is not None and offer.get('date_debut') != '':
        iso_date(offer['date_debut'])
    if offer.get('duree_mois') is not None:
        if type(offer['duree_mois']) is not int or offer['duree_mois'] <= 0:
            raise ValueError('duree_mois doit être un entier positif.')
    if not isinstance(offer.get('contact', {}), dict):
        raise ValueError('contact doit être un objet avec email, prenom et nom facultatifs.')
    for key in ('email', 'prenom', 'nom'):
        value = offer.get('contact', {}).get(key, '')
        if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
            raise ValueError(f'contact.{key} invalide.')
    if 'spontaneous' in offer and type(offer['spontaneous']) is not bool:
        raise ValueError('spontaneous doit être un booléen.')
    return offer


def load_offers(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    offers = [validate_offer(o) for o in (raw if isinstance(raw, list) else [raw])]
    if not offers:
        raise ValueError('Le fichier ne contient aucune offre.')
    ids = [o['id'] for o in offers]
    if len(set(ids)) != len(ids):
        raise ValueError('Deux offres portent le même id ; attribue un id différent à chaque offre.')
    return offers


def spontaneous_offers(path: Path) -> list[dict]:
    companies = list(dict.fromkeys(line.strip() for line in path.read_text(encoding='utf-8-sig').splitlines()
                                  if line.strip() and not line.strip().startswith('#')))
    if not companies:
        raise ValueError('La liste des entreprises est vide.')
    offers = [validate_offer({
        'id': slug(company) + '-spontanee', 'entreprise': company,
        'intitule': 'Stage Data & Intelligence Artificielle',
        'description': 'Candidature spontanée ; aucune offre détaillée fournie.',
        'spontaneous': True,
    }) for company in companies]
    if len({o['id'] for o in offers}) != len(offers):
        raise ValueError('Deux noms d’entreprise produisent le même identifiant de dossier.')
    return offers


def analyze_offer(profile: dict, offer: dict) -> dict:
    text = offer['intitule'] + '\n' + offer['description'] if not offer.get('spontaneous') else ''
    normalized_text, normalized_title = normalized(text), normalized(offer['intitule'])
    def contains(document, alias):
        return bool(re.search(r'(?<!\w)' + re.escape(normalized(alias)) + r'(?!\w)', document))
    matched = [s['id'] for s in profile['skills']
               if any(contains(normalized_text, a) for a in [s['label'], *s.get('aliases', [])])]
    title_matches = {s['id'] for s in profile['skills']
                     if text and any(contains(normalized_title, a) for a in [s['label'], *s.get('aliases', [])])}
    # En candidature Data/IA spontanée, les acquis du domaine précèdent les outils web.
    default_categories = {'Data et programmation': 0, 'Compétences attestées par les projets': 1,
                          'BI et Dataviz': 2, 'Bases de données & Cloud': 3, 'Modélisation': 4}
    ordered = sorted(profile['skills'], key=lambda s: (
        -(2 * (s['id'] in title_matches) + (s['id'] in matched)),
        default_categories.get(s.get('category'), 5),
    ))
    projects = sorted(profile['projects'], key=lambda p: -len(set(p.get('skills', [])) & set(matched)))
    absent = [label for label, aliases in REVIEW_TECHNOLOGIES.items()
              if any(contains(normalized_text, a) for a in aliases)
              and not any(mentions(s['label'], a) or any(mentions(b, a) for b in s.get('aliases', []))
                          for s in profile['skills'] for a in aliases)]
    notes = []
    if offer.get('spontaneous'):
        notes.append('Candidature spontanée : aucune mission ni exigence spécifique disponible ; personnalisation limitée à l’entreprise et au profil.')
    elif not matched:
        notes.append('Aucune compétence du profil reconnue dans le texte : relire la pertinence de cette candidature.')
    elif not any(set(p.get('skills', [])) & set(matched) for p in projects):
        notes.append('Les compétences demandées figurent au profil, mais aucun projet décrit ne démontre directement leur utilisation. Les projets présentés restent des exemples du parcours.')
    if absent:
        notes.append('Technologies citées sans acquis attesté dans le CV : ' + ', '.join(absent) + '. Elles ne sont pas ajoutées au CV ni revendiquées dans la lettre.')
    if offer.get('date_debut') and date.fromisoformat(offer['date_debut']) < date.fromisoformat(profile['internship']['available_from']):
        notes.append('La date de début demandée précède la disponibilité du candidat (' + month_label(profile['internship']['available_from']) + ').')
    if offer.get('duree_mois') and offer['duree_mois'] != profile['internship']['duration_months']:
        notes.append(f"Durée demandée ({offer['duree_mois']} mois) différente de la recherche ({profile['internship']['duration_months']} mois).")
    if not offer.get('spontaneous'):
        if not offer.get('date_debut'):
            notes.append('Date de début non structurée : vérifier le texte de l’offre et la compatibilité avec ' + month_label(profile['internship']['available_from']) + '.')
        if not offer.get('duree_mois'):
            notes.append(f"Durée non structurée : vérifier le texte de l’offre et la compatibilité avec les {profile['internship']['duration_months']} mois recherchés.")
        if not mentions(offer['intitule'], 'stage') and not mentions(offer['intitule'], 'internship'):
            notes.append('L’intitulé ne précise pas de stage : vérifier le type de contrat.')
    notes.append('L’analyse repose sur les mots-clés ; vérifier aussi les missions, le lieu et le niveau demandé. Aucun score de recrutement n’est calculé.')
    return {'matched_skill_ids': matched, 'ordered_skill_ids': [s['id'] for s in ordered],
            'ordered_project_ids': [p['id'] for p in projects], 'unverified_technologies': absent,
            'review_notes': notes}


def make_texts(profile: dict, offer: dict, analysis: dict, application_date: date | None = None) -> tuple[str, str]:
    person = profile['identity']
    internship = profile['internship']
    availability = month_label(internship['available_from'])
    duration = internship['duration_months']
    company = offer['entreprise']
    name = person['display_name']
    education = profile['education'][0]
    studies = f"Je prépare actuellement le {education['title']} ({education['institution']})"
    subject = f"Candidature{' spontanée' if offer.get('spontaneous') else ''} – {offer['intitule']} – {name}"
    skills_by_id = {s['id']: s for s in profile['skills']}
    matched = [skills_by_id[skill_id]['label'] for skill_id in analysis['ordered_skill_ids']
               if skill_id in analysis['matched_skill_ids']]
    if offer.get('spontaneous'):
        opening = f"Je souhaite rejoindre {company} dans le cadre de mon stage de fin d’études en Data et Intelligence Artificielle, pour une durée de {duration} mois à partir de {availability}."
        alignment = 'Je m’intéresse à l’intelligence artificielle, notamment à l’IA générative, ainsi qu’à la data engineering et à la valorisation des données.'
    else:
        opening = f"Je vous présente ma candidature au poste « {offer['intitule']} » au sein de {company}, dans le cadre de mon stage de fin d’études. Je recherche un stage de {duration} mois à partir de {availability}."
        alignment = ('Votre offre mentionne ' + ', '.join(matched[:6]) + '. Ces compétences figurent dans mon parcours et je souhaite les mobiliser dans les missions proposées.'
                     if matched else 'Je souhaite approfondir mes compétences en contribuant aux missions décrites dans votre offre.')
    projects_by_id = {p['id']: p for p in profile['projects']}
    selected = [projects_by_id[p] for p in analysis['ordered_project_ids'][:2]]
    def describe_project(project):
        description = project['description']
        # Des phrases à la première personne, sans ajouter de réalisation au texte source.
        replacements = {
            "Réalisation d'un ": "j’ai réalisé un ",
            'Pipeline complet de ': 'j’ai construit un pipeline complet de ',
            "Modélisation d'un ": 'j’ai modélisé un ',
            'Conception et réalisation du ': 'j’ai conçu et réalisé le ',
        }
        for prefix, replacement in replacements.items():
            if description.startswith(prefix):
                description = replacement + description[len(prefix):]
                # Les liens figurent dans le CV ; garder la lettre centrée sur la réalisation.
                description = re.split(r' Code source| Rapport disponible', description, maxsplit=1)[0]
                return f"Dans mon projet « {project['title']} », {description}"
        return f"Mon projet « {project['title']} » illustre mon parcours. {description}"

    evidence = '\n\n'.join(describe_project(p) for p in selected)
    closing = (f"Je suis mobile en {internship['mobility']}. Je serais heureux d’échanger avec vous sur les besoins de votre équipe et sur ma contribution en tant que stagiaire."
               + (' Si vous n’êtes pas la personne chargée du recrutement, je vous remercie de bien vouloir m’orienter vers le bon interlocuteur.' if offer.get('spontaneous') else ''))
    current = application_date or today_in_paris()
    letter = '\n\n'.join([
        f"Le {current.day} {MONTHS[current.month - 1]} {current.year}",
        f"À l’attention de l’équipe recrutement — {company}", 'Objet : ' + subject,
        'Madame, Monsieur,', opening, studies + '. ' + alignment, evidence, closing,
        'Je vous remercie pour l’attention portée à ma candidature.\nVeuillez agréer, Madame, Monsieur, mes salutations respectueuses.', name,
    ]) + '\n'
    email_body = '\n\n'.join([
        'Bonjour {prenom},', opening, studies + '.', alignment,
        'Vous trouverez en pièces jointes mon CV et ma lettre de motivation.\n' + closing,
        'Je vous remercie pour votre attention.\n\nCordialement,',
        f"{name}\n{person['email']}\n{person['phone']}",
    ])
    return letter, 'SUBJECT: ' + subject + '\n\n' + email_body + '\n'


def prepare_application(profile: dict, profile_path: Path, offer: dict, output_dir: Path,
                        *, overwrite: bool = False, application_date: date | None = None) -> Path:
    from pdf_documents import write_cv, write_letter

    offer = validate_offer(offer)
    directory = output_dir / offer['id']
    if directory.exists() and not overwrite:
        raise ValueError(f'{directory} existe déjà. Utilise --overwrite pour régénérer après avoir sauvegardé tes modifications.')
    analysis = analyze_offer(profile, offer)
    letter, email = make_texts(profile, offer, analysis, application_date)
    directory.mkdir(parents=True, exist_ok=True)
    # Invalider l’ancien manifest dès le début d’une régénération.
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        manifest_path.unlink()
    (directory / 'offre.json').write_bytes(json_bytes(offer))
    (directory / 'analyse.json').write_bytes(json_bytes(analysis))
    (directory / 'lettre_motivation.txt').write_text(letter, encoding='utf-8')
    (directory / 'email.txt').write_text(email, encoding='utf-8')
    files = {'cv': 'CV_CHRISTOPHER_KOUTOUA.pdf', 'letter': 'Lettre_motivation.pdf', 'email': 'email.txt'}
    write_cv(directory / files['cv'], profile, offer, analysis)
    write_letter(directory / files['letter'], profile, letter)
    manifest = {
        'schema_version': 1, 'offer_id': offer['id'], 'company': offer['entreprise'],
        'title': offer['intitule'], 'profile_email': profile['identity']['email'],
        'files': files, 'recipient': offer.get('contact', {}),
        'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'source_profile_sha256': file_hash(profile_path),
        'source_offer_sha256': file_hash(directory / 'offre.json'),
        'review_notes': analysis['review_notes'], 'spontaneous': offer.get('spontaneous', False),
    }
    manifest_path.write_bytes(json_bytes(manifest))
    return directory
