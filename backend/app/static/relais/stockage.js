// Magasin clé-valeur sur IndexedDB, chiffré.
// Les données (noms, visites, file d'envoi) sont chiffrées en AES-GCM avec une clé
// dérivée du PIN de la relais (PBKDF2-SHA-256). Sans le PIN, un téléphone perdu ne
// montre rien. Seuls le code relais, le « verrou » et le compteur d'essais sont en clair.
//
// Limite connue : un PIN à 6 chiffres reste devinable par force brute si quelqu'un
// copie la base hors du téléphone. Le chiffrement de l'appareil (Android) et un
// verrouillage d'écran restent nécessaires.

const BASE = "debbo-relais";
const TABLE = "kv";
const ITERATIONS = 310000;
const TEMOIN = "debbo-relais";
const EN_CLAIR = new Set(["code", "verrou", "essais"]);

let cle = null; // clé AES en mémoire seulement, effacée au verrouillage
let ouverture = null;

function ouvrir() {
  ouverture ??= new Promise((resoudre, rejeter) => {
    const requete = indexedDB.open(BASE, 1);
    requete.onupgradeneeded = () => requete.result.createObjectStore(TABLE);
    requete.onsuccess = () => resoudre(requete.result);
    requete.onerror = () => rejeter(requete.error);
  });
  return ouverture;
}

async function transaction(mode, action) {
  const base = await ouvrir();
  return new Promise((resoudre, rejeter) => {
    const requete = action(base.transaction(TABLE, mode).objectStore(TABLE));
    requete.onsuccess = () => resoudre(requete.result);
    requete.onerror = () => rejeter(requete.error);
  });
}

const brut = { lire: (c) => transaction("readonly", (t) => t.get(c)), ecrire: (c, v) => transaction("readwrite", (t) => t.put(v, c)) };

async function deriver(pin, sel) {
  const materiau = await crypto.subtle.importKey("raw", new TextEncoder().encode(pin), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: sel, iterations: ITERATIONS, hash: "SHA-256" },
    materiau,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function chiffrer(cleAes, valeur) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const donnees = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, cleAes, new TextEncoder().encode(JSON.stringify(valeur)));
  return { iv, donnees };
}

async function dechiffrer(cleAes, { iv, donnees }) {
  const clair = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, cleAes, donnees);
  return JSON.parse(new TextDecoder().decode(clair));
}

export const verrouExiste = async () => Boolean(await brut.lire("verrou"));
export const estDeverrouille = () => cle !== null;

export async function initialiserVerrou(pin) {
  const sel = crypto.getRandomValues(new Uint8Array(16));
  cle = await deriver(pin, sel);
  await brut.ecrire("verrou", { sel, temoin: await chiffrer(cle, TEMOIN) });
  await brut.ecrire("essais", 0);
}

// Renvoie true si le PIN ouvre les données ; compte les essais ratés.
export async function deverrouiller(pin) {
  const verrou = await brut.lire("verrou");
  if (!verrou) return false;
  const candidate = await deriver(pin, verrou.sel);
  try {
    if ((await dechiffrer(candidate, verrou.temoin)) !== TEMOIN) throw new Error();
  } catch {
    await brut.ecrire("essais", ((await brut.lire("essais")) ?? 0) + 1);
    return false;
  }
  cle = candidate;
  await brut.ecrire("essais", 0);
  return true;
}

export const essaisRates = async () => (await brut.lire("essais")) ?? 0;

export function verrouiller() {
  cle = null;
}

export async function lire(nom) {
  const valeur = await brut.lire(nom);
  if (valeur === undefined || EN_CLAIR.has(nom)) return valeur;
  if (!cle) throw new Error("Données verrouillées");
  return dechiffrer(cle, valeur);
}

export async function ecrire(nom, valeur) {
  if (EN_CLAIR.has(nom)) return brut.ecrire(nom, valeur);
  if (!cle) throw new Error("Données verrouillées");
  return brut.ecrire(nom, await chiffrer(cle, valeur));
}

export async function effacerTout() {
  cle = null;
  await transaction("readwrite", (t) => t.clear());
}
