import functools
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reader  # noqa: E402
import word_levels  # noqa: E402

BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cefr_levels.json")

# A tiny level list, so the tests do not depend on what the bundled one says about a word.
LEVELS = {"run": "A1", "home": "A1", "the": "A1", "she": "A1", "stop": "A1", "make": "A1", "child": "A1",
          "study": "A2", "break": "B1", "ice": "A2", "well": "A1", "known": "B1", "mother-in-law": "B2",
          "resilient": "C1", "ephemeral": "C2", "do": "A1", "can": "A1", "will": "A1", "it": "A1",
          "new": "A1", "its": "A1", "news": "A2", "leaf": "B2", "leave": "A2"}


def level_of(word, lemmas=()):
    return next((LEVELS[c] for c in (word, *lemmas) if c in LEVELS), None)


def analyse(text, saved=(), min_level="B2"):
    return reader.analyse(text, reader.SavedWords(saved), level_of, min_level)


def marks(text, saved=(), min_level="B2"):
    return {m.token.text: m.mark for m in analyse(text, saved, min_level)}


class TokeniseTest(unittest.TestCase):
    def test_words_with_offsets(self):
        text = "Hello, world!  Again."
        toks = reader.tokenise(text)
        self.assertEqual([t.text for t in toks], ["Hello", "world", "Again"])
        for t in toks:
            self.assertEqual(text[t.start:t.end], t.text)
        self.assertEqual([(t.start, t.end) for t in toks], [(0, 5), (7, 12), (15, 20)])

    def test_apostrophes_and_hyphens_stay_inside_a_word(self):
        toks = reader.tokenise("Don't use the well-known mother-in-law's rock'n'roll, o’clock.")
        self.assertEqual([t.text for t in toks],
                         ["Don't", "use", "the", "well-known", "mother-in-law's", "rock'n'roll", "o’clock"])

    def test_dangling_apostrophes_hyphens_digits_and_dashes(self):
        toks = reader.tokenise("dogs' bones -- 42 b2b mp3 -wow- 'quoted' a-b")
        self.assertEqual([t.text for t in toks], ["dogs", "bones", "b", "b", "mp", "wow", "quoted", "a-b"])

    def test_non_english_letters_are_words_too(self):
        self.assertEqual([t.text for t in reader.tokenise("café کتاب")],
                         ["café", "کتاب"])

    def test_empty_and_wordless_texts(self):
        self.assertEqual(reader.tokenise(""), [])
        self.assertEqual(reader.tokenise("123 ... !!"), [])

    def test_contractions_and_possessives_take_the_base_word(self):
        text = "Don't can't won’t isn't it's I'm they'll We've he'd John's boys' o'clock ain't"
        base = {t.text: t.base for t in reader.tokenise(text)}
        self.assertEqual(base["Don't"], "do")
        self.assertEqual(base["can't"], "can")
        self.assertEqual(base["won’t"], "will")
        self.assertEqual(base["isn't"], "is")
        self.assertEqual(base["it's"], "it")
        self.assertEqual(base["I'm"], "i")
        self.assertEqual(base["they'll"], "they")
        self.assertEqual(base["We've"], "we")
        self.assertEqual(base["he'd"], "he")
        self.assertEqual(base["John's"], "john")
        self.assertEqual(base["boys"], "boys")
        self.assertEqual(base["o'clock"], "o'clock")
        self.assertEqual(base["ain't"], "ain't")

    def test_normalise_text_unifies_line_breaks_and_drops_control_characters(self):
        self.assertEqual(reader.normalise_text("a\r\nb\rc d e\x00\x07f\tg"), "a\nb\nc\nd\nef\tg")

    def test_a_very_long_text_is_cut_at_a_word(self):
        text = "word " * (reader.MAX_CHARS // 5 + 100)
        cut, clipped = reader.clip_text(text)
        self.assertTrue(clipped)
        self.assertLessEqual(len(cut), reader.MAX_CHARS)
        self.assertTrue(cut.endswith("word"))
        self.assertEqual(reader.clip_text("short"), ("short", False))


class LemmaTest(unittest.TestCase):
    def test_irregular_and_regular_forms_map_to_the_base_word(self):
        self.assertIn("run", reader.lemma_candidates("ran"))
        self.assertIn("child", reader.lemma_candidates("children"))
        self.assertIn("stop", reader.lemma_candidates("stopped"))
        self.assertIn("study", reader.lemma_candidates("studies"))
        self.assertIn("make", reader.lemma_candidates("making"))
        self.assertIn("go", reader.lemma_candidates("went"))

    def test_every_candidate_really_inflects_to_the_word(self):
        for word in ("running", "leaves", "tried", "boxes", "ties", "dying", "wolves"):
            for cand in reader.lemma_candidates(word):
                self.assertIn(word, reader.cloze.word_forms(cand))

    def test_the_base_word_itself_and_short_words_have_no_candidates(self):
        self.assertEqual(reader.lemma_candidates("run"), [])
        self.assertEqual(reader.lemma_candidates("is"), ["be"])
        self.assertEqual(reader.lemma_candidates("as"), [])

    def test_resolution_prefers_a_listed_base_and_leaves_listed_words_alone(self):
        got = {m.token.text: m.lemma for m in analyse("She ran home, making children stop; its news; the studies; unknownish")}
        self.assertEqual(got["ran"], "run")
        self.assertEqual(got["making"], "make")  # not the non-word "mak"
        self.assertEqual(got["children"], "child")
        self.assertEqual(got["its"], "its")      # listed: must not become "it"
        self.assertEqual(got["news"], "news")    # listed: must not become "new"
        self.assertEqual(got["studies"], "study")
        self.assertEqual(got["unknownish"], "unknownish")


class ClassifyTest(unittest.TestCase):
    def test_saved_above_plain_and_unlisted(self):
        got = marks("She ran home with resilient Zorblax", saved=["home"], min_level="B2")
        self.assertEqual(got, {"She": "plain", "ran": "plain", "home": "saved", "with": "unlisted",
                               "resilient": "above", "Zorblax": "unlisted"})

    def test_the_level_threshold_is_inclusive_and_can_be_off(self):
        text = "stop break known resilient ephemeral"
        self.assertEqual(marks(text, min_level="B1"),
                         {"stop": "plain", "break": "above", "known": "above", "resilient": "above", "ephemeral": "above"})
        self.assertEqual(marks(text, min_level="C2"),
                         {"stop": "plain", "break": "plain", "known": "plain", "resilient": "plain", "ephemeral": "above"})
        self.assertEqual(set(marks(text, min_level=None).values()), {"plain"})
        self.assertEqual(set(marks(text, min_level="A1").values()), {"above"})

    def test_an_inflected_form_takes_the_level_of_its_base_word(self):
        got = {m.token.text: (m.level, m.mark) for m in analyse("studies children", min_level="A2")}
        self.assertEqual(got["studies"], ("A2", "above"))
        self.assertEqual(got["children"], ("A1", "plain"))

    def test_saved_matches_any_form_whichever_form_was_saved(self):
        self.assertEqual(marks("She ran and is running", saved=["run"]),
                         {"She": "plain", "ran": "saved", "and": "unlisted", "is": "unlisted", "running": "saved"})
        self.assertEqual(marks("run runs running", saved=["ran"]), {"run": "saved", "runs": "saved", "running": "saved"})
        saved = analyse("running", saved=["run"])[0]
        self.assertEqual((saved.mark, saved.lemma), ("saved", "run"))  # a click opens the entry you saved

    def test_saved_wins_over_above_level(self):
        self.assertEqual(marks("resilient", saved=["resilient"]), {"resilient": "saved"})

    def test_saved_phrases_mark_every_word_of_the_phrase_in_any_inflection(self):
        got = analyse("They broke the ice. Ice is cold; the break.", saved=["break the ice"])
        self.assertEqual([(m.token.text, m.mark) for m in got[:4]],
                         [("They", "unlisted"), ("broke", "saved"), ("the", "saved"), ("ice", "saved")])
        self.assertEqual(got[4].mark, "plain")   # "Ice" alone is not the saved phrase
        self.assertEqual(got[-1].mark, "plain")  # nor is "break" alone

    def test_sentences_are_not_looked_for_in_the_text(self):
        saved = reader.SavedWords(["she found the book by pure chance yesterday", "ice"])
        self.assertEqual(saved.phrases, {})
        self.assertEqual(saved.key_of("ice"), "ice")

    def test_hyphenated_words_use_their_own_entry_else_their_hardest_part(self):
        got = {m.token.text: (m.level, m.mark) for m in analyse("well-known mother-in-law resilient-minded zorb-ish")}
        self.assertEqual(got["well-known"], ("B1", "plain"))        # no entry of its own: the harder of well/known
        self.assertEqual(got["mother-in-law"], ("B2", "above"))      # its own entry
        self.assertEqual(got["resilient-minded"], ("C1", "above"))
        self.assertEqual(got["zorb-ish"], ("", "unlisted"))

    def test_contractions_are_classified_by_their_base_word(self):
        got = {m.token.text: (m.lemma, m.mark) for m in analyse("Don't can't it's John's", saved=["do"])}
        self.assertEqual(got["Don't"], ("do", "saved"))
        self.assertEqual(got["can't"], ("can", "plain"))
        self.assertEqual(got["it's"], ("it", "plain"))
        self.assertEqual(got["John's"], ("john", "unlisted"))

    def test_curly_apostrophes_match_saved_phrases(self):
        marked = analyse("Don’t break the ice", saved=["break the ice"])
        self.assertEqual([m.mark for m in marked[1:]], ["saved", "saved", "saved"])

    def test_offsets_survive_classification(self):
        text = "She ran\nhome."
        for m in analyse(text):
            self.assertEqual(text[m.token.start:m.token.end], m.token.text)

    def test_count_marks(self):
        counts = reader.count_marks(analyse("run resilient ephemeral Zorblax home", saved=["home"]))
        self.assertEqual(counts, {"saved": 1, "above": 2, "plain": 1, "unlisted": 1})

    def test_with_the_bundled_word_list(self):
        levels = functools.partial(word_levels.cefr_level, BUNDLED)
        marked = reader.analyse("She ran home. The ephemeral, serendipitous moment.", reader.SavedWords(), levels, "C1")
        got = {m.token.text: m for m in marked}
        self.assertEqual((got["ran"].lemma, got["ran"].mark), ("run", "plain"))
        self.assertEqual(got["ephemeral"].mark, "above")
        self.assertEqual(got["serendipitous"].mark, "unlisted")

    def test_five_thousand_words_are_analysed_quickly(self):
        levels = functools.partial(word_levels.cefr_level, BUNDLED)
        text = "She ran home and the children's resilient, well-known mother-in-law stopped making notes. " * 500
        saved = reader.SavedWords(["make", "break the ice", "note"])
        start = time.time()
        marked = reader.analyse(text, saved, levels, "B2")
        self.assertGreater(len(marked), 5000)
        self.assertLess(time.time() - start, 1.0)


class PositionTest(unittest.TestCase):
    def setUp(self):
        self.marked = analyse("She ran, home")  # She 0-3, ran 4-7, home 9-13

    def test_token_at_is_the_word_under_a_character(self):
        self.assertEqual(reader.token_at(self.marked, 0), 0)
        self.assertEqual(reader.token_at(self.marked, 2), 0)
        self.assertIsNone(reader.token_at(self.marked, 3))   # the space
        self.assertEqual(reader.token_at(self.marked, 6), 1)
        self.assertIsNone(reader.token_at(self.marked, 7))   # the comma
        self.assertEqual(reader.token_at(self.marked, 12), 2)
        self.assertIsNone(reader.token_at(self.marked, 13))
        self.assertIsNone(reader.token_at([], 0))

    def test_tokens_in_a_selection(self):
        self.assertEqual(reader.tokens_in(self.marked, 0, 13), (0, 2))
        self.assertEqual(reader.tokens_in(self.marked, 1, 5), (0, 1))     # partial words count
        self.assertEqual(reader.tokens_in(self.marked, 4, 7), (1, 1))
        self.assertIsNone(reader.tokens_in(self.marked, 3, 4))            # only the space
        self.assertIsNone(reader.tokens_in(self.marked, 7, 9))            # ", "
        self.assertEqual(reader.tokens_in(self.marked, 7, 10), (2, 2))
        self.assertIsNone(reader.tokens_in([], 0, 5))

    def test_lookup_term_for_a_word_and_for_a_phrase(self):
        marked = analyse("She ran, quickly home")
        self.assertEqual(reader.lookup_term(marked, 1), "run")
        self.assertEqual(reader.lookup_term(marked, 1, 3), "ran quickly home")

    def test_utf16_positions_account_for_emoji(self):
        text = "a \U0001F600 b \U0001F600\U0001F600 c"
        units = reader.Utf16Map(text)
        for offset in range(len(text) + 1):
            self.assertEqual(units.to_chars(units.to_units(offset)), offset)
        self.assertEqual(units.to_units(text.index("b")), text.index("b") + 1)
        self.assertEqual(units.to_units(text.index("c")), text.index("c") + 3)
        plain = reader.Utf16Map("plain text")
        self.assertEqual((plain.to_units(5), plain.to_chars(5)), (5, 5))


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "reader_text.json")

    def test_text_and_position_round_trip(self):
        self.assertTrue(reader.save_state(self.path, "Café کتاب\nsecond line", 7))
        self.assertEqual(reader.load_state(self.path), ("Café کتاب\nsecond line", 7))
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_missing_corrupt_or_odd_files_give_an_empty_text_or_position(self):
        self.assertEqual(reader.load_state(self.path), ("", 0))
        for content, expected in (("{not json", ("", 0)), ("[1, 2]", ("", 0)), ('{"text": 5}', ("", 0)),
                                  ('{"text": "ok", "top": 99}', ("ok", 0)), ('{"text": "ok", "top": "x"}', ("ok", 0))):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(content)
            self.assertEqual(reader.load_state(self.path), expected, content)

    def test_saving_an_empty_text_removes_the_file(self):
        reader.save_state(self.path, "something")
        self.assertTrue(reader.save_state(self.path, ""))
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(reader.save_state(self.path, ""))  # nothing to remove is fine

    def test_an_unwritable_place_is_reported_not_raised(self):
        self.assertFalse(reader.save_state(os.path.join(self.dir, "missing", "x.json"), "text"))

    def test_a_stored_file_is_plain_json(self):
        reader.save_state(self.path, "hello", 2)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"text": "hello", "top": 2})


class ModuleTest(unittest.TestCase):
    def test_module_has_no_heavy_dependencies(self):
        with open(reader.__file__, encoding="utf-8") as f:
            source = f.read()
        for name in ("PyQt", "nltk", "lexical_engine"):
            self.assertNotIn("import " + name, source)


if __name__ == "__main__":
    unittest.main()
