"""Hover-to-scroll for text that does not fit its box.

Text that fits is drawn as usual. Text that overflows shows an ellipsis at rest; while the pointer
is over it, it glides left to reveal the rest, pauses, glides back and repeats until the pointer
leaves (then it eases back to the start). Used by MarqueeLabel (single-line labels, the capsule
dropdowns' current value) and MarqueeItemDelegate (the rows of the popup lists)."""
from typing import Optional

from PyQt6.QtCore import (
    QElapsedTimer,
    QEasingCurve,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ui import theme
from ui.motion import motion_enabled

SPEED_PX_PER_S = 60.0   # reading-friendly scroll speed
START_DELAY_MS = 350    # hover must rest briefly before anything moves
END_HOLD_MS = 700       # pause with the end of the text visible
LOOP_PAUSE_MS = 1100    # pause at the start before the next pass
END_PADDING_PX = 10     # how far past the last glyph to scroll
GLIDE_BACK_MS = 220     # leaving the text: ease back to the start
CHECK_ROLE = Qt.ItemDataRole.UserRole + 1  # item data: True/False draws a check column (the popup menu's current value)

_EASE = QEasingCurve(QEasingCurve.Type.InOutSine)


def overflow_distance(text_width: int, box_width: int) -> int:
    """How far (px) the text has to move left to reveal its end; 0 when it fits."""
    return max(0, text_width - box_width + END_PADDING_PX) if text_width > box_width else 0


def marquee_offset(t_ms: float, distance: float, loop: bool = True) -> float:
    """Scroll offset (px, 0..distance) `t_ms` after the hover began: wait, glide left, hold, glide back, pause."""
    if distance <= 0:
        return 0.0
    forward = max(500.0, distance / SPEED_PX_PER_S * 1000.0)
    back = max(300.0, min(900.0, forward * 0.45))
    cycle = START_DELAY_MS + forward + END_HOLD_MS + back + LOOP_PAUSE_MS
    if loop:
        t = t_ms % cycle if t_ms >= START_DELAY_MS else t_ms
        if t_ms >= cycle:
            t = START_DELAY_MS + (t_ms - START_DELAY_MS) % (cycle - START_DELAY_MS)
    else:
        t = min(t_ms, cycle)
    if t < START_DELAY_MS:
        return 0.0
    t -= START_DELAY_MS
    if t < forward:
        return distance * _EASE.valueForProgress(t / forward)
    t -= forward
    if t < END_HOLD_MS:
        return float(distance)
    t -= END_HOLD_MS
    if t < back:
        return distance * (1.0 - _EASE.valueForProgress(t / back))
    return 0.0


class MarqueeAnimator(QObject):
    """Drives one scrolling text: `offset` changes while active; `changed` asks the owner to repaint."""
    changed = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.offset = 0.0
        self.distance = 0.0
        self.active = False       # hovered (or still gliding back): draw the full text, not the ellipsis
        self._gliding_back = False
        self._glide_from = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def start(self, distance: float) -> None:
        if distance <= 0:
            return
        self.distance = float(distance)
        self.active = True
        self._gliding_back = False
        self._clock.start()
        self._timer.start()
        self.changed.emit()

    def stop(self) -> None:
        if not self.active:
            return
        if self.offset <= 0.5 or not motion_enabled():
            self._finish()
            return
        self._gliding_back = True
        self._glide_from = self.offset
        self._clock.start()

    def _finish(self) -> None:
        self._timer.stop()
        self.offset = 0.0
        self.active = False
        self._gliding_back = False
        self.changed.emit()

    def _tick(self) -> None:
        t = float(self._clock.elapsed())
        if self._gliding_back:
            p = min(1.0, t / GLIDE_BACK_MS)
            self.offset = self._glide_from * (1.0 - _EASE.valueForProgress(p))
            if p >= 1.0:
                self._finish()
                return
        else:
            self.offset = marquee_offset(t, self.distance)
        self.changed.emit()


class MarqueeLabel(QLabel):
    """A single-line label that scrolls its text on hover when it does not fit. Use it instead of
    QLabel for any plain-text value in a box of limited width; labels that wrap never overflow."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.anim = MarqueeAnimator(self)
        self.anim.changed.connect(self.update)

    def attach_hover(self, trigger: QWidget) -> None:
        """Also scroll while the pointer is over `trigger` (e.g. the button this label sits in)."""
        trigger.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter:
            self._hover(True)
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._hover(False)
        return False

    def minimumSizeHint(self) -> QSize:
        return QSize(16, super().minimumSizeHint().height())  # let layouts shrink it below the text width

    def _box_width(self) -> int:
        return self.contentsRect().width()

    def distance(self) -> int:
        """Pixels of text hidden beyond the box (plus end padding); 0 when the text fits or wraps."""
        if self.wordWrap() or not self.text():
            return 0
        return overflow_distance(self.fontMetrics().horizontalAdvance(self.text()), self._box_width())

    def is_overflowing(self) -> bool:
        return self.distance() > 0

    def _hover(self, on: bool) -> None:
        if on:
            self.anim.start(self.distance())
        else:
            self.anim.stop()

    def setText(self, text: str) -> None:
        self.anim.stop()
        super().setText(text)

    def enterEvent(self, event) -> None:
        self._hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover(False)
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.anim.active and not self.is_overflowing():
            self.anim.stop()

    def paintEvent(self, event) -> None:
        if not self.is_overflowing():
            super().paintEvent(event)
            return
        r = self.contentsRect()
        p = QPainter(self)
        p.setClipRect(r)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        fm = self.fontMetrics()
        if self.anim.active:
            p.drawText(r.translated(-round(self.anim.offset), 0).adjusted(0, 0, 4096, 0),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        else:
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, r.width()))
        p.end()


class MarqueeItemDelegate(QStyledItemDelegate):
    """Paints list rows like the style sheet says (hover/selected backgrounds) but scrolls the text of
    the hovered row when it is wider than the row; other long rows end in an ellipsis."""
    TEXT_LEFT = 12
    TEXT_RIGHT = 10
    CHECK_WIDTH = 24

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self.view = view
        self._active: Optional[QPersistentModelIndex] = None
        self.anim = MarqueeAnimator(self)
        self.anim.changed.connect(self._repaint_active)
        view.setMouseTracking(True)
        view.entered.connect(self._on_entered)
        view.viewport().installEventFilter(self)
        view.setItemDelegate(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Leave:
            self._deactivate()
        return False

    def _text_rect(self, rect: QRect, index: QModelIndex) -> QRect:
        left = self.TEXT_LEFT + (self.CHECK_WIDTH if index.data(CHECK_ROLE) is not None else 0)
        return rect.adjusted(left, 0, -self.TEXT_RIGHT, 0)

    def _distance(self, index: QModelIndex, rect: QRect) -> int:
        fm = QFontMetrics(self.view.font())
        return overflow_distance(fm.horizontalAdvance(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                                 self._text_rect(rect, index).width())

    def _on_entered(self, index: QModelIndex) -> None:
        if self._active is not None and self._active.isValid() and self._active == QPersistentModelIndex(index):
            return
        self._deactivate()
        if not index.isValid():
            return
        distance = self._distance(index, self.view.visualRect(index))
        if distance > 0:
            self._active = QPersistentModelIndex(index)
            self.anim.start(distance)

    def _deactivate(self) -> None:
        self.anim.stop()
        if not self.anim.active:
            self._active = None

    def _repaint_active(self) -> None:
        if self._active is not None and self._active.isValid():
            self.view.viewport().update(self.view.visualRect(QModelIndex(self._active)))
        if not self.anim.active:
            self._active = None

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else self.view.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        hot = bool(opt.state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected))
        colors = theme.THEME_COLORS
        painter.save()
        painter.setPen(QColor(colors["accent_fg"] if hot else colors["text_main"]))
        painter.setFont(opt.font)
        check = index.data(CHECK_ROLE)
        if check is not None:
            box = QRect(opt.rect.left() + self.TEXT_LEFT, opt.rect.top(), self.CHECK_WIDTH, opt.rect.height())
            if check:
                painter.setPen(QColor(colors["accent"] if not hot else colors["accent_fg"]))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "✓")
                painter.setPen(QColor(colors["accent_fg"] if hot else colors["text_main"]))
        rect = self._text_rect(opt.rect, index)
        painter.setClipRect(rect)
        running = self._active is not None and self._active.isValid() and self._active == QPersistentModelIndex(index) and self.anim.active
        if running:
            painter.drawText(rect.translated(-round(self.anim.offset), 0).adjusted(0, 0, 4096, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        else:
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(opt.font).elidedText(text, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()


def apply_marquee(view: QAbstractItemView) -> MarqueeItemDelegate:
    """Give a list view hover-scrolling rows."""
    return MarqueeItemDelegate(view)
