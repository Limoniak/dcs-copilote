"""Chargement / sauvegarde de la configuration (config.json)."""
import copy
import json
import os

DEFAULTS = {
    "llm": {
        # Fournisseur actif : une des clés de "providers" (ollama, openai, gemini, grok, openrouter, custom)
        "provider": "ollama",
        # Toutes ces API sont compatibles OpenAI (/chat/completions). api_key vide = variable
        # d'environnement api_key_env. Modèles disponibles : python main.py --models
        "providers": {
            "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama",
                       "model": "qwen2.5:14b"},
            "openai": {"base_url": "https://api.openai.com/v1", "api_key": "",
                       "api_key_env": "OPENAI_API_KEY", "model": "gpt-4.1-mini"},
            "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                       "api_key": "", "api_key_env": "GEMINI_API_KEY", "model": "gemini-2.5-flash"},
            "grok": {"base_url": "https://api.x.ai/v1", "api_key": "",
                     "api_key_env": "XAI_API_KEY", "model": "grok-4-fast-non-reasoning"},
            "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key": "",
                           "api_key_env": "OPENROUTER_API_KEY", "model": "openai/gpt-4.1-mini"},
            "custom": {"base_url": "", "api_key": "", "model": ""},   # Open WebUI, LM Studio...
        },
        "temperature": 0.3,
        "answer_length": "courte",   # réponses vocales : tres_courte / courte / detaillee
        "custom_prompt": "",         # consignes ajoutées au prompt système (réglages → IA)
        "max_history": 6,          # nombre d'échanges question/réponse gardés en contexte
        "timeout": 120,
    },
    "stt": {
        "model": "small",          # tiny / base / small / medium / large-v3 / distil-large-v3
        "device": "cpu",           # "cpu" pour laisser le GPU à DCS, "cuda" si tu as de la marge en VRAM
        "compute_type": "int8",    # int8 (cpu) / float16 (cuda)
        "language": "fr",
    },
    "tts": {
        "enabled": True,
        "engine": "piper",         # "edge" (Microsoft, en ligne), "openai", "piper" (local) ou "system"
        "edge_voice": "fr-FR-HenriNeural",
        "openai_voice": "ash",
        "openai_model": "gpt-4o-mini-tts",
        "openai_key": "",          # vide = clé OpenAI de l'onglet IA, sinon OPENAI_API_KEY
        "piper_model": "voices/fr_FR-siwis-medium.onnx",
        "speed": 1.1,              # >1 = plus rapide
        "speak_typed": False,      # lire aussi à voix haute les questions tapées
    },
    "audio": {
        "input_device": None,      # index ou nom (voir --devices), None = défaut
        "output_device": None,
        "beeps": True,
    },
    "ptt": {
        "type": "keyboard",        # "keyboard" ou "joystick" (utilise --bind pour configurer)
        "key": "scroll_lock",
        "joystick_name": "",
        "joystick_guid": "",
        "button": 0,
    },
    "manuals": {
        "enabled": True,           # appuyer les réponses sur les manuels PDF indexés
        "dcs_dir": "",             # dossier d'installation de DCS World (Mods/aircraft/*/Doc)
        "extra_dir": "",           # dossier de PDF perso (Chuck's Guides…)
        "top_k": 4,
    },
    "dcs": {
        "udp_port": 7779,
        "saved_games": "",         # dossier Saved Games/DCS (installation du script depuis les réglages)
    },
    "overlay": {
        "enabled": True,
        "x": 40,
        "y": 40,
        "width": 560,
        "lines": 12,
        "alpha": 0.88,
        "font_size": 11,
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate(data):
    """Ancien format (base_url/api_key/model directement dans "llm") -> fournisseur "custom"."""
    llm = data.get("llm") or {}
    if "base_url" in llm:
        old = {k: llm.pop(k) for k in ("base_url", "api_key", "model") if k in llm}
        llm.setdefault("providers", {})["custom"] = old
        llm.setdefault("provider", "custom")
    return data


def load(path):
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    return _merge(DEFAULTS, _migrate(data))


def save(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
