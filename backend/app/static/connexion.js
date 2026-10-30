const ETAPES = { mot_de_passe: "etape-mot-de-passe", totp: "etape-totp", activation_2fa: "etape-activation" };

async function appel(chemin, corps) {
  const reponse = await fetch(`/api/auth${chemin}`, {
    method: corps === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: corps === undefined ? undefined : JSON.stringify(corps),
  });
  const donnees = await reponse.json().catch(() => ({}));
  if (!reponse.ok) throw Object.assign(new Error(donnees.detail || `Erreur ${reponse.status}`), { status: reponse.status });
  return donnees;
}

function message(texte) {
  document.getElementById("message").textContent = texte || "";
}

async function afficher(etape) {
  for (const [nom, id] of Object.entries(ETAPES)) document.getElementById(id).hidden = nom !== etape;
  if (etape === "activation_2fa") {
    const { qr_svg: qr, secret } = await appel("/2fa/debut", {});
    document.getElementById("qr").innerHTML = qr;
    document.getElementById("secret").textContent = secret.match(/.{1,4}/g).join(" ");
  }
  document.querySelector(`#${ETAPES[etape]} input`).focus();
}

function suite(resultat) {
  if (resultat.etape === "ok") location.replace("/");
  else afficher(resultat.etape);
}

async function soumettre(evenement, action) {
  evenement.preventDefault();
  message("");
  const bouton = evenement.target.querySelector("button");
  bouton.disabled = true;
  try {
    suite(await action());
  } catch (erreur) {
    message(erreur.message);
    if (erreur.status === 401 && evenement.target.id !== ETAPES.mot_de_passe) {
      // Session de connexion expirée : on recommence depuis le mot de passe.
      if (/Recommencez/.test(erreur.message)) afficher("mot_de_passe");
    }
  } finally {
    bouton.disabled = false;
  }
}

document.getElementById(ETAPES.mot_de_passe).addEventListener("submit", (e) =>
  soumettre(e, () =>
    appel("/connexion", {
      identifiant: document.getElementById("identifiant").value,
      mot_de_passe: document.getElementById("mot-de-passe").value,
    }).finally(() => { document.getElementById("mot-de-passe").value = ""; })));

document.getElementById(ETAPES.totp).addEventListener("submit", (e) =>
  soumettre(e, () => appel("/totp", { code: document.getElementById("code").value })));

document.getElementById(ETAPES.activation_2fa).addEventListener("submit", (e) =>
  soumettre(e, () => appel("/2fa/activer", { code: document.getElementById("code-activation").value })));

appel("/moi")
  .then(suite)
  .catch(() => afficher("mot_de_passe"));
