"""Render the actual Qt widgets with safe sample data for the project website.

Run from the repository root with PySide6-Essentials, numpy, requests and sounddevice
installed. No microphone recording, DCS session, API call or user configuration is used.
The screenshots intentionally preserve the application's current French UI.
"""
import copy
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from dcs_copilote import config, ui_kit  # noqa: E402
from dcs_copilote.overlay import Overlay  # noqa: E402
from dcs_copilote.settings import SettingsWindow  # noqa: E402


def settle(app):
    loop = QEventLoop()
    QTimer.singleShot(150, loop.quit)
    loop.exec()
    app.processEvents()


def main():
    output = ROOT / "docs" / "assets" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    family = ui_kit.ui_family

    def load_capture_fonts():
        # Windows' offscreen Qt plugin does not enumerate system fonts itself.
        if sys.platform == "win32" and not QFontDatabase.families():
            for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
                QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name))
        return family()

    with patch.object(ui_kit, "ui_family", load_capture_fonts):
        app = ui_kit.application()
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["overlay"].update(width=760, font_size=13, alpha=1.0, lines=10)
    noop = lambda *args: None

    # The tray is unrelated to the captured widget and unavailable offscreen.
    with patch.object(Overlay, "_setup_tray", noop):
        overlay = Overlay(cfg["overlay"], ["F/A-18C Hornet", "F-16C Viper"],
                          noop, noop, noop, noop, noop)
    overlay.show()
    overlay.set_aircraft("F/A-18C Hornet", True)
    overlay.start_answer("Comment peux-tu m'aider à apprendre le Hornet ?")
    overlay.append("Garde les mains sur le HOTAS. Pose ta question sur l'appareil, lance une checklist "
                   "ou demande une explication détaillée une fois au sol.\n\n"
                   "Avec tes manuels indexés, je peux retrouver les passages utiles et afficher leurs sources.")
    overlay.show_actions(True)
    overlay.set_status("Prêt · bouton PTT : Scroll Lock", state="ready")
    settle(app)
    overlay.grab().save(str(output / "overlay.png"))

    overlay.show_checklist("Briefing avant vol · exemple", [
        "Consulter la checklist publiée du module.",
        "Confirmer la mission et la configuration de l'appareil.",
        "Vérifier la navigation et préparer le départ.",
        "Préparer le retour et le terrain de déroutement.",
        "Poursuivre avec les procédures normales du module.",
    ], 1)
    settle(app)
    overlay.grab().save(str(output / "checklist.png"))

    stub = SimpleNamespace(cfg=cfg, overlay=overlay, ptt=None,
                           manuals=SimpleNamespace(ready=False),
                           current_aircraft=lambda: "F/A-18C Hornet")
    # Disable automatic catalogue fetching; all fields use repository defaults.
    with patch.object(SettingsWindow, "_bg", noop):
        settings = SettingsWindow(stub)
    settings.resize(1080, 800)
    settle(app)
    settings.grab().save(str(output / "settings-ai.png"))
    settings.nav.button(3).setChecked(True)
    settings.stack.setCurrentIndex(3)
    settle(app)
    settings.grab().save(str(output / "settings-voice.png"))
    settings.hide()
    overlay.hide()
    print("Rendered actual application widgets with sample content:")
    for path in output.glob("*.png"):
        print(f"  {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
