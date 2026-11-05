import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QTextCursor  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

import dictionary_sources  # noqa: E402
import lexical_engine  # noqa: E402
import quiz  # noqa: E402
import reader  # noqa: E402
from ui import theme  # noqa: E402
from ui.config import DB_FILE, READER_FILE, SettingsManager  # noqa: E402
from ui.drawer import PAGE_HISTORY, PAGE_MENU, PAGE_SETTINGS, PAGE_TEACH  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui import pronounce_page  # noqa: E402
from lexicon_fixture import use_lexicon  # noqa: E402

WORD = {
    "headword": "serendipity",
    "ipa_us": "/ˌserənˈdɪpəti/",
    "persian_equivalent": "اتفاق خوشایند",
    "senses": [
        {"pos": "Noun", "def": "Finding something good by chance.", "example_en": "It was serendipity that we met.",
         "example_fa": "این یک اتفاق خوشایند بود."},
    ],
    "collocations": [{"term": "pure serendipity", "fa": "اتفاق محض", "example_en": "Pure serendipity.", "example_fa": "اتفاق محض."}],
    "morphology": {"root": "serendip", "structure": "serendip + ity", "prefix": "None", "suffix": "ity",
                   "family": [{"word": "serendipitous", "pos": "adj"}]},
    "synonyms": [{"term": "luck", "fa": "شانس"}],
    "antonyms": [{"term": "misfortune", "fa": "بدشانسی"}],
    "cefr_level": "C2",
    "frequency_per_million": 0.4,
    "frequency_band": "Rare",
}
SENTENCE = {
    "headword": "She found the book by pure chance yesterday",
    "is_sentence": True,
    "ipa_us": "/ʃi/",
    "persian_equivalent": "او دیروز کتاب را اتفاقی پیدا کرد",
    "senses": [{"pos": "Sentence", "def": "A statement."}],
    "sentence_breakdown": [{"term": "by chance", "fa": "اتفاقی", "ipa": "/baɪ tʃæns/", "pos": "Idiom", "def": "Without planning."}],
}
REVERSE = {
    "headword": "کتاب",
    "is_reverse": True,
    "english_translation": "book",
    "candidates": [{"term": "book", "pos": "noun"}, {"term": "volume", "pos": "noun"}],
}
IDIOM = {
    "headword": "break the ice",
    "is_idiom": True,
    "did_you_mean": "break the ice.",
    "persian_equivalent": "یخ را شکستن",
    "senses": [{"pos": "Idiom", "def": "Start a conversation."}],
}


class FakeWorker(QObject):
    """Stands in for EngineWorker so a lookup never touches the network."""
    preview_ready = pyqtSignal(str, dict)
    finished_success = pyqtSignal(str, dict)
    finished_error = pyqtSignal(str)

    def __init__(self, term):
        super().__init__()
        self.term = term


def card_texts(win):
    layout = win.results_page.cards_layout
    texts = []
    for i in range(layout.count()):
        card = layout.itemAt(i).widget()
        if card is not None:
            texts += [lbl.text() for lbl in card.findChildren(QLabel)]
    return texts


def offline_badges(win):
    layout = win.results_page.cards_layout
    return [w for w in (layout.itemAt(i).widget() for i in range(layout.count())) if w and w.objectName() == "OfflineBadge"]


class UiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.register_custom_fonts()

    def setUp(self):
        self.win = MainWindow()
        self.win.show()
        for payload in (WORD, SENTENCE, REVERSE, IDIOM):
            self.win.db.save_entry(payload["headword"], payload)

    def tearDown(self):
        for word in getattr(self, "quiz_words", []):
            self.win.db.delete_entry(word)
        for payload in (WORD, SENTENCE, REVERSE, IDIOM):
            self.win.db.delete_entry(payload["headword"])
        self.win.settings.data["theme"] = "Dark Mode"
        self.win.settings.data["offline_only"] = False
        lexical_engine.set_offline_only(False)
        self.win.settings.data["disabled_sources"] = []
        self.win.settings.apply_sources()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self.win = None

    def test_every_result_kind_renders(self):
        for payload in (WORD, SENTENCE, REVERSE, IDIOM):
            self.win.results_page.render(payload["headword"], payload)
            self.assertTrue(any(payload["headword"] in t for t in card_texts(self.win)), payload["headword"])

    def test_level_and_frequency_badges_show_only_what_is_known(self):
        def badges():  # walks the layout: cleared cards linger as children until the event loop deletes them
            layout = self.win.results_page.cards_layout
            cards = [layout.itemAt(i).widget() for i in range(layout.count())]
            return {lbl.objectName(): lbl for c in cards if c is not None for lbl in c.findChildren(QLabel)
                    if lbl.objectName() in ("LevelBadge", "FrequencyBadge")}

        self.win.results_page.render("serendipity", WORD)
        shown = badges()
        self.assertEqual(shown["LevelBadge"].text(), "C2")
        self.assertEqual(shown["FrequencyBadge"].text(), "Rare")
        self.assertIn("0.4 occurrences per million", shown["FrequencyBadge"].toolTip())
        self.win.results_page.render("serendipity", dict(WORD, cefr_level=""))
        self.assertEqual(list(badges()), ["FrequencyBadge"])
        self.win.results_page.render("serendipity", dict(WORD, frequency_band="", frequency_per_million=None))
        self.assertEqual(list(badges()), ["LevelBadge"])
        self.win.results_page.render("break the ice", IDIOM)
        self.assertEqual(badges(), {})

    def test_lookup_of_a_saved_entry_shows_the_results_page(self):
        self.win._analyze_input(override_term="serendipity")
        self.assertIs(self.win.stack.currentWidget(), self.win.results_page)
        self.assertEqual(self.win.current_term, "serendipity")
        self.assertIn("Finding something good by chance.", " ".join(card_texts(self.win)))
        # The search bar moves into the results page and back home.
        self.assertIs(self.win.search_bar_wrapper.parent(), self.win.results_page)
        self.win.go_home()
        self.assertIs(self.win.stack.currentWidget(), self.win.home_page)
        self.assertIs(self.win.search_bar_wrapper.parent(), self.win.home_page)

    def test_external_lookup_hand_off(self):
        self.win.handle_external_lookup({"action": "lookup", "text": "  serendipity  "})
        self.assertEqual(self.win.current_term, "serendipity")
        self.win.handle_external_lookup({"action": "lookup", "text": ""})
        self.assertIn("Nothing selected", self.win.status_label.text())

    def test_drawer_pages(self):
        drawer = self.win.right_drawer
        self.win._toggle_right_drawer()
        self.assertTrue(drawer.isVisible())
        for index, title in ((PAGE_HISTORY, "History"), (PAGE_SETTINGS, "Settings"), (PAGE_TEACH, "Teach Ocean"), (PAGE_MENU, "Ocean Menu")):
            drawer.show_page(index)
            self.assertEqual(drawer.stack.currentIndex(), index)
            self.assertEqual(drawer.drawer_title.text(), title)
        self.win._toggle_right_drawer()
        self.assertFalse(drawer.isVisible())

    def test_history_page_lists_entries_and_toggles_favourite(self):
        drawer = self.win.right_drawer
        drawer.show_page(PAGE_HISTORY)
        listed = drawer.history_page.hist_list.count()
        self.assertGreaterEqual(listed, 4)
        self.win._analyze_input(override_term="serendipity")
        row = drawer.history_page.hist_list.itemWidget(drawer.history_page.hist_list.item(0))
        key = row.query_key
        drawer.history_page._on_row_star_toggled(key, row)
        self.assertTrue(self.win.db.get_entry(key)["_is_favorite"])
        self.assertTrue(row.is_fav)
        drawer.history_page._on_row_star_toggled(key, row)
        self.assertFalse(self.win.db.get_entry(key)["_is_favorite"])

    def _count(self, term):
        return next(e["lookup_count"] for e in self.win.db.list_entries() if e["query_key"] == term)

    def test_lookups_are_counted_but_refresh_rerender_and_navigation_are_not(self):
        self.assertEqual(self._count("serendipity"), 1)
        self.win._analyze_input(override_term="serendipity")  # served from the history cache
        self.assertEqual(self._count("serendipity"), 2)
        self.win.results_page.render("serendipity", self.win.current_data)  # re-render
        self.win.right_drawer.settings_page.theme_drop.setCurrentText("Dark Mode")
        with mock.patch("ui.main_window.start_worker"):
            self.win._analyze_input(force_refresh=True)  # Refresh button
        self.assertEqual(self._count("serendipity"), 2)
        self.win._analyze_input(override_term="break the ice")
        self.win._go_back()  # back/forward re-show an entry, they are not new lookups
        self.win._go_forward()
        self.assertEqual(self._count("serendipity"), 2)
        self.assertEqual(self._count("break the ice"), 2)

    def test_history_search_sort_and_saved_filter(self):
        drawer = self.win.right_drawer
        drawer.show_page(PAGE_HISTORY)
        page = drawer.history_page

        def shown():
            return [page.hist_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(page.hist_list.count())]

        everything = shown()
        self.assertGreaterEqual(len(everything), 4)
        # Typing filters after a short pause, in English and in Persian.
        page.search_box.setText("serend")
        QTest.qWait(300)
        page.search_box.setText("اتفاق خوشایند")
        QTest.qWait(300)
        self.assertEqual(shown(), ["serendipity"])
        page.search_box.setText("كتاب")  # Arabic kaf spelling of the Persian headword
        QTest.qWait(300)
        # The Persian headword, and the sentence whose Persian meaning contains it.
        self.assertEqual(shown(), ["کتاب", "she found the book by pure chance yesterday"])
        page.search_box.setText("")
        QTest.qWait(300)
        self.assertEqual(shown(), everything)
        # Sort by lookups, then combine with the Saved filter and search.
        for _ in range(3):
            self.win.db.record_lookup("break the ice")
        page.sort_drop._on_picked("Most Looked Up")
        self.assertEqual(shown()[0], "break the ice")
        self.assertEqual(page.hist_list.itemWidget(page.hist_list.item(0)).count_lbl.text(), "×4")
        self.win.db.toggle_favorite("serendipity")
        page.filter_drop._on_picked("Saved Favorites Only")
        self.assertEqual(shown(), ["serendipity"])
        page.search_box.setText("ice")
        page.populate()
        self.assertEqual(shown(), [])
        page.search_box.setText("serend")
        page.populate()
        self.assertEqual(shown(), ["serendipity"])
        page.filter_drop._on_picked("All Recent Searches")
        page.sort_drop._on_picked("Most Recent")

    def test_anki_export_from_history(self):
        drawer = self.win.right_drawer
        drawer.show_page(PAGE_HISTORY)
        out = os.path.join(_DATA, "anki.txt")
        with mock.patch("ui.history_page.QFileDialog.getSaveFileName", return_value=(out, "")), \
                mock.patch("ui.history_page.QMessageBox.information") as info:
            drawer.history_page._export_anki()
        self.assertTrue(info.called)
        with open(out, encoding="utf-8") as f:
            self.assertIn("serendipity", f.read())

    def test_settings_apply_switches_theme_and_rerenders(self):
        self.win._analyze_input(override_term="serendipity")
        page = self.win.right_drawer.settings_page
        page.theme_drop.setCurrentText("Light Mode")
        with mock.patch.object(self.win.settings, "save"):
            page._apply_live()
        self.assertEqual(self.win.settings.data["theme"], "Light Mode")
        self.assertEqual(theme.THEME_COLORS["bg_main"], "#F6F8FA")
        self.assertIn("#F6F8FA", self.win.styleSheet())
        page.theme_drop.setCurrentText("Dark Mode")
        with mock.patch.object(self.win.settings, "save"):
            page._save_and_close()
        self.assertEqual(theme.THEME_COLORS["bg_main"], "#0E1621")
        self.assertFalse(self.win.right_drawer.isVisible())

    def test_offline_only_setting_applies_to_engine_and_marks_results(self):
        page = self.win.right_drawer.settings_page
        self.assertFalse(page.offline_check.isChecked())
        self.win.results_page.render("serendipity", WORD)
        self.assertEqual(offline_badges(self.win), [])
        page.offline_check.setChecked(True)
        with mock.patch.object(self.win.settings, "save") as save:
            page._apply_live()
        self.assertTrue(save.called)
        self.assertTrue(self.win.settings.data["offline_only"])
        self.assertTrue(lexical_engine.is_offline_only())
        for payload in (WORD, SENTENCE, REVERSE, IDIOM):  # applying re-renders; every kind shows the badge
            self.win.results_page.render(payload["headword"], payload)
            badges = offline_badges(self.win)
            self.assertEqual(len(badges), 1, payload["headword"])
            self.assertIn("Offline-only mode", badges[0].text())
        page.offline_check.setChecked(False)
        with mock.patch.object(self.win.settings, "save"):
            page._apply_live()
        self.assertFalse(lexical_engine.is_offline_only())
        self.win.results_page.render("serendipity", WORD)
        self.assertEqual(offline_badges(self.win), [])

    def test_settings_lists_every_source_and_applies_the_toggles(self):
        registry = dictionary_sources.REGISTRY
        page = self.win.right_drawer.settings_page
        self.assertEqual(list(page.source_checks), registry.names())
        self.assertTrue(all(box.isChecked() for box in page.source_checks.values()))
        for source in registry.all():
            self.assertEqual(source.label, page.source_checks[source.name].text())
        page.source_checks["urban_dictionary"].setChecked(False)
        page.source_checks["wordnet"].setChecked(False)
        with mock.patch.object(self.win.settings, "save") as save:
            page._apply_live()
        self.assertTrue(save.called)
        self.assertEqual(self.win.settings.data["disabled_sources"], ["wordnet", "urban_dictionary"])  # menu order
        self.assertEqual(registry.disabled_names(), ["urban_dictionary", "wordnet"])
        page.source_checks["urban_dictionary"].setChecked(True)
        with mock.patch.object(self.win.settings, "save"):
            page._apply_live()
        self.assertEqual(registry.disabled_names(), ["wordnet"])
        # Reopening the page drops ticks that were never applied.
        page.source_checks["corpora"].setChecked(False)
        page.on_show()
        self.assertTrue(page.source_checks["corpora"].isChecked())
        self.assertFalse(page.source_checks["wordnet"].isChecked())

    def test_offline_badge_says_where_the_result_came_from(self):
        lexical_engine.set_offline_only(True)
        cases = [
            (dict(WORD, _from_history=True), "your history"),
            (dict(WORD, persian_source=lexical_engine.FA_SOURCE_NLLB), "Persian by the offline translator"),
            (dict(WORD, persian_source=lexical_engine.FA_SOURCE_WIKTIONARY), "Ocean's internal dictionary"),
        ]
        for payload, expected in cases:
            self.win.results_page.render("serendipity", payload)
            self.assertIn(expected, offline_badges(self.win)[0].text())

    def test_a_saved_lookup_shown_offline_is_marked_as_history(self):
        lexical_engine.set_offline_only(True)
        with mock.patch("ui.main_window.start_worker"):
            self.win._analyze_input(override_term="serendipity")
        self.assertIn("your history", offline_badges(self.win)[0].text())
        self.assertNotIn("_from_history", self.win.db.get_entry("serendipity"))  # never stored

    def test_synonyms_are_grouped_under_their_sense_and_derived_terms_show(self):
        payload = dict(WORD, derived_terms=["serendipitous", "serendipitously"], synonyms=[
            {"term": "luck", "fa": "شانس", "sense": "noun: an unknown force"},
            {"term": "fortune", "fa": "بخت", "sense": "noun: an unknown force"},
            {"term": "chance", "fa": "اتفاق", "sense": "noun: an unplanned event"},
        ])
        self.win.results_page.render("serendipity", payload)
        texts = card_texts(self.win)
        self.assertEqual(texts.count("noun: an unknown force"), 1)  # one heading per sense
        self.assertEqual(texts.count("noun: an unplanned event"), 1)
        self.assertLess(texts.index("noun: an unknown force"), texts.index("noun: an unplanned event"))
        self.assertIn("<b>Derived terms:</b>", texts)
        links = [b.text() for b in self.win.results_page.findChildren(QPushButton)]
        self.assertIn("serendipitously", links)

    def test_pronounce_page_reads_aloud_with_ipa_and_never_translates(self):
        use_lexicon(self)
        self.win.right_drawer.show_page(PAGE_MENU)
        menu_btn = next(b for b in self.win.right_drawer.findChildren(QPushButton) if b.text() == "Pronounce")
        no_lookup = AssertionError("the Pronounce page must not look up or translate")
        with mock.patch.object(lexical_engine, "analyze_with_dictionaries", side_effect=no_lookup), \
             mock.patch.object(lexical_engine, "translate_with_source", side_effect=no_lookup), \
             mock.patch.object(lexical_engine, "translate_batch_fast", side_effect=no_lookup), \
             mock.patch.object(self.win.speaker, "has_voice", return_value=True), \
             mock.patch.object(self.win.speaker, "speak", return_value=7) as speak:
            menu_btn.click()
            page = self.win.pronounce_page
            self.assertIs(self.win.stack.currentWidget(), page)
            page.input.setPlainText("  bank  ")
            page.pronounce()
            page.worker.wait(5000)
            for _ in range(20):
                self.app.processEvents()
                if page.ipa_label.text():
                    break
                time.sleep(0.05)
        speak.assert_called_once_with("bank")
        self.assertEqual(page.text_label.text(), "bank")
        self.assertEqual(page.ipa_label.text(), "US  /bæŋk/")  # the dictionary's transcription for a word
        self.assertEqual(pronounce_page.ipa_for("bank of the river").count("/"), 2)  # word by word for a phrase

    def test_a_clear_message_shows_only_when_every_source_is_off(self):
        registry = dictionary_sources.REGISTRY
        empty = {"headword": "serendipity", "persian_equivalent": "x", "senses": []}
        self.win.results_page.render("serendipity", empty)
        self.assertNotIn("All dictionary sources are turned off", card_texts(self.win))
        for name in registry.names():
            registry.set_enabled(name, False)
        self.win.results_page.render("serendipity", empty)
        self.assertIn("All dictionary sources are turned off", card_texts(self.win))
        self.assertTrue(any("Settings > Dictionary sources" in t for t in card_texts(self.win)))
        # Entries with definitions (e.g. saved ones) and one source left on need no notice.
        self.win.results_page.render("serendipity", WORD)
        self.assertNotIn("All dictionary sources are turned off", card_texts(self.win))
        registry.set_enabled("wordnet", True)
        self.win.results_page.render("serendipity", empty)
        self.assertNotIn("All dictionary sources are turned off", card_texts(self.win))

    def test_teach_page_prefills_current_term(self):
        self.win._analyze_input(override_term="serendipity")
        self.win.right_drawer.show_page(PAGE_TEACH)
        self.assertEqual(self.win.right_drawer.teach_page.teach_term.text(), "serendipity")

    def test_review_page_opens(self):
        self.win.open_review()
        self.assertIs(self.win.stack.currentWidget(), self.win.review_page)
        self.assertTrue(self.win.review_page.term_label.text())  # a card, or "Nothing to review"

    def save_quiz_words(self):
        pairs = [("book", "کتاب"), ("water", "آب"), ("house", "خانه"), ("tree", "درخت")]
        for en, fa in pairs:
            self.win.db.save_entry(en, {"headword": en, "persian_equivalent": fa})
            self.win.db.toggle_favorite(en)
        self.quiz_words = [en for en, _ in pairs]

    def test_quiz_explains_what_is_needed_with_too_few_saved_words(self):
        self.win.open_quiz()
        page = self.win.quiz_page
        self.assertIs(self.win.stack.currentWidget(), page)
        self.assertIn("at least 4", page.done_label.text())
        self.assertFalse(page.choice_box.isVisible())

    def test_quiz_session_scores_answers_and_leaves_reviews_alone(self):
        self.save_quiz_words()
        due_before = self.win.review_page.store.due_count(datetime.now())
        self.win.open_quiz()
        QTest.qWaitForWindowActive(self.win)  # shortcuts only fire in the active window
        page = self.win.quiz_page
        self.assertEqual(len(page.questions), 4)
        for n in range(4):
            question = page.questions[n]
            right = [question.is_correct(c) for c in question.choices].index(True)
            wrong = (right + 1) % 4
            QTest.keyClick(page, str(1 + (right if n < 3 else wrong)))  # the last one is answered wrongly
            self.assertTrue(page.answered)
            QTest.keyClick(page, Qt.Key.Key_Space)
        self.assertTrue(page.finished)
        self.assertEqual(page.prompt_label.text(), "3 of 4 correct")
        self.assertIn("75%", page.done_label.text())
        # Quiz answers never schedule anything: the review deck is exactly as it was.
        self.assertEqual(self.win.review_page.store.due_count(datetime.now()), due_before)
        with closing(sqlite3.connect(DB_FILE)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)
        # Space on the score screen plays again.
        QTest.keyClick(page, Qt.Key.Key_Space)
        self.assertFalse(page.finished)
        self.assertEqual(page.results, [])

    def test_quiz_escape_ends_early_with_a_score_then_goes_home(self):
        self.save_quiz_words()
        self.win.open_quiz()
        QTest.qWaitForWindowActive(self.win)
        page = self.win.quiz_page
        QTest.keyClick(page, "2")
        QTest.keyClick(page, Qt.Key.Key_Escape)
        self.assertTrue(page.finished)
        self.assertIn("ended early", page.done_label.text())
        QTest.keyClick(page, Qt.Key.Key_Escape)
        self.assertIs(self.win.stack.currentWidget(), self.win.home_page)

    def save_cloze_words(self):
        words = {
            "run": ("دویدن", "She ran to the station every morning."),
            "break the ice": ("یخ را شکستن", "Tom broke the ice with a joke."),
        }
        for en, (fa, example) in words.items():
            payload = {"headword": en, "persian_equivalent": fa, "senses": [{"pos": "Verb", "def": "d", "example_en": example}]}
            self.win.db.save_entry(en, payload)
            self.win.db.toggle_favorite(en)
        self.win.db.save_entry("door", {"headword": "door", "persian_equivalent": "در"})  # no example: skipped
        self.win.db.toggle_favorite("door")
        self.quiz_words = [*words, "door"]

    def open_cloze_quiz(self):
        self.save_cloze_words()
        self.win.open_quiz()
        QTest.qWaitForWindowActive(self.win)
        page = self.win.quiz_page
        self.assertTrue(page.mode_drop.isVisible())  # two modes now: the picker shows up
        page._on_mode_picked("Fill in the blank")
        return page

    def test_quiz_fill_in_the_blank_typed_answers(self):
        page = self.open_cloze_quiz()
        self.assertEqual(len(page.questions), 2)  # "door" has no example
        self.assertTrue(page.typed_box.isVisible())
        self.assertFalse(page.choice_box.isVisible())
        self.assertTrue(page.answer_edit.hasFocus())
        self.assertIn("_____", page.prompt_label.text())

        # Keys 1-4, Space and letters are typed into the box, not treated as shortcuts.
        QTest.keyClicks(page.answer_edit, "1 2 x")
        self.assertEqual(page.answer_edit.text(), "1 2 x")
        self.assertFalse(page.answered)
        self.assertEqual(page.results, [])
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)  # wrong answer
        self.assertTrue(page.answered)
        self.assertIn("Not quite", page.verdict_label.text())
        question = page.questions[0]
        self.assertIn(question.answer, page.verdict_label.text())
        self.assertEqual(page.reveal_label.text(), question.reveal)  # the full sentence
        self.assertTrue(page.answer_edit.isReadOnly())
        self.assertTrue(page.next_btn.isVisible())

        QTest.keyClick(page, Qt.Key.Key_Space)  # continue
        self.assertFalse(page.answered)
        self.assertEqual(page.answer_edit.text(), "")
        self.assertFalse(page.answer_edit.isReadOnly())
        self.assertTrue(page.answer_edit.hasFocus())
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)  # empty input is ignored
        self.assertFalse(page.answered)
        # The base word as saved is accepted, in any case.
        QTest.keyClicks(page.answer_edit, page.questions[1].accepted[-1].upper())
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)
        self.assertIn("Correct", page.verdict_label.text())
        QTest.keyClick(page, Qt.Key.Key_Return)  # Enter continues too
        self.assertTrue(page.finished)
        self.assertEqual(page.prompt_label.text(), "1 of 2 correct")
        self.assertIn(question.reveal, page.done_label.text())  # missed sentence listed to practise
        # Typed answers don't schedule reviews either.
        with closing(sqlite3.connect(DB_FILE)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_quiz_switching_back_to_multiple_choice_hides_the_typed_box(self):
        page = self.open_cloze_quiz()
        self.save_quiz_words()
        page._on_mode_picked("Multiple choice")
        self.assertFalse(page.typed_box.isVisible())
        self.assertTrue(page.choice_box.isVisible())
        QTest.keyClick(page, "1")
        self.assertTrue(page.answered)

    def test_quiz_cloze_needs_an_example_sentence(self):
        self.save_quiz_words()  # four words, none with an example
        self.win.open_quiz()
        page = self.win.quiz_page
        page._on_mode_picked("Fill in the blank")
        self.assertIn("example sentence", page.done_label.text())
        self.assertFalse(page.typed_box.isVisible())

    def open_listen_quiz(self, voice=True, loading=False):
        """The quiz page in Listen and type mode, with the speaker faked so nothing makes sound."""
        self.save_quiz_words()
        speaker = self.win.speaker
        for name, value in (("has_voice", voice), ("is_loading", loading)):
            patcher = mock.patch.object(speaker, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.spoken = []
        for name, side_effect in (("speak", lambda text: self.spoken.append(text) or len(self.spoken)),
                                  ("stop", lambda: self.spoken.append(None))):
            patcher = mock.patch.object(speaker, name, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.win.open_quiz()
        QTest.qWaitForWindowActive(self.win)
        page = self.win.quiz_page
        page._on_mode_picked("Listen and type")
        return page

    def test_quiz_listen_and_type_speaks_replays_and_never_shows_the_word(self):
        page = self.open_listen_quiz()
        self.assertEqual(len(page.questions), 4)
        question = page.questions[0]
        self.assertEqual(self.spoken[-1], question.answer)  # auto-played as the question appeared
        self.assertTrue(page.typed_box.isVisible())
        self.assertTrue(page.replay_btn.isVisible())
        self.assertTrue(page.answer_edit.hasFocus())
        shown = " ".join([page.prompt_label.text(), page.hint_label.text()])
        self.assertNotIn(question.answer, shown)

        page.replay_btn.click()
        self.assertEqual(self.spoken[-2:], [question.answer] * 2)
        before = len(self.spoken)
        QTest.keyClick(page.answer_edit, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)  # Ctrl+R while typing
        self.assertEqual(self.spoken[before:], [question.answer])
        self.assertEqual(page.answer_edit.text(), "")  # no "r" was typed
        QTest.keyClicks(page.answer_edit, "rrr")  # a plain r still types
        self.assertEqual(len(self.spoken), before + 1)
        self.assertEqual(page.answer_edit.text(), "rrr")

        # A wrong answer reveals the word, the Persian meaning and counts as missed.
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)
        self.assertIn("Not quite", page.verdict_label.text())
        self.assertIn(question.answer, page.verdict_label.text())
        self.assertIn(question.reveal, page.reveal_label.text())
        QTest.keyClick(page, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)  # replay still works after answering
        self.assertEqual(self.spoken[-1], question.answer)
        QTest.keyClick(page, Qt.Key.Key_Space)
        self.assertEqual(self.spoken[-1], page.questions[1].answer)  # the next word is spoken on its own

    def test_quiz_listen_and_type_checks_case_and_calls_one_typo_almost(self):
        page = self.open_listen_quiz()
        page.questions = [quiz.Question(
            mode=quiz.LISTEN_MODE, prompt="Listen and type the word", answer=answer, query_key=answer,
            direction=quiz.EN_ONLY, reveal="/x/ · معنی", speak=answer, near_miss=True) for answer in ("necessary", "water", "house")]
        page.results = []
        page._show_question()
        QTest.keyClicks(page.answer_edit, "NECCESSARY")
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)
        self.assertIn("Almost", page.verdict_label.text())
        self.assertIn("correct spelling: <b>necessary</b>", page.verdict_label.text())
        QTest.keyClick(page, Qt.Key.Key_Space)
        QTest.keyClicks(page.answer_edit, "  WaTeR ")
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)
        self.assertIn("Correct", page.verdict_label.text())
        self.assertIn("water", page.verdict_label.text())  # the word is shown once answered
        QTest.keyClick(page, Qt.Key.Key_Space)
        QTest.keyClicks(page.answer_edit, "hous")  # four letters, one short of the word: still just a miss
        QTest.keyClick(page.answer_edit, Qt.Key.Key_Return)
        self.assertIn("Almost", page.verdict_label.text())
        QTest.keyClick(page, Qt.Key.Key_Space)
        self.assertTrue(page.finished)
        self.assertEqual(page.prompt_label.text(), "1 of 3 correct")  # near-misses are not correct
        self.assertIn("2 almost", page.done_label.text())
        self.assertIn("/x/ · معنی", page.done_label.text())
        self.assertEqual(self.spoken[-1], None)  # the score screen is silent
        with closing(sqlite3.connect(DB_FILE)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_quiz_listen_and_type_stops_speech_when_leaving(self):
        page = self.open_listen_quiz()
        self.assertEqual(self.spoken[-1], page.questions[0].answer)
        QTest.keyClick(page, Qt.Key.Key_Escape)  # nothing answered yet: leaves the quiz
        self.assertIs(self.win.stack.currentWidget(), self.win.home_page)
        self.assertIsNone(self.spoken[-1])

    def test_quiz_other_modes_do_not_speak_or_show_replay(self):
        page = self.open_listen_quiz()
        page._on_mode_picked("Multiple choice")
        self.assertFalse(page.replay_btn.isVisible())
        spoken = len(self.spoken)
        QTest.keyClick(page, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual([s for s in self.spoken[spoken:] if s], [])

    def test_quiz_listen_and_type_is_disabled_without_a_voice(self):
        page = self.open_listen_quiz(voice=False)
        self.assertEqual(page.questions, [])
        self.assertIn("needs a voice", page.done_label.text())
        self.assertFalse(page.typed_box.isVisible())
        self.assertFalse(page.replay_btn.isVisible())
        self.assertEqual([s for s in self.spoken if s], [])
        page._on_mode_picked("Multiple choice")  # the other modes are unaffected
        self.assertTrue(page.choice_box.isVisible())

    def test_quiz_listen_and_type_waits_for_the_voice_to_load(self):
        page = self.open_listen_quiz(voice=False, loading=True)
        self.assertIn("still loading", page.done_label.text())
        self.assertEqual(page.questions, [])
        with mock.patch.object(self.win.speaker, "has_voice", return_value=True), \
                mock.patch.object(self.win.speaker, "is_loading", return_value=False):
            self.win.speaker.voice_loaded.emit()
        self.assertEqual(len(page.questions), 4)
        self.assertTrue(page.typed_box.isVisible())
        self.assertEqual(self.spoken[-1], page.questions[0].answer)
        # Once running, a later voice_loaded does not restart the round.
        page.results.append((page.questions[0], "x"))
        self.win.speaker.voice_loaded.emit()
        self.assertEqual(len(page.results), 1)

    def test_quiz_listen_and_type_loading_that_ends_without_a_voice_says_so(self):
        page = self.open_listen_quiz(voice=False, loading=True)
        with mock.patch.object(self.win.speaker, "is_loading", return_value=False):
            self.win.speaker.voice_loaded.emit()
        self.assertIn("needs a voice", page.done_label.text())  # re-checked when loading finished: now the full explanation
        self.assertEqual(page.questions, [])

    def test_quiz_reachable_from_home_and_menu(self):
        self.save_quiz_words()
        self.win.home_quiz_btn.click()
        self.assertIs(self.win.stack.currentWidget(), self.win.quiz_page)
        self.win.go_home()
        self.win._toggle_right_drawer()
        menu_buttons = {b.text(): b for b in self.win.right_drawer.stack.widget(PAGE_MENU).findChildren(QPushButton)}
        menu_buttons["Quiz"].click()
        self.assertIs(self.win.stack.currentWidget(), self.win.quiz_page)
        self.assertFalse(self.win.right_drawer.isVisible())

    def test_persian_widget_uses_registered_font(self):
        self.win.results_page.render("serendipity", WORD)
        buttons = [b.text() for b in self.win.results_page.findChildren(QPushButton)]
        self.assertIn("Reveal Persian Translation", buttons)

    def test_example_audio_button_speaks_the_sentence_and_leaves_the_text_alone(self):
        example = WORD["senses"][0]["example_en"]
        speaker = self.win.speaker
        with mock.patch.object(speaker, "prefetch") as prefetch:
            self.win.results_page.render("serendipity", WORD)
        prefetch.assert_any_call(example)  # synthesized ahead, so the click plays at once
        label = next(l for l in self.win.results_page.findChildren(QLabel) if "that we met" in l.text())
        button = next(b for b in label.parentWidget().findChildren(QPushButton) if b.toolTip() == f"Listen: {example}")
        original = label.text()
        with mock.patch.object(speaker, "speak") as speak:
            button.click()
        speak.assert_called_once_with(example)
        self.assertEqual(label.text(), original)  # no word highlighting

    def _make_stale(self, term="serendipity"):
        with self.win.db._conn() as conn:
            conn.execute("UPDATE lexicon SET produced_at = '2000-01-01T00:00:00', engine_version = 0 WHERE query_key = ?", (term,))

    def _lookup_stale(self, term="serendipity"):
        self._make_stale(term)
        with mock.patch("ui.main_window.EngineWorker", FakeWorker), mock.patch("ui.main_window.start_worker"):
            self.win._analyze_input(override_term=term)
        return self.win.worker

    def test_stale_entry_is_fetched_again_and_replaced(self):
        worker = self._lookup_stale()
        self.assertIsInstance(worker, FakeWorker)  # not served from the saved copy
        fresh = dict(WORD, persian_equivalent="شانس خوب")
        worker.finished_success.emit("serendipity", fresh)
        saved = self.win.db.get_entry("serendipity")
        self.assertEqual(saved["persian_equivalent"], "شانس خوب")
        self.assertEqual(saved["_engine_version"], lexical_engine.ENGINE_VERSION)
        self.assertEqual(self.win.current_data["persian_equivalent"], "شانس خوب")

    def test_a_preview_shows_first_and_the_full_entry_replaces_it(self):
        with mock.patch("ui.main_window.EngineWorker", FakeWorker), mock.patch("ui.main_window.start_worker"):
            self.win._analyze_input(override_term="zeitgeist")
        worker = self.win.worker
        preview = dict(WORD, headword="zeitgeist", persian_equivalent=lexical_engine.PENDING_FA, _preview=True)
        worker.preview_ready.emit("zeitgeist", preview)
        self.assertIn("Finding something good by chance.", " ".join(card_texts(self.win)))
        self.assertIn("Translating", self.win.status_label.text())
        self.assertIsNone(self.win.current_data)  # nothing to edit or save yet
        self.assertIsNone(self.win.db.get_entry("zeitgeist"))
        worker.finished_success.emit("zeitgeist", dict(WORD, headword="zeitgeist", persian_equivalent="روح زمانه"))
        self.assertEqual(self.win.current_data["persian_equivalent"], "روح زمانه")
        self.assertEqual(self.win.status_label.text(), "")
        self.win.db.delete_entry("zeitgeist")

    def test_a_refresh_that_fails_after_its_preview_shows_the_saved_copy(self):
        worker = self._lookup_stale()
        worker.preview_ready.emit("serendipity", dict(WORD, senses=[{"pos": "Noun", "def": "A preview sense."}], _preview=True))
        self.assertIn("A preview sense.", " ".join(card_texts(self.win)))
        worker.finished_error.emit("no network")
        self.assertIn("Finding something good by chance.", " ".join(card_texts(self.win)))  # the saved copy is back
        self.assertEqual(self.win.current_data["persian_equivalent"], WORD["persian_equivalent"])

    def test_stale_entry_is_shown_when_the_refetch_fails_or_is_empty(self):
        for outcome in ("error", "empty"):
            worker = self._lookup_stale()
            if outcome == "error":
                worker.finished_error.emit("no network")
            else:
                worker.finished_success.emit("serendipity", {"headword": "serendipity", "senses": [],
                                                             "persian_equivalent": lexical_engine.NOT_FOUND_FA})
            self.assertEqual(self.win.current_data["persian_equivalent"], WORD["persian_equivalent"], outcome)
            self.assertIn("Finding something good by chance.", " ".join(card_texts(self.win)))
            self.assertIn("saved copy", self.win.status_label.text())
            self.assertEqual(self.win.db.get_entry("serendipity")["_engine_version"], 0)  # still stale: retried next time
            self.assertTrue(self.win.explore_btn.isEnabled())

    def test_refresh_keeps_favourite_and_manual_persian_edit(self):
        self.win.db.toggle_favorite("serendipity")
        worker = self._lookup_stale()
        fresh = dict(WORD, persian_equivalent="شانس خوب", senses=[dict(WORD["senses"][0], example_fa="ماشینی")])
        with mock.patch.dict(lexical_engine.USER_MEMORY["overrides"], {"it was serendipity that we met.": "ویرایش من"}):
            worker.finished_success.emit("serendipity", fresh)
        saved = self.win.db.get_entry("serendipity")
        self.assertTrue(saved["_is_favorite"])
        self.assertEqual(saved["senses"][0]["example_fa"], "ویرایش من")

    def test_no_refresh_when_period_is_never_or_offline_mode_is_on(self):
        self._make_stale()
        for key, val in (("refresh_period", "Never"), ("offline_only", True)):
            with mock.patch.dict(self.win.settings.data, {key: val}), \
                    mock.patch("ui.main_window.EngineWorker", FakeWorker), mock.patch("ui.main_window.start_worker"):
                self.win.worker = None
                self.win._analyze_input(override_term="serendipity")
            self.assertIsNone(self.win.worker, key)
            self.assertEqual(self.win.current_term, "serendipity")

    def test_settings_page_saves_the_refresh_period(self):
        page = self.win.right_drawer.settings_page
        self.assertEqual(page.refresh_drop.currentText(), "3 months")
        page.refresh_drop.current_val = "1 week"
        with mock.patch.object(self.win.settings, "save"):
            page._apply_live()
        self.assertEqual(self.win.settings.data["refresh_period"], "1 week")
        self.win.settings.data["refresh_period"] = "3 months"


RUN = {
    "headword": "run",
    "ipa_us": "/rʌn/",
    "persian_equivalent": "دویدن",
    "senses": [{"pos": "Verb", "def": "Move fast on foot.", "example_en": "I run daily.", "example_fa": "من هر روز می دوم."}],
    "synonyms": [{"term": "sprint", "fa": "دوی سرعت"}],
}
ICE = {"headword": "break the ice", "is_idiom": True, "persian_equivalent": "یخ را شکستن",
       "senses": [{"pos": "Idiom", "def": "Start a conversation."}]}


def _luminance(color):
    channels = [int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ReaderPageTest(unittest.TestCase):
    TEXT = "She ran home. The children's resilient mother-in-law couldn't stop; break the ice, Zorblax!"

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.register_custom_fonts()

    def setUp(self):
        self._remove_state()
        self.words = []
        self.win = MainWindow()
        self.win.show()
        self.page = self.win.reader_page

    def tearDown(self):
        for word in self.words + ["run", "break the ice"]:
            self.win.db.delete_entry(word)
        self.win.settings.data["theme"] = "Dark Mode"
        self.win.settings.data["reader_min_level"] = "B2"
        self.page.view.clear()  # a 5,000-word document would be slow to tear down inside the next test's waits
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self._remove_state()
        self.win = self.page = None

    @staticmethod
    def _remove_state():
        if os.path.exists(READER_FILE):
            os.remove(READER_FILE)

    def open_with(self, text=None, min_level="B2"):
        self.win.settings.data["reader_min_level"] = min_level
        self.page.level_drop.setCurrentText(min_level)
        self.win.open_reader()
        self.page.set_text(self.TEXT if text is None else text)
        self.app.processEvents()

    def word_point(self, text, word, nth=0):
        """Viewport point in the middle of the nth occurrence of `word` in the text."""
        start = -1
        for _ in range(nth + 1):
            start = text.index(word, start + 1)
        units = self.page.units
        view = self.page.view
        cursor = QTextCursor(view.document())
        cursor.setPosition(units.to_units(start))
        left = view.cursorRect(cursor)
        cursor.setPosition(units.to_units(start + len(word)))
        right = view.cursorRect(cursor)
        return QPoint((left.x() + right.x()) // 2, left.center().y())

    def click(self, word, nth=0):
        QTest.mouseClick(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=self.word_point(self.page.text, word, nth))

    def marks_by_colour(self):
        """{word: background colour name} for the marked words, read from the view's extra selections."""
        out = {}
        for sel in self.page.view.extraSelections():
            out[sel.cursor.selectedText()] = sel.format.background().color().name()
        return out

    def fake_lookups(self):
        patches = [mock.patch("ui.reader_page.EngineWorker", FakeWorker), mock.patch("ui.reader_page.start_worker")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_reachable_from_home_and_menu(self):
        self.win.home_reader_btn.click()
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.win.go_home()
        self.win._toggle_right_drawer()
        menu_buttons = {b.text(): b for b in self.win.right_drawer.stack.widget(PAGE_MENU).findChildren(QPushButton)}
        menu_buttons["Reader"].click()
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.assertFalse(self.win.right_drawer.isVisible())

    def test_paste_a_text_then_read_it(self):
        self.win.open_reader()
        self.assertIs(self.page.stack.currentWidget(), self.page.stack.widget(0))  # nothing yet: the paste box
        self.assertFalse(self.page.edit_btn.isVisible())
        self.page.input.setPlainText("Hello there.\r\nSecond line, ten words.")
        self.assertIn("6 words", self.page.count_label.text())
        self.page.read_btn.click()
        self.assertIs(self.page.stack.currentWidget(), self.page.splitter)
        self.assertEqual(self.page.view.toPlainText(), "Hello there.\nSecond line, ten words.")
        self.assertTrue(self.page.view.isReadOnly())
        self.page.edit_btn.click()
        self.assertEqual(self.page.input.toPlainText(), "Hello there.\nSecond line, ten words.")
        self.page.cancel_btn.click()
        self.assertIs(self.page.stack.currentWidget(), self.page.splitter)
        self.page.edit_btn.click()
        self.page.clear_btn.click()
        self.page.read_btn.click()  # an empty text goes back to the paste box
        self.assertIs(self.page.stack.currentWidget(), self.page.stack.widget(0))
        self.assertEqual(self.page.text, "")

    def test_a_long_text_is_one_rich_text_view_not_a_widget_per_word(self):
        before = len(self.page.findChildren(QPushButton)) + len(self.page.findChildren(QLabel))
        text = (self.TEXT + " ") * 420  # ~5,500 words
        start = time.time()
        self.open_with(text)
        self.assertGreater(len(self.page.marked), 5000)
        self.assertLess(time.time() - start, 5.0)
        after = len(self.page.findChildren(QPushButton)) + len(self.page.findChildren(QLabel))
        self.assertEqual(after, before)
        self.assertEqual(self.page.view.toPlainText(), self.page.text)
        self.assertGreater(self.page.view.verticalScrollBar().maximum(), 0)  # it scrolls, it does not stretch the window

    def test_clicking_a_word_looks_up_its_base_word_without_moving_the_text(self):
        self.fake_lookups()
        self.open_with((self.TEXT + "\n") * 80)
        bar = self.page.view.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        self.app.processEvents()
        # click the first "ran" below the top of the view
        top = self.page.units.to_chars(self.page.view.cursorForPosition(QPoint(20, 16)).position())
        nth = self.page.text.count("ran", 0, top) + 1
        scroll = bar.value()
        self.words.append("run")
        self.click("ran", nth)
        worker = self.page.worker
        self.assertIsInstance(worker, FakeWorker)
        self.assertEqual(worker.term, "run")          # the base word, not "ran"
        self.assertIn("Looking up", self.page.panel.title.text())
        worker.finished_success.emit("run", RUN)
        self.assertEqual(self.page.panel.current_term, "run")
        self.assertIn("Move fast on foot.", " ".join(lbl.text() for lbl in self.page.panel.results.findChildren(QLabel)))
        self.assertIn("ran → run", self.page.panel.title.text())
        self.assertEqual(bar.value(), scroll)                      # the place in the text is kept
        self.assertIs(self.win.stack.currentWidget(), self.page)   # and the reader stayed on screen
        self.assertEqual(self.win.db.get_entry("run")["persian_equivalent"], "دویدن")

    def test_clicking_between_words_or_past_the_end_does_nothing(self):
        self.fake_lookups()
        self.open_with("She ran home.\n\nEnd")
        view = self.page.view
        point = self.word_point(self.page.text, "home")
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(point.x() + 200, point.y()))  # blank space right of the line
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(view.viewport().width() - 3, view.viewport().height() - 3))
        self.assertIsNone(self.page.worker)

    def test_selecting_several_words_looks_up_the_phrase(self):
        self.fake_lookups()
        self.open_with()
        start = self.page.text.index("break")
        cursor = QTextCursor(self.page.view.document())
        cursor.setPosition(start)
        cursor.setPosition(start + len("break the ice"), QTextCursor.MoveMode.KeepAnchor)
        self.page.view.setTextCursor(cursor)
        QTest.mouseRelease(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=self.word_point(self.page.text, "ice"))
        self.assertEqual(self.page.worker.term, "break the ice")
        # a selection that cuts words in half still covers whole words
        cursor.setPosition(start + 2)
        cursor.setPosition(start + len("break the ic"), QTextCursor.MoveMode.KeepAnchor)
        self.page.view.setTextCursor(cursor)
        self.page.pending_term = ""
        self.page.worker = None
        QTest.mouseRelease(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=self.word_point(self.page.text, "ice"))
        self.assertEqual(self.page.worker.term, "break the ice")

    def test_a_selection_of_a_whole_text_is_not_a_phrase_lookup(self):
        self.fake_lookups()
        self.open_with(("word " * 60).strip())
        self.page.view.selectAll()
        QTest.mouseRelease(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        self.assertIsNone(self.page.worker)
        self.assertIn("shorter phrase", self.page.panel.title.text())

    def test_a_saved_copy_is_shown_without_a_fetch(self):
        self.fake_lookups()
        self.win.db.save_entry("run", RUN)
        self.words.append("run")
        self.open_with()
        self.click("ran")
        self.assertIsNone(self.page.worker)
        self.assertEqual(self.page.panel.current_term, "run")
        self.assertEqual(self.win.db.list_entries(query="run")[0]["lookup_count"], 2)  # the lookup counted

    def test_a_failed_lookup_says_so_and_can_be_tried_again(self):
        self.fake_lookups()
        self.open_with()
        self.click("ran")
        self.page.worker.finished_error.emit("no network")
        self.assertIn("Couldn't look up 'run': no network", self.page.panel.title.text())
        self.page.worker = None
        self.click("ran")
        self.assertIsNotNone(self.page.worker)

    def test_related_words_in_the_panel_are_looked_up_in_the_panel(self):
        self.fake_lookups()
        self.open_with()
        self.click("ran")
        self.page.worker.finished_success.emit("run", RUN)
        pills = [b for b in self.page.panel.results.findChildren(QPushButton) if b.text() == "sprint"]
        self.assertTrue(pills)
        self.words.append("sprint")
        pills[0].click()
        self.assertEqual(self.page.worker.term, "sprint")
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.page.worker.finished_success.emit("sprint", dict(RUN, headword="sprint"))
        self.assertTrue(self.page.panel.back_btn.isVisible())
        self.page.panel.back_btn.click()
        self.assertEqual(self.page.panel.current_term, "run")
        self.assertFalse(self.page.panel.back_btn.isVisible())

    def test_saved_and_above_level_words_are_marked_differently(self):
        self.win.db.save_entry("home", {"headword": "home", "persian_equivalent": "خانه", "senses": [{"pos": "Noun", "def": "x"}]})
        self.win.db.toggle_favorite("home")
        self.words.append("home")
        self.open_with(min_level="B2")
        marks = self.marks_by_colour()
        saved_bg, above_bg = theme.THEME_COLORS["mark_saved_bg"].lower(), theme.THEME_COLORS["mark_above_bg"].lower()
        self.assertEqual(marks.get("home"), saved_bg)
        self.assertEqual(marks.get("resilient"), above_bg)
        self.assertNotIn("She", marks)
        self.assertNotIn("Zorblax", marks)                        # not in the word list: not marked
        self.assertNotIn("ran", marks)
        # a lower level marks more words, "Off" marks only the saved ones
        self.page.level_drop.valueChanged.emit("A1")
        self.assertEqual(self.marks_by_colour().get("ran"), above_bg)
        self.page.level_drop.valueChanged.emit("Off")
        self.assertEqual(set(self.marks_by_colour().values()), {saved_bg})
        self.assertIn("1 saved", self.page.stats_label.text())

    def test_the_chosen_level_is_remembered(self):
        self.open_with()
        self.page.level_drop.valueChanged.emit("C1")
        self.assertEqual(self.win.settings.data["reader_min_level"], "C1")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "settings.json")
            with mock.patch.object(self.win.settings, "path", path):
                self.win.settings.save()
            self.assertEqual(SettingsManager(path).data["reader_min_level"], "C1")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"reader_min_level": "Z9"}')
            self.assertEqual(SettingsManager(path).data["reader_min_level"], "B2")  # unknown value: the default

    def test_save_a_word_in_one_step_from_the_menu_action(self):
        self.fake_lookups()
        self.open_with()
        # never looked up: saving looks it up first, then saves when the result arrives
        i = reader.token_at(self.page.marked, self.page.text.index("ran"))
        self.words.append("run")
        self.page._menu_action('Save word "run"', "run", (i, i))
        worker = self.page.worker
        self.assertEqual(worker.term, "run")
        self.assertFalse((self.win.db.get_entry("run") or {}).get("_is_favorite"))
        worker.finished_success.emit("run", RUN)
        self.assertTrue(self.win.db.get_entry("run")["_is_favorite"])
        self.assertEqual(self.marks_by_colour().get("ran"), theme.THEME_COLORS["mark_saved_bg"].lower())  # marked right away
        self.assertTrue(any(b.text() == "★ Saved" for b in self.page.panel.results.findChildren(QPushButton)))
        # the menu can undo it, and a word that already has an entry is saved at once
        self.page._menu_action('Remove "run" from saved words', "run", (i, i))
        self.assertFalse(self.win.db.get_entry("run")["_is_favorite"])
        self.assertNotIn("ran", self.marks_by_colour())
        self.page.worker = None
        self.page._menu_action('Save word "run"', "run", (i, i))
        self.assertIsNone(self.page.worker)
        self.assertTrue(self.win.db.get_entry("run")["_is_favorite"])

    def test_the_menu_offers_look_up_and_save_for_the_word_under_the_pointer(self):
        self.open_with()
        char = self.page.units.to_units(self.page.text.index("ran") + 1)

        def options():
            lw = self.page._menu.list_widget
            return [lw.item(n).text().strip() for n in range(lw.count())]

        self.page._show_menu(self.page.mapToGlobal(QPoint(10, 10)), char)
        self.assertEqual(options()[:2], ['Look up "run"', 'Save word "run"'])
        self.assertIn("Select All", options())
        self.page._menu.hide()
        self.page._show_menu(self.page.mapToGlobal(QPoint(10, 10)), -1)   # not on a word
        self.assertEqual(options(), ["Select All"])
        self.page._menu.hide()

    def test_the_star_in_the_panel_saves_and_marks_the_text(self):
        self.fake_lookups()
        self.open_with()
        self.click("ran")
        self.words.append("run")
        self.page.worker.finished_success.emit("run", RUN)
        star = next(b for b in self.page.panel.results.findChildren(QPushButton) if b.text() == "☆ Save")
        self.assertNotIn("ran", self.marks_by_colour())
        star.click()
        self.assertTrue(self.win.db.get_entry("run")["_is_favorite"])
        self.assertEqual(self.marks_by_colour().get("ran"), theme.THEME_COLORS["mark_saved_bg"].lower())

    def test_saved_phrases_are_marked_too(self):
        self.win.db.save_entry("break the ice", ICE)
        self.win.db.toggle_favorite("break the ice")
        self.open_with()
        marks = self.marks_by_colour()
        saved_bg = theme.THEME_COLORS["mark_saved_bg"].lower()
        self.assertEqual([marks.get(w) for w in ("break", "the", "ice")], [saved_bg] * 3)

    def test_the_last_text_and_position_survive_a_restart(self):
        text = "\n".join(f"Line {n} with some words to read." for n in range(400))
        self.open_with(text)
        bar = self.page.view.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        self.app.processEvents()
        self.page.save_state()
        saved_text, top = reader.load_state(READER_FILE)
        self.assertEqual(saved_text, text)
        self.assertGreater(top, 0)
        win2 = MainWindow()
        try:
            win2.show()
            self.assertEqual(win2.reader_page.text, text)
            win2.open_reader()
            self.assertIs(win2.reader_page.stack.currentWidget(), win2.reader_page.splitter)
            for _ in range(3):
                self.app.processEvents()
            top2 = win2.reader_page.units.to_chars(win2.reader_page.view.cursorForPosition(QPoint(20, 16)).position())
            self.assertLess(abs(top2 - top), 80)   # within a couple of lines
            self.assertGreater(win2.reader_page.view.verticalScrollBar().value(), 0)
        finally:
            win2.reader_page.text = ""   # keep this window from rewriting the file as it closes
            win2.close()
            win2.deleteLater()

    def test_the_text_is_kept_in_the_user_data_folder_not_the_repo(self):
        self.assertTrue(READER_FILE.startswith(lexical_engine.USER_DATA_DIR))
        self.open_with()
        self.assertTrue(os.path.exists(READER_FILE))

    def test_emoji_do_not_shift_which_word_is_clicked(self):
        self.fake_lookups()
        self.open_with("\U0001F600\U0001F600 She \U0001F600 ran home")
        self.click("home")
        self.assertEqual(self.page.worker.term, "home")
        self.page.pending_term = ""
        self.click("ran")
        self.assertEqual(self.page.worker.term, "run")

    def test_offline_only_mode_still_looks_words_up_from_local_data(self):
        self.win.settings.data["offline_only"] = True
        lexical_engine.set_offline_only(True)
        seen = []

        def engine(term, on_preview=None):  # stands in for the engine: the real one needs WordNet data
            seen.append((term, lexical_engine.is_offline_only()))
            return dict(RUN, headword=term)

        try:
            with mock.patch("lexical_engine.analyze_with_dictionaries", engine):
                self.open_with("She ran home.")
                self.click("home")
                for _ in range(200):  # the lookup runs on a worker thread
                    self.app.processEvents()
                    if self.page.panel.current_term:
                        break
                    QTest.qWait(50)
            self.assertEqual(seen, [("home", True)])
            self.assertEqual(self.page.panel.current_term, "home")
            labels = [lbl.text() for lbl in self.page.panel.results.findChildren(QLabel)]
            self.assertTrue(any("Offline-only mode" in t for t in labels))
        finally:
            self.win.settings.data["offline_only"] = False
            lexical_engine.set_offline_only(False)
            self.win.db.delete_entry("home")

    def test_marks_are_readable_in_both_themes(self):
        for name in ("Dark Mode", "Light Mode"):
            self.win.settings.data["theme"] = name
            self.win._apply_theme()
            colours = theme.THEME_COLORS
            for key in ("mark_saved_bg", "mark_above_bg"):
                self.assertGreaterEqual(contrast(colours[key], colours["text_main"]), 4.5, (name, key))
                self.assertNotEqual(colours[key], colours["bg_card"])
            self.assertNotEqual(colours["mark_saved_bg"], colours["mark_above_bg"])
        self.open_with()
        self.win.settings.data["theme"] = "Light Mode"
        self.win._apply_theme()
        self.assertEqual(self.marks_by_colour().get("resilient"), theme.THEME_COLORS["mark_above_bg"].lower())  # re-marked
        self.assertIn(theme.THEME_COLORS["mark_above_bg"], self.page.legend.text())


if __name__ == "__main__":
    unittest.main()
