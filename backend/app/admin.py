"""Administration des comptes (rôle « admin » : aucun accès aux dossiers de santé)."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import journal
from app.auth import exiger, fermer_sessions
from app.comptes import creer_relais, creer_soignant, donner_secret_provisoire
from app.db import get_session
from app.models import Femme, JournalAcces, Relais, Utilisateur, maintenant
from app.vie_privee import demander_effacement, retirer_code

router = APIRouter(prefix="/api/admin")
admin = exiger("admin")


class CompteIn(BaseModel):
    role: str  # sage_femme | district | admin | relais
    nom: str
    identifiant: str | None = None  # soignants
    code: str | None = None  # relais
    zone: str | None = None
    telephone: str | None = None  # relais
    telephone_sf: str | None = None  # relais : sage-femme d'astreinte


class ModificationIn(BaseModel):
    nom: str | None = None
    zone: str | None = None
    telephone: str | None = None
    telephone_sf: str | None = None


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _texte(valeur: str | None) -> str | None:
    return valeur.strip() or None if valeur else None


def _fiche(u: Utilisateur) -> dict:
    fiche = {
        "identifiant": u.identifiant, "nom": u.nom, "role": u.role, "zone": u.relais.zone if u.relais else u.zone,
        "actif": u.actif, "totp_actif": u.totp_actif,
        "verrouille": bool(u.verrouille_jusqua and u.verrouille_jusqua > maintenant()),
        "secret_provisoire": u.secret_provisoire,
        "secret_expire_at": u.secret_expire_at.isoformat() + "Z" if u.secret_expire_at else None,
        "provisoire_expire": bool(u.secret_provisoire and u.secret_expire_at and u.secret_expire_at < maintenant()),
    }
    if u.relais:
        fiche["relais"] = {"code": u.relais.code, "telephone": u.relais.telephone,
                           "telephone_sf": u.relais.telephone_sf}
    return fiche


def _avec_secret(u: Utilisateur, secret: str) -> dict:
    return {"compte": _fiche(u), "secret_provisoire": secret}


@router.get("/utilisateurs")
def utilisateurs(_: Utilisateur = Depends(admin), db: Session = Depends(get_session)) -> list[dict]:
    return [_fiche(u) for u in db.scalars(select(Utilisateur).order_by(Utilisateur.role, Utilisateur.identifiant))]


@router.get("/zones")
def zones(_: Utilisateur = Depends(admin), db: Session = Depends(get_session)) -> dict:
    """Zones déjà connues (pour éviter « Ourossogui » et « Ourosogui ») et prochain code relais libre."""
    connues = set(db.scalars(select(Relais.zone))) | set(db.scalars(select(Utilisateur.zone)))
    numeros = [int(m.group(1)) for c in db.scalars(select(Relais.code)) if (m := re.fullmatch(r"R-(\d+)", c))]
    return {"zones": sorted(z for z in connues if z), "prochain_code_relais": f"R-{max(numeros, default=0) + 1:04d}"}


@router.post("/utilisateurs")
def creer_compte(corps: CompteIn, request: Request, a: Utilisateur = Depends(admin),
                 db: Session = Depends(get_session)) -> dict:
    """Le mot de passe (ou PIN) provisoire n'est affiché qu'une fois : il est remis en main propre."""
    nom, zone = _texte(corps.nom), _texte(corps.zone)
    if not nom:
        raise HTTPException(422, "Le nom est obligatoire")
    try:
        if corps.role == "relais":
            code = (corps.code or "").strip().upper()
            if not zone:
                raise ValueError("Une relais doit avoir une zone")
            if db.scalar(select(Relais).where(Relais.code == code)) or db.scalar(
                    select(Utilisateur).where(Utilisateur.identifiant == code.lower())):
                raise HTTPException(409, "Ce code relais existe déjà")
            u = creer_relais(db, code, nom, zone, None, _texte(corps.telephone), _texte(corps.telephone_sf),
                             a.id, _ip(request))
        else:
            identifiant = (corps.identifiant or "").strip().lower()
            if db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant)):
                raise HTTPException(409, "Cet identifiant existe déjà")
            u = creer_soignant(db, identifiant, nom, corps.role, None,
                               zone if corps.role == "sage_femme" else None, a.id, _ip(request))
    except ValueError as erreur:
        raise HTTPException(422, str(erreur)) from None
    secret = donner_secret_provisoire(u)
    db.commit()
    return _avec_secret(u, secret)


def _compte(db: Session, identifiant: str) -> Utilisateur:
    u = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))
    if u is None:
        raise HTTPException(404, "Compte introuvable")
    return u


@router.post("/utilisateurs/{identifiant}/modifier")
def modifier(identifiant: str, corps: ModificationIn, request: Request, a: Utilisateur = Depends(admin),
             db: Session = Depends(get_session)) -> dict:
    u = _compte(db, identifiant)
    nom, zone = _texte(corps.nom), _texte(corps.zone)
    if nom:
        u.nom = nom
        if u.relais:
            u.relais.nom = nom
    if u.relais:
        if zone:
            u.relais.zone = zone
        u.relais.telephone, u.relais.telephone_sf = _texte(corps.telephone), _texte(corps.telephone_sf)
    elif u.role == "sage_femme":
        if not zone:
            raise HTTPException(422, "Une sage-femme doit avoir une zone")
        if zone != u.zone:
            u.zone = zone
            fermer_sessions(db, u)  # elle se reconnecte et ne voit plus que sa nouvelle aire
    journal.tracer(db, "compte_modifie", a.id, u.identifiant, _ip(request))
    db.commit()
    return _fiche(u)


@router.post("/utilisateurs/{identifiant}/nouveau-secret")
def nouveau_secret(identifiant: str, request: Request, a: Utilisateur = Depends(admin),
                   db: Session = Depends(get_session)) -> dict:
    """Mot de passe ou PIN oublié : nouveau secret provisoire, les sessions ouvertes sont fermées."""
    u = _compte(db, identifiant)
    if u.id == a.id:
        raise HTTPException(409, "Un autre administrateur doit réinitialiser votre propre compte")
    secret = donner_secret_provisoire(u)
    fermer_sessions(db, u)
    journal.tracer(db, "secret_reinitialise", a.id, u.identifiant, _ip(request))
    db.commit()
    return _avec_secret(u, secret)


@router.post("/utilisateurs/{identifiant}/deverrouiller")
def deverrouiller(identifiant: str, request: Request, a: Utilisateur = Depends(admin),
                  db: Session = Depends(get_session)) -> dict:
    u = _compte(db, identifiant)
    u.echecs, u.verrouille_jusqua = 0, None
    journal.tracer(db, "compte_deverrouille", a.id, u.identifiant, _ip(request))
    db.commit()
    return _fiche(u)


@router.post("/utilisateurs/{identifiant}/reactiver")
def reactiver(identifiant: str, request: Request, a: Utilisateur = Depends(admin),
              db: Session = Depends(get_session)) -> dict:
    u = _compte(db, identifiant)
    u.actif = True
    journal.tracer(db, "compte_reactive", a.id, u.identifiant, _ip(request))
    db.commit()
    return _fiche(u)


@router.post("/utilisateurs/{identifiant}/desactiver")
def desactiver(identifiant: str, request: Request, a: Utilisateur = Depends(admin),
               db: Session = Depends(get_session)) -> dict:
    u = _compte(db, identifiant)
    if u.id == a.id:
        raise HTTPException(409, "Impossible de désactiver son propre compte")
    u.actif = False
    fermer_sessions(db, u)
    journal.tracer(db, "compte_desactive", a.id, u.identifiant, _ip(request))
    db.commit()
    return {"identifiant": u.identifiant, "actif": False}


@router.post("/utilisateurs/{identifiant}/reinitialiser-2fa")
def reinitialiser_2fa(identifiant: str, request: Request, a: Utilisateur = Depends(admin),
                      db: Session = Depends(get_session)) -> dict:
    """Téléphone perdu : le compte devra réactiver la double authentification à la prochaine connexion."""
    u = _compte(db, identifiant)
    if u.id == a.id:
        raise HTTPException(409, "Un autre administrateur doit réinitialiser votre propre compte")
    u.totp_actif, u.totp_secret, u.totp_dernier_pas = False, None, None
    fermer_sessions(db, u)
    journal.tracer(db, "2fa_reinitialisee", a.id, u.identifiant, _ip(request))
    db.commit()
    return {"identifiant": u.identifiant, "totp_actif": False}


@router.post("/femmes/{code}/effacer")
def effacer_femme(code: str, request: Request, a: Utilisateur = Depends(admin),
                  db: Session = Depends(get_session)) -> dict:
    """Demande d'effacement reçue au poste (par écrit ou en personne) ; l'admin ne voit pas le dossier."""
    femme = db.scalar(select(Femme).where(Femme.code == code))
    if femme is None:
        raise HTTPException(404, "Femme introuvable")
    resultat = demander_effacement(db, femme.identite, maintenant(), f"admin:{a.identifiant}")
    journal.tracer(db, "effacement_saisi_par_admin", a.id, code, _ip(request))
    db.commit()
    return {"code": code, "effacement": resultat}


@router.post("/femmes/{code}/retirer-code")
def retirer_code_femme(code: str, request: Request, a: Utilisateur = Depends(admin),
                       db: Session = Depends(get_session)) -> dict:
    """Code personnel oublié, après vérification de l'identité de la femme au poste."""
    femme = db.scalar(select(Femme).where(Femme.code == code))
    if femme is None:
        raise HTTPException(404, "Femme introuvable")
    retirer_code(db, femme.identite, f"admin:{a.identifiant}")
    db.commit()
    return {"code": code, "code_discret": False}


@router.get("/journal")
def lire_journal(limite: int = 200, _: Utilisateur = Depends(admin), db: Session = Depends(get_session)) -> dict:
    lignes = db.scalars(select(JournalAcces).order_by(JournalAcces.id.desc()).limit(min(limite, 1000))).all()
    identifiants = dict(db.execute(select(Utilisateur.id, Utilisateur.identifiant)).all())
    return {
        "chaine_intacte": journal.verifier(db) is None,
        "lignes": [
            {"at": l.at.isoformat() + "Z", "utilisateur_id": l.utilisateur_id,
             "par": identifiants.get(l.utilisateur_id), "action": l.action, "cible": l.cible, "ip": l.ip}
            for l in lignes
        ],
    }
