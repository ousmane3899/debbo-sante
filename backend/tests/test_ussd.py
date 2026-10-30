from datetime import date

from sqlalchemy import select

from app.models import Alerte, DemandeRappel, Femme, Identite
from app.ussd import repondre

NUMERO = "+221770000001"


def parcours(session, *saisies, numero=NUMERO, jour=None):
    return repondre(session, numero, "*".join(saisies), aujourdhui=jour)


def test_nouvelle_utilisatrice_choisit_la_langue_d_abord(session):
    assert parcours(session).startswith("CON Debbo Sante\n1 Pulaar")
    assert parcours(session, "3").startswith("CON Debbo Sante\n1 Urgence")
    # Rien n'est enregistré tant que la session n'est pas terminée.
    assert session.scalar(select(Identite)) is None


def test_urgence_ouvre_une_alerte_sans_detour(session):
    assert parcours(session, "3", "1").startswith("CON URGENCE")
    reponse = parcours(session, "3", "1", "1")
    assert reponse.startswith("END Alerte envoyee")

    alerte = session.scalar(select(Alerte))
    assert alerte.niveau == "urgence"
    assert alerte.etat == "ouverte"
    assert "saignement" in alerte.signes
    assert alerte.identite.langue == "fr"


def test_toute_reponse_du_menu_urgence_ouvre_une_alerte(session):
    parcours(session, "3", "1", "x")
    alerte = session.scalar(select(Alerte))
    assert alerte.niveau == "urgence"
    assert alerte.signes == ["urgence_declaree"]


def test_bebe_malade_est_en_contexte_nouveau_ne(session):
    parcours(session, "3", "1", "8")
    assert session.scalar(select(Alerte)).contexte == "nouveau_ne"


def test_utilisatrice_connue_va_directement_au_menu(session):
    parcours(session, "3", "5", "1")  # première session : conseils, langue enregistrée
    assert parcours(session).startswith("CON Debbo Sante\n1 Urgence")
    assert parcours(session, "1").startswith("CON URGENCE")


def test_inscription_demande_le_consentement_et_planifie(session):
    jour = date(2026, 10, 30)
    assert "J'accepte" in parcours(session, "3", "2")
    assert parcours(session, "3", "2", "1").startswith("CON Depuis combien de mois")
    reponse = parcours(session, "3", "2", "1", "3", jour=jour)
    assert reponse.startswith("END Merci, vous etes inscrite")

    femme = session.scalar(select(Femme))
    assert femme.identite.consentement_service
    assert [c.type for c in femme.contacts][:2] == ["CPN-R", "CPN2"]

    assert parcours(session, "3", jour=jour).startswith("END Prochaine consultation")
    assert parcours(session, "2").startswith("END Vous etes deja inscrite")


def test_refus_du_consentement_n_enregistre_aucune_femme(session):
    assert parcours(session, "3", "2", "2").startswith("END Nous ne gardons rien")
    assert session.scalar(select(Femme)) is None


def test_mois_invalide(session):
    assert parcours(session, "3", "2", "1", "12").startswith("END Reponse non comprise")
    assert session.scalar(select(Femme)) is None


def test_parler_a_une_sage_femme_est_prioritaire(session):
    parcours(session, "3", "0")
    demande = session.scalar(select(DemandeRappel))
    assert demande.prioritaire and demande.motif == "sage_femme"


def test_point_d_entree_http(client):
    reponse = client.post("/ussd", data={"sessionId": "s1", "serviceCode": "*1#", "phoneNumber": NUMERO, "text": ""})
    assert reponse.status_code == 200
    assert reponse.text.startswith("CON ")
