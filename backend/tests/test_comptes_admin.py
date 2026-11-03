from datetime import timedelta

import pytest
from sqlalchemy import select

from app import securite
from app.models import JournalAcces, Utilisateur, maintenant
from tests.conftest import connecter, creer_compte, nouveau_client


@pytest.fixture
def admin(session):
    creer_compte(session, "admin", "admin")
    return connecter("admin")


def creer(admin, **corps):
    reponse = admin.post("/api/admin/utilisateurs", json=corps)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def actions(session):
    return [l.action for l in session.scalars(select(JournalAcces).order_by(JournalAcces.id))]


# ---------- soignants ----------

def test_creer_une_sage_femme_avec_mot_de_passe_provisoire(session, admin):
    r = creer(admin, role="sage_femme", nom="Awa Ba", identifiant="Awa.Ba", zone="Kanel")
    assert r["compte"]["identifiant"] == "awa.ba"
    assert r["compte"]["secret_provisoire"] is True
    assert len(r["secret_provisoire"]) == 14  # xxxx-xxxx-xxxx
    u = session.scalar(select(Utilisateur).where(Utilisateur.identifiant == "awa.ba"))
    assert r["secret_provisoire"] not in u.secret_hache
    ligne = session.scalar(select(JournalAcces).where(JournalAcces.action == "compte_cree", JournalAcces.cible == "awa.ba"))
    assert ligne.utilisateur_id is not None  # tracé au nom de l'admin


def test_premiere_connexion_impose_un_nouveau_mot_de_passe(session, admin):
    provisoire = creer(admin, role="district", nom="Équipe", identifiant="equipe.matam")["secret_provisoire"]
    c = nouveau_client()
    r = c.post("/api/auth/connexion", json={"identifiant": "equipe.matam", "mot_de_passe": provisoire})
    assert r.json() == {"etape": "nouveau_mot_de_passe"}
    assert c.get("/api/auth/moi").json() == {"etape": "nouveau_mot_de_passe"}
    # La double authentification attend le vrai mot de passe.
    assert c.post("/api/auth/2fa/debut").status_code == 409
    assert c.get("/api/indicateurs").status_code == 401

    assert c.post("/api/auth/mot-de-passe", json={"nouveau": "court"}).status_code == 422
    assert c.post("/api/auth/mot-de-passe", json={"nouveau": provisoire}).status_code == 422
    r = c.post("/api/auth/mot-de-passe", json={"nouveau": "trois mots et 42 ans"})
    assert r.json() == {"etape": "activation_2fa"}
    secret = c.post("/api/auth/2fa/debut").json()["secret"]
    assert c.post("/api/auth/2fa/activer", json={"code": securite.code_totp(secret)}).status_code == 200
    assert c.get("/api/indicateurs").status_code == 200
    assert "secret_choisi" in actions(session)

    # L'ancien mot de passe provisoire ne marche plus.
    r = nouveau_client().post("/api/auth/connexion", json={"identifiant": "equipe.matam", "mot_de_passe": provisoire})
    assert r.status_code == 401


def test_mot_de_passe_provisoire_expire(session, admin):
    provisoire = creer(admin, role="district", nom="Équipe", identifiant="equipe.matam")["secret_provisoire"]
    u = session.scalar(select(Utilisateur).where(Utilisateur.identifiant == "equipe.matam"))
    u.secret_expire_at = maintenant() - timedelta(minutes=1)
    session.commit()
    r = nouveau_client().post("/api/auth/connexion", json={"identifiant": "equipe.matam", "mot_de_passe": provisoire})
    assert r.status_code == 401 and "expiré" in r.json()["detail"]
    assert admin.get("/api/admin/utilisateurs").json()[1]["provisoire_expire"] is True


@pytest.mark.parametrize("corps, statut", [
    ({"role": "sage_femme", "nom": "Sans zone", "identifiant": "sans.zone"}, 422),
    ({"role": "sage_femme", "nom": "X", "identifiant": "a b", "zone": "Kanel"}, 422),
    ({"role": "chef", "nom": "X", "identifiant": "chef.x"}, 422),
    ({"role": "district", "nom": " ", "identifiant": "vide.x"}, 422),
    ({"role": "district", "nom": "Doublon", "identifiant": "admin"}, 409),
])
def test_creation_refusee(admin, corps, statut):
    assert admin.post("/api/admin/utilisateurs", json=corps).status_code == statut


def test_seule_l_administration_gere_les_comptes(session, sf):
    assert sf.get("/api/admin/utilisateurs").status_code == 403
    corps = {"role": "admin", "nom": "Moi", "identifiant": "pirate"}
    assert sf.post("/api/admin/utilisateurs", json=corps).status_code == 403
    assert nouveau_client().post("/api/admin/utilisateurs", json=corps).status_code == 401


# ---------- relais ----------

def test_creer_une_relais_et_choisir_son_pin(session, admin):
    assert admin.get("/api/admin/zones").json()["prochain_code_relais"] == "R-0001"
    r = creer(admin, role="relais", nom="Coumba Sy", code="r-0001", zone="Ourossogui", telephone="+221770000300")
    pin = r["secret_provisoire"]
    assert pin.isdigit() and len(pin) == 6
    assert r["compte"]["relais"]["code"] == "R-0001"
    assert admin.get("/api/admin/zones").json() == {"zones": ["Ourossogui"], "prochain_code_relais": "R-0002"}

    c = nouveau_client()
    assert c.post("/api/auth/relais", json={"code": "R-0001", "pin": pin}).json() == {"etape": "nouveau_pin"}
    assert c.get("/api/relais/moi/dossier").status_code == 401
    assert c.post("/api/auth/pin", json={"nouveau": "123456"}).status_code == 422
    assert c.post("/api/auth/pin", json={"nouveau": pin}).status_code == 422
    assert c.post("/api/auth/pin", json={"nouveau": "583920"}).json()["etape"] == "ok"
    assert c.get("/api/relais/moi/dossier").status_code == 200

    autre = nouveau_client()
    assert autre.post("/api/auth/relais", json={"code": "R-0001", "pin": "583920"}).json()["etape"] == "ok"
    assert nouveau_client().post("/api/auth/relais", json={"code": "R-0001", "pin": pin}).status_code == 401


def test_code_relais_en_double(admin):
    creer(admin, role="relais", nom="A", code="R-0001", zone="Kanel")
    corps = {"role": "relais", "nom": "B", "code": "R-0001", "zone": "Kanel"}
    assert admin.post("/api/admin/utilisateurs", json=corps).status_code == 409


def test_une_relais_ne_peut_pas_passer_par_la_2fa(admin):
    pin = creer(admin, role="relais", nom="A", code="R-0001", zone="Kanel")["secret_provisoire"]
    c = nouveau_client()
    c.post("/api/auth/relais", json={"code": "R-0001", "pin": pin})
    assert c.post("/api/auth/2fa/debut").status_code == 403
    assert c.post("/api/auth/mot-de-passe", json={"nouveau": "un mot de passe long"}).status_code == 409


# ---------- actions sur un compte ----------

def test_nouveau_mot_de_passe_ferme_les_sessions(session, admin):
    creer_compte(session, "sf.oubli", "sage_femme", "Kanel")
    ancienne = connecter("sf.oubli")
    r = admin.post("/api/admin/utilisateurs/sf.oubli/nouveau-secret")
    assert r.status_code == 200 and len(r.json()["secret_provisoire"]) == 14
    assert ancienne.get("/api/femmes").status_code == 401
    c = nouveau_client()
    reponse = c.post("/api/auth/connexion", json={"identifiant": "sf.oubli", "mot_de_passe": r.json()["secret_provisoire"]})
    assert reponse.json()["etape"] == "nouveau_mot_de_passe"
    # La 2FA déjà activée est gardée : après le nouveau mot de passe, on demande le code TOTP.
    assert c.post("/api/auth/mot-de-passe", json={"nouveau": "un mot de passe neuf"}).json() == {"etape": "totp"}


def test_pas_de_reinitialisation_de_son_propre_compte(admin):
    assert admin.post("/api/admin/utilisateurs/admin/nouveau-secret").status_code == 409
    assert admin.post("/api/admin/utilisateurs/admin/reinitialiser-2fa").status_code == 409
    assert admin.post("/api/admin/utilisateurs/admin/desactiver").status_code == 409


def test_deverrouiller_et_reactiver(session, admin):
    creer_compte(session, "sf.x", "sage_femme", "Kanel")
    for _ in range(5):
        nouveau_client().post("/api/auth/connexion", json={"identifiant": "sf.x", "mot_de_passe": "faux mot de passe"})
    fiche = next(c for c in admin.get("/api/admin/utilisateurs").json() if c["identifiant"] == "sf.x")
    assert fiche["verrouille"] is True
    assert admin.post("/api/admin/utilisateurs/sf.x/deverrouiller").json()["verrouille"] is False
    connecter("sf.x")

    admin.post("/api/admin/utilisateurs/sf.x/desactiver")
    r = nouveau_client().post("/api/auth/connexion", json={"identifiant": "sf.x", "mot_de_passe": "mot-de-passe-de-test"})
    assert r.status_code == 401
    assert admin.post("/api/admin/utilisateurs/sf.x/reactiver").json()["actif"] is True
    connecter("sf.x")
    assert {"compte_deverrouille", "compte_reactive"} <= set(actions(session))


def test_changer_l_aire_d_une_sage_femme(session, admin):
    creer_compte(session, "sf.x", "sage_femme", "Kanel")
    ancienne = connecter("sf.x")
    r = admin.post("/api/admin/utilisateurs/sf.x/modifier", json={"nom": "Awa", "zone": "Ourossogui"})
    assert r.json()["zone"] == "Ourossogui"
    assert ancienne.get("/api/femmes").status_code == 401  # reconnexion : elle ne voit plus que sa nouvelle aire
    vide = admin.post("/api/admin/utilisateurs/sf.x/modifier", json={"nom": "Awa", "zone": ""})
    assert vide.status_code == 422


def test_modifier_une_relais(admin):
    creer(admin, role="relais", nom="A", code="R-0001", zone="Kanel")
    r = admin.post("/api/admin/utilisateurs/r-0001/modifier",
                   json={"nom": "Aïssata", "zone": "Ourossogui", "telephone": "+221770000301", "telephone_sf": ""})
    fiche = r.json()
    assert (fiche["nom"], fiche["zone"], fiche["relais"]["telephone"]) == ("Aïssata", "Ourossogui", "+221770000301")


def test_le_journal_nomme_l_auteur(admin):
    creer(admin, role="district", nom="Équipe", identifiant="equipe.matam")
    lignes = admin.get("/api/admin/journal").json()["lignes"]
    assert any(l["action"] == "compte_cree" and l["par"] == "admin" for l in lignes)


def test_page_admin_servie(client):
    assert "Administration" in client.get("/admin").text
