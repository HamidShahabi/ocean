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

import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402

import desktop_lookup  # noqa: E402
from desktop_lookup import (  # noqa: E402
    DoubleCopyDetector, DoubleCopyWatcher, InstanceServer, WindowsHotkeys, clean_lookup_text, read_selected_text,
    read_x11_selection)

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


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class DoubleCopyDetectorTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.detector = DoubleCopyDetector(window=0.6, duplicate_gap=0.08, clock=self.clock)

    def copy(self, text, after=0.0, **kwargs):
        self.clock.advance(after)
        return self.detector.on_clipboard_change(lambda: text, **kwargs)

    def test_one_copy_does_nothing(self):
        self.assertIsNone(self.copy("serendipity"))

    def test_two_copies_of_the_same_text_trigger(self):
        self.copy("serendipity")
        self.assertEqual(self.copy("serendipity", after=0.3), "serendipity")

    def test_second_copy_after_the_window_does_not_trigger_but_starts_a_new_pair(self):
        self.copy("word")
        self.assertIsNone(self.copy("word", after=0.9))
        self.assertEqual(self.copy("word", after=0.2), "word")

    def test_window_edges(self):
        self.copy("word")
        self.assertEqual(self.copy("word", after=0.5), "word")
        self.copy("word")
        self.assertIsNone(self.copy("word", after=0.7))

    def test_different_text_does_not_trigger(self):
        self.copy("one")
        self.assertIsNone(self.copy("two", after=0.2))
        self.assertEqual(self.copy("two", after=0.2), "two")

    def test_whitespace_differences_are_ignored_and_the_text_is_normalised(self):
        self.copy("bite the\nbullet ")
        self.assertEqual(self.copy(" bite  the bullet", after=0.2), "bite the bullet")

    def test_one_copy_written_several_times_is_not_a_double_copy(self):
        for _ in range(4):
            self.assertIsNone(self.copy("word", after=0.01))
        self.assertEqual(self.copy("word", after=0.25), "word")   # the real second press still counts

    def test_duplicate_writes_after_the_second_press_do_not_retrigger(self):
        self.copy("word")
        self.assertEqual(self.copy("word", after=0.3), "word")
        self.assertIsNone(self.copy("word", after=0.01))   # the second press written again
        self.assertIsNone(self.copy("word", after=0.01))

    def test_third_press_starts_a_new_pair(self):
        self.copy("word")
        self.assertEqual(self.copy("word", after=0.2), "word")
        self.assertIsNone(self.copy("word", after=0.2))
        self.assertEqual(self.copy("word", after=0.2), "word")

    def test_own_writes_are_ignored_and_break_the_sequence(self):
        self.copy("word")
        self.assertIsNone(self.copy("word", after=0.2, own=True))
        self.assertIsNone(self.copy("word", after=0.1))   # the pair was broken: this is a first copy again
        self.assertIsNone(self.copy("word", after=0.2, own=True))
        self.assertIsNone(self.copy("word", after=0.1, own=True))

    def test_changes_without_ctrl_are_ignored_and_never_read(self):
        reads = []
        self.assertIsNone(self.detector.on_clipboard_change(lambda: reads.append(1) or "word", ctrl_down=False))
        self.assertIsNone(self.detector.on_clipboard_change(lambda: reads.append(1) or "word", own=True))
        self.assertEqual(reads, [])
        self.copy("word")
        self.assertIsNone(self.copy("word", after=0.2, ctrl_down=False))
        self.assertIsNone(self.copy("word", after=0.1))

    def test_empty_or_non_text_copies_break_the_sequence(self):
        self.copy("word")
        self.assertIsNone(self.copy("", after=0.1))
        self.assertIsNone(self.copy("word", after=0.1))
        self.assertIsNone(self.copy("   \n", after=0.1))
        self.assertIsNone(self.copy("word", after=0.1))

    def test_unreadable_clipboard_returns_none(self):
        self.copy("word")
        self.assertIsNone(self.detector.on_clipboard_change(lambda: None))

    def test_sensitive_formats_are_recognised(self):
        self.assertTrue(desktop_lookup.is_sensitive_clipboard(
            ["text/plain", 'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"']))
        self.assertFalse(desktop_lookup.is_sensitive_clipboard(["text/plain", "text/html"]))


class FakeMime:
    def __init__(self, text, formats=("text/plain",)):
        self.text, self._formats = text, list(formats)

    def hasText(self):
        return bool(self.text)

    def formats(self):
        return self._formats


class FakeClipboard(QObject):
    dataChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.mime = FakeMime("")

    def mimeData(self):
        return self.mime

    def text(self):
        return self.mime.text

    def copy(self, text, **kwargs):
        self.mime = FakeMime(text, **kwargs)
        self.dataChanged.emit()


class DoubleCopyWatcherTest(unittest.TestCase):
    def setUp(self):
        self.clipboard = FakeClipboard()
        self.found = []
        self.clock = FakeClock()
        self.watcher = DoubleCopyWatcher(self.clipboard, self.found.append)
        self.watcher.detector.clock = self.clock
        self.ctrl, self.own = True, False
        for name, value in (("ctrl_key_down", lambda: self.ctrl), ("clipboard_written_by_ocean", lambda: self.own)):
            patcher = mock.patch.object(desktop_lookup, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def copy_twice(self, text="serendipity", **kwargs):
        self.clipboard.copy(text, **kwargs)
        self.clock.advance(0.3)
        self.clipboard.copy(text, **kwargs)

    def test_double_copy_calls_back_with_the_text(self):
        self.copy_twice()
        self.assertEqual(self.found, ["serendipity"])

    def test_copies_not_made_with_ctrl_are_ignored(self):
        self.ctrl = False
        self.copy_twice()
        self.assertEqual(self.found, [])

    def test_ocean_s_own_copies_are_ignored(self):
        self.own = True
        self.copy_twice()
        self.assertEqual(self.found, [])

    def test_secrets_and_images_are_not_read(self):
        secret = ["text/plain", 'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"']
        self.copy_twice("hunter2", formats=secret)
        self.copy_twice("")
        self.assertEqual(self.found, [])

    def test_a_failure_while_reading_is_contained(self):
        with mock.patch.object(desktop_lookup, "ctrl_key_down", side_effect=OSError("boom")), \
                self.assertLogs("ocean.lookup", "ERROR"):
            self.copy_twice()
        self.assertEqual(self.found, [])


class FakeUser32:
    """Stands in for the ctypes user32 on a machine without one, recording the calls."""

    def __init__(self):
        self.calls = []
        self.key_state = 0
        self.clipboard_owner = None
        self.owner_pid = 0
        self.foreground_thread = 77
        self.iconic = False
        self.register_results = {}

    def GetAsyncKeyState(self, vk):
        self.calls.append(("GetAsyncKeyState", vk))
        return self.key_state

    def GetClipboardOwner(self):
        return self.clipboard_owner

    def GetForegroundWindow(self):
        return 0xFEED

    def GetWindowThreadProcessId(self, hwnd, pid_ref):
        pid_ref._obj.value = self.owner_pid
        return self.foreground_thread

    def AttachThreadInput(self, ours, theirs, attach):
        self.calls.append(("AttachThreadInput", ours, theirs, bool(attach)))
        return 1

    def IsIconic(self, hwnd):
        return self.iconic

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("ShowWindow", hwnd, cmd))

    def BringWindowToTop(self, hwnd):
        self.calls.append(("BringWindowToTop", hwnd))

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))

    def RegisterHotKey(self, hwnd, key_id, modifiers, vk):
        self.calls.append(("RegisterHotKey", hwnd, key_id, modifiers, vk))
        return self.register_results.get(key_id, 1)


class WindowsPlumbingTest(unittest.TestCase):
    def setUp(self):
        self.user32 = FakeUser32()
        kernel32 = mock.Mock()
        kernel32.GetCurrentThreadId.return_value = 11
        for name, value in (("_user32", self.user32), ("_kernel32", kernel32)):
            patcher = mock.patch.object(desktop_lookup, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ctrl_state_uses_the_high_bit_of_the_ctrl_key(self):
        self.user32.key_state = -32768   # SHORT with the high bit set: down
        self.assertTrue(desktop_lookup.ctrl_key_down())
        self.user32.key_state = 1        # only "pressed since last call"
        self.assertFalse(desktop_lookup.ctrl_key_down())
        self.assertEqual(self.user32.calls[0], ("GetAsyncKeyState", 0x11))

    def test_clipboard_owner_is_compared_with_our_process(self):
        self.assertFalse(desktop_lookup.clipboard_written_by_ocean())   # no owner
        self.user32.clipboard_owner = 0x1234
        self.user32.owner_pid = os.getpid() + 1
        self.assertFalse(desktop_lookup.clipboard_written_by_ocean())
        self.user32.owner_pid = os.getpid()
        self.assertTrue(desktop_lookup.clipboard_written_by_ocean())

    def test_focus_borrows_the_foreground_thread_and_always_lets_go(self):
        self.user32.iconic = True
        desktop_lookup.focus_window(0xABC)
        self.assertEqual([c[0] for c in self.user32.calls],
                         ["AttachThreadInput", "ShowWindow", "BringWindowToTop", "SetForegroundWindow",
                          "AttachThreadInput"])
        self.assertEqual(self.user32.calls[0], ("AttachThreadInput", 11, 77, True))
        self.assertEqual(self.user32.calls[-1], ("AttachThreadInput", 11, 77, False))

    def test_focus_detaches_even_if_bringing_to_front_fails(self):
        self.user32.SetForegroundWindow = mock.Mock(side_effect=OSError("denied"))
        with self.assertLogs("ocean.lookup", "WARNING"):
            desktop_lookup.focus_window(0xABC)   # logged, not raised
        self.assertEqual(self.user32.calls[-1], ("AttachThreadInput", 11, 77, False))

    def test_focus_skips_attaching_to_its_own_thread(self):
        self.user32.foreground_thread = 11
        desktop_lookup.focus_window(0xABC)
        self.assertNotIn("AttachThreadInput", [c[0] for c in self.user32.calls])

    def test_both_chords_are_registered_and_one_failure_is_not_fatal(self):
        hotkeys = WindowsHotkeys(lambda: None)
        self.assertTrue(hotkeys.register())
        registered = {c[4]: c[3] for c in self.user32.calls if c[0] == "RegisterHotKey"}
        self.assertEqual(registered[0x51], 0x0001 | 0x4000)            # Alt+Q
        self.assertEqual(registered[0x4F], 0x0001 | 0x0002 | 0x4000)   # Ctrl+Alt+O
        self.user32.register_results = {0x0CEB: 0}
        with self.assertLogs("ocean.lookup", "WARNING") as logs:
            self.assertTrue(hotkeys.register())
        self.assertIn(desktop_lookup.CHORD_LABEL, logs.output[0])
        self.user32.register_results = {0x0CEA: 0, 0x0CEB: 0}
        with self.assertLogs("ocean.lookup", "WARNING"):
            self.assertFalse(hotkeys.register())

    def test_wm_hotkey_for_our_ids_is_consumed(self):
        fired = []
        hotkeys = WindowsHotkeys(lambda: fired.append(1))

        def post(message, w_param):
            msg = wintypes.MSG()
            msg.message, msg.wParam = message, w_param
            return hotkeys.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))

        self.assertEqual(post(0x0312, 0x0CEB), (True, 0))
        self.assertEqual(post(0x0312, 0x0CEA), (True, 0))
        self.assertEqual(post(0x0312, 0x9999), (False, 0))   # someone else's hotkey
        self.assertEqual(post(0x0100, 0x0CEB), (False, 0))   # not WM_HOTKEY
        self.assertEqual(hotkeys.nativeEventFilter(b"xcb_generic_event_t", 0), (False, 0))
        self.assertEqual(len(fired), 2)

    def test_labels(self):
        self.assertIn("Ctrl+C twice", desktop_lookup.HOTKEY_LABEL)
        self.assertIn("Alt+Q", desktop_lookup.HOTKEY_LABEL)


if __name__ == "__main__":
    unittest.main()
