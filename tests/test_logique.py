"""Tests de la logique sans micro, sans DCS ni réseau : python -m pytest tests"""
import time
import types

from dcs_copilote import checklist as cl
from dcs_copilote import config, dcs_install, llm
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


def test_procedure_entiere_pour_une_checklist(monkeypatch):
    import threading

    from dcs_copilote.manuals import Manuals
    m = Manuals(print)
    m._lock = threading.RLock()
    m.docs = [{"name": "Guide EN.pdf"}, {"name": "Autre.pdf"}]
    m.chunks = [{"doc": 0, "page": 99, "text": "AIRCRAFT CARRIER LAUNCH 1. Attach the launch bar"},
                {"doc": 0, "page": 100, "text": "AIRFIELD TAKEOFF 1. Align the aircraft"},
                {"doc": 0, "page": 101, "text": "5. At rotation speed, pull 6 to 8 degrees"},
                {"doc": 1, "page": 1, "text": "autre document"}]
    monkeypatch.setattr(m, "search", lambda q, a, k: [(0.9, m.chunks[0], m.docs[0]), (0.8, m.chunks[1], m.docs[0])])
    pages = [c["page"] for _s, c, _d in m.procedure("Checklist décollage", "F/A-18C Hornet")]
    assert pages == [100, 101]                        # piste : la suite du titre, sans le porte-avions ni l'autre PDF
    pages = [c["page"] for _s, c, _d in m.procedure("Checklist catapultage porte-avions", "F/A-18C Hornet")]
    assert pages[0] == 99


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


def test_modele_muet_passe_au_secours(monkeypatch):
    import requests
    calls, waits = [], []

    def fake_post(url, json, headers, stream, timeout):
        calls.append(json["model"])
        waits.append(timeout[1])
        if json["model"] == "muet":
            raise requests.exceptions.ReadTimeout("rien depuis 45 s")
        return types.SimpleNamespace(status_code=200, text="", headers={}, close=lambda: None)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm.LLMClient, "_read", lambda self, r: iter(["ok"]))
    monkeypatch.setattr(llm, "_exhausted", {})
    client = llm.LLMClient({"provider": "groq", "timeout": 120, "providers": {"groq": {
        "base_url": "http://x", "api_key": "k", "model": "muet", "fallback_model": "secours"}}})
    assert "".join(client.stream([], fast=True)) == "ok"
    assert calls[0] == "muet" and calls[-1] == "secours" and set(waits) == {45}   # en vol : 45 s, pas 120
    client2 = llm.LLMClient({"provider": "groq", "timeout": 120, "providers": {"groq": {
        "base_url": "http://x", "api_key": "k", "model": "muet"}}})
    try:
        "".join(client2.stream([]))
        raise AssertionError("aucune erreur levée")
    except llm.LLMError as e:
        assert e.kind == "timeout" and waits[-1] == 120        # sans secours : erreur claire, délai des réglages


def test_verification_des_mises_a_jour(monkeypatch):
    import sys

    from dcs_copilote import app
    newer = types.SimpleNamespace(TargetFullRelease=types.SimpleNamespace(Version="1.2.0"))

    class Manager:
        def __init__(self, source):
            if state["installed"] is False:
                raise RuntimeError("This application is not properly installed")

        def get_current_version(self):
            return "1.1.0"

        def check_for_updates(self):
            return state["update"]
    monkeypatch.setitem(sys.modules, "velopack", types.SimpleNamespace(UpdateManager=Manager, GithubSource=str))
    asked, said = [], []
    fake = types.SimpleNamespace(_ask=lambda text, *a, **k: asked.append(text))
    for state in ({"installed": False, "update": None}, {"installed": True, "update": None},
                  {"installed": True, "update": newer}):
        app.App._check_update(fake, report=said.append)
    assert "développement" in said[0] and said[1] == "Tu as la dernière version (1.1.0)."
    assert said[2] == "Nouvelle version 1.2.0 disponible." and "1.2.0" in asked[0] and "1.1.0" in asked[0]


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


def test_enregistrements_simultanes_sans_perte_ni_corruption(tmp_path):
    import json
    import threading
    path = str(tmp_path / "config.json")
    config.save(path, config.load(path))
    keys = [f"k{i}" for i in range(20)]
    threads = [threading.Thread(target=config.update, args=(path, lambda d, k=k: d["dcs"].update({k: k})))
               for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with open(path, encoding="utf-8") as f:
        assert set(keys) <= set(json.load(f)["dcs"])    # fichier lisible, aucune modification perdue


def test_coupure_reseau_pendant_la_reponse_devient_une_erreur_claire():
    import requests

    class CutResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"Train "}}]}'
            raise requests.exceptions.ChunkedEncodingError("connexion perdue")

    client = llm.LLMClient({"provider": "groq", "providers": {"groq": {"base_url": "http://x", "api_key": "k",
                                                                       "model": "m"}}})
    got = []
    try:
        for part in client._read(CutResponse()):
            got.append(part)
        raise AssertionError("aucune erreur levée")
    except llm.LLMError as e:
        assert got == ["Train "] and "coupée" in str(e)


def test_statut_du_script_dcs(tmp_path):
    assert dcs_install.status(str(tmp_path), 7779) == "missing"
    dcs_install.install(str(tmp_path), 7779)
    assert dcs_install.status(str(tmp_path), 7779) == "ok"
    assert dcs_install.status(str(tmp_path), 7780) == "outdated"


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


def test_guides_gardes_si_dcs_introuvable(tmp_path, monkeypatch):
    from dcs_copilote import app, chucks
    monkeypatch.setattr(chucks, "load_state", lambda: {"guides": GUIDES, "files": {"fa-18c": {}, "m-2000c": {}}})
    cfg = {"manuals": {"dcs_dir": str(tmp_path / "disque-debranche"), "chucks": {"aircraft": ["f-16cm"]}}}
    fake = types.SimpleNamespace(cfg=cfg)
    assert app.App.chucks_selection(fake) == ["f-16cm", "fa-18c", "m-2000c"]   # rien à effacer
    (tmp_path / "dcs" / "Mods" / "aircraft" / "FA-18C").mkdir(parents=True)
    cfg["manuals"]["dcs_dir"] = str(tmp_path / "dcs")
    assert app.App.chucks_selection(fake) == ["f-16cm", "fa-18c"]              # DCS là : le Mirage n'y est plus


def test_guides_reessayes_vite_si_l_indexation_est_occupee(monkeypatch):
    import threading

    from dcs_copilote import app, chucks
    monkeypatch.setattr(chucks, "load_state", lambda: {"last_check": 0})
    sleeps = []

    class Stop(Exception):
        pass

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:
            raise Stop
    monkeypatch.setattr(app.time, "sleep", fake_sleep)
    updates = []
    fake = types.SimpleNamespace(cfg={"manuals": {"chucks": {"enabled": True}}}, _chucks_lock=threading.Lock(),
                                 update_chucks=lambda background: updates.append(1))
    fake._chucks_lock.acquire()                       # indexation des manuels officiels en cours
    try:
        app.App._chucks_loop(fake)
    except Stop:
        pass
    assert sleeps == [20, 300] and updates == []      # nouvel essai dans 5 min, pas dans 6 h
    fake._chucks_lock.release()
    sleeps.clear()
    try:
        app.App._chucks_loop(fake)
    except Stop:
        pass
    assert updates == [1] and sleeps == [20, 6 * 3600]


# ------------------------------------------------------------------ voix, checklist
def test_decoupage_des_phrases_pour_la_voix():
    s = SentenceSplitter()
    assert s.feed("Master Arm sur ARM. On est parés pour le tir, ") == ["Master Arm sur ARM."]
    assert s.feed("vise la cible. Tir") == ["On est parés pour le tir, vise la cible."]
    assert s.flush() == ["Tir"]


def test_repete_relit_la_derniere_reponse_sans_l_ia():
    from dcs_copilote import app
    calls = []
    fake = types.SimpleNamespace(checklist=None, last_answer="Train sorti, trois vertes.",
                                 repeat=lambda: calls.append("repeat"), _ready=lambda: None,
                                 _answer=lambda *a: calls.append("ia"), _start_checklist=lambda *a: calls.append("cl"))
    for q in ("Répète.", "Redis ?", "Quoi ?"):
        app.App._route(fake, q, "vol", True, 1)
    app.App._route(fake, "Répète la procédure de démarrage moteur s'il te plaît", "vol", True, 1)
    assert calls == ["repeat", "repeat", "repeat", "ia"]      # une vraie question part toujours à l'IA
    fake.last_answer = ""
    app.App._route(fake, "Répète", "vol", True, 1)
    assert calls[-1] == "ia"                                  # rien à répéter : question normale


def test_checklist():
    assert cl.parse_steps("1. Batterie ON\n2. APU ON\n3) Moteur droit") == ["Batterie ON", "APU ON", "Moteur droit"]
    assert [cl.command(x) for x in ("suivant", "répète", "précédent", "stop", "bonjour")] == \
        ["next", "repeat", "prev", "stop", None]
    assert cl.is_start("lance la procédure de démarrage") and not cl.is_start("comment ça va")
