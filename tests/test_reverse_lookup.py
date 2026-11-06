import os
import sys
import tempfile
import unittest
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import use_lexicon  # noqa: E402
import lexical_engine as le  # noqa: E402

FLEETING = "زودگذر"            # زودگذر
BANK = "بانک"  # the fixture dictionary translates "bank" so


class DetectionAndNormalisationTest(unittest.TestCase):
    def test_is_persian_text(self):
        self.assertTrue(le.is_persian_text(FLEETING))
        self.assertTrue(le.is_persian_text(f"ok {FLEETING}"))  # majority script wins
        self.assertFalse(le.is_persian_text("hello"))
        self.assertFalse(le.is_persian_text("123"))

    def test_fa_key_unifies_arabic_letters_and_strips_noise(self):
        self.assertEqual(le.fa_key(f"  {FLEETING}؟ "), FLEETING)
        self.assertEqual(le.fa_key("كيف"), "کیف")       # Arabic kaf/yeh -> Persian
        self.assertEqual(le.fa_key("می‌روم"), "می روم")  # ZWNJ
        self.assertEqual(le.fa_key("كَتاب"), "کتاب")  # diacritic removed


class LocalReverseTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)

    def test_dictionary_translation_and_user_override_match_per_part(self):
        self.assertEqual(le.reverse_lookup_local(BANK), ["bank"])
        self.assertEqual(le.reverse_lookup_local("بانْك"), ["bank"])  # diacritic and Arabic kaf
        with mock.patch.dict(le.USER_MEMORY, {"overrides": {"ephemeral": f"گذرا ، {FLEETING}"}}):
            self.assertEqual(le.reverse_lookup_local(FLEETING)[0], "ephemeral")
        self.assertEqual(le.reverse_lookup_local("   "), [])


class GtxParsingTest(unittest.TestCase):
    def test_translation_and_candidates_by_part_of_speech(self):
        data = [[["fleeting", FLEETING, None, None, 10]], [
            ["adjective", ["fleeting", "transient", "ephemeral", "momentary", "passing", "brief", "seventh"], []],
            ["noun", ["transience"], []],
        ]]
        translation, cands = le.parse_gtx_reverse(data)
        self.assertEqual(translation, "fleeting")
        self.assertEqual([c["term"] for c in cands], ["fleeting", "transient", "ephemeral", "momentary", "passing",
                                                     "brief", "transience"])  # max 6 per part of speech
        self.assertEqual(cands[-1]["pos"], "noun")

    def test_sentence_without_dictionary_block(self):
        self.assertEqual(le.parse_gtx_reverse([[["The weather ", "x"], ["is good.", "y"]]]), ("The weather is good.", []))


class AnalyzePersianTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)

    def _run(self, text, gtx=("", []), c5="", offline=""):
        with mock.patch.object(le, "_google_fa_to_en", return_value=gtx), \
             mock.patch.object(le, "_clients5", return_value=c5), \
             mock.patch.object(le, "_ct2_translate", return_value=offline) as ct2:
            return le.analyze_persian(text), ct2

    def test_word_merges_sources_local_first_without_duplicates(self):
        d, ct2 = self._run(BANK, gtx=("Bank", [{"term": "bank", "pos": "noun"}, {"term": "depository", "pos": "noun"}]))
        self.assertTrue(d["is_reverse"])
        self.assertFalse(d["is_sentence"])
        self.assertEqual(d["english_translation"], "bank")      # the dictionary's word beats machine output
        self.assertEqual(d["machine_translation"], "")  # same word, different case: nothing to contrast
        self.assertEqual([c["term"] for c in d["candidates"]], ["bank", "depository"])
        ct2.assert_not_called()

    def test_falls_back_to_clients5_then_offline(self):
        d, _ = self._run(FLEETING, c5="fleeting")
        self.assertEqual((d["english_translation"], d["translation_offline"]), ("fleeting", False))
        self.assertEqual(d["machine_translation"], "")  # no curated match: nothing to contrast
        d, _ = self._run(FLEETING, offline="transient")
        self.assertEqual((d["english_translation"], d["translation_offline"]), ("transient", True))
        self.assertEqual(d["candidates"], [])  # offline machine output is never offered as a word

    def test_sentence_translation_is_not_offered_as_a_word(self):
        sentence = "امروز هوا خیلی خوب است."
        d, _ = self._run(sentence, c5="The weather is very good today.")
        self.assertTrue(d["is_sentence"])
        self.assertEqual(d["candidates"], [])

    def test_engine_entry_point_routes_persian_input(self):
        with mock.patch.object(le, "analyze_persian", return_value={"is_reverse": True}) as rev:
            self.assertEqual(le.analyze_with_dictionaries(f" {FLEETING} "), {"is_reverse": True})
            rev.assert_called_once_with(FLEETING)


if __name__ == "__main__":
    unittest.main()
