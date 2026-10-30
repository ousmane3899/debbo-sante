"""Connexion, sessions et contrôle d'accès.

- Soignants (sage-femme, district, admin) : mot de passe, puis code TOTP à 6 chiffres.
  À la première connexion, l'activation du TOTP est obligatoire avant tout accès.
- Relais : code relais + PIN à 6 chiffres ; session longue (30 jours, prolongée à
  chaque usage) pour couvrir les semaines sans réseau.
- 5 échecs de suite verrouillent le compte 15 minutes.
- Le cookie de session est HttpOnly, Secure et SameSite=Strict.
"""

from datetime import timedelta

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import securite
from app.db import get_session
from app.journal import tracer
from app.models import Relais, SessionAuth, Utilisateur, maintenant

COOKIE = "debbo_session"
DUREE_PARTIELLE = timedelta(minutes=10)
DUREE_SOIGNANT = timedelta(hours=12)
DUREE_RELAIS = timedelta(days=30)
MAX_ECHECS = 5
DUREE_VERROU = timedelta(minutes=15)
ROLES_SOIGNANTS = ("sage_femme", "district", "admin")

router = APIRouter(prefix="/api/auth")

ECHEC = HTTPException(401, "Identifiant ou mot de passe incorrect")
VERROUILLE = HTTPException(429, "Trop de tentatives. Réessayez dans 15 minutes.")


class ConnexionIn(BaseModel):
    identifiant: str
    mot_de_passe: str


class CodeIn(BaseModel):
    code: str


class RelaisIn(BaseModel):
    code: str
    pin: str


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _poser_cookie(response: Response, jeton: str, duree: timedelta) -> None:
    response.set_cookie(
        COOKIE, jeton, max_age=int(duree.total_seconds()), httponly=True, secure=True, samesite="strict", path="/"
    )


def _ouvrir_session(db: Session, response: Response, utilisateur: Utilisateur, niveau: str, duree: timedelta) -> None:
    jeton = securite.nouveau_jeton()
    db.add(SessionAuth(empreinte=securite.empreinte_jeton(jeton), utilisateur=utilisateur, niveau=niveau,
                       expire_at=maintenant() + duree))
    _poser_cookie(response, jeton, duree)


def _session(request: Request, db: Session) -> SessionAuth | None:
    jeton = request.cookies.get(COOKIE)
    if not jeton:
        return None
    s = db.scalar(select(SessionAuth).where(SessionAuth.empreinte == securite.empreinte_jeton(jeton)))
    if s is None or s.expire_at < maintenant() or not s.utilisateur.actif:
        return None
    return s


def _verrouille(u: Utilisateur) -> bool:
    return u.verrouille_jusqua is not None and u.verrouille_jusqua > maintenant()


def _echec(db: Session, u: Utilisateur, request: Request, action: str) -> None:
    u.echecs += 1
    if u.echecs >= MAX_ECHECS:
        u.verrouille_jusqua = maintenant() + DUREE_VERROU
        u.echecs = 0
        tracer(db, "compte_verrouille", u.id, u.identifiant, _ip(request))
    tracer(db, action, u.id, u.identifiant, _ip(request))
    db.commit()


def _profil(u: Utilisateur) -> dict:
    profil = {"identifiant": u.identifiant, "nom": u.nom, "role": u.role, "zone": u.zone}
    if u.relais:
        profil["relais"] = {"code": u.relais.code, "nom": u.relais.nom, "zone": u.relais.zone}
    return profil


# ---------- dépendances ----------

def exiger(*roles: str):
    """Dépendance FastAPI : session complète et rôle autorisé, sinon 401 / 403."""

    def dependance(request: Request, response: Response, db: Session = Depends(get_session)) -> Utilisateur:
        s = _session(request, db)
        if s is None or s.niveau != "complet":
            raise HTTPException(401, "Connexion requise")
        if roles and s.utilisateur.role not in roles:
            raise HTTPException(403, "Accès refusé pour ce rôle")
        if s.utilisateur.role == "relais" and s.expire_at - maintenant() < DUREE_RELAIS - timedelta(days=1):
            # Session glissante : chaque usage repousse l'échéance à 30 jours.
            s.expire_at = maintenant() + DUREE_RELAIS
            db.commit()
            _poser_cookie(response, request.cookies[COOKIE], DUREE_RELAIS)
        return s.utilisateur

    return dependance


def _session_partielle(request: Request, db: Session = Depends(get_session)) -> SessionAuth:
    s = _session(request, db)
    if s is None or s.niveau != "mot_de_passe":
        raise HTTPException(401, "Recommencez la connexion")
    return s


def _promouvoir(db: Session, response: Response, s: SessionAuth) -> None:
    """Remplace la session partielle par une session complète, avec un nouveau jeton."""
    utilisateur = s.utilisateur
    db.delete(s)
    _ouvrir_session(db, response, utilisateur, "complet", DUREE_SOIGNANT)


# ---------- soignants ----------

@router.post("/connexion")
def connexion(corps: ConnexionIn, request: Request, response: Response, db: Session = Depends(get_session)) -> dict:
    u = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == corps.identifiant.strip().lower()))
    if u is None or u.role not in ROLES_SOIGNANTS or not u.actif:
        securite.verifier(corps.mot_de_passe, securite.EMPREINTE_FACTICE)  # même durée qu'un vrai échec
        tracer(db, "connexion_echec", None, corps.identifiant[:64], _ip(request))
        db.commit()
        raise ECHEC
    if _verrouille(u):
        raise VERROUILLE
    if not securite.verifier(corps.mot_de_passe, u.secret_hache):
        _echec(db, u, request, "connexion_echec")
        raise ECHEC
    u.echecs = 0
    _ouvrir_session(db, response, u, "mot_de_passe", DUREE_PARTIELLE)
    tracer(db, "mot_de_passe_ok", u.id, u.identifiant, _ip(request))
    db.commit()
    return {"etape": "totp" if u.totp_actif else "activation_2fa"}


@router.post("/totp")
def totp(corps: CodeIn, request: Request, response: Response, s: SessionAuth = Depends(_session_partielle),
         db: Session = Depends(get_session)) -> dict:
    u = s.utilisateur
    if not u.totp_actif:
        raise HTTPException(409, "La double authentification n'est pas encore activée")
    if _verrouille(u):
        raise VERROUILLE
    pas = securite.verifier_totp(u.totp_secret, corps.code, u.totp_dernier_pas)
    if pas is None:
        _echec(db, u, request, "totp_echec")
        raise HTTPException(401, "Code incorrect")
    u.totp_dernier_pas = pas
    u.echecs = 0
    _promouvoir(db, response, s)
    tracer(db, "connexion", u.id, u.identifiant, _ip(request))
    db.commit()
    return {"etape": "ok", "utilisateur": _profil(u)}


@router.post("/2fa/debut")
def debut_2fa(s: SessionAuth = Depends(_session_partielle), db: Session = Depends(get_session)) -> dict:
    u = s.utilisateur
    if u.totp_actif:
        raise HTTPException(409, "Déjà activée")
    u.totp_secret = securite.nouveau_secret_totp()
    db.commit()
    uri = securite.uri_totp(u.totp_secret, u.identifiant)
    return {"secret": u.totp_secret, "uri": uri, "qr_svg": segno.make(uri, error="m").svg_inline(scale=5, border=2)}


@router.post("/2fa/activer")
def activer_2fa(corps: CodeIn, request: Request, response: Response, s: SessionAuth = Depends(_session_partielle),
                db: Session = Depends(get_session)) -> dict:
    u = s.utilisateur
    if u.totp_actif or not u.totp_secret:
        raise HTTPException(409, "Commencez par afficher le QR code")
    pas = securite.verifier_totp(u.totp_secret, corps.code, None)
    if pas is None:
        _echec(db, u, request, "totp_echec")
        raise HTTPException(401, "Code incorrect")
    u.totp_actif = True
    u.totp_dernier_pas = pas
    _promouvoir(db, response, s)
    tracer(db, "2fa_activee", u.id, u.identifiant, _ip(request))
    db.commit()
    return {"etape": "ok", "utilisateur": _profil(u)}


# ---------- relais ----------

@router.post("/relais")
def connexion_relais(corps: RelaisIn, request: Request, response: Response, db: Session = Depends(get_session)) -> dict:
    relais = db.scalar(select(Relais).where(Relais.code == corps.code.strip().upper()))
    u = db.scalar(select(Utilisateur).where(Utilisateur.relais_id == relais.id)) if relais else None
    if u is None or not u.actif:
        securite.verifier(corps.pin, securite.EMPREINTE_FACTICE)
        tracer(db, "connexion_echec", None, corps.code[:64], _ip(request))
        db.commit()
        raise HTTPException(401, "Code relais ou PIN incorrect")
    if _verrouille(u):
        raise VERROUILLE
    if not securite.verifier(corps.pin, u.secret_hache):
        _echec(db, u, request, "connexion_echec")
        raise HTTPException(401, "Code relais ou PIN incorrect")
    u.echecs = 0
    _ouvrir_session(db, response, u, "complet", DUREE_RELAIS)
    tracer(db, "connexion", u.id, u.identifiant, _ip(request))
    db.commit()
    return {"utilisateur": _profil(u)}


# ---------- commun ----------

@router.get("/moi")
def moi(request: Request, db: Session = Depends(get_session)) -> dict:
    s = _session(request, db)
    if s is None:
        raise HTTPException(401, "Connexion requise")
    if s.niveau != "complet":
        return {"etape": "totp" if s.utilisateur.totp_actif else "activation_2fa"}
    return {"etape": "ok", "utilisateur": _profil(s.utilisateur)}


@router.post("/deconnexion")
def deconnexion(request: Request, response: Response, db: Session = Depends(get_session)) -> dict:
    s = _session(request, db)
    if s is not None:
        tracer(db, "deconnexion", s.utilisateur_id, s.utilisateur.identifiant, _ip(request))
        db.delete(s)
        db.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {}


def fermer_sessions(db: Session, utilisateur: Utilisateur) -> None:
    db.execute(delete(SessionAuth).where(SessionAuth.utilisateur_id == utilisateur.id))
