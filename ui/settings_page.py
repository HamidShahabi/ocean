"""Drawer page with the theme, font size, learning mode, voice, refresh-period and dictionary-source settings."""
from typing import Dict, TYPE_CHECKING

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from dictionary_sources import REGISTRY

from storage import DEFAULT_REFRESH_PERIOD, REFRESH_PERIOD_DAYS
from ui.config import SPEED_OPTIONS, VOICE_OPTIONS
from ui.theme import VectorIcons
from ui.widgets import ModernGlassDropdown

if TYPE_CHECKING:
    from ui.drawer import AttachedRightDrawer
    from ui.main_window import MainWindow


class SettingsPage(QWidget):
    def __init__(self, main_win: "MainWindow", drawer: "AttachedRightDrawer") -> None:
        super().__init__()
        self.main_win = main_win
        self.drawer = drawer

        s_lay = QVBoxLayout(self)
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

        cur_refresh = self.main_win.settings.data.get("refresh_period", DEFAULT_REFRESH_PERIOD)
        if cur_refresh not in REFRESH_PERIOD_DAYS:
            cur_refresh = DEFAULT_REFRESH_PERIOD
        self.refresh_drop = ModernGlassDropdown(list(REFRESH_PERIOD_DAYS), cur_refresh, self)
        form.addRow("Refresh Saved Entries:", self.refresh_drop)

        s_lay.addLayout(form)

        self.test_voice_btn = QPushButton("  Test Voice")
        self.test_voice_btn.setObjectName("GlassPillButton")
        self.test_voice_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.test_voice_btn.setFixedHeight(38)
        self.test_voice_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.test_voice_btn.clicked.connect(self._test_voice)
        s_lay.addWidget(self.test_voice_btn)

        self._build_sources_group(s_lay)

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


    def _build_sources_group(self, parent_layout: QVBoxLayout) -> None:
        """The "Dictionary sources" group: one checkbox per registered source, bundled ones included
        (switching off a local source is as legitimate as switching off a slow site)."""
        title = QLabel("Dictionary sources")
        title.setObjectName("CardTitle")
        parent_layout.addWidget(title)
        hint = QLabel("Turn off a source that is broken, slow or unwanted. It is not contacted or read "
                      "for any lookup. Click Apply or Save to use the change.")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        parent_layout.addWidget(hint)
        self.source_checks: Dict[str, QCheckBox] = {}
        for source in REGISTRY.all():
            text = source.label if source.needs_network else f"{source.label} (bundled)"
            box = QCheckBox(text)
            box.setChecked(REGISTRY.is_enabled(source.name))
            self.source_checks[source.name] = box
            parent_layout.addWidget(box)

    def on_show(self) -> None:
        self.theme_drop.setCurrentText(self.main_win.settings.data.get("theme", "Dark Mode"))
        for name, box in self.source_checks.items():  # discard unsaved ticks from an earlier visit
            box.setChecked(REGISTRY.is_enabled(name))

    def apply_icons(self, accent: str) -> None:
        self.test_voice_btn.setIcon(VectorIcons.speaker_icon(accent))
        self.test_voice_btn.setIconSize(QSize(19, 19))

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
        self.main_win.settings.data["refresh_period"] = self.refresh_drop.currentText()
        sp_txt = self.speed_drop.currentText().split()[0]
        try:
            self.main_win.settings.data["speech_rate"] = int(sp_txt)
        except Exception:
            self.main_win.settings.data["speech_rate"] = 0

    def _collect_sources(self) -> None:
        """Store the ticked sources in the settings and switch the registry to match."""
        self.main_win.settings.data["disabled_sources"] = [n for n, box in self.source_checks.items() if not box.isChecked()]
        self.main_win.settings.apply_sources()

    def _test_voice(self) -> None:
        self._collect_settings()
        self.main_win.speaker.speak("Hello, welcome to Ocean.")

    def _apply_live(self) -> None:
        self._collect_settings()
        self._collect_sources()
        self.main_win.settings.save()
        self.main_win._apply_theme()
        if self.main_win.current_term and self.main_win.current_data:
            self.main_win.results_page.render(self.main_win.current_term, self.main_win.current_data)

    def _save_and_close(self) -> None:
        self._apply_live()
        self.drawer.hide()
