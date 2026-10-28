import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtNetwork import QLocalSocket  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import desktop_lookup  # noqa: E402
from desktop_lookup import InstanceServer, clean_lookup_text, read_selected_text, read_x11_selection  # noqa: E402

# A QApplication rather than a QCoreApplication: the UI smoke test shares this process and needs the GUI flavour.
APP = QApplication.instance() or QApplication(sys.argv)


class CleanLookupTextTest(unittest.TestCase):
    def test_single_word_loses_quotes_and_punctuation(self):
        self.assertEqual(clean_lookup_text("  “Serendipity,” "), "Serendipity")
        self.assertEqual(clean_lookup_text("word."), "word")
        self.assertEqual(clean_lookup_text("(ephemeral)"), "ephemeral")

    def test_sentence_keeps_final_punctuation_and_collapses_whitespace(self):
        self.assertEqual(clean_lookup_text("Bite\n the\tbullet."), "Bite the bullet.")

    def test_length_is_capped(self):
        self.assertEqual(len(clean_lookup_text("a " * 1000)), desktop_lookup.MAX_LOOKUP_CHARS - 1)

    def test_empty(self):
        self.assertEqual(clean_lookup_text("  \n "), "")


class InstanceHandOffTest(unittest.TestCase):
    def setUp(self):
        name = f"ocean-test-{os.getpid()}-{time.monotonic_ns()}"
        patcher = mock.patch.object(desktop_lookup, "_server_name", return_value=name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pump(self, until, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not until() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)

    def test_no_running_instance(self):
        self.assertFalse(desktop_lookup.send_to_running_instance({"action": "show"}, timeout_ms=200))

    def _send_from_another_process(self, message):
        """Real hand-offs come from a separate process; sending from this thread would block the
        server's event loop (on Windows it must prepare a new pipe instance per connection)."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]);"
                "from PyQt6.QtCore import QCoreApplication; app = QCoreApplication([]);"
                "import desktop_lookup; desktop_lookup._server_name = lambda: sys.argv[2];"
                "sys.exit(0 if desktop_lookup.send_to_running_instance(json.loads(sys.argv[3])) else 1)")
        return subprocess.Popen([sys.executable, "-c", code, repo, desktop_lookup._server_name(), json.dumps(message)])

    def test_message_round_trip_and_malformed_input_ignored(self):
        server = InstanceServer()
        self.assertTrue(server.listen())
        received = []
        server.message_received.connect(received.append)

        bad = QLocalSocket()
        bad.connectToServer(desktop_lookup._server_name())
        self.assertTrue(bad.waitForConnected(1000))
        bad.write(b"not json\n")
        bad.waitForBytesWritten(1000)
        self._pump(lambda: False, timeout=0.3)  # let the server accept it and read the bad line

        message = {"action": "lookup", "text": "zoodgozar \u0632\u0648\u062f\u06af\u0630\u0631"}
        sender = self._send_from_another_process(message)
        self._pump(lambda: received and sender.poll() is not None, timeout=20)
        self.assertEqual(sender.wait(5), 0)
        self.assertEqual(received, [message])
        bad.abort()
        server.server.close()

    def test_stale_socket_is_replaced(self):
        first = InstanceServer()
        self.assertTrue(first.listen())
        first.server.close()  # on Unix a crash leaves the socket file; close() is the clean equivalent
        second = InstanceServer()
        self.assertTrue(second.listen())
        second.server.close()


class SelectionReadingTest(unittest.TestCase):
    def test_no_display_means_no_selection(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None)
            self.assertEqual(read_selected_text(), "")

    def test_unreachable_display_returns_none(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":987"}):
            self.assertIsNone(read_x11_selection("CLIPBOARD", 0.2))


if __name__ == "__main__":
    unittest.main()
