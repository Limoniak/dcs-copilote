#!/usr/bin/env python3
"""DCS Copilote — assistant vocal d'apprentissage pour DCS World.

Tous les réglages se font dans l'interface (bouton ⚙ de l'overlay). Commandes optionnelles :

  python main.py                      lance l'assistant
  python main.py --bind               associe le bouton push-to-talk (touche ou HOTAS)
  python main.py --devices            liste micros, sorties audio et joysticks
  python main.py --ask "question"     teste la config IA sans micro
  python main.py --models             liste les modèles du fournisseur IA
  python main.py --provider gemini --model gemini-2.5-pro   change d'IA pour ce lancement
  python main.py --download-voice fr_FR-siwis-medium   télécharge une voix Piper
"""
import argparse
import os
import sys

# Version empaquetée (.exe PyInstaller) : les fichiers de l'utilisateur (config, journal, manuels,
# voix) vont à côté de l'exécutable ; sinon à côté de ce script.
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

from dcs_copilote import config as cfgmod  # noqa: E402

CFG_PATH = os.path.join(HERE, "config.json")


def cmd_bind(cfg):
    from dcs_copilote.ptt import capture_binding
    print("Appuie sur la touche clavier ou le bouton HOTAS à utiliser pour parler (20 s)…")
    b = capture_binding()
    if not b:
        print("Rien détecté.")
        return 1
    cfg["ptt"].update(b)
    cfgmod.save(CFG_PATH, cfg)
    if b["type"] == "keyboard":
        print(f"OK : touche « {b['key']} » enregistrée.")
    else:
        print(f"OK : bouton {b['button'] + 1} de « {b['joystick_name']} » enregistré.")
    print("Pense à vérifier que ce bouton n'est assigné à rien dans DCS.")
    return 0


def cmd_devices():
    import sounddevice as sd
    print("=== Audio (index : nom) ===")
    for i, d in enumerate(sd.query_devices()):
        io = ("E" if d["max_input_channels"] else "-") + ("S" if d["max_output_channels"] else "-")
        print(f"  {i:3d} [{io}] {d['name']}")
    print(f"  défaut : entrée {sd.default.device[0]}, sortie {sd.default.device[1]}")
    try:
        from dcs_copilote.ptt import list_joysticks
        print("=== Joysticks ===")
        for i, name, n in list_joysticks():
            print(f"  {i}: {name} ({n} boutons)")
    except Exception as e:  # noqa: BLE001
        print(f"(joysticks : {e})")
    return 0


def cmd_ask(cfg, question, aircraft):
    from dcs_copilote.llm import LLMClient, LLMError
    from dcs_copilote.prompts import system_prompt
    msgs = [{"role": "system", "content": system_prompt(aircraft, None, "vol", cfg["llm"].get("answer_length", "courte"),
                                                         cfg["llm"].get("custom_prompt", ""))},
            {"role": "user", "content": question}]
    try:
        for d in LLMClient(cfg["llm"]).stream(msgs):
            print(d, end="", flush=True)
        print()
    except LLMError as e:
        print(f"\nErreur : {e}")
        return 1
    return 0


def cmd_models(cfg):
    from dcs_copilote.llm import LLMClient, LLMError
    try:
        client = LLMClient(cfg["llm"])
        ids = client.models()
    except LLMError as e:
        print(f"Erreur : {e}")
        return 1
    print(f"=== {client.cfg['provider']} ({client.base}) — actuel : {client.cfg['model']} ===")
    for m in ids:
        print(f"  {m}")
    return 0


def cmd_selftest():
    """Vérifie que tout ce dont l'appli a besoin est présent (utilisé par la fabrication du .exe)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    checks = []

    def check(name, fn):
        try:
            detail = fn()
            checks.append((True, name, detail or ""))
        except Exception as e:  # noqa: BLE001
            checks.append((False, name, f"{type(e).__name__}: {e}"))

    def qt():
        from dcs_copilote import ui_kit
        ui_kit.application()
        ui_kit.icon("plane")
        return "PySide6"

    def whisper():
        import faster_whisper.utils as u
        assets = os.listdir(u.get_assets_path())
        assert any("silero" in a for a in assets), "modèle VAD absent"
        return "assets OK"

    def piper_voice():
        import glob
        from dcs_copilote.audio import PiperTTS
        voices = glob.glob(os.path.join("voices", "*.onnx"))
        if not voices:
            import piper  # noqa: F401
            return "paquet OK (aucune voix fournie)"
        audio, sr = PiperTTS(voices[0]).synth("Test.")
        assert audio is not None and len(audio), "synthèse vide"
        return f"{os.path.basename(voices[0])} : {len(audio) / sr:.1f} s"

    def lua():
        from dcs_copilote.dcs_install import LUA_SRC
        assert os.path.exists(LUA_SRC), LUA_SRC
        return "DCSAssistant.lua"

    def mods():
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import edge_tts  # noqa: F401
        import fastembed  # noqa: F401
        import miniaudio  # noqa: F401
        import pygame  # noqa: F401
        import pymupdf  # noqa: F401
        import requests  # noqa: F401
        return "edge-tts, fastembed, miniaudio, pygame, pymupdf"

    def audio():
        import sounddevice as sd
        return f"PortAudio {sd.get_portaudio_version()[1]}"

    def keyboard():
        if sys.platform != "win32" and not os.environ.get("DISPLAY"):
            return "ignoré (pas d'écran)"
        from pynput import keyboard as kb  # noqa: F401
        return "pynput"

    for name, fn in (("Interface", qt), ("Whisper", whisper), ("Voix Piper", piper_voice), ("Script DCS", lua),
                     ("Modules", mods), ("Audio", audio), ("Clavier", keyboard)):
        check(name, fn)
    lines = [f"{'OK ' if ok else 'ÉCHEC'} {name:12} {detail}" for ok, name, detail in checks]
    print("\n".join(lines), flush=True)
    with open("selftest.log", "w", encoding="utf-8") as f:    # le .exe fenêtré n'a pas de console
        f.write("\n".join(lines) + "\n")
    return 0 if all(ok for ok, _n, _d in checks) else 1


def cmd_download_voice(name):
    from dcs_copilote.audio import download_voice
    print(f"Téléchargement de {name}…")
    print(f"OK → {download_voice(name)}")
    return 0


def main():
    p = argparse.ArgumentParser(description="DCS Copilote")
    p.add_argument("--bind", action="store_true")
    p.add_argument("--devices", action="store_true")
    p.add_argument("--ask")
    p.add_argument("--aircraft", default=None, help="appareil pour --ask")
    p.add_argument("--download-voice", metavar="NOM")
    p.add_argument("--models", action="store_true", help="liste les modèles du fournisseur IA")
    p.add_argument("--provider", help="fournisseur IA pour ce lancement (ollama, openai, gemini, grok…)")
    p.add_argument("--model", help="modèle IA pour ce lancement")
    p.add_argument("--selftest", action="store_true", help="vérifie que l'appli est complète")
    a = p.parse_args()
    if a.selftest:
        return cmd_selftest()

    first_run = not os.path.exists(CFG_PATH)
    cfg = cfgmod.load(CFG_PATH)
    if first_run:
        cfgmod.save(CFG_PATH, cfg)
        print("config.json créé avec les valeurs par défaut — réglages : bouton ⚙ de l'overlay.")
    # surcharges pour ce lancement uniquement (non enregistrées dans config.json)
    llm = cfg["llm"]
    if a.provider:
        llm["provider"] = a.provider
    if a.model:
        llm.setdefault("providers", {}).setdefault(llm["provider"], {})["model"] = a.model

    if a.bind:
        return cmd_bind(cfg)
    if a.devices:
        return cmd_devices()
    if a.ask:
        return cmd_ask(cfg, a.ask, a.aircraft)
    if a.download_voice:
        return cmd_download_voice(a.download_voice)
    if a.models:
        return cmd_models(cfg)

    from dcs_copilote import logfile
    logfile.setup()
    from dcs_copilote import ui_kit
    qapp = ui_kit.application()
    lock = ui_kit.single_instance()
    if lock is None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "DCS Copilote", "DCS Copilote est déjà lancé.")
        return 0
    from dcs_copilote.app import App
    App(cfg, CFG_PATH, first_run).run()
    del qapp, lock
    return 0


if __name__ == "__main__":
    sys.exit(main())
