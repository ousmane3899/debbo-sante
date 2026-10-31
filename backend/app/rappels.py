"""« Debbo m'appelle » : le service va vers les femmes (cahier des charges, §6.1).

Trois règles :
1. La veille de chaque consultation, Debbo appelle la femme. Sans téléphone,
   c'est la relais qui reçoit une visite à faire.
2. Deux appels sans réponse : la relais reçoit une visite à faire (« injoignable »).
3. Consultation manquée depuis plus de 7 jours : visite de la relais et appel de relance.

`planifier` crée les appels et les tâches ; `executer` lance les appels arrivés à
échéance ; `enregistrer_resultat` traite la réponse du fournisseur de téléphonie.
Chaque fonction est idempotente : la relancer ne crée pas de doublon.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppelSortant, Contact, DemandeRappel, Femme, TacheRelais, maintenant
from app.planning import contacts_en_retard
from app.telephonie import Fournisseur

HEURE_APPEL = time(10, 0)  # heure locale ; le Sénégal est à UTC toute l'année
PLAGE_APPELS = (time(8, 0), time(20, 0))  # jamais d'appel la nuit
DELAI_NOUVEL_ESSAI = timedelta(hours=3)
MAX_TENTATIVES = 2
TOUCHE_RAPPEL_SF = "2"


def dans_la_plage(a: datetime) -> bool:
    return PLAGE_APPELS[0] <= a.time() < PLAGE_APPELS[1]


def heure_d_appel(souhaitee: datetime) -> datetime:
    """Ramène une heure d'appel dans la plage de jour : la nuit, on attend 10 h."""
    if dans_la_plage(souhaitee):
        return souhaitee
    jour = souhaitee.date() if souhaitee.time() < PLAGE_APPELS[0] else souhaitee.date() + timedelta(days=1)
    return datetime.combine(jour, HEURE_APPEL)


def _prochain_a_venir(femme: Femme, aujourdhui: date) -> Contact | None:
    return next((c for c in femme.contacts if c.date_realisee is None and c.date_prevue >= aujourdhui), None)


def _appel_existe(session: Session, contact: Contact, motif: str) -> bool:
    return session.scalar(
        select(AppelSortant.id).where(AppelSortant.contact_id == contact.id, AppelSortant.motif == motif).limit(1)
    ) is not None


def _tache_ouverte(session: Session, femme: Femme, motif: str, contact: Contact | None = None) -> bool:
    requete = select(TacheRelais.id).where(
        TacheRelais.femme_id == femme.id, TacheRelais.motif == motif, TacheRelais.faite_at.is_(None)
    )
    if contact is not None:
        requete = requete.where(TacheRelais.contact_id == contact.id)
    return session.scalar(requete.limit(1)) is not None


def _tache_creee(session: Session, femme: Femme, contact: Contact | None, motif: str) -> bool:
    """Une tâche par (femme, consultation, motif), même une fois faite."""
    requete = select(TacheRelais.id).where(TacheRelais.femme_id == femme.id, TacheRelais.motif == motif)
    if contact is not None:
        requete = requete.where(TacheRelais.contact_id == contact.id)
    return session.scalar(requete.limit(1)) is not None


def planifier(session: Session, aujourdhui: date) -> dict:
    cree = {"appels": 0, "taches": 0}
    a_l_heure = datetime.combine(aujourdhui, HEURE_APPEL)
    femmes = session.scalars(select(Femme)).all()
    for f in femmes:
        if not f.identite.consentement_service:
            continue
        telephone = f.identite.telephone

        contact = _prochain_a_venir(f, aujourdhui)
        if contact and contact.date_prevue == aujourdhui + timedelta(days=1):
            if telephone and not _appel_existe(session, contact, "veille_contact"):
                session.add(AppelSortant(femme=f, contact=contact, motif="veille_contact", prevu_at=a_l_heure))
                cree["appels"] += 1
            elif not telephone and not _tache_creee(session, f, contact, "rappel_sans_telephone"):
                session.add(TacheRelais(femme=f, contact=contact, motif="rappel_sans_telephone"))
                cree["taches"] += 1

        # Plusieurs consultations manquées : une seule relance et une seule visite, sur la plus récente.
        manquees = contacts_en_retard(f, aujourdhui)
        if manquees and not _tache_ouverte(session, f, "consultation_manquee"):
            derniere = manquees[-1]
            if not _tache_creee(session, f, derniere, "consultation_manquee"):
                session.add(TacheRelais(femme=f, contact=derniere, motif="consultation_manquee"))
                cree["taches"] += 1
                if telephone:
                    session.add(AppelSortant(femme=f, contact=derniere, motif="relance", prevu_at=a_l_heure))
                    cree["appels"] += 1
    session.flush()
    return cree


def executer(session: Session, a: datetime, fournisseur: Fournisseur) -> int:
    if not dans_la_plage(a):
        return 0  # même en retard (serveur arrêté), aucun appel ne part la nuit
    appels = session.scalars(
        select(AppelSortant).where(AppelSortant.statut == "prevu", AppelSortant.prevu_at <= a)
    ).all()
    lances = 0
    for appel in appels:
        if appel.contact is not None and appel.contact.date_realisee is not None:
            appel.statut, appel.termine_at = "annule", a  # elle est déjà venue
            continue
        identite = appel.femme.identite
        appel.reference = fournisseur.appeler(identite.telephone, f"appel_{appel.motif}", identite.langue)
        appel.statut = "en_cours"
        lances += 1
    session.flush()
    return lances


def enregistrer_resultat(session: Session, reference: str, statut: str, touche: str | None, a: datetime) -> AppelSortant:
    if statut not in ("repondu", "sans_reponse", "echec"):
        raise ValueError(f"Statut inconnu : {statut}")
    appel = session.scalar(select(AppelSortant).where(AppelSortant.reference == reference))
    if appel is None:
        raise KeyError(reference)
    if appel.statut != "en_cours":
        return appel  # résultat déjà reçu : le fournisseur peut renvoyer la même notification
    appel.statut, appel.touche, appel.termine_at = statut, touche, a
    femme = appel.femme

    if statut == "repondu":
        if touche == TOUCHE_RAPPEL_SF:
            session.add(DemandeRappel(identite=femme.identite, motif="rappel_debbo"))
    elif appel.tentative < MAX_TENTATIVES:
        session.add(AppelSortant(femme=femme, contact=appel.contact, motif=appel.motif, tentative=appel.tentative + 1,
                                 prevu_at=heure_d_appel(a + DELAI_NOUVEL_ESSAI)))
    elif not _tache_ouverte(session, femme, "injoignable"):
        session.add(TacheRelais(femme=femme, contact=appel.contact, motif="injoignable"))
    session.flush()
    return appel


def fermer_taches(session: Session, femme: Femme, a: datetime, contact: Contact | None = None) -> int:
    """Une visite de la relais (ou la venue de la femme) clôt les tâches ouvertes."""
    requete = select(TacheRelais).where(TacheRelais.femme_id == femme.id, TacheRelais.faite_at.is_(None))
    if contact is not None:
        requete = requete.where(TacheRelais.contact_id == contact.id)
    taches = session.scalars(requete).all()
    for tache in taches:
        tache.faite_at = a
    return len(taches)


def taches_ouvertes(session: Session, femme: Femme) -> list[TacheRelais]:
    return session.scalars(
        select(TacheRelais).where(TacheRelais.femme_id == femme.id, TacheRelais.faite_at.is_(None))
        .order_by(TacheRelais.created_at)
    ).all()


def tour(session: Session, a: datetime | None = None, fournisseur: Fournisseur | None = None) -> dict:
    """Un passage complet du planificateur (lancé chaque minute par le serveur)."""
    from app.telephonie import fournisseur_par_defaut

    a = a or maintenant()
    bilan = planifier(session, a.date())
    bilan["lances"] = executer(session, a, fournisseur or fournisseur_par_defaut())
    session.commit()
    return bilan
