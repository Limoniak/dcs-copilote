"""Fabrique build/icon.ico (le logo assets/logo/icone.svg) pour l'exécutable Windows."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from dcs_copilote import ui_kit  # noqa: E402

ui_kit.application()
size = 256
pm = QPixmap(size, size)
pm.fill(Qt.transparent)
p = QPainter(pm)
QSvgRenderer(os.path.join(ui_kit.LOGO_DIR, "icone.svg")).render(p)
p.end()
out = os.path.join(HERE, "icon.ico")
if not pm.save(out, "ICO"):
    pm.save(os.path.join(HERE, "icon.png"), "PNG")
    sys.exit("format ICO indisponible : icon.png écrit à la place")
print("icône :", out)
