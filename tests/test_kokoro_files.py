import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kokoro_files import find_kokoro_files  # noqa: E402


def touch(folder, *names):
    for n in names:
        open(os.path.join(folder, n), "wb").close()


class FindKokoroFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.a = os.path.join(self._tmp.name, "a")
        self.b = os.path.join(self._tmp.name, "b")
        os.makedirs(self.a)
        os.makedirs(self.b)

    def test_prefers_int8_over_full(self):
        touch(self.a, "kokoro-v1.0.onnx", "kokoro-v1.0.int8.onnx", "voices-v1.0.bin")
        model, voices = find_kokoro_files([self.a])
        self.assertEqual(os.path.basename(model), "kokoro-v1.0.int8.onnx")
        self.assertEqual(os.path.basename(voices), "voices-v1.0.bin")

    def test_falls_back_to_full_model(self):
        touch(self.a, "kokoro-v1.0.onnx", "voices-v1.0.bin")
        model, _ = find_kokoro_files([self.a])
        self.assertEqual(os.path.basename(model), "kokoro-v1.0.onnx")

    def test_int8_alone_works(self):
        touch(self.a, "kokoro-v1.0.int8.onnx", "voices-v1.0.bin")
        model, _ = find_kokoro_files([self.a])
        self.assertEqual(os.path.basename(model), "kokoro-v1.0.int8.onnx")

    def test_needs_both_model_and_voices(self):
        touch(self.a, "kokoro-v1.0.int8.onnx")
        touch(self.b, "voices-v1.0.bin")
        self.assertEqual(find_kokoro_files([self.a, self.b]), ("", ""))

    def test_skips_blank_and_incomplete_folders(self):
        touch(self.a, "kokoro-v1.0.onnx")  # no voices file here
        touch(self.b, "kokoro-v1.0.onnx", "voices-v1.0.bin")
        model, voices = find_kokoro_files(["", self.a, self.b])
        self.assertEqual(os.path.dirname(model), self.b)
        self.assertEqual(os.path.dirname(voices), self.b)

    def test_nothing_found(self):
        self.assertEqual(find_kokoro_files([self.a, ""]), ("", ""))


if __name__ == "__main__":
    unittest.main()
