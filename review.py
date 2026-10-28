# === SPACED-REPETITION REVIEW: SM-2 SCHEDULING & REVIEW-STATE STORAGE ===
# Favourited lexicon entries are the deck. Review progress lives in its own `reviews` table in the
# same SQLite file, keyed by query_key, so un-starring a word pauses it and re-starring resumes it.
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Dict, Iterator, List, Optional

RELEARN_DELAY = timedelta(minutes=10)
MIN_EASE = 1.3
START_EASE = 2.5


class Grade(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


@dataclass
class CardState:
    reps: int = 0            # successful reviews in a row (reset by Again)
    interval_days: float = 0.0
    ease: float = START_EASE
    lapses: int = 0
    due: Optional[datetime] = None   # None = new card, due immediately


def schedule(state: CardState, grade: Grade, now: datetime) -> CardState:
    """SM-2 with Anki-style four buttons. Returns the new state; `state` is not modified."""
    if grade == Grade.AGAIN:
        return CardState(reps=0, interval_days=0.0, ease=max(MIN_EASE, state.ease - 0.2),
                         lapses=state.lapses + 1, due=now + RELEARN_DELAY)

    if state.reps == 0:
        intervals = {Grade.HARD: 0.5, Grade.GOOD: 1.0, Grade.EASY: 4.0}
    else:
        good = 3.0 if state.reps == 1 else state.interval_days * state.ease
        # ease >= MIN_EASE (1.3) > 1.2, so Hard <= Good <= Easy always holds.
        intervals = {Grade.HARD: max(1.0, state.interval_days * 1.2), Grade.GOOD: good, Grade.EASY: good * 1.3}
    interval = intervals[grade]

    ease = state.ease
    if grade == Grade.HARD:
        ease = max(MIN_EASE, ease - 0.15)
    elif grade == Grade.EASY:
        ease += 0.15
    return CardState(reps=state.reps + 1, interval_days=round(interval, 2), ease=round(ease, 2),
                     lapses=state.lapses, due=now + timedelta(days=interval))


def format_delay(delay: timedelta) -> str:
    """Compact hint shown on grade buttons: 10m, 1d, 3.5mo, 1.2y."""
    minutes = delay.total_seconds() / 60
    if round(minutes) < 60:
        return f"{max(1, round(minutes))}m"
    if round(minutes / 60) < 24:
        return f"{round(minutes / 60)}h"
    days = minutes / 1440
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{days / 30:.1f}mo".replace(".0mo", "mo")
    return f"{days / 365:.1f}y".replace(".0y", "y")


@dataclass
class ReviewCard:
    query_key: str
    display_term: str
    payload: Dict[str, Any]
    state: CardState


class ReviewStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    query_key TEXT PRIMARY KEY,
                    reps INTEGER NOT NULL,
                    interval_days REAL NOT NULL,
                    ease REAL NOT NULL,
                    lapses INTEGER NOT NULL,
                    due TEXT NOT NULL,
                    last_reviewed TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Commit on success and always close (sqlite3's own context manager only commits)."""
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def due_cards(self, now: datetime, limit: int = 100) -> List[ReviewCard]:
        """Due favourites, cards seen before first (most overdue first), then new cards (oldest saved first)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT l.query_key, l.display_term, l.payload,
                       r.reps, r.interval_days, r.ease, r.lapses, r.due
                FROM lexicon l LEFT JOIN reviews r ON r.query_key = l.query_key
                WHERE l.is_favorite = 1 AND (r.due IS NULL OR r.due <= ?)
                ORDER BY r.due IS NULL, r.due, l.updated_at
                LIMIT ?
                """,
                (now.isoformat(timespec="seconds"), limit),
            ).fetchall()
        cards = []
        for key, term, payload, reps, interval, ease, lapses, due in rows:
            state = CardState() if due is None else CardState(reps, interval, ease, lapses, datetime.fromisoformat(due))
            cards.append(ReviewCard(key, term, json.loads(payload), state))
        return cards

    def due_count(self, now: datetime) -> int:
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(*) FROM lexicon l LEFT JOIN reviews r ON r.query_key = l.query_key
                WHERE l.is_favorite = 1 AND (r.due IS NULL OR r.due <= ?)
                """,
                (now.isoformat(timespec="seconds"),),
            ).fetchone()[0]

    def next_due(self) -> Optional[datetime]:
        """Earliest upcoming due time among favourites already reviewed at least once."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT MIN(r.due) FROM reviews r JOIN lexicon l ON l.query_key = r.query_key
                WHERE l.is_favorite = 1
                """
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def save_state(self, query_key: str, state: CardState, now: datetime) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO reviews (query_key, reps, interval_days, ease, lapses, due, last_reviewed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_key) DO UPDATE SET
                    reps = excluded.reps, interval_days = excluded.interval_days, ease = excluded.ease,
                    lapses = excluded.lapses, due = excluded.due, last_reviewed = excluded.last_reviewed
                """,
                (query_key, state.reps, state.interval_days, state.ease, state.lapses,
                 state.due.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
            )
