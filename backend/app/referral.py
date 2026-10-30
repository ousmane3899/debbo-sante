"""Boucle de référence fermée (cahier des charges, §6.4).

Chaque alerte suit : ouverte -> sage-femme jointe -> transport parti ->
arrivée confirmée -> clôturée. Chaque étape est horodatée et journalisée ;
le transporteur n'est payé qu'à l'arrivée confirmée par la structure.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.clinical import Evaluation
from app.models import Alerte, Evenement, Identite, maintenant

TRANSITIONS: dict[str, set[str]] = {
    "ouverte": {"sf_jointe", "arrivee_confirmee", "cloturee"},
    "sf_jointe": {"transport_parti", "arrivee_confirmee", "cloturee"},
    "transport_parti": {"arrivee_confirmee", "cloturee"},
    "arrivee_confirmee": {"cloturee"},
    "cloturee": set(),
}
HORODATAGE = {
    "sf_jointe": "sf_jointe_at",
    "transport_parti": "transport_at",
    "arrivee_confirmee": "arrivee_at",
    "cloturee": "cloturee_at",
}
ETATS_ACTIFS = ("ouverte", "sf_jointe", "transport_parti")
ESCALADE_APRES = timedelta(minutes=5)


class TransitionInvalide(ValueError):
    pass


def ouvrir_alerte(
    session: Session, identite: Identite, evaluation: Evaluation, contexte: str, canal: str
) -> Alerte:
    alerte = Alerte(
        identite=identite,
        canal=canal,
        contexte=contexte,
        signes=list(evaluation.signes_retenus + evaluation.signes_inconnus),
        niveau=evaluation.niveau,
        regles_version=evaluation.version,
        etat="ouverte",
        created_at=maintenant(),
    )
    alerte.evenements.append(Evenement(type="alerte_ouverte", acteur="systeme", note=evaluation.niveau))
    # Branchement prévu : appel ou SMS automatique à la sage-femme d'astreinte.
    alerte.evenements.append(Evenement(type="sf_astreinte_notifiee", acteur="systeme"))
    session.add(alerte)
    return alerte


def faire_transition(
    alerte: Alerte,
    etat: str,
    acteur: str,
    note: str | None = None,
    transporteur: str | None = None,
    structure: str | None = None,
) -> Alerte:
    if etat not in TRANSITIONS.get(alerte.etat, set()):
        raise TransitionInvalide(f"Passage impossible de « {alerte.etat} » à « {etat} »")
    if etat == "cloturee" and alerte.arrivee_at is None and not note:
        raise TransitionInvalide("Une alerte clôturée sans arrivée confirmée doit avoir un motif")
    if etat == "transport_parti" and not (transporteur or alerte.transporteur):
        raise TransitionInvalide("Indiquer le transporteur")

    if transporteur:
        alerte.transporteur = transporteur
    if structure:
        alerte.structure = structure
    alerte.etat = etat
    setattr(alerte, HORODATAGE[etat], maintenant())
    alerte.evenements.append(Evenement(type=etat, acteur=acteur, note=note))

    if etat == "arrivee_confirmee" and alerte.transport_at is not None:
        # Branchement prévu : paiement mobile money du transporteur.
        alerte.evenements.append(
            Evenement(type="paiement_transport_declenche", acteur="systeme", note=alerte.transporteur)
        )
    return alerte


def doit_escalader(alerte: Alerte, a: datetime) -> bool:
    return alerte.etat == "ouverte" and a - alerte.created_at > ESCALADE_APRES


def minutes_entre(debut: datetime | None, fin: datetime | None) -> float | None:
    if debut is None or fin is None:
        return None
    return round((fin - debut).total_seconds() / 60, 1)
