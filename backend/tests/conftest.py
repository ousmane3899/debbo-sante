import os

# SQLite en mémoire par défaut ; scripts/tester_postgres.py fait tourner la même suite sur PostgreSQL.
os.environ["DEBBO_DATABASE_URL"] = os.environ.get("DEBBO_TEST_DATABASE_URL", "sqlite://")
os.environ["DEBBO_PLANIFICATEUR"] = "0"  # les tests appellent le planificateur eux-mêmes

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import securite  # noqa: E402
from app.comptes import creer_relais, creer_soignant  # noqa: E402
from app import models  # noqa: E402, F401
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

securite.SCRYPT_N = 2**10  # tests rapides ; la production garde 2**15

MOT_DE_PASSE = "mot-de-passe-de-test"
PIN = "482917"


def nouveau_client() -> TestClient:
    # https : le cookie de session est « Secure » et ne repart que sur https.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def base_vide():
    # Le schéma des tests vient directement du modèle ; test_migrations.py vérifie que
    # les migrations produisent exactement le même.
    Base.metadata.create_all(engine)
    yield
    engine.dispose()  # ferme les connexions restées ouvertes avant de supprimer les tables
    Base.metadata.drop_all(engine)


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    return nouveau_client()


def creer_compte(session, identifiant, role, zone=None):
    u = creer_soignant(session, identifiant, identifiant, role, MOT_DE_PASSE, zone)
    session.commit()
    return u


def connecter(identifiant, client=None):
    """Connexion complète d'un soignant, avec activation du TOTP à la première connexion."""
    client = client or nouveau_client()
    reponse = client.post("/api/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 200, reponse.text
    if reponse.json()["etape"] == "activation_2fa":
        secret = client.post("/api/auth/2fa/debut").json()["secret"]
        reponse = client.post("/api/auth/2fa/activer", json={"code": securite.code_totp(secret)})
        assert reponse.status_code == 200, reponse.text
    return client


@pytest.fixture
def sf(session):
    """Client connecté comme sage-femme de la « Zone A »."""
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    return connecter("sf.a")


@pytest.fixture
def district(session):
    creer_compte(session, "district", "district")
    return connecter("district")


def connecter_relais(code, client=None):
    client = client or nouveau_client()
    reponse = client.post("/api/auth/relais", json={"code": code, "pin": PIN})
    assert reponse.status_code == 200, reponse.text
    return client


def creer_compte_relais(session, code, zone, **extra):
    u = creer_relais(session, code, f"Relais {code}", zone, PIN, **extra)
    session.commit()
    return u.relais
