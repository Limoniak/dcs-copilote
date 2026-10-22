#!/usr/bin/env bash
# Debian/Ubuntu : sudo apt install python3-venv libportaudio2 libxcb-cursor0 espeak-ng
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py --download-voice fr_FR-tom-medium
echo
echo "Installation terminée. Ensuite :"
echo "  ./run.sh  — les réglages (IA, bouton PTT, script DCS…) s'ouvrent au premier lancement (bouton ⚙)"
