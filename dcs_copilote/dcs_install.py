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


def _script(port):
    with open(LUA_SRC, "r", encoding="utf-8") as f:
        return re.sub(r"port = \d+", f"port = {int(port)}", f.read(), count=1)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def status(saved_games, port):
    """« ok », « missing » (jamais installé) ou « outdated » (ancienne version ou autre port)."""
    scripts = os.path.join(saved_games, "Scripts")
    if "DCSAssistant.lua" not in _read(os.path.join(scripts, "Export.lua")):
        return "missing"
    return "ok" if _read(os.path.join(scripts, "DCSAssistant.lua")) == _script(port) else "outdated"


def install(saved_games, port):
    """Copie le script (avec le port choisi) et l'ajoute à Export.lua. Renvoie un message."""
    if not saved_games or not os.path.isdir(saved_games):
        raise FileNotFoundError(f"Dossier introuvable : {saved_games or '(vide)'}")
    scripts = os.path.join(saved_games, "Scripts")
    os.makedirs(scripts, exist_ok=True)

    with open(os.path.join(scripts, "DCSAssistant.lua"), "w", encoding="utf-8", newline="") as f:
        f.write(_script(port))

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


def dcs_running():
    """Vrai si DCS (DCS.exe, ou sous Proton) tourne."""
    import subprocess
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DCS.exe", "/NH"], capture_output=True,
                                 text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            return "dcs.exe" in out.lower()
        return subprocess.run(["pgrep", "-f", "DCS.exe"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
