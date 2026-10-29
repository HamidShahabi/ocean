"""Build ocean_lexicon.db, the local database every lookup reads, from downloaded dataset dumps.

    python tools/build_lexicon_db.py --out ocean_lexicon.db \
        --kaikki kaikki.org-dictionary-English.jsonl.gz \
        --tatoeba-dir tatoeba/ \
        --opensubtitles en-fa.txt.zip \
        [--urban urbandict-word-defs.csv]

Every input is optional; a dataset left out is simply absent from the result (lookups work without it).
The database is written to <out>.building and moved into place only when complete, so an interrupted
build never leaves a half-written file where Ocean looks. Files are streamed, never loaded whole.

- --kaikki: the Kaikki.org English extract (JSON lines, optionally .gz), https://kaikki.org/dictionary/English/
- --tatoeba-dir: eng_sentences.tsv.bz2, pes_sentences.tsv.bz2 and eng-pes_links.tsv.bz2 from
  https://downloads.tatoeba.org/exports/per_language/
- --opensubtitles: the OPUS OpenSubtitles en-fa Moses zip, https://opus.nlpl.eu/OpenSubtitles/en&fa
- --urban: an Urban Dictionary dump, CSV or JSON (lines), with word, definition, up/down vote columns.
  Only entries with upvotes >= 2 x downvotes (and at least one upvote) are kept.
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
from lexicon_db import clean_persian, persian_key, text_key  # noqa: E402

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE entries (id INTEGER PRIMARY KEY, word TEXT NOT NULL, word_key TEXT NOT NULL, pos TEXT NOT NULL,
                      ipa TEXT NOT NULL, senses TEXT NOT NULL, collocations TEXT NOT NULL, derived TEXT NOT NULL);
CREATE TABLE form_of (form_key TEXT NOT NULL, lemma TEXT NOT NULL);
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
SKIPPED_SENSE_TAGS = {"obsolete", "archaic"}
# Usage labels worth showing in front of a definition ("(informal) ...").
SHOWN_TAGS = ["informal", "slang", "colloquial", "formal", "figuratively", "idiomatic", "British", "US",
              "derogatory", "vulgar", "offensive", "humorous", "dated", "rare"]
MAX_SENSES = 10
MAX_DERIVED = 30
PERSIAN_LANGS = {"Persian", "Iranian Persian"}  # Dari (Afghan Persian) also has code "fa"; Ocean targets Iranian usage
URBAN_RATIO = 2  # keep an Urban Dictionary entry only with upvotes >= URBAN_RATIO x downvotes
URBAN_PER_WORD = 3
SUBTITLE_MAX_WORDS = 12
SUBTITLE_MIN_AGREEMENT = 2  # a subtitle rendering counts when at least this many lines agree on it
_PERSIAN = re.compile(r"[؀-ۿ]")
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
    """The phonemic (/.../) transcription, American where one is labelled so."""
    phonemic = [s for s in sounds if str(s.get("ipa", "")).startswith("/")]
    for s in phonemic:
        if {"US", "General-American"} & set(s.get("tags", [])):
            return s["ipa"]
    return phonemic[0]["ipa"] if phonemic else ""


def _is_citation(text: str) -> bool:
    """Text that is really a source reference ("2007, Erin McKean, speech at TED ..."), whatever its type."""
    return bool(re.match(r"\d{4}\b", text))


def pick_example(examples: List[Dict[str, Any]]) -> str:
    """A usage example (not a collocation list), else a short quotation; never a citation."""
    usage = [e for e in examples if e.get("type") != "quotation" and "collocation" not in e.get("tags", [])]
    for e in usage:
        t = _clean(e.get("text", ""))
        if 3 <= len(t.split()) and len(t) <= 200 and not _is_citation(t):
            return t
    for e in examples:
        t = _clean(e.get("text", ""))
        if e.get("type") == "quotation" and 4 <= len(t.split()) and len(t) <= 160 and not _is_citation(t):
            return t
    return ""


def kaikki_entry(row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Tuple[str, str]], List[Dict[str, str]]]:
    """(entry or None, [(form, lemma)], [persian translation]) for one Kaikki JSON line."""
    word = _clean(row.get("word", ""))
    pos_code = row.get("pos", "")
    if not word or row.get("lang_code", "en") != "en" or pos_code in SKIPPED_POS:
        return None, [], []
    pos = POS_LABELS.get(pos_code, pos_code.replace("_", " ").capitalize() or "Entry")
    forms: List[Tuple[str, str]] = []
    senses: List[Dict[str, str]] = []
    collocations: List[str] = []
    translations = list(row.get("translations", []))
    for s in row.get("senses", []):
        translations += s.get("translations", [])
        lemma_refs = s.get("form_of") or s.get("alt_of")
        if lemma_refs:
            forms += [(word, _clean(ref["word"])) for ref in lemma_refs if ref.get("word")]
            continue
        tags = set(s.get("tags", []))
        glosses = s.get("glosses") or []
        if not glosses or tags & SKIPPED_SENSE_TAGS or glosses[-1].startswith("Used other than figuratively"):
            continue
        examples = s.get("examples", [])
        for e in examples:
            if "collocation" in e.get("tags", []):
                collocations += [_clean(p) for p in e.get("text", "").split(";") if 0 < len(_clean(p)) <= 40]
        if len(senses) >= MAX_SENSES:
            continue
        labels = [t for t in SHOWN_TAGS if t in tags]
        gloss = _clean(glosses[-1])  # subsenses list the parent gloss first
        senses.append({"def": (f"({', '.join(labels)}) " if labels else "") + gloss, "example": pick_example(examples)})

    fa_rows = []
    for t in translations:
        if t.get("code") == "fa" and t.get("lang") in PERSIAN_LANGS and t.get("word"):
            fa = clean_persian(t["word"])
            if fa:
                fa_rows.append({"sense": _clean(t.get("sense", "")), "fa": fa})
    entry = None
    if senses:
        derived = list(dict.fromkeys(_clean(d["word"]) for d in row.get("derived", []) if d.get("word")))[:MAX_DERIVED]
        entry = {"word": word, "pos": pos, "ipa": pick_ipa(row.get("sounds", [])), "senses": senses,
                 "collocations": list(dict.fromkeys(collocations)), "derived": derived}
    for r in fa_rows:
        r["pos"] = pos
    return entry, forms, fa_rows


def import_kaikki(db: sqlite3.Connection, lines: Iterable[str]) -> int:
    n = 0
    entries, forms, fas = [], [], []

    def flush() -> None:
        db.executemany("INSERT INTO entries (word, word_key, pos, ipa, senses, collocations, derived) VALUES (?, ?, ?, ?, ?, ?, ?)", entries)
        db.executemany("INSERT INTO form_of (form_key, lemma) VALUES (?, ?)", forms)
        db.executemany("INSERT INTO fa_translations (word, word_key, pos, sense, fa, fa_key) VALUES (?, ?, ?, ?, ?, ?)", fas)
        entries.clear(), forms.clear(), fas.clear()

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
            entries.append((entry["word"], text_key(entry["word"]), entry["pos"], entry["ipa"],
                            json.dumps(entry["senses"], ensure_ascii=False), json.dumps(entry["collocations"], ensure_ascii=False),
                            json.dumps(entry["derived"], ensure_ascii=False)))
        forms += [(text_key(f), lemma) for f, lemma in row_forms]
        fas += [(word, text_key(word), r["pos"], r["sense"], r["fa"], persian_key(r["fa"])) for r in fa_rows]
        if len(entries) >= 5000 or len(fas) >= 5000:
            flush()
            if n % 100000 < 5000:
                _say(f"  Kaikki: {n} entries")
    flush()
    db.executescript("""
        CREATE INDEX entries_key ON entries (word_key);
        CREATE INDEX form_of_key ON form_of (form_key);
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


def import_opensubtitles(db: sqlite3.Connection, pairs: Iterable[Tuple[str, str]]) -> Tuple[int, int]:
    """Short lines only (phrases and everyday sentences), keeping each line's most frequent Persian rendering
    once enough subtitle lines agree on it. Word frequencies are counted on every English line."""
    _create_parallel(db)
    db.execute("CREATE TABLE sub_raw (en_key TEXT, en TEXT, fa TEXT)")
    tokens: Counter = Counter()
    total_tokens = 0
    batch = []
    for i, (en, fa) in enumerate(pairs, 1):
        en = _clean(en.lstrip("-– "))
        line_tokens = _TOKEN.findall(en.lower().replace("’", "'"))
        tokens.update(line_tokens)
        total_tokens += len(line_tokens)
        fa = clean_persian(fa.lstrip("-– "))
        if 1 <= len(en.split()) <= SUBTITLE_MAX_WORDS and len(en) <= 100 and _usable_pair(en, fa):
            batch.append((text_key(en), en, fa))
        if len(batch) >= 20000:
            db.executemany("INSERT INTO sub_raw VALUES (?, ?, ?)", batch)
            batch.clear()
        if i % 1_000_000 == 0:
            _say(f"  OpenSubtitles: {i} lines")
    db.executemany("INSERT INTO sub_raw VALUES (?, ?, ?)", batch)
    db.executescript(f"""
        INSERT INTO parallel (corpus, en, en_key, fa, freq)
        SELECT 'opensubtitles', en, en_key, fa, n FROM (
            SELECT en_key, MIN(en) AS en, fa, COUNT(*) AS n,
                   ROW_NUMBER() OVER (PARTITION BY en_key ORDER BY COUNT(*) DESC, fa) AS rk
            FROM sub_raw GROUP BY en_key, fa
        ) WHERE rk = 1 AND n >= {SUBTITLE_MIN_AGREEMENT} ORDER BY n DESC;
        DROP TABLE sub_raw;
    """)
    db.execute("CREATE TABLE word_freq (word_key TEXT PRIMARY KEY, per_million REAL NOT NULL) WITHOUT ROWID")
    scale = 1_000_000 / max(total_tokens, 1)
    db.executemany("INSERT INTO word_freq VALUES (?, ?)", ((w, round(c * scale, 3)) for w, c in tokens.items() if c >= 3))
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


# --- Driver -----------------------------------------------------------------------------------
def build(out: str, kaikki: Optional[str] = None, tatoeba_dir: Optional[str] = None,
          opensubtitles: Optional[str] = None, urban: Optional[str] = None) -> Dict[str, Any]:
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
        if urban:
            _say(f"Importing Urban Dictionary from {urban}")
            stats["urban_entries"] = import_urban(db, read_urban_rows(urban))
        finish_parallel(db)
        db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in stats.items()] + [("built_at", time.strftime("%Y-%m-%d"))])
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
    args = ap.parse_args(argv)
    if not any((args.kaikki, args.tatoeba_dir, args.opensubtitles, args.urban)):
        ap.error("give at least one dataset")
    stats = build(args.out, args.kaikki, args.tatoeba_dir, args.opensubtitles, args.urban)
    _say(f"Built {args.out}: {stats}")


if __name__ == "__main__":
    main()
