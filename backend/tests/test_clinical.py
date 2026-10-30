"""Banc de scénarios du moteur clinique.

Cible du cahier des charges : 100 % des scénarios critiques bien classés.
Chaque modification de `regles.json` doit repasser ce banc.
"""

import json
from pathlib import Path

import pytest

from app.clinical import moteur

SCENARIOS = json.loads((Path(__file__).parent / "scenarios" / "critiques.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario(scenario):
    evaluation = moteur.evaluer(scenario["signes"], scenario["contexte"], scenario.get("pb_mm"))
    assert evaluation.niveau == scenario["attendu"]


@pytest.mark.parametrize(("pb_mm", "signe"), [(114, "pb_rouge"), (115, "pb_jaune"), (124, "pb_jaune"), (125, None)])
def test_seuils_du_perimetre_brachial(pb_mm, signe):
    assert moteur.signe_pb(pb_mm) == signe


def test_chaque_signe_urgent_a_un_scenario():
    couverts = {code for s in SCENARIOS if s["attendu"] == "urgence" for code in s["signes"]}
    urgents = {code for code, signe in moteur.signes.items() if signe["niveau"] == "urgence"}
    assert urgents <= couverts, f"Signes urgents sans scénario : {sorted(urgents - couverts)}"


@pytest.mark.parametrize("niveau_ia", ["conseil", "consultation_24h", None, "niveau_invente"])
def test_ia_ne_peut_pas_abaisser_une_urgence(niveau_ia):
    evaluation = moteur.evaluer(["saignement"], "grossesse")
    assert moteur.avec_suggestion_ia(evaluation, niveau_ia).niveau == "urgence"


def test_ia_peut_relever_un_niveau():
    evaluation = moteur.evaluer(["vomissements_persistants"], "grossesse")
    assert moteur.avec_suggestion_ia(evaluation, "urgence").niveau == "urgence"


def test_signe_inconnu_est_signale():
    evaluation = moteur.evaluer(["inconnu"], "grossesse")
    assert evaluation.signes_inconnus == ("inconnu",)
    assert evaluation.signes_retenus == ()
