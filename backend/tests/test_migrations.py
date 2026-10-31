"""Les migrations Alembic produisent exactement le schéma du modèle, et savent monter et descendre.

Tournent sur la base de test en cours : SQLite par défaut, PostgreSQL avec scripts/tester_postgres.py.
Chaque test travaille dans une base séparée (fichier SQLite, ou schéma PostgreSQL dédié).
"""

import os
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db import Base, _config_alembic

URL_TEST = os.environ.get("DEBBO_TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def connexion_vierge(tmp_path):
    """Une connexion à une base vide, isolée de la base des autres tests."""
    if URL_TEST.startswith("postgresql"):
        schema = f"migr_{uuid.uuid4().hex[:8]}"
        moteur = create_engine(URL_TEST)
        with moteur.begin() as cx:
            cx.execute(text(f"CREATE SCHEMA {schema}"))
        with moteur.connect() as cx:
            cx.execute(text(f"SET search_path TO {schema}"))
            cx.commit()
            yield cx
        with moteur.begin() as cx:
            cx.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        moteur.dispose()
    else:
        moteur = create_engine(f"sqlite:///{(tmp_path / 'migr.db').as_posix()}")
        with moteur.connect() as cx:
            yield cx
        moteur.dispose()


def migrer(connexion, revision, sens="upgrade"):
    config = _config_alembic()
    config.attributes["connexion"] = connexion
    getattr(command, sens)(config, revision)
    connexion.commit()


def ecarts(connexion):
    contexte = MigrationContext.configure(connexion, opts={"compare_type": True})
    return compare_metadata(contexte, Base.metadata)


def test_une_seule_tete_de_migration():
    assert len(ScriptDirectory.from_config(_config_alembic()).get_heads()) == 1


def test_les_migrations_donnent_exactement_le_schema_du_modele(connexion_vierge):
    migrer(connexion_vierge, "head")
    assert ecarts(connexion_vierge) == []


def test_descendre_puis_remonter(connexion_vierge):
    migrer(connexion_vierge, "head")
    migrer(connexion_vierge, "base", sens="downgrade")
    migrer(connexion_vierge, "head")
    assert ecarts(connexion_vierge) == []


def test_les_femmes_deja_inscrites_gardent_un_consentement_date_sans_recherche_ni_ia(connexion_vierge):
    migrer(connexion_vierge, "0001")
    connexion_vierge.execute(text(
        "INSERT INTO identite (telephone, consentement_service, created_at) "
        "VALUES ('+221770000001', :vrai, '2026-01-15 10:00:00'), ('+221770000002', :faux, '2026-01-16 10:00:00')"
    ), {"vrai": True, "faux": False})
    connexion_vierge.commit()

    migrer(connexion_vierge, "0002")
    lignes = connexion_vierge.execute(text(
        "SELECT telephone, consentement_par, consentement_recherche, consentement_ia FROM identite ORDER BY telephone"
    )).all()
    assert [(l[0], l[1], bool(l[2]), bool(l[3])) for l in lignes] == [
        ("+221770000001", "avant_v2", False, False),
        ("+221770000002", None, False, False),
    ]


@pytest.fixture
def base_fichier(tmp_path, monkeypatch):
    """Fait pointer l'application sur une base SQLite fichier, le temps d'un test."""
    from app import db

    moteur = create_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setattr(db, "engine", moteur)
    monkeypatch.setattr(db, "EN_MEMOIRE", False)
    yield db, moteur
    moteur.dispose()


def test_une_base_creee_avant_les_migrations_est_refusee(base_fichier):
    db, moteur = base_fichier
    Base.metadata.create_all(moteur)
    with pytest.raises(db.BaseNonMigree, match="avant les migrations"):
        db.init_db()


def test_en_production_le_serveur_refuse_une_base_en_retard(base_fichier):
    db, moteur = base_fichier
    with moteur.connect() as cx:
        migrer(cx, "0001")
    with pytest.raises(db.BaseNonMigree, match="alembic upgrade head"):
        db.init_db(migrer_automatiquement=False)
    db.init_db()  # en développement : migration automatique
    db.init_db(migrer_automatiquement=False)  # à jour : démarrage accepté
