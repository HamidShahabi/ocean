"""Results page: renders a lookup payload as cards (overview, collocations, morphology, ...)."""
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from PyQt6.QtCore import QBuffer, QIODevice, Qt, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import lexical_engine
from ui.flow_layout import FlowLayout
from ui.highlight import ExampleHighlighter
from ui.reports_page import ReportPopover
from ui.theme import PERSIAN_ALIGNMENT, THEME_COLORS
from ui.widgets import PersianTranslationWidget, SmoothGlassFrame, create_audio_button

if TYPE_CHECKING:
    from ui.main_window import MainWindow

# Morphology fields that mean "nothing found" (the engine writes these instead of leaving the field empty).
_NO_AFFIX = {"", "none", "none (base word)", "inflected form"}
SNAPSHOT_MAX_WIDTH = 1600  # reports store the page as a PNG no wider than this


def _term_of(item: Any, *keys: str) -> str:
    """The word of a list item that is either a plain string or a dict (under one of `keys`)."""
    if isinstance(item, dict):
        return next((str(item[k]).strip() for k in keys if item.get(k)), "")
    return str(item).strip() if item else ""


def affix_parts(text: Any) -> tuple:
    """("un-", "not") from "un-  (not)" and ("-ness", "Noun: state") from "-ness  [Noun: state]";
    ("", "") when the engine found no affix."""
    raw = str(text or "").strip()
    if raw.casefold() in _NO_AFFIX:
        return "", ""
    head, _, rest = raw.partition(" ")
    return head, rest.strip().strip("[]()").strip()


def is_composite_structure(structure: Any) -> bool:
    """A structure like "un- (Prefix) + happy (Root) + -ness (Suffix) -> unhappiness"; a lone word, or
    "word (Base Root Word)", says nothing the Root row does not."""
    return "+" in str(structure or "")


class ResultsPage(QWidget):
    """Renders a lookup payload (word, idiom, sentence or Persian input) as a stack of cards."""

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

        self._shown: Optional[tuple] = None  # (query term, payload) on screen: what "!" reports
        self._report_btn: Optional[QPushButton] = None
        self._popover: Optional[ReportPopover] = None

    def _clear_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()  # until deleted it would still be painted (and show up in a snapshot)
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

    def _add_relation_boxes(self, layout: QVBoxLayout, items: List[Dict[str, str]]) -> None:
        """Synonym or antonym boxes, under a heading for each sense they belong to ("noun: sloping land ...")."""
        last_sense = None
        for it in items:
            sense = it.get("sense", "")
            if sense and sense != last_sense:
                sense_lbl = QLabel(sense)
                sense_lbl.setObjectName("MutedText")
                sense_lbl.setWordWrap(True)
                sense_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(sense_lbl)
            last_sense = sense
            layout.addWidget(self._build_entry_with_example_box(it, radius=14, variant="card"))

    # --- subsections of "Forms, Roots & Alternatives" ---
    @staticmethod
    def _subsection(title: str) -> tuple[SmoothGlassFrame, QVBoxLayout]:
        box = SmoothGlassFrame(radius=18, variant="inner")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("SubsectionTitle")
        lay.addWidget(title_lbl)
        return box, lay

    def _pill_flow(self, pills: List[QPushButton]) -> QWidget:
        """Word pills that wrap onto new lines instead of running off the page."""
        holder = QWidget()
        holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        flow = FlowLayout(holder, h_spacing=8, v_spacing=8)
        for pill in pills:
            flow.addWidget(pill)
        return holder

    def _build_root_box(self, headword: str, morph: Dict[str, Any]) -> Optional[SmoothGlassFrame]:
        """Root / base word, how the word is built, and its prefix and suffix; parts the engine did not find are left out."""
        accent = THEME_COLORS["accent"]
        root = _term_of(morph.get("root"))
        structure = str(morph.get("structure") or "")
        prefix, prefix_meaning = affix_parts(morph.get("prefix"))
        suffix, suffix_meaning = affix_parts(morph.get("suffix"))
        is_base = not root or root.lower() == headword.lower()

        rows: List[tuple] = []  # (label, widget)
        if not is_base or prefix or suffix:
            rows.append(("Root", self._create_clickable_word_link(root or headword)))
        else:
            note = QLabel("This is the base form: no prefix or suffix.")
            note.setObjectName("MutedText")
            rows.append(("Base word", note))
        if is_composite_structure(structure):
            st_lbl = QLabel(f"<span style='color:{accent}; font-weight:600;'>{structure}</span>")
            st_lbl.setTextFormat(Qt.TextFormat.RichText)
            st_lbl.setObjectName("BodyText")
            st_lbl.setWordWrap(True)
            st_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rows.append(("Structure", st_lbl))
        for label, affix, meaning in (("Prefix", prefix, prefix_meaning), ("Suffix", suffix, suffix_meaning)):
            if affix:
                text = f"<b style='color:{accent};'>{affix}</b>" + (f"  <span>{meaning}</span>" if meaning else "")
                lbl = QLabel(text)
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setObjectName("BodyText")
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                rows.append((label, lbl))
        if not rows:
            return None

        box, lay = self._subsection("Root & Structure")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnMinimumWidth(0, 90)
        grid.setColumnStretch(1, 1)
        for r, (label, widget) in enumerate(rows):
            name = QLabel(label)
            name.setObjectName("RowLabel")
            grid.addWidget(name, r, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            if isinstance(widget, QPushButton):  # a pill keeps its own width
                grid.addWidget(widget, r, 1, Qt.AlignmentFlag.AlignLeft)
            else:
                grid.addWidget(widget, r, 1)
        lay.addLayout(grid)
        return box

    def _build_family_box(self, headword: str, morph: Dict[str, Any]) -> Optional[SmoothGlassFrame]:
        """Related forms ("Noun: privilege", "Adjective: privileged"); hidden when it only repeats the headword."""
        family = [(_term_of(f, "word", "term"), str(f.get("pos", "")) if isinstance(f, dict) else "")
                  for f in morph.get("family") or []]
        family = [(w, pos) for w, pos in family if w]
        if not any(w.lower() != headword.lower() for w, _ in family) and len(family) <= 1:
            return None
        box, lay = self._subsection("Word Family")
        lay.addWidget(self._pill_flow([
            self._create_clickable_word_link(w, f"{pos}: " if pos else "") for w, pos in family[:8]]))
        return box

    def _build_expressions_box(self, data: Dict[str, Any], morph: Dict[str, Any]) -> Optional[SmoothGlassFrame]:
        """Common expressions built on the word. The engine's list is read from whichever of the known keys
        holds it (items are strings or dicts), so a rename on that side does not blank the section."""
        raw = next((v for v in (data.get("common_expressions"), data.get("derived_terms"),
                                morph.get("common_expressions")) if v), [])
        terms = list(dict.fromkeys(t for t in (_term_of(x, "term", "expression", "text", "word") for x in raw) if t))
        if not terms:
            return None
        box, lay = self._subsection("Common Expressions")
        lay.addWidget(self._pill_flow([self._create_clickable_word_link(t) for t in terms[:12]]))
        return box

    def _build_relation_box(self, title: str, items: List[Dict[str, str]]) -> SmoothGlassFrame:
        box, lay = self._subsection(title)
        self._add_relation_boxes(lay, items)
        return box

    # --- header buttons: Refresh, Save and the "!" report capsule ---
    def _add_header_actions(self, header: QHBoxLayout, query_term: str, data: Dict[str, Any], with_save: bool = False) -> None:
        ref_btn = QPushButton("Refresh")
        ref_btn.setObjectName("GlassPillButton")
        ref_btn.setFixedHeight(34)
        ref_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ref_btn.setToolTip("Look this up again with online resources")
        ref_btn.clicked.connect(lambda: self.main_win.refresh_current())
        header.addWidget(ref_btn)

        if with_save:
            is_fav = bool(data.get("_is_favorite", False))
            fav_btn = QPushButton("\u2605 Saved" if is_fav else "\u2606 Save")
            fav_btn.setObjectName("GlassPillButton")
            fav_btn.setFixedHeight(34)
            fav_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

            def _toggle_fav() -> None:
                db = self.main_win.db
                if db.get_entry(query_term) is None:  # deleted from the history while on screen: save it again
                    db.save_entry(query_term, data)
                new_state = db.toggle_favorite(query_term)
                fav_btn.setText("\u2605 Saved" if new_state else "\u2606 Save")
                self.main_win.right_drawer.refresh_history()

            fav_btn.clicked.connect(_toggle_fav)
            header.addWidget(fav_btn)

        report_btn = QPushButton("!")
        report_btn.setObjectName("ReportBangButton")
        report_btn.setFixedSize(40, 34)
        report_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        report_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        report_btn.clicked.connect(lambda: self._on_report_clicked(query_term, data))
        header.addWidget(report_btn)
        self._shown = (query_term, data)
        self._report_btn = report_btn
        self.sync_report_button()

    def sync_report_button(self) -> None:
        """Show "!" as filled once the entry on screen has a report (and plain again when it has none)."""
        btn = self._report_btn
        if btn is None or self._shown is None:
            return
        reported = self.main_win.reports.latest_for(self._shown[0]) is not None
        btn.setProperty("reported", reported)
        btn.setToolTip("Reported. Click to add a note or remove the report" if reported
                       else "Report a problem with this result")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def snapshot_png(self) -> bytes:
        """The whole result page (not just the part scrolled into view) as PNG bytes."""
        pix = self.cards_container.grab()
        if pix.width() > SNAPSHOT_MAX_WIDTH:
            pix = pix.scaledToWidth(SNAPSHOT_MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        return bytes(buf.data())

    def _on_report_clicked(self, query_term: str, data: Dict[str, Any]) -> None:
        reports = self.main_win.reports
        existing = reports.latest_for(query_term)
        if existing is None:
            report_id = self.main_win.report_result(query_term, data, self.snapshot_png())
            note = ""
        else:
            report_id, note = existing["id"], existing["note"]
        self.sync_report_button()
        btn = self._report_btn
        if btn is None:
            return
        self._popover = ReportPopover(self.main_win, report_id, note, existing is not None, self.sync_report_button)
        self._popover.show_below(btn)

    # === SECTION 9: STRUCTURED RESULT RENDERER (SUPPORTS BOTH WORD/IDIOM & FULL-SENTENCE STACKED LAYOUT) ===
    def _render_reverse_result(self, query_term: str, data: Dict[str, Any]) -> None:
        """Persian input: the English translation and clickable English candidates."""
        headword = data.get("headword", query_term)
        translation = data.get("english_translation", "")
        is_sentence = bool(data.get("is_sentence"))
        px = int(self.main_win.settings.data.get("base_font_px", 14))

        card, vbox, header = self._create_glass_card("1. Persian \u2192 English")
        self._add_header_actions(header, query_term, data)

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

    def render(self, query_term: str, data: Dict[str, Any]) -> None:
        self._clear_cards()
        self._shown = None
        self._report_btn = None
        if data.get("is_reverse"):
            self._render_reverse_result(query_term, data)
            return

        headword = data.get("headword", query_term)
        is_sentence = bool(data.get("is_sentence", False)) or len(headword.split()) >= 5
        is_idiom = bool(data.get("is_idiom", False))
        did_you_mean = data.get("did_you_mean", "")
        ipa_us = data.get("ipa_us", "/-/")
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

        # --- SECTION 1: Overview & Definitions ---
        card1_title = "1. Sentence Analysis & Translation" if is_sentence else "1. Overview & Definitions"
        card1, vbox1, header1 = self._create_glass_card(card1_title)

        self._add_header_actions(header1, query_term, data, with_save=True)

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

        # --- SECTION 3: Forms, Roots & Alternatives ---
        morph = data.get("morphology") or {}
        syns = data.get("synonyms", [])
        ants = data.get("antonyms", [])

        subsections: List[QWidget] = []
        if not is_idiom and not is_sentence:
            subsections += [self._build_root_box(headword, morph), self._build_family_box(headword, morph),
                            self._build_expressions_box(data, morph)]
        if syns:
            subsections.append(self._build_relation_box("Synonyms", syns))
        if ants:
            subsections.append(self._build_relation_box("Antonyms", ants))
        subsections = [sub for sub in subsections if sub is not None]
        if subsections:
            sec_num = "3" if (not is_idiom and not is_sentence and colls) else "2"
            card3, vbox3, _ = self._create_glass_card(f"{sec_num}. Forms, Roots & Alternatives")
            for sub in subsections:
                vbox3.addWidget(sub)
            self.cards_layout.addWidget(card3)

        self.cards_layout.addStretch()
        QTimer.singleShot(10, lambda: self.scroll_area.verticalScrollBar().setValue(0))
