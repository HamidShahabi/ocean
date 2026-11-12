"""The main window: top bar, home page and navigation. It wires the pages together; the pages
themselves live in results_page.py and drawer.py (history, settings)."""
import copy
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QEvent, QObject, QPoint, QSize, QTimer
from PyQt6.QtGui import QCursor, QIcon, QKeySequence, QShortcut, QTextDocumentFragment
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import desktop_lookup
import lexical_engine
import report_export
import storage
from ui import theme
from ui.config import DB_FILE, SettingsManager
from ui.drawer import AttachedRightDrawer
from ui.motion import FadeStackedWidget
from ui.results_page import ResultsPage
from ui.speaker import NeuralSpeaker
from ui.theme import THEME_COLORS, VectorIcons
from ui.widgets import GlassPopupMenu, GlassSuggestionPopup, PersianTranslationWidget, SmoothGlassFrame
from ui.workers import EngineWorker, SuggestionWorker, start_worker

log = logging.getLogger("ocean.app")

_FILTERED_EVENTS = frozenset({
    QEvent.Type.ContextMenu, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.WindowDeactivate, QEvent.Type.KeyPress,
})


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ocean")
        self.setWindowIcon(VectorIcons.ocean_app_icon())
        self.resize(1220, 850)

        self.settings = SettingsManager()
        self.db = storage.HistoryStore(DB_FILE)
        self.reports = storage.ReportStore(DB_FILE)
        self.speaker = NeuralSpeaker(self.settings)
        self.current_term: Optional[str] = None
        self.current_data: Optional[Dict[str, Any]] = None
        self.worker: Optional[EngineWorker] = None
        self.sug_worker: Optional[SuggestionWorker] = None

        self._stale_copy: Optional[Dict[str, Any]] = None  # saved copy being refreshed, shown if the refetch fails
        # Back/forward steps: {"term", "payload"}. A step with a payload shows that saved output as it was
        # (the result before a refresh); one without it looks the term up (the history serves the latest).
        self.nav_history: List[Dict[str, Any]] = []
        self.nav_index: int = -1
        self._navigating_history: bool = False
        self._refreshing: bool = False  # a manual Refresh is running: the current result stays on screen
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_flash_status)
        self._flash_text = ""

        self.sug_popup = GlassSuggestionPopup(self)
        self.sug_popup.item_selected.connect(self._on_suggestion_picked)

        self._build_ui()
        self._apply_theme()

        QApplication.instance().installEventFilter(self)

    def _show_custom_context_menu(self, widget: QWidget, global_pos: QPoint) -> None:
        selected_txt = ""
        if isinstance(widget, QLineEdit):
            selected_txt = widget.selectedText().strip()
        elif isinstance(widget, QLabel):
            selected_txt = widget.selectedText().strip()

        clean_sel = re.sub(r"[^a-zA-Z0-9\s\-']", "", selected_txt).strip()
        options: List[str] = []
        if clean_sel and len(clean_sel) <= 65:
            options.append(f"Search \"{clean_sel}\" in Ocean")

        persian_owner: Optional[PersianTranslationWidget] = widget.property("persian_owner") if isinstance(widget, QLabel) else None
        if persian_owner is not None:
            options.append("Edit Persian Translation")

        if isinstance(widget, QLineEdit):
            if selected_txt:
                options.extend(["Copy", "Cut"])
            options.extend(["Paste", "Select All", "Clear"])
        elif isinstance(widget, QLabel):
            if selected_txt:
                options.append("Copy")
            options.append("Select All")

        if not options:
            return

        menu = GlassPopupMenu(self)
        menu.setStyleSheet(self.styleSheet())

        def _handle_action(act: str) -> None:
            if act.startswith("Search \""):
                self._analyze_input(force_refresh=False, override_term=clean_sel)
            elif act == "Edit Persian Translation" and persian_owner:
                persian_owner.trigger_manual_edit()
            elif act == "Copy":
                if isinstance(widget, QLineEdit):
                    widget.copy()
                elif isinstance(widget, QLabel) and selected_txt:
                    QApplication.clipboard().setText(selected_txt)
            elif act == "Cut" and isinstance(widget, QLineEdit):
                widget.cut()
            elif act == "Paste" and isinstance(widget, QLineEdit):
                widget.paste()
            elif act == "Select All":
                if isinstance(widget, QLineEdit):
                    widget.selectAll()
                elif isinstance(widget, QLabel):
                    plain = " ".join(QTextDocumentFragment.fromHtml(widget.text()).toPlainText().split())
                    QApplication.clipboard().setText(plain)
            elif act == "Clear" and isinstance(widget, QLineEdit):
                widget.clear()

        menu.option_picked.connect(_handle_action)
        menu.list_widget.clear()
        for opt in options:
            it = QListWidgetItem(opt)
            it.setData(Qt.ItemDataRole.UserRole, opt)
            menu.list_widget.addItem(it)

        w = max(240, min(380, max(len(o) for o in options) * 9 + 40))
        h = len(options) * 40 + 20
        menu.setFixedSize(w, h)
        menu.move(global_pos)
        menu.show()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Installed on the whole application, so this runs for every event: bail out early for the
        # overwhelming majority (paint, timer, mouse move...) that it never looks at.
        if event.type() not in _FILTERED_EVENTS:
            return False
        if event.type() == QEvent.Type.ContextMenu:
            if isinstance(obj, (QLineEdit, QLabel)):
                self._show_custom_context_menu(obj, QCursor.pos())
                return True
        if self.sug_popup.isVisible():
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.WindowDeactivate):
                if isinstance(obj, QWidget):
                    if not (obj is self.sug_popup or self.sug_popup.isAncestorOf(obj)):
                        self.sug_popup.hide()
            elif event.type() == QEvent.Type.KeyPress:
                if hasattr(event, "key") and event.key() == Qt.Key.Key_Escape:
                    self.sug_popup.hide()
        return super().eventFilter(obj, event)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName('CentralCanvas')
        self.setCentralWidget(central)
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(24, 16, 24, 18)
        self.root_layout.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(4, 0, 4, 0)
        top_bar.setSpacing(8)

        self.home_logo_btn = QPushButton("Ocean")
        self.home_logo_btn.setObjectName("HomeLogoButton")
        self.home_logo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_logo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.home_logo_btn.setToolTip("Return to Ocean Home")
        self.home_logo_btn.clicked.connect(self.go_home)
        top_bar.addWidget(self.home_logo_btn)

        self.back_btn = QPushButton()
        self.back_btn.setObjectName("NavCircleButton")
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_btn.setToolTip("Go Back")
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setEnabled(False)
        top_bar.addWidget(self.back_btn)

        self.fwd_btn = QPushButton()
        self.fwd_btn.setObjectName("NavCircleButton")
        self.fwd_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fwd_btn.setFixedSize(36, 36)
        self.fwd_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.fwd_btn.setToolTip("Go Forward")
        self.fwd_btn.clicked.connect(self._go_forward)
        self.fwd_btn.setEnabled(False)
        top_bar.addWidget(self.fwd_btn)

        top_bar.addStretch()

        self.status_label = QLabel("")
        self.status_label.setObjectName("MutedText")
        top_bar.addWidget(self.status_label)

        self.menu_btn = QPushButton()
        self.menu_btn.setObjectName("MenuCircleButton")
        self.menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_btn.setFixedSize(42, 42)
        self.menu_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.menu_btn.setToolTip("Open Menu")
        self.menu_btn.clicked.connect(self._toggle_right_drawer)
        top_bar.addWidget(self.menu_btn)

        self.root_layout.addLayout(top_bar)

        workspace_row = QHBoxLayout()
        workspace_row.setContentsMargins(0, 0, 0, 0)
        workspace_row.setSpacing(16)

        self.stack = FadeStackedWidget()
        workspace_row.addWidget(self.stack, stretch=1)

        self.right_drawer = AttachedRightDrawer(self)
        self.right_drawer.hide()
        workspace_row.addWidget(self.right_drawer)

        self.root_layout.addLayout(workspace_row, stretch=1)

        # VIEW 0: Centered Home Page
        self.home_page = QWidget()
        self.home_page.setObjectName('HomePage')
        self.home_vbox = QVBoxLayout(self.home_page)
        self.home_vbox.setContentsMargins(0, 0, 0, 42)
        self.home_vbox.setSpacing(22)
        self.home_vbox.addStretch(1)

        self.hero_title = QPushButton("Ocean")
        self.hero_title.setObjectName("HeroLogoText")
        self.hero_title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hero_title.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.hero_title.clicked.connect(self.go_home)
        self.home_vbox.addWidget(self.hero_title, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.search_bar_wrapper = QWidget()
        self.search_bar_wrapper.setMaximumWidth(760)
        self.search_bar_wrapper.setMinimumWidth(640)
        sb_wrap_lay = QVBoxLayout(self.search_bar_wrapper)
        sb_wrap_lay.setContentsMargins(0, 0, 0, 0)

        self.search_card = SmoothGlassFrame(radius=27, variant="search")
        self.search_card.setFixedHeight(54)
        sc_lay = QHBoxLayout(self.search_card)
        sc_lay.setContentsMargins(22, 8, 8, 8)
        sc_lay.setSpacing(10)

        self.input_box = QLineEdit()
        self.input_box.setObjectName("SearchInput")
        self.input_box.setPlaceholderText("Explore words, idioms, or phrases...")
        self.input_box.setMinimumHeight(36)
        self.input_box.returnPressed.connect(lambda: self._analyze_input(force_refresh=False))
        sc_lay.addWidget(self.input_box, stretch=1)

        self.explore_btn = QPushButton("Explore")
        self.explore_btn.setObjectName("PrimaryPillButton")
        self.explore_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.explore_btn.setFixedSize(104, 38)
        self.explore_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.explore_btn.clicked.connect(lambda: self._analyze_input(force_refresh=False))
        sc_lay.addWidget(self.explore_btn)

        sb_wrap_lay.addWidget(self.search_card)
        self.home_vbox.addWidget(self.search_bar_wrapper, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.home_vbox.addStretch(1)

        self.stack.addWidget(self.home_page)

        # VIEW 1: Results Page
        self.results_page = ResultsPage(self)
        self.stack.addWidget(self.results_page)

        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(150)
        self.debounce_timer.timeout.connect(self._trigger_live_suggestions)
        self.input_box.textEdited.connect(lambda _: self.debounce_timer.start())

        ctrl_enter = QShortcut(QKeySequence("Ctrl+Return"), self)
        ctrl_enter.activated.connect(lambda: self._analyze_input(force_refresh=False))
        self._update_nav_visibility()

    def _update_search_button_mode(self) -> None:
        fg_col = THEME_COLORS["on_primary"]
        if self.stack.currentIndex() == 0:
            self.explore_btn.setIcon(QIcon())
            self.explore_btn.setText("Explore")
            self.explore_btn.setFixedSize(104, 38)
            self.explore_btn.setToolTip("Explore")
        else:
            self.explore_btn.setText("")
            self.explore_btn.setIcon(VectorIcons.search_icon(fg_col))
            self.explore_btn.setIconSize(QSize(19, 19))
            self.explore_btn.setFixedSize(38, 38)
            self.explore_btn.setToolTip("Search")

    def _toggle_right_drawer(self) -> None:
        if self.right_drawer.isVisible():
            self.right_drawer.hide()
        else:
            self.right_drawer.show_page(0)

    def _update_nav_buttons(self) -> None:
        self.back_btn.setEnabled(self.nav_index > 0)
        self.fwd_btn.setEnabled(0 <= self.nav_index < len(self.nav_history) - 1)

    def _update_nav_visibility(self) -> None:
        """The Ocean button and Back/Forward are for pages after the home page; home itself has none of them."""
        on_home = self.stack.currentIndex() == 0
        for w in (self.home_logo_btn, self.back_btn, self.fwd_btn):
            w.setVisible(not on_home)

    def _push_nav(self, term: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """A new step after the current one (the steps that were ahead of it are dropped)."""
        self.nav_history = self.nav_history[: self.nav_index + 1]
        self.nav_history.append({"term": term, "payload": payload})
        self.nav_index = len(self.nav_history) - 1
        self._update_nav_buttons()

    def _go_back(self) -> None:
        if self.nav_index > 0:
            self._go_to_step(self.nav_index - 1)

    def _go_forward(self) -> None:
        if self.nav_index < len(self.nav_history) - 1:
            self._go_to_step(self.nav_index + 1)

    def _go_to_step(self, index: int) -> None:
        self.nav_index = index
        step = self.nav_history[index]
        self._navigating_history = True
        try:
            if step["payload"] is not None:
                self._show_payload(step["term"], step["payload"])
            else:
                self._analyze_input(force_refresh=False, override_term=step["term"])
        finally:
            self._navigating_history = False
        self._update_nav_buttons()

    def _show_payload(self, term: str, payload: Dict[str, Any]) -> None:
        """Show an output exactly as it was kept (before a refresh, or from a report), without any lookup."""
        self.sug_popup.hide()
        self.worker = None  # any lookup still running is now stale
        self._refreshing = False
        self._stale_copy = None
        self.explore_btn.setEnabled(True)
        self.input_box.setText(term)
        self._switch_to_results_view()
        saved = self.db.get_entry(term)
        data = dict(payload, _is_favorite=bool(saved and saved["_is_favorite"]))
        self.current_term = term
        self.current_data = data
        self.results_page.render(term, data)
        self.status_label.setText("")

    def open_report(self, report_id: int) -> None:
        """Re-show a reported entry as it was reported (as a step of its own, so Back returns to where you were)."""
        report = self.reports.get(report_id)
        if report is None:
            return
        payload = {k: v for k, v in report["payload"].items() if not k.startswith("_")}
        self._push_nav(report["term"], payload)
        self._show_payload(report["term"], payload)

    def go_home(self) -> None:
        self.stack.begin_fade()
        self.sug_popup.hide()
        self.input_box.clear()
        self.status_label.setText("")
        self.current_term = None
        self.current_data = None
        self.worker = None  # a lookup still running must not draw a result over the home page
        self._refreshing = False
        self._stale_copy = None
        self.explore_btn.setEnabled(True)
        self.search_bar_wrapper.setParent(self.home_page)
        self.home_vbox.insertWidget(2, self.search_bar_wrapper, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.stack.setCurrentIndex(0)
        self._update_nav_visibility()
        self._update_search_button_mode()
        self.input_box.setFocus()

    def handle_external_lookup(self, message: Dict[str, str]) -> None:
        """A hand-off from `--lookup`, a second launch, or a Windows trigger (Ctrl+C twice, Alt+Q): show the window and look up."""
        token = message.get("token", "")
        if token:
            # Lets Wayland compositors grant focus to this already-running window (xdg-activation).
            os.environ["XDG_ACTIVATION_TOKEN"] = token
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            desktop_lookup.focus_window(int(self.winId()))  # a clipboard change is no input event: Windows would only flash
        if message.get("action") != "lookup":
            return
        text = message.get("text", "")
        if not text and sys.platform == "win32":
            text = QApplication.clipboard().text()  # the hotkey path; Windows allows background reads
        text = desktop_lookup.clean_lookup_text(text)
        if text:
            log.info("External lookup of %d chars", len(text))
            self._analyze_input(force_refresh=False, override_term=text)
        else:
            self.status_label.setText(f"Nothing selected: copy a word, then press {desktop_lookup.HOTKEY_LABEL}")

    def _switch_to_results_view(self) -> None:
        if self.stack.currentIndex() != 1:
            self.stack.begin_fade()
            self.search_bar_wrapper.setParent(self.results_page)
            self.results_page.top_search_slot.addWidget(self.search_bar_wrapper)
            self.stack.setCurrentIndex(1)
            self._update_nav_visibility()
            self._update_search_button_mode()

    def _trigger_live_suggestions(self) -> None:
        txt = self.input_box.text().strip()
        if len(txt) < 2 or len(txt.split()) > 5:
            self.sug_popup.hide()
            return
        local_words = [x["display_term"] for x in self.db.list_entries(False)]
        self.sug_worker = SuggestionWorker(txt, local_words)
        self.sug_worker.suggestions_ready.connect(self._on_suggestions_ready)
        start_worker(self.sug_worker)

    def _on_suggestions_ready(self, items: List[str]) -> None:
        if self.sender() is not self.sug_worker:
            return  # superseded by a newer keystroke
        if not items or not self.input_box.hasFocus():
            self.sug_popup.hide()
            return
        self.sug_popup.show_suggestions(items, self.search_card)

    def _on_suggestion_picked(self, text: str) -> None:
        self.sug_popup.hide()
        self.input_box.setText(text)
        self._analyze_input(force_refresh=False)

    def _analyze_input(self, force_refresh: bool = False, override_term: Optional[str] = None) -> None:
        """Look a term up. `force_refresh` (the Refresh button) skips the saved copy and asks the engine for a
        better result, while the current one stays on screen."""
        self.sug_popup.hide()
        if override_term:
            self.input_box.setText(override_term)
        term = self.input_box.text().strip()
        if not term:
            return

        if not self._navigating_history:
            if self.nav_index == -1 or self.nav_history[self.nav_index]["term"].lower() != term.lower():
                self._push_nav(term)
            self._update_nav_buttons()

        self._switch_to_results_view()

        if not force_refresh and not self._navigating_history:
            # A user-initiated lookup (cached or fresh) goes to the top of the history; refreshes and back/forward do not.
            self.db.touch_entry(term)
        self._stale_copy = None
        self._refreshing = force_refresh
        if force_refresh:
            if self.current_data is not None and (self.current_term or "").strip().lower() == term.lower():
                self._stale_copy = self.current_data  # what is on screen: kept if the refresh brings nothing better
        else:
            cached = self.db.get_entry(term)
            if cached and (cached.get("senses") or cached.get("is_sentence")):
                if self._should_refresh(cached):
                    self._stale_copy = cached  # shown instead if the refetch fails or comes back empty
                    cached = None
            if cached and (cached.get("senses") or cached.get("is_sentence")):
                self.worker = None  # any lookup still running is now stale
                self.explore_btn.setEnabled(True)
                self.current_term = term
                self.current_data = cached
                self.results_page.render(term, cached)
                self.status_label.setText("")
                return

        self.explore_btn.setEnabled(False)
        if force_refresh:
            self.status_label.setText(f"Refreshing '{term}' with online resources...")
        elif self._stale_copy is not None:
            self.status_label.setText(f"Refreshing '{term}'...")
        else:
            self.status_label.setText(f"Analyzing '{term}'...")

        # The one place a refresh reaches the engine (see EngineWorker): online sources are queried again.
        self.worker = EngineWorker(term, refresh=True) if force_refresh else EngineWorker(term)
        self.worker.preview_ready.connect(self._on_preview)
        self.worker.finished_success.connect(self._on_inference_success)
        self.worker.finished_error.connect(self._on_inference_error)
        start_worker(self.worker)

    def refresh_current(self) -> None:
        """The Refresh button: fetch an improved result for what is on screen."""
        if self.worker is not None:
            return  # a lookup is already running
        term = self.current_term or self.input_box.text().strip()
        if term:
            self._analyze_input(force_refresh=True, override_term=term)

    def _should_refresh(self, cached: Dict[str, Any]) -> bool:
        """A saved entry older than the Settings refresh period (or from an older engine) is fetched again
        (if that fails, e.g. without internet, the saved copy is shown)."""
        days = storage.refresh_period_days(self.settings.data.get("refresh_period"))
        return self.db.is_entry_stale(cached, days)

    def _show_stale_copy(self, stale: Dict[str, Any], note: str) -> None:
        """The refetch failed or yielded nothing useful: keep showing (and keep) the saved copy."""
        term = self.input_box.text().strip()
        self.current_term = term
        self.current_data = stale
        self.results_page.render(term, stale)
        self.status_label.setText(note)

    def _on_preview(self, term: str, data: Dict[str, Any]) -> None:
        """Show what the local data says straight away; the full entry replaces it when its Persian is ready
        (or, for a refresh that fails, the saved copy does)."""
        if self.sender() is not self.worker or self._refreshing:
            return  # (a manual refresh keeps the result on screen until the improved one is ready)
        lexical_engine.apply_user_overrides(data)
        self.current_term = term
        self.current_data = None  # nothing to edit or save until the full entry arrives
        self.results_page.render(term, data)
        self.status_label.setText(f"Translating '{term}'...")

    def _on_inference_success(self, term: str, data: Dict[str, Any]) -> None:
        if self.sender() is not self.worker:
            return  # a newer search or history navigation superseded this lookup
        self.worker = None
        self.explore_btn.setEnabled(True)
        stale, self._stale_copy = self._stale_copy, None
        manual, self._refreshing = self._refreshing, False
        if stale is not None and not storage.refresh_replaces(stale, data):
            self._show_stale_copy(stale, "No better result found - kept the current one" if manual
                                  else "Couldn't refresh - showing your saved copy")
            return
        lexical_engine.apply_user_overrides(data)  # a refresh must not undo your manual Persian edits
        self.input_box.setText(data.get("headword", term))
        self.db.save_entry(term, data)
        saved = self.db.get_entry(term) or data
        self.current_term = term
        self.current_data = saved
        self.right_drawer.refresh_history()
        if manual and stale is not None:
            self._record_refresh_step(term, stale, saved)
        self.results_page.render(term, saved)
        self.status_label.setText("")
        if manual:
            self._flash_status("Refreshed" if not self._same_output(stale, saved) else "Already the best result")

    @staticmethod
    def _same_output(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
        def strip(d: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            return {k: v for k, v in (d or {}).items() if not k.startswith("_")}
        return strip(a) == strip(b)

    def _record_refresh_step(self, term: str, before: Dict[str, Any], after: Dict[str, Any]) -> None:
        """Make the output before a refresh a Back step of its own: Back shows it again, Forward the refreshed one."""
        if self._same_output(before, after):
            return
        step = self.nav_history[self.nav_index] if 0 <= self.nav_index < len(self.nav_history) else None
        if step is None or step["term"].lower() != term.lower():
            self._push_nav(term)
        self.nav_history[self.nav_index]["payload"] = copy.deepcopy({k: v for k, v in before.items() if not k.startswith("_")})
        self._push_nav(term)

    def _on_inference_error(self, error_msg: str) -> None:
        if self.sender() is not self.worker:
            return
        self.worker = None
        self.explore_btn.setEnabled(True)
        stale, self._stale_copy = self._stale_copy, None
        manual, self._refreshing = self._refreshing, False
        if stale is not None:
            self._show_stale_copy(stale, "Couldn't refresh - kept the current result" if manual
                                  else "Couldn't refresh - showing your saved copy")
            return
        self.status_label.setText("")
        QMessageBox.warning(self, "Ocean Notice", error_msg)

    # --- status line and reports ---
    def _flash_status(self, text: str, ms: int = 4000) -> None:
        """Show `text` in the top bar for a few seconds (unless something else replaces it first)."""
        self.status_label.setText(text)
        self._flash_text = text
        self._status_timer.start(ms)

    def _clear_flash_status(self) -> None:
        if self.status_label.text() == self._flash_text:
            self.status_label.setText("")

    def report_result(self, term: str, data: Dict[str, Any], snapshot: Optional[bytes]) -> int:
        """Record a report of the result on screen (the "!" button) and confirm it in the top bar."""
        report_id = self.reports.add(term, data, snapshot=snapshot, app_version=report_export.app_version())
        self.reports_changed()
        self._flash_status(f"Reported '{term}' - see Menu > Reports")
        return report_id

    def reports_changed(self) -> None:
        """Reports were added, edited or deleted: bring the Reports page and the "!" button up to date."""
        if self.right_drawer.isVisible():
            self.right_drawer.reports_page.populate()
        self.results_page.sync_report_button()

    def _apply_theme(self) -> None:
        qss = theme.apply_theme(self.settings.data)

        c = THEME_COLORS
        theme.sync_windows_titlebar(self, theme.is_dark_theme(self.settings.data), c["bg_main"], c["text_main"])

        icon_fg = c["text_main"]
        self.menu_btn.setIcon(VectorIcons.hamburger_icon(icon_fg))
        self.menu_btn.setIconSize(QSize(22, 22))
        self.back_btn.setIcon(VectorIcons.arrow_icon("left", icon_fg))
        self.back_btn.setIconSize(QSize(18, 18))
        self.fwd_btn.setIcon(VectorIcons.arrow_icon("right", icon_fg))
        self.fwd_btn.setIconSize(QSize(18, 18))
        self.right_drawer.apply_icons(icon_fg, c["accent"])
        self._update_search_button_mode()

        self.setStyleSheet(qss)
        self.sug_popup.setStyleSheet(qss)
        self.right_drawer.update()
        self.update()
