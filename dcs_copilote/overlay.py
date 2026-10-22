"""Overlay toujours au premier plan : appareil, réponse en direct, question tapée, état."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy,
                               QSystemTrayIcon, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import ui_kit as ui
from .ui_kit import ACCENT, ERROR, LISTEN, MUTED, TEXT, icon

MARGIN = 14          # place pour l'ombre autour de la carte
RADIUS = 16


class ElideLabel(QLabel):
    """Label sur une ligne, tronqué avec « … » (texte complet en infobulle)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full(self, text):
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def _refresh(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(10, self.width())))


class DragArea(QWidget):
    """Zone qui déplace la fenêtre (déplacement natif : fonctionne aussi sous XWayland)."""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag = e.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None


class Overlay(QWidget):
    def __init__(self, cfg, choices, on_question, on_reset, on_aircraft, on_settings, on_quit,
                 on_speak=None, speak=False):
        self.qapp = ui.application()
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.cfg = cfg
        self.on_question, self.on_reset = on_question, on_reset
        self.on_aircraft, self.on_settings, self.on_quit = on_aircraft, on_settings, on_quit
        self.on_speak = on_speak or (lambda on: None)
        # branchés par l'appli après construction
        self.on_mic_press = self.on_mic_release = self.on_repeat = self.on_more = lambda: None
        self.collapsed = False
        self._closing = False
        self._answer = ""
        self._dirty = False
        self._bridge = ui.Bridge()

        self.setWindowTitle("DCS Copilote")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN + 12, MARGIN + 10, MARGIN + 12, MARGIN + 10)
        root.setSpacing(8)

        # ---- en-tête (déplaçable) : appareil + boutons
        head = DragArea()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(2)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.addItems(choices)
        self.combo.setCurrentIndex(-1)
        self.combo.lineEdit().setPlaceholderText("Appareil ?")
        self.combo.setMinimumWidth(200)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.textActivated.connect(self.on_aircraft)
        self.combo.lineEdit().returnPressed.connect(lambda: self.on_aircraft(self.combo.currentText()))
        hl.addWidget(self.combo)
        hl.addStretch(1)
        # lecture à voix haute des réponses aux questions tapées (les questions vocales sont toujours lues)
        self.btn_speak = QToolButton()
        self.btn_speak.setCheckable(True)
        self.btn_speak.setCursor(Qt.PointingHandCursor)
        self._set_speak_icon(speak)
        self.btn_speak.setChecked(speak)
        self.btn_speak.toggled.connect(self._speak_toggled)     # après : pas de déclenchement au démarrage
        hl.addWidget(self.btn_speak)
        self.btn_collapse = None
        for name, tip, cb in (("settings", "Réglages", self.on_settings),
                              ("reset", "Nouvelle conversation", self.on_reset),
                              ("chevron-up", "Réduire", self.toggle),
                              ("x", "Quitter", self.on_quit)):
            b = QToolButton()
            b.setIcon(icon(name, MUTED, 16))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            hl.addWidget(b)
            if name == "chevron-up":
                self.btn_collapse = b
        root.addWidget(head)

        # ---- corps : question, réponse, message, saisie
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 2, 0, 0)
        bl.setSpacing(6)
        self.question = QLabel()
        self.question.setWordWrap(True)
        self.question.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.question.hide()
        bl.addWidget(self.question)

        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setFrameShape(QTextBrowser.NoFrame)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.text.document().setDocumentMargin(0)
        self.text.hide()
        bl.addWidget(self.text)

        self.notice = QLabel("Maintiens ton bouton push-to-talk (ou le micro ci-dessous) et pose ta question.")
        self.notice.setWordWrap(True)
        bl.addWidget(self.notice)

        # actions sur la dernière réponse
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        for name, text, tip, cb in (("volume", "Répéter", "Relire la dernière réponse", lambda: self.on_repeat()),
                                    ("plus", "Détailler", "Réponse complète et détaillée", lambda: self.on_more())):
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setIcon(icon(name, MUTED, 13))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            al.addWidget(b)
        al.addStretch(1)
        self.actions.hide()
        bl.addWidget(self.actions)

        irow = QHBoxLayout()
        irow.setSpacing(6)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Pose une question à l'instructeur…")
        self.entry.setClearButtonEnabled(False)
        send = self.entry.addAction(icon("send", ACCENT, 16), QLineEdit.TrailingPosition)
        send.triggered.connect(self._submit)
        self.entry.returnPressed.connect(self._submit)
        irow.addWidget(self.entry, 1)
        self.mic = QToolButton()
        self.mic.setObjectName("mic")
        self.mic.setIcon(icon("mic", TEXT, 17))
        self.mic.setFixedSize(36, 36)
        self.mic.setCursor(Qt.PointingHandCursor)
        self.mic.setToolTip("Maintiens pour parler")
        self.mic.pressed.connect(lambda: (self.mic.setIcon(icon("mic", LISTEN, 17)), self.on_mic_press()))
        self.mic.released.connect(lambda: (self.mic.setIcon(icon("mic", TEXT, 17)), self.on_mic_release()))
        irow.addWidget(self.mic)
        bl.addLayout(irow)
        root.addWidget(self.body)

        # ---- barre d'état (reste visible en mode réduit)
        st = QHBoxLayout()
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(4)
        self.dot = ui.StatusDot()
        self.status = ElideLabel()
        self.status.setProperty("muted", True)
        st.addWidget(self.dot)
        st.addWidget(self.status, 1)
        root.addLayout(st)

        self._render_timer = QTimer(self, interval=60, timeout=self._render)
        self._render_timer.start()
        self.apply_style(cfg)
        self.set_status("Démarrage…", state="idle")
        self._setup_tray()
        self.move(int(cfg["x"]), int(cfg["y"]))

    # ------------------------------------------------------------------ dessin
    def paintEvent(self, _e):
        ui.paint_card(self, MARGIN, RADIUS, self.cfg["alpha"])

    # ------------------------------------------------------------------ interne
    def _submit(self):
        txt = self.entry.text().strip()
        if txt:
            self.entry.clear()
            self.on_question(txt)

    def _setup_tray(self):
        """Icône de la zone de notification : afficher / masquer l'overlay sans quitter."""
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon("plane", ACCENT, 32), self)
        self.tray.setToolTip("DCS Copilote")
        menu = QMenu()
        self._tray_toggle = menu.addAction("Masquer l'overlay", self.toggle_visible)
        menu.addAction("Réglages…", self.on_settings)
        menu.addSeparator()
        menu.addAction("Quitter", self.on_quit)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.toggle_visible())
        self.tray.show()

    def toggle_visible(self):
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.raise_()
        if self.tray:
            self._tray_toggle.setText("Masquer l'overlay" if self.isVisible() else "Afficher l'overlay")

    def show_actions(self, on):
        self._call(lambda: (self.actions.setVisible(on), self.adjustSize()))

    def _set_speak_icon(self, on):
        self.btn_speak.setIcon(icon("volume" if on else "volume-off", ACCENT if on else MUTED, 16))
        self.btn_speak.setToolTip("Questions tapées : réponse lue à voix haute" if on else
                                  "Questions tapées : réponse non lue (clique pour l'entendre)")

    def _speak_toggled(self, on):
        self._set_speak_icon(on)
        self.on_speak(on)

    def set_speak(self, on):
        def f():
            self.btn_speak.blockSignals(True)
            self.btn_speak.setChecked(on)
            self.btn_speak.blockSignals(False)
            self._set_speak_icon(on)
        self._call(f)

    def _call(self, fn):
        self._bridge.run.emit(fn)

    def _render(self):
        if not self._dirty:
            return
        self._dirty = False
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.text.setMarkdown(self._answer)
        self.text.setVisible(bool(self._answer.strip()))
        self._fit()
        if at_end:
            bar.setValue(bar.maximum())

    def _fit(self):
        """La zone de réponse grandit avec le texte jusqu'à « lines » lignes, puis défile."""
        doc = self.text.document()
        doc.setTextWidth(self.text.viewport().width() or (int(self.cfg["width"]) - 24))
        line = self.text.fontMetrics().lineSpacing()
        h = int(doc.size().height()) + 4
        self.text.setFixedHeight(max(line + 4, min(h, line * int(self.cfg["lines"]) + 4)))
        self.adjustSize()

    def _notice(self, text, color):
        self.notice.setText(text)
        self.notice.setStyleSheet(f"color: {color};")
        self.notice.setVisible(bool(text))
        self.adjustSize()

    # ------------------------------------------------------------------ API (appelable depuis tout thread)
    def apply_style(self, cfg):
        self.cfg = cfg
        fs = float(cfg["font_size"])
        f = QFont(self.font())
        f.setPointSizeF(fs)
        self.text.setFont(f)
        self.text.document().setDefaultFont(f)
        self.entry.setFont(f)
        self.question.setStyleSheet(f"color: {ACCENT}; font-weight: 600; font-size: {fs}pt;")
        self.notice.setStyleSheet(f"color: {MUTED};")
        self.setFixedWidth(int(cfg["width"]) + 2 * MARGIN)
        self._dirty = True
        self.update()

    def toggle(self):
        self.collapsed = not self.collapsed
        self.body.setVisible(not self.collapsed)
        self.btn_collapse.setIcon(icon("chevron-down" if self.collapsed else "chevron-up", MUTED, 16))
        self.btn_collapse.setToolTip("Déplier" if self.collapsed else "Réduire")
        QTimer.singleShot(0, self.adjustSize)

    def set_status(self, text, error=False, state=None):
        def f():
            self.status.set_full(text)
            self.status.setStyleSheet(f"color: {ERROR if error else MUTED};")
            if error or state:
                self.dot.set_state("error" if error else state)
        self._call(f)

    def set_aircraft(self, name, locked):
        def f():
            # changement par l'appli (DCS) : ne pas le faire passer pour un choix du pilote
            for w in (self.combo, self.combo.lineEdit()):
                w.blockSignals(True)
            self.combo.setEditText(name or "")
            self.combo.setEnabled(not locked)
            for w in (self.combo, self.combo.lineEdit()):
                w.blockSignals(False)
        self._call(f)

    def start_answer(self, question):
        def f():
            self.question.setText(question)
            self.question.show()
            self.actions.hide()
            self._answer = ""
            self._dirty = True
            self._notice("", MUTED)
        self._call(f)

    def append(self, text, tag=None):
        def f():
            if tag == "err":
                self._notice("⚠ " + text.strip(), ERROR)
            elif tag == "info":
                self._notice(text.strip(), MUTED)
            else:
                self._answer += text
                self._dirty = True
        self._call(f)

    def info(self, text):
        self.append(text, "info")

    def later(self, ms, fn):
        self._call(lambda: QTimer.singleShot(ms, fn))

    def hide_window(self):
        self._call(self.hide)

    def position(self):
        return self.x(), self.y()

    def mainloop(self):
        self.show()
        self.setFocus()                      # pas de champ actif au démarrage
        self.qapp.exec()

    def closeEvent(self, e):
        if self._closing:
            e.accept()
        else:
            e.ignore()
            self.on_quit()

    def close(self):
        self._closing = True
        if getattr(self, "tray", None):
            self.tray.hide()
        super().close()
        self.qapp.quit()

