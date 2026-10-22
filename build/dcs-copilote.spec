# -*- mode: python ; coding: utf-8 -*-
# Recette PyInstaller : pyinstaller --noconfirm build/dcs-copilote.spec  →  dist/DCS-Copilote/
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.dirname(SPECPATH)

datas = [(os.path.join(ROOT, "dcs", "DCSAssistant.lua"), "dcs")]
binaries = []
hiddenimports = collect_submodules("pynput") + collect_submodules("pyttsx3.drivers")
# bibliothèques avec des fichiers non Python (modèles, DLL, données espeak…)
import importlib.util

for pkg in ("faster_whisper", "ctranslate2", "piper", "onnxruntime", "fastembed", "tokenizers",
            "pymupdf", "edge_tts",
            "_sounddevice_data"):   # PortAudio fourni avec sounddevice sous Windows / macOS
    if importlib.util.find_spec(pkg) is None:
        continue
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# parties exclues plus bas (entraînement) : ne pas les réclamer comme imports cachés
hiddenimports = [h for h in hiddenimports if not h.startswith(("piper.train", "onnxruntime.quantization"))]

icon = os.path.join(SPECPATH, "icon.ico")

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # entraînement de voix / quantification : inutiles et dépendent de torch / onnx
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "piper.train", "onnxruntime.quantization"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DCS-Copilote",
    console=False,                  # appli fenêtrée : le journal va dans logs/
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DCS-Copilote")
