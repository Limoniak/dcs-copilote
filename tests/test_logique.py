"""Tests de la logique sans micro, sans DCS ni réseau : python -m pytest tests"""
import time
import types

from dcs_copilote import checklist as cl
from dcs_copilote import config, dcs_install, llm
from dcs_copilote.app import _version
from dcs_copilote.manuals import TOC, english_queries, keep_languages, key_phrases
from dcs_copilote.prompts import system_prompt
from dcs_copilote.textutil import SentenceSplitter


# ------------------------------------------------------------------ manuels
def test_seules_les_versions_fr_en_sont_gardees():
    names = {"Guide FR", "Guide EN", "Guide DE", "Guide RU (2023)", "Quick DE", "Quick ES", "Chuck Hornet"}
    assert keep_languages(names) == {"Guide FR", "Guide EN", "Chuck Hornet", "Quick DE", "Quick ES"}


def test_recherche_anglaise_vise_la_piste_ou_le_porte_avions():
    assert "airfield takeoff" in english_queries("C'est quoi la procédure de décollage ?")[1]
    assert "carrier takeoff" in english_queries("Comment décoller d'un porte-avions ?")[1]
    assert english_queries("Quel est mon indicatif ?") == []
    assert key_phrases("Comment configurer les GBU-12 laser ?") >= {"gbu-12", "set up"}


def test_sommaire_reconnu():
    assert len(TOC.findall("Takeoff ........ 100\nLanding ......... 101\nTaxi ........ 99")) == 3
    assert not TOC.findall("Hold the stick back until 6 to 8 degrees.")


# ------------------------------------------------------------------ IA
def test_limite_par_minute_bascule_tout_de_suite_sur_le_secours(monkeypatch):
    calls = []

    def fake_request(self, model, messages, headers, fast=False):
        calls.append(model)
        status = 429 if model == "gros" else 200
        return types.SimpleNamespace(status_code=status, text="{}", headers={"Retry-After": "30"}, close=lambda: None)

    monkeypatch.setattr(llm.LLMClient, "_request", fake_request)
    monkeypatch.setattr(llm.LLMClient, "_read", lambda self, r: iter(["ok"]))
    monkeypatch.setattr(llm, "_exhausted", {})
    client = llm.LLMClient({"provider": "groq", "providers": {"groq": {
        "base_url": "http://x", "api_key": "k", "model": "gros", "fallback_model": "petit"}}})
    t = time.monotonic()
    assert "".join(client.stream([])) == "ok" and time.monotonic() - t < 1
    assert calls == ["gros", "petit"]
    calls.clear()
    "".join(client.stream([]))
    assert calls == ["petit"]                         # le gros modèle attend la fin de la fenêtre


def test_suivi_de_consommation_et_alerte_de_quota(tmp_path, monkeypatch):
    from dcs_copilote import app
    monkeypatch.setattr(llm, "USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(llm, "QUOTA", {})
    remaining = iter([60, 49, 48, 9])

    def fake_request(self, model, messages, headers, fast=False):
        h = {"x-ratelimit-remaining-requests": str(next(remaining)), "x-ratelimit-limit-requests": "1000"}
        return types.SimpleNamespace(status_code=200, text="", headers=h, close=lambda: None)

    monkeypatch.setattr(llm.LLMClient, "_request", fake_request)
    monkeypatch.setattr(llm.LLMClient, "_read", lambda self, r: iter(["ok"]))
    client = llm.LLMClient({"provider": "groq", "providers": {"groq": {"base_url": "http://x", "api_key": "k",
                                                                       "model": "gpt-oss-120b"}}})
    said = []
    fake = types.SimpleNamespace(llm=client, quota_warned=set(), overlay=types.SimpleNamespace(info=said.append))
    for _ in range(4):
        "".join(client.stream([]))
        app.App._quota_warning(fake)
    assert llm.usage_today() == {"groq|gpt-oss-120b": 4}
    assert llm.usage_summary("groq") == "Aujourd'hui : 4 requêtes · gpt-oss-120b : 9 sur 1000 restantes"
    assert len(said) == 2 and "49 requêtes" in said[0] and "9 requêtes" in said[1]     # une fois à 50, une fois à 10


def test_whisper_local_charge_seulement_en_secours():
    from dcs_copilote import app

    def down(audio, aircraft):
        raise ConnectionError("Groq injoignable")
    loads = []
    fake = types.SimpleNamespace(stt=None, current_aircraft=lambda: None,
                                 stt_api=types.SimpleNamespace(transcribe=down))
    fake._load_stt = lambda: (loads.append(1), setattr(fake, "stt", types.SimpleNamespace(
        transcribe=lambda audio, aircraft: "train sorti")))
    assert app.App._transcribe(fake, b"") == ("train sorti", "local") and loads == [1]
    app.App._transcribe(fake, b"")
    assert loads == [1]                               # chargé une seule fois


def test_indicatif_place_par_l_appli():
    on = system_prompt("F/A-18C Hornet", None, "vol", callsign="Viper 1", address=True)
    off = system_prompt("F/A-18C Hornet", None, "vol", callsign="Viper 1", address=False)
    assert "Commence ta réponse par cet indicatif" in on and "N'emploie pas l'indicatif" in off
    assert "Viper 1" in off                           # connu même quand il ne doit pas l'employer
    assert "reconnaissance vocale" in on and "reconnaissance vocale" not in system_prompt(None, None, "sol")


# ------------------------------------------------------------------ config, script DCS, versions
def test_ancien_vert_de_l_overlay_migre(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"overlay": {"accent": "#34d399"}}', encoding="utf-8")
    assert config.load(str(p))["overlay"]["accent"] == config.DEFAULTS["overlay"]["accent"]
    p.write_text('{"overlay": {"accent": "#ff0000"}}', encoding="utf-8")
    assert config.load(str(p))["overlay"]["accent"] == "#ff0000"


def test_statut_du_script_dcs(tmp_path):
    assert dcs_install.status(str(tmp_path), 7779) == "missing"
    dcs_install.install(str(tmp_path), 7779)
    assert dcs_install.status(str(tmp_path), 7779) == "ok"
    assert dcs_install.status(str(tmp_path), 7780) == "outdated"


def test_comparaison_des_versions():
    assert _version("v1.10.0") > _version("v1.9.3") > _version("v1.0.0")
    assert not _version("v1.0.4") > _version("v1.0.4")


def test_historique_markdown(tmp_path, monkeypatch):
    from dcs_copilote import app
    monkeypatch.setattr(app, "HISTORY_DIR", str(tmp_path))
    fake = types.SimpleNamespace(current_aircraft=lambda: "F/A-18C Hornet")
    app.App._note(fake, "Comment sortir le train ?", "Poignée du train sur DOWN.", "Sources : Guide EN p. 57")
    app.App._note(fake, "Et les volets ?", "Volets sur HALF.", "")
    (day,) = tmp_path.iterdir()
    text = day.read_text(encoding="utf-8")
    assert text.count("## ") == 2 and "— Comment sortir le train ?" in text
    assert "*F/A-18C Hornet · Sources : Guide EN p. 57*" in text and "*F/A-18C Hornet*" in text


GUIDES = {"a-10c": "A-10C Warthog", "av-8b": "AV-8B N/A Harrier II", "f-14b": "F-14B Tomcat",
          "f-15e": "F-15E Strike Eagle", "f-16cm": "F-16CM Viper", "fa-18c": "F/A-18C Hornet", "jf-17": "JF-17 Thunder",
          "m-2000c": "Mirage 2000C", "mig-21bis": "Mig-21bis", "ka-50": "Ka-50 Black Shark", "ah-64d": "AH-64D Apache",
          "p-51d": "P-51D Mustang", "uh-1h": "UH-1H Huey", "mi-8": "Mi-8MTV2"}


def test_guide_de_chuck_pour_chaque_appareil(tmp_path):
    from dcs_copilote import chucks
    from dcs_copilote.aircraft import pretty
    expected = {"FA-18C_hornet": "fa-18c", "M-2000C": "m-2000c", "F-16C_50": "f-16cm", "A-10C_2": "a-10c",
                "AV8BNA": "av-8b", "Ka-50_3": "ka-50", "AH-64D_BLK_II": "ah-64d", "MiG-21Bis": "mig-21bis",
                "F-15ESE": "f-15e", "Mi-8MT": "mi-8", "Su-25T": None}
    assert {a: chucks.slug_for(pretty(a), GUIDES) for a in expected} == expected
    assert chucks.slug_for("Mirage 2000C", GUIDES) == "m-2000c"           # choisi dans le menu de l'overlay
    for folder in ("FA-18C", "M-2000C", "F-16C", "AV8BNA", "Su-25T", "Uh-1H"):
        (tmp_path / "Mods" / "aircraft" / folder).mkdir(parents=True)
    assert sorted(chucks.installed_slugs(GUIDES, str(tmp_path))) == ["av-8b", "f-16cm", "fa-18c", "m-2000c", "uh-1h"]


def test_alertes_selon_les_limites_de_l_appareil():
    from dcs_copilote.alerts import MS_TO_KT, Alerts

    def fly(aircraft, kt, gear=1.0, flaps=0.0, seconds=3):
        said = []
        a = Alerts(lambda: {"enabled": True}, said.append)
        packet = {"aircraft": aircraft, "agl_m": 600, "vs_ms": 0, "ias_ms": kt / MS_TO_KT, "gear": gear, "flaps": flaps}
        a.feed(packet)
        a.since -= 60                                 # passé le délai de grâce après l'apparition
        for _ in range(seconds):
            a.feed(packet)
        return said

    assert fly("P-51D", 160) == ["Train sorti à 160 nœuds, réduis !"]
    assert fly("FA-18C_hornet", 160) == []
    assert fly("FA-18C_hornet", 270) == ["Train sorti à 270 nœuds, réduis !"]
    assert fly("F-16C_50", 280) == [] and fly("F-16C_50", 450, gear=0.0, flaps=0.6) == []   # volets automatiques
    assert fly("A-10C_2", 230, gear=0.0, flaps=0.5) == ["Volets sortis à 230 nœuds, réduis !"]


# ------------------------------------------------------------------ voix, checklist
def test_decoupage_des_phrases_pour_la_voix():
    s = SentenceSplitter()
    assert s.feed("Master Arm sur ARM. On est parés pour le tir, ") == ["Master Arm sur ARM."]
    assert s.feed("vise la cible. Tir") == ["On est parés pour le tir, vise la cible."]
    assert s.flush() == ["Tir"]


def test_checklist():
    assert cl.parse_steps("1. Batterie ON\n2. APU ON\n3) Moteur droit") == ["Batterie ON", "APU ON", "Moteur droit"]
    assert [cl.command(x) for x in ("suivant", "répète", "précédent", "stop", "bonjour")] == \
        ["next", "repeat", "prev", "stop", None]
    assert cl.is_start("lance la procédure de démarrage") and not cl.is_start("comment ça va")
