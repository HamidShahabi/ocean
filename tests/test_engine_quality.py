"""Content quality of a word page: senses, examples, synonyms, forms and expressions, IPA, idioms, Persian tiers
and Refresh, checked on the words from the review screenshots (possess, trump, privilege, rational, dial down,
bed of roses) over the fixture database, and on a database built by an older version of the importer."""
import json
import os
import sqlite3
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
import translation_cache  # noqa: E402

FORBIDDEN_IPA = "ɹɝɚɡʧʤː͡."


class EngineTestCase(unittest.TestCase):
    """The fixture database, no internet, no machine translation, a fresh cache."""
    stub_sentence_ipa = True

    def setUp(self):
        use_lexicon(self)
        self.translated = []

        def batch(sentences, shorts):
            self.translated += list(sentences) + list(shorts)
            return {s: "ترجمه" for s in list(sentences) + list(shorts)}

        self.batch = mock.patch.object(le, "translate_batch_fast", side_effect=batch)
        self.batch.start()
        self.addCleanup(self.batch.stop)
        for p in (
            mock.patch.object(le, "_google_translate", return_value=""),
            mock.patch.object(le, "_ct2_translate", return_value=""),
            mock.patch.object(le, "_ct2_translate_many", side_effect=lambda texts: [""] * len(texts)),
            mock.patch.object(le.SLM, "translate", return_value=""),
            mock.patch.dict(le.USER_MEMORY, {"overrides": {}, "entries": {}}),
        ) + ((mock.patch.object(le, "sentence_to_ipa", return_value=""),) if self.stub_sentence_ipa else ()):
            p.start()
            self.addCleanup(p.stop)

    def lookup(self, term, **kwargs):
        return le.analyze_with_dictionaries(term, **kwargs)


class DefinitionsTest(EngineTestCase):
    def test_possess_shows_two_ordinary_senses(self):
        d = self.lookup("possess")
        self.assertEqual([s["def"] for s in d["senses"]], [
            "To have something as, or as if as, an owner; to have, to own.",  # no "(something)", nothing dropped from the meaning
            "Of an idea, thought, etc.: to dominate; to strongly influence.",
        ])  # the supernatural-entity sense is a third of one part of speech: not shown

    def test_trump_hides_the_surname_the_trumpet_and_the_figurative_quotation(self):
        d = self.lookup("trump")
        self.assertEqual([(s["pos"], s["def"]) for s in d["senses"]], [
            ("Noun", "The suit, in a game of cards, that outranks all others."),
            ("Noun", "A playing card of that suit."),
            ("Verb", "To play on with a trump."),
        ])
        self.assertNotIn("A surname.", [s["def"] for s in d["senses"]])
        self.assertNotIn("A trumpet.", [s["def"] for s in d["senses"]])

    def test_privilege_leads_with_the_everyday_sense_not_the_popes_exemption(self):
        d = self.lookup("privilege")
        self.assertTrue(d["senses"][0]["def"].startswith("A particular benefit, advantage, or favor"))
        self.assertNotIn("Pope", " ".join(s["def"] for s in d["senses"]))
        self.assertLessEqual(len(d["senses"]), 3)

    def test_rational_does_not_lead_with_mathematics(self):
        d = self.lookup("rational")
        self.assertEqual([s["def"] for s in d["senses"]][0], "Having the capacity to reason; able to think clearly.")
        self.assertFalse(any("ratio of two integers" in s["def"] for s in d["senses"]))
        self.assertFalse(any("rational number" in s["example_en"] for s in d["senses"]))  # nor its example, nor its translation

    def test_a_word_with_only_specialist_senses_still_gets_them(self):
        senses = [{"pos": "Noun", "def": "A thing in mathematics.", "klass": 2}, {"pos": "Noun", "def": "A surname.", "klass": 3}]
        self.assertEqual([s["def"] for s in le.choose_senses(senses)], ["A thing in mathematics."])
        self.assertEqual([s["def"] for s in le.choose_senses(senses[1:])], ["A surname."])

    def test_the_cap_and_the_parts_of_speech(self):
        def sense(pos, text, klass=0, **extra):
            return dict(pos=pos, klass=klass, **{"def": text}, **extra)

        many = [sense("Noun", "A bank of the first kind."), sense("Noun", "A second thing entirely different."),
                sense("Noun", "A third unrelated meaning here."), sense("Verb", "To act in a certain way."),
                sense("Verb", "To act in another way.")]
        self.assertEqual([s["def"] for s in le.choose_senses(many)],
                         ["A bank of the first kind.", "A second thing entirely different.", "To act in a certain way."])  # at most three
        self.assertEqual(len(le.choose_senses(many[:3])), 2)  # one part of speech: two
        self.assertEqual([s["pos"] for s in le.choose_senses([many[0], sense("Verb", "To go.", tags=["informal"])])], ["Noun"])  # a rare use of another POS
        self.assertEqual([s["pos"] for s in le.choose_senses([many[0], sense("Interjection", "Hey there.")])], ["Noun"])
        self.assertEqual([s["def"] for s in le.choose_senses([many[0], dict(many[0], pos="Noun")])], ["A bank of the first kind."])  # a repeat
        taught = [sense("Custom", "Your own meaning.", klass=-1), many[0], many[1], many[3]]
        self.assertEqual(le.choose_senses(taught)[0]["def"], "Your own meaning.")  # always first

    def test_a_noun_idiom_keeps_its_idiom_label(self):
        d = self.lookup("bed of roses")
        self.assertEqual([s["pos"] for s in d["senses"]], ["Idiom / Expression"])


class ExamplesTest(EngineTestCase):
    def test_subtitles_first_then_wiktionary_then_tatoeba(self):
        d = self.lookup("bank")
        self.assertEqual([s["example_source"] for s in d["senses"]], ["OpenSubtitles", "Tatoeba", "Wiktionary"])
        # the Wiktionary examples of the first two senses lost to better material; none is used twice
        examples = [s["example_en"] for s in d["senses"]]
        self.assertEqual(len(set(examples)), 3)

    def test_the_subtitle_line_comes_with_its_human_translation_and_is_never_machine_translated(self):
        self.lookup("possess")
        self.assertNotIn("They say he was possessed by the devil.", self.translated)
        self.assertNotIn("He possesses a very rare talent.", self.translated)

    def test_damaged_wiktionary_quotations_are_never_shown_or_translated(self):
        for word in ("possess", "trump"):
            d = self.lookup(word)
            for s in d["senses"]:
                for bad in ("poſſeſt", "poffeft", "[…]", "VVere", "liuee", "lacke", "[translating"):
                    self.assertNotIn(bad, s["example_en"])
            for sentence in self.translated:
                for bad in ("poſſeſt", "poffeft", "[…]", "VVere", "ſ", "/ ", "#", "(s)"):
                    self.assertNotIn(bad, sentence)

    def test_the_trump_example_with_an_artefact_is_cleaned(self):
        d = self.lookup("trump")
        self.assertFalse(any("(s)" in s["example_en"] or "#" in s["example_en"] for s in d["senses"]))

    def test_a_sense_without_any_material_has_no_example_and_nothing_is_made_up(self):
        d = self.lookup("heavy")
        self.assertEqual(d["senses"][0]["example_en"], "")

    def test_the_verb_gets_a_verb_sentence_and_the_noun_a_noun_sentence(self):
        fits = le._fits_pos
        forms = ["run", "runs", "running", "ran"]
        self.assertTrue(fits("Spend my life on the run.", "Noun", forms))
        self.assertFalse(fits("Spend my life on the run.", "Verb", forms))
        self.assertTrue(fits("I have been running all over.", "Verb", forms))
        self.assertTrue(fits("We have to run now.", "Verb", forms))
        self.assertFalse(fits("We have to run now.", "Noun", forms))
        self.assertTrue(fits("Anything goes.", "Adjective", forms))  # only nouns and verbs are judged

    def test_examples_of_the_relations_are_routed_the_same_way(self):
        en, fa = le.relation_example("possess")
        self.assertIn("possess", en.lower())
        self.assertTrue(fa)
        self.assertEqual(le.relation_example("zzzz"), ("", ""))


class RelationsTest(EngineTestCase):
    def test_rational_gets_everyday_words_for_the_senses_shown_and_never_noetic(self):
        d = self.lookup("rational")
        terms = [s["term"] for s in d["synonyms"]]
        self.assertEqual(terms, ["logical", "reasonable", "sensible"])
        self.assertNotIn("noetic", terms)  # nobody says it (not in the subtitles)
        self.assertNotIn("ratiocinative", terms)  # Wiktionary labels it rare
        self.assertNotIn("sane", terms)  # not frequent enough in the sample subtitles
        self.assertEqual({s["sense"] for s in d["synonyms"] if s["term"] != "logical"},
                         {"adjective: Not contrary to reason; logical, reasonable, sensible"})  # the sense the hint describes
        self.assertEqual([a["term"] for a in d["antonyms"]], ["irrational"])
        self.assertTrue(all(len(d[k]) <= 5 for k in ("synonyms", "antonyms")))

    def test_synonyms_of_senses_that_are_not_shown_are_not_shown(self):
        d = self.lookup("trump")
        terms = {s["term"] for s in d["synonyms"]}
        self.assertFalse(terms & {"cornet", "horn", "outdo", "outflank", "beat", "top"})  # the trumpet and "to outdo" senses are hidden

    def test_possess_gets_own_and_have(self):
        d = self.lookup("possess")
        self.assertEqual([s["term"] for s in d["synonyms"]], ["own", "have"])  # not "bewitch" (dated) or "dominate" (no one says it here)
        self.assertEqual(d["synonyms"][0]["sense"], "verb: To have something as, or as if as, an owner; to have, to own")
        self.assertTrue(d["synonyms"][0]["example_en"])  # an example for each, from the same sources as any example

    def test_no_forced_filling(self):
        self.assertEqual(self.lookup("bank")["synonyms"], [])
        self.assertEqual(self.lookup("bank")["antonyms"], [])

    def test_ordinary_word_test(self):
        entries = [{"word": "bonnie", "pos": "Adjective", "senses": [{"def": "Beautiful.", "tags": ["scotland"]}]}]
        with mock.patch.object(le.LEXICON, "entries", return_value=entries):
            self.assertFalse(le._is_ordinary_word("bonnie"))
        self.assertTrue(le._is_ordinary_word("logical"))
        self.assertTrue(le._is_ordinary_word("go down"))  # no entry: left to the frequency test


class FormsRootsAndExpressionsTest(EngineTestCase):
    def test_privilege_keeps_only_common_expressions_once(self):
        d = self.lookup("privilege")
        self.assertEqual(d["common_expressions"], ["check your privilege", "white privilege"])
        self.assertNotIn("derived_terms", d)
        for rare in ("deprivilege", "privilegeless", "unprivileged", "cisprivilege", "cis-privilege", "cis privilege"):
            self.assertNotIn(rare, d["common_expressions"])

    def test_rational_drops_birational_and_extrarational(self):
        d = self.lookup("rational")
        self.assertNotIn("birational", d["common_expressions"])
        self.assertNotIn("extrarational", d["common_expressions"])

    def test_spelling_variants_of_one_expression_count_once_spaced_form_first(self):
        said = {"cis privilege": 9, "cis-privilege": 9, "cisprivilege": 0, "white privilege": 20, "x y": 1}
        with mock.patch.object(le.LEXICON, "phrase_hits", side_effect=lambda p, limit=25: said.get(p, 0)):
            out = le.common_expressions("privilege", ["cis-privilege", "cisprivilege", "cis privilege", "white privilege", "x y"], [])
        self.assertEqual(out, ["white privilege", "cis-privilege"][:1] + ["cis privilege"])  # commonest first; one of the three
        with mock.patch.object(le.LEXICON, "phrase_hits", return_value=30):
            many = le.common_expressions("x", [f"x thing {i}" for i in range(20)], [])
        self.assertEqual(len(many), le.MAX_COMMON_EXPRESSIONS)

    def test_a_collocation_listed_by_wiktionary_counts_even_if_rare_in_subtitles(self):
        with mock.patch.object(le.LEXICON, "phrase_hits", return_value=0):
            self.assertEqual(le.common_expressions("bank", [], ["blood bank"]), ["blood bank"])

    def test_forms_are_inflections_only(self):
        d = self.lookup("possess")
        self.assertEqual([(f["form"], f["label"]) for f in d["forms"]],
                         [("possesses", "third-person singular"), ("possessing", "present participle"), ("possessed", "past participle, past")])
        self.assertNotIn("possessed", d["common_expressions"])  # an inflection, not an expression
        self.assertEqual(d["morphology"]["root"], "possess")

    def test_an_inflected_word_names_its_root(self):
        d = self.lookup("possessed")
        self.assertEqual(d["morphology"]["root"], "possess")
        self.assertEqual(d["senses"][0]["def"][:10], "To have so")  # the lemma's entry

    def test_alternatives(self):
        self.assertEqual(self.lookup("color")["alternatives"], [])  # colour is not said in the sample subtitles
        with mock.patch.object(le, "is_everyday_word", return_value=True):
            self.assertEqual(self.lookup("color")["alternatives"], ["colour"])

    def test_the_word_family_names_everyday_relatives_with_their_part_of_speech(self):
        with mock.patch.object(le, "is_everyday_word", side_effect=lambda w, floor=1.0: w != "possessable"):
            family = le._word_family("possess", "possess", ["possessable", "possessed", "possession", "birational"],
                                     ["possession", "possessive"], lambda w: "Noun" if w == "possession" else "")
        self.assertEqual(family, [{"pos": "Noun", "word": "possession"}])

    def test_a_prefix_must_leave_a_word_people_say(self):
        self.assertEqual(le.analyze_morphology("colour")["prefix"], "None")  # "lour" is a word, but nobody says it
        with mock.patch.object(le, "is_everyday_word", return_value=True):
            self.assertNotEqual(le.analyze_morphology("rebook")["root"], "rebook")  # rebook = re- + book


class IpaTest(EngineTestCase):
    stub_sentence_ipa = False

    def test_headword_ipa_from_wiktionary_is_in_the_one_notation(self):
        self.assertEqual(self.lookup("privilege")["ipa_us"], "/ˈprɪvɪlɪdʒ/")  # ɹ -> r, tie bar and syllable dots gone
        self.assertEqual(self.lookup("trump")["ipa_us"], "/trʌmp/")
        self.assertEqual(self.lookup("possess")["ipa_us"], "/pəˈzɛs/")  # the American one

    def test_engine_ipa_uses_the_same_notation(self):
        for text in ("church", "bird", "letter", "great", "judge", "privilege"):
            ipa = le.to_ipa_us(text)
            for symbol in FORBIDDEN_IPA:
                self.assertNotIn(symbol, ipa, text)
        self.assertIn("tʃ", le.to_ipa_us("church"))
        self.assertTrue(le.to_ipa_us("run").startswith("/r"))

    def test_sentence_ipa_and_display_ipa_share_it_too(self):
        sentence = le.sentence_to_ipa("The bird is great and the church is large.")
        for symbol in FORBIDDEN_IPA:
            self.assertNotIn(symbol, sentence)
        self.assertEqual(le.clean_ipa_display("/ˈpɹɪv.ɪ.lɪd͡ʒ/"), "/ˈprɪvɪlɪdʒ/")
        self.assertEqual(le.clean_ipa_display(""), "/-/")
        self.assertEqual(le.clean_ipa_display("/-/"), "/-/")

    def test_synonym_and_example_ipa_in_the_payload(self):
        d = self.lookup("rational")
        self.assertTrue(d["synonyms"] and d["senses"][0]["example_ipa"])
        for item in d["synonyms"]:
            for symbol in FORBIDDEN_IPA:
                self.assertNotIn(symbol, item["ipa"] + item["example_ipa"])
        for s in d["senses"]:
            for symbol in FORBIDDEN_IPA:
                self.assertNotIn(symbol, s["example_ipa"])


class IdiomSearchTest(EngineTestCase):
    def test_rule_one_the_whole_phrase_is_looked_up_first(self):
        with mock.patch.object(le, "extract_sentence_key_terms", side_effect=AssertionError("word by word")):
            d = self.lookup("no bed of roses")
        self.assertEqual((d["headword"], d["is_idiom"], d["is_sentence"]), ("no bed of roses", True, False))
        self.assertNotIn("phrase_match", d)
        self.assertEqual(d["senses"][0]["def"], "Said of a situation or activity that is difficult or unpleasant.")

    def test_rule_two_normalised_matching_opens_the_base_entry(self):
        with mock.patch.object(le, "extract_sentence_key_terms", side_effect=AssertionError("word by word")):
            d = self.lookup("Dial it down")
        self.assertEqual((d["headword"], d["is_idiom"], d["is_sentence"]), ("dial down", True, False))
        self.assertEqual(d["phrase_match"], {"typed": "Dial it down", "entry": "dial down", "dropped": ["it"], "negated": False})
        self.assertEqual(d["senses"][0]["def"], "To reduce the intensity or level of something.")
        self.assertEqual(d["senses"][0]["example_en"], "Can you dial it down a little?")  # found under the typed phrase

    def test_inflections_negations_and_pronouns_all_find_the_base_entry(self):
        for typed, entry, negated in (("dialing it down", "dial down", False), ("she dials it down", None, False),
                                      ("not a bed of roses", "bed of roses", True), ("a bed of roses", "bed of roses", False),
                                      ("bring him to heel", "bring someone to heel", False), ("dialed down", "dial down", False)):
            with self.subTest(typed=typed):
                d = self.lookup(typed)
                if entry is None:
                    self.assertFalse(d.get("phrase_match"))
                    self.assertEqual(d["senses"], [])  # not in the dictionary: no pretending
                    continue
                self.assertEqual(d["headword"], entry)
                self.assertEqual(d.get("phrase_match", {}).get("negated", False), negated)

    def test_a_long_proverb_is_not_taken_for_a_sentence(self):
        with mock.patch.object(le, "extract_sentence_key_terms", side_effect=AssertionError("word by word")):
            d = self.lookup("A bird in the hand is worth two in the bush")
        self.assertEqual((d["is_sentence"], d["is_idiom"], d["headword"]), (False, True, "A bird in the hand is worth two in the bush"))
        self.assertTrue(d["senses"])

    def test_a_real_sentence_is_still_a_sentence(self):
        with mock.patch.object(le, "extract_sentence_key_terms", return_value=[]):
            d = self.lookup("She dials it down when he shouts at her.")
        self.assertTrue(d["is_sentence"])

    def test_the_idiom_gets_its_pre_translated_persian_and_no_machine_is_asked(self):
        with mock.patch.object(le, "_ct2_translate", side_effect=AssertionError("NLLB")), \
                mock.patch.object(le.SLM, "translate", side_effect=AssertionError("SLM")), \
                mock.patch.object(le, "_google_translate", side_effect=AssertionError("Google")):
            for typed, fa in (("Dial it down", "آرام تر باش"), ("no bed of roses", "کار آسانی نیست"), ("break the ice", "یخ را شکستن")):
                d = self.lookup(typed)
                self.assertEqual((d["persian_equivalent"], d["persian_source"]), (fa, le.FA_SOURCE_IDIOMS), typed)

    def test_the_idiom_dictionary_beats_a_cached_machine_translation(self):
        le.TRANSLATION_CACHE.put("dial it down", "ماشینی", le.FA_SOURCE_GOOGLE, le.QUALITY_GOOGLE, translation_cache.KIND_SENTENCE)
        self.assertEqual(le.translate_with_source("dial it down")[0], "آرام تر باش")


class PersianWordTest(EngineTestCase):
    def test_offline_an_equivalent_nobody_says_in_subtitles_gives_way_to_an_everyday_one(self):
        # privilege: امتیاز (3 subtitle occurrences) and حق ویژه (0): the common one is primary
        self.assertEqual(le.local_word_translation("privilege"), ("امتیاز", le.FA_SOURCE_WIKTIONARY))
        with mock.patch.object(le, "FA_MIN_SUBTITLE_COUNT", 10):
            self.assertEqual(le.local_word_translation("privilege"), ("", ""))  # nothing common enough: the machine tiers decide
        with mock.patch.object(le.LEXICON, "persian_frequency", return_value=None):  # a database without Persian frequencies
            self.assertEqual(le.local_word_translation("privilege")[0], "امتیاز")  # nothing to filter by: the first alternative

    def test_every_generated_translation_is_cached_for_good(self):
        le.set_offline_only(True)
        self.addCleanup(le.set_offline_only, False)
        with mock.patch.object(le, "_ct2_translate", return_value="ان ال ال بی") as nllb:
            first = le.translate_with_source("zeitgeist", is_short=True)
            second = le.translate_with_source("zeitgeist", is_short=True)
        self.assertEqual(first, ("ان ال ال بی", le.FA_SOURCE_NLLB))
        self.assertEqual(second, first)
        nllb.assert_called_once()  # the second time came from the cache

    def test_a_machine_translation_is_upgraded_when_google_answers(self):
        with mock.patch.object(le, "_ct2_translate", return_value="ان ال ال بی"), mock.patch.object(le, "_probe_connectivity", return_value=False):
            self.assertEqual(le.translate_with_source("zeitgeist", is_short=True)[1], le.FA_SOURCE_NLLB)
        le.forget_connectivity()
        with mock.patch.object(le, "_probe_connectivity", return_value=True), mock.patch.object(le, "_google_translate", return_value="روح زمان"):
            self.assertEqual(le.translate_with_source("zeitgeist", is_short=True), ("روح زمان", le.FA_SOURCE_GOOGLE))
        with mock.patch.object(le, "_probe_connectivity", return_value=False):
            le.forget_connectivity()
            self.assertEqual(le.translate_with_source("zeitgeist", is_short=True), ("روح زمان", le.FA_SOURCE_GOOGLE))  # now offline, still Google's

    def test_a_worse_translation_never_replaces_a_better_cached_one(self):
        cache = le.TRANSLATION_CACHE
        cache.put("zeitgeist", "گوگل", le.FA_SOURCE_GOOGLE, le.QUALITY_GOOGLE)
        cache.put("zeitgeist", "ماشین", le.FA_SOURCE_NLLB, le.QUALITY_NLLB)
        self.assertEqual(cache.get("zeitgeist")[0], "گوگل")

    def test_the_preview_uses_whatever_is_cached(self):
        le.TRANSLATION_CACHE.put("heavy", "سنگین", le.FA_SOURCE_GOOGLE, le.QUALITY_GOOGLE)
        previews = []
        self.lookup("heavy", on_preview=previews.append)
        self.assertEqual(previews[0]["persian_equivalent"], "سنگین")


class RefreshTest(EngineTestCase):
    def test_refresh_asks_google_again_and_replaces_the_cached_translation(self):
        answers = iter(["اول", "دوم", "سوم"])
        google = mock.patch.object(le, "_google_translate", side_effect=lambda t, s: next(answers))
        with mock.patch.object(le, "_probe_connectivity", return_value=True), google as g:
            first = self.lookup("heavy")
            again = self.lookup("heavy")  # cached: Google is not asked
            calls_before_refresh = g.call_count
            refreshed = self.lookup("heavy", refresh=True)
        self.assertEqual((first["persian_equivalent"], again["persian_equivalent"]), ("اول", "اول"))
        self.assertEqual(refreshed["persian_equivalent"], "دوم")
        self.assertGreater(g.call_count, calls_before_refresh)
        self.assertEqual(le.TRANSLATION_CACHE.get("heavy")[0], "دوم")  # the better answer is kept
        self.assertEqual(refreshed["refresh_info"], {"online": True})
        self.assertNotIn("refresh_info", first)

    def test_refresh_without_internet_keeps_the_local_result_and_says_so(self):
        le.TRANSLATION_CACHE.put("heavy", "سنگین", le.FA_SOURCE_GOOGLE, le.QUALITY_GOOGLE)
        d = self.lookup("heavy", refresh=True)  # the probe fails
        self.assertEqual(d["persian_equivalent"], "سنگین")
        self.assertEqual(d["refresh_info"], {"online": False})

    def test_refresh_probes_the_connection_afresh(self):
        with mock.patch.object(le, "_probe_connectivity", return_value=False):
            self.assertFalse(le.is_online())
        with mock.patch.object(le, "_probe_connectivity", return_value=True):
            d = self.lookup("heavy", refresh=True)  # offline for 60 s by the memo, but a refresh does not trust it
        self.assertTrue(d["refresh_info"]["online"])

    def test_the_refresh_flag_does_not_leak_into_the_next_lookup(self):
        self.lookup("heavy", refresh=True)
        self.assertFalse(le._REFRESHING.get())


class SlangRoutingTest(EngineTestCase):
    def test_an_ordinary_word_shows_no_slang(self):
        d = self.lookup("bank")
        self.assertNotIn("Slang", [s["pos"] for s in d["senses"]])

    def test_slang_is_the_answer_when_it_is_all_there_is(self):
        self.assertEqual([s["pos"] for s in self.lookup("rizz")["senses"]], ["Slang"])
        self.assertEqual([s["pos"] for s in self.lookup("yeet")["senses"]], ["Slang"])


class OldDatabaseTest(unittest.TestCase):
    """A database built by the previous importer (no tags, relations, forms, phrase keys or Persian frequencies, and
    quotations used as examples) still gives clean pages: the engine filters at read time."""

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(tempfile.mkdtemp(), "old.db")
        db = sqlite3.connect(cls.path)
        db.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE entries (id INTEGER PRIMARY KEY, word TEXT NOT NULL, word_key TEXT NOT NULL, pos TEXT NOT NULL,
                                  ipa TEXT NOT NULL, senses TEXT NOT NULL, collocations TEXT NOT NULL, derived TEXT NOT NULL);
            CREATE TABLE form_of (form_key TEXT NOT NULL, lemma TEXT NOT NULL);
            CREATE TABLE parallel (id INTEGER PRIMARY KEY, corpus TEXT NOT NULL, en TEXT NOT NULL, en_key TEXT NOT NULL, fa TEXT NOT NULL, freq INTEGER NOT NULL);
            CREATE VIRTUAL TABLE parallel_fts USING fts5 (en, content = 'parallel', content_rowid = 'id');
            CREATE TABLE fa_translations (word TEXT NOT NULL, word_key TEXT NOT NULL, pos TEXT NOT NULL, sense TEXT NOT NULL, fa TEXT NOT NULL, fa_key TEXT NOT NULL);
        """)
        senses = {
            "rational": [
                {"def": "(mathematics) Capable of being expressed as the ratio of two integers.", "example": "The quotient of two rational numbers is again a rational number."},
                {"def": "Capable of reasoning.", "example": "Man is a rational creature."},
            ],
            "possess": [
                {"def": "To have (something) as an owner.", "example": "I am poffeft with an adulterate blot, / My bloud is mingled with the crime of luft: […]"},
                {"def": "(archaic) To occupy the attention or time of (someone).", "example": ""},
            ],
        }
        for i, (word, items) in enumerate(senses.items(), 1):
            db.execute("INSERT INTO entries VALUES (?, ?, ?, ?, '/ˈpɹɪv/', ?, '[]', '[]')",
                       (i, word, word, "Adjective" if word == "rational" else "Verb", json.dumps(items)))
        db.executemany("INSERT INTO parallel (corpus, en, en_key, fa, freq) VALUES ('tatoeba', ?, ?, ?, 1)", [
            ("They possess a house in the north.", "they possess a house in the north", "آنها خانه ای در شمال دارند."),
            ("I am poffeft with an adulterate blot, / My bloud is mingled.", "i am poffeft with an adulterate blot my bloud is mingled", "من"),
        ])
        db.execute("INSERT INTO parallel_fts (parallel_fts) VALUES ('rebuild')")
        db.commit()
        db.close()

    def setUp(self):
        use_lexicon(self, self.path)
        for p in (mock.patch.object(le, "_ct2_translate", return_value=""),
                  mock.patch.object(le, "_ct2_translate_many", side_effect=lambda texts: [""] * len(texts)),
                  mock.patch.object(le, "sentence_to_ipa", return_value=""),
                  mock.patch.dict(le.USER_MEMORY, {"overrides": {}, "entries": {}})):
            p.start()
            self.addCleanup(p.stop)

    def test_the_mathematical_sense_is_demoted_even_when_the_old_database_lists_it_first(self):
        d = le.analyze_with_dictionaries("rational")
        self.assertEqual([s["def"] for s in d["senses"]], ["Capable of reasoning."])
        self.assertEqual(d["senses"][0]["example_en"], "Man is a rational creature.")

    def test_a_garbled_quotation_stored_as_an_example_is_dropped_and_never_translated(self):
        translated = []
        with mock.patch.object(le, "translate_batch_fast", side_effect=lambda sentences, shorts: translated.extend(sentences) or {}):
            d = le.analyze_with_dictionaries("possess")
        self.assertEqual([s["example_en"] for s in d["senses"]], ["They possess a house in the north."])  # a Tatoeba sentence instead
        self.assertEqual(d["senses"][0]["example_fa"], "آنها خانه ای در شمال دارند.")
        self.assertEqual(translated, [])
        self.assertNotIn("(archaic)", " ".join(s["def"] for s in d["senses"]))  # a label in the text still ranks the sense last

    def test_the_new_optional_features_are_simply_absent(self):
        d = le.analyze_with_dictionaries("possess")
        self.assertEqual((d["forms"], d["alternatives"], d["synonyms"]), ([], [], []))
        self.assertIsNone(le.LEXICON.match_phrase("it down"))
        self.assertEqual(le.LEXICON.slang("x"), [])
        self.assertEqual(le.LEXICON.persian_frequency("کتاب"), None)
        self.assertEqual(le.LEXICON.idiom_fa("break the ice"), None)

    def test_idioms_work_on_an_old_database_through_the_lazily_built_phrase_index(self):
        db = sqlite3.connect(self.path)
        db.execute("INSERT INTO entries VALUES (9, 'bring someone to heel', 'bring someone to heel', 'Verb', '', ?, '[]', '[]')",
                   (json.dumps([{"def": "To make someone obey.", "example": ""}]),))
        db.commit()
        db.close()
        use_lexicon(self, self.path)
        self.assertEqual(le.LEXICON.match_phrase("bring him to heel")["entry"], "bring someone to heel")


class TranslationCacheTest(unittest.TestCase):
    def test_the_default_subtitle_threshold_is_several_occurrences(self):
        self.assertGreaterEqual(le.FA_MIN_SUBTITLE_COUNT, 5)

    def test_the_cache_is_a_file_that_survives_a_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "translation_cache.db")
        cache = translation_cache.TranslationCache(path)
        cache.put("Hello there", "سلام", "Google Translate", 3)
        cache.put("Hello there, friend.", "سلام دوست", "offline translator", 1, translation_cache.KIND_SENTENCE)
        cache.close()
        again = translation_cache.TranslationCache(path)
        self.assertEqual(again.get("hello there!"), ("سلام", "Google Translate", 3))  # keyed without case and punctuation
        self.assertIsNone(again.get("hello there", translation_cache.KIND_SENTENCE))  # a sentence is a different key
        self.assertEqual(again.get("Hello there, friend.", translation_cache.KIND_SENTENCE)[0], "سلام دوست")
        self.assertEqual(again.count(), 2)

    def test_quality_ranks(self):
        cache = translation_cache.TranslationCache(":memory:")
        cache.put("a b", "x", "nllb", 1)
        cache.put("a b", "y", "slm", 2)
        self.assertEqual(cache.get("a b")[:2], ("y", "slm"))
        cache.put("a b", "z", "nllb", 1)
        self.assertEqual(cache.get("a b")[0], "y")
        cache.put("a b", "w", "google", 3)
        cache.put("a b", "w2", "google", 3)  # the same quality replaces: a Refresh
        self.assertEqual(cache.get("a b")[0], "w2")
        cache.put("  ", "x", "s", 3)
        cache.put("a", "", "s", 3)
        self.assertEqual(cache.count(), 1)

    def test_an_unusable_cache_never_raises(self):
        broken = translation_cache.TranslationCache(os.path.join(tempfile.mkdtemp(), "no", "such", "dir", "c.db"))
        with self.assertLogs("ocean.cache", "ERROR"):
            self.assertIsNone(broken.get("x"))
        broken.put("x", "y", "s", 1)
        self.assertEqual((broken.get("x"), broken.count()), (None, 0))

    def test_the_engine_cache_lives_in_the_user_data_folder(self):
        self.assertTrue(os.path.abspath(le.TRANSLATION_CACHE.path).startswith(os.path.abspath(le.USER_DATA_DIR)))


if __name__ == "__main__":
    unittest.main()
