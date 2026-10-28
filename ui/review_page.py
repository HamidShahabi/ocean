"""Flashcard review page: spaced-repetition scheduling lives in review.py, this is the screen."""
import html
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

import review
from ui.config import DB_FILE
from ui.theme import PERSIAN_ALIGNMENT
from ui.widgets import SmoothGlassFrame, create_audio_button

if TYPE_CHECKING:
    from ui.main_window import MainWindow

log = logging.getLogger("ocean.app")


class ReviewPage(QWidget):
    """Flashcard review of favourited entries, scheduled by spaced repetition (see review.py)."""

    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self.store = review.ReviewStore(DB_FILE)
        self.queue: List[review.ReviewCard] = []
        self.done_count = 0
        self.revealed = False
        self.setObjectName("ReviewPage")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 42)
        outer.addStretch(1)

        self.card = SmoothGlassFrame(radius=22, variant="card")
        self.card.setMinimumWidth(560)
        self.card.setMaximumWidth(760)
        vbox = QVBoxLayout(self.card)
        vbox.setContentsMargins(32, 24, 32, 26)
        vbox.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Review")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("MutedText")
        header.addWidget(self.progress_label)
        end_btn = QPushButton("End")
        end_btn.setObjectName("GlassPillButton")
        end_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        end_btn.setFixedHeight(34)
        end_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        end_btn.setToolTip("End review (Esc)")
        end_btn.clicked.connect(self.main_win.go_home)
        header.addWidget(end_btn)
        vbox.addLayout(header)

        self.front_row = QHBoxLayout()
        self.front_row.setSpacing(12)
        self.term_label = QLabel("")
        self.term_label.setObjectName("HeadwordTitle")
        self.term_label.setWordWrap(True)
        self.front_row.addWidget(self.term_label, stretch=1)
        self.audio_btn: Optional[QPushButton] = None
        vbox.addLayout(self.front_row)

        self.ipa_label = QLabel("")
        self.ipa_label.setObjectName("IpaText")
        vbox.addWidget(self.ipa_label)

        self.answer_box = QWidget()
        ans = QVBoxLayout(self.answer_box)
        ans.setContentsMargins(0, 6, 0, 0)
        ans.setSpacing(10)
        self.fa_label = QLabel("")
        self.fa_label.setObjectName("PersianText")
        self.fa_label.setWordWrap(True)
        self.fa_label.setTextFormat(Qt.TextFormat.PlainText)
        self.fa_label.setAlignment(PERSIAN_ALIGNMENT)
        ans.addWidget(self.fa_label)
        self.def_label = QLabel("")
        self.def_label.setObjectName("BodyText")
        self.def_label.setWordWrap(True)
        self.def_label.setTextFormat(Qt.TextFormat.RichText)
        ans.addWidget(self.def_label)
        vbox.addWidget(self.answer_box)

        self.show_btn = QPushButton("Show answer   (Space)")
        self.show_btn.setObjectName("PrimaryPillButton")
        self.show_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show_btn.setFixedHeight(40)
        self.show_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.show_btn.clicked.connect(self.reveal)
        vbox.addWidget(self.show_btn)

        self.grade_row = QWidget()
        g_lay = QHBoxLayout(self.grade_row)
        g_lay.setContentsMargins(0, 0, 0, 0)
        g_lay.setSpacing(10)
        self.grade_btns: Dict[review.Grade, QPushButton] = {}
        for grade in review.Grade:
            btn = QPushButton()
            btn.setObjectName("PrimaryPillButton" if grade == review.Grade.GOOD else "GlassPillButton")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(40)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, g=grade: self.grade(g))
            g_lay.addWidget(btn, stretch=1)
            self.grade_btns[grade] = btn
        vbox.addWidget(self.grade_row)

        self.done_label = QLabel("")
        self.done_label.setObjectName("BodyText")
        self.done_label.setWordWrap(True)
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.done_label)

        outer.addWidget(self.card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        bindings = [("Space", self.reveal), ("Return", self.reveal), ("Esc", self.main_win.go_home)]
        bindings += [(str(int(g)), lambda g=g: self.grade(g)) for g in review.Grade]
        for keys, slot in bindings:
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def start(self) -> None:
        try:
            self.queue = self.store.due_cards(datetime.now())
        except Exception:
            log.exception("Could not load review cards")
            self.queue = []
        self.done_count = 0
        self._show_current()

    def _show_current(self) -> None:
        self.revealed = False
        self.answer_box.hide()
        self.grade_row.hide()
        if self.audio_btn is not None:
            self.audio_btn.deleteLater()
            self.audio_btn = None

        if not self.queue:
            self._show_done()
            return

        card = self.queue[0]
        self.progress_label.setText(f"{self.done_count} done · {len(self.queue)} left")
        self.term_label.setText(card.payload.get("headword") or card.display_term)
        ipa = card.payload.get("ipa_us", "")
        self.ipa_label.setText(ipa)
        self.ipa_label.setVisible(bool(ipa) and not card.payload.get("is_sentence"))
        self.show_btn.show()
        self.done_label.hide()

        if card.payload.get("is_reverse"):
            # Persian on the front (no English voice for it), English on the back.
            self.fa_label.hide()
            translation = card.payload.get("english_translation", "")
            words = ", ".join(t for t in (c.get("term", "") for c in card.payload.get("candidates", [])[:6])
                              if t and t.lower() != translation.lower())
            answer = [f"<b>{html.escape(translation)}</b>"]
            if words:
                answer.append(html.escape(words))
            self.def_label.setText("<br>".join(answer))
            self.def_label.show()
            return
        self.fa_label.show()
        self.audio_btn = create_audio_button(self.term_label.text(), self.main_win.speaker, self.main_win.settings)
        self.front_row.addWidget(self.audio_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self.fa_label.setText(card.payload.get("persian_equivalent", ""))
        # The Persian meaning is the answer: larger than the theme's body size.
        self.fa_label.setStyleSheet(f"font-size: {int(self.main_win.settings.data.get('base_font_px', 14)) + 8}px;")
        parts = []
        for sense in card.payload.get("senses", [])[:2]:
            pos = html.escape(sense.get("pos", ""))
            line = f"<b>{pos}</b> — {html.escape(sense.get('def', ''))}" if pos else html.escape(sense.get("def", ""))
            if sense.get("example_en"):
                line += f"<br><i>{html.escape(sense['example_en'])}</i>"
            parts.append(line)
        self.def_label.setText("<br><br>".join(parts))
        self.def_label.setVisible(bool(parts))

    def _show_done(self) -> None:
        self.progress_label.setText(f"{self.done_count} done" if self.done_count else "")
        self.term_label.setText("All caught up" if self.done_count else "Nothing to review")
        self.ipa_label.hide()
        self.show_btn.hide()
        try:
            next_due = self.store.next_due()
        except Exception:
            log.exception("Could not read next review time")
            next_due = None
        if next_due is not None:
            wait = review.format_delay(max(next_due - datetime.now(), timedelta(minutes=1)))
            msg = f"Next review in {wait}."
        else:
            msg = "Save words with ☆ Save on a result card to add them to your review deck."
        self.done_label.setText(msg)
        self.done_label.show()

    def reveal(self) -> None:
        if not self.queue or self.revealed:
            return
        self.revealed = True
        self.answer_box.show()
        self.show_btn.hide()
        state, now = self.queue[0].state, datetime.now()
        names = {review.Grade.AGAIN: "Again", review.Grade.HARD: "Hard", review.Grade.GOOD: "Good", review.Grade.EASY: "Easy"}
        for grade, btn in self.grade_btns.items():
            delay = review.format_delay(review.schedule(state, grade, now).due - now)
            btn.setText(f"{names[grade]} · {delay}")
            btn.setToolTip(f"{names[grade]} (key {int(grade)})")
        self.grade_row.show()

    def grade(self, grade: review.Grade) -> None:
        if not self.queue or not self.revealed:
            return
        card, now = self.queue[0], datetime.now()
        new_state = review.schedule(card.state, grade, now)
        try:
            self.store.save_state(card.query_key, new_state, now)
        except Exception:
            log.exception("Could not save review of %r", card.query_key)
            QMessageBox.warning(self, "Ocean Notice", "Could not save this review. Please try again.")
            return
        self.queue.pop(0)
        if grade == review.Grade.AGAIN:
            card.state = new_state
            self.queue.append(card)  # relearn: show it again before the session ends
        else:
            self.done_count += 1
        self.main_win.refresh_review_badges()
        self._show_current()
