"""Overlay toujours au premier plan : appareil, réponse en direct, question tapée, état."""
import html

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
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
        if e.button() == Qt.LeftButton and not self.window().property("locked"):
            handle = self.window().windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag = e.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None


class Grip(QWidget):
    """Coin bas-droit : redimensionne l'overlay (largeur et hauteur maximale de la réponse)."""

    def __init__(self, overlay):
        super().__init__(overlay)
        self.ov = overlay
        self.setFixedSize(16, 16)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("Glisser pour redimensionner")
        self._start = None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(MUTED), 1.4))
        for k in (5, 9, 13):
            p.drawLine(QPointF(15, 15 - k), QPointF(15 - k, 15))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = (e.globalPosition(), int(self.ov.cfg["width"]), int(self.ov.cfg["lines"]))

    def mouseMoveEvent(self, e):
        if self._start is None:
            return
        pos, w, lines = self._start
        d = e.globalPosition() - pos
        line = self.ov.text.fontMetrics().lineSpacing()
        self.ov.cfg["width"] = max(280, min(1600, round((w + d.x()) / 10) * 10))
        self.ov.cfg["lines"] = max(1, min(50, lines + round(d.y() / line)))
        self.ov.setFixedWidth(int(self.ov.cfg["width"]) + 2 * MARGIN)
        self.ov.text.setVisible(True)
        self.ov._fit(full=True)

    def mouseReleaseEvent(self, _e):
        if self._start is not None:
            self._start = None
            self.ov.text.setVisible(bool(self.ov.text.toPlainText().strip()))
            self.ov._fit()
            self.ov.on_resized({"width": self.ov.cfg["width"], "lines": self.ov.cfg["lines"]})


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
        self.on_checklist = lambda cmd: None
        self.on_resized = lambda values: None
        self.collapsed = False
        self._closing = False
        self._answer = ""
        self._dirty = False
        self._bridge = ui.Bridge()

        self.setWindowTitle("DCS Copilote")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # réapparaître sans voler le focus à DCS
        self._user_hidden = False
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._auto_hide)

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN + 12, MARGIN + 10, MARGIN + 12, MARGIN + 10)
        root.setSpacing(8)

        # ---- en-tête (déplaçable) : appareil + boutons
        self.head = head = DragArea()
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
        self.text.setStyleSheet("QTextBrowser { background: transparent; border: none; "
                                "selection-background-color: rgba(61,139,255,0.35); selection-color: #e8edf3; }")
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

        # commandes du mode checklist
        self.cl_actions = QWidget()
        cl = QHBoxLayout(self.cl_actions)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        for name, text, tip, cmd in (("chevron-left", "Précédent", "Étape précédente", "prev"),
                                     ("volume", "Répéter", "Relire l'étape", "repeat"),
                                     ("chevron-right", "Suivant", "Étape suivante (ou appui bref sur le PTT)", "next"),
                                     ("x", "Terminer", "Quitter la checklist", "stop")):
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setIcon(icon(name, ACCENT if cmd == "next" else MUTED, 13))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, c=cmd: self.on_checklist(c))
            cl.addWidget(b)
        cl.addStretch(1)
        self.cl_actions.hide()
        bl.addWidget(self.cl_actions)

        self.inputs = QWidget()
        irow = QHBoxLayout(self.inputs)
        irow.setContentsMargins(0, 0, 0, 0)
        irow.setSpacing(6)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Une question pour ton copilote…")
        self.entry.setClearButtonEnabled(False)
        self.send = self.entry.addAction(icon("send", ACCENT, 16), QLineEdit.TrailingPosition)
        self.send.triggered.connect(self._submit)
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
        bl.addWidget(self.inputs)
        root.addWidget(self.body)

        # ---- barre d'état (reste visible en mode réduit)
        self.statusbar = QWidget()
        st = QHBoxLayout(self.statusbar)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(4)
        self.dot = ui.StatusDot()
        self.status = ElideLabel()
        self.status.setProperty("muted", True)
        st.addWidget(self.dot)
        st.addWidget(self.status, 1)
        root.addWidget(self.statusbar)

        self.grip = Grip(self)
        self._render_timer = QTimer(self, interval=60, timeout=self._render)
        self._render_timer.start()
        self.apply_style(cfg)
        self.set_status("Démarrage…", state="idle")
        self._setup_tray()
        self.move(int(cfg["x"]), int(cfg["y"]))

    # ------------------------------------------------------------------ dessin
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.grip.move(self.width() - MARGIN - 18, self.height() - MARGIN - 18)
        self.grip.raise_()

    def paintEvent(self, _e):
        ui.paint_card(self, MARGIN, RADIUS, self.cfg["alpha"], self.cfg.get("bg_color", ui.BASE))

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
        self.tray = QSystemTrayIcon(ui.logo(), self)
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

    def contextMenuEvent(self, e):
        """Clic droit : accès aux réglages même si l'en-tête est masqué."""
        menu = QMenu(self)
        menu.addAction("Réglages…", self.on_settings)
        menu.addAction("Masquer l'overlay", self.toggle_visible)
        menu.addSeparator()
        menu.addAction("Quitter", self.on_quit)
        menu.exec(e.globalPos())

    def _wake(self):
        """Activité (question, réponse, message) : réaffiche l'overlay masqué automatiquement."""
        if self._user_hidden:
            return
        if not self.isVisible():
            self.show()
        delay = int(self.cfg.get("autohide", 0))
        if delay:
            self._hide_timer.start(delay * 1000)
        else:
            self._hide_timer.stop()

    def _auto_hide(self):
        if self.entry.hasFocus() or self.mic.isDown():
            self._hide_timer.start(2000)        # question en cours de saisie : on attend
        else:
            self.hide()

    def toggle_visible(self):
        self._user_hidden = self.isVisible()
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.raise_()
        if self.tray:
            self._tray_toggle.setText("Masquer l'overlay" if self.isVisible() else "Afficher l'overlay")

    def show_checklist(self, title, steps, i):
        """Affiche la checklist : étapes faites (grisées), étape courante (en évidence), suivantes."""
        def f():
            n = len(steps)
            self.question.setText(f"Checklist — {title}  ·  étape {min(i + 1, n)}/{n}")
            self.question.show()
            accent, fg = self.cfg.get("accent", ACCENT), self.cfg.get("text_color", TEXT)
            rows = []
            for k in range(max(0, i - 2), min(n, i + 4)):
                step = html.escape(steps[k])
                if k < i:
                    rows.append(f'<div style="color:{MUTED}">✓ {k + 1}. {step}</div>')
                elif k == i:
                    rows.append(f'<div style="color:{accent}; font-weight:600; margin:4px 0">▶ {k + 1}. {step}</div>')
                else:
                    rows.append(f'<div style="color:{fg}">{k + 1}. {step}</div>')
            self._answer, self._dirty = "", False
            self.text.setHtml("".join(rows))
            self.text.show()
            self.actions.hide()
            self.cl_actions.show()
            self._notice("", MUTED)
            self._fit()
            self._wake()
        self._call(f)

    def hide_checklist(self):
        self._call(lambda: (self.cl_actions.hide(), self.adjustSize()))

    def show_actions(self, on):
        self._call(lambda: (self.actions.setVisible(on and self.cfg.get("show_actions", True)), self.adjustSize()))

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

    def _fit(self, full=False):
        """La zone de réponse grandit avec le texte jusqu'à « lines » lignes, puis défile.
        full : hauteur maximale (pendant le redimensionnement)."""
        doc = self.text.document()
        doc.setTextWidth(self.text.viewport().width() or (int(self.cfg["width"]) - 24))
        line = self.text.fontMetrics().lineSpacing()
        top = line * int(self.cfg["lines"]) + 4
        h = top if full else int(doc.size().height()) + 4
        self.text.setFixedHeight(max(line + 4, min(h, top)))
        self.adjustSize()

    def _notice(self, text, color):
        self.notice.setText(text)
        self.notice.setStyleSheet(f"color: {color};")
        self.notice.setVisible(bool(text))
        self.adjustSize()

    # ------------------------------------------------------------------ API (appelable depuis tout thread)
    def apply_style(self, cfg):
        self.cfg = cfg
        accent, fg = cfg.get("accent", ACCENT), cfg.get("text_color", TEXT)
        f = QFont(cfg.get("font_family") or self.font().family())
        f.setPointSizeF(float(cfg["font_size"]))
        f.setBold(bool(cfg.get("bold")))
        for w in (self.text, self.entry, self.question):
            w.setFont(f)
        self.text.document().setDefaultFont(f)
        self.text.setStyleSheet(f"QTextBrowser {{ background: transparent; border: none; color: {fg}; "
                                "selection-background-color: rgba(61,139,255,0.35); selection-color: #e8edf3; }")
        self.question.setStyleSheet(f"color: {accent}; font-weight: 600;")
        self.notice.setStyleSheet(f"color: {MUTED};")
        self.send.setIcon(icon("send", accent, 16))
        self.head.setVisible(cfg.get("show_header", True))
        self.inputs.setVisible(cfg.get("show_input", True))
        self.statusbar.setVisible(cfg.get("show_status", True))
        if not cfg.get("show_question", True):
            self.question.hide()
        if not cfg.get("show_actions", True):
            self.actions.hide()
        self.setProperty("locked", bool(cfg.get("locked")))
        self.grip.setVisible(not cfg.get("click_through"))
        self.setWindowOpacity(max(0.2, min(1.0, float(cfg.get("opacity", 1.0)))))
        through = bool(cfg.get("click_through"))
        if through != bool(self.windowFlags() & Qt.WindowTransparentForInput):
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowTransparentForInput, through)   # masque la fenêtre : on la réaffiche
            if visible:
                self.show()
        self.setFixedWidth(int(cfg["width"]) + 2 * MARGIN)
        self._dirty = True
        self.update()
        QTimer.singleShot(0, self.adjustSize)
        self._wake()

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
            if error or state in ("listen", "busy"):
                self._wake()
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
            self.question.setVisible(self.cfg.get("show_question", True))
            self.actions.hide()
            self.cl_actions.hide()
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
            self._wake()
        self._call(f)

    def info(self, text):
        self.append(text, "info")

    def later(self, ms, fn):
        self._call(lambda: QTimer.singleShot(ms, fn))

    def hide_window(self):
        self._user_hidden = True
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

