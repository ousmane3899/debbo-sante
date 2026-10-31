from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import AppelSortant, Femme, Identite, SeanceVaccin, TacheRelais
from app.planning import PEV, enregistrer_accouchement, penta3_a_jour, zero_dose
from app.rappels import executer, planifier
from tests.conftest import connecter_relais, creer_compte_relais

AUJOURDHUI = date(2026, 11, 2)
MIDI = datetime(2026, 11, 2, 12, 0)


class FournisseurFaux:
    def __init__(self):
        self.appels = []

    def appeler(self, telephone, message, langue):
        self.appels.append(message)
        return f"ref-{len(self.appels)}"


def mere(session, ne_il_y_a=0, bebes=1, telephone="+221770000001", zone="Zone A"):
    f = Femme(identite=Identite(nom="Mère test", telephone=telephone, consentement_service=True), zone=zone)
    enregistrer_accouchement(f, AUJOURDHUI - timedelta(days=ne_il_y_a), AUJOURDHUI - timedelta(days=ne_il_y_a), bebes)
    session.add(f)
    session.commit()
    return f


def suivi_mere_fait(f, jusqu_a=None):
    """Marque faites les visites postnatales de la mère (celles d'avant `jusqu_a`, ou toutes)."""
    for c in f.contacts:
        if jusqu_a is None or c.date_prevue < jusqu_a:
            c.date_realisee = c.date_prevue


def faire(f, *cles):
    for e in f.enfants:
        for s in e.seances:
            if s.cle in cles:
                s.date_realisee = s.date_prevue


# ---------- calendrier ----------

def test_chaque_bebe_recoit_le_calendrier_complet(session):
    f = mere(session, bebes=2)
    assert [e.rang for e in f.enfants] == [1, 2]
    assert [s.cle for s in f.enfants[0].seances] == [s["cle"] for s in PEV["seances"]]
    six_semaines = f.enfants[0].seances[1]
    assert six_semaines.date_prevue == AUJOURDHUI + timedelta(days=42)
    assert "Penta 1" in six_semaines.antigenes


def test_aucun_bebe_vivant_aucun_vaccin(session):
    f = mere(session, bebes=0)
    assert f.enfants == [] and f.statut == "postpartum"


def test_accouchement_enregistre_une_seule_fois(session):
    f = mere(session)
    with pytest.raises(ValueError):
        enregistrer_accouchement(f, AUJOURDHUI, AUJOURDHUI)


@pytest.mark.parametrize(("age_jours", "penta1_fait", "attendu"), [
    (50, False, False),  # 6 semaines dépassées de moins de 14 jours : pas encore zéro dose
    (60, False, True),
    (60, True, False),
])
def test_zero_dose(session, age_jours, penta1_fait, attendu):
    f = mere(session, ne_il_y_a=age_jours)
    if penta1_fait:
        faire(f, "6sem")
    assert zero_dose(f.enfants[0], AUJOURDHUI) is attendu


def test_penta3_compte_seulement_les_enfants_assez_ages(session):
    jeune = mere(session, ne_il_y_a=60, telephone="+221770000001")
    age = mere(session, ne_il_y_a=130, telephone="+221770000002")
    assert penta3_a_jour(jeune.enfants[0], AUJOURDHUI) is None
    assert penta3_a_jour(age.enfants[0], AUJOURDHUI) is False
    faire(age, "14sem")
    assert penta3_a_jour(age.enfants[0], AUJOURDHUI) is True


# ---------- « Debbo m'appelle » pour les vaccins ----------

def test_appel_la_veille_des_vaccins(session):
    f = mere(session, ne_il_y_a=41)  # séance des 6 semaines demain
    faire(f, "naissance")
    suivi_mere_fait(f)
    session.commit()
    fournisseur = FournisseurFaux()
    assert planifier(session, AUJOURDHUI) == {"appels": 1, "taches": 0}
    executer(session, MIDI, fournisseur)
    assert fournisseur.appels == ["appel_veille_vaccin"]
    assert planifier(session, AUJOURDHUI) == {"appels": 0, "taches": 0}


def test_un_seul_appel_quand_visite_postnatale_et_vaccins_tombent_le_meme_jour(session):
    f = mere(session, ne_il_y_a=41)  # visite postnatale des 6 semaines ET vaccins des 6 semaines demain
    faire(f, "naissance")
    suivi_mere_fait(f, jusqu_a=AUJOURDHUI)
    session.commit()
    assert planifier(session, AUJOURDHUI) == {"appels": 1, "taches": 0}


def test_sans_telephone_la_relais_rappelle_les_vaccins(session):
    f = mere(session, ne_il_y_a=41, telephone=None)
    faire(f, "naissance")
    suivi_mere_fait(f, jusqu_a=AUJOURDHUI)
    session.commit()
    planifier(session, AUJOURDHUI)
    motifs = set(session.scalars(select(TacheRelais.motif)))
    assert motifs == {"rappel_sans_telephone", "rappel_vaccin_sans_telephone"}


def test_jumeaux_en_retard_un_appel_pour_la_mere_une_visite_par_enfant(session):
    f = mere(session, ne_il_y_a=120, bebes=2)  # séances jusqu'aux 14 semaines en retard pour chacun des jumeaux
    suivi_mere_fait(f)
    session.commit()
    assert planifier(session, AUJOURDHUI) == {"appels": 1, "taches": 2}
    assert planifier(session, AUJOURDHUI) == {"appels": 0, "taches": 0}
    taches = session.scalars(select(TacheRelais)).all()
    assert {t.seance.cle for t in taches} == {"14sem"}  # la plus récente des séances manquées


def test_appel_annule_si_l_enfant_est_deja_vaccine(session):
    f = mere(session, ne_il_y_a=41)
    faire(f, "naissance")
    suivi_mere_fait(f)
    session.commit()
    planifier(session, AUJOURDHUI)
    faire(f, "6sem")
    executer(session, MIDI, FournisseurFaux())
    assert session.scalar(select(AppelSortant.statut)) == "annule"


# ---------- appli relais ----------

def test_relais_enregistre_accouchement_et_vaccins(session):
    creer_compte_relais(session, "R-1", "Zone A")
    client = connecter_relais("R-1")
    hier = (date.today() - timedelta(days=1)).isoformat()
    lot = [
        {"op_id": "i", "type": "inscription", "donnees": {"uid": "u1", "nom": "Mère", "mois": 9, "consentement": True}},
        {"op_id": "a", "type": "accouchement", "donnees": {"femme": {"uid": "u1"}, "date": hier, "bebes": 2}},
    ]
    reponse = client.post("/api/relais/moi/sync", json={"operations": lot}).json()
    assert len(reponse["resultats"][1]["enfants"]) == 2
    enfant = reponse["dossier"]["femmes"][0]["enfants"][0]
    seance_id = enfant["prochaine_seance"]["id"]
    assert enfant["prochaine_seance"]["nom"] == "Naissance"

    client.post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "v", "type": "vaccin_fait", "donnees": {"seance_id": seance_id}}]})
    assert session.get(SeanceVaccin, seance_id).date_realisee == date.today()


def test_relais_d_une_autre_zone_ne_peut_pas_vacciner(session):
    f = mere(session)
    creer_compte_relais(session, "R-B", "Zone B")
    seance_id = f.enfants[0].seances[0].id
    reponse = connecter_relais("R-B").post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "v", "type": "vaccin_fait", "donnees": {"seance_id": seance_id}}]}).json()
    assert reponse["resultats"][0]["statut"] == "erreur"


# ---------- poste de santé ----------

def test_liste_des_vaccinations_et_indicateurs(session, sf):
    mere(session, ne_il_y_a=60, telephone="+221770000001")  # zéro dose
    mere(session, ne_il_y_a=60, telephone="+221770000002", zone="Zone B")  # hors de l'aire de la sage-femme
    liste = sf.get("/api/vaccinations").json()
    assert {v["seance"] for v in liste} == {"Naissance", "6 semaines"}
    assert all(v["en_retard"] for v in liste)

    indicateurs = sf.get("/api/indicateurs").json()
    assert indicateurs["enfants_suivis"] == 1 and indicateurs["enfants_zero_dose"] == 1

    six_semaines = next(v for v in liste if v["seance"] == "6 semaines")
    assert sf.post(f"/api/vaccinations/{six_semaines['id']}/fait").status_code == 200
    assert sf.get("/api/indicateurs").json()["enfants_zero_dose"] == 0


def test_une_seance_d_une_autre_zone_est_introuvable(session, sf):
    f = mere(session, zone="Zone B")
    assert sf.post(f"/api/vaccinations/{f.enfants[0].seances[0].id}/fait").status_code == 404
