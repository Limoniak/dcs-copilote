"""Fabrique build/icon.ico (avion vert sur fond sombre arrondi) pour l'exécutable Windows."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402

from dcs_copilote import ui_kit  # noqa: E402

ui_kit.application()
size = 256
pm = QPixmap(size, size)
pm.fill(Qt.transparent)
p = QPainter(pm)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(Qt.NoPen)
p.setBrush(QColor(ui_kit.BASE))
p.drawRoundedRect(QRectF(8, 8, size - 16, size - 16), 52, 52)
plane = ui_kit.icon("plane", ui_kit.ACCENT, 160, stroke=2.2).pixmap(160, 160)
p.drawPixmap(48, 48, 160, 160, plane)
p.end()
out = os.path.join(HERE, "icon.ico")
if not pm.save(out, "ICO"):
    pm.save(os.path.join(HERE, "icon.png"), "PNG")
    sys.exit("format ICO indisponible : icon.png écrit à la place")
print("icône :", out)
