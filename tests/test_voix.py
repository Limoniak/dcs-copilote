"""Voix Microsoft (Edge) : connexion gardée ouverte, et repli sur une connexion par phrase si elle échoue."""
import asyncio
import types

import pytest

pytest.importorskip("edge_tts")
pytest.importorskip("miniaudio")
import aiohttp  # noqa: E402

from dcs_copilote import audio  # noqa: E402


class FakeWS:
    """Le service : réponse audio en morceaux binaires (2 octets de longueur d'en-tête), puis turn.end."""

    def __init__(self):
        self.closed, self.sent = False, []

    async def send_str(self, s):
        self.sent.append(s)

    def __aiter__(self):
        head = b"Path:audio\r\n"
        msgs = [types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="Path:turn.start\r\n\r\n{}")]
        # la longueur annoncée compte aussi ses 2 octets (comme edge-tts la lit : données après longueur + 2)
        msgs += [types.SimpleNamespace(type=aiohttp.WSMsgType.BINARY,
                                       data=(len(head) + 2).to_bytes(2, "big") + head + b"\r\n" + chunk)
                 for chunk in (b"MP3-1", b"MP3-2")]
        msgs.append(types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="Path:turn.end\r\n\r\n{}"))

        async def gen():
            for m in msgs:
                yield m
        return gen()


def test_reponse_du_service_reassemblee():
    t = audio.EdgeTTS("fr-FR-HenriNeural")
    t._ws = FakeWS()
    assert asyncio.run(t._turn("Train sorti.")) == b"MP3-1MP3-2"
    assert "Train sorti." in t._ws.sent[0] and "Path:ssml" in t._ws.sent[0]


def test_repli_si_la_connexion_gardee_echoue(monkeypatch):
    t = audio.EdgeTTS("fr-FR-HenriNeural")

    async def broken(text):
        raise ConnectionError("service injoignable")

    async def per_sentence(text):
        return b"mp3"
    monkeypatch.setattr(t, "_fetch_open", broken)
    monkeypatch.setattr(t, "_fetch", per_sentence)
    monkeypatch.setattr(t, "_decode", lambda mp3: audio.np.ones(10, dtype=audio.np.int16))
    pcm, sr = t.synth("On est parés.")
    assert sr == t.SR and len(pcm) == 10
