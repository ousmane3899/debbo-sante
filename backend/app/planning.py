"""Calendrier de suivi personnel (« Debbo m'appelle »).

Contacts prénatals aux semaines recommandées par l'OMS (8 contacts, 2016).
L'âge de la grossesse vient de ce que la femme déclare en mois : c'est une
estimation, que la sage-femme corrige à la première consultation.
Après l'accouchement, chaque enfant reçoit le calendrier vaccinal du PEV
(`clinical/pev.json`, à valider par la région médicale).
"""

import json
from datetime import date, timedelta
from pathlib import Path

from app.models import Contact, Enfant, Femme, SeanceVaccin

SEMAINES_CPN = (12, 20, 26, 30, 34, 36, 38, 40)
JOURS_POSTNATALS = (1, 3, 7, 42)  # OMS : dans les 24 h, jour 3, jours 7-14, 6 semaines
SEMAINES_PAR_MOIS = 4.33
DELAI_RATTRAPAGE = timedelta(days=7)
TOLERANCE_RETARD = timedelta(days=7)
MAX_BEBES = 4

PEV = json.loads((Path(__file__).parent / "clinical" / "pev.json").read_text(encoding="utf-8"))
TOLERANCE_VACCIN = timedelta(days=PEV["tolerance_retard_jours"])


def debut_estime(mois: int, aujourdhui: date) -> date:
    return aujourdhui - timedelta(weeks=round(mois * SEMAINES_PAR_MOIS))


def planifier_grossesse(femme: Femme, mois: int, aujourdhui: date) -> list[Contact]:
    if not 1 <= mois <= 9:
        raise ValueError("Le mois de grossesse doit être entre 1 et 9")
    debut = debut_estime(mois, aujourdhui)
    femme.debut_grossesse = debut

    contacts = []
    for rang, semaine in enumerate(SEMAINES_CPN, start=1):
        prevue = debut + timedelta(weeks=semaine)
        if prevue >= aujourdhui:
            contacts.append(Contact(type=f"CPN{rang}", semaine=semaine, date_prevue=prevue))

    # Inscrite après la date d'un contact : on propose un rattrapage rapide,
    # sauf si le prochain contact prévu tombe de toute façon dans les 2 semaines.
    a_manque = len(contacts) < len(SEMAINES_CPN)
    prochain_proche = contacts and contacts[0].date_prevue <= aujourdhui + 2 * DELAI_RATTRAPAGE
    if a_manque and not prochain_proche:
        contacts.insert(0, Contact(type="CPN-R", date_prevue=aujourdhui + DELAI_RATTRAPAGE))

    femme.contacts.extend(contacts)
    return contacts


def enregistrer_accouchement(femme: Femme, jour: date, aujourdhui: date, bebes: int = 1) -> list[Contact]:
    """Passe la femme en post-partum : les CPN restantes sont retirées, les contacts postnatals planifiés.

    `bebes` = nombre d'enfants nés vivants ; chacun reçoit son calendrier vaccinal.
    Zéro est possible : la mère reste suivie, Debbo ne parle jamais de vaccins.
    """
    if jour > aujourdhui:
        raise ValueError("La date d'accouchement ne peut pas être dans le futur")
    if not 0 <= bebes <= MAX_BEBES:
        raise ValueError("Nombre de bébés invalide")
    if femme.statut == "postpartum":
        raise ValueError("Accouchement déjà enregistré")
    femme.statut = "postpartum"
    femme.date_accouchement = jour
    femme.contacts[:] = [c for c in femme.contacts if c.date_realisee or not c.type.startswith("CPN")]
    contacts = [
        Contact(type=f"PN{rang}", date_prevue=jour + timedelta(days=jours))
        for rang, jours in enumerate(JOURS_POSTNATALS, start=1)
        if jour + timedelta(days=jours) >= aujourdhui
    ]
    femme.contacts.extend(contacts)
    for rang in range(1, bebes + 1):
        enfant = Enfant(rang=rang, date_naissance=jour)
        planifier_vaccins(enfant)
        femme.enfants.append(enfant)
    return contacts


def planifier_vaccins(enfant: Enfant) -> list[SeanceVaccin]:
    seances = [
        SeanceVaccin(cle=s["cle"], nom=s["nom"], antigenes=s["antigenes"],
                     date_prevue=enfant.date_naissance + timedelta(days=s["age_jours"]))
        for s in PEV["seances"]
    ]
    enfant.seances.extend(seances)
    return seances


def prochaine_seance(enfant: Enfant) -> SeanceVaccin | None:
    return next((s for s in enfant.seances if s.date_realisee is None), None)


def seances_en_retard(enfant: Enfant, aujourdhui: date) -> list[SeanceVaccin]:
    return [s for s in enfant.seances if s.date_realisee is None and s.date_prevue < aujourdhui - TOLERANCE_VACCIN]


def seance(enfant: Enfant, cle: str) -> SeanceVaccin | None:
    return next((s for s in enfant.seances if s.cle == cle), None)


def zero_dose(enfant: Enfant, aujourdhui: date) -> bool:
    """Pas de Penta 1 alors que l'âge et le délai de tolérance sont dépassés."""
    penta1 = seance(enfant, PEV["seance_penta1"])
    return penta1 is not None and penta1.date_realisee is None and penta1.date_prevue < aujourdhui - TOLERANCE_VACCIN


def penta3_a_jour(enfant: Enfant, aujourdhui: date) -> bool | None:
    """None si l'enfant est trop jeune pour être compté."""
    penta3 = seance(enfant, PEV["seance_penta3"])
    if penta3 is None or penta3.date_prevue >= aujourdhui - TOLERANCE_VACCIN:
        return None
    return penta3.date_realisee is not None


def terme_prevu(femme: Femme) -> date | None:
    if femme.statut != "enceinte" or not femme.debut_grossesse:
        return None
    return femme.debut_grossesse + timedelta(weeks=40)


def prochain_contact(femme: Femme) -> Contact | None:
    return next((c for c in femme.contacts if c.date_realisee is None), None)


def contacts_en_retard(femme: Femme, aujourdhui: date) -> list[Contact]:
    return [
        c
        for c in femme.contacts
        if c.date_realisee is None and c.date_prevue < aujourdhui - TOLERANCE_RETARD
    ]
