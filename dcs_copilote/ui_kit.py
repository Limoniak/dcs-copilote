"""Thème, icônes et petits widgets Qt partagés par l'overlay et la fenêtre Réglages."""
import os
import sys

# Linux/Wayland : passer par XWayland pour garder « toujours au premier plan » et le positionnement
# (impossibles pour une appli sous Wayland natif). DCS (Proton) et pynput sont de toute façon sous X.
if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import (QByteArray, QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, Qt,  # noqa: E402
                            QVariantAnimation, Signal, Property)
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette,  # noqa: E402
                           QPen, QPixmap)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox,  # noqa: E402
                               QSlider, QWidget)

# --------------------------------------------------------------------------- couleurs
BASE = "#0e1116"
SURFACE = "#151a21"
RAISED = "#1c232c"
BORDER = "#29313c"
TEXT = "#e8edf3"
MUTED = "#8b95a4"
ACCENT = "#3d8bff"           # bleu électrique du logo
ACCENT_HOVER = "#6aa6ff"
READY = "#34d399"            # voyant « prêt » et messages de réussite : restent verts
LISTEN = "#fb7185"
BUSY = "#fbbf24"
ERROR = "#f87171"

STATE_COLORS = {"idle": MUTED, "ready": READY, "listen": LISTEN, "busy": BUSY, "error": ERROR}

FONT_CANDIDATES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Cantarell",
                   "Noto Sans", "Ubuntu", "DejaVu Sans"]


def ui_family():
    families = set(QFontDatabase.families())
    return next((f for f in FONT_CANDIDATES if f in families), QApplication.font().family())


def application():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
        app.setApplicationName("DCS Copilote")
        app.setQuitOnLastWindowClosed(False)
        f = QFont(ui_family())
        f.setPointSizeF(10)
        f.setHintingPreference(QFont.PreferNoHinting)
        app.setFont(f)
        app.setStyle("Fusion")
        pal = app.palette()
        pal.setColor(QPalette.Link, QColor(ACCENT))
        app.setPalette(pal)
        app.setStyleSheet(STYLESHEET + _arrow_rule())
        app.setWindowIcon(logo())
        app._wheel_guard = _WheelGuard(app)
        app.installEventFilter(app._wheel_guard)
    return app


class _WheelGuard(QObject):
    """La molette fait défiler la page au lieu de modifier listes, compteurs et curseurs survolés."""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel or not isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)):
            return False
        # un événement renvoyé à la main ne remonte pas tout seul : on vise la zone défilante parente
        p = obj.parentWidget()
        while p and not isinstance(p, QAbstractScrollArea):
            p = p.parentWidget()
        if p:
            QApplication.sendEvent(p.viewport(), event)
        return True


def single_instance():
    """Verrou : une seule instance (deux se disputeraient le micro, le PTT et le port UDP).
    Renvoie le verrou à garder vivant, ou None si l'appli tourne déjà."""
    import tempfile

    from PySide6.QtCore import QLockFile
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "dcs-copilote.lock"))
    lock.setStaleLockTime(0)
    return lock if lock.tryLock(200) else None


# --------------------------------------------------------------------------- icônes (style Lucide)
_ICONS = {
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "bell": ('<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74'
             '-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>'),
    "reset": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "settings": ('<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3"/>'
                 '<path d="M14 2v4M8 10v4M16 18v4"/>'),
    "volume": '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/>',
    "volume-off": '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
    "book": ('<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5'
             'a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>'),
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "sparkles": ('<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>'
                 '<path d="M19 3v4M21 5h-4"/>'),
    "voice": ('<path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/><path d="M14 8v7"/>'
              '<path d="M18 5v13"/><path d="M22 10v3"/>'),
    "headphones": ('<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7'
                   'a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>'),
    "mic": ('<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>'
            '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>'),
    "plane": ('<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1'
              '-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3'
              'l.5-.2c.4-.3.6-.7.5-1.2z"/>'),
    "monitor": '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "refresh": ('<path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'
                '<path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/>'),
}


def icon(name, color=MUTED, size=18, stroke=2.0):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)


LOGO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo")


def logo(name="icone", sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Logo de l'appli (assets/logo/<name>.svg) en icône multi-tailles."""
    renderer = QSvgRenderer(os.path.join(LOGO_DIR, name + ".svg"))
    ic = QIcon()
    for s in sizes:
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        ic.addPixmap(pm)
    return ic


def paint_card(widget, margin, radius=16, alpha=1.0, color=BASE):
    """Fond de fenêtre sans bordure : carte arrondie + ombre douce dans la marge transparente."""
    if alpha <= 0:          # fond invisible : texte seul, sans ombre ni bordure
        return
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    card = QRectF(widget.rect()).adjusted(margin, margin, -margin, -margin)
    p.setPen(Qt.NoPen)
    for i in range(margin, 0, -2):
        p.setBrush(QColor(0, 0, 0, int(4 + (margin - i) * 1.6)))
        p.drawRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), radius + i, radius + i)
    path = QPainterPath()
    path.addRoundedRect(card, radius, radius)
    bg = QColor(color)
    bg.setAlphaF(max(0.0, min(1.0, float(alpha))))
    p.fillPath(path, bg)
    p.setPen(QPen(QColor(255, 255, 255, 22), 1))
    p.drawPath(path)


# --------------------------------------------------------------------------- feuille de style
STYLESHEET = f"""
* {{ color: {TEXT}; outline: none; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[h1="true"] {{ font-size: 18pt; font-weight: 600; }}
QLabel[h2="true"] {{ font-size: 11pt; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 10px; selection-background-color: {ACCENT}; selection-color: {BASE};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #3a4452; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {MUTED}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
    selection-background-color: #24453a; selection-color: {TEXT};
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none;
}}

QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 14px; font-weight: 500;
}}
QPushButton:hover {{ background: #232b35; border-color: #3a4452; }}
QPushButton:pressed {{ background: #1a2028; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; }}
QPushButton#primary {{ background: {ACCENT}; color: {BASE}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; }}
QPushButton#ghost:hover {{ color: {TEXT}; background: {RAISED}; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#vdivider {{ background: {BORDER}; max-width: 1px; min-width: 1px; border: none; }}
QPushButton#nav {{
    background: transparent; border: none; border-radius: 9px; padding: 9px 12px; text-align: left; color: {MUTED};
}}
QPushButton#nav:hover {{ background: rgba(255,255,255,0.05); color: {TEXT}; }}
QPushButton#nav:checked {{ background: rgba(61,139,255,0.14); color: {ACCENT}; }}
QPushButton#tile {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 10px; padding: 10px 12px; text-align: left;
}}
QPushButton#tile:hover {{ border-color: #3a4452; }}
QPushButton#tile:checked {{ border: 1px solid {ACCENT}; background: rgba(61,139,255,0.12); color: {ACCENT}; }}
QFrame#seg {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 9px; }}
QPushButton#segbtn {{ background: transparent; border: none; border-radius: 7px; padding: 6px 14px; color: {MUTED}; }}
QPushButton#segbtn:hover {{ color: {TEXT}; }}
QPushButton#segbtn:checked {{ background: #2c3541; color: {TEXT}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QPushButton#chip {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 12px; padding: 3px 10px;
    color: {MUTED}; font-size: 9pt;
}}
QPushButton#chip:hover {{ color: {TEXT}; border-color: #3a4452; background: rgba(255,255,255,0.04); }}
QToolButton#mic {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 18px; }}
QToolButton#mic:hover {{ border-color: #3a4452; }}
QToolButton#mic:pressed {{ background: rgba(251,113,133,0.25); border-color: {LISTEN}; }}
QListWidget {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 9px; padding: 4px; }}
QListWidget::item {{ padding: 4px 6px; border-radius: 6px; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.05); }}
QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a4452; background: {SURFACE}; }}
QListWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QProgressBar {{ background: {RAISED}; border: none; border-radius: 4px; max-height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QMenu {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #24453a; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 4px; }}
QToolButton:hover {{ background: rgba(255,255,255,0.07); }}
QToolButton:pressed {{ background: rgba(255,255,255,0.12); }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.14); border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.25); }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    height: 0; background: none;
}}
"""


def _arrow_rule():
    """Flèche des listes déroulantes : les feuilles de style Qt n'acceptent qu'un fichier image."""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "dcs_copilote_chevron.svg")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{MUTED}" '
           f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">{_ICONS["chevron-down"]}</svg>')
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    except OSError:
        return ""
    url = path.replace(os.sep, "/")
    return f"\nQComboBox::down-arrow {{ image: url({url}); width: 12px; height: 12px; }}\n"


# --------------------------------------------------------------------------- thread → interface
class Bridge(QObject):
    """Exécute des fonctions dans le thread de l'interface (appelable depuis n'importe quel thread)."""
    run = Signal(object)

    def __init__(self):
        super().__init__()
        self.run.connect(lambda fn: fn(), Qt.QueuedConnection)


# --------------------------------------------------------------------------- widgets
class Toggle(QAbstractButton):
    """Interrupteur animé."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._t = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"t", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_t(self):
        return self._t

    def _set_t(self, v):
        self._t = v
        self.update()

    t = Property(float, _get_t, _set_t)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(BORDER), QColor(ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._t),
            int(off.green() + (on.green() - off.green()) * self._t),
            int(off.blue() + (on.blue() - off.blue()) * self._t))
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        d = self.height() - 6
        x = 3 + (self.width() - d - 6) * self._t
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 3, d, d))


class StatusDot(QWidget):
    """Pastille d'état ; pulse pendant l'écoute et la réflexion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.color = QColor(MUTED)
        self._phase = 0.0
        self._pulse = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1100, loopCount=-1)
        self._pulse.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._phase = v
        self.update()

    def set_state(self, state):
        self.color = QColor(STATE_COLORS.get(state, MUTED))
        if state in ("listen", "busy"):
            self._pulse.start()
        else:
            self._pulse.stop()
            self._phase = 0.0
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        if self._phase:
            halo = QColor(self.color)
            halo.setAlphaF(0.45 * (1 - self._phase))
            r = 4 + 5 * self._phase
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(c, r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(c, 4, 4)
