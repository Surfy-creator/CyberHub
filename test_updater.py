"""Auto-test : python test_updater.py (réseau requis : nightcoop.net et l'API GitHub)."""
import json
import tempfile
import zipfile
from pathlib import Path

import mod_hub as u

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    u.CONFIG = tmp / "config.json"

    # liens .zip d'une page : relatifs résolus, version la plus haute retenue
    html = '<a href="dl/Mod-1.9.zip">a</a> <a href=\'https://cdn.x.net/Mod-1.10.zip?t=1\'>b</a> <a href="x.7z">c</a>'
    links = u.zip_links(html, "https://site.net/mods/page.html")
    assert links == {"https://site.net/mods/dl/Mod-1.9.zip", "https://cdn.x.net/Mod-1.10.zip?t=1"}, links
    assert max(links, key=lambda l: u.version_key(u.file_name(l))).endswith("1.10.zip?t=1")

    # migration de l'ancienne config mono-mod
    u.CONFIG.write_text(json.dumps({"game_dir": "X", "installed": "0.1.0"}))
    cfg = u.load_config()
    assert cfg["mods"] == [dict(u.NIGHTCOOP, installed="0.1.0")] and "installed" not in cfg

    # adresses non https refusées
    for bad in ("http://site.net/a.zip", "ftp://x"):
        try:
            u.resolve(bad)
            raise AssertionError(bad)
        except RuntimeError:
            pass

    # changement de site du fichier refusé
    mod = {"name": "M", "url": "https://a.net", "host": "a.net"}
    try:
        u.update_mod({"game_dir": "X"}, mod, info={"version": "2", "file": "https://evil.net/m.zip", "sha256": None})
        raise AssertionError("changement d'hôte non détecté")
    except RuntimeError:
        pass

    # zip slip refusé
    evil = tmp / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../evil.txt", "x")
    try:
        u.safe_extract(evil, tmp / "game")
        raise AssertionError("zip slip non détecté")
    except RuntimeError:
        pass

    # règle « latest » : une version retirée est ignorée
    m = {"latest": "2", "releases": [{"version": "2", "yanked": "bug"}, {"version": "1", "yanked": None}]}
    assert u.latest_release(m)["version"] == "1"

    # patchs switches.txt : cocher ajoute, décocher retire, les autres réglages restent
    g = tmp / "g"
    (g / u.NC_DIR).mkdir(parents=True)
    sw = u.switches_file(g)
    sw.write_text("save_splice_25 = off\n")
    u.set_patch(g, "led_scenes", True)
    u.set_patch(g, "led_scenes", True)
    assert sw.read_text() == "save_splice_25 = off\nled_scenes = on\n", sw.read_text()
    assert u.patch_enabled(g, "led_scenes") and not u.patch_enabled(g, "save_splice_25")
    u.set_patch(g, "led_scenes", False)
    assert sw.read_text() == "save_splice_25 = off\n" and not u.patch_enabled(g, "led_scenes")
    u.set_patch(g, "save_splice_25", False)
    assert not sw.exists()

    # désinstallation : fichiers du mod supprimés, partagés et hors jeu conservés, dossiers vides retirés
    g2 = tmp / "g2"
    for f in ("bin/x64/Cyberpunk2077.exe", "bin/x64/shared.dll", "r6/mod/a.reds", "r6/mod/sub/b.reds", "r6/user.txt"):
        (g2 / f).parent.mkdir(parents=True, exist_ok=True)
        (g2 / f).write_text("x")
    (tmp / "outside.txt").write_text("x")
    a = {"name": "A", "files": ["bin/x64/Cyberpunk2077.exe", "bin/x64/shared.dll", "r6/mod/a.reds",
                                "r6/mod/sub/b.reds", "../outside.txt", "absent.txt"], "installed": "1"}
    b = {"name": "B", "files": ["bin/x64/SHARED.dll"], "installed": "1"}
    removed, kept = u.uninstall_mod({"game_dir": str(g2), "mods": [a, b]}, a)
    assert (removed, kept) == (2, ["bin/x64/shared.dll"]), (removed, kept)
    assert not (g2 / "r6/mod").exists() and (g2 / "r6/user.txt").exists()
    assert (g2 / "bin/x64/Cyberpunk2077.exe").exists() and (tmp / "outside.txt").exists()
    assert "installed" not in a and "files" not in a

    # GitHub : dernière release avec un .zip
    gh = u.resolve("https://github.com/wopss/RED4ext")
    assert gh["file"].endswith(".zip"), gh
    print("GitHub RED4ext :", gh["version"], "sha256" if gh["sha256"] else "sans sha")

    # NightCoop réel (page JS -> releases.json) installé dans un faux dossier de jeu
    game = tmp / "Cyberpunk 2077"
    (game / u.GAME_EXE).parent.mkdir(parents=True)
    (game / u.GAME_EXE).touch()
    cfg = {"game_dir": str(game), "mods": [dict(u.NIGHTCOOP)]}
    version, installed = u.update_mod(cfg, cfg["mods"][0])
    assert installed and (game / "red4ext").is_dir(), list(game.iterdir())
    assert u.update_mod(cfg, cfg["mods"][0]) == (version, False)
    assert cfg["mods"][0]["host"] == "nightcoop.net"
    print("OK — NightCoop", version, "→", sorted(p.name for p in game.iterdir()))
    assert cfg["mods"][0]["files"]
    removed, _ = u.uninstall_mod(cfg, cfg["mods"][0])
    assert removed and sorted(p.name for p in game.iterdir()) == ["bin"] and (game / u.GAME_EXE).exists()
    print("OK — NightCoop désinstallé :", removed, "fichiers")
