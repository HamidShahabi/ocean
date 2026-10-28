"""Word-by-word highlighting of an example sentence while it is read aloud."""
import time
from typing import Callable, List, Optional

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, pyqtSlot
from PyQt6.QtWidgets import QLabel

import speech_timing
from ui.speaker import NeuralSpeaker
from ui.theme import THEME_COLORS

TICK_MS = 40


class ExampleHighlighter(QObject):
    """Highlights the word being spoken in a QLabel, following NeuralSpeaker's clip signals.

    Lives on the GUI thread; the speaker's signals are queued to it, and a QTimer polls the clock
    (rather than one timer per word) so the highlight cannot drift from the audio.
    """

    def __init__(self, speaker: NeuralSpeaker, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._speaker = speaker
        self._clock = clock
        self._pending: Optional[tuple[int, QLabel, str, str]] = None  # (token, label, text, original html)
        self._active: Optional[tuple[int, QLabel, str]] = None        # (token, label, original html)
        self._timings: List[speech_timing.WordTiming] = []
        self._speech_start = 0.0
        self._shown: Optional[int] = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        speaker.clip_started.connect(self._on_clip_started)
        speaker.playback_stopped.connect(self._on_playback_stopped)

    def speak(self, label: QLabel, text: str) -> None:
        """Read `text` aloud, highlighting its words in `label` (which shows `text`, possibly with tags)."""
        self.clear()  # also restores `label` if it is still highlighted from an earlier play
        token = self._speaker.speak(text)
        self._pending = (token, label, text, label.text()) if token else None

    @pyqtSlot(int, float, float)
    def _on_clip_started(self, token: int, lead_in: float, duration: float) -> None:
        self.clear()
        pending, self._pending = self._pending, None
        if pending is None or pending[0] != token:
            return
        _, label, text, original = pending
        timings = speech_timing.estimate_word_timings(text, duration)
        if not timings or sip.isdeleted(label):
            return  # fallback voice (no duration) or the card is gone: plain playback
        self._active = (token, label, original)
        self._timings = timings
        self._speech_start = self._clock() + lead_in
        self._shown = None
        self._timer.start()
        self._tick()

    @pyqtSlot(int)
    def _on_playback_stopped(self, token: int) -> None:
        if self._active is not None and self._active[0] == token:
            self.clear()

    def clear(self) -> None:
        """Remove the highlight and restore the label."""
        self._timer.stop()
        if self._active is not None:
            _, label, original = self._active
            if not sip.isdeleted(label):
                label.setText(original)
        self._active = None
        self._timings = []
        self._shown = None

    def _tick(self) -> None:
        if self._active is None:
            return
        _, label, original = self._active
        if sip.isdeleted(label):
            self.clear()
            return
        index = speech_timing.word_at(self._timings, self._clock() - self._speech_start)
        if index == self._shown:
            return
        self._shown = index
        if index is None:
            label.setText(original)  # in the lead-in or tail silence
            return
        word = self._timings[index]
        style = f"background-color: {THEME_COLORS['accent']}; color: {THEME_COLORS['bg_main']};"
        label.setText(speech_timing.highlight_word_html(original, word.start_char, word.end_char, style))
