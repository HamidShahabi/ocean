"""The importer and the engine on real Kaikki.org entries (tests/data/kaikki_real.jsonl: the words from the review
screenshots and a few neighbours, trimmed to the keys Ocean reads; Wiktionary text, CC BY-SA 4.0). No subtitles
here, so examples are Wiktionary's own."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
from lexicon_fixture import use_lexicon  # noqa: E402
import build_lexicon_db as build  # noqa: E402
import lexical_engine as le  # noqa: E402

REAL = os.path.join(HERE, "data", "kaikki_real.jsonl")
_DB = None


def real_db() -> str:
    global _DB
    if _DB is None:
        out = os.path.join(tempfile.mkdtemp(), "real.db")
        with contextlib.redirect_stdout(io.StringIO()):
            build.build(out, kaikki=REAL)
        _DB = out
    return _DB


def rows(word, pos=None):
    with open(REAL, encoding="utf-8") as f:
        return [r for r in map(json.loads, f) if r["word"] == word and (pos is None or r["pos"] == pos)]


class RealImportTest(unittest.TestCase):
    def test_no_stored_example_is_damaged(self):
        bad = ("ſ", "[…]", "[...]", " / ", "#", "[[", "''", "(s)", "VV", "[translating")
        n = 0
        with open(REAL, encoding="utf-8") as f:
            for line in f:
                entry, _, _ = build.kaikki_entry(json.loads(line))
                for sense in (entry or {}).get("senses", []):
                    n += bool(sense["example"])
                    for marker in bad:
                        self.assertNotIn(marker, sense["example"], (entry["word"], sense["example"]))
                    self.assertLessEqual(len(sense["example"].split()), 20)
        self.assertGreater(n, 20)  # plenty of good examples remain

    def test_possess_the_old_book_quotation_is_not_stored(self):
        entry, _, _ = build.kaikki_entry(rows("possess", "verb")[0])
        examples = [s["example"] for s in entry["senses"]]
        self.assertIn("He does not even possess a working telephone.", examples)
        self.assertFalse(any("poſſeſt" in e or "poffeft" in e or "bloud" in e for e in examples))

    def test_trump_artefacts_and_quotations(self):
        noun = build.kaikki_entry(rows("trump", "noun")[0])[0]
        self.assertEqual(noun["senses"][0]["example"], "Diamonds were declared trump.")  # "(s)" cleaned
        for row in rows("trump") + rows("Trump"):
            entry, _, _ = build.kaikki_entry(row)
            for sense in entry["senses"]:
                self.assertNotIn("Strawe", sense["example"])
                self.assertNotIn("trumpety", sense["example"])  # "# Off she went with a trumpety-trump #"

    def test_specialist_and_archaic_senses_are_stored_last(self):
        rational = build.kaikki_entry(rows("rational", "adj")[0])[0]
        defs = [s["def"] for s in rational["senses"]]
        self.assertTrue(defs[0].startswith("Capable of reasoning"))
        first_math = next(i for i, s in enumerate(rational["senses"]) if "mathematics" in s.get("tags", []))
        self.assertTrue(all("mathematics" in s.get("tags", []) or "economics" in s.get("tags", []) or "obsolete" in s.get("tags", [])
                            for s in rational["senses"][first_math:]))
        privilege = build.kaikki_entry(rows("privilege", "noun")[0])[0]
        defs = [s["def"] for s in privilege["senses"]]
        pope = next(i for i, d in enumerate(defs) if "Pope" in d)  # the historical, ecclesiastical sense
        ordinary = next(i for i, d in enumerate(defs) if d.startswith("A right or immunity enjoyed by a legislative body"))
        self.assertGreater(pope, ordinary)
        possess = build.kaikki_entry(rows("possess", "verb")[0])[0]
        self.assertEqual([s.get("tags") for s in possess["senses"][-2:]], [["archaic"], ["archaic"]])

    def test_ipa_is_the_american_phonemic_one(self):
        self.assertEqual(build.kaikki_entry(rows("possess", "verb")[0])[0]["ipa"], "/pəˈzɛs/")
        self.assertEqual(build.kaikki_entry(rows("privilege", "noun")[0])[0]["ipa"], "/ˈpɹɪv.ɪ.lɪd͡ʒ/")  # normalised when shown

    def test_forms_are_everyday_inflections_and_alternatives_are_kept_apart(self):
        possess = build.kaikki_entry(rows("possess", "verb")[0])[0]
        self.assertTrue({"possesses", "possessing", "possessed"} <= {f[0] for f in possess["forms"]})
        self.assertFalse(any("second-person" in f[1] or f[0].endswith(("est", "eth")) for f in possess["forms"]))  # no thou-forms
        privilege = build.kaikki_entry(rows("privilege", "noun")[0])[0]
        self.assertIn(["priviledge", "alternative"], privilege["forms"])

    def test_alternative_spellings_are_kept_apart_from_inflections(self):
        color = build.kaikki_entry(rows("color", "noun")[0])[0]
        self.assertIn(["colour", "alternative Commonwealth Ireland"], color["forms"])
        self.assertIn(["colors", "plural"], color["forms"])
        self.assertFalse(any(f[0] == "usage notes" for f in color["forms"]))  # a section title, not a form

class RealLookupTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self, real_db())
        for p in (mock.patch.object(le, "_ct2_translate", return_value=""),
                  mock.patch.object(le, "_ct2_translate_many", side_effect=lambda texts: [""] * len(texts)),
                  mock.patch.object(le, "sentence_to_ipa", return_value=""),
                  mock.patch.object(le, "translate_batch_fast", side_effect=lambda s, t: {x: "ترجمه" for x in list(s) + list(t)}),
                  mock.patch.dict(le.USER_MEMORY, {"overrides": {}, "entries": {}})):
            p.start()
            self.addCleanup(p.stop)

    def test_the_screenshot_words(self):
        possess = le.analyze_with_dictionaries("possess")
        self.assertEqual(possess["ipa_us"], "/pəˈzɛs/")
        self.assertEqual(len(possess["senses"]), 2)
        self.assertTrue(possess["senses"][0]["def"].startswith("To have something as"))
        self.assertTrue(all("poſſ" not in s["example_en"] for s in possess["senses"]))
        trump = le.analyze_with_dictionaries("trump")
        self.assertEqual([s["pos"] for s in trump["senses"]][:2], ["Noun", "Noun"])
        self.assertNotIn("A surname.", [s["def"] for s in trump["senses"]])
        self.assertNotIn("Strawe", " ".join(s["example_en"] for s in trump["senses"]))
        privilege = le.analyze_with_dictionaries("privilege")
        self.assertEqual(privilege["ipa_us"], "/ˈprɪvɪlɪdʒ/")
        self.assertNotIn("Pope", " ".join(s["def"] for s in privilege["senses"]))
        rational = le.analyze_with_dictionaries("rational")
        self.assertEqual(rational["senses"][0]["def"], "Capable of reasoning.")
        self.assertFalse(any("ratio of two integers" in s["def"] for s in rational["senses"]))
        self.assertNotIn("noetic", [s["term"] for s in rational["synonyms"]])

    def test_the_idioms(self):
        dial = le.analyze_with_dictionaries("Dial it down")
        self.assertEqual((dial["headword"], dial["phrase_match"]["entry"]), ("dial down", "dial down"))
        self.assertTrue(dial["senses"])
        roses = le.analyze_with_dictionaries("no bed of roses")  # Wiktionary has only "bed of roses"
        self.assertEqual((roses["headword"], roses["phrase_match"]["negated"]), ("bed of roses", True))
        self.assertTrue(roses["senses"])
        self.assertEqual(le.analyze_with_dictionaries("spill the beans")["headword"], "spill the beans")

    def test_inflected_forms_and_variants(self):
        self.assertEqual(le.analyze_with_dictionaries("possessed")["morphology"]["root"], "possess")
        self.assertEqual(le.analyze_with_dictionaries("dialing")["morphology"]["root"], "dial")
        colour = le.analyze_with_dictionaries("colour")
        self.assertTrue(colour["senses"])
        self.assertNotEqual(colour["morphology"]["root"], "lour")
        with mock.patch.object(le, "is_everyday_word", return_value=True):
            self.assertEqual(le.analyze_with_dictionaries("color")["alternatives"], ["colour"])


if __name__ == "__main__":
    unittest.main()
