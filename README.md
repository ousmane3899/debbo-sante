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
| Calendrier « Debbo m'appelle » (8 contacts prénatals OMS) | `backend/app/planning.py` | Calcul fait ; appels sortants à brancher |
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

```bash
cd backend
../.venv/Scripts/python -m app.comptes soignant --identifiant awa.sf --nom "Awa Ba" --role sage_femme --zone "Ourossogui"
../.venv/Scripts/python -m app.comptes relais --code R-0002 --nom "Coumba Sy" --zone "Ourossogui" --telephone-sf +221...
../.venv/Scripts/python -m app.comptes verifier-journal
```

Les comptes de démonstration (développement uniquement) sont dans `COMPTES_DEMO` de `app/seed.py`.

### Production

`DEBBO_ENV=prod` coupe le simulateur et la documentation de l'API, active HSTS, et exige
`DEBBO_USSD_SECRET`. Le serveur doit être derrière HTTPS (cookie `Secure`, service worker).

## Lancer en local

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
cd backend
../.venv/Scripts/python -m app.seed
../.venv/Scripts/python -m uvicorn app.main:app --reload
```

Puis ouvrir http://localhost:8000 (tableau de bord), http://localhost:8000/relais/ (appli relais) et http://localhost:8000/simulateur.

## Tests

```bash
cd backend
../.venv/Scripts/python -m pytest
node --test tests/js/moteur.test.mjs
```

Toute modification de `regles.json` doit repasser le banc de scénarios, côté serveur et côté appli.

## Avant un pilote

- Audit de sécurité indépendant et test d'intrusion.
- Interface d'administration des comptes (aujourd'hui : ligne de commande et API).
- Rattachement à une zone des femmes inscrites par USSD (aujourd'hui visibles de toutes les sages-femmes).
- Textes pulaar et wolof validés par 2 locuteurs natifs et 1 clinicien.
- Règles cliniques signées par les référents médicaux.
- Branchements : agrégateur USSD/SMS/voix, appels sortants, mobile money, export DHIS2.
- Déclaration CDP et analyse d'impact sur la protection des données.
