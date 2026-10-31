"""Réglages lus dans l'environnement.

DEBBO_ENV=prod active les protections de production : simulateur USSD coupé,
secret de l'agrégateur USSD obligatoire, HSTS.
"""

import os

ENV = os.environ.get("DEBBO_ENV", "dev")
EN_PROD = ENV == "prod"

# Secret partagé avec l'agrégateur USSD, envoyé dans l'en-tête X-Debbo-Ussd-Secret.
USSD_SECRET = os.environ.get("DEBBO_USSD_SECRET") or None

if EN_PROD and not USSD_SECRET:
    raise RuntimeError("DEBBO_USSD_SECRET est obligatoire quand DEBBO_ENV=prod")

SIMULATEUR_ACTIF = not EN_PROD

# Téléphonie sortante (« Debbo m'appelle »). Seul le fournisseur simulé existe pour l'instant :
# il ne passe aucun vrai appel, le simulateur USSD permet d'y répondre.
TELEPHONIE = os.environ.get("DEBBO_TELEPHONIE", "simule")
TELEPHONIE_SECRET = os.environ.get("DEBBO_TELEPHONIE_SECRET") or None
PLANIFICATEUR_ACTIF = os.environ.get("DEBBO_PLANIFICATEUR", "1") == "1"

# Clé des pseudonymes du jeu de données de recherche : sans elle, impossible de relier
# une ligne du jeu de données à une femme. À garder secrète et stable.
SECRET_PSEUDONYMES = os.environ.get("DEBBO_SECRET_PSEUDONYMES") or None
if EN_PROD and not SECRET_PSEUDONYMES:
    raise RuntimeError("DEBBO_SECRET_PSEUDONYMES est obligatoire quand DEBBO_ENV=prod")
SECRET_PSEUDONYMES = SECRET_PSEUDONYMES or "cle-de-developpement-uniquement"

if EN_PROD and PLANIFICATEUR_ACTIF and TELEPHONIE == "simule":
    raise RuntimeError("En production, branchez un vrai fournisseur (DEBBO_TELEPHONIE) ou DEBBO_PLANIFICATEUR=0")
if EN_PROD and not TELEPHONIE_SECRET:
    raise RuntimeError("DEBBO_TELEPHONIE_SECRET est obligatoire quand DEBBO_ENV=prod")
