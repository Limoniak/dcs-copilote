"""Micro (push-to-talk), bips, synthèse vocale et lecture interruptible."""
import itertools
import os
import queue
import re
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from .textutil import clean_for_speech

SR_IN = 16000
MAX_SECONDS = 30


def _dev(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


# Plugins ALSA internes : inutilisables tels quels, ils encombrent la liste sous Linux
_ALSA_HIDDEN = {"default", "sysdefault", "lavrate", "samplerate", "speexrate", "speex", "upmix", "vdownmix",
                "dmix", "dsnoop", "jack", "oss", "surround40", "surround51", "surround71"}
_ALSA_NAMES = {"pipewire": "PipeWire", "pulse": "PulseAudio", "hdmi": "HDMI"}


def list_devices(kind):
    """[(valeur config, libellé)] des entrées ("input") ou sorties ("output") utiles.
    La valeur « nom, API hôte » reste valable si les index changent (branchement USB)."""
    apis = sd.query_hostapis()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    default_api = sd.default.hostapi
    out = []
    for d in sd.query_devices():
        if d[key] <= 0:
            continue
        api = apis[d["hostapi"]]["name"]
        name = d["name"]
        if sys.platform == "win32":
            # un même appareil existe pour MME, DirectSound, WASAPI… : on garde l'API par défaut (MME),
            # qui accepte n'importe quelle fréquence (WASAPI refuse les 22 kHz de Piper)
            if d["hostapi"] != default_api:
                continue
            label = name
        elif api == "ALSA":
            if name in _ALSA_HIDDEN:
                continue
            label = _ALSA_NAMES.get(name) or name.replace(": -", "").replace(" - ", " ") + " — accès direct"
        else:
            label = f"{name} ({api})"
        out.append((f"{name}, {api}", label))
    return out


def download_voice(name, dest="voices"):
    """Télécharge une voix Piper (ex. fr_FR-siwis-medium) depuis Hugging Face."""
    import requests
    # ex. fr_FR-siwis-medium -> fr/fr_FR/siwis/medium/
    lang_region, voice, quality = name.split("-", 2)
    base = (f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{lang_region.split('_')[0]}/{lang_region}/{voice}/{quality}/{name}")
    os.makedirs(dest, exist_ok=True)
    for ext in (".onnx.json", ".onnx"):
        path = os.path.join(dest, name + ext)
        with requests.get(base + ext, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(path + ".part", "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        os.replace(path + ".part", path)
    return os.path.join(dest, name + ".onnx")


class _Output:
    """Sortie audio unique, ouverte en permanence et alimentée par un tampon (rappel PortAudio).

    sd.play / sd.stop / sd.wait partagent un flux global et ne supportent pas d'être appelés depuis
    plusieurs threads : couper la voix pendant une lecture pouvait bloquer définitivement la fermeture
    du flux (plus aucune voix). Ici, rien ne s'ouvre ni ne se ferme pendant les lectures : on remplace
    ou on vide le tampon, et une attente ne peut pas durer plus que le son lui-même (+ 3 s)."""

    def __init__(self):
        self.lock = threading.Lock()           # tampon (partagé avec le rappel audio)
        self.open_lock = threading.Lock()      # ouverture du flux
        self.stream, self.device, self.rate, self.ch = None, object(), 48000, 1
        self.buf, self.pos, self.token = None, 0, 0
        self.done = threading.Event()
        self.done.set()

    def _ensure(self, device):
        with self.open_lock:
            if self.stream is not None and self.device == device:
                return
            rate = int(sd.query_devices(device, "output")["default_samplerate"])
            ch = 1
            try:
                sd.check_output_settings(device=device, samplerate=rate, channels=1)
            except Exception:  # noqa: BLE001
                ch = 2
            stream = sd.OutputStream(samplerate=rate, channels=ch, dtype="float32", device=device,
                                     callback=self._callback, blocksize=1024)
            stream.start()
            old, self.stream = self.stream, stream
            self.device, self.rate, self.ch = device, rate, ch
        if old is not None:
            try:
                old.abort()
                old.close()
            except Exception:  # noqa: BLE001
                pass

    def _callback(self, outdata, frames, _time, _status):
        with self.lock:
            buf = self.buf
            if buf is None:
                outdata.fill(0)
                return
            n = min(frames, len(buf) - self.pos)
            outdata[:n] = buf[self.pos:self.pos + n, None]
            outdata[n:] = 0
            self.pos += n
            if self.pos >= len(buf):
                self.buf = None
                self.done.set()

    def play(self, audio, sr, device=None, wait=False):
        self._ensure(device)
        x = audio.astype(np.float32) / (32768.0 if audio.dtype == np.int16 else 1.0)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != self.rate and len(x):
            n = int(len(x) * self.rate / sr)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        with self.lock:
            self.token += 1
            token = self.token
            self.buf, self.pos = x, 0
            self.done.clear()
        if wait:
            end = time.monotonic() + len(x) / self.rate + 3
            while time.monotonic() < end and not self.done.wait(0.05):
                with self.lock:
                    if self.token != token:        # coupé (stop) ou remplacé par un autre son
                        return

    def stop(self):
        with self.lock:
            self.token += 1
            self.buf = None
            self.done.set()


_OUT = _Output()


def play(audio, sr, device=None, wait=False):
    _OUT.play(audio, sr, device, wait)


def stop_playback():
    _OUT.stop()


# --------------------------------------------------------------------------- micro
class Recorder:
    """Flux micro ouvert en permanence : aucune latence à l'appui sur le bouton."""

    def __init__(self, device=None):
        self._lock = threading.Lock()
        self._frames = []
        self._recording = False
        device = _dev(device)
        # Whisper veut du 16 kHz ; les micros en accès direct n'acceptent souvent que leur fréquence native
        self.rate = SR_IN
        try:
            sd.check_input_settings(device=device, samplerate=SR_IN, channels=1)
        except Exception:  # noqa: BLE001
            self.rate = int(sd.query_devices(device, "input")["default_samplerate"])
        self._stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32",
                                      device=device, callback=self._cb, blocksize=1024)
        self._stream.start()

    def _cb(self, indata, frames, t, status):
        if self._recording:
            with self._lock:
                if len(self._frames) * 1024 < self.rate * MAX_SECONDS:
                    self._frames.append(indata[:, 0].copy())

    def start(self):
        with self._lock:
            self._frames = []
            self._recording = True

    def stop(self):
        with self._lock:
            self._recording = False
            frames, self._frames = self._frames, []
        if not frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(frames)
        if self.rate != SR_IN:
            n = int(len(audio) * SR_IN / self.rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio

    def close(self):
        self._stream.stop()
        self._stream.close()


# --------------------------------------------------------------------------- bips
def _tone(freqs, dur=0.07, sr=22050, vol=0.25):
    out = []
    for f in freqs:
        t = np.arange(int(sr * dur)) / sr
        env = np.minimum(1, np.minimum(t, dur - t) * 60)
        out.append(vol * env * np.sin(2 * np.pi * f * t))
    return np.concatenate(out).astype(np.float32), sr


BEEP_START = _tone([880, 1320])
BEEP_STOP = _tone([1320, 880])
BEEP_ERR = _tone([300, 220], dur=0.12)
TEST_CHIME = _tone([523, 659, 784, 1047], dur=0.16, vol=0.3)   # do-mi-sol-do


# --------------------------------------------------------------------------- effet radio
# niveau : (fréquence basse, fréquence haute, saturation, souffle)
RADIO_LEVELS = {1: (250, 4000, 1.6, 0.003), 2: (300, 3200, 2.6, 0.007), 3: (450, 2600, 4.0, 0.014)}


def _bandpass(x, sr, lo, hi, order=4):
    """Filtre passe-bande (gabarit de Butterworth appliqué dans le domaine fréquentiel)."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    f[0] = 1e-3
    h = 1 / np.sqrt(1 + (lo / f) ** (2 * order)) / np.sqrt(1 + (f / hi) ** (2 * order))
    return np.fft.irfft(np.fft.rfft(x) * h, n).astype(np.float32)


def radio_fx(audio, sr, level=2):
    """Voix « radio de cockpit » : bande étroite, légère saturation, souffle."""
    lo, hi, drive, hiss = RADIO_LEVELS.get(level, RADIO_LEVELS[2])
    x = audio.astype(np.float32) / (32768.0 if audio.dtype == np.int16 else 1.0)
    x = _bandpass(x, sr, lo, hi)
    x = np.tanh(drive * x / (np.abs(x).max() + 1e-6)) / np.tanh(drive)
    noise = _bandpass(np.random.default_rng().normal(0, 1, len(x)).astype(np.float32), sr, lo, hi)
    x = x + hiss * noise / (np.abs(noise).max() + 1e-6) * 3
    return (0.6 * x / (np.abs(x).max() + 1e-6)).astype(np.float32)   # saturée = plus forte à crête égale


def squelch(sr, level=2, kind="open"):
    """Clic d'ouverture (« open ») ou souffle de fin de transmission (« tail »)."""
    lo, hi, _drive, _hiss = RADIO_LEVELS.get(level, RADIO_LEVELS[2])
    dur = 0.04 if kind == "open" else 0.16
    n = int(sr * dur)
    noise = _bandpass(np.random.default_rng().normal(0, 1, n).astype(np.float32), sr, lo, hi)
    env = np.exp(-np.linspace(0, 7 if kind == "open" else 4, n)).astype(np.float32)
    burst = 0.5 * noise / (np.abs(noise).max() + 1e-6) * env
    gap = np.zeros(int(sr * 0.03), np.float32)
    return np.concatenate([burst, gap]) if kind == "open" else np.concatenate([gap, burst])


# --------------------------------------------------------------------------- TTS
class PiperTTS:
    def __init__(self, model_path, speed=1.0):
        from piper import PiperVoice
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Voix Piper introuvable : {model_path} (⚙ Réglages → Voix → Télécharger)")
        self.voice = PiperVoice.load(model_path)
        self.length_scale = 1.0 / max(0.5, float(speed))
        try:
            from piper import SynthesisConfig  # piper-tts >= 1.3
            self._cfg = SynthesisConfig(length_scale=self.length_scale)
        except ImportError:
            self._cfg = None

    def synth(self, text):
        if self._cfg is not None:
            chunks = list(self.voice.synthesize(text, syn_config=self._cfg))
            if not chunks:
                return None, 0
            return np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate
        raw = b"".join(self.voice.synthesize_stream_raw(text, length_scale=self.length_scale))
        return np.frombuffer(raw, dtype=np.int16), self.voice.config.sample_rate


class SystemTTS:
    """Repli : SAPI sous Windows, espeak-ng sous Linux (via pyttsx3)."""

    def __init__(self, speed=1.0):
        self.speed = speed
        self.engine = None  # créé dans le thread de lecture (COM sous Windows)

    def _init(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            pass
        import pyttsx3
        self.engine = pyttsx3.init()
        for v in self.engine.getProperty("voices"):
            ident = f"{v.id} {v.name} {getattr(v, 'languages', '')}".lower()
            if "fr" in ident or "french" in ident:
                self.engine.setProperty("voice", v.id)
                break
        self.engine.setProperty("rate", int(180 * self.speed))

    def synth(self, text):
        if self.engine is None:
            self._init()
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            self.engine.save_to_file(text, path)
            self.engine.runAndWait()
            with wave.open(path, "rb") as w:
                sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
                data = w.readframes(w.getnframes())
            if width != 2:
                return None, 0
            audio = np.frombuffer(data, dtype=np.int16)
            if ch > 1:
                audio = audio[::ch]
            return audio, sr
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


_QUOTED = re.compile(r"[«“\"]\s*([^«»“”\"]+?)\s*[»”\"]")


def split_quoted(text):
    """« Master Arm » → segments [(texte, entre_guillemets)] ; le prompt demande de citer ainsi les
    termes anglais du cockpit."""
    out, pos = [], 0
    for m in _QUOTED.finditer(text):
        if text[pos:m.start()].strip():
            out.append((text[pos:m.start()].strip(), False))
        out.append((m.group(1), True))
        pos = m.end()
    if text[pos:].strip():
        out.append((text[pos:].strip(), False))
    return out


def _trim(audio, thr=250):
    idx = np.flatnonzero(np.abs(audio) > thr)
    return audio[idx[0]:idx[-1] + 1] if len(idx) else audio[:0]


class EdgeTTS:
    """Voix neuronales Microsoft (service de lecture à voix haute d'Edge) : très naturelles, gratuites,
    sans clé, mais service non officiel → repli automatique sur Piper en cas d'échec.

    Les voix « Multilingual » devinent la langue elles-mêmes et se trompent sur les phrases mixtes
    (accent anglais sur du français). Le service refuse le SSML <lang>, donc on synthétise séparément
    (en parallèle) le français et les « termes » entre guillemets, puis on recolle avec une courte pause."""

    SR = 24000

    def __init__(self, voice, speed=1.0):
        import edge_tts  # noqa: F401  (vérifie la présence du paquet)
        self.voice = voice or "fr-FR-HenriNeural"
        self.rate = f"{round((float(speed) - 1) * 100):+d}%"
        self.split = "Multilingual" in self.voice
        _edge_locale_patch(self.voice)

    async def _fetch(self, text):
        import edge_tts
        data = bytearray()
        async for chunk in edge_tts.Communicate(text, self.voice, rate=self.rate).stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return bytes(data)

    def _decode(self, mp3):
        import miniaudio
        d = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                             sample_rate=self.SR)
        return np.frombuffer(d.samples, dtype=np.int16)

    def synth(self, text):
        import asyncio
        parts = split_quoted(text) if self.split else []
        if len(parts) < 2:
            parts = [(text.replace("«", "").replace("»", ""), False)]

        async def fetch_all():
            return await asyncio.gather(*(self._fetch(t) for t, _ in parts))

        mp3s = asyncio.run(asyncio.wait_for(fetch_all(), timeout=12))
        if len(parts) == 1:
            return (self._decode(mp3s[0]), self.SR) if mp3s[0] else (None, 0)
        gap = np.zeros(int(self.SR * 0.07), dtype=np.int16)
        pieces = []
        for mp3 in mp3s:
            seg = _trim(self._decode(mp3)) if mp3 else None
            if seg is not None and len(seg):
                pieces += [seg, gap]
        return (np.concatenate(pieces[:-1]), self.SR) if pieces else (None, 0)


def _edge_locale_patch(voice):
    """edge-tts annonce le texte en en-US : avec une voix multilingue, ça pousse vers l'accent anglais.
    On annonce la langue de la voix (fr-FR…). Sans effet si la structure d'edge-tts change."""
    try:
        import edge_tts.communicate as comm
        if getattr(comm.mkssml, "_dcs_patched", False):
            comm.mkssml._locale = "-".join(voice.split("-")[:2])
            return
        orig = comm.mkssml

        def mkssml(tc, escaped_text):
            return orig(tc, escaped_text).replace("xml:lang='en-US'", f"xml:lang='{mkssml._locale}'", 1)
        mkssml._dcs_patched = True
        mkssml._locale = "-".join(voice.split("-")[:2])
        comm.mkssml = mkssml
    except Exception:  # noqa: BLE001
        pass


class OpenAITTS:
    """Synthèse OpenAI (/v1/audio/speech, PCM 24 kHz) : excellente qualité, payante, clé OpenAI requise."""

    INSTRUCTIONS = ("Tu es le copilote français du pilote, à la radio de bord : voix calme, posée et assurée, "
                    "débit légèrement rapide, prononciation claire des termes techniques.")

    def __init__(self, api_key, voice="ash", model="gpt-4o-mini-tts", speed=1.0):
        if not api_key:
            raise ValueError("clé OpenAI manquante (⚙ Réglages → Voix)")
        self.key, self.voice, self.model, self.speed = api_key, voice or "ash", model, float(speed)

    def synth(self, text):
        import requests
        payload = {"model": self.model, "voice": self.voice, "input": text,
                   "response_format": "pcm", "speed": self.speed}
        if "gpt-4o" in self.model:
            payload["instructions"] = self.INSTRUCTIONS
        r = requests.post("https://api.openai.com/v1/audio/speech", json=payload, timeout=20,
                          headers={"Authorization": f"Bearer {self.key}"})
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI TTS HTTP {r.status_code} : {r.text[:200]}")
        return np.frombuffer(r.content, dtype=np.int16), 24000


class FallbackTTS:
    """Voix en ligne avec repli local : après un échec, Piper (ou voix système) prend le relais 60 s."""

    RETRY_AFTER = 60

    def __init__(self, primary, make_fallback, log):
        self.primary, self.make_fallback, self.log = primary, make_fallback, log
        self.fallback = None
        self.failed_at = None

    def synth(self, text):
        if self.failed_at is None or time.monotonic() - self.failed_at > self.RETRY_AFTER:
            try:
                res = self.primary.synth(text)
                self.failed_at = None
                return res
            except Exception as e:  # noqa: BLE001
                if self.failed_at is None:
                    self.log(f"Voix en ligne indisponible ({e}) → voix locale")
                self.failed_at = time.monotonic()
        if self.fallback is None:
            self.fallback = self.make_fallback()
        return self.fallback.synth(text)


def _local_tts(cfg, log):
    try:
        return PiperTTS(cfg["piper_model"], cfg.get("speed", 1.0))
    except Exception as e:  # noqa: BLE001
        log(f"Piper indisponible ({e}) → voix système")
    return SystemTTS(cfg.get("speed", 1.0))


def openai_key(tts_cfg, llm_cfg):
    """Clé OpenAI : celle des réglages Voix, sinon celle du fournisseur OpenAI de l'onglet IA, sinon l'env."""
    prov = ((llm_cfg or {}).get("providers") or {}).get("openai") or {}
    return (tts_cfg.get("openai_key") or prov.get("api_key")
            or os.environ.get(prov.get("api_key_env") or "OPENAI_API_KEY", ""))


def make_tts(cfg, log, llm_cfg=None):
    if not cfg.get("enabled", True):
        return None
    engine = cfg.get("engine")
    try:
        if engine == "edge":
            primary = EdgeTTS(cfg.get("edge_voice"), cfg.get("speed", 1.0))
        elif engine == "openai":
            primary = OpenAITTS(openai_key(cfg, llm_cfg), cfg.get("openai_voice"),
                                cfg.get("openai_model") or "gpt-4o-mini-tts", cfg.get("speed", 1.0))
        else:
            primary = None
    except Exception as e:  # noqa: BLE001
        log(f"Voix {engine} indisponible ({e}) → voix locale")
        primary = None
    if primary is not None:
        return FallbackTTS(primary, lambda: _local_tts(cfg, log), log)
    if engine == "system":
        return SystemTTS(cfg.get("speed", 1.0))
    return _local_tts(cfg, log)


# --------------------------------------------------------------------------- lecture
class Speaker(threading.Thread):
    """File de phrases à lire ; stop() coupe immédiatement (nouvel appui PTT).
    Deux étages : la phrase suivante est synthétisée pendant que la courante est jouée (pas de blanc,
    important avec les voix en ligne)."""

    def __init__(self, tts, output_device, current_gen, log):
        super().__init__(daemon=True)
        self.tts = tts
        self.device = _dev(output_device)
        self.current_gen = current_gen
        self.log = log
        # (priorité, ordre, …) : 0 = alerte (passe devant et coupe la phrase en cours), 1 = réponse
        self.q = queue.PriorityQueue()
        self.audio_q = queue.PriorityQueue()
        self._seq = itertools.count()
        self.busy = threading.Event()
        self.on_start = None                   # on_start(gen) : début de lecture d'une phrase (mesure de latence)
        self.radio = 0                         # effet radio : 0 = aucun, 1 léger, 2 radio, 3 fort
        self._keyed = None                     # transmission en cours (clic d'ouverture déjà joué)
        self._synth_busy = False
        threading.Thread(target=self._player, daemon=True).start()

    def say(self, text, gen):
        text = clean_for_speech(text)
        if self.tts and text:
            self.q.put((1, next(self._seq), text, gen))

    def alert(self, text):
        """Annonce prioritaire, indépendante de la question en cours (alertes de vol)."""
        text = clean_for_speech(text)
        if self.tts and text:
            self.q.put((0, next(self._seq), text, None))

    def stop(self):
        self._keyed = None
        for q in (self.q, self.audio_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        stop_playback()

    def play(self, sound):
        audio, sr = sound
        play(audio, sr, self.device)

    def run(self):
        while True:
            try:
                self._synth_one()
            except Exception as e:  # noqa: BLE001  (ne jamais laisser mourir le thread : plus de voix sinon)
                self.log(f"Erreur voix : {e}")

    def _synth_one(self):
        prio, seq, text, gen = self.q.get()
        if gen is not None and gen != self.current_gen():
            return
        tts = self.tts
        if tts is None:
            return
        self._synth_busy = True
        try:
            audio, sr = tts.synth(text)
        except Exception as e:  # noqa: BLE001
            self.log(f"Erreur TTS : {e}")
            return
        finally:
            self._synth_busy = False
        if audio is not None and (gen is None or gen == self.current_gen()):
            self.audio_q.put((prio, seq, audio, sr, gen))
            if prio == 0 and self.busy.is_set():
                stop_playback()            # l'alerte coupe la phrase en cours

    def _player(self):
        while True:
            try:
                self._play_one()
            except Exception as e:  # noqa: BLE001  (ne jamais laisser mourir le thread : plus de voix sinon)
                self.busy.clear()
                self.log(f"Erreur lecture : {e}")

    def _play_one(self):
        _prio, _seq, audio, sr, gen = self.audio_q.get()
        if gen is not None and gen != self.current_gen():
            return
        self.busy.set()
        try:
            if self.on_start:
                try:
                    self.on_start(gen)
                except Exception as e:  # noqa: BLE001
                    print(f"[voix] mesure de latence : {e}", flush=True)
            level = self.radio
            if level:
                audio = radio_fx(audio, sr, level)
                key = ("alerte", _seq) if gen is None else gen
                if self._keyed != key:         # début de transmission : clic d'ouverture
                    audio = np.concatenate([squelch(sr, level, "open"), audio])
                    self._keyed = key
            play(audio, sr, self.device, wait=True)
            # fin de transmission (plus rien à dire) : souffle de squelch
            if level and self.q.empty() and self.audio_q.empty() and not self._synth_busy:
                play(squelch(sr, level, "tail"), sr, self.device, wait=True)
                self._keyed = None
        except Exception as e:  # noqa: BLE001
            self.log(f"Erreur audio : {e}")
        finally:
            self.busy.clear()
