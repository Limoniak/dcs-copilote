"""Journal dans logs/dcs-copilote.log : indispensable sous Windows (run.bat lance pythonw, sans console)."""
import os
import sys
import threading
import time
import traceback

LOG_DIR = "logs"
LOG_PATH = os.path.join(LOG_DIR, "dcs-copilote.log")
MAX_BYTES = 2 * 1024 * 1024


class _Tee:
    """Duplique la sortie vers le fichier journal, horodatée ligne par ligne."""

    def __init__(self, stream, f, lock):
        self.stream, self.f, self.lock = stream, f, lock
        self.bol = True

    def write(self, s):
        if self.stream is not None:
            try:
                self.stream.write(s)
            except (OSError, ValueError):
                pass
        with self.lock:
            for part in s.splitlines(keepends=True):
                if self.bol:
                    self.f.write(time.strftime("%H:%M:%S "))
                self.f.write(part)
                self.bol = part.endswith("\n")
            self.f.flush()
        return len(s)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        return False


def setup():
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_BYTES:
        os.replace(LOG_PATH, LOG_PATH + ".1")
    f = open(LOG_PATH, "a", encoding="utf-8")
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — démarrage =====\n")
    lock = threading.Lock()
    sys.stdout = _Tee(sys.stdout, f, lock)
    sys.stderr = _Tee(sys.stderr, f, lock)

    def hook(exc_type, exc, tb):
        print("ERREUR NON GÉRÉE :\n" + "".join(traceback.format_exception(exc_type, exc, tb)), file=sys.stderr)
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
