"""Fonts, vector icons, the colour palette and the Qt style sheet (light and dark)."""
import sys
import os

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMainWindow

from ui.config import BASE_DIR

# The font families are rebound by register_custom_fonts(): read them as `theme.EN_FONT_FAMILY`
# (not `from ui.theme import EN_FONT_FAMILY`, which would keep the value from import time).
EN_FONT_FAMILY = "Inter"
# Qt swaps Left/Right for right-to-left text unless AlignAbsolute is set, so plain AlignRight
# renders Persian flush left.
PERSIAN_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter
FA_FONT_FAMILY = "Vazirmatn"

THEME_COLORS = {
    "bg_main": "#0E1621",
    "bg_card": "#17212B",
    "bg_inner": "#1E2C3A",
    "bg_drawer": "#17212B",
    "border": "#242F3D",
    "border_focus": "#64B5F6",
    "text_main": "#F5F5F5",
    "text_muted": "#8A9AA9",
    "accent": "#64B5F6",
    "accent_bg": "#2B5278",
    "accent_fg": "#FFFFFF",
}


# === SECTION 2: FONT REGISTRATION & POLISHING ===
def register_custom_fonts() -> None:
    global EN_FONT_FAMILY, FA_FONT_FAMILY
    search_dirs = [
        os.path.join(BASE_DIR, "fonts"),
        os.path.join(os.path.dirname(sys.executable), "fonts") if getattr(sys, "frozen", False) else "",
    ]
    for d in search_dirs:
        if d and os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.lower().endswith((".ttf", ".otf")):
                    QFontDatabase.addApplicationFont(os.path.join(d, fn))

    families = QFontDatabase.families()
    for fam in families:
        low = fam.lower()
        if "google sans medium" in low or "inter" in low:
            EN_FONT_FAMILY = fam
            break
    for fam in families:
        low = fam.lower()
        if "vazirmatn" in low or "shabnam" in low:
            FA_FONT_FAMILY = fam
            break


def make_polished_font(family: str, size_px: int, bold: bool = False) -> QFont:
    f = QFont(family)
    f.setPixelSize(size_px)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


# === SECTION 3: CUSTOM VECTOR ICONS ===
class VectorIcons:
    @staticmethod
    def speaker_icon(color_hex: str = "#FFFFFF") -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(color_hex)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)

        path = QPainterPath()
        path.moveTo(14, 25)
        path.lineTo(23, 25)
        path.lineTo(34, 15)
        path.lineTo(34, 49)
        path.lineTo(23, 39)
        path.lineTo(14, 39)
        path.closeSubpath()
        p.drawPath(path)

        pen = QPen(col, 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(28, 21, 14, 22), -60 * 16, 120 * 16)
        p.drawArc(QRectF(24, 13, 26, 38), -60 * 16, 120 * 16)
        p.end()
        return QIcon(pix)

    @staticmethod
    def search_icon(color_hex: str = "#0E1621") -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color_hex), 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(14, 14, 26, 26))
        p.drawLine(36, 36, 50, 50)
        p.end()
        return QIcon(pix)

    @staticmethod
    def hamburger_icon(color_hex: str = "#F5F5F5") -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color_hex), 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(16, 20, 48, 20)
        p.drawLine(16, 32, 48, 32)
        p.drawLine(16, 44, 48, 44)
        p.end()
        return QIcon(pix)

    @staticmethod
    def arrow_icon(direction: str = "left", color_hex: str = "#F5F5F5") -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color_hex), 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if direction == "left":
            p.drawLine(46, 32, 18, 32)
            p.drawLine(18, 32, 30, 20)
            p.drawLine(18, 32, 30, 44)
        else:
            p.drawLine(18, 32, 46, 32)
            p.drawLine(46, 32, 34, 20)
            p.drawLine(46, 32, 34, 44)
        p.end()
        return QIcon(pix)

    @staticmethod
    def ocean_app_icon() -> QIcon:
        pixmap = QPixmap(256, 256)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        grad = QLinearGradient(0, 0, 256, 256)
        grad.setColorAt(0.0, QColor("#1E88E5"))
        grad.setColorAt(0.5, QColor("#42A5F5"))
        grad.setColorAt(1.0, QColor("#90CAF9"))

        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(16, 16, 224, 224, 72, 72)

        painter.setPen(QColor("#0E1621"))
        painter.setFont(make_polished_font(EN_FONT_FAMILY, 130, bold=True))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "O")
        painter.end()
        return QIcon(pixmap)


# === SECTION 4: OPEN-SOURCE PRIMER / MATERIAL-3 DAYLIGHT & NIGHT THEME ENGINE ===
def sync_windows_titlebar(window: QMainWindow, is_dark: bool, bg_hex: str) -> None:
    """Synchronizes the native Windows 10/11 titlebar with Ocean's Light or Dark theme."""
    try:
        import ctypes
        hwnd = int(window.winId())
        dark_val = ctypes.c_int(1 if is_dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val))
        col = QColor(bg_hex)
        colorref = ctypes.c_int((col.blue() << 16) | (col.green() << 8) | col.red())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))
    except Exception:
        pass


def apply_theme(settings_data: dict) -> str:
    """Set the application font and palette for the saved theme and return the matching style sheet."""
    px = int(settings_data.get("base_font_px", 14))
    is_dark = settings_data.get("theme", "Dark Mode") == "Dark Mode"

    QApplication.instance().setFont(make_polished_font(EN_FONT_FAMILY, px, bold=False))

    if is_dark:
        # Open-Source Telegram Night / GitHub Dimmed Dark Palette
        THEME_COLORS.update({
            "bg_main": "#0E1621",
            "bg_card": "#17212B",
            "bg_inner": "#1E2C3A",
            "bg_drawer": "#17212B",
            "border": "#2B3848",
            "border_focus": "#58A6FF",
            "text_main": "#F0F6FC",
            "text_muted": "#8B949E",
            "accent": "#58A6FF",
            "accent_bg": "#1F3A5F",
            "accent_fg": "#FFFFFF",
        })
        btn_primary_bg = "#58A6FF"
        btn_primary_fg = "#0D1117"
        scroll_track = "#141D26"
        scroll_thumb = "#3B4C5E"
        scroll_hover = "#58A6FF"
    else:
        # Open-Source GitHub Primer Light / Material-3 Daylight Palette
        THEME_COLORS.update({
            "bg_main": "#F6F8FA",
            "bg_card": "#FFFFFF",
            "bg_inner": "#F0F4F9",
            "bg_drawer": "#FFFFFF",
            "border": "#D0D7DE",
            "border_focus": "#0969DA",
            "text_main": "#1F2328",
            "text_muted": "#59636E",
            "accent": "#0969DA",
            "accent_bg": "#DDF4FF",
            "accent_fg": "#0550AE",
        })
        btn_primary_bg = "#0969DA"
        btn_primary_fg = "#FFFFFF"
        scroll_track = "#EAEEF2"
        scroll_thumb = "#AFB8C1"
        scroll_hover = "#0969DA"

    c = THEME_COLORS

    # Sync native Qt Fusion Palette so window canvas never falls back to black
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["bg_main"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["bg_card"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["bg_inner"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["bg_inner"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_fg"]))
    QApplication.instance().setPalette(pal)

    qss = f"""
        * {{
            font-family: "{EN_FONT_FAMILY}", "{FA_FONT_FAMILY}", sans-serif;
            outline: none;
        }}
        QWidget {{
            background: transparent;
            color: {c['text_main']};
            font-size: {px}px;
        }}
        QMainWindow, QDialog, QWidget#CentralCanvas, QWidget#HomePage, QWidget#ResultsPage, QWidget#CardsContainer {{
            background-color: {c['bg_main']};
            color: {c['text_main']};
        }}
        QScrollArea, QScrollArea > QWidget > QWidget {{
            background-color: {c['bg_main']};
            border: none;
        }}
        QPushButton#HomeLogoButton {{
            background: transparent;
            border: none;
            color: {c['accent']};
            font-size: {px + 9}px;
            font-weight: 700;
            padding: 4px 10px;
        }}
        QPushButton#HeroLogoText {{
            background: transparent;
            border: none;
            color: {c['accent']};
            font-size: 68px;
            font-weight: 700;
        }}
        QLabel#CardTitle {{
            font-size: {px + 2}px;
            font-weight: 700;
            color: {c['accent']};
            background: transparent;
            border: none;
        }}
        QLabel#HeadwordTitle {{
            font-size: {px + 13}px;
            font-weight: 700;
            color: {c['text_main']};
            background: transparent;
            border: none;
        }}
        QLabel#SentenceTitle {{
            font-size: {px + 6}px;
            font-weight: 700;
            color: {c['text_main']};
            background: transparent;
            border: none;
        }}
        QLabel#IpaText {{
            font-family: "Segoe UI", "Lucida Sans Unicode", sans-serif;
            font-size: {px}px;
            color: {c['accent']};
            background: transparent;
            border: none;
        }}
        QLabel#LevelBadge {{
            color: {c['accent']};
            background: transparent;
            border: 1px solid {c['accent']};
            border-radius: 10px;
            padding: 2px 10px;
            font-size: {px - 1}px;
            font-weight: 700;
        }}
        QLabel#FrequencyBadge {{
            color: {c['text_muted']};
            background: transparent;
            border: 1px solid {c['border']};
            border-radius: 10px;
            padding: 2px 10px;
            font-size: {px - 1}px;
        }}
        QLabel#BodyText {{
            color: {c['text_main']};
            font-size: {px}px;
            background: transparent;
            border: none;
        }}
        QLabel#PersianText {{
            font-family: "{FA_FONT_FAMILY}", "Vazirmatn", sans-serif;
            color: {c['text_main']};
            font-size: {px + 1}px;
            background: transparent;
            border: none;
        }}
        QLabel#MutedText {{
            color: {c['text_muted']};
            font-size: {max(11, px - 1)}px;
            background: transparent;
            border: none;
        }}
        QCheckBox {{
            color: {c['text_main']};
            font-size: {px}px;
            spacing: 8px;
            background: transparent;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {c['text_muted']};
            border-radius: 5px;
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            border-color: {c['accent']};
            background: {c['accent']};
        }}
        QLabel#OfflineBadge {{
            color: {c['accent']};
            font-size: {max(11, px - 1)}px;
            font-weight: 600;
            background: transparent;
            border: 1px solid {c['accent']};
            border-radius: 12px;
            padding: 6px 12px;
        }}
        QLabel#HistoryTermLabel {{
            color: {c['text_main']};
            font-size: {px}px;
            font-weight: 500;
            background: transparent;
            border: none;
        }}
        QLineEdit#SearchInput {{
            background: transparent;
            color: {c['text_main']};
            border: none;
            font-size: {px + 1}px;
            padding: 2px 4px;
        }}
        QLineEdit#DrawerInput {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 18px;
            padding: 0px 14px;
            font-size: {px}px;
        }}
        QLineEdit#DrawerInput:focus {{
            border: 1.5px solid {c['accent']};
        }}
        QPushButton#PrimaryPillButton {{
            background-color: {btn_primary_bg};
            color: {btn_primary_fg};
            border: none;
            border-radius: 18px;
            font-size: {px}px;
            font-weight: 700;
        }}
        QPushButton#PrimaryPillButton:hover {{
            background-color: {c['accent_bg']};
            color: {c['accent_fg']};
        }}
        QPushButton#GlassPillButton {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 17px;
            padding: 0px 16px;
            font-size: {max(12, px - 1)}px;
            font-weight: 600;
        }}
        QPushButton#GlassPillButton:hover {{
            border: 1px solid {c['accent']};
            background-color: {c['accent_bg']};
            color: {c['accent_fg']};
        }}
        QPushButton#QuizCorrectButton {{
            background-color: rgba(46, 160, 67, 0.28);
            color: {c['text_main']};
            border: 1.5px solid #2EA043;
            border-radius: 17px;
            padding: 0px 16px;
            font-size: {max(12, px - 1)}px;
            font-weight: 700;
        }}
        QPushButton#QuizWrongButton {{
            background-color: rgba(248, 81, 73, 0.24);
            color: {c['text_main']};
            border: 1.5px solid #F85149;
            border-radius: 17px;
            padding: 0px 16px;
            font-size: {max(12, px - 1)}px;
            font-weight: 700;
        }}
        QPushButton#ClickableWordPill {{
            background-color: {c['bg_card']};
            color: {c['accent']};
            border: 1px solid {c['border']};
            border-radius: 15px;
            padding: 0px 12px;
            font-size: {px}px;
            font-weight: 700;
        }}
        QPushButton#ClickableWordPill:hover {{
            background-color: {c['accent_bg']};
            color: {c['accent_fg']};
            border: 1px solid {c['accent']};
        }}
        QPushButton#DrawerMenuItem {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 16px;
            padding: 0px 16px;
            text-align: left;
            font-size: {px}px;
            font-weight: 600;
        }}
        QPushButton#DrawerMenuItem:hover {{
            background-color: {c['accent_bg']};
            color: {c['accent_fg']};
            border: 1px solid {c['accent']};
        }}
        QPushButton#HistoryStarButton {{
            background: transparent;
            color: {c['text_muted']};
            border: none;
            border-radius: 15px;
            font-size: {px + 3}px;
        }}
        QPushButton#HistoryStarButton:hover {{
            color: {c['accent']};
            background-color: {c['bg_card']};
        }}
        QPushButton#HistoryStarButtonActive {{
            background: transparent;
            color: {c['accent']};
            border: none;
            border-radius: 15px;
            font-size: {px + 3}px;
        }}
        QPushButton#DropdownPill {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 19px;
            padding: 0px 14px;
            text-align: left;
            font-size: {px}px;
        }}
        QPushButton#DropdownPill:hover {{
            border: 1px solid {c['accent']};
        }}
        QPushButton#MenuCircleButton, QPushButton#NavCircleButton {{
            background-color: {c['bg_card']};
            border: 1px solid {c['border']};
            border-radius: 18px;
        }}
        QPushButton#MenuCircleButton {{
            border-radius: 21px;
        }}
        QPushButton#MenuCircleButton:hover, QPushButton#NavCircleButton:hover:enabled {{
            background-color: {c['accent_bg']};
            border: 1px solid {c['accent']};
        }}
        QPushButton#NavCircleButton:disabled {{
            opacity: 0.35;
        }}
        QPushButton#SpoilerPill {{
            background-color: {c['bg_card']};
            color: {c['accent']};
            border: 1px solid {c['border']};
            border-radius: 14px;
            padding: 0px 12px;
            font-size: {max(11, px - 2)}px;
            font-weight: 600;
        }}
        QPushButton#SpoilerPill:hover {{
            background-color: {c['accent_bg']};
            color: {c['accent_fg']};
            border: 1px solid {c['accent']};
        }}
        QPushButton#AudioCircleButton {{
            background-color: {c['accent_bg']};
            border: none;
            border-radius: 17px;
        }}
        QPushButton#AudioCircleButton:hover {{
            background-color: {btn_primary_bg};
        }}
        QListWidget#PopupList {{
            background: transparent;
            border: none;
            color: {c['text_main']};
            font-size: {px}px;
        }}
        QListWidget#PopupList::item {{
            padding: 6px 8px;
            border-radius: 12px;
        }}
        QListWidget#PopupList::item:hover, QListWidget#PopupList::item:selected {{
            background-color: {c['accent_bg']};
            color: {c['accent_fg']};
        }}
        QScrollBar:vertical {{
            background: {scroll_track};
            width: 12px;
            margin: 14px 2px 14px 2px;
            border-radius: 6px;
        }}
        QScrollBar::handle:vertical {{
            background: {scroll_thumb};
            min-height: 44px;
            border-radius: 5px;
            margin: 1px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {scroll_hover};
        }}
        QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {{
            height: 0px;
            width: 0px;
            background: none;
            border: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: {scroll_track};
            height: 12px;
            margin: 2px 14px 2px 14px;
            border-radius: 6px;
        }}
        QScrollBar::handle:horizontal {{
            background: {scroll_thumb};
            min-width: 44px;
            border-radius: 5px;
            margin: 1px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {scroll_hover};
        }}
        QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {{
            height: 0px;
            width: 0px;
            background: none;
            border: none;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        """
    return qss
