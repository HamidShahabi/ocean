import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cloze  # noqa: E402


def blank(sentence, term, comparative=False):
    result = cloze.make_cloze(sentence, term, comparative)
    return result and (result.blanked, result.forms)


class FormsTest(unittest.TestCase):
    def test_module_has_no_heavy_dependencies(self):
        with open(cloze.__file__, encoding="utf-8") as f:
            source = f.read()
        for name in ("PyQt", "nltk", "lexical_engine"):
            self.assertNotIn("import " + name, source)

    def test_regular_endings(self):
        self.assertTrue({"walks", "walked", "walking"} <= cloze.word_forms("walk"))
        self.assertTrue({"watches", "watched", "watching"} <= cloze.word_forms("watch"))
        self.assertTrue({"studies", "studied", "studying"} <= cloze.word_forms("study"))
        self.assertTrue({"plays", "played", "playing"} <= cloze.word_forms("play"))
        self.assertTrue({"tied", "tying", "ties"} <= cloze.word_forms("tie"))

    def test_doubled_consonant_and_dropped_e(self):
        self.assertTrue({"running", "runs", "ran"} <= cloze.word_forms("run"))
        self.assertTrue({"stopped", "stopping"} <= cloze.word_forms("stop"))
        self.assertTrue({"making", "makes", "made"} <= cloze.word_forms("make"))
        self.assertTrue({"hoped", "hoping"} <= cloze.word_forms("hope"))
        self.assertTrue({"panicked", "panicking"} <= cloze.word_forms("panic"))
        self.assertNotIn("fixxing", cloze.word_forms("fix"))
        self.assertIn("fixes", cloze.word_forms("fix"))

    def test_irregulars_and_nouns(self):
        self.assertTrue({"broke", "broken", "breaks", "breaking"} <= cloze.word_forms("break"))
        self.assertTrue({"was", "were", "been", "being"} <= cloze.word_forms("be"))
        self.assertIn("men", cloze.word_forms("man"))
        self.assertTrue({"wolves", "wolf"} <= cloze.word_forms("wolf"))
        self.assertIn("children", cloze.word_forms("child"))

    def test_saved_inflected_word_finds_its_family(self):
        self.assertIn("break", cloze.word_forms("broke"))
        self.assertIn("running", cloze.word_forms("ran"))

    def test_comparatives_only_when_asked(self):
        self.assertNotIn("bigger", cloze.word_forms("big"))
        self.assertTrue({"bigger", "biggest"} <= cloze.word_forms("big", comparative=True))
        self.assertTrue({"happier", "happiest"} <= cloze.word_forms("happy", comparative=True))
        self.assertTrue({"nicer", "nicest"} <= cloze.word_forms("nice", comparative=True))
        self.assertTrue({"better", "best"} <= cloze.word_forms("good"))

    def test_short_words_do_not_generate_rule_forms(self):
        self.assertEqual(cloze.regular_forms("as"), frozenset())
        self.assertNotIn("is", cloze.word_forms("i"))


class ClozeTest(unittest.TestCase):
    def test_run_blanks_all_forms(self):
        self.assertEqual(blank("She ran home, and he runs daily, but they were running late.", "run"),
                         ("She _____ home, and he _____ daily, but they were _____ late.", ("ran", "runs", "running")))

    def test_phrase_with_irregular_verb(self):
        self.assertEqual(blank("At the party, Tom broke the ice with a joke.", "break the ice"),
                         ("At the party, Tom _____ with a joke.", ("broke the ice",)))
        self.assertEqual(blank("Let me break the ice for you.", "break the ice")[1], ("break the ice",))
        self.assertEqual(blank("He is breaking the ice now.", "break the ice")[1], ("breaking the ice",))

    def test_case_is_ignored_and_original_text_kept(self):
        self.assertEqual(blank("Running is good for you.", "run"), ("_____ is good for you.", ("Running",)))
        self.assertEqual(blank("He RAN away from home.", "RUN"), ("He _____ away from home.", ("RAN",)))

    def test_whole_words_only(self):
        self.assertIsNone(blank("The runner was a hotelier on the sunrise side.", "run"))
        self.assertIsNone(blank("Her run-down house was rather cold.", "run"))
        self.assertIsNone(blank("He studied the cat carefully.", "cat dog"))

    def test_missing_word_gives_none(self):
        self.assertIsNone(blank("This sentence has nothing relevant in it.", "run"))
        self.assertIsNone(blank("", "run"))
        self.assertIsNone(blank("Some sentence here.", ""))

    def test_sentence_with_nothing_left_is_unsuitable(self):
        self.assertIsNone(blank("Run!", "run"))
        self.assertIsNone(blank("Break the ice.", "break the ice"))

    def test_distinct_forms_only(self):
        self.assertEqual(blank("Run, run, run to the hills.", "run"), ("_____, _____, _____ to the hills.", ("Run",)))
        self.assertEqual(blank("Run when he runs.", "run")[1], ("Run", "runs"))

    def test_comparatives_need_the_adjective_flag(self):
        self.assertIsNone(blank("This box is bigger than that one.", "big"))
        self.assertEqual(blank("This box is bigger than that one.", "big", comparative=True)[0],
                         "This box is _____ than that one.")

    def test_curly_apostrophe_and_hyphen_neighbours(self):
        self.assertEqual(blank("They’re not here yet, so wait.", "wait")[0], "They’re not here yet, so _____.")
        self.assertIsNone(blank("A well-known author wrote it down.", "known"))


if __name__ == "__main__":
    unittest.main()
