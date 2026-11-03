"""Téléphonie sortante : interface du fournisseur, fournisseur simulé, retour des résultats.

Un vrai fournisseur (agrégateur voix du Sénégal) implémentera `Fournisseur.appeler`
et notifiera le résultat de chaque appel sur POST /api/telephonie/resultat,
avec le secret partagé dans l'en-tête X-Debbo-Telephonie-Secret.
"""

import hmac
import secrets
from typing import Protocol

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_session
from app.i18n import t
from app.models import AppelSortant, Femme, Identite, maintenant


class Fournisseur(Protocol):
    def appeler(self, telephone: str, message: str, langue: str | None) -> str:
        """Lance l'appel et renvoie sa référence chez le fournisseur."""
        ...


class FournisseurSimule:
    """Ne passe aucun appel réel : l'appel reste « en cours » jusqu'à une réponse du simulateur."""

    def appeler(self, telephone: str, message: str, langue: str | None) -> str:
        return "sim-" + secrets.token_hex(8)


def fournisseur_par_defaut() -> Fournisseur:
    if config.TELEPHONIE == "simule":
        return FournisseurSimule()
    raise RuntimeError(f"Fournisseur de téléphonie inconnu : {config.TELEPHONIE}")


router = APIRouter()


class ResultatIn(BaseModel):
    reference: str
    statut: str  # repondu | sans_reponse | echec
    touche: str | None = None


def verifier_fournisseur(x_debbo_telephonie_secret: str | None = Header(default=None)) -> None:
    if config.TELEPHONIE_SECRET and not hmac.compare_digest(x_debbo_telephonie_secret or "", config.TELEPHONIE_SECRET):
        raise HTTPException(403, "Fournisseur non reconnu")


@router.post("/api/telephonie/resultat", dependencies=[Depends(verifier_fournisseur)])
def resultat(corps: ResultatIn, session: Session = Depends(get_session)) -> dict:
    from app.rappels import enregistrer_resultat

    try:
        appel = enregistrer_resultat(session, corps.reference, corps.statut, corps.touche, maintenant())
    except KeyError as erreur:
        raise HTTPException(404, "Appel inconnu") from erreur
    except ValueError as erreur:
        raise HTTPException(422, str(erreur)) from erreur
    session.commit()
    return {"reference": appel.reference, "statut": appel.statut}


@router.get("/api/simulateur/appels")
def appels_simules(telephone: str, session: Session = Depends(get_session)) -> list[dict]:
    """Développement uniquement : les appels en cours vers ce numéro, pour y répondre depuis le simulateur."""
    from app.rappels import message_appel

    if not config.SIMULATEUR_ACTIF:
        raise HTTPException(404)
    appels = session.scalars(
        select(AppelSortant).join(Femme).join(Identite)
        .where(AppelSortant.statut == "en_cours", Identite.telephone == telephone)
        .order_by(AppelSortant.prevu_at)
    ).all()
    return [
        {"reference": a.reference, "message": t(message_appel(a), a.femme.identite.langue), "tentative": a.tentative}
        for a in appels
    ]
