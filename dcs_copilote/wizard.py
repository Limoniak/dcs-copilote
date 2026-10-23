"""Assistant du premier lancement : IA, équipier (indicatif, voix), bouton PTT, script DCS.
Le reste garde ses valeurs par défaut."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from . import dcs_install
from . import ui_kit as ui
from .overlay import DragArea
from .settings import (EDGE_VOICES, MARGIN, PROVIDERS, Card, Segmented, SettingsWindow, _combo, _label, _links,
                       _result, _show, play_test_phrase)
from .ui_kit import ACCENT, TEXT, icon

# fournisseurs proposés ici (les autres : ⚙ Réglages → IA)
CHOICES = ["groq", "gemini", "openai", "grok", "openrouter", "ollama"]


class Wizard(QWidget):
    # mêmes outils que la fenêtre Réglages (capture du bouton PTT, tâches en arrière-plan)
    _bg, _capture, _ptt_text = SettingsWindow._bg, SettingsWindow._capture, SettingsWindow._ptt_text
    alive, lift = SettingsWindow.alive, SettingsWindow.lift

    def __init__(self, app):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.app = app
        self.c = copy.deepcopy(app.cfg)
        self.call = app.overlay._call
        self.capturing = False
        self._closed = False
        self.setWindowTitle("DCS Copilote — Bienvenue")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(640 + 2 * MARGIN, 520 + 2 * MARGIN)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGIN + 28, MARGIN + 16, MARGIN + 24, MARGIN + 18)
        top = DragArea()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        logo = _label("")
        logo.setPixmap(ui.logo("marque").pixmap(26, 26))
        tl.addWidget(logo)
        self.step = _label("", muted=True)
        tl.addWidget(self.step, 1)
        outer.addWidget(top)

        self.stack = QStackedWidget()
        for build in (self._page_llm, self._page_crew, self._page_ptt, self._page_dcs):
            self.stack.addWidget(build())
        outer.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        skip = QPushButton("Passer")
        skip.setObjectName("ghost")
        skip.setToolTip("Tout se règle aussi plus tard avec le bouton ⚙ de l'overlay")
        skip.clicked.connect(self.close)
        foot.addWidget(skip)
        foot.addStretch(1)
        self.b_back = QPushButton("Précédent")
        self.b_back.clicked.connect(lambda: self._go(-1))
        self.b_next = QPushButton()
        self.b_next.setObjectName("primary")
        self.b_next.setDefault(True)
        self.b_next.clicked.connect(lambda: self._go(1))
        foot.addWidget(self.b_back)
        foot.addWidget(self.b_next)
        outer.addLayout(foot)
        QShortcut(QKeySequence("Escape"), self, self.close)
        self._go(0)

        geo = self.screen().availableGeometry()
        self.move(geo.center() - self.rect().center())
        self.show()
        self.activateWindow()

    def paintEvent(self, _e):
        ui.paint_card(self, MARGIN, 16)

    def closeEvent(self, e):
        self._closed = True
        e.accept()

    def _go(self, delta):
        i = self.stack.currentIndex() + delta
        if i >= self.stack.count():
            self._finish()
            return
        self.stack.setCurrentIndex(i)
        self.step.setText(f"Bienvenue — étape {i + 1} sur {self.stack.count()}")
        self.b_back.setVisible(i > 0)
        self.b_next.setText("Enregistrer" if i == self.stack.count() - 1 else "Suivant")

    def _page(self, title, subtitle):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(14)
        lay.addWidget(_label(title, h1=True))
        lay.addWidget(_label(subtitle, muted=True, wrap=True))
        return w, lay

    # ------------------------------------------------------------------ 1. IA
    def _page_llm(self):
        w, lay = self._page("Ton copilote", "Choisis l'IA qui répond. Groq est gratuit et rapide : crée un compte, "
                                            "copie ta clé et colle-la ici. Elle sert aussi à reconnaître ta voix.")
        card = Card()
        self.names = {PROVIDERS[k][0] + " — " + PROVIDERS[k][1]: k for k in CHOICES}
        cur = self.c["llm"].get("provider")
        self.cb_prov = card.add("Fournisseur", _combo(list(self.names), next(
            (n for n, k in self.names.items() if k == cur), next(iter(self.names)))))
        self.e_key = QLineEdit()
        self.e_key.setEchoMode(QLineEdit.Password)
        self.e_key.setPlaceholderText("colle ta clé ici")
        card.add("Clé API", self.e_key)
        self.key_hint = card.add_widget(_label("", muted=True, wrap=True))
        self.key_hint.setOpenExternalLinks(True)
        trow = QHBoxLayout()
        self.b_test = QPushButton("Tester")
        self.b_test.setIcon(icon("send", TEXT, 15))
        self.b_test.clicked.connect(self._test_llm)
        trow.addWidget(self.b_test)
        trow.addStretch(1)
        card.lay.addLayout(trow)
        self.llm_result = card.add_widget(_result())
        lay.addWidget(card)
        imp = QPushButton("J'avais déjà DCS Copilote : importer mes réglages…")
        imp.setObjectName("ghost")
        imp.setToolTip("Choisis le config.json de l'ancienne version (dossier de l'ancien .zip, à côté de l'exe)")
        imp.clicked.connect(self._import)
        lay.addWidget(imp, 0, Qt.AlignLeft)
        lay.addStretch(1)
        self.provider = None
        self.cb_prov.currentTextChanged.connect(self._provider_changed)
        self._provider_changed(self.cb_prov.currentText())
        return w

    def _provider_changed(self, name):
        if self.provider:
            self.c["llm"]["providers"][self.provider]["api_key"] = self.e_key.text().strip()
        self.provider = self.names[name]
        self.c["llm"]["provider"] = self.provider
        self.e_key.setText(self.c["llm"]["providers"][self.provider].get("api_key", ""))
        self.e_key.row.setVisible(self.provider != "ollama")
        self.key_hint.setText(_links(PROVIDERS[self.provider][2]) +
                              (" — Ollama doit être installé et lancé sur ce PC." if self.provider == "ollama" else ""))
        _show(self.llm_result, "")

    def _llm_cfg(self):
        self._provider_changed(self.cb_prov.currentText())
        return copy.deepcopy(self.c["llm"])

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
            _show(self.llm_result, f"Échec : {res}" if isinstance(res, Exception) else f"✓ {res[:300]}",
                  "err" if isinstance(res, Exception) else "ok")
        self._bg(work, done)

    # ------------------------------------------------------------------ 2. équipier
    def _page_crew(self):
        w, lay = self._page("Ton équipier", "Comment il t'appelle, sa voix et la longueur de ses réponses. "
                                            "Tout se change ensuite dans ⚙ Réglages.")
        llm, tts = self.c["llm"], self.c["tts"]
        card = Card()
        self.e_callsign = QLineEdit(llm.get("callsign", ""))
        self.e_callsign.setPlaceholderText("ex. Viper 1, Maverick…")
        card.add("Ton indicatif", self.e_callsign, "Il t'appelle ainsi de temps en temps, comme à la radio.")
        self.cb_voice = card.add("Voix", _combo(EDGE_VOICES, tts.get("edge_voice", EDGE_VOICES[0]), editable=True),
                                 "Henri, Rémy, Antoine, Gérard, Fabrice : voix d'homme. Denise, Éloïse, Vivienne, "
                                 "Sylvie : voix de femme.")
        self.seg_radio = card.add("Effet radio", Segmented(
            [("0", "Aucun"), ("1", "Léger"), ("2", "Radio"), ("3", "Fort")], str(tts.get("radio", 0))), stretch=False)
        self.seg_len = card.add("Réponses vocales", Segmented(
            [("tres_courte", "Très courtes"), ("courte", "Courtes"), ("detaillee", "Détaillées")],
            llm.get("answer_length", "courte")), "1 phrase · 2-3 phrases · jusqu'à 6.", stretch=False)
        trow = QHBoxLayout()
        self.b_say = QPushButton("Écouter")
        self.b_say.setIcon(icon("voice", TEXT, 15))
        self.b_say.clicked.connect(self._test_voice)
        trow.addWidget(self.b_say)
        trow.addStretch(1)
        card.lay.addLayout(trow)
        self.voice_result = card.add_widget(_result())
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _store_crew(self):
        self.c["llm"].update(callsign=self.e_callsign.text().strip(),
                             answer_length=self.seg_len.value() or "courte")
        self.c["tts"].update(engine="edge", edge_voice=self.cb_voice.currentText().strip(),
                             radio=int(self.seg_radio.value() or 0))

    def _test_voice(self):
        self._store_crew()
        tts_cfg, llm_cfg, out = copy.deepcopy(self.c["tts"]), copy.deepcopy(self.c["llm"]), self.c["audio"]["output_device"]
        callsign = self.c["llm"]["callsign"]
        text = f"{callsign}, ici ton copilote. Contact radar, deux bandits à douze heures." if callsign else \
            "Ici ton copilote. Contact radar, deux bandits à douze heures."
        self.b_say.setEnabled(False)
        _show(self.voice_result, "Synthèse…")

        def done(res):
            self.b_say.setEnabled(True)
            if isinstance(res, Exception):
                _show(self.voice_result, f"Voix : {res}", "err")
            else:
                _show(self.voice_result, res or "✓ Lecture en cours.", "muted" if res else "ok")
        self._bg(lambda: play_test_phrase(tts_cfg, llm_cfg, out, text), done)

    # ------------------------------------------------------------------ 3. PTT
    def _page_ptt(self):
        w, lay = self._page("Bouton pour parler", "Maintiens-le pendant ta question, comme une radio. Il marche "
                                                  "même quand DCS est au premier plan : choisis un bouton du HOTAS "
                                                  "ou une touche qui n'est assigné à rien dans DCS.")
        card = Card()
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
        lay.addWidget(_label("Pas de bouton sous la main ? Maintiens le micro de l'overlay.", muted=True, wrap=True))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------ 4. DCS
    def _page_dcs(self):
        w, lay = self._page("DCS World", "Un petit script dans DCS dit au copilote ce que tu pilotes et ta situation "
                                         "de vol. Il s'ajoute à Export.lua (sauvegardé en Export.lua.bak), "
                                         "compatible SRS, Tacview, Helios.")
        card = Card()
        self.folders = dcs_install.candidates()
        port = int(self.c["dcs"]["udp_port"])
        todo = [d for d in self.folders if dcs_install.status(d, port) != "ok"]
        if not self.folders:
            card.add_widget(_label("Dossier Saved Games/DCS introuvable. Choisis-le plus tard dans ⚙ Réglages → DCS, "
                                   "ou sélectionne l'appareil dans le menu de l'overlay.", wrap=True))
        for d in self.folders:
            card.add_widget(_label("•  " + d, muted=True, wrap=True))
        self.b_lua = QPushButton("Installer le script")
        self.b_lua.setObjectName("primary")
        self.b_lua.clicked.connect(self._install_lua)
        self.b_lua.setVisible(bool(todo))
        irow = QHBoxLayout()
        irow.addWidget(self.b_lua)
        irow.addStretch(1)
        card.lay.addLayout(irow)
        self.dcs_result = card.add_widget(_result())
        if self.folders and not todo:
            _show(self.dcs_result, "✓ Script déjà installé.", "ok")
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _install_lua(self):
        try:
            msgs = [dcs_install.install(d, int(self.c["dcs"]["udp_port"])) for d in self.folders]
        except OSError as e:
            _show(self.dcs_result, f"Installation impossible : {e}", "err")
            return
        self.c["dcs"]["saved_games"] = self.c["dcs"].get("saved_games") or self.folders[0]
        self.b_lua.hide()
        _show(self.dcs_result, "✓ " + "\n".join(msgs), "ok")

    # ------------------------------------------------------------------ fin
    def _import(self):
        """Réglages d'une ancienne version (.zip) : clés, indicatif, bouton PTT, voix… repris tels quels."""
        from PySide6.QtWidgets import QFileDialog

        from . import config as cfgmod
        path, _ = QFileDialog.getOpenFileName(self, "config.json de l'ancienne version", "", "config.json (config.json)")
        if not path:
            return
        try:
            old = cfgmod.load(path)
            self.app.apply_settings(old)
        except (OSError, ValueError) as e:
            _show(self.llm_result, f"Import impossible : {e}", "err")
            return
        self.app.overlay.info("Réglages de l'ancienne version importés. Tout se change dans ⚙ Réglages.")
        self.close()

    def _finish(self):
        self._llm_cfg()
        self._store_crew()
        try:
            self.app.apply_settings(self.c)
        except OSError as e:
            _show(self.dcs_result, f"Enregistrement impossible : {e}", "err")
            return
        self.app.overlay.info("C'est prêt ! Maintiens ton bouton et pose ta question. Tous les réglages : bouton ⚙.")
        self.close()
