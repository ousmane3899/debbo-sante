from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.clinical import moteur
from app.models import Alerte, AppelSortant, Femme, Identite, JournalAcces, Visite, maintenant
from app.planning import planifier_grossesse
from app.rappels import planifier
from app.referral import faire_transition, ouvrir_alerte
from app.ussd import repondre
from app.vie_privee import demander_effacement, pseudonyme_recherche, traiter_effacements_en_attente
from tests.conftest import connecter, connecter_relais, creer_compte, creer_compte_relais

NUMERO = "+221770000001"


def ussd(session, *saisies, numero=NUMERO):
    return repondre(session, numero, "*".join(saisies))


def inscrite_par_ussd(session, numero=NUMERO):
    assert ussd(session, "3", "2", "1", "4", numero=numero).startswith("END Merci")
    return session.scalar(select(Identite).where(Identite.telephone == numero))


def femme(session, zone="Zone A", recherche=False, telephone=NUMERO, nom="Femme test"):
    f = Femme(identite=Identite(nom=nom, telephone=telephone, consentement_service=True,
                                consentement_recherche=recherche), zone=zone)
    planifier_grossesse(f, 4, date.today())
    session.add(f)
    session.commit()
    return f


# ---------- consentement ----------

def test_inscription_ussd_consentement_au_service_seulement(session):
    identite = inscrite_par_ussd(session)
    assert identite.consentement_service and identite.consentement_par == "ussd"
    assert not identite.consentement_recherche and not identite.consentement_ia
    assert identite.consentement_at is not None


def test_inscription_par_la_relais_avec_les_choix_facultatifs(session):
    creer_compte_relais(session, "R-1", "Zone A")
    connecter_relais("R-1").post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "i", "type": "inscription", "donnees": {"uid": "u", "nom": "X", "mois": 3, "consentement": True,
                                                         "consentement_recherche": True, "consentement_ia": False}}]})
    identite = session.scalar(select(Identite))
    assert identite.consentement_recherche and not identite.consentement_ia
    assert identite.consentement_par == "relais:R-1"


def test_changer_son_choix_pour_la_recherche_par_ussd(session):
    identite = inscrite_par_ussd(session)
    assert "recherche" in ussd(session, "6", "2")
    assert ussd(session, "6", "2", "1").startswith("END Votre choix est enregistre")
    session.refresh(identite)
    assert identite.consentement_recherche and identite.consentement_service


# ---------- effacement ----------

def test_effacement_par_ussd(session):
    identite = inscrite_par_ussd(session)
    code = identite.femme.code
    assert ussd(session, "6", "1").startswith("CON Effacer toutes vos donnees")
    assert ussd(session, "6", "1", "2").startswith("END Rien n'a ete efface")
    assert ussd(session, "6", "1", "1").startswith("END C'est fait")

    session.expire_all()
    f = session.scalar(select(Femme).where(Femme.code == code))
    assert f.statut == "effacee"
    assert (f.identite.nom, f.identite.telephone, f.identite.langue) == (None, None, None)
    assert f.contacts  # le suivi passé reste, sans identité, pour les statistiques
    assert {"effacement_demande", "effacement_effectue"} <= set(session.scalars(select(JournalAcces.action)))
    # Le même numéro redevient un numéro inconnu.
    assert ussd(session).startswith("CON Debbo Sante\n1 Pulaar")


def test_effacement_d_un_numero_inconnu_n_enregistre_rien(session):
    assert ussd(session, "3", "6", "1", "1").startswith("END Debbo n'a aucune donnee")
    assert session.scalar(select(Identite)) is None


def test_les_notes_libres_partent_avec_l_identite(session):
    f = femme(session)
    relais = creer_compte_relais(session, "R-1", "Zone A")
    session.add(Visite(femme=f, relais_id=relais.id, date=maintenant(), contexte="grossesse", niveau="conseil",
                       note="Vue chez sa tante Fatou"))
    alerte = ouvrir_alerte(session, f.identite, moteur.evaluer(["saignement"]), "grossesse", "ussd")
    faire_transition(alerte, "cloturee", "sf", note="Rappelée par son mari Moussa")
    session.commit()
    demander_effacement(session, f.identite, maintenant(), "test")
    session.commit()
    assert session.scalar(select(Visite.note)) is None
    assert all(e.note is None for e in alerte.evenements)
    assert session.scalar(select(Alerte.niveau)) == "urgence"  # l'événement de santé reste


def test_pendant_une_urgence_l_effacement_attend_mais_les_rappels_s_arretent(session):
    f = femme(session)
    f.contacts[0].date_prevue = date.today() + timedelta(days=1)
    alerte = ouvrir_alerte(session, f.identite, moteur.evaluer(["saignement"]), "grossesse", "ussd")
    session.commit()

    assert demander_effacement(session, f.identite, maintenant(), "test") == "en_attente"
    session.commit()
    assert f.identite.telephone == NUMERO  # la sage-femme doit pouvoir la rappeler
    assert planifier(session, date.today()) == {"appels": 0, "taches": 0}
    assert traiter_effacements_en_attente(session, maintenant()) == 0

    faire_transition(alerte, "arrivee_confirmee", "sf")
    faire_transition(alerte, "cloturee", "sf")
    session.commit()
    assert traiter_effacements_en_attente(session, maintenant()) == 1
    assert f.identite.telephone is None and f.statut == "effacee"


def test_les_appels_prevus_sont_annules(session):
    f = femme(session)
    f.contacts[0].date_prevue = date.today() + timedelta(days=1)
    session.commit()
    planifier(session, date.today())
    demander_effacement(session, f.identite, maintenant(), "test")
    session.commit()
    assert session.scalar(select(AppelSortant.statut)) == "annule"


def test_une_femme_effacee_disparait_des_listes(session, sf):
    creer_compte_relais(session, "R-1", "Zone A")
    f = femme(session)
    relais = connecter_relais("R-1")
    assert len(relais.get("/api/relais/moi/dossier").json()["femmes"]) == 1

    reponse = relais.post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "e", "type": "effacement", "donnees": {"femme": {"code": f.code}}}]}).json()
    assert reponse["resultats"][0]["effacement"] == "efface"
    assert reponse["dossier"]["femmes"] == []
    assert sf.get("/api/femmes").json() == []
    # Une visite saisie hors ligne avant l'effacement est refusée à l'envoi.
    refus = relais.post("/api/relais/moi/sync", json={"operations": [
        {"op_id": "v", "type": "visite", "donnees": {"femme": {"code": f.code}, "contexte": "grossesse", "signes": []}}]}).json()
    assert refus["resultats"][0]["statut"] == "erreur"


def test_effacement_saisi_au_poste_par_l_admin(session):
    creer_compte(session, "admin", "admin")
    f = femme(session)
    reponse = connecter("admin").post(f"/api/admin/femmes/{f.code}/effacer").json()
    assert reponse == {"code": f.code, "effacement": "efface"}


# ---------- jeu de données de recherche ----------

def test_export_recherche_seulement_les_femmes_d_accord_et_sans_identite(session, district):
    oui = femme(session, recherche=True, telephone="+221770000001", nom="Aminata Sow")
    femme(session, recherche=False, telephone="+221770000002")
    export = district.get("/api/recherche/export").json()
    assert len(export["lignes"]) == 1
    ligne = export["lignes"][0]
    assert ligne["id"] == pseudonyme_recherche(oui.id) and ligne["id"] != oui.code
    texte = str(export)
    assert "Aminata" not in texte and "+22177" not in texte and oui.code not in texte
    assert ligne["zone"] == "autre"  # moins de 10 femmes dans la zone : elle n'est pas nommée


def test_export_recherche_nomme_les_zones_assez_grandes(session, district):
    for i in range(10):
        femme(session, recherche=True, telephone=f"+2217700001{i:02d}")
    assert {l["zone"] for l in district.get("/api/recherche/export").json()["lignes"]} == {"Zone A"}


def test_export_recherche_reserve_au_district_et_trace(session, sf, district):
    assert sf.get("/api/recherche/export").status_code == 403
    district.get("/api/recherche/export")
    assert "export_recherche" in set(session.scalars(select(JournalAcces.action)))
