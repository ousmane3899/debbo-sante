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
