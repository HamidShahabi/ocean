import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlparse

import requests

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexical_engine as le  # noqa: E402
import dictionary_sources as ds  # noqa: E402
from dictionary_sources import LookupContext, PartialEntry, DictionarySource, SourceRegistry, merge_entries  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sources")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def json_fixture(name):
    return json.loads(fixture(name))


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)


NOT_FOUND = FakeResponse("", 404)


def fixture_site(method, url, **kwargs):
    """Serves the saved pages for 'bright' / 'break the ice' in place of the real sites."""
    u = urlparse(url)
    params = kwargs.get("params") or {}
    slug = u.path.rsplit("/", 1)[-1]
    if u.netloc == "dictionary.cambridge.org" and slug == "bright":
        return FakeResponse(fixture("cambridge_bright.html"))
    if u.netloc == "www.ldoceonline.com" and slug == "bright":
        return FakeResponse(fixture("longman_bright.html"))
    if u.netloc == "idioms.thefreedictionary.com" and u.path.lstrip("/") == "break+the+ice":
        return FakeResponse(fixture("thefreedictionary_break_the_ice.html"))
    if u.netloc == "en.wiktionary.org" and slug == "break_the_ice":
        return FakeResponse(fixture("wiktionary_break_the_ice.json"))
    if u.netloc == "api.dictionaryapi.dev" and slug == "bright":
        return FakeResponse(fixture("dictionaryapi_bright.json"))
    if u.netloc == "api.datamuse.com" and u.path == "/words" and params.get("rel_syn") == "bright":
        return FakeResponse(fixture("datamuse_syn_bright.json"))
    if u.netloc == "api.datamuse.com" and u.path == "/words" and params.get("sp") == "bright":
        return FakeResponse(fixture("datamuse_freq_bright.json"))
    if u.netloc == "api.datamuse.com" and u.path == "/words" and params.get("rel_ant") == "bright":
        return FakeResponse(fixture("datamuse_ant_bright.json"))
    if u.netloc == "api.datamuse.com" and u.path == "/words" and ("rel_jja" in params or "rel_jjb" in params):
        return FakeResponse(fixture("datamuse_colls_bright.json"))
    if u.netloc == "api.datamuse.com" and u.path == "/sug" and params.get("s") == "brigt":
        return FakeResponse(fixture("datamuse_sug_brigt.json"))
    if u.netloc == "tatoeba.org" and params.get("query") == "bright":
        return FakeResponse(fixture("tatoeba_bright.json"))
    return NOT_FOUND


def ctx_for(term, **kwargs):
    return LookupContext(term=term, requester=fixture_site, **kwargs)


class SourceFixtureTest(unittest.TestCase):
    """Each source parses a saved page: if a site's markup changes and the selectors stop matching,
    refresh the fixture from the real page and fix the parser; if these fail with the fixture
    untouched, the parser broke."""

    def test_cambridge_parses_headword_pronunciation_senses_and_synonyms(self):
        entry = PartialEntry()
        ds.CambridgeSource().parse(fixture("cambridge_bright.html"), "bright", entry)
        self.assertEqual(entry.ipa_us, "/braɪt/")
        self.assertEqual(entry.senses, [
            {"pos": "Adjective", "def": "Giving out or reflecting a lot of light.", "example_en": "The room was bright and sunny."},
            {"pos": "Adjective", "def": "Intelligent and quick to learn.", "example_en": "She was the brightest student in the class."},
            {"pos": "Adverb", "def": "In a way that gives out a lot of light.", "example_en": "The stars shone bright over the desert."},
        ])  # "when you ..." and pub definitions are dropped
        self.assertEqual(entry.syns, ["brilliant", "radiant", "good"])  # the headword itself is dropped

    def test_cambridge_ignores_pronunciation_of_a_different_headword(self):
        entry = PartialEntry()
        ds.CambridgeSource().parse(fixture("cambridge_bright.html"), "snug", entry)
        self.assertEqual(entry.ipa_us, "")

    def test_longman_parses_pronunciation_senses_and_collocations(self):
        entry = PartialEntry()
        ds.LongmanSource().parse(fixture("longman_bright.html"), "bright", entry)
        self.assertEqual(entry.ipa_us, "/braɪt/")
        self.assertEqual(entry.senses, [
            {"pos": "Adjective", "def": "Shining strongly with a lot of light.", "example_en": "The room was bright and airy."},
            {"pos": "Adjective", "def": "Clever and quick to learn.", "example_en": "She is a very bright child."},
            {"pos": "Adjective", "def": "Full of hope.", "example_en": ""},
        ])  # the 'when you ...' sense is dropped
        self.assertEqual(entry.collocations, [
            {"term": "bright light", "example_en": "She closed her eyes against the bright light of the sun."},
            {"term": "bright idea", "example_en": "Whose bright idea was it to come here?"},
            {"term": "bright future", "example_en": "He has a bright future ahead of him."},
        ])

    def test_thefreedictionary_parses_idiom_definitions_and_examples(self):
        entry = PartialEntry()
        ds.TheFreeDictionarySource().parse(fixture("thefreedictionary_break_the_ice.html"), "break the ice", entry)
        self.assertEqual(entry.senses, [
            {"pos": "Idiom / Expression",
             "def": "To do or say something to relax a tense situation.",
             "example_en": "He told a joke to break the ice at the start of the meeting."},
            {"pos": "Idiom / Expression",
             "def": "To start a conversation with people you do not know.",
             "example_en": "Asking about the weather is a good way to break the ice."},
            {"pos": "Idiom / Expression", "def": "To begin a project that others will then continue.", "example_en": ""},
        ])

    def test_wiktionary_parses_the_english_blocks(self):
        entry = PartialEntry()
        ds.WiktionarySource().parse(json_fixture("wiktionary_break_the_ice.json"), entry)
        self.assertEqual([(s["pos"], s["def"], s["example_en"]) for s in entry.senses], [
            ("Verb", "To initiate social contact in an awkward situation.", "Breaking the ice at a party can be hard."),
            ("Noun", "An act of breaking the ice.", "Her joke was a fine break the ice."),
        ])

    def test_dictionaryapi_parses_pronunciation_senses_and_relations(self):
        entry = PartialEntry()
        ds.DictionaryApiSource().parse(json_fixture("dictionaryapi_bright.json"), "bright", "bright", entry)
        self.assertEqual(entry.ipa_us, "/braɪt/")  # the entry with a '-us' recording wins
        self.assertEqual(entry.primary_pos, "Adjective")
        self.assertEqual(entry.senses, [
            {"pos": "Adjective", "def": "Emitting or reflecting much light; shining.", "example_en": "The bright sun made me squint."},
            {"pos": "Adjective", "def": "Intelligent and quick-witted.", "example_en": ""},  # example lacks the word
            {"pos": "Noun", "def": "A brilliant colour.", "example_en": ""},
            {"pos": "Adverb", "def": "In a bright manner.", "example_en": "The lamp burned bright all night."},
        ])
        self.assertEqual(entry.syns, ["brilliant", "shiny"])  # the headword, 'ok' and its own variants are dropped
        self.assertEqual(entry.ants, ["dim", "dull", "pale"])

    def test_datamuse_parses_synonyms_antonyms_and_collocations(self):
        ctx = ctx_for("bright")
        prior = PartialEntry(primary_pos="Adjective")
        entry = ds.DatamuseSource().lookup(ctx, prior)
        self.assertEqual(entry.syns, ["brilliant", "smart", "clever"])
        self.assertEqual(entry.ants, ["dim", "dull"])
        self.assertEqual(entry.collocation_terms, ["bright future", "bright light", "bright idea"])

    def test_datamuse_asks_for_the_collocation_matching_the_part_of_speech(self):
        asked = []

        def requester(method, url, **kwargs):
            asked.append(sorted(k for k in kwargs.get("params", {}) if k.startswith("rel_")))
            return FakeResponse("[]")
        ds.DatamuseSource().lookup(LookupContext(term="bright", requester=requester), PartialEntry(primary_pos="Noun"))
        self.assertEqual(asked, [["rel_syn"], ["rel_ant"], ["rel_jjb"]])

    def test_datamuse_frequency_reads_the_headwords_own_tag(self):
        entry = ds.DatamuseFrequencySource().lookup(ctx_for("bright"))
        self.assertEqual(entry.frequency, 31.2)  # not the near spelling "brighter" that comes with it
        self.assertIsNone(ds.DatamuseFrequencySource().lookup(ctx_for("zzzz")).frequency)  # 404
        parse = ds.DatamuseFrequencySource.parse
        self.assertIsNone(parse([{"word": "bright", "tags": ["n"]}], "bright"))
        self.assertIsNone(parse([{"word": "bright", "tags": ["f:oops"]}], "bright"))
        self.assertEqual(parse([{"word": "Bright", "tags": ["f:0.5"]}], "bright"), 0.5)

    def test_datamuse_frequency_asks_for_the_word_with_md_f_and_only_for_single_words(self):
        asked = []

        def requester(method, url, **kwargs):
            asked.append(kwargs.get("params"))
            return FakeResponse("[]")
        ds.DatamuseFrequencySource().lookup(LookupContext(term="bright", requester=requester))
        self.assertEqual(asked, [{"sp": "bright", "md": "f", "max": 1}])
        source = ds.REGISTRY.get("datamuse_frequency")
        self.assertTrue(source.applies(ctx_for("bright")))
        self.assertFalse(source.applies(ctx_for("break the ice", is_idiom=True)))
        self.assertFalse(source.applies(ctx_for("a*")))

    def test_datamuse_suggestions(self):
        with mock.patch.object(le, "smart_request", fixture_site):
            self.assertEqual(ds.DatamuseSource().suggest("brigt"), ["bright", "brigade", "brighton"])
            self.assertEqual(ds.DatamuseSource().suggest("zzzz"), [])

    def test_tatoeba_keeps_usable_sentence_pairs(self):
        entry = PartialEntry()
        ds.TatoebaSource().parse(json_fixture("tatoeba_bright.json"), "bright", entry)
        self.assertEqual([p["en"] for p in entry.examples], [
            "The room is bright and warm in the morning.",
            "She has a bright future ahead of her in this city.",
        ])  # too short, no target word and no Persian translation are skipped
        self.assertTrue(all(le.is_persian_text(p["fa"]) for p in entry.examples))


class ContractTest(unittest.TestCase):
    def test_required_sources_are_registered_with_the_shared_contract(self):
        for name in ("cambridge", "longman", "thefreedictionary", "wiktionary", "dictionaryapi", "datamuse", "tatoeba"):
            source = ds.REGISTRY.get(name)
            self.assertIsInstance(source, DictionarySource)
            self.assertEqual(source.name, name)
            self.assertTrue(source.label)
            self.assertIs(source.needs_network, True)

    def test_sources_reading_only_bundled_data_do_not_need_the_network(self):
        offline = [s.name for s in ds.REGISTRY.all() if not s.needs_network]
        self.assertEqual(offline, ["offline_idioms", "idiom_fallback", "curated_synonyms", "wordnet"])

    def test_registry_rejects_duplicates_and_forward_dependencies(self):
        class A(DictionarySource):
            name = "a"

        class B(DictionarySource):
            name = "b"
            needs = ("a",)

        registry = SourceRegistry()
        with self.assertRaises(ValueError):
            registry.register(B())
        registry.register(A())
        registry.register(B())
        with self.assertRaises(ValueError):
            registry.register(A())
        with self.assertRaises(KeyError):
            registry.set_enabled("nope", False)


class FailureLoggingTest(unittest.TestCase):
    def test_failures_are_logged_with_the_source_name_and_keep_partial_results(self):
        def requester(method, url, **kwargs):
            if "rel_ant" in kwargs.get("params", {}):
                raise ValueError("site changed")
            return fixture_site(method, url, **kwargs)
        with self.assertLogs("ocean", level="WARNING") as logs:
            entry = ds.DatamuseSource().lookup(LookupContext(term="bright", requester=requester), PartialEntry())
        self.assertIn("Source 'datamuse' failed for 'bright'", logs.output[0])
        self.assertEqual(entry.syns, ["brilliant", "smart", "clever"])  # what was read before the failure survives

    def test_network_errors_are_a_one_line_warning_naming_the_source(self):
        def requester(method, url, **kwargs):
            raise requests.ConnectionError("offline")
        with self.assertLogs("ocean", level="WARNING") as logs:
            entry = ds.CambridgeSource().lookup(LookupContext(term="bright", requester=requester))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Source 'cambridge' failed for 'bright': ConnectionError", logs.output[0])
        self.assertEqual(entry, PartialEntry())

    def test_broken_parser_is_reported_not_raised(self):
        def requester(method, url, **kwargs):
            return FakeResponse("[1, 2]")  # not the shape the Tatoeba parser expects
        with self.assertLogs("ocean", level="ERROR") as logs:
            entry = ds.TatoebaSource().lookup(LookupContext(term="bright", requester=requester))
        self.assertIn("Source 'tatoeba' failed", logs.output[0])
        self.assertEqual(entry.examples, [])


class RegistryRunTest(unittest.TestCase):
    def test_sources_with_needs_get_the_merged_result_of_those_sources(self):
        # Datamuse only asks for adjective collocations because dictionaryapi.dev reported an adjective.
        merged = ds.REGISTRY.run(ctx_for("bright"))
        self.assertEqual(merged.primary_pos, "Adjective")
        self.assertIn("bright future", merged.collocation_terms)

    def test_merge_order_follows_the_registry(self):
        # Cambridge, then Longman, then dictionaryapi.dev senses; WordNet data is not installed in tests.
        with mock.patch.object(le, "wn", None):
            merged = ds.REGISTRY.run(ctx_for("bright"))
        defs = [s["def"] for s in merged.senses]
        self.assertEqual(defs[0], "Giving out or reflecting a lot of light.")
        self.assertLess(defs.index("Shining strongly with a lot of light."), defs.index("Emitting or reflecting much light; shining."))
        self.assertEqual(merged.ipa_us, "/braɪt/")
        self.assertEqual(merged.syns[:3], ["brilliant", "radiant", "good"])  # Cambridge's come first

    def test_disabled_source_is_not_contacted_and_the_others_still_answer(self):
        urls = []

        def requester(method, url, **kwargs):
            urls.append(url)
            return fixture_site(method, url, **kwargs)
        self.addCleanup(ds.REGISTRY.set_enabled, "cambridge", True)
        ds.REGISTRY.set_enabled("cambridge", False)
        self.assertEqual(ds.REGISTRY.disabled_names(), ["cambridge"])
        merged = ds.REGISTRY.run(LookupContext(term="bright", requester=requester))
        self.assertFalse([u for u in urls if "cambridge" in u])
        self.assertTrue([u for u in urls if "ldoceonline" in u])
        defs = [s["def"] for s in merged.senses]
        self.assertNotIn("Giving out or reflecting a lot of light.", defs)
        self.assertEqual(defs[0], "Shining strongly with a lot of light.")

    def test_all_sources_disabled_gives_an_empty_entry(self):
        for name in ds.REGISTRY.names():
            self.addCleanup(ds.REGISTRY.set_enabled, name, True)
            ds.REGISTRY.set_enabled(name, False)
        self.assertEqual(ds.REGISTRY.run(ctx_for("bright")), PartialEntry())

    def test_offline_lookup_contacts_no_network_source(self):
        urls = []

        def requester(method, url, **kwargs):
            urls.append(url)
            return fixture_site(method, url, **kwargs)
        ctx = LookupContext(term="spill the beans", is_idiom=True, offline=True, requester=requester)
        active = [s.name for s in ds.REGISTRY.active(ctx)]
        self.assertEqual(active, ["offline_idioms", "idiom_fallback", "curated_synonyms"])
        merged = ds.REGISTRY.run(ctx)
        self.assertEqual(urls, [])
        self.assertEqual(merged.senses[0]["pos"], "Idiom / Expression")  # the bundled idiom list still answers

    def test_phrase_only_sources_skip_single_words(self):
        names = [s.name for s in ds.REGISTRY.active(ctx_for("bright"))]
        self.assertNotIn("thefreedictionary", names)
        self.assertNotIn("wiktionary", names)
        names = [s.name for s in ds.REGISTRY.active(ctx_for("bright", deep_mode=True))]
        self.assertIn("thefreedictionary", names)

    def test_wiktionary_only_fills_in_when_there_are_fewer_than_two_senses(self):
        ctx = ctx_for("break the ice", is_idiom=True)
        wik = ds.REGISTRY.get("wiktionary")
        self.assertEqual(len(wik.lookup(ctx, PartialEntry()).senses), 2)
        self.assertEqual(wik.lookup(ctx, PartialEntry(senses=[{"def": "a"}, {"def": "b"}])).senses, [])

    def test_merge_entries_concatenates_and_takes_first_ipa_and_pos(self):
        a = PartialEntry(ipa_us="/a/", syns=["x"])
        b = PartialEntry(ipa_us="/b/", primary_pos="Noun", syns=["y"])
        c = PartialEntry(primary_pos="Verb")
        merged = merge_entries([a, b, c])
        self.assertEqual((merged.ipa_us, merged.primary_pos, merged.syns), ("/a/", "Noun", ["x", "y"]))
        self.assertIsNone(merged.frequency)
        self.assertEqual(merge_entries([a, PartialEntry(frequency=0.0), PartialEntry(frequency=5.0)]).frequency, 0.0)


class EngineIntegrationTest(unittest.TestCase):
    """analyze_with_dictionaries merges whatever the registered sources return."""

    def lookup(self, term, **kwargs):
        patches = [
            mock.patch.object(le, "smart_request", fixture_site),
            mock.patch.object(le, "wn", None),
            mock.patch.object(le, "LEMMATIZER", None),
            mock.patch.object(le, "fetch_persian_word_meaning", lambda t, is_idiom=False: "FA:" + t),
            mock.patch.object(le, "translate_batch_fast", lambda sentences, short_terms: {s: "fa " + s for s in list(sentences) + list(short_terms)}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return le.analyze_with_dictionaries(term, **kwargs)

    def test_merges_the_registered_sources(self):
        result = self.lookup("bright")
        self.assertEqual(result["ipa_us"], "/braɪt/")
        self.assertEqual(result["senses"][0]["def"], "Giving out or reflecting a lot of light.")
        self.assertEqual([s["term"] for s in result["synonyms"]], ["brilliant", "radiant", "good"])
        self.assertEqual([s["term"] for s in result["antonyms"]], ["dim", "dull", "pale"])
        self.assertEqual([c["term"] for c in result["collocations"]], ["bright light", "bright idea", "bright future"])

    def test_result_carries_the_level_and_the_frequency_band(self):
        result = self.lookup("bright")
        self.assertEqual(result["cefr_level"], "A1")
        self.assertEqual(result["frequency_per_million"], 31.2)
        self.assertEqual(result["frequency_band"], "Common")

    def test_level_works_offline_while_the_band_needs_datamuse(self):
        self.addCleanup(ds.REGISTRY.set_enabled, "datamuse_frequency", True)
        ds.REGISTRY.set_enabled("datamuse_frequency", False)
        result = self.lookup("bright")
        self.assertEqual(result["cefr_level"], "A1")
        self.assertIsNone(result["frequency_per_million"])
        self.assertEqual(result["frequency_band"], "")

    def test_lookup_still_works_with_a_source_switched_off(self):
        self.addCleanup(ds.REGISTRY.set_enabled, "cambridge", True)
        ds.REGISTRY.set_enabled("cambridge", False)
        result = self.lookup("bright")
        self.assertEqual(result["senses"][0]["def"], "Shining strongly with a lot of light.")
        self.assertEqual(result["ipa_us"], "/braɪt/")  # from Longman now

    def test_lookup_still_works_with_every_source_switched_off(self):
        for name in ds.REGISTRY.names():
            self.addCleanup(ds.REGISTRY.set_enabled, name, True)
            ds.REGISTRY.set_enabled(name, False)
        result = self.lookup("bright")
        self.assertEqual(result["headword"], "bright")
        self.assertEqual(result["senses"], [])
        self.assertTrue(result["ipa_us"])  # rule-based fallback
        self.assertEqual(le.fetch_live_suggestions("brigt"), [])

    def test_suggestions_use_datamuse_unless_it_is_switched_off(self):
        with mock.patch.object(le, "smart_request", fixture_site):
            self.assertEqual(le.fetch_live_suggestions("brigt"), ["bright", "brigade", "brighton"])
            self.addCleanup(ds.REGISTRY.set_enabled, "datamuse", True)
            ds.REGISTRY.set_enabled("datamuse", False)
            self.assertEqual(le.fetch_live_suggestions("brigt"), [])

    def test_did_you_mean_comes_from_suggestions_when_no_source_knows_the_word(self):
        self.assertEqual(self.lookup("brigt")["did_you_mean"], "bright")


if __name__ == "__main__":
    unittest.main()
