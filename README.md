<p align="center"><img src="assets/logo/horizontal.svg" alt="DCS Copilote" width="720"></p>

# DCS Copilote

Ton copilote vocal pour DCS World (Windows / Linux) : il te parle comme un équipier dans le cockpit. Tu maintiens un bouton (clavier ou HOTAS), tu poses ta question à voix haute, la réponse s'affiche dans un overlay et est lue au casque. L'appareil piloté est détecté automatiquement via `Export.lua`.

```
[bouton PTT] → micro → Whisper (local) → LLM compatible OpenAI (streaming) → overlay + Piper (voix locale)
                                            ↑
                    DCS Export.lua → UDP 127.0.0.1:7779 (appareil, altitude, vitesse)
```

- **Appui** : bip, enregistrement. Un nouvel appui coupe la réponse en cours.
- **Question vocale** = mode *vol* : 1 à 3 phrases, lues au fur et à mesure qu'elles arrivent.
- **Question tapée** dans l'overlay = mode *sol* : réponse détaillée, non lue (sauf `speak_typed`).
- Contexte conservé sur 6 échanges (« et ensuite ? » fonctionne), remis à zéro au changement d'appareil ou avec ↺.

## Version Windows (.exe)

Pas besoin d'installer Python : télécharge `DCS-Copilote-vX.Y.Z-windows.zip` dans les **Releases** du dépôt, décompresse-le dans un dossier où tu peux écrire (par exemple `Documents\DCS-Copilote`, pas `Program Files`), puis lance `DCS-Copilote.exe`. La config, le journal, les manuels et les modèles téléchargés sont rangés à côté de l'exécutable. Quand une nouvelle version sort, l'appli le signale au démarrage : décompresse le nouveau .zip par-dessus, tes réglages sont conservés.

Fabrication automatique par GitHub Actions (`.github/workflows/windows-exe.yml`) :
- **publier une version** : `git tag v1.0.0 && git push origin v1.0.0` → construit, vérifie (`--selftest`) et publie le .zip dans les Releases ;
- **construction d'essai** : onglet *Actions* → *Windows .exe* → *Run workflow* → le .zip est téléchargeable dans le résultat (14 jours).

Fabrication à la main sous Windows : `pip install -r requirements.txt pyinstaller`, `python build/make_icon.py`, `pyinstaller --noconfirm build/dcs-copilote.spec` → `dist/DCS-Copilote/`.

Tests : `python -m pytest tests` (logique et interface sans écran, sans micro ni DCS) ; lancés aussi par GitHub Actions à chaque push.

## Installation

**Windows** (Python 3.11/3.12) : `install.bat`, puis `run.bat`.

**Linux** :
```bash
sudo apt install python3-venv libportaudio2 libxcb-cursor0 espeak-ng
./install.sh && ./run.sh
```

## Interface

Overlay semi-transparent toujours au premier plan (Qt / PySide6) : appareil, réponse affichée au fil de l'eau (mise en forme Markdown), champ de question, pastille d'état (vert prêt · rouge écoute · ambre réflexion). Fais-le glisser par son en-tête ; sa position est mémorisée. Le coin bas-droit le redimensionne (largeur et hauteur de la réponse) ; la taille est mémorisée.

- **Micro** à droite du champ : maintiens-le pour parler (pratique pour tester sans bouton PTT).
- **Répéter** relit la dernière réponse ; **Détailler** demande la version complète.
- **Haut-parleur** : lire ou non les réponses aux questions tapées.
- Icône dans la **zone de notification** (si le bureau en a une) : afficher / masquer l'overlay, réglages, quitter.
- Une seule instance à la fois ; journal dans `logs/dcs-copilote.log` (bouton « Ouvrir le journal » des réglages).
- **Historique** : chaque question et sa réponse (appareil, pages du manuel) sont notées dans `data/historique/AAAA-MM-JJ.md`, à relire après le vol (bouton « Historique des questions » des réglages).

## Checklists, alertes, latence

- **Checklist** : « Lance la procédure de démarrage » ou « checklist décollage » (à la voix ou tapé). Les étapes sont tirées des manuels indexés et annoncées une par une ; **appui bref sur le PTT** ou « suivant » = étape suivante, « répète », « précédent », « stop ». Boutons équivalents dans l'overlay.
- **Alertes vocales** (Réglages → Alertes) : train rentré en approche, taux de chute élevé près du sol, train ou volets sortis à grande vitesse, carburant bas, facteur de charge élevé. Une alerte passe devant la réponse en cours. Il faut le script DCS v2.
- **Effet radio** (Réglages → Voix) : léger, radio ou fort — bande étroite, saturation, souffle, clic d'ouverture et « kssh » de fin de transmission (une fois par réponse).
- **Latence** : chaque question laisse une ligne `[latence]` dans le journal (transcription, 1er mot de l'IA, début de la voix). Pour aller plus vite : « Réponse rapide en vol » (Réglages → IA, limite la réflexion de Gemini 2.5 / GPT-5…) et reconnaissance vocale **en ligne** (Réglages → Voix : Groq gratuit ~0,3 s, ou OpenAI), avec repli automatique sur Whisper local.

## Manuels : des réponses fiables

Sans manuel, l'IA répond de mémoire et peut inventer des noms d'interrupteurs. Dans **⚙ Réglages → Manuels**, indexe les PDF officiels de tes modules (`DCS World/Mods/aircraft/*/Doc/`) et, si tu veux, un dossier perso (Chuck's Guides…). À chaque question, les passages les plus utiles **du manuel de l'appareil piloté** sont donnés à l'IA, et les sources (document, page) s'affichent sous la réponse.

**Chuck's Guides** (chucksguides.com) : dès que tu voles sur un appareil sans manuel (détecté par DCS ou choisi dans l'overlay), son guide est **téléchargé et lu automatiquement** (~1 à 2 min). Les manuels officiels de DCS sont aussi repérés et lus au démarrage, sans clic. Les guides de tes modules installés (et ceux que tu coches) sont téléchargés dans `data/chucks/`, puis vérifiés chaque semaine : un guide n'est retéléchargé que s'il a changé (ETag), et seul ce guide est réanalysé. L'analyse attend que tu ne sois plus en vol pour ne pas faire chuter les FPS. Compte 15 à 150 Mo par guide.

La recherche est sémantique et multilingue : une question en français trouve le bon passage du manuel anglais (modèle local `paraphrase-multilingual-MiniLM-L12-v2`, ~220 Mo, téléchargé au premier usage), complétée par une recherche par mots-clés pour les termes exacts du cockpit. Tout reste local ; l'index est dans `data/manuals/`.

## Réglages — bouton ⚙ de l'overlay

Au **premier lancement**, un assistant en 4 étapes règle l'essentiel : clé de l'IA (Groq, gratuit, par défaut ; la même clé sert à la reconnaissance vocale en ligne), ton équipier (indicatif, voix, effet radio, longueur des réponses, avec un bouton pour l'écouter), bouton PTT, script DCS. Par défaut, la voix est masculine (Microsoft Henri) avec l'effet radio fort.

Tout le reste se configure dans la fenêtre **Réglages** (bouton ⚙ de l'overlay), qui s'ouvre aussi toute seule quand la clé de l'IA choisie manque. « Enregistrer » applique les changements immédiatement, sans redémarrer.

| Onglet | Contenu |
|---|---|
| **IA** | Fournisseur, adresse, clé API, modèle et modèle de secours (« Lister »), longueur des réponses vocales (1 phrase · 2-3 · jusqu'à 6, toujours 15 mots max par phrase), consignes personnelles ajoutées au prompt, température, mémoire, « Tester l'IA » |
| **Manuels** | Dossier de DCS World (détecté : standalone ou Steam) et dossier de PDF perso ; « Indexer les manuels » (une fois, barre de progression) ; « Tester la recherche » pour voir les passages que l'IA recevra |
| **Voix** | Whisper (modèle, CPU/GPU, langue) ; synthèse vocale : **Microsoft** (voix neuronales naturelles, gratuites, en ligne), **OpenAI** (payant, clé OpenAI), **Piper** (local) ou voix système ; débit, « Tester la voix ». Les voix en ligne retombent sur Piper si le service ne répond pas. |
| **Audio** | Micro, sortie casque, bips |
| **Bouton PTT** | « Assigner… » puis appuie sur la touche ou le bouton HOTAS voulu |
| **DCS & Overlay** | Dossier Saved Games/DCS, installation du script DCS en un clic, port UDP ; opacité (fond, ou tout l'overlay), couleurs (fond, accent, réponses), police, taille, gras, largeur, hauteur ; éléments affichés (en-tête, question, boutons, saisie, barre d'état) ; masquage automatique après un délai sans activité, clics traversants vers DCS, position verrouillée. Clic droit sur l'overlay : réglages, même en-tête masqué |

Fournisseurs d'IA proposés (tous via leur API compatible OpenAI) :

| Fournisseur | Où obtenir la clé | Modèle par défaut |
|---|---|---|
| **Groq** (par défaut, gratuit) | console.groq.com/keys | `openai/gpt-oss-120b` (secours : `qwen/qwen3.8-27b`) |
| Ollama (local) | aucune clé | `qwen2.5:14b` |
| ChatGPT (OpenAI) | platform.openai.com/api-keys | `gpt-4.1-mini` |
| Google Gemini | aistudio.google.com/apikey | `gemini-2.5-flash` |
| Grok (xAI) | console.x.ai | `grok-4-fast-non-reasoning` |
| OpenRouter | openrouter.ai/keys | `openai/gpt-4.1-mini` |
| Autre | Open WebUI, LM Studio… | à renseigner |

Si le service est surchargé (erreur 503), l'appli réessaie deux fois puis passe aux **modèles de secours** (liste séparée par des virgules). **Quota gratuit du jour épuisé** (Gemini gratuit : ~20 requêtes/jour *par modèle*) : pas de nouvel essai inutile, le modèle est sauté jusqu'à son retour et les modèles de secours prennent le relais (chaque modèle Gemini a son propre quota), puis le **fournisseur de secours** (ex. Groq, gratuit).

Les clés de chaque fournisseur sont conservées : changer d'IA ne fait pas perdre les autres. Au lieu de saisir la clé, tu peux aussi définir `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` ou `OPENROUTER_API_KEY`. Pour la voix en vol, préfère les modèles rapides (« mini », « flash », « fast ») : les modèles de raisonnement sont plus justes mais mettent plusieurs secondes à répondre.

Le PTT est lu même quand DCS a le focus : choisis un bouton **non assigné dans DCS**.

L'installation du script DCS copie `DCSAssistant.lua` dans `Saved Games/DCS/Scripts/` et ajoute à `Export.lua` (sauvegardé en `Export.lua.bak`) la ligne :
```lua
pcall(function() local l=require('lfs'); dofile(l.writedir()..[[Scripts\DCSAssistant.lua]]) end)
```
Compatible SRS, Tacview, Helios… (fonctions chaînées). Au démarrage, l'appli propose de l'installer s'il manque et le met à jour tout seul s'il est ancien ; si DCS tourne sans rien envoyer, l'overlay le signale. Sans le script, choisis l'appareil dans le menu de l'overlay.

### Avancé : `config.json` et ligne de commande

Les réglages sont enregistrés dans `config.json` (modifiable à la main ; dict `extra` par fournisseur ajouté tel quel à la requête, ex. `{"reasoning_effort": "low"}`). Commandes utiles pour dépanner :

```bash
python main.py --ask "Comment je démarre l'APU ?" --aircraft "F/A-18C Hornet"   # test sans micro
python main.py --models                              # modèles du fournisseur actif
python main.py --provider grok --model grok-4        # changer d'IA pour ce lancement seulement
python main.py --devices | --bind | --download-voice fr_FR-tom-medium
```

## Limites

- **Fiabilité des réponses** : elle dépend entièrement du modèle. Les petits modèles locaux (7-14B) inventent volontiers des noms d'interrupteurs ou des raccourcis ; un gros modèle cloud est nettement plus fiable sur les procédures DCS. Le prompt demande à l'IA de signaler ses incertitudes, mais vérifie avant de t'y fier.
- **Overlay** invisible en plein écran exclusif et en VR → utilise le mode fenêtré sans bordure, ou fie-toi à la voix.
- **Linux/Wayland** : la touche clavier globale (pynput) ne fonctionne qu'avec X11/XWayland ; préfère un bouton HOTAS.
