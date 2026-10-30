from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Alerte, Femme, Visite
from tests.conftest import connecter_relais, creer_compte_relais

CODE = "R-DEMO"


@pytest.fixture
def relais(session):
    return creer_compte_relais(session, CODE, "Zone A", telephone_sf="+221770000900")


@pytest.fixture
def client(relais):
    """Ici, le client est connecté comme la relais R-DEMO."""
    return connecter_relais(CODE)


def sync(client, *operations):
    reponse = client.post("/api/relais/moi/sync", json={"operations": list(operations)})
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def op(op_id, type_, **donnees):
    return {"op_id": op_id, "type": type_, "cree_le": None, "donnees": donnees}


def inscription(op_id="op-1", uid="uid-1", **extra):
    return op(op_id, "inscription", uid=uid, nom="Femme test", mois=4, consentement=True, **extra)


def test_sans_connexion_pas_de_dossier(relais):
    from tests.conftest import nouveau_client

    assert nouveau_client().get("/api/relais/moi/dossier").status_code == 401


def test_dossier_contient_les_regles(client, relais):
    dossier = client.get("/api/relais/moi/dossier").json()
    assert dossier["relais"]["zone"] == "Zone A"
    assert "saignement" in dossier["regles"]["signes"]


def test_inscription_puis_visite_dans_le_meme_lot(client, session, relais):
    reponse = sync(
        client,
        inscription(),
        op("op-2", "visite", femme={"uid": "uid-1"}, contexte="grossesse", signes=["vomissements_persistants"]),
    )
    statuts = [r["statut"] for r in reponse["resultats"]]
    assert statuts == ["ok", "ok"]
    assert reponse["resultats"][1]["niveau"] == "consultation_24h"
    femme = session.scalar(select(Femme))
    assert femme.identite.telephone is None
    assert femme.relais_id == relais.id
    assert reponse["dossier"]["femmes"][0]["uid"] == "uid-1"


def test_un_lot_renvoye_n_est_applique_qu_une_fois(client, session, relais):
    sync(client, inscription())
    reponse = sync(client, inscription())
    assert reponse["resultats"][0]["statut"] == "deja_recu"
    assert len(session.scalars(select(Femme)).all()) == 1


def test_sans_consentement_l_inscription_est_refusee(client, session, relais):
    reponse = sync(client, op("op-1", "inscription", uid="u", nom="X", mois=3, consentement=False))
    assert reponse["resultats"][0]["statut"] == "erreur"
    assert session.scalar(select(Femme)) is None


def test_visite_urgente_ouvre_une_alerte_a_l_heure_du_telephone(client, session, relais):
    sync(client, inscription())
    heure = "2026-01-15T08:30:00Z"
    sync(client, {"op_id": "op-2", "type": "visite", "cree_le": heure,
                  "donnees": {"femme": {"uid": "uid-1"}, "contexte": "grossesse", "signes": ["saignement"]}})
    alerte = session.scalar(select(Alerte))
    assert alerte.niveau == "urgence" and alerte.canal == "relais"
    assert alerte.created_at.isoformat() == "2026-01-15T08:30:00"
    assert alerte.relais.code == CODE


def test_le_serveur_reevalue_les_signes(client, session, relais):
    sync(client, inscription())
    reponse = sync(client, op("op-2", "visite", femme={"uid": "uid-1"}, contexte="grossesse",
                              signes=["saignement"], niveau="conseil"))
    assert reponse["resultats"][0]["niveau"] == "urgence"


def test_perimetre_brachial_rouge(client, session, relais):
    sync(client, inscription())
    reponse = sync(client, op("op-2", "visite", femme={"uid": "uid-1"}, contexte="enfant", signes=[], pb_mm=108))
    assert reponse["resultats"][0]["niveau"] == "consultation_24h"
    assert session.scalar(select(Visite)).signes == ["pb_rouge"]


def test_alerte_pour_une_femme_non_inscrite(client, session, relais):
    reponse = sync(client, op("op-1", "alerte", femme=None, contexte="grossesse", signes=["convulsions"]))
    assert reponse["resultats"][0]["niveau"] == "urgence"
    assert session.scalar(select(Alerte)).identite.nom.startswith("Non inscrite")


def test_femme_d_une_autre_zone_refusee(client, session, relais):
    creer_compte_relais(session, "R-AUTRE", "Zone B")
    sync(client, inscription())
    reponse = connecter_relais("R-AUTRE").post("/api/relais/moi/sync", json={"operations": [
        op("op-x", "visite", femme={"uid": "uid-1"}, contexte="grossesse", signes=[])]}).json()
    assert reponse["resultats"][0]["statut"] == "erreur"
    assert reponse["dossier"]["femmes"] == []


def test_accouchement_passe_en_post_partum(client, session, relais):
    sync(client, inscription())
    hier = (date.today() - timedelta(days=1)).isoformat()
    sync(client, op("op-2", "accouchement", femme={"uid": "uid-1"}, date=hier))
    femme = session.scalar(select(Femme))
    assert femme.statut == "postpartum"
    assert [c.type for c in femme.contacts] == ["PN1", "PN2", "PN3", "PN4"]
