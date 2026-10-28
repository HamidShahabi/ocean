import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from anki_export import build_anki_tsv, entry_kind, note_fields  # noqa: E402

WORD = {
    "headword": "ephemeral",
    "ipa_us": "/əˈfem(ə)rəl/",
    "persian_equivalent": "زودگذر",
    "senses": [
        {"pos": "Adjective", "def": "Lasting for a\tvery short time.", "example_en": 'Fashions are "ephemeral".',
         "example_fa": "مدها زودگذرند."},
        {"pos": "Noun", "def": "A plant <with> a short life cycle."},
        {"def": "third"},
        {"def": "fourth, beyond MAX_SENSES"},
    ],
}


def parse(text):
    header = [line for line in text.splitlines() if line.startswith("#")]
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    return header, list(csv.reader(io.StringIO(body), delimiter="\t"))


class AnkiExportTest(unittest.TestCase):
    def test_header_directives(self):
        header, _ = parse(build_anki_tsv([WORD])[0])
        self.assertEqual(header, ["#separator:Tab", "#html:true", "#notetype:Basic", "#deck:Ocean",
                                  "#columns:Front\tBack\tTags", "#tags column:3"])

    def test_word_note_content_is_escaped_and_single_line(self):
        front, back, tags = note_fields(WORD)
        self.assertEqual(front, "ephemeral")
        self.assertEqual(tags, "ocean ocean::word")
        self.assertIn("زودگذر", back)
        self.assertIn("<b>Adjective</b> — Lasting for a very short time.", back)  # tab folded
        self.assertIn("&quot;ephemeral&quot;", back)
        self.assertIn("A plant &lt;with&gt; a short life cycle.", back)
        self.assertIn("third", back)
        self.assertNotIn("fourth", back)
        self.assertNotIn("\t", back)
        self.assertNotIn("\n", back)

    def test_rows_round_trip_through_a_tsv_reader(self):
        text, count = build_anki_tsv([WORD, {"headword": "bite the bullet", "persian_equivalent": "x"},
                                      {"headword": ""}])
        _, rows = parse(text)
        self.assertEqual(count, 2)
        self.assertEqual([r[0] for r in rows], ["ephemeral", "bite the bullet"])
        self.assertTrue(all(len(r) == 3 for r in rows))
        self.assertEqual(rows[1][2], "ocean ocean::idiom")

    def test_entry_kind(self):
        self.assertEqual(entry_kind({"headword": "run"}), "word")
        self.assertEqual(entry_kind({"headword": "spill the beans"}), "idiom")
        self.assertEqual(entry_kind({"headword": "I went home.", "is_sentence": True}), "sentence")
        self.assertNotIn("color:#888", note_fields({"headword": "I went home.", "is_sentence": True, "ipa_us": "/x/"})[1])


if __name__ == "__main__":
    unittest.main()
