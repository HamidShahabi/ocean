import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from speech_timing import estimate_word_timings, highlight_word_html, word_at  # noqa: E402

STYLE = "background: red;"


class EstimateTest(unittest.TestCase):
    def test_empty_or_unspeakable_input_gives_no_timings(self):
        self.assertEqual(estimate_word_timings("", 2.0), [])
        self.assertEqual(estimate_word_timings("... !?", 2.0), [])
        self.assertEqual(estimate_word_timings("hello", 0), [])

    def test_timings_tile_the_clip_exactly(self):
        text = "It was serendipity that we met, wasn't it?"
        timings = estimate_word_timings(text, 3.2)
        self.assertEqual(timings[0].start, 0.0)
        self.assertEqual(timings[-1].end, 3.2)
        for prev, cur in zip(timings, timings[1:]):
            self.assertAlmostEqual(prev.end, cur.start)
            self.assertLess(cur.start, cur.end)

    def test_spans_point_at_the_words(self):
        text = "It wasn't well-known, was it?"
        words = [text[t.start_char:t.end_char] for t in estimate_word_timings(text, 2.0)]
        self.assertEqual(words, ["It", "wasn't", "well-known", "was", "it"])

    def test_longer_words_take_longer(self):
        a, b = estimate_word_timings("an extraordinarily", 1.0)
        self.assertGreater(b.end - b.start, a.end - a.start)

    def test_punctuation_adds_a_pause(self):
        # Duration 23.5 s makes each word's length equal its weight: chars + 1.5, plus the pause.
        red, blue, green = estimate_word_timings("red. blue, green", 23.5)
        self.assertAlmostEqual(red.end - red.start, 3 + 1.5 + 5.0)    # sentence stop
        self.assertAlmostEqual(blue.end - blue.start, 4 + 1.5 + 2.0)  # comma
        self.assertAlmostEqual(green.end - green.start, 5 + 1.5)      # last word: no pause
        colon = estimate_word_timings("red: blue", 1.0)[0]
        comma = estimate_word_timings("red, blue", 1.0)[0]
        stop = estimate_word_timings("red. blue", 1.0)[0]
        self.assertGreater(stop.end, colon.end)
        self.assertGreater(colon.end, comma.end)

    def test_final_punctuation_is_not_a_pause(self):
        with_dot = estimate_word_timings("red blue.", 2.0)
        without = estimate_word_timings("red blue", 2.0)
        for a, b in zip(with_dot, without):
            self.assertAlmostEqual(a.end, b.end)

    def test_duration_scales_every_time(self):
        slow = estimate_word_timings("one two three", 4.0)
        fast = estimate_word_timings("one two three", 2.0)
        for s, f in zip(slow, fast):
            self.assertAlmostEqual(s.end, f.end * 2)

    def test_non_ascii_words(self):
        text = "Café naïve"
        self.assertEqual([text[t.start_char:t.end_char] for t in estimate_word_timings(text, 1.0)], ["Café", "naïve"])


class WordAtTest(unittest.TestCase):
    def test_finds_the_current_word(self):
        timings = estimate_word_timings("one two three", 3.0)
        self.assertEqual(word_at(timings, 0.0), 0)
        self.assertEqual(word_at(timings, timings[1].start + 0.001), 1)
        self.assertEqual(word_at(timings, timings[2].end - 0.001), 2)

    def test_outside_the_speech(self):
        timings = estimate_word_timings("one two", 1.0)
        self.assertIsNone(word_at(timings, -0.1))
        self.assertIsNone(word_at(timings, 1.0))
        self.assertIsNone(word_at([], 0.5))


class HighlightHtmlTest(unittest.TestCase):
    def test_plain_sentence(self):
        self.assertEqual(
            highlight_word_html("Pure serendipity.", 5, 16, STYLE),
            f"Pure <span style='{STYLE}'>serendipity</span>.",
        )

    def test_word_at_the_ends(self):
        self.assertEqual(highlight_word_html("Hi there", 0, 2, STYLE), f"<span style='{STYLE}'>Hi</span> there")
        self.assertEqual(highlight_word_html("Hi there", 3, 8, STYLE), f"Hi <span style='{STYLE}'>there</span>")

    def test_phrase_tags_are_skipped_when_counting(self):
        html = "A <b style='color:#fff;'>lucky</b> break"
        self.assertEqual(
            highlight_word_html(html, 2, 7, STYLE),
            f"A <b style='color:#fff;'><span style='{STYLE}'>lucky</span></b> break",
        )

    def test_span_never_crosses_a_tag(self):
        html = "a n<b style='color:#fff;'>ice cream</b> b"
        out = highlight_word_html(html, 2, 6, STYLE)  # the word "nice"
        self.assertEqual(
            out,
            f"a <span style='{STYLE}'>n</span><b style='color:#fff;'><span style='{STYLE}'>ice</span> cream</b> b",
        )

    def test_text_with_stray_angle_brackets_is_left_alone(self):
        self.assertEqual(highlight_word_html("1 < 2 go", 6, 8, STYLE), f"1 < 2 <span style='{STYLE}'>go</span>")


if __name__ == "__main__":
    unittest.main()
