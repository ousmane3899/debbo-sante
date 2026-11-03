// Administration : comptes des soignants et des relais, demandes reçues au poste, journal d'accès.
// Aucun dossier de santé n'est visible ici.

const ROLES = { sage_femme: "Sage-femme", district: "District", admin: "Administration", relais: "Relais" };
const ACTIONS = {
  compte_cree: "Compte créé",
  compte_modifie: "Compte modifié",
  compte_desactive: "Compte désactivé",
  compte_reactive: "Compte réactivé",
  compte_deverrouille: "Compte déverrouillé",
  compte_verrouille: "Compte verrouillé (trop d'essais)",
  secret_reinitialise: "Nouveau code provisoire",
  secret_choisi: "Secret choisi par la personne",
  "2fa_reinitialisee": "Double authentification réinitialisée",
  "2fa_activee": "Double authentification activée",
  connexion: "Connexion",
  connexion_echec: "Échec de connexion",
  deconnexion: "Déconnexion",
  effacement_saisi_par_admin: "Effacement demandé au poste",
  code_discret_retire: "Code personnel retiré",
};

const echapper = (texte) =>
  String(texte ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const dateHeure = (iso) =>
  new Date(iso).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

let moi = null;
let comptes = [];
let filtre = "tous";

async function api(chemin, corps) {
  const reponse = await fetch(`/api${chemin}`, {
    method: corps === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: corps === undefined ? undefined : JSON.stringify(corps),
  });
  if (reponse.status === 401) {
    location.replace("/connexion");
    throw new Error("Connexion requise");
  }
  const donnees = await reponse.json().catch(() => ({}));
  if (!reponse.ok) {
    const detail = Array.isArray(donnees.detail) ? "Champ manquant ou invalide" : donnees.detail;
    throw new Error(detail || `Erreur ${reponse.status}`);
  }
  return donnees;
}

// ---------- comptes ----------

function etiquettes(c) {
  const liste = [];
  if (!c.actif) liste.push(`<span class="badge urgence">Désactivé</span>`);
  if (c.verrouille) liste.push(`<span class="badge urgence">Verrouillé</span>`);
  if (c.provisoire_expire) liste.push(`<span class="badge urgence">Code provisoire expiré</span>`);
  else if (c.secret_provisoire) liste.push(`<span class="badge">Pas encore connecté·e</span>`);
  else if (c.role !== "relais" && !c.totp_actif) liste.push(`<span class="badge">Double authentification à activer</span>`);
  return liste.join(" ");
}

function boutons(c) {
  const soi = c.identifiant === moi.identifiant;
  const liste = [`<button data-action="modifier">Modifier</button>`];
  if (!soi) liste.push(`<button data-action="nouveau-secret">${c.role === "relais" ? "Nouveau PIN" : "Nouveau mot de passe"}</button>`);
  if (!soi && c.role !== "relais" && c.totp_actif) liste.push(`<button data-action="reinitialiser-2fa">Téléphone perdu</button>`);
  if (c.verrouille) liste.push(`<button data-action="deverrouiller">Déverrouiller</button>`);
  if (!soi) liste.push(c.actif ? `<button data-action="desactiver">Désactiver</button>` : `<button data-action="reactiver">Réactiver</button>`);
  return liste.join("");
}

function ligneCompte(c) {
  const details = [
    ROLES[c.role] || c.role,
    c.relais ? c.relais.code : c.identifiant,
    c.zone,
    c.relais?.telephone,
  ].filter(Boolean).map(echapper).join(" · ");
  return `
    <div class="ligne ${c.actif ? "" : "inactif"}" data-compte="${echapper(c.identifiant)}">
      <div>
        <div><strong>${echapper(c.nom)}</strong>${c.identifiant === moi.identifiant ? " (vous)" : ""} ${etiquettes(c)}</div>
        <div class="meta">${details}</div>
      </div>
      <div class="actions">${boutons(c)}</div>
    </div>`;
}

function afficherComptes() {
  const visibles = comptes.filter((c) =>
    filtre === "tous" || (filtre === "relais" ? c.role === "relais" : c.role !== "relais"));
  document.getElementById("comptes").innerHTML = visibles.length
    ? visibles.map(ligneCompte).join("")
    : `<div class="vide">Aucun compte.</div>`;
}

async function charger() {
  const [liste, { zones, prochain_code_relais: prochain }] = await Promise.all([api("/admin/utilisateurs"), api("/admin/zones")]);
  comptes = liste;
  afficherComptes();
  document.getElementById("zones").innerHTML = zones.map((z) => `<option value="${echapper(z)}">`).join("");
  // Code relais proposé tant que l'administration ne l'a pas changé à la main.
  const code = document.getElementById("c-code");
  if (!code.value || code.dataset.auto === "oui") {
    code.value = prochain;
    code.dataset.auto = "oui";
  }
  chargerJournal();
}

function montrerSecret(compte, secret) {
  const relais = compte.role === "relais";
  document.getElementById("secret-titre").textContent = `${compte.nom} · ${relais ? compte.relais.code : compte.identifiant}`;
  document.getElementById("secret-consigne").textContent = relais
    ? "Donnez ce PIN provisoire à la relais, en main propre. À sa première connexion, l'appli lui demandera de choisir son propre PIN."
    : "Donnez ce mot de passe provisoire en main propre (jamais par SMS ni WhatsApp). À la première connexion, il faudra choisir un mot de passe personnel puis activer la double authentification.";
  document.getElementById("secret-valeur").textContent = secret;
  document.getElementById("secret-expire").textContent =
    `Valable jusqu'au ${dateHeure(compte.secret_expire_at)}. Il ne sera plus jamais affiché.`;
  document.getElementById("secret").showModal();
}

const CONFIRMATIONS = {
  "nouveau-secret": (c) => `Créer un nouveau ${c.role === "relais" ? "PIN" : "mot de passe"} provisoire pour ${c.nom} ? L'ancien ne marchera plus et ses sessions seront fermées.`
    + (c.role === "relais" ? " Les informations pas encore envoyées depuis son téléphone seront perdues s'il faut se reconnecter." : ""),
  "reinitialiser-2fa": (c) => `${c.nom} a perdu son téléphone ? Il faudra réactiver la double authentification à la prochaine connexion.`,
  desactiver: (c) => `Désactiver le compte de ${c.nom} ? Ses sessions seront fermées immédiatement.`,
};

async function agir(action, compte) {
  if (action === "modifier") return ouvrirModification(compte);
  if (CONFIRMATIONS[action] && !confirm(CONFIRMATIONS[action](compte))) return;
  try {
    const resultat = await api(`/admin/utilisateurs/${encodeURIComponent(compte.identifiant)}/${action}`, {});
    if (resultat.secret_provisoire) montrerSecret(resultat.compte, resultat.secret_provisoire);
  } catch (erreur) {
    alert(erreur.message);
  }
  charger();
}

// ---------- modification ----------

function basculer(conteneur, { relais, zone }) {
  conteneur.querySelectorAll('[data-pour="relais"]').forEach((e) => { e.hidden = !relais; });
  conteneur.querySelectorAll('[data-pour="soignant"]').forEach((e) => { e.hidden = relais; });
  conteneur.querySelectorAll('[data-pour="zone"]').forEach((e) => { e.hidden = !zone; });
}

function ouvrirModification(c) {
  const dialogue = document.getElementById("modifier");
  const relais = c.role === "relais";
  basculer(dialogue, { relais, zone: relais || c.role === "sage_femme" });
  document.getElementById("m-titre").textContent = `${ROLES[c.role]} · ${relais ? c.relais.code : c.identifiant}`;
  document.getElementById("m-nom").value = c.nom;
  document.getElementById("m-zone").value = c.zone || "";
  document.getElementById("m-telephone").value = c.relais?.telephone || "";
  document.getElementById("m-telephone-sf").value = c.relais?.telephone_sf || "";
  dialogue.returnValue = "";
  dialogue.showModal();
  dialogue.addEventListener("close", async () => {
    if (dialogue.returnValue !== "ok") return;
    try {
      await api(`/admin/utilisateurs/${encodeURIComponent(c.identifiant)}/modifier`, {
        nom: document.getElementById("m-nom").value,
        zone: document.getElementById("m-zone").value,
        telephone: document.getElementById("m-telephone").value,
        telephone_sf: document.getElementById("m-telephone-sf").value,
      });
    } catch (erreur) {
      alert(erreur.message);
    }
    charger();
  }, { once: true });
}

// ---------- création ----------

function formulaireSelonRole() {
  const role = document.getElementById("c-role").value;
  basculer(document.getElementById("creation"), { relais: role === "relais", zone: role === "relais" || role === "sage_femme" });
}

async function creer(evenement) {
  evenement.preventDefault();
  const message = document.getElementById("creation-message");
  const bouton = evenement.target.querySelector("button");
  const role = document.getElementById("c-role").value;
  message.textContent = "";
  bouton.disabled = true;
  try {
    const resultat = await api("/admin/utilisateurs", {
      role,
      nom: document.getElementById("c-nom").value,
      identifiant: document.getElementById("c-identifiant").value,
      code: document.getElementById("c-code").value,
      zone: document.getElementById("c-zone").value,
      telephone: document.getElementById("c-telephone").value,
      telephone_sf: document.getElementById("c-telephone-sf").value,
    });
    evenement.target.reset();
    document.getElementById("c-role").value = role;
    document.getElementById("c-code").dataset.auto = "oui";
    formulaireSelonRole();
    montrerSecret(resultat.compte, resultat.secret_provisoire);
    charger();
  } catch (erreur) {
    message.textContent = erreur.message;
  } finally {
    bouton.disabled = false;
  }
}

// ---------- demandes reçues au poste ----------

async function enregistrerDemande(evenement) {
  evenement.preventDefault();
  const message = document.getElementById("demande-message");
  const code = document.getElementById("d-code").value.trim().toUpperCase();
  const action = document.getElementById("d-action").value;
  const texte = action === "effacer"
    ? `Effacer les données de ${code} ? C'est définitif. Si une alerte est en cours, l'effacement aura lieu à sa clôture.`
    : `Retirer le code personnel de ${code} ? Elle pourra en choisir un nouveau par USSD.`;
  if (!confirm(texte)) return;
  message.textContent = "";
  try {
    const r = await api(`/admin/femmes/${encodeURIComponent(code)}/${action}`, {});
    message.textContent = action === "effacer"
      ? (r.effacement === "efface" ? "Données effacées." : "Alerte en cours : l'effacement aura lieu à sa clôture.")
      : "Code personnel retiré.";
    message.classList.add("ok");
    evenement.target.reset();
    chargerJournal();
  } catch (erreur) {
    message.classList.remove("ok");
    message.textContent = erreur.message;
  }
}

// ---------- journal ----------

async function chargerJournal() {
  const { chaine_intacte: intacte, lignes } = await api("/admin/journal?limite=40");
  document.getElementById("journal-etat").innerHTML = intacte
    ? `<span class="badge accent">Chaîne intacte</span> 40 dernières lignes`
    : `<span class="badge urgence">Journal altéré : prévenez le responsable sécurité</span>`;
  document.getElementById("journal").innerHTML = lignes.map((l) => `
    <div class="ligne">
      <div>
        <div>${echapper(ACTIONS[l.action] || l.action)}${l.cible ? ` · ${echapper(l.cible)}` : ""}</div>
        <div class="meta">${dateHeure(l.at)}${l.par ? ` · par ${echapper(l.par)}` : ""}${l.ip ? ` · ${echapper(l.ip)}` : ""}</div>
      </div>
    </div>`).join("");
}

// ---------- démarrage ----------

document.getElementById("comptes").addEventListener("click", (evenement) => {
  const bouton = evenement.target.closest("button[data-action]");
  if (!bouton) return;
  const compte = comptes.find((c) => c.identifiant === bouton.closest("[data-compte]").dataset.compte);
  agir(bouton.dataset.action, compte);
});

document.getElementById("filtres").addEventListener("click", (evenement) => {
  const bouton = evenement.target.closest("button");
  if (!bouton) return;
  filtre = bouton.dataset.filtre;
  document.querySelectorAll("#filtres button").forEach((b) => b.classList.toggle("principal", b === bouton));
  afficherComptes();
});

document.getElementById("c-role").addEventListener("change", formulaireSelonRole);
document.getElementById("c-code").addEventListener("input", (e) => { e.target.dataset.auto = "non"; });
document.getElementById("creation").addEventListener("submit", creer);
document.getElementById("demande").addEventListener("submit", enregistrerDemande);
document.getElementById("m-annuler").addEventListener("click", () => document.getElementById("modifier").close("annuler"));

document.getElementById("deconnexion").addEventListener("click", async () => {
  await fetch("/api/auth/deconnexion", { method: "POST" });
  location.replace("/connexion");
});

api("/auth/moi").then(({ utilisateur: u }) => {
  if (!u) return location.replace("/connexion");
  if (u.role !== "admin") return location.replace("/");
  moi = u;
  document.getElementById("utilisateur").textContent = `${u.nom} · Administration`;
  formulaireSelonRole();
  charger();
});
