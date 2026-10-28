# Qt-free storage for history and favourites (the `lexicon` table).
# Review progress lives in the same database file but is handled by review.ReviewStore.
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

import lexical_engine


SORT_RECENT = "recent"
SORT_MOST_LOOKED_UP = "most_looked_up"


class HistoryStore:
    """Looked-up entries: history plus the favourites flag and a per-entry lookup counter.

    What counts as a lookup: a user-initiated one (typing a term, picking a suggestion, clicking a
    history row, candidate or related word, the global hotkey), whether it was fetched fresh or served
    from this store's cache. Re-rendering an entry, toggling its star, refreshing it, deep-searching it
    again and back/forward navigation are not lookups. The UI calls record_lookup() for each lookup;
    save_entry() never touches the counter, so storing a fresh or refreshed result cannot inflate it.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Commit on success and always close (sqlite3's own context manager only commits)."""
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute('PRAGMA auto_vacuum = FULL;')
            conn.execute('PRAGMA journal_mode = WAL;')
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lexicon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key TEXT UNIQUE NOT NULL,
                    display_term TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    is_favorite INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._add_column_if_missing(conn, "lookup_count", "INTEGER NOT NULL DEFAULT 1")

    @staticmethod
    def _add_column_if_missing(conn: sqlite3.Connection, name: str, definition: str) -> None:
        """Guarded, idempotent migration: older databases gain the column (existing rows get its default)."""
        if name in {row[1] for row in conn.execute("PRAGMA table_info(lexicon)")}:
            return
        try:
            conn.execute(f"ALTER TABLE lexicon ADD COLUMN {name} {definition}")
        except sqlite3.OperationalError:
            # Another process may have added it between the check and the ALTER.
            if name not in {row[1] for row in conn.execute("PRAGMA table_info(lexicon)")}:
                raise

    def save_entry(self, term: str, payload: Dict[str, Any]) -> None:
        key = term.strip().lower()
        now = datetime.now().isoformat(timespec="seconds")
        json_str = json.dumps(payload, ensure_ascii=False)
        with self._conn() as conn:
            cur = conn.execute("SELECT is_favorite FROM lexicon WHERE query_key = ?", (key,))
            row = cur.fetchone()
            fav = row[0] if row else 0
            conn.execute(
                """
                INSERT INTO lexicon (query_key, display_term, payload, is_favorite, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(query_key) DO UPDATE SET
                    display_term = excluded.display_term,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (key, payload.get("headword", term.strip()), json_str, fav, now),
            )

    def record_lookup(self, term: str) -> None:
        """Count one user-initiated lookup of an existing entry and move it to the top of the recent list.
        A term not stored yet is ignored: save_entry() creates it with a count of 1."""
        key = term.strip().lower()
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "UPDATE lexicon SET lookup_count = lookup_count + 1, updated_at = ? WHERE query_key = ?",
                (now, key),
            )

    def get_entry(self, term: str) -> Optional[Dict[str, Any]]:
        key = term.strip().lower()
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT payload, is_favorite FROM lexicon WHERE query_key = ?", (key,)
            )
            row = cur.fetchone()
        if row:
            data = json.loads(row[0])
            data["_is_favorite"] = bool(row[1])
            return data
        return None

    def toggle_favorite(self, term: str) -> bool:
        """Flip the favourite flag; returns the new state (False if the entry doesn't exist)."""
        key = term.strip().lower()
        with self._conn() as conn:
            cur = conn.execute("SELECT is_favorite FROM lexicon WHERE query_key = ?", (key,))
            row = cur.fetchone()
            if not row:
                return False
            new_val = 0 if row[0] else 1
            conn.execute("UPDATE lexicon SET is_favorite = ? WHERE query_key = ?", (new_val, key))
            return bool(new_val)

    def delete_entry(self, term: str) -> None:
        key = term.strip().lower()
        with self._conn() as conn:
            conn.execute("DELETE FROM lexicon WHERE query_key = ?", (key,))

    def english_entries_with_persian(self, fa: str, limit: int = 8) -> List[str]:
        """English entries in your history whose Persian meaning (any comma-separated part) matches `fa`."""
        target = lexical_engine.fa_key(fa)
        if not target:
            return []
        with self._conn() as conn:
            rows = conn.execute("SELECT display_term, payload FROM lexicon ORDER BY updated_at DESC").fetchall()
        matches = []
        for term, payload in rows:
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            if data.get("is_reverse"):
                continue
            parts = re.split(r"[،,]", data.get("persian_equivalent", "") or "")
            if any(lexical_engine.fa_key(p) == target for p in parts):
                matches.append(term)
                if len(matches) >= limit:
                    break
        return matches

    def list_entries(
        self, favorites_only: bool = False, query: str = "", sort: str = SORT_RECENT
    ) -> List[Dict[str, Any]]:
        """History rows, optionally narrowed to favourites and to those matching `query`, in `sort` order.

        `query` is a case-insensitive substring match on the English/display term and on the Persian
        meaning; Persian is compared through lexical_engine.fa_key so Arabic/Persian letter variants,
        diacritics and zero-width joiners do not matter. SORT_RECENT is newest first; SORT_MOST_LOOKED_UP
        is highest lookup count first, ties broken by recency."""
        sql = "SELECT query_key, display_term, is_favorite, updated_at, lookup_count"
        needle = lexical_engine.fa_key(query).lower()
        if needle:
            sql += ", payload"
        sql += " FROM lexicon"
        if favorites_only:
            sql += " WHERE is_favorite = 1"
        if sort == SORT_MOST_LOOKED_UP:
            sql += " ORDER BY lookup_count DESC, updated_at DESC, id DESC"
        else:
            sql += " ORDER BY updated_at DESC, id DESC"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        if needle:
            rows = [r for r in rows if self._matches(needle, r[0], r[1], r[5])]
        return [
            {
                "query_key": r[0],
                "display_term": r[1],
                "is_favorite": bool(r[2]),
                "updated_at": r[3],
                "lookup_count": r[4],
            }
            for r in rows
        ]

    @staticmethod
    def _matches(needle: str, query_key: str, display_term: str, payload: str) -> bool:
        if needle in query_key or needle in lexical_engine.fa_key(display_term).lower():
            return True
        try:
            persian = json.loads(payload).get("persian_equivalent", "")
        except (ValueError, AttributeError):
            return False
        return isinstance(persian, str) and needle in lexical_engine.fa_key(persian).lower()
