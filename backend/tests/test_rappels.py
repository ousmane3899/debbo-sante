from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import config
from app.models import AppelSortant, Contact, DemandeRappel, Femme, Identite, TacheRelais
from app.rappels import DELAI_NOUVEL_ESSAI, enregistrer_resultat, executer, planifier
from tests.conftest import connecter_relais, creer_compte_relais

AUJOURDHUI = date(2026, 11, 2)
MIDI = datetime(2026, 11, 2, 12, 0)


class FournisseurFaux:
    def __init__(self):
        self.appels = []

    def appeler(self, telephone, message, langue):
        self.appels.append((telephone, message))
        return f"ref-{len(self.appels)}"


@pytest.fixture
def fournisseur():
    return FournisseurFaux()


def femme(session, telephone="+221770000001", consentement=True, contact_le=AUJOURDHUI + timedelta(days=1),
          zone="Zone A"):
    f = Femme(identite=Identite(nom="Femme test", telephone=telephone, consentement_service=consentement), zone=zone)
    f.contacts.append(Contact(type="CPN2", date_prevue=contact_le))
    session.add(f)
    session.commit()
    return f


def un_tour(session, fournisseur, a=MIDI):
    bilan = planifier(session, a.date())
    executer(session, a, fournisseur)
    session.commit()
    return bilan


def test_appel_la_veille_de_la_consultation_une_seule_fois(session, fournisseur):
    femme(session)
    assert un_tour(session, fournisseur)["appels"] == 1
    assert un_tour(session, fournisseur)["appels"] == 0
    assert fournisseur.appels == [("+221770000001", "appel_veille_contact")]


def test_rien_sans_consentement(session, fournisseur):
    femme(session, consentement=False)
    assert un_tour(session, fournisseur) == {"appels": 0, "taches": 0}


def test_sans_telephone_la_relais_passe_la_prevenir(session, fournisseur):
    femme(session, telephone=None)
    un_tour(session, fournisseur)
    assert session.scalar(select(TacheRelais.motif)) == "rappel_sans_telephone"
    assert fournisseur.appels == []


def test_appel_annule_si_elle_est_deja_venue(session, fournisseur):
    f = femme(session)
    planifier(session, AUJOURDHUI)
    f.contacts[0].date_realisee = AUJOURDHUI
    executer(session, MIDI, fournisseur)
    assert session.scalar(select(AppelSortant.statut)) == "annule"
    assert fournisseur.appels == []


def test_touche_2_demande_un_rappel_par_une_sage_femme(session, fournisseur):
    femme(session)
    un_tour(session, fournisseur)
    enregistrer_resultat(session, "ref-1", "repondu", "2", MIDI)
    assert session.scalar(select(DemandeRappel.motif)) == "rappel_debbo"


def test_deux_appels_sans_reponse_puis_visite_de_la_relais(session, fournisseur):
    femme(session)
    un_tour(session, fournisseur)
    enregistrer_resultat(session, "ref-1", "sans_reponse", None, MIDI)
    assert session.scalar(select(TacheRelais)) is None

    # Le second essai part 3 heures plus tard, pas avant.
    un_tour(session, fournisseur, MIDI + DELAI_NOUVEL_ESSAI - timedelta(minutes=1))
    assert len(fournisseur.appels) == 1
    un_tour(session, fournisseur, MIDI + DELAI_NOUVEL_ESSAI)
    assert len(fournisseur.appels) == 2

    enregistrer_resultat(session, "ref-2", "sans_reponse", None, MIDI + DELAI_NOUVEL_ESSAI)
    assert session.scalar(select(TacheRelais.motif)) == "injoignable"
    assert session.scalar(select(AppelSortant).where(AppelSortant.statut == "prevu")) is None


@pytest.mark.parametrize(("sans_reponse_a", "essai_attendu"), [
    (datetime(2026, 11, 2, 12, 0), datetime(2026, 11, 2, 15, 0)),   # +3 h, en journée
    (datetime(2026, 11, 2, 18, 30), datetime(2026, 11, 3, 10, 0)),  # 21 h 30 : reporté au lendemain 10 h
    (datetime(2026, 11, 2, 1, 0), datetime(2026, 11, 2, 10, 0)),    # 4 h du matin : reporté à 10 h
])
def test_jamais_d_appel_la_nuit(session, fournisseur, sans_reponse_a, essai_attendu):
    femme(session)
    un_tour(session, fournisseur)
    enregistrer_resultat(session, "ref-1", "sans_reponse", None, sans_reponse_a)
    second = session.scalar(select(AppelSortant).where(AppelSortant.tentative == 2))
    assert second.prevu_at == essai_attendu


def test_aucun_appel_ne_part_la_nuit_meme_en_retard(session, fournisseur):
    femme(session)
    planifier(session, AUJOURDHUI)
    assert executer(session, datetime(2026, 11, 2, 22, 0), fournisseur) == 0
    assert executer(session, datetime(2026, 11, 3, 9, 0), fournisseur) == 1


def test_notification_repetee_sans_effet(session, fournisseur):
    femme(session)
    un_tour(session, fournisseur)
    enregistrer_resultat(session, "ref-1", "sans_reponse", None, MIDI)
    enregistrer_resultat(session, "ref-1", "sans_reponse", None, MIDI)
    assert len(session.scalars(select(AppelSortant)).all()) == 2


def test_consultation_manquee_relance_et_visite_une_seule_fois(session, fournisseur):
    femme(session, contact_le=AUJOURDHUI - timedelta(days=10))
    assert un_tour(session, fournisseur) == {"appels": 1, "taches": 1}
    assert un_tour(session, fournisseur) == {"appels": 0, "taches": 0}
    assert fournisseur.appels[0][1] == "appel_relance"
    assert session.scalar(select(TacheRelais.motif)) == "consultation_manquee"


def test_plusieurs_consultations_manquees_une_seule_relance(session, fournisseur):
    f = femme(session, contact_le=AUJOURDHUI - timedelta(days=40))
    f.contacts.append(Contact(type="CPN3", date_prevue=AUJOURDHUI - timedelta(days=10)))
    session.commit()
    assert un_tour(session, fournisseur) == {"appels": 1, "taches": 1}
    assert session.scalar(select(TacheRelais)).contact.type == "CPN3"


def test_la_visite_de_la_relais_clot_les_taches(session, fournisseur):
    creer_compte_relais(session, "R-1", "Zone A")
    femme(session, telephone=None, contact_le=AUJOURDHUI - timedelta(days=10))
    un_tour(session, fournisseur)
    client = connecter_relais("R-1")
    fiche = client.get("/api/relais/moi/dossier").json()["femmes"][0]
    assert [t["motif"] for t in fiche["taches"]] == ["consultation_manquee"]

    code = fiche["code"]
    client.post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "v1", "type": "visite", "donnees": {"femme": {"code": code}, "contexte": "grossesse", "signes": []}}]})
    assert client.get("/api/relais/moi/dossier").json()["femmes"][0]["taches"] == []


def test_webhook_du_fournisseur(session, client, fournisseur, monkeypatch):
    femme(session)
    un_tour(session, fournisseur)
    assert client.post("/api/telephonie/resultat", json={"reference": "inconnue", "statut": "repondu"}).status_code == 404
    assert client.post("/api/telephonie/resultat", json={"reference": "ref-1", "statut": "bizarre"}).status_code == 422

    monkeypatch.setattr(config, "TELEPHONIE_SECRET", "secret-test")
    corps = {"reference": "ref-1", "statut": "repondu", "touche": "1"}
    assert client.post("/api/telephonie/resultat", json=corps).status_code == 403
    reponse = client.post("/api/telephonie/resultat", json=corps, headers={"X-Debbo-Telephonie-Secret": "secret-test"})
    assert reponse.json() == {"reference": "ref-1", "statut": "repondu"}


def test_le_simulateur_voit_les_appels_en_cours(session, client, fournisseur):
    femme(session)
    un_tour(session, fournisseur)
    appels = client.get("/api/simulateur/appels", params={"telephone": "+221770000001"}).json()
    assert appels[0]["reference"] == "ref-1"
    assert "rendez-vous demain" in appels[0]["message"]
    assert "enceinte" not in appels[0]["message"].lower()  # formulation neutre, téléphone partagé


def test_indicateurs_des_appels(session, sf, fournisseur):
    femme(session)
    un_tour(session, fournisseur)
    enregistrer_resultat(session, "ref-1", "repondu", "1", datetime.now())
    session.commit()
    indicateurs = sf.get("/api/indicateurs").json()
    assert indicateurs["appels_7j"] == 1 and indicateurs["taux_reponse_7j"] == 100
