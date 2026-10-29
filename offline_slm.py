"""Offline English -> Persian translation with a small local language model (GGUF, run by llama-cpp-python).

Used for the looked-up sentence, idiom or proverb when the local corpora don't contain it. Optional: without
llama-cpp-python or a model file in offline_slm_model/ every call returns "" and the caller uses the NLLB
translator. The setup scripts install Qwen2.5-1.5B-Instruct Q4_K_M (~1.1 GB file) only when asked
(--with-slm): measured against NLLB it was slower and worse (see the README), so it is not on by default.
"""
import glob
import logging
import os
import re
import threading
from typing import Optional

log = logging.getLogger("ocean.slm")

SYSTEM_PROMPT = (
    "You are an expert English-Persian translator. Translate the given English text into natural, idiomatic "
    "Persian. Avoid literal word-by-word translation. If it is an idiom or slang, provide the culturally "
    "equivalent Persian proverb or phrasing."
)
MAX_INPUT_CHARS = 600


class OfflineSLM:
    def __init__(self, model_dir: str) -> None:
        self.model_dir = model_dir
        self._llm = None
        self._unavailable = False
        self._load_lock = threading.Lock()
        self._run_lock = threading.Lock()  # one llama context: generations take turns

    def model_path(self) -> Optional[str]:
        files = sorted(glob.glob(os.path.join(self.model_dir, "*.gguf")))
        return files[0] if files else None

    def _load(self):
        with self._load_lock:
            if self._llm is not None or self._unavailable:
                return self._llm
            path = self.model_path()
            if not path:
                self._unavailable = True
                log.info("No GGUF model in %s: idioms and sentences use the NLLB translator offline", self.model_dir)
                return None
            try:
                from llama_cpp import Llama
                self._llm = Llama(model_path=path, n_ctx=1024, n_threads=max(1, (os.cpu_count() or 2) - 1), verbose=False)
                log.info("Loaded offline language model %s", os.path.basename(path))
            except Exception:
                self._unavailable = True
                log.exception("Could not load the offline language model %s", path)
        return self._llm

    def available(self) -> bool:
        return self._load() is not None

    def translate(self, text: str) -> str:
        clean = " ".join(text.split())
        if not clean or len(clean) > MAX_INPUT_CHARS:
            return ""
        llm = self._load()
        if llm is None:
            return ""
        try:
            with self._run_lock:
                out = llm.create_chat_completion(
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": clean}],
                    temperature=0.2,
                    max_tokens=min(512, 32 + 4 * len(clean.split())),
                )
            reply = out["choices"][0]["message"]["content"] or ""
        except Exception:
            log.exception("Offline language model failed for %r", clean)
            return ""
        return clean_reply(reply)


_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)  # reasoning models (Qwen3...) think aloud first
_PERSIAN = re.compile(r"[\u0600-\u06FF]")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def clean_reply(reply: str) -> str:
    """The translation itself: the first non-empty line, without surrounding quotes or a 'Translation:' label.
    "" when that line is not Persian (an explanation in English, stray Chinese), so the caller falls back."""
    for line in _THINK.sub("", reply).splitlines():
        line = line.strip().strip("\"'\u00ab\u00bb\u201c\u201d*").strip()
        if ":" in line[:20] and not _PERSIAN.search(line.split(":", 1)[0]):
            line = line.split(":", 1)[1].strip().strip("\"'\u00ab\u00bb\u201c\u201d*").strip()
        if line:
            return line if _PERSIAN.search(line) and not _CJK.search(line) else ""
    return ""
