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


class FakeLemma:
    def __init__(self, name, antonyms=()):
        self._name, self._antonyms = name, list(antonyms)

    def name(self):
        return self._name

    def antonyms(self):
        return [FakeLemma(a) for a in self._antonyms]

    def derivationally_related_forms(self):  # morphology asks for these
        return []


class FakeSynset:
    def __init__(self, pos, definition, lemmas, examples=()):
        self._pos, self._def, self._lemmas, self._examples = pos, definition, lemmas, list(examples)

    def pos(self):
        return self._pos

    def definition(self):
        return self._def

    def examples(self):
        return self._examples

    def lemmas(self):
        return self._lemmas


class FakeWordNet:
    """Two senses of "bank" (WordNet lists the river one first) and a verb."""
    SYNSETS = {
        "bank": [
            FakeSynset("n", "sloping land beside a body of water", [FakeLemma("bank"), FakeLemma("riverbank"), FakeLemma("riverside")]),
            FakeSynset("v", "do business with a bank", [FakeLemma("bank"), FakeLemma("deposit", ["withdraw"])]),
            FakeSynset("n", "a financial institution", [FakeLemma("depository_financial_institution"), FakeLemma("banking_company"),
                                                        FakeLemma("bank_building")], ["he cashed a check at the bank"]),
        ],
        "zeitgeist": [FakeSynset("n", "the spirit of the time", [FakeLemma("zeitgeist"), FakeLemma("spirit_of_the_age")],
                                 ["the zeitgeist of the sixties"])],
    }

    def synsets(self, word):
        return list(self.SYNSETS.get(word, []))


def use_fake_wordnet(test, wn=None):
    for name, value in (("wn", wn or FakeWordNet()), ("LEMMATIZER", None)):
        p = mock.patch.object(le, name, value)
        p.start()
        test.addCleanup(p.stop)


def enable_every_source():
    for name in ds.REGISTRY.names():
        ds.REGISTRY.set_enabled(name, True)


class ContractTest(unittest.TestCase):
    def test_the_four_datasets_in_merge_order(self):
        self.assertEqual(ds.REGISTRY.names(), ["kaikki", "wordnet", "urban_dictionary", "corpora"])
        for source in ds.REGISTRY.all():
            self.assertIsInstance(source, DictionarySource)
            self.assertTrue(source.label)
        self.assertEqual(ds.REGISTRY.get("wordnet").needs, ("kaikki",))

    def test_no_dictionary_source_touches_the_network(self):
        use_lexicon(self)
        use_fake_wordnet(self)
        with mock.patch.object(le, "smart_request", side_effect=AssertionError("network")):
            for term in ("bank", "break the ice", "rizz"):
                ds.REGISTRY.run(LookupContext(term=term, is_idiom=" " in term))


class SourceTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)
        use_fake_wordnet(self)

    def lookup(self, name, term, prior=None, **ctx):
        return ds.REGISTRY.get(name).lookup(LookupContext(term=term, **ctx), prior)

    def test_kaikki_word(self):
        e = self.lookup("kaikki", "bank")
        self.assertEqual((e.ipa_us, e.primary_pos), ("/bæŋk/", "Noun"))
        self.assertEqual([(s["pos"], s["def"]) for s in e.senses], [
            ("Noun", "An institution where one can place and borrow money."),
            ("Noun", "Sloping land beside a river."),
            ("Verb", "To deposit money in a bank."),
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

    def test_wordnet_groups_synonyms_by_sense_main_part_of_speech_first(self):
        prior = PartialEntry(primary_pos="Noun", senses=[{"pos": "Noun", "def": "x"}])
        e = self.lookup("wordnet", "bank", prior)
        self.assertEqual(e.senses, [])  # Wiktionary already defined it
        river = "noun: Sloping land beside a body of water"
        money = "noun: A financial institution"
        self.assertEqual([(s["term"], s["sense"]) for s in e.syns], [
            ("riverbank", river), ("riverside", river),
            ("depository financial institution", money), ("banking company", money), ("bank building", money),
            ("deposit", "verb: Do business with a bank"),
        ])
        self.assertEqual(e.ants, [{"term": "withdraw", "sense": "verb: Do business with a bank"}])

    def test_wordnet_defines_a_word_wiktionary_lacks(self):
        e = self.lookup("wordnet", "zeitgeist", PartialEntry())
        self.assertEqual(e.senses, [{"pos": "Noun", "def": "The spirit of the time.", "example_en": "The zeitgeist of the sixties."}])
        self.assertEqual(e.primary_pos, "Noun")

    def test_urban_dictionary_slang(self):
        e = self.lookup("urban_dictionary", "rizz")
        self.assertEqual(e.senses, [{"pos": "Slang", "def": "Charisma, especially in flirting.", "example_en": "He has so much rizz."}])

    def test_corpora_examples_and_frequency(self):
        e = self.lookup("corpora", "bank")
        self.assertEqual(e.examples[0], {"en": "I'm going to the bank.", "fa": "من دارم به بانک می روم."})
        self.assertIsNone(e.frequency)  # too rare in the sample subtitles
        self.assertIsNotNone(self.lookup("corpora", "break").frequency)
        self.assertIsNone(self.lookup("corpora", "break a leg", is_idiom=True).frequency)


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
        merged = merge_entries([PartialEntry(ipa_us="/a/", derived=["x"]), PartialEntry(ipa_us="/b/", frequency=2.0, derived=["y"])])
        self.assertEqual((merged.ipa_us, merged.frequency, merged.derived), ("/a/", 2.0, ["x", "y"]))


class EngineIntegrationTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)
        use_fake_wordnet(self)
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
        self.assertEqual(le.ENGINE_VERSION, 2)
        self.assertEqual((d["headword"], d["ipa_us"], d["is_idiom"]), ("bank", "/bæŋk/", False))
        self.assertEqual((d["persian_equivalent"], d["persian_source"]), ("بانک ، ساحل", le.FA_SOURCE_WIKTIONARY))
        self.assertEqual([s["pos"] for s in d["senses"]], ["Noun", "Noun", "Verb"])
        self.assertEqual(d["senses"][1]["example_en"], "I'm going to the bank.")  # a corpus pair fills the missing example...
        self.assertEqual(d["senses"][1]["example_fa"], "من دارم به بانک می روم.")  # ...with its human translation
        self.assertEqual([c["term"] for c in d["collocations"]], ["blood bank", "data bank"])
        self.assertEqual(d["derived_terms"], ["bank account", "bankroll"])
        syns = [(s["term"], s["sense"]) for s in d["synonyms"]]
        self.assertEqual(syns[:2], [("riverbank", "noun: Sloping land beside a body of water"),
                                    ("riverside", "noun: Sloping land beside a body of water")])
        self.assertEqual(len(syns), 5)  # at most two per sense (river 2, money 2, verb 1)
        self.assertEqual([a["term"] for a in d["antonyms"]], ["withdraw"])

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

    def test_slang_appears_as_its_own_sense(self):
        d = le.analyze_with_dictionaries("rizz")
        self.assertEqual([s["pos"] for s in d["senses"]], ["Slang"])

    def test_idiom(self):
        d = le.analyze_with_dictionaries("break the ice")
        self.assertTrue(d["is_idiom"])
        self.assertEqual(d["senses"][0]["example_en"], "He told a joke to break the ice.")
        self.assertEqual((d["collocations"], d["derived_terms"]), ([], []))

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
        self.assertEqual(d["senses"][0]["def"], "The spirit of the time.")  # WordNet
        self.assertEqual(d["persian_equivalent"], le.NOT_FOUND_FA)


if __name__ == "__main__":
    unittest.main()
