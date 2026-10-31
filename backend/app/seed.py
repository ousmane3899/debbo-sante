"""Données de démonstration, toutes fictives : python -m app.seed

Refuse de tourner en production. Les comptes de démonstration ci-dessous
n'existent que dans une base de développement ; la double authentification
s'active à leur première connexion.
"""

import sys
from datetime import date, timedelta

from app import config
from app.clinical import moteur
from app.comptes import creer_relais, creer_soignant
from app.db import SessionLocal, init_db, vider_base
from app.models import DemandeRappel, Femme, Identite, maintenant
from app.planning import enregistrer_accouchement, planifier_grossesse
from app.referral import faire_transition, ouvrir_alerte

# Comptes de démonstration (développement uniquement).
MOT_DE_PASSE_DEMO = "demo-debbo-matam"
PIN_RELAIS_DEMO = "482917"
COMPTES_DEMO = [
    ("sf.demo", "Sage-femme démo", "sage_femme", "Démo"),
    ("sf.autre", "Sage-femme autre zone", "sage_femme", "Autre zone"),
    ("district.demo", "Équipe district démo", "district", None),
    ("admin.demo", "Administrateur démo", "admin", None),
]


def peupler() -> None:
    if config.EN_PROD:
        sys.exit("Refusé : python -m app.seed efface la base, il ne tourne pas en production.")
    vider_base()
    init_db()
    aujourdhui = date.today()
    with SessionLocal() as session:
        for identifiant, nom, role, zone in COMPTES_DEMO:
            creer_soignant(session, identifiant, nom, role, MOT_DE_PASSE_DEMO, zone)
        relais = creer_relais(session, "R-0001", "Relais démo", "Démo", PIN_RELAIS_DEMO,
                              telephone="+221770000800", telephone_sf="+221770000900").relais

        femmes = []
        exemples = [
            ("Femme démo 1", "+221770000011", 3, 0),
            ("Femme démo 2", "+221770000012", 6, 0),
            ("Femme démo 3", "+221770000013", 4, 60),
            ("Femme démo 4 (sans téléphone)", None, 8, 0),
        ]
        for nom, numero, mois, inscrite_il_y_a in exemples:
            identite = Identite(nom=nom, telephone=numero, langue="ff", consentement_service=True)
            femme = Femme(identite=identite, relais=relais, zone="Démo")
            planifier_grossesse(femme, mois, aujourdhui - timedelta(days=inscrite_il_y_a))
            session.add(femme)
            femmes.append(femme)

        # Consultation demain, au numéro par défaut du simulateur : son appel de veille
        # (« Debbo m'appelle ») apparaît dans /simulateur dès le premier passage du planificateur.
        identite = Identite(nom="Femme démo 5 (consultation demain)", telephone="+221770000101", langue="fr",
                            consentement_service=True)
        demain = Femme(identite=identite, relais=relais, zone="Démo")
        planifier_grossesse(demain, 5, aujourdhui)
        demain.contacts[0].date_prevue = aujourdhui + timedelta(days=1)
        session.add(demain)

        # Bébé de 2 mois sans Penta 1 : enfant « zéro dose », visite demandée à la relais.
        identite = Identite(nom="Femme démo 6 (bébé de 2 mois)", telephone="+221770000102", langue="ff",
                            consentement_service=True)
        jeune_mere = Femme(identite=identite, relais=relais, zone="Démo")
        naissance = aujourdhui - timedelta(days=62)
        enregistrer_accouchement(jeune_mere, naissance, naissance, bebes=1)
        for c in jeune_mere.contacts:
            c.date_realisee = c.date_prevue
        jeune_mere.enfants[0].seances[0].date_realisee = naissance
        session.add(jeune_mere)

        urgence = ouvrir_alerte(session, femmes[1].identite, moteur.evaluer(["saignement", "urgence_declaree"]), "grossesse", "ussd")
        urgence.created_at = maintenant() - timedelta(minutes=7)

        en_route = ouvrir_alerte(session, femmes[0].identite, moteur.evaluer(["cephalees_vision", "urgence_declaree"]), "grossesse", "voix")
        en_route.created_at = maintenant() - timedelta(minutes=18)
        faire_transition(en_route, "sf_jointe", "sage-femme (démo)")
        faire_transition(en_route, "transport_parti", "sage-femme (démo)", transporteur="Charrette d'Amadou (fictif)")

        session.add(DemandeRappel(identite=femmes[2].identite, motif="sage_femme", prioritaire=True))
        session.add(DemandeRappel(identite=femmes[0].identite, motif="rappel", creneau="soir"))
        session.commit()
    print("Base de démonstration prête. Comptes de démonstration : voir COMPTES_DEMO dans app/seed.py.")


if __name__ == "__main__":
    peupler()
