"""Bouton push-to-talk : touche clavier globale (pynput) ou bouton HOTAS/joystick (pygame/SDL)."""
import os
import queue
import threading
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# indispensable : recevoir les boutons du joystick quand DCS a le focus
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")


# --------------------------------------------------------------------------- clavier
def key_to_str(key):
    from pynput import keyboard
    if isinstance(key, keyboard.Key):
        return key.name
    if getattr(key, "char", None):
        return key.char.lower()
    return f"vk{getattr(key, 'vk', '?')}"


class KeyboardPTT:
    def __init__(self, key_name, on_press, on_release):
        from pynput import keyboard
        self.key_name = key_name.lower()
        self.down = False
        self.on_press, self.on_release = on_press, on_release
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def _press(self, key):
        if key_to_str(key) == self.key_name and not self.down:  # ignore l'auto-répétition
            self.down = True
            self.on_press()

    def _release(self, key):
        if key_to_str(key) == self.key_name and self.down:
            self.down = False
            self.on_release()

    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()

    def describe(self):
        return f"touche « {self.key_name} »"


# --------------------------------------------------------------------------- joystick
def _pygame():
    import pygame
    pygame.display.init()
    pygame.joystick.init()
    return pygame


def _guid(js):
    try:
        return js.get_guid()
    except AttributeError:
        return ""


class JoystickPTT(threading.Thread):
    def __init__(self, name, guid, button, on_press, on_release, log):
        super().__init__(daemon=True)
        self.name, self.guid, self.button = name, guid, int(button)
        self.on_press, self.on_release, self.log = on_press, on_release, log
        self.running = True

    def stop(self):
        self.running = False

    def describe(self):
        return f"bouton {self.button + 1} de « {self.name} »"

    def _find(self, pygame):
        cands = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
        for js in cands:
            if self.guid and _guid(js) == self.guid and js.get_name() == self.name:
                return js
        for js in cands:
            if js.get_name() == self.name:
                return js
        return None

    def run(self):
        pygame = _pygame()
        js, down, last_scan = None, False, 0.0
        while self.running:
            for ev in pygame.event.get():
                iid = getattr(ev, "instance_id", None)
                if js is not None and iid == js.get_instance_id():
                    if ev.type == pygame.JOYBUTTONDOWN and ev.button == self.button and not down:
                        down = True
                        self.on_press()
                    elif ev.type == pygame.JOYBUTTONUP and ev.button == self.button and down:
                        down = False
                        self.on_release()
                    elif ev.type == pygame.JOYDEVICEREMOVED:
                        self.log(f"Joystick « {self.name} » déconnecté")
                        js = None
                        if down:
                            down = False
                            self.on_release()
            if js is None and time.monotonic() - last_scan > 2:
                last_scan = time.monotonic()
                js = self._find(pygame)
                if js is not None:
                    self.log(f"PTT prêt : {self.describe()}")
            time.sleep(0.008)


def make_ptt(cfg, on_press, on_release, log):
    if cfg.get("type") == "joystick":
        return JoystickPTT(cfg["joystick_name"], cfg.get("joystick_guid", ""), cfg["button"],
                           on_press, on_release, log)
    return KeyboardPTT(cfg.get("key", "scroll_lock"), on_press, on_release)


# --------------------------------------------------------------------------- bind
def capture_binding(timeout=20):
    """Attend le premier appui (clavier OU joystick) et renvoie la config PTT correspondante."""
    result = queue.Queue()
    done = threading.Event()
    listener = None

    def kb():
        nonlocal listener
        try:
            from pynput import keyboard

            def on_press(key):
                result.put({"type": "keyboard", "key": key_to_str(key)})
                return False

            listener = keyboard.Listener(on_press=on_press)
            listener.start()
        except Exception as e:  # noqa: BLE001
            print(f"(clavier indisponible : {e})")

    def joy():
        try:
            pygame = _pygame()
            # joysticks déjà connus (pygame peut avoir été initialisé par le PTT de l'appli)
            sticks = {}
            for i in range(pygame.joystick.get_count()):
                js = pygame.joystick.Joystick(i)
                sticks[js.get_instance_id()] = js
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout and not done.is_set():
                for ev in pygame.event.get():
                    if ev.type == pygame.JOYDEVICEADDED:
                        js = pygame.joystick.Joystick(ev.device_index)
                        sticks[js.get_instance_id()] = js
                    elif ev.type == pygame.JOYBUTTONDOWN and ev.instance_id in sticks:
                        js = sticks[ev.instance_id]
                        result.put({"type": "joystick", "joystick_name": js.get_name(),
                                    "joystick_guid": _guid(js), "button": ev.button})
                        return
                time.sleep(0.008)
        except Exception as e:  # noqa: BLE001
            print(f"(joysticks indisponibles : {e})")

    kb()
    threading.Thread(target=joy, daemon=True).start()
    try:
        return result.get(timeout=timeout)
    except queue.Empty:
        return None
    finally:
        done.set()
        if listener is not None:
            listener.stop()


def list_joysticks():
    pygame = _pygame()
    time.sleep(0.3)
    pygame.event.get()
    out = []
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        out.append((i, js.get_name(), js.get_numbuttons()))
    return out
