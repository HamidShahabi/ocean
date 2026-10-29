import os
import sys
import tempfile
import unittest
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import use_lexicon  # noqa: E402
import lexical_engine as le  # noqa: E402


class SentenceLookupTest(unittest.TestCase):
    def _lookup(self, sentence):
        """Run a sentence lookup; return (result, texts handed to the translator with the model allowed)."""
        use_lexicon(self)
        sent = []

        def fake_translate(text, is_short=False, use_slm=False):
            if use_slm:
                sent.append(text)
            return "ترجمه", le.FA_SOURCE_SLM

        for p in (
            mock.patch.object(le, "translate_with_source", side_effect=fake_translate),
            mock.patch.object(le, "sentence_to_ipa", return_value=""),
            mock.patch.object(le, "wn", None),  # WordNet may be missing and is not what is under test
            mock.patch.object(le, "LEMMATIZER", None),
        ):
            p.start()
            self.addCleanup(p.stop)
        return le.analyze_with_dictionaries(sentence), sent

    def test_the_sentence_is_translated_whole_and_unchanged(self):
        result, sent = self._lookup("We had to break the ice at the bank meeting.")
        self.assertEqual(sent, ["We had to break the ice at the bank meeting."])  # whole, nothing substituted into it;
        # the model is asked for the sentence only, not for each term of the breakdown
        self.assertEqual((result["persian_equivalent"], result["persian_source"]), ("ترجمه", le.FA_SOURCE_SLM))
        self.assertTrue(result["is_sentence"])

    def test_dictionary_phrases_and_words_are_broken_down(self):
        result, _ = self._lookup("We had to break the ice at the bank meeting.")
        terms = [k["term"] for k in result["sentence_breakdown"]]
        self.assertEqual(terms, ["break the ice", "bank"])  # "meeting" is not in the sample dictionary
        self.assertEqual(result["sentence_breakdown"][0]["def"], "(idiomatic) To start a conversation with strangers.")

    def test_inflected_words_are_explained_by_their_lemma(self):
        result, _ = self._lookup("They ran to the banks of the river.")
        first = result["sentence_breakdown"][0]
        self.assertEqual((first["term"], first["def"]), ("ran", "To move swiftly on foot."))  # run's sense


if __name__ == "__main__":
    unittest.main()
