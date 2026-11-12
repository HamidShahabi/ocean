import io
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest import mock

_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from ui import speaker as speaker_mod  # noqa: E402
from ui.config import SettingsManager  # noqa: E402

RATE = 24000


class FakeKokoro:
    """Stands in for kokoro_onnx.Kokoro (not installed): 2 s of silence at 24 kHz."""

    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed))
        time.sleep(self.delay)
        return np.zeros(2 * RATE, dtype=np.float32), RATE


def make_speaker(kokoro=None):
    with mock.patch.object(speaker_mod.NeuralSpeaker, "_load_kokoro"):  # no background load
        sp = speaker_mod.NeuralSpeaker(SettingsManager())
    sp._loaded.set()
    sp._kokoro = kokoro
    return sp


def kokoro_env():
    """Patches that let a speaker 'synthesize' with FakeKokoro and 'play' through a fake CLI player."""
    return [
        mock.patch.object(speaker_mod, "np", np, create=True),
        mock.patch.object(speaker_mod, "HAS_KOKORO", True),
        mock.patch.object(speaker_mod, "HAS_WINSOUND", False),
        mock.patch.object(speaker_mod, "WAV_PLAYER_CMD", ["fake-player"]),
    ]


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class KokoroPlaybackTest(unittest.TestCase):
    def setUp(self):
        self.kokoro = FakeKokoro()
        self.sp = make_speaker(self.kokoro)
        self.played = []
        patches = kokoro_env() + [mock.patch.object(speaker_mod.subprocess, "Popen")]
        for p in patches:
            self.addCleanup(p.stop)
        for p in patches[:-1]:
            p.start()
        self.popen = patches[-1].start()
        self.popen.return_value.communicate.side_effect = lambda data, timeout: self.played.append(data)

    def test_clip_is_padded_with_a_short_lead_in_and_tail(self):
        self.sp._token = 5
        self.sp._speak("Hello there.", 5)
        self.assertEqual(len(self.played), 1)
        with wave.open(io.BytesIO(self.played[0])) as wf:
            frames = wf.getnframes()
            self.assertEqual(wf.getframerate(), RATE)
        expected = int(RATE * speaker_mod.LEAD_IN_SECS) + 2 * RATE + int(RATE * speaker_mod.TAIL_SECS)
        self.assertEqual(frames, expected)
        self.assertLessEqual(speaker_mod.LEAD_IN_SECS, 0.1)  # the lead-in is pure start-up delay

    def test_a_replayed_clip_is_not_synthesized_again(self):
        for _ in range(3):
            self.sp._token += 1
            self.sp._speak("Hello there.", self.sp._token)
        self.assertEqual(len(self.played), 3)
        self.assertEqual(len(self.kokoro.calls), 1)

    def test_voice_or_speed_change_makes_a_new_clip(self):
        self.sp._speak("Hello there.", self.sp._token)
        self.sp.settings.data["speech_rate"] = 2
        self.sp._speak("Hello there.", self.sp._token)
        self.assertEqual(len(self.kokoro.calls), 2)
        self.assertNotEqual(self.kokoro.calls[0][2], self.kokoro.calls[1][2])

    def test_cache_drops_the_least_recently_used_clip_over_its_byte_budget(self):
        one_clip = len(self.sp._wav_for("a", "af_heart", 1.0))
        with mock.patch.object(speaker_mod, "CACHE_MAX_BYTES", one_clip * 2 + 1):
            self.sp._wav_for("b", "af_heart", 1.0)
            self.sp._wav_for("a", "af_heart", 1.0)  # touch a: b is now the oldest
            self.sp._wav_for("c", "af_heart", 1.0)
        self.assertEqual({k[0] for k in self.sp._cache}, {"a", "c"})
        self.assertEqual(self.sp._cache_bytes, sum(len(v) for v in self.sp._cache.values()))

    def test_superseded_clip_never_plays(self):
        self.sp._token = 9  # a newer clip exists
        self.sp._speak("old clip", 8)
        self.popen.assert_not_called()
        self.assertEqual(self.played, [])

    def test_the_next_click_silences_the_player(self):
        player = mock.Mock()
        player.poll.return_value = None
        self.sp._player = player
        self.sp._token = 3
        with mock.patch.object(threading, "Thread"):
            self.sp.speak("next")
        player.terminate.assert_called_once()
        self.assertGreater(self.sp._token, 3)

    def test_speak_ignores_blank_text_and_returns_nothing(self):
        with mock.patch.object(threading, "Thread") as thread:
            self.assertIsNone(self.sp.speak("   "))
            thread.assert_not_called()
            first = self.sp._token
            self.assertIsNone(self.sp.speak("one"))
        self.assertGreater(self.sp._token, first)


class PrefetchTest(unittest.TestCase):
    def setUp(self):
        self.kokoro = FakeKokoro()
        self.sp = make_speaker(self.kokoro)
        for p in kokoro_env():
            p.start()
            self.addCleanup(p.stop)

    def idle(self):
        return wait_for(lambda: self.sp._prefetcher is None and not self.sp._queue)

    def test_prefetched_clips_are_cached_before_any_click(self):
        self.sp.prefetch("ocean")
        self.sp.prefetch("ocean")  # already queued
        self.sp.prefetch("a longer example sentence.")
        self.assertTrue(self.idle())
        self.assertEqual(sorted(c[0] for c in self.kokoro.calls), ["a longer example sentence.", "ocean"])
        self.kokoro.calls.clear()
        with mock.patch.object(self.sp, "_play_wav", return_value=True) as play:
            self.sp._speak("ocean", self.sp._token)
        play.assert_called_once()
        self.assertEqual(self.kokoro.calls, [])  # the click found it in the cache

    def test_click_during_a_prefetch_waits_for_it_instead_of_synthesizing_twice(self):
        self.kokoro.delay = 0.3
        self.sp.prefetch("ocean")
        self.assertTrue(wait_for(lambda: self.kokoro.calls))
        with mock.patch.object(self.sp, "_play_wav", return_value=True) as play:
            self.sp._speak("ocean", self.sp._token)
        play.assert_called_once()
        self.assertEqual(len(self.kokoro.calls), 1)

    def test_reset_drops_clips_not_yet_started(self):
        self.kokoro.delay = 0.3
        for word in ("one", "two", "three"):
            self.sp.prefetch(word)
        self.assertTrue(wait_for(lambda: self.kokoro.calls))
        self.sp.reset_prefetch()
        self.assertTrue(self.idle())
        self.assertEqual([c[0] for c in self.kokoro.calls], ["one"])

    def test_long_text_and_a_full_queue_are_not_prefetched(self):
        with mock.patch.object(threading, "Thread"):
            self.sp.prefetch("x" * (speaker_mod.PREFETCH_MAX_CHARS + 1))
            for i in range(speaker_mod.PREFETCH_MAX_QUEUED + 3):
                self.sp.prefetch(f"word{i}")
        self.assertEqual(len(self.sp._queue), speaker_mod.PREFETCH_MAX_QUEUED)

    def test_prefetch_without_a_model_does_nothing_and_keeps_no_thread(self):
        self.sp._kokoro = None
        self.sp.prefetch("ocean")
        self.assertTrue(self.idle())

    def test_prefetch_is_off_once_the_app_is_quitting(self):
        self.sp._on_quit()
        self.sp.prefetch("ocean")
        self.assertEqual(len(self.sp._queue), 0)
        self.assertEqual(self.kokoro.calls, [])


class FallbackVoiceTest(unittest.TestCase):
    def setUp(self):
        self.sp = make_speaker()
        for p in (mock.patch.object(speaker_mod, "HAS_KOKORO", False),
                  mock.patch.object(speaker_mod, "HAS_WINSOUND", False)):
            p.start()
            self.addCleanup(p.stop)

    def test_fallback_cli_voice_speaks_the_text(self):
        with mock.patch.object(speaker_mod, "HAS_WIN32_SAPI", False), \
                mock.patch.object(speaker_mod, "FALLBACK_TTS_BIN", "/usr/bin/espeak-ng"), \
                mock.patch.object(speaker_mod.subprocess, "Popen") as popen:
            self.sp._token = 2
            self.sp._speak("Hello there.", 2)
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/espeak-ng")
        self.assertIn("Hello there.", cmd)
        popen.return_value.wait.assert_called_once()

    def test_failed_kokoro_synthesis_falls_back_to_the_system_voice(self):
        self.sp._kokoro = mock.Mock()
        self.sp._kokoro.create.side_effect = RuntimeError("boom")
        with mock.patch.object(speaker_mod, "np", np, create=True), \
                mock.patch.object(speaker_mod, "HAS_WIN32_SAPI", False), \
                mock.patch.object(speaker_mod, "FALLBACK_TTS_BIN", "/usr/bin/espeak-ng"), \
                mock.patch.object(speaker_mod.subprocess, "Popen") as popen:
            self.sp._speak("Hello there.", self.sp._token)
        self.assertEqual(popen.call_args.args[0][0], "/usr/bin/espeak-ng")

    def _fake_sapi(self, done_after):
        voice = mock.Mock()
        polls = []

        def wait_until_done(ms):
            polls.append(ms)
            return len(polls) > done_after

        voice.WaitUntilDone.side_effect = wait_until_done
        com = mock.Mock()
        com.client.Dispatch.return_value = voice
        return voice, com

    def test_sapi_speaks_asynchronously_with_short_padding_and_escapes_xml(self):
        voice, com = self._fake_sapi(done_after=2)
        with mock.patch.object(speaker_mod, "HAS_WIN32_SAPI", True), \
                mock.patch.object(speaker_mod, "pythoncom", mock.Mock(), create=True), \
                mock.patch.object(speaker_mod, "win32com", com, create=True):
            self.sp._token = 1
            self.sp._speak("Tom & Jerry", 1)
        text, flags = voice.Speak.call_args.args
        self.assertEqual(flags, 1 | 8)  # async + XML
        self.assertIn("Tom &amp; Jerry", text)
        self.assertIn(f'<silence msec="{speaker_mod.SAPI_LEAD_IN_MS}"/>', text)
        self.assertEqual(voice.Speak.call_count, 1)  # not cut off

    def test_sapi_is_cut_off_by_the_next_click(self):
        voice, com = self._fake_sapi(done_after=100)

        def next_click(ms):
            self.sp._token += 1
            return False

        voice.WaitUntilDone.side_effect = next_click
        with mock.patch.object(speaker_mod, "HAS_WIN32_SAPI", True), \
                mock.patch.object(speaker_mod, "pythoncom", mock.Mock(), create=True), \
                mock.patch.object(speaker_mod, "win32com", com, create=True):
            self.sp._token = 1
            self.sp._speak("A long sentence to read.", 1)
        self.assertEqual(voice.Speak.call_args.args, ("", 1 | 2))  # async purge


if __name__ == "__main__":
    unittest.main()
