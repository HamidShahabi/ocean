"""Pronounce page: type or paste a word, phrase or text and hear it read aloud by the voice chosen in Settings,
with its US IPA underneath. Nothing is translated or explained here: no lookup runs at all."""
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

import lexical_engine
from ui.widgets import SmoothGlassFrame
from ui.workers import start_worker

if TYPE_CHECKING:
    from ui.main_window import MainWindow

MAX_CHARS = 5000  # about 800 words; the voice reads it in one go


def ipa_for(text: str) -> str:
    """US IPA of the text: a single word's dictionary transcription when it has one, else word by word."""
    words = text.split()
    if len(words) == 1:
        known = lexical_engine.LEXICON.ipa(words[0].strip(".,!?;:\"'()"))
        if known:
            return lexical_engine.clean_ipa_display(known)
        return lexical_engine.to_ipa_us(text)
    return lexical_engine.sentence_to_ipa(text)


class IpaWorker(QThread):
    """IPA for a long text takes a while (one dictionary look-up per word): off the GUI thread."""
    ready = pyqtSignal(str, str)

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def run(self) -> None:
        self.ready.emit(self.text, ipa_for(self.text))


class PronouncePage(QWidget):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self.worker: Optional[IpaWorker] = None
        self.text = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 24)
        card = SmoothGlassFrame(radius=22, variant="card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(28, 22, 28, 22)
        vbox.setSpacing(12)

        title = QLabel("Pronounce")
        title.setObjectName("CardTitle")
        vbox.addWidget(title)
        hint = QLabel("Type or paste English, then press Pronounce (Ctrl+Enter) to hear it with its IPA. "
                      "Nothing is translated here.")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self.input = QPlainTextEdit()
        self.input.setObjectName("ReaderInput")
        self.input.setPlaceholderText("A word, a phrase or a whole text...")
        self.input.setMaximumHeight(140)
        vbox.addWidget(self.input)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.stop_btn = self._button("Stop", "GlassPillButton")
        self.stop_btn.clicked.connect(self.stop)
        buttons.addWidget(self.stop_btn)
        self.speak_btn = self._button("Pronounce", "PrimaryPillButton")
        self.speak_btn.clicked.connect(self.pronounce)
        buttons.addWidget(self.speak_btn)
        vbox.addLayout(buttons)

        result = QWidget()
        r_lay = QVBoxLayout(result)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(8)
        self.text_label = QLabel("")
        self.text_label.setObjectName("SentenceTitle")
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        r_lay.addWidget(self.text_label)
        self.ipa_label = QLabel("")
        self.ipa_label.setObjectName("IpaText")
        self.ipa_label.setWordWrap(True)
        self.ipa_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        r_lay.addWidget(self.ipa_label)
        self.status = QLabel("")
        self.status.setObjectName("MutedText")
        self.status.setWordWrap(True)
        r_lay.addWidget(self.status)
        r_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(result)
        vbox.addWidget(scroll, stretch=1)
        outer.addWidget(card)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        shortcut.activated.connect(self.pronounce)

    def _button(self, label: str, name: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName(name)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(38)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return btn

    def start(self) -> None:
        self.input.setFocus()

    def pronounce(self) -> None:
        text = " ".join(self.input.toPlainText().split())[:MAX_CHARS]
        if not text:
            return
        self.text = text
        self.text_label.setText(text)
        self.ipa_label.setText("")
        speaker = self.main_win.speaker
        if speaker.has_voice():
            self.status.setText("")
            speaker.speak(text)
        elif speaker.is_loading():
            self.status.setText("The voice is still loading; press Pronounce again in a moment.")
        else:
            self.status.setText("No voice available: install the Kokoro model (setup script) or a system voice.")
        self.worker = IpaWorker(text)
        self.worker.ready.connect(self._on_ipa)
        start_worker(self.worker)

    def _on_ipa(self, text: str, ipa: str) -> None:
        if text == self.text:  # not superseded by a newer text
            self.ipa_label.setText(f"US  {ipa}" if ipa else "")

    def stop(self) -> None:
        self.main_win.speaker.stop()

    def hideEvent(self, event) -> None:
        self.stop()  # leaving the page silences it
        super().hideEvent(event)
