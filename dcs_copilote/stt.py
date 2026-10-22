"""Transcription locale avec faster-whisper."""
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
