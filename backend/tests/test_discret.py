from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.i18n import TEXTES
from app.models import Alerte, Femme, Identite, JournalAcces
from app.planning import planifier_grossesse
from app.rappels import executer, planifier
from app.ussd import repondre
from tests.conftest import connecter, connecter_relais, creer_compte, creer_compte_relais, nouveau_client

NUMERO = "+221770000001"
CODE = "4827"


def ussd(session, *saisies, numero=NUMERO):
    return repondre(session, numero, "*".join(saisies))


@pytest.fixture
def inscrite(session):
    assert ussd(session, "3", "2", "1", "4").startswith("END Merci")
    return session.scalar(select(Identite).where(Identite.telephone == NUMERO))


@pytest.fixture
def avec_code(session, inscrite):
    assert ussd(session, "6", "4").startswith("CON Choisissez un code")
    assert ussd(session, "6", "4", CODE).startswith("CON Retapez")
    assert ussd(session, "6", "4", CODE, CODE).startswith("END Code enregistre")
    session.refresh(inscrite)
    return inscrite


# ---------- écrans USSD ----------

@pytest.mark.parametrize("cle", [c for c in TEXTES["fr"] if not c.startswith("appel_")])
def test_chaque_ecran_ussd_tient_dans_182_caracteres(cle):
    texte = TEXTES["fr"][cle].replace("{zones}", "\n".join(f"{i} Zone numero {i}" for i in range(1, 9)))
    assert len(texte.replace("{date}", "31/12/2026")) <= 182


# ---------- code personnel ----------

def test_le_code_est_hache(avec_code):
    assert avec_code.code_hache and CODE not in avec_code.code_hache


@pytest.mark.parametrize("code", ["1234", "0000", "12a4", "123", "1212"])
def test_code_trop_simple_refuse(session, inscrite, code):
    assert ussd(session, "6", "4", code).startswith("END Ce code est trop facile")


def test_codes_differents(session, inscrite):
    assert ussd(session, "6", "4", CODE, "4828").startswith("END Les deux codes sont differents")
    session.refresh(inscrite)
    assert inscrite.code_hache is None


def test_menus_personnels_proteges(session, avec_code):
    assert ussd(session, "3").startswith("CON Entrez votre code")
    assert ussd(session, "3", CODE).startswith("END Prochaine consultation")
    assert ussd(session, "3", "9999").startswith("END Code incorrect")
    assert ussd(session, "2").startswith("CON Entrez votre code")  # « déjà inscrite » montre une date
    assert ussd(session, "6").startswith("CON Entrez votre code")


def test_urgence_rappel_et_conseils_restent_ouverts_sans_code(session, avec_code):
    assert ussd(session, "1", "1").startswith("END Alerte envoyee")
    assert session.scalar(select(Alerte.niveau)) == "urgence"
    assert ussd(session, "4", "1").startswith("END C'est note")
    assert ussd(session, "5", "1").startswith("END Faites au moins 8 consultations")


def test_trois_erreurs_verrouillent_le_code(session, avec_code):
    for _ in range(3):
        assert ussd(session, "3", "1111").startswith("END Code incorrect")
    assert ussd(session, "3", CODE).startswith("END Trop d'essais")
    assert ussd(session, "1", "1").startswith("END Alerte envoyee")  # l'urgence, jamais bloquée
    assert "code_discret_verrouille" in set(session.scalars(select(JournalAcces.action)))


def test_l_effacement_demande_le_code(session, avec_code):
    assert ussd(session, "6", "1", "1").startswith("END Code incorrect")  # « 1 » pris pour un code faux
    assert session.scalar(select(Femme.statut)) == "enceinte"
    assert ussd(session, "6", CODE, "1", "1").startswith("END C'est fait")


def test_changer_puis_supprimer_le_code(session, avec_code):
    assert ussd(session, "6", CODE, "4").startswith("CON Code personnel\n1 Changer")
    assert ussd(session, "6", CODE, "4", "1", "5931", "5931").startswith("END Code enregistre")
    assert ussd(session, "3", CODE).startswith("END Code incorrect")
    assert ussd(session, "6", "5931", "4", "2").startswith("END Code supprime")
    assert ussd(session, "3").startswith("END Prochaine consultation")


def test_pas_de_code_pour_un_numero_inconnu(session):
    assert ussd(session, "3", "6", "4").startswith("END Debbo n'a aucune donnee")
    assert session.scalar(select(Identite)) is None


# ---------- appels discrets ----------

class FournisseurFaux:
    def __init__(self):
        self.messages = []

    def appeler(self, telephone, message, langue):
        self.messages.append(message)
        return f"ref-{len(self.messages)}"


def test_en_mode_discret_l_appel_ne_dit_rien(session, avec_code, client):
    femme = avec_code.femme
    femme.contacts[0].date_prevue = date.today() + timedelta(days=1)
    session.commit()
    fournisseur = FournisseurFaux()
    planifier(session, date.today())
    executer(session, datetime.combine(date.today(), datetime.min.time()).replace(hour=12), fournisseur)
    session.commit()
    assert fournisseur.messages == ["appel_discret"]
    message = client.get("/api/simulateur/appels", params={"telephone": NUMERO}).json()[0]["message"]
    assert "rendez-vous" not in message and "poste de sante" not in message


# ---------- code oublié ----------

def test_la_relais_retire_un_code_oublie(session, avec_code):
    avec_code.femme.zone = "Zone A"
    session.commit()
    creer_compte_relais(session, "R-1", "Zone A")
    relais = connecter_relais("R-1")
    fiche = relais.get("/api/relais/moi/dossier").json()["femmes"][0]
    assert fiche["code_discret"] is True
    relais.post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "r", "type": "retirer_code", "donnees": {"femme": {"code": fiche["code"]}}}]})
    session.refresh(avec_code)
    assert avec_code.code_hache is None
    assert ussd(session, "3").startswith("END Prochaine consultation")


def test_le_poste_retire_un_code_oublie(session, avec_code):
    creer_compte(session, "admin", "admin")
    code_femme = avec_code.femme.code
    assert connecter("admin").post(f"/api/admin/femmes/{code_femme}/retirer-code").status_code == 200
    session.refresh(avec_code)
    assert avec_code.code_hache is None


# ---------- zone ----------

def test_la_femme_choisit_sa_zone_a_l_inscription(session):
    creer_compte_relais(session, "R-1", "Ourossogui")
    creer_compte_relais(session, "R-2", "Kanel")
    ecran = ussd(session, "3", "2", "1", "4")
    assert ecran.startswith("CON Ou habitez-vous ?\n1 Kanel\n2 Ourossogui\n0 Je ne sais pas")
    assert ussd(session, "3", "2", "1", "4", "9", numero="+221770000077").startswith("END Reponse non comprise")
    assert ussd(session, "3", "2", "1", "4", "2").startswith("END Merci")
    assert session.scalar(select(Femme.zone)) == "Ourossogui"


def test_je_ne_sais_pas_laisse_la_zone_vide(session):
    creer_compte_relais(session, "R-1", "Ourossogui")
    assert ussd(session, "3", "2", "1", "4", "0").startswith("END Merci")
    assert session.scalar(select(Femme.zone)) is None


def femme_sans_zone(session):
    f = Femme(identite=Identite(nom="Sans zone", telephone="+221770000009", consentement_service=True))
    planifier_grossesse(f, 4, date.today())
    session.add(f)
    session.commit()
    return f


def test_la_sage_femme_rattache_a_son_aire_une_femme_sans_zone(session, sf):
    f = femme_sans_zone(session)
    creer_compte(session, "sf.b", "sage_femme", "Zone B")
    autre = connecter("sf.b")
    assert len(autre.get("/api/femmes").json()) == 1  # sans zone : visible de toutes

    assert sf.post(f"/api/femmes/{f.id}/zone", json={"zone": "Zone B"}).status_code == 403  # pas son aire
    assert sf.post(f"/api/femmes/{f.id}/zone", json={"zone": "Zone A"}).json()["zone"] == "Zone A"
    assert autre.get("/api/femmes").json() == []
    assert sf.post(f"/api/femmes/{f.id}/zone", json={"zone": "Zone A"}).status_code == 403  # déjà rattachée
    assert "femme_rattachee" in set(session.scalars(select(JournalAcces.action)))


def test_le_district_peut_changer_la_zone(session, district):
    f = femme_sans_zone(session)
    f.zone = "Zone A"
    session.commit()
    assert district.post(f"/api/femmes/{f.id}/zone", json={"zone": "Zone B"}).json()["zone"] == "Zone B"


def test_sans_connexion_pas_de_rattachement(session):
    f = femme_sans_zone(session)
    assert nouveau_client().post(f"/api/femmes/{f.id}/zone", json={"zone": "Zone A"}).status_code == 401
