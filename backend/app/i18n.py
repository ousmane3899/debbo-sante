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
            "4 Etre rappelee\n5 Conseils\n6 Mes donnees\n0 Parler a une sage-femme"
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
        "donnees_menu": (
            "Mes donnees\n1 Effacer mes donnees\n2 Recherche et statistiques\n3 Amelioration de l'assistant\n"
            "4 Code personnel"
        ),
        "code_demande": "Entrez votre code personnel",
        "code_incorrect": "Code incorrect. En cas d'urgence, choisissez 1 dans le menu : pas besoin de code.",
        "code_verrouille": (
            "Trop d'essais. Reessayez dans 30 minutes. En cas d'urgence, choisissez 1 dans le menu : pas besoin de code."
        ),
        "code_gerer": "Code personnel\n1 Changer le code\n2 Supprimer le code",
        "code_nouveau": (
            "Choisissez un code de 4 chiffres. Il sera demande avant de montrer vos informations. "
            "L'urgence reste ouverte sans code."
        ),
        "code_confirmer": "Retapez le code",
        "code_trop_simple": "Ce code est trop facile a deviner. Recommencez avec un autre code.",
        "code_different": "Les deux codes sont differents. Rien n'a change.",
        "code_enregistre": (
            "Code enregistre. Debbo ne dira plus rien de personnel sans lui. "
            "Code oublie : demandez a votre relais ou au poste de sante."
        ),
        "code_supprime": "Code supprime.",
        "zone_choix": "Ou habitez-vous ?\n{zones}\n0 Je ne sais pas",
        "effacement_confirmer": (
            "Effacer toutes vos donnees ? Debbo ne vous suivra plus et ne vous appellera plus.\n1 Oui, effacer\n2 Non"
        ),
        "effacement_fait": "C'est fait : vos donnees sont effacees. Le menu Urgence reste toujours ouvert pour vous.",
        "effacement_en_attente": (
            "Demande enregistree. Debbo ne vous appellera plus. "
            "L'effacement sera fait a la fin de votre prise en charge en cours."
        ),
        "effacement_annule": "Rien n'a ete efface.",
        "aucune_donnee": "Debbo n'a aucune donnee sur ce numero.",
        "consentement_recherche": (
            "Vos donnees, sans votre nom ni votre numero, peuvent aider la recherche sur la sante des meres. "
            "D'accord ?\n1 Oui\n2 Non"
        ),
        "consentement_ia": (
            "Vos reponses, sans votre nom ni votre numero, peuvent aider a ameliorer l'assistant de Debbo. "
            "D'accord ?\n1 Oui\n2 Non"
        ),
        "choix_enregistre": "Votre choix est enregistre. Il ne change rien a votre suivi.",
        # Appels sortants (textes des messages vocaux ; en production, fichiers audio enregistrés).
        # Formulation neutre : le téléphone peut être partagé, rien ne doit trahir une grossesse.
        "appel_veille_contact": (
            "Bonjour, ici Debbo Sante. Vous avez rendez-vous demain au poste de sante. "
            "Appuyez sur 1 si vous pourrez y aller. Appuyez sur 2 pour etre rappelee par une sage-femme."
        ),
        "appel_relance": (
            "Bonjour, ici Debbo Sante. Vous aviez un rendez-vous au poste de sante. Il n'est pas trop tard. "
            "Appuyez sur 1 si vous pourrez y aller cette semaine. Appuyez sur 2 pour etre rappelee par une sage-femme."
        ),
        # Mode discret : aucun contenu, la femme consulte Debbo avec son code quand elle est seule.
        "appel_discret": (
            "Bonjour, ici Debbo Sante. Vous avez un message. Composez le code Debbo sur votre telephone "
            "quand vous le pourrez. Appuyez sur 2 pour etre rappelee par une sage-femme."
        ),
        "appel_veille_vaccin": (
            "Bonjour, ici Debbo Sante. Votre enfant a rendez-vous demain au poste de sante pour ses vaccins. "
            "Appuyez sur 1 si vous pourrez y aller. Appuyez sur 2 pour etre rappelee par une sage-femme."
        ),
        "appel_relance_vaccin": (
            "Bonjour, ici Debbo Sante. Votre enfant n'a pas encore recu tous ses vaccins. Il n'est pas trop tard. "
            "Appuyez sur 1 si vous pourrez aller au poste de sante cette semaine. "
            "Appuyez sur 2 pour etre rappelee par une sage-femme."
        ),
    },
    "ff": {},
    "wo": {},
}


def t(cle: str, langue: str | None, **valeurs: str) -> str:
    texte = TEXTES.get(langue or "fr", {}).get(cle) or TEXTES["fr"][cle]
    return texte.format(**valeurs) if valeurs else texte
