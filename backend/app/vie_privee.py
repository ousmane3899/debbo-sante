"""Consentement et droit à l'effacement (cahier des charges, §8 ; loi sénégalaise n° 2008-12).

Consentement en trois niveaux séparés : service, recherche, amélioration de l'IA.
Refuser la recherche ou l'IA ne change rien au suivi.

Effacement (« Debbo, oublie-moi ») : le nom, le téléphone, la langue et les notes
libres sont supprimés ; le suivi s'arrête. Les événements de santé restent, sans
aucune identité, pour les statistiques de la région (ils ne permettent plus de
retrouver la femme). Pendant une alerte en cours, l'effacement attend la clôture :
une urgence ne doit jamais disparaître de l'écran d'une sage-femme. Les rappels,
eux, s'arrêtent tout de suite.
"""

import hashlib
import hmac
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.journal import tracer
from app.models import Alerte, AppelSortant, DemandeRappel, Identite, TacheRelais
from app.referral import ETATS_ACTIFS


def enregistrer_consentement(identite: Identite, a: datetime, par: str, service: bool,
                             recherche: bool = False, ia: bool = False) -> None:
    identite.consentement_service = service
    identite.consentement_recherche = recherche
    identite.consentement_ia = ia
    identite.consentement_at = a
    identite.consentement_par = par


def changer_consentement(session: Session, identite: Identite, niveau: str, accepte: bool, a: datetime,
                         par: str) -> None:
    if niveau not in ("recherche", "ia"):
        raise ValueError(f"Niveau de consentement inconnu : {niveau}")
    setattr(identite, f"consentement_{niveau}", accepte)
    identite.consentement_at, identite.consentement_par = a, par
    tracer(session, f"consentement_{niveau}_{'oui' if accepte else 'non'}", None, _cible(identite), par)


def _cible(identite: Identite) -> str:
    return identite.femme.code if identite.femme else f"identite:{identite.id}"


def alerte_en_cours(session: Session, identite: Identite) -> bool:
    return session.scalar(
        select(Alerte.id).where(Alerte.identite_id == identite.id, Alerte.etat.in_(ETATS_ACTIFS)).limit(1)
    ) is not None


def demander_effacement(session: Session, identite: Identite, a: datetime, par: str) -> str:
    """Renvoie « efface » ou « en_attente » (alerte en cours)."""
    if identite.efface_at is not None:
        return "efface"
    if identite.effacement_demande_at is None:
        identite.effacement_demande_at = a
        tracer(session, "effacement_demande", None, _cible(identite), par)
    _arreter_le_suivi(session, identite, a)
    if alerte_en_cours(session, identite):
        return "en_attente"
    effacer(session, identite, a)
    return "efface"


def _arreter_le_suivi(session: Session, identite: Identite, a: datetime) -> None:
    identite.consentement_service = identite.consentement_recherche = identite.consentement_ia = False
    femme = identite.femme
    if femme is None:
        return
    for appel in session.scalars(select(AppelSortant).where(
            AppelSortant.femme_id == femme.id, AppelSortant.statut.in_(("prevu", "en_cours")))):
        appel.statut, appel.termine_at = "annule", a
    for tache in session.scalars(select(TacheRelais).where(
            TacheRelais.femme_id == femme.id, TacheRelais.faite_at.is_(None))):
        tache.faite_at = a


def effacer(session: Session, identite: Identite, a: datetime) -> None:
    cible = _cible(identite)
    identite.nom = identite.telephone = identite.langue = None
    identite.efface_at = a
    for demande in session.scalars(select(DemandeRappel).where(
            DemandeRappel.identite_id == identite.id, DemandeRappel.traitee_at.is_(None))):
        demande.traitee_at = a
    # Les notes libres (soignants, relais) peuvent contenir un nom : elles partent aussi.
    for alerte in session.scalars(select(Alerte).where(Alerte.identite_id == identite.id)):
        for evenement in alerte.evenements:
            evenement.note = None
    femme = identite.femme
    if femme is not None:
        femme.statut = "effacee"
        femme.client_uid = None
        femme.relais_id = None
        for visite in femme.visites:
            visite.note = None
    tracer(session, "effacement_effectue", None, cible, "systeme")


def traiter_effacements_en_attente(session: Session, a: datetime) -> int:
    """Exécute les effacements qui attendaient la clôture d'une alerte (lancé chaque minute)."""
    en_attente = session.scalars(select(Identite).where(
        Identite.effacement_demande_at.is_not(None), Identite.efface_at.is_(None))).all()
    faits = 0
    for identite in en_attente:
        if not alerte_en_cours(session, identite):
            effacer(session, identite, a)
            faits += 1
    session.commit()
    return faits


def pseudonyme_recherche(femme_id: int) -> str:
    """Identifiant stable pour les jeux de données de recherche, sans lien avec le code utilisé au poste."""
    mac = hmac.new(config.SECRET_PSEUDONYMES.encode(), f"femme:{femme_id}".encode(), hashlib.sha256)
    return "R-" + mac.hexdigest()[:12].upper()
