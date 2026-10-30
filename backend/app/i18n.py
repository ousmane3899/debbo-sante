"""Textes du service, par langue.

Seul le français est rédigé. Les textes pulaar (`ff`) et wolof (`wo`) doivent
être écrits puis validés par 2 locuteurs natifs et 1 clinicien (cahier des
charges, §9) : tant qu'une clé manque, le français s'affiche.
Un écran USSD ne doit pas dépasser 182 caractères, et les textes USSD sont
sans accents : beaucoup de téléphones basiques les affichent mal.
"""

LANGUES = {"1": "ff", "2": "wo", "3": "fr"}

TEXTES: dict[str, dict[str, str]] = {
    "fr": {
        "choix_langue": "Debbo Sante\n1 Pulaar\n2 Wolof\n3 Francais",
        "menu": (
            "Debbo Sante\n1 Urgence\n2 Je suis enceinte\n3 Mes rendez-vous\n"
            "4 Etre rappelee\n5 Conseils\n0 Parler a une sage-femme"
        ),
        "urgence_menu": (
            "URGENCE - quel signe ?\n1 Saignement\n2 Convulsions\n3 Mal de tete fort/vue trouble\n"
            "4 Forte fievre\n5 Fort mal au ventre\n6 Perte des eaux\n7 Bebe ne bouge plus\n"
            "8 Bebe malade\n9 Autre"
        ),
        "urgence_ok": (
            "Alerte envoyee. Une sage-femme vous appelle dans quelques minutes. "
            "Si vous pouvez, partez deja vers le poste de sante."
        ),
        "consentement": (
            "Debbo garde votre numero pour vous rappeler vos consultations. "
            "Seuls les soignants le voient.\n1 J'accepte\n2 Je refuse"
        ),
        "consentement_refus": (
            "Nous ne gardons rien. Vous pouvez toujours utiliser le menu Urgence. "
            "Allez au poste de sante pour vos consultations."
        ),
        "mois_grossesse": "Depuis combien de mois etes-vous enceinte ? Tapez un chiffre de 1 a 9",
        "inscrite": "Merci, vous etes inscrite. Debbo vous appellera avant chaque consultation. Prochaine : {date}.",
        "deja_inscrite": "Vous etes deja inscrite. Prochaine consultation : {date}.",
        "pas_inscrite": "Vous n'etes pas encore inscrite. Recomposez le code et choisissez 2.",
        "aucun_rdv": "Aucun rendez-vous prevu. Parlez-en a la sage-femme.",
        "rdv": "Prochaine consultation : {date}. Debbo vous appellera la veille.",
        "rappel_creneau": "Quand voulez-vous etre rappelee ?\n1 Matin\n2 Apres-midi\n3 Soir",
        "rappel_ok": "C'est note. Une sage-femme ou une relais vous rappellera.",
        "sf_ok": "Une sage-femme vous rappelle des que possible. En cas d'urgence, choisissez 1 dans le menu.",
        "conseils_menu": "Conseils\n1 Grossesse\n2 Apres l'accouchement\n3 Bebe\n4 Planification familiale",
        "conseil_grossesse": (
            "Faites au moins 8 consultations. Dormez sous moustiquaire. "
            "Prenez le fer et l'acide folique donnes au poste de sante."
        ),
        "conseil_postpartum": (
            "Consultez dans les 24 h apres l'accouchement, puis aux jours 3, 7 et a 6 semaines. "
            "Saignement fort ou fievre : urgence."
        ),
        "conseil_bebe": (
            "Donnez seulement le lait maternel jusqu'a 6 mois. Faites tous les vaccins. "
            "Bebe qui ne tete pas : urgence."
        ),
        "conseil_pf": "La sage-femme peut vous conseiller en toute confidentialite. Choisissez 4 dans le menu pour etre rappelee.",
        "invalide": "Reponse non comprise. Recomposez le code pour recommencer.",
    },
    "ff": {},
    "wo": {},
}


def t(cle: str, langue: str | None, **valeurs: str) -> str:
    texte = TEXTES.get(langue or "fr", {}).get(cle) or TEXTES["fr"][cle]
    return texte.format(**valeurs) if valeurs else texte
