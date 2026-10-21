# Préparer les candidatures de Christopher

Ce projet prépare les candidatures au stage de fin d'études de **Christopher Isaac William KOUTOUA**, en Master 2 MIAGE, option Data & Business Intelligence. Le profil recherche un **stage de 6 mois à partir de mars 2027**, avec une mobilité en France.

Les fonctions de recherche de contacts Hunter et d'envoi SMTP sont conservées. Le nouveau parcours crée, pour chaque offre ou entreprise, un CV PDF, une lettre de motivation PDF et un email à relire avant l'envoi.

## Interface simple

Après l'installation, double-clique sur **`lancer_interface.bat`** dans le dossier du projet. Tu peux aussi lancer l'interface depuis PowerShell :

```powershell
Set-Location "D:\JeChercheTravailOhhh\JeChercheTravailOhhh"
.\.venv\Scripts\python.exe web_app.py --open
```

Le navigateur ouvre **http://127.0.0.1:8765**. Garde le terminal ouvert ; `Ctrl+C` arrête le serveur et les vérifications d'alertes. Si le port est déjà utilisé, ajoute `--port 8766` à la commande. Les scripts décrits plus bas restent utilisables. La configuration des sources est accessible dans l'interface : tu peux commencer avec les sources publiques sans créer de compte ni renseigner de clé.

1. **Localisation et recherche** : renseigne une ville, choisis une suggestion et ton rayon, puis indique le métier et le contrat recherchés. Le bouton de géolocalisation demande l'autorisation du navigateur ; la saisie manuelle reste disponible. « France » permet une recherche nationale. Les mots-clés séparés par une virgule représentent des alternatives, par exemple `data, intelligence artificielle, BI`.
2. **Alertes** : enregistre ces critères, donne un nom à l'alerte et retrouve les nouvelles offres dans les notifications de l'interface. Les alertes actives sont vérifiées toutes les **30 minutes tant que le serveur fonctionne** ; le bouton de vérification permet de relancer immédiatement la recherche. Tu peux mettre une alerte en pause ou la supprimer. Une même offre ne déclenche qu'une notification par alerte. Aucune alerte n'est envoyée automatiquement par email.
3. **Offres et dossiers** : ouvre une offre issue de la recherche ou importe le texte d'une annonce avec son entreprise et son intitulé. Prépare ensuite le CV, la lettre et l'email adaptés depuis ton profil réel. Si le dossier existe déjà, **« Ouvrir ma candidature »** le retrouve en conservant les retouches, même après rechargement ; un double clic ne recrée pas les documents. Les dossiers déjà présents dans `candidatures/` apparaissent également dans l'interface.
4. **Relecture** : consulte les PDF et les points à vérifier, retouche le mail ou la lettre, puis enregistre. L'édition de la lettre actualise son PDF. Les corrections du profil laissent un avertissement sur les anciens dossiers. Pour actualiser leurs documents ou réparer un dossier incomplet, utilise **« Régénérer les documents »** puis confirme ; cette action remplace les retouches du dossier. L'interface récupère l'offre enregistrée pour cette réparation.
5. **Contacts et envoi** : ajoute un contact manuellement ou lance la recherche RH Hunter pour l'entreprise, avec son domaine si tu le connais. Consulte les sources et choisis toi-même le destinataire ; la sélection conserve le mail et la lettre en cours d'édition. Consulte ensuite l'aperçu et confirme explicitement l'envoi. Les identifiants SMTP sont requis pour envoyer ; rechercher un contact, le sélectionner, préparer et relire une candidature n'envoient aucun email. Un dossier déjà envoyé depuis l'interface est marqué pour éviter un second envoi.

La localisation, les alertes, les offres importées, les notifications et les statuts d'envoi sont conservés localement dans `data/dashboard.json`. Les contacts ajoutés depuis l'interface sont dans `contacts.interface.csv` ; les exports `contacts*.csv` existants restent accessibles. Les sources, dates et informations de classement des contacts sont conservées dans `data/contact_metadata.json`, séparément des sept colonnes CSV historiques (`entreprise,prenom,nom,poste,email,confiance,linkedin`).

### Trouver un interlocuteur dans l'entreprise

Dans une candidature ou la page Contacts, lance la recherche avec le nom de l'entreprise. Le domaine, par exemple `entreprise.fr`, est facultatif et permet de cibler la bonne entreprise ; le domaine trouvé est affiché avec les résultats.

La recherche commence par les RH et recruteurs. S'il reste moins de trois contacts exploitables, elle s'élargit automatiquement à l'IT, au management, à la recherche et au conseil, puis aux autres collaborateurs. Une recherche utilise au maximum **trois requêtes Hunter de dix résultats chacune** et affiche au maximum **dix contacts distincts**. Les recruteurs passent en premier, puis les responsables Data/IA, puis les autres collaborateurs ; les cartes expliquent le classement.

Les résultats automatiques retiennent uniquement les emails accompagnés d'une source publique fournie par Hunter. Les adresses déduites sans source et les sources signalées comme retirées sont exclues. Chaque contact affiche son poste, ses sources, leur dernière observation disponible et le statut de vérification fourni par Hunter. Un lien LinkedIn est présenté lorsqu'il est disponible ; il n'est pas garanti pour tous les contacts.

Les résultats sont conservés localement pendant **24 heures**, ou **30 minutes** si aucune adresse publique n'est trouvée. Une recherche répétée réutilise ce cache, y compris après redémarrage ; l'action d'actualisation force une nouvelle recherche. En cas de panne, de quota épuisé ou d'actualisation sans résultat, les contacts enregistrés sont conservés et les précédents résultats disponibles restent accessibles avec un avertissement. Une actualisation vide garde les dates d'origine et évite de refaire la même recherche pendant 30 minutes. Les corrections manuelles du nom, du poste et du profil gardent la priorité lors d'une actualisation, y compris celles effectuées directement dans les CSV.

Si Hunter n'est pas configuré ou ne trouve aucun email public, des liens permettent de rechercher des profils LinkedIn et des pages professionnelles dans le navigateur. Il n'y a aucun scraping d'un compte LinkedIn connecté. Tu peux vérifier un profil puis ajouter un contact manuellement. Aucun contact n'est sélectionné automatiquement et ces recherches n'ouvrent aucune connexion SMTP.

### Sources des offres

La recherche utilise de vraies sources ; un problème de connexion est affiché et aucune annonce fictive ne remplace les résultats. La configuration affiche les sources disponibles et leur état. Les critères de départ ciblent ton stage en Data, Intelligence Artificielle ou BI en France ; tu peux les modifier selon ta localisation et ton objectif.

- **SmartRecruiters** : les [offres rendues publiques par les employeurs](https://developers.smartrecruiters.com/docs/posting-api) sont consultées sans clé pour une sélection d'entreprises intégrée au projet. Ce périmètre ne couvre pas toutes les entreprises utilisant SmartRecruiters.
- **Arbeitnow** : l'[API publique sans clé](https://www.arbeitnow.com/blog/job-board-api) couvre l'Europe, surtout l'Allemagne ; la couverture des stages français peut être faible.
- **France Travail** : l'API nécessite des identifiants autorisés pour l'API Offres d'emploi. Son accès est actuellement restreint ; la simple création d'un compte ne garantit pas l'accès au service. Elle est facultative : les sources publiques restent utilisables sans ces identifiants.

Une recherche peut ne retourner aucune offre alors que des stages existent sur d'autres sites. Tu peux toujours importer une annonce trouvée ailleurs.

### Ajouter France Travail

Si tu disposes déjà d'identifiants autorisés pour l'API **Offres d'emploi v2**, copie l'identifiant client et le secret client de ton application dans la configuration des sources de l'interface, puis enregistre. Le statut indique si les deux valeurs sont renseignées ; une recherche permet de vérifier que tes identifiants et tes droits fonctionnent.

Pour un nouvel accès, consulte la [fiche officielle de l'API](https://www.data.gouv.fr/dataservices/api-offres-demploi) puis adresse une demande via le [formulaire de contact France Travail](https://francetravail.io/contact). La fiche précise que l'API n'est plus librement exposée pendant l'examen de ses modalités de distribution et de réutilisation. Aucun identifiant public ou accès instantané n'est fourni par ce projet.

Les identifiants sont enregistrés dans le `.env` local et pris en compte immédiatement par le serveur. Les champs restent vides dans l'interface après l'enregistrement : laisser un champ vide conserve sa valeur existante. L'action de suppression de la configuration retire les identifiants France Travail. Les paramètres SMTP et Hunter déjà présents sont conservés. Les clés ne sont pas renvoyées par les endpoints de consultation ni par l'état du tableau de bord.

Tu peux aussi renseigner les variables directement dans `.env`, puis redémarrer le serveur :

```dotenv
FRANCE_TRAVAIL_CLIENT_ID=ton_identifiant
FRANCE_TRAVAIL_CLIENT_SECRET=ton_secret
```

La recherche de villes et la conversion des coordonnées utilisent l'[API française des communes](https://geo.api.gouv.fr/decoupage-administratif/communes). Les recherches d'offres, la résolution d'une ville et Hunter nécessitent une connexion Internet ; tes documents et brouillons sont préparés localement.

## Installation sous Windows

Dans PowerShell, avec Python 3.10 ou plus récent :

```powershell
Set-Location "D:\JeChercheTravailOhhh\JeChercheTravailOhhh"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Les commandes suivantes utilisent directement le Python de l'environnement ; aucune activation PowerShell n'est nécessaire.

## Préparer les candidatures spontanées

```powershell
.\.venv\Scripts\python.exe prepare_applications.py
```

Cette commande prépare les candidatures spontanées aux **8 entreprises de `companies.txt`** dans `candidatures/`. Elle ne recherche pas de contacts et n'envoie aucun email. Une candidature spontanée adapte le destinataire et l'entreprise ; elle ne prétend pas connaître des missions ou une offre qui n'ont pas été fournies.

Les huit dossiers sont déjà préparés. Pour les régénérer, utilise `--overwrite` après avoir sauvegardé les éventuelles retouches manuelles.

Pour utiliser une autre liste, avec un nom d'entreprise par ligne :

```powershell
.\.venv\Scripts\python.exe prepare_applications.py --companies mes_entreprises.txt
```

## Adapter les documents à une offre

Crée `offres.json` en recopiant le texte d'une offre réelle. Le fichier accepte un objet JSON ou une liste d'objets. `entreprise`, `intitule` et `description` sont obligatoires ; un `id` explicite facilite la sélection et la régénération. Il doit contenir 1 à 80 lettres minuscules, chiffres, tirets ou caractères `_`.

```json
{
  "id": "mon-offre",
  "entreprise": "Nom de l'entreprise",
  "intitule": "Intitulé exact du stage",
  "description": "Texte complet de l'offre : missions, compétences et conditions.",
  "lieu": "Ville, France",
  "date_debut": "2027-03-01",
  "duree_mois": 6,
  "contact": {"email": "", "prenom": "", "nom": ""}
}
```

`lieu`, `date_debut`, `duree_mois` et `contact` sont facultatifs. Une date structurée s'écrit `AAAA-MM-JJ`. Si un destinataire réel est renseigné dans `contact.email`, il sera utilisé pour l'aperçu et l'envoi de cette candidature. Sinon, le script d'envoi choisit les contacts du CSV dont le champ `entreprise` correspond au même nom d'entreprise.

```powershell
.\.venv\Scripts\python.exe prepare_applications.py --offers offres.json
.\.venv\Scripts\python.exe prepare_applications.py --offers offres.json --only mon-offre
```

Pour essayer le parcours avec les deux **offres fictives** fournies :

```powershell
.\.venv\Scripts\python.exe prepare_applications.py --offers offres.example.json --output exemples_generes
```

Ces exemples servent à vérifier les documents, notamment la détection d'une exigence LLM absente du profil. Ils ne représentent pas des offres auxquelles postuler.

Les autres options de préparation :

| Option | Utilisation |
| --- | --- |
| `--output dossier` | Choisir le dossier racine des candidatures. |
| `--date AAAA-MM-JJ` | Fixer la date inscrite sur les lettres ; sinon la date du jour est utilisée. |
| `--overwrite` | Régénérer les dossiers existants. Sauvegarder d'abord les modifications manuelles à conserver. |
| `--profile chemin.json` | Préparer les documents depuis un autre fichier de profil compatible. |

## Relire le dossier préparé

Chaque dossier `candidatures/<id>/` contient :

| Fichier | Contenu |
| --- | --- |
| `CV_CHRISTOPHER_KOUTOUA.pdf` | CV reconstitué depuis le profil, avec les compétences et projets pertinents mis en avant. |
| `Lettre_motivation.pdf` | Lettre adaptée à l'entreprise et aux éléments reconnus dans l'offre. |
| `lettre_motivation.txt` | Version texte de la lettre. |
| `email.txt` | Objet et corps de l'email, avec le prénom du contact à compléter lors de l'aperçu. |
| `analyse.json` | Correspondances, technologies non attestées et points à vérifier. |
| `offre.json` | Copie des informations utilisées pour cette candidature. |
| `manifest.json` | Association entre l'offre, le profil, le destinataire et les fichiers à joindre. |

La personnalisation fonctionne **localement, par mots-clés et alias**, sans appel à un modèle de langage. Elle réordonne les compétences et projets existants, les conserve dans le CV et utilise les projets les plus pertinents dans la lettre. L'analyse signale aussi certains outils cités dans l'offre sans acquis attesté, les différences de durée et une date de début antérieure à la disponibilité. Le lexique n'est pas exhaustif : relis les missions, le lieu, le niveau demandé et les PDF.

`candidate_profile.json` est la source des informations personnelles et de la signature. Il reprend les faits de `CHRISTOPHER_KOUTOUA.pdf`, ses liens, ainsi que le nom complet fourni par Christopher. L'IA générative et le data engineering sont des intérêts ; une pratique des modèles de langage n'est pas ajoutée aux acquis. Pour corriger une information ou ajouter une compétence réellement acquise, modifie ce profil puis régénère les candidatures concernées :

```powershell
.\.venv\Scripts\python.exe prepare_applications.py --offers offres.json --only mon-offre --overwrite
```

Un dossier généré avec un ancien profil doit être régénéré avant son envoi. L'envoi utilise le profil `candidate_profile.json` du projet ; les aperçus de dossiers produits avec `--profile` doivent également correspondre à ce profil.

## Contacts et aperçu de l'email

Si `contact.email` n'est pas renseigné dans l'offre, utilise un CSV existant ou ajoute les contacts manuellement. Les colonnes minimales sont `entreprise,email`, et les colonnes `prenom,nom` permettent de personnaliser la salutation.

```csv
entreprise,prenom,nom,email
```

L'aperçu d'un dossier affiche le destinataire, l'objet, le texte et les deux pièces jointes, sans connexion SMTP :

```powershell
.\.venv\Scripts\python.exe send_emails.py --application candidatures/mon-offre --contacts contacts.hr.csv
.\.venv\Scripts\python.exe send_emails.py --application candidatures/mon-offre --contacts contacts.hr.csv --only recrutement@example.fr
```

Si le contact est directement renseigné dans l'offre, `--contacts` est inutile. `--only` sélectionne une adresse parmi les contacts retenus pour cette candidature.

Tu peux également consulter le brouillon avant de rechercher un contact :

```powershell
.\.venv\Scripts\python.exe send_emails.py --application candidatures/sopra-steria-spontanee
```

Si aucun destinataire n'est disponible, l'aperçu affiche le mail et les pièces jointes avec une indication pour renseigner le contact. L'envoi exige une adresse réelle.

Le parcours initial reste disponible : il utilise `email_template.txt` et le CV original `CHRISTOPHER_KOUTOUA.pdf` pour les contacts du CSV, en aperçu par défaut :

```powershell
.\.venv\Scripts\python.exe send_emails.py
.\.venv\Scripts\python.exe send_emails.py --contacts contacts.hr.csv --only recrutement@example.fr
```

Les options `--cv fichier.pdf` et `--template fichier.txt` permettent de choisir d'autres fichiers dans ce parcours. Elles ne se combinent pas avec `--application`, qui fournit déjà ses propres documents.

## Configuration et actions externes

Le fichier `.env` existant reste la configuration privée du projet. S'il n'existe pas, crée-le depuis `.env.example`, puis renseigne ses valeurs localement :

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

`EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `SMTP_SERVER` et `SMTP_PORT` configurent le compte SMTP. La signature et l'adresse de réponse viennent du profil candidat. `HUNTER_API_KEY` sert uniquement à la recherche Hunter. Les identifiants restent dans `.env`, exclu de Git ; ne les recopie pas dans les offres, documents ou messages.

Pour vérifier la connexion Gmail sans envoyer de message, lance `.\.venv\Scripts\python.exe check_smtp.py`. Ce diagnostic lit le `.env`, vérifie `smtp.gmail.com:587`, établit une connexion TLS avec contrôle du certificat et teste l'authentification. Sa sortie JSON ne contient aucun identifiant ni réponse brute du serveur ; les codes de sortie sont `0` (authentification acceptée), `1` (configuration ou authentification refusée) et `2` (connexion réseau/TLS impossible). Il ne crée aucun destinataire et n'envoie aucun email.

Pour rechercher les contacts du département RH des entreprises de la liste, cette commande appelle Hunter et produit `contacts.hr.csv` :

```powershell
.\.venv\Scripts\python.exe script.py --department hr
```

Les options historiques `--input`, `--output` et `--department` restent disponibles, par exemple :

```powershell
.\.venv\Scripts\python.exe script.py --input mes_entreprises.txt --output mes_contacts.csv --department hr
```

**Dans les scripts en ligne de commande, l'envoi réel se déclenche uniquement avec `--send`.** Après avoir relu une candidature réelle et son aperçu, la commande suivante envoie son email avec le CV et la lettre joints au destinataire sélectionné :

```powershell
.\.venv\Scripts\python.exe send_emails.py --application candidatures/mon-offre --contacts contacts.hr.csv --only recrutement@example.fr --send
```

Remplace l'adresse d'exemple par celle du contact réel. Aucune commande de préparation ou d'aperçu n'envoie de candidature.

## Vérifications

Les tests du projet s'exécutent localement :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Pour consulter les options exactes d'une commande :

```powershell
.\.venv\Scripts\python.exe prepare_applications.py --help
.\.venv\Scripts\python.exe send_emails.py --help
.\.venv\Scripts\python.exe script.py --help
```
