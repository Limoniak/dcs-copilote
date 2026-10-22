"""Orchestration : PTT → micro → Whisper → LLM (streaming) → overlay + voix."""
import copy
import queue
import threading
import time
from collections import deque

from . import aircraft as ac
from . import checklist as cl
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
        ov.on_checklist = self.checklist_cmd
        self.checklist = None
        self.last_answer = ""
        self.warned_old_script = False
        if not cfg["overlay"].get("enabled", True):
            self.overlay.hide_window()

        self.llm = self._make_llm()
        self.recorder = None
        self._open_recorder()
        self.speaker = Speaker(make_tts(cfg["tts"], self.log, cfg["llm"]), cfg["audio"]["output_device"],
                               lambda: self.gen, self.log)
        self.speaker.on_start = self._on_voice_start
        self.speaker.radio = int(cfg["tts"].get("radio", 0))
        self.speaker.start()
        self.lat = {}                          # mesures de latence de la question en cours
        self.stt_api = None

        self._make_stt_api()
        from .alerts import Alerts
        self.alerts = Alerts(lambda: self.cfg.get("alerts", {}), self._announce_alert)
        self.dcs = DCSLink(int(cfg["dcs"]["udp_port"]), self.on_dcs, self.log, self.alerts.feed)
        self.dcs.start()

        self.ptt = None
        self.start_ptt()

        from .manuals import Manuals
        self.manuals = Manuals(self.log)
        threading.Thread(target=self.load_manuals, daemon=True).start()
        self._chucks_lock = threading.Lock()
        threading.Thread(target=self._chucks_loop, daemon=True).start()
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
        self.speaker.radio = int(cfg["tts"].get("radio", 0))
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
        voice_keys = [k for k in cfg["tts"] if k not in ("radio", "speak_typed")]
        if any(cfg["tts"].get(k) != old["tts"].get(k) for k in voice_keys) or \
                (cfg["tts"].get("engine") == "openai" and cfg["llm"] != old["llm"]):
            self.speaker.stop()
            self.speaker.tts = self._make_tts(cfg["tts"], self.log, cfg["llm"])
        if cfg["dcs"]["udp_port"] != old["dcs"]["udp_port"]:
            self.dcs.stop()
            self.dcs.join(timeout=3)
            self.dcs = self._DCSLink(int(cfg["dcs"]["udp_port"]), self.on_dcs, self.log, self.alerts.feed)
            self.dcs.start()
        if cfg["manuals"].get("enabled") and not old["manuals"].get("enabled"):
            self.load_manuals()
        local = ("model", "device", "compute_type", "language")
        if cfg["stt"] != old["stt"] or cfg["llm"] != old["llm"]:
            self._make_stt_api()
        if any(cfg["stt"].get(k) != old["stt"].get(k) for k in local):
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
        if self.checklist is not None:
            return "Checklist — appui bref sur le PTT ou « suivant » : étape suivante"
        if self.ptt is None:
            return "Prêt — aucun bouton PTT (⚙ Réglages)"
        return f"Prêt — maintiens {self.ptt.describe()} pour parler"

    def _ready(self):
        msg = self._ready_status()
        print(msg, flush=True)
        self.overlay.set_status(msg, state="ready")

    def load_manuals(self):
        if not self.cfg["manuals"].get("enabled", True):
            return
        try:
            if self.manuals.load():
                s = self.manuals.summary()
                print(f"Manuels : {s['docs']} documents, {s['chunks']} extraits", flush=True)
        except Exception as e:  # noqa: BLE001
            self.log(f"Manuels indisponibles : {e}", error=True)

    # ------------------------------------------------------------------ Chuck's Guides
    def chucks_selection(self, cfg=None):
        from . import chucks
        m = (cfg or self.cfg)["manuals"]
        ch = m.get("chucks", {})
        slugs = list(ch.get("aircraft", []))
        if ch.get("follow_installed", True) and m.get("dcs_dir"):
            guides = chucks.load_state()["guides"] or chucks.list_guides()
            slugs += [s for s in chucks.installed_slugs(guides, m["dcs_dir"]) if s not in slugs]
        return slugs

    def _chucks_loop(self):
        """Vérifie au démarrage puis toutes les 6 h (chaque guide au plus une fois par semaine)."""
        from . import chucks
        time.sleep(20)                         # laisser l'appli démarrer tranquillement
        while True:
            try:
                if self.cfg["manuals"].get("chucks", {}).get("enabled") and chucks.due(chucks.load_state()):
                    self.update_chucks(background=True)
            except Exception as e:  # noqa: BLE001
                print(f"[chucks] {e}", flush=True)
            time.sleep(6 * 3600)

    def update_chucks(self, progress=lambda msg, frac=None: None, background=False, cfg=None):
        """Télécharge les guides nouveaux ou modifiés puis réindexe. En arrière-plan, l'analyse
        (gourmande en CPU) attend que le pilote ne soit plus en vol pour ne pas faire chuter les FPS."""
        from . import chucks
        from .manuals import build_index, find_pdfs
        if not self._chucks_lock.acquire(blocking=False):
            raise RuntimeError("mise à jour déjà en cours")
        try:
            cfg = cfg or self.cfg
            updated, errors = chucks.update(self.chucks_selection(cfg), progress)
            for e in errors:
                print(f"[chucks] {e}", flush=True)
            if updated or not background:
                while background and self.dcs.snapshot() is not None:
                    time.sleep(30)
                m = cfg["manuals"]
                build_index(find_pdfs(m.get("dcs_dir"), m.get("extra_dir")),
                            lambda d, n, msg: progress(msg, d / max(1, n)), threads=2 if background else None)
                self.load_manuals()
            if updated and background:
                self.overlay.info("Chuck's Guides mis à jour : " + ", ".join(updated))
            return updated, errors
        finally:
            self._chucks_lock.release()

    def _manual_context(self, question):
        """Passages des manuels pour la question (+ la précédente si c'est une relance courte)."""
        from .manuals import format_context, sources_line
        if not (self.cfg["manuals"].get("enabled", True) and self.manuals.ready):
            return "", ""
        query = question
        if len(question.split()) < 6:
            prev = [m["content"] for m in self.history if m["role"] == "user"]
            if prev:
                query = prev[-1] + " " + question
        try:
            res = self.manuals.search(query, self.current_aircraft(), int(self.cfg["manuals"].get("top_k", 4)))
        except Exception as e:  # noqa: BLE001
            print(f"[manuels] recherche impossible : {e}", flush=True)
            return "", ""
        return format_context(res), sources_line(res)

    def _make_stt_api(self):
        from .stt import ApiSTT
        self.stt_api = None
        if self.cfg["stt"].get("engine") == "api":
            try:
                self.stt_api = ApiSTT(self.cfg["stt"], self.cfg["llm"])
            except Exception as e:  # noqa: BLE001
                self.log(f"Reconnaissance en ligne indisponible ({e}) → Whisper local", error=True)

    def _transcribe(self, audio):
        """En ligne si configuré (rapide), sinon / en cas d'échec Whisper local."""
        if self.stt_api is not None:
            try:
                return self.stt_api.transcribe(audio, self.current_aircraft()), "en ligne"
            except Exception as e:  # noqa: BLE001
                print(f"[STT en ligne] échec ({e}) → Whisper local", flush=True)
        if self.stt is None:
            raise RuntimeError("Whisper pas encore prêt…")
        return self.stt.transcribe(audio, self.current_aircraft()), "local"

    def _on_voice_start(self, gen):
        lat = self.lat
        if gen is not None and lat.get("gen") == gen and "t0" in lat and lat.get("voice") is None:
            lat["voice"] = time.monotonic() - lat["t0"]
            self._log_latency()

    def _log_latency(self):
        lat = self.lat
        parts = []
        if lat.get("stt") is not None:
            parts.append(f"transcription {lat['stt']:.1f} s ({lat.get('stt_engine')})")
        if lat.get("first") is not None:
            parts.append(f"1er mot de l'IA {lat['first']:.1f} s")
        if lat.get("voice") is not None:
            parts.append(f"début de la voix {lat['voice']:.1f} s")
        print("[latence, depuis la fin de la question] " + " · ".join(parts), flush=True)

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
        self.checklist = None
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
            if self.checklist is not None:
                self.checklist_cmd("next")     # appui bref = étape suivante
            else:
                self._ready()
            return
        self._beep(1)
        self.jobs.put(("voice", (audio, time.monotonic()), self.gen))

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
            gen = self._new_gen()
            self.speaker.stop()
            self.jobs.put(("detail", "Détaille ta réponse précédente : toutes les étapes et les explications utiles.",
                           gen))

    def ask_typed(self, question):
        print(f"[question tapée] {question}", flush=True)
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
                    audio, t0 = payload
                    self.overlay.set_status("Transcription…", state="busy")
                    question, engine = self._transcribe(audio)
                    if gen != self.gen:
                        continue
                    self.lat = {"gen": gen, "t0": t0, "stt": time.monotonic() - t0, "stt_engine": engine}
                    if not question:
                        self._beep(2)
                        self.overlay.set_status("Je n'ai rien compris, réessaie", state="ready")
                        continue
                    print(f"[question] {question}", flush=True)
                    self._route(question, "vol", True, gen)
                else:
                    self.lat = {"gen": gen, "t0": time.monotonic()}
                    speak = self.cfg["tts"].get("speak_typed", False)
                    if kind == "detail":
                        self._answer(payload, "sol", speak, gen)
                    else:
                        # lue à voix haute : courte comme une question vocale (« Détailler » pour la suite)
                        self._route(payload, "vol" if speak else "sol", speak, gen)
            except Exception as e:  # noqa: BLE001
                self.log(f"Erreur : {e}", error=True)

    def _on_retry(self, msg):
        print(f"[IA] {msg}", flush=True)
        self.overlay.set_status(msg, state="busy")

    def _route(self, question, mode, speak, gen):
        """Commande de checklist, démarrage d'une checklist, ou question ordinaire."""
        cmd = cl.command(question) if self.checklist is not None else None
        if cmd:
            self.checklist_cmd(cmd)
        elif cl.is_start(question):
            self._start_checklist(question, gen)
        else:
            self._answer(question, mode, speak, gen)

    # ------------------------------------------------------------------ checklist
    def _start_checklist(self, question, gen):
        import re
        self.overlay.start_answer(question)
        self.overlay.set_status("Préparation de la checklist…", state="busy")
        manual, sources = self._manual_context(question)
        system = system_prompt(self.current_aircraft(), self.dcs.snapshot(), "sol",
                               custom=self.cfg["llm"].get("custom_prompt", ""), manual=manual)
        messages = [{"role": "system", "content": system + "\n\n" + cl.PROMPT},
                    {"role": "user", "content": question}]
        try:
            if self.llm is None:
                raise LLMError("IA non configurée : ⚙ Réglages → IA")
            text = "".join(self.llm.stream(messages, alive=lambda: gen == self.gen, fast=True,
                                           on_retry=self._on_retry))
        except LLMError as e:
            self._beep(2)
            self.overlay.append(f"\n{e}\n", "err")
            self.log("Erreur IA", error=True)
            return
        if gen != self.gen:
            return
        steps = cl.parse_steps(text)
        if len(steps) < 2:
            self.overlay.append("Je ne trouve pas cette procédure pour cet appareil. Précise-la, "
                                "par exemple « checklist démarrage ».", "err")
            self._ready()
            return
        title = re.sub(r"^(lance|démarre|commence|déroule|fais)(-moi|-nous)?\s+(la |une |le )?", "",
                       question.strip().rstrip("?.!"), flags=re.I)
        self.checklist = cl.Checklist(title[:1].upper() + title[1:], steps)
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1))})
        print(f"[checklist] {title} : {len(steps)} étapes", flush=True)
        self._show_step(first=True)
        if sources:
            self.overlay.info(sources)

    def _show_step(self, first=False):
        c = self.checklist
        self.overlay.show_checklist(c.title, c.steps, c.i)
        gen = self._new_gen()
        self.speaker.stop()
        self.speaker.say(c.spoken(first), gen)
        self._ready()

    def checklist_cmd(self, cmd):
        """Suivant / précédent / répéter / stop (boutons, voix ou appui bref sur le PTT)."""
        c = self.checklist
        if c is None:
            return
        if cmd == "stop":
            self.checklist = None
            self.overlay.hide_checklist()
            self.overlay.info("Checklist arrêtée.")
            self._ready()
            return
        if cmd == "next":
            c.i += 1
        elif cmd == "prev":
            c.i = max(0, c.i - 1)
        if c.done:
            self.checklist = None
            gen = self._new_gen()
            self.speaker.stop()
            self.speaker.say(f"Checklist {c.title} terminée.", gen)
            self.overlay.show_checklist(c.title, c.steps, len(c.steps))
            self.overlay.hide_checklist()
            self.overlay.info(f"✓ Checklist terminée ({len(c.steps)} étapes).")
            self._ready()
            return
        self._show_step()

    # ------------------------------------------------------------------ alertes
    def _announce_alert(self, msg):
        print(f"[alerte] {msg}", flush=True)
        self.overlay.append(msg, "err")
        self.overlay.set_status(msg, error=True)
        self.speaker.alert(msg)

    def _answer(self, question, mode, speak, gen):
        flight = self.dcs.snapshot()
        manual, sources = self._manual_context(question)
        messages = [{"role": "system", "content": system_prompt(
            self.current_aircraft(), flight, mode, self.cfg["llm"].get("answer_length", "courte"),
            self.cfg["llm"].get("custom_prompt", ""), manual)}]
        messages += list(self.history)
        messages.append({"role": "user", "content": question})

        self.overlay.start_answer(question)
        self.overlay.set_status("Réflexion…", state="busy")
        splitter = SentenceSplitter()
        full, cancelled = [], False
        try:
            if self.llm is None:
                raise LLMError("IA non configurée : ⚙ Réglages → IA")
            fast = self.cfg["llm"].get("fast", True)
            for delta in self.llm.stream(messages, alive=lambda: gen == self.gen, fast=fast,
                                         on_retry=self._on_retry):
                if gen != self.gen:
                    cancelled = True
                    break
                if self.lat.get("gen") == gen and self.lat.get("first") is None:
                    self.lat["first"] = time.monotonic() - self.lat["t0"]
                full.append(delta)
                self.overlay.append(delta)
                if speak:
                    for s in splitter.feed(delta):
                        self.speaker.say(s, gen)
        except LLMError as e:
            print(f"[IA] erreur : {e}", flush=True)
            self._beep(2)
            self.overlay.append(f"\n{e}\n", "err")
            self.log("Erreur IA", error=True)
            return
        if cancelled:
            return
        if not speak and self.lat.get("gen") == gen:
            self._log_latency()
        if speak:
            for s in splitter.flush():
                self.speaker.say(s, gen)
        answer = "".join(full).strip()
        if answer:
            self.history.append({"role": "user", "content": question})
            self.history.append({"role": "assistant", "content": answer})
            self.last_answer = answer
            self.overlay.show_actions(True)
            if sources:
                self.overlay.info(sources)
        self._ready()

    def run(self):
        self.overlay.mainloop()
