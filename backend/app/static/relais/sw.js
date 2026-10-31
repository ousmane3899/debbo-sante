// Service worker : garde l'appli en cache pour qu'elle s'ouvre sans réseau.
// Réseau d'abord (fichiers à jour quand il y a du réseau), cache sinon.
// Les appels /api ne passent jamais par le cache : l'appli gère elle-même la file d'envoi.

const CACHE = "debbo-relais-v5";
const COQUILLE = ["./", "index.html", "app.js", "moteur.js", "stockage.js", "relais.css", "manifest.webmanifest", "icone.svg"];

self.addEventListener("install", (evenement) => {
  evenement.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(COQUILLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evenement) => {
  evenement.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((c) => c !== CACHE).map((c) => caches.delete(c))))
      .then(() => self.clients.claim()),
  );
});

async function reseauPuisCache(requete) {
  try {
    // « no-cache » : toujours revalider auprès du serveur (304 si rien n'a changé),
    // pour qu'une relais n'utilise jamais une ancienne version de l'appli.
    const reponse = await Promise.race([
      fetch(requete.url, { cache: "no-cache", credentials: "same-origin" }),
      new Promise((_, rejeter) => setTimeout(() => rejeter(new Error("lent")), 4000)),
    ]);
    if (reponse.ok) (await caches.open(CACHE)).put(requete, reponse.clone());
    return reponse;
  } catch {
    const enCache = await caches.match(requete, { ignoreSearch: true });
    return enCache ?? caches.match("index.html");
  }
}

self.addEventListener("fetch", (evenement) => {
  const url = new URL(evenement.request.url);
  if (evenement.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  evenement.respondWith(reseauPuisCache(evenement.request));
});
