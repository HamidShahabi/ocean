"""Paths, option lists and the settings file shared by every part of the UI (no Qt imports)."""
import copy
import json
import logging
import os
import sys
from typing import Any, Dict

import lexical_engine
from storage import DEFAULT_REFRESH_PERIOD

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
READER_FILE = lexical_engine.user_data_path("reader_text.json")  # the Reader's last text and position

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


READER_LEVEL_OPTIONS = ["A1", "A2", "B1", "B2", "C1", "C2", "Off"]


class SettingsManager:
    DEFAULTS = {
        "font_scale": "Medium (14px)",
        "base_font_px": 14,
        "learning_mode": "Click to Reveal Persian",
        "speech_rate": 0,
        "voice_id": "am_michael (Male - US)",
        "theme": "Dark Mode",
        "refresh_period": DEFAULT_REFRESH_PERIOD,
        # Names (DictionarySource.name) of the dictionary sources switched off in Settings. Storing the
        # off ones keeps every source, including any added later, on by default.
        "disabled_sources": [],
        "offline_only": False,
        # Reader page: words of this CEFR level or higher are marked ("Off" = no level marking).
        "reader_min_level": "B2",
    }

    def __init__(self, path: str = SETTINGS_FILE) -> None:
        self.path = path
        self.data: Dict[str, Any] = copy.deepcopy(self.DEFAULTS)
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
                off = self.data.get("disabled_sources")
                self.data["disabled_sources"] = [n for n in off if isinstance(n, str)] if isinstance(off, list) else []
                if self.data.get("reader_min_level") not in READER_LEVEL_OPTIONS:
                    self.data["reader_min_level"] = self.DEFAULTS["reader_min_level"]
            except Exception:
                log.exception("Could not read settings %s; using defaults", self.path)

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            log.exception("Could not save settings %s", self.path)

    def apply_sources(self, registry: Any = None) -> None:
        """Switch the registry's dictionary sources on or off to match the saved choice (names the registry
        no longer knows are ignored, sources the settings never heard of stay on)."""
        if registry is None:
            from dictionary_sources import REGISTRY as registry
        off = set(self.data.get("disabled_sources", []))
        for name in registry.names():
            registry.set_enabled(name, name not in off)
