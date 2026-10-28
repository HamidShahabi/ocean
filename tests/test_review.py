import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from review import CardState, Grade, ReviewStore, format_delay, schedule  # noqa: E402

NOW = datetime(2026, 1, 10, 9, 0, 0)


class ScheduleTest(unittest.TestCase):
    def test_new_card_intervals(self):
        new = CardState()
        self.assertEqual(schedule(new, Grade.AGAIN, NOW).due, NOW + timedelta(minutes=10))
        self.assertEqual(schedule(new, Grade.HARD, NOW).interval_days, 0.5)
        self.assertEqual(schedule(new, Grade.GOOD, NOW).interval_days, 1.0)
        self.assertEqual(schedule(new, Grade.EASY, NOW).interval_days, 4.0)

    def test_good_progression_grows_by_ease(self):
        s = schedule(CardState(), Grade.GOOD, NOW)
        s = schedule(s, Grade.GOOD, NOW)
        self.assertEqual(s.interval_days, 3.0)
        s = schedule(s, Grade.GOOD, NOW)
        self.assertEqual(s.interval_days, 7.5)
        self.assertEqual(s.due, NOW + timedelta(days=7.5))
        self.assertEqual(s.reps, 3)

    def test_grades_are_ordered_for_every_state(self):
        for state in (CardState(), CardState(reps=1, interval_days=1.0),
                      CardState(reps=5, interval_days=40.0, ease=1.3)):
            hard, good, easy = (schedule(state, g, NOW).interval_days for g in (Grade.HARD, Grade.GOOD, Grade.EASY))
            self.assertLessEqual(hard, good, state)
            self.assertLessEqual(good, easy, state)

    def test_again_resets_and_lowers_ease_with_floor(self):
        s = schedule(CardState(reps=4, interval_days=20.0, ease=1.4, lapses=1), Grade.AGAIN, NOW)
        self.assertEqual((s.reps, s.interval_days, s.lapses), (0, 0.0, 2))
        self.assertEqual(s.ease, 1.3)

    def test_input_state_is_not_mutated(self):
        state = CardState(reps=2, interval_days=3.0)
        schedule(state, Grade.EASY, NOW)
        self.assertEqual(state, CardState(reps=2, interval_days=3.0))

    def test_format_delay(self):
        self.assertEqual(format_delay(timedelta(minutes=10)), "10m")
        self.assertEqual(format_delay(timedelta(minutes=59, seconds=50)), "1h")
        self.assertEqual(format_delay(timedelta(hours=5)), "5h")
        self.assertEqual(format_delay(timedelta(hours=23, minutes=59)), "1d")
        self.assertEqual(format_delay(timedelta(days=3)), "3d")
        self.assertEqual(format_delay(timedelta(days=45)), "1.5mo")
        self.assertEqual(format_delay(timedelta(days=730)), "2y")


class ReviewStoreTest(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.db)
        # Same columns as app.DatabaseManager's lexicon table.
        conn.execute("""CREATE TABLE lexicon (id INTEGER PRIMARY KEY AUTOINCREMENT, query_key TEXT UNIQUE NOT NULL,
                        display_term TEXT NOT NULL, payload TEXT NOT NULL, is_favorite INTEGER DEFAULT 0,
                        updated_at TEXT NOT NULL)""")
        for key, fav, saved in (("alpha", 1, "2026-01-01T00:00:00"), ("beta", 1, "2026-01-02T00:00:00"),
                                ("gamma", 0, "2026-01-03T00:00:00")):
            conn.execute("INSERT INTO lexicon (query_key, display_term, payload, is_favorite, updated_at) VALUES (?,?,?,?,?)",
                         (key, key.title(), json.dumps({"headword": key.title()}), fav, saved))
        conn.commit()
        conn.close()
        self.store = ReviewStore(self.db)

    def tearDown(self):
        os.remove(self.db)

    def test_only_favourites_are_due_and_new_cards_ordered_by_save_date(self):
        cards = self.store.due_cards(NOW)
        self.assertEqual([c.query_key for c in cards], ["alpha", "beta"])
        self.assertIsNone(cards[0].state.due)
        self.assertEqual(cards[0].payload["headword"], "Alpha")
        self.assertEqual(self.store.due_count(NOW), 2)

    def test_reviewed_card_leaves_queue_until_due(self):
        state = schedule(CardState(), Grade.GOOD, NOW)
        self.store.save_state("alpha", state, NOW)
        self.assertEqual([c.query_key for c in self.store.due_cards(NOW)], ["beta"])
        self.assertEqual(self.store.next_due(), NOW + timedelta(days=1))
        later = NOW + timedelta(days=1, minutes=1)
        due = self.store.due_cards(later)
        self.assertEqual([c.query_key for c in due], ["alpha", "beta"])  # seen-before cards first
        self.assertEqual(due[0].state, state)

    def test_unfavourited_card_is_paused_not_lost(self):
        self.store.save_state("alpha", schedule(CardState(), Grade.GOOD, NOW), NOW)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE lexicon SET is_favorite = 0 WHERE query_key = 'alpha'")
        conn.commit()
        conn.close()
        self.assertEqual(self.store.due_count(NOW + timedelta(days=30)), 1)  # only beta
        self.assertIsNone(self.store.next_due())
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE lexicon SET is_favorite = 1 WHERE query_key = 'alpha'")
        conn.commit()
        conn.close()
        self.assertEqual(self.store.next_due(), NOW + timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
