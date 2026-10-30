"""Calendrier de suivi personnel (« Debbo m'appelle »).

Contacts prénatals aux semaines recommandées par l'OMS (8 contacts, 2016).
L'âge de la grossesse vient de ce que la femme déclare en mois : c'est une
estimation, que la sage-femme corrige à la première consultation.
"""

from datetime import date, timedelta

from app.models import Contact, Femme

SEMAINES_CPN = (12, 20, 26, 30, 34, 36, 38, 40)
JOURS_POSTNATALS = (1, 3, 7, 42)  # OMS : dans les 24 h, jour 3, jours 7-14, 6 semaines
SEMAINES_PAR_MOIS = 4.33
DELAI_RATTRAPAGE = timedelta(days=7)
TOLERANCE_RETARD = timedelta(days=7)


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


def enregistrer_accouchement(femme: Femme, jour: date, aujourdhui: date) -> list[Contact]:
    """Passe la femme en post-partum : les CPN restantes sont retirées, les contacts postnatals planifiés."""
    if jour > aujourdhui:
        raise ValueError("La date d'accouchement ne peut pas être dans le futur")
    femme.statut = "postpartum"
    femme.date_accouchement = jour
    femme.contacts[:] = [c for c in femme.contacts if c.date_realisee or not c.type.startswith("CPN")]
    contacts = [
        Contact(type=f"PN{rang}", date_prevue=jour + timedelta(days=jours))
        for rang, jours in enumerate(JOURS_POSTNATALS, start=1)
        if jour + timedelta(days=jours) >= aujourdhui
    ]
    femme.contacts.extend(contacts)
    return contacts


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
