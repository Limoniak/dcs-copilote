"""Tests de l'interface Qt, sans écran (QT_QPA_PLATFORM=offscreen)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QScrollArea, QSpinBox, QVBoxLayout, QWidget  # noqa: E402

from dcs_copilote import config, ui_kit  # noqa: E402
from dcs_copilote.settings import describe_model, model_kind, sort_models  # noqa: E402

APP = ui_kit.application()


def test_molette_fait_defiler_sans_changer_les_valeurs():
    area, page = QScrollArea(), QWidget()
    lay = QVBoxLayout(page)
    spin = QSpinBox()
    spin.setValue(5)
    lay.addWidget(spin)
    for _ in range(40):
        lay.addWidget(QSpinBox())
    area.setWidget(page)
    area.resize(200, 200)
    area.show()
    APP.processEvents()
    APP.sendEvent(spin, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120), Qt.NoButton,
                                    Qt.NoModifier, Qt.NoScrollPhase, False))
    assert spin.value() == 5 and area.verticalScrollBar().value() > 0


def test_logo_present():
    assert not ui_kit.logo().isNull() and not ui_kit.logo("marque").isNull()


def test_poignee_redimensionne_et_memorise():
    from dcs_copilote.overlay import Overlay
    n = lambda *a: None  # noqa: E731
    ov = Overlay(config.load("absent.json")["overlay"], ["F-16C"], n, n, n, n, n)
    saved = []
    ov.on_resized = saved.append
    ov.show()
    APP.processEvents()
    line = ov.text.fontMetrics().lineSpacing()
    g, start = ov.grip, ov.grip.mapToGlobal(QPoint(8, 8))
    for kind, pos in ((QMouseEvent.Type.MouseButtonPress, start),
                      (QMouseEvent.Type.MouseMove, start + QPoint(100, -8 * line)),
                      (QMouseEvent.Type.MouseButtonRelease, start + QPoint(100, -8 * line))):
        g.event(QMouseEvent(kind, QPointF(8, 8), QPointF(pos), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert saved == [{"width": 660, "lines": 4}]


def test_classement_des_modeles():
    assert [model_kind(m) for m in ("whisper-large-v3", "gemini-2.5-flash", "o3", "gpt-4.1")] == \
        ["other", "fast", "slow", "general"]
    groq = ["openai/gpt-oss-20b", "whisper-large-v3", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    assert sort_models("groq", groq)[0] == "openai/gpt-oss-120b" and "whisper-large-v3" not in sort_models("groq", groq)
    assert describe_model("groq", "llama-3.3-70b-versatile", set(groq)).startswith("Ce modèle n'est plus proposé")
