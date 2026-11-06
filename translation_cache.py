"""Ocean's permanent translation cache: every English -> Persian translation the app generated (Google Translate
online, the local language model or the NLLB translator offline) is kept in a small SQLite file in the user's data
folder, so asking for the same word, phrase or sentence again is instant, works without internet and always gives
the same answer.

Each row remembers where the translation came from and a quality rank (Google 3, local language model 2, NLLB 1):
a better translation replaces a worse one, never the other way round, so a word translated offline is upgraded the
next time Google is reachable (or when the user presses Refresh). Qt-free; every failure is logged and answers
"not cached" instead of raising, because a lookup must never fail over its cache.
"""
import logging
import sqlite3
import threading
import time
from typing import Optional, Tuple

from lexicon_db import text_key

log = logging.getLogger("ocean.cache")

KIND_TERM = "w"       # a word or short phrase: keyed without punctuation and case
KIND_SENTENCE = "s"   # a sentence: keyed by its lower-case text with punctuation


def _key(text: str, kind: str) -> str:
    return kind + ":" + (text_key(text) if kind == KIND_TERM else " ".join(text.lower().split()))


class TranslationCache:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._failed = False

    def _connection(self) -> Optional[sqlite3.Connection]:
        if self._conn is None and not self._failed:
            try:
                conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
                conn.execute("CREATE TABLE IF NOT EXISTS fa_cache (key TEXT PRIMARY KEY, en TEXT NOT NULL, fa TEXT NOT NULL, "
                             "source TEXT NOT NULL, quality INTEGER NOT NULL, saved_at REAL NOT NULL) WITHOUT ROWID")
                conn.commit()
                self._conn = conn
            except sqlite3.Error:
                self._failed = True
                log.exception("Could not open the translation cache %s: translations will not be remembered", self.path)
        return self._conn

    def get(self, text: str, kind: str = KIND_TERM) -> Optional[Tuple[str, str, int]]:
        """(Persian, source, quality) of the cached translation, None when there is none."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return None
            try:
                row = conn.execute("SELECT fa, source, quality FROM fa_cache WHERE key = ?", (_key(text, kind),)).fetchone()
            except sqlite3.Error:
                log.exception("Translation cache read failed for %r", text)
                return None
        return (row[0], row[1], row[2]) if row else None

    def put(self, text: str, fa: str, source: str, quality: int, kind: str = KIND_TERM) -> None:
        """Remember a translation unless a better one is already cached (a result of the same quality replaces it)."""
        if not (text.strip() and fa.strip()):
            return
        with self._lock:
            conn = self._connection()
            if conn is None:
                return
            try:
                conn.execute(
                    "INSERT INTO fa_cache (key, en, fa, source, quality, saved_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET fa = excluded.fa, source = excluded.source, quality = excluded.quality, "
                    "saved_at = excluded.saved_at WHERE excluded.quality >= fa_cache.quality",
                    (_key(text, kind), text.strip(), fa.strip(), source, quality, time.time()),
                )
                conn.commit()
            except sqlite3.Error:
                log.exception("Translation cache write failed for %r", text)

    def count(self) -> int:
        with self._lock:
            conn = self._connection()
            if conn is None:
                return 0
            try:
                return conn.execute("SELECT COUNT(*) FROM fa_cache").fetchone()[0]
            except sqlite3.Error:
                return 0

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        self._failed = False
