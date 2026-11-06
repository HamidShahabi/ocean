"""Drawer page listing past lookups: favourite toggling, multi-select bulk delete and re-opening."""
import math
from typing import List, Optional, Set, TYPE_CHECKING

from PyQt6.QtCore import QPointF, Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
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

from ui.confirm_bar import ConfirmBar
from ui.theme import THEME_COLORS
from ui.widgets import ModernGlassDropdown, SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class StarButton(QAbstractButton):
    """A favourite star drawn by hand, so its centre is exactly the centre of the button (a star glyph
    sits low in its text box and cannot be centred reliably across fonts)."""

    RADIUS = 7.0  # circumscribed radius of the star in pixels

    def __init__(self, filled: bool) -> None:
        super().__init__()
        self.filled = filled
        self.setFixedSize(24, 24)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _polygon(self) -> QPolygonF:
        r = self.RADIUS
        # A five-pointed star's bounding box reaches r above its circle centre but only r*cos(36deg) below
        # it: shift the circle centre down by half the difference so the box, not the circle, is centred.
        cx, cy = self.width() / 2, self.height() / 2 + r * (1 - math.cos(math.radians(36))) / 2
        pts = []
        for i in range(10):
            ang = math.radians(-90 + i * 36)
            rad = r if i % 2 == 0 else r * 0.4
            pts.append(QPointF(cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        return QPolygonF(pts)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hot = self.underMouse()
        if self.filled:
            color = QColor(THEME_COLORS["accent"])
        else:
            color = QColor(THEME_COLORS["accent"] if hot else THEME_COLORS["text_muted"])
        pen = QPen(color, 1.4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(color if self.filled else Qt.BrushStyle.NoBrush)
        p.drawPolygon(self._polygon())

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class HistoryItemRowWidget(QWidget):
    """History row: select checkbox, favourite star (toggled in place, so the list never jumps) and the term."""

    star_toggled = pyqtSignal(str, object)
    term_clicked = pyqtSignal(str)
    check_toggled = pyqtSignal(str, bool)

    def __init__(self, query_key: str, display_term: str, is_fav: bool, checked: bool = False) -> None:
        super().__init__()
        self.query_key = query_key
        self.is_fav = is_fav

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        self.check = QCheckBox()
        self.check.setObjectName("HistoryCheck")
        self.check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.check.setFixedSize(20, 24)
        self.check.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.check.setToolTip("Select for deleting")
        self.check.setChecked(checked)
        self.check.toggled.connect(lambda on: self.check_toggled.emit(self.query_key, on))
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)

        self.star_btn = StarButton(is_fav)
        self.star_btn.setToolTip("Favourite")
        self.star_btn.clicked.connect(lambda: self.star_toggled.emit(self.query_key, self))
        lay.addWidget(self.star_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.term_lbl = QLabel(display_term)
        self.term_lbl.setWordWrap(True)
        self.term_lbl.setObjectName("HistoryTermLabel")
        self.term_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay.addWidget(self.term_lbl, 1, Qt.AlignmentFlag.AlignVCenter)

    def set_favorite_visual(self, fav: bool) -> None:
        self.is_fav = fav
        self.star_btn.filled = fav
        self.star_btn.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.term_clicked.emit(self.query_key)
        super().mouseReleaseEvent(event)


FILTER_ALL = "All Recent Searches"
FILTER_FAVORITES = "Favorites Only"


class HistoryPage(QWidget):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self._checked: Set[str] = set()  # query keys ticked for deleting (only listed ones count)
        self._listed: List[str] = []

        h_lay = QVBoxLayout(self)
        h_lay.setContentsMargins(0, 4, 0, 0)
        h_lay.setSpacing(10)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("DrawerInput")
        self.search_box.setPlaceholderText("Search history (English or فارسی)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedHeight(36)
        h_lay.addWidget(self.search_box)
        # Filtering as you type re-reads the history, so wait for a short pause in typing.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self.populate)
        self.search_box.textChanged.connect(lambda _: self._search_timer.start())

        self.filter_drop = ModernGlassDropdown([FILTER_ALL, FILTER_FAVORITES], FILTER_ALL, self)
        self.filter_drop.valueChanged.connect(lambda _: self.populate())
        h_lay.addWidget(self.filter_drop)

        select_row = QHBoxLayout()
        select_row.setContentsMargins(8, 0, 8, 0)
        select_row.setSpacing(6)
        self.select_all = QCheckBox("Select all")
        self.select_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.select_all.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.select_all.toggled.connect(self._on_select_all)
        select_row.addWidget(self.select_all)
        select_row.addStretch()
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("MutedText")
        select_row.addWidget(self.count_lbl)
        h_lay.addLayout(select_row)

        list_box = SmoothGlassFrame(radius=16, variant="inner")
        lb_lay = QVBoxLayout(list_box)
        lb_lay.setContentsMargins(8, 8, 8, 8)
        self.hist_list = QListWidget()
        self.hist_list.setObjectName("PopupList")
        self.hist_list.setFrameShape(QFrame.Shape.NoFrame)
        self.hist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lb_lay.addWidget(self.hist_list)
        h_lay.addWidget(list_box, stretch=1)

        self.confirm = ConfirmBar()
        h_lay.addWidget(self.confirm)

        self.del_btn = QPushButton("Delete Selected")
        self.del_btn.setObjectName("GlassPillButton")
        self.del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.del_btn.setFixedHeight(36)
        self.del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.del_btn.clicked.connect(self._ask_delete_checked)
        h_lay.addWidget(self.del_btn)

    def on_show(self) -> None:
        self.confirm.hide()
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
        entries = self._current_entries()
        self._listed = [item["query_key"] for item in entries]
        self._checked &= set(self._listed)  # an entry that is no longer listed cannot be deleted by mistake
        for item in entries:
            disp = item["display_term"]
            # Dynamically calculate row height so multi-line sentences never get cut off!
            lines = max(1, (len(disp) // 30) + 1)
            row_h = max(44, lines * 24 + 16)
            lw = QListWidgetItem()
            lw.setData(Qt.ItemDataRole.UserRole, item["query_key"])
            lw.setSizeHint(QSize(0, row_h))
            row_w = HistoryItemRowWidget(item["query_key"], disp, item["is_favorite"], item["query_key"] in self._checked)
            row_w.star_toggled.connect(self._on_row_star_toggled)
            row_w.term_clicked.connect(self._on_row_term_clicked)
            row_w.check_toggled.connect(self._on_row_checked)
            self.hist_list.addItem(lw)
            self.hist_list.setItemWidget(lw, row_w)
        self._sync_selection_labels()

    # --- selection ---
    def checked_keys(self) -> List[str]:
        """Query keys of the ticked entries, in list order."""
        return [k for k in self._listed if k in self._checked]

    def _rows(self):
        return [self.hist_list.itemWidget(self.hist_list.item(i)) for i in range(self.hist_list.count())]

    def _on_row_checked(self, key: str, on: bool) -> None:
        (self._checked.add if on else self._checked.discard)(key)
        self._sync_selection_labels()

    def _on_select_all(self, on: bool) -> None:
        self._checked = set(self._listed) if on else set()
        for row in self._rows():
            row.check.blockSignals(True)
            row.check.setChecked(on)
            row.check.blockSignals(False)
        self._sync_selection_labels()

    def _sync_selection_labels(self) -> None:
        n = len(self.checked_keys())
        self.count_lbl.setText(f"{n} selected" if n else "")
        self.select_all.blockSignals(True)
        self.select_all.setChecked(bool(self._listed) and n == len(self._listed))
        self.select_all.setEnabled(bool(self._listed))
        self.select_all.blockSignals(False)

    # --- actions ---
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

    def _ask_delete_checked(self) -> None:
        keys = self.checked_keys()
        if not keys:
            self.count_lbl.setText("Tick entries to delete")
            return
        favourites = sum(1 for r in self._rows() if r.query_key in self._checked and r.is_fav)
        n = len(keys)
        msg = f"Delete {n} {'entry' if n == 1 else 'entries'} from your history?"
        if favourites:
            msg += f" {favourites} {'is a favourite' if favourites == 1 else 'are favourites'} and will be removed too."
        self.confirm.ask(msg, lambda: self._delete_keys(keys))

    def _delete_keys(self, keys: List[str]) -> None:
        self.main_win.db.delete_entries(keys)  # history row and favourite flag live in one row: they go together
        self._checked -= set(keys)
        self.populate()
        current = (self.main_win.current_term or "").strip().lower()
        if current in keys and self.main_win.current_data:
            # The result on screen is no longer saved: show it as such (Save instead of Saved).
            self.main_win.current_data["_is_favorite"] = False
            self.main_win.results_page.render(self.main_win.current_term, self.main_win.current_data)
