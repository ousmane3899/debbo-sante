"""Moteur clinique déterministe.

C'est la seule brique qui fixe un niveau d'urgence (cahier des charges, §6.3).
L'IA peut proposer des signes ou un niveau, mais `avec_suggestion_ia` garantit
qu'une suggestion ne fait jamais baisser le niveau calculé par les règles.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

REGLES_PAR_DEFAUT = Path(__file__).with_name("regles.json")


@dataclass(frozen=True)
class Evaluation:
    niveau: str
    signes_retenus: tuple[str, ...]
    signes_inconnus: tuple[str, ...] = field(default=())
    version: str = ""

    @property
    def est_urgence(self) -> bool:
        return self.niveau == "urgence"


class MoteurClinique:
    def __init__(self, chemin: Path = REGLES_PAR_DEFAUT):
        regles = json.loads(chemin.read_text(encoding="utf-8"))
        self.version: str = regles["version"]
        self.statut: str = regles["statut"]
        self.niveaux: list[str] = regles["niveaux"]
        self.niveau_inconnu: str = regles["niveau_signe_inconnu"]
        self.signes: dict[str, dict] = regles["signes"]
        self.seuils_pb: dict[str, int] = regles["seuils_pb_mm"]
        self._verifier()

    def _verifier(self) -> None:
        if self.niveau_inconnu not in self.niveaux:
            raise ValueError(f"niveau_signe_inconnu invalide : {self.niveau_inconnu}")
        for code, signe in self.signes.items():
            if signe["niveau"] not in self.niveaux:
                raise ValueError(f"Niveau invalide pour le signe {code} : {signe['niveau']}")

    def rang(self, niveau: str) -> int:
        return self.niveaux.index(niveau)

    def plus_grave(self, *niveaux: str) -> str:
        return max(niveaux, key=self.rang)

    def signe_pb(self, pb_mm: int | None) -> str | None:
        """Traduit le périmètre brachial (enfant de 6 à 59 mois) en signe."""
        if pb_mm is None:
            return None
        if pb_mm < self.seuils_pb["rouge_sous"]:
            return "pb_rouge"
        if pb_mm < self.seuils_pb["jaune_sous"]:
            return "pb_jaune"
        return None

    def evaluer(
        self, signes: list[str] | tuple[str, ...], contexte: str = "grossesse", pb_mm: int | None = None
    ) -> Evaluation:
        """Renvoie le niveau le plus grave parmi les signes déclarés.

        Un signe inconnu, ou connu mais hors de son contexte, n'est jamais
        ignoré : il impose au minimum `niveau_signe_inconnu`, pour qu'un humain
        regarde le cas.
        """
        niveau = self.niveaux[0]
        retenus: list[str] = []
        inconnus: list[str] = []
        mesure = self.signe_pb(pb_mm)
        for code in dict.fromkeys([*signes, *([mesure] if mesure else [])]):
            signe = self.signes.get(code)
            if signe is None or contexte not in signe["contextes"]:
                inconnus.append(code)
                niveau = self.plus_grave(niveau, self.niveau_inconnu)
                continue
            retenus.append(code)
            niveau = self.plus_grave(niveau, signe["niveau"])
        return Evaluation(niveau, tuple(retenus), tuple(inconnus), self.version)

    def avec_suggestion_ia(self, evaluation: Evaluation, niveau_ia: str | None) -> Evaluation:
        """Combine le niveau des règles et celui proposé par l'IA : le plus grave gagne."""
        if niveau_ia is None or niveau_ia not in self.niveaux:
            return evaluation
        niveau = self.plus_grave(evaluation.niveau, niveau_ia)
        return Evaluation(niveau, evaluation.signes_retenus, evaluation.signes_inconnus, evaluation.version)


moteur = MoteurClinique()
