"""The dictionary sources over the fixture database (tests/lexicon_fixture.py), the registry that runs and merges
them, and whole lookups built from them."""
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
import dictionary_sources as ds  # noqa: E402
from dictionary_sources import LookupContext, PartialEntry, DictionarySource, SourceRegistry, merge_entries  # noqa: E402


def enable_every_source():
    for name in ds.REGISTRY.names():
        ds.REGISTRY.set_enabled(name, True)


class ContractTest(unittest.TestCase):
    def test_the_three_datasets_in_merge_order(self):
        self.assertEqual(ds.REGISTRY.names(), ["kaikki", "urban_dictionary", "corpora"])  # no WordNet any more
        for source in ds.REGISTRY.all():
            self.assertIsInstance(source, DictionarySource)
            self.assertTrue(source.label)
        self.assertEqual(ds.REGISTRY.get("urban_dictionary").needs, ("kaikki",))
        with self.assertRaises(KeyError):
            ds.REGISTRY.get("wordnet")

    def test_no_dictionary_source_touches_the_network(self):
        use_lexicon(self)
        with mock.patch.object(le, "smart_request", side_effect=AssertionError("network")):
            for term in ("bank", "break the ice", "rizz"):
                ds.REGISTRY.run(LookupContext(term=term, is_idiom=" " in term))


class SourceTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)

    def lookup(self, name, term, prior=None, **ctx):
        return ds.REGISTRY.get(name).lookup(LookupContext(term=term, **ctx), prior)

    def test_kaikki_word(self):
        e = self.lookup("kaikki", "bank")
        self.assertEqual((e.ipa_us, e.primary_pos), ("/bæŋk/", "Noun"))
        self.assertEqual([(s["pos"], s["def"], s["klass"]) for s in e.senses], [
            ("Noun", "An institution where one can place and borrow money.", 0),
            ("Noun", "Sloping land beside a river.", 0),
            ("Noun", "A bench.", 3),  # obsolete: kept, ranked last, never shown
            ("Verb", "To deposit money in a bank.", 0),
        ])
        self.assertEqual(e.senses[0]["example_en"], "I keep my savings in a bank.")
        self.assertEqual([c["term"] for c in e.collocations], ["blood bank", "data bank"])
        self.assertEqual(e.derived, ["bank account", "bankroll"])

    def test_kaikki_inflected_form_and_idiom(self):
        ran = self.lookup("kaikki", "ran")
        self.assertEqual(ran.senses[0]["def"], "To move swiftly on foot.")
        self.assertEqual(ran.ipa_us, "")  # run's /ɹʌn/ is not how "ran" sounds: the IPA engine fills it in
        e = self.lookup("kaikki", "break the ice", is_idiom=True)
        self.assertEqual([s["pos"] for s in e.senses], ["Idiom / Expression"])  # the literal-use note was not imported
        self.assertEqual(e.senses[0]["def"], "(idiomatic) To start a conversation with strangers.")
        self.assertEqual((e.collocations, e.derived), ([], []))

    def test_kaikki_ipa_is_in_one_notation(self):
        self.assertEqual(self.lookup("kaikki", "privilege").ipa_us, "/ˈprɪvɪlɪdʒ/")  # ɹ -> r, no tie bar, no syllable dots
        self.assertEqual(self.lookup("kaikki", "run").ipa_us, "/rʌn/")

    def test_kaikki_senses_carry_class_tags_and_what_they_relate_to(self):
        e = self.lookup("kaikki", "trump")
        by_def = {s["raw_def"]: s for s in e.senses}
        self.assertEqual(by_def["A surname."]["klass"], 3)
        self.assertEqual(by_def["A trumpet."]["klass"], 3)
        self.assertEqual(by_def["Something that gives one an advantage, especially one held in reserve."]["tags"], ["figuratively"])
        self.assertIn({"term": "cornet", "pos": "Noun", "raw_def": "A trumpet.", "hint": "", "tags": ""}, e.syns)
        self.assertIn("trumps", [f["form"] for f in e.forms])
        self.assertEqual(e.senses[0]["example_en"], "Diamonds were declared trump.")  # the "(s)" artefact cleaned

    def test_kaikki_examples_that_are_damaged_are_dropped_even_from_an_old_database(self):
        e = self.lookup("kaikki", "possess")
        self.assertEqual([s["example_en"] for s in e.senses],
                         ["He does not even possess a working telephone.", "", "They thought he was possessed by evil spirits."])
        with mock.patch.object(le.LEXICON, "entries", return_value=[{
            "word": "possess", "pos": "Verb", "ipa": "", "collocations": [], "derived": [], "synonyms": [], "antonyms": [],
            "forms": [], "related": [],
            "senses": [{"def": "(informal) To have.", "example": "I am poffeft with an adulterate blot, / My bloud is mingled: […]"},
                       {"def": "To own.", "example": "He possesses a car."}],
        }]):
            old = self.lookup("kaikki", "possess")
        self.assertEqual([s["example_en"] for s in old.senses], ["", "He possesses a car."])
        self.assertEqual(old.senses[0]["def"], "(informal) To have.")  # labels in the definition (an older import) still read

    def test_urban_dictionary_slang_only_where_wiktionary_has_nothing(self):
        e = self.lookup("urban_dictionary", "rizz", PartialEntry())
        self.assertEqual([(s["pos"], s["def"], s["example_en"]) for s in e.senses], [("Slang", "Charisma, especially in flirting.", "He has so much rizz.")])
        prior = self.lookup("kaikki", "bank")
        self.assertEqual(self.lookup("urban_dictionary", "bank", prior).senses, [])  # an ordinary word stays clean
        self.assertEqual(self.lookup("urban_dictionary", "bank", PartialEntry()).senses[0]["def"], "To be sure of; as in you can bank on it.")

    def test_urban_dictionary_strong_slang_of_a_rare_word_is_shown_beside_wiktionary(self):
        strong = [{"def": "A stronger meaning.", "example": "", "up": 900, "down": 20, "ratio": 0.97}]
        prior = PartialEntry(senses=[{"pos": "Noun", "def": "x", "klass": 0}])
        with mock.patch.object(le.LEXICON, "slang", return_value=strong), mock.patch.object(le.LEXICON, "frequency", return_value=0.1):
            self.assertEqual(len(self.lookup("urban_dictionary", "simp", prior).senses), 1)
        with mock.patch.object(le.LEXICON, "slang", return_value=strong), mock.patch.object(le.LEXICON, "frequency", return_value=50.0):
            self.assertEqual(self.lookup("urban_dictionary", "simp", prior).senses, [])  # a common word: no
        weak = [dict(strong[0], up=30)]
        with mock.patch.object(le.LEXICON, "slang", return_value=weak), mock.patch.object(le.LEXICON, "frequency", return_value=0.1):
            self.assertEqual(self.lookup("urban_dictionary", "simp", prior).senses, [])

    def test_urban_dictionary_looks_for_the_typed_phrase_too(self):
        e = self.lookup("urban_dictionary", "dial down", PartialEntry(), is_idiom=True, typed="dial it down")
        self.assertEqual(e.senses[0]["def"], "To tone down intensity or attitude.")

    def test_corpora_examples_come_tagged_with_their_corpus_and_frequency(self):
        e = self.lookup("corpora", "bank")
        self.assertEqual([(x["corpus"], x["en"]) for x in e.examples], [
            ("opensubtitles", "The bank is closed today, sorry."),
            ("tatoeba", "The river bank was muddy after the rain."),
            ("tatoeba", "I'm going to the bank."),
        ])
        self.assertIsNone(e.frequency)  # too rare in the sample subtitles
        self.assertIsNotNone(self.lookup("corpora", "break").frequency)
        self.assertIsNone(self.lookup("corpora", "break a leg", is_idiom=True).frequency)

    def test_corpora_also_search_the_typed_phrase(self):
        e = self.lookup("corpora", "dial down", is_idiom=True, typed="dial it down")
        self.assertEqual(e.examples[0]["en"], "Can you dial it down a little?")


class RegistryRunTest(unittest.TestCase):
    def make(self, *specs):
        reg = SourceRegistry()
        calls = []
        for name, needs, fill in specs:
            src = type(name, (DictionarySource,), {
                "name": name, "label": name, "needs": needs,
                "fetch": lambda self, ctx, entry, prior, fill=fill: (calls.append(self.name), fill(entry, prior)),
            })()
            reg.register(src)
        return reg, calls

    def test_merge_order_needs_and_disabled_sources(self):
        reg, calls = self.make(
            ("a", (), lambda e, p: e.senses.append({"def": "from a"})),
            ("b", ("a",), lambda e, p: e.senses.append({"def": f"b saw {len(p.senses)}"})),
            ("c", (), lambda e, p: setattr(e, "ipa_us", "/c/")),
        )
        merged = reg.run(LookupContext(term="x"))
        self.assertEqual([s["def"] for s in merged.senses], ["from a", "b saw 1"])
        self.assertEqual(merged.ipa_us, "/c/")
        reg.set_enabled("a", False)
        calls.clear()
        merged = reg.run(LookupContext(term="x"))
        self.assertNotIn("a", calls)
        self.assertEqual([s["def"] for s in merged.senses], ["b saw 0"])  # a need that is switched off is just absent
        for name in reg.names():
            reg.set_enabled(name, False)
        self.assertEqual(reg.run(LookupContext(term="x")), PartialEntry())
        self.assertFalse(reg.any_enabled())

    def test_registration_rules(self):
        reg, _ = self.make(("a", (), lambda e, p: None))
        with self.assertRaises(ValueError):
            self.make(("b", ("missing",), lambda e, p: None))
        with self.assertRaises(ValueError):
            reg.register(reg.get("a"))
        with self.assertRaises(KeyError):
            reg.set_enabled("cambridge", False)

    def test_failures_are_logged_with_the_source_name_and_keep_partial_results(self):
        def broken(e, p):
            e.syns.append({"term": "kept", "sense": ""})
            raise ValueError("parser broke")

        reg, _ = self.make(("broken", (), broken), ("fine", (), lambda e, p: e.senses.append({"def": "ok"})))
        with self.assertLogs("ocean", "ERROR") as logs:
            merged = reg.run(LookupContext(term="x"))
        self.assertIn("Source 'broken' failed for 'x'", logs.output[0])
        self.assertEqual((merged.syns[0]["term"], merged.senses[0]["def"]), ("kept", "ok"))

    def test_merge_entries_keeps_the_first_ipa_and_frequency(self):
        merged = merge_entries([PartialEntry(ipa_us="/a/", derived=["x"], forms=[{"form": "f", "label": ""}]),
                                PartialEntry(ipa_us="/b/", frequency=2.0, derived=["y"], alternatives=["alt"], related=["r"])])
        self.assertEqual((merged.ipa_us, merged.frequency, merged.derived), ("/a/", 2.0, ["x", "y"]))
        self.assertEqual((merged.forms, merged.alternatives, merged.related), ([{"form": "f", "label": ""}], ["alt"], ["r"]))

    def test_the_context_names_the_typed_phrase_only_when_it_differs(self):
        self.assertEqual(LookupContext(term="dial down", typed="Dial it down").aliases, ["Dial it down"])
        self.assertEqual(LookupContext(term="dial down", typed="Dial Down").aliases, [])
        self.assertEqual(LookupContext(term="bank").aliases, [])


class EngineIntegrationTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)
        self.addCleanup(enable_every_source)
        for p in (
            mock.patch.object(le, "_google_translate", return_value=""),
            mock.patch.object(le, "_ct2_translate", return_value=""),
            mock.patch.object(le, "_ct2_translate_many", side_effect=lambda texts: [""] * len(texts)),
            mock.patch.object(le.SLM, "translate", return_value=""),
            mock.patch.object(le, "sentence_to_ipa", return_value=""),
            mock.patch.dict(le.USER_MEMORY, {"overrides": {}, "entries": {}}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_word_lookup(self):
        d = le.analyze_with_dictionaries("bank")
        self.assertEqual(le.ENGINE_VERSION, 3)
        self.assertEqual((d["headword"], d["ipa_us"], d["is_idiom"]), ("bank", "/bæŋk/", False))
        self.assertEqual((d["persian_equivalent"], d["persian_source"]), ("بانک ، ساحل", le.FA_SOURCE_WIKTIONARY))
        # two ordinary noun senses and the verb (the obsolete "A bench." is never shown), each with one example
        self.assertEqual([(s["pos"], s["def"]) for s in d["senses"]], [
            ("Noun", "An institution where one can place and borrow money."),
            ("Noun", "Sloping land beside a river."),
            ("Verb", "To deposit money in a bank."),
        ])
        self.assertEqual(d["senses"][0]["example_en"], "The bank is closed today, sorry.")  # a subtitle line first
        self.assertEqual(d["senses"][0]["example_source"], "OpenSubtitles")
        self.assertEqual(d["senses"][0]["example_fa"], "امروز بانک تعطیله، ببخشید.")  # with its human translation
        self.assertEqual(d["senses"][1]["example_source"], "Tatoeba")  # no subtitle line left, no Wiktionary example for it
        self.assertEqual(d["senses"][2]["example_en"], "I bank with a local branch.")  # Wiktionary's own, before Tatoeba
        self.assertEqual([c["term"] for c in d["collocations"]], ["blood bank", "data bank"])
        self.assertEqual(d["common_expressions"], d["derived_terms"])
        self.assertEqual([f["form"] for f in d["forms"]], [])
        self.assertEqual(d["synonyms"], [])  # Wiktionary lists none, and nothing is made up
        self.assertEqual(d["antonyms"], [])

    def test_the_text_never_comes_from_wordnet(self):
        import lexical_engine
        self.assertFalse(hasattr(lexical_engine, "wn"))
        self.assertFalse(hasattr(lexical_engine, "LEMMATIZER"))
        self.assertNotIn("nltk", sys.modules)

    def test_a_preview_from_local_data_comes_before_the_translations(self):
        order, previews = [], []

        def batch(sentences, shorts):
            order.append("batch")
            return {s: "ترجمه" for s in list(sentences) + list(shorts)}

        with mock.patch.object(le, "translate_batch_fast", side_effect=batch):
            d = le.analyze_with_dictionaries("bank", on_preview=lambda p: (order.append("preview"), previews.append(p)))
        self.assertEqual(order, ["preview", "batch"])
        p = previews[0]
        self.assertTrue(p["_preview"])
        self.assertEqual(p["persian_equivalent"], "بانک ، ساحل")  # the local tier, already
        self.assertEqual(p["senses"][0]["example_fa"], "امروز بانک تعطیله، ببخشید.")  # a subtitle pair: already known
        self.assertEqual(p["senses"][2]["example_fa"], le.PENDING_FA)  # Wiktionary's example: still to translate
        self.assertNotIn("_preview", d)
        self.assertEqual(d["senses"][2]["example_fa"], "ترجمه")

    def test_no_preview_for_a_sentence(self):
        with mock.patch.object(le, "extract_sentence_key_terms", return_value=[]):
            le.analyze_with_dictionaries("We went to the bank this morning.", on_preview=self.fail)

    def test_examples_are_never_made_up(self):
        d = le.analyze_with_dictionaries("heavy")  # Wiktionary gives no example and no corpus sentence has the word
        self.assertEqual(d["senses"][0]["example_en"], "")

    def test_level_and_subtitle_frequency(self):
        d = le.analyze_with_dictionaries("break")
        self.assertIsNotNone(d["frequency_per_million"])
        self.assertTrue(d["frequency_band"])
        self.assertIn("cefr_level", d)

    def test_did_you_mean_and_suggestions_come_from_the_dictionary(self):
        d = le.analyze_with_dictionaries("brigt")
        self.assertEqual((d["senses"], d["did_you_mean"]), ([], "bright"))
        self.assertEqual(le.fetch_live_suggestions("ba"), ["bank", "Banks"])
        self.assertEqual(le.fetch_live_suggestions("کتا"), [])

    def test_slang_appears_as_its_own_sense_when_it_is_the_only_match(self):
        d = le.analyze_with_dictionaries("rizz")
        self.assertEqual([s["pos"] for s in d["senses"]], ["Slang"])

    def test_idiom(self):
        d = le.analyze_with_dictionaries("break the ice")
        self.assertTrue(d["is_idiom"])
        self.assertEqual(d["senses"][0]["example_en"], "He told a joke to break the ice.")
        self.assertEqual((d["collocations"], d["derived_terms"], d["common_expressions"]), ([], [], []))
        self.assertEqual((d["persian_equivalent"], d["persian_source"]), ("یخ را شکستن", le.FA_SOURCE_IDIOMS))

    def test_lookup_works_with_every_source_switched_off(self):
        for name in ds.REGISTRY.names():
            ds.REGISTRY.set_enabled(name, False)
        d = le.analyze_with_dictionaries("bank")
        self.assertEqual(d["senses"], [])
        self.assertEqual(d["persian_equivalent"], "بانک ، ساحل")  # the translation tiers still answer

    def test_sentence_breakdown_finds_dictionary_phrases_first(self):
        d = le.analyze_with_dictionaries("They tried hard to break the ice with the new bank manager.")
        terms = [k["term"] for k in d["sentence_breakdown"]]
        self.assertEqual(terms[0], "break the ice")
        self.assertIn("bank", terms)
        self.assertNotIn("ice", terms)  # covered by the phrase
        d = le.analyze_with_dictionaries("We had to break the ice with the bank.")
        self.assertNotIn("the bank", [k["term"] for k in d["sentence_breakdown"]])  # an article is no phrase
        self.assertEqual(d["sentence_breakdown"][0]["pos"], "Idiom / Compound")

    def test_without_a_database_lookups_still_answer(self):
        use_lexicon(self, None)
        d = le.analyze_with_dictionaries("zeitgeist")
        self.assertEqual(d["senses"], [])  # no WordNet to fall back on
        self.assertEqual(d["persian_equivalent"], le.NOT_FOUND_FA)
        self.assertTrue(d["ipa_us"])  # the IPA engine still answers


if __name__ == "__main__":
    unittest.main()
