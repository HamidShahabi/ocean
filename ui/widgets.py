"""Reusable glass-style widgets shared by the pages: frames, dialogs, popups, dropdowns, buttons."""
from typing import List, Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import lexical_engine
from ui import theme
from ui.config import SettingsManager
from ui.marquee import CHECK_ROLE, MarqueeLabel, apply_marquee
from ui.speaker import NeuralSpeaker
from ui.theme import PERSIAN_ALIGNMENT, THEME_COLORS, VectorIcons, make_polished_font, paint_soft_shadow

POPUP_SHADOW = 14  # transparent margin around the popup cards, where their soft shadow is painted

if TYPE_CHECKING:
    from ui.main_window import MainWindow


# === SECTION 6: REUSABLE GLASS UI COMPONENTS & HARMONIOUS EDIT DIALOG ===
_focus_ring_watched = False


def _watch_focus_rings() -> None:
    """Repaint the search frames when focus enters or leaves their input (they draw a focus ring).
    One application-wide connection, made once: a connection per frame would outlive deleted frames."""
    global _focus_ring_watched
    if _focus_ring_watched:
        return
    _focus_ring_watched = True

    def repaint(old: Optional[QWidget], new: Optional[QWidget]) -> None:
        for w in (old, new):
            while w is not None:
                if isinstance(w, SmoothGlassFrame) and w.variant == "search":
                    w.update()
                w = w.parentWidget()

    QApplication.instance().focusChanged.connect(repaint)


class SmoothGlassFrame(QFrame):
    def __init__(self, radius: int = 22, variant: str = "card", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.radius = radius
        self.variant = variant
        self.hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if variant == "search":
            _watch_focus_rings()

    def _has_focus_inside(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and self.isAncestorOf(focus)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self.variant == "inner":
            bg = QColor(THEME_COLORS["bg_inner"])
        elif self.variant == "drawer":
            bg = QColor(THEME_COLORS["bg_drawer"])
        elif self.variant == "popup":
            bg = QColor(THEME_COLORS["bg_popup"])
        else:
            bg = QColor(THEME_COLORS["bg_card"])

        if self.variant == "search" and (self.hovered or self._has_focus_inside()):
            border_col = QColor(THEME_COLORS["border_focus"])
            pen_w = 1.5
        else:
            border_col = QColor(THEME_COLORS["border"])
            pen_w = 1.0

        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, float(self.radius), float(self.radius))

        p.fillPath(path, bg)
        p.setPen(QPen(border_col, pen_w))
        p.drawPath(path)
        p.end()

    def enterEvent(self, event) -> None:
        if self.variant == "search":
            self.hovered = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.variant == "search":
            self.hovered = False
            self.update()
        super().leaveEvent(event)


class SmoothGlassEditDialog(QDialog):
    """Frameless rounded glass dialog replacing the old rectangular Windows QInputDialog (Image 1 fix)."""

    def __init__(self, parent: QWidget, en_source: str, current_fa: str) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = SmoothGlassFrame(radius=24, variant="card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(14)

        title = QLabel("Edit Persian Translation")
        title.setObjectName("CardTitle")
        lay.addWidget(title)

        src_lbl = QLabel(f"English:  \"{en_source}\"")
        src_lbl.setWordWrap(True)
        src_lbl.setObjectName("MutedText")
        lay.addWidget(src_lbl)

        self.input_box = QLineEdit()
        self.input_box.setObjectName("DrawerInput")
        self.input_box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.input_box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.input_box.setFixedHeight(40)
        self.input_box.setText(current_fa)
        self.input_box.setPlaceholderText("\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627 \u0645\u0639\u0627\u062f\u0644 \u062f\u0642\u06cc\u0642 \u0641\u0627\u0631\u0633\u06cc \u0631\u0627 \u0648\u0627\u0631\u062f \u06a9\u0646\u06cc\u062f...")
        self.input_box.returnPressed.connect(self.accept)
        lay.addWidget(self.input_box)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("GlassPillButton")
        cancel_btn.setFixedSize(100, 36)
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("PrimaryPillButton")
        save_btn.setFixedSize(100, 36)
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.clicked.connect(self.accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        lay.addLayout(btn_row)

        outer.addWidget(card)
        self.setStyleSheet(parent.styleSheet())

    def get_value(self) -> str:
        return self.input_box.text().strip()


class GlassPopupMenu(QWidget):
    option_picked = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(POPUP_SHADOW, POPUP_SHADOW, POPUP_SHADOW, POPUP_SHADOW)

        self.card = SmoothGlassFrame(radius=16, variant="popup")
        c_lay = QVBoxLayout(self.card)
        c_lay.setContentsMargins(8, 8, 8, 8)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("PopupList")
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setMouseTracking(True)
        apply_marquee(self.list_widget)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        c_lay.addWidget(self.list_widget)
        vbox.addWidget(self.card)

    # Callers size and place the card; the window around it also holds the shadow margin.
    def setFixedSize(self, w: int, h: int) -> None:
        super().setFixedSize(w + 2 * POPUP_SHADOW, h + 2 * POPUP_SHADOW)

    def move(self, pos: QPoint) -> None:
        super().move(pos - QPoint(POPUP_SHADOW, POPUP_SHADOW))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        paint_soft_shadow(p, QRectF(self.rect()).adjusted(POPUP_SHADOW, POPUP_SHADOW, -POPUP_SHADOW, -POPUP_SHADOW), 16.0, spread=POPUP_SHADOW - 2)
        p.end()

    def open_at(self, options: List[str], current_val: str, anchor: QWidget, min_w: int = 200) -> None:
        self.list_widget.clear()
        for opt in options:
            it = QListWidgetItem(opt)
            it.setData(Qt.ItemDataRole.UserRole, opt)
            it.setData(CHECK_ROLE, opt == current_val)
            it.setSizeHint(QSize(0, 40))
            self.list_widget.addItem(it)

        # As wide as the capsule, or as the longest option when that fits in a sensible width
        # (anything longer than that scrolls on hover instead).
        fm = QFontMetrics(self.list_widget.font())
        longest = max((fm.horizontalAdvance(o) for o in options), default=0) + 16 + 24 + 12 + 16 + 8
        w = max(anchor.width(), min_w, min(longest, 360))
        h = len(options) * 40 + 20
        self.setFixedSize(w, h)
        pos = anchor.mapToGlobal(QPoint(anchor.width() - w, anchor.height() + 6))
        self.move(pos)
        self.show()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        val = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        if val is not None:
            self.option_picked.emit(val)


class ChevronIndicator(QWidget):
    """The small drop-down arrow of the capsule dropdowns (drawn, so it follows the theme)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(THEME_COLORS["text_muted"]), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPointF(3.0, 5.0), QPointF(7.0, 9.0), QPointF(11.0, 5.0))
        p.end()


class ModernGlassDropdown(QPushButton):
    """Capsule dropdown. The current value is a MarqueeLabel: if it is wider than the capsule it
    scrolls while the pointer is over the capsule."""
    valueChanged = pyqtSignal(str)

    def __init__(self, options: List[str], initial: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.options = options
        self.current_val = initial if initial in options else (options[0] if options else "")
        self.setObjectName("DropdownPill")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(38)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(8)
        self.value_label = MarqueeLabel()
        self.value_label.setObjectName("DropdownValue")
        self.value_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.value_label.attach_hover(self)
        row.addWidget(self.value_label, 1)
        row.addWidget(ChevronIndicator(), 0, Qt.AlignmentFlag.AlignVCenter)

        self.popup = GlassPopupMenu(self)
        self.popup.option_picked.connect(self._on_picked)
        self.clicked.connect(self._open_popup)
        self._update_label()

    def _update_label(self) -> None:
        self.value_label.setText(self.current_val)

    def _open_popup(self) -> None:
        self.popup.open_at(self.options, self.current_val, self, min_w=self.width())

    def _on_picked(self, val: str) -> None:
        self.current_val = val
        self._update_label()
        self.valueChanged.emit(val)

    def currentText(self) -> str:
        return self.current_val

    def setCurrentText(self, val: str) -> None:
        if val in self.options:
            self.current_val = val
            self._update_label()


class GlassSuggestionPopup(QWidget):
    item_selected = pyqtSignal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(POPUP_SHADOW, POPUP_SHADOW, POPUP_SHADOW, POPUP_SHADOW)

        self.container = SmoothGlassFrame(radius=20, variant="popup")
        c_lay = QVBoxLayout(self.container)
        c_lay.setContentsMargins(10, 10, 10, 10)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("PopupList")
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setMouseTracking(True)
        apply_marquee(self.list_widget)
        self.list_widget.itemClicked.connect(self._on_clicked)
        c_lay.addWidget(self.list_widget)
        vbox.addWidget(self.container)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        paint_soft_shadow(p, QRectF(self.rect()).adjusted(POPUP_SHADOW, POPUP_SHADOW, -POPUP_SHADOW, -POPUP_SHADOW), 20.0, spread=POPUP_SHADOW - 2)
        p.end()

    def show_suggestions(self, items: List[str], anchor_widget: QWidget) -> None:
        if not items:
            self.hide()
            return
        self.list_widget.clear()
        for word in items:
            it = QListWidgetItem(word)
            it.setData(Qt.ItemDataRole.UserRole, word)
            it.setSizeHint(QSize(0, 42))
            self.list_widget.addItem(it)

        row_h = 42
        total_h = min(len(items) * row_h + 24, 280)
        self.setFixedWidth(anchor_widget.width() + 2 * POPUP_SHADOW)
        self.setFixedHeight(total_h + 2 * POPUP_SHADOW)
        pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 8))
        self.move(pos - QPoint(POPUP_SHADOW, POPUP_SHADOW))
        self.show()

    def _on_clicked(self, item: QListWidgetItem) -> None:
        word = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        if word:
            self.item_selected.emit(word)


class PersianTranslationWidget(QWidget):
    def __init__(self, text: str, font_px: int, mode: str, en_source: str = "", main_win: Optional["MainWindow"] = None, large: bool = False) -> None:
        super().__init__()
        clean_fa = text.strip() if text else "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f"
        self.real_text = clean_fa
        self.en_source = en_source.strip()
        self.main_win = main_win
        self.mode = mode
        self.revealed = (mode == "Always Show")

        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self.reveal_btn = QPushButton("Reveal Persian Translation")
        self.reveal_btn.setObjectName("SpoilerPill")
        self.reveal_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reveal_btn.setFixedHeight(32)
        self.reveal_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.reveal_btn.clicked.connect(self._reveal)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setObjectName("SpoilerPill")
        self.edit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.edit_btn.setFixedHeight(28)
        self.edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.edit_btn.clicked.connect(self.trigger_manual_edit)

        self.label = QLabel()
        self.label.setProperty("is_persian_widget", True)
        self.label.setProperty("persian_owner", self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setAlignment(PERSIAN_ALIGNMENT)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setFont(make_polished_font(theme.FA_FONT_FAMILY, font_px + (2 if large else 0), bold=large))
        self.label.setObjectName("PersianText")

        layout.addWidget(self.reveal_btn, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.edit_btn, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.label, 1)

        self._set_persian_text(self.real_text)
        self._sync_visibility()

    def _set_persian_text(self, fa_str: str) -> None:
        self.real_text = fa_str.strip() or "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f"
        self.label.setText(self.real_text)

    def _is_missing_translation(self) -> bool:
        return "\u06cc\u0627\u0641\u062a \u0646\u0634\u062f" in self.real_text or not self.real_text.strip()

    def _sync_visibility(self) -> None:
        self.reveal_btn.setVisible(not self.revealed)
        self.label.setVisible(self.revealed)
        self.edit_btn.setVisible(self.revealed and self._is_missing_translation() and bool(self.en_source))

    def _reveal(self) -> None:
        self.revealed = True
        self._sync_visibility()

    def trigger_manual_edit(self) -> None:
        cur_val = "" if self._is_missing_translation() else self.real_text
        dlg = SmoothGlassEditDialog(self.main_win or self, self.en_source, cur_val)
        if dlg.exec():
            new_txt = dlg.get_value()
            if new_txt:
                lexical_engine.record_user_override(self.en_source, new_txt)
                self._apply_new_persian(new_txt)

    def _apply_new_persian(self, new_fa: str) -> None:
        self._set_persian_text(new_fa)
        self._sync_visibility()
        if self.main_win and self.main_win.current_term and self.main_win.current_data:
            d = self.main_win.current_data
            if self.en_source.lower() == d.get("headword", "").lower():
                d["persian_equivalent"] = new_fa
            for s in d.get("senses", []):
                if s.get("example_en") == self.en_source:
                    s["example_fa"] = new_fa
            for grp in ("collocations", "synonyms", "antonyms", "sentence_breakdown"):
                for item in d.get(grp, []):
                    if item.get("term") == self.en_source:
                        item["fa"] = new_fa
                    if item.get("example_en") == self.en_source:
                        item["example_fa"] = new_fa
            self.main_win.db.save_entry(self.main_win.current_term, d, fetched=False)

    def enterEvent(self, event) -> None:
        if self.mode == "Hover to Reveal Persian" and not self.revealed:
            self._reveal()
        super().enterEvent(event)


def create_audio_button(text_to_speak: str, speaker: NeuralSpeaker, settings: SettingsManager) -> QPushButton:
    """Speaker button for `text_to_speak`. The clip is synthesized in the background right away, so the click
    plays it at once."""
    speaker.prefetch(text_to_speak)
    btn = QPushButton()
    btn.setIcon(VectorIcons.speaker_icon(THEME_COLORS["accent_fg"]))
    btn.setIconSize(QSize(18, 18))
    btn.setObjectName("AudioCircleButton")
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setFixedSize(34, 34)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    btn.setToolTip(f"Listen: {text_to_speak}")
    btn.clicked.connect(lambda _, t=text_to_speak: speaker.speak(t))
    return btn
