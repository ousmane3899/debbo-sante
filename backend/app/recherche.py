"""Jeu de données de recherche (évaluation de l'impact, cahier des charges §11).

N'y figurent que les femmes qui ont accepté la recherche (consentement à part du
service). Aucun nom, téléphone, code utilisé au poste ni date précise : un
pseudonyme à clé (impossible à relier sans DEBBO_SECRET_PSEUDONYMES), le mois
d'inscription, et la zone, fusionnée en « autre » quand elle compte moins de
SEUIL_PETITS_EFFECTIFS femmes (pour ne pas désigner quelqu'un dans un petit village).
"""

from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import exiger
from app.db import get_session
from app.journal import tracer
from app.models import Alerte, Femme, Identite, Utilisateur
from app.planning import penta3_a_jour, zero_dose
from app.vie_privee import pseudonyme_recherche

SEUIL_PETITS_EFFECTIFS = 10

router = APIRouter(prefix="/api/recherche")


def jeu_de_donnees(session: Session, aujourdhui: date) -> list[dict]:
    femmes = session.scalars(
        select(Femme).join(Identite).where(Identite.consentement_recherche, Femme.statut != "effacee")
    ).all()
    effectifs = Counter(f.zone for f in femmes)
    lignes = []
    for f in femmes:
        alertes = Counter(a.niveau for a in session.scalars(select(Alerte).where(Alerte.identite_id == f.identite_id)))
        lignes.append({
            "id": pseudonyme_recherche(f.id),
            "zone": f.zone if f.zone and effectifs[f.zone] >= SEUIL_PETITS_EFFECTIFS else "autre",
            "mois_inscription": f.created_at.strftime("%Y-%m"),
            "statut": f.statut,
            "contacts_prevus": len(f.contacts),
            "contacts_realises": sum(c.date_realisee is not None for c in f.contacts),
            "alertes": dict(alertes),
            "enfants": [{"zero_dose": zero_dose(e, aujourdhui), "penta3_a_jour": penta3_a_jour(e, aujourdhui)}
                        for e in f.enfants],
        })
    return lignes


@router.get("/export")
def exporter(request: Request, u: Utilisateur = Depends(exiger("district")),
             session: Session = Depends(get_session)) -> dict:
    lignes = jeu_de_donnees(session, date.today())
    tracer(session, "export_recherche", u.id, f"{len(lignes)} ligne(s)", request.client.host if request.client else None)
    session.commit()
    return {"seuil_petits_effectifs": SEUIL_PETITS_EFFECTIFS, "lignes": lignes}
