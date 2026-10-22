@echo off
cd /d "%~dp0"
rem Python 3.11 ou 3.12 recommande (roues piper-tts / ctranslate2 disponibles)
py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py --download-voice fr_FR-siwis-medium
echo.
echo Installation terminee. Etapes suivantes :
echo   lance run.bat : les reglages (IA, bouton PTT, script DCS...) s'ouvrent au premier lancement
pause
