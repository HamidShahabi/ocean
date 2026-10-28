"""Background QThread workers, and the helpers that keep them alive until they finish."""
import logging
import os
import sys
from typing import List

from PyQt6.QtCore import QDeadlineTimer, QThread, pyqtSignal

import lexical_engine

log = logging.getLogger("ocean.app")


_RUNNING_WORKERS: set = set()


def start_worker(worker: QThread) -> None:
    """Start a worker and keep it referenced until it finishes: a running QThread whose
    Python wrapper gets garbage-collected (e.g. replaced by a newer search) aborts the process."""
    _RUNNING_WORKERS.add(worker)
    worker.finished.connect(lambda: _RUNNING_WORKERS.discard(worker))
    worker.finished.connect(worker.deleteLater)
    worker.start()


def exit_app(exit_code: int, grace_ms: int = 2000) -> None:
    """Exit after the event loop ends. Workers still busy (e.g. a slow lookup) would crash
    Python's teardown, which destroys their running QThreads; workers only read, and every
    save happens synchronously on the GUI thread, so skipping the teardown loses nothing."""
    deadline = QDeadlineTimer(grace_ms)
    for worker in list(_RUNNING_WORKERS):
        worker.wait(deadline)
    if any(w.isRunning() for w in _RUNNING_WORKERS):
        log.info("Workers still running at exit; skipping interpreter teardown")
        logging.shutdown()
        os._exit(exit_code)
    sys.exit(exit_code)


class SuggestionWorker(QThread):
    suggestions_ready = pyqtSignal(list)

    def __init__(self, prefix: str, local_history: List[str]) -> None:
        super().__init__()
        self.prefix = prefix.strip()
        self.local_history = local_history

    def run(self) -> None:
        q = self.prefix.lower()
        if len(q) < 2:
            return
        local_matches = [w for w in self.local_history if w.lower().startswith(q)]
        remote_matches = lexical_engine.fetch_live_suggestions(self.prefix)
        combined = list(dict.fromkeys(local_matches + remote_matches))[:6]
        self.suggestions_ready.emit(combined)


class EngineWorker(QThread):
    finished_success = pyqtSignal(str, dict)
    finished_error = pyqtSignal(str)

    def __init__(self, term: str, deep_mode: bool = False) -> None:
        super().__init__()
        self.term = term
        self.deep_mode = deep_mode

    def run(self) -> None:
        try:
            res = lexical_engine.analyze_with_dictionaries(self.term, deep_mode=self.deep_mode)
            self.finished_success.emit(res.get("headword", self.term), res)
        except Exception as exc:
            log.exception("Lookup failed for %r", self.term)
            self.finished_error.emit(str(exc))


class AutoFixWorker(QThread):
    fixed_ready = pyqtSignal(str)

    def __init__(self, en_source: str) -> None:
        super().__init__()
        self.en_source = en_source

    def run(self) -> None:
        res = lexical_engine.auto_fix_translation_deep(self.en_source)
        self.fixed_ready.emit(res)
