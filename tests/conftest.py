import pytest


@pytest.fixture(autouse=True)
def _pas_de_fichiers_reels(tmp_path, monkeypatch):
    """Aucun test n'écrit dans les vrais fichiers de l'utilisateur (suivi de consommation, paramètres appris)."""
    from dcs_copilote import llm
    monkeypatch.setattr(llm, "USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(llm, "_ACCEPTED_PATH", str(tmp_path / "llm_params.json"))
