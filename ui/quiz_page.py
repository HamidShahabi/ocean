"""Quiz page: question generation lives in quiz.py, this is the screen. Answers here never touch the
spaced-repetition schedule (see the note at the top of quiz.py)."""
import html
import logging
from typing import List, Tuple, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

import quiz
from ui.theme import PERSIAN_ALIGNMENT
from ui.widgets import ModernGlassDropdown, SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow

log = logging.getLogger("ocean.app")


class QuizPage(QWidget):
    """Quiz on your saved words. Multiple choice and typed-answer modes (fill in the blank); more plug in through quiz.MODES."""

    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self.mode_key = quiz.CHOICE_MODE
        self.saved_count = 0
        self.questions: List[quiz.Question] = []
        self.results: List[Tuple[quiz.Question, str]] = []  # (question, what was picked), in order asked
        self.answered = False
        self.finished = False
        self.setObjectName("QuizPage")
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
        title = QLabel("Quiz")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        # The mode picker only appears once there is more than one mode to pick from.
        self.mode_labels = {mode.label: key for key, mode in quiz.MODES.items()}
        self.mode_drop = ModernGlassDropdown(list(self.mode_labels), quiz.MODES[self.mode_key].label, self)
        self.mode_drop.valueChanged.connect(self._on_mode_picked)
        self.mode_drop.setVisible(len(quiz.MODES) > 1)
        header.addWidget(self.mode_drop)
        header.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("MutedText")
        header.addWidget(self.progress_label)
        end_btn = QPushButton("End")
        end_btn.setObjectName("GlassPillButton")
        end_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        end_btn.setFixedHeight(34)
        end_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        end_btn.setToolTip("End quiz (Esc)")
        end_btn.clicked.connect(self.end_session)
        header.addWidget(end_btn)
        vbox.addLayout(header)

        self.prompt_label = QLabel("")
        self.prompt_label.setObjectName("HeadwordTitle")
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setTextFormat(Qt.TextFormat.PlainText)
        vbox.addWidget(self.prompt_label)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("MutedText")
        vbox.addWidget(self.hint_label)

        self.choice_box = QWidget()
        c_lay = QVBoxLayout(self.choice_box)
        c_lay.setContentsMargins(0, 4, 0, 0)
        c_lay.setSpacing(10)
        self.choice_btns: List[QPushButton] = []
        for i in range(quiz.CHOICE_COUNT):
            btn = QPushButton()
            btn.setObjectName("GlassPillButton")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(44)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setToolTip(f"Answer (key {i + 1})")
            btn.clicked.connect(lambda _, n=i: self.answer(n))
            c_lay.addWidget(btn)
            self.choice_btns.append(btn)
        vbox.addWidget(self.choice_box)

        # Typed answers: any Question without choices uses this box (Enter submits, then shows the verdict
        # and Question.reveal; Enter/Space continues). Listen-and-type reuses it as is.
        self.typed_box = QWidget()
        t_lay = QVBoxLayout(self.typed_box)
        t_lay.setContentsMargins(0, 4, 0, 0)
        t_lay.setSpacing(10)
        self.answer_edit = QLineEdit()
        self.answer_edit.setObjectName("DrawerInput")
        self.answer_edit.setFixedHeight(44)
        self.answer_edit.setPlaceholderText("Type your answer, then press Enter")
        t_lay.addWidget(self.answer_edit)
        self.verdict_label = QLabel("")
        self.verdict_label.setObjectName("BodyText")
        self.verdict_label.setTextFormat(Qt.TextFormat.RichText)
        self.verdict_label.setWordWrap(True)
        t_lay.addWidget(self.verdict_label)
        self.reveal_label = QLabel("")
        self.reveal_label.setObjectName("BodyText")
        self.reveal_label.setTextFormat(Qt.TextFormat.PlainText)
        self.reveal_label.setWordWrap(True)
        self.reveal_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        t_lay.addWidget(self.reveal_label)
        vbox.addWidget(self.typed_box)

        self.next_btn = QPushButton()
        self.next_btn.setObjectName("PrimaryPillButton")
        self.next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.next_btn.setFixedHeight(40)
        self.next_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.next_btn.clicked.connect(self.advance)
        vbox.addWidget(self.next_btn)

        self.done_label = QLabel("")
        self.done_label.setObjectName("BodyText")
        self.done_label.setWordWrap(True)
        self.done_label.setTextFormat(Qt.TextFormat.RichText)
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.done_label)

        outer.addWidget(self.card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        # While the answer box has focus it keeps letters, digits and Space (so keys 1-4 can't answer a
        # typed question); Return and Esc still reach these shortcuts.
        bindings = [("Space", self.advance), ("Return", self.on_enter), ("Enter", self.on_enter), ("Esc", self.end_session)]
        bindings += [(str(i + 1), lambda n=i: self.answer(n)) for i in range(quiz.CHOICE_COUNT)]
        for keys, slot in bindings:
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def start(self) -> None:
        """Begin a fresh session on the current saved words."""
        try:
            items = quiz.items_from_entries(self.main_win.db.favorite_entries())
        except Exception:
            log.exception("Could not load saved words for the quiz")
            items = []
        self.saved_count = len(items)
        self.questions = quiz.new_session(items, self.mode_key)
        self.results = []
        self.finished = False
        if not self.questions:
            self._show_unavailable()
            return
        self._show_question()

    def _on_mode_picked(self, label: str) -> None:
        self.mode_key = self.mode_labels[label]
        self.start()

    def _hide_question(self) -> None:
        self.hint_label.hide()
        self.choice_box.hide()
        self.typed_box.hide()
        self.next_btn.hide()
        self.done_label.hide()

    def _show_unavailable(self) -> None:
        self.finished = True
        self._hide_question()
        self.progress_label.setText("")
        self.prompt_label.setText("Not enough saved words")
        self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.done_label.setText(html.escape(quiz.unavailable_message(self.mode_key, self.saved_count)).replace("\n", "<br>"))
        self.done_label.show()

    def _show_question(self) -> None:
        question = self.questions[len(self.results)]
        self.answered = False
        self._hide_question()
        self.progress_label.setText(f"{len(self.results) + 1} of {len(self.questions)}")
        self.prompt_label.setText(question.prompt)
        self.prompt_label.setAlignment(PERSIAN_ALIGNMENT if question.prompt_is_persian else Qt.AlignmentFlag.AlignLeft)
        default_hint = "Type your answer" if question.is_typed else (
            "Choose the English word" if question.prompt_is_persian else "Choose the Persian meaning")
        self.hint_label.setText(question.hint or default_hint)
        self.hint_label.show()
        if question.is_typed:
            self.verdict_label.setText("")
            self.reveal_label.setText("")
            self.answer_edit.clear()
            self.answer_edit.setReadOnly(False)
            self.answer_edit.setAlignment(PERSIAN_ALIGNMENT if question.answer_is_persian else Qt.AlignmentFlag.AlignLeft)
            self.typed_box.show()
            self.answer_edit.setFocus()
            return
        for i, btn in enumerate(self.choice_btns):
            has_choice = i < len(question.choices)
            btn.setVisible(has_choice)
            self._style_choice(btn, "GlassPillButton")
            if has_choice:
                btn.setText(f"{i + 1}    {question.choices[i]}")
        self.choice_box.show()

    @staticmethod
    def _style_choice(btn: QPushButton, name: str) -> None:
        btn.setObjectName(name)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def answer(self, index: int) -> None:
        if self.finished or self.answered or not self.questions:
            return
        question = self.questions[len(self.results)]
        if index >= len(question.choices):
            return
        self.answered = True
        self.results.append((question, question.choices[index]))
        for i, btn in enumerate(self.choice_btns[:len(question.choices)]):
            if question.is_correct(question.choices[i]):
                self._style_choice(btn, "QuizCorrectButton")
            elif i == index:
                self._style_choice(btn, "QuizWrongButton")
        last = len(self.results) == len(self.questions)
        self.next_btn.setText("See score   (Space)" if last else "Next   (Space)")
        self.next_btn.show()

    def on_enter(self) -> None:
        """Enter submits the typed answer, then continues; for choice questions it just continues."""
        if self._typing():
            self.submit_text()
        else:
            self.advance()

    def _typing(self) -> bool:
        """True while a typed question is waiting for its answer."""
        return bool(not self.finished and not self.answered and self.questions
                    and len(self.results) < len(self.questions) and self.questions[len(self.results)].is_typed)

    def submit_text(self) -> None:
        if not self._typing():
            return
        text = self.answer_edit.text().strip()
        if not text:
            return
        question = self.questions[len(self.results)]
        self.answered = True
        self.results.append((question, text))
        if question.is_correct(text):
            self.verdict_label.setText("<b style='color:#2EA043'>Correct</b>")
        else:
            self.verdict_label.setText(f"<b style='color:#F85149'>Not quite</b> — answer: <b>{html.escape(question.answer)}</b>")
        self.reveal_label.setText(question.reveal)
        self.answer_edit.setReadOnly(True)
        self.setFocus()  # so Space/Enter continue instead of editing the finished answer
        last = len(self.results) == len(self.questions)
        self.next_btn.setText("See score   (Space)" if last else "Next   (Space)")
        self.next_btn.show()

    def advance(self) -> None:
        if self.finished:
            self.start()  # "Play again" on the score screen
        elif self.answered:
            if len(self.results) == len(self.questions):
                self._show_score()
            else:
                self._show_question()

    def end_session(self) -> None:
        """Esc / End: stop and show the score so far, or leave when there is nothing to score."""
        if not self.finished and self.results:
            self._show_score(early=True)
        else:
            self.main_win.go_home()

    def _show_score(self, early: bool = False) -> None:
        self.finished = True
        self._hide_question()
        correct = sum(1 for q, picked in self.results if q.is_correct(picked))
        total = len(self.results)
        self.progress_label.setText("")
        self.prompt_label.setText(f"{correct} of {total} correct")
        self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lines = [f"{round(100 * correct / total)}%" + (" · ended early" if early else "")]
        missed = [q for q, picked in self.results if not q.is_correct(picked)]
        if missed:
            lines.append("<br><b>To practise</b>")
            for q in missed:
                if q.reveal:
                    lines.append(f"{html.escape(q.answer)} — {html.escape(q.reveal)}")
                    continue
                pair = (q.answer, q.prompt) if q.prompt_is_persian else (q.prompt, q.answer)
                lines.append(f"{html.escape(pair[0])} — {html.escape(pair[1])}")
        else:
            lines.append("<br>Perfect round.")
        self.done_label.setText("<br>".join(lines))
        self.done_label.show()
        self.next_btn.setText("Play again   (Space)")
        self.next_btn.show()
