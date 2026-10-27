import sys

import pytest


def pytest_sessionfinish(session, exitstatus):
    """Fenêtres détruites avant l'arrêt de Python : sinon PySide les détruit avec la QApplication alors que le
    ramasse-miettes en a déjà défait une partie, et Python plante après « N passed » (code 1 sous Windows)."""
    if "PySide6.QtWidgets" not in sys.modules:
        return
    import shiboken6
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        if shiboken6.isValid(w):
            shiboken6.delete(w)


@pytest.fixture(autouse=True)
def _pas_de_fichiers_reels(tmp_path, monkeypatch):
    """Aucun test n'écrit dans les vrais fichiers de l'utilisateur (suivi de consommation, paramètres appris)."""
    from dcs_copilote import llm
    monkeypatch.setattr(llm, "USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(llm, "_ACCEPTED_PATH", str(tmp_path / "llm_params.json"))
