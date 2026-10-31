import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config, journal, securite
from app.clinical import moteur
from app.models import Femme, Identite, JournalAcces, SessionAuth, Utilisateur, maintenant
from app.referral import ouvrir_alerte
from tests.conftest import (
    MOT_DE_PASSE, PIN, connecter, connecter_relais, creer_compte, creer_compte_relais, nouveau_client,
)


def connexion(client, identifiant, mot_de_passe=MOT_DE_PASSE):
    return client.post("/api/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": mot_de_passe})


# ---------- primitives ----------

def test_mot_de_passe_hache_et_verifie():
    stocke = securite.hacher("un secret assez long")
    assert stocke.startswith("scrypt$") and "un secret" not in stocke
    assert securite.verifier("un secret assez long", stocke)
    assert not securite.verifier("un autre secret", stocke)
    assert not securite.verifier("x", "format-invalide")


def test_totp_vecteur_rfc_6238():
    # RFC 6238, annexe B : secret ASCII « 12345678901234567890 », T = 59 s -> 94287082 (6 derniers chiffres).
    assert securite.code_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"


def test_totp_refuse_le_rejeu():
    secret = securite.nouveau_secret_totp()
    maintenant_s = time.time()
    pas = securite.verifier_totp(secret, securite.code_totp(secret, maintenant_s), None, maintenant_s)
    assert pas is not None
    assert securite.verifier_totp(secret, securite.code_totp(secret, maintenant_s), pas, maintenant_s) is None


# ---------- connexion des soignants ----------

def test_message_identique_pour_compte_inconnu_et_mauvais_mot_de_passe(session, client):
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    inconnu = connexion(client, "personne")
    mauvais = connexion(client, "sf.a", "mauvais mot de passe")
    assert inconnu.status_code == mauvais.status_code == 401
    assert inconnu.json() == mauvais.json()


def test_cinq_echecs_verrouillent_le_compte(session, client):
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    for _ in range(5):
        assert connexion(client, "sf.a", "mauvais").status_code == 401
    assert connexion(client, "sf.a").status_code == 429


def test_le_mot_de_passe_seul_ne_donne_acces_a_rien(session, client):
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    assert connexion(client, "sf.a").json() == {"etape": "activation_2fa"}
    assert client.get("/api/alertes").status_code == 401


def test_cookie_de_session_protege(session, client):
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    entete = connexion(client, "sf.a").headers["set-cookie"].lower()
    assert "httponly" in entete and "secure" in entete and "samesite=strict" in entete


def test_seule_l_empreinte_du_jeton_est_stockee(session, sf):
    jeton = sf.cookies.get("debbo_session")
    empreintes = session.scalars(select(SessionAuth.empreinte)).all()
    assert jeton not in empreintes
    assert securite.empreinte_jeton(jeton) in empreintes


def test_seconde_connexion_demande_le_code_et_refuse_le_rejeu(session):
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    connecter("sf.a")
    secret = session.scalar(select(Utilisateur.totp_secret))
    client = nouveau_client()
    assert connexion(client, "sf.a").json() == {"etape": "totp"}
    # Le code qui vient de servir à l'activation est refusé...
    assert client.post("/api/auth/totp", json={"code": securite.code_totp(secret)}).status_code == 401
    # ...le suivant est accepté.
    assert client.post("/api/auth/totp", json={"code": securite.code_totp(secret, time.time() + 30)}).status_code == 200
    assert client.get("/api/alertes").status_code == 200


def test_le_jeton_change_apres_le_second_facteur(session, client):
    creer_compte(session, "sf.a", "sage_femme", "Zone A")
    connexion(client, "sf.a")
    partiel = client.cookies.get("debbo_session")
    secret = client.post("/api/auth/2fa/debut").json()["secret"]
    client.post("/api/auth/2fa/activer", json={"code": securite.code_totp(secret)})
    assert client.cookies.get("debbo_session") != partiel


def test_deconnexion(sf):
    assert sf.post("/api/auth/deconnexion").status_code == 200
    assert sf.get("/api/alertes").status_code == 401


# ---------- rôles et zones ----------

def test_chaque_role_reste_dans_son_perimetre(session, sf):
    creer_compte(session, "admin", "admin")
    creer_compte_relais(session, "R-1", "Zone A")
    admin = connecter("admin")
    relais = connecter_relais("R-1")
    assert admin.get("/api/femmes").status_code == 403  # l'admin ne voit aucun dossier
    assert admin.get("/api/admin/utilisateurs").status_code == 200
    assert relais.get("/api/alertes").status_code == 403
    assert sf.get("/api/relais/moi/dossier").status_code == 403
    assert sf.get("/api/admin/utilisateurs").status_code == 403


@pytest.fixture
def trois_alertes(session):
    """Une alerte en Zone A, une en Zone B, une venue de l'USSD sans zone."""
    alertes = {}
    for nom, zone in [("a", "Zone A"), ("b", "Zone B"), ("ussd", None)]:
        identite = Identite(nom=nom, telephone=f"+22177000{len(alertes)}")
        if zone:
            session.add(Femme(identite=identite, zone=zone))
        alertes[nom] = ouvrir_alerte(session, identite, moteur.evaluer(["saignement"]), "grossesse", "ussd")
    session.commit()
    return alertes


def test_une_sage_femme_ne_voit_que_son_aire_et_les_urgences_sans_zone(sf, trois_alertes):
    noms = {a["nom"] for a in sf.get("/api/alertes").json()}
    assert noms == {"a", "ussd"}
    autre = trois_alertes["b"].id
    assert sf.post(f"/api/alertes/{autre}/transition", json={"etat": "sf_jointe"}).status_code == 404
    assert sf.get("/api/indicateurs").json()["alertes_actives"] == 2


def test_le_district_voit_toutes_les_zones(district, trois_alertes):
    assert {a["nom"] for a in district.get("/api/alertes").json()} == {"a", "b", "ussd"}


# ---------- relais ----------

def test_connexion_relais_par_pin(session):
    creer_compte_relais(session, "R-1", "Zone A")
    client = nouveau_client()
    assert client.post("/api/auth/relais", json={"code": "R-1", "pin": "999999"}).status_code == 401
    assert client.post("/api/auth/relais", json={"code": "r-1", "pin": PIN}).status_code == 200
    assert client.get("/api/relais/moi/dossier").json()["relais"]["code"] == "R-1"


def test_session_relais_glissante(session):
    creer_compte_relais(session, "R-1", "Zone A")
    client = connecter_relais("R-1")
    s = session.scalar(select(SessionAuth))
    s.expire_at = maintenant() + timedelta(days=3)
    session.commit()
    client.get("/api/relais/moi/dossier")
    session.refresh(s)
    assert s.expire_at > maintenant() + timedelta(days=29)


# ---------- administration ----------

def test_desactiver_un_compte_ferme_ses_sessions(session, sf):
    creer_compte(session, "admin", "admin")
    admin = connecter("admin")
    assert admin.post("/api/admin/utilisateurs/sf.a/desactiver").status_code == 200
    assert sf.get("/api/alertes").status_code == 401
    assert connexion(nouveau_client(), "sf.a").status_code == 401


def test_reinitialiser_la_2fa(session, sf):
    creer_compte(session, "admin", "admin")
    connecter("admin").post("/api/admin/utilisateurs/sf.a/reinitialiser-2fa")
    assert connexion(nouveau_client(), "sf.a").json() == {"etape": "activation_2fa"}


# ---------- journal ----------

def test_journal_chaine_et_detection_d_alteration(session, sf, trois_alertes):
    sf.post(f"/api/alertes/{trois_alertes['a'].id}/transition", json={"etat": "sf_jointe"})
    actions = session.scalars(select(JournalAcces.action)).all()
    assert {"compte_cree", "mot_de_passe_ok", "2fa_activee", "alerte_sf_jointe"} <= set(actions)
    assert journal.verifier(session) is None

    ligne = session.scalar(select(JournalAcces).where(JournalAcces.action == "alerte_sf_jointe"))
    ligne.cible = "alerte:999"  # quelqu'un maquille la trace
    session.commit()
    assert journal.verifier(session) == ligne.id


# ---------- protections HTTP ----------

def test_entetes_de_securite(client):
    entetes = client.get("/connexion").headers
    assert "frame-ancestors 'none'" in entetes["content-security-policy"]
    assert "script-src 'self'" in entetes["content-security-policy"]
    assert entetes["x-content-type-options"] == "nosniff"


def test_ecriture_depuis_un_autre_site_refusee(sf, trois_alertes):
    reponse = sf.post(f"/api/alertes/{trois_alertes['a'].id}/transition", json={"etat": "sf_jointe"},
                      headers={"Origin": "https://site-malveillant.example"})
    assert reponse.status_code == 403


def test_secret_de_l_agregateur_ussd(client, monkeypatch):
    monkeypatch.setattr(config, "USSD_SECRET", "secret-de-test")
    donnees = {"phoneNumber": "+221770000001", "text": ""}
    assert client.post("/ussd", data=donnees).status_code == 403
    assert client.post("/ussd", data=donnees, headers={"X-Debbo-Ussd-Secret": "secret-de-test"}).status_code == 200


def test_reponses_api_non_mises_en_cache(sf):
    assert sf.get("/api/alertes").headers["cache-control"] == "no-store"


def test_fichiers_de_l_interface_toujours_reverifies(client):
    assert client.get("/static/dashboard.js").headers["cache-control"] == "no-cache"
    assert client.get("/relais/app.js").headers["cache-control"] == "no-cache"
