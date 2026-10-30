from datetime import timedelta

import pytest

from app.clinical import moteur
from app.models import Identite
from app.referral import TransitionInvalide, doit_escalader, faire_transition, ouvrir_alerte


@pytest.fixture
def alerte(session):
    identite = Identite(telephone="+221770000002", langue="fr")
    a = ouvrir_alerte(session, identite, moteur.evaluer(["saignement"]), "grossesse", "ussd")
    session.commit()
    return a


def types(alerte):
    return [e.type for e in alerte.evenements]


def test_parcours_complet_declenche_le_paiement_du_transport(alerte):
    faire_transition(alerte, "sf_jointe", "sf")
    faire_transition(alerte, "transport_parti", "sf", transporteur="Moussa, charrette")
    faire_transition(alerte, "arrivee_confirmee", "poste", structure="Poste de santé")
    assert alerte.arrivee_at is not None
    assert "paiement_transport_declenche" in types(alerte)


def test_alerte_utilisable_avant_enregistrement(session):
    identite = Identite(telephone="+221770000003")
    a = ouvrir_alerte(session, identite, moteur.evaluer(["saignement"]), "grossesse", "ussd")
    faire_transition(a, "sf_jointe", "sf")
    assert a.etat == "sf_jointe"


def test_arrivee_sans_transport_ne_paie_personne(alerte):
    faire_transition(alerte, "arrivee_confirmee", "poste")
    assert "paiement_transport_declenche" not in types(alerte)


def test_on_ne_saute_pas_en_arriere(alerte):
    faire_transition(alerte, "sf_jointe", "sf")
    with pytest.raises(TransitionInvalide):
        faire_transition(alerte, "ouverte", "sf")


def test_transport_exige_un_transporteur(alerte):
    faire_transition(alerte, "sf_jointe", "sf")
    with pytest.raises(TransitionInvalide):
        faire_transition(alerte, "transport_parti", "sf")


def test_cloture_sans_arrivee_exige_un_motif(alerte):
    with pytest.raises(TransitionInvalide):
        faire_transition(alerte, "cloturee", "sf")
    faire_transition(alerte, "cloturee", "sf", note="fausse alerte, rappelée")
    assert alerte.etat == "cloturee"


def test_escalade_apres_cinq_minutes_sans_rappel(alerte):
    assert not doit_escalader(alerte, alerte.created_at + timedelta(minutes=4))
    assert doit_escalader(alerte, alerte.created_at + timedelta(minutes=6))
    faire_transition(alerte, "sf_jointe", "sf")
    assert not doit_escalader(alerte, alerte.created_at + timedelta(minutes=6))


def test_api_refuse_une_transition_invalide(sf, alerte):
    reponse = sf.post(f"/api/alertes/{alerte.id}/transition", json={"etat": "transport_parti"})
    assert reponse.status_code == 409


def test_api_liste_les_alertes_actives(sf, alerte):
    donnees = sf.get("/api/alertes").json()
    assert [a["id"] for a in donnees] == [alerte.id]
    assert donnees[0]["signes"][0]["libelle"] == "Saignement vaginal"


def test_l_acteur_est_la_personne_connectee(sf, alerte):
    donnees = sf.post(f"/api/alertes/{alerte.id}/transition", json={"etat": "sf_jointe"}).json()
    assert donnees["evenements"][-1]["acteur"] == "sf.a (sf.a)"
