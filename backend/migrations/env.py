"""Environnement Alembic : la base visée est celle de l'application (DEBBO_DATABASE_URL)."""

from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (enregistre les tables dans Base.metadata)
from app.db import Base, engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configurer_journaux", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _executer(connexion) -> None:
    context.configure(
        connection=connexion,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite ne sait pas modifier une colonne en place : Alembic recopie la table.
        render_as_batch=connexion.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def lancer() -> None:
    # Les tests passent leur propre connexion pour migrer une autre base que celle de l'application.
    connexion = config.attributes.get("connexion")
    if connexion is not None:
        _executer(connexion)
        return
    with engine.connect() as connexion:
        _executer(connexion)
        connexion.commit()


if context.is_offline_mode():
    raise SystemExit("Mode hors ligne non pris en charge : lancez les migrations sur une base réelle.")
lancer()
