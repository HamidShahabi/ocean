import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests  # noqa: E402

import lexical_engine as le  # noqa: E402

GTX = "https://translate.googleapis.com/translate_a/single"


def make_response(body, status=200):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
    return r


class CountingNetwork:
    """Stands in for the real send: counts calls per request, optionally slow or failing."""

    def __init__(self, delay=0.0, status=200, body=None):
        self.delay = delay
        self.status = status
        self.body = [["ok"]] if body is None else body
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, method, url, **kwargs):
        with self._lock:
            self.calls.append((method, url, repr(kwargs.get("params")), repr(kwargs.get("data"))))
        if self.delay:
            time.sleep(self.delay)
        return make_response(self.body, self.status)


class RequestCacheTest(unittest.TestCase):
    def test_identical_requests_reach_the_network_once(self):
        net = CountingNetwork()
        cache = le.RequestCache()
        first = cache.request("GET", GTX, net, params=[("q", "bright")], timeout=2.8)
        second = cache.request("GET", GTX, net, params=[("q", "bright")], timeout=3.0)  # timeout is not part of the answer
        self.assertEqual(len(net.calls), 1)
        self.assertIs(first, second)
        self.assertEqual((cache.hits, cache.misses), (1, 1))

    def test_different_requests_are_kept_apart(self):
        net = CountingNetwork()
        cache = le.RequestCache()
        cache.request("GET", GTX, net, params=[("q", "bright")])
        cache.request("GET", GTX, net, params=[("q", "dim")])
        cache.request("GET", GTX + "x", net, params=[("q", "bright")])
        cache.request("GET", GTX, net, params={"q": "bright"}, headers={"Accept": "a"})
        self.assertEqual(len(net.calls), 4)

    def test_dict_params_ignore_key_order(self):
        net = CountingNetwork()
        cache = le.RequestCache()
        cache.request("GET", GTX, net, params={"a": 1, "b": 2})
        cache.request("GET", GTX, net, params={"b": 2, "a": 1})
        self.assertEqual(len(net.calls), 1)

    def test_post_is_keyed_by_its_whole_body(self):
        net = CountingNetwork()
        cache = le.RequestCache()
        cache.request("POST", GTX, net, params=[("dt", "t")], data={"q": "one\ntwo"})
        cache.request("POST", GTX, net, params=[("dt", "t")], data={"q": "one\ntwo"})
        cache.request("POST", GTX, net, params=[("dt", "t")], data={"q": "one\nthree"})
        self.assertEqual(len(net.calls), 2)

    def test_unusual_requests_are_never_cached(self):
        net = CountingNetwork()
        cache = le.RequestCache()
        for _ in range(2):
            cache.request("DELETE", GTX, net)
            cache.request("GET", GTX, net, stream=True)
            cache.request("POST", GTX, net, data=object())
            cache.request("POST", GTX, net, files={"f": b"x"})
        self.assertEqual(len(net.calls), 8)
        self.assertEqual(len(cache), 0)

    def test_failed_responses_are_not_remembered(self):
        net = CountingNetwork(status=429)
        cache = le.RequestCache()
        for _ in range(3):
            self.assertEqual(cache.request("GET", GTX, net, params=[("q", "x")]).status_code, 429)
        self.assertEqual(len(net.calls), 3)
        self.assertEqual(len(cache), 0)

    def test_exceptions_are_not_remembered_and_the_next_call_retries(self):
        cache = le.RequestCache()
        net = CountingNetwork()

        def failing(method, url, **kwargs):
            net.calls.append(url)
            raise requests.ConnectionError("down")

        with self.assertRaises(requests.ConnectionError):
            cache.request("GET", GTX, failing)
        self.assertEqual(cache.request("GET", GTX, net).status_code, 200)
        self.assertEqual(len(net.calls), 2)
        self.assertEqual(cache._in_flight, {})

    def test_oversized_responses_are_passed_on_but_not_kept(self):
        net = CountingNetwork(body=b"x" * (le.RequestCache.MAX_BODY_BYTES + 1))
        cache = le.RequestCache()
        cache.request("GET", GTX, net)
        cache.request("GET", GTX, net)
        self.assertEqual(len(net.calls), 2)
        self.assertEqual(len(cache), 0)

    def test_size_is_bounded_and_least_recently_used_goes_first(self):
        net = CountingNetwork()
        cache = le.RequestCache(max_entries=3)
        for word in ("a", "b", "c"):
            cache.request("GET", GTX, net, params=[("q", word)])
        cache.request("GET", GTX, net, params=[("q", "a")])  # a is now the freshest
        cache.request("GET", GTX, net, params=[("q", "d")])  # evicts b
        self.assertEqual(len(cache), 3)
        before = len(net.calls)
        cache.request("GET", GTX, net, params=[("q", "a")])
        cache.request("GET", GTX, net, params=[("q", "d")])
        self.assertEqual(len(net.calls), before)
        cache.request("GET", GTX, net, params=[("q", "b")])
        self.assertEqual(len(net.calls), before + 1)

    def test_concurrent_identical_requests_share_one_call(self):
        net = CountingNetwork(delay=0.2)
        cache = le.RequestCache()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(cache.request, "GET", GTX, net, params=[("q", "bright")]) for _ in range(8)]
            results = [f.result() for f in futs]
        self.assertEqual(len(net.calls), 1)
        self.assertTrue(all(r is results[0] for r in results))

    def test_concurrent_waiters_share_a_failure_instead_of_retrying_one_by_one(self):
        net = CountingNetwork(delay=0.2, status=429)
        cache = le.RequestCache()
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = [pool.submit(cache.request, "GET", GTX, net) for _ in range(5)]
            codes = [f.result().status_code for f in futs]
        self.assertEqual(codes, [429] * 5)
        self.assertEqual(len(net.calls), 1)
        self.assertEqual(len(cache), 0)


class LookupScopeTest(unittest.TestCase):
    def setUp(self):
        self.net = CountingNetwork()
        patcher = mock.patch.object(le, "_send_request", self.net)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_smart_request_is_not_cached_outside_a_lookup(self):
        le.smart_request("GET", GTX, params=[("q", "x")])
        le.smart_request("GET", GTX, params=[("q", "x")])
        self.assertEqual(len(self.net.calls), 2)

    def test_smart_request_is_cached_inside_a_lookup_and_forgotten_after_it(self):
        with le.lookup_request_cache() as cache:
            le.smart_request("GET", GTX, params=[("q", "x")])
            le.smart_request("GET", GTX, params=[("q", "x")])
            self.assertEqual(len(self.net.calls), 1)
            self.assertEqual(len(cache), 1)
        le.smart_request("GET", GTX, params=[("q", "x")])
        self.assertEqual(len(self.net.calls), 2)  # a later lookup starts empty

    def test_worker_threads_see_the_cache_only_when_given_the_context(self):
        with le.lookup_request_cache() as cache:
            with ThreadPoolExecutor(max_workers=2) as pool:
                pool.submit(le.smart_request, "GET", GTX, params=[("q", "x")]).result()
                self.assertEqual(len(cache), 0)  # a plain submit() runs in an empty context
                le.submit_in_context(pool, le.smart_request, "GET", GTX, params=[("q", "y")]).result()
                le.submit_in_context(pool, le.smart_request, "GET", GTX, params=[("q", "y")]).result()
            self.assertEqual(len(cache), 1)
        self.assertEqual(len(self.net.calls), 2)

    def test_nested_scope_reuses_the_outer_cache(self):
        with le.lookup_request_cache() as outer:
            with le.lookup_request_cache() as inner:
                self.assertIs(inner, outer)
            le.smart_request("GET", GTX)
        self.assertEqual(len(outer), 1)

    def test_lookups_running_at_the_same_time_do_not_share_a_cache(self):
        barrier = threading.Barrier(2)
        caches = []

        def lookup():
            with le.lookup_request_cache() as cache:
                caches.append(cache)
                barrier.wait(timeout=5)  # both lookups are now open at once
                le.smart_request("GET", GTX, params=[("q", "same")])
                le.smart_request("GET", GTX, params=[("q", "same")])
                barrier.wait(timeout=5)

        threads = [threading.Thread(target=lookup) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIsNot(caches[0], caches[1])
        self.assertEqual(len(self.net.calls), 2)  # one real call per lookup, not one overall

    def test_scope_is_closed_when_the_lookup_raises(self):
        with self.assertRaises(RuntimeError):
            with le.lookup_request_cache():
                raise RuntimeError("boom")
        self.assertIsNone(le._LOOKUP_CACHE.get())


class WholeLookupTest(unittest.TestCase):
    """analyze_with_dictionaries() under a counting fake network."""

    def run_lookup(self, term, network):
        patches = [
            mock.patch.object(le, "_send_request", network),
            mock.patch.object(le, "wn", None),  # WordNet may be missing and is not what is under test
            mock.patch.object(le, "sentence_to_ipa", return_value=""),
            mock.patch.dict(le.USER_MEMORY, {"overrides": {}, "entries": {}}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return le.analyze_with_dictionaries(term)

    def test_word_lookup_asks_the_network_for_each_distinct_request_once(self):
        # "dull" is both a synonym and an antonym, so two worker threads want the same example lookup.
        def respond(method, url, **kwargs):
            time.sleep(0.02)
            if "datamuse.com/words" in url:
                params = dict(kwargs["params"])
                if "rel_syn" in params:
                    return make_response([{"word": "shiny"}, {"word": "dull"}])
                if "rel_ant" in params:
                    return make_response([{"word": "dull"}, {"word": "dim"}])
                return make_response([])
            if "dictionaryapi.dev" in url:
                word = url.rsplit("/", 1)[-1]
                return make_response([{"meanings": [{"partOfSpeech": "adjective", "definitions": [
                    {"definition": "Giving out a lot of light.", "example": f"The {word} light filled the whole room."}]}]}])
            if "translate" in url:
                return make_response([[["ترجمه", "x"]], [["adj", ["ترجمه"]]]])
            return make_response(b"<html></html>")

        net = CountingNetwork()
        net_calls = net.calls

        def counted(method, url, **kwargs):
            net_calls.append((method, url, repr(kwargs.get("params")), repr(kwargs.get("data"))))
            return respond(method, url, **kwargs)

        result = self.run_lookup("bright", counted)
        self.assertEqual(result["headword"], "bright")
        self.assertTrue(net_calls)
        self.assertEqual(len(net_calls), len(set(net_calls)), "an identical request was sent twice")
        self.assertEqual(sum("api.dictionaryapi.dev" in c[1] and c[1].endswith("/dull") for c in net_calls), 1)

    def test_two_lookups_in_a_row_do_not_share_responses(self):
        net = CountingNetwork(body=[])
        self.run_lookup("bright", net)
        first = len(net.calls)
        self.assertGreater(first, 0)
        le.analyze_with_dictionaries("bright")
        self.assertEqual(len(net.calls), 2 * first)


if __name__ == "__main__":
    unittest.main()
