"""Build ocean_lexicon.db, the local database every lookup reads, from downloaded dataset dumps.

    python tools/build_lexicon_db.py --out ocean_lexicon.db \
        --kaikki kaikki.org-dictionary-English.jsonl.gz \
        --tatoeba-dir tatoeba/ \
        --opensubtitles en-fa.txt.zip \
        [--urban urbandict-word-defs.csv | --slang-db ocean_slang.db] [--idioms-fa idioms_fa.tsv]

Every input is optional; a dataset left out is simply absent from the result (lookups work without it).
The database is written to <out>.building and moved into place only when complete, so an interrupted
build never leaves a half-written file where Ocean looks. Files are streamed, never loaded whole.

- --kaikki: the Kaikki.org English extract (JSON lines, optionally .gz), https://kaikki.org/dictionary/English/
- --tatoeba-dir: eng_sentences.tsv.bz2, pes_sentences.tsv.bz2 and eng-pes_links.tsv.bz2 from
  https://downloads.tatoeba.org/exports/per_language/
- --opensubtitles: the OPUS OpenSubtitles en-fa Moses zip, https://opus.nlpl.eu/OpenSubtitles/en&fa
- --urban: an Urban Dictionary dump, CSV or JSON (lines), with word, definition, up/down vote columns.
  Only entries with upvotes >= 2 x downvotes (and at least one upvote) are kept.
- --slang-db: a compact SQLite file with a `slang` table of the top slang entries (word, definition, example and
  up/down votes or a thumbs ratio), copied as it is; use it instead of the multi-gigabyte dump.
- --idioms-fa: pre-translated idioms, TSV (phrase, Persian[, source]) or JSON lines ({"phrase", "fa", "source"}),
  optionally .gz. Lookups answer from this table before any machine translation is asked.
"""
import argparse
import bz2
import csv
import gzip
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from collections import Counter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexicon_text as lt  # noqa: E402
from lexicon_db import MIN_SUBTITLE_AGREEMENT, clean_persian, persian_key, text_key  # noqa: E402

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE entries (id INTEGER PRIMARY KEY, word TEXT NOT NULL, word_key TEXT NOT NULL, pos TEXT NOT NULL,
                      ipa TEXT NOT NULL, senses TEXT NOT NULL, collocations TEXT NOT NULL, derived TEXT NOT NULL,
                      synonyms TEXT NOT NULL DEFAULT '[]', antonyms TEXT NOT NULL DEFAULT '[]',
                      forms TEXT NOT NULL DEFAULT '[]', related TEXT NOT NULL DEFAULT '[]',
                      phrase_key TEXT NOT NULL DEFAULT '');
CREATE TABLE form_of (form_key TEXT NOT NULL, lemma TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'form');
CREATE TABLE fa_translations (word TEXT NOT NULL, word_key TEXT NOT NULL, pos TEXT NOT NULL, sense TEXT NOT NULL,
                              fa TEXT NOT NULL, fa_key TEXT NOT NULL);
"""

POS_LABELS = {
    "noun": "Noun", "verb": "Verb", "adj": "Adjective", "adv": "Adverb", "pron": "Pronoun", "prep": "Preposition",
    "conj": "Conjunction", "intj": "Interjection", "det": "Determiner", "num": "Numeral", "name": "Proper noun",
    "phrase": "Idiom / Expression", "prep_phrase": "Idiom / Expression", "proverb": "Proverb", "particle": "Particle",
    "article": "Article", "abbrev": "Abbreviation", "contraction": "Contraction",
}
SKIPPED_POS = {"character", "symbol", "punct", "prefix", "suffix", "infix", "interfix", "affix", "circumfix"}
SKIPPED_FORM_TAGS = {"table-tags", "inflection-template", "class", "romanization", "canonical"}
US_TAGS = {"US", "General-American", "GA", "GenAm", "American"}
OTHER_ACCENT_TAGS = {"UK", "Received-Pronunciation", "RP", "Australia", "Australian", "New-Zealand", "Ireland",
                     "Scotland", "South-Africa", "Canada", "India", "Cockney", "Northern-England", "Southern-England"}
MAX_SENSES = 10
MAX_DERIVED = 30
MAX_RELATED = 20
MAX_RELATIONS = 15
MAX_FORMS = 10
SUBTITLE_EXAMPLE_ROWS = 1_000_000  # most subtitle sentences the database keeps as examples (the best agreed, then about eight words)
FA_FREQ_MIN_COUNT = 3  # Persian subtitle words seen fewer times are left out of the frequency table
PERSIAN_LANGS = {"Persian", "Iranian Persian"}  # Dari (Afghan Persian) also has code "fa"; Ocean targets Iranian usage
URBAN_RATIO = 2  # keep an Urban Dictionary entry only with upvotes >= URBAN_RATIO x downvotes
URBAN_PER_WORD = 3
SUBTITLE_MAX_WORDS = 12  # a line this short may be an exact-phrase translation; examples go up to lt.MAX_SUBTITLE_WORDS
SUBTITLE_MIN_AGREEMENT = MIN_SUBTITLE_AGREEMENT  # a subtitle rendering counts when at least this many lines agree on it
_PERSIAN = re.compile(r"[؀-ۿ]")
_PERSIAN_WORD = re.compile(r"[؀-ۿ]+")
_SUBTITLE_NOISE = re.compile(r"[♪♫#\[\]{}<>]")
SCHEMA_VERSION = 2  # 2: sense tags, relations, forms, phrase keys, Persian word frequencies, idioms_fa
_LATIN = re.compile(r"[A-Za-z]")
_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?")


def _open_text(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    if path.endswith(".bz2"):
        return bz2.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _clean(text: str) -> str:
    return " ".join(str(text).split())


def _say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --- Kaikki.org -------------------------------------------------------------------------------
def pick_ipa(sounds: List[Dict[str, Any]]) -> str:
    """The phonemic (/.../) transcription: one labelled American first, then an unlabelled one, then any that
    is not another accent's (the engine's US G2P is better than a British transcription, but an entry with
    only that one still gets it, normalised for display)."""
    phonemic = [s for s in sounds if str(s.get("ipa", "")).startswith("/")]
    for accept in (lambda t: US_TAGS & t, lambda t: not t, lambda t: not OTHER_ACCENT_TAGS & t, lambda t: True):
        for s in phonemic:
            if accept(set(s.get("tags", []))):
                return s["ipa"]
    return ""


def pick_example(examples: List[Dict[str, Any]]) -> str:
    """A usage example (not a collocation list, never a citation or damaged text), else a short quotation
    dated 1900 or later: older ones are long-s OCR text ("poffeft") in old spelling. "" when none is fit."""
    for e in examples:
        if e.get("type") != "quotation" and "collocation" not in e.get("tags", []):
            t = lt.clean_example(e.get("text", ""))
            if t:
                return t
    for e in examples:
        if e.get("type") == "quotation":
            t = lt.clean_example(e.get("text", ""), quotation=True, year=lt.quotation_year(e.get("ref", "")),
                                 max_words=lt.MAX_SUBTITLE_WORDS)
            if t:
                return t
    return ""


def _relations(items: List[Dict[str, Any]]) -> List[List[str]]:
    """Kaikki synonym or antonym items as [word, sense hint, tags], one per word."""
    out, seen = [], set()
    for r in items or []:
        w = _clean(r.get("word", ""))
        if w and w.lower() not in seen and len(w) <= 40:
            seen.add(w.lower())
            out.append([w, _clean(r.get("sense", "")), " ".join(r.get("tags", []))])
    return out[:MAX_RELATIONS]


def kaikki_entry(row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Tuple[str, str, str]], List[Dict[str, str]]]:
    """(entry or None, [(form, lemma, kind)], [persian translation]) for one Kaikki JSON line. kind is "form" for
    an inflection and "alt" for an alternative spelling or form. Multi-word entries (idioms, phrasal verbs,
    proverbs) are entries like any other. Every sense keeps its usage labels and topics (tags) so that the
    reader can put "mathematics" or "archaic" senses last; the stored order already does so."""
    word = _clean(row.get("word", ""))
    pos_code = row.get("pos", "")
    if not word or row.get("lang_code", "en") != "en" or pos_code in SKIPPED_POS:
        return None, [], []
    pos = POS_LABELS.get(pos_code, pos_code.replace("_", " ").capitalize() or "Entry")
    forms: List[Tuple[str, str, str]] = []
    senses: List[Dict[str, Any]] = []
    collocations: List[str] = []
    translations = list(row.get("translations", []))
    for s in row.get("senses", []):
        translations += s.get("translations", [])
        for key, kind in (("form_of", "form"), ("alt_of", "alt")):
            if kind == "alt" and lt.tag_set(s.get("tags", [])) & lt.NOT_AN_ALTERNATIVE:
                continue  # an abbreviation, a clipping, a misspelling: not another way to spell the word
            forms += [(word, _clean(ref["word"]), kind) for ref in s.get(key) or [] if ref.get("word")]
        if s.get("form_of") or s.get("alt_of"):
            continue
        tags = lt.tag_set(s.get("tags", []), s.get("topics", []))
        glosses = s.get("glosses") or []
        if not glosses or glosses[-1].startswith("Used other than figuratively"):
            continue
        examples = s.get("examples", [])
        for e in examples:
            if "collocation" in e.get("tags", []):
                collocations += [_clean(p) for p in e.get("text", "").split(";") if 0 < len(_clean(p)) <= 40]
        gloss = _clean(glosses[-1])  # subsenses list the parent gloss first
        sense: Dict[str, Any] = {"def": gloss, "example": pick_example(examples)}
        kept = sorted(tags & lt.KEPT_TAGS)
        if kept:
            sense["tags"] = kept
        syn, ant = _relations(s.get("synonyms")), _relations(s.get("antonyms"))
        if syn:
            sense["syn"] = syn
        if ant:
            sense["ant"] = ant
        sense["_class"] = lt.sense_class(kept, gloss, pos, has_other_pos=False)
        senses.append(sense)
    senses.sort(key=lambda sn: sn["_class"])  # stable: Wiktionary's order within a class; specialist and archaic senses last
    senses = [{k: v for k, v in sn.items() if k != "_class"} for sn in senses[:MAX_SENSES]]

    fa_rows = []
    for t in translations:
        if t.get("code") == "fa" and t.get("lang") in PERSIAN_LANGS and t.get("word"):
            fa = clean_persian(t["word"])
            if fa:
                fa_rows.append({"sense": _clean(t.get("sense", "")), "fa": fa})
    entry = None
    if senses:
        derived = list(dict.fromkeys(_clean(d["word"]) for d in row.get("derived", []) if d.get("word")))[:MAX_DERIVED]
        related = list(dict.fromkeys(_clean(d["word"]) for d in row.get("related", []) if d.get("word")))[:MAX_RELATED]
        inflections: List[List[str]] = []
        for f in row.get("forms", []):
            form, ftags = _clean(f.get("form", "")), f.get("tags", [])
            if form and form != word and " " not in form and not SKIPPED_FORM_TAGS & set(ftags) and form != "no-table-tags" \
                    and lt.classify_form(" ".join(ftags)):
                inflections.append([form, " ".join(ftags)])
        key = text_key(word)
        entry = {"word": word, "pos": pos, "ipa": pick_ipa(row.get("sounds", [])), "senses": senses,
                 "collocations": list(dict.fromkeys(collocations)), "derived": derived, "related": related,
                 "synonyms": _relations(row.get("synonyms")), "antonyms": _relations(row.get("antonyms")),
                 "forms": inflections[:MAX_FORMS],
                 "phrase_key": lt.phrase_norm(key.split()) if " " in key else ""}
    for r in fa_rows:
        r["pos"] = pos
    return entry, forms, fa_rows


def import_kaikki(db: sqlite3.Connection, lines: Iterable[str]) -> int:
    n = 0
    entries, forms, fas = [], [], []

    def flush() -> None:
        db.executemany("INSERT INTO entries (word, word_key, pos, ipa, senses, collocations, derived, synonyms, antonyms, "
                       "forms, related, phrase_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", entries)
        db.executemany("INSERT INTO form_of (form_key, lemma, kind) VALUES (?, ?, ?)", forms)
        db.executemany("INSERT INTO fa_translations (word, word_key, pos, sense, fa, fa_key) VALUES (?, ?, ?, ?, ?, ?)", fas)
        entries.clear(), forms.clear(), fas.clear()

    def dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        entry, row_forms, fa_rows = kaikki_entry(row)
        word = _clean(row.get("word", ""))
        if entry:
            n += 1
            entries.append((entry["word"], text_key(entry["word"]), entry["pos"], entry["ipa"], dump(entry["senses"]),
                            dump(entry["collocations"]), dump(entry["derived"]), dump(entry["synonyms"]), dump(entry["antonyms"]),
                            dump(entry["forms"]), dump(entry["related"]), entry["phrase_key"]))
        forms += [(text_key(f), lemma, kind) for f, lemma, kind in row_forms]
        fas += [(word, text_key(word), r["pos"], r["sense"], r["fa"], persian_key(r["fa"])) for r in fa_rows]
        if len(entries) >= 5000 or len(fas) >= 5000:
            flush()
            if n % 100000 < 5000:
                _say(f"  Kaikki: {n} entries")
    flush()
    db.executescript("""
        CREATE INDEX entries_key ON entries (word_key);
        CREATE INDEX entries_phrase ON entries (phrase_key) WHERE phrase_key != '';
        CREATE INDEX form_of_key ON form_of (form_key);
        CREATE INDEX form_of_lemma ON form_of (lemma);
        CREATE INDEX fa_word ON fa_translations (word_key);
        CREATE INDEX fa_key ON fa_translations (fa_key);
        CREATE VIRTUAL TABLE headword_trigrams USING fts5 (word, tokenize = 'trigram');
        INSERT INTO headword_trigrams (word)
            SELECT DISTINCT word_key FROM entries WHERE length(word_key) >= 3 AND word_key NOT GLOB '*[^a-z]*';
    """)
    return n


# --- Urban Dictionary -------------------------------------------------------------------------
_UD_FIELDS = {
    "word": ("word", "term", "headword"),
    "definition": ("definition", "meaning", "def"),
    "example": ("example", "examples"),
    "up": ("up_votes", "upvotes", "thumbs_up", "up", "likes"),
    "down": ("down_votes", "downvotes", "thumbs_down", "down", "dislikes"),
}


def _ud_field(row: Dict[str, Any], name: str) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for k in _UD_FIELDS[name]:
        if k in lowered:
            return lowered[k]
    return None


def _ud_text(text: Any) -> str:
    """Urban Dictionary marks links as [word]: keep the word."""
    return _clean(re.sub(r"\[([^\]]*)\]", r"\1", str(text or "")).replace("\r", " "))


def read_urban_rows(path: str) -> Iterator[Dict[str, Any]]:
    with _open_text(path) as f:
        if re.sub(r"\.(gz|bz2)$", "", path).endswith(".csv"):
            csv.field_size_limit(10_000_000)
            yield from csv.DictReader(f)
            return
        first = f.read(1)
        f.seek(0)
        if first == "[":
            yield from json.load(f)
        else:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def urban_keep(up: int, down: int) -> bool:
    return up >= 1 and up >= URBAN_RATIO * down


def import_urban(db: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    db.execute("CREATE TABLE slang_raw (word_key TEXT, word TEXT, definition TEXT, example TEXT, up INTEGER, down INTEGER)")
    batch = []
    for row in rows:
        try:
            up, down = int(float(_ud_field(row, "up") or 0)), int(float(_ud_field(row, "down") or 0))
        except (TypeError, ValueError):
            continue
        word, definition = _ud_text(_ud_field(row, "word")), _ud_text(_ud_field(row, "definition"))
        if not (word and definition and len(definition) <= 600 and urban_keep(up, down)):
            continue
        example = _ud_text(_ud_field(row, "example"))
        batch.append((text_key(word), word, definition, example if len(example) <= 300 else "", up, down))
        if len(batch) >= 10000:
            db.executemany("INSERT INTO slang_raw VALUES (?, ?, ?, ?, ?, ?)", batch)
            batch.clear()
    db.executemany("INSERT INTO slang_raw VALUES (?, ?, ?, ?, ?, ?)", batch)
    db.executescript(f"""
        CREATE TABLE slang (word_key TEXT NOT NULL, word TEXT NOT NULL, definition TEXT NOT NULL, example TEXT NOT NULL,
                            up INTEGER NOT NULL, down INTEGER NOT NULL);
        INSERT INTO slang SELECT word_key, word, definition, example, up, down FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY word_key ORDER BY up - down DESC) AS rk FROM slang_raw
        ) WHERE rk <= {URBAN_PER_WORD};
        DROP TABLE slang_raw;
        CREATE INDEX slang_key ON slang (word_key);
    """)
    return db.execute("SELECT COUNT(*) FROM slang").fetchone()[0]


# --- Parallel corpora (Tatoeba first, so the full-text index returns its sentences first) -----
def _create_parallel(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS parallel (id INTEGER PRIMARY KEY, corpus TEXT NOT NULL, en TEXT NOT NULL, "
               "en_key TEXT NOT NULL, fa TEXT NOT NULL, freq INTEGER NOT NULL)")


def _usable_pair(en: str, fa: str) -> bool:
    return bool(en and fa and _PERSIAN.search(fa) and not _LATIN.search(fa) and text_key(en))


def read_tatoeba_pairs(folder: str) -> Iterator[Tuple[str, str]]:
    def sentences(name: str) -> Iterator[Tuple[str, str]]:
        with _open_text(os.path.join(folder, name)) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    yield parts[0], parts[2]

    links: Dict[str, List[str]] = {}
    with _open_text(os.path.join(folder, "eng-pes_links.tsv.bz2")) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                links.setdefault(parts[0], []).append(parts[1])
    persian = dict(sentences("pes_sentences.tsv.bz2"))
    for eng_id, en in sentences("eng_sentences.tsv.bz2"):
        for pes_id in links.get(eng_id, []):
            if pes_id in persian:
                yield en, persian[pes_id]


def import_tatoeba(db: sqlite3.Connection, pairs: Iterable[Tuple[str, str]]) -> int:
    _create_parallel(db)
    rows = [("tatoeba", _clean(en), text_key(en), clean_persian(fa), 1) for en, fa in pairs]
    rows = [r for r in rows if _usable_pair(r[1], r[3])]
    db.executemany("INSERT INTO parallel (corpus, en, en_key, fa, freq) VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def read_opus_moses_zip(path: str) -> Iterator[Tuple[str, str]]:
    """Line pairs of an OPUS Moses zip (<corpus>.en-fa.en next to <corpus>.en-fa.fa)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        en_name = next(n for n in names if n.endswith(".en"))
        fa_name = next(n for n in names if n.endswith(".fa"))
        with z.open(en_name) as fe, z.open(fa_name) as ff:
            for en, fa in zip(io.TextIOWrapper(fe, encoding="utf-8"), io.TextIOWrapper(ff, encoding="utf-8")):
                yield en.strip(), fa.strip()


def _loose_subtitle(en: str) -> str:
    """A subtitle line as an exact-phrase translation key: short, without artefacts, "" otherwise."""
    s = " ".join(re.sub(r"<[^>]*>", "", en).split()).lstrip("-–— ")
    if not s or re.search(r"[♪♫#\[\]{}]", s) or not 1 <= len(s.split()) <= SUBTITLE_MAX_WORDS or len(s) > 100:
        return ""
    return s


def _verb_forms(db: sqlite3.Connection) -> set:
    """Lower-case single words Wiktionary lists as verbs or as forms of a verb (for the "has a verb" test)."""
    forms = {r[0] for r in db.execute("SELECT word_key FROM entries WHERE pos = 'Verb' AND word_key NOT LIKE '% %'")}
    forms |= {r[0] for r in db.execute(
        "SELECT form_key FROM form_of WHERE kind = 'form' AND lemma IN (SELECT word FROM entries WHERE pos = 'Verb') "
        "AND form_key NOT LIKE '% %'")}
    return forms


def import_opensubtitles(db: sqlite3.Connection, pairs: Iterable[Tuple[str, str]]) -> Tuple[int, int]:
    """Two kinds of subtitle rows, each line's most frequent Persian rendering once:
    - short lines (phrases, everyday sentences) that enough subtitle lines agree on, for exact-phrase
      translations;
    - every line that makes a good example sentence (complete, natural, up to 16 words, with a verb, no
      subtitle artefacts: lt.clean_subtitle_line), even when seen once.
    Word frequencies are counted on every English line and every Persian one (the Persian table tells which
    Wiktionary equivalents are everyday words)."""
    _create_parallel(db)
    db.execute("CREATE TABLE sub_raw (en_key TEXT, en TEXT, fa TEXT, strict INTEGER)")
    verbs = _verb_forms(db)
    is_verb = verbs.__contains__ if verbs else None
    tokens: Counter = Counter()
    fa_tokens: Counter = Counter()
    total_tokens = 0
    batch = []
    for i, (en, fa) in enumerate(pairs, 1):
        en = _clean(en.lstrip("-– "))
        line_tokens = _TOKEN.findall(en.lower().replace("’", "'"))
        tokens.update(line_tokens)
        total_tokens += len(line_tokens)
        fa = clean_persian(fa.lstrip("-– "))
        fa_tokens.update(_PERSIAN_WORD.findall(fa))
        if _usable_pair(en, fa) and not _SUBTITLE_NOISE.search(fa):
            good = lt.clean_subtitle_line(en, is_verb=is_verb)
            short = _loose_subtitle(en)
            if good or short:
                batch.append((text_key(good or short), good or short, fa, 1 if good else 0))
        if len(batch) >= 20000:
            db.executemany("INSERT INTO sub_raw VALUES (?, ?, ?, ?)", batch)
            batch.clear()
        if i % 1_000_000 == 0:
            _say(f"  OpenSubtitles: {i} lines")
    db.executemany("INSERT INTO sub_raw VALUES (?, ?, ?, ?)", batch)
    db.executescript(f"""
        INSERT INTO parallel (corpus, en, en_key, fa, freq)
        SELECT 'opensubtitles', en, en_key, fa, n FROM (
            SELECT en_key, MIN(en) AS en, fa, COUNT(*) AS n, MAX(strict) AS strict,
                   ROW_NUMBER() OVER (PARTITION BY en_key ORDER BY COUNT(*) DESC, fa) AS rk
            FROM sub_raw GROUP BY en_key, fa
        ) WHERE rk = 1 AND (n >= {SUBTITLE_MIN_AGREEMENT} OR strict = 1) ORDER BY n DESC, ABS(length(en) - length(replace(en, ' ', '')) - 7) LIMIT {SUBTITLE_EXAMPLE_ROWS};
        DROP TABLE sub_raw;
    """)
    db.execute("CREATE TABLE word_freq (word_key TEXT PRIMARY KEY, per_million REAL NOT NULL) WITHOUT ROWID")
    scale = 1_000_000 / max(total_tokens, 1)
    db.executemany("INSERT INTO word_freq VALUES (?, ?)", ((w, round(c * scale, 3)) for w, c in tokens.items() if c >= 3))
    db.execute("CREATE TABLE fa_word_freq (fa_key TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID")
    db.executemany("INSERT INTO fa_word_freq VALUES (?, ?)", ((w, c) for w, c in fa_tokens.items() if c >= FA_FREQ_MIN_COUNT))
    kept = db.execute("SELECT COUNT(*) FROM parallel WHERE corpus = 'opensubtitles'").fetchone()[0]
    return kept, len(tokens)


def finish_parallel(db: sqlite3.Connection) -> None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'parallel'").fetchone():
        return
    db.executescript("""
        CREATE INDEX parallel_key ON parallel (en_key);
        CREATE VIRTUAL TABLE parallel_fts USING fts5 (en, content = 'parallel', content_rowid = 'id');
        INSERT INTO parallel_fts (parallel_fts) VALUES ('rebuild');
    """)


# --- Pre-translated idioms --------------------------------------------------------------------
IDIOMS_FA_SCHEMA = """
CREATE TABLE idioms_fa (phrase_key TEXT PRIMARY KEY, norm_key TEXT NOT NULL, phrase TEXT NOT NULL, fa TEXT NOT NULL,
                        source TEXT NOT NULL);
CREATE INDEX idioms_fa_norm ON idioms_fa (norm_key);
"""


def read_idioms_fa(path: str) -> Iterator[Dict[str, str]]:
    """Rows of an idiom dataset: JSON lines ({"phrase", "fa", "source"}), or TSV, plain or .gz. A TSV with a header
    line (tools/build_idioms.py's idioms_fa.tsv.gz: phrase, key, ..., fa, fa_source) is read by column name; one
    without is read as phrase, Persian[, source]."""
    columns: Optional[Dict[str, int]] = None
    with _open_text(path) as f:
        for line in f:
            line = line.strip("\n")
            if not line.strip():
                continue
            if line.lstrip().startswith("{"):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                yield {"phrase": str(row.get("phrase") or row.get("en") or ""), "fa": str(row.get("fa") or row.get("persian") or ""),
                       "source": str(row.get("source") or "")}
                continue
            parts = line.split("\t")
            if parts[0].strip().lower() == "phrase":
                columns = {name.strip().lower(): i for i, name in enumerate(parts)}
                continue
            if columns and "fa" in columns:
                get = lambda name: parts[columns[name]] if name in columns and columns[name] < len(parts) else ""  # noqa: E731
                yield {"phrase": parts[0], "fa": get("fa"), "source": get("fa_source") or get("source")}
            elif len(parts) >= 2:
                yield {"phrase": parts[0], "fa": parts[1], "source": parts[2] if len(parts) > 2 else ""}


def import_idioms_fa(db: sqlite3.Connection, rows: Iterable[Dict[str, str]], default_source: str = "Idiom dictionary") -> int:
    """Fills idioms_fa; the first row of a phrase wins. Rows without a phrase or without Persian are skipped."""
    db.executescript(IDIOMS_FA_SCHEMA)
    batch = []
    for r in rows:
        phrase, key = _clean(r.get("phrase", "")), text_key(r.get("phrase", ""))
        fa = clean_persian(r.get("fa", ""))
        if key and " " in key and fa and _PERSIAN.search(fa):
            batch.append((key, lt.phrase_norm(key.split()), phrase, fa, _clean(r.get("source", "")) or default_source))
    db.executemany("INSERT OR IGNORE INTO idioms_fa VALUES (?, ?, ?, ?, ?)", batch)
    return db.execute("SELECT COUNT(*) FROM idioms_fa").fetchone()[0]


# --- A ready-made compact slang table ---------------------------------------------------------
def import_slang_db(db: sqlite3.Connection, path: str) -> int:
    """Copies the `slang` table of a compact SQLite file (the top slang entries, built elsewhere) into the
    database. Column names vary: word / term, definition / meaning, example, up and down votes (up_votes,
    thumbs_up, ...) or a thumbs ratio (0-1) that becomes votes out of 1000."""
    src = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        cols = [r[1].lower() for r in src.execute("PRAGMA table_info(slang)")]
        if not cols:
            raise ValueError(f"{path} has no slang table")
        pick = lambda *names: next((n for n in names if n in cols), None)  # noqa: E731
        word, definition, example = pick("word", "term", "headword"), pick("definition", "meaning", "def"), pick("example", "examples")
        up, down = pick("up", "up_votes", "upvotes", "thumbs_up", "likes"), pick("down", "down_votes", "downvotes", "thumbs_down", "dislikes")
        ratio = pick("ratio", "thumbs_ratio", "score")
        if not (word and definition):
            raise ValueError(f"{path}: the slang table needs word and definition columns")
        select = ", ".join([word, definition, example or "''", up or "NULL", down or "NULL", ratio or "NULL"])
        batch = []
        for w, d, e, u, dn, r in src.execute(f"SELECT {select} FROM slang"):
            w, d = _ud_text(w), _ud_text(d)
            if u is None and r is not None:
                u = round(float(r) * 1000 if float(r) <= 1 else float(r))
                dn = 1000 - u if float(r) <= 1 else 0
            if w and d:
                batch.append((text_key(w), w, d, _ud_text(e), int(u or 0), int(dn or 0)))
    finally:
        src.close()
    db.executescript("""
        CREATE TABLE slang (word_key TEXT NOT NULL, word TEXT NOT NULL, definition TEXT NOT NULL, example TEXT NOT NULL,
                            up INTEGER NOT NULL, down INTEGER NOT NULL);
        CREATE INDEX slang_key ON slang (word_key);
    """)
    db.executemany("INSERT INTO slang VALUES (?, ?, ?, ?, ?, ?)", batch)
    return len(batch)


# --- Driver -----------------------------------------------------------------------------------
def build(out: str, kaikki: Optional[str] = None, tatoeba_dir: Optional[str] = None,
          opensubtitles: Optional[str] = None, urban: Optional[str] = None, slang_db: Optional[str] = None,
          idioms_fa: Optional[str] = None) -> Dict[str, Any]:
    tmp = out + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    stats: Dict[str, Any] = {}
    try:
        db.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; PRAGMA temp_store = FILE;" + SCHEMA)
        if kaikki:
            _say(f"Importing Kaikki.org from {kaikki}")
            with _open_text(kaikki) as f:
                stats["kaikki_entries"] = import_kaikki(db, f)
        if tatoeba_dir:  # before OpenSubtitles: see finish_parallel / LexiconDB.examples
            _say(f"Importing Tatoeba from {tatoeba_dir}")
            stats["tatoeba_pairs"] = import_tatoeba(db, read_tatoeba_pairs(tatoeba_dir))
        if opensubtitles:
            _say(f"Importing OpenSubtitles from {opensubtitles}")
            stats["opensubtitles_pairs"], stats["frequency_words"] = import_opensubtitles(db, read_opus_moses_zip(opensubtitles))
        if slang_db:
            _say(f"Importing the slang table from {slang_db}")
            stats["urban_entries"] = import_slang_db(db, slang_db)
        elif urban:
            _say(f"Importing Urban Dictionary from {urban}")
            stats["urban_entries"] = import_urban(db, read_urban_rows(urban))
        if idioms_fa:
            _say(f"Importing Persian idioms from {idioms_fa}")
            stats["idioms_fa"] = import_idioms_fa(db, read_idioms_fa(idioms_fa))
        finish_parallel(db)
        db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in stats.items()]
                       + [("built_at", time.strftime("%Y-%m-%d")), ("schema_version", SCHEMA_VERSION)])
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
    os.replace(tmp, out)
    return stats


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="ocean_lexicon.db")
    ap.add_argument("--kaikki")
    ap.add_argument("--tatoeba-dir")
    ap.add_argument("--opensubtitles")
    ap.add_argument("--urban")
    ap.add_argument("--slang-db", help="a compact SQLite file with a slang table, used instead of --urban")
    ap.add_argument("--idioms-fa", help="pre-translated English idioms: TSV (phrase, Persian[, source]) or JSON lines")
    args = ap.parse_args(argv)
    if not any((args.kaikki, args.tatoeba_dir, args.opensubtitles, args.urban, args.slang_db, args.idioms_fa)):
        ap.error("give at least one dataset")
    stats = build(args.out, args.kaikki, args.tatoeba_dir, args.opensubtitles, args.urban, args.slang_db, args.idioms_fa)
    _say(f"Built {args.out}: {stats}")


if __name__ == "__main__":
    main()
