"""Build ocean_slang.db, the compact slang table the lexicon import reads (build_lexicon_db.py --slang-db).

    python tools/build_slang_db.py --csv urbandict-word-defs.csv --out ocean_slang.db --work slang_work.db [--count 12000]

Source: the Urban Dictionary dump "urbandict-word-defs.csv" (word_id, word, up_votes, down_votes, author, definition;
votes as of May 2016; the Kaggle set "Urban Dictionary Words and Definitions", also mirrored on Hugging Face as
opensporks/urban_dict_word_defs). The live API returns 0 votes for everything, so it cannot rank; it is only asked for
the one example sentence each kept definition has (https://api.urbandictionary.com/v0/define?defid=<word_id>).

Three resumable steps, all recorded in the --work database (stop and run the same command again to continue):
  1. select: stream the CSV, drop offensive-only, joke and unusable entries, keep the best-voted definition of each
     term and the top --count terms by thumbs score (up - down);
  2. fetch: one polite request per kept definition (--delay seconds apart, backing off on errors) for its example;
  3. write: ocean_slang.db with one `slang` table (word, definition, example, up_votes, down_votes), written to
     <out>.building and moved into place only when complete.

The filters are heuristics, not moderation: a word list for slurs and vulgarity (see below) and a few joke patterns.
Entries are never edited beyond whitespace, link brackets and length.
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from build_lexicon_db import _ud_text  # noqa: E402  (link brackets out, whitespace squeezed)

API = "https://api.urbandictionary.com/v0/define?defid="
MIN_UP = 100                # fewer up votes: too little agreement to be a good definition
MIN_RATIO = 0.8             # share of up votes
MAX_DEFINITION = 240        # characters of the shown definition
MAX_EXAMPLE = 200
MAX_TERM_WORDS = 4

# Slurs and abuse: an entry whose term or definition holds one is dropped (it is offensive, not merely slang).
SLURS = re.compile(
    r"\b(nigg\w*|fagg?\w*|kikes?|chinks?|spics?|wetbacks?|gooks?|coons?|trann(?:y|ies)|retard\w*|beaners?|towelheads?|"
    r"ragheads?|pakis?|dykes?|negros?|sluts?|whores?|cunts?|rap(?:e|es|ed|ist|ists|ing)|pedo\w*|paedo\w*|molest\w*|"
    r"incest\w*|nazis?|hitler|jihad\w*|kkk)\b", re.IGNORECASE)
# Vulgar words: an entry whose term or definition holds one is dropped (the word itself is the profanity, or the
# definition is crude or a joke; the pool is large enough to lose them).
VULGAR = re.compile(
    r"\b(fuck\w*|shit\w*|bitch\w*|cock\w*|dick\w*|pussy|pussies|tits?|boobs?|ass|asses|arse|piss\w*|cum\w*|jizz\w*|"
    r"wank\w*|bollocks|bastard|twat|slag|skank\w*|hoes?|porn\w*|penis|vagina|anal|dildo|blowjob|handjob|orgasm|"
    r"masturbat\w*|erection|hentai|sex\w*|horny|boner|fap\w*|nude|naked|nsfw)\b", re.IGNORECASE)
# Jokes, insults aimed at the reader, chatter.
JOKE = re.compile(
    r"\b(your|yo|ur|ya) (mom|mum|mama|momma|mother|dad|face)\b|that'?s what she said|\b(lol|lmao|rofl|haha\w*|hehe\w*)\b"
    r"|:-?[)(DP]|\bxD\b|https?:|www\.|@\w+\.\w+|urban ?dictionary|this (definition|word|entry)|^\s*(you are|you're|you |your |u r |ur )", re.IGNORECASE)
# Names of people, bands, brands and characters are not slang: a Title Case phrase, a capitalised single word that
# the definition describes as a person, or a definition that is about an entertainer or a franchise.
ENTERTAINMENT = re.compile(
    r"\b(bands?|singer|guitarist|drummer|bassist|rapper|actor|actress|album|musician|anime|manga|lead vocalist|"
    r"tv show|youtuber|character (in|from)|cigarettes?|film|movie)\b", re.IGNORECASE)
PERSON = re.compile(r"^\W*(an? |the )?(\w+ ){0,2}(girl|guy|man|woman|boy|lad|lady|dude|person|chick|name|female|male)\b", re.IGNORECASE)
_TERM_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-.&]*$")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

WORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS cand (defid INTEGER PRIMARY KEY, word TEXT NOT NULL, definition TEXT NOT NULL,
                                 up INTEGER NOT NULL, down INTEGER NOT NULL, example TEXT NOT NULL DEFAULT '',
                                 fetched INTEGER NOT NULL DEFAULT 0);
"""


def _say(msg: str) -> None:
    print(msg, flush=True)


# --- 1. select ---------------------------------------------------------------------------------
def shorten(text: str, limit: int) -> str:
    """The text itself when it fits, else its leading sentences that do; "" when not even one sentence does."""
    if len(text) <= limit:
        return text
    cut = ""
    for m in _SENTENCE_END.finditer(text[: limit + 1]):
        cut = text[: m.end()].strip()
    return cut


def clean_definition(raw: str) -> str:
    """"" for a definition not worth keeping: too short or too long, abusive, or a joke."""
    text = _ud_text(raw.split(";;")[0] if ";;" in raw else raw)  # ";;" separates the sub-definitions of old entries
    text = shorten(text, MAX_DEFINITION)
    if len(text) < 12 or SLURS.search(text) or VULGAR.search(text) or JOKE.search(text) or (text.isupper() and len(text) > 20):
        return ""
    return text


def is_name(word: str, definition: str) -> bool:
    parts = word.split()
    if len(parts) > 1 and all(p[0].isupper() for p in parts):
        return True
    return (len(parts) == 1 and word[0].isupper() and word[1:].islower() and bool(PERSON.search(definition[:60]))
            or bool(ENTERTAINMENT.search(definition[:120])))


def usable_term(word: str) -> bool:
    return (2 <= len(word) <= 40 and len(word.split()) <= MAX_TERM_WORDS and bool(_TERM_OK.match(word))
            and any(c.isalpha() for c in word) and not SLURS.search(word) and not VULGAR.search(word))


def read_dump(path: str) -> Iterator[Tuple[int, str, int, int, str]]:
    """(defid, word, up, down, definition) of every row of the dump that has the votes to be considered."""
    csv.field_size_limit(10_000_000)
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            try:
                defid, up, down = int(row["word_id"]), int(row["up_votes"]), int(row["down_votes"])
            except (KeyError, TypeError, ValueError):
                continue
            if up >= MIN_UP and up >= MIN_RATIO * (up + down):
                yield defid, (row.get("word") or "").strip(), up, down, row.get("definition") or ""


def select_candidates(csv_path: str, count: int) -> List[Tuple[int, str, str, int, int]]:
    """The top `count` terms by thumbs score, each with its best-voted definition: (defid, word, definition, up, down)."""
    best: Dict[str, Tuple[int, str, str, int, int]] = {}
    for defid, word, up, down, raw in read_dump(csv_path):
        key = word.lower()
        if key in best and up - down <= best[key][3] - best[key][4]:
            continue
        if not usable_term(word):
            continue
        definition = clean_definition(raw)
        if definition and not is_name(word, definition):
            best[key] = (defid, word, definition, up, down)
    ranked = sorted(best.values(), key=lambda r: (-(r[3] - r[4]), r[1].lower()))
    return ranked[:count]


# --- 2. fetch ----------------------------------------------------------------------------------
class Throttled(Exception):
    """The service refused or failed; try again later."""


def api_example(defid: int) -> Optional[str]:
    """The example sentence of a definition from the live API; None when the definition no longer exists. Raises
    Throttled on rate limits, server errors and network trouble."""
    req = urllib.request.Request(API + str(defid), headers={"User-Agent": "ocean-slang-builder (offline dictionary app)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Throttled(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise Throttled(str(e)) from e
    rows = body.get("list") or []
    return str(rows[0].get("example") or "") if rows else None


def clean_example(raw: str, word: str) -> str:
    """The first paragraph of the example when it is short, clean and shows the term; else ""."""
    first = next((p for p in re.split(r"\r?\n+", raw or "") if p.strip()), "")
    text = shorten(_ud_text(first), MAX_EXAMPLE)
    if not text or SLURS.search(text) or VULGAR.search(text) or JOKE.search(text) or word.lower() not in text.lower():
        return ""
    return text


def fetch_examples(work: sqlite3.Connection, fetcher: Callable[[int], Optional[str]] = api_example, delay: float = 0.4,
                   sleep: Callable[[float], None] = time.sleep, max_failures: int = 8) -> int:
    """Fills the example of every candidate not fetched yet, in rank order; returns how many are still missing.
    Backs off (doubling, up to 5 minutes) after a failure and gives up after `max_failures` in a row; a later run
    resumes where this one stopped."""
    todo = work.execute("SELECT defid, word FROM cand WHERE fetched = 0 ORDER BY up - down DESC, word").fetchall()
    failures, done = 0, 0
    for defid, word in todo:
        while True:
            try:
                raw = fetcher(defid)
                break
            except Throttled as e:
                failures += 1
                if failures >= max_failures:
                    _say(f"  giving up for now ({e}); run again to resume")
                    work.commit()
                    return work.execute("SELECT COUNT(*) FROM cand WHERE fetched = 0").fetchone()[0]
                sleep(min(300.0, 5.0 * 2 ** failures))
        failures = 0
        work.execute("UPDATE cand SET example = ?, fetched = 1 WHERE defid = ?", (clean_example(raw or "", word), defid))
        done += 1
        if done % 100 == 0:
            work.commit()
            _say(f"  examples: {done}/{len(todo)}")
        sleep(delay)
    work.commit()
    return 0


# --- 3. write ----------------------------------------------------------------------------------
def write_db(work: sqlite3.Connection, out: str) -> int:
    tmp = out + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    try:
        db.executescript("""
            CREATE TABLE slang (word TEXT NOT NULL, definition TEXT NOT NULL, example TEXT NOT NULL,
                                up_votes INTEGER NOT NULL, down_votes INTEGER NOT NULL);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        db.executemany("INSERT INTO slang VALUES (?, ?, ?, ?, ?)",
                       work.execute("SELECT word, definition, example, up, down FROM cand ORDER BY up - down DESC, word"))
        n = db.execute("SELECT COUNT(*) FROM slang").fetchone()[0]
        db.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("source", "Urban Dictionary dump urbandict-word-defs.csv (votes as of May 2016); examples from the public API"),
            ("built_at", time.strftime("%Y-%m-%d")), ("entries", str(n))])
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
    os.replace(tmp, out)
    return n


def build(csv_path: Optional[str], out: str, work_path: str, count: int = 12000, delay: float = 0.4,
          fetcher: Callable[[int], Optional[str]] = api_example, sleep: Callable[[float], None] = time.sleep) -> int:
    """Runs the three steps; returns the number of entries written, or -1 when the examples are not all fetched yet."""
    work = sqlite3.connect(work_path)
    try:
        work.executescript(WORK_SCHEMA)
        if not work.execute("SELECT 1 FROM cand LIMIT 1").fetchone():
            if not csv_path:
                raise ValueError("--csv is needed for the first run")
            _say(f"Selecting the top {count} terms from {csv_path}")
            work.executemany("INSERT INTO cand (defid, word, definition, up, down) VALUES (?, ?, ?, ?, ?)",
                             select_candidates(csv_path, count))
            work.commit()
        _say(f"{work.execute('SELECT COUNT(*) FROM cand').fetchone()[0]} candidates; fetching the missing examples")
        if fetch_examples(work, fetcher, delay, sleep):
            return -1
        return write_db(work, out)
    finally:
        work.close()


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="urbandict-word-defs.csv (only needed on the first run)")
    ap.add_argument("--out", default="ocean_slang.db")
    ap.add_argument("--work", default="slang_work.db", help="progress database; keep it between runs")
    ap.add_argument("--count", type=int, default=12000, help="how many terms to keep (10000-15000)")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between API requests")
    args = ap.parse_args(argv)
    n = build(args.csv, args.out, args.work, args.count, args.delay)
    _say(f"Wrote {n} entries to {args.out}" if n >= 0 else "Examples still missing: run the same command again to resume")


if __name__ == "__main__":
    main()
