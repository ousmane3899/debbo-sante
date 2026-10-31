"""Synchronisation de l'appli hors-ligne des relais (cahier des charges, §5).

L'appli met chaque action dans une file locale et l'envoie par lots dès qu'il
y a du réseau. Chaque opération porte un `op_id` unique : un lot renvoyé après
une coupure n'est jamais appliqué deux fois. Le serveur réévalue toujours les
signes avec son propre moteur clinique, qui fait foi.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import exiger
from app.clinical import moteur
from app.db import get_session
from app.journal import tracer
from app.models import (
    Alerte, Contact, Femme, Identite, OperationSync, Relais, SeanceVaccin, Utilisateur, Visite, maintenant,
)
from app.planning import (
    contacts_en_retard, enregistrer_accouchement, planifier_grossesse, prochain_contact, prochaine_seance,
    seances_en_retard, terme_prevu, zero_dose,
)
from app.rappels import fermer_taches, taches_ouvertes
from app.referral import ETATS_ACTIFS, ouvrir_alerte

router = APIRouter(prefix="/api/relais")


class OperationIn(BaseModel):
    op_id: str
    type: str
    cree_le: str | None = None
    donnees: dict = {}


class LotIn(BaseModel):
    operations: list[OperationIn]


class OperationRefusee(ValueError):
    pass


def _instant(texte: str | None) -> datetime:
    """Heure de l'action sur le téléphone (UTC), jamais dans le futur."""
    if not texte:
        return maintenant()
    try:
        instant = datetime.fromisoformat(texte.replace("Z", "+00:00"))
    except ValueError:
        return maintenant()
    if instant.tzinfo:
        instant = instant.astimezone(timezone.utc).replace(tzinfo=None)
    return min(instant, maintenant())


def _femme(session: Session, relais: Relais, ref: dict | None) -> Femme:
    if not ref:
        raise OperationRefusee("Femme non précisée")
    if ref.get("code"):
        femme = session.scalar(select(Femme).where(Femme.code == ref["code"]))
    else:
        femme = session.scalar(select(Femme).where(Femme.client_uid == ref.get("uid")))
    if femme is None:
        raise OperationRefusee("Femme introuvable")
    if femme.relais_id != relais.id and femme.zone != relais.zone:
        raise OperationRefusee("Cette femme n'est pas dans la zone de la relais")
    return femme


def _inscription(session: Session, relais: Relais, d: dict, instant: datetime) -> dict:
    if not d.get("consentement"):
        raise OperationRefusee("Le consentement de la femme est obligatoire")
    if not (d.get("nom") or "").strip():
        raise OperationRefusee("Nom obligatoire")
    telephone = (d.get("telephone") or "").strip() or None
    identite = session.scalar(select(Identite).where(Identite.telephone == telephone)) if telephone else None
    if identite is not None and identite.femme is not None:
        raise OperationRefusee(f"Ce numéro est déjà inscrit ({identite.femme.code})")
    if identite is None:
        identite = Identite(telephone=telephone)
    identite.nom = d["nom"].strip()
    identite.consentement_service = True
    femme = Femme(identite=identite, relais=relais, zone=relais.zone, client_uid=d.get("uid"))
    planifier_grossesse(femme, int(d.get("mois", 0)), instant.date())
    session.add(femme)
    session.flush()
    return {"code": femme.code}


def _visite(session: Session, relais: Relais, d: dict, instant: datetime) -> dict:
    femme = _femme(session, relais, d.get("femme"))
    contexte = d.get("contexte", "grossesse")
    pb_mm = int(d["pb_mm"]) if d.get("pb_mm") not in (None, "") else None
    evaluation = moteur.evaluer(d.get("signes", []), contexte, pb_mm)
    session.add(
        Visite(
            femme=femme,
            relais_id=relais.id,
            date=instant,
            contexte=contexte,
            signes=list(evaluation.signes_retenus + evaluation.signes_inconnus),
            niveau=evaluation.niveau,
            pb_mm=pb_mm,
            note=d.get("note"),
        )
    )
    fermer_taches(session, femme, instant)  # la relais est passée : les visites demandées sont faites
    resultat = {"niveau": evaluation.niveau}
    if moteur.rang(evaluation.niveau) >= moteur.rang("consultation_24h"):
        alerte = _ouvrir(session, relais, femme.identite, evaluation, contexte, instant)
        resultat["alerte_id"] = alerte.id
    return resultat


def _alerte(session: Session, relais: Relais, d: dict, instant: datetime) -> dict:
    if d.get("femme"):
        identite = _femme(session, relais, d["femme"]).identite
    else:
        identite = Identite(nom=f"Non inscrite, signalée par {relais.nom}")
    contexte = d.get("contexte", "grossesse")
    # La relais a déclenché l'urgence elle-même : le niveau ne peut pas être plus bas.
    evaluation = moteur.evaluer([*d.get("signes", []), "urgence_declaree"], contexte)
    alerte = _ouvrir(session, relais, identite, evaluation, contexte, instant)
    return {"niveau": evaluation.niveau, "alerte_id": alerte.id}


def _ouvrir(session, relais, identite, evaluation, contexte, instant) -> Alerte:
    alerte = ouvrir_alerte(session, identite, evaluation, contexte, canal="relais")
    alerte.relais = relais
    alerte.created_at = instant
    session.flush()
    return alerte


def _accouchement(session: Session, relais: Relais, d: dict, instant: datetime) -> dict:
    femme = _femme(session, relais, d.get("femme"))
    try:
        jour = date.fromisoformat(d.get("date", ""))
    except ValueError as erreur:
        raise OperationRefusee("Date d'accouchement invalide") from erreur
    enregistrer_accouchement(femme, jour, instant.date(), int(d.get("bebes", 1)))
    session.flush()
    return {"statut": femme.statut, "enfants": [e.code for e in femme.enfants]}


def _vaccin_fait(session: Session, relais: Relais, d: dict, instant: datetime) -> dict:
    seance = session.get(SeanceVaccin, d.get("seance_id"))
    if seance is None:
        raise OperationRefusee("Séance de vaccination introuvable")
    femme = _femme(session, relais, {"code": seance.enfant.mere.code})
    seance.date_realisee = instant.date()
    fermer_taches(session, femme, instant, seance=seance)
    return {}


def _contact_realise(session: Session, relais: Relais, d: dict, instant: datetime) -> dict:
    contact = session.get(Contact, d.get("contact_id"))
    if contact is None:
        raise OperationRefusee("Contact introuvable")
    femme = _femme(session, relais, {"code": contact.femme.code})
    contact.date_realisee = instant.date()
    fermer_taches(session, femme, instant, contact)
    return {}


TRAITEMENTS = {
    "inscription": _inscription,
    "visite": _visite,
    "alerte": _alerte,
    "accouchement": _accouchement,
    "contact_realise": _contact_realise,
    "vaccin_fait": _vaccin_fait,
}


def appliquer(session: Session, relais: Relais, op: OperationIn) -> dict:
    deja = session.get(OperationSync, op.op_id)
    if deja is not None:
        return {"op_id": op.op_id, "statut": "deja_recu", **deja.resultat}
    traitement = TRAITEMENTS.get(op.type)
    if traitement is None:
        return {"op_id": op.op_id, "statut": "erreur", "message": f"Type inconnu : {op.type}"}
    try:
        resultat = traitement(session, relais, op.donnees, _instant(op.cree_le))
    except (OperationRefusee, ValueError) as erreur:
        session.rollback()
        return {"op_id": op.op_id, "statut": "erreur", "message": str(erreur)}
    session.add(OperationSync(op_id=op.op_id, relais_id=relais.id, type=op.type, resultat=resultat))
    session.commit()
    return {"op_id": op.op_id, "statut": "ok", **resultat}


def _seance(s: SeanceVaccin) -> dict:
    return {"id": s.id, "nom": s.nom, "antigenes": s.antigenes, "date": s.date_prevue.isoformat(),
            "faite": s.date_realisee.isoformat() if s.date_realisee else None}


def _enfant(e, aujourdhui: date) -> dict:
    prochaine = prochaine_seance(e)
    return {
        "code": e.code,
        "rang": e.rang,
        "date_naissance": e.date_naissance.isoformat(),
        "prochaine_seance": _seance(prochaine) if prochaine else None,
        "seances_en_retard": len(seances_en_retard(e, aujourdhui)),
        "zero_dose": zero_dose(e, aujourdhui),
        "seances": [_seance(s) for s in e.seances],
    }


def dossier(session: Session, relais: Relais) -> dict:
    aujourdhui = date.today()
    femmes = session.scalars(
        select(Femme).where(or_(Femme.relais_id == relais.id, Femme.zone == relais.zone)).order_by(Femme.created_at)
    ).all()
    lignes = []
    for f in femmes:
        alerte = session.scalar(
            select(Alerte)
            .where(Alerte.identite_id == f.identite_id, Alerte.etat.in_(ETATS_ACTIFS))
            .order_by(Alerte.created_at.desc())
        )
        contact = prochain_contact(f)
        lignes.append(
            {
                "code": f.code,
                "uid": f.client_uid,
                "nom": f.identite.nom,
                "telephone": f.identite.telephone,
                "statut": f.statut,
                "terme_prevu": terme_prevu(f).isoformat() if terme_prevu(f) else None,
                "date_accouchement": f.date_accouchement.isoformat() if f.date_accouchement else None,
                "prochain_contact": (
                    {"id": contact.id, "type": contact.type, "date": contact.date_prevue.isoformat()} if contact else None
                ),
                "contacts_en_retard": len(contacts_en_retard(f, aujourdhui)),
                "derniere_visite": f.visites[-1].date.isoformat() + "Z" if f.visites else None,
                "alerte_active": {"niveau": alerte.niveau, "etat": alerte.etat} if alerte else None,
                "taches": [{"motif": t.motif, "depuis": t.created_at.isoformat() + "Z"} for t in taches_ouvertes(session, f)],
                "enfants": [_enfant(e, aujourdhui) for e in f.enfants],
            }
        )
    return {
        "relais": {"code": relais.code, "nom": relais.nom, "zone": relais.zone, "telephone_sf": relais.telephone_sf},
        "regles": {
            "version": moteur.version,
            "niveaux": moteur.niveaux,
            "niveau_signe_inconnu": moteur.niveau_inconnu,
            "seuils_pb_mm": moteur.seuils_pb,
            "signes": moteur.signes,
        },
        "femmes": lignes,
        "genere_le": maintenant().isoformat() + "Z",
    }


def _relais(u: Utilisateur) -> Relais:
    if u.relais is None:
        raise HTTPException(403, "Compte relais sans relais rattachée")
    return u.relais


@router.get("/moi/dossier")
def lire_dossier(u: Utilisateur = Depends(exiger("relais")), session: Session = Depends(get_session)) -> dict:
    return dossier(session, _relais(u))


@router.post("/moi/sync")
def synchroniser(lot: LotIn, request: Request, u: Utilisateur = Depends(exiger("relais")),
                 session: Session = Depends(get_session)) -> dict:
    relais = _relais(u)
    resultats = [appliquer(session, relais, op) for op in lot.operations]
    if lot.operations:
        tracer(session, "synchro_relais", u.id, f"{len(lot.operations)} operation(s)",
               request.client.host if request.client else None)
        session.commit()
    return {"resultats": resultats, "dossier": dossier(session, relais)}
