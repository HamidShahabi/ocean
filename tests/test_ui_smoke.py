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
from PyQt6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

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

    def _example_button_and_label(self):
        """The audio button and sentence label of the first sense's example."""
        example = WORD["senses"][0]["example_en"]
        self.win.results_page.render("serendipity", WORD)
        label = next(l for l in self.win.results_page.findChildren(QLabel) if "that we met" in l.text())
        button = next(b for b in label.parentWidget().findChildren(QPushButton) if b.toolTip() == f"Listen: {example}")
        return button, label

    def test_playing_an_example_highlights_each_word_and_stopping_clears_it(self):
        button, label = self._example_button_and_label()
        original = label.text()
        speaker = self.win.speaker
        highlighter = self.win.results_page.example_highlighter
        now = [0.0]
        highlighter._clock = lambda: now[0]
        with mock.patch.object(speaker, "speak", return_value=7) as speak:
            button.click()
        speak.assert_called_once_with(WORD["senses"][0]["example_en"])

        speaker.clip_started.emit(7, 0.5, 3.0)  # speech starts 0.5 s in, lasts 3 s
        self.app.processEvents()
        self.assertNotIn("<span", label.text())  # lead-in silence: nothing highlighted yet

        seen = []
        for t in (0.6, 1.0, 2.2, 3.4):
            now[0] = t
            highlighter._tick()
            self.assertIn("<span", label.text())
            seen.append(label.text().split("<span")[1].split(">")[1].split("<")[0])
        self.assertEqual(seen[0], "It")
        self.assertEqual(seen[-1], "met")
        self.assertEqual(len(set(seen)), len(seen))  # the highlight moved along

        speaker.playback_stopped.emit(7)
        self.app.processEvents()
        self.assertEqual(label.text(), original)
        self.assertFalse(highlighter._timer.isActive())

    def test_starting_another_clip_clears_the_highlight(self):
        button, label = self._example_button_and_label()
        original = label.text()
        speaker = self.win.speaker
        with mock.patch.object(speaker, "speak", return_value=1):
            button.click()
        speaker.clip_started.emit(1, 0.0, 3.0)
        self.app.processEvents()
        self.assertNotEqual(label.text(), original)
        speaker.clip_started.emit(2, 0.0, 1.0)  # some other clip (e.g. a headword button) starts
        self.app.processEvents()
        self.assertEqual(label.text(), original)
        # A late "stopped" for the first clip must not disturb anything.
        speaker.playback_stopped.emit(1)
        self.assertEqual(label.text(), original)

    def test_stopping_the_speaker_clears_the_highlight(self):
        button, label = self._example_button_and_label()
        original = label.text()
        with mock.patch.object(self.win.speaker, "speak", return_value=1):
            button.click()
        self.win.speaker.clip_started.emit(1, 0.0, 3.0)
        self.app.processEvents()
        self.assertNotEqual(label.text(), original)
        self.win.speaker._token = 1
        self.win.speaker.stop()
        self.assertEqual(label.text(), original)

    def test_fallback_voice_plays_without_highlighting(self):
        button, label = self._example_button_and_label()
        original = label.text()
        speaker = self.win.speaker
        with mock.patch.object(speaker, "speak", return_value=4):
            button.click()
        speaker.clip_started.emit(4, 0.0, 0.0)  # SAPI / CLI voices report no duration
        self.app.processEvents()
        self.assertEqual(label.text(), original)
        self.assertFalse(self.win.results_page.example_highlighter._timer.isActive())

    def test_rerender_while_playing_does_not_crash(self):
        button, label = self._example_button_and_label()
        with mock.patch.object(self.win.speaker, "speak", return_value=1):
            button.click()
        self.win.speaker.clip_started.emit(1, 0.0, 3.0)
        self.app.processEvents()
        self.win.results_page.render("serendipity", WORD)  # the old label is deleted
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.win.results_page.example_highlighter._tick()
        self.assertFalse(self.win.results_page.example_highlighter._timer.isActive())


if __name__ == "__main__":
    unittest.main()
