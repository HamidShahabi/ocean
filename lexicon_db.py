"""Read-only access to ocean_lexicon.db, Ocean's local copy of its datasets (built by tools/build_lexicon_db.py):

- Kaikki.org (Wiktionary): entries by part of speech with IPA, senses, examples, collocations and derived
  terms, sense-labelled Persian translations, and inflected forms pointing at their lemma.
- Urban Dictionary: slang definitions, filtered at import to well-voted entries.
- Tatoeba and OpenSubtitles: English-Persian sentence pairs (exact-match translations and example sentences,
  through an FTS5 index), plus word frequencies counted on the English subtitles.

Qt-free and free of lexical_engine, so the import tool can share the key functions. Every query returns an
empty result when the database is missing or broken: lookups then work from the other sources.
"""
import json
import logging
import os
import re
import sqlite3
import threading
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("ocean.lexicon")

_ARABIC_DIACRITICS = re.compile(r"[ً-ٰٟـ]")  # harakat, superscript alef, tatweel


def text_key(text: str) -> str:
    """Lookup key for English text: lower case, curly quotes straightened, punctuation other than inner
    apostrophes and hyphens dropped, spaces collapsed ("Break a leg!" -> "break a leg")."""
    s = re.sub(r"[‘’ʼ]", "'", text.lower())
    s = re.sub(r"[^\w\s'-]|_", " ", s)
    s = re.sub(r"(?<!\w)['-]+|['-]+(?!\w)", " ", s)
    return " ".join(s.split())


def clean_persian(text: str) -> str:
    """Persian as stored: diacritics removed, Arabic ي/ك unified to Persian ی/ک, spaces collapsed."""
    t = _ARABIC_DIACRITICS.sub("", text.replace("ي", "ی").replace("ك", "ک"))
    return " ".join(t.split())


def persian_key(text: str) -> str:
    """Comparison key for Persian: clean_persian() with zero-width non-joiners as spaces and edge punctuation off."""
    return " ".join(clean_persian(text).replace("‌", " ").split()).strip(" .!?؟،,؛;:\"'()")


class LexiconDB:
    """Thread-safe, read-only. One connection shared behind a lock: every query is an indexed read of a few rows."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._failed = False
        self._tables: set = set()

    def _connection(self) -> Optional[sqlite3.Connection]:
        with self._open_lock:  # sources open it concurrently: publish the connection only once it is ready
            if self._conn is None and not self._failed:
                if not os.path.exists(self.path):
                    self._failed = True
                    log.info("Lexicon database %s not found: run tools/build_lexicon_db.py (the setup scripts do)", self.path)
                    return None
                try:
                    uri = "file:" + os.path.abspath(self.path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
                    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
                    self._tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
                    self._conn = conn
                except sqlite3.Error:
                    self._failed = True
                    log.exception("Could not open the lexicon database %s", self.path)
            return self._conn

    def available(self) -> bool:
        return self._connection() is not None

    def has(self, table: str) -> bool:
        """Whether this dataset was imported (a build can leave out e.g. Urban Dictionary)."""
        return self._connection() is not None and table in self._tables

    def _rows(self, table: str, sql: str, args: Tuple = ()) -> List[Tuple]:
        if not self.has(table):
            return []
        try:
            with self._lock:
                return self._conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            log.exception("Lexicon query failed: %s %r", sql.split()[0:4], args)
            return []

    # --- Kaikki (Wiktionary) ---
    def entries(self, term: str) -> List[Dict[str, Any]]:
        """Entries for the term in dictionary order (one per part of speech and etymology). Entries spelled exactly
        as typed come first and other capitalisations ("Ran", a name) last. An inflected form ("ran", "banks")
        also gets its lemma's entries, ahead of its own when those are slight (fewer than three senses)."""
        key = text_key(term)
        sql = "SELECT word, pos, ipa, senses, collocations, derived FROM entries WHERE word_key = ? ORDER BY id"
        rows = [self._entry(r) for r in self._rows("entries", sql, (key,))]
        typed = " ".join(term.split())
        exact = [e for e in rows if e["word"] == typed]
        other_case = [e for e in rows if e not in exact]
        lemma_rows: List[Dict[str, Any]] = []
        lemmas = dict.fromkeys(r[0] for r in self._rows("form_of", "SELECT lemma FROM form_of WHERE form_key = ? ORDER BY rowid", (key,)))
        for lemma in sorted(lemmas, key=lambda w: w != w.lower()):  # "bank" before the name "Bank"
            if text_key(lemma) != key:
                lemma_rows += [e for e in (self._entry(r) for r in self._rows("entries", sql, (text_key(lemma),))) if e["word"] == lemma]
        if sum(len(e["senses"]) for e in exact) >= 3:
            return exact + lemma_rows + other_case
        return lemma_rows + exact + other_case

    @staticmethod
    def _entry(row: Tuple) -> Dict[str, Any]:
        w, pos, ipa, senses, colls, derived = row
        return {"word": w, "pos": pos, "ipa": ipa or "", "senses": json.loads(senses or "[]"),
                "collocations": json.loads(colls or "[]"), "derived": json.loads(derived or "[]")}

    def has_entry(self, term: str) -> bool:
        return bool(self._rows("entries", "SELECT 1 FROM entries WHERE word_key = ? LIMIT 1", (text_key(term),)))

    def ipa(self, word: str) -> str:
        rows = self._rows("entries", "SELECT ipa FROM entries WHERE word_key = ? AND ipa != '' ORDER BY id LIMIT 1", (text_key(word),))
        return rows[0][0] if rows else ""

    def persian(self, term: str) -> List[Dict[str, str]]:
        """Sense-labelled Persian translations in dictionary order: [{"pos", "sense", "fa"}]."""
        rows = self._rows("fa_translations", "SELECT pos, sense, fa FROM fa_translations WHERE word_key = ? ORDER BY rowid", (text_key(term),))
        return [{"pos": p, "sense": s, "fa": fa} for p, s, fa in rows]

    def english_for_persian(self, fa: str) -> List[str]:
        """English headwords whose Kaikki translation is exactly this Persian word or phrase."""
        rows = self._rows("fa_translations", "SELECT word FROM fa_translations WHERE fa_key = ? ORDER BY rowid", (persian_key(fa),))
        return list(dict.fromkeys(r[0] for r in rows))

    # --- Urban Dictionary ---
    def slang(self, term: str, limit: int = 2) -> List[Dict[str, str]]:
        rows = self._rows("slang", "SELECT definition, example FROM slang WHERE word_key = ? ORDER BY up - down DESC LIMIT ?", (text_key(term), limit))
        return [{"def": d, "example": e or ""} for d, e in rows]

    # --- Tatoeba / OpenSubtitles ---
    def translation_pair(self, text: str) -> Tuple[str, str]:
        """(Persian, corpus) for English text found as a whole in a corpus, Tatoeba first, else ("", "")."""
        rows = self._rows(
            "parallel",
            "SELECT fa, corpus FROM parallel WHERE en_key = ? ORDER BY corpus = 'tatoeba' DESC, freq DESC LIMIT 1",
            (text_key(text),),
        )
        return (rows[0][0], rows[0][1]) if rows else ("", "")

    def examples(self, term: str, limit: int = 3, min_words: int = 5, max_words: int = 20) -> List[Dict[str, str]]:
        """Sentence pairs containing the term as a phrase: [{"en", "fa", "corpus"}]. Tatoeba rows were imported
        first, so the index returns them first; within that order sentences of moderate length are kept."""
        words = text_key(term).split()
        if not words:
            return []
        query = '"' + " ".join(words).replace('"', "") + '"'
        rows = self._rows(
            "parallel_fts",
            "SELECT p.en, p.fa, p.corpus FROM parallel_fts JOIN parallel p ON p.id = parallel_fts.rowid "
            "WHERE parallel_fts MATCH ? LIMIT 300",
            (query,),
        )
        out, seen = [], set()
        for en, fa, corpus in rows:
            n = len(en.split())
            k = text_key(en)
            if min_words <= n <= max_words and k not in seen:
                seen.add(k)
                out.append({"en": en, "fa": fa, "corpus": corpus})
                if len(out) >= limit:
                    break
        return out

    def frequency(self, word: str) -> Optional[float]:
        """Occurrences per million words of English subtitles, None when unknown."""
        rows = self._rows("word_freq", "SELECT per_million FROM word_freq WHERE word_key = ?", (text_key(word),))
        return rows[0][0] if rows else None

    # --- Suggestions ---
    def suggestions(self, prefix: str, limit: int = 7) -> List[str]:
        """Headwords starting with the prefix, most frequent first (the lower-case spelling when there are two)."""
        key = text_key(prefix)
        if len(key) < 2:
            return []
        rows = self._rows(
            "entries",
            "SELECT MAX(e.word) FROM entries e LEFT JOIN word_freq f ON f.word_key = e.word_key "
            "WHERE e.word_key >= ? AND e.word_key < ? GROUP BY e.word_key "
            "ORDER BY COALESCE(MAX(f.per_million), 0) DESC, length(e.word_key) LIMIT ?",
            (key, key + "\U0010ffff", limit),
        ) if self.has("word_freq") else self._rows(
            "entries",
            "SELECT MAX(word) FROM entries WHERE word_key >= ? AND word_key < ? GROUP BY word_key ORDER BY length(word_key) LIMIT ?",
            (key, key + "\U0010ffff", limit),
        )
        return [r[0] for r in rows]

    def close_matches(self, word: str, limit: int = 1) -> List[str]:
        """Spelling suggestions for a word with no entry, ranked by similarity, then frequency. Candidates come
        from the trigram index and from headwords sharing the first two letters (a short word like "hevy"
        shares no trigram with "heavy")."""
        key = text_key(word)
        if len(key) < 3 or " " in key:
            return []
        grams = {key[i:i + 3] for i in range(len(key) - 2)}
        query = " OR ".join('"' + g.replace('"', "") + '"' for g in grams)
        cands = [r[0] for r in self._rows("headword_trigrams", "SELECT word FROM headword_trigrams WHERE headword_trigrams MATCH ? ORDER BY rank LIMIT 200", (query,))]
        cands += [r[0] for r in self._rows(
            "entries",
            "SELECT DISTINCT word_key FROM entries WHERE word_key >= ? AND word_key < ? AND abs(length(word_key) - ?) <= 2 LIMIT 5000",
            (key[:2], key[:2] + "\U0010ffff", len(key)),
        )]
        scored = []
        for c in dict.fromkeys(cands):
            if c == key:
                continue
            m = SequenceMatcher(None, key, c)
            if m.real_quick_ratio() >= 0.75 and m.quick_ratio() >= 0.75 and m.ratio() >= 0.75:
                scored.append((round(m.ratio(), 1), self.frequency(c) or 0.0, c))  # near-ties go to the commoner word
        scored.sort(reverse=True)
        return [c for _, _, c in scored[:limit]]
