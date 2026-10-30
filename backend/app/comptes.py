"""Gestion des comptes en ligne de commande (mots de passe et PIN saisis sans écho).

    python -m app.comptes soignant --identifiant awa.sf --nom "Awa Ba" --role sage_femme --zone "Ourossogui"
    python -m app.comptes relais --code R-0002 --nom "Coumba Sy" --zone "Ourossogui" --telephone-sf +221...
    python -m app.comptes verifier-journal
"""

import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import journal, securite
from app.db import SessionLocal, init_db
from app.models import Relais, Utilisateur

PINS_INTERDITS = {"000000", "111111", "123456", "654321", "121212", "123123", "112233"}


def valider_mot_de_passe(mot_de_passe: str) -> None:
    if len(mot_de_passe) < 12:
        raise ValueError("Le mot de passe doit faire au moins 12 caractères")


def valider_pin(pin: str) -> None:
    if not (pin.isdigit() and len(pin) == 6):
        raise ValueError("Le PIN doit faire exactement 6 chiffres")
    if pin in PINS_INTERDITS or len(set(pin)) == 1:
        raise ValueError("Ce PIN est trop facile à deviner")


def creer_soignant(db: Session, identifiant: str, nom: str, role: str, mot_de_passe: str,
                   zone: str | None = None) -> Utilisateur:
    if role not in ("sage_femme", "district", "admin"):
        raise ValueError(f"Rôle inconnu : {role}")
    if role == "sage_femme" and not zone:
        raise ValueError("Une sage-femme doit avoir une zone")
    valider_mot_de_passe(mot_de_passe)
    u = Utilisateur(identifiant=identifiant.strip().lower(), nom=nom, role=role, zone=zone,
                    secret_hache=securite.hacher(mot_de_passe))
    db.add(u)
    journal.tracer(db, "compte_cree", None, u.identifiant, "cli")
    return u


def creer_relais(db: Session, code: str, nom: str, zone: str, pin: str, telephone: str | None = None,
                 telephone_sf: str | None = None) -> Utilisateur:
    valider_pin(pin)
    code = code.strip().upper()
    relais = Relais(code=code, nom=nom, zone=zone, telephone=telephone, telephone_sf=telephone_sf)
    u = Utilisateur(identifiant=code.lower(), nom=nom, role="relais", relais=relais, secret_hache=securite.hacher(pin))
    db.add(u)
    journal.tracer(db, "compte_cree", None, u.identifiant, "cli")
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
