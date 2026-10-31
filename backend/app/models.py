"""Modèle de données.

Principe du cahier des charges (section 8) : l'identité (numéro de téléphone)
vit dans `Identite`, séparée des données de santé. Les autres tables ne
connaissent la femme que par son pseudonyme `Femme.code`.
"""

import secrets
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def maintenant() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def nouveau_code() -> str:
    return "DB-" + secrets.token_hex(3).upper()


class Identite(Base):
    """Coffre d'identité : seul endroit où figurent le nom et le numéro de téléphone.

    Une femme inscrite par une relais peut ne pas avoir de téléphone.
    """

    __tablename__ = "identite"

    id: Mapped[int] = mapped_column(primary_key=True)
    telephone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    nom: Mapped[str | None] = mapped_column(String(128))
    langue: Mapped[str | None] = mapped_column(String(8))
    consentement_service: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)

    femme: Mapped["Femme | None"] = relationship(back_populates="identite", uselist=False)


class Relais(Base):
    """Relais communautaire ou bajenu gox, rattachée à une zone."""

    __tablename__ = "relais"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    nom: Mapped[str] = mapped_column(String(128))
    zone: Mapped[str] = mapped_column(String(64), index=True)
    telephone: Mapped[str | None] = mapped_column(String(32))  # pour que la sage-femme rappelle la relais
    telephone_sf: Mapped[str | None] = mapped_column(String(32))  # sage-femme d'astreinte à appeler


class Femme(Base):
    __tablename__ = "femme"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, default=nouveau_code)
    client_uid: Mapped[str | None] = mapped_column(String(64), unique=True)  # créée hors-ligne par une relais
    identite_id: Mapped[int] = mapped_column(ForeignKey("identite.id"), unique=True)
    relais_id: Mapped[int | None] = mapped_column(ForeignKey("relais.id"), index=True)
    statut: Mapped[str] = mapped_column(String(16), default="enceinte")  # enceinte | postpartum
    debut_grossesse: Mapped[date | None] = mapped_column(Date)
    date_accouchement: Mapped[date | None] = mapped_column(Date)
    zone: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)

    identite: Mapped[Identite] = relationship(back_populates="femme")
    relais: Mapped[Relais | None] = relationship()
    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="femme", order_by="Contact.date_prevue", cascade="all, delete-orphan"
    )
    visites: Mapped[list["Visite"]] = relationship(
        back_populates="femme", order_by="Visite.date", cascade="all, delete-orphan"
    )


class Contact(Base):
    """Contact de suivi planifié (consultation prénatale, postnatale, vaccin)."""

    __tablename__ = "contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    femme_id: Mapped[int] = mapped_column(ForeignKey("femme.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    semaine: Mapped[int | None] = mapped_column(Integer)
    date_prevue: Mapped[date] = mapped_column(Date)
    date_realisee: Mapped[date | None] = mapped_column(Date)

    femme: Mapped[Femme] = relationship(back_populates="contacts")


class Alerte(Base):
    """Un dossier de la boucle de référence, ouvert par un signe de danger."""

    __tablename__ = "alerte"

    id: Mapped[int] = mapped_column(primary_key=True)
    identite_id: Mapped[int] = mapped_column(ForeignKey("identite.id"), index=True)
    relais_id: Mapped[int | None] = mapped_column(ForeignKey("relais.id"))
    canal: Mapped[str] = mapped_column(String(16), default="ussd")
    contexte: Mapped[str] = mapped_column(String(16))
    signes: Mapped[list[str]] = mapped_column(JSON, default=list)
    niveau: Mapped[str] = mapped_column(String(24))
    regles_version: Mapped[str] = mapped_column(String(32))
    etat: Mapped[str] = mapped_column(String(24), default="ouverte", index=True)
    transporteur: Mapped[str | None] = mapped_column(String(64))
    structure: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)
    sf_jointe_at: Mapped[datetime | None] = mapped_column(DateTime)
    transport_at: Mapped[datetime | None] = mapped_column(DateTime)
    arrivee_at: Mapped[datetime | None] = mapped_column(DateTime)
    cloturee_at: Mapped[datetime | None] = mapped_column(DateTime)

    identite: Mapped[Identite] = relationship()
    relais: Mapped[Relais | None] = relationship()
    evenements: Mapped[list["Evenement"]] = relationship(
        back_populates="alerte", order_by="Evenement.at", cascade="all, delete-orphan"
    )


class Evenement(Base):
    """Journal de chaque étape d'une alerte (qui, quoi, quand)."""

    __tablename__ = "evenement"

    id: Mapped[int] = mapped_column(primary_key=True)
    alerte_id: Mapped[int] = mapped_column(ForeignKey("alerte.id"), index=True)
    type: Mapped[str] = mapped_column(String(48))
    acteur: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)

    alerte: Mapped[Alerte] = relationship(back_populates="evenements")


class DemandeRappel(Base):
    __tablename__ = "demande_rappel"

    id: Mapped[int] = mapped_column(primary_key=True)
    identite_id: Mapped[int] = mapped_column(ForeignKey("identite.id"), index=True)
    motif: Mapped[str] = mapped_column(String(32))
    creneau: Mapped[str | None] = mapped_column(String(16))
    prioritaire: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)
    traitee_at: Mapped[datetime | None] = mapped_column(DateTime)

    identite: Mapped[Identite] = relationship()


class Visite(Base):
    """Visite à domicile faite par une relais, évaluée par le moteur clinique."""

    __tablename__ = "visite"

    id: Mapped[int] = mapped_column(primary_key=True)
    femme_id: Mapped[int] = mapped_column(ForeignKey("femme.id"), index=True)
    relais_id: Mapped[int] = mapped_column(ForeignKey("relais.id"))
    date: Mapped[datetime] = mapped_column(DateTime)
    contexte: Mapped[str] = mapped_column(String(16))
    signes: Mapped[list[str]] = mapped_column(JSON, default=list)
    niveau: Mapped[str] = mapped_column(String(24))
    pb_mm: Mapped[int | None] = mapped_column(Integer)  # périmètre brachial de l'enfant
    note: Mapped[str | None] = mapped_column(Text)

    femme: Mapped[Femme] = relationship(back_populates="visites")


class OperationSync(Base):
    """Opération reçue de l'appli relais : garantit qu'un renvoi n'est appliqué qu'une fois."""

    __tablename__ = "operation_sync"

    op_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    relais_id: Mapped[int] = mapped_column(ForeignKey("relais.id"))
    type: Mapped[str] = mapped_column(String(24))
    resultat: Mapped[dict] = mapped_column(JSON, default=dict)
    recu_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)


ROLES = ("sage_femme", "district", "admin", "relais")


class Utilisateur(Base):
    """Compte d'accès. Soignants : mot de passe + code TOTP. Relais : code relais + PIN."""

    __tablename__ = "utilisateur"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    nom: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    zone: Mapped[str | None] = mapped_column(String(64))  # aire de la sage-femme ; vide = toutes (district, admin)
    relais_id: Mapped[int | None] = mapped_column(ForeignKey("relais.id"), unique=True)
    secret_hache: Mapped[str] = mapped_column(String(256))  # mot de passe ou PIN, haché par scrypt
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_actif: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_dernier_pas: Mapped[int | None] = mapped_column(Integer)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    echecs: Mapped[int] = mapped_column(Integer, default=0)
    verrouille_jusqua: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)

    relais: Mapped[Relais | None] = relationship()

    @property
    def exige_totp(self) -> bool:
        return self.role != "relais"


class SessionAuth(Base):
    """Session ouverte. Seule l'empreinte du jeton est stockée.

    niveau « mot_de_passe » : il manque le code TOTP (ou son activation) ;
    seul le niveau « complet » donne accès aux données.
    """

    __tablename__ = "session_auth"

    id: Mapped[int] = mapped_column(primary_key=True)
    empreinte: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    utilisateur_id: Mapped[int] = mapped_column(ForeignKey("utilisateur.id"), index=True)
    niveau: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)
    expire_at: Mapped[datetime] = mapped_column(DateTime)

    utilisateur: Mapped[Utilisateur] = relationship()


class JournalAcces(Base):
    """Journal des accès et des actions, chaîné : chaque ligne contient l'empreinte de la précédente.

    Modifier ou supprimer une ligne casse la chaîne, ce que `journal.verifier` détecte.
    """

    __tablename__ = "journal_acces"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)
    utilisateur_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(48))
    cible: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(64))
    precedente: Mapped[str] = mapped_column(String(64))
    empreinte: Mapped[str] = mapped_column(String(64))


class AppelSortant(Base):
    """Appel (ou SMS) que Debbo passe de lui-même à une femme : « Debbo m'appelle »."""

    __tablename__ = "appel_sortant"

    id: Mapped[int] = mapped_column(primary_key=True)
    femme_id: Mapped[int] = mapped_column(ForeignKey("femme.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"))
    motif: Mapped[str] = mapped_column(String(24))  # veille_contact | relance
    tentative: Mapped[int] = mapped_column(Integer, default=1)
    prevu_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    statut: Mapped[str] = mapped_column(String(16), default="prevu", index=True)
    # prevu -> en_cours -> repondu | sans_reponse | echec ; ou annule
    touche: Mapped[str | None] = mapped_column(String(4))  # réponse au menu vocal
    reference: Mapped[str | None] = mapped_column(String(64), unique=True)  # identifiant chez le fournisseur
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)
    termine_at: Mapped[datetime | None] = mapped_column(DateTime)

    femme: Mapped[Femme] = relationship()
    contact: Mapped[Contact | None] = relationship()


class TacheRelais(Base):
    """Visite demandée à la relais : femme injoignable, consultation manquée, femme sans téléphone."""

    __tablename__ = "tache_relais"

    id: Mapped[int] = mapped_column(primary_key=True)
    femme_id: Mapped[int] = mapped_column(ForeignKey("femme.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"))
    motif: Mapped[str] = mapped_column(String(32))  # injoignable | consultation_manquee | rappel_sans_telephone
    created_at: Mapped[datetime] = mapped_column(DateTime, default=maintenant)
    faite_at: Mapped[datetime | None] = mapped_column(DateTime)

    femme: Mapped[Femme] = relationship()
    contact: Mapped[Contact | None] = relationship()
