// Appli des relais communautaires : fonctionne sans réseau.
// Chaque action va dans une file locale (IndexedDB), envoyée par lots dès que le réseau revient.
// Le moteur clinique tourne sur le téléphone pour guider la relais tout de suite ;
// le serveur réévalue chaque visite à la réception.

import { evaluer, questions } from "./moteur.js";
import {
  deverrouiller, ecrire, effacerTout, essaisRates, initialiserVerrou, lire, verrouExiste, verrouiller,
} from "./stockage.js";

const CONTEXTES = {
  grossesse: "Femme enceinte",
  postpartum: "La mère, après l'accouchement",
  nouveau_ne: "Le bébé (0 à 2 mois)",
  enfant: "Enfant (6 mois à 5 ans)",
};
const NIVEAUX = {
  urgence: { titre: "URGENCE", consigne: "Appelez la sage-femme maintenant et partez vers le poste de santé." },
  consultation_24h: { titre: "Au poste de santé sous 24 h", consigne: "Rendez-vous au poste de santé aujourd'hui ou demain." },
  conseil: { titre: "Pas de signe de danger", consigne: "Rappelez-lui sa prochaine consultation." },
};
const LOT_MAX = 50;
const MAX_ESSAIS_PIN = 10; // au-delà, les données du téléphone sont effacées
const VERROU_APRES_MS = 5 * 60 * 1000; // appli verrouillée après 5 min en arrière-plan

const etat = {
  code: null,
  pin: null, // en mémoire seulement, pour se reconnecter quand la session expire
  verrouille: false,
  sessionExpiree: false,
  dossier: null,
  file: [],
  erreurs: [],
  derniereSynchro: null,
  enCours: false,
  dernierEchec: false,
  visite: null,
  urgence: null,
  resultat: null,
};
const DONNEES = ["dossier", "file", "erreurs", "derniereSynchro"];
const app = document.getElementById("app");
const barre = document.getElementById("statut");

// ---------- outils ----------

const echapper = (texte) =>
  String(texte ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const nouvelId = () =>
  self.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

const dateLocale = (iso) => new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
const jour = (iso) => dateLocale(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
const heure = (iso) => dateLocale(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const joursAvant = (iso) => Math.round((dateLocale(iso) - new Date(new Date().toDateString())) / 864e5);
const aujourdhuiIso = () => new Date().toLocaleDateString("en-CA");

const cle = (f) => f.code || f.uid;
const ref = (f) => (f.code ? { code: f.code } : { uid: f.uid });
const memeFemme = (r, f) => Boolean(r) && ((r.code && r.code === f.code) || (r.uid && r.uid === f.uid));
const regles = () => etat.dossier?.regles;
const libelle = (code) => regles()?.signes[code]?.libelle ?? code;

// Les femmes du dossier, plus celles inscrites sur ce téléphone et pas encore envoyées.
function femmes() {
  const connues = etat.dossier?.femmes ?? [];
  const uids = new Set(connues.map((f) => f.uid).filter(Boolean));
  const locales = etat.file
    .filter((op) => op.type === "inscription" && !uids.has(op.donnees.uid))
    .map((op) => ({
      code: null,
      uid: op.donnees.uid,
      nom: op.donnees.nom,
      telephone: op.donnees.telephone || null,
      statut: "enceinte",
      prochain_contact: null,
      contacts_en_retard: 0,
      alerte_active: null,
      locale: true,
    }));
  return [...connues, ...locales].map((f) => ({
    ...f,
    enAttente: f.locale || etat.file.some((op) => memeFemme(op.donnees.femme, f)),
  }));
}

const trouver = (c) => femmes().find((f) => cle(f) === c);

// Visites demandées par Debbo (tâches) : pourquoi passer voir cette femme.
const TACHES = {
  injoignable: "Debbo n'arrive pas à la joindre",
  consultation_manquee: "Consultation manquée",
  rappel_sans_telephone: "Lui rappeler sa consultation de demain",
};

function priorite(f) {
  if (f.alerte_active) return 0;
  if (f.taches?.length || f.contacts_en_retard) return 1;
  if (f.prochain_contact && joursAvant(f.prochain_contact.date) <= 3) return 2;
  if (f.locale) return 3;
  return null;
}

// ---------- file d'envoi et synchronisation ----------

async function sauver() {
  await Promise.all([ecrire("code", etat.code), ...DONNEES.map((nom) => ecrire(nom, etat[nom]))]);
}

const JSON_POST = (corps) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corps) });

// La session serveur dure 30 jours ; si elle a expiré, on se reconnecte avec le PIN saisi au déverrouillage.
async function reconnecter() {
  if (!etat.pin) return false;
  try {
    await appelApi("/api/auth/relais", JSON_POST({ code: etat.code, pin: etat.pin }));
    etat.sessionExpiree = false;
    return true;
  } catch (erreur) {
    if (erreur.status === 401) etat.sessionExpiree = true;
    return false;
  }
}

async function ajouter(type, donnees) {
  const op = { op_id: nouvelId(), type, cree_le: new Date().toISOString(), donnees };
  etat.file.push(op);
  await sauver();
  synchroniser();
  return op;
}

async function appelApi(chemin, options = {}) {
  const arret = new AbortController();
  const minuterie = setTimeout(() => arret.abort(), 15000);
  try {
    const reponse = await fetch(chemin, { ...options, signal: arret.signal });
    if (!reponse.ok) {
      const corps = await reponse.json().catch(() => ({}));
      throw Object.assign(new Error(corps.detail || `Erreur ${reponse.status}`), { status: reponse.status });
    }
    return reponse.json();
  } finally {
    clearTimeout(minuterie);
  }
}

async function envoyerLots() {
  do {
    const lot = etat.file.slice(0, LOT_MAX);
    const { resultats, dossier } = await appelApi("/api/relais/moi/sync", JSON_POST({ operations: lot }));
    for (const r of resultats.filter((r) => r.statut === "erreur")) {
      const op = lot.find((o) => o.op_id === r.op_id);
      etat.erreurs.push({ message: r.message, type: op?.type, nom: op?.donnees?.nom, at: new Date().toISOString() });
    }
    const traites = new Set(resultats.map((r) => r.op_id));
    etat.file = etat.file.filter((op) => !traites.has(op.op_id));
    etat.dossier = dossier;
    etat.derniereSynchro = new Date().toISOString();
    await sauver();
  } while (etat.file.length);
}

async function synchroniser() {
  if (etat.enCours || !etat.code || etat.verrouille || !etat.dossier) return;
  etat.enCours = true;
  rendreStatut();
  try {
    try {
      await envoyerLots();
    } catch (erreur) {
      if (erreur.status !== 401 || !(await reconnecter())) throw erreur;
      await envoyerLots();
    }
    etat.dernierEchec = false;
    etat.sessionExpiree = false;
  } catch (erreur) {
    etat.dernierEchec = true;
    if (erreur.status === 401) etat.sessionExpiree = true;
  } finally {
    etat.enCours = false;
    rendreStatut();
    rafraichirSiPassive();
  }
}

// ---------- écrans ----------

function rendreStatut() {
  if (etat.verrouille || !etat.code || !etat.dossier) {
    barre.innerHTML = `<span class="qui">Debbo Relais</span>`;
    return;
  }
  const enLigne = navigator.onLine && !etat.dernierEchec;
  const attente = etat.file.length;
  const info = etat.sessionExpiree
    ? "Reconnexion requise"
    : attente ? `${attente} à envoyer` : etat.derniereSynchro ? `À jour ${heure(etat.derniereSynchro)}` : "";
  barre.innerHTML = `
    <span class="point ${enLigne ? "en-ligne" : "hors-ligne"}" title="${enLigne ? "En ligne" : "Hors ligne"}"></span>
    <span class="qui">${echapper(etat.dossier.relais.nom)}</span>
    <span class="doux">${info}</span>
    <button data-action="synchro" ${etat.enCours ? "disabled" : ""}>${etat.enCours ? "Envoi…" : "Envoyer"}</button>`;
}

function ligneFemme(f) {
  const badges = [];
  if (f.alerte_active) badges.push(`<span class="badge urgence">Alerte en cours</span>`);
  for (const t of f.taches ?? []) badges.push(`<span class="badge attention">${TACHES[t.motif] ?? echapper(t.motif)}</span>`);
  if (f.contacts_en_retard && !(f.taches ?? []).some((t) => t.motif === "consultation_manquee")) badges.push(`<span class="badge urgence">${f.contacts_en_retard} consultation(s) manquée(s)</span>`);
  if (f.prochain_contact && joursAvant(f.prochain_contact.date) <= 7) {
    badges.push(`<span class="badge attention">${echapper(f.prochain_contact.type)} ${jour(f.prochain_contact.date)}</span>`);
  }
  if (f.statut === "postpartum") badges.push(`<span class="badge">A accouché</span>`);
  if (f.enAttente) badges.push(`<span class="badge">À envoyer</span>`);
  return `
    <button class="femme" data-action="aller" data-href="#/femme/${encodeURIComponent(cle(f))}">
      <span>
        <span class="nom">${echapper(f.nom || f.code)}</span>
        <span class="details">${badges.join("") || (f.telephone ? echapper(f.telephone) : "Sans téléphone")}</span>
      </span>
      <span class="fleche">›</span>
    </button>`;
}

const champPin = (id) =>
  `<input id="${id}" class="mesure" type="password" inputmode="numeric" autocomplete="off" maxlength="6" pattern="[0-9]{6}">`;

function vueConnexion(message = "") {
  return `
    <h1>Bienvenue</h1>
    <p class="doux">La première connexion demande du réseau ; ensuite l'appli marche sans réseau.</p>
    ${message ? `<div class="bandeau erreur">${echapper(message)}</div>` : ""}
    <label for="code">Code relais</label>
    <input id="code" autocomplete="username" autocapitalize="characters" placeholder="R-0001">
    <label for="pin">PIN (6 chiffres)</label>
    ${champPin("pin")}
    <p></p>
    <button class="principal" data-action="connexion">Entrer</button>`;
}

function vueDeverrouillage(message = "") {
  return `
    <h1>Appli verrouillée</h1>
    <p class="doux">Relais ${echapper(etat.code)} · entrez votre PIN pour ouvrir vos données.</p>
    ${message ? `<div class="bandeau erreur">${echapper(message)}</div>` : ""}
    <label for="pin-local">PIN</label>
    ${champPin("pin-local")}
    <p></p>
    <button class="principal" data-action="deverrouiller">Ouvrir</button>
    <p></p>
    <button class="lien" data-action="deconnexion">Ce n'est pas mon téléphone : changer de relais</button>`;
}

function vueAccueil() {
  const liste = femmes();
  const aVisiter = liste
    .map((f) => [priorite(f), f])
    .filter(([p]) => p !== null)
    .sort((a, b) => a[0] - b[0])
    .map(([, f]) => f);
  const autres = liste.filter((f) => !aVisiter.includes(f)).sort((a, b) => (a.nom || "").localeCompare(b.nom || ""));
  const erreurs = etat.erreurs.length
    ? `<button class="bandeau erreur" data-action="aller" data-href="#/erreurs">${etat.erreurs.length} envoi(s) refusé(s) : voir pourquoi</button>`
    : "";
  return `
    ${erreurs}
    <button class="danger urgence-geante" data-action="aller" data-href="#/urgence">URGENCE</button>
    <button class="principal" data-action="aller" data-href="#/inscription">+ Nouvelle grossesse</button>
    <h2>À visiter (${aVisiter.length})</h2>
    <div class="pile">${aVisiter.map(ligneFemme).join("") || `<p class="doux">Personne à voir en priorité.</p>`}</div>
    <h2>Toutes les femmes (${autres.length})</h2>
    <div class="pile">${autres.map(ligneFemme).join("") || `<p class="doux">Aucune autre femme suivie.</p>`}</div>
    <p></p>
    <div class="duo">
      <button data-action="verrouiller">Verrouiller</button>
      <button class="lien" data-action="deconnexion">Changer de relais</button>
    </div>`;
}

function vueFemme(f) {
  if (!f) return `<p>Femme introuvable.</p><button data-action="aller" data-href="#/">Retour</button>`;
  const contact = f.prochain_contact;
  const lignes = [
    f.telephone ? `<a class="bouton" href="tel:${echapper(f.telephone)}">Appeler ${echapper(f.telephone)}</a>` : `<p class="doux">Sans téléphone</p>`,
    f.terme_prevu ? `<p>Terme prévu vers le <strong>${jour(f.terme_prevu)}</strong></p>` : "",
    f.date_accouchement ? `<p>A accouché le <strong>${jour(f.date_accouchement)}</strong></p>` : "",
    contact ? `<p>Prochaine consultation : <strong>${echapper(contact.type)} le ${jour(contact.date)}</strong></p>` : "",
    f.contacts_en_retard ? `<div class="bandeau erreur">${f.contacts_en_retard} consultation(s) manquée(s) : encouragez-la à aller au poste.</div>` : "",
    f.alerte_active ? `<div class="bandeau erreur">Alerte en cours au poste de santé.</div>` : "",
    ...(f.taches ?? []).map((t) => `<div class="bandeau attention">Visite demandée : ${TACHES[t.motif] ?? echapper(t.motif)}. Faites une visite pour la clore.</div>`),
    f.enAttente ? `<div class="bandeau attention">Des informations attendent le réseau pour partir.</div>` : "",
  ];
  const venue = contact?.id && !etat.file.some((op) => op.type === "contact_realise" && op.donnees.contact_id === contact.id);
  return `
    <button class="lien" data-action="aller" data-href="#/">‹ Retour</button>
    <h1>${echapper(f.nom || f.code)}</h1>
    <p class="doux">${f.code ? echapper(f.code) : "Pas encore envoyée"}</p>
    ${lignes.join("")}
    <div class="pile">
      <button class="principal" data-action="aller" data-href="#/visite/${encodeURIComponent(cle(f))}">Faire une visite</button>
      <button class="danger" data-action="aller" data-href="#/urgence/${encodeURIComponent(cle(f))}">Urgence</button>
      ${venue ? `<button data-action="venue" data-contact="${contact.id}">Elle est allée à sa consultation</button>` : ""}
      ${f.statut === "enceinte" && !f.locale ? `<button data-action="aller" data-href="#/accouchement/${encodeURIComponent(cle(f))}">Elle a accouché</button>` : ""}
    </div>`;
}

function vueInscription() {
  return `
    <button class="lien" data-action="aller" data-href="#/">‹ Retour</button>
    <h1>Nouvelle grossesse</h1>
    <label for="nom">Nom de la femme</label>
    <input id="nom" autocomplete="off">
    <label for="tel">Téléphone (si elle en a un)</label>
    <input id="tel" type="tel" inputmode="tel" autocomplete="off" placeholder="+221…">
    <label>Depuis combien de mois est-elle enceinte ?</label>
    <div class="grille" id="mois">
      ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((m) => `<button data-action="mois" data-mois="${m}">${m}</button>`).join("")}
    </div>
    <label class="case"><input type="checkbox" id="accord"> J'ai expliqué Debbo à la femme : elle est d'accord pour être suivie.</label>
    <div id="message"></div>
    <p></p>
    <button class="principal" data-action="inscrire">Enregistrer</button>`;
}

function vueVisite() {
  const v = etat.visite;
  const f = trouver(v.cle);
  const entete = `<button class="lien" data-action="aller" data-href="#/femme/${encodeURIComponent(v.cle)}">‹ Arrêter</button>
    <h1>Visite · ${echapper(f?.nom || "")}</h1>`;
  if (v.etape === "contexte") {
    const choix = f?.statut === "postpartum" ? ["postpartum", "nouveau_ne", "enfant"] : ["grossesse", "enfant"];
    return `${entete}<p>Qui voyez-vous ?</p><div class="pile">
      ${choix.map((c) => `<button data-action="visite-contexte" data-ctx="${c}">${CONTEXTES[c]}</button>`).join("")}</div>`;
  }
  if (v.etape === "questions") {
    const q = v.questions[v.index];
    return `${entete}
      <div class="progression"><div style="width:${Math.round((100 * v.index) / v.questions.length)}%"></div></div>
      <p class="doux">${CONTEXTES[v.contexte]} · question ${v.index + 1} sur ${v.questions.length}</p>
      <div class="question">${echapper(q.libelle)} ?</div>
      <div class="oui-non">
        <button class="danger" data-action="repondre" data-reponse="oui">OUI</button>
        <button data-action="repondre" data-reponse="non">NON</button>
      </div>`;
  }
  if (v.etape === "pb") {
    const s = regles().seuils_pb_mm;
    return `${entete}
      <label for="pb">Périmètre brachial de l'enfant (mm)</label>
      <input id="pb" class="mesure" type="number" inputmode="numeric" min="60" max="250">
      <div class="bandes"><span class="rouge">Rouge &lt; ${s.rouge_sous}</span><span class="jaune">Jaune ${s.rouge_sous}-${s.jaune_sous - 1}</span><span class="vert">Vert ≥ ${s.jaune_sous}</span></div>
      <p></p>
      <div class="duo"><button data-action="pb-passer">Pas de mesure</button><button class="principal" data-action="pb-valider">Valider</button></div>`;
  }
  return `${entete}
    <label for="note">Remarque (facultatif)</label>
    <textarea id="note" rows="3"></textarea>
    <p></p>
    <button class="principal" data-action="visite-terminer">Terminer la visite</button>`;
}

function vueUrgence() {
  const u = etat.urgence;
  const entete = `<button class="lien" data-action="aller" data-href="#/">‹ Annuler</button><h1 style="color:var(--urgence)">Urgence</h1>`;
  if (u.cle === undefined) {
    const liste = femmes().sort((a, b) => (priorite(a) ?? 9) - (priorite(b) ?? 9));
    return `${entete}<p>Pour qui ?</p><div class="pile">
      <button data-action="urgence-femme" data-cle="">Une femme qui n'est pas dans la liste</button>
      ${liste.map((f) => `<button data-action="urgence-femme" data-cle="${echapper(cle(f))}">${echapper(f.nom || f.code)}</button>`).join("")}
    </div>`;
  }
  if (!u.contexte) {
    const f = u.cle ? trouver(u.cle) : null;
    const choix = f ? (f.statut === "postpartum" ? ["postpartum", "nouveau_ne", "enfant"] : ["grossesse", "enfant"]) : Object.keys(CONTEXTES);
    return `${entete}<p>${f ? echapper(f.nom) : "Femme non inscrite"} : qui est malade ?</p><div class="pile">
      ${choix.map((c) => `<button data-action="urgence-contexte" data-ctx="${c}">${CONTEXTES[c]}</button>`).join("")}</div>`;
  }
  const signes = questions(regles(), u.contexte).filter((q) => q.niveau === "urgence");
  return `${entete}<p>Quel signe ?</p><div class="pile">
    ${signes.map((q) => `<button data-action="urgence-signe" data-code="${q.code}">${echapper(q.libelle)}</button>`).join("")}
    <button data-action="urgence-signe" data-code="">Autre urgence</button></div>`;
}

function vueResultat() {
  const r = etat.resultat;
  if (!r) return vueAccueil();
  const relais = etat.dossier.relais;
  const f = r.cle ? trouver(r.cle) : null;
  const niveau = NIVEAUX[r.niveau];
  const alerteAttendue = r.niveau !== "conseil";
  const envoyee = !etat.file.some((op) => op.op_id === r.opId);
  const sms = `Debbo ${niveau.titre} - ${f?.nom || "femme non inscrite"} : ${r.signes.join(", ")}. Relais ${relais.nom}`;
  const appel = relais.telephone_sf
    ? `<a class="bouton ${r.niveau === "urgence" ? "danger" : ""}" href="tel:${echapper(relais.telephone_sf)}">Appeler la sage-femme</a>
       <a class="bouton" href="sms:${echapper(relais.telephone_sf)}?body=${encodeURIComponent(sms)}">Envoyer un SMS à la sage-femme</a>`
    : "";
  let etatEnvoi = "";
  if (alerteAttendue) {
    etatEnvoi = envoyee
      ? `<div class="bandeau ok">Alerte reçue par le poste de santé.</div>`
      : `<div class="bandeau erreur">Pas de réseau : l'alerte n'est pas encore partie. Appelez ou envoyez un SMS à la sage-femme.</div>`;
  }
  const nutrition = r.pb === "pb_jaune" ? `<div class="bandeau attention">Périmètre brachial jaune : orientez l'enfant vers le programme de nutrition.</div>` : "";
  const prochain = r.niveau === "conseil" && f?.prochain_contact
    ? `<p>Prochaine consultation : <strong>${echapper(f.prochain_contact.type)} le ${jour(f.prochain_contact.date)}</strong></p>`
    : "";
  return `
    <div class="verdict ${r.niveau}">
      <div class="titre">${niveau.titre}</div>
      <p>${niveau.consigne}</p>
    </div>
    ${r.signes.length ? `<p class="doux">Signes : ${r.signes.map(echapper).join(", ")}</p>` : ""}
    ${etatEnvoi}${nutrition}${prochain}
    <div class="pile">${alerteAttendue ? appel : ""}
      <button class="principal" data-action="aller" data-href="#/">Retour à l'accueil</button>
    </div>`;
}

function vueAccouchement(f) {
  return `
    <button class="lien" data-action="aller" data-href="#/femme/${encodeURIComponent(cle(f))}">‹ Retour</button>
    <h1>${echapper(f.nom)} a accouché</h1>
    <label for="date">Date de l'accouchement</label>
    <input id="date" type="date" value="${aujourdhuiIso()}" max="${aujourdhuiIso()}">
    <p class="doux">Debbo planifiera ses consultations après l'accouchement (jours 1, 3, 7 et 42).</p>
    <button class="principal" data-action="accouchement" data-cle="${echapper(cle(f))}">Enregistrer</button>`;
}

function vueErreurs() {
  return `
    <button class="lien" data-action="aller" data-href="#/">‹ Retour</button>
    <h1>Envois refusés</h1>
    <p class="doux">Le poste de santé a refusé ces informations. Corrigez-les puis recommencez.</p>
    <div class="pile">${etat.erreurs
      .map((e) => `<div class="carte"><strong>${echapper(e.message)}</strong><div class="doux">${echapper(e.type)}${e.nom ? ` · ${echapper(e.nom)}` : ""} · ${jour(e.at)} ${heure(e.at)}</div></div>`)
      .join("")}</div>
    <p></p>
    <button data-action="effacer-erreurs">J'ai compris, effacer</button>`;
}

// ---------- navigation ----------

function route() {
  const [vue = "", param = ""] = location.hash.replace(/^#\/?/, "").split("/");
  return { vue, param: decodeURIComponent(param) };
}

const VUES_PASSIVES = new Set(["", "femme", "resultat", "erreurs"]);

function rafraichirSiPassive() {
  if (VUES_PASSIVES.has(route().vue)) rendre();
}

function rendre() {
  rendreStatut();
  if (etat.verrouille) {
    app.innerHTML = vueDeverrouillage();
    return;
  }
  if (!etat.code || !etat.dossier) {
    app.innerHTML = vueConnexion();
    return;
  }
  const { vue, param } = route();
  if (vue === "femme") app.innerHTML = vueFemme(trouver(param));
  else if (vue === "inscription") app.innerHTML = vueInscription();
  else if (vue === "visite") {
    if (etat.visite?.cle !== param) etat.visite = { cle: param, etape: "contexte", oui: [], pb: null };
    app.innerHTML = vueVisite();
  } else if (vue === "urgence") {
    if (!etat.urgence || etat.urgence.depuis !== param) {
      etat.urgence = { depuis: param, cle: param ? param : undefined, contexte: null };
    }
    app.innerHTML = vueUrgence();
  } else if (vue === "resultat") app.innerHTML = vueResultat();
  else if (vue === "accouchement") app.innerHTML = vueAccouchement(trouver(param));
  else if (vue === "erreurs") app.innerHTML = vueErreurs();
  else app.innerHTML = vueAccueil();
  window.scrollTo(0, 0);
}

// ---------- actions ----------

function terminerVisite() {
  const v = etat.visite;
  const f = trouver(v.cle);
  const note = document.getElementById("note")?.value.trim() || null;
  const evaluation = evaluer(regles(), v.oui, v.contexte, v.pb);
  const signePb = evaluation.signesRetenus.find((s) => s.startsWith("pb_"));
  ajouter("visite", { femme: ref(f), contexte: v.contexte, signes: v.oui, pb_mm: v.pb, note }).then((op) => {
    etat.resultat = { niveau: evaluation.niveau, signes: evaluation.signesRetenus.map(libelle), opId: op.op_id, cle: v.cle, pb: signePb };
    etat.visite = null;
    location.hash = "#/resultat";
  });
}

function suivante() {
  const v = etat.visite;
  v.index += 1;
  if (v.index < v.questions.length) return;
  v.etape = v.contexte === "enfant" ? "pb" : "note";
}

const actions = {
  aller: (d) => { location.hash = d.href; },
  synchro: () => synchroniser(),
  deconnexion: async () => {
    const attente = etat.verrouille ? "Les informations non envoyées" : `${etat.file.length} information(s) non envoyée(s)`;
    if ((etat.verrouille || etat.file.length) && !confirm(`${attente} seront effacées de ce téléphone. Continuer ?`)) return;
    await fetch("/api/auth/deconnexion", { method: "POST" }).catch(() => {});
    await effacerTout();
    Object.assign(etat, { code: null, pin: null, verrouille: false, sessionExpiree: false, dossier: null, file: [], erreurs: [], derniereSynchro: null });
    location.hash = "#/";
    rendre();
  },
  connexion: async () => {
    const code = document.getElementById("code").value.trim().toUpperCase();
    const pin = document.getElementById("pin").value.trim();
    if (!code || !/^\d{6}$/.test(pin)) {
      app.innerHTML = vueConnexion("Entrez votre code relais et votre PIN à 6 chiffres.");
      return;
    }
    try {
      await appelApi("/api/auth/relais", JSON_POST({ code, pin }));
      const dossier = await appelApi("/api/relais/moi/dossier");
      await effacerTout();
      await initialiserVerrou(pin);
      Object.assign(etat, { code, pin, dossier, file: [], erreurs: [], derniereSynchro: new Date().toISOString() });
      await sauver();
      location.hash = "#/";
      rendre();
    } catch (erreur) {
      const message = { 401: "Code relais ou PIN incorrect.", 429: "Trop d'essais. Réessayez dans 15 minutes." }[erreur.status];
      app.innerHTML = vueConnexion(message || "Pas de réseau. La première connexion demande du réseau.");
    }
  },
  deverrouiller: async () => {
    const pin = document.getElementById("pin-local").value.trim();
    if (await deverrouiller(pin)) {
      for (const nom of DONNEES) etat[nom] = (await lire(nom)) ?? (nom === "file" || nom === "erreurs" ? [] : null);
      Object.assign(etat, { pin, verrouille: false });
      rendre();
      synchroniser();
      return;
    }
    const essais = await essaisRates();
    if (essais >= MAX_ESSAIS_PIN) {
      await effacerTout();
      Object.assign(etat, { code: null, verrouille: false });
      app.innerHTML = vueConnexion("Trop d'essais : les données de ce téléphone ont été effacées. Reconnectez-vous avec du réseau.");
      return;
    }
    app.innerHTML = vueDeverrouillage(`PIN incorrect. Encore ${MAX_ESSAIS_PIN - essais} essai(s) avant l'effacement des données.`);
  },
  verrouiller: () => verrouillerAppli(),
  mois: (d, bouton) => {
    document.querySelectorAll("#mois button").forEach((b) => b.classList.toggle("principal", b === bouton));
    document.getElementById("mois").dataset.choix = d.mois;
  },
  inscrire: async () => {
    const nom = document.getElementById("nom").value.trim();
    const telephone = document.getElementById("tel").value.trim();
    const mois = Number(document.getElementById("mois").dataset.choix);
    const accord = document.getElementById("accord").checked;
    const manque = !nom ? "Entrez le nom." : !mois ? "Choisissez le mois de grossesse." : !accord ? "Il faut l'accord de la femme." : "";
    if (manque) {
      document.getElementById("message").innerHTML = `<div class="bandeau erreur">${manque}</div>`;
      return;
    }
    const uid = nouvelId();
    await ajouter("inscription", { uid, nom, telephone: telephone || null, mois, consentement: true });
    location.hash = `#/femme/${encodeURIComponent(uid)}`;
  },
  "visite-contexte": (d) => {
    const v = etat.visite;
    Object.assign(v, { contexte: d.ctx, questions: questions(regles(), d.ctx), index: 0, oui: [], etape: "questions" });
    if (!v.questions.length) v.etape = d.ctx === "enfant" ? "pb" : "note";
    rendre();
  },
  repondre: (d) => {
    const v = etat.visite;
    const q = v.questions[v.index];
    if (d.reponse === "oui") {
      v.oui.push(q.code);
      // Un signe d'urgence arrête la visite : on ne retarde jamais une urgence.
      if (q.niveau === "urgence") return terminerVisite();
    }
    suivante();
    rendre();
  },
  "pb-valider": () => {
    const valeur = Number(document.getElementById("pb").value);
    if (!valeur) return;
    etat.visite.pb = valeur;
    etat.visite.etape = "note";
    rendre();
  },
  "pb-passer": () => {
    etat.visite.etape = "note";
    rendre();
  },
  "visite-terminer": () => terminerVisite(),
  "urgence-femme": (d) => {
    etat.urgence.cle = d.cle;
    rendre();
  },
  "urgence-contexte": (d) => {
    etat.urgence.contexte = d.ctx;
    rendre();
  },
  "urgence-signe": async (d) => {
    const u = etat.urgence;
    const f = u.cle ? trouver(u.cle) : null;
    const signes = d.code ? [d.code] : [];
    const op = await ajouter("alerte", { femme: f ? ref(f) : null, contexte: u.contexte, signes });
    etat.resultat = { niveau: "urgence", signes: signes.map(libelle), opId: op.op_id, cle: u.cle || null };
    etat.urgence = null;
    location.hash = "#/resultat";
  },
  venue: async (d) => {
    await ajouter("contact_realise", { contact_id: Number(d.contact) });
    rendre();
  },
  accouchement: async (d) => {
    const date = document.getElementById("date").value;
    if (!date) return;
    await ajouter("accouchement", { femme: ref(trouver(d.cle)), date });
    location.hash = `#/femme/${encodeURIComponent(d.cle)}`;
  },
  "effacer-erreurs": async () => {
    etat.erreurs = [];
    await sauver();
    location.hash = "#/";
  },
};

document.addEventListener("click", (evenement) => {
  const bouton = evenement.target.closest("[data-action]");
  if (!bouton) return;
  actions[bouton.dataset.action]?.(bouton.dataset, bouton);
});

addEventListener("hashchange", rendre);
addEventListener("online", () => synchroniser());
addEventListener("offline", rendreStatut);

// Efface de la mémoire tout ce qui est déchiffré ; les données restent chiffrées sur le téléphone.
function verrouillerAppli() {
  verrouiller();
  Object.assign(etat, { pin: null, verrouille: true, dossier: null, file: [], erreurs: [], visite: null, urgence: null, resultat: null });
  location.hash = "#/";
  rendre();
}

let cacheDepuis = null;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    cacheDepuis = Date.now();
  } else if (cacheDepuis && Date.now() - cacheDepuis > VERROU_APRES_MS && !etat.verrouille && etat.dossier) {
    verrouillerAppli();
  }
});

async function demarrer() {
  etat.code = (await lire("code")) ?? null;
  etat.verrouille = Boolean(etat.code) && (await verrouExiste());
  if (!etat.verrouille) etat.code = null;
  rendre();
  setInterval(synchroniser, 60000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

demarrer();
