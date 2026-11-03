# Debbo Santé

Suivi de la santé maternelle et infantile pour la région de Matam, accessible
sans Internet (USSD, voix). Ce dépôt suit le cahier des charges v2 ; il en
couvre aujourd'hui la phase MVP.

## Ce qui existe

| Brique | Fichier | État |
| --- | --- | --- |
| Moteur clinique déterministe (signes de danger OMS) | `backend/app/clinical/` | Règles en **brouillon**, à valider par les référents médicaux |
| Banc de scénarios critiques (cible : 100 % bien classés) | `backend/tests/scenarios/critiques.json` | 37 scénarios |
| Parcours USSD : urgence, inscription, rendez-vous, rappel, conseils | `backend/app/ussd.py` | Français rédigé ; pulaar et wolof à écrire |
| Calendrier de suivi (8 contacts prénatals OMS, 4 postnatals) | `backend/app/planning.py` | — |
| Suivi de l'enfant 0-24 mois : calendrier vaccinal PEV par enfant (jumeaux compris), repérage zéro dose, Penta 3 | `backend/app/clinical/pev.json`, `backend/app/planning.py` | Calendrier en **brouillon**, à valider par la région médicale |
| « Debbo m'appelle » : appel la veille de chaque consultation, 2 essais, puis visite de la relais ; relance des consultations manquées | `backend/app/rappels.py` | Fournisseur de téléphonie **simulé** ; vrai fournisseur à brancher |
| Boucle de référence : alerte → appel → transport → arrivée | `backend/app/referral.py` | Paiement mobile money à brancher |
| Tableau de bord sage-femme | `backend/app/static/index.html` | Connexion avec double authentification |
| Simulateur de téléphone USSD | `/simulateur` | — |
| Appli relais hors-ligne (PWA installable sur Android) | `backend/app/static/relais/`, servie sur `/relais/` | Inscription, visites guidées, PB, urgence, accouchement |
| Synchronisation par lots, idempotente | `backend/app/relais.py` | Le serveur réévalue chaque visite |
| Copie JS du moteur clinique (pour le hors-ligne) | `backend/app/static/relais/moteur.js` | Passe le même banc de scénarios |

### Appli relais

- Chaque action va dans une file locale (IndexedDB) et part par lots dès que le réseau revient ;
  un renvoi n'est jamais appliqué deux fois (`op_id`).
- Une visite s'arrête au premier signe d'urgence. Sans réseau, l'appli demande d'appeler ou
  d'envoyer un SMS à la sage-femme d'astreinte.
- Une visite classée « sous 24 h » ou « urgence » ouvre une alerte dans le tableau de bord,
  datée à l'heure de la visite (pas à l'heure de l'envoi).
- Le service worker et `crypto.randomUUID` exigent HTTPS (sauf sur `localhost`) : à déployer derrière HTTPS.

L'identité (numéro) est stockée à part des données de santé (`Identite`).

## Base de données et migrations

SQLite en développement, PostgreSQL en production (`DEBBO_DATABASE_URL`, par exemple
`postgresql+psycopg://debbo:…@hote/debbo`). Le schéma évolue par migrations Alembic
(`backend/migrations/versions/`), jamais en recréant les tables.

```bash
cd backend
../.venv/Scripts/alembic upgrade head                       # amener la base à la dernière version
../.venv/Scripts/alembic revision --autogenerate -m "…"     # après une modification de app/models.py
```

- En développement, le serveur migre la base tout seul au démarrage.
- En production, il **refuse de démarrer** sur une base en retard : sauvegarder, puis `alembic upgrade head`.
- Toute nouvelle migration doit passer `tests/test_migrations.py` : schéma identique au modèle, descente et remontée.
- Une valeur par défaut d'une colonne booléenne s'écrit `sa.false()`, jamais `'0'` (refusé par PostgreSQL).

## Export vers DHIS2

Chaque mois, Debbo calcule des **totaux par zone** (jamais de dossier individuel) et les envoie au DHIS2
national (`POST /api/dataValueSets`, période `AAAAMM`, jeton d'accès personnel). Indicateurs : inscriptions,
consultations prénatales et postnatales, accouchements, naissances vivantes, urgences et arrivées au soin,
Penta 1, Penta 3, RR 1, enfants zéro dose, visites des relais.

- La correspondance avec les identifiants DHIS2 (éléments de données, structures) est dans
  `backend/app/dhis2_correspondances.json`, **à compléter avec le ministère**. Tant qu'un identifiant
  vaut `A_RENSEIGNER`, la valeur n'est pas envoyée et l'écran le signale.
- L'équipe de district voit l'aperçu du mois et envoie depuis le tableau de bord ; chaque envoi est tracé.
  Renvoyer un mois remplace ses valeurs dans DHIS2.
- Tâche mensuelle possible : `python -m app.dhis2 envoyer --periode 2026-10` (`apercu` pour voir sans envoyer).
- Réglages : `DEBBO_DHIS2_URL` (HTTPS obligatoire en production) et `DEBBO_DHIS2_JETON`.

## Consentement et effacement

| Droit | Où |
| --- | --- |
| Consentement en trois niveaux séparés : service (obligatoire pour être suivie), recherche, amélioration de l'assistant (facultatifs, refusés par défaut) | inscription USSD et relais ; USSD « 6 Mes données » pour changer d'avis |
| Effacement (« Debbo, oublie-moi ») : nom, téléphone, langue et notes libres supprimés, suivi arrêté ; événements de santé gardés sans identité pour les statistiques | USSD « 6 Mes données → 1 », appli relais, admin (`POST /api/admin/femmes/{code}/effacer`) |
| Pendant une urgence en cours, l'effacement attend la clôture de l'alerte ; les rappels s'arrêtent tout de suite | `app/vie_privee.py` |
| Jeu de données de recherche : femmes d'accord seulement, pseudonyme à clé, mois au lieu des dates, zones de moins de 10 femmes fusionnées | `GET /api/recherche/export` (district) |

## Mode discret (téléphone partagé)

- Par USSD (« 6 Mes données → 4 Code personnel »), la femme peut choisir un code de 4 chiffres (haché, jamais stocké en clair).
- Avec un code, « Mes rendez-vous », « Je suis enceinte » et « Mes données » (effacement compris) le demandent d'abord ;
  3 erreurs bloquent 30 minutes. **Urgence, rappel, conseils et « parler à une sage-femme » restent ouverts sans code.**
- Les appels de Debbo deviennent sans contenu : « vous avez un message, composez le code Debbo quand vous le pourrez ».
- Code oublié : la relais (qui la connaît en personne) ou le poste (`POST /api/admin/femmes/{code}/retirer-code`) le retire.

## Zone des femmes inscrites par USSD

À l'inscription, la femme choisit sa zone dans la liste des zones des relais (« 0 Je ne sais pas » possible). Une femme
sans zone reste visible de toutes les sages-femmes, pour qu'aucune urgence ne se perde ; une sage-femme peut la rattacher
à son aire depuis le tableau de bord (le district peut changer n'importe quelle zone).

## Sécurité

| Mesure | Où |
| --- | --- |
| Soignants : mot de passe (12 caractères min.) puis code TOTP à 6 chiffres ; activation obligatoire à la première connexion, par QR code | `app/auth.py`, `/connexion` |
| Relais : code relais + PIN à 6 chiffres ; session de 30 jours prolongée à chaque usage, reconnexion automatique | `app/auth.py` |
| Mots de passe et PIN hachés par scrypt ; seuls les empreintes des jetons de session sont stockées | `app/securite.py` |
| 5 échecs de suite : compte verrouillé 15 min ; codes TOTP non rejouables | `app/auth.py` |
| Droits : `sage_femme` voit son aire + les dossiers sans zone (appels USSD) ; `district` voit tout ; `admin` gère les comptes et ne voit aucun dossier ; `relais` ne voit que sa zone | `app/api.py`, `app/relais.py` |
| Journal d'accès chaîné : toute modification d'une ligne est détectée | `app/journal.py` |
| Cookie HttpOnly, Secure, SameSite=Strict ; écritures depuis un autre site refusées ; CSP stricte, pas d'iframe | `app/main.py` |
| Appli relais : données chiffrées sur le téléphone (AES-GCM, clé dérivée du PIN), verrouillage après 5 min en arrière-plan, effacement après 10 PIN faux | `static/relais/stockage.js` |
| Secret partagé avec l'agrégateur USSD | `DEBBO_USSD_SECRET` |

Limite connue : un PIN à 6 chiffres reste devinable par force brute si quelqu'un copie la
base locale hors du téléphone ; le chiffrement de l'appareil et un verrouillage d'écran restent nécessaires.

### Comptes

Un compte `admin` gère les comptes depuis http://localhost:8000/admin (il ne voit aucun dossier de santé) :

- créer une sage-femme, une relais, un compte district ou admin. Debbo affiche **une seule fois** un mot de passe
  (ou un PIN pour une relais) provisoire, valable 72 h, à remettre en main propre ;
- à la première connexion, la personne choisit son propre mot de passe (puis active la double authentification)
  ou son propre PIN dans l'appli relais : l'administration ne le connaît jamais ;
- mot de passe ou PIN oublié → nouveau secret provisoire ; téléphone perdu → double authentification réinitialisée ;
  déverrouiller, désactiver, réactiver, changer d'aire (la sage-femme est déconnectée et ne voit plus que la nouvelle) ;
- un admin ne peut pas réinitialiser ni désactiver son propre compte : il faut un second admin ;
- demandes reçues au poste : code personnel USSD oublié, effacement des données ;
- journal d'accès (chaîne vérifiée, auteur de chaque action).

Le premier compte admin se crée en ligne de commande :

```bash
cd backend
../.venv/Scripts/python -m app.comptes soignant --identifiant awa.sf --nom "Awa Ba" --role sage_femme --zone "Ourossogui"
../.venv/Scripts/python -m app.comptes relais --code R-0002 --nom "Coumba Sy" --zone "Ourossogui" --telephone-sf +221...
../.venv/Scripts/python -m app.comptes verifier-journal
```

Les comptes de démonstration (développement uniquement) sont dans `COMPTES_DEMO` de `app/seed.py`.

## « Debbo m'appelle »

Le serveur lance le planificateur chaque minute (`app/rappels.py`) :

- la veille d'une consultation, à 10 h, Debbo appelle la femme (message neutre : le téléphone peut être partagé).
  Touche 1 : elle confirme. Touche 2 : une sage-femme la rappelle ;
- pas de réponse : nouvel essai 3 h plus tard ; après 2 essais, la relais reçoit une visite à faire ;
- femme sans téléphone : la relais reçoit directement la visite à faire ;
- consultation manquée depuis 7 jours : appel de relance et visite de la relais ;
- mêmes règles pour les vaccins de chaque enfant (veille de la séance ; relance et visite après 14 jours de retard) ;
- jamais plus d'un appel par femme et par jour (la visite postnatale et les vaccins des 6 semaines tombent le même jour) ;
- aucun appel entre 20 h et 8 h, même en retard. Une visite de la relais clôt les visites demandées.

En développement, le fournisseur est simulé : les appels apparaissent dans `/simulateur`, où l'on répond à la place de la femme.
Un vrai fournisseur implémente `Fournisseur.appeler` (`app/telephonie.py`) et renvoie les résultats sur
`POST /api/telephonie/resultat` avec le secret `DEBBO_TELEPHONIE_SECRET`.

### Production

`DEBBO_ENV=prod` coupe le simulateur et la documentation de l'API, active HSTS, et exige
`DEBBO_USSD_SECRET`, `DEBBO_TELEPHONIE_SECRET` et `DEBBO_SECRET_PSEUDONYMES` (clé des pseudonymes de recherche,
à garder secrète et stable). Il refuse de démarrer avec le fournisseur de téléphonie
simulé, sauf si le planificateur est coupé (`DEBBO_PLANIFICATEUR=0`). Le serveur doit être derrière HTTPS (cookie `Secure`, service worker).

## Lancer en local

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
cd backend
../.venv/Scripts/python -m app.seed
../.venv/Scripts/python -m uvicorn app.main:app --reload
```

Puis ouvrir http://localhost:8000 (tableau de bord), http://localhost:8000/admin (comptes),
http://localhost:8000/relais/ (appli relais) et http://localhost:8000/simulateur.

## Tests

```bash
cd backend
../.venv/Scripts/python -m pytest
node --test tests/js/moteur.test.mjs
../.venv/Scripts/python scripts/tester_postgres.py    # toute la suite sur un PostgreSQL jetable
```

`tester_postgres.py` crée une instance PostgreSQL neuve dans un dossier temporaire (programmes
cherchés dans `PG_BIN`, le PATH ou `C:\Program Files\PostgreSQL`), puis la détruit ; il ne touche
à aucune base existante.

Toute modification de `regles.json` doit repasser le banc de scénarios, côté serveur et côté appli.

## Avant un pilote

- Audit de sécurité indépendant et test d'intrusion.
- Textes pulaar et wolof validés par 2 locuteurs natifs et 1 clinicien.
- Règles cliniques signées par les référents médicaux ; calendrier PEV vérifié (VPI 2, MenA).
- Branchements : agrégateur USSD/SMS/voix, fournisseur d'appels sortants (messages enregistrés en pulaar et wolof), mobile money.
- DHIS2 : identifiants des éléments et des structures (`dhis2_correspondances.json`), compte d'envoi et jeton, essai sur le serveur de test du ministère.
- Déclaration CDP et analyse d'impact sur la protection des données.
