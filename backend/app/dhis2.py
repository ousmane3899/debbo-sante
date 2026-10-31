"""Export mensuel vers DHIS2, le système national d'information sanitaire (cahier des charges, principe 7).

Debbo n'envoie que des **totaux par zone et par mois** (jamais de dossier individuel),
au format `dataValueSet` de l'API DHIS2 (`POST /api/dataValueSets`, période « AAAAMM »).

La correspondance entre les indicateurs de Debbo, les zones et les identifiants
DHIS2 (UID) vit dans `dhis2_correspondances.json`, à compléter avec le ministère.
Une valeur dont l'élément ou la zone n'est pas encore relié n'est jamais envoyée :
mieux vaut un trou signalé qu'un chiffre rangé au mauvais endroit.

    python -m app.dhis2 apercu --periode 2026-10
    python -m app.dhis2 envoyer --periode 2026-10
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.auth import exiger
from app.db import get_session
from app.journal import tracer
from app.models import (
    Alerte, AppelSortant, Contact, Enfant, EnvoiDhis2, Femme, SeanceVaccin, Utilisateur, Visite, maintenant,
)
from app.planning import PEV, TOLERANCE_VACCIN

CORRESPONDANCES = Path(__file__).with_name("dhis2_correspondances.json")
A_RENSEIGNER = "A_RENSEIGNER"
SANS_ZONE = "(sans zone)"


class ErreurDhis2(RuntimeError):
    pass


def lire_correspondances(chemin: Path | None = None) -> dict:
    return json.loads((chemin or CORRESPONDANCES).read_text(encoding="utf-8"))


def mappe(uid: str | None) -> bool:
    return bool(uid) and uid != A_RENSEIGNER


# ---------- période ----------

def lire_periode(texte: str, aujourdhui: date | None = None) -> tuple[date, date]:
    """« 2026-10 » -> (1er octobre, 31 octobre). Refuse un mois futur."""
    trouve = re.fullmatch(r"(\d{4})-(\d{2})", texte or "")
    if not trouve or not 1 <= int(trouve[2]) <= 12:
        raise ValueError("Période attendue au format AAAA-MM")
    debut = date(int(trouve[1]), int(trouve[2]), 1)
    fin = (debut.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if debut > (aujourdhui or date.today()):
        raise ValueError("On ne peut pas déclarer un mois futur")
    return debut, fin


def periode_dhis2(debut: date) -> str:
    return debut.strftime("%Y%m")


def _dans(valeur: date | datetime | None, debut: date, fin: date) -> bool:
    if valeur is None:
        return False
    jour = valeur.date() if isinstance(valeur, datetime) else valeur
    return debut <= jour <= fin


# ---------- indicateurs ----------

def indicateurs(session: Session, debut: date, fin: date) -> dict[str, dict[str, float]]:
    """Totaux du mois par zone. Les dossiers effacés comptent : leurs événements restent, sans identité."""
    totaux: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(int))

    def ajouter(zone: str | None, cle: str, n: float = 1) -> None:
        totaux[zone or SANS_ZONE][cle] += n

    for f in session.scalars(select(Femme)):
        if _dans(f.created_at, debut, fin):
            ajouter(f.zone, "femmes_inscrites")
        if _dans(f.date_accouchement, debut, fin):
            ajouter(f.zone, "accouchements")
    for c in session.scalars(select(Contact).where(Contact.date_realisee.between(debut, fin))):
        ajouter(c.femme.zone, "cpn_realisees" if c.type.startswith("CPN") else "pn_realisees")
    for e in session.scalars(select(Enfant).where(Enfant.date_naissance.between(debut, fin))):
        ajouter(e.mere.zone, "naissances_vivantes")

    vaccins = {PEV["seance_penta1"]: "vaccins_penta1", PEV["seance_penta3"]: "vaccins_penta3", "9mois": "vaccins_rr1"}
    for s in session.scalars(select(SeanceVaccin).where(SeanceVaccin.date_realisee.between(debut, fin))):
        if s.cle in vaccins:
            ajouter(s.enfant.mere.zone, vaccins[s.cle])

    # Zéro dose à la fin du mois : Penta 1 dû (avec la tolérance) et pas encore fait à cette date.
    for s in session.scalars(select(SeanceVaccin).where(SeanceVaccin.cle == PEV["seance_penta1"])):
        if s.date_prevue < fin - TOLERANCE_VACCIN and (s.date_realisee is None or s.date_realisee > fin):
            ajouter(s.enfant.mere.zone, "enfants_zero_dose")

    for a in session.scalars(select(Alerte).where(Alerte.niveau == "urgence")):
        if _dans(a.created_at, debut, fin):
            zone = a.relais.zone if a.relais else (a.identite.femme.zone if a.identite.femme else None)
            ajouter(zone, "alertes_urgence")
            if a.arrivee_at is not None:
                ajouter(zone, "urgences_arrivees")

    for v in session.scalars(select(Visite)):
        if _dans(v.date, debut, fin):
            ajouter(v.femme.zone, "visites_relais")

    # Utile au pilotage de Debbo, pas destiné à DHIS2 (pas d'élément de données prévu).
    for appel in session.scalars(select(AppelSortant).where(AppelSortant.statut.in_(("repondu", "sans_reponse", "echec")))):
        if _dans(appel.termine_at, debut, fin):
            ajouter(appel.femme.zone, "appels_debbo")
            if appel.statut == "repondu":
                ajouter(appel.femme.zone, "appels_debbo_decroches")

    return {zone: dict(valeurs) for zone, valeurs in totaux.items()}


# ---------- dataValueSet ----------

def construire(totaux: dict[str, dict[str, float]], debut: date, correspondances: dict) -> dict:
    """Prépare l'envoi et liste ce qui manque. Les valeurs non reliées ne sont pas envoyées."""
    elements = correspondances["elements"]
    zones = correspondances["zones"]
    valeurs, manques = [], []
    for zone, indicateurs_zone in sorted(totaux.items()):
        org_unit = zones.get(zone)
        if not mappe(org_unit):
            manques.append({"type": "zone", "zone": zone, "valeurs_non_envoyees": len(indicateurs_zone)})
            continue
        for cle, valeur in sorted(indicateurs_zone.items()):
            element = elements.get(cle)
            if element is None:
                continue  # indicateur interne à Debbo, pas prévu pour DHIS2
            if not mappe(element.get("dataElement")):
                manques.append({"type": "element", "cle": cle, "libelle": element["libelle"], "zone": zone})
                continue
            valeur_dhis2 = {"dataElement": element["dataElement"], "period": periode_dhis2(debut),
                            "orgUnit": org_unit, "value": str(int(valeur) if float(valeur).is_integer() else valeur)}
            if element.get("categoryOptionCombo"):
                valeur_dhis2["categoryOptionCombo"] = element["categoryOptionCombo"]
            valeurs.append(valeur_dhis2)
    ensemble = {"dataValues": valeurs}
    if mappe(correspondances.get("dataSet")):
        ensemble["dataSet"] = correspondances["dataSet"]
    return {"dataValueSet": ensemble, "manques": manques}


# ---------- envoi ----------

def _resume(reponse: dict) -> dict:
    """DHIS2 2.38+ enveloppe le résultat dans « response » ; les versions plus anciennes non."""
    corps = reponse.get("response", reponse)
    return {
        "statut": corps.get("status") or reponse.get("status"),
        "compte": corps.get("importCount", {}),
        "conflits": [c.get("value") or c.get("object") for c in corps.get("conflicts", [])][:20],
    }


def envoyer(ensemble: dict, url: str | None = None, jeton: str | None = None,
            transport: httpx.BaseTransport | None = None) -> dict:
    url, jeton = url or config.DHIS2_URL, jeton or config.DHIS2_JETON
    if not url or not jeton:
        raise ErreurDhis2("DHIS2 n'est pas configuré (DEBBO_DHIS2_URL et DEBBO_DHIS2_JETON)")
    if config.EN_PROD and not url.startswith("https://"):
        raise ErreurDhis2("En production, DHIS2 doit être joint en HTTPS")
    if not ensemble["dataValues"]:
        raise ErreurDhis2("Aucune valeur reliée à DHIS2 : complétez dhis2_correspondances.json")
    with httpx.Client(transport=transport, timeout=60) as client:
        try:
            reponse = client.post(f"{url.rstrip('/')}/api/dataValueSets", json=ensemble,
                                  headers={"Authorization": f"ApiToken {jeton}", "Accept": "application/json"})
        except httpx.HTTPError as erreur:
            raise ErreurDhis2(f"DHIS2 injoignable : {erreur.__class__.__name__}") from erreur
    if reponse.status_code in (401, 403):
        raise ErreurDhis2("DHIS2 refuse le jeton d'accès")
    try:
        resume = _resume(reponse.json())
    except ValueError as erreur:
        raise ErreurDhis2(f"Réponse DHIS2 illisible (HTTP {reponse.status_code})") from erreur
    if reponse.status_code >= 400 or resume["statut"] in ("ERROR", None):
        raise ErreurDhis2(f"DHIS2 a refusé l'envoi : {resume['conflits'] or resume['statut']}")
    return resume


def rapport(session: Session, periode: str) -> dict:
    debut, fin = lire_periode(periode)
    totaux = indicateurs(session, debut, fin)
    return {"periode": periode, "totaux": totaux, **construire(totaux, debut, lire_correspondances())}


def envoyer_periode(session: Session, periode: str, lance_par: int | None,
                    transport: httpx.BaseTransport | None = None) -> EnvoiDhis2:
    """Envoie le mois et garde la trace de l'essai, réussi ou non. Renvoyer un mois met à jour ses valeurs."""
    preparation = rapport(session, periode)
    ensemble = preparation["dataValueSet"]
    envoi = EnvoiDhis2(periode=periode, lance_par=lance_par, at=maintenant(), nb_valeurs=len(ensemble["dataValues"]))
    try:
        envoi.resume = envoyer(ensemble, transport=transport)
        envoi.statut = "ok"
    except ErreurDhis2 as erreur:
        envoi.resume, envoi.statut = {"erreur": str(erreur)}, "erreur"
    session.add(envoi)
    session.commit()
    return envoi


# ---------- API (équipe de district) ----------

router = APIRouter(prefix="/api/dhis2")


class EnvoiIn(BaseModel):
    periode: str


def _envoi(e: EnvoiDhis2) -> dict:
    return {"periode": e.periode, "at": e.at.isoformat() + "Z", "statut": e.statut, "nb_valeurs": e.nb_valeurs,
            "resume": e.resume}


@router.get("/apercu")
def apercu(periode: str, u: Utilisateur = Depends(exiger("district")), session: Session = Depends(get_session)) -> dict:
    try:
        preparation = rapport(session, periode)
    except ValueError as erreur:
        raise HTTPException(422, str(erreur)) from erreur
    correspondances = lire_correspondances()
    envois = session.scalars(select(EnvoiDhis2).order_by(EnvoiDhis2.at.desc()).limit(10)).all()
    return {
        "periode": periode,
        "configure": bool(config.DHIS2_URL and config.DHIS2_JETON),
        "elements": {cle: {"libelle": e["libelle"], "relie": mappe(e.get("dataElement"))}
                     for cle, e in correspondances["elements"].items()},
        "zones": {zone: mappe(correspondances["zones"].get(zone)) for zone in preparation["totaux"]},
        "totaux": preparation["totaux"],
        "valeurs_pretes": len(preparation["dataValueSet"]["dataValues"]),
        "manques": preparation["manques"],
        "envois": [_envoi(e) for e in envois],
    }


@router.post("/envoyer")
def envoyer_mois(corps: EnvoiIn, request: Request, u: Utilisateur = Depends(exiger("district")),
                 session: Session = Depends(get_session)) -> dict:
    try:
        lire_periode(corps.periode)
    except ValueError as erreur:
        raise HTTPException(422, str(erreur)) from erreur
    envoi = envoyer_periode(session, corps.periode, lance_par=u.id)
    tracer(session, f"dhis2_envoi_{envoi.statut}", u.id, corps.periode, request.client.host if request.client else None)
    session.commit()
    return _envoi(envoi)


# ---------- ligne de commande (tâche mensuelle) ----------

def main() -> None:
    from app.db import SessionLocal, init_db

    parseur = argparse.ArgumentParser(prog="python -m app.dhis2")
    parseur.add_argument("action", choices=["apercu", "envoyer"])
    parseur.add_argument("--periode", required=True, help="AAAA-MM")
    args = parseur.parse_args()
    init_db(migrer_automatiquement=not config.EN_PROD)
    with SessionLocal() as session:
        if args.action == "apercu":
            print(json.dumps(rapport(session, args.periode), ensure_ascii=False, indent=2))
            return
        envoi = envoyer_periode(session, args.periode, lance_par=None)
        print(json.dumps({"statut": envoi.statut, "valeurs": envoi.nb_valeurs, **envoi.resume}, ensure_ascii=False))
        sys.exit(0 if envoi.statut == "ok" else 1)


if __name__ == "__main__":
    main()
