"""Fenêtre Réglages : toute la configuration (IA, voix, audio, PTT, DCS, overlay) sans éditer config.json."""
import copy
import glob
import os
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import ui_kit as ui
from .overlay import DragArea
from .ui_kit import ACCENT, ERROR, MUTED, TEXT, Toggle, icon

MARGIN = 14

PROVIDERS = {
    "ollama": ("Ollama", "Local, gratuit", "aucune clé nécessaire"),
    "openai": ("ChatGPT", "OpenAI", "clé sur platform.openai.com/api-keys"),
    "gemini": ("Gemini", "Google", "clé sur aistudio.google.com/apikey"),
    "grok": ("Grok", "xAI", "clé sur console.x.ai"),
    "groq": ("Groq", "Gratuit, très rapide", "clé gratuite sur console.groq.com/keys"),
    "openrouter": ("OpenRouter", "Tous les modèles", "clé sur openrouter.ai/keys"),
    "custom": ("Autre", "Compatible OpenAI", "Open WebUI, LM Studio…"),
}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"]
FR_VOICES = ["fr_FR-siwis-medium", "fr_FR-siwis-low", "fr_FR-upmc-medium", "fr_FR-tom-medium",
             "fr_FR-gilles-low", "fr_FR-mls-medium"]
DEFAULT_DEV = "Par défaut"
ENGINES = [("edge", "Microsoft"), ("openai", "OpenAI"), ("piper", "Piper"), ("system", "Système")]
ENGINE_HINTS = {
    "edge": "Voix neuronales très naturelles, gratuites, sans clé. Service en ligne non officiel : "
            "s'il ne répond plus, Piper prend le relais automatiquement.",
    "openai": "Qualité excellente, ton d'équipier. Payant, clé OpenAI requise. Repli sur Piper en cas d'échec.",
    "piper": "Local et hors-ligne, rapide, mais voix plus synthétique.",
    "system": "Voix du système (SAPI / espeak) : dernier recours, très robotique.",
}
EDGE_VOICES = ["fr-FR-HenriNeural", "fr-FR-RemyMultilingualNeural", "fr-FR-DeniseNeural", "fr-FR-EloiseNeural",
               "fr-FR-VivienneMultilingualNeural", "fr-CA-AntoineNeural", "fr-CA-SylvieNeural",
               "fr-BE-GerardNeural", "fr-CH-FabriceNeural"]
OPENAI_VOICES = ["ash", "onyx", "echo", "sage", "coral", "alloy", "ballad", "nova", "shimmer", "verse", "fable"]
TEST_PHRASE = "Contact radar, deux bandits à douze heures, trente nautiques."


# --------------------------------------------------------------------------- briques
def _label(text, muted=False, h1=False, h2=False, wrap=False):
    lbl = QLabel(text)
    for prop, on in (("muted", muted), ("h1", h1), ("h2", h2)):
        if on:
            lbl.setProperty(prop, True)
    lbl.setWordWrap(wrap)
    return lbl


class Card(QFrame):
    """Section encadrée ; add() ajoute une ligne « libellé + aide | contrôle »."""

    def __init__(self, title=None, subtitle=None):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 16, 18, 16)
        self.lay.setSpacing(12)
        self._rows = 0
        if title:
            self.lay.addWidget(_label(title, h2=True))
        if subtitle:
            self.lay.addWidget(_label(subtitle, muted=True, wrap=True))

    def add(self, label, widget, hint=None, stretch=True):
        """Renvoie le contrôle ; widget.row est la ligne entière (pour l'afficher / la masquer)."""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        if self._rows:
            div = QFrame()
            div.setObjectName("divider")
            outer.addWidget(div)
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        left.addWidget(_label(label))
        if hint:
            left.addWidget(_label(hint, muted=True, wrap=True))
        holder = QWidget()
        holder.setFixedWidth(220)
        holder.setLayout(left)
        row.addWidget(holder, 0, Qt.AlignVCenter)
        if stretch:
            row.addWidget(widget, 1)
        else:
            row.addStretch(1)
            row.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
        outer.addLayout(row)
        self.lay.addWidget(box)
        widget.row = box
        self._rows += 1
        return widget

    def add_widget(self, w):
        self.lay.addWidget(w)
        return w


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options, value):
        super().__init__()
        self.setObjectName("seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("segbtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        self.set(value)

    def set(self, key):
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def value(self):
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)


class Slider(QWidget):
    def __init__(self, lo, hi, value, fmt):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(lo, hi)
        self.s.setValue(int(value))
        self.lbl = _label("", muted=True)
        self.lbl.setFixedWidth(52)
        self.lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fmt = fmt
        self.s.valueChanged.connect(lambda v: self.lbl.setText(self.fmt(v)))
        self.lbl.setText(fmt(self.s.value()))
        lay.addWidget(self.s, 1)
        lay.addWidget(self.lbl)

    def value(self):
        return self.s.value()


def _spin(lo, hi, value, step=1, suffix=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(int(value))
    s.setSuffix(suffix)
    s.setFixedWidth(110)
    s.setAlignment(Qt.AlignRight)
    return s


def _combo(values, current="", editable=False):
    c = QComboBox()
    c.setEditable(editable)
    c.addItems(values)
    if editable:
        c.setEditText(current)
    elif current in values:
        c.setCurrentText(current)
    return c


def _result():
    lbl = _label("", wrap=True)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lbl.hide()
    return lbl


def _show(lbl, text, kind="muted"):
    lbl.setText(text)
    lbl.setStyleSheet(f"color: {dict(muted=MUTED, ok=ACCENT, err=ERROR)[kind]};")
    lbl.setVisible(bool(text))


# --------------------------------------------------------------------------- fenêtre
class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.app = app
        self.c = copy.deepcopy(app.cfg)
        self.call = app.overlay._call          # retour vers le thread de l'interface
        self.capturing = False
        self._closed = False
        self._saved = False

        self.setWindowTitle("DCS Copilote — Réglages")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(900 + 2 * MARGIN, 640 + 2 * MARGIN)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        outer.setSpacing(0)

        # ---- barre latérale
        side = DragArea()
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 20, 12, 16)
        sl.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icon("plane", ACCENT, 22).pixmap(22, 22))
        brand.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = _label("DCS Copilote")
        t.setStyleSheet("font-weight: 700; font-size: 11pt;")
        titles.addWidget(t)
        titles.addWidget(_label("Réglages", muted=True))
        brand.addLayout(titles, 1)
        sl.addLayout(brand)
        sl.addSpacing(18)

        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        pages = (("IA", "sparkles", self._page_llm), ("Manuels", "book", self._page_manuals),
                 ("Alertes", "bell", self._page_alerts),
                 ("Voix", "voice", self._page_voice),
                 ("Audio", "headphones", self._page_audio), ("Bouton PTT", "mic", self._page_ptt),
                 ("DCS", "plane", self._page_dcs), ("Overlay", "monitor", self._page_overlay))
        for i, (name, ic, build) in enumerate(pages):
            b = QPushButton(f"  {name}")
            b.setObjectName("nav")
            b.setIcon(icon(ic, MUTED, 18))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.toggled.connect(lambda on, b=b, ic=ic: b.setIcon(icon(ic, ACCENT if on else MUTED, 18)))
            b.clicked.connect(lambda _c=False, i=i: self.stack.setCurrentIndex(i))
            self.nav.addButton(b, i)
            sl.addWidget(b)
            self.stack.addWidget(self._scroll(build()))
        self.nav.button(0).setChecked(True)
        sl.addStretch(1)
        logb = QPushButton("  Ouvrir le journal")
        logb.setObjectName("nav")
        logb.setIcon(icon("folder", MUTED, 16))
        logb.setToolTip("Fichier logs/dcs-copilote.log : à joindre si tu signales un problème")
        logb.clicked.connect(self._open_log)
        sl.addWidget(logb)
        sl.addSpacing(6)
        sl.addWidget(_label("Ctrl+S : enregistrer\nÉchap : fermer", muted=True, wrap=True))
        outer.addWidget(side)

        vdiv = QFrame()
        vdiv.setObjectName("vdivider")
        outer.addWidget(vdiv)

        # ---- contenu
        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        top = DragArea()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 8, 10, 0)
        tl.addStretch(1)
        close = QToolButton()
        close.setIcon(icon("x", MUTED, 16))
        close.setToolTip("Fermer")
        close.clicked.connect(self.close)
        tl.addWidget(close)
        main.addWidget(top)
        main.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(28, 12, 20, 18)
        foot.setSpacing(8)
        self.status = _label("", muted=True, wrap=True)
        foot.addWidget(self.status, 1)
        cancel = QPushButton("Annuler")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.close)
        save = QPushButton("Enregistrer")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.save)
        foot.addWidget(cancel)
        foot.addWidget(save)
        main.addLayout(foot)
        outer.addLayout(main, 1)

        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save)

        geo = self.screen().availableGeometry()
        self.move(geo.center() - self.rect().center())
        self.show()
        self.activateWindow()

    def paintEvent(self, _e):
        ui.paint_card(self, MARGIN, 16)

    def _open_log(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from .logfile import LOG_PATH
        if not os.path.exists(LOG_PATH):
            self.msg("Pas encore de journal.", error=True)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(LOG_PATH)))

    @staticmethod
    def _scroll(page):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(page)
        return sa

    @staticmethod
    def _page(title, subtitle):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 4, 24, 12)
        lay.setSpacing(14)
        lay.addWidget(_label(title, h1=True))
        lay.addWidget(_label(subtitle, muted=True, wrap=True))
        lay.addSpacing(4)
        return w, lay

    # ------------------------------------------------------------------ outils
    def alive(self):
        return not self._closed

    def lift(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not self._saved:
            self.app.overlay.apply_style(self.app.cfg["overlay"])   # annule l'aperçu de l'overlay
        self._closed = True
        e.accept()

    def msg(self, text, error=False):
        if self.alive():
            self.status.setText(text)
            self.status.setStyleSheet(f"color: {ERROR if error else MUTED};")

    def _bg(self, work, done):
        """Exécute work() dans un thread puis done(résultat ou exception) dans l'interface."""
        def run():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                res = e
            self.call(lambda: self.alive() and done(res))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ IA
    def _page_llm(self):
        w, lay = self._page("Intelligence artificielle",
                            "Le modèle qui joue ton copilote. Toutes ces API sont compatibles OpenAI.")
        llm = self.c["llm"]
        self.provider = llm.get("provider", "ollama")

        prov = Card("Fournisseur")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.tiles = QButtonGroup(self)
        for i, key in enumerate(llm["providers"]):
            name, sub, _ = PROVIDERS.get(key, (key, "", ""))
            b = QPushButton(f"{name}\n{sub}")
            b.setObjectName("tile")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(54)
            b.setChecked(key == self.provider)
            b.clicked.connect(lambda _c=False, k=key: self._provider_changed(k))
            self.tiles.addButton(b)
            grid.addWidget(b, i // 4, i % 4)
        prov.lay.addLayout(grid)
        lay.addWidget(prov)

        conn = Card("Connexion")
        self.e_url = conn.add("Adresse de l'API", QLineEdit())
        self.e_key = QLineEdit()
        self.e_key.setEchoMode(QLineEdit.Password)
        eye = self.e_key.addAction(icon("eye", MUTED, 16), QLineEdit.TrailingPosition)
        eye.triggered.connect(lambda: self.e_key.setEchoMode(
            QLineEdit.Normal if self.e_key.echoMode() == QLineEdit.Password else QLineEdit.Password))
        conn.add("Clé API", self.e_key)
        self.key_hint = conn.add_widget(_label("", muted=True, wrap=True))

        mrow = QWidget()
        ml = QHBoxLayout(mrow)
        ml.setContentsMargins(0, 0, 0, 0)
        self.cb_model = _combo([], editable=True)
        ml.addWidget(self.cb_model, 1)
        self.b_models = QPushButton("Lister")
        self.b_models.setIcon(icon("refresh", TEXT, 15))
        self.b_models.clicked.connect(self._list_models)
        ml.addWidget(self.b_models)
        conn.add("Modèle", mrow, "Pour la voix en vol, préfère un modèle rapide (mini, flash, fast).")
        self.cb_fallback = conn.add("Modèles de secours", _combo([], editable=True),
                                    "Essayés dans l'ordre si le modèle principal est surchargé ou à court de "
                                    "quota. Plusieurs : séparés par des virgules.")
        self.cb_fallback.lineEdit().setPlaceholderText("aucun — ex. gemini-2.5-flash-lite, gemini-3.5-flash-lite")
        names = [(k, PROVIDERS.get(k, (k,))[0]) for k in llm["providers"]]
        self.cb_backup = conn.add("Fournisseur de secours", _combo(["Aucun"] + [n for _k, n in names]),
                                  "Prend le relais quand tous les modèles ci-dessus sont épuisés ou en panne "
                                  "(sa clé et son modèle se règlent en cliquant sur sa tuile).")
        self._backup_keys = {n: k for k, n in names}
        cur = llm.get("fallback_provider", "")
        self.cb_backup.setCurrentText(PROVIDERS.get(cur, (cur,))[0] if cur else "Aucun")

        trow = QHBoxLayout()
        self.b_test = QPushButton("Tester l'IA")
        self.b_test.setIcon(icon("send", TEXT, 15))
        self.b_test.clicked.connect(self._test_llm)
        trow.addWidget(self.b_test)
        trow.addStretch(1)
        conn.lay.addLayout(trow)
        self.llm_result = conn.add_widget(_result())
        lay.addWidget(conn)

        adv = Card("Comportement")
        self.t_fast = adv.add("Réponse rapide", Toggle(llm.get("fast", True)),
                              "Limite la « réflexion » des modèles qui en ont (Gemini 2.5, GPT-5…) : "
                              "1er mot en ~0,5 s au lieu de 2 à 15 s.", stretch=False)
        self.seg_len = adv.add("Réponses vocales", Segmented(
            [("tres_courte", "Très courtes"), ("courte", "Courtes"), ("detaillee", "Détaillées")],
            llm.get("answer_length", "courte")), "1 phrase · 2-3 phrases · jusqu'à 6. Toujours 15 mots max par "
                                                 "phrase.", stretch=False)
        self.e_callsign = QLineEdit(llm.get("callsign", ""))
        self.e_callsign.setPlaceholderText("ex. Viper 1, Maverick…")
        adv.add("Ton indicatif", self.e_callsign, "Le copilote t'appelle ainsi de temps en temps, comme à la radio.")
        self.e_custom = QPlainTextEdit(llm.get("custom_prompt", ""))
        self.e_custom.setPlaceholderText("Ex. : Donne les distances en kilomètres. Je débute sur le Hornet, "
                                         "explique les acronymes.")
        self.e_custom.setFixedHeight(84)
        adv.add("Consignes personnelles", self.e_custom, "Ajoutées aux consignes du copilote, prioritaires.")
        self.s_temp = adv.add("Créativité (température)", Slider(0, 20, float(llm["temperature"]) * 10,
                                                                 lambda v: f"{v / 10:.1f}"),
                              "Bas = réponses plus factuelles.")
        self.s_hist = adv.add("Mémoire", _spin(0, 30, llm["max_history"], suffix=" échanges"),
                              "Questions précédentes gardées en contexte.", stretch=False)
        self.s_timeout = adv.add("Délai maximum", _spin(10, 600, llm["timeout"], 10, " s"), stretch=False)
        lay.addWidget(adv)
        lay.addStretch(1)
        self._load_provider(self.provider)
        return w

    def _provider_changed(self, key):
        self._store_provider()
        self.provider = key
        self._load_provider(key)

    def _load_provider(self, name):
        p = self.c["llm"]["providers"].setdefault(name, {"base_url": "", "api_key": "", "model": ""})
        self.e_url.setText(p.get("base_url", ""))
        self.e_url.setCursorPosition(0)
        self.e_key.setText(p.get("api_key", ""))
        self.cb_model.clear()
        self.cb_model.setEditText(p.get("model", ""))
        self.cb_fallback.clear()
        self.cb_fallback.setEditText(p.get("fallback_model", ""))
        hint = PROVIDERS.get(name, ("", "", ""))[2]
        if p.get("api_key_env"):
            hint += f" — ou variable d'environnement {p['api_key_env']}"
        self.key_hint.setText(hint)
        _show(self.llm_result, "")

    def _store_provider(self):
        p = self.c["llm"]["providers"][self.provider]
        p["base_url"] = self.e_url.text().strip()
        p["api_key"] = self.e_key.text().strip()
        p["model"] = self.cb_model.currentText().strip()
        p["fallback_model"] = self.cb_fallback.currentText().strip()
        self.c["llm"]["provider"] = self.provider

    def _llm_cfg(self):
        self._store_provider()
        return copy.deepcopy(self.c["llm"])

    def _list_models(self):
        from .llm import LLMClient
        cfg = self._llm_cfg()
        self.b_models.setEnabled(False)
        _show(self.llm_result, "Récupération des modèles…")

        def done(res):
            self.b_models.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.llm_result, f"Liste impossible : {res}", "err")
                return
            for cb in (self.cb_model, self.cb_fallback):
                cur = cb.currentText()
                cb.clear()
                cb.addItems(res)
                cb.setEditText(cur)
            _show(self.llm_result, f"{len(res)} modèles disponibles — ouvre la liste « Modèle ».", "ok")
            self.cb_model.showPopup()
        self._bg(lambda: LLMClient(cfg).models(), done)

    def _test_llm(self):
        from .llm import LLMClient
        cfg = self._llm_cfg()
        self.b_test.setEnabled(False)
        _show(self.llm_result, "Question de test envoyée…")

        def work():
            msgs = [{"role": "user", "content": "Réponds en une courte phrase : es-tu prêt à m'aider sur DCS ?"}]
            return "".join(LLMClient(cfg).stream(msgs)).strip()

        def done(res):
            self.b_test.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.llm_result, f"Échec : {res}", "err")
            else:
                _show(self.llm_result, f"✓ {res[:300]}", "ok")
        self._bg(work, done)

    # ------------------------------------------------------------------ Alertes
    def _page_alerts(self):
        from .alerts import RULES
        w, lay = self._page("Alertes", "Le copilote prend la parole de lui-même quand la situation l'exige, "
                                       "à partir des données de vol du script DCS.")
        a = self.c.get("alerts", {})
        card = Card()
        self.t_alerts = card.add("Alertes vocales", Toggle(a.get("enabled", True)),
                                 "Une alerte passe devant la réponse en cours.", stretch=False)
        lay.addWidget(card)
        rules = Card("Situations surveillées", "Seuils prudents et génériques (avions à réaction) ; les règles "
                                               "de train et de volets sont ignorées en hélicoptère.")
        self.t_rules = {}
        on = a.get("rules", {})
        for rid, (label, hint, _hold, cooldown) in RULES.items():
            rep = ("une fois par minute" if cooldown == 60 else f"toutes les {cooldown // 60} min" if cooldown > 60
                   else f"toutes les {cooldown} s")
            self.t_rules[rid] = rules.add(label, Toggle(on.get(rid, True)), f"{hint} Répétée au plus {rep}.",
                                          stretch=False)
        lay.addWidget(rules)
        trow = QHBoxLayout()
        b = QPushButton("Tester une alerte")
        b.setIcon(icon("bell", TEXT, 15))
        b.clicked.connect(lambda: self.app.speaker.alert("Train rentré, on est en approche !"))
        trow.addWidget(b)
        trow.addStretch(1)
        lay.addLayout(trow)
        lay.addWidget(_label("Il faut le script DCS v2 (Réglages → DCS → Installer). Certains serveurs multijoueurs "
                             "bloquent une partie des données : les alertes concernées restent alors muettes.",
                             muted=True, wrap=True))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------ Manuels
    def _page_manuals(self):
        from .manuals import dcs_install_candidates
        w, lay = self._page("Manuels", "Le copilote s'appuie sur les manuels PDF de tes modules : réponses "
                                        "bien plus fiables, avec la page source.")
        m = self.c["manuals"]
        card = Card("Sources")
        self.t_manuals = card.add("Utiliser les manuels", Toggle(m.get("enabled", True)),
                                  "Les passages utiles sont donnés à l'IA à chaque question.", stretch=False)
        cands = dcs_install_candidates()
        self.cb_dcsdir = card.add("Dossier de DCS World", self._folder_row(
            _combo(cands, m.get("dcs_dir") or (cands[0] if cands else ""), editable=True)),
            "Celui qui contient Mods/aircraft (manuels officiels des modules).").box
        self.e_extra = card.add("Dossier de PDF perso", self._folder_row(QLineEdit(m.get("extra_dir", ""))),
                                "Facultatif : Chuck's Guides, checklists… Le nom du fichier doit contenir "
                                "l'appareil (ex. « F-16C »).").box
        irow = QHBoxLayout()
        self.b_index = QPushButton("Indexer les manuels")
        self.b_index.setObjectName("primary")
        self.b_index.setIcon(icon("book", ui.BASE, 15))
        self.b_index.clicked.connect(self._index_manuals)
        irow.addWidget(self.b_index)
        self.b_stop_index = QPushButton("Arrêter")
        self.b_stop_index.setObjectName("ghost")
        self.b_stop_index.hide()
        self.b_stop_index.clicked.connect(lambda: setattr(self, "_stop_index", True))
        irow.addWidget(self.b_stop_index)
        irow.addStretch(1)
        card.lay.addLayout(irow)
        self.index_bar = QProgressBar()
        self.index_bar.setTextVisible(False)
        self.index_bar.hide()
        card.add_widget(self.index_bar)
        self.index_result = card.add_widget(_result())
        s = self.app.manuals.summary() if self.app.manuals.ready else None
        _show(self.index_result, self._summary_text(s) if s else "Aucun manuel indexé pour l'instant.")
        lay.addWidget(card)

        lay.addWidget(self._chucks_card())

        test = Card("Tester la recherche", "Ce que l'IA recevrait pour cette question, avec l'appareil actuel.")
        trow = QHBoxLayout()
        self.e_query = QLineEdit()
        self.e_query.setPlaceholderText("Ex. : comment je démarre l'APU ?")
        self.e_query.returnPressed.connect(self._test_search)
        trow.addWidget(self.e_query, 1)
        b = QPushButton("Chercher")
        b.clicked.connect(self._test_search)
        trow.addWidget(b)
        test.lay.addLayout(trow)
        self.search_result = test.add_widget(_result())
        lay.addWidget(test)
        lay.addWidget(_label("L'indexation se fait une fois (quelques minutes pour beaucoup de modules) ; "
                             "relance-la après l'achat ou la mise à jour d'un module. Le modèle de recherche "
                             "(~220 Mo) est téléchargé au premier usage.", muted=True, wrap=True))
        lay.addStretch(1)
        return w

    def _chucks_card(self):
        from . import chucks
        ch = self.c["manuals"].get("chucks", {})
        card = Card("Chuck's Guides", "Guides détaillés et très illustrés de chucksguides.com, téléchargés et "
                                      "mis à jour automatiquement (vérification hebdomadaire, seulement si un "
                                      "guide a changé). Comptent ~50 à 150 Mo chacun.")
        self.t_chucks = card.add("Mise à jour automatique", Toggle(ch.get("enabled", False)), stretch=False)
        self.t_follow = card.add("Suivre mes modules", Toggle(ch.get("follow_installed", True)),
                                 "Ajoute d'office les guides des modules installés (dossier de DCS ci-dessus).",
                                 stretch=False)
        card.add_widget(_label("Guides à télécharger :", muted=True))
        self.chucks_list = QListWidget()
        self.chucks_list.setFixedHeight(190)
        card.add_widget(self.chucks_list)
        brow = QHBoxLayout()
        self.b_chucks = QPushButton("Mettre à jour maintenant")
        self.b_chucks.setObjectName("primary")
        self.b_chucks.setIcon(icon("refresh", ui.BASE, 15))
        self.b_chucks.clicked.connect(self._update_chucks)
        brow.addWidget(self.b_chucks)
        mine = QPushButton("Cocher mes modules")
        mine.clicked.connect(self._check_installed)
        brow.addWidget(mine)
        brow.addStretch(1)
        card.lay.addLayout(brow)
        self.chucks_bar = QProgressBar()
        self.chucks_bar.setTextVisible(False)
        self.chucks_bar.setRange(0, 1000)
        self.chucks_bar.hide()
        card.add_widget(self.chucks_bar)
        self.chucks_result = card.add_widget(_result())

        state = chucks.load_state()
        self._fill_chucks(state["guides"], ch.get("aircraft", []))
        self._chucks_status(state)
        if not state["guides"]:
            _show(self.chucks_result, "Chargement de la liste des guides…")
            self._bg(chucks.list_guides, lambda res: _show(self.chucks_result, f"Liste indisponible : {res}", "err")
                     if isinstance(res, Exception) else (self._fill_chucks(res, ch.get("aircraft", [])),
                                                         self._chucks_status(chucks.load_state())))
        return card

    def _fill_chucks(self, guides, selected):
        self.chucks_list.clear()
        for slug, name in sorted(guides.items(), key=lambda kv: kv[1]):
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, slug)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if slug in selected else Qt.Unchecked)
            self.chucks_list.addItem(it)

    def _chucks_checked(self):
        return [self.chucks_list.item(i).data(Qt.UserRole) for i in range(self.chucks_list.count())
                if self.chucks_list.item(i).checkState() == Qt.Checked]

    def _chucks_status(self, state):
        import time as _t
        files = state.get("files", {})
        if not files:
            _show(self.chucks_result, "Aucun guide téléchargé." if state.get("guides") else "")
            return
        last = _t.strftime("%d/%m/%Y", _t.localtime(state["last_check"])) if state.get("last_check") else "jamais"
        names = ", ".join(state["guides"].get(s, s) for s in files)
        _show(self.chucks_result, f"✓ {len(files)} guide(s) : {names} — dernière vérification : {last}", "ok")

    def _check_installed(self):
        from . import chucks
        dcs_dir = self.cb_dcsdir.get().strip()
        guides = {self.chucks_list.item(i).data(Qt.UserRole): 1 for i in range(self.chucks_list.count())}
        mine = chucks.installed_slugs(guides, dcs_dir)
        if not mine:
            _show(self.chucks_result, "Aucun module trouvé : renseigne le dossier de DCS World ci-dessus.", "err")
            return
        for i in range(self.chucks_list.count()):
            it = self.chucks_list.item(i)
            if it.data(Qt.UserRole) in mine:
                it.setCheckState(Qt.Checked)
        _show(self.chucks_result, f"{len(mine)} guide(s) correspondant à tes modules cochés.", "ok")

    def _update_chucks(self):
        from . import chucks
        self._collect()                        # la sélection courante, même non enregistrée
        cfg = copy.deepcopy(self.c)
        if not self.app.chucks_selection(cfg):
            _show(self.chucks_result, "Coche au moins un guide (ou active « Suivre mes modules »).", "err")
            return
        self.b_chucks.setEnabled(False)
        self.chucks_bar.show()
        self.chucks_bar.setValue(0)

        def progress(msg, frac=None):
            def f():
                if self.alive():
                    _show(self.chucks_result, msg)
                    if frac is not None:
                        self.chucks_bar.setValue(int(frac * 1000))
            self.call(f)

        def done(res):
            self.b_chucks.setEnabled(True)
            self.chucks_bar.hide()
            if isinstance(res, Exception):
                _show(self.chucks_result, f"Mise à jour impossible : {res}", "err")
                return
            updated, errors = res
            self._chucks_status(chucks.load_state())
            msg = self.chucks_result.text()
            if updated:
                msg = f"Téléchargé : {', '.join(updated)}. Manuels réindexés.\n" + msg
            if errors:
                msg += "\nErreurs : " + " ; ".join(errors)
            _show(self.chucks_result, msg, "err" if errors and not updated else "ok")
            if self.app.manuals.ready:
                _show(self.index_result, self._summary_text(self.app.manuals.summary()), "ok")
        self._bg(lambda: self.app.update_chucks(progress, cfg=cfg), done)

    def _folder_row(self, field):
        roww = QWidget()
        rl = QHBoxLayout(roww)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(field, 1)
        b = QPushButton()
        b.setIcon(icon("folder", TEXT, 16))
        b.setToolTip("Parcourir…")
        get = field.currentText if isinstance(field, QComboBox) else field.text
        put = field.setEditText if isinstance(field, QComboBox) else field.setText

        def browse():
            d = QFileDialog.getExistingDirectory(self, "Choisir un dossier", get() or os.path.expanduser("~"))
            if d:
                put(d)
        b.clicked.connect(browse)
        rl.addWidget(b)
        roww.box, roww.get = roww, get       # .box : la ligne (pour Card.add), .get : valeur
        return roww

    @staticmethod
    def _summary_text(s):
        mods = ", ".join(f"{k} ({v})" for k, v in sorted(s["modules"].items()))
        return f"✓ {s['docs']} documents indexés, {s['chunks']} extraits — {mods}"

    def _index_manuals(self):
        from .manuals import build_index, find_pdfs
        dcs_dir, extra = self.cb_dcsdir.get().strip(), self.e_extra.get().strip()
        pdfs = find_pdfs(dcs_dir, extra)
        if not pdfs:
            _show(self.index_result, "Aucun PDF trouvé : vérifie le dossier de DCS World (il doit contenir "
                                     "Mods/aircraft) ou le dossier perso.", "err")
            return
        self.c["manuals"].update(dcs_dir=dcs_dir, extra_dir=extra)
        self._stop_index = False
        self.b_index.setEnabled(False)
        self.b_stop_index.show()
        self.index_bar.show()
        self.index_bar.setValue(0)
        _show(self.index_result, f"{len(pdfs)} PDF trouvés…")

        def progress(done, total, msg):
            def f():
                if self.alive():
                    self.index_bar.setMaximum(max(1, total))
                    self.index_bar.setValue(done)
                    _show(self.index_result, msg)
            self.call(f)

        def work():
            s = build_index(pdfs, progress, lambda: self._stop_index)
            self.app.load_manuals()
            return s

        def done(res):
            self.b_index.setEnabled(True)
            self.b_stop_index.hide()
            self.index_bar.hide()
            if isinstance(res, Exception):
                _show(self.index_result, f"Indexation impossible : {res}", "err")
            else:
                _show(self.index_result, self._summary_text(res), "ok")
        self._bg(work, done)

    def _test_search(self):
        from .manuals import sources_line
        q = self.e_query.text().strip()
        if not q:
            return
        if not self.app.manuals.ready:
            _show(self.search_result, "Indexe d'abord les manuels.", "err")
            return
        ac = self.app.current_aircraft()
        res = self.app.manuals.search(q, ac, int(self.c["manuals"].get("top_k", 4)))
        if not res:
            _show(self.search_result, f"Rien de pertinent pour « {ac or 'tous appareils'} ». "
                                      "L'IA répondra de mémoire.", "err")
            return
        lines = [f"{sources_line(res)}  (appareil : {ac or 'tous'})", ""]
        for score, c, d in res:
            lines.append(f"p. {c['page']} — {c['text'][:220].strip()}…")
        _show(self.search_result, "\n".join(lines), "muted")

    # ------------------------------------------------------------------ Voix
    def _page_voice(self):
        w, lay = self._page("Voix", "Reconnaissance de ta voix (Whisper, local) et lecture des réponses au casque.")
        stt, tts = self.c["stt"], self.c["tts"]

        rec = Card("Reconnaissance vocale")
        self.seg_stt = rec.add("Moteur", Segmented([("local", "Sur ce PC (Whisper)"), ("api", "En ligne (rapide)")],
                                                   stt.get("engine", "local")))
        self.stt_hint = rec.add_widget(_label("", muted=True, wrap=True))
        self.cb_sttp = rec.add("Service", _combo(["groq", "openai", "custom"], stt.get("api_provider", "groq")),
                               "Groq : gratuit (clé sur console.groq.com), ~0,3 s. OpenAI : payant.")
        self.e_sttkey = QLineEdit(stt.get("api_key", ""))
        self.e_sttkey.setEchoMode(QLineEdit.Password)
        self.e_sttkey.setPlaceholderText("vide = GROQ_API_KEY / OPENAI_API_KEY (ou clé OpenAI de l'onglet IA)")
        rec.add("Clé API", self.e_sttkey)
        self.e_sttmodel = rec.add("Modèle", QLineEdit(stt.get("api_model", "")))
        self.e_sttmodel.setPlaceholderText("vide = whisper-large-v3-turbo (Groq) / gpt-4o-mini-transcribe (OpenAI)")
        self.e_sttbase = rec.add("Adresse", QLineEdit(stt.get("api_base", "")))
        self.e_sttbase.setPlaceholderText("vide = adresse du service choisi")
        self.cb_stt = rec.add("Modèle Whisper", _combo(WHISPER_MODELS, stt["model"], editable=True),
                              "small : bon compromis sur CPU. medium / large-v3 : plus précis, plus lents.")
        self.seg_dev = rec.add("Calcul", Segmented([("cpu", "Processeur"), ("cuda", "Carte graphique")],
                                                   stt["device"]),
                               "Le processeur laisse toute la VRAM à DCS.", stretch=False)
        self.cb_ct = rec.add("Précision", _combo(["int8", "int8_float16", "float16", "float32"],
                                                 stt["compute_type"], editable=True))
        self.seg_dev.changed.connect(lambda d: self.cb_ct.setEditText("float16" if d == "cuda" else "int8"))
        self.e_lang = QLineEdit(stt.get("language") or "")
        self.e_lang.setPlaceholderText("auto")
        self.e_lang.setFixedWidth(110)
        rec.add("Langue", self.e_lang, "fr, en… (vide = détection automatique)", stretch=False)
        lay.addWidget(rec)
        self.seg_stt.changed.connect(self._stt_engine_changed)
        self._stt_engine_changed(self.seg_stt.value())

        syn = Card("Synthèse vocale")
        self.t_tts = syn.add("Lire les réponses", Toggle(tts["enabled"]), stretch=False)
        self.t_typed = syn.add("Lire aussi les questions tapées", Toggle(tts.get("speak_typed", False)),
                               "Activé : réponse courte et lue, comme à la voix (« Détailler » pour la "
                               "version complète). Désactivé : réponse détaillée, à lire à l'écran.",
                               stretch=False)
        self.seg_engine = syn.add("Moteur", Segmented(ENGINES, tts["engine"]))
        self.engine_hint = syn.add_widget(_label("", muted=True, wrap=True))
        self.cb_edge = syn.add("Voix", _combo(EDGE_VOICES, tts.get("edge_voice", EDGE_VOICES[0]), editable=True),
                               "Henri, Denise, Éloïse : 100 % français, termes anglais à la française. « Multilingual » (Rémy, Vivienne) : accent anglais sur les « termes » cités.")
        self.cb_openai = syn.add("Voix", _combo(OPENAI_VOICES, tts.get("openai_voice", "ash"), editable=True))
        self.e_okey = QLineEdit(tts.get("openai_key", ""))
        self.e_okey.setEchoMode(QLineEdit.Password)
        self.e_okey.setPlaceholderText("vide = clé OpenAI de l'onglet IA")
        syn.add("Clé OpenAI", self.e_okey, "Facturée à l'usage (quelques centimes par heure de vol).")
        self.cb_voice = syn.add("Voix", _combo(self._voices(), tts["piper_model"], editable=True))
        drow = QWidget()
        dl = QHBoxLayout(drow)
        dl.setContentsMargins(0, 0, 0, 0)
        self.cb_dl = _combo(FR_VOICES, FR_VOICES[0], editable=True)
        dl.addWidget(self.cb_dl, 1)
        self.b_dl = QPushButton("Télécharger")
        self.b_dl.clicked.connect(self._download_voice)
        dl.addWidget(self.b_dl)
        self.dl_row = syn.add("Autre voix", drow, "huggingface.co/rhasspy/piper-voices (ex. en_US-amy-medium)")
        self.seg_radio = syn.add("Effet radio", Segmented(
            [("0", "Aucun"), ("1", "Léger"), ("2", "Radio"), ("3", "Fort")], str(tts.get("radio", 0))),
            "Son de casque radio, clic d'ouverture et souffle de fin de transmission.", stretch=False)
        self.s_speed = syn.add("Débit", Slider(60, 180, float(tts.get("speed", 1.0)) * 100,
                                               lambda v: f"×{v / 100:.2f}"))
        trow = QHBoxLayout()
        self.b_say = QPushButton("Tester la voix")
        self.b_say.setIcon(icon("voice", TEXT, 15))
        self.b_say.clicked.connect(self._test_voice)
        trow.addWidget(self.b_say)
        trow.addStretch(1)
        syn.lay.addLayout(trow)
        self.voice_result = syn.add_widget(_result())
        lay.addWidget(syn)
        lay.addStretch(1)
        self.seg_engine.changed.connect(self._engine_changed)
        self._engine_changed(self.seg_engine.value())
        return w

    def _stt_engine_changed(self, engine):
        for wdg in (self.cb_sttp, self.e_sttkey, self.e_sttmodel, self.e_sttbase):
            wdg.row.setVisible(engine == "api")
        self.stt_hint.setText(
            "Ta voix est envoyée au service choisi : transcription en ~0,3 s au lieu de 1 à 3 s sur processeur. "
            "Whisper local reste chargé et prend le relais si le service ne répond pas." if engine == "api" else
            "Gratuit et hors-ligne ; 1 à 3 s de transcription sur processeur selon le modèle.")

    def _engine_changed(self, engine):
        rows = {"edge": [self.cb_edge], "openai": [self.cb_openai, self.e_okey],
                "piper": [self.cb_voice, self.dl_row]}
        for eng, widgets in rows.items():
            for wdg in widgets:
                wdg.row.setVisible(eng == engine)
        self.engine_hint.setText(ENGINE_HINTS.get(engine, ""))

    @staticmethod
    def _voices():
        return sorted(p.replace(os.sep, "/") for p in glob.glob(os.path.join("voices", "*.onnx")))

    def _download_voice(self):
        from .audio import download_voice
        name = self.cb_dl.currentText().strip()
        if name.count("-") < 2:
            _show(self.voice_result, "Nom attendu : langue_PAYS-nom-qualité (ex. fr_FR-siwis-medium)", "err")
            return
        self.b_dl.setEnabled(False)
        _show(self.voice_result, f"Téléchargement de {name}…")

        def done(res):
            self.b_dl.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.voice_result, f"Téléchargement impossible : {res}", "err")
                return
            self.cb_voice.clear()
            self.cb_voice.addItems(self._voices())
            self.cb_voice.setEditText(res.replace(os.sep, "/"))
            self.seg_engine.set("piper")
            _show(self.voice_result, f"✓ Voix {name} installée et sélectionnée.", "ok")
        self._bg(lambda: download_voice(name), done)

    def _test_voice(self):
        from .audio import _dev, make_tts, play, radio_fx, squelch
        self._collect()
        tts_cfg, out = dict(self.c["tts"], enabled=True), self.c["audio"]["output_device"]
        llm_cfg = copy.deepcopy(self.c["llm"])
        self.b_say.setEnabled(False)
        _show(self.voice_result, "Synthèse…")
        notes = []

        def work():
            tts = make_tts(tts_cfg, lambda m, error=False: notes.append(m), llm_cfg)
            audio, sr = tts.synth(TEST_PHRASE)
            if audio is None:
                raise RuntimeError("aucun son produit")
            level = int(tts_cfg.get("radio", 0))
            if level:
                import numpy as np
                audio = np.concatenate([squelch(sr, level, "open"), radio_fx(audio, sr, level),
                                        squelch(sr, level, "tail")])
            play(audio, sr, _dev(out))
            return " ".join(notes)

        def done(res):
            self.b_say.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.voice_result, f"Voix : {res}", "err")
            else:
                _show(self.voice_result, res or "✓ Lecture en cours.", "muted" if res else "ok")
        self._bg(work, done)

    # ------------------------------------------------------------------ Audio
    def _page_audio(self):
        from .audio import list_devices
        w, lay = self._page("Audio", "Micro utilisé pour tes questions et sortie où les réponses sont lues.")
        a = self.c["audio"]
        card = Card()
        self.devices = {}
        for kind, key, label in (("input", "input_device", "Micro"), ("output", "output_device", "Casque / sortie")):
            try:
                devs = list_devices(kind)
            except Exception as e:  # noqa: BLE001
                devs = []
                self.msg(f"Périphériques audio : {e}", error=True)
            mapping = {DEFAULT_DEV: None}
            mapping.update({lbl: val for val, lbl in devs})
            cur = a.get(key)
            cur_lbl = next((lbl for lbl, val in mapping.items() if val == cur), None)
            if cur_lbl is None:                     # ancien index numérique ou appareil débranché
                cur_lbl = f"{cur} (actuel)"
                mapping[cur_lbl] = cur
            cb = card.add(label, _combo(list(mapping), cur_lbl))
            self.devices[key] = (cb, mapping)
        self.t_beeps = card.add("Bips", Toggle(a.get("beeps", True)),
                                "À l'appui et au relâchement du bouton.", stretch=False)
        trow = QHBoxLayout()
        self.b_out = QPushButton("Tester la sortie")
        self.b_out.setIcon(icon("headphones", TEXT, 15))
        self.b_out.clicked.connect(self._test_output)
        self.b_mic = QPushButton("Tester le micro")
        self.b_mic.setIcon(icon("mic", TEXT, 15))
        self.b_mic.clicked.connect(self._test_mic)
        trow.addWidget(self.b_out)
        trow.addWidget(self.b_mic)
        trow.addStretch(1)
        card.lay.addLayout(trow)
        self.audio_result = card.add_widget(_result())
        lay.addWidget(card)
        lay.addWidget(_label(
            "« Par défaut » suit la sortie choisie dans Windows." if sys.platform == "win32" else
            "« Par défaut » suit la sortie choisie dans les réglages son du système (PipeWire/PulseAudio). "
            "Les entrées « accès direct » contournent le mixeur et peuvent être occupées par une autre appli.",
            muted=True, wrap=True))
        lay.addStretch(1)
        return w

    def _device(self, key):
        cb, mapping = self.devices[key]
        return mapping.get(cb.currentText())

    def _test_output(self):
        from .audio import TEST_CHIME, _dev, play
        out = self._device("output_device")
        self.b_out.setEnabled(False)
        _show(self.audio_result, "Lecture du son de test…")

        def done(res):
            self.b_out.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.audio_result, f"Sortie inutilisable : {res}", "err")
            else:
                _show(self.audio_result, "✓ Son joué. Rien entendu ? Essaie une autre sortie ou vérifie le volume "
                                         "du système.", "ok")
        self._bg(lambda: play(*TEST_CHIME, device=_dev(out), wait=True), done)

    def _test_mic(self):
        import time

        import numpy as np

        from .audio import SR_IN, Recorder, _dev, play
        dev, out = self._device("input_device"), self._device("output_device")
        # même micro que l'appli : on réutilise son flux (un accès direct n'accepte qu'un seul flux)
        shared = self.app.recorder if dev == self.app.cfg["audio"]["input_device"] else None
        self.b_mic.setEnabled(False)
        _show(self.audio_result, "Parle maintenant… (3 s)")

        def work():
            rec = shared or Recorder(dev)
            try:
                rec.start()
                time.sleep(3)
                audio = rec.stop()
            finally:
                if rec is not shared:
                    rec.close()
            peak = float(np.abs(audio).max()) if len(audio) else 0.0
            self.call(lambda: self.alive() and _show(self.audio_result, "Réécoute de l'enregistrement…"))
            if len(audio):
                play(audio, SR_IN, _dev(out), wait=True)
            return peak

        def done(res):
            self.b_mic.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.audio_result, f"Micro inutilisable : {res}", "err")
            elif res < 0.02:
                _show(self.audio_result, f"Micro muet ou très faible (niveau max {res:.0%}). Vérifie le micro "
                                         "choisi, qu'il n'est pas coupé, et son volume dans le système.", "err")
            else:
                _show(self.audio_result, f"✓ Niveau max {res:.0%} — tu as dû t'entendre."
                      + (" Un peu fort : risque de saturation." if res > 0.98 else ""), "ok")
        self._bg(work, done)

    # ------------------------------------------------------------------ PTT
    def _page_ptt(self):
        w, lay = self._page("Bouton push-to-talk",
                            "Maintiens-le pour parler. Il est lu même quand DCS a le focus.")
        card = Card()
        card.add_widget(_label("Bouton actuel", muted=True))
        brow = QHBoxLayout()
        self.ptt_dot = ui.StatusDot()
        self.ptt_dot.set_state("ready")
        brow.addWidget(self.ptt_dot)
        self.ptt_lbl = _label(self._ptt_text())
        self.ptt_lbl.setStyleSheet(f"font-size: 15pt; font-weight: 600; color: {ACCENT};")
        brow.addWidget(self.ptt_lbl, 1)
        card.lay.addLayout(brow)
        arow = QHBoxLayout()
        self.b_bind = QPushButton("Assigner une touche ou un bouton HOTAS")
        self.b_bind.setObjectName("primary")
        self.b_bind.setIcon(icon("mic", ui.BASE, 15))
        self.b_bind.clicked.connect(self._capture)
        arow.addWidget(self.b_bind)
        arow.addStretch(1)
        card.lay.addLayout(arow)
        self.ptt_result = card.add_widget(_result())
        lay.addWidget(card)
        tips = Card("Conseils")
        for t in ("Choisis un bouton qui n'est assigné à rien dans DCS.",
                  "Linux/Wayland : la touche clavier globale n'est pas fiable, préfère un bouton HOTAS.",
                  "Un nouvel appui coupe la réponse en cours."):
            tips.add_widget(_label("•  " + t, muted=True, wrap=True))
        lay.addWidget(tips)
        lay.addStretch(1)
        return w

    def _ptt_text(self):
        p = self.c["ptt"]
        if p.get("type") == "joystick":
            return f"Bouton {int(p['button']) + 1} · {p['joystick_name']}"
        return f"Touche « {p.get('key', '?')} »"

    def _capture(self):
        from .ptt import capture_binding
        if self.capturing:
            return
        self.capturing = True
        self.app.stop_ptt()                  # sinon le PTT de l'appli « mange » les événements joystick
        self.b_bind.setEnabled(False)
        self.ptt_dot.set_state("listen")
        remaining = [20]
        _show(self.ptt_result, "Appuie sur la touche ou le bouton voulu… 20 s")

        def tick():
            remaining[0] -= 1
            if self.capturing and remaining[0] > 0:
                _show(self.ptt_result, f"Appuie sur la touche ou le bouton voulu… {remaining[0]} s")
            else:
                timer.stop()
        timer = QTimer(self, interval=1000, timeout=tick)
        timer.start()

        def done(res):
            timer.stop()
            self.b_bind.setEnabled(True)
            self.ptt_dot.set_state("ready")
            if isinstance(res, dict):
                self.c["ptt"].update(res)
                self.ptt_lbl.setText(self._ptt_text())
                _show(self.ptt_result, "✓ Bouton détecté — « Enregistrer » pour l'utiliser.", "ok")
            else:
                _show(self.ptt_result, "Rien détecté." if res is None else f"Erreur : {res}", "err")

        def run():
            try:
                res = capture_binding()
            except Exception as e:  # noqa: BLE001
                res = e

            def back():
                self.capturing = False
                if self.app.ptt is None:
                    self.app.start_ptt()     # reprend l'ancien bouton tant que rien n'est enregistré
                if self.alive():
                    done(res)
            self.call(back)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ DCS
    def _page_dcs(self):
        from . import dcs_install
        w, lay = self._page("DCS World", "Le script DCS transmet l'appareil piloté, l'altitude et la vitesse.")
        d = self.c["dcs"]
        card = Card("Script d'export", "Copie DCSAssistant.lua dans Saved Games/DCS/Scripts et l'ajoute à "
                                       "Export.lua (sauvegardé en Export.lua.bak). Compatible SRS, Tacview, Helios.")
        cands = dcs_install.candidates()
        prow = QWidget()
        pl = QHBoxLayout(prow)
        pl.setContentsMargins(0, 0, 0, 0)
        self.cb_saved = _combo(cands, d.get("saved_games") or (cands[0] if cands else ""), editable=True)
        pl.addWidget(self.cb_saved, 1)
        browse = QPushButton()
        browse.setIcon(icon("folder", TEXT, 16))
        browse.setToolTip("Parcourir…")
        browse.clicked.connect(self._browse)
        pl.addWidget(browse)
        card.add("Dossier Saved Games/DCS", prow, "Celui qui contient Config, Logs, Missions…")
        self.s_port = card.add("Port UDP", _spin(1024, 65535, d["udp_port"]), stretch=False)
        irow = QHBoxLayout()
        b = QPushButton("Installer / mettre à jour le script")
        b.setObjectName("primary")
        b.clicked.connect(self._install_lua)
        irow.addWidget(b)
        irow.addStretch(1)
        card.lay.addLayout(irow)
        self.dcs_result = card.add_widget(_result())
        lay.addWidget(card)
        lay.addWidget(_label("Sans le script, choisis l'appareil dans le menu de l'overlay.", muted=True, wrap=True))
        lay.addStretch(1)
        return w

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier Saved Games/DCS",
                                             self.cb_saved.currentText() or os.path.expanduser("~"))
        if d:
            self.cb_saved.setEditText(d)

    def _install_lua(self):
        from . import dcs_install
        path = self.cb_saved.currentText().strip()
        try:
            _show(self.dcs_result, "✓ " + dcs_install.install(path, self.s_port.value()), "ok")
            self.c["dcs"]["saved_games"] = path
        except (OSError, ValueError) as e:
            _show(self.dcs_result, f"Installation impossible : {e}", "err")

    # ------------------------------------------------------------------ Overlay
    def _page_overlay(self):
        w, lay = self._page("Overlay", "La fenêtre de réponses. Les changements s'affichent en direct.")
        o = self.c["overlay"]
        card = Card()
        self.s_alpha = card.add("Opacité du fond", Slider(30, 100, float(o["alpha"]) * 100, lambda v: f"{v} %"))
        self.s_fs = card.add("Taille du texte", _spin(8, 28, o["font_size"], suffix=" pt"), stretch=False)
        self.s_lines = card.add("Hauteur maximale", _spin(3, 50, o["lines"], suffix=" lignes"),
                                "Au-delà, la réponse défile.", stretch=False)
        self.s_width = card.add("Largeur", _spin(280, 1600, o["width"], 20, " px"), stretch=False)
        lay.addWidget(card)
        lay.addWidget(_label("Astuce : fais glisser l'overlay par son en-tête ; sa position est mémorisée.",
                             muted=True, wrap=True))
        lay.addStretch(1)
        for sig in (self.s_alpha.s.valueChanged, self.s_fs.valueChanged, self.s_lines.valueChanged,
                    self.s_width.valueChanged):
            sig.connect(self._preview_overlay)
        return w

    def _overlay_values(self):
        return dict(alpha=round(self.s_alpha.value() / 100, 2), font_size=self.s_fs.value(),
                    lines=self.s_lines.value(), width=self.s_width.value())

    def _preview_overlay(self, *_):
        self.app.overlay.apply_style(dict(self.app.cfg["overlay"], **self._overlay_values()))

    # ------------------------------------------------------------------ enregistrement
    def _collect(self):
        c = self.c
        self._store_provider()
        c["llm"]["fallback_provider"] = self._backup_keys.get(self.cb_backup.currentText(), "")
        c["llm"].update(answer_length=self.seg_len.value() or "courte", fast=self.t_fast.isChecked(),
                        custom_prompt=self.e_custom.toPlainText().strip(), callsign=self.e_callsign.text().strip(),
                        temperature=self.s_temp.value() / 10, max_history=self.s_hist.value(),
                        timeout=self.s_timeout.value())
        c["stt"].update(engine=self.seg_stt.value() or "local", api_provider=self.cb_sttp.currentText().strip(),
                        api_key=self.e_sttkey.text().strip(), api_model=self.e_sttmodel.text().strip(),
                        api_base=self.e_sttbase.text().strip())
        c["stt"].update(model=self.cb_stt.currentText().strip(), device=self.seg_dev.value() or "cpu",
                        compute_type=self.cb_ct.currentText().strip(),
                        language=self.e_lang.text().strip() or None)
        c["tts"].update(enabled=self.t_tts.isChecked(), speak_typed=self.t_typed.isChecked(),
                        engine=self.seg_engine.value() or "piper",
                        edge_voice=self.cb_edge.currentText().strip(),
                        openai_voice=self.cb_openai.currentText().strip(), openai_key=self.e_okey.text().strip(),
                        radio=int(self.seg_radio.value() or 0),
                        piper_model=self.cb_voice.currentText().strip(), speed=self.s_speed.value() / 100)
        for key, (cb, mapping) in self.devices.items():
            c["audio"][key] = mapping.get(cb.currentText())
        c["audio"]["beeps"] = self.t_beeps.isChecked()
        c["alerts"] = {"enabled": self.t_alerts.isChecked(),
                       "rules": {rid: t.isChecked() for rid, t in self.t_rules.items()}}
        c["manuals"].update(enabled=self.t_manuals.isChecked(), dcs_dir=self.cb_dcsdir.get().strip(),
                            extra_dir=self.e_extra.get().strip())
        c["manuals"]["chucks"] = {"enabled": self.t_chucks.isChecked(), "follow_installed": self.t_follow.isChecked(),
                                  "aircraft": self._chucks_checked()}
        c["dcs"].update(udp_port=self.s_port.value(), saved_games=self.cb_saved.currentText().strip())
        c["overlay"].update(self._overlay_values())

    def save(self):
        self._collect()
        port_changed = self.c["dcs"]["udp_port"] != self.app.cfg["dcs"]["udp_port"]
        try:
            self.app.apply_settings(self.c)
        except OSError as e:
            self.msg(f"Enregistrement impossible : {e}", error=True)
            return
        if port_changed:
            self.app.overlay.info("Port UDP changé : réinstalle le script DCS (Réglages → DCS) pour le même port.")
        self._saved = True
        self.close()
