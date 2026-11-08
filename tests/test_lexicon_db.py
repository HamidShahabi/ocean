"""The dataset import tool (tools/build_lexicon_db.py) and the read side (lexicon_db.LexiconDB)."""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import IDIOMS_FA, KAIKKI_ROWS, fixture_db, write_dumps, write_slang_db  # noqa: E402
import build_lexicon_db as build  # noqa: E402
import lexicon_db  # noqa: E402


class KeyTest(unittest.TestCase):
    def test_text_key(self):
        self.assertEqual(lexicon_db.text_key("  Break a LEG! "), "break a leg")
        self.assertEqual(lexicon_db.text_key("Don’t stop—now."), "don't stop now")
        self.assertEqual(lexicon_db.text_key("'well-known'"), "well-known")
        self.assertEqual(lexicon_db.text_key("snake_case"), "snake case")

    def test_persian_cleaning(self):
        self.assertEqual(lexicon_db.clean_persian("بانْک  كيف"), "بانک کیف")
        self.assertEqual(lexicon_db.persian_key("می‌روم؟"), "می روم")


class KaikkiMappingTest(unittest.TestCase):
    def test_entry_fields(self):
        entry, forms, fas = build.kaikki_entry(KAIKKI_ROWS[0])
        self.assertEqual((entry["word"], entry["pos"], entry["ipa"]), ("bank", "Noun", "/bæŋk/"))  # US, phonemic
        self.assertEqual([s["def"] for s in entry["senses"]],
                         ["An institution where one can place and borrow money.", "Sloping land beside a river.", "A bench."])
        self.assertEqual(entry["senses"][2]["tags"], ["obsolete"])  # kept, but stored last and labelled: the reader hides it
        self.assertEqual(entry["senses"][0]["example"], "I keep my savings in a bank.")  # a usage example beats a quotation
        self.assertEqual(entry["senses"][1]["example"], "")  # a collocation list is not an example...
        self.assertEqual(entry["collocations"], ["blood bank", "data bank"])  # ...it becomes collocations
        self.assertEqual(entry["derived"], ["bank account", "bankroll"])
        self.assertEqual(forms, [])
        self.assertEqual([(t["sense"], t["fa"]) for t in fas],
                         [("institution", "بانک"), ("edge of river", "کرانه"), ("edge of river", "ساحل")])  # no Dari, no German

    def test_a_quotation_that_is_only_its_citation_is_no_example(self):
        self.assertEqual(build.pick_example([{"type": "quotation", "text": "2007, Erin McKean, speech at TED Serendipity"}]), "")
        self.assertEqual(build.pick_example([{"type": "example", "text": "2007, Erin McKean, speech at TED Serendipity"}]), "")

    def test_quotations_are_a_last_resort_and_only_modern_ones(self):
        modern = {"type": "quotation", "text": "It was pure serendipity that we met.", "ref": "2007, Erin McKean, speech"}
        old = dict(modern, ref="1812, The Freethinking Christians' Magazine")
        undated = {"type": "quotation", "text": "It was pure serendipity that we met."}
        self.assertEqual(build.pick_example([modern]), "It was pure serendipity that we met.")
        self.assertEqual(build.pick_example([old]), "")  # old books: old spelling and long-s OCR text
        self.assertEqual(build.pick_example([undated]), "")  # no date, no way to know
        usage = {"type": "example", "text": "I keep my savings in a bank."}
        self.assertEqual(build.pick_example([modern, usage]), "I keep my savings in a bank.")  # a usage example wins

    def test_damaged_text_is_never_an_example(self):
        for text in ("I am poſſeſt with an adulterate blot, / My bloud is mingled with the crime of luſt: […]",
                     "This cornet [translating Cornet] was a brave young cavalier and not more than two years older.",
                     "# Off she went with a trumpety-trump #", "THIS IS ALL IN CAPITALS AND SHOUTING AT YOU",
                     "Thou hast a gift for it, my friend.", "Too short.", "[[wiki]] markup in the text is not clean."):
            self.assertEqual(build.pick_example([{"type": "example", "text": text}]), "", text)
        self.assertEqual(build.pick_example([{"type": "example", "text": "Diamonds were declared trump(s)."}]),
                         "Diamonds were declared trump.")  # the "(s)" artefact is cleaned, not translated

    def test_the_american_phonemic_transcription_is_chosen(self):
        sounds = [{"ipa": "/ʌk/", "tags": ["UK"]}, {"ipa": "[ʌk]", "tags": ["US"]}, {"ipa": "/ɐk/", "tags": []}, {"ipa": "/ɑk/", "tags": ["US"]}]
        self.assertEqual(build.pick_ipa(sounds), "/ɑk/")
        self.assertEqual(build.pick_ipa(sounds[:1] + sounds[2:3]), "/ɐk/")  # an unlabelled one beats the British one
        self.assertEqual(build.pick_ipa(sounds[:1]), "/ʌk/")  # better than none

    def test_usage_labels_prefix_the_definition(self):
        entry, _, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "bright"))
        self.assertEqual(entry["senses"][0]["def"], "Giving off a lot of light.")
        self.assertEqual(entry["senses"][0]["tags"], ["informal"])  # the reader puts "(informal)" in front

    def test_abbreviations_and_clippings_are_not_alternatives(self):
        row = {"word": "lt", "pos": "noun", "lang_code": "en", "senses": [
            {"glosses": ["Abbreviation of light"], "alt_of": [{"word": "light"}], "tags": ["abbreviation", "alt-of"]},
            {"glosses": ["Alternative form of lit"], "alt_of": [{"word": "lit"}], "tags": ["alt-of"]}]}
        self.assertEqual(build.kaikki_entry(row)[1], [("lt", "lit", "alt")])

    def test_alternative_spellings_point_at_their_word(self):
        entry, forms, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "colour"))
        self.assertEqual((entry, forms), (None, [("colour", "color", "alt")]))

    def test_senses_keep_labels_relations_and_go_specialist_last(self):
        rational = next(r for r in KAIKKI_ROWS if r["word"] == "rational" and r["pos"] == "adj")
        entry, _, _ = build.kaikki_entry(rational)
        self.assertEqual(entry["senses"][-1]["tags"], ["mathematics"])  # first in Wiktionary, last stored
        self.assertTrue(entry["senses"][0]["def"].startswith("Having the capacity"))
        self.assertEqual([r[0] for r in entry["synonyms"]], ["logical", "reasonable", "sensible", "sane", "noetic", "ratiocinative"])
        self.assertEqual(entry["synonyms"][1][1], "not contrary to reason")  # the sense hint
        self.assertEqual(entry["synonyms"][-1][2], "rare")
        self.assertEqual([r[0] for r in entry["senses"][0]["syn"]], ["noetic", "lucid"])  # sense-level ones stay with the sense
        with_forms = dict(rational, forms=[
            {"form": "rationaler", "tags": ["comparative"]}, {"form": "more rational", "tags": ["comparative"]},  # a phrase: no form
            {"form": "rationalest", "tags": ["archaic", "superlative"]}, {"form": "rashnal", "tags": ["alternative"]},
            {"form": "rashnall", "tags": ["alternative", "obsolete"]}, {"form": "rashnul", "tags": ["Scotland", "alternative"]}])
        self.assertEqual(build.kaikki_entry(with_forms)[0]["forms"], [["rationaler", "comparative"], ["rashnal", "alternative"]])

    def test_multi_word_entries_are_kept_with_a_phrase_key(self):
        entry, _, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "bring someone to heel"))
        self.assertEqual((entry["word"], entry["phrase_key"]), ("bring someone to heel", "bring to heel"))
        entry, _, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "no bed of roses"))
        self.assertEqual((entry["pos"], entry["phrase_key"]), ("Idiom / Expression", "bed of roses"))
        self.assertEqual(build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "bank"))[0]["phrase_key"], "")

    def test_form_of_entries_only_point_at_their_lemma(self):
        entry, forms, _ = build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == "banks"))
        self.assertIsNone(entry)
        self.assertEqual(forms, [("banks", "bank", "form")])

    def test_skipped_rows(self):
        for word in ("-ness", "maison"):
            self.assertEqual(build.kaikki_entry(next(r for r in KAIKKI_ROWS if r["word"] == word)), (None, [], []))


class UrbanFilterTest(unittest.TestCase):
    def test_only_well_voted_entries_are_kept(self):
        self.assertEqual(build.URBAN_RATIO, 2)
        self.assertTrue(build.urban_keep(2, 1))
        self.assertTrue(build.urban_keep(10, 0))
        self.assertFalse(build.urban_keep(5, 3))
        self.assertFalse(build.urban_keep(0, 0))  # nobody vouched for it

    def test_json_lines_dump(self):
        path = os.path.join(tempfile.mkdtemp(), "ud.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"word": "sus", "definition": "Suspicious.", "upvotes": 9, "downvotes": 1}) + "\n")
        db = sqlite3.connect(":memory:")
        self.assertEqual(build.import_urban(db, build.read_urban_rows(path)), 1)


class BuiltDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = lexicon_db.LexiconDB(fixture_db())

    def test_meta_records_the_build(self):
        with sqlite3.connect(fixture_db()) as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        entries = sum(1 for r in KAIKKI_ROWS if build.kaikki_entry(r)[0])
        self.assertEqual(meta["kaikki_entries"], str(entries))  # form-of rows, a suffix and a French word make no entry
        self.assertEqual(meta["tatoeba_pairs"], "4")  # the pair without Persian was dropped
        self.assertEqual(meta["idioms_fa"], str(len(IDIOMS_FA)))
        self.assertEqual(meta["schema_version"], str(build.SCHEMA_VERSION))
        self.assertIn("built_at", meta)

    def test_entries_in_dictionary_order(self):
        self.assertEqual([(e["word"], e["pos"]) for e in self.db.entries("bank")], [("bank", "Noun"), ("bank", "Verb")])
        self.assertTrue(self.db.has_entry("Break the ice"))
        self.assertFalse(self.db.has_entry("zeitgeist"))

    def test_an_inflected_form_gets_its_lemma_before_a_name(self):
        self.assertEqual([(e["word"], e["pos"]) for e in self.db.entries("banks")],
                         [("bank", "Noun"), ("bank", "Verb"), ("Banks", "Proper noun")])
        self.assertEqual([e["word"] for e in self.db.entries("ran")], ["run", "run"])

    def test_persian_translations(self):
        self.assertEqual([t["fa"] for t in self.db.persian("bank")], ["بانک", "کرانه", "ساحل", "سپردن"])
        self.assertEqual(self.db.english_for_persian("بانک"), ["bank"])

    def test_slang_keeps_the_well_voted_definition_without_link_brackets(self):
        rizz = self.db.slang("rizz")
        self.assertEqual([(r["def"], r["example"], r["up"], r["down"]) for r in rizz],
                         [("Charisma, especially in flirting.", "He has so much rizz.", 10, 2)])
        self.assertAlmostEqual(rizz[0]["ratio"], 10 / 12)
        self.assertEqual(len(self.db.slang("yeet")), 1)

    def test_whole_text_translations(self):
        self.assertEqual(self.db.translation_pair("break a leg"), ("موفق باشی!", "opensubtitles"))  # the agreed rendering
        self.assertEqual(self.db.translation_pair("I love you"), ("دوستت دارم.", "tatoeba"))  # Tatoeba beats subtitles
        self.assertEqual(self.db.translation_pair("Good morning."), ("", ""))  # one subtitle line is not enough
        self.assertEqual(self.db.translation_pair("This line has far too many words to be kept as a phrase in the database at all."), ("", ""))

    def test_a_line_one_subtitle_has_is_no_exact_translation_but_is_an_example(self):
        self.assertEqual(self.db.translation_pair("The garage was full of old cars."), ("", ""))
        single = self.db.examples("garage", "opensubtitles")
        self.assertEqual([e["en"] for e in single], ["The garage was full of old cars."])

    def test_examples_come_from_one_corpus_filtered_for_quality(self):
        subs = self.db.examples("bank", "opensubtitles")
        self.assertEqual([e["en"] for e in subs], ["The bank is closed today, sorry."])
        self.assertEqual([e["corpus"] for e in subs], ["opensubtitles"])
        tatoeba = self.db.examples("bank", "tatoeba", limit=5)
        self.assertEqual([e["en"] for e in tatoeba], ["The river bank was muddy after the rain.", "I'm going to the bank."])  # about eight words first
        self.assertEqual(self.db.examples("river bank", "tatoeba")[0]["fa"], "ساحل رودخانه بعد از باران گل آلود بود.")

    def test_subtitle_artefacts_and_fragments_are_never_examples(self):
        self.assertEqual(self.db.examples("because", "opensubtitles"), [])  # "Because I..." is a cut-off fragment
        self.assertEqual(self.db.examples("always", "opensubtitles"), [])  # a lyric between notes
        self.assertEqual(self.db.examples("home", "opensubtitles"), [])  # two speakers' lines run together
        self.assertEqual(self.db.examples("possess", "opensubtitles", limit=9)[-1]["en"], "He possesses a very rare talent.")
        self.assertNotIn("natural subtitle", " ".join(e["en"] for e in self.db.examples("possess", "opensubtitles", limit=9)))  # too long

    def test_examples_find_inflected_forms_of_the_word(self):
        found = [e["en"] for e in self.db.examples("possess", "opensubtitles", limit=9)]
        self.assertIn("They say he was possessed by the devil.", found)
        self.assertIn("He possesses a very rare talent.", found)

    def test_idioms_are_found_as_typed_then_normalised(self):
        db = self.db
        self.assertEqual(db.match_phrase("No bed of roses!")["entry"], "no bed of roses")  # Rule 1: as typed, first
        self.assertTrue(db.match_phrase("no bed of roses")["exact"])
        m = db.match_phrase("Dial it down")  # Rule 2: "it" is a neutral pronoun
        self.assertEqual((m["entry"], m["exact"], m["dropped"]), ("dial down", False, ["it"]))
        self.assertEqual(db.match_phrase("dialing it down")["entry"], "dial down")  # inflection
        self.assertIsNone(db.match_phrase("she dials it down"))  # a sentence is no idiom
        m = db.match_phrase("not a bed of roses")  # determiners and negations
        self.assertEqual((m["entry"], m["negated"]), ("bed of roses", True))
        self.assertEqual(db.match_phrase("a bed of roses")["negated"], False)
        self.assertEqual(db.match_phrase("bring him to heel")["entry"], "bring someone to heel")  # the entry's own "someone"
        self.assertEqual(db.match_phrase("dialed down")["entry"], "dial down")  # a form of an entry is an exact match
        self.assertIsNone(db.match_phrase("purple elephant parade"))
        self.assertIsNone(db.match_phrase("bank"))  # one word: not a phrase
        self.assertIsNone(db.match_phrase("it down"))  # nothing is left once the neutral words go

    def test_pre_translated_idioms_are_looked_up_like_entries(self):
        self.assertEqual(self.db.idiom_fa("Dial it down!")["fa"], "آرام تر باش")
        self.assertEqual(self.db.idiom_fa("dialing it down")["source"], "idioms-fa")
        self.assertEqual(self.db.idiom_fa("bring him to heel")["fa"], "کسی را سر به راه کردن")
        self.assertEqual(self.db.idiom_fa("no bed of roses")["fa"], "کار آسانی نیست")
        self.assertIsNone(self.db.idiom_fa("a bed of roses"))  # the opposite meaning: negation must agree
        self.assertIsNone(self.db.idiom_fa("bank"))

    def test_lemmas_and_forms(self):
        self.assertEqual(self.db.lemmas("Possessed"), ["possess"])
        self.assertEqual(self.db.lemma_candidates("dialing"), ["dial"])
        self.assertEqual(self.db.lemma_candidates("banking"), ["bank"])  # by rule, confirmed by the dictionary
        self.assertEqual(self.db.lemma_candidates("zzzzing"), [])
        self.assertEqual(self.db.inflections("possess"), ["possesses", "possessed", "possessing"])
        self.assertEqual((self.db.alternatives("colour"), self.db.alternatives("color")), (["color"], ["colour"]))
        self.assertTrue(self.db.is_verb_form("possessed"))
        self.assertFalse(self.db.is_verb_form("garage"))
        self.assertTrue(self.db.known_word("possess"))
        self.assertFalse(self.db.known_word("poffeft"))

    def test_persian_word_frequencies(self):
        self.assertEqual(self.db.persian_frequency("امتیاز"), 3)
        self.assertEqual(self.db.persian_frequency("حق ویژه"), 0)  # an equivalent nobody says in the subtitles
        self.assertEqual(self.db.persian_frequency(""), 0)

    def test_phrase_hits_tell_whether_a_phrase_is_said(self):
        self.assertGreater(self.db.phrase_hits("check your privilege"), 0)
        self.assertEqual(self.db.phrase_hits("absolute privilege"), 0)
        self.assertGreater(self.db.phrase_hits("cis-privilege") + self.db.phrase_hits("white privilege"), 0)

    def test_subtitle_word_frequencies(self):
        self.assertAlmostEqual(self.db.frequency("break"), 3 / self._subtitle_tokens() * 1_000_000, places=2)
        self.assertIsNone(self.db.frequency("bank"))  # fewer than three occurrences: not counted
        self.assertIsNone(self.db.frequency("zeitgeist"))

    @staticmethod
    def _subtitle_tokens():
        from lexicon_fixture import SUBTITLES
        return sum(len(build._TOKEN.findall(en.lower())) for en, _ in SUBTITLES)

    def test_slang_from_a_compact_database_with_other_column_names(self):
        folder = tempfile.mkdtemp()
        out = os.path.join(folder, "ocean_lexicon.db")
        with contextlib.redirect_stdout(io.StringIO()):
            stats = build.build(out, slang_db=write_slang_db(folder))
        self.assertEqual(stats, {"urban_entries": 3})
        db = lexicon_db.LexiconDB(out)
        rizz = db.slang("RIZZ", limit=2)
        self.assertEqual(rizz[0]["def"], "Charisma, especially in flirting.")
        self.assertEqual((rizz[0]["up"], rizz[0]["down"]), (850, 150))  # a ratio becomes votes out of 1000
        self.assertEqual(db.slang("simp")[0]["example"], "Stop being a simp.")
        self.assertEqual(db.slang("nothing"), [])

    def test_idiom_files_in_both_formats(self):
        folder = tempfile.mkdtemp()
        jsonl = os.path.join(folder, "idioms.jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            f.write(json.dumps({"phrase": "Under the weather", "fa": "کسل", "source": "x"}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"phrase": "single", "fa": "تک"}, ensure_ascii=False) + "\n")  # one word: not an idiom
            f.write(json.dumps({"phrase": "no persian here", "fa": "latin"}) + "\n")
        out = os.path.join(folder, "db.sqlite")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.build(out, idioms_fa=jsonl), {"idioms_fa": 1})
        self.assertEqual(lexicon_db.LexiconDB(out).idiom_fa("under the weather"), {"phrase": "Under the weather", "fa": "کسل", "source": "x"})

    def test_suggestions_and_spelling(self):
        self.assertEqual(self.db.suggestions("ba"), ["bank", "Banks"])
        self.assertEqual(self.db.suggestions("b"), [])
        self.assertEqual(self.db.close_matches("brigt"), ["bright"])
        self.assertEqual(self.db.close_matches("hevy"), ["heavy"])  # no shared trigram: found by prefix
        self.assertEqual(self.db.close_matches("xyzzy"), [])


class BuildDriverTest(unittest.TestCase):
    def test_build_replaces_the_database_only_when_complete(self):
        folder = tempfile.mkdtemp()
        out = os.path.join(folder, "ocean_lexicon.db")
        with open(out, "w") as f:
            f.write("old")
        with self.assertRaises(FileNotFoundError):
            build.build(out, kaikki=os.path.join(folder, "missing.jsonl"))
        with open(out) as f:
            self.assertEqual(f.read(), "old")  # a failed build leaves the previous database alone
        dumps = write_dumps(folder)
        with contextlib.redirect_stdout(io.StringIO()):
            stats = build.build(out, tatoeba_dir=dumps["tatoeba_dir"])
        self.assertEqual(stats, {"tatoeba_pairs": 4})
        self.assertFalse(os.path.exists(out + ".building"))
        db = lexicon_db.LexiconDB(out)
        self.assertTrue(db.has("parallel"))
        self.assertFalse(db.has("slang"))
        self.assertEqual(db.entries("bank"), [])  # a dataset left out is simply absent

    def test_missing_or_broken_database_answers_empty(self):
        missing = lexicon_db.LexiconDB(os.path.join(tempfile.mkdtemp(), "none.db"))
        self.assertFalse(missing.available())
        self.assertEqual((missing.entries("bank"), missing.translation_pair("hi"), missing.suggestions("ba")), ([], ("", ""), []))
        broken_path = os.path.join(tempfile.mkdtemp(), "broken.db")
        with open(broken_path, "wb") as f:
            f.write(b"this is not a database" * 100)
        with self.assertLogs("ocean.lexicon", "ERROR"):
            self.assertEqual(lexicon_db.LexiconDB(broken_path).slang("rizz"), [])


if __name__ == "__main__":
    unittest.main()
