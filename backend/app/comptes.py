"""Gestion des comptes en ligne de commande (mots de passe et PIN saisis sans écho).

    python -m app.comptes soignant --identifiant awa.sf --nom "Awa Ba" --role sage_femme --zone "Ourossogui"
    python -m app.comptes relais --code R-0002 --nom "Coumba Sy" --zone "Ourossogui" --telephone-sf +221...
    python -m app.comptes verifier-journal
"""

import argparse
import getpass
import re
import secrets
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import journal, securite
from app.db import SessionLocal, init_db
from app.models import Relais, Utilisateur, maintenant

PINS_INTERDITS = {"000000", "111111", "123456", "654321", "121212", "123123", "112233"}
DUREE_SECRET_PROVISOIRE = timedelta(hours=72)
ALPHABET_PROVISOIRE = "abcdefghjkmnpqrstuvwxyz23456789"  # sans l, i, o, 0, 1 : se lit à voix haute sans erreur
FORMAT_IDENTIFIANT = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")
FORMAT_CODE_RELAIS = re.compile(r"[A-Z0-9][A-Z0-9-]{2,15}")
SANS_SECRET = "!"  # aucun secret ne correspond : securite.verifier renvoie toujours faux


def valider_mot_de_passe(mot_de_passe: str) -> None:
    if len(mot_de_passe) < 12:
        raise ValueError("Le mot de passe doit faire au moins 12 caractères")


def valider_pin(pin: str) -> None:
    if not (pin.isdigit() and len(pin) == 6):
        raise ValueError("Le PIN doit faire exactement 6 chiffres")
    if pin in PINS_INTERDITS or len(set(pin)) == 1:
        raise ValueError("Ce PIN est trop facile à deviner")


def valider_identifiant(identifiant: str) -> None:
    if not FORMAT_IDENTIFIANT.fullmatch(identifiant):
        raise ValueError("Identifiant : 3 à 32 caractères, lettres minuscules, chiffres, point ou tiret")


def valider_code_relais(code: str) -> None:
    if not FORMAT_CODE_RELAIS.fullmatch(code):
        raise ValueError("Code relais : 3 à 16 caractères, lettres majuscules, chiffres ou tiret (ex. R-0003)")


def donner_secret_provisoire(u: Utilisateur) -> str:
    """Nouveau mot de passe (PIN pour une relais) à remettre en main propre et à changer à la première
    connexion. Renvoyé une seule fois, jamais stocké en clair."""
    if u.role == "relais":
        secret = f"{secrets.randbelow(10**6):06d}"
        while secret in PINS_INTERDITS or len(set(secret)) == 1:
            secret = f"{secrets.randbelow(10**6):06d}"
    else:
        secret = "-".join("".join(secrets.choice(ALPHABET_PROVISOIRE) for _ in range(4)) for _ in range(3))
    u.secret_hache = securite.hacher(secret)
    u.secret_provisoire, u.secret_expire_at = True, maintenant() + DUREE_SECRET_PROVISOIRE
    u.echecs, u.verrouille_jusqua = 0, None
    return secret


def creer_soignant(db: Session, identifiant: str, nom: str, role: str, mot_de_passe: str | None,
                   zone: str | None = None, par: int | None = None, origine: str = "cli") -> Utilisateur:
    """mot_de_passe=None : compte sans secret utilisable, à compléter par donner_secret_provisoire."""
    if role not in ("sage_femme", "district", "admin"):
        raise ValueError(f"Rôle inconnu : {role}")
    if role == "sage_femme" and not zone:
        raise ValueError("Une sage-femme doit avoir une zone")
    if mot_de_passe is not None:
        valider_mot_de_passe(mot_de_passe)
    valider_identifiant(identifiant.strip().lower())
    u = Utilisateur(identifiant=identifiant.strip().lower(), nom=nom, role=role, zone=zone,
                    secret_hache=securite.hacher(mot_de_passe) if mot_de_passe is not None else SANS_SECRET)
    db.add(u)
    journal.tracer(db, "compte_cree", par, u.identifiant, origine)
    return u


def creer_relais(db: Session, code: str, nom: str, zone: str, pin: str | None, telephone: str | None = None,
                 telephone_sf: str | None = None, par: int | None = None, origine: str = "cli") -> Utilisateur:
    """pin=None : compte sans secret utilisable, à compléter par donner_secret_provisoire."""
    if pin is not None:
        valider_pin(pin)
    code = code.strip().upper()
    valider_code_relais(code)
    relais = Relais(code=code, nom=nom, zone=zone, telephone=telephone, telephone_sf=telephone_sf)
    u = Utilisateur(identifiant=code.lower(), nom=nom, role="relais", relais=relais,
                    secret_hache=securite.hacher(pin) if pin is not None else SANS_SECRET)
    db.add(u)
    journal.tracer(db, "compte_cree", par, u.identifiant, origine)
    return u


def _saisir(invite: str) -> str:
    premier = getpass.getpass(f"{invite} : ")
    if getpass.getpass("Confirmez : ") != premier:
        sys.exit("Les deux saisies sont différentes.")
    return premier


def main() -> None:
    parseur = argparse.ArgumentParser(prog="python -m app.comptes")
    sous = parseur.add_subparsers(dest="commande", required=True)
    s = sous.add_parser("soignant")
    s.add_argument("--identifiant", required=True)
    s.add_argument("--nom", required=True)
    s.add_argument("--role", required=True, choices=["sage_femme", "district", "admin"])
    s.add_argument("--zone")
    r = sous.add_parser("relais")
    r.add_argument("--code", required=True)
    r.add_argument("--nom", required=True)
    r.add_argument("--zone", required=True)
    r.add_argument("--telephone")
    r.add_argument("--telephone-sf")
    sous.add_parser("verifier-journal")
    args = parseur.parse_args()

    init_db()
    with SessionLocal() as db:
        try:
            if args.commande == "soignant":
                creer_soignant(db, args.identifiant, args.nom, args.role, _saisir("Mot de passe"), args.zone)
            elif args.commande == "relais":
                if db.scalar(select(Relais).where(Relais.code == args.code.upper())):
                    sys.exit("Ce code relais existe déjà.")
                creer_relais(db, args.code, args.nom, args.zone, _saisir("PIN (6 chiffres)"), args.telephone,
                             args.telephone_sf)
            else:
                alteree = journal.verifier(db)
                print("Journal intact." if alteree is None else f"Journal ALTÉRÉ à partir de la ligne {alteree}.")
                sys.exit(0 if alteree is None else 1)
            db.commit()
        except ValueError as erreur:
            sys.exit(str(erreur))
    print("Compte créé.")


if __name__ == "__main__":
    main()
