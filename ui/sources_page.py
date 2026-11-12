"""Drawer page that lists, read-only, where Ocean's information comes from: each source's name, priority and role.

The text lives in SOURCES: edit that list when a source is added, removed or reordered."""
from typing import Dict, List, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ui.widgets import SmoothGlassFrame

if TYPE_CHECKING:
    from ui.main_window import MainWindow

# In the order Ocean consults them (priority 1 first): the dictionary sources in the engine's merge order
# (dictionary_sources.REGISTRY), then the tiers of lexical_engine.translate_with_source for Persian.
# `works` says whether the source needs the internet.
SOURCES: List[Dict[str, str]] = [
    {"name": "Idioms table", "works": "Offline",
     "role": "Meanings of idioms and phrases, with natural Persian equivalents. Checked first for any phrase, "
             "so these never go through machine translation."},
    {"name": "Wiktionary (Kaikki.org)", "works": "Offline",
     "role": "Definitions, pronunciation (IPA), synonyms and antonyms, word forms, roots and common expressions; "
             "its own usage examples when the film dialogue has none that fits."},
    {"name": "Urban Dictionary (top slang)", "works": "Offline",
     "role": "Slang meanings from a bundled table of well-rated entries, shown when Wiktionary has nothing for the "
             "word or the slang is its main use."},
    {"name": "OpenSubtitles", "works": "Offline",
     "role": "Example sentences from films and series, first choice, with their Persian translations; also how "
             "often a Persian word is used in subtitles (to prefer everyday Persian)."},
    {"name": "Tatoeba", "works": "Offline",
     "role": "Example sentences with Persian translations, when nothing above has one."},
    {"name": "Translation cache", "works": "Offline",
     "role": "Every Persian translation Ocean has fetched before, kept for good: a word you looked up once is "
             "instant, and works without internet."},
    {"name": "Google Translate", "works": "Online, then cached",
     "role": "Persian for words and phrases that nothing above translated, when you are online. Each answer is "
             "saved in the cache."},
    {"name": "Local language model (optional)", "works": "Offline",
     "role": "If installed, translates idioms and sentences offline before the NLLB translator is tried."},
    {"name": "NLLB (local model)", "works": "Offline",
     "role": "Offline translation of sentences and words that nothing above covers; replaced by Google's "
             "translation the next time you are online."},
]


class SourcesPage(QWidget):
    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)

        intro = QLabel("Where Ocean's information comes from, in the order it is used.")
        intro.setObjectName("MutedText")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 6, 0)
        body_lay.setSpacing(10)
        for priority, src in enumerate(SOURCES, start=1):
            body_lay.addWidget(self._source_card(priority, src))
        body_lay.addStretch()
        scroll.setWidget(body)
        lay.addWidget(scroll, stretch=1)

    @staticmethod
    def _source_card(priority: int, src: Dict[str, str]) -> SmoothGlassFrame:
        card = SmoothGlassFrame(radius=16, variant="inner")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        num = QLabel(str(priority))
        num.setObjectName("SourcePriority")
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setFixedSize(26, 26)
        num.setToolTip(f"Priority {priority}: the lower the number, the earlier Ocean uses this source")
        head.addWidget(num)
        name = QLabel(src["name"])
        name.setObjectName("SourceName")
        name.setWordWrap(True)
        head.addWidget(name, 1)
        v.addLayout(head)

        role = QLabel(src["role"])
        role.setObjectName("BodyText")
        role.setWordWrap(True)
        v.addWidget(role)
        works = QLabel(src["works"])
        works.setObjectName("MutedText")
        v.addWidget(works)
        return card

    def on_show(self) -> None:
        pass
