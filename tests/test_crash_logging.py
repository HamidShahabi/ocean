import logging
import os
import sys
import tempfile
import threading
import unittest

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexical_engine as le  # noqa: E402


class UncaughtExceptionLoggingTest(unittest.TestCase):
    def setUp(self):
        saved = sys.excepthook, threading.excepthook
        self.addCleanup(lambda: (setattr(sys, "excepthook", saved[0]), setattr(threading, "excepthook", saved[1])))
        le.log_uncaught_exceptions()

    def test_main_thread_crash_is_logged(self):
        with self.assertLogs("ocean", logging.CRITICAL) as logs:
            try:
                raise ValueError("boom")
            except ValueError:
                sys.excepthook(*sys.exc_info())
        self.assertIn("ValueError: boom", logs.output[0])

    def test_background_thread_crash_is_logged(self):
        def crash():
            raise RuntimeError("thread boom")

        with self.assertLogs("ocean", logging.CRITICAL) as logs:
            t = threading.Thread(target=crash, name="preload")
            t.start()
            t.join()
        self.assertIn("thread preload", logs.output[0])
        self.assertIn("RuntimeError: thread boom", logs.output[0])


if __name__ == "__main__":
    unittest.main()
