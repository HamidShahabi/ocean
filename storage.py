# Qt-free storage for history and favourites (the `lexicon` table).
# Review progress lives in the same database file but is handled by review.ReviewStore.
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

import lexical_engine


class HistoryStore:
    """Looked-up entries: history plus the favourites flag."""

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

    def list_entries(self, favorites_only: bool = False) -> List[Dict[str, Any]]:
        query = "SELECT query_key, display_term, is_favorite, updated_at FROM lexicon"
        if favorites_only:
            query += " WHERE is_favorite = 1"
        query += " ORDER BY updated_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query).fetchall()
        return [
            {
                "query_key": r[0],
                "display_term": r[1],
                "is_favorite": bool(r[2]),
                "updated_at": r[3],
            }
            for r in rows
        ]
