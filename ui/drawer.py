"""The attached right-hand drawer: a menu plus the history, settings and Teach Ocean pages."""
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ui.history_page import HistoryPage
from ui.settings_page import SettingsPage
from ui.teach_page import TeachPage
from ui.theme import VectorIcons
from ui.widgets import SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow

PAGE_MENU, PAGE_HISTORY, PAGE_SETTINGS, PAGE_TEACH = 0, 1, 2, 3


class AttachedRightDrawer(SmoothGlassFrame):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__(radius=22, variant="drawer", parent=main_win)
        self.main_win = main_win
        self.setFixedWidth(400)
        self._build_drawer()

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

        self.stack = QStackedWidget()
        vbox.addWidget(self.stack, stretch=1)

        # Main menu (page 0); the other pages are separate widgets
        page_menu = QWidget()
        m_lay = QVBoxLayout(page_menu)
        m_lay.setContentsMargins(0, 8, 0, 0)
        m_lay.setSpacing(10)

        self.review_menu_btn = QPushButton("Review")
        self.review_menu_btn.setObjectName("DrawerMenuItem")
        self.review_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.review_menu_btn.setFixedHeight(46)
        self.review_menu_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.review_menu_btn.clicked.connect(lambda: self.main_win.open_review())
        m_lay.addWidget(self.review_menu_btn)

        btn_quiz = QPushButton("Quiz")
        btn_quiz.setObjectName("DrawerMenuItem")
        btn_quiz.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_quiz.setFixedHeight(46)
        btn_quiz.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_quiz.clicked.connect(lambda: self.main_win.open_quiz())
        m_lay.addWidget(btn_quiz)

        btn_reader = QPushButton("Reader")
        btn_reader.setObjectName("DrawerMenuItem")
        btn_reader.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_reader.setFixedHeight(46)
        btn_reader.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_reader.clicked.connect(lambda: self.main_win.open_reader())
        m_lay.addWidget(btn_reader)

        btn_pronounce = QPushButton("Pronounce")
        btn_pronounce.setObjectName("DrawerMenuItem")
        btn_pronounce.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_pronounce.setFixedHeight(46)
        btn_pronounce.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_pronounce.clicked.connect(lambda: self.main_win.open_pronounce())
        m_lay.addWidget(btn_pronounce)

        btn_hist = QPushButton("History")
        btn_hist.setObjectName("DrawerMenuItem")
        btn_hist.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_hist.setFixedHeight(46)
        btn_hist.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_hist.clicked.connect(lambda: self.show_page(PAGE_HISTORY))
        m_lay.addWidget(btn_hist)

        btn_set = QPushButton("Settings")
        btn_set.setObjectName("DrawerMenuItem")
        btn_set.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_set.setFixedHeight(46)
        btn_set.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_set.clicked.connect(lambda: self.show_page(PAGE_SETTINGS))
        m_lay.addWidget(btn_set)

        btn_teach = QPushButton("Teach Ocean")
        btn_teach.setObjectName("DrawerMenuItem")
        btn_teach.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_teach.setFixedHeight(46)
        btn_teach.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_teach.clicked.connect(lambda: self.show_page(PAGE_TEACH))
        m_lay.addWidget(btn_teach)

        m_lay.addStretch()
        self.stack.addWidget(page_menu)

        # Page order must match the PAGE_* indexes above.
        self.history_page = HistoryPage(self.main_win)
        self.settings_page = SettingsPage(self.main_win, self)
        self.teach_page = TeachPage(self.main_win)
        self.pages = {PAGE_HISTORY: self.history_page, PAGE_SETTINGS: self.settings_page, PAGE_TEACH: self.teach_page}
        for index in sorted(self.pages):
            self.stack.addWidget(self.pages[index])

    def show_page(self, index: int) -> None:
        titles = {PAGE_MENU: "Ocean Menu", PAGE_HISTORY: "History", PAGE_SETTINGS: "Settings", PAGE_TEACH: "Teach Ocean"}
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
