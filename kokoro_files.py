"""Locating the Kokoro voice model files (no Qt, so it can be unit-tested headless)."""
import os
from typing import List

# The int8 model is ~88 MB against ~310 MB for the full one; the full one is still used when it is all
# that is installed.
KOKORO_MODEL_NAMES = ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx")
KOKORO_VOICES_NAME = "voices-v1.0.bin"


def find_kokoro_files(dirs: List[str]) -> tuple[str, str]:
    """(model path, voices path) from the first folder holding a model and the voices file, int8 model first."""
    for d in dirs:
        if not d:
            continue
        voices = os.path.join(d, KOKORO_VOICES_NAME)
        if not os.path.exists(voices):
            continue
        for name in KOKORO_MODEL_NAMES:
            model = os.path.join(d, name)
            if os.path.exists(model):
                return model, voices
    return "", ""
