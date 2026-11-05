"""Text-to-speech: the Kokoro neural voice, with Windows SAPI / Linux CLI fallbacks.

Kokoro takes ~0.5-1.5 s per clip on a CPU, so clips are synthesized ahead of the click (prefetch(),
called for every speaker button) and kept in a small cache; a click on a cached clip only starts the player.
"""
import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections import OrderedDict, deque
from typing import Any, Optional

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

import kokoro_files
from ui.config import BASE_DIR, VOICE_OPTIONS, SettingsManager

try:
    import numpy as np
    from kokoro_onnx import Kokoro
    HAS_KOKORO = True
except Exception:
    HAS_KOKORO = False

try:
    import pythoncom
    import win32com.client
    HAS_WIN32_SAPI = True
except Exception:
    HAS_WIN32_SAPI = False

try:
    import winsound
    HAS_WINSOUND = True
except Exception:
    HAS_WINSOUND = False

# Linux/macOS playback: first available CLI player that accepts WAV on stdin.
WAV_PLAYER_CMD = next(
    (cmd for cmd in (["pw-play", "-"], ["paplay"], ["aplay", "-q", "-"], ["afplay", "-"]) if shutil.which(cmd[0])),
    None,
)
FALLBACK_TTS_BIN = shutil.which("spd-say") or shutil.which("espeak-ng") or shutil.which("espeak")

log = logging.getLogger("ocean.app")

# Silence added around the Kokoro samples so the audio device does not clip the first / last phoneme.
# Only the lead-in delays the sound; the tail just keeps the clip open until the device has drained.
LEAD_IN_SECS = 0.06
TAIL_SECS = 0.30
# SAPI takes the same padding as <silence> tags, in milliseconds.
SAPI_LEAD_IN_MS = 60
SAPI_TAIL_MS = 300

# Synthesized clips kept for replays, as WAV bytes (~100 KB for a sentence); oldest dropped first.
CACHE_MAX_BYTES = 24 * 1024 * 1024
# Prefetch only what a click is likely to need: a page's first few clips, and none of a long text.
PREFETCH_MAX_QUEUED = 8
PREFETCH_MAX_CHARS = 300

SAPI_SPEAK_ASYNC = 1
SAPI_SPEAK_PURGE = 2
SAPI_SPEAK_IS_XML = 8


class NeuralSpeaker(QObject):
    """Speaks text.

    voice_loaded(): the background load of the Kokoro model has finished (successfully or not); re-check
        has_voice().
    """
    voice_loaded = pyqtSignal()

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self.settings = settings
        self._kokoro: Optional[Any] = None
        self._player: Optional[subprocess.Popen] = None
        # Id of the newest clip; a worker whose id is stale was superseded and must stay silent.
        self._token = 0
        self._play_lock = threading.Lock()
        # Synthesized clips: (text, voice, speed) -> WAV bytes, least recently used first.
        self._cache: "OrderedDict[tuple[str, str, float], bytes]" = OrderedDict()
        self._cache_bytes = 0
        self._inflight: dict[tuple[str, str, float], threading.Event] = {}
        self._cache_lock = threading.Lock()
        # Kokoro's phonemizer (espeak) is not thread-safe, so one clip is synthesized at a time.
        self._synth_lock = threading.Lock()
        self._urgent = 0  # clicks waiting for a clip: the prefetch worker pauses meanwhile
        self._queue: deque[str] = deque()
        self._queue_lock = threading.Lock()
        self._prefetcher: Optional[threading.Thread] = None
        self._closing = False
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_quit)
        # Not a daemon: quitting while onnxruntime is mid-load (~1s after start) crashes the process,
        # so let interpreter shutdown wait for the load to finish.
        self._loading = True
        self._loaded = threading.Event()
        threading.Thread(target=self._load_kokoro).start()

    def _on_quit(self) -> None:
        self._closing = True
        with self._queue_lock:
            self._queue.clear()

    def _load_kokoro(self) -> None:
        try:
            self._init_kokoro()
        finally:
            self._loading = False
            self._loaded.set()
            try:
                self.voice_loaded.emit()
            except RuntimeError:  # the app quit while the model was loading
                pass

    def is_loading(self) -> bool:
        """True while the Kokoro model is still loading in the background (it takes a moment after start)."""
        return self._loading

    def has_voice(self) -> bool:
        """Whether speak() can make sound: Kokoro loaded with something to play it, or a fallback voice
        (Windows SAPI, spd-say / espeak). False while Kokoro is still loading and there is no fallback; check
        is_loading() and voice_loaded to tell "not yet" from "never"."""
        kokoro_plays = self._kokoro is not None and (HAS_WINSOUND or WAV_PLAYER_CMD is not None)
        return kokoro_plays or HAS_WIN32_SAPI or FALLBACK_TTS_BIN is not None

    def _find_model_files(self) -> tuple[str, str]:
        frozen = getattr(sys, "frozen", False)
        return kokoro_files.find_kokoro_files([
            BASE_DIR,
            os.path.dirname(sys.executable) if frozen else "",
            os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "..")) if frozen else "",
        ])

    def _init_kokoro(self) -> None:
        if not HAS_KOKORO:
            return
        onnx_p, voic_p = self._find_model_files()
        if onnx_p and voic_p:
            try:
                self._kokoro = Kokoro(onnx_p, voic_p)
            except Exception:
                log.exception("Could not load Kokoro voice model %s", onnx_p)
                self._kokoro = None
        else:
            log.info("Kokoro model files not found; using fallback voice")

    def _voice_and_speed(self) -> tuple[str, float]:
        """Kokoro voice code and speed from the settings (also the cache key of a clip)."""
        voice_choice = self.settings.data.get("voice_id", VOICE_OPTIONS[0])
        rate_offset = int(self.settings.data.get("speech_rate", 0))
        return voice_choice.split()[0].strip(), max(0.75, min(1.3, 1.0 + (rate_offset * 0.08)))

    # --- speaking ---

    def speak(self, text: str) -> None:
        """Start speaking `text`, cutting off any clip still playing."""
        clean = text.strip()
        if not clean:
            return
        token = self._cut()
        threading.Thread(target=self._speak, args=(clean, token), daemon=True).start()

    def stop(self) -> None:
        self._cut()

    def _cut(self) -> int:
        """Supersede the current clip and silence it. Returns the token of the next clip."""
        self._token += 1
        with self._play_lock:
            if HAS_WINSOUND:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except RuntimeError:
                    log.debug("winsound purge failed", exc_info=True)
            player = self._player
            if player is not None and player.poll() is None:
                player.terminate()
        return self._token

    def _speak(self, text: str, token: int) -> None:
        if not self._loaded.is_set() and HAS_KOKORO:
            self._loaded.wait(30)  # a click right after start-up: the neural voice is nearly ready
        if self._kokoro is not None:
            try:
                wav = self._wav_for(text, *self._voice_and_speed(), urgent=True)
                if token != self._token:
                    return  # superseded while synthesizing: stay silent
                if self._play_wav(wav, token):
                    return
                log.warning("No WAV player found (pw-play/paplay/aplay); using fallback voice")
            except Exception:
                log.exception("Kokoro speech failed")

        if token != self._token:
            return
        rate_offset = int(self.settings.data.get("speech_rate", 0))
        if HAS_WIN32_SAPI:
            self._speak_sapi(text, token, rate_offset)
        elif FALLBACK_TTS_BIN:
            self._speak_cli(text, token, rate_offset)

    def _speak_sapi(self, text: str, token: int, rate_offset: int) -> None:
        """Windows speech, started asynchronously and polled so the next click can cut it off."""
        try:
            pythoncom.CoInitialize()
            try:
                v = win32com.client.Dispatch("SAPI.SpVoice")
                v.Rate = rate_offset
                escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                v.Speak(f'<silence msec="{SAPI_LEAD_IN_MS}"/>{escaped}<silence msec="{SAPI_TAIL_MS}"/>',
                        SAPI_SPEAK_ASYNC | SAPI_SPEAK_IS_XML)
                while not v.WaitUntilDone(50):
                    if token != self._token:
                        v.Speak("", SAPI_SPEAK_ASYNC | SAPI_SPEAK_PURGE)
                        break
                del v
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            log.exception("Windows SAPI speech failed")

    def _speak_cli(self, text: str, token: int, rate_offset: int) -> None:
        # SAPI rate is -10..10; spd-say takes -100..100, espeak takes words-per-minute.
        if os.path.basename(FALLBACK_TTS_BIN) == "spd-say":
            cmd = [FALLBACK_TTS_BIN, "-w", "-r", str(rate_offset * 10), "--", text]
        else:
            cmd = [FALLBACK_TTS_BIN, "-s", str(175 + rate_offset * 15), "--", text]
        try:
            with self._play_lock:
                if token != self._token:
                    return
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._player = proc
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            log.exception("Fallback speech (%s) failed", FALLBACK_TTS_BIN)

    def _play_wav(self, wav_bytes: bytes, token: int) -> bool:
        """Play the clip from memory. False if there is no player to use."""
        if HAS_WINSOUND:
            # SND_MEMORY cannot be combined with SND_ASYNC (winsound refuses it); this runs on the clip's
            # own thread and _cut() purges it, so playing synchronously costs nothing.
            winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
            return True
        if WAV_PLAYER_CMD is None:
            return False
        with self._play_lock:
            if token != self._token:
                return True
            proc = subprocess.Popen(WAV_PLAYER_CMD, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._player = proc
        try:
            proc.communicate(wav_bytes, timeout=120)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            proc.kill()
        return True

    # --- synthesis, cache and prefetch ---

    def _wav_for(self, text: str, voice: str, speed: float, urgent: bool = False) -> bytes:
        """The padded WAV of `text`: from the cache, from a synthesis already running for it, or made now."""
        key = (text, voice, speed)
        if urgent:
            with self._cache_lock:
                self._urgent += 1
        try:
            while True:
                with self._cache_lock:
                    wav = self._cache.get(key)
                    if wav is not None:
                        self._cache.move_to_end(key)
                        return wav
                    pending = self._inflight.get(key)
                    if pending is None:
                        pending = self._inflight[key] = threading.Event()
                        break
                pending.wait()  # someone else (usually the prefetcher) is making it
            try:
                wav = self._synthesize(text, voice, speed)
                with self._cache_lock:
                    self._cache[key] = wav
                    self._cache_bytes += len(wav)
                    while self._cache_bytes > CACHE_MAX_BYTES and len(self._cache) > 1:
                        _, dropped = self._cache.popitem(last=False)
                        self._cache_bytes -= len(dropped)
                return wav
            finally:
                with self._cache_lock:
                    self._inflight.pop(key, None)
                pending.set()
        finally:
            if urgent:
                with self._cache_lock:
                    self._urgent -= 1

    def _synthesize(self, text: str, voice: str, speed: float) -> bytes:
        with self._synth_lock:
            samples, sample_rate = self._kokoro.create(text, voice=voice, speed=speed, lang="en-us")
        pad_start = np.zeros(int(sample_rate * LEAD_IN_SECS), dtype=np.float32)
        pad_end = np.zeros(int(sample_rate * TAIL_SECS), dtype=np.float32)
        full_audio = np.concatenate([pad_start, samples, pad_end])
        pcm16 = (np.clip(full_audio, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16.tobytes())
        return buf.getvalue()

    def prefetch(self, text: str) -> None:
        """Synthesize `text` in the background so a click on its speaker button starts at once.
        Call reset_prefetch() when a new page replaces the old one."""
        clean = text.strip()
        if not HAS_KOKORO or not clean or len(clean) > PREFETCH_MAX_CHARS or self._closing:
            return
        with self._queue_lock:
            if clean in self._queue or len(self._queue) >= PREFETCH_MAX_QUEUED:
                return
            self._queue.append(clean)
            if self._prefetcher is None or not self._prefetcher.is_alive():
                # Not a daemon, for the same reason as the model load; _on_quit() empties the queue.
                self._prefetcher = threading.Thread(target=self._prefetch_worker)
                self._prefetcher.start()

    def reset_prefetch(self) -> None:
        """Forget the clips still waiting to be prefetched (the page that wanted them is gone)."""
        with self._queue_lock:
            self._queue.clear()

    def _prefetch_worker(self) -> None:
        self._loaded.wait()
        while not self._closing:
            with self._queue_lock:
                if not self._queue:
                    self._prefetcher = None
                    return
                text = self._queue.popleft()
            if self._kokoro is None:
                self.reset_prefetch()
                continue
            while self._urgent and not self._closing:
                time.sleep(0.05)  # a click is waiting for its clip: let it go first
            try:
                self._wav_for(text, *self._voice_and_speed())
            except Exception:
                log.debug("Prefetching %r failed", text, exc_info=True)
