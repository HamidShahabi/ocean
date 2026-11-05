# Qt-free storage for history and favourites (the `lexicon` table).
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional

import lexical_engine

# Settings choices for "refresh saved entries": label -> days (None = never refresh).
REFRESH_PERIOD_DAYS: Dict[str, Optional[int]] = {
    "Never": None,
    "1 week": 7,
    "1 month": 30,
    "3 months": 90,
    "6 months": 180,
}
DEFAULT_REFRESH_PERIOD = "3 months"


def refresh_period_days(label: Any) -> Optional[int]:
    """Days for a Settings label; unknown labels fall back to the default period."""
    return REFRESH_PERIOD_DAYS.get(label, REFRESH_PERIOD_DAYS[DEFAULT_REFRESH_PERIOD])


def is_stale(
    produced_at: Optional[str],
    engine_version: Optional[int],
    period_days: Optional[int],
    now: Optional[datetime] = None,
    current_version: Optional[int] = None,
) -> bool:
    """Whether a saved entry should be fetched again.

    Stale = produced by an older engine version (entries saved before versions were
    recorded count as version 0), or produced `period_days` or more ago. A missing or
    unreadable timestamp can't prove the entry is fresh, so it counts as stale.
    `period_days` None means the user chose "Never": nothing is ever stale.
    """
    if period_days is None:
        return False
    if current_version is None:
        current_version = lexical_engine.ENGINE_VERSION
    if (engine_version or 0) < current_version:
        return True
    try:
        produced = datetime.fromisoformat(produced_at) if produced_at else None
    except (TypeError, ValueError):
        produced = None
    if produced is None:
        return True
    return (now or datetime.now()) - produced >= timedelta(days=period_days)


def has_real_translation(data: Dict[str, Any]) -> bool:
    equiv = (data.get("persian_equivalent") or "").strip()
    return bool(equiv) and equiv not in lexical_engine.INVALID_FA_HEADERS and equiv != lexical_engine.NOT_FOUND_FA


def is_useful_lookup(data: Optional[Dict[str, Any]]) -> bool:
    """A lookup result worth saving over an old copy: it has senses, or it's a sentence with a translation."""
    if not data:
        return False
    if data.get("senses"):
        return True
    return bool(data.get("is_sentence")) and has_real_translation(data)


def refresh_replaces(old: Dict[str, Any], new: Optional[Dict[str, Any]]) -> bool:
    """Whether a refetched result should replace the stale copy (offline fetches usually come back empty)."""
    if not is_useful_lookup(new):
        return False
    return not (has_real_translation(old) and not has_real_translation(new))


class HistoryStore:
    """Looked-up entries: history plus the favourites flag, newest lookup first."""

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
            # produced_at / engine_version say when and by which engine the payload was fetched
            # (updated_at changes on every save, including edits). NULL = saved before they existed.
            self._add_column_if_missing(conn, "produced_at", "TEXT")
            self._add_column_if_missing(conn, "engine_version", "INTEGER")
            # Progress table of the removed Review feature. (Older databases keep their now unused
            # lookup_count column: it has a default, so inserts ignore it.)
            conn.execute("DROP TABLE IF EXISTS reviews")

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

    def save_entry(self, term: str, payload: Dict[str, Any], fetched: bool = True) -> None:
        """Save a payload. `fetched` stamps it as freshly produced by the current engine; pass False
        for edits to an existing entry (fixing a translation) so they don't make it look newer."""
        key = term.strip().lower()
        now = datetime.now().isoformat(timespec="seconds")
        # Keys starting with "_" are added on the way out (get_entry, the UI) and never stored.
        json_str = json.dumps({k: v for k, v in payload.items() if not k.startswith("_")}, ensure_ascii=False)
        with self._conn() as conn:
            cur = conn.execute("SELECT is_favorite FROM lexicon WHERE query_key = ?", (key,))
            row = cur.fetchone()
            fav = row[0] if row else 0
            conn.execute(
                """
                INSERT INTO lexicon (query_key, display_term, payload, is_favorite, updated_at,
                                     produced_at, engine_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_key) DO UPDATE SET
                    display_term = excluded.display_term,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (key, payload.get("headword", term.strip()), json_str, fav, now, now, lexical_engine.ENGINE_VERSION),
            )
            if fetched:
                conn.execute(
                    "UPDATE lexicon SET produced_at = ?, engine_version = ? WHERE query_key = ?",
                    (now, lexical_engine.ENGINE_VERSION, key),
                )

    def touch_entry(self, term: str) -> None:
        """Move an existing entry to the top of the recent list (a term not stored yet is ignored:
        save_entry() creates it)."""
        key = term.strip().lower()
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("UPDATE lexicon SET updated_at = ? WHERE query_key = ?", (now, key))

    def get_entry(self, term: str) -> Optional[Dict[str, Any]]:
        key = term.strip().lower()
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT payload, is_favorite, produced_at, engine_version FROM lexicon WHERE query_key = ?", (key,)
            )
            row = cur.fetchone()
        if row:
            data = json.loads(row[0])
            data["_is_favorite"] = bool(row[1])
            data["_produced_at"] = row[2]
            data["_engine_version"] = row[3] or 0
            return data
        return None

    def is_entry_stale(self, entry: Dict[str, Any], period_days: Optional[int], now: Optional[datetime] = None) -> bool:
        """is_stale() for an entry returned by get_entry()."""
        return is_stale(entry.get("_produced_at"), entry.get("_engine_version"), period_days, now)

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

    def list_entries(self, favorites_only: bool = False, query: str = "") -> List[Dict[str, Any]]:
        """History rows, newest first, optionally narrowed to favourites and to those matching `query`.

        `query` is a case-insensitive substring match on the English/display term and on the Persian
        meaning; Persian is compared through lexical_engine.fa_key so Arabic/Persian letter variants,
        diacritics and zero-width joiners do not matter."""
        sql = "SELECT query_key, display_term, is_favorite, updated_at"
        needle = lexical_engine.fa_key(query).lower()
        if needle:
            sql += ", payload"
        sql += " FROM lexicon"
        if favorites_only:
            sql += " WHERE is_favorite = 1"
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        if needle:
            rows = [r for r in rows if self._matches(needle, r[0], r[1], r[4])]
        return [
            {"query_key": r[0], "display_term": r[1], "is_favorite": bool(r[2]), "updated_at": r[3]}
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
