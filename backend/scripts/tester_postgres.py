"""Lance toute la suite de tests sur un PostgreSQL jetable.

    python scripts/tester_postgres.py            # toute la suite
    python scripts/tester_postgres.py -k migr    # arguments passés à pytest

Crée une instance PostgreSQL neuve dans un dossier temporaire, sur un port libre,
accessible seulement depuis cette machine, puis la détruit. Ne touche à aucune base
existante et ne demande aucun mot de passe. Les programmes PostgreSQL (initdb,
pg_ctl) sont cherchés dans PG_BIN, le PATH, puis C:\\Program Files\\PostgreSQL.
"""

import glob
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def dossier_postgres() -> Path:
    if os.environ.get("PG_BIN"):
        return Path(os.environ["PG_BIN"])
    if shutil.which("initdb"):
        return Path(shutil.which("initdb")).parent
    candidats = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin"), key=lambda c: int(Path(c).parent.name))
    if not candidats:
        sys.exit("PostgreSQL introuvable : indiquez son dossier bin dans PG_BIN.")
    return Path(candidats[-1])


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    binaires = dossier_postgres()
    with tempfile.TemporaryDirectory(prefix="debbo-pg-", ignore_cleanup_errors=True) as dossier:
        donnees, journal, port = Path(dossier) / "data", Path(dossier) / "pg.log", port_libre()
        subprocess.run([binaires / "initdb", "-D", donnees, "-U", "debbo", "-A", "trust", "-E", "UTF8",
                        "--no-locale"], check=True, capture_output=True)
        # Pas de capture de la sortie : le serveur lancé hériterait des canaux et Python
        # attendrait leur fermeture indéfiniment. Le serveur écrit dans son propre journal.
        subprocess.run([binaires / "pg_ctl", "-D", donnees, "-l", journal, "-w", "-o",
                        f"-p {port} -c listen_addresses=127.0.0.1", "start"], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import psycopg

            with psycopg.connect(f"host=127.0.0.1 port={port} user=debbo dbname=postgres", autocommit=True) as cx:
                cx.execute("CREATE DATABASE debbo_test")
            version = psycopg.connect(f"host=127.0.0.1 port={port} user=debbo dbname=debbo_test").info.server_version
            print(f"PostgreSQL jetable {version // 10000}.{version % 10000} sur le port {port}", flush=True)
            env = {**os.environ, "DEBBO_TEST_DATABASE_URL": f"postgresql+psycopg://debbo@127.0.0.1:{port}/debbo_test"}
            return subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=BACKEND, env=env).returncode
        finally:
            subprocess.run([binaires / "pg_ctl", "-D", donnees, "-m", "fast", "-w", "stop"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(main())
