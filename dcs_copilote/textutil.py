"""Découpage en phrases (pour lire la réponse pendant qu'elle arrive) et nettoyage pour la synthèse vocale."""
import re

_SENT_END = re.compile(r"(?<=[.!?…])\s+|\n+")


class SentenceSplitter:
    def __init__(self, min_len=12):
        self.buf = ""
        self.min_len = min_len

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            m = _SENT_END.search(self.buf)
            if not m:
                break
            piece = self.buf[: m.start()].strip()
            # éviter de lire des fragments minuscules ("1." d'une liste) : on attend la suite
            if len(piece) < self.min_len and "\n" not in m.group(0):
                nxt = _SENT_END.search(self.buf, m.end())
                if not nxt:
                    break
                piece = self.buf[: nxt.start()].strip()
                self.buf = self.buf[nxt.end():]
            else:
                self.buf = self.buf[m.end():]
            if piece:
                out.append(piece)
        return out

    def flush(self):
        rest = self.buf.strip()
        self.buf = ""
        return [rest] if rest else []


_REPLACEMENTS = [
    (re.compile(r"[*_#`>|]+"), " "),
    (re.compile(r"^\s*[-•]\s+", re.M), ""),
    (re.compile(r"(\d)\s*kt\b"), r"\1 nœuds"),
    (re.compile(r"(\d)\s*kts\b"), r"\1 nœuds"),
    (re.compile(r"(\d)\s*ft\b"), r"\1 pieds"),
    (re.compile(r"(\d)\s*NM\b"), r"\1 nautiques"),
    (re.compile(r"(\d)\s*°"), r"\1 degrés"),
    (re.compile(r"\s{2,}"), " "),
]


def clean_for_speech(text):
    for rx, rep in _REPLACEMENTS:
        text = rx.sub(rep, text)
    return text.strip()
