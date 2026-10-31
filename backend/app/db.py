"""Base de données : SQLite en développement, PostgreSQL en production.

Le schéma évolue par migrations Alembic (dossier `migrations/`), jamais en
recréant les tables : les dossiers des femmes suivies doivent survivre à
chaque mise à jour.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parent.parent
BASE_LOCALE = BACKEND / "debbo.db"
DATABASE_URL = os.environ.get("DEBBO_DATABASE_URL", f"sqlite:///{BASE_LOCALE.as_posix()}")
EN_MEMOIRE = DATABASE_URL in ("sqlite://", "sqlite:///:memory:")

# Noms de contraintes stables : Alembic peut ensuite les modifier ou les supprimer
# de la même façon sur SQLite et PostgreSQL.
CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENTION)


def _config_alembic():
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.attributes["configurer_journaux"] = False  # ne pas écraser les journaux du serveur
    return config


def migrer() -> None:
    """Amène la base à la dernière version du schéma (alembic upgrade head)."""
    from alembic import command

    command.upgrade(_config_alembic(), "head")


def revision_en_retard() -> tuple[str | None, str] | None:
    """(révision de la base, dernière révision) si la base n'est pas à jour, sinon None."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    tete = ScriptDirectory.from_config(_config_alembic()).get_current_head()
    with engine.connect() as connexion:
        actuelle = MigrationContext.configure(connexion).get_current_revision()
    return None if actuelle == tete else (actuelle, tete)


class BaseNonMigree(RuntimeError):
    pass


def init_db(migrer_automatiquement: bool = True) -> None:
    """Prépare la base au démarrage.

    Développement : migre automatiquement. Production (migrer_automatiquement=False) :
    refuse de démarrer sur une base en retard ; la migration se lance à la main,
    après une sauvegarde, avec « alembic upgrade head ».
    """
    from app import models  # noqa: F401  (enregistre les tables)

    if EN_MEMOIRE:
        Base.metadata.create_all(engine)  # base jetable, pas besoin de migrations
        return
    with engine.connect() as connexion:
        tables = set(inspect(connexion).get_table_names())
    if tables and "alembic_version" not in tables:
        raise BaseNonMigree(
            "Cette base a été créée avant les migrations. En développement : python -m app.seed. "
            "Avec de vraies données : sauvegardez-la, puis « alembic stamp 0001 » et « alembic upgrade head »."
        )
    if migrer_automatiquement:
        migrer()
        return
    retard = revision_en_retard()
    if retard:
        raise BaseNonMigree(f"Base à la version {retard[0]}, le code attend {retard[1]} : "
                            "sauvegardez la base puis lancez « alembic upgrade head ».")


def vider_base() -> None:
    """Développement uniquement : supprime toutes les tables, suivi des migrations compris."""
    from app import models  # noqa: F401

    Base.metadata.drop_all(engine)
    with engine.begin() as connexion:
        if inspect(connexion).has_table("alembic_version"):
            connexion.exec_driver_sql("DROP TABLE alembic_version")


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
