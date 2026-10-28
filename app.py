# === SECTION 1: IMPORTS & ENVIRONMENT SETUP ===
import io
import os
import html
import re
import sys
import json
import wave
import logging
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lexical_engine
import review
import storage
import anki_export
import desktop_lookup
from PyQt6.QtCore import Qt, QThread, QDeadlineTimer, QStandardPaths, pyqtSignal, QBuffer, QIODevice, QTimer, QPoint, QRectF, QSize, QEvent, QObject
from PyQt6.QtGui import (
    QFont,
    QFontDatabase,
    QKeySequence,
    QShortcut,
    QIcon,
    QPixmap,
    QPainter,
    QPainterPath,
    QPen,
    QColor,
    QPalette,
    QLinearGradient,
    QCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QScrollArea,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QFormLayout,
    QStackedWidget,
    QSizePolicy,
    QDialog,
    QFileDialog,
)

try:
    import numpy as np
    from kokoro_onnx import Kokoro
    HAS_KOKORO = True
except Exception:
    HAS_KOKORO = False

try:
    import pythoncom
    import win32com.client
    HAS_WIN32_SAPI = True
except Exception:
    HAS_WIN32_SAPI = False

try:
    import winsound
    HAS_WINSOUND = True
except Exception:
    HAS_WINSOUND = False

# Linux/macOS playback: first available CLI player that accepts WAV on stdin.
WAV_PLAYER_CMD = next(
    (cmd for cmd in (["pw-play", "-"], ["paplay"], ["aplay", "-q", "-"], ["afplay", "-"]) if shutil.which(cmd[0])),
    None,
)
FALLBACK_TTS_BIN = shutil.which("spd-say") or shutil.which("espeak-ng") or shutil.which("espeak")

log = logging.getLogger("ocean.app")


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "offline_lexicon.json")):
            return exe_dir
        parent_dir = os.path.abspath(os.path.join(exe_dir, "..", ".."))
        if os.path.exists(os.path.join(parent_dir, "app.py")):
            return parent_dir
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
DB_FILE = lexical_engine.user_data_path("lexicon_vault.db")
SETTINGS_FILE = lexical_engine.user_data_path("ocean_settings.json")

EN_FONT_FAMILY = "Inter"
# Qt swaps Left/Right for right-to-left text unless AlignAbsolute is set, so plain AlignRight
# renders Persian flush left.
PERSIAN_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter
FA_FONT_FAMILY = "Vazirmatn"

VOICE_OPTIONS = [
    "am_michael (Male - US)",
    "af_heart (Female - US)",
]

SPEED_OPTIONS = [
    "-3 (Slow)",
    "-2 (Relaxed)",
    "-1 (Slightly Slow)",
    "0 (Normal 1.0x)",
    "+1 (Slightly Fast)",
    "+2 (Fast)",
    "+3 (Very Fast)",
]

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


# === SECTION 4: SETTINGS, AUDIO SPEAKER & DATABASE MANAGERS ===
class SettingsManager:
    DEFAULTS = {
        "font_scale": "Medium (14px)",
        "base_font_px": 14,
        "learning_mode": "Click to Reveal Persian",
        "speech_rate": 0,
        "voice_id": "am_michael (Male - US)",
        "theme": "Dark Mode",
    }

    def __init__(self, path: str = SETTINGS_FILE) -> None:
        self.path = path
        self.data: Dict[str, Any] = dict(self.DEFAULTS)
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
                if self.data.get("voice_id") not in VOICE_OPTIONS:
                    self.data["voice_id"] = VOICE_OPTIONS[0]
            except Exception:
                log.exception("Could not read settings %s; using defaults", self.path)

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            log.exception("Could not save settings %s", self.path)


class NeuralSpeaker:
    def __init__(self, settings: SettingsManager) -> None:
        self.settings = settings
        self._kokoro: Optional[Any] = None
        self._player: Optional[subprocess.Popen] = None
        # Not a daemon: quitting while onnxruntime is mid-load (~1s after start) crashes the process,
        # so let interpreter shutdown wait for the load to finish.
        threading.Thread(target=self._init_kokoro).start()

    def _find_model_files(self) -> tuple[str, str]:
        candidates = [
            BASE_DIR,
            os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else "",
            os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "..")) if getattr(sys, "frozen", False) else "",
        ]
        for c in candidates:
            if c:
                onnx_p = os.path.join(c, "kokoro-v1.0.onnx")
                voic_p = os.path.join(c, "voices-v1.0.bin")
                if os.path.exists(onnx_p) and os.path.exists(voic_p):
                    return onnx_p, voic_p
        return "", ""

    def _init_kokoro(self) -> None:
        if not HAS_KOKORO:
            return
        onnx_p, voic_p = self._find_model_files()
        if onnx_p and voic_p:
            try:
                self._kokoro = Kokoro(onnx_p, voic_p)
            except Exception:
                log.exception("Could not load Kokoro voice model %s", onnx_p)
                self._kokoro = None
        else:
            log.info("Kokoro model files not found; using fallback voice")

    def speak(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        threading.Thread(target=self._speak_worker, args=(clean,), daemon=True).start()

    def _speak_worker(self, text: str) -> None:
        voice_choice = self.settings.data.get("voice_id", VOICE_OPTIONS[0])
        rate_offset = int(self.settings.data.get("speech_rate", 0))
        speed = max(0.75, min(1.3, 1.0 + (rate_offset * 0.08)))

        if self._kokoro is None and HAS_KOKORO:
            self._init_kokoro()

        if self._kokoro is not None:
            voice_code = voice_choice.split()[0].strip()
            try:
                samples, sample_rate = self._kokoro.create(
                    text, voice=voice_code, speed=speed, lang="en-us"
                )
                pad_start = np.zeros(int(sample_rate * 0.12), dtype=np.float32)
                pad_end = np.zeros(int(sample_rate * 0.45), dtype=np.float32)
                full_audio = np.concatenate([pad_start, samples, pad_end])
                pcm16 = (np.clip(full_audio, -1.0, 1.0) * 32767).astype(np.int16)

                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm16.tobytes())

                if self._play_wav(buf.getvalue()):
                    return
                log.warning("No WAV player found (pw-play/paplay/aplay); using fallback voice")
            except Exception:
                log.exception("Kokoro speech failed")

        if HAS_WIN32_SAPI:
            try:
                pythoncom.CoInitialize()
                v = win32com.client.Dispatch("SAPI.SpVoice")
                v.Rate = rate_offset
                escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                v.Speak(f'<silence msec="120"/>{escaped}<silence msec="450"/>', 8)
            except Exception:
                log.exception("Windows SAPI speech failed")
        elif FALLBACK_TTS_BIN:
            # SAPI rate is -10..10; spd-say takes -100..100, espeak takes words-per-minute.
            if os.path.basename(FALLBACK_TTS_BIN) == "spd-say":
                cmd = [FALLBACK_TTS_BIN, "-w", "-r", str(rate_offset * 10), "--", text]
            else:
                cmd = [FALLBACK_TTS_BIN, "-s", str(175 + rate_offset * 15), "--", text]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            except Exception:
                log.exception("Fallback speech (%s) failed", FALLBACK_TTS_BIN)

    def _play_wav(self, wav_bytes: bytes) -> bool:
        if HAS_WINSOUND:
            winsound.PlaySound(None, winsound.SND_PURGE)
            winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
            return True
        if WAV_PLAYER_CMD is None:
            return False
        # Stop any clip still playing, mirroring SND_PURGE.
        prev = self._player
        if prev is not None and prev.poll() is None:
            prev.terminate()
        proc = subprocess.Popen(WAV_PLAYER_CMD, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._player = proc
        try:
            proc.communicate(wav_bytes, timeout=120)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            proc.kill()
        return True


# === SECTION 5: BACKGROUND WORKERS ===
_RUNNING_WORKERS: set = set()


def start_worker(worker: QThread) -> None:
    """Start a worker and keep it referenced until it finishes: a running QThread whose
    Python wrapper gets garbage-collected (e.g. replaced by a newer search) aborts the process."""
    _RUNNING_WORKERS.add(worker)
    worker.finished.connect(lambda: _RUNNING_WORKERS.discard(worker))
    worker.finished.connect(worker.deleteLater)
    worker.start()


def exit_app(exit_code: int, grace_ms: int = 2000) -> None:
    """Exit after the event loop ends. Workers still busy (e.g. a slow lookup) would crash
    Python's teardown, which destroys their running QThreads; workers only read, and every
    save happens synchronously on the GUI thread, so skipping the teardown loses nothing."""
    deadline = QDeadlineTimer(grace_ms)
    for worker in list(_RUNNING_WORKERS):
        worker.wait(deadline)
    if any(w.isRunning() for w in _RUNNING_WORKERS):
        log.info("Workers still running at exit; skipping interpreter teardown")
        logging.shutdown()
        os._exit(exit_code)
    sys.exit(exit_code)


class SuggestionWorker(QThread):
    suggestions_ready = pyqtSignal(list)

    def __init__(self, prefix: str, local_history: List[str]) -> None:
        super().__init__()
        self.prefix = prefix.strip()
        self.local_history = local_history

    def run(self) -> None:
        q = self.prefix.lower()
        if len(q) < 2:
            return
        local_matches = [w for w in self.local_history if w.lower().startswith(q)]
        remote_matches = lexical_engine.fetch_live_suggestions(self.prefix)
        combined = list(dict.fromkeys(local_matches + remote_matches))[:6]
        self.suggestions_ready.emit(combined)


class EngineWorker(QThread):
    finished_success = pyqtSignal(str, dict)
    finished_error = pyqtSignal(str)

    def __init__(self, term: str, deep_mode: bool = False) -> None:
        super().__init__()
        self.term = term
        self.deep_mode = deep_mode

    def run(self) -> None:
        try:
            res = lexical_engine.analyze_with_dictionaries(self.term, deep_mode=self.deep_mode)
            self.finished_success.emit(res.get("headword", self.term), res)
        except Exception as exc:
            log.exception("Lookup failed for %r", self.term)
            self.finished_error.emit(str(exc))


class AutoFixWorker(QThread):
    fixed_ready = pyqtSignal(str)

    def __init__(self, en_source: str) -> None:
        super().__init__()
        self.en_source = en_source

    def run(self) -> None:
        res = lexical_engine.auto_fix_translation_deep(self.en_source)
        self.fixed_ready.emit(res)


# === SECTION 6: REUSABLE GLASS UI COMPONENTS & HARMONIOUS EDIT DIALOG ===
class SmoothGlassFrame(QFrame):
    def __init__(self, radius: int = 22, variant: str = "card", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.radius = radius
        self.variant = variant
        self.hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self.variant == "inner":
            bg = QColor(THEME_COLORS["bg_inner"])
        elif self.variant == "drawer":
            bg = QColor(THEME_COLORS["bg_drawer"])
        else:
            bg = QColor(THEME_COLORS["bg_card"])

        if self.variant == "search" and self.hovered:
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

        title = QLabel("Teach Ocean \u2014 Edit Persian Translation")
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
        vbox.setContentsMargins(0, 0, 0, 0)

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
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        c_lay.addWidget(self.list_widget)
        vbox.addWidget(self.card)

    def open_at(self, options: List[str], current_val: str, anchor: QWidget, min_w: int = 200) -> None:
        self.list_widget.clear()
        for opt in options:
            prefix = "\u2713  " if opt == current_val else "     "
            it = QListWidgetItem(f"{prefix}{opt}")
            it.setData(Qt.ItemDataRole.UserRole, opt)
            self.list_widget.addItem(it)

        w = max(anchor.width(), min_w)
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


class ModernGlassDropdown(QPushButton):
    valueChanged = pyqtSignal(str)

    def __init__(self, options: List[str], initial: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.options = options
        self.current_val = initial if initial in options else (options[0] if options else "")
        self.setObjectName("DropdownPill")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(38)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.popup = GlassPopupMenu(self)
        self.popup.option_picked.connect(self._on_picked)
        self.clicked.connect(self._open_popup)
        self._update_label()

    def _update_label(self) -> None:
        self.setText(f"{self.current_val}  \u25be")

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
        vbox.setContentsMargins(0, 0, 0, 0)

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
        self.list_widget.itemClicked.connect(self._on_clicked)
        c_lay.addWidget(self.list_widget)
        vbox.addWidget(self.container)

    def show_suggestions(self, items: List[str], anchor_widget: QWidget) -> None:
        if not items:
            self.hide()
            return
        self.list_widget.clear()
        for word in items:
            it = QListWidgetItem(f"   {word}")
            it.setData(Qt.ItemDataRole.UserRole, word)
            self.list_widget.addItem(it)

        row_h = 42
        total_h = min(len(items) * row_h + 24, 280)
        self.setFixedWidth(anchor_widget.width())
        self.setFixedHeight(total_h)
        pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 8))
        self.move(pos)
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
        self._fix_worker: Optional[AutoFixWorker] = None

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

        self.autofix_btn = QPushButton("Auto-Fix")
        self.autofix_btn.setObjectName("SpoilerPill")
        self.autofix_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.autofix_btn.setFixedHeight(28)
        self.autofix_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.autofix_btn.clicked.connect(self.trigger_auto_fix)

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
        self.label.setFont(make_polished_font(FA_FONT_FAMILY, font_px + (2 if large else 0), bold=large))
        self.label.setObjectName("PersianText")

        layout.addWidget(self.reveal_btn, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.autofix_btn, 0, Qt.AlignmentFlag.AlignRight)
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
        show_tools = self.revealed and self._is_missing_translation() and bool(self.en_source)
        self.autofix_btn.setVisible(show_tools)
        self.edit_btn.setVisible(show_tools)

    def _reveal(self) -> None:
        self.revealed = True
        self._sync_visibility()

    def trigger_auto_fix(self) -> None:
        if not self.en_source:
            return
        self.autofix_btn.setText("Searching...")
        self.autofix_btn.setEnabled(False)
        self._fix_worker = AutoFixWorker(self.en_source)
        self._fix_worker.fixed_ready.connect(self._on_auto_fixed)
        start_worker(self._fix_worker)

    def _on_auto_fixed(self, new_fa: str) -> None:
        self.autofix_btn.setText("Auto-Fix")
        self.autofix_btn.setEnabled(True)
        if new_fa and "\u06cc\u0627\u0641\u062a \u0646\u0634\u062f" not in new_fa:
            self._apply_new_persian(new_fa)

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
            self.main_win.db.save_entry(self.main_win.current_term, d)

    def enterEvent(self, event) -> None:
        if self.mode == "Hover to Reveal Persian" and not self.revealed:
            self._reveal()
        super().enterEvent(event)


# === SECTION 7: ATTACHED RIGHT SIDE DRAWER (DYNAMIC ROW HEIGHTS & NO GEMINI KEY ROW) ===
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
        self.back_menu_btn.clicked.connect(lambda: self.show_page(0))
        self.back_menu_btn.hide()
        header.addWidget(self.back_menu_btn)

        vbox.addLayout(header)

        self.stack = QStackedWidget()
        vbox.addWidget(self.stack, stretch=1)

        # PAGE 0: Main Menu
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

        btn_hist = QPushButton("History")
        btn_hist.setObjectName("DrawerMenuItem")
        btn_hist.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_hist.setFixedHeight(46)
        btn_hist.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_hist.clicked.connect(lambda: self.show_page(1))
        m_lay.addWidget(btn_hist)

        btn_set = QPushButton("Settings")
        btn_set.setObjectName("DrawerMenuItem")
        btn_set.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_set.setFixedHeight(46)
        btn_set.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_set.clicked.connect(lambda: self.show_page(2))
        m_lay.addWidget(btn_set)

        btn_teach = QPushButton("Teach Ocean")
        btn_teach.setObjectName("DrawerMenuItem")
        btn_teach.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_teach.setFixedHeight(46)
        btn_teach.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_teach.clicked.connect(lambda: self.show_page(3))
        m_lay.addWidget(btn_teach)

        m_lay.addStretch()
        self.stack.addWidget(page_menu)

        # PAGE 1: History View
        page_hist = QWidget()
        h_lay = QVBoxLayout(page_hist)
        h_lay.setContentsMargins(0, 4, 0, 0)
        h_lay.setSpacing(10)

        self.filter_drop = ModernGlassDropdown(["All Recent Searches", "Saved Favorites Only"], "All Recent Searches", self)
        self.filter_drop.valueChanged.connect(lambda _: self.populate_history())
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

        export_btn = QPushButton("Export to Anki")
        export_btn.setObjectName("GlassPillButton")
        export_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        export_btn.setFixedHeight(36)
        export_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        export_btn.setToolTip("Export the entries listed above as an Anki import file")
        export_btn.clicked.connect(self._export_anki)

        hist_btns = QHBoxLayout()
        hist_btns.setSpacing(10)
        hist_btns.addWidget(del_btn, stretch=1)
        hist_btns.addWidget(export_btn, stretch=1)
        h_lay.addLayout(hist_btns)
        self.stack.addWidget(page_hist)

        # PAGE 2: Settings View (Removed Gemini AI Key as requested in Image 4)
        page_set = QWidget()
        s_lay = QVBoxLayout(page_set)
        s_lay.setContentsMargins(0, 4, 0, 0)
        s_lay.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(12)

        self.theme_drop = ModernGlassDropdown(["Dark Mode", "Light Mode"], self.main_win.settings.data.get("theme", "Dark Mode"), self)
        form.addRow("Theme:", self.theme_drop)

        scales = ["Small (12px)", "Medium (14px)", "Large (16px)", "Extra Large (18px)"]
        self.font_drop = ModernGlassDropdown(scales, self.main_win.settings.data.get("font_scale", "Medium (14px)"), self)
        form.addRow("Font Size:", self.font_drop)

        modes = ["Click to Reveal Persian", "Hover to Reveal Persian", "Always Show"]
        self.mode_drop = ModernGlassDropdown(modes, self.main_win.settings.data.get("learning_mode", "Click to Reveal Persian"), self)
        form.addRow("Learning Mode:", self.mode_drop)

        cur_v = self.main_win.settings.data.get("voice_id", VOICE_OPTIONS[0])
        if cur_v not in VOICE_OPTIONS:
            cur_v = VOICE_OPTIONS[0]
        self.voice_drop = ModernGlassDropdown(VOICE_OPTIONS, cur_v, self)
        form.addRow("TTS Voice:", self.voice_drop)

        cur_rate = int(self.main_win.settings.data.get("speech_rate", 0))
        rate_label = next((s for s in SPEED_OPTIONS if s.startswith(f"{cur_rate:+d}" if cur_rate != 0 else "0")), "0 (Normal 1.0x)")
        self.speed_drop = ModernGlassDropdown(SPEED_OPTIONS, rate_label, self)
        form.addRow("Speech Speed:", self.speed_drop)

        s_lay.addLayout(form)

        self.test_voice_btn = QPushButton("  Test Voice")
        self.test_voice_btn.setObjectName("GlassPillButton")
        self.test_voice_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.test_voice_btn.setFixedHeight(38)
        self.test_voice_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.test_voice_btn.clicked.connect(self._test_voice)
        s_lay.addWidget(self.test_voice_btn)

        s_lay.addStretch()

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("GlassPillButton")
        apply_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        apply_btn.setFixedHeight(38)
        apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        apply_btn.clicked.connect(self._apply_live)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("PrimaryPillButton")
        save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        save_btn.setFixedHeight(38)
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.clicked.connect(self._save_and_close)

        action_row.addWidget(apply_btn, stretch=1)
        action_row.addWidget(save_btn, stretch=1)
        s_lay.addLayout(action_row)

        self.stack.addWidget(page_set)

        # PAGE 3: Teach Ocean
        page_teach = QWidget()
        t_lay = QVBoxLayout(page_teach)
        t_lay.setContentsMargins(0, 4, 0, 0)
        t_lay.setSpacing(10)

        t_desc = QLabel("Add or customize any word, idiom, or Persian meaning in Ocean's permanent offline memory:")
        t_desc.setWordWrap(True)
        t_desc.setObjectName("MutedText")
        t_lay.addWidget(t_desc)

        self.teach_term = QLineEdit()
        self.teach_term.setObjectName("DrawerInput")
        self.teach_term.setPlaceholderText("English Word or Idiom (e.g. get lousy)")
        self.teach_term.setFixedHeight(36)
        t_lay.addWidget(self.teach_term)

        self.teach_fa = QLineEdit()
        self.teach_fa.setObjectName("DrawerInput")
        self.teach_fa.setPlaceholderText("Persian Meaning (\u0645\u0639\u0627\u062f\u0644 \u062f\u0642\u06cc\u0642 \u0641\u0627\u0631\u0633\u06cc)")
        self.teach_fa.setFixedHeight(36)
        t_lay.addWidget(self.teach_fa)

        self.teach_pos = ModernGlassDropdown(["Noun", "Verb", "Adjective", "Adverb", "Idiom / Expression"], "Idiom / Expression", self)
        t_lay.addWidget(self.teach_pos)

        self.teach_def = QLineEdit()
        self.teach_def.setObjectName("DrawerInput")
        self.teach_def.setPlaceholderText("English Definition (Optional)")
        self.teach_def.setFixedHeight(36)
        t_lay.addWidget(self.teach_def)

        self.teach_ex_en = QLineEdit()
        self.teach_ex_en.setObjectName("DrawerInput")
        self.teach_ex_en.setPlaceholderText("Example Sentence in English (Optional)")
        self.teach_ex_en.setFixedHeight(36)
        t_lay.addWidget(self.teach_ex_en)

        self.teach_ex_fa = QLineEdit()
        self.teach_ex_fa.setObjectName("DrawerInput")
        self.teach_ex_fa.setPlaceholderText("Example Persian Translation (Optional)")
        self.teach_ex_fa.setFixedHeight(36)
        t_lay.addWidget(self.teach_ex_fa)

        save_teach_btn = QPushButton("Save to Ocean Memory")
        save_teach_btn.setObjectName("PrimaryPillButton")
        save_teach_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        save_teach_btn.setFixedHeight(38)
        save_teach_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_teach_btn.clicked.connect(self._save_taught_entry)
        t_lay.addWidget(save_teach_btn)

        mem_box = SmoothGlassFrame(radius=16, variant="inner")
        mb_lay = QVBoxLayout(mem_box)
        mb_lay.setContentsMargins(8, 8, 8, 8)
        self.teach_list = QListWidget()
        self.teach_list.setObjectName("PopupList")
        self.teach_list.setFrameShape(QFrame.Shape.NoFrame)
        self.teach_list.setWordWrap(True)
        self.teach_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.teach_list.itemClicked.connect(self._on_taught_item_clicked)
        mb_lay.addWidget(self.teach_list)
        t_lay.addWidget(mem_box, stretch=1)

        del_teach_btn = QPushButton("Remove Selected from Memory")
        del_teach_btn.setObjectName("GlassPillButton")
        del_teach_btn.setFixedHeight(34)
        del_teach_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        del_teach_btn.clicked.connect(self._delete_taught_entry)
        t_lay.addWidget(del_teach_btn)

        self.stack.addWidget(page_teach)

    def show_page(self, index: int) -> None:
        titles = {0: "Ocean Menu", 1: "History", 2: "Settings", 3: "Teach Ocean"}
        self.drawer_title.setText(titles.get(index, "Ocean Menu"))
        self.back_menu_btn.setVisible(index != 0)
        if index == 1:
            self.populate_history()
        elif index == 2:
            self.theme_drop.setCurrentText(self.main_win.settings.data.get("theme", "Dark Mode"))
        elif index == 3:
            if self.main_win.current_term and not self.teach_term.text().strip():
                self.teach_term.setText(self.main_win.current_term)
            self.populate_taught_list()
        self.stack.setCurrentIndex(index)
        self.show()

    def populate_taught_list(self) -> None:
        self.teach_list.clear()
        for k, val in lexical_engine.USER_MEMORY.get("entries", {}).items():
            hw = val.get("headword", k)
            fa = val.get("fa", "")
            it = QListWidgetItem(f"{hw}\n\u2192 {fa}")
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.teach_list.addItem(it)
        for k, fa_val in lexical_engine.USER_MEMORY.get("overrides", {}).items():
            if k not in lexical_engine.USER_MEMORY.get("entries", {}):
                it = QListWidgetItem(f"{k}\n\u2192 {fa_val}")
                it.setData(Qt.ItemDataRole.UserRole, k)
                self.teach_list.addItem(it)

    def _save_taught_entry(self) -> None:
        term = self.teach_term.text().strip()
        fa = self.teach_fa.text().strip()
        if not term or not fa:
            QMessageBox.information(self, "Teach Ocean", "Please enter both the English term and its Persian meaning.")
            return
        lexical_engine.record_taught_entry(
            term=term,
            fa_equiv=fa,
            pos=self.teach_pos.currentText(),
            eng_def=self.teach_def.text().strip(),
            ex_en=self.teach_ex_en.text().strip(),
            ex_fa=self.teach_ex_fa.text().strip(),
        )
        self.populate_taught_list()
        self.teach_term.clear()
        self.teach_fa.clear()
        self.teach_def.clear()
        self.teach_ex_en.clear()
        self.teach_ex_fa.clear()
        self.main_win._analyze_input(force_refresh=True, override_term=term)

    def _on_taught_item_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.teach_term.setText(key)
            ent = lexical_engine.USER_MEMORY.get("entries", {}).get(key, {})
            ov = lexical_engine.USER_MEMORY.get("overrides", {}).get(key, "")
            self.teach_fa.setText(ent.get("fa", ov))
            self.teach_def.setText(ent.get("def", ""))
            self.teach_ex_en.setText(ent.get("example_en", ""))
            self.teach_ex_fa.setText(ent.get("example_fa", ""))

    def _delete_taught_entry(self) -> None:
        item = self.teach_list.currentItem()
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                lexical_engine.delete_taught_entry(key)
                if key in lexical_engine.USER_MEMORY.get("overrides", {}):
                    del lexical_engine.USER_MEMORY["overrides"][key]
                    lexical_engine.save_user_memory()
                self.populate_taught_list()

    def populate_history(self) -> None:
        self.hist_list.clear()
        fav_only = "Saved" in self.filter_drop.currentText()
        for item in self.main_win.db.list_entries(favorites_only=fav_only):
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
        self.main_win.refresh_review_badges()
        fav_only = "Saved" in self.filter_drop.currentText()
        if fav_only and not new_state:
            scroll_pos = self.hist_list.verticalScrollBar().value()
            self.populate_history()
            QTimer.singleShot(5, lambda: self.hist_list.verticalScrollBar().setValue(scroll_pos))
        elif row_widget is not None:
            row_widget.set_favorite_visual(new_state)

        if self.main_win.current_term and self.main_win.current_term.strip().lower() == key.lower():
            updated = self.main_win.db.get_entry(key)
            if updated:
                self.main_win.current_data = updated
                self.main_win._render_result(self.main_win.current_term, updated)

    def _on_row_term_clicked(self, key: str) -> None:
        self.main_win._analyze_input(force_refresh=False, override_term=key)

    def _delete_hist_item(self) -> None:
        item = self.hist_list.currentItem()
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                self.main_win.db.delete_entry(key)
                self.main_win.refresh_review_badges()
                self.populate_history()

    def _export_anki(self) -> None:
        fav_only = "Saved" in self.filter_drop.currentText()
        db = self.main_win.db
        payloads = [p for p in (db.get_entry(e["query_key"]) for e in db.list_entries(favorites_only=fav_only)) if p]
        if not payloads:
            QMessageBox.information(self, "Ocean Notice", "Nothing to export yet.")
            return
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(self, "Export to Anki", os.path.join(docs, "Ocean-anki.txt"), "Text files (*.txt)")
        if not path:
            return
        text, count = anki_export.build_anki_tsv(payloads)
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as exc:
            log.exception("Anki export to %s failed", path)
            QMessageBox.warning(self, "Ocean Notice", f"Could not write the export file:\n{exc}")
            return
        QMessageBox.information(
            self, "Ocean",
            f"Exported {count} notes to:\n{path}\n\n"
            f"In Anki choose File \u2192 Import and pick this file. Notes go into the "
            f"\u201c{anki_export.DECK_NAME}\u201d deck; importing again later updates them.",
        )

    def _collect_settings(self) -> None:
        scale_map = {
            "Small (12px)": 12,
            "Medium (14px)": 14,
            "Large (16px)": 16,
            "Extra Large (18px)": 18,
        }
        sel = self.font_drop.currentText()
        self.main_win.settings.data["theme"] = self.theme_drop.currentText()
        self.main_win.settings.data["font_scale"] = sel
        self.main_win.settings.data["base_font_px"] = scale_map.get(sel, 14)
        self.main_win.settings.data["learning_mode"] = self.mode_drop.currentText()
        self.main_win.settings.data["voice_id"] = self.voice_drop.currentText()
        sp_txt = self.speed_drop.currentText().split()[0]
        try:
            self.main_win.settings.data["speech_rate"] = int(sp_txt)
        except Exception:
            self.main_win.settings.data["speech_rate"] = 0

    def _test_voice(self) -> None:
        self._collect_settings()
        self.main_win.speaker.speak("Hello, welcome to Ocean.")

    def _apply_live(self) -> None:
        self._collect_settings()
        self.main_win.settings.save()
        self.main_win._apply_theme()
        if self.main_win.current_term and self.main_win.current_data:
            self.main_win._render_result(self.main_win.current_term, self.main_win.current_data)

    def _save_and_close(self) -> None:
        self._apply_live()
        self.hide()


# === SECTION 8: MAIN APPLICATION WINDOW ===
class ReviewPage(QWidget):
    """Flashcard review of favourited entries, scheduled by spaced repetition (see review.py)."""

    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self.store = review.ReviewStore(DB_FILE)
        self.queue: List[review.ReviewCard] = []
        self.done_count = 0
        self.revealed = False
        self.setObjectName("ReviewPage")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 42)
        outer.addStretch(1)

        self.card = SmoothGlassFrame(radius=22, variant="card")
        self.card.setMinimumWidth(560)
        self.card.setMaximumWidth(760)
        vbox = QVBoxLayout(self.card)
        vbox.setContentsMargins(32, 24, 32, 26)
        vbox.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Review")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("MutedText")
        header.addWidget(self.progress_label)
        end_btn = QPushButton("End")
        end_btn.setObjectName("GlassPillButton")
        end_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        end_btn.setFixedHeight(34)
        end_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        end_btn.setToolTip("End review (Esc)")
        end_btn.clicked.connect(self.main_win.go_home)
        header.addWidget(end_btn)
        vbox.addLayout(header)

        self.front_row = QHBoxLayout()
        self.front_row.setSpacing(12)
        self.term_label = QLabel("")
        self.term_label.setObjectName("HeadwordTitle")
        self.term_label.setWordWrap(True)
        self.front_row.addWidget(self.term_label, stretch=1)
        self.audio_btn: Optional[QPushButton] = None
        vbox.addLayout(self.front_row)

        self.ipa_label = QLabel("")
        self.ipa_label.setObjectName("IpaText")
        vbox.addWidget(self.ipa_label)

        self.answer_box = QWidget()
        ans = QVBoxLayout(self.answer_box)
        ans.setContentsMargins(0, 6, 0, 0)
        ans.setSpacing(10)
        self.fa_label = QLabel("")
        self.fa_label.setObjectName("PersianText")
        self.fa_label.setWordWrap(True)
        self.fa_label.setTextFormat(Qt.TextFormat.PlainText)
        self.fa_label.setAlignment(PERSIAN_ALIGNMENT)
        ans.addWidget(self.fa_label)
        self.def_label = QLabel("")
        self.def_label.setObjectName("BodyText")
        self.def_label.setWordWrap(True)
        self.def_label.setTextFormat(Qt.TextFormat.RichText)
        ans.addWidget(self.def_label)
        vbox.addWidget(self.answer_box)

        self.show_btn = QPushButton("Show answer   (Space)")
        self.show_btn.setObjectName("PrimaryPillButton")
        self.show_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show_btn.setFixedHeight(40)
        self.show_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.show_btn.clicked.connect(self.reveal)
        vbox.addWidget(self.show_btn)

        self.grade_row = QWidget()
        g_lay = QHBoxLayout(self.grade_row)
        g_lay.setContentsMargins(0, 0, 0, 0)
        g_lay.setSpacing(10)
        self.grade_btns: Dict[review.Grade, QPushButton] = {}
        for grade in review.Grade:
            btn = QPushButton()
            btn.setObjectName("PrimaryPillButton" if grade == review.Grade.GOOD else "GlassPillButton")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(40)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, g=grade: self.grade(g))
            g_lay.addWidget(btn, stretch=1)
            self.grade_btns[grade] = btn
        vbox.addWidget(self.grade_row)

        self.done_label = QLabel("")
        self.done_label.setObjectName("BodyText")
        self.done_label.setWordWrap(True)
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.done_label)

        outer.addWidget(self.card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        bindings = [("Space", self.reveal), ("Return", self.reveal), ("Esc", self.main_win.go_home)]
        bindings += [(str(int(g)), lambda g=g: self.grade(g)) for g in review.Grade]
        for keys, slot in bindings:
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def start(self) -> None:
        try:
            self.queue = self.store.due_cards(datetime.now())
        except Exception:
            log.exception("Could not load review cards")
            self.queue = []
        self.done_count = 0
        self._show_current()

    def _show_current(self) -> None:
        self.revealed = False
        self.answer_box.hide()
        self.grade_row.hide()
        if self.audio_btn is not None:
            self.audio_btn.deleteLater()
            self.audio_btn = None

        if not self.queue:
            self._show_done()
            return

        card = self.queue[0]
        self.progress_label.setText(f"{self.done_count} done · {len(self.queue)} left")
        self.term_label.setText(card.payload.get("headword") or card.display_term)
        ipa = card.payload.get("ipa_us", "")
        self.ipa_label.setText(ipa)
        self.ipa_label.setVisible(bool(ipa) and not card.payload.get("is_sentence"))
        self.show_btn.show()
        self.done_label.hide()

        if card.payload.get("is_reverse"):
            # Persian on the front (no English voice for it), English on the back.
            self.fa_label.hide()
            translation = card.payload.get("english_translation", "")
            words = ", ".join(t for t in (c.get("term", "") for c in card.payload.get("candidates", [])[:6])
                              if t and t.lower() != translation.lower())
            answer = [f"<b>{html.escape(translation)}</b>"]
            if words:
                answer.append(html.escape(words))
            self.def_label.setText("<br>".join(answer))
            self.def_label.show()
            return
        self.fa_label.show()
        self.audio_btn = self.main_win._create_audio_button(self.term_label.text())
        self.front_row.addWidget(self.audio_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self.fa_label.setText(card.payload.get("persian_equivalent", ""))
        # The Persian meaning is the answer: larger than the theme's body size.
        self.fa_label.setStyleSheet(f"font-size: {int(self.main_win.settings.data.get('base_font_px', 14)) + 8}px;")
        parts = []
        for sense in card.payload.get("senses", [])[:2]:
            pos = html.escape(sense.get("pos", ""))
            line = f"<b>{pos}</b> — {html.escape(sense.get('def', ''))}" if pos else html.escape(sense.get("def", ""))
            if sense.get("example_en"):
                line += f"<br><i>{html.escape(sense['example_en'])}</i>"
            parts.append(line)
        self.def_label.setText("<br><br>".join(parts))
        self.def_label.setVisible(bool(parts))

    def _show_done(self) -> None:
        self.progress_label.setText(f"{self.done_count} done" if self.done_count else "")
        self.term_label.setText("All caught up" if self.done_count else "Nothing to review")
        self.ipa_label.hide()
        self.show_btn.hide()
        try:
            next_due = self.store.next_due()
        except Exception:
            log.exception("Could not read next review time")
            next_due = None
        if next_due is not None:
            wait = review.format_delay(max(next_due - datetime.now(), timedelta(minutes=1)))
            msg = f"Next review in {wait}."
        else:
            msg = "Save words with ☆ Save on a result card to add them to your review deck."
        self.done_label.setText(msg)
        self.done_label.show()

    def reveal(self) -> None:
        if not self.queue or self.revealed:
            return
        self.revealed = True
        self.answer_box.show()
        self.show_btn.hide()
        state, now = self.queue[0].state, datetime.now()
        names = {review.Grade.AGAIN: "Again", review.Grade.HARD: "Hard", review.Grade.GOOD: "Good", review.Grade.EASY: "Easy"}
        for grade, btn in self.grade_btns.items():
            delay = review.format_delay(review.schedule(state, grade, now).due - now)
            btn.setText(f"{names[grade]} · {delay}")
            btn.setToolTip(f"{names[grade]} (key {int(grade)})")
        self.grade_row.show()

    def grade(self, grade: review.Grade) -> None:
        if not self.queue or not self.revealed:
            return
        card, now = self.queue[0], datetime.now()
        new_state = review.schedule(card.state, grade, now)
        try:
            self.store.save_state(card.query_key, new_state, now)
        except Exception:
            log.exception("Could not save review of %r", card.query_key)
            QMessageBox.warning(self, "Ocean Notice", "Could not save this review. Please try again.")
            return
        self.queue.pop(0)
        if grade == review.Grade.AGAIN:
            card.state = new_state
            self.queue.append(card)  # relearn: show it again before the session ends
        else:
            self.done_count += 1
        self.main_win.refresh_review_badges()
        self._show_current()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ocean")
        self.setWindowIcon(VectorIcons.ocean_app_icon())
        self.resize(1220, 850)

        self.settings = SettingsManager()
        self.db = storage.HistoryStore(DB_FILE)
        self.speaker = NeuralSpeaker(self.settings)
        self.current_term: Optional[str] = None
        self.current_data: Optional[Dict[str, Any]] = None
        self.worker: Optional[EngineWorker] = None
        self.sug_worker: Optional[SuggestionWorker] = None

        self.nav_history: List[str] = []
        self.nav_index: int = -1
        self._navigating_history: bool = False

        self.sug_popup = GlassSuggestionPopup(self)
        self.sug_popup.item_selected.connect(self._on_suggestion_picked)

        self._build_ui()
        self._apply_theme()
        self.refresh_review_badges()
        # Cards fall due while the app stays open (e.g. 10 minutes after "Again").
        self.review_badge_timer = QTimer(self)
        self.review_badge_timer.setInterval(60_000)
        self.review_badge_timer.timeout.connect(self.refresh_review_badges)
        self.review_badge_timer.start()

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
            options.append("Auto-Fix Translation")

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
            elif act == "Auto-Fix Translation" and persian_owner:
                persian_owner.trigger_auto_fix()
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
                    plain = BeautifulSoup(widget.text(), "lxml").get_text(" ", strip=True)
                    QApplication.clipboard().setText(plain)
            elif act == "Clear" and isinstance(widget, QLineEdit):
                widget.clear()

        menu.option_picked.connect(_handle_action)
        menu.list_widget.clear()
        for opt in options:
            it = QListWidgetItem(f"   {opt}")
            it.setData(Qt.ItemDataRole.UserRole, opt)
            menu.list_widget.addItem(it)

        w = max(240, min(380, max(len(o) for o in options) * 9 + 40))
        h = len(options) * 40 + 20
        menu.setFixedSize(w, h)
        menu.move(global_pos)
        menu.show()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
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

        self.stack = QStackedWidget()
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
        self.input_box.setPlaceholderText("Search English or Persian: a word, idiom, collocation, or sentence...")
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

        self.home_review_btn = QPushButton("")
        self.home_review_btn.setObjectName("GlassPillButton")
        self.home_review_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_review_btn.setFixedHeight(36)
        self.home_review_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.home_review_btn.setToolTip("Review your saved words (spaced repetition)")
        self.home_review_btn.clicked.connect(self.open_review)
        self.home_vbox.addWidget(self.home_review_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.home_vbox.addStretch(1)

        self.stack.addWidget(self.home_page)

        # VIEW 1: Results Page
        self.results_page = QWidget()
        self.results_page.setObjectName('ResultsPage')
        res_vbox = QVBoxLayout(self.results_page)
        res_vbox.setContentsMargins(0, 0, 0, 0)
        res_vbox.setSpacing(14)

        self.top_search_slot = QHBoxLayout()
        self.top_search_slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        res_vbox.addLayout(self.top_search_slot)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.cards_container = QWidget()
        self.cards_container.setObjectName('CardsContainer')
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(4, 6, 12, 24)
        self.cards_layout.setSpacing(16)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        res_vbox.addWidget(self.scroll_area, stretch=1)

        self.stack.addWidget(self.results_page)

        # VIEW 2: Spaced-repetition review
        self.review_page = ReviewPage(self)
        self.stack.addWidget(self.review_page)

        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(150)
        self.debounce_timer.timeout.connect(self._trigger_live_suggestions)
        self.input_box.textEdited.connect(lambda _: self.debounce_timer.start())

        ctrl_enter = QShortcut(QKeySequence("Ctrl+Return"), self)
        ctrl_enter.activated.connect(lambda: self._analyze_input(force_refresh=False))

    def _update_search_button_mode(self) -> None:
        is_dark = self.settings.data.get("theme", "Dark Mode") == "Dark Mode"
        fg_col = "#0E1621" if is_dark else "#FFFFFF"
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

    def _go_back(self) -> None:
        if self.nav_index > 0:
            self.nav_index -= 1
            self._navigating_history = True
            self._analyze_input(force_refresh=False, override_term=self.nav_history[self.nav_index])
            self._navigating_history = False
            self._update_nav_buttons()

    def _go_forward(self) -> None:
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_index += 1
            self._navigating_history = True
            self._analyze_input(force_refresh=False, override_term=self.nav_history[self.nav_index])
            self._navigating_history = False
            self._update_nav_buttons()

    def go_home(self) -> None:
        self.sug_popup.hide()
        self.input_box.clear()
        self.status_label.setText("")
        self.current_term = None
        self.current_data = None
        self.search_bar_wrapper.setParent(self.home_page)
        self.home_vbox.insertWidget(2, self.search_bar_wrapper, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.stack.setCurrentIndex(0)
        self._update_search_button_mode()
        self.refresh_review_badges()
        self.input_box.setFocus()

    def handle_external_lookup(self, message: Dict[str, str]) -> None:
        """A hand-off from `--lookup`, a second launch, or the Windows hotkey: show the window and look up."""
        token = message.get("token", "")
        if token:
            # Lets Wayland compositors grant focus to this already-running window (xdg-activation).
            os.environ["XDG_ACTIVATION_TOKEN"] = token
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
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
            self.status_label.setText("Nothing selected: copy a word, then press the shortcut")

    def open_review(self) -> None:
        self.sug_popup.hide()
        self.right_drawer.hide()
        self.status_label.setText("")
        self.review_page.start()
        self.stack.setCurrentIndex(2)
        self.review_page.setFocus()

    def refresh_review_badges(self) -> None:
        try:
            due = self.review_page.store.due_count(datetime.now())
        except Exception:
            log.exception("Could not count due reviews")
            due = 0
        self.home_review_btn.setText(f"Review · {due} due")
        self.home_review_btn.setVisible(due > 0)
        self.right_drawer.review_menu_btn.setText(f"Review ({due} due)" if due else "Review")

    def _switch_to_results_view(self) -> None:
        if self.stack.currentIndex() != 1:
            self.search_bar_wrapper.setParent(self.results_page)
            self.top_search_slot.addWidget(self.search_bar_wrapper)
            self.stack.setCurrentIndex(1)
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

    def _clear_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _analyze_input(self, force_refresh: bool = False, override_term: Optional[str] = None, deep_mode: bool = False) -> None:
        self.sug_popup.hide()
        if override_term:
            self.input_box.setText(override_term)
        term = self.input_box.text().strip()
        if not term:
            return

        if not self._navigating_history:
            if self.nav_index == -1 or (self.nav_history and self.nav_history[self.nav_index].lower() != term.lower()):
                self.nav_history = self.nav_history[: self.nav_index + 1]
                self.nav_history.append(term)
                self.nav_index = len(self.nav_history) - 1
            self._update_nav_buttons()

        self._switch_to_results_view()

        if not force_refresh and not deep_mode:
            cached = self.db.get_entry(term)
            if cached and (cached.get("senses") or cached.get("is_sentence")):
                self.worker = None  # any lookup still running is now stale
                self.explore_btn.setEnabled(True)
                self.current_term = term
                self.current_data = cached
                self._render_result(term, cached)
                self.status_label.setText("")
                return

        self.explore_btn.setEnabled(False)
        self.status_label.setText(f"Deep searching '{term}'..." if deep_mode else f"Analyzing '{term}'...")

        self.worker = EngineWorker(term, deep_mode=deep_mode)
        self.worker.finished_success.connect(self._on_inference_success)
        self.worker.finished_error.connect(self._on_inference_error)
        start_worker(self.worker)

    def _on_inference_success(self, term: str, data: Dict[str, Any]) -> None:
        if self.sender() is not self.worker:
            return  # a newer search or history navigation superseded this lookup
        self.worker = None
        self.explore_btn.setEnabled(True)
        self.input_box.setText(data.get("headword", term))
        self.db.save_entry(term, data)
        saved = self.db.get_entry(term) or data
        self.current_term = term
        self.current_data = saved
        if self.right_drawer.isVisible() and self.right_drawer.stack.currentIndex() == 1:
            self.right_drawer.populate_history()
        self._render_result(term, saved)
        self.status_label.setText("")

    def _on_inference_error(self, error_msg: str) -> None:
        if self.sender() is not self.worker:
            return
        self.worker = None
        self.explore_btn.setEnabled(True)
        self.status_label.setText("")
        QMessageBox.warning(self, "Ocean Notice", error_msg)

    def _create_audio_button(self, text_to_speak: str) -> QPushButton:
        is_dark = self.settings.data.get("theme", "Dark Mode") == "Dark Mode"
        icon_col = "#FFFFFF" if is_dark else "#0B57D0"
        btn = QPushButton()
        btn.setIcon(VectorIcons.speaker_icon(icon_col))
        btn.setIconSize(QSize(18, 18))
        btn.setObjectName("AudioCircleButton")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedSize(34, 34)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setToolTip(f"Listen: {text_to_speak}")
        btn.clicked.connect(lambda _, t=text_to_speak: self.speaker.speak(t))
        return btn

    def _create_clickable_word_link(self, word_text: str, prefix_label: str = "") -> QPushButton:
        display = f"{prefix_label}{word_text}" if prefix_label else word_text
        btn = QPushButton(display)
        btn.setObjectName("ClickableWordPill")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(30)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setToolTip(f"Click to explore '{word_text}'")
        btn.clicked.connect(lambda _, w=word_text: self._analyze_input(False, w))
        return btn

    def _create_persian_widget(self, text: str, en_source: str = "", large: bool = False) -> PersianTranslationWidget:
        px = int(self.settings.data.get("base_font_px", 14))
        mode = self.settings.data.get("learning_mode", "Click to Reveal Persian")
        return PersianTranslationWidget(text, px, mode, en_source=en_source, main_win=self, large=large)

    def _highlight_phrase(self, sentence: str, phrase: str) -> str:
        accent = THEME_COLORS["accent"]
        clean_p = phrase.strip()
        if not clean_p:
            return sentence
        pat_full = re.compile(rf"({re.escape(clean_p)}\w*)", re.IGNORECASE)
        if pat_full.search(sentence):
            return pat_full.sub(rf"<b style='color:{accent};'>\1</b>", sentence)
        out = sentence
        for w in clean_p.split():
            if w.lower() not in lexical_engine.STOPWORDS and len(w) >= 3:
                pat_w = re.compile(rf"(\b{re.escape(w[:4])}\w*\b)", re.IGNORECASE)
                out = pat_w.sub(rf"<b style='color:{accent};'>\1</b>", out)
        return out

    def _create_glass_card(self, title: str) -> tuple[SmoothGlassFrame, QVBoxLayout, QHBoxLayout]:
        card = SmoothGlassFrame(radius=22, variant="card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(14)

        header = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setObjectName("CardTitle")
        title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(title_lbl)
        header.addStretch()

        vbox.addLayout(header)
        return card, vbox, header

    def _build_entry_with_example_box(self, item: Dict[str, str], radius: int = 18, variant: str = "inner") -> SmoothGlassFrame:
        term = item.get("term", "")
        ipa = item.get("ipa", "") or lexical_engine.to_ipa_us(term)
        fa = item.get("fa", "")
        ex_en = item.get("example_en", "")
        ex_ipa = item.get("example_ipa", "") or (lexical_engine.sentence_to_ipa(ex_en) if ex_en else "")
        ex_fa = item.get("example_fa", "")

        box = SmoothGlassFrame(radius=radius, variant=variant)
        b_lay = QVBoxLayout(box)
        b_lay.setContentsMargins(18, 12, 18, 12)
        b_lay.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(self._create_audio_button(term))
        top_row.addWidget(self._create_clickable_word_link(term))

        if ipa:
            ipa_lbl = QLabel(ipa)
            ipa_lbl.setObjectName("IpaText")
            ipa_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            top_row.addWidget(ipa_lbl)

        top_row.addWidget(self._create_persian_widget(fa, en_source=term, large=False), stretch=1)
        b_lay.addLayout(top_row)

        if ex_en:
            ex_row = QHBoxLayout()
            ex_row.setSpacing(10)
            ex_row.addWidget(self._create_audio_button(ex_en))

            ex_col = QVBoxLayout()
            ex_col.setSpacing(3)

            ex_lbl = QLabel(self._highlight_phrase(ex_en, term))
            ex_lbl.setWordWrap(True)
            ex_lbl.setTextFormat(Qt.TextFormat.RichText)
            ex_lbl.setObjectName("BodyText")
            ex_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            ex_col.addWidget(ex_lbl)

            if ex_ipa:
                s_ipa_lbl = QLabel(ex_ipa)
                s_ipa_lbl.setWordWrap(True)
                s_ipa_lbl.setObjectName("IpaText")
                s_ipa_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                ex_col.addWidget(s_ipa_lbl)

            ex_row.addLayout(ex_col, stretch=1)
            b_lay.addLayout(ex_row)

            b_lay.addWidget(self._create_persian_widget(ex_fa, en_source=ex_en, large=False))

        return box

    # === SECTION 9: STRUCTURED RESULT RENDERER (SUPPORTS BOTH WORD/IDIOM & FULL-SENTENCE STACKED LAYOUT) ===
    def _render_reverse_result(self, query_term: str, data: Dict[str, Any]) -> None:
        """Persian input: the English translation and clickable English candidates."""
        headword = data.get("headword", query_term)
        translation = data.get("english_translation", "")
        is_sentence = bool(data.get("is_sentence"))
        px = int(self.settings.data.get("base_font_px", 14))

        card, vbox, header = self._create_glass_card("1. Persian \u2192 English")
        ref_btn = QPushButton("Refresh")
        ref_btn.setObjectName("GlassPillButton")
        ref_btn.setFixedHeight(34)
        ref_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ref_btn.clicked.connect(lambda: self._analyze_input(True))
        header.addWidget(ref_btn)

        hw_box = SmoothGlassFrame(radius=18, variant="inner")
        hw_vbox = QVBoxLayout(hw_box)
        hw_vbox.setContentsMargins(20, 16, 20, 16)
        hw_vbox.setSpacing(10)
        fa_lbl = QLabel(headword)
        fa_lbl.setObjectName("PersianText")
        fa_lbl.setStyleSheet(f"font-size: {px + (8 if is_sentence else 14)}px; font-weight: 700;")
        fa_lbl.setTextFormat(Qt.TextFormat.PlainText)
        fa_lbl.setAlignment(PERSIAN_ALIGNMENT)
        fa_lbl.setWordWrap(True)
        fa_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hw_vbox.addWidget(fa_lbl)

        if translation:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(self._create_audio_button(translation), 0, Qt.AlignmentFlag.AlignTop)
            en_lbl = QLabel(translation)
            en_lbl.setObjectName("SentenceTitle")
            en_lbl.setWordWrap(True)
            en_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(en_lbl, 1)
            if is_sentence:
                explore = QPushButton("Analyze in English")
                explore.setObjectName("GlassPillButton")
                explore.setFixedHeight(34)
                explore.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                explore.clicked.connect(lambda _, t=translation: self._analyze_input(False, t))
                row.addWidget(explore, 0, Qt.AlignmentFlag.AlignTop)
            hw_vbox.addLayout(row)
        else:
            none_lbl = QLabel("No English translation found (no internet and no local match).")
            none_lbl.setObjectName("MutedText")
            hw_vbox.addWidget(none_lbl)
        if data.get("machine_translation"):
            mt_lbl = QLabel(f"Machine translation: {data['machine_translation']}")
            mt_lbl.setObjectName("MutedText")
            mt_lbl.setWordWrap(True)
            hw_vbox.addWidget(mt_lbl)
        if data.get("translation_offline"):
            off_lbl = QLabel("Offline translation (lower quality)")
            off_lbl.setObjectName("MutedText")
            hw_vbox.addWidget(off_lbl)
        vbox.addWidget(hw_box)

        groups: Dict[str, List[str]] = {}
        seen = set()
        sources = [("your history", t) for t in self.db.english_entries_with_persian(headword)]
        sources += [(c.get("pos") or "other", c.get("term", "")) for c in data.get("candidates", [])]
        for group, term in sources:
            if term and term.lower() not in seen:
                seen.add(term.lower())
                groups.setdefault(group, []).append(term)
        if groups:
            cand_box = SmoothGlassFrame(radius=18, variant="inner")
            cand_vbox = QVBoxLayout(cand_box)
            cand_vbox.setContentsMargins(20, 14, 20, 14)
            cand_vbox.setSpacing(10)
            hint = QLabel("English words \u2014 click one to open its full entry")
            hint.setObjectName("MutedText")
            cand_vbox.addWidget(hint)
            for group, terms in groups.items():
                row = QHBoxLayout()
                row.setSpacing(8)
                g_lbl = QLabel(group)
                g_lbl.setObjectName("BodyText")
                g_lbl.setMinimumWidth(170)
                row.addWidget(g_lbl)
                for term in terms:
                    row.addWidget(self._create_clickable_word_link(term))
                row.addStretch()
                cand_vbox.addLayout(row)
            vbox.addWidget(cand_box)

        self.cards_layout.addWidget(card)
        self.cards_layout.addStretch()

    def _render_result(self, query_term: str, data: Dict[str, Any]) -> None:
        self._clear_cards()
        if data.get("is_reverse"):
            self._render_reverse_result(query_term, data)
            return

        headword = data.get("headword", query_term)
        is_sentence = bool(data.get("is_sentence", False)) or len(headword.split()) >= 5
        is_idiom = bool(data.get("is_idiom", False))
        did_you_mean = data.get("did_you_mean", "")
        ipa_us = data.get("ipa_us", "/-/")
        is_fav = bool(data.get("_is_favorite", False))
        fa_equiv = data.get("persian_equivalent", "")
        senses: List[Dict[str, str]] = data.get("senses", [])
        sent_breakdown: List[Dict[str, str]] = data.get("sentence_breakdown", [])
        accent = THEME_COLORS["accent"]

        if did_you_mean:
            dym_btn = QPushButton(f"Did you mean '{did_you_mean}'?")
            dym_btn.setObjectName("PrimaryPillButton")
            dym_btn.setFixedHeight(36)
            dym_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            dym_btn.clicked.connect(lambda _, w=did_you_mean: self._analyze_input(False, w))
            self.cards_layout.addWidget(dym_btn)

        # --- SECTION 1: Overview & Definitions (No Emoji on Deep Search!) ---
        card1_title = "1. Sentence Analysis & Translation" if is_sentence else "1. Overview & Definitions"
        card1, vbox1, header1 = self._create_glass_card(card1_title)

        deep_btn = QPushButton("Deep Search")
        deep_btn.setObjectName("GlassPillButton")
        deep_btn.setFixedHeight(34)
        deep_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        deep_btn.setToolTip("Run multi-source deep search for this entry")
        deep_btn.clicked.connect(lambda: self._analyze_input(force_refresh=True, deep_mode=True))
        header1.addWidget(deep_btn)


        ref_btn = QPushButton("Refresh")
        ref_btn.setObjectName("GlassPillButton")
        ref_btn.setFixedHeight(34)
        ref_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ref_btn.clicked.connect(lambda: self._analyze_input(True))
        header1.addWidget(ref_btn)

        fav_btn = QPushButton("\u2605 Saved" if is_fav else "\u2606 Save")
        fav_btn.setObjectName("GlassPillButton")
        fav_btn.setFixedHeight(34)
        fav_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def _toggle_fav() -> None:
            new_state = self.db.toggle_favorite(query_term)
            fav_btn.setText("\u2605 Saved" if new_state else "\u2606 Save")
            self.refresh_review_badges()
            if self.right_drawer.isVisible() and self.right_drawer.stack.currentIndex() == 1:
                self.right_drawer.populate_history()

        fav_btn.clicked.connect(_toggle_fav)
        header1.addWidget(fav_btn)

        hw_box = SmoothGlassFrame(radius=18, variant="inner")

        if is_sentence:
            # Vertical 3-Line Full-Width Stack for Sentences (Matches Image 1 & 2 Request!)
            hw_vbox = QVBoxLayout(hw_box)
            hw_vbox.setContentsMargins(20, 16, 20, 16)
            hw_vbox.setSpacing(10)

            line1_row = QHBoxLayout()
            line1_row.setSpacing(12)
            line1_row.addWidget(self._create_audio_button(headword), 0, Qt.AlignmentFlag.AlignTop)

            sent_lbl = QLabel(headword)
            sent_lbl.setWordWrap(True)
            sent_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            sent_lbl.setObjectName("SentenceTitle")
            sent_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            line1_row.addWidget(sent_lbl, 1)
            hw_vbox.addLayout(line1_row)

            ipa_lbl = QLabel(f"US  {ipa_us}")
            ipa_lbl.setWordWrap(True)
            ipa_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            ipa_lbl.setObjectName("IpaText")
            ipa_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            hw_vbox.addWidget(ipa_lbl)

            hw_vbox.addWidget(self._create_persian_widget(fa_equiv, en_source=headword, large=True))
        else:
            # Standard Single Word / Short Idiom Header Row
            hw_lay = QHBoxLayout(hw_box)
            hw_lay.setContentsMargins(18, 12, 18, 12)
            hw_lay.setSpacing(14)

            hw_lay.addWidget(self._create_audio_button(headword))

            hw_lbl = QLabel(headword)
            hw_lbl.setObjectName("HeadwordTitle")
            hw_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            hw_lay.addWidget(hw_lbl)

            ipa_lbl = QLabel(f"US  {ipa_us}")
            ipa_lbl.setObjectName("IpaText")
            ipa_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            hw_lay.addWidget(ipa_lbl)

            hw_lay.addWidget(self._create_persian_widget(fa_equiv, en_source=headword, large=True), stretch=1)

        vbox1.addWidget(hw_box)

        for idx, s_item in enumerate(senses, start=1):
            s_box = SmoothGlassFrame(radius=18, variant="inner")
            s_vbox = QVBoxLayout(s_box)
            s_vbox.setContentsMargins(18, 14, 18, 14)
            s_vbox.setSpacing(8)

            pos_tag = s_item.get("pos", "Entry")
            def_txt = s_item.get("def", "")
            ex_en = s_item.get("example_en", "")
            ex_ipa = s_item.get("example_ipa", "")
            ex_fa = s_item.get("example_fa", "")

            def_lbl = QLabel(
                f"<b>{idx}.</b> <span style='color:{accent}; font-weight:700;'>[{pos_tag}]</span>  {def_txt}"
            )
            def_lbl.setWordWrap(True)
            def_lbl.setTextFormat(Qt.TextFormat.RichText)
            def_lbl.setObjectName("BodyText")
            def_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            s_vbox.addWidget(def_lbl)

            if ex_en:
                ex_row = QHBoxLayout()
                ex_row.setSpacing(10)
                ex_row.addWidget(self._create_audio_button(ex_en))

                ex_col = QVBoxLayout()
                ex_col.setSpacing(3)

                en_sent_lbl = QLabel(self._highlight_phrase(ex_en, headword))
                en_sent_lbl.setWordWrap(True)
                en_sent_lbl.setTextFormat(Qt.TextFormat.RichText)
                en_sent_lbl.setObjectName("BodyText")
                en_sent_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                ex_col.addWidget(en_sent_lbl)

                if ex_ipa:
                    ipa_sent_lbl = QLabel(ex_ipa)
                    ipa_sent_lbl.setWordWrap(True)
                    ipa_sent_lbl.setObjectName("IpaText")
                    ipa_sent_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    ex_col.addWidget(ipa_sent_lbl)

                ex_row.addLayout(ex_col, stretch=1)
                s_vbox.addLayout(ex_row)

                s_vbox.addWidget(self._create_persian_widget(ex_fa, en_source=ex_en, large=False))

            vbox1.addWidget(s_box)

        self.cards_layout.addWidget(card1)

        # --- SENTENCE KEY TERMS BREAKDOWN (Matches Gemini's Image 7 Breakdown!) ---
        if is_sentence and sent_breakdown:
            card_sb, vbox_sb, _ = self._create_glass_card("2. Key Terms & Expressions in Sentence")
            for kt in sent_breakdown:
                kt_box = SmoothGlassFrame(radius=18, variant="inner")
                kt_lay = QVBoxLayout(kt_box)
                kt_lay.setContentsMargins(18, 12, 18, 12)
                kt_lay.setSpacing(6)

                top_r = QHBoxLayout()
                top_r.setSpacing(10)
                top_r.addWidget(self._create_audio_button(kt["term"]))
                top_r.addWidget(self._create_clickable_word_link(kt["term"]))

                ipa_l = QLabel(kt.get("ipa", ""))
                ipa_l.setObjectName("IpaText")
                ipa_l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                top_r.addWidget(ipa_l)

                top_r.addWidget(self._create_persian_widget(kt.get("fa", ""), en_source=kt["term"], large=False), stretch=1)
                kt_lay.addLayout(top_r)

                def_l = QLabel(f"<span style='color:{accent}; font-weight:700;'>[{kt.get('pos', 'Term')}]</span>  {kt.get('def', '')}")
                def_l.setWordWrap(True)
                def_l.setTextFormat(Qt.TextFormat.RichText)
                def_l.setObjectName("BodyText")
                def_l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                kt_lay.addWidget(def_l)

                vbox_sb.addWidget(kt_box)

            self.cards_layout.addWidget(card_sb)

        # --- SECTION 2: Collocations & Usage ---
        colls = data.get("collocations", [])
        if not is_idiom and not is_sentence and colls:
            card2, vbox2, _ = self._create_glass_card("2. Collocations & Usage")
            for c_item in colls:
                vbox2.addWidget(self._build_entry_with_example_box(c_item, radius=18, variant="inner"))
            self.cards_layout.addWidget(card2)

        # --- SECTION 3: Word Breakdown, Morphology & Lexical Relations ---
        morph = data.get("morphology", {})
        syns = data.get("synonyms", [])
        ants = data.get("antonyms", [])

        if (not is_idiom and not is_sentence) or syns or ants:
            sec_num = "3" if (not is_idiom and not is_sentence and colls) else "2"
            card3, vbox3, _ = self._create_glass_card(f"{sec_num}. Word Breakdown, Morphology & Lexical Relations")

            if not is_idiom and not is_sentence:
                morph_box = SmoothGlassFrame(radius=18, variant="inner")
                m_lay = QVBoxLayout(morph_box)
                m_lay.setContentsMargins(18, 14, 18, 14)
                m_lay.setSpacing(10)

                root_w = morph.get("root", headword)
                structure_txt = morph.get("structure", headword)
                pref_txt = morph.get("prefix", "None")
                suff_txt = morph.get("suffix", "None")

                r_row = QHBoxLayout()
                r_row_lbl = QLabel("<b>Root / Base Word:</b>")
                r_row_lbl.setObjectName("BodyText")
                r_row.addWidget(r_row_lbl)
                r_row.addWidget(self._create_clickable_word_link(root_w))
                r_row.addSpacing(18)

                st_lbl = QLabel(f"<b>Structure:</b>  <span style='color:{accent};'>{structure_txt}</span>")
                st_lbl.setTextFormat(Qt.TextFormat.RichText)
                st_lbl.setObjectName("BodyText")
                st_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                r_row.addWidget(st_lbl)
                r_row.addStretch()
                m_lay.addLayout(r_row)

                affix_lbl = QLabel(
                    f"<b>Prefix:</b> <span style='color:{accent};'>{pref_txt}</span>      \u2022      "
                    f"<b>Suffix:</b> <span style='color:{accent};'>{suff_txt}</span>"
                )
                affix_lbl.setWordWrap(True)
                affix_lbl.setTextFormat(Qt.TextFormat.RichText)
                affix_lbl.setObjectName("BodyText")
                affix_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                m_lay.addWidget(affix_lbl)

                family_list = morph.get("family", [])
                if family_list:
                    fam_row = QHBoxLayout()
                    fam_row.setSpacing(8)
                    f_title = QLabel("<b>Word Family:</b>")
                    f_title.setObjectName("BodyText")
                    fam_row.addWidget(f_title)
                    for f_item in family_list[:6]:
                        fam_row.addWidget(self._create_clickable_word_link(f_item["word"], f"{f_item['pos']}: "))
                    fam_row.addStretch()
                    m_lay.addLayout(fam_row)

                vbox3.addWidget(morph_box)

            if syns:
                syn_sub = SmoothGlassFrame(radius=18, variant="inner")
                syn_lay = QVBoxLayout(syn_sub)
                syn_lay.setContentsMargins(16, 14, 16, 14)
                syn_lay.setSpacing(10)
                st = QLabel("Synonyms")
                st.setObjectName("CardTitle")
                syn_lay.addWidget(st)
                for s_it in syns:
                    syn_lay.addWidget(self._build_entry_with_example_box(s_it, radius=14, variant="card"))
                vbox3.addWidget(syn_sub)

            if ants:
                ant_sub = SmoothGlassFrame(radius=18, variant="inner")
                ant_lay = QVBoxLayout(ant_sub)
                ant_lay.setContentsMargins(16, 14, 16, 14)
                ant_lay.setSpacing(10)
                at = QLabel("Antonyms")
                at.setObjectName("CardTitle")
                ant_lay.addWidget(at)
                for a_it in ants:
                    ant_lay.addWidget(self._build_entry_with_example_box(a_it, radius=14, variant="card"))
                vbox3.addWidget(ant_sub)

            self.cards_layout.addWidget(card3)

        self.cards_layout.addStretch()
        QTimer.singleShot(10, lambda: self.scroll_area.verticalScrollBar().setValue(0))

    # === SECTION 10: OPEN-SOURCE PRIMER / MATERIAL-3 DAYLIGHT & NIGHT THEME ENGINE ===
    def _sync_windows_titlebar(self, is_dark: bool, bg_hex: str) -> None:
        """Synchronizes the native Windows 10/11 titlebar with Ocean's Light or Dark theme."""
        try:
            import ctypes
            hwnd = int(self.winId())
            dark_val = ctypes.c_int(1 if is_dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val))
            col = QColor(bg_hex)
            colorref = ctypes.c_int((col.blue() << 16) | (col.green() << 8) | col.red())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))
        except Exception:
            pass

    def _apply_theme(self) -> None:
        px = int(self.settings.data.get("base_font_px", 14))
        is_dark = self.settings.data.get("theme", "Dark Mode") == "Dark Mode"

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
        self._sync_windows_titlebar(is_dark, c["bg_main"])

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

        icon_fg = c["text_main"]
        self.menu_btn.setIcon(VectorIcons.hamburger_icon(icon_fg))
        self.menu_btn.setIconSize(QSize(22, 22))
        self.back_btn.setIcon(VectorIcons.arrow_icon("left", icon_fg))
        self.back_btn.setIconSize(QSize(18, 18))
        self.fwd_btn.setIcon(VectorIcons.arrow_icon("right", icon_fg))
        self.fwd_btn.setIconSize(QSize(18, 18))
        self.right_drawer.back_menu_btn.setIcon(VectorIcons.arrow_icon("right", icon_fg))
        self.right_drawer.back_menu_btn.setIconSize(QSize(18, 18))
        self.right_drawer.test_voice_btn.setIcon(VectorIcons.speaker_icon(c["accent"]))
        self.right_drawer.test_voice_btn.setIconSize(QSize(19, 19))
        self._update_search_button_mode()

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
        self.setStyleSheet(qss)
        self.sug_popup.setStyleSheet(qss)
        self.right_drawer.update()
        self.update()


def generate_ico_file(ico_path: str = "ocean.ico") -> None:
    import struct
    app = QApplication.instance() or QApplication(sys.argv)
    register_custom_fonts()
    icon = VectorIcons.ocean_app_icon()
    pixmap = icon.pixmap(256, 256)
    ba = QBuffer()
    ba.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(ba, "PNG")
    png_bytes = bytes(ba.data())
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
    with open(ico_path, "wb") as f:
        f.write(header + entry + png_bytes)


def main() -> None:
    if "--make-icon" in sys.argv:
        generate_ico_file("ocean.ico")
        return
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    message = {"action": "show", "token": os.environ.get("XDG_ACTIVATION_TOKEN", "")}
    if "--lookup" in sys.argv:
        words = sys.argv[sys.argv.index("--lookup") + 1:]
        message.update(action="lookup", text=" ".join(words) if words else desktop_lookup.read_selected_text())
    if desktop_lookup.send_to_running_instance(message):
        return  # the running Ocean takes over; never open a second window on the same data

    app.setStyle("Fusion")
    register_custom_fonts()
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, lexical_engine.preload_offline_translator)  # once the event loop is running

    instance_server = desktop_lookup.InstanceServer(app)
    instance_server.message_received.connect(window.handle_external_lookup)
    instance_server.listen()
    if sys.platform == "win32":
        hotkey = desktop_lookup.WindowsHotkey(lambda: window.handle_external_lookup({"action": "lookup"}))
        if hotkey.register():
            app.installNativeEventFilter(hotkey)
    if message["action"] == "lookup":
        window.handle_external_lookup(message)
    exit_app(app.exec())


if __name__ == "__main__":
    main()
