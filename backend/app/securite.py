"""Primitives de sécurité, sans dépendance externe : scrypt, TOTP (RFC 6238), jetons."""

import base64
import hashlib
import hmac
import secrets
import struct
import time

# scrypt : n=2^15, r=8, p=1 (recommandation OWASP), environ 32 Mo de mémoire par calcul.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
SCRYPT_MEMOIRE = 64 * 1024 * 1024


def _scrypt(secret: str, sel: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(secret.encode(), salt=sel, n=n, r=r, p=p, maxmem=SCRYPT_MEMOIRE, dklen=32)


def hacher(secret: str) -> str:
    sel = secrets.token_bytes(16)
    empreinte = _scrypt(secret, sel, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${sel.hex()}${empreinte.hex()}"


def verifier(secret: str, stocke: str) -> bool:
    try:
        _, n, r, p, sel, empreinte = stocke.split("$")
        calcule = _scrypt(secret, bytes.fromhex(sel), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calcule, bytes.fromhex(empreinte))


# Calculé une fois : sert à faire durer un échec autant qu'un succès quand l'identifiant
# n'existe pas, pour ne pas révéler quels identifiants existent.
EMPREINTE_FACTICE = hacher(secrets.token_urlsafe(16))


def nouveau_jeton() -> str:
    return secrets.token_urlsafe(32)


def empreinte_jeton(jeton: str) -> str:
    """Les jetons de session ne sont stockés que sous forme d'empreinte."""
    return hashlib.sha256(jeton.encode()).hexdigest()


# ---------- TOTP (applications type Google Authenticator, Aegis, FreeOTP) ----------

TOTP_PERIODE = 30
TOTP_CHIFFRES = 6
TOTP_FENETRE = 1  # tolère un pas de 30 s d'écart d'horloge


def nouveau_secret_totp() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, pas: int) -> str:
    cle = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(cle, struct.pack(">Q", pas), hashlib.sha1).digest()
    decalage = mac[-1] & 0x0F
    valeur = struct.unpack(">I", mac[decalage : decalage + 4])[0] & 0x7FFFFFFF
    return str(valeur % 10**TOTP_CHIFFRES).zfill(TOTP_CHIFFRES)


def code_totp(secret: str, instant: float | None = None) -> str:
    return _code(secret, int((instant if instant is not None else time.time()) // TOTP_PERIODE))


def verifier_totp(secret: str, code: str, dernier_pas: int | None, instant: float | None = None) -> int | None:
    """Renvoie le pas de temps accepté, ou None.

    Un pas déjà utilisé (ou plus ancien) est refusé : un code intercepté ne
    peut pas être rejoué.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_CHIFFRES:
        return None
    pas_courant = int((instant if instant is not None else time.time()) // TOTP_PERIODE)
    for pas in range(pas_courant - TOTP_FENETRE, pas_courant + TOTP_FENETRE + 1):
        if dernier_pas is not None and pas <= dernier_pas:
            continue
        if hmac.compare_digest(_code(secret, pas), code):
            return pas
    return None


def uri_totp(secret: str, compte: str, emetteur: str = "Debbo Santé") -> str:
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(emetteur)}:{quote(compte)}"
        f"?secret={secret}&issuer={quote(emetteur)}&digits={TOTP_CHIFFRES}&period={TOTP_PERIODE}"
    )
