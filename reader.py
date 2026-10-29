# === READER: TOKENISING A PASTED TEXT AND DECIDING WHICH WORDS TO MARK (Qt-free) ===
# The Reader page shows a text you paste and marks words worth a look: words you saved, and words whose CEFR
# level (bundled word list, works offline) is at or above a level you pick. This module is the logic behind
# that: split the text into words with character offsets, map an inflected form to its base word, classify
# each word, and keep the last text between sessions. Base words come from cloze.py's inflection tables and
# spelling rules, not from WordNet: that data may be missing, and loading it must never stall the GUI.
import bisect
import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import cloze
import word_levels

# Marks, strongest first: a saved word wins over a word above your level.
SAVED = "saved"
ABOVE = "above"      # its CEFR level is at or above the level you picked
PLAIN = "plain"      # listed, but below that level (or no level picked)
UNLISTED = "unlisted"  # not in the bundled word list: rare words, names, typos. Never marked (see README)

MAX_CHARS = 300_000        # a longer paste is cut here; each word is a mark, and a novel would make the page sluggish
MAX_SAVED_PHRASE_WORDS = 4  # saved entries longer than this are sentences, not phrases to look for in the text
MAX_LOOKUP_WORDS = 30      # a selection longer than this is not a phrase lookup

# letters (any script), joined by one apostrophe or hyphen at a time: don't, mother-in-law's, well-known
_LETTERS = r"[^\W\d_]+"
WORD_RE = re.compile(rf"{_LETTERS}(?:['’ʼ]{_LETTERS}|[-‐‑]{_LETTERS})*")

_CONTRACTION_BASE = {"can't": "can", "won't": "will", "shan't": "shall", "ain't": "ain't"}
_CLITICS = ("'s", "'re", "'ve", "'ll", "'d", "'m")


@dataclass(frozen=True)
class Token:
    text: str   # as written
    start: int  # offsets into the text; text[start:end] == token text
    end: int
    base: str   # lowercase with the contraction or possessive taken off: "Don't" -> "do", "John's" -> "john"


@dataclass(frozen=True)
class Marked:
    token: Token
    lemma: str  # the base word to look up: "ran" -> "run" (the base itself when nothing better is known)
    level: str  # CEFR level, '' when not listed
    mark: str   # SAVED, ABOVE, PLAIN or UNLISTED


def normalise_text(text: str) -> str:
    """Line breaks unified to \\n and control characters dropped, so offsets mean the same in the view."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
    return "".join(ch for ch in text if ch in "\n\t" or ch >= " " and ch != "\x7f")


def clip_text(text: str) -> Tuple[str, bool]:
    """(text cut to MAX_CHARS at a word boundary, whether it was cut)."""
    if len(text) <= MAX_CHARS:
        return text, False
    cut = text[:MAX_CHARS]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    return (cut[:space] if space > MAX_CHARS // 2 else cut).rstrip(), True


def base_form(word: str) -> str:
    """Lowercase `word` without its contraction or possessive ending: don't -> do, can't -> can, it's -> it."""
    w = word.lower().replace("’", "'").replace("ʼ", "'")
    if w in _CONTRACTION_BASE:
        return _CONTRACTION_BASE[w]
    if w.endswith("n't") and len(w) > 4:
        return w[:-3]
    for clitic in _CLITICS:
        if w.endswith(clitic) and len(w) > len(clitic):
            return w[: -len(clitic)]
    return w


def tokenise(text: str) -> List[Token]:
    """Every word of `text` with its character offsets, in order. Digits and punctuation are not words."""
    return [Token(m.group(), m.start(), m.end(), base_form(m.group())) for m in WORD_RE.finditer(text)]


# (ending, replacement) pairs that undo a regular inflection; every candidate is checked against cloze.word_forms
_UNDO_RULES = (
    ("ies", "y"), ("ied", "y"), ("ying", "ie"), ("ves", "f"), ("ves", "fe"),
    ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
)


def lemma_candidates(word: str) -> List[str]:
    """Possible base words of an inflected `word`, most reliable first: irregular forms from cloze's table
    ("ran" -> run), then the spelling rules undone ("stopped" -> stop, "studies" -> study). Each candidate
    really inflects to `word`, but a candidate may still be a non-word ("mak" for "making"): the caller
    keeps only those that are in a word list or a saved entry."""
    w = word.lower()
    found: List[str] = list(cloze._LEMMAS_OF.get(w, ()))
    if w.isalpha() and len(w) >= 3:
        for ending, repl in _UNDO_RULES:
            if w.endswith(ending) and len(w) > len(ending):
                stem = w[: -len(ending)] + repl
                found.append(stem)
                if len(stem) > 2 and stem[-1] == stem[-2]:
                    found.append(stem[:-1])  # stopped -> stopp -> stop
    out: List[str] = []
    for cand in found:
        if cand != w and cand not in out and w in cloze.word_forms(cand):
            out.append(cand)
    return out


class SavedWords:
    """The saved entries, ready to be matched against a text. A single saved word matches every form of
    itself, whichever form was saved (saved "run" marks ran/running; saved "ran" marks run too), ignoring
    part of speech. A saved phrase of 2-4 words matches the phrase in any inflection ("broke the ice")."""

    def __init__(self, keys: Iterable[str] = ()) -> None:
        self.forms: Dict[str, str] = {}   # inflected form -> the saved key it belongs to
        self.phrases: Dict[str, "re.Pattern[str]"] = {}
        for key in keys:
            key = " ".join(key.lower().split())
            words = WORD_RE.findall(key)
            if not words or len(words) > MAX_SAVED_PHRASE_WORDS:
                continue
            if len(words) == 1 and words[0] == key:
                for form in cloze.word_forms(key):
                    self.forms.setdefault(form, key)
            else:
                pattern = cloze.term_pattern(key)
                if pattern is not None:
                    self.phrases[key] = pattern

    def key_of(self, word: str) -> Optional[str]:
        return self.forms.get(word)

    def phrase_spans(self, text: str) -> List[Tuple[int, int]]:
        if not self.phrases:
            return []
        flat = text.replace("’", "'").replace("ʼ", "'")  # same length, so spans stay valid
        return [m.span() for pattern in self.phrases.values() for m in pattern.finditer(flat)]

    def __bool__(self) -> bool:
        return bool(self.forms or self.phrases)


LevelOf = Callable[[str, Sequence[str]], Optional[str]]   # (word, base-word candidates) -> level or None


def level_rank(level: str) -> int:
    return word_levels.LEVELS.index(level) if level in word_levels.LEVELS else -1


def _level_of_compound(base: str, level_of: LevelOf) -> Optional[str]:
    """Level of a hyphenated word: its own entry if listed, else its highest-level listed part
    ("well-known" is as hard as its hardest part)."""
    own = level_of(base, ())
    if own or "-" not in base and "‐" not in base and "‑" not in base:
        return own
    parts = [level_of(p, lemma_candidates(p)) for p in re.split("[-‐‑]", base) if p]
    listed = [p for p in parts if p]
    return max(listed, key=level_rank) if listed else None


def analyse(
    text: str,
    saved: SavedWords,
    level_of: LevelOf,
    min_level: Optional[str] = None,
) -> List[Marked]:
    """Tokenise `text` and decide the mark of every word.

    - SAVED: the word is a form of a saved word, or lies inside a saved phrase.
    - ABOVE: not saved, and its CEFR level is `min_level` or higher (`min_level` None = never).
    - PLAIN: listed, below `min_level`.
    - UNLISTED: not in the list at all (names, rare words, typos).
    `level_of(word, candidates)` gives a word's level, trying the word first and then its base-word
    candidates (word_levels.cefr_level does exactly that). Each distinct word is worked out once."""
    tokens = tokenise(text)
    threshold = level_rank(min_level) if min_level else None
    known: Dict[str, Tuple[str, str]] = {}   # base -> (lemma, level)

    def resolve(base: str) -> Tuple[str, str]:
        if base not in known:
            level = _level_of_compound(base, level_of) or ""
            lemma = base
            if not level:
                # An unlisted form takes the first of its base-word candidates that is listed or saved
                # ("ran" -> run). A listed word stays as written: "its" must not become "it".
                for cand in lemma_candidates(base):
                    cand_level = level_of(cand, ())
                    if cand_level or saved.key_of(cand):
                        lemma, level = cand, cand_level or ""
                        break
            known[base] = (lemma, level)
        return known[base]

    in_phrase = [False] * len(tokens)
    starts = [t.start for t in tokens]
    for s, e in saved.phrase_spans(text):
        i = bisect.bisect_left(starts, s)
        while i < len(tokens) and tokens[i].end <= e:
            in_phrase[i] = True
            i += 1

    out: List[Marked] = []
    for i, tok in enumerate(tokens):
        lemma, level = resolve(tok.base)
        key = saved.key_of(tok.base)
        if key is not None:
            lemma = key
        if in_phrase[i] or key is not None:
            mark = SAVED
        elif threshold is not None and level and level_rank(level) >= threshold:
            mark = ABOVE
        else:
            mark = PLAIN if level else UNLISTED
        out.append(Marked(tok, lemma, level, mark))
    return out


def count_marks(marked: Iterable[Marked]) -> Dict[str, int]:
    counts = {SAVED: 0, ABOVE: 0, PLAIN: 0, UNLISTED: 0}
    for m in marked:
        counts[m.mark] += 1
    return counts


def lookup_term(marked: Sequence[Marked], first: int, last: Optional[int] = None) -> str:
    """What to look up for words `first`..`last` (inclusive) of the analysed text: a single word is looked
    up as its base word ("ran" -> run); several words as the phrase as written, joined by single spaces
    ("break the ice"; the base-word endings are kept: "John's" stays)."""
    last = first if last is None else last
    if first == last:
        return marked[first].lemma
    return " ".join(m.token.text for m in marked[first:last + 1])


def token_at(marked: Sequence[Marked], offset: int) -> Optional[int]:
    """Index of the word whose characters include the character at `offset`, or None between words."""
    i = bisect.bisect_right([m.token.start for m in marked], offset) - 1
    if i >= 0 and marked[i].token.start <= offset < marked[i].token.end:
        return i
    return None


def tokens_in(marked: Sequence[Marked], start: int, end: int) -> Optional[Tuple[int, int]]:
    """(first, last) indexes of the words that overlap the selection start..end, None if it holds no word."""
    starts = [m.token.start for m in marked]
    first = bisect.bisect_right(starts, start) - 1
    if first < 0 or marked[first].token.end <= start:
        first += 1
    last = bisect.bisect_left(starts, end) - 1
    if first > last or first >= len(marked) or last < 0:
        return None
    return first, last


class Utf16Map:
    """Qt text positions count UTF-16 units, Python offsets count characters; they differ after any
    character outside the Basic Multilingual Plane (emoji). Identity for text without such characters."""

    def __init__(self, text: str) -> None:
        astral = [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]
        self._chars = astral                                 # character offsets of the wide characters
        self._units = [c + n for n, c in enumerate(astral)]  # their UTF-16 offsets

    def to_units(self, offset: int) -> int:
        return offset + bisect.bisect_left(self._chars, offset)

    def to_chars(self, units: int) -> int:
        return units - bisect.bisect_left(self._units, units)


# --- the last text, kept in the user data folder ---

def load_state(path: str) -> Tuple[str, int]:
    """(text, character offset of the first visible line) saved by save_state; ('', 0) if none or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        text = data.get("text", "")
        top = data.get("top", 0)
        if isinstance(text, str):
            text = clip_text(normalise_text(text))[0]
            return text, top if isinstance(top, int) and 0 <= top <= len(text) else 0
    except (OSError, ValueError, AttributeError):
        pass
    return "", 0


def save_state(path: str, text: str, top: int = 0) -> bool:
    """Write the text and reading position (via a temporary file, so a crash never leaves half a file).
    An empty text removes the file. Returns False when the file could not be written."""
    try:
        if not text:
            if os.path.exists(path):
                os.remove(path)
            return True
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"text": text, "top": top}, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
