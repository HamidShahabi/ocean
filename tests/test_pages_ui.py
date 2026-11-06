"""UI tests for the page changes: home/navigation, history bulk delete, sources, reports, refresh and Back."""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton  # noqa: E402

import report_export  # noqa: E402
from ui import theme  # noqa: E402
from ui.drawer import PAGE_HISTORY, PAGE_REPORTS, PAGE_SOURCES  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.sources_page import SOURCES  # noqa: E402
from test_ui_smoke import FakeWorker, IDIOM, SENTENCE, WORD, card_texts  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_word(headword, **extra):
    return dict(WORD, headword=headword, **extra)


class PagesUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.register_custom_fonts()

    def setUp(self):
        self.win = MainWindow()
        self.win.show()
        self.win.db.delete_entries([r["query_key"] for r in self.win.db.list_entries()])
        for r in self.win.reports.list():
            self.win.reports.delete(r["id"])
        for payload in (WORD, SENTENCE, IDIOM):
            self.win.db.save_entry(payload["headword"], payload)
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self.win = None

    def buttons(self, widget=None):
        return {b.text(): b for b in (widget or self.win).findChildren(QPushButton)}

    # --- home (001) ---
    def test_home_has_the_new_placeholder_and_no_top_left_buttons(self):
        self.assertEqual(self.win.input_box.placeholderText(), "Explore words, idioms, or phrases...")
        for w in (self.win.home_logo_btn, self.win.back_btn, self.win.fwd_btn):
            self.assertFalse(w.isVisible())
        self.assertTrue(self.win.menu_btn.isVisible())

    def test_ocean_back_and_forward_appear_after_leaving_home_and_work_as_before(self):
        self.win._analyze_input(override_term="serendipity")
        for w in (self.win.home_logo_btn, self.win.back_btn, self.win.fwd_btn):
            self.assertTrue(w.isVisible())
        self.win._analyze_input(override_term="break the ice")
        self.assertTrue(self.win.back_btn.isEnabled())
        self.win.back_btn.click()
        self.assertEqual(self.win.current_term, "serendipity")
        self.assertTrue(self.win.fwd_btn.isEnabled())
        self.win.home_logo_btn.click()
        self.assertIs(self.win.stack.currentWidget(), self.win.home_page)
        for w in (self.win.home_logo_btn, self.win.back_btn, self.win.fwd_btn):
            self.assertFalse(w.isVisible())

    # --- history (003) ---
    def test_history_rows_have_a_checkbox_before_the_star_and_the_filter_says_favorites_only(self):
        self.win.right_drawer.show_page(PAGE_HISTORY)
        page = self.win.right_drawer.history_page
        self.assertEqual(page.filter_drop.options, ["All Recent Searches", "Favorites Only"])
        row = page.hist_list.itemWidget(page.hist_list.item(0))
        lay = row.layout()
        self.assertIs(lay.itemAt(0).widget(), row.check)
        self.assertIs(lay.itemAt(1).widget(), row.star_btn)
        # The star is centred on the row's centre line, like the word.
        self.assertLessEqual(abs(row.star_btn.geometry().center().y() - row.term_lbl.geometry().center().y()), 2)
        self.assertLessEqual(row.star_btn.width(), 26)

    def test_delete_selected_removes_every_ticked_entry_after_confirming(self):
        drawer = self.win.right_drawer
        drawer.show_page(PAGE_HISTORY)
        page = drawer.history_page
        self.win.db.toggle_favorite("serendipity")
        page.populate()
        rows = {page.hist_list.itemWidget(page.hist_list.item(i)).query_key: page.hist_list.itemWidget(page.hist_list.item(i))
                for i in range(page.hist_list.count())}
        rows["serendipity"].check.setChecked(True)
        rows["break the ice"].check.setChecked(True)
        self.assertEqual(page.count_lbl.text(), "2 selected")
        page.del_btn.click()
        self.assertTrue(page.confirm.isVisible())
        self.assertIn("Delete 2 entries", page.confirm.message.text())
        self.assertIn("1 is a favourite", page.confirm.message.text())
        self.assertIsNotNone(self.win.db.get_entry("serendipity"))  # nothing is deleted before confirming
        page.confirm.cancel_btn.click()
        self.assertIsNotNone(self.win.db.get_entry("serendipity"))
        page.del_btn.click()
        page.confirm.confirm_btn.click()
        self.assertIsNone(self.win.db.get_entry("serendipity"))
        self.assertIsNone(self.win.db.get_entry("break the ice"))
        self.assertIsNotNone(self.win.db.get_entry(SENTENCE["headword"]))  # unticked: kept
        self.assertEqual([e["query_key"] for e in self.win.db.list_entries(favorites_only=True)], [])
        self.assertEqual(page.hist_list.count(), 1)

    def test_delete_selected_with_nothing_ticked_deletes_nothing_and_select_all_ticks_all(self):
        drawer = self.win.right_drawer
        drawer.show_page(PAGE_HISTORY)
        page = drawer.history_page
        before = page.hist_list.count()
        page.del_btn.click()
        self.assertFalse(page.confirm.isVisible())
        self.assertEqual(self.win.db.list_entries().__len__(), before)
        page.select_all.setChecked(True)
        self.assertEqual(len(page.checked_keys()), before)
        self.assertTrue(all(page.hist_list.itemWidget(page.hist_list.item(i)).check.isChecked() for i in range(before)))
        page.del_btn.click()
        page.confirm.confirm_btn.click()
        self.assertEqual(self.win.db.list_entries(), [])

    def test_deleting_the_entry_on_screen_shows_it_as_not_saved(self):
        self.win.db.toggle_favorite("serendipity")
        self.win._analyze_input(override_term="serendipity")
        self.assertIn("★ Saved", self.buttons(self.win.results_page))
        self.win.right_drawer.show_page(PAGE_HISTORY)
        page = self.win.right_drawer.history_page
        page._delete_keys(["serendipity"])
        layout = self.win.results_page.cards_layout
        shown = {b.text() for i in range(layout.count()) if layout.itemAt(i).widget()
                 for b in layout.itemAt(i).widget().findChildren(QPushButton)}
        self.assertIn("☆ Save", shown)
        self.assertNotIn("★ Saved", shown)

    # --- sources (004) ---
    def test_sources_page_is_read_only_and_lists_every_source_in_priority_order(self):
        self.win.right_drawer.show_page(PAGE_SOURCES)
        page = self.win.right_drawer.sources_page
        self.assertEqual(page.findChildren(QCheckBox), [])
        names = [lbl.text() for lbl in page.findChildren(QLabel) if lbl.objectName() == "SourceName"]
        self.assertEqual(names, [s["name"] for s in SOURCES])
        numbers = [lbl.text() for lbl in page.findChildren(QLabel) if lbl.objectName() == "SourcePriority"]
        self.assertEqual(numbers, [str(i) for i in range(1, len(SOURCES) + 1)])
        self.assertFalse(any("WordNet" in s["name"] or "WordNet" in s["role"] for s in SOURCES))
        text = " ".join(lbl.text() for lbl in page.findChildren(QLabel))
        for needle in ("Wiktionary", "OpenSubtitles", "Tatoeba", "Urban Dictionary", "Idioms", "Google Translate", "NLLB"):
            self.assertIn(needle, text)

    # --- result page (005) ---
    def test_section_titles_and_subsections(self):
        payload = dict(WORD, morphology={
            "root": "happy", "structure": "un-  (Prefix)  +  happy  (Root)  +  -ness  (Suffix)   →   unhappiness",
            "prefix": "un-  (not)", "suffix": "-ness  [Noun: state or quality]",
            "family": [{"word": "happy", "pos": "Adjective"}, {"word": "happiness", "pos": "Noun"}]},
            derived_terms=["happy hour", "happy birthday"])
        self.win.results_page.render("unhappiness", dict(payload, headword="unhappiness"))
        texts = card_texts(self.win)
        self.assertIn("3. Forms, Roots & Alternatives", texts)
        self.assertFalse(any("Morphology" in t or "Word Breakdown" in t for t in texts))
        for title in ("Root & Structure", "Word Family", "Common Expressions", "Synonyms", "Antonyms"):
            self.assertIn(title, texts)
        self.assertNotIn("Derived Terms", texts)
        self.assertIn("Prefix", texts)
        self.assertIn("Suffix", texts)
        pills = {b.text() for b in self.win.results_page.cards_container.findChildren(QPushButton)}
        self.assertTrue({"happy hour", "happy birthday", "Noun: happiness"} <= pills)

    def test_empty_morphology_parts_are_hidden(self):
        payload = dict(WORD, headword="privilege", derived_terms=[],
                       morphology={"root": "privilege", "structure": "privilege (Base Root Word)", "prefix": "None",
                                   "suffix": "None (Base Word)", "family": [{"word": "privilege", "pos": "Noun"}]})
        self.win.results_page.render("privilege", payload)
        joined = " ".join(card_texts(self.win))
        for noise in ("None", "Base Root Word", "Prefix", "Suffix", "Word Family", "Common Expressions"):
            self.assertNotIn(noise, joined)
        self.assertIn("Base word", card_texts(self.win))

    def test_common_expressions_accept_renamed_keys_and_dict_items(self):
        payload = dict(WORD, derived_terms=[], common_expressions=[{"term": "pure luck"}, "sheer luck", {"term": "pure luck"}])
        self.win.results_page.render("serendipity", payload)
        pills = [b.text() for b in self.win.results_page.cards_container.findChildren(QPushButton)]
        self.assertEqual(pills.count("pure luck"), 1)
        self.assertIn("sheer luck", pills)

    # --- refresh and Back (005.1) ---
    def test_refresh_queries_online_resources_and_back_returns_to_the_output_before_it(self):
        self.win._analyze_input(override_term="serendipity")
        self.assertIsNone(self.win.worker)  # the saved copy was shown at once
        with mock.patch("ui.main_window.EngineWorker", FakeWorker) as worker_cls, mock.patch("ui.main_window.start_worker"):
            self.buttons(self.win.results_page)["Refresh"].click()
        worker = self.win.worker
        self.assertIsInstance(worker, worker_cls)
        self.assertTrue(worker.refresh)
        self.assertIn("online", self.win.status_label.text())
        # While it runs the current result stays on screen: a preview does not replace it.
        worker.preview_ready.emit("serendipity", dict(WORD, senses=[{"pos": "Noun", "def": "A preview sense."}], _preview=True))
        self.assertNotIn("A preview sense.", " ".join(card_texts(self.win)))
        better = dict(WORD, senses=[dict(WORD["senses"][0], def_="x", **{"def": "Finding something good by luck."})])
        worker.finished_success.emit("serendipity", better)
        self.assertIn("Finding something good by luck.", " ".join(card_texts(self.win)))
        self.assertEqual(self.win.status_label.text(), "Refreshed")
        self.assertEqual(self.win.db.get_entry("serendipity")["senses"][0]["def"], "Finding something good by luck.")

        self.win.back_btn.click()  # the output before the refresh, not a new lookup
        texts = " ".join(card_texts(self.win))
        self.assertIn("Finding something good by chance.", texts)
        self.assertNotIn("by luck", texts)
        self.assertEqual(self.win.current_term, "serendipity")
        self.assertTrue(self.win.fwd_btn.isEnabled())
        self.win.fwd_btn.click()
        self.assertIn("Finding something good by luck.", " ".join(card_texts(self.win)))

    def test_a_refresh_that_brings_nothing_keeps_the_result_and_adds_no_back_step(self):
        self.win._analyze_input(override_term="serendipity")
        steps = len(self.win.nav_history)
        for outcome in ("error", "empty", "same"):
            with mock.patch("ui.main_window.EngineWorker", FakeWorker), mock.patch("ui.main_window.start_worker"):
                self.win.refresh_current()
            worker = self.win.worker
            if outcome == "error":
                worker.finished_error.emit("no network")
            elif outcome == "empty":
                worker.finished_success.emit("serendipity", {"headword": "serendipity", "senses": [], "persian_equivalent": ""})
            else:
                worker.finished_success.emit("serendipity", dict(WORD))
            self.assertEqual(len(self.win.nav_history), steps, outcome)
            self.assertIn("Finding something good by chance.", " ".join(card_texts(self.win)), outcome)
            self.assertTrue(self.win.explore_btn.isEnabled())
        self.assertEqual(self.win.status_label.text(), "Already the best result")

    def test_a_lookup_that_starts_a_refresh_calls_the_engine_with_refresh_only_when_it_can(self):
        import lexical_engine
        from ui.workers import EngineWorker
        calls = []

        def engine(term, on_preview=None, refresh=False):
            calls.append(refresh)
            return {"headword": term}

        with mock.patch.object(lexical_engine, "analyze_with_dictionaries", engine):
            EngineWorker("x", refresh=True).run()
            EngineWorker("x").run()
        self.assertEqual(calls, [True, False])

        def old_engine(term, on_preview=None):
            calls.append("old")
            return {"headword": term}

        with mock.patch.object(lexical_engine, "analyze_with_dictionaries", old_engine):
            EngineWorker("x", refresh=True).run()
        self.assertEqual(calls[-1], "old")

    # --- reports (section 3) ---
    def test_report_button_sits_right_of_save_and_is_a_compact_bang(self):
        self.win._analyze_input(override_term="serendipity")
        header = self.win.results_page.cards_layout.itemAt(0).widget().layout().itemAt(0).layout()
        widgets = [header.itemAt(i).widget() for i in range(header.count()) if header.itemAt(i).widget()]
        names = [w.text() for w in widgets if isinstance(w, QPushButton)]
        self.assertEqual(names, ["Refresh", "☆ Save", "!"])
        bang = widgets[-1]
        self.assertEqual(bang.objectName(), "ReportBangButton")
        self.assertLessEqual(bang.width(), 44)
        self.assertFalse(bang.property("reported"))

    def test_the_bang_button_works_with_a_single_click_and_stores_the_entry_and_a_snapshot(self):
        self.win._analyze_input(override_term="serendipity")
        self.app.processEvents()
        bang = self.buttons(self.win.results_page)["!"]
        bang.click()
        reports = self.win.reports.list()
        self.assertEqual(len(reports), 1)
        report = self.win.reports.get(reports[0]["id"])
        self.assertEqual((report["term"], report["kind"], report["note"]), ("serendipity", "word", ""))
        self.assertEqual(report["payload"]["senses"][0]["def"], WORD["senses"][0]["def"])
        self.assertTrue(report["snapshot"].startswith(PNG_MAGIC))
        self.assertGreater(len(report["snapshot"]), 1000)
        self.assertTrue(bang.property("reported"))
        self.assertIn("Reported 'serendipity'", self.win.status_label.text())
        # The popover offers an optional note; a second click edits the same report instead of adding one.
        popover = self.win.results_page._popover
        self.assertTrue(popover.isVisible())
        popover.note_edit.setText("  the example  is wrong ")
        popover.save_btn.click()
        self.assertEqual(self.win.reports.list()[0]["note"], "the example is wrong")
        bang.click()
        self.assertEqual(len(self.win.reports.list()), 1)
        self.win.results_page._popover._remove()
        self.assertEqual(self.win.reports.list(), [])
        self.assertFalse(bang.property("reported"))

    def test_sentence_idiom_and_persian_results_have_the_button_too(self):
        reverse = {"headword": "کتاب", "is_reverse": True, "english_translation": "book", "candidates": []}
        for payload, kind in ((SENTENCE, "sentence"), (IDIOM, "idiom"), (reverse, "persian")):
            self.win.results_page.render(payload["headword"], payload)
            self.app.processEvents()
            self.assertIn("!", self.buttons(self.win.results_page), kind)
            self.win.results_page._report_btn.click()
            self.assertEqual(self.win.reports.list()[0]["kind"], kind)

    def test_reports_page_lists_opens_deletes_and_exports(self):
        self.win._analyze_input(override_term="serendipity")
        self.app.processEvents()
        self.win.results_page._report_btn.click()
        self.win.results_page._popover.note_edit.setText("wrong example")
        self.win.results_page._popover.save_btn.click()
        self.win._analyze_input(override_term="break the ice")
        self.app.processEvents()
        self.win.results_page._report_btn.click()
        self.win.results_page._popover.close()

        drawer = self.win.right_drawer
        drawer.show_page(PAGE_REPORTS)
        page = drawer.reports_page
        self.assertEqual(page.report_list.count(), 2)
        row0 = page.report_list.itemWidget(page.report_list.item(0))
        self.assertEqual(row0.term_lbl.text(), "break the ice")  # newest first
        row1 = page.report_list.itemWidget(page.report_list.item(1))
        self.assertIn("wrong example", row1.meta_lbl.toolTip())

        # Open re-shows the reported entry as reported, as a step of its own.
        self.win.go_home()
        page._on_item_clicked(page.report_list.item(1))
        self.assertIs(self.win.stack.currentWidget(), self.win.results_page)
        self.assertEqual(self.win.current_term, "serendipity")
        self.assertIn("Finding something good by chance.", " ".join(card_texts(self.win)))

        # Export writes one zip: README, reports.json and a PNG per report.
        path = os.path.join(tempfile.mkdtemp(), "reports.zip")
        with mock.patch.object(page, "_choose_export_path", return_value=path):
            page.export_reports()
        self.assertIn("Exported 2 reports", page.status_lbl.text())
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            self.assertIn("README.md", names)
            self.assertIn("reports.json", names)
            pngs = [n for n in names if n.startswith("snapshots/")]
            self.assertEqual(len(pngs), 2)
            self.assertTrue(all(z.read(n).startswith(PNG_MAGIC) for n in pngs))
            doc = json.loads(z.read("reports.json"))
        self.assertEqual([r["term"] for r in doc["reports"]], ["serendipity", "break the ice"])  # oldest first
        self.assertEqual(doc["reports"][0]["note"], "wrong example")

        # Deleting asks first.
        page._ask_delete(self.win.reports.list()[0]["id"])
        self.assertTrue(page.confirm.isVisible())
        page.confirm.confirm_btn.click()
        self.assertEqual(page.report_list.count(), 1)
        self.assertEqual(len(self.win.reports.list()), 1)

    def test_empty_reports_page_cannot_export(self):
        self.win.right_drawer.show_page(PAGE_REPORTS)
        page = self.win.right_drawer.reports_page
        self.assertFalse(page.export_btn.isEnabled())
        self.assertTrue(page.empty_lbl.isVisible())
        with mock.patch.object(page, "_choose_export_path") as chooser:
            page.export_reports()
        chooser.assert_not_called()

    def test_snapshot_covers_the_whole_page(self):
        from PyQt6.QtGui import QImage
        self.win.results_page.render("serendipity", WORD)
        self.app.processEvents()
        img = QImage.fromData(self.win.results_page.snapshot_png())
        self.assertFalse(img.isNull())
        self.assertEqual(img.height(), self.win.results_page.cards_container.height())

    def test_the_bundle_environment_lists_versions(self):
        env = report_export.environment_info({"theme": "Dark Mode", "secret": "x"})
        self.assertEqual(env["settings"], {"theme": "Dark Mode"})
        self.assertIn("engine_version", env)
        self.assertIn("os", env)
        json.dumps(env)  # serialisable


if __name__ == "__main__":
    unittest.main()
