"""Réception des données envoyées par DCSAssistant.lua (UDP local)."""
import json
import socket
import threading
import time

TIMEOUT = 8  # s sans paquet => plus en vol / DCS fermé


class DCSLink(threading.Thread):
    def __init__(self, port, on_change, log):
        super().__init__(daemon=True)
        self.port, self.on_change, self.log = port, on_change, log
        self.state = None
        self._lock = threading.Lock()
        self.running = True

    def stop(self):
        self.running = False

    def snapshot(self):
        with self._lock:
            return dict(self.state) if self.state else None

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", self.port))
        except OSError as e:
            self.log(f"Port UDP {self.port} indisponible : {e}")
            return
        sock.settimeout(1.0)
        last = 0.0
        while self.running:
            try:
                data, _ = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8", errors="replace"))
                last = time.monotonic()
                with self._lock:
                    prev = self.state
                    self.state = msg
                if not prev or prev.get("aircraft") != msg.get("aircraft"):
                    self.on_change(msg)
            except socket.timeout:
                pass
            except (ValueError, UnicodeDecodeError):
                continue
            if self.state and time.monotonic() - last > TIMEOUT:
                with self._lock:
                    self.state = None
                self.on_change(None)
        sock.close()
