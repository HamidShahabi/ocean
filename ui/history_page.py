"""Drawer page listing past lookups: favourite toggling, deleting and re-opening."""
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import ModernGlassDropdown, SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class HistoryItemRowWidget(QWidget):
    """Multi-line capable History row with IN-PLACE star toggling (zero scroll jumping!)."""

    star_toggled = pyqtSignal(str, object)
    term_clicked = pyqtSignal(str)

    def __init__(self, query_key: str, display_term: str, is_fav: bool) -> None:
        super().__init__()
        self.query_key = query_key
        self.is_fav = is_fav

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        self.star_btn = QPushButton("\u2605" if is_fav else "\u2606")
        self.star_btn.setObjectName("HistoryStarButtonActive" if is_fav else "HistoryStarButton")
        self.star_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.star_btn.setFixedSize(30, 30)
        self.star_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.star_btn.clicked.connect(lambda: self.star_toggled.emit(self.query_key, self))
        lay.addWidget(self.star_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.term_lbl = QLabel(display_term)
        self.term_lbl.setWordWrap(True)
        self.term_lbl.setObjectName("HistoryTermLabel")
        self.term_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay.addWidget(self.term_lbl, 1)

    def set_favorite_visual(self, fav: bool) -> None:
        self.is_fav = fav
        self.star_btn.setText("\u2605" if fav else "\u2606")
        self.star_btn.setObjectName("HistoryStarButtonActive" if fav else "HistoryStarButton")
        self.star_btn.style().unpolish(self.star_btn)
        self.star_btn.style().polish(self.star_btn)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.term_clicked.emit(self.query_key)
        super().mouseReleaseEvent(event)


FILTER_ALL = "All Recent Searches"


class HistoryPage(QWidget):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win

        h_lay = QVBoxLayout(self)
        h_lay.setContentsMargins(0, 4, 0, 0)
        h_lay.setSpacing(10)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("DrawerInput")
        self.search_box.setPlaceholderText("Search history (English or \u0641\u0627\u0631\u0633\u06cc)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedHeight(36)
        h_lay.addWidget(self.search_box)
        # Filtering as you type re-reads the history, so wait for a short pause in typing.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self.populate)
        self.search_box.textChanged.connect(lambda _: self._search_timer.start())

        self.filter_drop = ModernGlassDropdown([FILTER_ALL, "Saved Favorites Only"], FILTER_ALL, self)
        self.filter_drop.valueChanged.connect(lambda _: self.populate())
        h_lay.addWidget(self.filter_drop)

        list_box = SmoothGlassFrame(radius=16, variant="inner")
        lb_lay = QVBoxLayout(list_box)
        lb_lay.setContentsMargins(8, 8, 8, 8)
        self.hist_list = QListWidget()
        self.hist_list.setObjectName("PopupList")
        self.hist_list.setFrameShape(QFrame.Shape.NoFrame)
        self.hist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lb_lay.addWidget(self.hist_list)
        h_lay.addWidget(list_box, stretch=1)

        del_btn = QPushButton("Delete Selected")
        del_btn.setObjectName("GlassPillButton")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.setFixedHeight(36)
        del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        del_btn.clicked.connect(self._delete_hist_item)

        h_lay.addWidget(del_btn)

    def on_show(self) -> None:
        self.populate()

    def _favorites_only(self) -> bool:
        return self.filter_drop.currentText() != FILTER_ALL

    def _current_entries(self):
        """Entries for the current search text and favourites filter."""
        return self.main_win.db.list_entries(
            favorites_only=self._favorites_only(),
            query=self.search_box.text(),
        )

    def populate(self) -> None:
        self._search_timer.stop()  # a pending keystroke refresh would only repeat this one
        self.hist_list.clear()
        for item in self._current_entries():
            disp = item["display_term"]
            # Dynamically calculate row height so multi-line sentences never get cut off!
            lines = max(1, (len(disp) // 34) + 1)
            row_h = max(44, lines * 24 + 16)
            lw = QListWidgetItem()
            lw.setData(Qt.ItemDataRole.UserRole, item["query_key"])
            lw.setSizeHint(QSize(0, row_h))
            row_w = HistoryItemRowWidget(item["query_key"], disp, item["is_favorite"])
            row_w.star_toggled.connect(self._on_row_star_toggled)
            row_w.term_clicked.connect(self._on_row_term_clicked)
            self.hist_list.addItem(lw)
            self.hist_list.setItemWidget(lw, row_w)

    def _on_row_star_toggled(self, key: str, row_widget: Optional[HistoryItemRowWidget] = None) -> None:
        new_state = self.main_win.db.toggle_favorite(key)
        if self._favorites_only() and not new_state:
            scroll_pos = self.hist_list.verticalScrollBar().value()
            self.populate()
            QTimer.singleShot(5, lambda: self.hist_list.verticalScrollBar().setValue(scroll_pos))
        elif row_widget is not None:
            row_widget.set_favorite_visual(new_state)

        if self.main_win.current_term and self.main_win.current_term.strip().lower() == key.lower():
            updated = self.main_win.db.get_entry(key)
            if updated:
                self.main_win.current_data = updated
                self.main_win.results_page.render(self.main_win.current_term, updated)

    def _on_row_term_clicked(self, key: str) -> None:
        self.main_win._analyze_input(force_refresh=False, override_term=key)

    def _delete_hist_item(self) -> None:
        item = self.hist_list.currentItem()
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                self.main_win.db.delete_entry(key)
                self.populate()
