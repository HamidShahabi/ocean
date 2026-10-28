import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexical_engine as le  # noqa: E402


def make_model_dir() -> str:
    d = tempfile.mkdtemp()
    open(os.path.join(d, "sentencepiece.model"), "wb").close()
    os.mkdir(os.path.join(d, "model"))
    with open(os.path.join(d, "model", "shared_vocabulary.txt"), "w", encoding="utf-8") as f:
        f.write("eng_Latn\npes_Arab\n")
    return d


class OfflinePreloadTest(unittest.TestCase):
    def setUp(self):
        self.ct2 = mock.MagicMock()
        self.spm = mock.MagicMock()
        self.spm.SentencePieceProcessor.return_value.encode.return_value = ["a"]
        self.spm.SentencePieceProcessor.return_value.decode.return_value = "salam"
        hyp = mock.MagicMock(hypotheses=[["pes_Arab", "x"]])
        self.ct2.Translator.return_value.translate_batch.return_value = [hyp]
        patches = [
            mock.patch.object(le, "ctranslate2", self.ct2, create=True),
            mock.patch.object(le, "spm", self.spm, create=True),
            mock.patch.object(le, "HAS_CT2_BACKUP", True),
            mock.patch.object(le, "OFFLINE_MT_DIR", make_model_dir()),
            mock.patch.object(le, "_CT2_TRANSLATOR", None),
            mock.patch.object(le, "_SP_PROCESSOR", None),
            mock.patch.object(le, "_CT2_IS_NLLB", False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_preload_loads_model_so_lookup_does_not(self):
        le.preload_offline_translator().join(5)
        self.assertEqual(self.ct2.Translator.call_count, 1)
        self.assertTrue(le._CT2_IS_NLLB)
        self.assertEqual(le._ct2_translate("hello"), "salam")
        self.assertEqual(self.ct2.Translator.call_count, 1)

    def test_preload_thread_is_not_a_daemon(self):
        # A daemon thread killed mid-load crashes the process on quit.
        thread = le.preload_offline_translator()
        thread.join(5)
        self.assertFalse(thread.daemon)

    def test_lookup_during_load_waits_and_loads_once(self):
        started, release = threading.Event(), threading.Event()

        def slow_translator(*args, **kwargs):
            started.set()
            release.wait(5)
            return mock.DEFAULT

        self.ct2.Translator.side_effect = slow_translator
        thread = le.preload_offline_translator()
        self.assertTrue(started.wait(5))
        result = []
        lookup = threading.Thread(target=lambda: result.append(le._ct2_translate("hello")))
        lookup.start()
        lookup.join(0.3)
        self.assertTrue(lookup.is_alive())  # blocked on the load in progress
        release.set()
        lookup.join(5)
        thread.join(5)
        self.assertEqual(result, ["salam"])
        self.assertEqual(self.ct2.Translator.call_count, 1)

    def test_missing_model_is_logged_not_an_error(self):
        with mock.patch.object(le, "OFFLINE_MT_DIR", tempfile.mkdtemp()):
            with self.assertLogs(le.log, level="INFO") as logs:
                le.preload_offline_translator().join(5)
            self.assertFalse(any(r.levelname in ("ERROR", "CRITICAL") for r in logs.records))
            self.assertIn("not found", logs.output[0])
            self.assertEqual(le._ct2_translate("hello"), "")
        self.ct2.Translator.assert_not_called()

    def test_without_ctranslate2_preload_is_a_no_op(self):
        with mock.patch.object(le, "HAS_CT2_BACKUP", False):
            le.preload_offline_translator().join(5)
        self.ct2.Translator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
