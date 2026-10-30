"""Reader page: paste an English text and read it inside Ocean. The tokenising and marking logic lives in
reader.py; this is the screen.

Layout decision: a split view. The text is on the left and never leaves the screen; a click on a word (or a
selection of several words) shows the result in a panel on the right, so the scroll position and the
selection are exactly where you left them and there is no Back to press. The panel is a second ResultsPage
(the same cards as a normal lookup), and related words clicked inside it are looked up in the panel too."""
import functools
import logging
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from PyQt6.QtCore import QPoint, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QClipboard, QColor, QContextMenuEvent, QCursor, QMouseEvent, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import lexical_engine
import reader
import storage
import word_levels
from ui.config import READER_FILE, READER_LEVEL_OPTIONS
from ui.results_page import ResultsPage
from ui.theme import THEME_COLORS
from ui.widgets import GlassPopupMenu, ModernGlassDropdown, SmoothGlassFrame
from ui.workers import EngineWorker, start_worker

if TYPE_CHECKING:
    from ui.main_window import MainWindow

log = logging.getLogger("ocean.app")

LINE_HEIGHT_PERCENT = 150
OFF = "Off"


class ReaderView(QTextEdit):
    """The text, read-only. Tells the page which character was clicked, or which range was selected."""

    char_clicked = pyqtSignal(int)            # document position of the character that was clicked
    range_selected = pyqtSignal(int, int)     # start, end of a selection made with the mouse
    menu_requested = pyqtSignal(QPoint, int)  # global position, character under the pointer (-1 = none)
    resized = pyqtSignal()
    touched = pyqtSignal()                    # the reader used the keyboard or mouse on the text

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ReaderView")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setAcceptRichText(False)

    def char_at(self, pos: QPoint) -> int:
        """Document position of the character under viewport point `pos`, -1 when it is not on text."""
        point = QPointF(pos.x() + self.horizontalScrollBar().value(), pos.y() + self.verticalScrollBar().value())
        boundary = self.document().documentLayout().hitTest(point, Qt.HitTestAccuracy.ExactHit)
        if boundary < 0:
            return -1
        cursor = QTextCursor(self.document())
        cursor.setPosition(boundary)
        # hitTest gives the nearest boundary between characters: left of it is the character that was hit
        # when the pointer is left of the boundary, right of it otherwise.
        return boundary - 1 if pos.x() < self.cursorRect(cursor).x() else boundary

    def keyPressEvent(self, event) -> None:
        self.touched.emit()
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.touched.emit()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            self.range_selected.emit(cursor.selectionStart(), cursor.selectionEnd())
            return
        pos = self.char_at(event.position().toPoint())
        if pos >= 0:
            self.char_clicked.emit(pos)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.menu_requested.emit(event.globalPos(), self.char_at(event.pos()))


class ReaderResultPanel(QWidget):
    """The right-hand side: a ResultsPage for the word or phrase looked up from the text.

    ResultsPage was written for MainWindow and reaches it through a few attributes (settings, speaker, db,
    current_term/current_data, refresh_review_badges, right_drawer and _analyze_input). This widget offers the
    same ones, passes the shared things on to the real main window and sends lookups to the Reader instead of
    switching the main window to its own results page."""

    def __init__(self, main_win: "MainWindow", page: "ReaderPage") -> None:
        super().__init__()
        self.main_win = main_win
        self.page = page
        self.settings = main_win.settings
        self.speaker = main_win.speaker
        self.db = main_win.db
        self.right_drawer = main_win.right_drawer
        self.current_term: Optional[str] = None
        self.current_data: Optional[Dict[str, Any]] = None
        self.history: List[str] = []   # terms shown here, so Back can return to the previous one
        self.setMinimumWidth(420)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.back_btn = QPushButton("‹ Back")
        self.back_btn.setObjectName("GlassPillButton")
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_btn.setFixedHeight(30)
        self.back_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_btn.setToolTip("Back to the previous result in this panel")
        self.back_btn.clicked.connect(self.go_back)
        self.back_btn.hide()
        head.addWidget(self.back_btn)
        self.title = QLabel("Click a word to look it up, or select several words to look up the phrase.")
        self.title.setObjectName("MutedText")
        self.title.setWordWrap(True)
        head.addWidget(self.title, stretch=1)
        vbox.addLayout(head)
        self.results = ResultsPage(self)
        self.results.layout().setSpacing(0)  # no search bar in this one: its slot must not leave a gap
        self.results.favorite_toggled.connect(lambda *_: page.refresh_marks())
        vbox.addWidget(self.results, stretch=1)

    # --- what ResultsPage expects of its main window ---
    def _analyze_input(self, force_refresh: bool = False, override_term: Optional[str] = None) -> None:
        self.page.lookup(override_term or self.current_term or "", force_refresh=force_refresh)

    def refresh_review_badges(self) -> None:
        self.main_win.refresh_review_badges()

    # --- the panel itself ---
    def show_message(self, text: str) -> None:
        self.title.setText(text)

    def show_result(self, term: str, data: Dict[str, Any], note: str = "") -> None:
        if self.current_term and self.current_term.lower() != term.lower() and (not self.history or self.history[-1] != self.current_term):
            self.history.append(self.current_term)
        self.back_btn.setVisible(bool(self.history))
        self.current_term = term
        self.current_data = data
        self.title.setText(note)
        self.results.render(term, data)

    def reset(self) -> None:
        """A different text: forget the results of the previous one."""
        self.history.clear()
        self.back_btn.hide()
        self.current_term = None
        self.current_data = None
        self.results._clear_cards()

    def go_back(self) -> None:
        if self.history:
            term = self.history.pop()
            self.current_term = None  # not a new step: keep the stack as it is
            self.back_btn.setVisible(bool(self.history))
            self.page.lookup(term, record=False)


class ReaderPage(QWidget):
    """Paste or type a text, then read it with saved words and words above your level marked."""

    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self.setObjectName("ReaderPage")
        self.text = ""
        self.marked: List[reader.Marked] = []
        self.units = reader.Utf16Map("")
        self.saved = reader.SavedWords()
        self.level_of = functools.partial(word_levels.cefr_level, lexical_engine.CEFR_LEVELS_FILE)
        self.worker: Optional[EngineWorker] = None
        self.pending_term = ""              # the term the running (or last shown) lookup is for
        self.save_after: Optional[str] = None  # term to mark as saved once its lookup arrives
        self._stale_copy: Optional[Dict[str, Any]] = None
        self._restoring = False
        self._rendered = False   # the text is in the view (it is put there the first time the page is opened)
        self._started = False
        self._top_offset = 0     # character offset of the first line in view
        self._restore_pending = False  # put the view back at _top_offset once it has its final width
        self._note = ""
        self._menu: Optional[GlassPopupMenu] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Reader")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self.level_label = QLabel("Mark level")
        self.level_label.setObjectName("MutedText")
        header.addWidget(self.level_label)
        self.level_drop = ModernGlassDropdown(READER_LEVEL_OPTIONS, main_win.settings.data.get("reader_min_level", "B2"), self)
        self.level_drop.setToolTip("Words at this CEFR level or higher are marked (Off = only your saved words)")
        self.level_drop.valueChanged.connect(self._on_level_picked)
        header.addWidget(self.level_drop)
        self.above_label = QLabel("and above")
        self.above_label.setObjectName("MutedText")
        header.addWidget(self.above_label)
        self.edit_btn = QPushButton("Edit text")
        self.edit_btn.setObjectName("GlassPillButton")
        self.edit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.edit_btn.setFixedHeight(34)
        self.edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.edit_btn.setToolTip("Paste or type another text")
        self.edit_btn.clicked.connect(self.show_editor)
        header.addWidget(self.edit_btn)
        outer.addLayout(header)

        self.legend = QLabel("")
        self.legend.setObjectName("MutedText")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(self.legend)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("MutedText")
        outer.addWidget(self.stats_label)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, stretch=1)

        # Mode 0: paste or type the text
        editor = SmoothGlassFrame(radius=22, variant="card")
        e_lay = QVBoxLayout(editor)
        e_lay.setContentsMargins(20, 18, 20, 18)
        e_lay.setSpacing(10)
        hint = QLabel("Paste or type an English text, then press Read. Click any word to look it up; "
                      "saved words and words above your level stand out.")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        e_lay.addWidget(hint)
        self.input = QPlainTextEdit()
        self.input.setObjectName("ReaderInput")
        self.input.setPlaceholderText("Paste or type an English text here...")
        self.input.textChanged.connect(self._update_input_count)
        e_lay.addWidget(self.input, stretch=1)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.count_label = QLabel("")
        self.count_label.setObjectName("MutedText")
        buttons.addWidget(self.count_label)
        buttons.addStretch()
        self.paste_btn = self._button("Paste", "GlassPillButton", "Paste the clipboard here")
        self.paste_btn.clicked.connect(self._paste)
        buttons.addWidget(self.paste_btn)
        self.clear_btn = self._button("Clear", "GlassPillButton", "Empty the box")
        self.clear_btn.clicked.connect(self.input.clear)
        buttons.addWidget(self.clear_btn)
        self.cancel_btn = self._button("Back to text", "GlassPillButton", "Keep the text you were reading")
        self.cancel_btn.clicked.connect(self.show_reading)
        buttons.addWidget(self.cancel_btn)
        self.read_btn = self._button("Read", "PrimaryPillButton", "Read this text")
        self.read_btn.setFixedWidth(104)
        self.read_btn.clicked.connect(self._read_input)
        buttons.addWidget(self.read_btn)
        e_lay.addLayout(buttons)
        self.stack.addWidget(editor)

        # Mode 1: the text and the result panel side by side
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(14)
        self.view = ReaderView()
        self.view.char_clicked.connect(self._on_char_clicked)
        self.view.range_selected.connect(self._on_range_selected)
        self.view.menu_requested.connect(self._show_menu)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.view.resized.connect(self._restore_top)
        self.view.touched.connect(self._stop_restoring)
        self.view.verticalScrollBar().actionTriggered.connect(lambda *_: self._stop_restoring())  # wheel, drag, page keys
        self.splitter.addWidget(self.view)
        self.panel = ReaderResultPanel(main_win, self)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.stack.addWidget(self.splitter)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(1500)
        self.save_timer.timeout.connect(self.save_state)

        self.text, self._top_offset = reader.load_state(READER_FILE)
        self.apply_theme()

    def _button(self, label: str, name: str, tip: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName(name)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(38)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setToolTip(tip)
        return btn

    # --- modes ---
    def start(self) -> None:
        """The page is opened from the home screen or the menu."""
        if not self._started:
            self._started = True
            if self.text:
                self.show_reading()
            else:
                self.show_editor()
        else:
            self.refresh_marks()  # the saved words may have changed since the page was last open
        if self.stack.currentIndex() == 1:
            self.view.setFocus()

    def show_editor(self) -> None:
        self.input.setPlainText(self.text)
        self.stack.setCurrentIndex(0)
        self.edit_btn.hide()
        self.cancel_btn.setVisible(bool(self.text))
        self.legend.hide()
        self.stats_label.hide()
        self.input.setFocus()

    def show_reading(self) -> None:
        if not self.text:
            self.show_editor()
            return
        self.stack.setCurrentIndex(1)
        self.edit_btn.show()
        self.legend.show()
        self.stats_label.show()
        if not self._rendered:
            self._render()
        self.view.setFocus()

    def _update_input_count(self) -> None:
        n = len(reader.WORD_RE.findall(self.input.toPlainText()))
        self.count_label.setText(f"{n:,} words" if n else "")

    def _paste(self) -> None:
        self.input.insertPlainText(QApplication.clipboard().text(QClipboard.Mode.Clipboard))

    def _read_input(self) -> None:
        self.set_text(self.input.toPlainText())

    def set_text(self, raw: str) -> None:
        """Make `raw` the text being read (an empty one returns to the paste box) and keep it for next time."""
        text, clipped = reader.clip_text(reader.normalise_text(raw).strip())
        self.text = text
        self._top_offset = 0
        self.save_state()
        if not text:
            self.view.clear()
            self.marked = []
            self._rendered = False
            self.panel.reset()
            self.show_editor()
            return
        self._render()
        self.show_reading()
        self.panel.reset()
        note = f"The text was cut to its first {reader.MAX_CHARS:,} characters." if clipped else ""
        self.panel.show_message(note or "Click a word to look it up, or select several words to look up the phrase.")

    # --- the text and its marks ---
    def _render(self) -> None:
        self._restoring = True
        self.view.setPlainText(self.text)
        block = QTextBlockFormat()
        block.setLineHeight(LINE_HEIGHT_PERCENT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor = QTextCursor(self.view.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeBlockFormat(block)
        cursor.clearSelection()
        self.units = reader.Utf16Map(self.text)
        self._rendered = True
        self.refresh_marks()
        self.view.setTextCursor(QTextCursor(self.view.document()))
        self._restoring = False
        # The line wrapping (so the height of everything above the place) is only known at the view's final
        # width, which it gets when the page is first shown and the splitter settles: restore on each resize
        # until you scroll yourself.
        self._restore_pending = self._top_offset > 0
        QTimer.singleShot(0, self._restore_top)
        QTimer.singleShot(1500, self._stop_restoring)  # by then the layout has settled

    def _stop_restoring(self) -> None:
        if self._restore_pending:
            self._restore_pending = False
            self._on_scrolled()

    def _restore_top(self) -> None:
        if not self._restore_pending or not self.text or not self.view.isVisible():
            return
        self._restoring = True
        cursor = QTextCursor(self.view.document())
        cursor.setPosition(self.units.to_units(min(self._top_offset, len(self.text))))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.value() + self.view.cursorRect(cursor).top())
        self._restoring = False

    def min_level(self) -> Optional[str]:
        level = self.main_win.settings.data.get("reader_min_level", "B2")
        return level if level in word_levels.LEVELS else None

    def refresh_marks(self) -> None:
        """Work out the marks again (the saved words, the level or the theme changed) and show them."""
        if not self.text or not self._rendered:
            return
        try:
            keys = [e["query_key"] for e in self.main_win.db.list_entries(favorites_only=True)]
        except Exception:
            log.exception("Could not read the saved words for the Reader")
            keys = []
        self.saved = reader.SavedWords(keys)
        self.marked = reader.analyse(self.text, self.saved, self.level_of, self.min_level())
        self._apply_marks()
        counts = reader.count_marks(self.marked)
        level = self.min_level()
        self.stats_label.setText(
            f"{len(self.marked):,} words \u00b7 {counts[reader.SAVED]:,} saved"
            + (f" \u00b7 {counts[reader.ABOVE]:,} at {level} or above" if level else ""))

    def _apply_marks(self) -> None:
        c = THEME_COLORS
        saved_fmt, above_fmt = QTextCharFormat(), QTextCharFormat()
        saved_fmt.setBackground(QColor(c["mark_saved_bg"]))
        above_fmt.setBackground(QColor(c["mark_above_bg"]))
        # A second cue besides colour: words above your level also get a dashed underline.
        above_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
        above_fmt.setUnderlineColor(QColor(c["text_main"]))
        formats = {reader.SAVED: saved_fmt, reader.ABOVE: above_fmt}
        doc = self.view.document()
        selections = []
        for m in self.marked:
            fmt = formats.get(m.mark)
            if fmt is None:
                continue
            cursor = QTextCursor(doc)
            cursor.setPosition(self.units.to_units(m.token.start))
            cursor.setPosition(self.units.to_units(m.token.end), QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            selections.append(sel)
        self.view.setExtraSelections(selections)

    def apply_theme(self) -> None:
        c = THEME_COLORS
        self.legend.setText(
            f"<span style='background:{c['mark_saved_bg']}; color:{c['text_main']};'>&nbsp;saved word&nbsp;</span>"
            f"&nbsp;&nbsp;<span style='background:{c['mark_above_bg']}; color:{c['text_main']}; "
            f"text-decoration: underline;'>&nbsp;above your level&nbsp;</span>"
            "&nbsp;&nbsp;Words missing from the level list (names, rare words) are not marked."
        )
        self.refresh_marks()

    def _on_level_picked(self, level: str) -> None:
        self.main_win.settings.data["reader_min_level"] = level
        self.main_win.settings.save()
        self.refresh_marks()

    # --- reading position ---
    def _on_scrolled(self) -> None:
        if self._restore_pending:
            return  # the view is still settling into its final size; that is not you scrolling
        if not self._restoring and self._rendered and self.stack.currentIndex() == 1:
            self._top_offset = self.units.to_chars(self.view.cursorForPosition(QPoint(20, 16)).position())
            self.save_timer.start()

    def save_state(self) -> None:
        self.save_timer.stop()
        if not reader.save_state(READER_FILE, self.text, self._top_offset):
            log.warning("Could not save the Reader text to %s", READER_FILE)

    def hideEvent(self, event) -> None:
        if self.text:
            self.save_state()
        super().hideEvent(event)

    # --- lookups ---
    def _on_char_clicked(self, pos: int) -> None:
        i = reader.token_at(self.marked, self.units.to_chars(pos))
        if i is not None:
            self._lookup_words(i, i)

    def _on_range_selected(self, start: int, end: int) -> None:
        found = reader.tokens_in(self.marked, self.units.to_chars(start), self.units.to_chars(end))
        if found is not None:
            self._lookup_words(*found)

    def _lookup_words(self, first: int, last: int, save: bool = False) -> None:
        if last - first + 1 > reader.MAX_LOOKUP_WORDS:
            self.panel.show_message(f"Select a shorter phrase (up to {reader.MAX_LOOKUP_WORDS} words).")
            return
        term = reader.lookup_term(self.marked, first, last)
        if first == last and term.lower() != self.marked[first].token.text.lower():
            note = f"{self.marked[first].token.text} → {term}"
        else:
            note = ""
        if term.lower() == self.pending_term.lower() and not save:
            return  # the same word again (e.g. a double-click): already shown or on its way
        self.lookup(term, note=note, save_after=term if save else None)

    def lookup(self, term: str, force_refresh: bool = False,
               save_after: Optional[str] = None, record: bool = True, note: str = "") -> None:
        """Show the entry for `term` in the panel: from the saved copy when there is one, else fetched on a
        worker thread (offline-only mode uses local data only, the engine takes care of that)."""
        term = term.strip()
        if not term:
            return
        db = self.main_win.db
        self.pending_term = term
        self.save_after = save_after
        self._stale_copy = None
        self._note = note
        if not force_refresh:
            if record:
                db.record_lookup(term)  # a lookup you asked for counts, like one from the search box
            cached = db.get_entry(term)
            if cached and (cached.get("senses") or cached.get("is_sentence")):
                if self.main_win._should_refresh(cached):
                    self._stale_copy = cached  # shown instead if the refetch fails or comes back empty
                else:
                    self.worker = None
                    cached["_from_history"] = True
                    self._show(term, cached)
                    return
        self.panel.show_message(f"Looking up '{term}'...")
        self.worker = EngineWorker(term)
        self.worker.finished_success.connect(self._on_success)
        self.worker.finished_error.connect(self._on_error)
        start_worker(self.worker)

    def _show(self, term: str, data: Dict[str, Any], note: Optional[str] = None) -> None:
        if self.save_after and self.save_after.lower() == term.lower():
            self.save_after = None
            if not data.get("_is_favorite") and self.main_win.db.get_entry(term):
                self.main_win.db.toggle_favorite(term)
                data = self.main_win.db.get_entry(term) or data
            self.main_win.refresh_review_badges()
            self.main_win.right_drawer.refresh_history()
            self.refresh_marks()
        self.panel.show_result(term, data, self._note if note is None else note)

    def _on_success(self, headword: str, data: Dict[str, Any]) -> None:
        if self.sender() is not self.worker:
            return  # a newer lookup superseded this one
        self.worker = None
        term = self.pending_term
        stale, self._stale_copy = self._stale_copy, None
        if stale is not None and not storage.refresh_replaces(stale, data):
            self._show(term, stale, "Couldn't refresh - showing your saved copy")
            return
        lexical_engine.apply_user_overrides(data)  # a refresh must not undo your manual Persian edits
        self.main_win.db.save_entry(term, data)
        self.main_win.right_drawer.refresh_history()
        self._show(term, self.main_win.db.get_entry(term) or data)

    def _on_error(self, message: str) -> None:
        if self.sender() is not self.worker:
            return
        self.worker = None
        stale, self._stale_copy = self._stale_copy, None
        if stale is not None:
            self._show(self.pending_term, stale, "Couldn't refresh - showing your saved copy")
            return
        self.save_after = None
        failed, self.pending_term = self.pending_term, ""  # clicking the word again tries again
        self.panel.show_message(f"Couldn't look up '{failed}': {message}")

    # --- right-click menu: look up / save in one step ---
    def _selection_words(self, char: int) -> Optional[tuple]:
        """The words to act on for a right-click at document position `char`: the selected words when the
        click is inside the selection, otherwise the word under the pointer."""
        cursor = self.view.textCursor()
        if cursor.hasSelection() and cursor.selectionStart() <= char <= cursor.selectionEnd():
            return reader.tokens_in(self.marked, self.units.to_chars(cursor.selectionStart()),
                                    self.units.to_chars(cursor.selectionEnd()))
        i = reader.token_at(self.marked, self.units.to_chars(char)) if char >= 0 else None
        return None if i is None else (i, i)

    def _show_menu(self, global_pos: QPoint, char: int) -> None:
        words = self._selection_words(char)
        options: List[str] = []
        term = ""
        if words and words[1] - words[0] + 1 <= reader.MAX_LOOKUP_WORDS:
            term = reader.lookup_term(self.marked, *words)
            entry = self.main_win.db.get_entry(term)
            options.append(f"Look up \"{term}\"")
            options.append(f"Remove \"{term}\" from saved words" if entry and entry.get("_is_favorite") else f"Save word \"{term}\"")
        if self.view.textCursor().hasSelection():
            options.append("Copy")
        options.append("Select All")

        menu = GlassPopupMenu(self.main_win)
        menu.setStyleSheet(self.main_win.styleSheet())
        menu.option_picked.connect(lambda act: self._menu_action(act, term, words))
        for opt in options:
            item = QListWidgetItem(f"   {opt}")
            item.setData(Qt.ItemDataRole.UserRole, opt)
            menu.list_widget.addItem(item)
        menu.setFixedSize(max(240, min(420, max(len(o) for o in options) * 9 + 40)), len(options) * 40 + 20)
        menu.move(global_pos)
        menu.show()
        self._menu = menu

    def _menu_action(self, action: str, term: str, words: Optional[tuple]) -> None:
        if action.startswith("Look up") and words:
            self._lookup_words(*words)
        elif action.startswith("Save word") and words:
            self.save_word(words)
        elif action.startswith("Remove") and words:
            if self.main_win.db.get_entry(term):
                self.main_win.db.toggle_favorite(term)
                self.main_win.refresh_review_badges()
                self.main_win.right_drawer.refresh_history()
                self.refresh_marks()
                if self.panel.current_term and self.panel.current_term.lower() == term.lower():
                    self.lookup(term, record=False)  # redraw the panel with the star off
        elif action == "Copy":
            self.view.copy()
        elif action == "Select All":
            self.view.selectAll()

    def save_word(self, words: tuple) -> None:
        """Save the word or phrase in one step. Saving needs an entry, so a word that was never looked up is
        looked up first (shown in the panel) and saved when the result arrives."""
        term = reader.lookup_term(self.marked, *words)
        entry = self.main_win.db.get_entry(term)
        if entry and (entry.get("senses") or entry.get("is_sentence")):
            self.pending_term = term
            self.save_after = term
            self._note = ""
            self._show(term, entry)
        else:
            self.lookup(term, save_after=term)
