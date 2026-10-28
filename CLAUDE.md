# Cyber Mod Hub

Appli Windows (Python 3.14 + tkinter, stdlib uniquement) qui garde à jour des mods de Cyberpunk 2077 à partir de leur page de téléchargement, en extrayant le .zip dans le dossier du jeu.

- `mod_hub.py` : tout le code. `resolve(url)` reconnaît : dépôt GitHub (API releases/latest, asset .zip, `digest` SHA-256), page HTML avec liens .zip (version la plus haute), sinon `releases.json` à côté de la page (cas NightCoop : https://nightcoop.net/download.html, page remplie en JS).
- Patchs de synchro NightCoop (`PATCHES`, vide depuis le retrait du Lot 2 le 2026-10-28 : section masquée) : cases à cocher qui ajoutent/retirent `nom = on` dans `<jeu>\red4ext\plugins\NightCoop\switches.txt` (lu par NightCoop au lancement du jeu), sans toucher aux autres lignes ; fichier supprimé s'il devient vide. Le fichier fait foi pour l'état des cases. Origine et mesures : projet `24 - Mods Cyberpunk`.
- `--startup` : fenêtre réduite, réessaie le réseau jusqu'à 3 min, met tout à jour, revient au premier plan si installation ou erreur.
- Config : `%APPDATA%\NightCoopUpdater\config.json` (`game_dir`, `mods[]` : `name`, `url`, `host`, `installed`, `files`) ; migration auto de l'ancien format mono-mod.
- Désinstallation (`uninstall_mod`) : supprime les fichiers de `files` (cumul de toutes les versions installées) puis les dossiers devenus vides ; garde les fichiers listés par un autre mod, l'exe du jeu et tout ce qui sortirait du dossier. Sans `files` (installé avant le suivi), relit la liste dans l'archive actuelle.
- Démarrage auto : clé `HKCU\...\CurrentVersion\Run\NightCoopUpdater` (nom historique), réécrite vers l'exe courant à chaque ouverture.
- Lancer : `CyberModHub.exe` (ou `mod_hub.pyw` depuis les sources). Tester : `python test_updater.py` (réseau requis).
- Build exe : `python -m PyInstaller --noconfirm --onefile --windowed --name CyberModHub --distpath . --workpath "$env:TEMP\nc_build" --specpath "$env:TEMP\nc_build" mod_hub.py`
- UI : thème sombre jaune/cyan, police Bahnschrift.
- Sécurité : https uniquement (redirections comprises), plafond 1 Go, SHA-256 vérifié quand la source le fournit, confirmation du fichier détecté à l'ajout, refus si l'hôte du fichier change ensuite, protection zip slip, contrôle que c'est un vrai zip. Niveau de code : standard.
- Limites connues : zip uniquement (pas .7z/.rar), pas de Nexus Mods (connexion requise), pas de sauvegarde des fichiers écrasés (un fichier vanilla écrasé par un mod est supprimé à la désinstallation, pas restauré).
