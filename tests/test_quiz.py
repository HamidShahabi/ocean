import os
import random
import sys
import tempfile
import unittest

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quiz  # noqa: E402
from quiz import EN_TO_FA, FA_TO_EN, QuizItem, QuizMode  # noqa: E402

WORDS = [
    ("book", "کتاب"), ("water", "آب"), ("house", "خانه"), ("tree", "درخت"), ("sun", "خورشید"),
    ("moon", "ماه"), ("river", "رودخانه"), ("door", "در"),
]
ITEMS = [QuizItem(en, en, fa) for en, fa in WORDS]


def make_entry(term, payload):
    return {"query_key": term.lower(), "display_term": term, "payload": payload}


class ItemTest(unittest.TestCase):
    def test_quiz_module_has_no_qt_dependency(self):
        with open(quiz.__file__, encoding="utf-8") as f:
            self.assertNotIn("PyQt", f.read())

    def test_entries_become_items_in_both_shapes(self):
        entries = [
            make_entry("book", {"headword": "book", "persian_equivalent": "کتاب"}),
            make_entry("آب", {"headword": "آب", "is_reverse": True, "english_translation": "water"}),
        ]
        items = quiz.items_from_entries(entries)
        self.assertEqual([(i.english, i.persian) for i in items], [("book", "کتاب"), ("water", "آب")])

    def test_unusable_entries_are_skipped(self):
        entries = [
            make_entry("a long sentence here", {"headword": "a long sentence here", "is_sentence": True, "persian_equivalent": "جمله"}),
            make_entry("nothing", {"headword": "nothing", "persian_equivalent": ""}),
            make_entry("کتاب", {"headword": "کتاب", "is_reverse": True}),
        ]
        self.assertEqual(quiz.items_from_entries(entries), [])

    def test_english_duplicates_collapse(self):
        entries = [
            make_entry("book", {"headword": "book", "persian_equivalent": "کتاب"}),
            make_entry("کتاب", {"headword": "کتاب", "is_reverse": True, "english_translation": "Book"}),
        ]
        self.assertEqual(len(quiz.items_from_entries(entries)), 1)


class ChoiceQuestionTest(unittest.TestCase):
    def questions(self, items=ITEMS, seed=1, count=quiz.SESSION_LENGTH):
        return quiz.new_session(items, quiz.CHOICE_MODE, random.Random(seed), count)

    def test_seed_makes_sessions_repeatable(self):
        self.assertEqual(self.questions(seed=7), self.questions(seed=7))
        self.assertNotEqual(self.questions(seed=7), self.questions(seed=8))

    def test_four_unique_choices_containing_the_answer(self):
        for q in self.questions():
            self.assertEqual(len(q.choices), 4)
            self.assertEqual(len(set(q.choices)), 4)
            self.assertEqual(sum(q.is_correct(c) for c in q.choices), 1)

    def test_both_directions_are_asked(self):
        qs = self.questions()
        self.assertEqual({q.direction for q in qs}, {EN_TO_FA, FA_TO_EN})
        by_key = {i.query_key: i for i in ITEMS}
        for q in qs:
            item = by_key[q.query_key]
            if q.direction == EN_TO_FA:
                self.assertEqual((q.prompt, q.answer), (item.english, item.persian))
                self.assertTrue(q.answer_is_persian and not q.prompt_is_persian)
            else:
                self.assertEqual((q.prompt, q.answer), (item.persian, item.english))
                self.assertTrue(q.prompt_is_persian and not q.answer_is_persian)

    def test_each_word_is_asked_once_and_session_is_capped(self):
        qs = self.questions(count=5)
        self.assertEqual(len(qs), 5)
        self.assertEqual(len({q.query_key for q in qs}), 5)
        self.assertEqual(len(self.questions(items=ITEMS[:4])), 4)

    def test_distractors_never_share_the_answers_persian_meaning(self):
        # "big" and "large" both mean بزرگ (as one of several comma-separated meanings).
        items = [
            QuizItem("big", "big", "بزرگ، گنده"), QuizItem("large", "large", "بزرگ"),
            QuizItem("tree", "tree", "درخت"), QuizItem("sun", "sun", "خورشید"),
            QuizItem("moon", "moon", "ماه"), QuizItem("door", "door", "در"),
        ]
        by_text = {i.english: i for i in items}
        by_text.update({i.persian: i for i in items})
        for seed in range(30):
            for q in self.questions(items=items, seed=seed, count=6):
                chosen = [by_text[c] for c in q.choices]
                for n, a in enumerate(chosen):
                    for b in chosen[n + 1:]:
                        self.assertFalse(a.clashes_with(b), (q, a.english, b.english))

    def test_question_skipped_when_no_clash_free_distractors_exist(self):
        # Four words but only three distinct meanings: nobody can get three distractors.
        items = [QuizItem("big", "big", "بزرگ"), QuizItem("large", "large", "بزرگ"),
                 QuizItem("tree", "tree", "درخت"), QuizItem("sun", "sun", "خورشید")]
        self.assertEqual(self.questions(items=items), [])

    def test_too_few_words_gives_no_session_and_a_helpful_message(self):
        self.assertEqual(self.questions(items=ITEMS[:3]), [])
        self.assertEqual(self.questions(items=[]), [])
        msg = quiz.unavailable_message(quiz.CHOICE_MODE, 3)
        self.assertIn("at least 4", msg)
        self.assertIn("3 usable saved words", msg)
        self.assertIn("no saved words", quiz.unavailable_message(quiz.CHOICE_MODE, 0))

    def test_answer_check_ignores_case_and_persian_variants(self):
        q = quiz.Question(mode="x", prompt="p", answer="Book", query_key="book")
        self.assertTrue(q.is_correct("  book "))
        fa = quiz.Question(mode="x", prompt="p", answer="كتاب", query_key="book")  # Arabic kaf
        self.assertTrue(fa.is_correct("کتاب"))
        self.assertFalse(fa.is_correct("آب"))


class ClozeQuestionTest(unittest.TestCase):
    RUN = QuizItem("run", "run", "دویدن", {"senses": [
        {"pos": "Verb", "def": "Move fast.", "example_en": "She ran to the station every morning."},
        {"pos": "Verb", "def": "Operate.", "example_en": "They run a small shop."},
        {"pos": "Verb", "def": "Other.", "example_en": "Nothing relevant lives in this one."},
    ]})
    ICE = QuizItem("break the ice", "break the ice", "یخ را شکستن", {"senses": [
        {"pos": "Idiom", "def": "Start talking.", "example_en": "Tom broke the ice with a joke."}]})
    BOOK = QuizItem("book", "book", "کتاب", {"senses": [{"pos": "Noun", "def": "Pages.", "example_en": ""}]})
    BARE = QuizItem("door", "door", "در")  # saved without a payload: nothing to blank

    def questions(self, items, seed=0, count=10):
        return quiz.new_session(items, quiz.CLOZE_MODE, random.Random(seed), count)

    def test_mode_is_registered_and_typed(self):
        self.assertIn(quiz.CLOZE_MODE, quiz.MODES)
        for q in self.questions([self.RUN, self.ICE]):
            self.assertTrue(q.is_typed)
            self.assertEqual(q.choices, ())

    def test_sentence_is_blanked_and_revealed_in_full(self):
        by_key = {q.query_key: q for q in self.questions([self.RUN, self.ICE])}
        ice = by_key["break the ice"]
        self.assertEqual(ice.prompt, "Tom _____ with a joke.")
        self.assertEqual(ice.reveal, "Tom broke the ice with a joke.")
        self.assertEqual(ice.answer, "broke the ice")
        run = by_key["run"]
        self.assertIn("_____", run.prompt)
        self.assertNotIn(run.answer, run.prompt)
        self.assertIn(run.answer, run.reveal)
        self.assertIn("دویدن", run.hint)
        self.assertFalse(run.prompt_is_persian or run.answer_is_persian)

    def test_one_of_the_usable_sentences_is_picked(self):
        seen = {q.reveal for seed in range(20) for q in self.questions([self.RUN], seed=seed)}
        self.assertEqual(seen, {"She ran to the station every morning.", "They run a small shop."})

    def test_words_without_a_suitable_example_are_skipped(self):
        qs = self.questions([self.BOOK, self.BARE, self.RUN])
        self.assertEqual([q.query_key for q in qs], ["run"])
        self.assertEqual(self.questions([self.BOOK, self.BARE]), [])

    def test_collocation_examples_count_but_synonym_examples_do_not(self):
        item = QuizItem("tea", "tea", "چای", {
            "collocations": [{"term": "strong tea", "example_en": "I like strong tea in the morning."}],
            "synonyms": [{"term": "brew", "example_en": "The tea was a lovely brew."}]})
        qs = self.questions([item])
        self.assertEqual([q.reveal for q in qs], ["I like strong tea in the morning."])

    def test_adjectives_blank_comparatives(self):
        big = QuizItem("big", "big", "بزرگ", {"senses": [
            {"pos": "Adjective", "def": "Large.", "example_en": "This house is bigger than ours."}]})
        self.assertEqual(self.questions([big])[0].prompt, "This house is _____ than ours.")
        noun = QuizItem("big", "big", "بزرگ", {"senses": [
            {"pos": "Noun", "def": "Large.", "example_en": "This house is bigger than ours."}]})
        self.assertEqual(self.questions([noun]), [])

    def test_exact_form_or_base_word_is_accepted_case_insensitively(self):
        q = [q for q in self.questions([self.ICE])][0]
        for ok in ("broke the ice", "  Broke The ICE ", "break the ice", "BREAK THE ICE"):
            self.assertTrue(q.is_correct(ok), ok)
        for bad in ("", "   ", "broke", "breaks the ice"):
            self.assertFalse(q.is_correct(bad), bad)

    def test_every_form_blanked_in_a_sentence_is_accepted(self):
        item = QuizItem("run", "run", "دویدن", {"senses": [
            {"pos": "Verb", "def": "Move.", "example_en": "Run when he runs, said the coach."}]})
        q = self.questions([item])[0]
        self.assertEqual(q.prompt, "_____ when he _____, said the coach.")
        for ok in ("run", "Run", "runs", "RUNS"):
            self.assertTrue(q.is_correct(ok), ok)
        self.assertFalse(q.is_correct("ran"))

    def test_session_is_capped_and_repeatable(self):
        items = [QuizItem(w, w, "x", {"senses": [{"pos": "Noun", "def": "d", "example_en": f"I saw a {w} there today."}]})
                 for w in ("cat", "dog", "bird", "fish", "frog")]
        self.assertEqual(len(self.questions(items, count=3)), 3)
        self.assertEqual(self.questions(items, seed=4), self.questions(items, seed=4))

    def test_no_saved_words_gives_a_helpful_message(self):
        self.assertEqual(self.questions([]), [])
        msg = quiz.unavailable_message(quiz.CLOZE_MODE, 2)
        self.assertIn("example sentence", msg)
        self.assertIn("2 usable saved words", msg)


class ListenQuestionTest(unittest.TestCase):
    BOOK = QuizItem("book", "book", "کتاب", {"headword": "book", "ipa_us": "/bʊk/"})
    ICE = QuizItem("break the ice", "break the ice", "یخ را شکستن")

    def questions(self, items, seed=0, count=10):
        return quiz.new_session(items, quiz.LISTEN_MODE, random.Random(seed), count)

    def test_mode_is_registered_typed_and_speaks_without_showing_the_word(self):
        self.assertEqual(quiz.MODES[quiz.LISTEN_MODE].needs, quiz.CAP_VOICE)
        for q in self.questions([self.BOOK, self.ICE]):
            self.assertTrue(q.is_typed)
            self.assertEqual(q.speak, q.answer)
            for shown in (q.prompt, q.hint):
                self.assertNotIn(q.answer.lower(), shown.lower())
            self.assertNotIn("کتاب", q.prompt + q.hint)  # the meaning isn't given away before answering

    def test_reveal_has_ipa_when_saved_and_the_persian_meaning(self):
        by_key = {q.query_key: q for q in self.questions([self.BOOK, self.ICE])}
        self.assertEqual(by_key["book"].reveal, "/bʊk/ · کتاب")
        self.assertEqual(by_key["break the ice"].reveal, "یخ را شکستن")  # no IPA stored: just the meaning
        reverse = QuizItem("water", "water", "آب", {"is_reverse": True, "ipa_us": "/آب/"})
        self.assertEqual(self.questions([reverse])[0].reveal, "آب")

    def test_only_single_words_and_short_phrases_are_spoken(self):
        long_phrase = QuizItem("a", "this is a rather long phrase", "x")
        short_sentence = QuizItem("b", "She walked home.", "x")  # four words: still a short phrase
        odd = QuizItem("c", "water, tea", "x")
        digits = QuizItem("d", "3d printer", "x")
        qs = self.questions([long_phrase, odd, digits, self.BOOK, short_sentence])
        self.assertEqual(sorted(q.answer for q in qs), ["She walked home.", "book"])
        self.assertEqual(self.questions([long_phrase, odd, digits]), [])

    def test_session_is_capped_and_repeatable(self):
        items = [QuizItem(w, w, "x") for w in ("cat", "dog", "bird", "fish", "frog")]
        self.assertEqual(len(self.questions(items, count=3)), 3)
        self.assertEqual(self.questions(items, seed=2), self.questions(items, seed=2))

    def test_answers_are_checked_case_insensitively(self):
        q = self.questions([self.ICE])[0]
        for ok in ("break the ice", "  Break  The ICE ", "BREAK THE ICE."):
            self.assertEqual(q.grade(ok), quiz.CORRECT, ok)
        self.assertEqual(quiz.Question("m", "p", "don't", "k").grade("DON’T"), quiz.CORRECT)

    def test_one_typo_is_almost_and_not_correct(self):
        q = self.questions([QuizItem("necessary", "necessary", "x")])[0]
        for typo in ("neccessary", "necesary", "necessery", "necesssary", "necessray"):  # +1, -1, sub, +1, swap
            self.assertFalse(q.is_correct(typo), typo)
            self.assertEqual(q.grade(typo), quiz.ALMOST, typo)
            self.assertEqual(q.near_miss_of(typo), "necessary")
        for far in ("neccesary", "necesar", "recessery", "", "   "):  # two edits, or nothing
            self.assertEqual(q.grade(far), quiz.WRONG, far)
        self.assertEqual(q.grade("Necessary"), quiz.CORRECT)

    def test_short_words_get_no_typo_tolerance(self):
        qs = self.questions([QuizItem("form", "form", "x"), QuizItem("house", "house", "x")])
        by_answer = {x.answer: x for x in qs}
        self.assertEqual(by_answer["form"].grade("from"), quiz.WRONG)  # a different real word
        self.assertEqual(by_answer["form"].grade("fom"), quiz.WRONG)
        self.assertEqual(by_answer["house"].grade("hous"), quiz.ALMOST)  # five letters: tolerance starts

    def test_other_modes_never_report_almost(self):
        q = quiz.Question("x", "p", "necessary", "k")
        self.assertEqual(q.grade("neccessary"), quiz.WRONG)
        self.assertEqual(q.near_miss_of("neccessary"), "")

    def test_within_one_edit(self):
        for a, b in (("cat", "cat"), ("cat", "cut"), ("cat", "ct"), ("cat", "cats"), ("cat", "act"), ("abcd", "abdc"), ("", "a")):
            self.assertTrue(quiz.within_one_edit(a, b), (a, b))
            self.assertTrue(quiz.within_one_edit(b, a), (b, a))
        for a, b in (("cat", "dog"), ("cat", "tac"), ("cat", "c"), ("abcd", "badc"), ("abc", "xbcde"), ("abc", "bca")):
            self.assertFalse(quiz.within_one_edit(a, b), (a, b))

    def test_missing_voice_explains_and_loading_voice_waits(self):
        mode = quiz.LISTEN_MODE
        self.assertEqual(quiz.missing_capability(mode, {quiz.CAP_VOICE}), "")
        none = quiz.missing_capability(mode, set())
        self.assertIn("needs a voice", none)
        self.assertIn("Kokoro", none)
        loading = quiz.missing_capability(mode, set(), {quiz.CAP_VOICE})
        self.assertIn("still loading", loading)
        self.assertNotEqual(loading, none)
        self.assertEqual(quiz.missing_capability(quiz.CHOICE_MODE, set()), "")  # modes without a need are unaffected
        self.assertEqual(quiz.missing_capability(quiz.CLOZE_MODE, set(), {quiz.CAP_VOICE}), "")

    def test_no_saved_words_gives_a_helpful_message(self):
        self.assertEqual(self.questions([]), [])
        self.assertIn("single words or short phrases", quiz.unavailable_message(quiz.LISTEN_MODE, 0))


class ModeRegistryTest(unittest.TestCase):
    def test_new_modes_plug_in_through_the_registry(self):
        def generate(items, rng, count):
            return [quiz.Question(mode="typed", prompt=i.english, answer=i.persian, query_key=i.query_key) for i in items[:count]]

        quiz.register_mode(QuizMode("typed", "Typed", generate, min_items=1, requirement="Needs one word."))
        try:
            qs = quiz.new_session(ITEMS[:2], "typed", random.Random(0))
            self.assertEqual([q.choices for q in qs], [(), ()])
            self.assertEqual(quiz.new_session([], "typed"), [])
            self.assertTrue(qs[0].is_correct(ITEMS[0].persian))
        finally:
            del quiz.MODES["typed"]


if __name__ == "__main__":
    unittest.main()
