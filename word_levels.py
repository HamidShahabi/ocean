"""Word level (CEFR) and frequency band lookups. Qt-free and offline: the level list is the bundled
cefr_levels.json (CEFR-J Wordlist A1-B2 + Octanove C1/C2, see its _meta for citations and licences),
read the first time a level is asked for."""
import json
import threading
from typing import Dict, Iterable, Optional

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

# Datamuse gives a word's frequency as occurrences per million words (the "f:" tag, md=f).
# Band floors, highest first; below the last floor a word is "Rare".
FREQUENCY_BANDS = (
    (100.0, "Very common"),
    (10.0, "Common"),
    (1.0, "Uncommon"),
)
RARE_BAND = "Rare"

_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, str]] = {}   # file path -> {word: level}; loaded on first use


def _load(path: str) -> Dict[str, str]:
    with _LOCK:
        if path not in _CACHE:
            levels: Dict[str, str] = {}
            try:
                with open(path, encoding="utf-8") as f:
                    words = json.load(f).get("words", {})
                for level in LEVELS:  # the file lists each word once; the lowest level wins anyway
                    for word in words.get(level, []):
                        levels.setdefault(word, level)
            except (OSError, ValueError):
                pass  # no list, no badge: never an error in a lookup
            _CACHE[path] = levels
        return _CACHE[path]


def cefr_level(path: str, term: str, lemmas: Iterable[str] = ()) -> Optional[str]:
    """CEFR level (A1-C2) of `term`, or of the first of `lemmas` that is listed when the term itself
    is not (an inflected form such as 'running' takes the level of 'run'); None when unknown."""
    levels = _load(path)
    for candidate in (term, *lemmas):
        level = levels.get(" ".join(candidate.lower().split()))
        if level:
            return level
    return None


def frequency_band(per_million: Optional[float]) -> str:
    """Band name for a Datamuse frequency (occurrences per million words); '' when unknown."""
    if per_million is None:
        return ""
    for floor, name in FREQUENCY_BANDS:
        if per_million >= floor:
            return name
    return RARE_BAND
