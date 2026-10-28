import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexical_engine  # noqa: E402
import review  # noqa: E402
import storage  # noqa: E402

NOW = datetime(2026, 6, 1, 12, 0, 0)
V = lexical_engine.ENGINE_VERSION


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat(timespec="seconds")


class IsStaleTest(unittest.TestCase):
    def test_age_threshold(self):
        self.assertFalse(storage.is_stale(ago(days=6), V, 7, NOW))
        self.assertTrue(storage.is_stale(ago(days=7), V, 7, NOW))
        self.assertTrue(storage.is_stale(ago(days=400), V, 90, NOW))
        self.assertFalse(storage.is_stale(ago(days=89), V, 90, NOW))

    def test_never_is_never_stale(self):
        self.assertFalse(storage.is_stale(ago(days=4000), 0, None, NOW))
        self.assertFalse(storage.is_stale(None, None, None, NOW))

    def test_older_engine_version_is_stale_however_young(self):
        self.assertTrue(storage.is_stale(ago(minutes=1), V - 1, 180, NOW))
        self.assertFalse(storage.is_stale(ago(minutes=1), V, 180, NOW))
        self.assertFalse(storage.is_stale(ago(minutes=1), V + 1, 180, NOW))
        self.assertTrue(storage.is_stale(ago(minutes=1), 5, 180, NOW, current_version=6))

    def test_entries_from_before_versioning_count_as_old(self):
        self.assertGreaterEqual(V, 1)
        self.assertTrue(storage.is_stale(None, None, 180, NOW))
        self.assertTrue(storage.is_stale(ago(days=1), None, 180, NOW))

    def test_unreadable_timestamp_is_stale_and_future_one_is_not(self):
        self.assertTrue(storage.is_stale("garbage", V, 30, NOW))
        self.assertTrue(storage.is_stale(None, V, 30, NOW))
        self.assertFalse(storage.is_stale(ago(days=-3), V, 30, NOW))

    def test_period_labels(self):
        self.assertEqual(list(storage.REFRESH_PERIOD_DAYS), ["Never", "1 week", "1 month", "3 months", "6 months"])
        self.assertIsNone(storage.refresh_period_days("Never"))
        self.assertEqual(storage.refresh_period_days("1 week"), 7)
        default = storage.refresh_period_days(storage.DEFAULT_REFRESH_PERIOD)
        self.assertEqual(storage.refresh_period_days("bogus"), default)
        self.assertEqual(storage.refresh_period_days(None), default)


class RefreshReplacesTest(unittest.TestCase):
    OLD = {"persian_equivalent": "گذرا", "senses": [{"def": "x"}]}

    def test_empty_or_failed_fetch_does_not_replace(self):
        self.assertFalse(storage.refresh_replaces(self.OLD, None))
        self.assertFalse(storage.refresh_replaces(self.OLD, {}))
        self.assertFalse(storage.refresh_replaces(self.OLD, {"senses": [], "persian_equivalent": lexical_engine.NOT_FOUND_FA}))

    def test_useful_fetch_replaces(self):
        self.assertTrue(storage.refresh_replaces(self.OLD, {"persian_equivalent": "زودگذر", "senses": [{"def": "y"}]}))
        sentence = {"is_sentence": True, "persian_equivalent": "جمله"}
        self.assertTrue(storage.refresh_replaces(self.OLD, sentence))
        self.assertFalse(storage.refresh_replaces(self.OLD, dict(sentence, persian_equivalent=lexical_engine.NOT_FOUND_FA)))

    def test_losing_the_translation_does_not_replace(self):
        worse = {"persian_equivalent": lexical_engine.NOT_FOUND_FA, "senses": [{"def": "y"}]}
        self.assertFalse(storage.refresh_replaces(self.OLD, worse))
        self.assertTrue(storage.refresh_replaces({"senses": [{"def": "x"}], "persian_equivalent": lexical_engine.NOT_FOUND_FA}, worse))


class StoredProvenanceTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "lexicon_vault.db")
        self.store = storage.HistoryStore(self.db_path)

    def _set_provenance(self, key, produced_at, version):
        with self.store._conn() as conn:
            conn.execute("UPDATE lexicon SET produced_at = ?, engine_version = ? WHERE query_key = ?", (produced_at, version, key))

    def test_save_records_time_and_engine_version(self):
        self.store.save_entry("sea", {"headword": "sea"})
        got = self.store.get_entry("sea")
        self.assertEqual(got["_engine_version"], V)
        self.assertLess(abs(datetime.now() - datetime.fromisoformat(got["_produced_at"])), timedelta(minutes=1))
        self.assertFalse(self.store.is_entry_stale(got, 7))

    def test_edit_save_keeps_original_provenance(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self._set_provenance("sea", ago(days=100), V)
        self.store.save_entry("sea", {"headword": "sea", "persian_equivalent": "دریا"}, fetched=False)
        got = self.store.get_entry("sea")
        self.assertEqual(got["persian_equivalent"], "دریا")
        self.assertEqual(got["_produced_at"], ago(days=100))
        self.assertTrue(self.store.is_entry_stale(got, 90, NOW))

    def test_fetch_save_renews_provenance_and_keeps_favourite(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.store.toggle_favorite("sea")
        self._set_provenance("sea", ago(days=100), 0)
        self.assertTrue(self.store.is_entry_stale(self.store.get_entry("sea"), 90))
        self.store.save_entry("sea", {"headword": "sea", "v": 2})
        got = self.store.get_entry("sea")
        self.assertEqual(got["_engine_version"], V)
        self.assertFalse(self.store.is_entry_stale(got, 90))
        self.assertTrue(got["_is_favorite"])

    def test_review_progress_survives_a_refresh(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.store.toggle_favorite("sea")
        reviews = review.ReviewStore(self.db_path)
        state = review.CardState(reps=3, interval_days=6.0, ease=2.4, lapses=1, due=datetime.now() + timedelta(days=6))
        reviews.save_state("sea", state, datetime.now())
        self.store.save_entry("sea", {"headword": "sea", "v": 2})  # refresh
        self.assertEqual(reviews.due_cards(datetime.now() + timedelta(days=30))[0].state.reps, 3)
        self.assertEqual(reviews.due_count(datetime.now()), 0)  # still scheduled, not reset to new

    def test_legacy_database_is_migrated_and_its_entries_count_as_old(self):
        legacy = os.path.join(self._dir.name, "legacy.db")
        conn = sqlite3.connect(legacy)
        conn.execute(
            "CREATE TABLE lexicon (id INTEGER PRIMARY KEY AUTOINCREMENT, query_key TEXT UNIQUE NOT NULL, "
            "display_term TEXT NOT NULL, payload TEXT NOT NULL, is_favorite INTEGER DEFAULT 0, updated_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO lexicon (query_key, display_term, payload, is_favorite, updated_at) VALUES "
                     "('sea', 'sea', '{\"headword\": \"sea\"}', 1, '2025-01-01T00:00:00')")
        conn.commit()
        conn.close()
        for _ in range(2):  # opening twice: the migration is idempotent
            store = storage.HistoryStore(legacy)
        got = store.get_entry("sea")
        self.assertIsNone(got["_produced_at"])
        self.assertEqual(got["_engine_version"], 0)
        self.assertTrue(got["_is_favorite"])
        self.assertTrue(store.is_entry_stale(got, 180))
        self.assertFalse(store.is_entry_stale(got, None))


class ApplyUserOverridesTest(unittest.TestCase):
    def test_manual_edits_are_put_back_into_a_fresh_entry(self):
        fresh = {
            "headword": "Sea",
            "persian_equivalent": "machine",
            "senses": [{"example_en": "We sail the sea.", "example_fa": "machine"}, {"example_en": "Other.", "example_fa": "keep"}],
            "collocations": [{"term": "open sea", "fa": "machine", "example_en": "Open sea.", "example_fa": "machine"}],
            "synonyms": [{"term": "ocean", "fa": "keep"}],
            "antonyms": [],
            "sentence_breakdown": [{"term": "by chance", "fa": "machine"}],
        }
        overrides = {"sea": "دریا", "we sail the sea.": "ما دریانوردی می‌کنیم", "open sea": "دریای آزاد",
                     "open sea.": "دریای آزاد.", "by chance": "اتفاقی"}
        out = lexical_engine.apply_user_overrides(fresh, overrides)
        self.assertEqual(out["persian_equivalent"], "دریا")
        self.assertEqual(out["senses"][0]["example_fa"], "ما دریانوردی می‌کنیم")
        self.assertEqual(out["senses"][1]["example_fa"], "keep")
        self.assertEqual(out["collocations"][0]["fa"], "دریای آزاد")
        self.assertEqual(out["collocations"][0]["example_fa"], "دریای آزاد.")
        self.assertEqual(out["synonyms"][0]["fa"], "keep")
        self.assertEqual(out["sentence_breakdown"][0]["fa"], "اتفاقی")

    def test_no_overrides_changes_nothing_and_reverse_entries_keep_their_headword(self):
        data = {"headword": "x", "persian_equivalent": "y"}
        self.assertEqual(lexical_engine.apply_user_overrides(dict(data), {}), data)
        rev = {"headword": "کتاب", "is_reverse": True, "persian_equivalent": "کتاب"}
        self.assertEqual(lexical_engine.apply_user_overrides(dict(rev), {"کتاب": "zzz"}), rev)


if __name__ == "__main__":
    unittest.main()
