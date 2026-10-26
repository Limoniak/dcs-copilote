"""Orchestration : PTT → micro → Whisper → LLM (streaming) → overlay + voix."""
import copy
import os
import queue
import re
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
REPO_URL = "https://github.com/Limoniak/dcs-copilote"
HISTORY_DIR = os.path.join("data", "historique")


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
        self.turns = 0                         # réponses données (pour doser l'emploi de l'indicatif)
        self.quota_warned = set()              # seuils de quota déjà annoncés (50, 10 requêtes)
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
        self.overlay.on_resized = self._overlay_resized
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
        self._chucks_lock = threading.Lock()
        self._fetching = set()
        threading.Thread(target=self._startup_manuals, daemon=True).start()
        threading.Thread(target=self._chucks_loop, daemon=True).start()
        if self.stt_api is None:               # en ligne : Whisper local chargé seulement s'il doit servir
            threading.Thread(target=self._load_stt, daemon=True).start()
        else:
            self._ready()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._dcs_watch, args=(first_run,), daemon=True).start()
        threading.Thread(target=self._check_update, daemon=True).start()

        if first_run:
            self.overlay.later(500, self.open_wizard)
        elif needs_key(cfg["llm"]):
            self.overlay.later(500, self.open_settings)

    # ------------------------------------------------------------------ réglages
    def _make_llm(self):
        try:
            return LLMClient(self.cfg["llm"])
        except LLMError as e:
            self.log(f"IA : {e}", error=True)
            return None

    def _persist_replacements(self):
        """Modèle retiré par le fournisseur et remplacé (conseil du fournisseur) : on met à jour les
        réglages pour ne pas le redécouvrir à chaque lancement."""
        from . import llm as llm_mod
        prov = self.cfg["llm"]["provider"]
        pcfg = self.cfg["llm"]["providers"].get(prov, {})
        changes = {}
        model = pcfg.get("model", "")
        if (prov, model) in llm_mod._replaced:
            changes["model"] = llm_mod._replaced[(prov, model)]
        fb = [m.strip() for m in str(pcfg.get("fallback_model") or "").split(",") if m.strip()]
        new_fb = [llm_mod._replaced.get((prov, m), m) for m in fb]
        new_fb = [m for i, m in enumerate(new_fb) if m not in new_fb[:i] and m != changes.get("model", model)]
        if new_fb != fb:
            changes["fallback_model"] = ", ".join(new_fb)
        if not changes:
            return
        pcfg.update(changes)
        try:
            cfgmod.update(self.cfg_path, lambda d: d["llm"]["providers"].setdefault(prov, {}).update(changes))
        except (OSError, ValueError) as e:
            print(f"[IA] réglage non enregistré : {e}", flush=True)
            return
        self.llm = self._make_llm()
        if "model" in changes:
            msg = f"Modèle {model} retiré par {self.llm.name if self.llm else prov} : remplacé par {changes['model']} dans tes réglages."
            print(f"[IA] {msg}", flush=True)
            self.overlay.info(msg)

    def _stream(self, messages, gen, fast):
        """Réponse de l'IA ; si le fournisseur est à court de quota ou en panne, le fournisseur de
        secours (Réglages → IA) prend le relais pour cette question."""
        if self.llm is None:
            raise LLMError("IA non configurée : ⚙ Réglages → IA")
        alive = lambda: gen == self.gen  # noqa: E731
        try:
            yield from self.llm.stream(messages, alive=alive, fast=fast, on_retry=self._on_retry)
            self._persist_replacements()
            return
        except LLMError as e:
            backup = self.cfg["llm"].get("fallback_provider")
            if e.kind not in ("quota", "overload", "model", "timeout") or not backup or backup == self.cfg["llm"]["provider"]:
                raise
            first = e
        try:
            client = LLMClient(dict(self.cfg["llm"], provider=backup))
        except LLMError:
            raise first from None
        self._on_retry(f"{self.llm.name} indisponible → secours {client.name}")
        yield from client.stream(messages, alive=alive, fast=fast, on_retry=self._on_retry)

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
            cfgmod.update(self.cfg_path, lambda d: d["tts"].update(speak_typed=on))
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

    def open_wizard(self):
        """Premier lancement : 3 étapes (IA, bouton PTT, script DCS) au lieu de tous les réglages."""
        from .wizard import Wizard
        self.settings_win = Wizard(self)

    def _overlay_resized(self, values):
        """Taille changée avec la poignée de l'overlay : retenue pour les prochains lancements."""
        for k, v in values.items():
            self.cfg["overlay"][k] = v
            self._save_setting(("overlay", k), v)

    def apply_settings(self, new):
        """Enregistre la nouvelle config et l'applique sans redémarrer."""
        old, self.cfg = self.cfg, copy.deepcopy(new)
        # trouvés en arrière-plan pendant que la fenêtre était ouverte : un champ resté vide ne les efface pas
        for sec, key in (("manuals", "dcs_dir"), ("dcs", "saved_games")):
            if not self.cfg[sec].get(key) and old[sec].get(key):
                self.cfg[sec][key] = old[sec][key]
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
            self.stt = None                    # rechargé au besoin, avec les nouveaux réglages
        if self.stt_api is None and self.stt is None:
            self._load_stt()
        else:
            self._ready()

    # ------------------------------------------------------------------ script DCS, mises à jour
    def _ask(self, text, on_yes, on_no=None, yes="Oui", no="Non"):
        """Question oui/non posée depuis n'importe quel thread ; la suite s'exécute hors de l'interface."""
        def f():
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(QMessageBox.Question, "DCS Copilote", text)
            box.setWindowFlag(Qt.WindowStaysOnTopHint)
            ok = box.addButton(yes, QMessageBox.AcceptRole)
            box.addButton(no, QMessageBox.RejectRole)
            box.exec()
            fn = on_yes if box.clickedButton() is ok else on_no
            if fn:
                threading.Thread(target=fn, daemon=True).start()
        self.overlay._call(f)

    def _dcs_folders(self):
        from . import dcs_install
        sg = self.cfg["dcs"].get("saved_games")
        return [sg] if sg and os.path.isdir(sg) else dcs_install.candidates()

    def _install_dcs(self, folders):
        from . import dcs_install
        for d in folders:
            try:
                self.overlay.info(dcs_install.install(d, int(self.cfg["dcs"]["udp_port"])))
            except OSError as e:
                self.log(f"Script DCS non installé dans {d} : {e}", error=True)
        if folders and not self.cfg["dcs"].get("saved_games"):
            self.cfg["dcs"]["saved_games"] = folders[0]
            self._save_setting(("dcs", "saved_games"), folders[0])

    def _refuse_dcs_install(self):
        self.cfg["dcs"]["auto_install"] = False
        self._save_setting(("dcs", "auto_install"), False)
        self.overlay.info("D'accord. Le script s'installe aussi depuis ⚙ Réglages → DCS ; sans lui, choisis "
                          "l'appareil dans le menu de l'overlay.")

    def _dcs_watch(self, first_run):
        """Au démarrage : met à jour le script DCS s'il est ancien et propose de l'installer s'il manque
        (au premier lancement, c'est l'assistant qui s'en charge). Ensuite, signale une fois par session
        DCS que DCS tourne sans rien envoyer."""
        from . import dcs_install
        port = int(self.cfg["dcs"]["udp_port"])
        missing = []
        for d in self._dcs_folders():
            st = dcs_install.status(d, port)
            if st == "outdated":
                self._install_dcs([d])
            elif st == "missing":
                missing.append(d)
        if missing and not first_run and self.cfg["dcs"].get("auto_install", True):
            self._ask("Installer le script DCS ? Il donne au copilote l'appareil piloté et la situation de vol "
                      "(altitude, vitesse, train…).\n\nIl est ajouté à Export.lua (sauvegardé en Export.lua.bak), "
                      "compatible SRS, Tacview, Helios.\n\n" + "\n".join(missing),
                      lambda: self._install_dcs(missing), self._refuse_dcs_install, yes="Installer", no="Non merci")
        minutes = 0
        while True:
            time.sleep(60)
            if self.dcs.snapshot() is not None or not dcs_install.dcs_running():
                minutes = 0
                continue
            minutes += 1
            port = int(self.cfg["dcs"]["udp_port"])
            installed = any(dcs_install.status(d, port) == "ok" for d in self._dcs_folders())
            if not installed and minutes == 1:
                self.overlay.info("DCS est lancé mais mon script n'y est pas installé : je ne sais pas ce que tu "
                                  "pilotes. ⚙ Réglages → DCS → « Installer », puis relance la mission.")
            elif installed and minutes == 10:
                self.overlay.info("DCS tourne depuis 10 min sans m'envoyer de données. Normal au menu ; en "
                                  "mission, vérifie le dossier dans ⚙ Réglages → DCS et réinstalle le script.")

    def _check_update(self):
        """Appli installée (Velopack) : nouvelle version sur GitHub → proposer de l'installer et redémarrer."""
        try:
            import velopack
            um = velopack.UpdateManager(velopack.GithubSource(REPO_URL))
            info = um.check_for_updates()
        except Exception as e:  # noqa: BLE001  (lancé depuis les sources : pas installé ; ou hors ligne)
            print(f"[mise à jour] {e}", flush=True)
            return
        if not info:
            return
        new = info.TargetFullRelease.Version

        def install():
            self.overlay.info(f"Téléchargement de la version {new}…")
            try:
                um.download_updates(info)
                um.apply_updates_and_restart(info)
            except Exception as e:  # noqa: BLE001
                self.log(f"Mise à jour impossible : {e}", error=True)
        self._ask(f"Nouvelle version {new} disponible (tu as {um.get_current_version()}).\n\nL'installer maintenant ? "
                  "L'appli redémarre ; tes réglages, ton historique et tes manuels sont conservés.",
                  install, yes="Installer", no="Plus tard")

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

    def _startup_manuals(self):
        """Au démarrage : charge l'index, repère DCS, indexe les manuels officiels nouveaux ou mis à jour,
        puis récupère le guide de l'appareil courant s'il manque."""
        from .manuals import build_index, dcs_install_candidates, find_pdfs, index_is_current
        self.load_manuals()
        m = self.cfg["manuals"]
        if not m.get("enabled", True):
            return
        if not m.get("dcs_dir"):
            found = dcs_install_candidates()
            if found:
                m["dcs_dir"] = found[0]
                self._save_setting(("manuals", "dcs_dir"), found[0])
                print(f"[manuels] DCS trouvé : {found[0]}", flush=True)
        pdfs = find_pdfs(m.get("dcs_dir"), m.get("extra_dir"))
        if pdfs and not index_is_current(pdfs):
            with self._chucks_lock:
                while self.dcs.snapshot() is not None:      # pas d'analyse lourde en plein vol
                    time.sleep(30)
                self.overlay.info("Je lis les manuels de tes modules (première fois : quelques minutes)…")
                try:
                    s = build_index(pdfs, threads=2)
                    self.load_manuals()
                    self.overlay.info(f"Manuels prêts : {s['docs']} documents.")
                except Exception as e:  # noqa: BLE001
                    print(f"[manuels] indexation impossible : {e}", flush=True)
        self.ensure_manual(self.current_aircraft())

    def _save_setting(self, path, value):
        """Enregistre une seule valeur dans config.json (sans écraser le reste)."""
        try:
            def change(disk):
                node = disk
                for k in path[:-1]:
                    node = node.setdefault(k, {})
                node[path[-1]] = value
            cfgmod.update(self.cfg_path, change)
        except (OSError, ValueError) as e:
            print(f"[réglages] {e}", flush=True)

    def ensure_manual(self, aircraft):
        """Appareil sans manuel indexé : télécharge son Chuck's Guide et l'indexe, en arrière-plan."""
        m = self.cfg["manuals"]
        if not aircraft or not m.get("enabled", True) or not m.get("chucks", {}).get("auto_current", True):
            return
        if self.manuals.covers(aircraft) or aircraft in self._fetching:
            return
        self._fetching.add(aircraft)
        threading.Thread(target=self._fetch_manual, args=(aircraft,), daemon=True).start()

    def _fetch_manual(self, aircraft):
        from . import chucks
        from .manuals import build_index, find_pdfs
        try:
            state = chucks.load_state()
            guides = state["guides"] or chucks.list_guides()
            slug = chucks.slug_for(aircraft, guides)
            if not slug:
                print(f"[manuels] pas de Chuck's Guide pour {aircraft}", flush=True)
                return
            name = guides.get(slug, slug)
            ch = self.cfg["manuals"].setdefault("chucks", {})
            if slug not in ch.setdefault("aircraft", []):
                ch["aircraft"].append(slug)
                self._save_setting(("manuals", "chucks", "aircraft"), list(ch["aircraft"]))
            with self._chucks_lock:
                self.overlay.info(f"Je récupère le guide de Chuck pour le {name}…")
                updated, errors = chucks.update(self.chucks_selection())
                if errors and not any(name in u for u in updated):
                    raise RuntimeError("; ".join(errors))
                self.overlay.info(f"Je lis le guide du {name}…")
                m = self.cfg["manuals"]
                build_index(find_pdfs(m.get("dcs_dir"), m.get("extra_dir")), threads=1)
                self.load_manuals()
            self.overlay.info(f"Guide du {name} prêt : mes réponses s'appuient dessus.")
        except Exception as e:  # noqa: BLE001
            print(f"[manuels] récupération du guide pour {aircraft} impossible : {e}", flush=True)
            self.overlay.info(f"Guide pour {aircraft} indisponible pour l'instant (pas d'Internet ?).")
        finally:
            self._fetching.discard(aircraft)

    # ------------------------------------------------------------------ Chuck's Guides
    def chucks_selection(self, cfg=None):
        from . import chucks
        m = (cfg or self.cfg)["manuals"]
        ch = m.get("chucks", {})
        slugs = list(ch.get("aircraft", []))
        if not ch.get("follow_installed", True):
            return slugs
        if m.get("dcs_dir") and os.path.isdir(os.path.join(m["dcs_dir"], "Mods", "aircraft")):
            guides = chucks.load_state()["guides"] or chucks.list_guides()
            slugs += [s for s in chucks.installed_slugs(guides, m["dcs_dir"]) if s not in slugs]
        else:
            # DCS introuvable (disque débranché, déplacé) : ses modules sembleraient désinstallés et la mise à jour
            # effacerait leurs guides ; on garde ceux déjà téléchargés
            slugs += [s for s in chucks.load_state()["files"] if s not in slugs]
        return slugs

    def _chucks_loop(self):
        """Vérifie au démarrage puis toutes les 6 h (chaque guide au plus une fois par semaine)."""
        from . import chucks
        time.sleep(20)                         # laisser l'appli démarrer tranquillement
        while True:
            wait = 6 * 3600
            try:
                if self.cfg["manuals"].get("chucks", {}).get("enabled") and chucks.due(chucks.load_state()):
                    if self._chucks_lock.locked():
                        # 1er lancement : les manuels officiels sont en cours d'indexation ; sans ce nouvel essai,
                        # les guides des modules installés n'arrivaient que 6 h plus tard
                        wait = 300
                    else:
                        self.update_chucks(background=True)
            except Exception as e:  # noqa: BLE001
                print(f"[chucks] {e}", flush=True)
            time.sleep(wait)

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
            # list() copie d'un bloc : l'historique peut être vidé par un autre thread (changement d'appareil)
            prev = [m["content"] for m in list(self.history) if m["role"] == "user"]
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
        engine = self.cfg["stt"].get("engine")
        if engine == "auto":                    # en ligne dès qu'une clé Groq ou OpenAI est disponible
            for prov in ("groq", "openai"):
                try:
                    self.stt_api = ApiSTT(dict(self.cfg["stt"], api_provider=prov), self.cfg["llm"])
                    print(f"Reconnaissance vocale en ligne ({prov}), Whisper local en secours", flush=True)
                    return
                except ValueError:
                    pass
        elif engine == "api":
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
            # chargé à la demande quand la reconnaissance en ligne est active : des centaines de Mo de mémoire
            # laissés à DCS tant que le secours ne sert pas (premier secours : quelques secondes de chargement)
            self._load_stt()
        if self.stt is None:
            raise RuntimeError("Whisper indisponible")
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
            self.overlay.info("Script DCS ancien : ⚙ Réglages → DCS → « Installer » pour donner au copilote "
                              "cap, incidence, carburant, train, volets…")
        name = state.get("aircraft") if state else None
        if name == self.dcs_aircraft:
            return
        self.dcs_aircraft = name
        if name:
            self.history.clear()
            self.overlay.set_aircraft(ac.pretty(name), locked=True)
            self.log(f"DCS : {ac.pretty(name)}")
            self.ensure_manual(ac.pretty(name))
        else:
            self.overlay.set_aircraft(self.manual_aircraft, locked=False)
            self.log("DCS déconnecté")

    def set_manual_aircraft(self, name):
        name = (name or "").strip()
        if name and name != self.manual_aircraft:
            self.manual_aircraft = name
            self.history.clear()
            self.overlay.info(f"Appareil : {name}")
            self.ensure_manual(name)

    def reset(self):
        self.checklist = None
        self._new_gen()
        self.speaker.stop()
        self.history.clear()
        self.overlay.start_answer("Nouvelle conversation")

    def quit(self):
        try:
            # relit le fichier : les surcharges --provider/--model ne sont pas enregistrées
            x, y = self.overlay.position()
            cfgmod.update(self.cfg_path, lambda d: d["overlay"].update(x=x, y=y))
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
        getattr(self.speaker.tts, "warm", lambda: None)()    # voix en ligne prête quand la réponse arrive

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
            print("[question] (bouton Détailler)", flush=True)
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
                    if not re.search(r"\w{2}", question):   # silence ou bruit transcrit en « . » / « ... »
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
        elif self.last_answer and (cl.is_detail(question) or
                                   ("détaille" in self.last_answer.lower() and cl.is_yes(question))):
            self._answer("Détaille ta réponse précédente : toutes les étapes et les explications utiles.",
                         "sol", speak, gen)
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
                               custom=self.cfg["llm"].get("custom_prompt", ""), manual=manual,
                               callsign=self.cfg["llm"].get("callsign", ""))
        messages = [{"role": "system", "content": system + "\n\n" + cl.PROMPT},
                    {"role": "user", "content": question}]
        try:
            text = "".join(self._stream(messages, gen, True))
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
        print(f"[checklist] {title} : {len(steps)} étapes\n" + "\n".join(f"  {n}. {s}" for n, s in enumerate(steps, 1)),
              flush=True)
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
            self.cfg["llm"].get("custom_prompt", ""), manual, self.cfg["llm"].get("callsign", ""),
            address=self.turns % 4 == 0)}]                 # indicatif : 1re réponse puis une sur quatre
        self.turns += 1
        messages += list(self.history)
        messages.append({"role": "user", "content": question})

        self.overlay.start_answer(question)
        self.overlay.set_status("Réflexion…", state="busy")
        splitter = SentenceSplitter()
        full, cancelled = [], False
        try:
            for delta in self._stream(messages, gen, self.cfg["llm"].get("fast", True)):
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
        model = self.llm.last_model if self.llm else "?"
        print(f"[réponse · {model}]{' (' + sources + ')' if sources else ' (sans manuel)'}\n{answer}", flush=True)
        if answer:
            self.history.append({"role": "user", "content": question})
            self.history.append({"role": "assistant", "content": answer})
            self.last_answer = answer
            self.overlay.show_actions(True)
            if sources:
                self.overlay.info(sources)
            self._note(question, answer, sources)
            self._quota_warning()
        self._ready()

    def _quota_warning(self):
        """Quota du jour presque épuisé (annoncé par le fournisseur) : prévenir une fois à 50, puis à 10."""
        from .llm import QUOTA
        q = QUOTA.get((self.llm.cfg["provider"], self.llm.last_model)) if self.llm else None
        level = next((n for n in (10, 50) if q and q[0] <= n), None)
        if level and level not in self.quota_warned:
            self.quota_warned |= {n for n in (10, 50) if n >= level}
            self.overlay.info(f"Quota du jour presque épuisé sur {self.llm.last_model} : {q[0]} requêtes "
                              "restantes. Ensuite, le modèle de secours prendra le relais.")

    def _note(self, question, answer, sources):
        """Historique à relire après le vol : un fichier Markdown par jour dans data/historique/."""
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            now = time.localtime()
            meta = " · ".join(x for x in (self.current_aircraft(), sources) if x)
            with open(os.path.join(HISTORY_DIR, time.strftime("%Y-%m-%d.md", now)), "a", encoding="utf-8") as f:
                f.write(f"## {time.strftime('%H:%M', now)} — {question}\n\n" + (f"*{meta}*\n\n" if meta else "")
                        + answer.strip() + "\n\n")
        except OSError as e:
            print(f"[historique] {e}", flush=True)

    def run(self):
        self.overlay.mainloop()

