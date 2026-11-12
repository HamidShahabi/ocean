"""Background QThread workers, and the helpers that keep them alive until they finish."""
import inspect
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
    """One lookup. `refresh=True` (the Refresh button) asks the engine for an improved result by querying the
    online resources again; the first lookup of a term is always the best local result."""
    preview_ready = pyqtSignal(str, dict)  # the entry from local data, before the slower translations
    finished_success = pyqtSignal(str, dict)
    finished_error = pyqtSignal(str)

    def __init__(self, term: str, refresh: bool = False) -> None:
        super().__init__()
        self.term = term
        self.refresh = refresh

    def run(self) -> None:
        try:
            kwargs = {}
            # The engine's refresh option (online sources, bypassing its caches) is passed once it has one;
            # until then a refresh is simply a fresh lookup that skips the saved copy.
            if self.refresh and "refresh" in inspect.signature(lexical_engine.analyze_with_dictionaries).parameters:
                kwargs["refresh"] = True
            res = lexical_engine.analyze_with_dictionaries(
                self.term, on_preview=lambda d: self.preview_ready.emit(d.get("headword", self.term), d), **kwargs)
            self.finished_success.emit(res.get("headword", self.term), res)
        except Exception as exc:
            log.exception("Lookup failed for %r", self.term)
            self.finished_error.emit(str(exc))
