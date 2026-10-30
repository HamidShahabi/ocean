"""The dataset import tool (tools/build_lexicon_db.py) and the read side (lexicon_db.LexiconDB)."""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import KAIKKI_ROWS, fixture_db, write_dumps  # noqa: E402
import build_lexicon_db as build  # noqa: E402
import lexicon_db  # noqa: E402


class KeyTest(unittest.TestCase):
    def test_text_key(self):
        self.assertEqual(lexicon_db.text_key("  Break a LEG! "), "break a leg")
        self.assertEqual(lexicon_db.text_key("Don’t stop—now."), "don't stop now")
        self.assertEqual(lexicon_db.text_key("'well-known'"), "well-known")
        self.assertEqual(lexicon_db.text_key("snake_case"), "snake case")

    def test_persian_cleaning(self):
        self.assertEqual(lexicon_db.clean_persian("بانْک  كيف"), "بانک کیف")
        self.assertEqual(lexicon_db.persian_key("می‌روم؟"), "می روم")


class KaikkiMappingTest(unittest.TestCase):
    def test_entry_fields(self):
        entry, forms, fas = build.kaikki_entry(KAIKKI_ROWS[0])
        self.assertEqual((entry["word"], entry["pos"], entry["ipa"]), ("bank", "Noun", "/bæŋk/"))  # US, phonemic
        self.assertEqual([s["def"] for s in entry["senses"]],
                         ["An institution where one can place and borrow money.", "Sloping land beside a river."])  # obsolete dropped
        self.assertEqual(entry["senses"][0]["example"], "I keep my savings in a bank.")  # a usage example beats a quotation
        self.assertEqual(entry["senses"][1]["example"], "")  # a collocation list is not an example...
        self.assertEqual(entry["collocations"], ["blood bank", "data bank"])  # ...it becomes collocations
        self.assertEqual(entry["derived"], ["bank account", "bankroll"])
        self.assertEqual(forms, [])
        self.assertEqual([(t["sense"], t["fa"]) for t in fas],
                         [("institution", "بانک"), ("edge of river", "کرانه"), ("edge of river", "ساحل")])  # no Dari, no German

    def test_a_quotation_that_is_only_its_citation_is_no_example(self):
        self.assertEqual(build.pick_example([{"type": "quotation", "text": "2007, Erin McKean, speech at TED Serendipity"}]), "")
        self.assertEqual(build.pick_example([{"type": "example", "text": "2007, Erin McKean, speech at TED Serendipity"}]), "")
        self.assertEqual(build.pick_example([{"type": "quotation", "text": "It was pure serendipity that we met."}]),
                         "It was pure serendipity that we met.")

    def test_usage_labels_prefix_the_definition(self):
        entry, _, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "bright"))
        self.assertEqual(entry["senses"][0]["def"], "(informal) Giving off a lot of light.")

    def test_form_of_entries_only_point_at_their_lemma(self):
        entry, forms, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "banks"))
        self.assertIsNone(entry)
        self.assertEqual(forms, [("banks", "bank")])

    def test_skipped_rows(self):
        for word in ("-ness", "maison"):
            self.assertEqual(build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == word)), (None, [], []))


class UrbanFilterTest(unittest.TestCase):
    def test_only_well_voted_entries_are_kept(self):
        self.assertEqual(build.URBAN_RATIO, 2)
        self.assertTrue(build.urban_keep(2, 1))
        self.assertTrue(build.urban_keep(10, 0))
        self.assertFalse(build.urban_keep(5, 3))
        self.assertFalse(build.urban_keep(0, 0))  # nobody vouched for it

    def test_json_lines_dump(self):
        path = os.path.join(tempfile.mkdtemp(), "ud.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"word": "sus", "definition": "Suspicious.", "upvotes": 9, "downvotes": 1}) + "\n")
        db = sqlite3.connect(":memory:")
        self.assertEqual(build.import_urban(db, build.read_urban_rows(path)), 1)


class BuiltDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = lexicon_db.LexiconDB(fixture_db())

    def test_meta_records_the_build(self):
        with sqlite3.connect(fixture_db()) as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["kaikki_entries"], "9")  # form-of rows, a suffix and a French word make no entry
        self.assertEqual(meta["tatoeba_pairs"], "4")  # the pair without Persian was dropped
        self.assertIn("built_at", meta)

    def test_entries_in_dictionary_order(self):
        self.assertEqual([(e["word"], e["pos"]) for e in self.db.entries("bank")], [("bank", "Noun"), ("bank", "Verb")])
        self.assertTrue(self.db.has_entry("Break the ice"))
        self.assertFalse(self.db.has_entry("zeitgeist"))

    def test_an_inflected_form_gets_its_lemma_before_a_name(self):
        self.assertEqual([(e["word"], e["pos"]) for e in self.db.entries("banks")],
                         [("bank", "Noun"), ("bank", "Verb"), ("Banks", "Proper noun")])
        self.assertEqual([e["word"] for e in self.db.entries("ran")], ["run", "run"])

    def test_persian_translations(self):
        self.assertEqual([t["fa"] for t in self.db.persian("bank")], ["بانک", "کرانه", "ساحل", "سپردن"])
        self.assertEqual(self.db.english_for_persian("بانک"), ["bank"])

    def test_slang_keeps_the_well_voted_definition_without_link_brackets(self):
        self.assertEqual(self.db.slang("rizz"), [{"def": "Charisma, especially in flirting.", "example": "He has so much rizz."}])
        self.assertEqual(len(self.db.slang("yeet")), 1)

    def test_whole_text_translations(self):
        self.assertEqual(self.db.translation_pair("break a leg"), ("موفق باشی!", "opensubtitles"))  # the agreed rendering
        self.assertEqual(self.db.translation_pair("I love you"), ("دوستت دارم.", "tatoeba"))  # Tatoeba beats subtitles
        self.assertEqual(self.db.translation_pair("Good morning."), ("", ""))  # one subtitle line is not enough
        self.assertEqual(self.db.translation_pair("This line has far too many words to be kept as a phrase in the database at all."), ("", ""))

    def test_examples_containing_a_word_tatoeba_first(self):
        ex = self.db.examples("bank", limit=3)
        self.assertEqual([e["corpus"] for e in ex], ["tatoeba", "tatoeba", "opensubtitles"])
        self.assertEqual(ex[0]["en"], "I'm going to the bank.")
        self.assertEqual(self.db.examples("river bank", limit=5)[0]["fa"], "ساحل رودخانه بعد از باران گل آلود بود.")

    def test_subtitle_word_frequencies(self):
        self.assertAlmostEqual(self.db.frequency("break"), 3 / self._subtitle_tokens() * 1_000_000, places=2)
        self.assertIsNone(self.db.frequency("bank"))  # fewer than three occurrences: not counted
        self.assertIsNone(self.db.frequency("zeitgeist"))

    @staticmethod
    def _subtitle_tokens():
        from lexicon_fixture import SUBTITLES
        return sum(len(build._TOKEN.findall(en.lower())) for en, _ in SUBTITLES)

    def test_suggestions_and_spelling(self):
        self.assertEqual(self.db.suggestions("ba"), ["bank", "Banks"])
        self.assertEqual(self.db.suggestions("b"), [])
        self.assertEqual(self.db.close_matches("brigt"), ["bright"])
        self.assertEqual(self.db.close_matches("hevy"), ["heavy"])  # no shared trigram: found by prefix
        self.assertEqual(self.db.close_matches("xyzzy"), [])


class BuildDriverTest(unittest.TestCase):
    def test_build_replaces_the_database_only_when_complete(self):
        folder = tempfile.mkdtemp()
        out = os.path.join(folder, "ocean_lexicon.db")
        with open(out, "w") as f:
            f.write("old")
        with self.assertRaises(FileNotFoundError):
            build.build(out, kaikki=os.path.join(folder, "missing.jsonl"))
        with open(out) as f:
            self.assertEqual(f.read(), "old")  # a failed build leaves the previous database alone
        dumps = write_dumps(folder)
        with contextlib.redirect_stdout(io.StringIO()):
            stats = build.build(out, tatoeba_dir=dumps["tatoeba_dir"])
        self.assertEqual(stats, {"tatoeba_pairs": 4})
        self.assertFalse(os.path.exists(out + ".building"))
        db = lexicon_db.LexiconDB(out)
        self.assertTrue(db.has("parallel"))
        self.assertFalse(db.has("slang"))
        self.assertEqual(db.entries("bank"), [])  # a dataset left out is simply absent

    def test_missing_or_broken_database_answers_empty(self):
        missing = lexicon_db.LexiconDB(os.path.join(tempfile.mkdtemp(), "none.db"))
        self.assertFalse(missing.available())
        self.assertEqual((missing.entries("bank"), missing.translation_pair("hi"), missing.suggestions("ba")), ([], ("", ""), []))
        broken_path = os.path.join(tempfile.mkdtemp(), "broken.db")
        with open(broken_path, "wb") as f:
            f.write(b"this is not a database" * 100)
        with self.assertLogs("ocean.lexicon", "ERROR"):
            self.assertEqual(lexicon_db.LexiconDB(broken_path).slang("rizz"), [])


if __name__ == "__main__":
    unittest.main()
