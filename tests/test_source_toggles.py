import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlparse

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexical_engine as le  # noqa: E402
import dictionary_sources as ds  # noqa: E402
from ui.config import SettingsManager  # noqa: E402
from test_dictionary_sources import fixture_site  # noqa: E402

# The site each network source talks to (what a recorded request URL is checked against).
HOSTS = {
    "thefreedictionary": "idioms.thefreedictionary.com",
    "wiktionary": "en.wiktionary.org",
    "cambridge": "dictionary.cambridge.org",
    "longman": "www.ldoceonline.com",
    "dictionaryapi": "api.dictionaryapi.dev",
    "datamuse": "api.datamuse.com",
    "tatoeba": "tatoeba.org",
}


def enable_every_source():
    for name in ds.REGISTRY.names():
        ds.REGISTRY.set_enabled(name, True)


class SettingsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "settings.json")
        self.addCleanup(enable_every_source)

    def test_every_source_is_on_by_default(self):
        s = SettingsManager(self.path)
        self.assertEqual(s.data["disabled_sources"], [])
        s.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), [])

    def test_defaults_are_not_shared_between_managers(self):
        a = SettingsManager(self.path)
        a.data["disabled_sources"].append("cambridge")
        self.assertEqual(SettingsManager(self.path).data["disabled_sources"], [])
        self.assertEqual(SettingsManager.DEFAULTS["disabled_sources"], [])

    def test_choice_persists_and_is_applied_to_the_registry(self):
        s = SettingsManager(self.path)
        s.data["disabled_sources"] = ["cambridge", "wordnet"]
        s.save()
        reloaded = SettingsManager(self.path)
        self.assertEqual(reloaded.data["disabled_sources"], ["cambridge", "wordnet"])
        reloaded.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), ["cambridge", "wordnet"])
        self.assertTrue(ds.REGISTRY.is_enabled("longman"))
        # Switching one back on is applied too.
        reloaded.data["disabled_sources"] = ["wordnet"]
        reloaded.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), ["wordnet"])

    def test_unknown_and_malformed_entries_are_ignored(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"disabled_sources": ["longman", "no_such_source", 7, ["x"]]}, f)
        s = SettingsManager(self.path)
        s.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), ["longman"])
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"disabled_sources": "cambridge"}, f)
        self.assertEqual(SettingsManager(self.path).data["disabled_sources"], [])

    def test_a_settings_file_from_before_the_toggles_keeps_everything_on(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"theme": "Light Mode"}, f)
        s = SettingsManager(self.path)
        s.apply_sources()
        self.assertEqual((s.data["theme"], ds.REGISTRY.disabled_names()), ("Light Mode", []))

    def test_any_enabled(self):
        self.assertTrue(ds.REGISTRY.any_enabled())
        for name in ds.REGISTRY.names():
            ds.REGISTRY.set_enabled(name, False)
        self.assertFalse(ds.REGISTRY.any_enabled())
        ds.REGISTRY.set_enabled("wordnet", True)
        self.assertTrue(ds.REGISTRY.any_enabled())


class DisabledSourcesAreNeverContactedTest(unittest.TestCase):
    """Runs whole lookups with every request recorded: a switched-off site must not be asked anything."""

    def setUp(self):
        self.urls = []

        def recorder(method, url, **kwargs):
            self.urls.append(url)
            return fixture_site(method, url, **kwargs)

        for p in (
            mock.patch.object(le, "smart_request", recorder),
            mock.patch.object(le, "wn", None),
            mock.patch.object(le, "LEMMATIZER", None),
            mock.patch.object(le, "fetch_persian_word_meaning", lambda t, is_idiom=False: "FA:" + t),
            mock.patch.object(le, "translate_batch_fast", lambda sentences, short_terms: {s: "fa " + s for s in list(sentences) + list(short_terms)}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(enable_every_source)

    def contacted_hosts(self):
        return {urlparse(u).netloc for u in self.urls}

    def lookup_all_kinds(self):
        """A word, a deep search, an idiom and a misspelling (which asks for suggestions) reach every site."""
        results = [le.analyze_with_dictionaries(t) for t in ("bright", "break the ice", "brigt")]
        return results + [le.analyze_with_dictionaries("bright", deep_mode=True)]

    def test_every_network_source_is_contacted_when_enabled(self):
        self.lookup_all_kinds()
        for name, host in HOSTS.items():
            self.assertIn(host, self.contacted_hosts(), name)

    def test_a_disabled_source_is_never_contacted(self):
        for name, host in HOSTS.items():
            with self.subTest(source=name):
                self.urls.clear()
                ds.REGISTRY.set_enabled(name, False)
                results = self.lookup_all_kinds()
                ds.REGISTRY.set_enabled(name, True)
                self.assertNotIn(host, self.contacted_hosts())
                self.assertTrue(all(r["headword"] for r in results))  # the others still answer

    def test_a_lookup_works_with_any_one_source_disabled(self):
        for name in ds.REGISTRY.names():
            with self.subTest(source=name):
                ds.REGISTRY.set_enabled(name, False)
                result = le.analyze_with_dictionaries("bright")
                ds.REGISTRY.set_enabled(name, True)
                self.assertEqual(result["headword"], "bright")
                self.assertTrue(result["ipa_us"])

    def test_nothing_is_contacted_with_every_source_disabled(self):
        for name in ds.REGISTRY.names():
            ds.REGISTRY.set_enabled(name, False)
        results = self.lookup_all_kinds()
        self.assertEqual(self.urls, [])
        self.assertEqual([r["senses"] for r in results], [[]] * 4)
        self.assertEqual([r["headword"] for r in results], ["bright", "break the ice", "brigt", "bright"])

    def test_only_one_source_left_on_is_the_only_one_contacted(self):
        for name in ds.REGISTRY.names():
            ds.REGISTRY.set_enabled(name, name == "longman")
        result = le.analyze_with_dictionaries("bright")
        self.assertEqual(self.contacted_hosts(), {HOSTS["longman"]})
        self.assertEqual(result["senses"][0]["def"], "Shining strongly with a lot of light.")


if __name__ == "__main__":
    unittest.main()
