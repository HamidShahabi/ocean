import os
import sys
import tempfile
import unittest
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

import lexical_engine  # noqa: E402
from ui import theme  # noqa: E402
from ui.drawer import PAGE_HISTORY, PAGE_MENU, PAGE_SETTINGS, PAGE_TEACH  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

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
    finished_success = pyqtSignal(str, dict)
    finished_error = pyqtSignal(str)

    def __init__(self, term, deep_mode=False):
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
        for payload in (WORD, SENTENCE, REVERSE, IDIOM):
            self.win.db.delete_entry(payload["headword"])
        self.win.settings.data["theme"] = "Dark Mode"
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self.win = None

    def test_every_result_kind_renders(self):
        for payload in (WORD, SENTENCE, REVERSE, IDIOM):
            self.win.results_page.render(payload["headword"], payload)
            self.assertTrue(any(payload["headword"] in t for t in card_texts(self.win)), payload["headword"])

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

    def test_teach_page_prefills_current_term(self):
        self.win._analyze_input(override_term="serendipity")
        self.win.right_drawer.show_page(PAGE_TEACH)
        self.assertEqual(self.win.right_drawer.teach_page.teach_term.text(), "serendipity")

    def test_review_page_opens(self):
        self.win.open_review()
        self.assertIs(self.win.stack.currentWidget(), self.win.review_page)
        self.assertTrue(self.win.review_page.term_label.text())  # a card, or "Nothing to review"

    def test_persian_widget_uses_registered_font(self):
        self.win.results_page.render("serendipity", WORD)
        buttons = [b.text() for b in self.win.results_page.findChildren(QPushButton)]
        self.assertIn("Reveal Persian Translation", buttons)

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
        for key, val in (("refresh_period", "Never"), ("offline_mode", True)):
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


if __name__ == "__main__":
    unittest.main()
