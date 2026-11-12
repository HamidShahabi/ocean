"""Window and page transitions: one short, uniform fade for every change of what is on screen.

Animating a live page every frame is expensive (the results page is hundreds of word-wrapped
labels, and showing the drawer changes its width, so every frame would re-lay everything out).
So a transition snapshots the region before and after the change (two QWidget.grab() calls),
applies the change instantly under an opaque overlay of the old snapshot, and the overlay fades
to the new one (for the drawer: the drawer slides and fades while the content snaps): each frame
is just two pixmap blits and nothing is laid out or repainted."""
import os
import sys
from typing import Callable, Optional

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QRect, QTimer, QVariantAnimation, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget

from ui.theme import THEME_COLORS

PAGE_FADE_MS = 160
DRAWER_FADE_MS = 190
DRAWER_SLIDE_PX = 28

_override: Optional[bool] = None


def set_motion_override(value: Optional[bool]) -> None:
    """Force animations on or off (None: follow the system); the tests use it."""
    global _override
    _override = value


def motion_enabled() -> bool:
    """False when the user asked for reduced motion (OCEAN_REDUCE_MOTION=1, or Windows'
    "Show animations" turned off) and on the headless test platform."""
    if _override is not None:
        return _override
    if os.environ.get("OCEAN_REDUCE_MOTION", "").lower() in ("1", "true", "yes"):
        return False
    if QGuiApplication.platformName() == "offscreen":
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            enabled = ctypes.c_int(1)
            SPI_GETCLIENTAREAANIMATION = 0x1042
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)
        except Exception:
            pass
    return True


class _Overlay(QWidget):
    """Opaque child that paints the old snapshot fading into the new one, or (drawer) the drawer sliding."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.old = QPixmap()
        self.new = QPixmap()
        self.slide: Optional[QRect] = None   # the drawer's rectangle in overlay coordinates
        self.opening = True
        self.t = 0.0
        self.hide()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        if self.new.isNull() or self.slide is None:
            p.drawPixmap(0, 0, self.old)
        if self.new.isNull():
            return
        if self.slide is None:
            p.setOpacity(self.t)
            p.drawPixmap(0, 0, self.new)
            return
        # The content around the drawer snaps to its new layout (cross-fading two different line
        # wrappings of the same text looks like ghosting); only the drawer fades and slides.
        dx = round((1.0 - self.t) * DRAWER_SLIDE_PX) if self.opening else round(self.t * DRAWER_SLIDE_PX)
        p.drawPixmap(0, 0, self.new)
        p.setClipRect(self.slide)
        if self.opening:
            p.fillRect(self.slide, QColor(THEME_COLORS["bg_main"]))
            p.setOpacity(self.t)
            p.drawPixmap(dx, 0, self.new)
        else:
            p.setOpacity(1.0 - self.t)
            p.drawPixmap(dx, 0, self.old)


class Crossfade(QObject):
    """Cross-fades the `region` of `host` (host coordinates) around a change made by `run(mutate)`.
    `slide_fn`, if given, returns the drawer's rectangle in host coordinates (None while hidden)."""

    def __init__(self, host: QWidget, region_fn: Callable[[], QRect], slide_fn: Optional[Callable[[], Optional[QRect]]] = None,
                 duration: int = PAGE_FADE_MS) -> None:
        super().__init__(host)
        self.host = host
        self.region_fn = region_fn
        self.slide_fn = slide_fn
        self.overlay = _Overlay(host)
        self._token = 0
        self._host_size = host.size()
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._on_value)
        self.anim.finished.connect(self.finish)
        host.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and obj is self.host and self.overlay.isVisible() and self.host.size() != self._host_size:
            self.finish()  # the window was resized: the snapshots no longer match the layout
        return False

    @property
    def running(self) -> bool:
        return self.overlay.isVisible()

    def can_animate(self) -> bool:
        w = self.host.window()
        return (motion_enabled() and self.host.isVisible() and not w.isMinimized()
                and self.region_fn().width() > 0 and self.region_fn().height() > 0)

    def _grab(self, region: QRect) -> QPixmap:
        self.overlay.hide()
        win = self.host.window()
        origin = self.host.mapTo(win, region.topLeft())
        return win.grab(QRect(origin, region.size()))

    def capture(self) -> Optional[QPixmap]:
        """A snapshot of the region as it is now (for a change that starts before `run`)."""
        return self._grab(self.region_fn()) if self.can_animate() else None

    def run(self, mutate: Callable[[], None], old: Optional[QPixmap] = None) -> None:
        if not self.can_animate():
            self.finish()
            mutate()
            return
        self.finish()
        region = self.region_fn()
        if old is None:
            old = self._grab(region)
        slide_old = self.slide_fn() if self.slide_fn else None
        self._token += 1
        token = self._token
        self._host_size = self.host.size()
        self.overlay.setGeometry(region)
        self.overlay.old, self.overlay.new, self.overlay.slide, self.overlay.t = old, QPixmap(), None, 0.0
        self.overlay.show()
        self.overlay.raise_()
        mutate()
        # The change may have posted layout work; start once the event loop has settled it.
        QTimer.singleShot(0, lambda: self._begin(token, region, old, slide_old))

    def _begin(self, token: int, region: QRect, old: QPixmap, slide_old: Optional[QRect]) -> None:
        if token != self._token or not self.overlay.isVisible():
            return
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        new = self._grab(region)
        slide_new = self.slide_fn() if self.slide_fn else None
        o = self.overlay
        o.old, o.new, o.t = old, new, 0.0
        o.slide, o.opening = None, True
        if slide_old is None and slide_new is not None:
            o.slide = slide_new.translated(-region.topLeft())
        elif slide_old is not None and slide_new is None:
            o.slide, o.opening = slide_old.translated(-region.topLeft()), False
        o.show()
        o.raise_()
        self.anim.stop()
        self.anim.start()

    def _on_value(self, value) -> None:
        self.overlay.t = float(value)
        self.overlay.update()

    def finish(self) -> None:
        self._token += 1
        self.anim.stop()
        self.overlay.hide()
        self.overlay.old = self.overlay.new = QPixmap()


class FadeStackedWidget(QStackedWidget):
    """A QStackedWidget that cross-fades when its page changes. Call `begin_fade()` first when the
    caller rearranges the page it is leaving just before switching (the snapshot then shows the page
    as the user last saw it)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fade = Crossfade(self, self.rect, duration=PAGE_FADE_MS)
        self._pending: Optional[QPixmap] = None

    def begin_fade(self) -> None:
        self._pending = self._fade.capture()
        QTimer.singleShot(0, self._drop_pending)

    def _drop_pending(self) -> None:
        self._pending = None

    def setCurrentIndex(self, index: int) -> None:
        if index == self.currentIndex() or not (0 <= index < self.count()):
            self._pending = None
            super().setCurrentIndex(index)
            return
        old, self._pending = self._pending, None
        self._fade.run(lambda: QStackedWidget.setCurrentIndex(self, index), old=old)

    def setCurrentWidget(self, widget: QWidget) -> None:
        self.setCurrentIndex(self.indexOf(widget))
