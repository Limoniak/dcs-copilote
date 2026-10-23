"""Bouton push-to-talk clavier (sans vrai clavier : touches simulées)."""
import pytest

keyboard = pytest.importorskip("pynput.keyboard")

from dcs_copilote import ptt  # noqa: E402


def test_touche_avec_maj_relachee_sous_un_autre_caractere():
    events = []
    p = ptt.make_ptt({"type": "keyboard", "key": "?"}, lambda: events.append("press"),
                     lambda: events.append("release"), print)
    p._press(keyboard.KeyCode(char="?", vk=188))
    p._press(keyboard.KeyCode(char="?", vk=188))          # auto-répétition ignorée
    p._release(keyboard.KeyCode(char=",", vk=188))        # Maj déjà relâchée : même touche, autre caractère
    assert events == ["press", "release"]


def test_une_erreur_de_l_appli_ne_tue_pas_l_ecoute():
    def broken():
        raise RuntimeError("micro débranché")
    p = ptt.make_ptt({"type": "keyboard", "key": "$"}, broken, broken, print)
    p._press(keyboard.KeyCode(char="$", vk=186))          # ne lève rien : pynput garderait son écouteur
    p._release(keyboard.KeyCode(char="$", vk=186))
    assert p.down is False
