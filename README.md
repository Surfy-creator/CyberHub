# CyberHub

Cyber Mod Hub : une petite appli Windows qui garde à jour les mods de Cyberpunk 2077 à partir de leur page de téléchargement.

- Tu choisis le dossier du jeu avec l'explorateur Windows.
- Tu ajoutes un mod avec un nom et l'adresse de sa page de téléchargement. Sont reconnus : un dépôt GitHub, une page avec des liens `.zip`, ou un site qui publie un `releases.json` (comme NightCoop).
- Le hub vérifie la dernière version, télécharge le `.zip` (SHA-256 vérifié quand la source le fournit) et l'extrait dans le jeu.
- Il peut désinstaller les fichiers d'un mod.
- Option « Lancer au démarrage de Windows » : mise à jour automatique, fenêtre réduite.

## Utilisation

Télécharge `CyberModHub.exe` dans l'onglet **Releases**, ou lance depuis les sources (Python 3.12 ou plus, aucune dépendance) :

```
python mod_hub.py
```

## Mise à jour du hub lui-même

Au lancement, le hub regarde la dernière release de ce dépôt. Si elle est plus récente que sa version (affichée dans l'en-tête), une bannière jaune propose de remplacer l'exe et de redémarrer. Le fichier est vérifié par son SHA-256 avant d'être installé.

Pour publier une mise à jour (mainteneur) :
1. Augmente `APP_VERSION` dans `mod_hub.py` (par exemple `1.0.1`) et compile l'exe.
2. Pousse le code, puis crée une release GitHub dont l'étiquette est cette version (`v1.0.1`).
3. Joins `CyberModHub.exe` à la release, avec exactement ce nom. Sans ce fichier, les utilisateurs ne voient aucune mise à jour.

## Compiler l'exe

```
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name CyberModHub mod_hub.py
```

## Tests

```
python test_updater.py
```

Les tests utilisent le réseau (GitHub et nightcoop.net).

## Limites

- Archives `.zip` uniquement (pas `.7z` ni `.rar`), pas de Nexus Mods (connexion requise).
- Les fichiers écrasés par une mise à jour ne sont pas sauvegardés : un fichier d'origine du jeu écrasé par un mod est supprimé à la désinstallation, pas restauré.
- Ferme le jeu avant de mettre à jour ou de désinstaller.
