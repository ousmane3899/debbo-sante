// Banc de scénarios pour la copie JavaScript du moteur clinique : node --test tests/js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { evaluer, questions, signePb } from "../../app/static/relais/moteur.js";

const lire = (chemin) => JSON.parse(readFileSync(new URL(chemin, import.meta.url), "utf-8"));
const regles = lire("../../app/clinical/regles.json");
const scenarios = lire("../scenarios/critiques.json");

for (const s of scenarios) {
  test(`scénario ${s.id}`, () => {
    assert.equal(evaluer(regles, s.signes, s.contexte, s.pb_mm ?? null).niveau, s.attendu);
  });
}

test("seuils du périmètre brachial", () => {
  assert.equal(signePb(regles, 114), "pb_rouge");
  assert.equal(signePb(regles, 115), "pb_jaune");
  assert.equal(signePb(regles, 125), null);
});

test("la visite guidée pose d'abord les signes urgents", () => {
  const q = questions(regles, "grossesse");
  assert.equal(q[0].niveau, "urgence");
  assert.ok(!q.some((x) => x.code === "urgence_declaree"));
  assert.equal(q.at(-1).niveau, "consultation_24h");
});
