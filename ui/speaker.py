"""Text-to-speech: the Kokoro neural voice, with Windows SAPI / Linux CLI fallbacks."""
import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import wave
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

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

# Silence added around the Kokoro samples; the lead-in is how long after playback starts speech begins.
LEAD_IN_SECS = 0.12
TAIL_SECS = 0.45


class NeuralSpeaker(QObject):
    """Speaks text. Signals (emitted from the playback thread; Qt queues them to GUI-thread receivers):

    clip_started(token, lead_in, duration): playback of the clip `speak()` returned `token` for has
        begun; speech starts `lead_in` s in and lasts `duration` s. Fallback voices (SAPI / CLI) give
        no length, so they report duration 0: play without word timing.
    playback_stopped(token): that clip ended or was cut off by stop() / the next speak().
    """
    clip_started = pyqtSignal(int, float, float)
    playback_stopped = pyqtSignal(int)

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self.settings = settings
        self._kokoro: Optional[Any] = None
        self._player: Optional[subprocess.Popen] = None
        # Id of the newest clip; a worker whose id is stale was superseded and must stay silent.
        self._token = 0
        self._play_lock = threading.Lock()
        # Not a daemon: quitting while onnxruntime is mid-load (~1s after start) crashes the process,
        # so let interpreter shutdown wait for the load to finish.
        threading.Thread(target=self._init_kokoro).start()

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

    def speak(self, text: str) -> int:
        """Start speaking `text`, cutting off any clip still playing. Returns the clip's token (0 if empty)."""
        clean = text.strip()
        if not clean:
            return 0
        self._cut()
        token = self._token
        threading.Thread(target=self._speak_worker, args=(clean, token), daemon=True).start()
        return token

    def stop(self) -> None:
        self._cut()

    def _cut(self) -> None:
        """Supersede the current clip: silence it and tell listeners it is over."""
        previous = self._token
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
        if previous:
            self.playback_stopped.emit(previous)

    def _speak_worker(self, text: str, token: int) -> None:
        try:
            self._speak(text, token)
        finally:
            if token == self._token:
                self.playback_stopped.emit(token)

    def _speak(self, text: str, token: int) -> None:
        voice_choice = self.settings.data.get("voice_id", VOICE_OPTIONS[0])
        rate_offset = int(self.settings.data.get("speech_rate", 0))
        speed = max(0.75, min(1.3, 1.0 + (rate_offset * 0.08)))

        if self._kokoro is None and HAS_KOKORO:
            self._init_kokoro()

        if self._kokoro is not None:
            voice_code = voice_choice.split()[0].strip()
            try:
                samples, sample_rate = self._kokoro.create(
                    text, voice=voice_code, speed=speed, lang="en-us"
                )
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

                def announce() -> None:
                    self.clip_started.emit(token, LEAD_IN_SECS, len(samples) / sample_rate)

                if self._play_wav(buf.getvalue(), token, announce):
                    return
                log.warning("No WAV player found (pw-play/paplay/aplay); using fallback voice")
            except Exception:
                log.exception("Kokoro speech failed")

        if token != self._token:
            return
        if HAS_WIN32_SAPI:
            self.clip_started.emit(token, 0.0, 0.0)
            try:
                pythoncom.CoInitialize()
                v = win32com.client.Dispatch("SAPI.SpVoice")
                v.Rate = rate_offset
                escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                v.Speak(f'<silence msec="120"/>{escaped}<silence msec="450"/>', 8)
            except Exception:
                log.exception("Windows SAPI speech failed")
        elif FALLBACK_TTS_BIN:
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
                self.clip_started.emit(token, 0.0, 0.0)
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                log.exception("Fallback speech (%s) failed", FALLBACK_TTS_BIN)

    def _play_wav(self, wav_bytes: bytes, token: int, on_start: Callable[[], None]) -> bool:
        """Play the clip, calling `on_start` as playback begins. False if there is no player to use."""
        if HAS_WINSOUND:
            if token != self._token:
                return True
            on_start()
            winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
            return True
        if WAV_PLAYER_CMD is None:
            return False
        with self._play_lock:
            if token != self._token:
                return True  # superseded while synthesizing: stay silent
            proc = subprocess.Popen(WAV_PLAYER_CMD, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._player = proc
        on_start()
        try:
            proc.communicate(wav_bytes, timeout=120)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            proc.kill()
        return True
