import os
import sys
import tempfile
import unittest
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

    def create(self, text, voice, speed, lang):
        return np.zeros(2 * RATE, dtype=np.float32), RATE


def make_speaker():
    with mock.patch.object(speaker_mod.NeuralSpeaker, "_init_kokoro"):
        sp = speaker_mod.NeuralSpeaker(SettingsManager())
    return sp


class SpeakerSignalsTest(unittest.TestCase):
    def setUp(self):
        self.sp = make_speaker()
        self.started, self.stopped = [], []
        self.sp.clip_started.connect(lambda *a: self.started.append(a))
        self.sp.playback_stopped.connect(lambda t: self.stopped.append(t))

    def test_kokoro_clip_reports_lead_in_and_exact_duration(self):
        self.sp._kokoro = FakeKokoro()
        played = []
        with mock.patch.object(speaker_mod, "np", np, create=True), \
                mock.patch.object(speaker_mod, "HAS_KOKORO", True), \
                mock.patch.object(speaker_mod, "HAS_WINSOUND", False), \
                mock.patch.object(speaker_mod, "WAV_PLAYER_CMD", ["fake-player"]), \
                mock.patch.object(speaker_mod.subprocess, "Popen") as popen:
            popen.return_value.communicate.side_effect = lambda data, timeout: played.append(len(data))
            self.sp._token = 5
            self.sp._speak_worker("Hello there.", 5)
        self.assertEqual(self.started, [(5, speaker_mod.LEAD_IN_SECS, 2.0)])
        self.assertEqual(self.stopped, [5])
        self.assertTrue(played and played[0] > 2 * RATE * 2)  # 16-bit WAV with padding

    def test_speak_returns_increasing_tokens_and_ignores_blank_text(self):
        with mock.patch.object(self.sp, "_speak_worker"):
            first = self.sp.speak("one")
            second = self.sp.speak("two")
            self.assertEqual(self.sp.speak("   "), 0)
        self.assertGreater(second, first)
        # Starting the second clip ended the first.
        self.assertEqual(self.stopped, [first])

    def test_stop_reports_the_current_clip_and_silences_the_player(self):
        player = mock.Mock()
        player.poll.return_value = None
        self.sp._player = player
        self.sp._token = 3
        self.sp.stop()
        player.terminate.assert_called_once()
        self.assertEqual(self.stopped, [3])
        self.assertGreater(self.sp._token, 3)

    def test_superseded_clip_never_plays_or_announces(self):
        self.sp._kokoro = FakeKokoro()
        with mock.patch.object(speaker_mod, "np", np, create=True), \
                mock.patch.object(speaker_mod, "HAS_KOKORO", True), \
                mock.patch.object(speaker_mod, "HAS_WINSOUND", False), \
                mock.patch.object(speaker_mod, "WAV_PLAYER_CMD", ["fake-player"]), \
                mock.patch.object(speaker_mod.subprocess, "Popen") as popen:
            self.sp._token = 9  # a newer clip exists
            self.sp._speak_worker("old clip", 8)
        popen.assert_not_called()
        self.assertEqual(self.started, [])
        self.assertEqual(self.stopped, [])

    def test_fallback_cli_voice_plays_without_a_duration(self):
        with mock.patch.object(speaker_mod, "HAS_KOKORO", False), \
                mock.patch.object(speaker_mod, "HAS_WIN32_SAPI", False), \
                mock.patch.object(speaker_mod, "FALLBACK_TTS_BIN", "/usr/bin/espeak-ng"), \
                mock.patch.object(speaker_mod.subprocess, "Popen") as popen:
            self.sp._token = 2
            self.sp._speak_worker("Hello there.", 2)
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/espeak-ng")
        self.assertIn("Hello there.", cmd)
        popen.return_value.wait.assert_called_once()
        self.assertEqual(self.started, [(2, 0.0, 0.0)])
        self.assertEqual(self.stopped, [2])


class VoiceAvailabilityTest(unittest.TestCase):
    def check(self, kokoro=False, winsound=False, player=None, sapi=False, cli=None):
        sp = make_speaker()
        sp._kokoro = FakeKokoro() if kokoro else None
        with mock.patch.object(speaker_mod, "HAS_WINSOUND", winsound), \
                mock.patch.object(speaker_mod, "WAV_PLAYER_CMD", player), \
                mock.patch.object(speaker_mod, "HAS_WIN32_SAPI", sapi), \
                mock.patch.object(speaker_mod, "FALLBACK_TTS_BIN", cli):
            return sp.has_voice()

    def test_no_voice_without_kokoro_or_a_fallback(self):
        self.assertFalse(self.check())
        self.assertFalse(self.check(kokoro=True))  # Kokoro loaded but nothing to play the WAV with

    def test_loaded_kokoro_with_a_player_is_a_voice(self):
        self.assertTrue(self.check(kokoro=True, player=["paplay"]))
        self.assertTrue(self.check(kokoro=True, winsound=True))

    def test_system_voices_count(self):
        self.assertTrue(self.check(sapi=True))
        self.assertTrue(self.check(cli="/usr/bin/espeak-ng"))

    def test_loading_flag_clears_and_announces_when_the_model_load_ends(self):
        sp = make_speaker()
        loaded = []
        sp.voice_loaded.connect(lambda: loaded.append(sp.is_loading()))
        sp._load_kokoro()
        self.assertFalse(sp.is_loading())
        self.assertEqual(loaded, [False])  # listeners see the final state


if __name__ == "__main__":
    unittest.main()
