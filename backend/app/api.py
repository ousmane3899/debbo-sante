"""API du tableau de bord des soignants.

Droits : seuls les rôles « sage_femme » et « district » voient des données de
santé. Une sage-femme ne voit que son aire (sa zone) et les dossiers pas encore
rattachés à une zone, comme les appels USSD : une urgence ne doit jamais
rester invisible. Le district voit toutes les zones. L'admin gère les comptes
mais ne voit aucun dossier.
"""

from datetime import date, datetime, timedelta
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import exiger
from app.clinical import moteur
from app.db import get_session
from app.journal import tracer
from app.models import (
    Alerte, AppelSortant, Contact, DemandeRappel, Enfant, Femme, Identite, SeanceVaccin, TacheRelais, Utilisateur,
    maintenant,
)
from app.planning import (
    TOLERANCE_VACCIN, contacts_en_retard, penta3_a_jour, prochain_contact, terme_prevu, zero_dose,
)
from app.rappels import fermer_taches
from app.referral import ETATS_ACTIFS, TransitionInvalide, doit_escalader, faire_transition, minutes_entre

ROLES_SANTE = ("sage_femme", "district")

router = APIRouter(prefix="/api")
soignant = exiger(*ROLES_SANTE)


class TransitionIn(BaseModel):
    etat: str
    note: str | None = None
    transporteur: str | None = None
    structure: str | None = None


def _iso(d: datetime | date | None) -> str | None:
    if d is None:
        return None
    return d.isoformat() + ("Z" if isinstance(d, datetime) else "")


def voit(u: Utilisateur, zone: str | None) -> bool:
    return u.role == "district" or zone is None or zone == u.zone


def zone_identite(i: Identite) -> str | None:
    return i.femme.zone if i.femme else None


def zone_alerte(a: Alerte) -> str | None:
    return a.relais.zone if a.relais else zone_identite(a.identite)


def _acteur(u: Utilisateur) -> str:
    return f"{u.nom} ({u.identifiant})"


def _alerte(a: Alerte, a_l_instant: datetime) -> dict:
    femme = a.identite.femme
    return {
        "id": a.id,
        "code": femme.code if femme else None,
        "nom": a.identite.nom,
        "telephone": a.identite.telephone,
        "relais": {"nom": a.relais.nom, "zone": a.relais.zone, "telephone": a.relais.telephone} if a.relais else None,
        "zone": zone_alerte(a),
        "niveau": a.niveau,
        "etat": a.etat,
        "contexte": a.contexte,
        "signes": [{"code": s, "libelle": moteur.signes.get(s, {}).get("libelle", s)} for s in a.signes],
        "canal": a.canal,
        "regles_version": a.regles_version,
        "transporteur": a.transporteur,
        "structure": a.structure,
        "created_at": _iso(a.created_at),
        "minutes_ecoulees": minutes_entre(a.created_at, a.cloturee_at or a.arrivee_at or a_l_instant),
        "escalade": doit_escalader(a, a_l_instant),
        "evenements": [
            {"type": e.type, "acteur": e.acteur, "note": e.note, "at": _iso(e.at)} for e in a.evenements
        ],
    }


@router.get("/alertes")
def lister_alertes(actives: bool = True, u: Utilisateur = Depends(soignant),
                   session: Session = Depends(get_session)) -> list[dict]:
    requete = select(Alerte).order_by(Alerte.created_at.desc())
    if actives:
        requete = requete.where(Alerte.etat.in_(ETATS_ACTIFS))
    alertes = [a for a in session.scalars(requete).all() if voit(u, zone_alerte(a))]
    a_l_instant = maintenant()
    # D'abord les femmes que personne n'a encore rappelées, puis les plus graves, puis les plus anciennes.
    rang = {"urgence": 0, "consultation_24h": 1, "conseil": 2}
    alertes.sort(key=lambda a: (a.etat != "ouverte", rang.get(a.niveau, 3), a.created_at))
    return [_alerte(a, a_l_instant) for a in alertes]


@router.post("/alertes/{alerte_id}/transition")
def transition(alerte_id: int, corps: TransitionIn, request: Request, u: Utilisateur = Depends(soignant),
               session: Session = Depends(get_session)) -> dict:
    alerte = session.get(Alerte, alerte_id)
    if alerte is None or not voit(u, zone_alerte(alerte)):
        raise HTTPException(404, "Alerte introuvable")
    try:
        faire_transition(alerte, corps.etat, _acteur(u), corps.note, corps.transporteur, corps.structure)
    except TransitionInvalide as erreur:
        raise HTTPException(409, str(erreur)) from erreur
    tracer(session, f"alerte_{corps.etat}", u.id, f"alerte:{alerte.id}", request.client.host if request.client else None)
    session.commit()
    return _alerte(alerte, maintenant())


@router.get("/rappels")
def lister_rappels(u: Utilisateur = Depends(soignant), session: Session = Depends(get_session)) -> list[dict]:
    rappels = session.scalars(
        select(DemandeRappel)
        .where(DemandeRappel.traitee_at.is_(None))
        .order_by(DemandeRappel.prioritaire.desc(), DemandeRappel.created_at)
    ).all()
    return [
        {
            "id": r.id,
            "telephone": r.identite.telephone,
            "nom": r.identite.nom,
            "code": r.identite.femme.code if r.identite.femme else None,
            "motif": r.motif,
            "creneau": r.creneau,
            "prioritaire": r.prioritaire,
            "created_at": _iso(r.created_at),
        }
        for r in rappels
        if voit(u, zone_identite(r.identite))
    ]


@router.post("/rappels/{rappel_id}/traiter")
def traiter_rappel(rappel_id: int, request: Request, u: Utilisateur = Depends(soignant),
                   session: Session = Depends(get_session)) -> dict:
    rappel = session.get(DemandeRappel, rappel_id)
    if rappel is None or not voit(u, zone_identite(rappel.identite)):
        raise HTTPException(404, "Demande introuvable")
    rappel.traitee_at = maintenant()
    tracer(session, "rappel_traite", u.id, f"rappel:{rappel.id}", request.client.host if request.client else None)
    session.commit()
    return {"id": rappel.id, "traitee_at": _iso(rappel.traitee_at)}


def _femmes_visibles(u: Utilisateur, session: Session) -> list[Femme]:
    femmes = session.scalars(select(Femme).where(Femme.statut != "effacee").order_by(Femme.created_at.desc())).all()
    return [f for f in femmes if voit(u, f.zone)]


@router.get("/femmes")
def lister_femmes(u: Utilisateur = Depends(soignant), session: Session = Depends(get_session)) -> list[dict]:
    aujourdhui = date.today()
    resultat = []
    for f in _femmes_visibles(u, session):
        contact = prochain_contact(f)
        retards = contacts_en_retard(f, aujourdhui)
        resultat.append(
            {
                "id": f.id,
                "code": f.code,
                "nom": f.identite.nom,
                "telephone": f.identite.telephone,
                "relais": f.relais.nom if f.relais else None,
                "zone": f.zone,
                "statut": f.statut,
                "terme_prevu": _iso(terme_prevu(f)),
                "prochain_contact": (
                    {"id": contact.id, "type": contact.type, "date": _iso(contact.date_prevue)} if contact else None
                ),
                "contacts_en_retard": len(retards),
                "perdue_de_vue": bool(retards),
            }
        )
    resultat.sort(key=lambda f: (not f["perdue_de_vue"], f["prochain_contact"]["date"] if f["prochain_contact"] else "9"))
    return resultat


@router.post("/contacts/{contact_id}/realise")
def contact_realise(contact_id: int, request: Request, u: Utilisateur = Depends(soignant),
                    session: Session = Depends(get_session)) -> dict:
    contact = session.get(Contact, contact_id)
    if contact is None or not voit(u, contact.femme.zone):
        raise HTTPException(404, "Contact introuvable")
    contact.date_realisee = date.today()
    fermer_taches(session, contact.femme, maintenant(), contact)
    tracer(session, "contact_realise", u.id, f"contact:{contact.id}", request.client.host if request.client else None)
    session.commit()
    return {"id": contact.id, "date_realisee": _iso(contact.date_realisee)}


@router.get("/vaccinations")
def vaccinations(jours: int = 7, u: Utilisateur = Depends(soignant), session: Session = Depends(get_session)) -> list[dict]:
    """Séances de vaccination à faire d'ici `jours` jours, retards compris."""
    aujourdhui = date.today()
    seances = session.scalars(
        select(SeanceVaccin).where(SeanceVaccin.date_realisee.is_(None),
                                   SeanceVaccin.date_prevue <= aujourdhui + timedelta(days=jours))
        .order_by(SeanceVaccin.date_prevue)
    ).all()
    return [
        {
            "id": s.id,
            "enfant": s.enfant.code,
            "rang": s.enfant.rang,
            "mere": s.enfant.mere.identite.nom or s.enfant.mere.code,
            "telephone": s.enfant.mere.identite.telephone,
            "seance": s.nom,
            "antigenes": s.antigenes,
            "date_prevue": _iso(s.date_prevue),
            "en_retard": s.date_prevue < aujourdhui - TOLERANCE_VACCIN,
        }
        for s in seances
        if voit(u, s.enfant.mere.zone) and s.enfant.mere.statut != "effacee"
    ]


@router.post("/vaccinations/{seance_id}/fait")
def vaccination_faite(seance_id: int, request: Request, u: Utilisateur = Depends(soignant),
                      session: Session = Depends(get_session)) -> dict:
    seance = session.get(SeanceVaccin, seance_id)
    if seance is None or not voit(u, seance.enfant.mere.zone):
        raise HTTPException(404, "Séance introuvable")
    seance.date_realisee = date.today()
    fermer_taches(session, seance.enfant.mere, maintenant(), seance=seance)
    tracer(session, "vaccination_faite", u.id, f"seance:{seance.id}", request.client.host if request.client else None)
    session.commit()
    return {"id": seance.id, "date_realisee": _iso(seance.date_realisee)}


@router.get("/indicateurs")
def indicateurs(u: Utilisateur = Depends(soignant), session: Session = Depends(get_session)) -> dict:
    alertes = [a for a in session.scalars(select(Alerte)).all() if voit(u, zone_alerte(a))]
    urgences = [a for a in alertes if a.niveau == "urgence"]
    arrivees = [a for a in urgences if a.arrivee_at]
    delais = [minutes_entre(a.created_at, a.arrivee_at) for a in arrivees]
    femmes = _femmes_visibles(u, session)
    rappels = session.scalars(select(DemandeRappel).where(DemandeRappel.traitee_at.is_(None))).all()
    aujourdhui = date.today()
    depuis = maintenant() - timedelta(days=7)
    appels = [a for a in session.scalars(select(AppelSortant).where(AppelSortant.termine_at >= depuis)).all()
              if a.statut in ("repondu", "sans_reponse", "echec") and voit(u, a.femme.zone)]
    taches = [t for t in session.scalars(select(TacheRelais).where(TacheRelais.faite_at.is_(None))).all()
              if voit(u, t.femme.zone)]
    return {
        "femmes_suivies": len(femmes),
        "perdues_de_vue": sum(1 for f in femmes if contacts_en_retard(f, aujourdhui)),
        "alertes_actives": sum(1 for a in alertes if a.etat in ETATS_ACTIFS),
        "urgences_total": len(urgences),
        "part_arrivee_confirmee": round(100 * len(arrivees) / len(urgences)) if urgences else None,
        "delai_median_arrivee_min": median(delais) if delais else None,
        "rappels_en_attente": sum(1 for r in rappels if voit(u, zone_identite(r.identite))),
        "appels_7j": len(appels),
        "taux_reponse_7j": round(100 * sum(a.statut == "repondu" for a in appels) / len(appels)) if appels else None,
        "taches_relais_ouvertes": len(taches),
        **_indicateurs_enfants([e for f in femmes for e in f.enfants], aujourdhui),
    }


def _indicateurs_enfants(enfants: list[Enfant], aujourdhui: date) -> dict:
    comptes = [a for a in (penta3_a_jour(e, aujourdhui) for e in enfants) if a is not None]
    return {
        "enfants_suivis": len(enfants),
        "enfants_zero_dose": sum(zero_dose(e, aujourdhui) for e in enfants),
        "part_penta3_a_jour": round(100 * sum(comptes) / len(comptes)) if comptes else None,
    }


@router.get("/regles")
def regles(u: Utilisateur = Depends(soignant)) -> dict:
    return {"version": moteur.version, "statut": moteur.statut, "signes": moteur.signes}
