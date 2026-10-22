# Synthèse du projet — DCS Copilote

Assistant IA d'apprentissage pour DCS World. On maintient un bouton (clavier ou HOTAS), on pose sa question à voix haute en vol, la réponse s'affiche dans un overlay et est lue au casque. Windows et Linux, 100 % Python.

## Choix techniques

| Brique | Choix | Pourquoi |
|---|---|---|
| Manuels | PyMuPDF + fastembed (`paraphrase-multilingual-MiniLM-L12-v2`) + BM25, local | Question FR → passage du manuel EN, filtré sur l'appareil piloté |
| IA | API compatible OpenAI (`/chat/completions`, streaming SSE), préréglages par fournisseur | Un seul client pour Ollama, ChatGPT, Gemini, Grok, OpenRouter, Open WebUI… |
| Voix → texte | faster-whisper, local, CPU int8 | Gratuit, hors-ligne, ne prend pas de VRAM à DCS |
| Texte → voix | Microsoft Edge (edge-tts, en ligne), OpenAI TTS, Piper (local), pyttsx3 (SAPI / espeak) ; repli auto sur Piper | Voix neuronales naturelles ; Piper garantit le hors-ligne |
| Push-to-talk | pynput (clavier global) + pygame/SDL (joystick) | Fonctionne même quand DCS a le focus |
| Contexte DCS | Script `Export.lua` (v2) → UDP local | Appareil + sol/vol, altitude, hauteur sol, vitesse, Mach, cap, vario, incidence, G, carburant, train, volets |
| Interface | Qt / PySide6 (overlay translucide toujours au premier plan + fenêtre Réglages) | Rendu moderne (transparence, coins arrondis, animations), multiplateforme |

## Flux principal

```
appui PTT ──► bip + Recorder.start()          (annule réponse/voix en cours)
relâche   ──► Recorder.stop() ─► file jobs
worker    ──► STT.transcribe() ─► App._answer()
               ├─ prompt système = rôle instructeur + appareil + données de vol + mode (vol/sol)
               ├─ + historique (6 échanges)
               └─ LLMClient.stream() ─► morceaux de texte
                    ├─► Overlay.append()            (affichage au fil de l'eau)
                    └─► SentenceSplitter ─► Speaker.say()  (lecture phrase par phrase)

DCS : DCSAssistant.lua ─UDP 127.0.0.1:7779 (1×/s)─► DCSLink ─► App.on_dcs()
```

Deux modes :

- **vol** (question vocale) : 1 à 3 phrases, sans markdown, lues à voix haute.
- **sol** (question tapée dans l'overlay) : réponse détaillée, non lue par défaut (`tts.speak_typed`).

## Arborescence

```
dcs-copilote/
├── main.py                 Point d'entrée + commandes CLI (--bind, --devices, --ask, --models, --provider, --model, --download-voice)
├── requirements.txt
├── install.bat / run.bat   Windows (venv, deps, voix Piper fr_FR-siwis-medium)
├── install.sh  / run.sh    Linux
├── README.md               Guide utilisateur
├── SYNTHESE.md             Ce fichier
├── dcs/
│   └── DCSAssistant.lua    Script côté DCS (Saved Games/DCS/Scripts), chaîne les exports existants
└── dcs_copilote/
    ├── app.py              Orchestration : événements PTT, worker, annulation, historique
    ├── config.py           Valeurs par défaut + fusion avec config.json
    ├── llm.py              Client OpenAI-compatible streaming, choix du fournisseur, liste des modèles,
    │                       nouvel essai sur 429/5xx + modèle de secours, messages d'erreur lisibles, filtre <think>
    ├── stt.py              faster-whisper + prompt de vocabulaire aviation
    ├── audio.py            Recorder (micro toujours ouvert), bips, PiperTTS/SystemTTS, Speaker
    ├── ptt.py              KeyboardPTT, JoystickPTT, capture_binding (--bind), list_joysticks
    ├── dcs_link.py         Écoute UDP, détecte changement d'appareil / perte de DCS (8 s)
    ├── ui_kit.py           Thème Qt (couleurs, feuille de style), icônes SVG, interrupteur et pastille animés
    ├── overlay.py          Overlay Qt : menu appareil, réponse Markdown en direct, champ de saisie, pastille d'état, bouton ⚙
    ├── settings.py         Fenêtre Réglages (onglets IA, Voix, Audio, Bouton PTT, DCS & Overlay)
    ├── dcs_install.py      Détection de Saved Games/DCS, installation du script + ligne Export.lua
    ├── prompts.py          Prompt système : base + mode vol (longueur réglable, phrases ≤ 15 mots) / sol + consignes perso
    ├── manuals.py          Manuels PDF : indexation (PyMuPDF + embeddings multilingues fastembed) et recherche hybride
    ├── chucks.py           Chuck's Guides : liste du site, ETag, téléchargement des guides nouveaux/modifiés
    ├── logfile.py          Journal logs/dcs-copilote.log (horodaté) + capture des exceptions
    ├── textutil.py         Découpage en phrases + nettoyage pour la voix (kt → nœuds…)
    └── aircraft.py         Noms internes DCS → noms lisibles + liste du menu
```

## Threads

| Thread | Rôle |
|---|---|
| principal | Boucle Qt — l'interface n'est modifiée que via `overlay._call()` (signal Qt mis en file) |
| worker | Transcription + appel LLM, une requête à la fois |
| Speaker | Synthèse des phrases en file (la suivante pendant que la courante est jouée) |
| Speaker._player | Lecture audio des phrases synthétisées |
| DCSLink | Réception UDP |
| PTT | Listener pynput ou boucle d'événements pygame |
| _chucks_loop | Vérifie les Chuck's Guides au démarrage puis toutes les 6 h (chaque guide ≤ 1×/semaine) ; réindexation différée hors vol |
| _load_stt | Chargement de Whisper au démarrage (non bloquant) |

**Annulation** : un compteur `App.gen` est incrémenté à chaque appui PTT, question tapée ou reset. Toute tâche (stream LLM, phrase à lire) porteuse d'un ancien `gen` est abandonnée.

## Configuration

Tout se règle dans la fenêtre **Réglages** (⚙), ouverte automatiquement au premier lancement ou si la clé API manque. « Enregistrer » écrit `config.json` et applique à chaud : nouveau client IA, style de l'overlay, micro, sortie, PTT ; en arrière-plan : voix, port UDP (relance de DCSLink), modèle Whisper (l'ancien reste utilisable pendant le chargement).

### `config.json`

| Section | Clés importantes |
|---|---|
| llm | `provider`, `providers.<nom>` (`base_url`, `api_key`/`api_key_env`, `model`, `extra`), `temperature`, `max_history`, `timeout` |
| stt | `model` (`small` par défaut), `device` (cpu/cuda), `compute_type`, `language` |
| tts | `enabled`, `engine` (piper/system), `piper_model`, `speed`, `speak_typed` |
| audio | `input_device`, `output_device` (voir `--devices`), `beeps` |
| ptt | `type` (keyboard/joystick), `key` ou `joystick_name`/`joystick_guid`/`button` — rempli par `--bind` |
| dcs | `udp_port` (7779, doit correspondre au script Lua) |
| overlay | `position`, `width`, `lines`, `alpha`, `font_size` |

La position de l'overlay est sauvegardée à la fermeture (seule modification écrite : les surcharges `--provider`/`--model` ne sont pas enregistrées).

## Commandes

```bash
python main.py                         # lancer
python main.py --bind                  # associer le bouton PTT
python main.py --devices               # micros / sorties / joysticks
python main.py --ask "…" --aircraft "F-16C Viper"   # tester l'IA sans micro
python main.py --models                # modèles du fournisseur IA actif
python main.py --provider gemini --model gemini-2.5-pro   # changer d'IA pour un lancement
python main.py --download-voice fr_FR-siwis-medium
```

## État des tests

- **Vérifié avec matériel simulé** : streaming SSE (accents, `<think>` coupé entre deux paquets), découpage en phrases, nettoyage vocal, liaison UDP, enchaînement complet PTT → STT → LLM → voix, historique, annulation, mode vol/sol.
- **Vérifié sur serveur simulé** : choix du fournisseur, clé via variable d'environnement, `--models`, relance sans `temperature` (modèles de raisonnement OpenAI), migration de l'ancien format de config.
- **Vérifié avec carte son simulée, fenêtres masquées** : fenêtre Réglages (changement de fournisseur sans perte des clés, application à chaud, enregistrement, valeurs invalides), installation du script DCS (idempotente, sauvegarde d'Export.lua, port reporté dans le Lua).
- **Non testé sur machine réelle** : rendu visuel de la fenêtre Réglages, capture PTT depuis l'interface, appels réels à OpenAI / Gemini / Grok, overlay Qt sur écran réel (vérifié en rendu hors écran + captures), micro (sounddevice), Piper, lecture joystick sous SDL, script Lua dans DCS.

## Limites connues

- Fiabilité des réponses = celle du modèle. Les petits modèles locaux inventent des noms d'interrupteurs / raccourcis.
- Overlay invisible en plein écran exclusif et en VR (la voix reste).
- Wayland : touche clavier globale non fiable → bouton HOTAS conseillé.
- Linux/Wayland : l'interface passe par XWayland (`QT_QPA_PLATFORM=xcb`) pour rester au premier plan ; transparence de l'overlay seulement avec un compositeur actif.
- Script Lua v1 (ancien) : détecté, l'overlay propose de réinstaller le v2.
- Le script Lua utilise `os.clock()` pour cadencer l'envoi (continue en pause, contrairement au temps de mission).

## Pistes d'évolution

- Manuels officiels : réindexer automatiquement après une mise à jour de DCS (les Chuck's Guides le sont déjà).
- Armement sélectionné dans les données Lua (dépend du module).
- Mode checklist : l'assistant déroule une procédure étape par étape, « suivant » au PTT.
- Packaging : exécutable Windows avec PyInstaller.
