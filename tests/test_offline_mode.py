import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

import requests

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import use_lexicon  # noqa: E402
import lexical_engine as le  # noqa: E402
from ui.config import SettingsManager  # noqa: E402
from ui.workers import SuggestionWorker  # noqa: E402

PERSIAN_WORD = "کتاب"  # کتاب
PERSIAN_SENTENCE = "من امروز به مدرسه رفتم."


def wordnet_installed():
    try:
        return bool(le.wn and le.wn.synsets("dog"))
    except LookupError:
        return False


HAS_WORDNET = wordnet_installed()


class NetworkTripwire:
    """Patches every way Python code reaches the network and records the attempts.
    The engine swallows request errors, so a test must check `attempts`, not just that nothing crashed."""

    def __init__(self):
        self.attempts = []
        self._patches = []

    def _trip(self, name):
        def raiser(*args, **kwargs):
            self.attempts.append(name)
            raise AssertionError(f"network access: {name}")
        return raiser

    def __enter__(self):
        targets = [
            (requests.Session, "request", "requests.Session.request"),
            (requests, "request", "requests.request"),
            (urllib.request, "urlopen", "urllib.request.urlopen"),
            (socket, "create_connection", "socket.create_connection"),
            (socket, "getaddrinfo", "socket.getaddrinfo"),
            (socket.socket, "connect", "socket.connect"),
            (socket.socket, "connect_ex", "socket.connect_ex"),
        ]
        for obj, attr, name in targets:
            p = mock.patch.object(obj, attr, self._trip(name))
            p.start()
            self._patches.append(p)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        self._patches = []


class OfflineSwitchTest(unittest.TestCase):
    def tearDown(self):
        le.set_offline_only(False)

    def test_off_by_default_and_toggles(self):
        self.assertFalse(le.is_offline_only())
        le.set_offline_only(True)
        self.assertTrue(le.is_offline_only())
        le.set_offline_only(False)
        self.assertFalse(le.is_offline_only())

    def test_smart_request_refuses_without_touching_a_session(self):
        le.set_offline_only(True)
        with NetworkTripwire() as net:
            with self.assertRaises(le.OfflineModeError):
                le.smart_request("GET", "https://example.com/")
        self.assertEqual(net.attempts, [])
        self.assertIsInstance(le.OfflineModeError("x"), requests.RequestException)  # callers' except clauses catch it

    def test_smart_request_still_works_when_off(self):
        with NetworkTripwire() as net:
            with self.assertRaises(AssertionError):
                le.smart_request("GET", "https://example.com/")
        self.assertIn("requests.Session.request", net.attempts)  # the tripwire does trip when the switch is off

    def test_the_setting_is_gone_and_an_old_settings_file_still_loads(self):
        self.assertNotIn("offline_only", SettingsManager.DEFAULTS)
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"offline_only": True, "theme": "Light Mode"}, f)
        s = SettingsManager(path)
        self.assertEqual(s.data["theme"], "Light Mode")
        self.assertNotIn("offline_only", s.data)


class NoNetworkInOfflineModeTest(unittest.TestCase):
    """With the switch on, no lookup entry point makes a request (or even tries to)."""

    def setUp(self):
        use_lexicon(self)
        le.set_offline_only(True)
        self.addCleanup(le.set_offline_only, False)
        self.net = NetworkTripwire()
        self.net.__enter__()
        self.addCleanup(self.net.__exit__)
        # Not even an attempt through the engine's own seam: callers must check the switch first.
        self.seam = mock.patch.object(le, "smart_request", side_effect=AssertionError("smart_request called"))
        self.seam_mock = self.seam.start()
        self.addCleanup(self.seam.stop)
        if not HAS_WORDNET:  # the NLTK corpus is a separate download; the offline paths work without it
            for name in ("wn", "LEMMATIZER"):
                patcher = mock.patch.object(le, name, None)
                patcher.start()
                self.addCleanup(patcher.stop)

    def assert_silent(self):
        self.assertEqual(self.net.attempts, [])
        self.seam_mock.assert_not_called()

    def test_word_lookup(self):
        data = le.analyze_with_dictionaries("run")
        if HAS_WORDNET:
            self.assertTrue(data["senses"])  # WordNet still answers
        self.assert_silent()

    def test_bundled_idiom_lookup(self):
        data = le.analyze_with_dictionaries("spill the beans")
        self.assertTrue(data["is_idiom"])
        self.assertTrue(data["senses"])
        self.assert_silent()

    def test_unknown_phrase_lookup(self):
        data = le.analyze_with_dictionaries("purple elephant parade")
        self.assertTrue(data["is_idiom"])
        self.assert_silent()

    def test_sentence_lookup(self):
        data = le.analyze_with_dictionaries("The quick brown fox jumps over the lazy dog.")
        self.assertTrue(data["is_sentence"])
        self.assert_silent()

    def test_persian_reverse_lookup_word_and_sentence(self):
        with mock.patch.object(le, "_ct2_translate", return_value="book") as ct2:
            word = le.analyze_persian(PERSIAN_WORD)
            sentence = le.analyze_with_dictionaries(PERSIAN_SENTENCE)
        self.assertEqual(word["english_translation"], "book")
        self.assertTrue(word["translation_offline"])  # the NLLB model answered, directly
        self.assertTrue(sentence["is_reverse"])
        self.assertTrue(all(c.kwargs.get("to_english") for c in ct2.call_args_list))
        self.assert_silent()

    def test_persian_reverse_lookup_without_a_model(self):
        with mock.patch.object(le, "_ct2_translate", return_value=""):
            word = le.analyze_persian(PERSIAN_WORD)
            unknown = le.analyze_persian("زودگذر")
        self.assertEqual(word["english_translation"], "book")  # the internal dictionary's translations work backwards
        self.assertEqual(unknown["english_translation"], "")
        self.assert_silent()

    def test_suggestions_use_local_data_only(self):
        with mock.patch.dict(le.USER_MEMORY, {"entries": {"serenade": {"headword": "serenade"}}}):
            sugs = le.fetch_live_suggestions("serena")
        self.assertEqual(sugs, ["serenade"])
        self.assertEqual(le.fetch_live_suggestions(PERSIAN_WORD), [])
        self.assert_silent()

    def test_suggestion_worker_offers_history_only(self):
        got = []
        worker = SuggestionWorker("seren", ["Serenity", "other", "serendipity"])
        worker.suggestions_ready.connect(got.append)
        worker.run()
        self.assertEqual(got, [["Serenity", "serendipity"]])
        self.assert_silent()

    def test_other_engine_entry_points(self):
        self.assertEqual(le.translate_via_clients5("hello"), "")
        self.assertEqual(le._google_fa_to_en(PERSIAN_WORD), ("", []))
        self.assertIn("bank", le.fetch_verified_example_for_relation("bank", "Noun").lower())  # WordNet's or the corpora's
        le.fetch_persian_word_meaning("zeitgeist")
        le.fetch_persian_word_meaning("under the weather", is_idiom=True)
        le.translate_batch_fast(["The cat sat on the mat today."], ["cat", "zeitgeist"])
        self.assertEqual(le.translate_with_source("I love you.")[0], "دوستت دارم.")  # the local tier answers offline
        self.assert_silent()

    def test_sentence_and_persian_lookups(self):
        le.analyze_with_dictionaries("The bank was closed when we arrived there.")
        le.analyze_with_dictionaries(PERSIAN_WORD)
        self.assert_silent()

    def test_translation_goes_straight_to_the_offline_model(self):
        with mock.patch.object(le, "_ct2_translate", return_value="سلام") as ct2:
            fa = le.translate_single_string("zzyzx qwerty", is_short=True)
        self.assertEqual(fa, "سلام")
        ct2.assert_called()
        self.assert_silent()


class OnlineModeUnchangedTest(unittest.TestCase):
    def test_lookup_still_tries_the_network_when_the_switch_is_off(self):
        use_lexicon(self)
        with NetworkTripwire() as net, mock.patch.object(le, "wn", None), mock.patch.object(le, "LEMMATIZER", None):
            le.analyze_with_dictionaries("zeitgeist")
        self.assertIn("requests.Session.request", net.attempts)


if __name__ == "__main__":
    unittest.main()
