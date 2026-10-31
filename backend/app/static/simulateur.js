const ecran = document.getElementById("ecran");
const boutonEnvoyer = document.getElementById("envoyer");
let parcours = null; // null = pas de session en cours
let sessionId = "";

function afficher(texte, avecSaisie) {
  ecran.textContent = texte;
  if (avecSaisie) {
    const champ = document.createElement("input");
    champ.id = "reponse";
    champ.inputMode = "numeric";
    champ.autocomplete = "off";
    champ.addEventListener("keydown", (e) => { if (e.key === "Enter") envoyer(); });
    ecran.appendChild(champ);
    champ.focus();
  }
  boutonEnvoyer.textContent = avecSaisie ? "Envoyer" : "Composer *xxx#";
}

async function appeler() {
  const corps = new URLSearchParams({
    sessionId,
    serviceCode: "*xxx#",
    phoneNumber: document.getElementById("numero").value.trim(),
    text: parcours.join("*"),
  });
  const reponse = await fetch("/ussd", { method: "POST", body: corps });
  const texte = await reponse.text();
  if (texte.startsWith("CON ")) {
    afficher(texte.slice(4), true);
  } else {
    afficher(texte.replace(/^END /, ""), false);
    parcours = null;
  }
}

function envoyer() {
  if (parcours === null) {
    parcours = [];
    sessionId = `sim-${Date.now()}`;
    appeler();
    return;
  }
  const champ = document.getElementById("reponse");
  if (!champ || !champ.value.trim()) return;
  parcours.push(champ.value.trim());
  appeler();
}

function raccrocher() {
  parcours = null;
  afficher("Session terminée.", false);
}

boutonEnvoyer.addEventListener("click", envoyer);
document.getElementById("annuler").addEventListener("click", raccrocher);

const touches = document.getElementById("touches");
for (const touche of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]) {
  const bouton = document.createElement("button");
  bouton.textContent = touche;
  bouton.addEventListener("click", () => {
    const champ = document.getElementById("reponse");
    if (champ) { champ.value += touche; champ.focus(); }
  });
  touches.appendChild(bouton);
}

// ---------- appels sortants simulés (« Debbo m'appelle ») ----------

const zoneAppels = document.getElementById("appels");

async function repondreAppel(reference, statut, touche) {
  await fetch("/api/telephonie/resultat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference, statut, touche }),
  });
  chargerAppels();
}

async function chargerAppels() {
  const telephone = document.getElementById("numero").value.trim();
  const appels = await fetch(`/api/simulateur/appels?telephone=${encodeURIComponent(telephone)}`)
    .then((r) => (r.ok ? r.json() : []))
    .catch(() => []);
  zoneAppels.replaceChildren();
  if (!appels.length) {
    const vide = document.createElement("p");
    vide.className = "vide";
    vide.textContent = "Aucun appel en cours.";
    zoneAppels.append(vide);
    return;
  }
  for (const appel of appels) {
    const carte = document.createElement("div");
    carte.className = "ligne appel";
    const texte = document.createElement("div");
    texte.innerHTML = `<div class="meta">Appel entrant · essai ${appel.tentative}</div>`;
    const message = document.createElement("div");
    message.textContent = appel.message;
    texte.append(message);
    const actions = document.createElement("div");
    actions.className = "actions";
    for (const [libelle, statut, touche] of [["Touche 1", "repondu", "1"], ["Touche 2", "repondu", "2"], ["Ne pas décrocher", "sans_reponse", null]]) {
      const bouton = document.createElement("button");
      bouton.textContent = libelle;
      bouton.addEventListener("click", () => repondreAppel(appel.reference, statut, touche));
      actions.append(bouton);
    }
    carte.append(texte, actions);
    zoneAppels.append(carte);
  }
}

document.getElementById("numero").addEventListener("change", chargerAppels);
chargerAppels();
setInterval(chargerAppels, 3000);
