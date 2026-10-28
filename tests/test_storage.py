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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage  # noqa: E402

FLEETING = "زودگذر"      # زودگذر
PASSING = "گذرا"          # گذرا


class HistoryStoreTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "lexicon_vault.db")
        self.store = storage.HistoryStore(self.db_path)

    def test_storage_module_has_no_qt_dependency(self):
        with open(storage.__file__, encoding="utf-8") as f:
            self.assertNotIn("PyQt", f.read())

    def test_schema_has_the_original_columns_plus_later_ones(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(lexicon)")]
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(cols[:6], ["id", "query_key", "display_term", "payload", "is_favorite", "updated_at"])
        self.assertIn("lookup_count", cols)
        self.assertIn("produced_at", cols)
        self.assertIn("engine_version", cols)
        self.assertEqual(mode, "wal")

    def test_save_then_get_normalises_the_key(self):
        self.store.save_entry("  Ephemeral ", {"headword": "Ephemeral", "persian_equivalent": FLEETING})
        got = self.store.get_entry("EPHEMERAL")
        self.assertEqual(got["headword"], "Ephemeral")
        self.assertEqual(got["persian_equivalent"], FLEETING)
        self.assertFalse(got["_is_favorite"])
        self.assertIsNone(self.store.get_entry("missing"))

    def test_data_survives_reopening_the_database(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.assertEqual(storage.HistoryStore(self.db_path).get_entry("sea")["headword"], "sea")

    def test_resave_updates_payload_but_keeps_favourite(self):
        self.store.save_entry("sea", {"headword": "sea", "v": 1})
        self.assertTrue(self.store.toggle_favorite("sea"))
        self.store.save_entry("sea", {"headword": "Sea", "v": 2})
        got = self.store.get_entry("sea")
        self.assertEqual(got["v"], 2)
        self.assertTrue(got["_is_favorite"])
        entries = self.store.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["display_term"], "Sea")

    def test_display_term_falls_back_to_the_typed_term(self):
        self.store.save_entry(" Foo ", {})
        self.assertEqual(self.store.list_entries()[0]["display_term"], "Foo")

    def test_toggle_favorite_flips_and_ignores_unknown_terms(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.assertTrue(self.store.toggle_favorite("Sea"))
        self.assertTrue(self.store.get_entry("sea")["_is_favorite"])
        self.assertFalse(self.store.toggle_favorite("sea"))
        self.assertFalse(self.store.get_entry("sea")["_is_favorite"])
        self.assertFalse(self.store.toggle_favorite("unknown"))
        self.assertEqual([e["query_key"] for e in self.store.list_entries()], ["sea"])

    def test_delete_entry(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.store.save_entry("sky", {"headword": "sky"})
        self.store.delete_entry(" SEA ")
        self.assertIsNone(self.store.get_entry("sea"))
        self.assertIsNotNone(self.store.get_entry("sky"))
        self.store.delete_entry("never saved")  # no error

    def test_list_entries_newest_first_and_favourites_filter(self):
        for term, stamp in (("old", "2026-01-01T00:00:00"), ("mid", "2026-01-02T00:00:00"),
                            ("new", "2026-01-03T00:00:00")):
            self.store.save_entry(term, {"headword": term})
            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    conn.execute("UPDATE lexicon SET updated_at = ? WHERE query_key = ?", (stamp, term))
            finally:
                conn.close()
        self.store.toggle_favorite("old")
        self.store.toggle_favorite("new")
        everything = self.store.list_entries()
        self.assertEqual([e["query_key"] for e in everything], ["new", "mid", "old"])
        self.assertEqual(everything[0], {"query_key": "new", "display_term": "new",
                                         "is_favorite": True, "updated_at": "2026-01-03T00:00:00",
                                         "lookup_count": 1})
        self.assertFalse(everything[1]["is_favorite"])
        self.assertEqual([e["query_key"] for e in self.store.list_entries(favorites_only=True)], ["new", "old"])

    def test_english_entries_with_persian_matches_any_part(self):
        self.store.save_entry("ephemeral", {"headword": "ephemeral", "persian_equivalent": f"{PASSING}، {FLEETING}"})
        self.store.save_entry("fleeting", {"headword": "fleeting", "persian_equivalent": FLEETING})
        self.store.save_entry("sea", {"headword": "sea", "persian_equivalent": "دریا"})
        # Arabic letters and punctuation are normalised by lexical_engine.fa_key.
        self.assertEqual(sorted(self.store.english_entries_with_persian(f" {FLEETING}؟ ")),
                         ["ephemeral", "fleeting"])
        self.assertEqual(self.store.english_entries_with_persian(PASSING), ["ephemeral"])
        self.assertEqual(self.store.english_entries_with_persian("ناشناخته"), [])
        self.assertEqual(self.store.english_entries_with_persian("  "), [])

    def test_english_entries_with_persian_skips_reverse_entries_and_honours_limit(self):
        self.store.save_entry(FLEETING, {"headword": FLEETING, "is_reverse": True, "persian_equivalent": FLEETING})
        for term in ("a", "b", "c"):
            self.store.save_entry(term, {"headword": term, "persian_equivalent": f"x, {FLEETING}"})
        found = self.store.english_entries_with_persian(FLEETING, limit=2)
        self.assertEqual(len(found), 2)
        self.assertNotIn(FLEETING, found)

    def test_english_entries_with_persian_ignores_corrupt_payloads(self):
        self.store.save_entry("good", {"headword": "good", "persian_equivalent": FLEETING})
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute("INSERT INTO lexicon (query_key, display_term, payload, updated_at) "
                             "VALUES ('bad', 'bad', 'not json', '2999-01-01T00:00:00')")
        finally:
            conn.close()
        self.assertEqual(self.store.english_entries_with_persian(FLEETING), ["good"])


class HistorySearchAndSortTest(unittest.TestCase):
    OLD_SCHEMA = """
        CREATE TABLE lexicon (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_key TEXT UNIQUE NOT NULL,
            display_term TEXT NOT NULL,
            payload TEXT NOT NULL,
            is_favorite INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "lexicon_vault.db")
        self.store = storage.HistoryStore(self.db_path)

    def _sql(self, sql, *params):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _stamp(self, term, stamp):
        self._sql("UPDATE lexicon SET updated_at = ? WHERE query_key = ?", stamp, term)

    def _keys(self, **kwargs):
        return [e["query_key"] for e in self.store.list_entries(**kwargs)]

    def _populate(self):
        # sea (oldest, saved), sky, moon (newest, saved); each with a Persian meaning
        for term, fa, stamp in (("sea", "دریا", "2026-01-01T00:00:00"),
                                ("sky", "آسمان", "2026-01-02T00:00:00"),
                                ("moon", "ماه", "2026-01-03T00:00:00")):
            self.store.save_entry(term, {"headword": term, "persian_equivalent": fa})
            self._stamp(term, stamp)
        self.store.toggle_favorite("sea")
        self.store.toggle_favorite("moon")

    # migration

    def test_old_schema_database_is_migrated_and_entries_start_at_one(self):
        path = os.path.join(self._dir.name, "old.db")
        conn = sqlite3.connect(path)
        with conn:
            conn.execute(self.OLD_SCHEMA)
            conn.execute("INSERT INTO lexicon (query_key, display_term, payload, is_favorite, updated_at) "
                         "VALUES ('sea', 'sea', '{\"headword\": \"sea\"}', 1, '2026-01-01T00:00:00')")
        conn.close()
        store = storage.HistoryStore(path)
        entries = store.list_entries()
        self.assertEqual([(e["query_key"], e["lookup_count"], e["is_favorite"]) for e in entries],
                         [("sea", 1, True)])
        self.assertEqual(store.get_entry("sea")["headword"], "sea")

    def test_migration_is_idempotent_and_keeps_counts(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.store.record_lookup("sea")
        reopened = storage.HistoryStore(self.db_path)
        storage.HistoryStore(self.db_path)
        self.assertEqual(reopened.list_entries()[0]["lookup_count"], 2)
        cols = [r[1] for r in self._sql("PRAGMA table_info(lexicon)")]
        self.assertEqual(cols.count("lookup_count"), 1)

    def test_migration_coexists_with_other_added_columns(self):
        self._sql("ALTER TABLE lexicon ADD COLUMN some_future_column TEXT")
        self.store.save_entry("sea", {"headword": "sea"})
        reopened = storage.HistoryStore(self.db_path)
        self.assertEqual(reopened.list_entries()[0]["lookup_count"], 1)

    # counter

    def test_new_entry_starts_at_one_and_save_entry_never_changes_the_count(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.assertEqual(self.store.list_entries()[0]["lookup_count"], 1)
        self.store.save_entry("sea", {"headword": "Sea", "v": 2})  # refresh / re-save
        self.assertEqual(self.store.list_entries()[0]["lookup_count"], 1)

    def test_record_lookup_increments_and_survives_a_resave(self):
        self.store.save_entry("sea", {"headword": "sea"})
        self.store.record_lookup(" SEA ")
        self.store.record_lookup("sea")
        self.assertEqual(self.store.list_entries()[0]["lookup_count"], 3)
        self.store.save_entry("sea", {"headword": "sea", "v": 2})
        self.assertEqual(self.store.list_entries()[0]["lookup_count"], 3)

    def test_record_lookup_of_an_unknown_term_stores_nothing(self):
        self.store.record_lookup("never saved")
        self.assertEqual(self.store.list_entries(), [])

    def test_record_lookup_moves_the_entry_to_the_top_of_recent(self):
        self._populate()
        self.assertEqual(self._keys()[0], "moon")
        self.store.record_lookup("sea")
        self.assertEqual(self._keys()[0], "sea")

    # search

    def test_search_matches_english_display_term_case_insensitively(self):
        self._populate()
        self.assertEqual(self._keys(query="SE"), ["sea"])
        self.assertEqual(self._keys(query="  oo "), ["moon"])
        self.assertEqual(self._keys(query="zzz"), [])

    def test_blank_query_lists_everything(self):
        self._populate()
        self.assertEqual(self._keys(query="   "), ["moon", "sky", "sea"])
        self.assertEqual(self._keys(query=""), ["moon", "sky", "sea"])

    def test_search_matches_the_persian_meaning_with_letter_variants(self):
        self.store.save_entry("ephemeral", {"headword": "ephemeral", "persian_equivalent": f"{PASSING}، {FLEETING}"})
        self.store.save_entry("sea", {"headword": "sea", "persian_equivalent": "دریا"})
        self.store.save_entry("book", {"headword": "book", "persian_equivalent": "کتاب"})
        self.store.save_entry("hand", {"headword": "hand", "persian_equivalent": "دستِ"})
        self.assertEqual(self._keys(query=PASSING), ["ephemeral"])
        self.assertEqual(self._keys(query=FLEETING[:4]), ["ephemeral"])
        self.assertEqual(self._keys(query="كتاب"), ["book"])  # Arabic kaf
        self.assertEqual(self._keys(query="دريا"), ["sea"])  # Arabic yeh
        self.assertEqual(self._keys(query="دست"), ["hand"])  # stored form carries a diacritic

    def test_search_matches_a_persian_display_term(self):
        self.store.save_entry(FLEETING, {"headword": FLEETING, "is_reverse": True})
        self.store.save_entry("sea", {"headword": "sea", "persian_equivalent": "دریا"})
        self.assertEqual(self._keys(query=FLEETING), [FLEETING])

    def test_search_copes_with_corrupt_payloads(self):
        self._sql("INSERT INTO lexicon (query_key, display_term, payload, updated_at) "
                  "VALUES ('bad', 'bad', 'not json', '2026-01-01T00:00:00')")
        self._sql("INSERT INTO lexicon (query_key, display_term, payload, updated_at) "
                  "VALUES ('odd', 'odd', '[1, 2]', '2026-01-01T00:00:00')")
        self.assertEqual(self._keys(query="bad"), ["bad"])
        self.assertEqual(self._keys(query="دریا"), [])

    # sort

    def test_sort_most_looked_up(self):
        self._populate()
        self.store.record_lookup("sky")
        self.store.record_lookup("sky")
        self.store.record_lookup("sea")
        self._stamp("sky", "2026-01-02T00:00:00")
        self._stamp("sea", "2026-01-01T00:00:00")
        self.assertEqual(self._keys(sort=storage.SORT_MOST_LOOKED_UP), ["sky", "sea", "moon"])
        self.assertEqual(self._keys(sort=storage.SORT_RECENT), ["moon", "sky", "sea"])
        self.assertEqual(self._keys(), ["moon", "sky", "sea"])  # recent is the default

    def test_equal_counts_sort_newest_first(self):
        self._populate()
        self.assertEqual(self._keys(sort=storage.SORT_MOST_LOOKED_UP), ["moon", "sky", "sea"])

    # combined with the favourites filter

    def test_search_and_sort_combine_with_the_favourites_filter(self):
        self._populate()
        self.store.record_lookup("sea")
        self.store.record_lookup("sky")
        self.store.record_lookup("sky")
        self._stamp("sea", "2026-01-01T00:00:00")
        self.assertEqual(self._keys(favorites_only=True), ["moon", "sea"])
        self.assertEqual(self._keys(favorites_only=True, sort=storage.SORT_MOST_LOOKED_UP), ["sea", "moon"])
        self.assertEqual(self._keys(favorites_only=True, query="دریا"), ["sea"])
        self.assertEqual(self._keys(favorites_only=True, query="sky"), [])  # sky is not saved
        self.assertEqual(self._keys(favorites_only=True, query="o", sort=storage.SORT_MOST_LOOKED_UP), ["moon"])
        self.assertEqual(self._keys(query="sky"), ["sky"])
        self.assertEqual(self._keys(sort=storage.SORT_MOST_LOOKED_UP)[0], "sky")


class ConnectionHandlingTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "lexicon_vault.db")
        self.opened = []
        real_connect = sqlite3.connect

        class Tracked:
            """Wraps a real connection and records close()."""
            def __init__(self, conn):
                self._conn = conn
                self.closed = False

            def close(self):
                self.closed = True
                self._conn.close()

            def __enter__(self):
                return self._conn.__enter__()

            def __exit__(self, *exc):
                return self._conn.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        def connect(*args, **kwargs):
            tracked = Tracked(real_connect(*args, **kwargs))
            self.opened.append(tracked)
            return tracked

        patcher = mock.patch.object(storage.sqlite3, "connect", connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_connection_is_closed(self):
        store = storage.HistoryStore(self.db_path)
        store.save_entry("sea", {"headword": "sea", "persian_equivalent": FLEETING})
        store.get_entry("sea")
        store.toggle_favorite("sea")
        store.toggle_favorite("missing")
        store.list_entries(True)
        store.english_entries_with_persian(FLEETING)
        store.delete_entry("sea")
        self.assertGreaterEqual(len(self.opened), 8)
        self.assertTrue(all(c.closed for c in self.opened))

    def test_connection_is_closed_and_rolled_back_when_a_statement_fails(self):
        store = storage.HistoryStore(self.db_path)
        before = len(self.opened)
        with self.assertRaises(sqlite3.Error):
            with store._conn() as conn:
                conn.execute("INSERT INTO lexicon (query_key) VALUES ('x')")  # NOT NULL columns missing
        self.assertTrue(all(c.closed for c in self.opened[before:]))
        self.assertEqual(store.list_entries(), [])


if __name__ == "__main__":
    unittest.main()
