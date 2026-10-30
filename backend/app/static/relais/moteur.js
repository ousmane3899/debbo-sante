// Copie hors-ligne du moteur clinique (backend/app/clinical/engine.py).
// Elle lit les mêmes règles, reçues à la synchronisation, et passe le même banc
// de scénarios (tests/js/moteur.test.mjs). Le serveur réévalue chaque visite et fait foi.

export function rang(regles, niveau) {
  return regles.niveaux.indexOf(niveau);
}

function plusGrave(regles, a, b) {
  return rang(regles, a) >= rang(regles, b) ? a : b;
}

export function signePb(regles, pbMm) {
  if (pbMm == null || Number.isNaN(pbMm)) return null;
  if (pbMm < regles.seuils_pb_mm.rouge_sous) return "pb_rouge";
  if (pbMm < regles.seuils_pb_mm.jaune_sous) return "pb_jaune";
  return null;
}

export function evaluer(regles, signes, contexte = "grossesse", pbMm = null) {
  let niveau = regles.niveaux[0];
  const retenus = [];
  const inconnus = [];
  const mesure = signePb(regles, pbMm);
  for (const code of new Set([...signes, ...(mesure ? [mesure] : [])])) {
    const signe = regles.signes[code];
    if (!signe || !signe.contextes.includes(contexte)) {
      inconnus.push(code);
      niveau = plusGrave(regles, niveau, regles.niveau_signe_inconnu);
      continue;
    }
    retenus.push(code);
    niveau = plusGrave(regles, niveau, signe.niveau);
  }
  return { niveau, signesRetenus: retenus, signesInconnus: inconnus, version: regles.version };
}

// Questions posées pendant une visite guidée : les signes du contexte, les plus graves d'abord.
export function questions(regles, contexte) {
  return Object.entries(regles.signes)
    .filter(([code, s]) => s.contextes.includes(contexte) && !s.mesure && !code.endsWith("_declare") && code !== "urgence_declaree")
    .sort(([, a], [, b]) => rang(regles, b.niveau) - rang(regles, a.niveau))
    .map(([code, s]) => ({ code, libelle: s.libelle, niveau: s.niveau }));
}
