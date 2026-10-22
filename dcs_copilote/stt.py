"""Transcription : locale (faster-whisper) ou en ligne (API compatible OpenAI /audio/transcriptions)."""
import io
import os
import wave

import numpy as np

# Vocabulaire aviation : aide Whisper à orthographier correctement les termes
PROMPT = ("Question à un instructeur DCS World : HUD, MFD, DDI, UFC, HOTAS, TMS, DMS, CMS, Master Arm, "
          "TACAN, ILS, ICLS, INS, GPS, waypoint, AMRAAM, Sidewinder, Maverick, JDAM, TGP, FLIR, radar, "
          "IFF, datalink, APU, throttle, trim, flaps, crosswind, ravitaillement, appontage.")


class STT:
    def __init__(self, cfg):
        from faster_whisper import WhisperModel
        self.language = cfg.get("language") or None
        self.model = WhisperModel(cfg["model"], device=cfg.get("device", "cpu"),
                                  compute_type=cfg.get("compute_type", "int8"))

    def transcribe(self, audio: np.ndarray, aircraft=None) -> str:
        # le nom de l'appareil aide Whisper à orthographier ses termes (Hornet, Viper, Apache…)
        prompt = PROMPT + (f" Appareil : {aircraft}." if aircraft else "")
        segments, _ = self.model.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=True,
            initial_prompt=prompt, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


STT_PROVIDERS = {
    # Groq : Whisper large très rapide (~0,3 s), offre gratuite ; OpenAI : payant, très bon
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo",
             "api_key_env": "GROQ_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini-transcribe",
               "api_key_env": "OPENAI_API_KEY"},
}


class ApiSTT:
    """Transcription en ligne : bien plus rapide que Whisper sur processeur."""

    def __init__(self, cfg, llm_cfg=None):
        prov = cfg.get("api_provider", "groq")
        preset = STT_PROVIDERS.get(prov, {})
        self.base = (cfg.get("api_base") or preset.get("base_url", "")).rstrip("/")
        self.model = cfg.get("api_model") or preset.get("model", "")
        key = cfg.get("api_key") or ""
        if not key and prov in ("groq", "openai"):   # même clé que l'onglet IA
            key = (((llm_cfg or {}).get("providers") or {}).get(prov) or {}).get("api_key", "")
        self.key = key or os.environ.get(preset.get("api_key_env", ""), "")
        self.language = cfg.get("language") or None
        if not (self.base and self.model and self.key):
            raise ValueError("reconnaissance en ligne : adresse, modèle et clé requis (⚙ Réglages → Voix)")

    def transcribe(self, audio: np.ndarray, aircraft=None) -> str:
        import requests
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        data = {"model": self.model, "prompt": PROMPT + (f" Appareil : {aircraft}." if aircraft else "")}
        if self.language:
            data["language"] = self.language
        r = requests.post(f"{self.base}/audio/transcriptions", data=data, timeout=15,
                          headers={"Authorization": f"Bearer {self.key}"},
                          files={"file": ("question.wav", buf.getvalue(), "audio/wav")})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} : {r.text[:200]}")
        return (r.json().get("text") or "").strip()
