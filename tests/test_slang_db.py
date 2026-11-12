"""tools/build_slang_db.py: choosing the slang entries, fetching their examples (never from the network here: a fake
API answers), resuming, and the file it writes being what build_lexicon_db.py --slang-db reads."""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import UD_API, write_ud_dump  # noqa: E402
import build_lexicon_db  # noqa: E402
import build_slang_db as slang  # noqa: E402
import lexicon_db  # noqa: E402


def fake_api(calls):
    def fetch(defid):
        calls.append(defid)
        return UD_API.get(defid)
    return fetch


class SelectTest(unittest.TestCase):
    def setUp(self):
        self.rows = {r[1].lower(): r for r in slang.select_candidates(write_ud_dump(tempfile.mkdtemp()), 100)}

    def test_best_voted_clean_definition_per_term_ranked_by_thumbs_score(self):
        ranked = [r[1] for r in slang.select_candidates(write_ud_dump(tempfile.mkdtemp()), 100)]
        self.assertEqual(ranked, ["slay", "rizz", "yeet", "no cap", "vibe check"])
        self.assertEqual(self.rows["rizz"][0], 101)  # the better voted of the two rizz definitions
        self.assertEqual(self.rows["rizz"][2], "Charisma, especially in flirting. Used a lot online.")  # link brackets gone

    def test_count_keeps_only_the_top_terms(self):
        self.assertEqual([r[1] for r in slang.select_candidates(write_ud_dump(tempfile.mkdtemp()), 2)], ["slay", "rizz"])

    def test_old_sub_definitions_and_long_definitions_are_cut(self):
        self.assertEqual(self.rows["yeet"][2], "To throw something with force.")  # ";;" starts a second definition
        self.assertLessEqual(len(self.rows["slay"][2]), slang.MAX_DEFINITION)
        self.assertTrue(self.rows["slay"][2].endswith("."))

    def test_unwanted_entries_are_dropped(self):
        for term in ("bruh", "hot take", "mom joke", "sus", "jane doe", "bigot", "fuckery"):
            self.assertNotIn(term, self.rows, term)  # few votes, too many down votes, joke, crude, a name, a slur, vulgar term

    def test_examples_are_short_clean_and_show_the_term(self):
        self.assertEqual(slang.clean_example("He has so much rizz.\r\nSecond paragraph.", "rizz"), "He has so much rizz.")
        self.assertEqual(slang.clean_example("Totally unrelated.", "slay"), "")
        self.assertEqual(slang.clean_example("Slay what the fuck.", "slay"), "")
        self.assertEqual(slang.clean_example("", "slay"), "")


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.csv = write_ud_dump(self.folder)
        self.out = os.path.join(self.folder, "ocean_slang.db")
        self.work = os.path.join(self.folder, "work.db")

    def build(self, calls, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return slang.build(self.csv, self.out, self.work, delay=0, fetcher=fake_api(calls), sleep=lambda s: None, **kw)

    def test_builds_the_table_the_lexicon_import_reads(self):
        calls = []
        self.assertEqual(self.build(calls), 5)
        self.assertEqual(sorted(calls), [101, 103, 104, 105, 113])
        rows = sqlite3.connect(self.out).execute("SELECT word, example, up_votes, down_votes FROM slang ORDER BY rowid").fetchall()
        self.assertEqual(rows[0], ("slay", "", 2000, 300))  # the fetched example did not show the term
        self.assertEqual(rows[1], ("rizz", "He has so much rizz.", 900, 100))
        self.assertEqual(rows[4], ("vibe check", "", 350, 15))  # a definition gone from the service: no example, still kept
        lexicon = os.path.join(self.folder, "ocean_lexicon.db")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build_lexicon_db.build(lexicon, slang_db=self.out), {"urban_entries": 5})
        found = lexicon_db.LexiconDB(lexicon).slang("RIZZ")
        self.assertEqual((found[0]["def"], found[0]["example"], found[0]["up"]), ("Charisma, especially in flirting. Used a lot online.",
                                                                                   "He has so much rizz.", 900))

    def test_a_stopped_run_resumes_and_never_asks_twice(self):
        calls = []
        def flaky(defid):
            if defid == 104:
                raise slang.Throttled("HTTP 429")
            return fake_api(calls)(defid)
        with contextlib.redirect_stdout(io.StringIO()):
            first = slang.build(self.csv, self.out, self.work, delay=0, fetcher=flaky, sleep=lambda s: None)
        self.assertEqual(first, -1)  # gave up after repeated failures: nothing written yet
        self.assertFalse(os.path.exists(self.out))
        calls.clear()
        self.assertEqual(self.build(calls), 5)  
        self.assertEqual(calls, [104, 113])  # only what the first run did not get (101, 103 and 105 were fetched)

    def test_failures_back_off_before_giving_up(self):
        waits = []
        work = sqlite3.connect(":memory:")
        work.executescript(slang.WORK_SCHEMA)
        work.execute("INSERT INTO cand (defid, word, definition, up, down) VALUES (1, 'rizz', 'x', 9, 1)")
        def refuse(defid):
            raise slang.Throttled("HTTP 429")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(slang.fetch_examples(work, refuse, 0, waits.append, max_failures=4), 1)
        self.assertEqual(waits, [10.0, 20.0, 40.0])


if __name__ == "__main__":
    unittest.main()
