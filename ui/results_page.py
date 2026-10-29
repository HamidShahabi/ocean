"""Results page: renders a lookup payload as cards (overview, collocations, morphology, ...)."""
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import dictionary_sources
import lexical_engine
from ui.highlight import ExampleHighlighter
from ui.theme import PERSIAN_ALIGNMENT, THEME_COLORS
from ui.widgets import PersianTranslationWidget, SmoothGlassFrame, create_audio_button

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ResultsPage(QWidget):
    """Renders a lookup payload (word, idiom, sentence or Persian input) as a stack of cards."""

    favorite_toggled = pyqtSignal(str, bool)  # (term, now saved): the Reader re-marks its text on this

    def __init__(self, main_win: "MainWindow") -> None:
        super().__init__()
        self.main_win = main_win
        self.example_highlighter = ExampleHighlighter(main_win.speaker)
        self.setObjectName('ResultsPage')
        res_vbox = QVBoxLayout(self)
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

    def _clear_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _create_audio_button(self, text_to_speak: str) -> QPushButton:
        return create_audio_button(text_to_speak, self.main_win.speaker, self.main_win.settings)

    def _create_clickable_word_link(self, word_text: str, prefix_label: str = "") -> QPushButton:
        display = f"{prefix_label}{word_text}" if prefix_label else word_text
        btn = QPushButton(display)
        btn.setObjectName("ClickableWordPill")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(30)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setToolTip(f"Click to explore '{word_text}'")
        btn.clicked.connect(lambda _, w=word_text: self.main_win._analyze_input(False, w))
        return btn

    def _create_persian_widget(self, text: str, en_source: str = "", large: bool = False) -> PersianTranslationWidget:
        px = int(self.main_win.settings.data.get("base_font_px", 14))
        mode = self.main_win.settings.data.get("learning_mode", "Click to Reveal Persian")
        return PersianTranslationWidget(text, px, mode, en_source=en_source, main_win=self.main_win, large=large)

    def _create_level_badges(self, data: Dict[str, Any]) -> Optional[QHBoxLayout]:
        """CEFR level and frequency band of an English word, whichever of the two is known."""
        level = data.get("cefr_level", "")
        band = data.get("frequency_band", "")
        per_million = data.get("frequency_per_million")
        if not (level or band):
            return None
        row = QHBoxLayout()
        row.setSpacing(8)
        if level:
            badge = QLabel(level)
            badge.setObjectName("LevelBadge")
            badge.setToolTip(f"CEFR level {level} (CEFR-J Wordlist / Octanove vocabulary profile)")
            row.addWidget(badge)
        if band:
            badge = QLabel(band)
            badge.setObjectName("FrequencyBadge")
            badge.setToolTip(f"About {per_million:g} occurrences per million words (Datamuse)" if per_million is not None else band)
            row.addWidget(badge)
        row.addStretch(1)
        return row

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
            ex_lbl = QLabel(self._highlight_phrase(ex_en, term))
            ex_lbl.setWordWrap(True)
            ex_lbl.setTextFormat(Qt.TextFormat.RichText)
            ex_lbl.setObjectName("BodyText")
            ex_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

            ex_row = QHBoxLayout()
            ex_row.setSpacing(10)
            # Reading the example aloud highlights each word in ex_lbl as it is spoken.
            ex_row.addWidget(create_audio_button(
                ex_en, self.main_win.speaker, self.main_win.settings,
                on_click=lambda lbl=ex_lbl, t=ex_en: self.example_highlighter.speak(lbl, t)))

            ex_col = QVBoxLayout()
            ex_col.setSpacing(3)
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
        px = int(self.main_win.settings.data.get("base_font_px", 14))

        card, vbox, header = self._create_glass_card("1. Persian \u2192 English")
        ref_btn = QPushButton("Refresh")
        ref_btn.setObjectName("GlassPillButton")
        ref_btn.setFixedHeight(34)
        ref_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ref_btn.clicked.connect(lambda: self.main_win._analyze_input(True))
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
                explore.clicked.connect(lambda _, t=translation: self.main_win._analyze_input(False, t))
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
        sources = [("your history", t) for t in self.main_win.db.english_entries_with_persian(headword)]
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

    def _add_all_sources_off_notice(self) -> None:
        """Explain the empty definitions when every dictionary source is switched off in Settings
        (a separate case from offline-only mode, which leaves the bundled sources on)."""
        card, vbox, _ = self._create_glass_card("All dictionary sources are turned off")
        msg = QLabel("No dictionary source was used for this word, so there are no definitions, synonyms or examples. "
                     "Turn some back on under Menu > Settings > Dictionary sources.")
        msg.setObjectName("BodyText")
        msg.setWordWrap(True)
        vbox.addWidget(msg)
        self.cards_layout.addWidget(card)

    def render(self, query_term: str, data: Dict[str, Any]) -> None:
        self._clear_cards()
        if lexical_engine.is_offline_only():
            badge = QLabel("Offline-only mode \u2014 results use only your history, Ocean's bundled lexicon and the offline translator")
            badge.setObjectName("OfflineBadge")
            badge.setWordWrap(True)
            self.cards_layout.addWidget(badge)
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
            dym_btn.clicked.connect(lambda _, w=did_you_mean: self.main_win._analyze_input(False, w))
            self.cards_layout.addWidget(dym_btn)

        if not is_sentence and not senses and not dictionary_sources.REGISTRY.any_enabled():
            self._add_all_sources_off_notice()

        # --- SECTION 1: Overview & Definitions (No Emoji on Deep Search!) ---
        card1_title = "1. Sentence Analysis & Translation" if is_sentence else "1. Overview & Definitions"
        card1, vbox1, header1 = self._create_glass_card(card1_title)

        deep_btn = QPushButton("Deep Search")
        deep_btn.setObjectName("GlassPillButton")
        deep_btn.setFixedHeight(34)
        deep_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        deep_btn.setToolTip("Run multi-source deep search for this entry")
        deep_btn.clicked.connect(lambda: self.main_win._analyze_input(force_refresh=True, deep_mode=True))
        header1.addWidget(deep_btn)


        ref_btn = QPushButton("Refresh")
        ref_btn.setObjectName("GlassPillButton")
        ref_btn.setFixedHeight(34)
        ref_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ref_btn.clicked.connect(lambda: self.main_win._analyze_input(True))
        header1.addWidget(ref_btn)

        fav_btn = QPushButton("\u2605 Saved" if is_fav else "\u2606 Save")
        fav_btn.setObjectName("GlassPillButton")
        fav_btn.setFixedHeight(34)
        fav_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def _toggle_fav() -> None:
            new_state = self.main_win.db.toggle_favorite(query_term)
            fav_btn.setText("\u2605 Saved" if new_state else "\u2606 Save")
            self.main_win.refresh_review_badges()
            self.main_win.right_drawer.refresh_history()
            self.favorite_toggled.emit(query_term, new_state)

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
        badge_row = self._create_level_badges(data)
        if badge_row is not None:
            vbox1.addLayout(badge_row)

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
                en_sent_lbl = QLabel(self._highlight_phrase(ex_en, headword))
                en_sent_lbl.setWordWrap(True)
                en_sent_lbl.setTextFormat(Qt.TextFormat.RichText)
                en_sent_lbl.setObjectName("BodyText")
                en_sent_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

                ex_row = QHBoxLayout()
                ex_row.setSpacing(10)
                ex_row.addWidget(create_audio_button(
                    ex_en, self.main_win.speaker, self.main_win.settings,
                    on_click=lambda lbl=en_sent_lbl, t=ex_en: self.example_highlighter.speak(lbl, t)))

                ex_col = QVBoxLayout()
                ex_col.setSpacing(3)
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
