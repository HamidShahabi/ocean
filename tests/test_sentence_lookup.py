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
import lexical_engine as le  # noqa: E402

SENTENCE = "The actor used spirit gum to attach the fake beard before going on stage."


class SentenceCorrectionTest(unittest.TestCase):
    def _translate(self, sentence, lexicon=None):
        """Run a sentence lookup offline; return (result, texts handed to the translator)."""
        sent = []

        def fake_translate(text, is_short=False):
            sent.append(text)
            return "ترجمه"

        patches = [
            mock.patch.object(le, "translate_via_clients5", side_effect=fake_translate),
            mock.patch.object(le, "translate_single_string", side_effect=fake_translate),
            mock.patch.object(le, "sentence_to_ipa", return_value=""),
            mock.patch.object(le, "wn", None),  # WordNet may be missing and is not what is under test
            mock.patch.dict(le.USER_MEMORY, {"overrides": {}}),
        ]
        if lexicon is not None:
            patches.append(mock.patch.object(le, "OFFLINE_LEXICON", lexicon))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return le.analyze_with_dictionaries(sentence), sent

    def test_bundled_lexicon_entry_is_applied_inside_sentence(self):
        result, sent = self._translate(SENTENCE)
        self.assertIn("theatrical makeup adhesive", sent[0])
        self.assertNotIn("spirit gum", sent[0])
        self.assertEqual(result["sentence_breakdown"][0]["term"], "spirit gum")

    def test_data_defined_correction_applies_to_any_term(self):
        lexicon = {"idioms": {"foo bar": {"fa": "x", "meaning_en": "a replacement phrase", "def": "d"}}}
        _, sent = self._translate("We all agreed that foo bar was needed here.", lexicon)
        self.assertEqual(sent[0], "We all agreed that a replacement phrase was needed here.")

    def test_sentence_without_lexicon_terms_is_sent_unchanged(self):
        _, sent = self._translate("The quick brown fox jumps over the dog.", {"idioms": {}})
        self.assertEqual(sent[0], "The quick brown fox jumps over the dog.")


if __name__ == "__main__":
    unittest.main()
