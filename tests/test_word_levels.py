import json
import os
import sys
import tempfile
import unittest

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lexicon_fixture import use_lexicon  # noqa: E402
import lexical_engine as le  # noqa: E402
import word_levels  # noqa: E402

BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cefr_levels.json")


class FrequencyBandTest(unittest.TestCase):
    def test_band_thresholds_are_per_million_floors(self):
        cases = [(25000, "Very common"), (100, "Very common"), (99.9, "Common"), (10, "Common"),
                 (9.99, "Uncommon"), (1, "Uncommon"), (0.99, "Rare"), (0, "Rare")]
        for per_million, band in cases:
            self.assertEqual(word_levels.frequency_band(per_million), band, per_million)

    def test_unknown_frequency_has_no_band(self):
        self.assertEqual(word_levels.frequency_band(None), "")


class BundledListTest(unittest.TestCase):
    def test_every_level_is_filled_and_no_word_has_two_levels(self):
        with open(BUNDLED, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(tuple(data["words"]), word_levels.LEVELS)
        seen = set()
        for level, words in data["words"].items():
            self.assertGreater(len(words), 500, level)
            self.assertFalse(seen & set(words), level)
            seen |= set(words)
        self.assertTrue(all(w == w.lower() for w in seen))

    def test_sources_and_licences_are_recorded_in_the_file(self):
        with open(BUNDLED, encoding="utf-8") as f:
            sources = json.load(f)["_meta"]["sources"]
        text = json.dumps(sources)
        self.assertIn("Compiled by Yukio Tono, Tokyo University of Foreign Studies", text)
        self.assertIn("CC BY-SA 4.0", text)

    def test_levels_of_known_words(self):
        for word, level in (("the", "A1"), ("bright", "A1"), ("abandon", "B1"), ("timid", "C1")):
            self.assertEqual(word_levels.cefr_level(BUNDLED, word), level, word)

    def test_slash_variants_and_case_and_unknown_words(self):
        self.assertEqual(word_levels.cefr_level(BUNDLED, "Colour"), "A1")  # listed as "color/colour"
        self.assertEqual(word_levels.cefr_level(BUNDLED, "  Check-In  "), "B1")
        self.assertIsNone(word_levels.cefr_level(BUNDLED, "serendipity"))

    def test_lemma_is_tried_when_the_form_is_not_listed(self):
        self.assertIsNone(word_levels.cefr_level(BUNDLED, "walks"))
        self.assertEqual(word_levels.cefr_level(BUNDLED, "walks", ["walk"]), "A1")
        self.assertEqual(word_levels.cefr_level(BUNDLED, "bright", ["dim"]), "A1")  # the term itself wins

    def test_missing_or_broken_file_gives_no_level(self):
        self.assertIsNone(word_levels.cefr_level(os.path.join(_DATA, "nope.json"), "the"))
        broken = os.path.join(_DATA, "broken.json")
        with open(broken, "w") as f:
            f.write("{not json")
        self.assertIsNone(word_levels.cefr_level(broken, "the"))

    def test_the_list_is_read_once_and_only_when_needed(self):
        path = os.path.join(_DATA, "lazy.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"words": {"A1": ["alpha"], "B2": ["beta"]}}, f)
        self.assertNotIn(path, word_levels._CACHE)
        self.assertEqual(word_levels.cefr_level(path, "beta"), "B2")
        os.remove(path)
        self.assertEqual(word_levels.cefr_level(path, "alpha"), "A1")  # served from memory


class EngineLevelTest(unittest.TestCase):
    def test_engine_looks_up_the_bundled_list_and_takes_the_lemmas_level(self):
        use_lexicon(self)
        self.assertEqual(le.lookup_cefr_level("bright"), "A1")
        self.assertEqual(le.lookup_cefr_level("Ran"), "A1")      # run: Wiktionary's lemma
        self.assertEqual(le.lookup_cefr_level("banks"), "A1")    # bank
        self.assertEqual(le.lookup_cefr_level("possessing"), "B1")  # possess: Wiktionary lists the form
        self.assertEqual(le.lookup_cefr_level("dialling"), "B1")  # dial, by a suffix rule the dictionary confirms
        self.assertEqual(le.lookup_cefr_level("serendipity"), "")

    def test_without_a_dictionary_only_exact_words_have_a_level(self):
        use_lexicon(self, None)
        self.assertEqual(le.lookup_cefr_level("bright"), "A1")
        self.assertEqual(le.lookup_cefr_level("ran"), "")

    def test_bundled_file_sits_where_the_engine_looks(self):
        self.assertEqual(os.path.abspath(le.CEFR_LEVELS_FILE), BUNDLED)


if __name__ == "__main__":
    unittest.main()
