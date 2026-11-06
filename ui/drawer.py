"""The attached right-hand drawer: a menu plus the history, reports, sources and settings pages."""
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, Qt, QSize
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.history_page import HistoryPage
from ui.reports_page import ReportsPage
from ui.settings_page import SettingsPage
from ui.motion import DRAWER_FADE_MS, Crossfade, FadeStackedWidget
from ui.sources_page import SourcesPage
from ui.theme import VectorIcons
from ui.widgets import SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow

PAGE_MENU, PAGE_HISTORY, PAGE_SETTINGS, PAGE_REPORTS, PAGE_SOURCES = 0, 1, 2, 3, 4


class AttachedRightDrawer(SmoothGlassFrame):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__(radius=22, variant="drawer", parent=main_win)
        self.main_win = main_win
        self.setFixedWidth(400)
        self._workspace_fade: Optional[Crossfade] = None
        self._changing = False
        self._build_drawer()

    # Opening and closing the drawer re-lays out the whole workspace, so both go through one
    # cross-fade (with the drawer sliding in or out): see ui/motion.py. Hooking setVisible covers
    # every caller (menu button, Settings "Save", ...).
    def _workspace(self) -> Crossfade:
        if self._workspace_fade is None:
            central = self.main_win.centralWidget()

            def region() -> QRect:
                top = self.main_win.stack.mapTo(central, QPoint(0, 0)).y()
                return QRect(0, top, central.width(), central.height() - top)

            def drawer_rect() -> Optional[QRect]:
                return QRect(self.mapTo(central, QPoint(0, 0)), self.size()) if self.isVisible() else None

            self._workspace_fade = Crossfade(central, region, drawer_rect, duration=DRAWER_FADE_MS)
        return self._workspace_fade

    def setVisible(self, visible: bool) -> None:
        if visible == self.isVisible() or self._changing:
            super().setVisible(visible)
            return

        def change() -> None:
            self._changing = True
            try:
                SmoothGlassFrame.setVisible(self, visible)
            finally:
                self._changing = False

        self._workspace().run(change)

    def _build_drawer(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(14)

        header = QHBoxLayout()
        self.drawer_title = QLabel("Ocean Menu")
        self.drawer_title.setObjectName("CardTitle")
        header.addWidget(self.drawer_title)
        header.addStretch()

        self.back_menu_btn = QPushButton()
        self.back_menu_btn.setObjectName("NavCircleButton")
        self.back_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_menu_btn.setFixedSize(36, 36)
        self.back_menu_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_menu_btn.setToolTip("Back to Menu")
        self.back_menu_btn.clicked.connect(lambda: self.show_page(PAGE_MENU))
        self.back_menu_btn.hide()
        header.addWidget(self.back_menu_btn)

        vbox.addLayout(header)

        self.stack = FadeStackedWidget()
        vbox.addWidget(self.stack, stretch=1)

        # Main menu (page 0); the other pages are separate widgets
        page_menu = QWidget()
        m_lay = QVBoxLayout(page_menu)
        m_lay.setContentsMargins(0, 8, 0, 0)
        m_lay.setSpacing(10)

        # Menu order differs from the page indexes above.
        for text, page in (("History", PAGE_HISTORY), ("Reports", PAGE_REPORTS), ("Sources", PAGE_SOURCES),
                           ("Settings", PAGE_SETTINGS)):
            btn = QPushButton(text)
            btn.setObjectName("DrawerMenuItem")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(46)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, i=page: self.show_page(i))
            m_lay.addWidget(btn)

        m_lay.addStretch()
        self.stack.addWidget(page_menu)

        # Page order must match the PAGE_* indexes above.
        self.history_page = HistoryPage(self.main_win)
        self.settings_page = SettingsPage(self.main_win, self)
        self.reports_page = ReportsPage(self.main_win)
        self.sources_page = SourcesPage(self.main_win)
        self.pages = {PAGE_HISTORY: self.history_page, PAGE_SETTINGS: self.settings_page,
                      PAGE_REPORTS: self.reports_page, PAGE_SOURCES: self.sources_page}
        for index in sorted(self.pages):
            self.stack.addWidget(self.pages[index])

    def show_page(self, index: int) -> None:
        self.stack.begin_fade()
        titles = {PAGE_MENU: "Ocean Menu", PAGE_HISTORY: "History", PAGE_SETTINGS: "Settings",
                  PAGE_REPORTS: "Reports", PAGE_SOURCES: "Sources"}
        self.drawer_title.setText(titles.get(index, "Ocean Menu"))
        self.back_menu_btn.setVisible(index != PAGE_MENU)
        page = self.pages.get(index)
        if page is not None:
            page.on_show()
        self.stack.setCurrentIndex(index)
        self.show()

    def refresh_history(self) -> None:
        """Re-list the history if it is the page on screen."""
        if self.isVisible() and self.stack.currentIndex() == PAGE_HISTORY:
            self.history_page.populate()

    def apply_icons(self, icon_fg: str, accent: str) -> None:
        self.back_menu_btn.setIcon(VectorIcons.arrow_icon("right", icon_fg))
        self.back_menu_btn.setIconSize(QSize(18, 18))
        self.settings_page.apply_icons(accent)
