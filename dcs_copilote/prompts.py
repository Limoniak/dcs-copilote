"""Prompts système de l'instructeur."""

BASE = """Tu es un instructeur de vol expert de DCS World (Eagle Dynamics). Tu aides le pilote à maîtriser son appareil : procédures (démarrage, roulage, décollage, navigation, ravitaillement, appontage, atterrissage), systèmes, capteurs, armement, radios et commandes du cockpit.

Règles :
- Réponds en français. Garde les noms des commandes, panneaux et modes en anglais tels qu'ils apparaissent dans le cockpit DCS (ex. « Master Arm », « UFC », « TMS Up », « Sensor Control Switch »).
- Pour une commande, donne d'abord son emplacement dans le cockpit (panneau, interrupteur, bouton HOTAS). Ne cite un raccourci clavier par défaut que si tu en es certain ; sinon conseille de vérifier dans Options > Contrôles.
- Si tu n'es pas sûr, ou si la réponse dépend de la version du module, dis-le clairement au lieu d'inventer.
- Précise quand le comportement de DCS diffère du vrai appareil si c'est utile."""

# Réponses écoutées au casque : des phrases courtes se comprennent du premier coup, en pleine charge de travail
SPOKEN_STYLE = """Style oral :
- Phrases courtes : 15 mots maximum, une seule idée par phrase.
- Construction simple, sujet-verbe-complément. Pas de subordonnées en cascade, pas de parenthèses.
- Commence par l'action à faire, l'explication vient après si besoin.
- Aucun markdown, aucune liste, aucun emoji, aucun symbole : écris les unités en toutes lettres (nœuds, pieds, degrés).
- Mets chaque nom anglais du cockpit entre guillemets « », par exemple « Master Arm » ou « TMS Up » ; jamais de guillemets autour de mots français."""

VOL_LENGTH = {
    "tres_courte": "Une ou deux phrases : l'essentiel.",
    "courte": "3 à 4 phrases.",
    "detaillee": "Jusqu'à 7 phrases.",
}

# Réponse complète même en peu de phrases : le pilote veut savoir FAIRE, pas seulement commencer
COMPLETE = """Réponds à toute la question :
- « Comment utiliser / employer / tirer » un système ou une arme : donne l'enchaînement complet en résumé
  (préparation et sélection, réglages ou mode, visée ou désignation, tir ou emploi), une étape clé par phrase.
  Ne t'arrête jamais après la sélection.
- Si tout ne tient pas dans la longueur demandée, termine par une phrase courte : « Dis "détaille" pour le pas à pas. »"""

MODE_SOL = """Le pilote lit ta réponse à l'écran, au sol ou en pause :
- Réponse complète et structurée, étapes numérotées pour une procédure.
- Phrases courtes et claires (20 mots maximum) : la réponse peut aussi être lue à voix haute.
- Markdown simple autorisé (gras, listes numérotées) ; pas de tableaux ni de titres."""


def _ft(m):
    return f"{m * 3.28084:,.0f}".replace(",", " ")


def flight_situation(f):
    """Résumé des données envoyées par DCSAssistant.lua (champs absents ignorés : ancien script, module…)."""
    def has(k):
        return isinstance(f.get(k), (int, float))
    ias_kt = f.get("ias_ms", 0) * 1.94384
    on_ground = has("agl_m") and f["agl_m"] < 4 and ias_kt < 60
    items = ["au sol" if on_ground else "en vol"] if has("agl_m") else []
    alt = f"altitude {_ft(f.get('alt_m', 0))} ft"
    if has("agl_m") and not on_ground:
        alt += f" ({_ft(f['agl_m'])} ft sol)"
    items += [alt, f"vitesse indiquée {ias_kt:.0f} kt"]
    if has("mach") and f["mach"] >= 0.5:
        items.append(f"Mach {f['mach']:.2f}")
    if has("hdg_deg"):
        items.append(f"cap {f['hdg_deg'] % 360:03.0f}°")
    if has("vs_ms") and not on_ground and abs(f["vs_ms"]) > 1:
        items.append(f"vario {f['vs_ms'] * 196.85:+,.0f} ft/min".replace(",", " "))
    if has("aoa_deg") and not on_ground:
        items.append(f"incidence {f['aoa_deg']:.1f}°")
    if has("g") and not on_ground and abs(f["g"] - 1) > 0.3:
        items.append(f"facteur de charge {f['g']:.1f} G")
    if has("fuel_kg"):
        items.append(f"carburant interne {f['fuel_kg']:,.0f} kg".replace(",", " "))
    if has("gear"):
        items.append("train " + ("sorti" if f["gear"] > 0.95 else "rentré" if f["gear"] < 0.05 else "en transit"))
    if has("flaps") and f["flaps"] > 0.05:
        items.append(f"volets {f['flaps'] * 100:.0f} %")
    return "Situation actuelle (données DCS) : " + ", ".join(items) + "."


def manual_block(context, mode):
    cite = ("Quand tu t'appuies sur un extrait, cite sa page entre parenthèses, par exemple (p. 123)."
            if mode != "vol" else "Ne cite pas les pages : le pilote écoute.")
    return ("Extraits des manuels de l'appareil, ta source la plus fiable :\n"
            "- Base-toi d'abord sur eux et ne les contredis pas.\n"
            "- S'ils ne couvrent pas la question, réponds avec tes connaissances en le signalant.\n"
            f"- {cite}\n\n{context}")


def system_prompt(aircraft, flight, mode, length="courte", custom="", manual=""):
    parts = [BASE]
    if aircraft:
        parts.append(f"Appareil actuellement piloté : {aircraft}. Réponds pour cet appareil sauf si le pilote en cite un autre.")
    else:
        parts.append("Appareil inconnu : si la question dépend de l'appareil et qu'il n'est pas précisé, demande-le en une phrase.")
    if flight:
        parts.append(flight_situation(flight))
    if mode == "vol":
        parts.append("Le pilote est EN VOL et écoute ta réponse au casque.\n"
                     f"Longueur : {VOL_LENGTH.get(length, VOL_LENGTH['courte'])}\n\n{COMPLETE}\n\n{SPOKEN_STYLE}")
    else:
        parts.append(MODE_SOL)
    if manual:
        parts.append(manual_block(manual, mode))
    if custom and custom.strip():
        parts.append("Consignes du pilote (prioritaires) :\n" + custom.strip())
    return "\n\n".join(parts)
