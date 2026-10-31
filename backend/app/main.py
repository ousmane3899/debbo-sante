import asyncio
import hmac
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app import admin, auth, config, rappels, recherche, relais, telephonie, ussd, vie_privee
from app.api import router
from app.db import SessionLocal, get_session, init_db
from app.models import maintenant

journal_serveur = logging.getLogger("debbo")
INTERVALLE_PLANIFICATEUR = 60  # secondes

STATIC = Path(__file__).with_name("static")

ENTETES_SECURITE = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
METHODES_ECRITURE = {"POST", "PUT", "PATCH", "DELETE"}


def _tour_planificateur() -> None:
    with SessionLocal() as session:
        bilan = rappels.tour(session)
        bilan["effacements"] = vie_privee.traiter_effacements_en_attente(session, maintenant())
    if any(bilan.values()):
        journal_serveur.info("Debbo m'appelle : %s", bilan)


async def _boucle_planificateur() -> None:
    while True:
        try:
            await run_in_threadpool(_tour_planificateur)
        except Exception:  # une erreur ne doit pas arrêter les rappels suivants
            journal_serveur.exception("Échec du planificateur de rappels")
        await asyncio.sleep(INTERVALLE_PLANIFICATEUR)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(migrer_automatiquement=not config.EN_PROD)
    tache = asyncio.create_task(_boucle_planificateur()) if config.PLANIFICATEUR_ACTIF else None
    yield
    if tache:
        tache.cancel()
        with suppress(asyncio.CancelledError):
            await tache


app = FastAPI(title="Debbo Santé", version="0.2.0", lifespan=lifespan,
              docs_url=None if config.EN_PROD else "/docs", redoc_url=None, openapi_url=None if config.EN_PROD else "/openapi.json")


@app.middleware("http")
async def securite(request: Request, appel_suivant):
    # Une écriture sur l'API venue d'un autre site est refusée (en plus du cookie SameSite=Strict).
    origine = request.headers.get("origin")
    if (request.method in METHODES_ECRITURE and request.url.path.startswith("/api/") and origine
            and urlparse(origine).netloc != request.headers.get("host")):
        return JSONResponse({"detail": "Origine refusée"}, status_code=403)
    reponse = await appel_suivant(request)
    for nom, valeur in ENTETES_SECURITE.items():
        reponse.headers.setdefault(nom, valeur)
    if request.url.path.startswith("/api/"):
        reponse.headers["Cache-Control"] = "no-store"
    else:
        # Interface : le navigateur revérifie chaque fichier (304 s'il n'a pas changé),
        # pour qu'une mise à jour soit vue tout de suite par les soignants.
        reponse.headers.setdefault("Cache-Control", "no-cache")
    if config.EN_PROD:
        reponse.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return reponse


app.include_router(auth.router)
app.include_router(router)
app.include_router(relais.router)
app.include_router(admin.router)
app.include_router(telephonie.router)
app.include_router(recherche.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
# Appli des relais : servie sous /relais/ pour que son service worker couvre tout ce chemin.
app.mount("/relais", StaticFiles(directory=STATIC / "relais", html=True), name="relais")


def verifier_agregateur(x_debbo_ussd_secret: str | None = Header(default=None)) -> None:
    """L'agrégateur USSD prouve son identité par un secret partagé (obligatoire en production)."""
    if config.USSD_SECRET and not hmac.compare_digest(x_debbo_ussd_secret or "", config.USSD_SECRET):
        raise HTTPException(403, "Agrégateur non reconnu")


@app.post("/ussd", response_class=PlainTextResponse, dependencies=[Depends(verifier_agregateur)])
def point_ussd(
    phoneNumber: str = Form(...),
    text: str = Form(""),
    sessionId: str = Form(""),
    serviceCode: str = Form(""),
    session: Session = Depends(get_session),
) -> str:
    return ussd.repondre(session, phoneNumber, text)


@app.get("/", include_in_schema=False)
def tableau_de_bord() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/connexion", include_in_schema=False)
def page_connexion() -> FileResponse:
    return FileResponse(STATIC / "connexion.html")


@app.get("/simulateur", include_in_schema=False)
def simulateur() -> FileResponse:
    if not config.SIMULATEUR_ACTIF:
        raise HTTPException(404)
    return FileResponse(STATIC / "simulateur.html")
