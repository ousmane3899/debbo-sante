"""Administration des comptes (rôle « admin » : aucun accès aux dossiers de santé)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import journal
from app.auth import exiger, fermer_sessions
from app.db import get_session
from app.models import Femme, JournalAcces, Utilisateur, maintenant
from app.vie_privee import demander_effacement, retirer_code

router = APIRouter(prefix="/api/admin")
admin = exiger("admin")


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/utilisateurs")
def utilisateurs(_: Utilisateur = Depends(admin), db: Session = Depends(get_session)) -> list[dict]:
    return [
        {"identifiant": u.identifiant, "nom": u.nom, "role": u.role, "zone": u.zone, "actif": u.actif,
         "totp_actif": u.totp_actif, "verrouille": bool(u.verrouille_jusqua)}
        for u in db.scalars(select(Utilisateur).order_by(Utilisateur.identifiant))
    ]


def _compte(db: Session, identifiant: str) -> Utilisateur:
    u = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))
    if u is None:
        raise HTTPException(404, "Compte introuvable")
    return u


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
    return {
        "chaine_intacte": journal.verifier(db) is None,
        "lignes": [
            {"at": l.at.isoformat() + "Z", "utilisateur_id": l.utilisateur_id, "action": l.action, "cible": l.cible,
             "ip": l.ip}
            for l in lignes
        ],
    }
