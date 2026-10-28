"""Word timing for read-aloud highlighting (Qt-free).

Kokoro returns audio samples but no word timestamps. We know the exact length of the
synthesized clip, so each word's share of it is estimated: proportional to its length in
characters (a cheap stand-in for phoneme count), plus a pause after punctuation. The
result is scaled so the last word ends exactly when the audio does, which keeps the
highlight roughly in sync with no per-word synthesis.
"""
import re
from typing import List, NamedTuple, Optional, Tuple

# A word: letters/digits, with internal apostrophes or hyphens kept ("don't", "well-known").
WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*")
# Tags that ResultsPage._highlight_phrase puts into the example label.
_TAG_RE = re.compile(r"<b style='[^']*'>|</b>")

# Every word costs its length in characters plus this, so short words ("a", "I") still take time.
WORD_BASE = 1.5
# Pause after a word, in the same character units, by the strongest punctuation that follows it.
PAUSE_SENTENCE = 5.0   # . ! ? ...
PAUSE_CLAUSE = 3.0     # ; : and dashes
PAUSE_COMMA = 2.0      # ,
_SENTENCE_MARKS = ".!?…"
_CLAUSE_MARKS = ";:—–"


class WordTiming(NamedTuple):
    """One word: its [start_char, end_char) span in the text and its [start, end) time in seconds.

    A word stays "current" through the pause after it, so spans tile the clip with no gaps.
    """
    start_char: int
    end_char: int
    start: float
    end: float


def _pause_weight(gap: str) -> float:
    if any(ch in _SENTENCE_MARKS for ch in gap):
        return PAUSE_SENTENCE
    if any(ch in _CLAUSE_MARKS for ch in gap):
        return PAUSE_CLAUSE
    if "," in gap:
        return PAUSE_COMMA
    return 0.0


def estimate_word_timings(text: str, duration: float) -> List[WordTiming]:
    """Estimate when each word of `text` is spoken in a clip of `duration` seconds of speech.

    Times are relative to the start of the speech itself (leading silence excluded). Returns
    an empty list when there is nothing to time (no words, or a non-positive duration).
    """
    words = list(WORD_RE.finditer(text))
    if not words or duration <= 0:
        return []
    weights = []
    for i, m in enumerate(words):
        weight = len(m.group()) + WORD_BASE
        # Punctuation up to the next word; after the last word it is the clip's end, not a pause.
        if i + 1 < len(words):
            weight += _pause_weight(text[m.end():words[i + 1].start()])
        weights.append(weight)
    scale = duration / sum(weights)
    timings = []
    t = 0.0
    for m, weight in zip(words, weights):
        end = t + weight * scale
        timings.append(WordTiming(m.start(), m.end(), t, end))
        t = end
    # Pin the last end to the duration, whatever float rounding did along the way.
    timings[-1] = timings[-1]._replace(end=duration)
    return timings


def word_at(timings: List[WordTiming], elapsed: float) -> Optional[int]:
    """Index of the word being spoken `elapsed` seconds into the speech, or None outside it."""
    if not timings or elapsed < 0 or elapsed >= timings[-1].end:
        return None
    lo, hi = 0, len(timings) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if timings[mid].end <= elapsed:
            lo = mid + 1
        else:
            hi = mid
    return lo


def highlight_word_html(html: str, start_char: int, end_char: int, style: str) -> str:
    """Wrap characters [start_char, end_char) of the label's text in a styled span.

    `html` is the example sentence as ResultsPage renders it: the raw sentence with only
    `<b style='..'>` / `</b>` tags added around the searched phrase, so the character offsets
    of `estimate_word_timings` (computed on the raw sentence) apply to its non-tag characters.
    The span is closed and reopened around any tag it crosses so nesting stays valid.
    """
    out: List[str] = []
    span_open = f"<span style='{style}'>"
    in_span = False
    pos = 0   # characters of text seen so far (tags excluded)
    i = 0
    while i < len(html):
        tag = _TAG_RE.match(html, i)
        if tag:
            if in_span and pos == end_char:
                out.append("</span>")
                in_span = False
            if in_span:
                out.append("</span>" + tag.group() + span_open)
            else:
                out.append(tag.group())
            i = tag.end()
            continue
        if pos == start_char:
            out.append(span_open)
            in_span = True
        if pos == end_char and in_span:
            out.append("</span>")
            in_span = False
        out.append(html[i])
        pos += 1
        i += 1
    if in_span:
        out.append("</span>")
    return "".join(out)
