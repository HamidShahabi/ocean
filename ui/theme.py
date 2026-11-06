"""Fonts, vector icons, the colour palette and the Qt style sheet (light and dark).

The themes are a hand-tuned Material 3 palette (Google-style neutrals, one blue accent, tonal
surfaces, state layers for hover and pressed), not a third-party style sheet: a library QSS would
restyle every widget and fight the custom glass frames, capsule dropdowns and cards."""
import os
import struct
import sys
import tempfile
from typing import Dict, List

from PyQt6.QtCore import Qt, QBuffer, QIODevice, QPointF, QRectF
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
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

from ui.config import BASE_DIR

# The font families are rebound by register_custom_fonts(): read them as `theme.EN_FONT_FAMILY`
# (not `from ui.theme import EN_FONT_FAMILY`, which would keep the value from import time).
EN_FONT_FAMILY = "Google Sans Flex"
FA_FONT_FAMILY = "Vazirmatn"
# Charis SIL is designed for phonetics (every IPA letter, stress mark and diacritic, with open,
# unambiguous shapes). It is used for pronunciation text only; Google Sans Flex has no IPA letters.
IPA_FONT_FAMILY = "Charis SIL"
IPA_OBJECT_NAME = "IpaText"  # the QLabel objectName the style sheet maps to IPA_FONT_FAMILY
# Qt swaps Left/Right for right-to-left text unless AlignAbsolute is set, so plain AlignRight
# renders Persian flush left.
PERSIAN_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter

# Material 3 colour roles (Google's neutral + blue baseline): `bg_card` is surface-container,
# `bg_inner` surface-container-high, `bg_popup` the highest container, `accent_bg`/`accent_fg`
# the container pair used for selected and hovered list items.
DARK_COLORS = {
    "bg_main": "#121316",
    "bg_card": "#1B1C20",
    "bg_inner": "#26282D",
    "bg_drawer": "#1B1C20",
    "bg_popup": "#2B2D33",
    "border": "#34363D",
    "border_focus": "#A8C7FA",
    "text_main": "#E4E6EB",
    "text_muted": "#A0A5AF",
    "accent": "#A8C7FA",
    "accent_bg": "#2B4A75",
    "accent_fg": "#D8E6FF",
    "on_primary": "#062E6F",
    "shadow": "#000000",
}
LIGHT_COLORS = {
    "bg_main": "#F6F8FC",
    "bg_card": "#FFFFFF",
    "bg_inner": "#EDF1F8",
    "bg_drawer": "#FFFFFF",
    "bg_popup": "#FFFFFF",
    "border": "#DCE1EA",
    "border_focus": "#0B57D0",
    "text_main": "#1B1F27",
    "text_muted": "#5A6270",
    "accent": "#0B57D0",
    "accent_bg": "#D6E4FC",
    "accent_fg": "#062E6F",
    "on_primary": "#FFFFFF",
    "shadow": "#1B2B4A",
}
# Mutated in place by apply_theme(); modules read the current colours from here when they paint.
THEME_COLORS = dict(DARK_COLORS)


def is_dark_theme(settings_data: dict) -> bool:
    """Anything but the saved "Light Mode" (a missing or unknown value too) is the dark theme."""
    return settings_data.get("theme", "Dark Mode") != "Light Mode"


def mix(a: str, b: str, t: float) -> str:
    """The colour `t` (0..1) of the way from `a` to `b`: Material's state layer of `b` over `a`."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


# === SECTION 2: FONT REGISTRATION & POLISHING ===
_REGISTERED_FONTS: Dict[str, List[str]] = {}  # font file path -> the families Qt reports for it


def _font_dirs() -> List[str]:
    """Where the bundled fonts live: next to the sources (the parent of the ui/ package) or the frozen exe.
    BASE_DIR is the ui/ folder when running from source, so looking only in BASE_DIR/fonts found nothing."""
    dirs = [os.path.join(BASE_DIR, "fonts"), os.path.join(os.path.dirname(BASE_DIR), "fonts")]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "fonts"))
        dirs.append(os.path.join(getattr(sys, "_MEIPASS", ""), "fonts"))
    found: List[str] = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in found:
            found.append(d)
    return found


def _family_of(prefix: str, fallback: str) -> str:
    """The family Qt reports for the registered `<prefix>-Regular.ttf` (or `fallback`)."""
    for path, families in _REGISTERED_FONTS.items():
        if os.path.basename(path).lower().startswith(prefix.lower() + "-regular") and families:
            return families[0]
    return fallback


def register_custom_fonts() -> None:
    """Load the bundled TTFs (Google Sans Flex, Vazirmatn, Charis SIL) and bind the family names."""
    global EN_FONT_FAMILY, FA_FONT_FAMILY, IPA_FONT_FAMILY
    for d in _font_dirs():
        for fn in sorted(os.listdir(d)):
            path = os.path.join(d, fn)
            if fn.lower().endswith((".ttf", ".otf")) and path not in _REGISTERED_FONTS:
                font_id = QFontDatabase.addApplicationFont(path)
                _REGISTERED_FONTS[path] = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    EN_FONT_FAMILY = _family_of("GoogleSansFlex", EN_FONT_FAMILY)
    FA_FONT_FAMILY = _family_of("Vazirmatn", FA_FONT_FAMILY)
    IPA_FONT_FAMILY = _family_of("CharisSIL", IPA_FONT_FAMILY)


def make_polished_font(family: str, size_px: int, bold: bool = False) -> QFont:
    """A UI font: `family` first, Vazirmatn behind it so Persian text in an English-font widget still
    gets the Persian face. Hinting and kerning stay at the platform defaults: the old explicit
    PreferNoHinting + PreferQuality is what we suspected of mangling the `y` and `T` glyphs on Windows
    (and, running from source, the bundled fonts were never even found)."""
    f = QFont(family)
    if family != FA_FONT_FAMILY:
        f.setFamilies([family, FA_FONT_FAMILY])
    f.setPixelSize(size_px)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    f.setKerning(True)
    return f


def make_ipa_font(size_px: int) -> QFont:
    """The font for pronunciation text (and nothing else)."""
    f = QFont(IPA_FONT_FAMILY)
    f.setPixelSize(size_px)
    f.setWeight(QFont.Weight.Normal)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    f.setKerning(True)
    return f


def style_ipa_label(widget: QWidget) -> None:
    """Make a label that shows IPA use the IPA font and colour from the style sheet (the
    results page's pronunciation labels already carry this object name)."""
    widget.setObjectName(IPA_OBJECT_NAME)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


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


def paint_soft_shadow(p: QPainter, rect: QRectF, radius: float, spread: int = 12, offset_y: float = 3.0,
                      strength: float = 0.20) -> None:
    """Soft elevation under a rounded card: stacked translucent outlines that fade outwards.
    `rect` is the card's rectangle; the shadow extends `spread` px around it, so the host window
    must leave that margin (used by the popups, which are top-level translucent windows)."""
    col = QColor(THEME_COLORS["shadow"])
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(spread, 0, -1):
        t = 1.0 - i / (spread + 1.0)
        col.setAlphaF(strength * t * t * 0.55)
        p.setPen(QPen(col, 1.6))
        grow = float(i)
        p.drawRoundedRect(rect.adjusted(-grow, -grow + offset_y, grow, grow + offset_y), radius + grow, radius + grow)
    p.restore()


# === SECTION 4: MATERIAL-3 DAYLIGHT & NIGHT THEME ENGINE ===
def sync_windows_titlebar(window: QMainWindow, is_dark: bool, bg_hex: str, fg_hex: str = "") -> None:
    """Synchronizes the native Windows 10/11 titlebar with Ocean's Light or Dark theme."""
    try:
        import ctypes
        hwnd = int(window.winId())
        dark_val = ctypes.c_int(1 if is_dark else 0)
        dwm = ctypes.windll.dwmapi
        # 20 is DWMWA_USE_IMMERSIVE_DARK_MODE; Windows 10 before 20H1 only knows the undocumented 19.
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark_val), ctypes.sizeof(dark_val))
        for attr, hex_val in ((35, bg_hex), (36, fg_hex)):  # caption colour, caption text colour (Windows 11)
            if hex_val:
                col = QColor(hex_val)
                colorref = ctypes.c_int((col.blue() << 16) | (col.green() << 8) | col.red())
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(colorref), ctypes.sizeof(colorref))
    except Exception:
        pass


def _asset_url(name: str, draw) -> str:
    """Path (as a QSS url) of a small PNG used by the style sheet, drawn once into the temp folder."""
    folder = os.path.join(tempfile.gettempdir(), "ocean-ui-assets")
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        try:
            os.makedirs(folder, exist_ok=True)
            pix = QPixmap(36, 36)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            draw(p)
            p.end()
            pix.save(path, "PNG")
        except Exception:
            return "none"
    return "url(" + path.replace("\\", "/") + ")"


def _check_mark_url(color_hex: str) -> str:
    def draw(p: QPainter) -> None:
        p.setPen(QPen(QColor(color_hex), 4.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(*[QPointF(x, y) for x, y in ((9, 19), (15.5, 25.5), (27, 11.5))])
    return _asset_url(f"check-{color_hex.lstrip('#')}.png", draw)


def apply_theme(settings_data: dict) -> str:
    """Set the application font and palette for the saved theme and return the matching style sheet."""
    px = int(settings_data.get("base_font_px", 14))
    is_dark = is_dark_theme(settings_data)

    QApplication.instance().setFont(make_polished_font(EN_FONT_FAMILY, px, bold=False))
    THEME_COLORS.update(DARK_COLORS if is_dark else LIGHT_COLORS)
    c = THEME_COLORS

    # State layers (Material): the accent over a surface for hover and pressed.
    primary_hover = mix(c["accent"], c["on_primary"], 0.10)
    primary_pressed = mix(c["accent"], c["on_primary"], 0.18)
    inner_hover = mix(c["bg_inner"], c["accent"], 0.14)
    inner_pressed = mix(c["bg_inner"], c["accent"], 0.24)
    card_hover = mix(c["bg_card"], c["accent"], 0.12)
    disabled_fg = mix(c["bg_inner"], c["text_muted"], 0.55)
    scroll_thumb = mix(c["bg_main"], c["text_muted"], 0.40)
    scroll_hover = mix(c["bg_main"], c["text_muted"], 0.75)
    check_url = _check_mark_url(c["on_primary"])

    # Sync native Qt Fusion Palette so window canvas never falls back to black
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["bg_main"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["bg_card"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["bg_inner"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["text_muted"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["bg_inner"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["bg_popup"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text_main"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent_bg"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_fg"]))
    pal.setColor(QPalette.ColorRole.Link, QColor(c["accent"]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(disabled_fg))
    QApplication.instance().setPalette(pal)

    # No `font-family`/`font-size` on the generic selectors: the application font above already
    # carries both, and a QSS font on every widget makes Qt rebuild each font on every polish.
    qss = f"""
        QWidget {{
            background: transparent;
            color: {c['text_main']};
            outline: none;
        }}
        QMainWindow, QDialog, QWidget#CentralCanvas, QWidget#HomePage, QWidget#ResultsPage, QWidget#CardsContainer {{
            background-color: {c['bg_main']};
            color: {c['text_main']};
        }}
        QScrollArea, QScrollArea > QWidget > QWidget {{
            background-color: {c['bg_main']};
            border: none;
        }}
        QToolTip {{
            background-color: {c['bg_popup']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            padding: 6px 10px;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 12px;
            padding: 6px 10px;
            selection-background-color: {c['accent_bg']};
            selection-color: {c['accent_fg']};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border: 1.5px solid {c['accent']};
        }}
        QPushButton {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 16px;
            padding: 6px 18px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: {inner_hover};
            border: 1px solid {c['accent']};
        }}
        QPushButton:pressed {{
            background-color: {inner_pressed};
        }}
        QPushButton:disabled {{
            color: {disabled_fg};
        }}
        QPushButton#HomeLogoButton {{
            background: transparent;
            border: none;
            color: {c['accent']};
            font-size: {px + 9}px;
            font-weight: 600;
            padding: 4px 10px;
        }}
        QPushButton#HeroLogoText {{
            background: transparent;
            border: none;
            color: {c['accent']};
            font-size: 68px;
            font-weight: 500;
        }}
        QLabel#CardTitle {{
            font-size: {px + 2}px;
            font-weight: 600;
            color: {c['accent']};
            background: transparent;
            border: none;
        }}
        QLabel#HeadwordTitle {{
            font-size: {px + 13}px;
            font-weight: 600;
            color: {c['text_main']};
            background: transparent;
            border: none;
        }}
        QLabel#SentenceTitle {{
            font-size: {px + 6}px;
            font-weight: 600;
            color: {c['text_main']};
            background: transparent;
            border: none;
        }}
        QLabel#{IPA_OBJECT_NAME} {{
            font-family: "{IPA_FONT_FAMILY}", "Noto Sans", "Segoe UI", "Lucida Sans Unicode", sans-serif;
            font-size: {px + 1}px;
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
            font-weight: 600;
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
            spacing: 8px;
            background: transparent;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border: 1.5px solid {c['text_muted']};
            border-radius: 5px;
            background: transparent;
        }}
        QCheckBox::indicator:hover {{
            border-color: {c['accent']};
            background: {card_hover};
        }}
        QCheckBox::indicator:checked {{
            border-color: {c['accent']};
            background: {c['accent']};
            image: {check_url};
        }}
        QLabel#HistoryTermLabel {{
            color: {c['text_main']};
            font-weight: 500;
            background: transparent;
            border: none;
        }}
        QLabel#DropdownValue {{
            color: {c['text_main']};
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
        }}
        QLineEdit#DrawerInput:focus {{
            border: 1.5px solid {c['accent']};
        }}
        QPushButton#PrimaryPillButton {{
            background-color: {c['accent']};
            color: {c['on_primary']};
            border: none;
            border-radius: 18px;
            padding: 0px 16px;
            font-weight: 600;
        }}
        QPushButton#PrimaryPillButton:hover {{
            background-color: {primary_hover};
            border: none;
        }}
        QPushButton#PrimaryPillButton:pressed {{
            background-color: {primary_pressed};
        }}
        QPushButton#PrimaryPillButton:disabled {{
            background-color: {c['bg_inner']};
            color: {disabled_fg};
        }}
        QPushButton#GlassPillButton {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 17px;
            padding: 0px 16px;
            font-size: {max(12, px - 1)}px;
            font-weight: 500;
        }}
        QPushButton#GlassPillButton:hover {{
            border: 1px solid {c['accent']};
            background-color: {inner_hover};
        }}
        QPushButton#ClickableWordPill {{
            background-color: {c['bg_card']};
            color: {c['accent']};
            border: 1px solid {c['border']};
            border-radius: 15px;
            padding: 0px 12px;
            font-weight: 500;
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
            font-weight: 500;
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
            border-radius: 12px;
            padding: 0px;
            font-size: {px + 1}px;
        }}
        QPushButton#HistoryStarButton:hover {{
            color: {c['accent']};
            background-color: {card_hover};
        }}
        QPushButton#HistoryStarButtonActive {{
            background: transparent;
            color: {c['accent']};
            border: none;
            border-radius: 12px;
            padding: 0px;
            font-size: {px + 1}px;
        }}
        QPushButton#DropdownPill {{
            background-color: {c['bg_inner']};
            color: {c['text_main']};
            border: 1px solid {c['border']};
            border-radius: 19px;
            padding: 0px;
            text-align: left;
        }}
        QPushButton#DropdownPill:hover {{
            border: 1px solid {c['accent']};
            background-color: {inner_hover};
        }}
        QPushButton#MenuCircleButton, QPushButton#NavCircleButton {{
            background-color: {c['bg_card']};
            border: 1px solid {c['border']};
            border-radius: 18px;
            padding: 0px;
        }}
        QPushButton#MenuCircleButton {{
            border-radius: 21px;
        }}
        QPushButton#MenuCircleButton:hover, QPushButton#NavCircleButton:hover:enabled {{
            background-color: {card_hover};
            border: 1px solid {c['accent']};
        }}
        QPushButton#NavCircleButton:disabled {{
            background-color: {c['bg_card']};
            border: 1px solid {c['border']};
        }}
        QPushButton#SpoilerPill {{
            background-color: {c['bg_card']};
            color: {c['accent']};
            border: 1px solid {c['border']};
            border-radius: 14px;
            padding: 0px 12px;
            font-size: {max(11, px - 2)}px;
            font-weight: 500;
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
            padding: 0px;
        }}
        QPushButton#AudioCircleButton:hover {{
            background-color: {mix(c['accent_bg'], c['accent'], 0.35)};
        }}
        QListWidget#PopupList {{
            background: transparent;
            border: none;
            color: {c['text_main']};
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
            background: transparent;
            width: 12px;
            margin: 14px 2px 14px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {scroll_thumb};
            min-height: 44px;
            border-radius: 4px;
            margin: 0px 1px;
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
            background: transparent;
            height: 12px;
            margin: 2px 14px 2px 14px;
        }}
        QScrollBar::handle:horizontal {{
            background: {scroll_thumb};
            min-width: 44px;
            border-radius: 4px;
            margin: 1px 0px;
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


def write_app_icon_ico(ico_path: str) -> None:
    """Write the app icon as a single-image .ico (the Windows build embeds it in Ocean.exe)."""
    pixmap = VectorIcons.ocean_app_icon().pixmap(256, 256)
    ba = QBuffer()
    ba.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(ba, "PNG")
    png_bytes = bytes(ba.data())
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
    with open(ico_path, "wb") as f:
        f.write(header + entry + png_bytes)
