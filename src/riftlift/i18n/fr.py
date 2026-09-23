"""French strings. Structure must mirror src/riftlift/i18n/en.py exactly."""

STRINGS = {
    "app": {
        "name": "RiftLift",
    },
    "setup": {
        "banner_text": (
            "La compatibilité RiftLift n'est pas entièrement configurée - "
            "certains jeux peuvent ne pas se lancer."
        ),
        "run_now": "Configurer maintenant",
        "heading": "Compatibilité système",
        "explanation": (
            "Installe ou met à jour Proton, le pont d'exécution Meta, et la "
            "couche de compatibilité OpenXR/OpenVR nécessaire à cette "
            "version. Peut être relancé sans risque."
        ),
        "run": "Lancer la configuration",
        "running": "Configuration de la compatibilité en cours",
        "done": "Compatibilité prête",
        "status_heading": "État du système",
        "status_ok": "Tout est prêt.",
        "status_needs_setup": (
            "La compatibilité n'est pas encore configurée. Lance la "
            "configuration ci-dessous."
        ),
        "status_no_openxr_runtime": (
            "Aucun runtime OpenXR actif, les jeux ne pourront pas démarrer "
            "en VR. Installe et démarre Monado ou WiVRn (selon ton casque), "
            "puis revérifie."
        ),
        "checking": "Vérification...",
        "recheck": "Revérifier",
    },
    "nav": {
        "settings": "Paramètres",
        "account": "Compte",
        "sign_in": "Connexion",
        "steam_games": "Jeux Steam",
        "add_game": "Ajouter un jeu",
    },
    "library": {
        "title": "Bibliothèque",
        "installed": "Installés",
        "installed_steam": "Jeux Steam installés",
        "not_installed": "Non installés",
        "version": "Version {version}",
        "refresh_tooltip": "Rafraîchir les jeux installés et ta bibliothèque Meta",
    },
    "empty": {
        "title": "Aucun jeu Rift pour l'instant",
        "hint": (
            "Ajoute un jeu Rift que tu possèdes pour le télécharger et le préparer "
            "pour OpenXR."
        ),
    },
    "game": {
        "launch": "Lancer en VR",
        "install": "Installer",
        "files": "Fichiers",
        "launch_options": "Options de lancement",
        "add_to_steam": "Ajouter à Steam",
        "uninstall": "Désinstaller",
        "remove_from_riftlift": "Retirer de RiftLift",
        "open_rift_store": "Ouvrir sur le Rift Store ↗",
        "open_steam": "Ouvrir sur Steam ↗",
        "local": "Jeu local",
        "not_installed": "Non installé",
        "not_played_yet": "Pas encore joué",
        "played_for": "{duration} de jeu",
        "about": "À propos",
    },
    "status": {
        "ready": "Prêt",
        "view_activity": "Voir l'activité",
        "signed_in": "Connecté à Meta",
        "signed_out": "Déconnecté de Meta",
        "busy": "Une autre opération est déjà en cours",
    },
    "task": {
        "checking_system": "Vérification du système",
        "adding_from_steam": "Ajout de {name} depuis Steam",
        "added_from_steam": "{name} ajouté depuis Steam",
        "launching": "Lancement de {name}",
        "closed": "{name} fermé",
        "adding_to_steam": "Ajout de {name} à Steam",
        "added_to_steam": "{name} ajouté à Steam",
        "removing": "Suppression de {name}",
        "removed": "{name} supprimé",
        "refreshing_library": "Rafraîchissement de la bibliothèque",
        "library_refreshed": "Bibliothèque rafraîchie",
        "adding_local_game": "Ajout du jeu local",
        "installed_name": "{name} installé",
    },
    "activity": {
        "title": "Activité RiftLift",
        "heading": "Activité",
    },
    "confirm": {
        "uninstall_deletes_files": (
            "Désinstaller {name} ?\nÇa supprime les fichiers téléchargés."
        ),
        "uninstall_keeps_files": (
            "Retirer {name} de RiftLift ?\nSes fichiers ne sont pas touchés."
        ),
        "change_language": (
            "Passer en {language} ?\nRiftLift va se rafraîchir pour appliquer le "
            "changement."
        ),
    },
    "add_game": {
        "title": "Ajouter un jeu Rift",
        "heading": "Ajouter à ta bibliothèque",
        "install_heading": "Confirmer l'installation",
        "add_local": "Ajouter un jeu local…",
        "url_section": "URL du store Meta Rift",
        "browse_store": "Parcourir le store Rift / PC VR…",
        "url_placeholder": "https://www.meta.com/experiences/pcvr/…",
        "paste_valid_link": "Colle un lien valide du store Meta Rift pour continuer.",
        "checking_link": "Vérification du lien Rift store…",
        "game_not_found": "Ce jeu du Rift store est introuvable.",
        "link_check_failed": "Impossible de vérifier ce lien. Vérifie ta connexion.",
        "ready_to_install": "Prêt à installer {name}.",
        "confirm_install": "Installer {name} ?",
        "add_to_steam": "Ajouter à Steam une fois terminé",
        "starting_install": "Démarrage de l'installation…",
        "phase_preparing_segments": "Préparation des segments",
        "phase_downloading": "Téléchargement",
        "phase_assembling_files": "Assemblage des fichiers",
    },
    "local_game": {
        "title": "Ajouter un jeu VR local",
        "hint": (
            "Choisis un jeu VR Windows déjà installé. RiftLift laisse ses fichiers "
            "en place."
        ),
        "executable": "Exécutable du jeu",
        "name": "Nom",
        "name_placeholder": "Rempli depuis l'exécutable",
        "arguments": "Arguments de lancement (optionnel)",
        "artwork": "Image de couverture (optionnel)",
        "add": "Ajouter",
        "browse": "Parcourir…",
    },
    "steam_games": {
        "title": "Jeux Steam avec mode Oculus",
        "heading": "Ajouter un jeu Steam VR installé",
        "explanation": (
            "RiftLift scanne tes jeux Steam installés pour trouver un mode Oculus "
            "compatible. En ajouter un ne télécharge ni ne duplique le jeu."
        ),
        "accessible_name": "Jeux Steam compatibles",
        "scanning": "Scan des jeux Steam installés…",
        "scan_again": "Scanner à nouveau",
        "add_to_riftlift": "Ajouter à RiftLift",
        "refresh_in_riftlift": "Rafraîchir dans RiftLift",
        "already_in_riftlift": " (déjà dans RiftLift)",
        "none_found": (
            "Aucun jeu compatible trouvé. Installe un jeu Steam avec un mode "
            "Oculus, puis clique sur Scanner à nouveau."
        ),
        "found_one": (
            "1 jeu Steam compatible trouvé. "
            "Sélectionne-le pour l'ajouter à ta bibliothèque RiftLift."
        ),
        "found_other": (
            "{count} jeux Steam compatibles trouvés. "
            "Sélectionnes-en un pour l'ajouter à ta bibliothèque RiftLift."
        ),
    },
    "auth": {
        "title": "Compte Meta",
        "heading": "Se connecter à Meta",
        "explanation": (
            "RiftLift ouvre ton navigateur par défaut avec ton profil habituel "
            "et revient ici une fois Meta terminé. Autorise le navigateur à ouvrir "
            "RiftLift quand demandé. Ton mot de passe et tes codes de sécurité ne "
            "vont qu'à Meta."
        ),
        "open_browser": "Ouvrir le navigateur par défaut",
        "sign_out_reset": "Se déconnecter et réinitialiser",
        "signed_in": "RiftLift est connecté à Meta.",
        "opening_browser": "Ouverture de ton navigateur par défaut…",
        "preparing": "Préparation d'une connexion Meta sécurisée…",
        "cancel_sign_in": "Annuler la connexion",
        "waiting_for_meta": "En attente de Meta dans {browser}…",
        "browser_open_failed": (
            "Impossible d'ouvrir le navigateur pour la connexion Meta. "
            "Réessaie quand tu es prêt."
        ),
        "finishing": "Finalisation sécurisée de la connexion…",
        "signed_in_returning": "Connecté. Retour à RiftLift…",
        "try_again": "Réessayer",
        "signed_out": "Déconnecté. Ouvre ton navigateur par défaut quand tu es prêt.",
    },
    "settings": {
        "title": "Paramètres",
        "language": "Langue",
        "debug_logging": "Journalisation détaillée",
        "debug_logging_tooltip": (
            "Capture les diagnostics Proton, Wine XR/Steam/Vulkan, DXVK, VKD3D, "
            "loader et crash pour les futurs rapports Système. Stockage limité."
        ),
        "system_check_explanation": (
            "Lance une série de vérifications sur ta configuration "
            "Proton/OpenXR/SteamVR et envoie un rapport anonymisé et "
            "partageable - utile pour demander de l'aide en cas de souci."
        ),
        "run_system_check": "Générer un rapport de diagnostic",
    },
    "action": {
        "cancel": "Annuler",
        "close": "Fermer",
        "yes": "Oui",
        "no": "Non",
        "ok": "OK",
    },
    "launch_options": {
        "title": "Options de lancement — {name}",
        "arguments_label": "Arguments de lancement supplémentaires",
        "arguments_placeholder": '--exemple "valeur avec espaces"',
        "arguments_hint": (
            "Ajoutés aux arguments par défaut du jeu. Mets des guillemets "
            "autour des valeurs contenant des espaces. Entre des arguments "
            "du jeu ici, pas une commande shell ni %command%."
        ),
        "overrides_label": "Redéfinitions de DLL",
        "overrides_placeholder": "version=n,b;winhttp=n,b",
        "overrides_hint": (
            "Sépare les règles par des points-virgules. n = natif, b = "
            "intégré ; version= désactive cette DLL. Laisse vide pour "
            "utiliser les réglages hérités et la détection automatique des "
            "mod loaders. Ces choix ne s'appliquent qu'à ce jeu."
        ),
        "environment_label": "Variables d'environnement",
        "environment_placeholder": "PROTON_LOG=1\nPROTON_USE_WINED3D=1",
        "environment_hint": (
            "Une variable NOM=valeur par ligne. Les valeurs sont littérales : "
            "n'ajoute pas de guillemets shell ni de export. Les valeurs "
            "enregistrées remplacent les réglages hérités pour ce jeu. Les "
            "règles de DLL ci-dessus sont prioritaires sur WINEDLLOVERRIDES "
            "saisi ici."
        ),
        "save_error_title": "Impossible d'enregistrer les options de lancement",
        "environment_format_error": (
            "Entre les variables d'environnement au format NOM=valeur, une par ligne"
        ),
    },
}
