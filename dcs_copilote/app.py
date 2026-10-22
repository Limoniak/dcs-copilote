"""Orchestration : PTT → micro → Whisper → LLM (streaming) → overlay + voix."""
import copy
import queue
import threading
import time
from collections import deque

from . import aircraft as ac
from . import config as cfgmod
from .llm import LLMClient, LLMError, needs_key
from .prompts import system_prompt
from .textutil import SentenceSplitter

MIN_AUDIO_S = 0.35
DCS_SCRIPT_VERSION = 2          # champ « v » envoyé par dcs/DCSAssistant.lua


class App:
    def __init__(self, cfg, cfg_path, first_run=False):
        from .audio import BEEP_ERR, BEEP_START, BEEP_STOP, Recorder, Speaker, make_tts
        from .dcs_link import DCSLink
        from .overlay import Overlay
        from .ptt import make_ptt

        self._Recorder, self._make_tts, self._DCSLink, self._make_ptt = Recorder, make_tts, DCSLink, make_ptt
        self.cfg, self.cfg_path = cfg, cfg_path
        self.beeps = (BEEP_START, BEEP_STOP, BEEP_ERR)
        self.gen = 0
        self.gen_lock = threading.Lock()
        self.history = deque(maxlen=2 * int(cfg["llm"]["max_history"]))
        self.manual_aircraft = None
        self.dcs_aircraft = None
        self.stt = None
        self.recording = False
        self.jobs = queue.Queue()
        self.settings_win = None

        self.overlay = Overlay(cfg["overlay"], ac.CHOICES, self.ask_typed, self.reset,
                               self.set_manual_aircraft, self.open_settings, self.quit,
                               self.set_speak_typed, cfg["tts"].get("speak_typed", False))
        ov = self.overlay
        ov.on_mic_press, ov.on_mic_release = self.on_press, self.on_release
        ov.on_repeat, ov.on_more = self.repeat, self.more
        self.last_answer = ""
        self.warned_old_script = False
        if not cfg["overlay"].get("enabled", True):
            self.overlay.hide_window()

        self.llm = self._make_llm()
        self.recorder = None
        self._open_recorder()
        self.speaker = Speaker(make_tts(cfg["tts"], self.log, cfg["llm"]), cfg["audio"]["output_device"],
                               lambda: self.gen, self.log)
        self.speaker.start()

        self.dcs = DCSLink(int(cfg["dcs"]["udp_port"]), self.on_dcs, self.log)
        self.dcs.start()

        self.ptt = None
        self.start_ptt()

        threading.Thread(target=self._load_stt, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()

        if first_run or needs_key(cfg["llm"]):
            self.overlay.later(500, self.open_settings)

    # ------------------------------------------------------------------ réglages
    def _make_llm(self):
        try:
            return LLMClient(self.cfg["llm"])
        except LLMError as e:
            self.log(f"IA : {e}", error=True)
            return None

    def _open_recorder(self):
        try:
            rec = self._Recorder(self.cfg["audio"]["input_device"])
        except Exception as e:  # noqa: BLE001
            self.log(f"Micro indisponible : {e}", error=True)
            return
        if self.recorder is not None:
            self.recorder.close()
        self.recorder = rec

    def start_ptt(self):
        try:
            self.ptt = self._make_ptt(self.cfg["ptt"], self.on_press, self.on_release, self.log)
            self.ptt.start()
        except Exception as e:  # noqa: BLE001
            self.ptt = None
            self.log(f"Bouton PTT indisponible : {e}", error=True)

    def stop_ptt(self):
        if self.ptt is not None:
            self.ptt.stop()
            self.ptt = None
        if self.recording:
            self.recording = False
            self.recorder.stop()

    def set_speak_typed(self, on):
        """Bouton haut-parleur de l'overlay : lecture des réponses aux questions tapées."""
        self.cfg["tts"]["speak_typed"] = on
        try:
            disk = cfgmod.load(self.cfg_path)
            disk["tts"]["speak_typed"] = on
            cfgmod.save(self.cfg_path, disk)
        except (OSError, ValueError) as e:
            self.log(f"Réglage non enregistré : {e}", error=True)
        if not on:
            self.speaker.stop()
        self.overlay.set_status("Réponses aux questions tapées lues à voix haute" if on else
                                "Réponses aux questions tapées non lues", state="ready")

    def open_settings(self):
        from .settings import SettingsWindow
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.lift()
            return
        self.settings_win = SettingsWindow(self)

    def apply_settings(self, new):
        """Enregistre la nouvelle config et l'applique sans redémarrer."""
        old, self.cfg = self.cfg, copy.deepcopy(new)
        x, y = self.overlay.position()
        self.cfg["overlay"]["x"], self.cfg["overlay"]["y"] = x, y
        cfgmod.save(self.cfg_path, self.cfg)
        cfg = self.cfg

        if cfg["llm"] != old["llm"]:
            self.llm = self._make_llm()
            self.history = deque(self.history, maxlen=2 * int(cfg["llm"]["max_history"]))
        if cfg["tts"].get("speak_typed") != old["tts"].get("speak_typed"):
            self.overlay.set_speak(cfg["tts"].get("speak_typed", False))
        if cfg["overlay"] != old["overlay"]:
            self.overlay.apply_style(cfg["overlay"])
        if cfg["audio"]["input_device"] != old["audio"]["input_device"]:
            self._open_recorder()
        if cfg["audio"]["output_device"] != old["audio"]["output_device"]:
            from .audio import _dev
            self.speaker.device = _dev(cfg["audio"]["output_device"])
        if cfg["ptt"] != old["ptt"] or self.ptt is None:
            self.stop_ptt()
            self.start_ptt()
        threading.Thread(target=self._apply_slow, args=(old,), daemon=True).start()
        self.log("Réglages enregistrés")

    def _apply_slow(self, old):
        """Ce qui prend du temps (modèles, port UDP) se fait hors de l'interface."""
        cfg = self.cfg
        if cfg["tts"] != old["tts"] or (cfg["tts"].get("engine") == "openai" and cfg["llm"] != old["llm"]):
            self.speaker.stop()
            self.speaker.tts = self._make_tts(cfg["tts"], self.log, cfg["llm"])
        if cfg["dcs"]["udp_port"] != old["dcs"]["udp_port"]:
            self.dcs.stop()
            self.dcs.join(timeout=3)
            self.dcs = self._DCSLink(int(cfg["dcs"]["udp_port"]), self.on_dcs, self.log)
            self.dcs.start()
        if cfg["stt"] != old["stt"]:
            self._load_stt()
        elif self.stt is not None:
            self._ready()

    # ------------------------------------------------------------------ utilitaires
    def log(self, msg, error=False):
        print(msg, flush=True)
        self.overlay.set_status(msg, error)

    def _beep(self, i):
        if self.cfg["audio"].get("beeps", True):
            try:
                self.speaker.play(self.beeps[i])
            except Exception:  # noqa: BLE001
                pass

    def _new_gen(self):
        with self.gen_lock:
            self.gen += 1
            return self.gen

    def current_aircraft(self):
        return ac.pretty(self.dcs_aircraft) if self.dcs_aircraft else self.manual_aircraft

    def _ready_status(self):
        if self.ptt is None:
            return "Prêt — aucun bouton PTT (⚙ Réglages)"
        return f"Prêt — maintiens {self.ptt.describe()} pour parler"

    def _ready(self):
        msg = self._ready_status()
        print(msg, flush=True)
        self.overlay.set_status(msg, state="ready")

    def _load_stt(self):
        from .stt import STT
        print(f"Chargement de Whisper « {self.cfg['stt']['model']} »…", flush=True)
        self.overlay.set_status(f"Chargement de Whisper « {self.cfg['stt']['model']} »…", state="busy")
        try:
            self.stt = STT(self.cfg["stt"])   # l'ancien modèle reste utilisable pendant le chargement
            self._ready()
        except Exception as e:  # noqa: BLE001
            self.log(f"Whisper indisponible : {e}", error=True)

    # ------------------------------------------------------------------ événements
    def on_dcs(self, state):
        if state and int(state.get("v", 1)) < DCS_SCRIPT_VERSION and not self.warned_old_script:
            self.warned_old_script = True
            self.overlay.info("Script DCS ancien : ⚙ Réglages → DCS → « Installer » pour donner à l'instructeur "
                              "cap, incidence, carburant, train, volets…")
        name = state.get("aircraft") if state else None
        if name == self.dcs_aircraft:
            return
        self.dcs_aircraft = name
        if name:
            self.history.clear()
            self.overlay.set_aircraft(ac.pretty(name), locked=True)
            self.log(f"DCS : {ac.pretty(name)}")
        else:
            self.overlay.set_aircraft(self.manual_aircraft, locked=False)
            self.log("DCS déconnecté")

    def set_manual_aircraft(self, name):
        name = (name or "").strip()
        if name and name != self.manual_aircraft:
            self.manual_aircraft = name
            self.history.clear()
            self.overlay.info(f"Appareil : {name}")

    def reset(self):
        self._new_gen()
        self.speaker.stop()
        self.history.clear()
        self.overlay.start_answer("Nouvelle conversation")

    def quit(self):
        try:
            # relit le fichier : les surcharges --provider/--model ne sont pas enregistrées
            disk = cfgmod.load(self.cfg_path)
            disk["overlay"]["x"], disk["overlay"]["y"] = self.overlay.position()
            cfgmod.save(self.cfg_path, disk)
        except Exception:  # noqa: BLE001
            pass
        self.overlay.close()

    def on_press(self):
        if self.recorder is None:
            self.log("Pas de micro (⚙ Réglages → Audio)", error=True)
            return
        self._new_gen()            # annule la réponse en cours
        self.speaker.stop()
        self._beep(0)
        self.recording = True
        self.recorder.start()
        self.overlay.set_status("Écoute…", state="listen")

    def on_release(self):
        if not self.recording:
            return
        self.recording = False
        audio = self.recorder.stop()
        if len(audio) < MIN_AUDIO_S * 16000:
            self._ready()
            return
        self._beep(1)
        self.jobs.put(("voice", audio, self.gen))

    def repeat(self):
        """Relit la dernière réponse (bouton « Répéter »)."""
        if not self.last_answer:
            return
        gen = self._new_gen()
        self.speaker.stop()
        splitter = SentenceSplitter()
        for s in splitter.feed(self.last_answer) + splitter.flush():
            self.speaker.say(s, gen)

    def more(self):
        """Bouton « Détailler » : réponse complète à la question précédente (mode sol)."""
        if self.last_answer:
            self.ask_typed("Détaille ta réponse précédente : toutes les étapes et les explications utiles.")

    def ask_typed(self, question):
        gen = self._new_gen()
        self.speaker.stop()
        self.jobs.put(("text", question, gen))

    # ------------------------------------------------------------------ traitement
    def _worker(self):
        while True:
            kind, payload, gen = self.jobs.get()
            if gen != self.gen:
                continue
            try:
                if kind == "voice":
                    if self.stt is None:
                        self.log("Whisper pas encore prêt…", error=True)
                        continue
                    self.overlay.set_status("Transcription…", state="busy")
                    t0 = time.monotonic()
                    question = self.stt.transcribe(payload, self.current_aircraft())
                    if gen != self.gen:
                        continue
                    if not question:
                        self._beep(2)
                        self.overlay.set_status("Je n'ai rien compris, réessaie", state="ready")
                        continue
                    print(f"[STT {time.monotonic() - t0:.1f}s] {question}", flush=True)
                    self._answer(question, "vol", True, gen)
                else:
                    self._answer(payload, "sol", self.cfg["tts"].get("speak_typed", False), gen)
            except Exception as e:  # noqa: BLE001
                self.log(f"Erreur : {e}", error=True)

    def _answer(self, question, mode, speak, gen):
        flight = self.dcs.snapshot()
        messages = [{"role": "system", "content": system_prompt(
            self.current_aircraft(), flight, mode, self.cfg["llm"].get("answer_length", "courte"),
            self.cfg["llm"].get("custom_prompt", ""))}]
        messages += list(self.history)
        messages.append({"role": "user", "content": question})

        self.overlay.start_answer(question)
        self.overlay.set_status("Réflexion…", state="busy")
        splitter = SentenceSplitter()
        full, cancelled = [], False
        try:
            if self.llm is None:
                raise LLMError("IA non configurée : ⚙ Réglages → IA")
            for delta in self.llm.stream(messages, alive=lambda: gen == self.gen,
                                         on_retry=lambda m: self.overlay.set_status(m, state="busy")):
                if gen != self.gen:
                    cancelled = True
                    break
                full.append(delta)
                self.overlay.append(delta)
                if speak:
                    for s in splitter.feed(delta):
                        self.speaker.say(s, gen)
        except LLMError as e:
            self._beep(2)
            self.overlay.append(f"\n{e}\n", "err")
            self.log("Erreur IA", error=True)
            return
        if cancelled:
            return
        if speak:
            for s in splitter.flush():
                self.speaker.say(s, gen)
        answer = "".join(full).strip()
        if answer:
            self.history.append({"role": "user", "content": question})
            self.history.append({"role": "assistant", "content": answer})
            self.last_answer = answer
            self.overlay.show_actions(True)
        self._ready()

    def run(self):
        self.overlay.mainloop()
