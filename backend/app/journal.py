"""Journal d'accès chaîné (cahier des charges, §8 : journal d'accès inaltérable)."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JournalAcces, maintenant

ORIGINE = "0" * 64


def _empreinte(precedente: str, ligne: JournalAcces) -> str:
    contenu = json.dumps(
        [ligne.at.isoformat(), ligne.utilisateur_id, ligne.action, ligne.cible, ligne.ip], ensure_ascii=False
    )
    return hashlib.sha256((precedente + contenu).encode()).hexdigest()


def tracer(session: Session, action: str, utilisateur_id: int | None = None, cible: str | None = None,
           ip: str | None = None) -> JournalAcces:
    """Ajoute une ligne ; elle est enregistrée avec la transaction en cours."""
    derniere = session.scalar(select(JournalAcces).order_by(JournalAcces.id.desc()).limit(1))
    precedente = derniere.empreinte if derniere else ORIGINE
    ligne = JournalAcces(at=maintenant(), utilisateur_id=utilisateur_id, action=action, cible=cible, ip=ip,
                         precedente=precedente)
    ligne.empreinte = _empreinte(precedente, ligne)
    session.add(ligne)
    session.flush()
    return ligne


def verifier(session: Session) -> int | None:
    """Renvoie l'id de la première ligne altérée, ou None si la chaîne est intacte."""
    precedente = ORIGINE
    for ligne in session.scalars(select(JournalAcces).order_by(JournalAcces.id)):
        if ligne.precedente != precedente or ligne.empreinte != _empreinte(precedente, ligne):
            return ligne.id
        precedente = ligne.empreinte
    return None
