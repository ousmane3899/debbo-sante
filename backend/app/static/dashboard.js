const ETAPES = [
  ["ouverte", "Alerte"],
  ["sf_jointe", "Femme appelée"],
  ["transport_parti", "Transport parti"],
  ["arrivee_confirmee", "Arrivée confirmée"],
];
const NIVEAUX = { urgence: "Urgence", consultation_24h: "Sous 24 h", conseil: "Conseil" };
const CONTEXTES = { grossesse: "Grossesse", postpartum: "Post-partum", nouveau_ne: "Nouveau-né", enfant: "Enfant" };
const MOTIFS = {
  sage_femme: "Veut parler à une sage-femme",
  rappel: "Demande de rappel",
  rappel_debbo: "A demandé un rappel pendant l'appel de Debbo",
};
const CRENEAUX = { matin: "le matin", apres_midi: "l'après-midi", soir: "le soir" };

const echapper = (texte) =>
  String(texte ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const jour = (iso) => new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });

function duree(minutes) {
  if (minutes == null) return "–";
  if (minutes < 60) return `${Math.floor(minutes)} min`;
  return `${Math.floor(minutes / 60)} h ${String(Math.floor(minutes % 60)).padStart(2, "0")}`;
}

async function api(chemin, options = {}) {
  const reponse = await fetch(`/api${chemin}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (reponse.status === 401) {
    location.replace("/connexion");
    throw new Error("Connexion requise");
  }
  if (!reponse.ok) {
    const corps = await reponse.json().catch(() => ({}));
    throw new Error(corps.detail || `Erreur ${reponse.status}`);
  }
  return reponse.json();
}

function demander(libelle) {
  const dialogue = document.getElementById("saisie");
  const champ = document.getElementById("saisie-champ");
  document.getElementById("saisie-libelle").textContent = libelle;
  champ.value = "";
  dialogue.returnValue = "";
  dialogue.showModal();
  return new Promise((resoudre) => {
    dialogue.addEventListener(
      "close",
      () => resoudre(dialogue.returnValue === "ok" && champ.value.trim() ? champ.value.trim() : null),
      { once: true },
    );
  });
}

async function transition(id, etat) {
  const corps = { etat };
  if (etat === "transport_parti") {
    corps.transporteur = await demander("Qui fait le transport ? (nom, véhicule)");
    if (!corps.transporteur) return;
  }
  if (etat === "arrivee_confirmee") {
    corps.structure = await demander("Structure d'accueil");
    if (!corps.structure) return;
  }
  if (etat === "cloturee") {
    corps.note = await demander("Motif de clôture (ex. prise en charge terminée, fausse alerte)");
    if (!corps.note) return;
  }
  try {
    await api(`/alertes/${id}/transition`, { method: "POST", body: JSON.stringify(corps) });
  } catch (erreur) {
    alert(erreur.message);
  }
  rafraichir();
}

function actionsAlerte(a) {
  const boutons = {
    ouverte: [["sf_jointe", "J'ai appelé la femme", true]],
    sf_jointe: [["transport_parti", "Transport parti", true], ["arrivee_confirmee", "Arrivée sans transport"]],
    transport_parti: [["arrivee_confirmee", "Arrivée confirmée", true]],
  }[a.etat] || [];
  boutons.push(["cloturee", "Clôturer"]);
  return boutons
    .map(([etat, libelle, principal]) =>
      `<button class="${principal ? "principal" : ""}" data-alerte="${a.id}" data-etat="${etat}">${libelle}</button>`)
    .join("");
}

function carteAlerte(a) {
  const rangActuel = ETAPES.findIndex(([etat]) => etat === a.etat);
  const etapes = ETAPES.map(([, libelle], i) => `<span class="etape ${i <= rangActuel ? "faite" : ""}">${libelle}</span>`).join("");
  const signes = a.signes.filter((s) => s.code !== "urgence_declaree" || a.signes.length === 1);
  return `
    <article class="alerte ${a.niveau === "urgence" ? "urgence" : ""} ${a.escalade ? "escalade" : ""}">
      <div class="entete">
        <span class="signes">${signes.map((s) => echapper(s.libelle)).join(" · ")}</span>
        <span class="chrono">${duree(a.minutes_ecoulees)}</span>
      </div>
      <div class="meta">
        <span class="badge ${a.niveau === "urgence" ? "urgence" : ""}">${NIVEAUX[a.niveau] || a.niveau}</span>
        ${CONTEXTES[a.contexte] || ""} · ${a.nom ? `${echapper(a.nom)} · ` : ""}${a.telephone ? echapper(a.telephone) : "sans téléphone"} ${a.code ? `· ${echapper(a.code)}` : "· non inscrite"}
        ${a.relais ? `· signalée par la relais ${echapper(a.relais.nom)}${a.relais.telephone ? ` (${echapper(a.relais.telephone)})` : ""}` : ""}
        ${a.transporteur ? `· transport : ${echapper(a.transporteur)}` : ""}
        ${a.escalade ? `· <strong>personne n'a rappelé depuis plus de 5 min</strong>` : ""}
      </div>
      <div class="etapes">${etapes}</div>
      <div class="actions">${actionsAlerte(a)}</div>
    </article>`;
}

function ligneRappel(r) {
  return `
    <div class="ligne">
      <div>
        <div>${r.prioritaire ? `<span class="badge accent">Prioritaire</span> ` : ""}${MOTIFS[r.motif] || r.motif}</div>
        <div class="meta">${echapper(r.telephone)}${r.creneau ? ` · rappeler ${CRENEAUX[r.creneau]}` : ""}</div>
      </div>
      <button data-rappel="${r.id}">Fait</button>
    </div>`;
}

function ligneFemme(f) {
  const prochain = f.prochain_contact
    ? `${f.prochain_contact.type} le ${jour(f.prochain_contact.date)}`
    : "aucun contact prévu";
  return `
    <div class="ligne ${f.perdue_de_vue ? "retard" : ""}">
      <div>
        <div><strong>${echapper(f.nom || f.code)}</strong> ${f.perdue_de_vue ? `<span class="badge urgence">${f.contacts_en_retard} contact(s) manqué(s)</span>` : ""}${f.zone ? "" : ` <span class="badge">sans zone</span>`}</div>
        <div class="meta">${f.nom ? `${echapper(f.code)} · ` : ""}${f.telephone ? echapper(f.telephone) : "sans téléphone"} · ${prochain}${f.terme_prevu ? ` · terme ≈ ${jour(f.terme_prevu)}` : ""}${f.relais ? ` · relais ${echapper(f.relais)}` : ""}</div>
      </div>
      <div class="actions">
        ${!f.zone && utilisateur?.role === "sage_femme" ? `<button data-rattacher="${f.id}">Rattacher à mon aire</button>` : ""}
        ${f.prochain_contact ? `<button data-contact="${f.prochain_contact.id}">Venue</button>` : ""}
      </div>
    </div>`;
}

function ligneVaccin(v) {
  return `
    <div class="ligne ${v.en_retard ? "retard" : ""}">
      <div>
        <div><strong>${echapper(v.seance)}</strong> · ${echapper(v.mere)}${v.rang > 1 ? ` (bébé ${v.rang})` : ""}
          ${v.en_retard ? `<span class="badge urgence">En retard</span>` : ""}</div>
        <div class="meta">${v.antigenes.map(echapper).join(" · ")} · prévu le ${jour(v.date_prevue)}${v.telephone ? ` · ${echapper(v.telephone)}` : ""}</div>
      </div>
      <button data-vaccin="${v.id}">Fait</button>
    </div>`;
}

function tuiles(i) {
  const liste = [
    [i.alertes_actives, "alertes en cours"],
    [i.rappels_en_attente, "rappels en attente"],
    [i.femmes_suivies, "femmes suivies"],
    [i.perdues_de_vue, "perdues de vue"],
    [i.part_arrivee_confirmee == null ? "–" : `${i.part_arrivee_confirmee} %`, "urgences arrivées au soin"],
    [duree(i.delai_median_arrivee_min), "délai médian alerte → arrivée"],
    [i.taux_reponse_7j == null ? "–" : `${i.taux_reponse_7j} %`, `appels de Debbo décrochés (${i.appels_7j} en 7 j)`],
    [i.taches_relais_ouvertes, "visites demandées aux relais"],
    [i.enfants_zero_dose, `enfants zéro dose (sur ${i.enfants_suivis} suivis)`],
    [i.part_penta3_a_jour == null ? "–" : `${i.part_penta3_a_jour} %`, "enfants à jour du Penta 3"],
  ];
  return liste.map(([valeur, libelle]) => `<div class="tuile"><div class="valeur">${valeur}</div><div class="libelle">${libelle}</div></div>`).join("");
}

function remplir(id, elements, rendu, vide) {
  document.getElementById(id).innerHTML = elements.length ? elements.map(rendu).join("") : `<div class="vide">${vide}</div>`;
}

async function rafraichir() {
  try {
    const [alertes, rappels, femmes, indicateurs, vaccinations] = await Promise.all([
      api("/alertes"), api("/rappels"), api("/femmes"), api("/indicateurs"), api("/vaccinations"),
    ]);
    remplir("vaccinations", vaccinations, ligneVaccin, "Aucune vaccination à prévoir.");
    remplir("alertes", alertes, carteAlerte, "Aucune alerte en cours.");
    remplir("rappels", rappels, ligneRappel, "Aucune demande en attente.");
    remplir("femmes", femmes, ligneFemme, "Aucune femme inscrite pour l'instant.");
    document.getElementById("indicateurs").innerHTML = tuiles(indicateurs);
  } catch (erreur) {
    console.error(erreur);
  }
}

document.addEventListener("click", async (evenement) => {
  const bouton = evenement.target.closest("button");
  if (!bouton) return;
  if (bouton.dataset.alerte) {
    transition(bouton.dataset.alerte, bouton.dataset.etat);
  } else if (bouton.dataset.rappel) {
    await api(`/rappels/${bouton.dataset.rappel}/traiter`, { method: "POST" });
    rafraichir();
  } else if (bouton.dataset.vaccin) {
    await api(`/vaccinations/${bouton.dataset.vaccin}/fait`, { method: "POST" });
    rafraichir();
  } else if (bouton.dataset.rattacher) {
    if (!confirm(`Rattacher cette femme à votre aire (${utilisateur.zone}) ? Les autres sages-femmes ne verront plus son dossier.`)) return;
    await api(`/femmes/${bouton.dataset.rattacher}/zone`, { method: "POST", body: JSON.stringify({ zone: utilisateur.zone }) });
    rafraichir();
  } else if (bouton.dataset.contact) {
    await api(`/contacts/${bouton.dataset.contact}/realise`, { method: "POST" });
    rafraichir();
  }
});

document.getElementById("saisie-annuler").addEventListener("click", () => document.getElementById("saisie").close("annuler"));

document.getElementById("deconnexion").addEventListener("click", async () => {
  await fetch("/api/auth/deconnexion", { method: "POST" });
  location.replace("/connexion");
});

const ROLES = { sage_femme: "Sage-femme", district: "District" };
let utilisateur = null;

api("/auth/moi").then(({ utilisateur: u }) => {
  utilisateur = u;
  if (!u) return location.replace("/connexion");
  if (u.role === "admin") return location.replace("/admin");
  if (!ROLES[u.role]) {
    document.querySelector("main").innerHTML = `<p class="vide">Ce compte (${echapper(u.role)}) n'a pas accès aux dossiers de santé.</p>`;
    return;
  }
  document.getElementById("utilisateur").textContent = `${u.nom} · ${ROLES[u.role]}${u.zone ? ` · ${u.zone}` : ""}`;
  if (u.role === "district") activerDhis2();
  api("/regles").then((r) => { document.getElementById("regles-version").textContent = r.version; });
  rafraichir();
  setInterval(rafraichir, 5000);
});

// ---------- rapport mensuel DHIS2 (équipe de district) ----------

function moisPrecedent() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function tableauDhis2(r) {
  const zones = Object.keys(r.totaux);
  if (!zones.length) return `<p class="vide">Aucune activité enregistrée ce mois-là.</p>`;
  const entete = zones
    .map((z) => `<th>${echapper(z)}${r.zones[z] ? "" : ` <span class="badge">non reliée</span>`}</th>`)
    .join("");
  const lignes = Object.entries(r.elements)
    .map(([cle, e]) => {
      const cellules = zones.map((z) => `<td class="${e.relie && r.zones[z] ? "" : "non-relie"}">${r.totaux[z][cle] ?? 0}</td>`).join("");
      return `<tr><td>${echapper(e.libelle)}${e.relie ? "" : ` <span class="badge">non relié</span>`}</td>${cellules}</tr>`;
    })
    .join("");
  const zonesNonReliees = zones.filter((z) => !r.zones[z]);
  const indicateursNonRelies = Object.values(r.elements).filter((e) => !e.relie).length;
  const parties = [
    zonesNonReliees.length ? `${zonesNonReliees.length} zone(s) (${zonesNonReliees.map(echapper).join(", ")})` : "",
    indicateursNonRelies ? `${indicateursNonRelies} indicateur(s)` : "",
  ].filter(Boolean);
  const manques = parties.length
    ? `<p class="avertissement">Pas encore relié à DHIS2 : ${parties.join(" et ")}. Ces valeurs ne seront pas envoyées.
       Complétez <code>dhis2_correspondances.json</code> avec les identifiants du ministère.</p>`
    : "";
  return `<div class="defile"><table><thead><tr><th>Indicateur</th>${entete}</tr></thead><tbody>${lignes}</tbody></table></div>${manques}`;
}

async function chargerDhis2() {
  const periode = document.getElementById("dhis2-mois").value;
  if (!periode) return;
  const etat = document.getElementById("dhis2-etat");
  const bouton = document.getElementById("dhis2-envoyer");
  try {
    const r = await api(`/dhis2/apercu?periode=${periode}`);
    document.getElementById("dhis2-tableau").innerHTML = tableauDhis2(r);
    bouton.disabled = !r.configure || !r.valeurs_pretes;
    etat.textContent = !r.configure
      ? "Serveur DHIS2 non configuré : envoi impossible."
      : `${r.valeurs_pretes} valeur(s) prête(s) à envoyer.`;
    document.getElementById("dhis2-envois").innerHTML = r.envois.length
      ? `Derniers envois : ${r.envois.map((e) => `${echapper(e.periode)} (${e.statut === "ok" ? "réussi" : "échec"}, ${e.nb_valeurs} valeurs)`).join(" · ")}`
      : "Aucun envoi pour l'instant.";
  } catch (erreur) {
    etat.textContent = erreur.message;
  }
}

async function envoyerDhis2() {
  const periode = document.getElementById("dhis2-mois").value;
  if (!confirm(`Envoyer les totaux de ${periode} à DHIS2 ? Un nouvel envoi du même mois remplace les valeurs précédentes.`)) return;
  try {
    const envoi = await api("/dhis2/envoyer", { method: "POST", body: JSON.stringify({ periode }) });
    alert(envoi.statut === "ok" ? "Envoi réussi." : `Échec de l'envoi : ${envoi.resume.erreur}`);
  } catch (erreur) {
    alert(erreur.message);
  }
  chargerDhis2();
}

function activerDhis2() {
  const champ = document.getElementById("dhis2-mois");
  champ.value = moisPrecedent();
  champ.max = new Date().toISOString().slice(0, 7);
  champ.addEventListener("change", chargerDhis2);
  document.getElementById("dhis2-envoyer").addEventListener("click", envoyerDhis2);
  document.getElementById("dhis2").hidden = false;
  chargerDhis2();
}
