"""Cyber Mod Hub — garde à jour les mods de Cyberpunk 2077 à partir de leur page de téléchargement.

Sources reconnues : dépôt GitHub (dernière release), page web avec liens .zip, manifeste releases.json (NightCoop).
Lancement : pythonw mod_hub.py            (fenêtre)
            pythonw mod_hub.py --startup  (démarrage Windows : fenêtre réduite, met tout à jour)
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import winreg
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

CONFIG = Path(os.environ["APPDATA"]) / "NightCoopUpdater" / "config.json"  # chemin historique conservé
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "NightCoopUpdater"
GAME_EXE = Path("bin") / "x64" / "Cyberpunk2077.exe"
MAX_BYTES = 1 << 30  # 1 Go : aucun mod sérieux ne dépasse ça
NIGHTCOOP = {"name": "NightCoop", "url": "https://nightcoop.net/download.html"}
NC_DIR = Path("red4ext") / "plugins" / "NightCoop"
# Réglages de synchro lus par NightCoop au démarrage du jeu (switches.txt, « nom = on|off »).
PATCHES = []  # (nom, libellé) ; la section disparaît de la fenêtre quand la liste est vide


def load_config():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if "mods" not in cfg:  # migration de l'ancienne config mono-mod
        cfg["mods"] = [dict(NIGHTCOOP, installed=cfg.pop("installed")) if "installed" in cfg else dict(NIGHTCOOP)]
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def is_game_dir(path):
    return bool(path) and (Path(path) / GAME_EXE).is_file()


def fetch(url, progress=None):
    if urlparse(url).scheme != "https":
        raise RuntimeError(f"Seules les adresses https sont acceptées : {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "CyberModHub/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if urlparse(r.geturl()).scheme != "https":
            raise RuntimeError(f"Redirection non sécurisée vers {r.geturl()}")
        total = int(r.headers.get("Content-Length") or 0)
        if total > MAX_BYTES:
            raise RuntimeError("Fichier trop volumineux (plus de 1 Go).")
        buf = bytearray()
        while chunk := r.read(65536):
            buf += chunk
            if len(buf) > MAX_BYTES:
                raise RuntimeError("Fichier trop volumineux (plus de 1 Go).")
            if progress and total:
                progress(len(buf) / total)
        return bytes(buf)


def latest_release(manifest):
    """Même règle que nightcoop.net : la version 'latest' si non retirée, sinon la première non retirée."""
    rs = manifest.get("releases") or []
    for r in rs:
        if r.get("version") == manifest.get("latest") and not r.get("yanked"):
            return r
    return next((r for r in rs if not r.get("yanked")), None)


def mod_file(release):
    fs = release.get("files") or []
    return next((f for f in fs if f.get("kind") == "mod"), fs[0] if fs else None)


def file_name(url):
    return PurePosixPath(urlparse(url).path).name


def version_key(name):
    return tuple(int(n) for n in re.findall(r"\d+", name))


def zip_links(html, base):
    """Liens .zip d'une page, en adresses absolues."""
    return {urljoin(base, h) for h in re.findall(r'href=["\']([^"\']+?\.zip(?:\?[^"\']*)?)["\']', html, re.I)}


def resolve(url):
    """Trouve la dernière version d'un mod : {"version", "file", "sha256" (ou None)}."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise RuntimeError("L'adresse doit commencer par https://")
    if u.hostname == "github.com":
        parts = [p for p in u.path.split("/") if p]
        if len(parts) < 2:
            raise RuntimeError("Adresse GitHub attendue : https://github.com/<auteur>/<dépôt>")
        rel = json.loads(fetch(f"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases/latest"))
        asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")), None)
        if not asset:
            raise RuntimeError("La dernière release GitHub n'a pas de fichier .zip.")
        digest = asset.get("digest") or ""
        return {"version": rel["tag_name"], "file": asset["browser_download_url"],
                "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else None}

    links = zip_links(fetch(url).decode("utf-8", "replace"), url)
    if links:
        # shortcut: prend le .zip au numéro de version le plus haut ; une page à plusieurs variantes demande un lien direct
        best = max(links, key=lambda l: version_key(file_name(l)))
        return {"version": file_name(best)[:-4], "file": best, "sha256": None}
    try:  # page remplie en JavaScript (ex. NightCoop) : manifeste releases.json à côté
        rel = latest_release(json.loads(fetch(urljoin(url, "releases.json"))))
    except (OSError, ValueError):
        rel = None
    f = rel and mod_file(rel)
    if not f:
        raise RuntimeError("Aucun fichier .zip trouvé sur cette page (les .7z / .rar ne sont pas gérés).")
    return {"version": rel["version"], "file": urljoin(url, f["url"]), "sha256": f.get("sha256")}


def safe_extract(zip_path, dest):
    """Extrait le zip dans dest en refusant tout chemin qui en sortirait (zip slip). Retourne les fichiers extraits."""
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            if not (dest / m.filename).resolve().is_relative_to(dest):
                raise RuntimeError(f"Chemin suspect dans l'archive : {m.filename}")
        z.extractall(dest)
        return [m.filename for m in z.infolist() if not m.is_dir()]


def install(info, game_dir, progress=None, extract=True):
    """Télécharge et vérifie l'archive, l'extrait si extract ; retourne la liste de ses fichiers."""
    data = fetch(info["file"], progress)
    if info["sha256"] and hashlib.sha256(data).hexdigest() != info["sha256"].lower():
        raise RuntimeError("SHA-256 incorrect : fichier corrompu ou modifié, installation annulée.")
    with tempfile.TemporaryDirectory() as tmp:
        zp = Path(tmp) / "mod.zip"
        zp.write_bytes(data)
        if not zipfile.is_zipfile(zp):
            raise RuntimeError("Le fichier téléchargé n'est pas une archive .zip.")
        if not extract:
            with zipfile.ZipFile(zp) as z:
                return [m.filename for m in z.infolist() if not m.is_dir()]
        return safe_extract(zp, game_dir)


def update_mod(cfg, mod, progress=None, info=None):
    """Retourne (dernière_version, a_été_installée)."""
    info = info or resolve(mod["url"])
    host = urlparse(info["file"]).hostname
    if mod.setdefault("host", host) != host:
        raise RuntimeError(f"Le fichier vient maintenant de {host} au lieu de {mod['host']} : "
                           "retire puis rajoute le mod pour confirmer cette nouvelle source.")
    if info["version"] == mod.get("installed"):
        return info["version"], False
    files = install(info, cfg["game_dir"], progress)
    mod["installed"] = info["version"]
    # on garde aussi les fichiers des versions précédentes : la désinstallation les retirera tous
    mod["files"] = sorted(set(mod.get("files", [])) | set(files))
    save_config(cfg)
    return info["version"], True


def uninstall_mod(cfg, mod, progress=None):
    """Supprime du jeu les fichiers installés par le mod. Retourne (nb_supprimés, fichiers_partagés_conservés)."""
    files = mod.get("files")
    if files is None:
        # shortcut: mod installé avant le suivi des fichiers ; on relit l'archive actuelle, une ancienne version
        # peut avoir laissé d'autres fichiers
        files = install(resolve(mod["url"]), None, progress, extract=False)
    game = Path(cfg["game_dir"]).resolve()
    shared = {f.lower() for m in cfg["mods"] if m is not mod for f in m.get("files", [])}
    removed, kept, dirs = 0, [], set()
    for f in files:
        path = (game / f).resolve()
        if not path.is_relative_to(game) or path == (game / GAME_EXE).resolve():
            continue
        if f.lower() in shared:
            kept.append(f)
            continue
        if path.is_file():
            path.unlink()
            removed += 1
        dirs.update(p for p in path.parents if p.is_relative_to(game) and p != game)
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):  # les plus profonds d'abord
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    mod.pop("installed", None)
    mod.pop("files", None)
    save_config(cfg)
    return removed, kept


def switches_file(game_dir):
    return Path(game_dir) / NC_DIR / "switches.txt"


def switch_name(line):
    return line.split("=", 1)[0].strip()


def patch_enabled(game_dir, name):
    f = switches_file(game_dir)
    lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    return any(switch_name(l) == name and l.split("=", 1)[-1].strip().lower() == "on" for l in lines)


def set_patch(game_dir, name, on):
    """Ajoute ou retire la ligne « name = on » sans toucher aux autres réglages du fichier."""
    f = switches_file(game_dir)
    lines = [l for l in (f.read_text(encoding="utf-8").splitlines() if f.is_file() else [])
             if l.strip() and switch_name(l) != name]
    if on:
        lines.append(f"{name} = on")
    if lines:
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif f.is_file():
        f.unlink()  # plus aucun réglage : le mod repart sur ses valeurs par défaut


def startup_command():
    if getattr(sys, "frozen", False):  # version .exe (PyInstaller)
        return f'"{sys.executable}" --startup'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else exe}" "{Path(__file__).resolve()}" --startup'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


BG, PANEL, LINE = "#0a0a0f", "#14141c", "#2a2a36"
YELLOW, CYAN, RED, GREEN, TEXT, MUTED = "#fcee0a", "#00f0ff", "#ff003c", "#3dff8a", "#e8e8ee", "#8a8a99"
FONT = "Bahnschrift"


def run_gui(startup=False):
    import tkinter as tk
    import webbrowser
    from tkinter import filedialog, messagebox, ttk

    cfg = load_config()
    infos = {}  # url -> résultat de resolve() ou l'exception
    state = {"busy": False}
    if autostart_enabled():
        set_autostart(True)  # garde la clé alignée sur l'emplacement actuel de l'exe

    root = tk.Tk()
    root.title("Cyber Mod Hub")
    root.configure(bg=BG)
    root.resizable(False, False)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("NC.Horizontal.TProgressbar", troughcolor=PANEL, background=YELLOW,
                    bordercolor=LINE, lightcolor=YELLOW, darkcolor=YELLOW)

    def label(parent, text="", size=10, color=TEXT, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color,
                        font=(FONT, size, "bold" if bold else "normal"), **kw)

    def button(parent, text, cmd, bg=PANEL, fg=TEXT, size=10, padx=16):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=fg,
                         activeforeground=bg, disabledforeground=MUTED, relief="flat", bd=0,
                         font=(FONT, size, "bold"), padx=padx, pady=7, cursor="hand2")

    def entry(parent, width):
        return tk.Entry(parent, width=width, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                        font=(FONT, 10), highlightthickness=1, highlightbackground=LINE, highlightcolor=CYAN)

    def title(parent, text):
        label(parent, text, 9, MUTED).pack(anchor="w", pady=(0, 4))

    # --- en-tête
    head = tk.Frame(root, bg=BG, padx=24, pady=16)
    head.pack(fill="x")
    label(head, "CYBER MOD HUB", 26, YELLOW, True).pack(anchor="w")
    label(head, "MISES À JOUR DES MODS  //  CYBERPUNK 2077", 10, CYAN).pack(anchor="w")
    tk.Frame(root, bg=RED, height=2).pack(fill="x", padx=24)

    body = tk.Frame(root, bg=BG, padx=24, pady=14)
    body.pack(fill="both")

    # --- dossier du jeu
    title(body, "DOSSIER DE CYBERPUNK 2077")
    row = tk.Frame(body, bg=BG)
    row.pack(fill="x", pady=(0, 12))
    dir_lbl = label(row, "", 10, TEXT, anchor="w")
    dir_lbl.pack(side="left", fill="x", expand=True)

    def browse():
        d = filedialog.askdirectory(title="Choisir le dossier de Cyberpunk 2077",
                                    initialdir=cfg.get("game_dir") or None)
        if not d:
            return
        if not is_game_dir(d):
            messagebox.showwarning("Dossier invalide", f"{GAME_EXE} est introuvable dans ce dossier.\n"
                                   "Choisis le dossier racine du jeu (celui qui contient « bin »).")
            return
        cfg["game_dir"] = str(Path(d))
        save_config(cfg)
        refresh()

    button(row, "PARCOURIR…", browse).pack(side="right")

    # --- liste des mods
    title(body, "MODS SUIVIS")
    mods_box = tk.Frame(body, bg=BG)
    mods_box.pack(fill="x")

    def mod_line(mod):
        info, inst = infos.get(mod["url"]), mod.get("installed", "—")
        if isinstance(info, Exception):
            return f"installé {inst}  ·  erreur : {info}", RED
        if not info:
            return f"installé {inst}  ·  vérification…", MUTED
        if info["version"] == mod.get("installed"):
            return f"✔ à jour  ·  {inst}", GREEN
        return f"installé {inst}  →  disponible {info['version']}", YELLOW

    def render_mods():
        for w in mods_box.winfo_children():
            w.destroy()
        for mod in cfg["mods"]:
            card = tk.Frame(mods_box, bg=PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=LINE)
            card.pack(fill="x", pady=(0, 6))
            txt = tk.Frame(card, bg=PANEL)
            txt.pack(side="left", fill="x", expand=True)
            label(txt, mod["name"], 12, TEXT, True, anchor="w").pack(fill="x")
            line, color = mod_line(mod)
            label(txt, line, 9, color, anchor="w", wraplength=380, justify="left").pack(fill="x")
            button(card, "✕", lambda m=mod: remove_mod(m), PANEL, MUTED, 9, 8).pack(side="right")
            if mod.get("installed"):
                button(card, "DÉSINSTALLER", lambda m=mod: uninstall(m), PANEL, RED, 9, 10).pack(side="right")
            button(card, "PAGE", lambda m=mod: webbrowser.open(m["url"]), PANEL, CYAN, 9, 10).pack(side="right", padx=4)
            button(card, "MAJ", lambda m=mod: run_updates([m]), PANEL, YELLOW, 9, 10).pack(side="right")
        if not cfg["mods"]:
            label(mods_box, "Aucun mod : ajoute une page de téléchargement ci-dessous.", 10, MUTED).pack(anchor="w")

    # --- ajout d'un mod
    add = tk.Frame(body, bg=BG, pady=8)
    add.pack(fill="x")
    label(add, "Nom", 9, MUTED).grid(row=0, column=0, sticky="w")
    label(add, "Page de téléchargement (GitHub, page avec liens .zip…)", 9, MUTED).grid(row=0, column=1, sticky="w")
    name_in, url_in = entry(add, 16), entry(add, 42)
    name_in.grid(row=1, column=0, sticky="we", ipady=5)
    url_in.grid(row=1, column=1, sticky="we", padx=6, ipady=5)
    add.columnconfigure(1, weight=1)

    def add_mod():
        name, url = name_in.get().strip(), url_in.get().strip()
        if not name or not url.startswith("https://"):
            messagebox.showwarning("Ajout", "Indique un nom et une adresse qui commence par https://")
            return
        if any(m["url"] == url for m in cfg["mods"]):
            messagebox.showinfo("Ajout", "Ce mod est déjà dans la liste.")
            return
        status.configure(text=f"Analyse de {url}…")

        def work():
            try:
                info = resolve(url)
            except Exception as e:
                root.after(0, lambda: (status.configure(text=""), messagebox.showerror("Ajout impossible", str(e))))
                return
            root.after(0, lambda: confirm(name, url, info))
        threading.Thread(target=work, daemon=True).start()

    def confirm(name, url, info):
        status.configure(text="")
        sha = "vérifié par SHA-256" if info["sha256"] else "sans empreinte SHA-256 (confiance dans le site)"
        if not messagebox.askyesno("Ajouter ce mod ?", f"Dernière version : {info['version']}\n"
                                   f"Fichier : {info['file']}\n({sha})\n\nLe contenu du .zip sera extrait "
                                   "dans le dossier du jeu. Ajouter ?"):
            return
        cfg["mods"].append({"name": name, "url": url, "host": urlparse(info["file"]).hostname})
        infos[url] = info
        save_config(cfg)
        name_in.delete(0, "end")
        url_in.delete(0, "end")
        refresh()

    def remove_mod(mod):
        if messagebox.askyesno("Retirer", f"Retirer {mod['name']} de la liste ?\n"
                               "Les fichiers déjà installés dans le jeu restent en place."):
            cfg["mods"].remove(mod)
            save_config(cfg)
            refresh()

    def uninstall(mod):
        if state["busy"] or not is_game_dir(cfg.get("game_dir")):
            return
        known = "files" in mod
        others = [m["name"] for m in cfg["mods"] if m is not mod and m.get("installed") and "files" not in m]
        if not messagebox.askyesno("Désinstaller", f"Supprimer du jeu les fichiers de {mod['name']} ?\n\n"
                                   + ("" if known else "Liste des fichiers inconnue (installé avant cette fonction) : "
                                      "l'archive actuelle va être téléchargée pour la retrouver.\n")
                                   + (f"Attention : les fichiers communs avec {', '.join(others)} ne sont pas "
                                      "connus et peuvent être supprimés aussi.\n" if others else "")
                                   + "\nFerme le jeu avant de continuer.", icon="warning"):
            return
        state["busy"] = True
        refresh()

        def work():
            try:
                removed, kept = uninstall_mod(cfg, mod, lambda p: root.after(0, lambda: bar.configure(value=p)))
                msg, color = f"{mod['name']} désinstallé : {removed} fichier(s) supprimé(s)" + \
                    (f", {len(kept)} conservé(s) car utilisés par un autre mod." if kept else "."), GREEN
            except Exception as e:
                msg, color = f"{mod['name']} : désinstallation impossible : {e}  (le jeu est peut-être ouvert)", RED
            state["busy"] = False
            root.after(0, lambda: (status.configure(text=msg, fg=color), refresh()))
        threading.Thread(target=work, daemon=True).start()

    button(add, "AJOUTER", add_mod, PANEL, CYAN).grid(row=1, column=2)

    # --- actions
    actions = tk.Frame(body, bg=BG, pady=10)
    actions.pack(fill="x")
    all_btn = button(actions, "TOUT METTRE À JOUR", lambda: run_updates(list(cfg["mods"])), YELLOW, BG, 12)
    all_btn.pack(side="left")
    play_btn = button(actions, "▶  LANCER LE JEU",
                      lambda: os.startfile(Path(cfg["game_dir"]) / GAME_EXE), PANEL, CYAN)
    play_btn.pack(side="left", padx=10)
    dir_btn = button(actions, "OUVRIR LE DOSSIER", lambda: os.startfile(cfg["game_dir"]))
    dir_btn.pack(side="left")

    status = label(body, "", 10, MUTED, anchor="w", wraplength=600, justify="left")
    status.pack(fill="x", pady=(4, 4))
    bar = ttk.Progressbar(body, style="NC.Horizontal.TProgressbar", maximum=1.0)
    bar.pack(fill="x")

    # --- patchs de synchro NightCoop
    patch_box = tk.Frame(body, bg=BG, pady=8)
    if PATCHES:
        patch_box.pack(fill="x")
    title(patch_box, "PATCHS DE SYNCHRO NIGHTCOOP  (à cocher pareil chez tous les joueurs · effet au prochain lancement)")
    patch_vars, patch_checks = {}, []

    def toggle_patch(name):
        try:
            set_patch(cfg["game_dir"], name, patch_vars[name].get())
        except OSError as e:
            patch_vars[name].set(not patch_vars[name].get())
            messagebox.showerror("Erreur", f"Impossible de modifier switches.txt :\n{e}")

    for name, text in PATCHES:
        patch_vars[name] = tk.BooleanVar()
        cb = tk.Checkbutton(patch_box, text=text, variable=patch_vars[name], command=lambda n=name: toggle_patch(n),
                            bg=BG, fg=TEXT, selectcolor=PANEL, activebackground=BG, activeforeground=YELLOW,
                            disabledforeground=MUTED, font=(FONT, 10), cursor="hand2")
        cb.pack(anchor="w")
        patch_checks.append(cb)

    auto_var = tk.BooleanVar(value=autostart_enabled())

    def toggle_auto():
        try:
            set_autostart(auto_var.get())
        except OSError as e:
            auto_var.set(not auto_var.get())
            messagebox.showerror("Erreur", f"Impossible de modifier le démarrage automatique :\n{e}")

    tk.Checkbutton(body, text="Lancer au démarrage de Windows (réduit, met tout à jour automatiquement)",
                   variable=auto_var, command=toggle_auto, bg=BG, fg=TEXT, selectcolor=PANEL,
                   activebackground=BG, activeforeground=YELLOW, font=(FONT, 10),
                   cursor="hand2").pack(anchor="w", pady=(10, 0))

    def refresh():
        ok_dir = is_game_dir(cfg.get("game_dir"))
        dir_lbl.configure(text=cfg.get("game_dir") or "Aucun dossier choisi", fg=TEXT if ok_dir else RED)
        for b in (play_btn, dir_btn):
            b.configure(state="normal" if ok_dir else "disabled")
        all_btn.configure(state="disabled" if state["busy"] or not ok_dir else "normal")
        nc_ok = ok_dir and (Path(cfg["game_dir"]) / NC_DIR / "NightCoop.dll").is_file()
        for (name, _), cb in zip(PATCHES, patch_checks):
            patch_vars[name].set(nc_ok and patch_enabled(cfg["game_dir"], name))
            cb.configure(state="normal" if nc_ok else "disabled")
        render_mods()

    def resolve_waiting_network(url):
        # au démarrage de Windows le réseau arrive souvent après l'appli : on réessaie jusqu'à 3 min
        for attempt in range(18 if startup else 1):
            try:
                return resolve(url)
            except urllib.error.URLError as e:
                if isinstance(e, urllib.error.HTTPError) or attempt == (17 if startup else 0):
                    raise
                time.sleep(10)

    def check_all(then=None):
        def work():
            for mod in list(cfg["mods"]):
                try:
                    infos[mod["url"]] = resolve_waiting_network(mod["url"])
                except Exception as e:
                    infos[mod["url"]] = e
                root.after(0, refresh)
            if then:
                root.after(0, then)
        threading.Thread(target=work, daemon=True).start()

    def run_updates(mods):
        if state["busy"]:
            return
        if not is_game_dir(cfg.get("game_dir")):
            messagebox.showwarning("Dossier manquant", "Choisis d'abord le dossier de Cyberpunk 2077.")
            return
        state["busy"] = True
        refresh()

        def work():
            done, errors = [], []
            for mod in mods:
                def prog(p, n=mod["name"]):
                    root.after(0, lambda: (bar.configure(value=p), status.configure(text=f"{n} : téléchargement… {p:.0%}")))
                root.after(0, lambda n=mod["name"]: (bar.configure(value=0), status.configure(text=f"{n} : vérification…")))
                try:
                    info = infos.get(mod["url"])
                    version, installed = update_mod(cfg, mod, prog, info if isinstance(info, dict) else None)
                    if installed:
                        done.append(f"{mod['name']} {version}")
                except Exception as e:
                    errors.append(f"{mod['name']} : {e}")
            state["busy"] = False
            msg = ("Installé : " + ", ".join(done) if done else "Tout est à jour.") + \
                  ("\nErreurs : " + " | ".join(errors) + "  (le jeu est peut-être ouvert)" if errors else "")
            root.after(0, lambda: (status.configure(text=msg, fg=RED if errors else GREEN), refresh(),
                                   (done or errors) and root.deiconify()))
        threading.Thread(target=work, daemon=True).start()

    refresh()
    if startup:
        root.iconify()  # visible dans la barre des tâches, revient au premier plan s'il a installé quelque chose
        check_all(then=lambda: run_updates(list(cfg["mods"])) if is_game_dir(cfg.get("game_dir")) else None)
    else:
        check_all()
    root.mainloop()


if __name__ == "__main__":
    run_gui(startup="--startup" in sys.argv)
