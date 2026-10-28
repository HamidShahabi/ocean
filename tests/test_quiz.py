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
