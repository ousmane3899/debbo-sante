import json
from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import select

from app import config, dhis2
from app.clinical import moteur
from app.models import (
    Contact, EnvoiDhis2, Femme, Identite, JournalAcces, Visite,
)
from app.planning import enregistrer_accouchement
from app.referral import faire_transition, ouvrir_alerte
from tests.conftest import creer_compte_relais

OCT_DEBUT, OCT_FIN = date(2026, 10, 1), date(2026, 10, 31)


@pytest.fixture
def correspondances(tmp_path, monkeypatch):
    """Zone A et quelques éléments reliés ; le reste volontairement « A_RENSEIGNER »."""
    donnees = dhis2.lire_correspondances()
    donnees["dataSet"] = "dsDebbo0001"
    donnees["zones"] = {"Zone A": "ouZoneA0001", "Zone B": "A_RENSEIGNER"}
    donnees["elements"]["cpn_realisees"]["dataElement"] = "deCPN000001"
    donnees["elements"]["vaccins_penta1"]["dataElement"] = "dePENTA0001"
    donnees["elements"]["vaccins_penta1"]["categoryOptionCombo"] = "cocMoins1an"
    chemin = tmp_path / "correspondances.json"
    chemin.write_text(json.dumps(donnees), encoding="utf-8")
    monkeypatch.setattr(dhis2, "CORRESPONDANCES", chemin)
    return donnees


def femme(session, zone="Zone A", inscrite=datetime(2026, 10, 5, 9, 0), telephone="+221770000001"):
    f = Femme(identite=Identite(nom="Test", telephone=telephone, consentement_service=True), zone=zone,
              created_at=inscrite)
    session.add(f)
    session.commit()
    return f


# ---------- période ----------

@pytest.mark.parametrize(("texte", "fin"), [("2026-10", date(2026, 10, 31)), ("2028-02", date(2028, 2, 29))])
def test_periode(texte, fin):
    assert dhis2.lire_periode(texte, aujourdhui=date(2030, 1, 1))[1] == fin


@pytest.mark.parametrize("texte", ["2026-13", "10-2026", "", "2026-1"])
def test_periode_invalide(texte):
    with pytest.raises(ValueError):
        dhis2.lire_periode(texte)


def test_pas_de_mois_futur():
    with pytest.raises(ValueError, match="futur"):
        dhis2.lire_periode("2026-12", aujourdhui=date(2026, 11, 15))


# ---------- indicateurs ----------

def test_indicateurs_du_mois_par_zone(session):
    relais = creer_compte_relais(session, "R-1", "Zone A")
    f = femme(session)
    femme(session, inscrite=datetime(2026, 9, 30, 23, 0), telephone="+221770000002")  # mois précédent
    femme(session, zone="Zone B", telephone="+221770000003")
    f.contacts += [Contact(type="CPN1", date_prevue=date(2026, 10, 3), date_realisee=date(2026, 10, 3)),
                   Contact(type="CPN2", date_prevue=date(2026, 11, 3), date_realisee=date(2026, 11, 3))]
    enregistrer_accouchement(f, date(2026, 8, 1), date(2026, 8, 1), bebes=1)  # bébé né en août
    six_semaines = f.enfants[0].seances[1]
    six_semaines.date_realisee = date(2026, 10, 20)
    session.add(Visite(femme=f, relais_id=relais.id, date=datetime(2026, 10, 7, 10), contexte="grossesse",
                       niveau="conseil"))
    alerte = ouvrir_alerte(session, f.identite, moteur.evaluer(["saignement"]), "grossesse", "ussd")
    alerte.created_at = datetime(2026, 10, 12, 8, 0)
    faire_transition(alerte, "arrivee_confirmee", "sf")
    session.commit()

    totaux = dhis2.indicateurs(session, OCT_DEBUT, OCT_FIN)
    assert totaux["Zone A"]["femmes_inscrites"] == 1
    assert totaux["Zone A"]["cpn_realisees"] == 1  # la CPN de novembre n'est pas comptée
    assert totaux["Zone A"]["vaccins_penta1"] == 1
    assert totaux["Zone A"]["visites_relais"] == 1
    assert totaux["Zone A"]["alertes_urgence"] == 1 and totaux["Zone A"]["urgences_arrivees"] == 1
    assert "accouchements" not in totaux["Zone A"]  # accouchement en août
    assert totaux["Zone B"] == {"femmes_inscrites": 1}


def test_zero_dose_en_fin_de_mois(session):
    f = femme(session)
    enregistrer_accouchement(f, date(2026, 8, 1), date(2026, 8, 1), bebes=1)  # Penta 1 dû le 12 septembre
    f.enfants[0].seances[1].date_realisee = date(2026, 11, 5)  # fait seulement en novembre
    session.commit()
    assert dhis2.indicateurs(session, OCT_DEBUT, OCT_FIN)["Zone A"]["enfants_zero_dose"] == 1
    assert "enfants_zero_dose" not in dhis2.indicateurs(session, date(2026, 11, 1), date(2026, 11, 30)).get("Zone A", {})


def test_un_dossier_efface_compte_toujours_dans_les_totaux(session):
    from app.vie_privee import demander_effacement

    f = femme(session)
    demander_effacement(session, f.identite, datetime(2026, 10, 20), "test")
    session.commit()
    assert dhis2.indicateurs(session, OCT_DEBUT, OCT_FIN)["Zone A"]["femmes_inscrites"] == 1


# ---------- dataValueSet ----------

def test_seules_les_valeurs_reliees_sont_preparees(correspondances):
    totaux = {"Zone A": {"cpn_realisees": 12, "vaccins_penta1": 4, "visites_relais": 30, "appels_debbo": 9},
              "Zone B": {"cpn_realisees": 5}}
    resultat = dhis2.construire(totaux, OCT_DEBUT, correspondances)
    assert resultat["dataValueSet"] == {"dataSet": "dsDebbo0001", "dataValues": [
        {"dataElement": "deCPN000001", "period": "202610", "orgUnit": "ouZoneA0001", "value": "12"},
        {"dataElement": "dePENTA0001", "period": "202610", "orgUnit": "ouZoneA0001", "value": "4",
         "categoryOptionCombo": "cocMoins1an"},
    ]}
    manques = resultat["manques"]
    assert {"type": "zone", "zone": "Zone B", "valeurs_non_envoyees": 1} in manques
    assert any(m["type"] == "element" and m["cle"] == "visites_relais" for m in manques)
    assert not any(m.get("cle") == "appels_debbo" for m in manques)  # indicateur interne, pas pour DHIS2


# ---------- envoi ----------

ENSEMBLE = {"dataValues": [{"dataElement": "deCPN000001", "period": "202610", "orgUnit": "ouZoneA0001", "value": "12"}]}


def faux_dhis2(statut_http=200, corps=None, recus=None):
    def repondre(requete: httpx.Request) -> httpx.Response:
        if recus is not None:
            recus.append(requete)
        return httpx.Response(statut_http, json=corps if corps is not None else {
            "httpStatus": "OK", "status": "OK",
            "response": {"status": "SUCCESS", "importCount": {"imported": 1, "updated": 0, "ignored": 0}}})
    return httpx.MockTransport(repondre)


def test_envoi_au_format_dhis2():
    recus = []
    resume = dhis2.envoyer(ENSEMBLE, url="https://dhis2.exemple.sn/", jeton="d2pat_test", transport=faux_dhis2(recus=recus))
    requete = recus[0]
    assert requete.method == "POST" and str(requete.url) == "https://dhis2.exemple.sn/api/dataValueSets"
    assert requete.headers["authorization"] == "ApiToken d2pat_test"
    assert json.loads(requete.content) == ENSEMBLE
    assert resume == {"statut": "SUCCESS", "compte": {"imported": 1, "updated": 0, "ignored": 0}, "conflits": []}


def test_envoi_ancien_format_de_reponse():
    corps = {"status": "SUCCESS", "importCount": {"imported": 0, "updated": 1}}
    assert dhis2.envoyer(ENSEMBLE, url="https://d", jeton="j", transport=faux_dhis2(corps=corps))["statut"] == "SUCCESS"


@pytest.mark.parametrize(("statut_http", "corps", "message"), [
    (409, {"status": "ERROR", "response": {"status": "ERROR", "conflicts": [{"value": "Élément inconnu"}]}}, "Élément inconnu"),
    (401, {}, "jeton"),
])
def test_envoi_refuse(statut_http, corps, message):
    with pytest.raises(dhis2.ErreurDhis2, match=message):
        dhis2.envoyer(ENSEMBLE, url="https://d", jeton="j", transport=faux_dhis2(statut_http, corps))


def test_dhis2_injoignable():
    def coupure(requete):
        raise httpx.ConnectError("réseau coupé")
    with pytest.raises(dhis2.ErreurDhis2, match="injoignable"):
        dhis2.envoyer(ENSEMBLE, url="https://d", jeton="j", transport=httpx.MockTransport(coupure))


def test_rien_a_envoyer_ou_non_configure(monkeypatch):
    with pytest.raises(dhis2.ErreurDhis2, match="pas configuré"):
        dhis2.envoyer(ENSEMBLE)
    with pytest.raises(dhis2.ErreurDhis2, match="Aucune valeur"):
        dhis2.envoyer({"dataValues": []}, url="https://d", jeton="j")


def test_chaque_envoi_est_trace(session, correspondances, monkeypatch):
    monkeypatch.setattr(config, "DHIS2_URL", "https://dhis2.exemple.sn")
    monkeypatch.setattr(config, "DHIS2_JETON", "d2pat_test")
    f = femme(session)
    f.contacts.append(Contact(type="CPN1", date_prevue=date(2026, 10, 3), date_realisee=date(2026, 10, 3)))
    session.commit()
    assert dhis2.envoyer_periode(session, "2026-10", None, transport=faux_dhis2()).statut == "ok"
    assert dhis2.envoyer_periode(session, "2026-10", None, transport=faux_dhis2(500, {"status": "ERROR"})).statut == "erreur"
    assert [e.statut for e in session.scalars(select(EnvoiDhis2).order_by(EnvoiDhis2.id))] == ["ok", "erreur"]


# ---------- API ----------

def test_apercu_reserve_au_district(session, sf, district, correspondances):
    femme(session)
    assert sf.get("/api/dhis2/apercu?periode=2026-10").status_code == 403
    apercu = district.get("/api/dhis2/apercu?periode=2026-10").json()
    assert apercu["totaux"]["Zone A"]["femmes_inscrites"] == 1
    assert apercu["zones"] == {"Zone A": True}
    assert apercu["configure"] is False
    assert district.get("/api/dhis2/apercu?periode=2026-99").status_code == 422


def test_envoi_depuis_le_tableau_de_bord(session, district, correspondances, monkeypatch):
    monkeypatch.setattr(dhis2, "envoyer", lambda ensemble, **_: {"statut": "SUCCESS", "compte": {}, "conflits": []})
    f = femme(session)
    f.contacts.append(Contact(type="CPN1", date_prevue=date(2026, 10, 3), date_realisee=date(2026, 10, 3)))
    session.commit()
    reponse = district.post("/api/dhis2/envoyer", json={"periode": "2026-10"}).json()
    assert reponse["statut"] == "ok" and reponse["nb_valeurs"] == 1
    assert "dhis2_envoi_ok" in set(session.scalars(select(JournalAcces.action)))
