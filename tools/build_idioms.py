"""Build the bundled English idiom list (data/idioms_en.tsv.gz) and the Persian-annotated list (data/idioms_fa.tsv.gz).

    python tools/build_idioms.py extract --kaikki kaikki.org-dictionary-English.jsonl.gz --out candidates.jsonl.gz
    python tools/build_idioms.py rank --candidates candidates.jsonl.gz --tatoeba eng_sentences.tsv.bz2 \
        --extra data/idioms_extra_en.tsv --out data/idioms_en.tsv.gz --limit 8000
    python tools/build_idioms.py merge-fa --idioms data/idioms_en.tsv.gz --fa-dir data/idioms_fa_parts \
        --out data/idioms_fa.tsv.gz

- extract: streams the Kaikki English dump (JSON lines, optionally .gz, https://kaikki.org/dictionary/English/, Wiktionary
  text, CC BY-SA 4.0) and keeps multi-word entries that can be idioms: phrases, proverbs, interjections, phrasal / multi-word
  verbs, adverbs, adjectives, prepositions and the compound nouns that carry idiomatic or informal labels. Nothing else
  is kept; the dump itself can be deleted afterwards.
- rank: scores the candidates by how useful they are to a learner (translations into many languages, a readable example,
  how often the phrase occurs in Tatoeba sentences, usage labels), cleans the glosses, adds alias rows for "alternative
  form of" entries and the hand-written --extra rows, and keeps the best --limit rows.
- merge-fa: joins the hand-written Persian files (data/idioms_fa_parts/*.tsv, columns key<TAB>fa) onto the ranked list.

Columns of data/idioms_en.tsv.gz (UTF-8, tab separated, header row, no tabs or newlines inside fields, no quoting):
    phrase, key, base_key, pos, meaning_en, example_en, wikt_fa, rank_score, source
source is "wiktionary" or "curated" (the --extra rows). data/idioms_fa.tsv.gz has
    phrase, key, base_key, meaning_en, example_en, fa, fa_source
fa_source is "claude" (written by Claude from the English meaning), "wiktionary" (taken from Wiktionary) or empty when
there is no Persian yet (fa is empty then too).
"""
import argparse
import bz2
import glob
import gzip
import json
import math
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lexicon_db import clean_persian, text_key  # noqa: E402

IDIOM_POS = {"phrase", "prep_phrase", "proverb", "adv_phrase", "intj"}
# Multi-word entries of these parts of speech are phrasal verbs, idiomatic adverbs and the like; nouns need a label.
OPEN_POS = IDIOM_POS | {"verb", "adj", "adv", "prep", "conj", "pron", "det", "particle"}
SKIPPED_POS = {"name", "character", "symbol", "punct", "prefix", "suffix", "infix", "interfix", "affix", "circumfix",
               "num", "contraction"}
IDIOM_TAGS = {"idiomatic", "idiom"}
# Labels that make a compound noun worth considering as an expression ("cold feet", "blessing in disguise" has none).
NOUN_TAGS = IDIOM_TAGS | {"figuratively", "informal", "slang", "colloquial", "humorous", "euphemistic"}
# Senses carrying one of these labels are dropped (whole entries with only such senses fall out with them).
BAD_TAGS = {"obsolete", "archaic", "rare", "dated", "nonstandard", "dialectal", "Scotland", "Ireland", "Ulster",
            "Northern-England", "Scottish", "Hiberno-English", "Appalachia", "Cockney", "Yorkshire", "Geordie",
            "Jamaica", "India", "Singapore", "Philippines", "South-Africa", "Canada", "obsolete-spelling", "misspelling",
            "proscribed", "hapax-legomenon", "nonce-word", "no-gloss", "uncommon"}
PLACEHOLDER_TOKENS = {"a", "an", "the", "no", "it", "its", "one", "one's", "ones", "oneself", "someone", "someone's",
                      "somebody", "somebody's", "something", "anyone", "anyone's", "anything", "to",
                      "my", "your", "his", "her", "their", "our"}
NON_ENGLISH = re.compile(r"[^\x00-\x7f’‘“”–—…é]")
# Persian from Wiktionary is only used if it is plain Persian script (no Latin letters, no transliteration).
PERSIAN_OK = re.compile(r"^[؀-ۿ\s‌()/،\-]+$")
LITERAL_GLOSS = re.compile(r"^Used other than figuratively or idiomatically", re.I)
STOPWORDS = set("a an the to of in on at by for with from up down out off over i you he she it we they me him her us them "
                "my your his its our their is are am was were be been do does did have has had will would can could shall "
                "should may might must not no and or but if so as that this these those there here what who how when "
                "where why which than then too very just all any some one".split())
CONTRACTION_PARTS = {"gonna", "wanna", "gotta"}
_SPACE = re.compile(r"\s+")


# --- Pure helpers (unit tested in tests/test_build_data.py) -----------------------------------------
def base_key(phrase: str) -> str:
    """Key with the slots of an idiom dropped so "dial it down" and "dial down", "no bed of roses" and "bed of
    roses", "do one's best" and "do my best" meet: lower case key minus articles, "no", "it", one's / someone /
    something / possessives and "to". Falls back to the plain key if fewer than two words would be left."""
    toks = text_key(phrase).split()
    kept = [t for t in toks if t not in PLACEHOLDER_TOKENS]
    return " ".join(kept) if len(kept) >= 2 or (kept and len(toks) == 1) else " ".join(toks)


def clean_gloss(gloss: str) -> str:
    """One short English meaning: markup dropped, first sentence only, at most ~200 characters, capitalised."""
    if "translation hub" in (gloss or ""):
        return ""
    g = re.sub(r"\{\{[^}]*\}\}|\[\[|\]\]", "", gloss or "")
    g = _SPACE.sub(" ", g.replace("\n", " ")).strip()
    g = re.split(r"(?<=[.!?])\s+(?=[A-Z])", g)[0]
    g = g.rstrip(" ;:,")
    if len(g) > 200:
        g = g[:200].rsplit(" ", 1)[0].rstrip(" ,;:(") + "…"
    return g[:1].upper() + g[1:] if g else ""


def clean_example(text: str) -> str:
    t = _SPACE.sub(" ", (text or "").replace("\n", " ").replace("\t", " ")).strip()
    return re.sub(r"\[\d+\]", "", t).strip()


def good_example(text: str) -> bool:
    """A single readable modern sentence: 4-25 words, plain Latin text, no markup leftovers or archaic pronouns."""
    n = len(text.split())
    return 4 <= n <= 25 and len(text) <= 200 and not NON_ENGLISH.search(text) and "[" not in text and "{" not in text \
        and "http" not in text and not re.search(r"\b(?:thou|thee|thy|hath|doth)\b", text, re.I)


def only_function_words(phrase: str) -> bool:
    """"to the", "do you", "want to": phrases made of nothing but function words are grammar, not idioms."""
    return all(t in STOPWORDS or t in CONTRACTION_PARTS for t in text_key(phrase).split())


def tag_set(sense: Dict[str, Any]) -> Set[str]:
    return set(sense.get("tags", [])) | set(sense.get("raw_tags", []))


def classify(entry: Dict[str, Any]) -> Optional[str]:
    """What a Kaikki entry is for the idiom list: None (skip), "alt" (alternative spelling of another entry), "strong"
    (labelled idiom, phrase, proverb, phrasal / multi-word verb ...) or "weak" (unlabelled compound noun, kept only if it
    is frequent in running text)."""
    word = (entry.get("word") or "").strip()
    pos = entry.get("pos")
    if " " not in word or pos in SKIPPED_POS or entry.get("lang_code", "en") != "en" or only_function_words(word):
        return None
    if re.search(r"[0-9/:;&@]|^-|-$|^[A-Z]{2,}\b", word) or len(word.split()) > 8 or len(word) > 60 or word[0].isupper() and pos == "noun":
        return None
    senses = entry.get("senses", [])
    if senses and all("form_of" in s for s in senses):
        return None  # inflections: "breaks the ice"
    if senses and all("alt_of" in s or "form_of" in s for s in senses):
        return "alt"
    if pos in OPEN_POS:
        return "strong"
    for s in senses:
        if tag_set(s) & NOUN_TAGS:
            return "strong"
        for c in s.get("categories", []):
            if (c.get("name", "") if isinstance(c, dict) else str(c)) in ("English catchphrases", "English proverbs", "Internet memes"):
                return "strong"
    return "weak"


def compact_entry(entry: Dict[str, Any], kind: str = "strong") -> Dict[str, Any]:
    """The part of a Kaikki entry the ranking needs, small enough to keep for tens of thousands of entries."""
    if kind == "alt":
        target = ""
        for s in entry.get("senses", []):
            if s.get("alt_of"):
                target = s["alt_of"][0].get("word", "")
                break
        return {"w": entry["word"].strip(), "pos": entry.get("pos"), "kind": "alt", "alt": target, "s": [], "nl": 0,
                "fa": [], "syn": 0, "pv": False}
    senses = []
    limit = 2 if kind == "weak" else 8
    for s in entry.get("senses", []):
        glosses = s.get("glosses") or []
        if any(k in s for k in ("form_of", "alt_of")) or not glosses or LITERAL_GLOSS.match(glosses[0]):
            continue
        exs = [e.get("text", "") for e in s.get("examples", []) if e.get("text") and e.get("type") != "quotation"][:3]
        exs += [e.get("text", "") for e in s.get("examples", []) if e.get("text") and e.get("type") == "quotation"][:1]
        senses.append({"g": glosses[-1] if len(glosses) > 1 and len(glosses[-1]) > 12 else glosses[0],
                       "t": sorted(tag_set(s)), "ex": exs[:4]})
        if len(senses) >= limit:
            break
    pv = any(str(c.get("name", "") if isinstance(c, dict) else c).startswith("English phrasal verbs")
             for s in entry.get("senses", []) for c in s.get("categories", []))
    langs: Set[str] = set()
    fa: List[Any] = []
    all_tr = list(entry.get("translations", []))
    for s in entry.get("senses", []):
        all_tr += s.get("translations", [])
    for t in all_tr:
        code = t.get("lang_code") or t.get("code")
        if code:
            langs.add(code)
        if code == "fa" and t.get("word"):
            fa.append([t.get("sense", ""), t["word"], t.get("lang", ""), sorted(t.get("tags", []) + t.get("raw_tags", []))])
    return {"w": entry["word"].strip(), "pos": entry.get("pos"), "kind": kind, "s": senses, "nl": len(langs),
            "fa": fa[:6], "syn": len(entry.get("synonyms", [])), "pv": pv}


def usable_senses(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Current, non-regional senses; when some of them come with a readable example, only those (examples mark the
    senses editors consider everyday)."""
    senses = [s for s in c["s"] if not set(s["t"]) & BAD_TAGS and clean_gloss(s["g"])]
    with_ex = [s for s in senses if any(good_example(clean_example(e)) for e in s["ex"])]
    return with_ex or senses


def is_eligible(c: Dict[str, Any], freq: int) -> bool:
    """Idiom-like entries always qualify; an unlabelled compound noun ("ice cream", "silver sheet") only if it is
    frequent in text yet has few translations (concrete compounds are translated into many languages, idioms are not)."""
    if not usable_senses(c):
        return False
    if c["kind"] == "weak":
        return freq >= 3 and c["nl"] <= 4
    return True


def entry_score(c: Dict[str, Any], freq: int = 0) -> float:
    """Usefulness of an idiom to a learner. Translations into many languages and corpus frequency dominate; a readable
    example, being labelled idiomatic and proverbs add a little; dated / regional labels subtract."""
    senses = usable_senses(c)
    if not senses:
        return -99.0
    first = senses[0]
    tags = set(first["t"])
    score = min(c["nl"], 40) * 0.25 + 1.2 * math.log1p(freq)
    if any(good_example(clean_example(e)) for s in senses for e in s["ex"]):
        score += 1.5
    if c["pos"] == "proverb":
        score += 0.5
    if tags & IDIOM_TAGS:
        score += 2.0
    elif c["pos"] == "phrase" and not c["pv"]:
        score -= 1.0  # phrasebook sentences ("I'm British") are rarely what is being looked up
    if c["kind"] == "weak":
        score -= 2.5
    if tags & {"offensive", "ethnic-slur", "derogatory"}:
        score -= 3
    if len(c["w"].split()) >= 6:
        score -= 1
    if c["syn"]:
        score += 0.3
    if NON_ENGLISH.search(c["w"]):
        score -= 3
    return score


def meaning_of(c: Dict[str, Any], limit: int = 2) -> str:
    """The 1-2 primary glosses joined with " ; "; senses that merely say "alternative form of" are skipped."""
    out: List[str] = []
    for s in usable_senses(c):
        g = clean_gloss(s["g"])
        if re.match(r"(?i)(alternative|obsolete|dated|eye dialect|nonstandard|abbreviation|initialism)\b", g):
            continue
        if g and g.lower() not in (o.lower() for o in out):
            out.append(g)
        if len(out) >= limit:
            break
    meaning = " ; ".join(o.rstrip(".") for o in out)
    return meaning if len(meaning) <= 300 else (out[0].rstrip(".") if out else "")


def example_of(c: Dict[str, Any], phrase: str) -> str:
    """The shortest readable example (from the first sense that has one) containing the phrase or an inflected form of
    it. Wiktionary lists made-up modern examples before old quotations, so the result is usually modern."""
    variants = surface_variants(phrase, "verb" if c["pos"] == "verb" else "")
    base_words = base_key(phrase).split()
    for s in usable_senses(c):
        best = ""
        for e in s["ex"]:
            t = clean_example(e)
            if not good_example(t):
                continue
            tk = text_key(t)
            hit = any(f" {v} " in f" {tk} " for v in variants) or (len(base_words) > 1 and all(w in tk.split() for w in base_words))
            if hit and (not best or len(t) < len(best)):
                best = t
        if best:
            return best
    return ""


def wiktionary_fa(c: Dict[str, Any]) -> str:
    """Iranian-Persian translations Wiktionary lists for the idiom, plain Persian script only, at most 2."""
    out: List[str] = []
    for _sense, word, lang, tags in c["fa"]:
        w = clean_persian(word)
        if lang == "Dari" or "Afghanistan" in tags or not PERSIAN_OK.match(w) or w in out:
            continue
        out.append(w)
    return " / ".join(out[:2])


def tsv_clean(s: str) -> str:
    return _SPACE.sub(" ", (s or "").replace("\t", " ")).strip()


def write_row(out, cols: List[str]) -> None:
    out.write("\t".join(tsv_clean(c) for c in cols) + "\n")


# --- Frequency from Tatoeba sentences ---------------------------------------------------------------------
_POSSESSIVES = ("my", "your", "his", "her", "its", "our", "their")
_REFLEXIVES = ("myself", "yourself", "himself", "herself", "itself", "ourselves", "themselves", "yourselves")


def _verb_forms(w: str) -> List[str]:
    forms = [w, w + "s", w + "ed", w + "d", w + "ing", w + "es"]
    if w.endswith("e"):
        forms.append(w[:-1] + "ing")
    if w.endswith("y"):
        forms += [w[:-1] + "ies", w[:-1] + "ied"]
    if len(w) >= 3 and w[-1] not in "aeiouwxy" and w[-2] in "aeiou" and w[-3] not in "aeiou":
        forms += [w + w[-1] + "ed", w + w[-1] + "ing"]
    return forms


def surface_variants(phrase: str, pos: str = "") -> Set[str]:
    """Key variants to count in running text: the exact key, one's / oneself filled in, first word inflected for verbs."""
    toks = text_key(phrase).split()
    variants = {" ".join(toks)}
    if pos == "verb" and toks:
        variants |= {" ".join([f] + toks[1:]) for f in _verb_forms(toks[0])}
    out: Set[str] = set()
    for v in variants:
        if "one's" in v.split():
            out |= {v.replace("one's", p) for p in _POSSESSIVES}
        elif "oneself" in v.split():
            out |= {v.replace("oneself", r) for r in _REFLEXIVES}
        else:
            out.add(v)
    return {v for v in out if "someone" not in v and "something" not in v and "somebody" not in v}


def count_phrases(sentences: Iterable[str], wanted: Dict[str, Set[str]], max_n: int = 8) -> Counter:
    """In how many sentences does each wanted phrase occur? wanted maps a phrase to its surface variants."""
    lookup: Dict[str, str] = {}
    for phrase, variants in wanted.items():
        for v in variants:
            lookup.setdefault(v, phrase)
    counts: Counter = Counter()
    for sent in sentences:
        toks = text_key(sent).split()
        seen: Set[str] = set()
        for n in range(2, min(max_n, len(toks)) + 1):
            for i in range(len(toks) - n + 1):
                p = lookup.get(" ".join(toks[i:i + n]))
                if p and p not in seen:
                    seen.add(p)
                    counts[p] += 1
    return counts


# --- Commands ---------------------------------------------------------------------------------------------
def _open_text(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    if path.endswith(".bz2"):
        return bz2.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def cmd_extract(args: argparse.Namespace) -> None:
    kept: Counter = Counter()
    with _open_text(args.kaikki) as src, gzip.open(args.out, "wt", encoding="utf-8") as out:
        for line in src:
            if '"word": "' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            kind = classify(entry)
            if kind:
                out.write(json.dumps(compact_entry(entry, kind), ensure_ascii=False) + "\n")
                kept[kind] += 1
    print(f"candidates {dict(kept)} -> {args.out}")


def _merge_by_word(cands: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One candidate per phrase key: the senses of the same phrase under several parts of speech are joined."""
    merged: Dict[str, Dict[str, Any]] = {}
    for c in cands:
        k = text_key(c["w"])
        if not k:
            continue
        m = merged.get(k)
        if m is None:
            merged[k] = c
        elif c["kind"] == "alt" or m["kind"] == "alt":
            if m["kind"] == "alt" and c["kind"] != "alt":
                merged[k] = c
        else:
            m["s"] += c["s"]
            m["nl"] = max(m["nl"], c["nl"])
            m["fa"] += c["fa"]
            m["syn"] += c["syn"]
            m["pv"] = m["pv"] or c["pv"]
            if c["kind"] == "strong":
                m["kind"] = "strong"
            if m["pos"] != "proverb" and c["pos"] == "proverb":
                m["pos"] = "proverb"
    return merged


def read_extra(path: str) -> List[Dict[str, str]]:
    """Hand-written rows for expressions Wiktionary lacks: phrase, pos, meaning_en, example_en (tab separated, # comments)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 3 or not cols[0].strip() or not cols[2].strip():
                raise SystemExit(f"{path}:{n}: expected phrase<TAB>pos<TAB>meaning_en[<TAB>example_en]")
            rows.append({"phrase": cols[0].strip(), "pos": cols[1].strip() or "phrase", "meaning": cols[2].strip(),
                         "example": cols[3].strip() if len(cols) > 3 else ""})
    return rows


def cmd_rank(args: argparse.Namespace) -> None:
    with _open_text(args.candidates) as f:
        merged = _merge_by_word(json.loads(line) for line in f)
    freq: Counter = Counter()
    if args.tatoeba:
        wanted = {c["w"]: surface_variants(c["w"], "verb" if c["pos"] == "verb" else "") for c in merged.values()}
        with _open_text(args.tatoeba) as f:
            freq = count_phrases((row.split("\t")[-1] for row in f), wanted)
    scored = []
    for c in merged.values():
        if c["kind"] == "alt":
            continue
        meaning = meaning_of(c)
        if meaning and is_eligible(c, freq[c["w"]]):
            scored.append((entry_score(c, freq[c["w"]]), c, meaning))
    scored.sort(key=lambda r: (-r[0], r[1]["w"].lower()))
    scored = [r for r in scored if r[0] >= args.min_score][:args.limit]
    rows: List[List[str]] = []
    seen: Set[str] = set()
    selected = {text_key(c["w"]): (score, c, meaning) for score, c, meaning in scored}
    for score, c, meaning in scored:
        phrase = tsv_clean(c["w"])
        seen.add(text_key(phrase))
        rows.append([phrase, text_key(phrase), base_key(phrase), c["pos"], meaning, example_of(c, phrase),
                     wiktionary_fa(c), f"{score:.2f}", "wiktionary"])
    # "wild goose chase" -> alias of "wild-goose chase": same meaning, a little lower in the ranking
    for c in merged.values():
        if c["kind"] != "alt" or not c.get("alt"):
            continue
        key, target = text_key(c["w"]), selected.get(text_key(c["alt"]))
        if target and key not in seen and not only_function_words(c["w"]):
            score, tc, meaning = target
            seen.add(key)
            rows.append([tsv_clean(c["w"]), key, base_key(c["w"]), tc["pos"], meaning, example_of(tc, c["alt"]),
                         wiktionary_fa(tc), f"{score - 0.5:.2f}", "wiktionary"])
    if args.extra:
        for r in read_extra(args.extra):
            key = text_key(r["phrase"])
            if key in seen:
                continue
            seen.add(key)
            rows.append([r["phrase"], key, base_key(r["phrase"]), r["pos"], r["meaning"], r["example"], "", "30.00", "curated"])
    rows.sort(key=lambda r: (-float(r[7]), r[0].lower()))
    with gzip.open(args.out, "wt", encoding="utf-8", newline="") as out:
        write_row(out, ["phrase", "key", "base_key", "pos", "meaning_en", "example_en", "wikt_fa", "rank_score", "source"])
        for r in rows:
            write_row(out, r)
    print(f"{len(rows)} idioms -> {args.out}")


def read_tsv(path: str) -> Iterator[Dict[str, str]]:
    with _open_text(path) as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            cols = line.rstrip("\n").split("\t")
            yield dict(zip(head, cols + [""] * (len(head) - len(cols))))


def read_fa_parts(fa_dir: str) -> Dict[str, Tuple[str, str]]:
    """key -> (persian, source) from every *.tsv in fa_dir. Lines: key<TAB>fa[<TAB>source], '#' starts a comment."""
    fa: Dict[str, Tuple[str, str]] = {}
    for part in sorted(glob.glob(os.path.join(fa_dir, "*.tsv"))):
        with open(part, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.rstrip("\n")
                if not line.strip() or line.startswith("#"):
                    continue
                cols = line.split("\t")
                if len(cols) < 2 or not cols[1].strip():
                    raise SystemExit(f"{part}:{n}: expected key<TAB>fa[<TAB>source]")
                source = cols[2].strip() if len(cols) > 2 and cols[2].strip() else "claude"
                if source not in ("claude", "wiktionary"):
                    raise SystemExit(f"{part}:{n}: source must be claude or wiktionary")
                fa[cols[0].strip()] = (clean_persian(cols[1]), source)
    return fa


def cmd_merge_fa(args: argparse.Namespace) -> None:
    fa = read_fa_parts(args.fa_dir)
    done = unknown = 0
    keys = set()
    with gzip.open(args.out, "wt", encoding="utf-8", newline="") as out:
        write_row(out, ["phrase", "key", "base_key", "meaning_en", "example_en", "fa", "fa_source"])
        for row in read_tsv(args.idioms):
            keys.add(row["key"])
            text, source = fa.get(row["key"], ("", ""))
            done += bool(text)
            write_row(out, [row["phrase"], row["key"], row["base_key"], row["meaning_en"], row["example_en"], text, source])
    unknown = len([k for k in fa if k not in keys])
    print(f"{done}/{len(keys)} idioms have Persian -> {args.out}"
          + (f" ({unknown} Persian keys match no idiom)" if unknown else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--kaikki", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("rank")
    p.add_argument("--candidates", required=True)
    p.add_argument("--tatoeba", help="eng_sentences.tsv.bz2 from https://downloads.tatoeba.org/exports/per_language/eng/")
    p.add_argument("--extra", help="hand-written rows for expressions Wiktionary lacks")
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=8000)
    p.add_argument("--min-score", type=float, default=0.0)
    p.set_defaults(func=cmd_rank)
    p = sub.add_parser("merge-fa")
    p.add_argument("--idioms", required=True)
    p.add_argument("--fa-dir", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_merge_fa)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
