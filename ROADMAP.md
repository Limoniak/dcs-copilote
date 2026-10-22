# Feuille de route — DCS Copilote

Pistes d'amélioration, par ordre d'intérêt. Cocher au fur et à mesure.

## 1. Valider en conditions réelles (prioritaire)
Tout a été testé avec des simulations (faux DCS, carte son simulée, IA simulée) ; rien encore en vol.
Session de test à faire sur le PC de jeu, en notant ce qui coince (journal : Réglages → « Ouvrir le journal ») :
- [ ] Bouton PTT sur le HOTAS (Réglages → Bouton PTT → Assigner)
- [ ] Overlay par-dessus DCS (fenêtré sans bordure), déplacement, transparence
- [ ] Script Lua v2 installé : appareil détecté, données de vol (cap, incidence, carburant, train, volets)
- [ ] Manuels officiels indexés + recherche (« Tester la recherche »)
- [ ] Chuck's Guides téléchargés et utilisés
- [ ] Voix Microsoft (Henri / Rémy Multilingual), accents sur les termes anglais
- [ ] Latence ressentie entre la fin de la question et le début de la réponse

## 2. VR et plein écran
L'overlay est invisible en VR et en plein écran exclusif : il ne reste que la voix.
- [ ] Afficher la question, la réponse et l'étape de checklist sur la planchette (kneeboard)
      via OpenKneeboard (mise à jour en direct).

## 3. Mode checklist
- [x] « Lance la procédure de démarrage » → étapes tirées des manuels, une à la fois
- [x] Appui bref sur le PTT (ou « suivant ») = étape suivante ; « répète », « précédent », « stop »
- [x] Overlay : étape courante, progression, boutons Précédent / Répéter / Suivant / Terminer

## 4. Alertes proactives (optionnelles)
- [x] Le copilote parle de lui-même à partir des données de vol : train rentré en approche,
      train ou volets sortis à grande vitesse, taux de chute élevé près du sol, carburant bas,
      facteur de charge élevé
- [x] Réglables (activables une par une), délai minimum entre deux répétitions
- [x] Limites train / volets par appareil (F/A-18C, F-16C, F-15E, F-14, AV-8B, A-10C, Mirage 2000, P-51D)
- [ ] Autres seuils par appareil (incidence d'approche, facteur de charge…), autres modules

## 5. Latence
- [x] Mesurer chaque étape dans le journal (transcription, 1er mot de l'IA, début de la voix)
- [x] Reconnaissance vocale en ligne optionnelle (OpenAI, Groq…) avec repli sur Whisper local
- [x] Réponse rapide en vol : limiter la « réflexion » des modèles qui en ont (Gemini 2.5, GPT-5…)
- [x] Faire parler la voix dès le premier morceau de phrase
- [ ] Garder une connexion ouverte avec la voix Microsoft (gain ~0,5 s par phrase)

## Fait en plus
- [x] Effet radio sur la voix (bande étroite, saturation, souffle, squelch)

## 6. Installeur Windows
- [x] `.exe` sans installation de Python (PyInstaller), fabriqué et publié par GitHub Actions à chaque tag `v*`
- [x] Mise à jour depuis l'appli (vérifier la dernière Release et proposer de la télécharger)

## 7. Tests automatiques
- [x] Tests (pytest) dans le dépôt : client IA (secours), prompts, découpage des phrases, manuels,
      checklist, config, script DCS, interface (molette, poignée, logo, classement des modèles)
- [x] Correspondance Chuck's Guides ↔ modules
- [x] Script Lua (via lupa, en Lua 5.1 comme DCS) : paquet envoyé, exports chaînés (SRS, Tacview), sans LuaSocket
- [x] GitHub Actions à chaque push (`.github/workflows/tests.yml`)

## 8. Petits plus
- [x] Réindexer automatiquement les manuels officiels après une mise à jour de DCS (au lancement, seuls les PDF modifiés)
- [x] Suivi de la consommation : requêtes du jour et quota restant (réglages → IA), alerte quand le quota du jour baisse
- [x] Historique des questions, exportable en notes (Markdown, un fichier par jour)
- [ ] Interface en anglais
