"""Parcours USSD.

Format des agrégateurs USSD courants : à chaque écran, l'agrégateur renvoie
tout le parcours de la session dans `text` (« 1*2*5 ») et attend une réponse
commençant par « CON » (écran suivant) ou « END » (fin de session).

Rien n'est écrit en base avant un écran « END » : le parcours se relit donc
toujours de la même façon pendant une session, même si la femme est nouvelle.
Le menu Urgence passe avant tout : aucun consentement ni question ne le retarde.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clinical import moteur
from app.i18n import LANGUES, t
from app.models import DemandeRappel, Femme, Identite, maintenant
from app.planning import planifier_grossesse, prochain_contact
from app.referral import ouvrir_alerte
from app.vie_privee import changer_consentement, demander_effacement, enregistrer_consentement

SIGNES_URGENCE = {
    "1": "saignement",
    "2": "convulsions",
    "3": "cephalees_vision",
    "4": "fievre_forte",
    "5": "douleur_ventre_forte",
    "6": "perte_eaux",
    "7": "bebe_ne_bouge_plus",
    "8": "nn_malade_declare",
    "9": "urgence_declaree",
}
CRENEAUX = {"1": "matin", "2": "apres_midi", "3": "soir"}
CONSEILS = {"1": "conseil_grossesse", "2": "conseil_postpartum", "3": "conseil_bebe", "4": "conseil_pf"}


@dataclass
class Parcours:
    session: Session
    identite: Identite
    langue: str | None
    aujourdhui: date

    def con(self, cle: str, **valeurs) -> str:
        return "CON " + t(cle, self.langue, **valeurs)

    def end(self, cle: str, garder_langue: bool = True, **valeurs) -> str:
        """Fin de session : c'est seulement ici que la base est modifiée.

        garder_langue=False : ne rien enregistrer de nouveau sur la femme (effacement,
        numéro inconnu) ; seules les modifications déjà faites sont validées.
        """
        if garder_langue:
            if self.langue and self.identite.langue != self.langue:
                self.identite.langue = self.langue
            self.session.add(self.identite)
        self.session.commit()
        return "END " + t(cle, self.langue, **valeurs)


def repondre(session: Session, telephone: str, text: str, aujourdhui: date | None = None) -> str:
    identite = session.scalar(select(Identite).where(Identite.telephone == telephone))
    if identite is None:
        identite = Identite(telephone=telephone)  # ajoutée à la session seulement à l'écran final

    saisies = [s.strip() for s in text.split("*")] if text else []
    p = Parcours(session, identite, identite.langue, aujourdhui or date.today())

    if identite.langue is None:
        if not saisies:
            return p.con("choix_langue")
        p.langue = LANGUES.get(saisies[0])
        if p.langue is None:
            return p.end("invalide")
        saisies = saisies[1:]

    if not saisies:
        return p.con("menu")
    choix, reste = saisies[0], saisies[1:]
    actions = {"1": urgence, "2": inscription, "3": rendez_vous, "4": rappel, "5": conseils, "6": mes_donnees,
               "0": parler_sf}
    action = actions.get(choix)
    return action(p, reste) if action else p.end("invalide")


def urgence(p: Parcours, reste: list[str]) -> str:
    if not reste:
        return p.con("urgence_menu")
    # Toute réponse dans ce menu ouvre une alerte : la femme a déclaré une urgence.
    signe = SIGNES_URGENCE.get(reste[0], "urgence_declaree")
    if signe.startswith("nn_"):
        contexte = "nouveau_ne"
    elif p.identite.femme and p.identite.femme.statut == "postpartum":
        contexte = "postpartum"
    else:
        contexte = "grossesse"
    evaluation = moteur.evaluer([signe, "urgence_declaree"], contexte)
    ouvrir_alerte(p.session, p.identite, evaluation, contexte, canal="ussd")
    return p.end("urgence_ok")


def inscription(p: Parcours, reste: list[str]) -> str:
    femme = p.identite.femme
    if femme is not None:
        contact = prochain_contact(femme)
        return p.end("deja_inscrite", date=_jour(contact.date_prevue) if contact else "-")

    if not p.identite.consentement_service:
        if not reste:
            return p.con("consentement")
        if reste[0] == "2":
            return p.end("consentement_refus")
        if reste[0] != "1":
            return p.end("invalide")
        reste = reste[1:]

    if not reste:
        return p.con("mois_grossesse")
    if not reste[0].isdigit() or not 1 <= int(reste[0]) <= 9:
        return p.end("invalide")

    enregistrer_consentement(p.identite, maintenant(), "ussd", service=True)
    femme = Femme(identite=p.identite)
    contacts = planifier_grossesse(femme, int(reste[0]), p.aujourdhui)
    p.session.add(femme)
    return p.end("inscrite", date=_jour(contacts[0].date_prevue) if contacts else "-")


def rendez_vous(p: Parcours, reste: list[str]) -> str:
    femme = p.identite.femme
    if femme is None:
        return p.end("pas_inscrite")
    contact = prochain_contact(femme)
    return p.end("rdv", date=_jour(contact.date_prevue)) if contact else p.end("aucun_rdv")


def rappel(p: Parcours, reste: list[str]) -> str:
    if not reste:
        return p.con("rappel_creneau")
    creneau = CRENEAUX.get(reste[0])
    if creneau is None:
        return p.end("invalide")
    p.session.add(DemandeRappel(identite=p.identite, motif="rappel", creneau=creneau))
    return p.end("rappel_ok")


def conseils(p: Parcours, reste: list[str]) -> str:
    if not reste:
        return p.con("conseils_menu")
    cle = CONSEILS.get(reste[0])
    return p.end(cle) if cle else p.end("invalide")


def mes_donnees(p: Parcours, reste: list[str]) -> str:
    """Effacer ses données, ou changer ses choix pour la recherche et l'IA."""
    if not reste:
        return p.con("donnees_menu")
    choix, suite = reste[0], reste[1:]
    if choix == "1":
        if not suite:
            return p.con("effacement_confirmer")
        if suite[0] != "1":
            return p.end("effacement_annule")
        if p.identite.id is None:
            return p.end("aucune_donnee", garder_langue=False)  # numéro inconnu : rien à effacer
        resultat = demander_effacement(p.session, p.identite, maintenant(), "ussd")
        return p.end("effacement_fait" if resultat == "efface" else "effacement_en_attente", garder_langue=False)
    niveau = {"2": "recherche", "3": "ia"}.get(choix)
    if niveau is None:
        return p.end("invalide")
    if not suite:
        return p.con(f"consentement_{niveau}")
    if suite[0] not in ("1", "2"):
        return p.end("invalide")
    if p.identite.id is None:
        return p.end("aucune_donnee", garder_langue=False)
    changer_consentement(p.session, p.identite, niveau, suite[0] == "1", maintenant(), "ussd")
    return p.end("choix_enregistre")


def parler_sf(p: Parcours, reste: list[str]) -> str:
    p.session.add(DemandeRappel(identite=p.identite, motif="sage_femme", prioritaire=True))
    return p.end("sf_ok")


def _jour(d: date) -> str:
    return d.strftime("%d/%m/%Y")
