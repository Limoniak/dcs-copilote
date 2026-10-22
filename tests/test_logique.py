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
