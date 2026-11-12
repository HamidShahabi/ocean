"""Read-only access to ocean_lexicon.db, Ocean's local copy of its datasets (built by tools/build_lexicon_db.py):

- Kaikki.org (Wiktionary): entries by part of speech with IPA, senses (with their usage labels and topics),
  examples, synonyms and antonyms, forms, collocations and derived terms, sense-labelled Persian translations,
  and inflected forms and alternative spellings pointing at their lemma.
- Slang: Urban Dictionary definitions, a compact table of the best-voted ones.
- Tatoeba and OpenSubtitles: English-Persian sentence pairs (exact-match translations and example sentences,
  through an FTS5 index), plus word frequencies counted on the subtitles (English and Persian).
- idioms_fa: pre-translated idioms.

Qt-free and free of lexical_engine, so the import tool can share the key functions. Every query returns an
empty result when the database is missing or broken: lookups then work from the other sources.
"""
import json
import logging
import os
import pathlib
import re
import sqlite3
import threading
from difflib import SequenceMatcher
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

import lexicon_text as lt

log = logging.getLogger("ocean.lexicon")

MIN_SUBTITLE_AGREEMENT = 2  # a subtitle line is an exact-phrase translation when this many lines agree on it
VOCABULARY_MIN_ENTRIES = 50_000  # below this the dictionary is too small to call a word "unknown"
_PERSIAN_WORD = re.compile(r"[؀-ۿ]+")

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
        self._columns: Dict[str, List[str]] = {}
        self._indexes: set = set()
        self._cache_lock = threading.Lock()
        self._known: Dict[str, bool] = {}
        self._verbs: Dict[str, bool] = {}
        self._lemmas: Dict[str, List[str]] = {}
        self._phrase_map: Optional[Dict[str, List[str]]] = None
        self._vocabulary_ok: Optional[bool] = None

    def _connection(self) -> Optional[sqlite3.Connection]:
        with self._open_lock:  # sources open it concurrently: publish the connection only once it is ready
            if self._conn is None and not self._failed:
                if not os.path.exists(self.path):
                    self._failed = True
                    log.info("Lexicon database %s not found: run tools/build_lexicon_db.py (the setup scripts do)", self.path)
                    return None
                try:
                    uri = pathlib.Path(self.path).resolve().as_uri() + "?mode=ro"  # file:///C:/... on Windows, escaped
                    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
                    self._tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
                    self._indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
                    for table in ("entries", "slang", "form_of"):  # databases from older builds lack newer columns
                        self._columns[table] = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
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
    _ENTRY_COLUMNS = ("word", "pos", "ipa", "senses", "collocations", "derived", "synonyms", "antonyms", "forms", "related")

    def _entry_sql(self) -> str:
        """The entry query for this database: columns an older build did not have read as empty lists."""
        have = set(self._columns.get("entries", ()))
        cols = ", ".join(c if c in have else "'[]'" for c in self._ENTRY_COLUMNS)
        return f"SELECT {cols} FROM entries WHERE word_key = ? ORDER BY id"

    def entries(self, term: str) -> List[Dict[str, Any]]:
        """Entries for the term in dictionary order (one per part of speech and etymology). Entries spelled exactly
        as typed come first and other capitalisations ("Ran", a name) last. An inflected form ("ran", "banks")
        also gets its lemma's entries, ahead of its own when those are slight (fewer than three senses)."""
        key = text_key(term)
        self._connection()
        sql = self._entry_sql()
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
        w, pos, ipa, senses, colls, derived, syns, ants, forms, related = row
        return {"word": w, "pos": pos, "ipa": ipa or "", "senses": json.loads(senses or "[]"),
                "collocations": json.loads(colls or "[]"), "derived": json.loads(derived or "[]"),
                "synonyms": json.loads(syns or "[]"), "antonyms": json.loads(ants or "[]"),
                "forms": json.loads(forms or "[]"), "related": json.loads(related or "[]")}

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

    # --- Word forms, lemmas and what the dictionary knows ---
    def lemmas(self, word: str) -> List[str]:
        """The lemmas Wiktionary gives an inflected form ("ran" -> run), lower-cased. (Alternative spellings, kept in
        the same table by this version's importer, are not inflections: see alternatives().)"""
        key = text_key(word)
        self._connection()
        only_forms = " AND kind = 'form'" if "kind" in self._columns.get("form_of", ()) else ""
        rows = self._rows("form_of", f"SELECT lemma FROM form_of WHERE form_key = ?{only_forms} ORDER BY rowid", (key,))
        return [k for k in dict.fromkeys(text_key(r[0]) for r in rows) if k and k != key]

    def lemma_candidates(self, word: str) -> List[str]:
        """Base forms of a word: Wiktionary's lemmas first, then what plain suffix rules give that the dictionary
        lists ("dialing" -> dial, "tries" -> try). Never includes the word itself."""
        key = text_key(word)
        with self._cache_lock:
            if key in self._lemmas:
                return self._lemmas[key]
        out = self.lemmas(key)
        if key.isalpha() and len(key) >= 4:
            cands: List[str] = []
            for suffix, repl in (("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e"), ("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("er", ""), ("est", "")):
                if key.endswith(suffix) and len(key) - len(suffix) >= 3:
                    stem = key[:-len(suffix)] + repl
                    cands.append(stem)
                    if len(stem) >= 4 and stem[-1] == stem[-2] and not repl:
                        cands.append(stem[:-1])  # "running" -> run
            out += [c for c in dict.fromkeys(cands) if c not in out and self.has_entry(c)]
        with self._cache_lock:
            self._lemmas[key] = out
        return out

    def known_word(self, word: str) -> bool:
        """Whether the dictionary or the subtitle word list has this (lower-case) word: a headword, a form of
        one, or a word that occurs in the subtitles."""
        key = text_key(word)
        with self._cache_lock:
            if key in self._known:
                return self._known[key]
        known = (bool(self._rows("entries", "SELECT 1 FROM entries WHERE word_key = ? LIMIT 1", (key,)))
                 or bool(self._rows("form_of", "SELECT 1 FROM form_of WHERE form_key = ? LIMIT 1", (key,)))
                 or bool(self._rows("word_freq", "SELECT 1 FROM word_freq WHERE word_key = ?", (key,))))
        with self._cache_lock:
            self._known[key] = known
        return known

    def vocabulary_ok(self) -> bool:
        """Whether the dictionary is big enough for "unknown word" to mean something (not a handful of test entries)."""
        if self._vocabulary_ok is None:
            rows = self._rows("entries", "SELECT MAX(id) FROM entries")
            self._vocabulary_ok = bool(rows and (rows[0][0] or 0) >= VOCABULARY_MIN_ENTRIES)
        return self._vocabulary_ok

    def is_verb_form(self, word: str) -> bool:
        """Whether Wiktionary lists the (lower-case) word as a verb, or as a form of one."""
        key = text_key(word)
        with self._cache_lock:
            if key in self._verbs:
                return self._verbs[key]
        verb = bool(self._rows("entries", "SELECT 1 FROM entries WHERE word_key = ? AND pos = 'Verb' LIMIT 1", (key,)))
        if not verb:
            verb = any(self._rows("entries", "SELECT 1 FROM entries WHERE word_key = ? AND pos = 'Verb' LIMIT 1", (lemma,))
                       for lemma in self.lemmas(key))
        with self._cache_lock:
            self._verbs[key] = verb
        return verb

    def inflections(self, lemma: str, limit: int = 8) -> List[str]:
        """Inflected forms Wiktionary lists for a lemma ("possess" -> possesses, possessed, possessing), when the
        database has the lemma index (built by this version's importer)."""
        self._connection()
        if "form_of_lemma" not in self._indexes:
            return []
        rows = self._rows("form_of", "SELECT form_key FROM form_of WHERE lemma = ? AND kind = 'form' LIMIT ?", (" ".join(lemma.split()), limit * 2))
        return [k for k in dict.fromkeys(r[0] for r in rows) if k != text_key(lemma) and " " not in k][:limit]

    def alternatives(self, word: str, limit: int = 4) -> List[str]:
        """Alternative spellings or forms of a word: those pointing at it ("color" -> colour), and the base
        spelling when the word itself is one ("colour" -> color)."""
        self._connection()
        if "kind" not in self._columns.get("form_of", ()):
            return []
        out = [r[0] for r in self._rows("form_of", "SELECT lemma FROM form_of WHERE form_key = ? AND kind = 'alt' ORDER BY rowid", (text_key(word),))]
        if "form_of_lemma" in self._indexes:
            out += [r[0] for r in self._rows("form_of", "SELECT form_key FROM form_of WHERE lemma = ? AND kind = 'alt' ORDER BY rowid LIMIT 20", (" ".join(word.split()),))]
        key = text_key(word)
        return [w for w in dict.fromkeys(out) if text_key(w) != key][:limit]

    # --- Idioms and phrases ---
    def _phrase_entries(self, norm: str) -> List[str]:
        """Headwords of multi-word entries whose normalised key (phrase_norm) is `norm`."""
        self._connection()
        if "phrase_key" in self._columns.get("entries", ()):
            rows = self._rows("entries", "SELECT word FROM entries WHERE phrase_key = ? AND phrase_key != '' ORDER BY id LIMIT 5", (norm,))
            return list(dict.fromkeys(r[0] for r in rows))
        with self._cache_lock:  # a database from an older build: index its phrases once, on first use
            if self._phrase_map is None:
                mapping: Dict[str, List[str]] = {}
                for word, in self._rows("entries", "SELECT word FROM entries WHERE word_key LIKE '% %' ORDER BY id"):
                    n = lt.phrase_norm(text_key(word).split())
                    if " " in n:
                        mapping.setdefault(n, []).append(word)
                self._phrase_map = mapping
            return list(dict.fromkeys(self._phrase_map.get(norm, [])))[:5]

    def phrase_variants(self, words: List[str]) -> List[str]:
        """Normalised keys for typed words: every word also as its base forms (dials, dialing -> dial), then
        neutral pronouns, determiners and negations dropped. A leading "to" is dropped too."""
        options = [[w] + [l for l in self.lemma_candidates(w)[:2]] if w.isalpha() else [w] for w in words]
        out: List[str] = []
        for combo in list(product(*options))[:48]:
            for variant in (list(combo), list(combo)[1:] if combo[0] == "to" else []):
                norm = lt.phrase_norm(variant)
                if " " in norm and norm not in out:
                    out.append(norm)
        return out

    def match_phrase(self, term: str) -> Optional[Dict[str, Any]]:
        """The dictionary entry a typed phrase means, or None. Rule 1: the whole phrase, as typed (or as a form
        of an entry: "dialed down"), is looked up first, before anything is split into words. Rule 2: failing that,
        the phrase with neutral pronouns, determiners, negations and inflections normalised away is matched
        against the multi-word entries reduced the same way ("Dial it down" -> "dial down", "no bed of roses" ->
        "bed of roses"). Returns {"entry": headword, "exact": bool, "typed": key, "dropped": [typed words the entry
        does not have], "negated": bool}."""
        key = text_key(term)
        words = key.split()
        if len(words) < 2 or len(words) > 12:
            return None
        found = self.entries(key)
        if found:
            return {"entry": found[0]["word"], "exact": True, "typed": key, "dropped": [], "negated": False}
        for variant in self.phrase_variants(words):
            for entry in self._phrase_entries(variant):
                have = set(text_key(entry).split())
                dropped = [w for w in words if w not in have and lt.phrase_norm([w]) == ""]
                return {"entry": entry, "exact": False, "typed": key, "dropped": dropped,
                        "negated": bool({"no", "not", "n't", "never"} & set(dropped)) and not {"no", "not", "never"} & have}
        return None

    def idiom_fa(self, term: str) -> Optional[Dict[str, str]]:
        """A pre-translated idiom (idioms_fa): {"phrase", "fa", "source"}, looked up as typed, then normalised."""
        key = text_key(term)
        words = key.split()
        if len(words) < 2:
            return None
        rows = self._rows("idioms_fa", "SELECT phrase, fa, source FROM idioms_fa WHERE phrase_key = ?", (key,))
        if not rows:
            negated = lt.is_negated(words)
            for variant in self.phrase_variants(words):
                rows = [r for r in self._rows("idioms_fa", "SELECT phrase, fa, source FROM idioms_fa WHERE norm_key = ? ORDER BY rowid LIMIT 5", (variant,))
                        if lt.is_negated(text_key(r[0]).split()) == negated]  # "no bed of roses" is not "a bed of roses"
                if rows:
                    break
        return {"phrase": rows[0][0], "fa": rows[0][1], "source": rows[0][2]} if rows else None

    # --- Slang ---
    def _slang_query(self) -> Optional[Tuple[str, bool]]:
        """(SQL, matches on word_key) for this database's slang table, whatever its columns are called."""
        cols = [c.lower() for c in self._columns.get("slang", ())]
        if not cols:
            return None

        def pick(*names: str) -> Optional[str]:
            return next((n for n in names if n in cols), None)

        word, definition = pick("word", "term", "headword"), pick("definition", "meaning", "def")
        if not (word and definition):
            return None
        example = pick("example", "examples") or "''"
        up, down = pick("up", "up_votes", "upvotes", "thumbs_up", "likes"), pick("down", "down_votes", "downvotes", "thumbs_down", "dislikes")
        ratio = pick("ratio", "thumbs_ratio", "score")
        by_key = "word_key" in cols
        votes = f"{up}, {down}" if up and down else (f"{ratio} * 1000, (1 - {ratio}) * 1000" if ratio else "1, 0")
        order = f"{up} - {down}" if up and down else (ratio or "1")
        match = "word_key = ?" if by_key else f"LOWER({word}) = ?"
        sql = f"SELECT {definition}, {example}, {votes} FROM slang WHERE {match} ORDER BY {order} DESC LIMIT ?"
        return sql, by_key

    def slang(self, term: str, limit: int = 2) -> List[Dict[str, Any]]:
        """Slang definitions, best-voted first: [{"def", "example", "up", "down", "ratio"}]. Reads the table the
        importer writes and the compact one of a ready-made slang database alike."""
        query = self._slang_query() if self.has("slang") else None
        if not query:
            return []
        sql, by_key = query
        key = text_key(term) if by_key else " ".join(term.lower().split())
        rows = self._rows("slang", sql, (key, limit))
        out = []
        for d, e, up, down in rows:
            up, down = float(up or 0), float(down or 0)
            out.append({"def": d, "example": e or "", "up": int(up), "down": int(down), "ratio": up / (up + down) if up + down else 0.0})
        return out

    # --- Tatoeba / OpenSubtitles ---
    def translation_pair(self, text: str) -> Tuple[str, str]:
        """(Persian, corpus) for English text found as a whole in a corpus, Tatoeba first, else ("", ""). A
        subtitle line counts when enough subtitle lines agree on its translation."""
        rows = self._rows(
            "parallel",
            "SELECT fa, corpus FROM parallel WHERE en_key = ? AND (corpus = 'tatoeba' OR freq >= ?) "
            "ORDER BY corpus = 'tatoeba' DESC, freq DESC LIMIT 1",
            (text_key(text), MIN_SUBTITLE_AGREEMENT),
        )
        return (rows[0][0], rows[0][1]) if rows else ("", "")

    def _fts_query(self, term: str, with_forms: bool) -> str:
        words = text_key(term).split()
        phrases = [" ".join(words)]
        if with_forms and len(words) == 1:
            phrases += self.inflections(words[0])
        return " OR ".join('"' + p.replace('"', "") + '"' for p in dict.fromkeys(phrases) if p)

    def sentence_pairs(self, term: str, limit: int = 3, min_words: int = 5, max_words: int = 20) -> List[Dict[str, str]]:
        """Sentence pairs containing the term as a phrase, as the corpora have them: [{"en", "fa", "corpus"}].
        Not filtered for quality: the engine counts Persian words in them to judge a translation. For examples to
        show, see examples()."""
        query = self._fts_query(term, with_forms=False)
        if not query:
            return []
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

    def examples(self, term: str, corpus: str, limit: int = 4) -> List[Dict[str, str]]:
        """Example sentences with their Persian from one corpus ("opensubtitles" or "tatoeba"), the best first:
        [{"en", "fa", "corpus"}]. Containing the term (or one of its inflected forms); complete, natural, short
        sentences only (lexicon_text.clean_subtitle_line; for Tatoeba the usage-example rules), so fragments, long
        lines and subtitle artefacts never get here, whatever an older import let through. Lines other subtitle
        lines agree on and of about eight words come first, strong profanity last."""
        query = self._fts_query(term, with_forms=True)
        if not query:
            return []
        rows = self._rows(
            "parallel_fts",
            "SELECT p.en, p.fa, p.freq FROM parallel_fts JOIN parallel p ON p.id = parallel_fts.rowid "
            "WHERE parallel_fts MATCH ? AND p.corpus = ? LIMIT 400",
            (query, corpus),
        )
        check_vocabulary = self.vocabulary_ok()
        candidates = []
        seen: set = set()
        for en, fa, freq in rows:
            if corpus == "opensubtitles":
                good = lt.clean_subtitle_line(en, is_verb=self.is_verb_form)
                if good and check_vocabulary and len(lt.unknown_words(good, self.known_word, term)) >= 2:
                    good = ""
            else:
                good = lt.clean_example(en, max_words=lt.MAX_SUBTITLE_WORDS, is_known=self.known_word if check_vocabulary else None, target=term)
            k = text_key(good)
            if good and k not in seen and not re.search(r"[A-Za-z]", fa):
                seen.add(k)
                candidates.append((bool(lt.PROFANITY.search(good)), -min(freq, 5), abs(len(good.split()) - 8), len(candidates),
                                   {"en": good, "fa": fa, "corpus": corpus}))
        candidates.sort(key=lambda c: c[:4])
        return [c[4] for c in candidates[:limit]]

    def phrase_hits(self, phrase: str, limit: int = 25) -> int:
        """How many subtitle and corpus lines contain the phrase (a line several subtitle files agree on counts for
        each), counted up to `limit`: a cheap "is it said, and how often" test."""
        query = self._fts_query(phrase, with_forms=False)
        if not query:
            return 0
        rows = self._rows(
            "parallel_fts",
            "SELECT COALESCE(SUM(freq), 0) FROM (SELECT p.freq FROM parallel_fts JOIN parallel p ON p.id = parallel_fts.rowid "
            "WHERE parallel_fts MATCH ? LIMIT ?)",
            (query, limit),
        )
        return min(int(rows[0][0]), limit) if rows else 0

    def frequency(self, word: str) -> Optional[float]:
        """Occurrences per million words of English subtitles, None when unknown."""
        rows = self._rows("word_freq", "SELECT per_million FROM word_freq WHERE word_key = ?", (text_key(word),))
        return rows[0][0] if rows else None

    def persian_frequency(self, fa: str) -> Optional[int]:
        """How often the rarest content word of a Persian word or phrase occurs in the Persian subtitles; None
        when the database has no Persian frequencies (nothing can then be said against the word)."""
        if not self.has("fa_word_freq"):
            return None
        tokens = _PERSIAN_WORD.findall(persian_key(fa))
        content = [t for t in tokens if len(t) > 2] or tokens
        if not content:
            return 0
        counts = []
        for t in content:
            rows = self._rows("fa_word_freq", "SELECT n FROM fa_word_freq WHERE fa_key = ?", (t,))
            counts.append(rows[0][0] if rows else 0)
        return min(counts)

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
