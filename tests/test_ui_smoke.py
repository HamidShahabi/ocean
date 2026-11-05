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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton  # noqa: E402

import lexical_engine  # noqa: E402
from ui import theme  # noqa: E402
from ui.drawer import PAGE_HISTORY, PAGE_MENU, PAGE_SETTINGS  # noqa: E402
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
        for index, title in ((PAGE_HISTORY, "History"), (PAGE_SETTINGS, "Settings"), (PAGE_MENU, "Ocean Menu")):
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

    def test_history_search_and_saved_filter(self):
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
        # The Saved filter combines with search.
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

    def test_history_page_has_no_sort_dropdown_counter_or_anki_button(self):
        drawer = self.win.right_drawer
        drawer.show_page(PAGE_HISTORY)
        page = drawer.history_page
        self.assertFalse(hasattr(page, "sort_drop"))
        self.win.db.touch_entry("serendipity")
        self.win._analyze_input(override_term="serendipity")
        page.populate()
        row = page.hist_list.itemWidget(page.hist_list.item(0))
        self.assertFalse(hasattr(row, "count_lbl"))
        self.assertNotIn("Export to Anki", [b.text() for b in page.findChildren(QPushButton)])

    def test_a_lookup_moves_the_entry_to_the_top_of_history_but_refresh_and_navigation_do_not(self):
        with self.win.db._conn() as conn:  # timestamps have one-second resolution: start from a known order
            conn.execute("UPDATE lexicon SET updated_at = '2000-01-01T00:00:00'")
            conn.execute("UPDATE lexicon SET updated_at = '2000-01-02T00:00:00' WHERE query_key = 'break the ice'")
        self.win._analyze_input(override_term="serendipity")  # served from the history cache
        self.assertEqual(self.win.db.list_entries()[0]["query_key"], "serendipity")
        with mock.patch("ui.main_window.start_worker"):
            self.win._analyze_input(override_term="break the ice")
        self.assertEqual(self.win.db.list_entries()[0]["query_key"], "break the ice")
        self.win._go_back()  # back/forward re-show an entry, they are not new lookups
        self.assertEqual(self.win.db.list_entries()[0]["query_key"], "break the ice")

    def test_menu_and_home_offer_only_the_remaining_features(self):
        self.win.right_drawer.show_page(PAGE_MENU)
        menu = {b.text() for b in self.win.right_drawer.stack.widget(PAGE_MENU).findChildren(QPushButton)}
        self.assertEqual(menu, {"History", "Settings"})
        home = {b.text() for b in self.win.home_page.findChildren(QPushButton)}
        for removed in ("Quiz", "Reader", "Review"):
            self.assertNotIn(removed, home)
            self.assertFalse(any(t.startswith(removed) for t in home))
        for attr in ("review_page", "quiz_page", "reader_page", "pronounce_page"):
            self.assertFalse(hasattr(self.win, attr), attr)
        self.assertEqual(self.win.stack.count(), 2)  # home and results

    def test_settings_have_no_offline_checkbox_or_source_toggles(self):
        page = self.win.right_drawer.settings_page
        self.assertEqual(page.findChildren(QCheckBox), [])
        self.assertNotIn("offline_only", self.win.settings.data)
        self.assertNotIn("disabled_sources", self.win.settings.data)
        with mock.patch.object(self.win.settings, "save"):
            page._apply_live()
        self.assertNotIn("offline_only", self.win.settings.data)
        self.assertNotIn("disabled_sources", self.win.settings.data)

    def test_a_saved_lookup_has_no_offline_badge_and_leaves_no_history_marker(self):
        self.win.results_page.render("serendipity", WORD)
        layout = self.win.results_page.cards_layout
        self.assertNotIn("OfflineBadge", [layout.itemAt(i).widget().objectName() for i in range(layout.count())
                                          if layout.itemAt(i).widget()])
        self.assertNotIn("_from_history", self.win.db.get_entry("serendipity"))

    def test_the_sources_off_notice_is_gone(self):
        empty = {"headword": "serendipity", "persian_equivalent": "x", "senses": []}
        self.win.results_page.render("serendipity", empty)
        self.assertNotIn("All dictionary sources are turned off", card_texts(self.win))

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

    def test_no_refresh_when_period_is_never(self):
        self._make_stale()
        with mock.patch.dict(self.win.settings.data, {"refresh_period": "Never"}), \
                mock.patch("ui.main_window.EngineWorker", FakeWorker), mock.patch("ui.main_window.start_worker"):
            self.win.worker = None
            self.win._analyze_input(override_term="serendipity")
        self.assertIsNone(self.win.worker)
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
