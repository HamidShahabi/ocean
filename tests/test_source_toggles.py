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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lexical_engine as le  # noqa: E402
import dictionary_sources as ds  # noqa: E402
from ui.config import SettingsManager  # noqa: E402
from lexicon_fixture import use_lexicon  # noqa: E402


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
        a.data["disabled_sources"].append("kaikki")
        self.assertEqual(SettingsManager(self.path).data["disabled_sources"], [])
        self.assertEqual(SettingsManager.DEFAULTS["disabled_sources"], [])

    def test_choice_persists_and_is_applied_to_the_registry(self):
        s = SettingsManager(self.path)
        s.data["disabled_sources"] = ["urban_dictionary", "corpora"]
        s.save()
        reloaded = SettingsManager(self.path)
        self.assertEqual(reloaded.data["disabled_sources"], ["urban_dictionary", "corpora"])
        reloaded.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), ["corpora", "urban_dictionary"])
        self.assertTrue(ds.REGISTRY.is_enabled("kaikki"))
        # Switching one back on is applied too.
        reloaded.data["disabled_sources"] = ["corpora"]
        reloaded.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), ["corpora"])

    def test_unknown_and_malformed_entries_are_ignored(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"disabled_sources": ["corpora", "no_such_source", 7, ["x"]]}, f)
        s = SettingsManager(self.path)
        s.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), ["corpora"])
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"disabled_sources": "kaikki"}, f)
        self.assertEqual(SettingsManager(self.path).data["disabled_sources"], [])

    def test_sources_removed_in_an_update_are_ignored(self):
        # A settings file from a version with the online dictionaries (Cambridge, Longman...) or WordNet still loads.
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"disabled_sources": ["cambridge", "longman", "datamuse", "wordnet"]}, f)
        s = SettingsManager(self.path)
        s.apply_sources()
        self.assertEqual(ds.REGISTRY.disabled_names(), [])

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
        ds.REGISTRY.set_enabled("corpora", True)
        self.assertTrue(ds.REGISTRY.any_enabled())


class DisabledSourcesAreNeverReadTest(unittest.TestCase):
    """Whole lookups with every source's fetch recorded: a switched-off source must not be read."""

    def setUp(self):
        use_lexicon(self)
        self.fetched = []
        for source in ds.REGISTRY.all():
            original = source.fetch

            def recording(ctx, entry, prior, name=source.name, original=original):
                self.fetched.append(name)
                return original(ctx, entry, prior)

            p = mock.patch.object(source, "fetch", recording)
            p.start()
            self.addCleanup(p.stop)
        for p in (
            mock.patch.object(le, "fetch_persian_word_meaning", lambda t, is_idiom=False: ("FA:" + t, "")),
            mock.patch.object(le, "translate_batch_fast", lambda sentences, short_terms: {s: "fa " + s for s in list(sentences) + list(short_terms)}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(enable_every_source)

    def lookup_all_kinds(self):
        return [le.analyze_with_dictionaries(t) for t in ("bright", "break the ice", "rizz")]

    def test_a_disabled_source_is_never_read(self):
        for name in ds.REGISTRY.names():
            with self.subTest(source=name):
                self.fetched.clear()
                ds.REGISTRY.set_enabled(name, False)
                results = self.lookup_all_kinds()
                ds.REGISTRY.set_enabled(name, True)
                self.assertNotIn(name, self.fetched)
                self.assertTrue(all(r["headword"] for r in results))  # the others still answer

    def test_every_enabled_source_is_read(self):
        self.lookup_all_kinds()
        self.assertEqual(set(self.fetched), {"kaikki", "urban_dictionary", "corpora"})

    def test_nothing_is_read_with_every_source_disabled(self):
        for name in ds.REGISTRY.names():
            ds.REGISTRY.set_enabled(name, False)
        results = self.lookup_all_kinds()
        self.assertEqual(self.fetched, [])
        self.assertEqual([r["senses"] for r in results], [[]] * 3)

    def test_only_one_source_left_on(self):
        for name in ds.REGISTRY.names():
            ds.REGISTRY.set_enabled(name, name == "kaikki")
        result = le.analyze_with_dictionaries("bright")
        self.assertEqual(set(self.fetched), {"kaikki"})
        self.assertEqual(result["senses"][0]["def"], "(informal) Giving off a lot of light.")


if __name__ == "__main__":
    unittest.main()
