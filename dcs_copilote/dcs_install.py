"""Installation de DCSAssistant.lua dans Saved Games/DCS/Scripts (depuis les réglages)."""
import glob
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUA_SRC = os.path.join(HERE, "dcs", "DCSAssistant.lua")
EXPORT_LINE = ("pcall(function() local l=require('lfs'); "
               "dofile(l.writedir()..[[Scripts\\DCSAssistant.lua]]) end)")


def candidates():
    """Dossiers Saved Games/DCS existants (Windows, ou Steam/Proton sous Linux)."""
    if sys.platform == "win32":
        bases = [os.path.join(os.path.expanduser("~"), "Saved Games")]
    else:
        home = os.path.expanduser("~")
        steams = [os.path.join(home, ".steam", "steam"),
                  os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam")]
        bases = [os.path.join(s, "steamapps", "compatdata", "223750", "pfx", "drive_c",
                              "users", "steamuser", "Saved Games") for s in steams]
    out = []
    for b in bases:
        for d in sorted(glob.glob(os.path.join(b, "DCS*"))):
            real = os.path.realpath(d)
            if os.path.isdir(d) and real not in (os.path.realpath(o) for o in out):
                out.append(d)
    return out


def install(saved_games, port):
    """Copie le script (avec le port choisi) et l'ajoute à Export.lua. Renvoie un message."""
    if not saved_games or not os.path.isdir(saved_games):
        raise FileNotFoundError(f"Dossier introuvable : {saved_games or '(vide)'}")
    scripts = os.path.join(saved_games, "Scripts")
    os.makedirs(scripts, exist_ok=True)

    with open(LUA_SRC, "r", encoding="utf-8") as f:
        lua = f.read()
    lua = re.sub(r"port = \d+", f"port = {int(port)}", lua, count=1)
    with open(os.path.join(scripts, "DCSAssistant.lua"), "w", encoding="utf-8", newline="") as f:
        f.write(lua)

    export = os.path.join(scripts, "Export.lua")
    content = ""
    if os.path.exists(export):
        with open(export, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    if "DCSAssistant.lua" in content:
        return f"Script mis à jour dans {scripts} (Export.lua déjà configuré)."
    if content:
        shutil.copy2(export, export + ".bak")
    with open(export, "a", encoding="utf-8", newline="") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(EXPORT_LINE + "\n")
    return f"Script installé dans {scripts}. Relance la mission DCS."
