# === SECTION 1: IMPORTS, OFFLINE DATA PATHS & PERSISTENT SESSIONS ===
import os
import re
import sys
import html
import json
import shutil
import logging
import sqlite3
import threading
import contextvars
from collections import OrderedDict
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Iterator, List, Optional, Tuple
import requests
from requests.adapters import HTTPAdapter

import lexicon_db
import offline_slm
import word_levels


def _get_engine_base_dir() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "cefr_levels.json")):
            return exe_dir
        parent = os.path.abspath(os.path.join(exe_dir, "..", ".."))
        if os.path.exists(os.path.join(parent, "cefr_levels.json")):
            return parent
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


def _get_user_data_dir() -> str:
    """Per-user writable folder; the install folder may be read-only (e.g. Program Files)."""
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(root, "Ocean")
    os.makedirs(path, exist_ok=True)
    return path


ENGINE_BASE_DIR = _get_engine_base_dir()
USER_DATA_DIR = _get_user_data_dir()

log = logging.getLogger("ocean")
if not log.handlers:
    _log_handler = RotatingFileHandler(os.path.join(USER_DATA_DIR, "ocean.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    _log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_log_handler)
    log.setLevel(logging.INFO)


def log_uncaught_exceptions() -> None:
    """Send crashes that nothing caught (a Qt slot, a background thread) to ocean.log. With a custom
    sys.excepthook, PyQt also stops aborting the whole app over an exception raised in a slot."""
    def on_main(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def on_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        log.critical("Uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = on_main
    threading.excepthook = on_thread


def user_data_path(filename: str) -> str:
    """Path of a user-written file in USER_DATA_DIR. On first use, copies (never moves) the newest
    copy left next to the app by older versions, which kept user data in the install folder."""
    target = os.path.join(USER_DATA_DIR, filename)
    if os.path.exists(target):
        return target
    legacy = [
        p for p in (
            os.path.join(ENGINE_BASE_DIR, filename),
            os.path.join(ENGINE_BASE_DIR, "dist", "Ocean", filename),
        ) if os.path.isfile(p)
    ]
    if legacy:
        src = max(legacy, key=os.path.getmtime)
        try:
            if filename.endswith(".db"):
                # The backup API also captures writes still sitting in the -wal file.
                s, d = sqlite3.connect(src), sqlite3.connect(target)
                try:
                    s.backup(d)
                finally:
                    s.close()
                    d.close()
            else:
                shutil.copy2(src, target)
            log.info("Migrated %s from %s", filename, src)
        except Exception:
            log.exception("Could not migrate %s from %s; starting fresh (original left untouched)", filename, src)
            if os.path.exists(target):
                os.remove(target)
    return target


# Bump when lookups improve (new sources, better translations, fixed bugs): saved entries produced
# by an older version get fetched again on their next lookup (see storage.is_stale).
# Entries saved before versions were recorded count as version 0.
ENGINE_VERSION = 2  # 2: local datasets (Kaikki, WordNet 3.1, Urban Dictionary, Tatoeba, OpenSubtitles)

USER_MEMORY_FILE = user_data_path("user_memory.json")
CEFR_LEVELS_FILE = os.path.join(ENGINE_BASE_DIR, "cefr_levels.json")
LEXICON_DB_FILE = os.path.join(ENGINE_BASE_DIR, "ocean_lexicon.db")  # built by tools/build_lexicon_db.py
OFFLINE_MT_DIR = os.path.join(ENGINE_BASE_DIR, "offline_mt_model")
OFFLINE_SLM_DIR = os.path.join(ENGINE_BASE_DIR, "offline_slm_model")

try:
    import nltk
    for cand_nltk in (
        os.path.join(ENGINE_BASE_DIR, "nltk_data"),
        os.path.join(os.path.dirname(sys.executable), "nltk_data") if getattr(sys, "frozen", False) else "",
        os.path.join(os.path.dirname(sys.executable), "_internal", "nltk_data") if getattr(sys, "frozen", False) else "",
    ):
        if cand_nltk and os.path.isdir(cand_nltk) and cand_nltk not in nltk.data.path:
            nltk.data.path.insert(0, cand_nltk)
    def _installed(resource: str) -> bool:
        for name in (resource, resource + ".zip"):  # the downloader may leave it zipped
            try:
                nltk.data.find(name)
                return True
            except LookupError:
                pass
        return False

    # WordNet 3.1 when installed (it needs the 3.0 data too, which the lemmatizer uses)
    if _installed("corpora/wordnet31"):
        from nltk.corpus import wordnet31 as wn
    else:
        log.warning("WordNet 3.1 not installed: using WordNet 3.0")
        from nltk.corpus import wordnet as wn
    from nltk.stem import WordNetLemmatizer
    LEMMATIZER = WordNetLemmatizer()
except Exception:
    log.warning("NLTK/WordNet unavailable: morphology and relations disabled", exc_info=True)
    wn = None
    LEMMATIZER = None

try:
    import eng_to_ipa as ipa_engine
except Exception:
    log.warning("eng_to_ipa unavailable: IPA falls back to rule-based G2P", exc_info=True)
    ipa_engine = None

try:
    import ctranslate2
    import sentencepiece as spm
    HAS_CT2_BACKUP = True
except Exception:
    log.warning("ctranslate2/sentencepiece unavailable: no offline translation", exc_info=True)
    HAS_CT2_BACKUP = False

_CT2_TRANSLATOR = None
_SP_PROCESSOR = None
_CT2_IS_NLLB = False  # NLLB models need eng_Latn/pes_Arab language tokens; Marian-style models don't.
_CT2_LOAD_LOCK = threading.Lock()  # a lookup arriving mid-preload waits on this instead of loading twice


def _log_failure(what: str) -> None:
    """Call from an except block: one line for network errors, a traceback for anything
    else (most likely a scraper broken by a site redesign)."""
    exc = sys.exc_info()[1]
    if isinstance(exc, OfflineModeError):
        return  # offline-only mode refusing a request is expected, not a failure
    if isinstance(exc, requests.RequestException):
        log.warning("%s: %s", what, exc.__class__.__name__)
    else:
        log.exception(what)


def load_json_safe(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Keep the unreadable file: the next save would otherwise overwrite it with defaults.
            backup = path + ".corrupt"
            log.exception("Could not read %s; keeping a copy at %s and using defaults", path, backup)
            try:
                shutil.copy2(path, backup)
            except Exception:
                log.exception("Could not back up %s", path)
    return dict(default)


USER_MEMORY = load_json_safe(USER_MEMORY_FILE, {"entries": {}, "overrides": {}})
LEXICON = lexicon_db.LexiconDB(LEXICON_DB_FILE)
SLM = offline_slm.OfflineSLM(OFFLINE_SLM_DIR)


def _load_ct2_model() -> None:
    """Load the CTranslate2 model once; concurrent callers wait for the load in progress."""
    global _CT2_TRANSLATOR, _SP_PROCESSOR, _CT2_IS_NLLB
    with _CT2_LOAD_LOCK:
        if _CT2_TRANSLATOR is not None and _SP_PROCESSOR is not None:
            return
        sp_p = os.path.join(OFFLINE_MT_DIR, "sentencepiece.model")
        md_p = os.path.join(OFFLINE_MT_DIR, "model")
        if not (os.path.exists(sp_p) and os.path.isdir(md_p)):
            return
        sp_proc = spm.SentencePieceProcessor(model_file=sp_p)
        translator = ctranslate2.Translator(md_p, device="cpu", compute_type="int8")
        vocab_p = os.path.join(md_p, "shared_vocabulary.txt")
        if os.path.exists(vocab_p):
            with open(vocab_p, encoding="utf-8") as vf:
                _CT2_IS_NLLB = any(line.strip() == "pes_Arab" for line in vf)
        _SP_PROCESSOR, _CT2_TRANSLATOR = sp_proc, translator


def preload_wordnet() -> threading.Thread:
    """Read WordNet in the background: WordNet 3.1 takes a few seconds to load, which would otherwise
    delay the first lookup."""
    def run() -> None:
        try:
            if wn is not None:
                wn.ensure_loaded()
        except Exception:
            log.exception("Could not preload WordNet")

    thread = threading.Thread(target=run, name="wordnet-preload", daemon=True)
    thread.start()
    return thread


def preload_offline_translator() -> threading.Thread:
    """Load the offline model in the background so the first offline lookup doesn't stall.
    Not a daemon: quitting while the native model is mid-load crashes the process, so
    interpreter shutdown waits for the load to finish (same as the Kokoro voice model)."""
    def run() -> None:
        if not HAS_CT2_BACKUP:
            return
        try:
            _load_ct2_model()
            if _CT2_TRANSLATOR is None:
                log.info("Offline translation model not found in %s", OFFLINE_MT_DIR)
            else:
                log.info("Offline translation model loaded")
        except Exception:
            log.exception("Could not preload the offline translation model")

    thread = threading.Thread(target=run, name="ct2-preload")
    thread.start()
    return thread


def _ct2_translate(text: str, to_english: bool = False) -> str:
    """Raw offline translation with the CTranslate2 model (EN->FA, or FA->EN with an NLLB model)."""
    if not HAS_CT2_BACKUP:
        return ""
    try:
        _load_ct2_model()
        if _CT2_TRANSLATOR is None or _SP_PROCESSOR is None:
            return ""
        tokens = _SP_PROCESSOR.encode(text, out_type=str)
        if _CT2_IS_NLLB:
            src, tgt = ("pes_Arab", "eng_Latn") if to_english else ("eng_Latn", "pes_Arab")
            res = _CT2_TRANSLATOR.translate_batch([[src] + tokens + ["</s>"]], target_prefix=[[tgt]], beam_size=2)
            out_tokens = res[0].hypotheses[0][1:]
        elif to_english:
            return ""  # a Marian-style model translates one direction only
        else:
            res = _CT2_TRANSLATOR.translate_batch([tokens], beam_size=2)
            out_tokens = res[0].hypotheses[0]
        return _SP_PROCESSOR.decode(out_tokens).strip()
    except Exception:
        _log_failure(f"Offline translator failed for {text!r}")
        return ""


def _ct2_translate_many(texts: List[str]) -> List[str]:
    """Offline EN->FA for many strings in one batch (much faster than one at a time); "" for each on failure."""
    if not texts or not HAS_CT2_BACKUP:
        return [""] * len(texts)
    try:
        _load_ct2_model()
        if _CT2_TRANSLATOR is None or _SP_PROCESSOR is None:
            return [""] * len(texts)
        batch = [_SP_PROCESSOR.encode(t, out_type=str) for t in texts]
        if _CT2_IS_NLLB:
            res = _CT2_TRANSLATOR.translate_batch([["eng_Latn"] + toks + ["</s>"] for toks in batch],
                                                  target_prefix=[["pes_Arab"]] * len(batch), beam_size=2)
            return [_SP_PROCESSOR.decode(r.hypotheses[0][1:]).strip() for r in res]
        res = _CT2_TRANSLATOR.translate_batch(batch, beam_size=2)
        return [_SP_PROCESSOR.decode(r.hypotheses[0]).strip() for r in res]
    except Exception:
        _log_failure(f"Offline batch translation of {len(texts)} strings failed")
        return [""] * len(texts)


def translate_offline_backup(text: str, is_short: bool = False) -> str:
    """The offline NLLB translator, normalized; "" without the model."""
    clean = text.strip()
    if not clean:
        return ""
    decoded = _ct2_translate(clean)
    return normalize_persian(decoded, is_short_term=is_short, source_en=clean) if decoded else ""


def save_user_memory() -> None:
    # Write-then-rename so a crash mid-save can't leave a truncated file.
    tmp = USER_MEMORY_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(USER_MEMORY, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USER_MEMORY_FILE)
    except Exception:
        log.exception("Could not save %s", USER_MEMORY_FILE)


def record_user_override(en_text: str, fa_text: str) -> None:
    key = en_text.strip().lower()
    if key and fa_text.strip():
        USER_MEMORY.setdefault("overrides", {})[key] = fa_text.strip()
        save_user_memory()


def record_taught_entry(term: str, fa_equiv: str, pos: str, eng_def: str, ex_en: str, ex_fa: str) -> None:
    key = term.strip().lower()
    if not key:
        return
    USER_MEMORY.setdefault("entries", {})[key] = {
        "headword": term.strip(),
        "fa": fa_equiv.strip(),
        "pos": pos.strip() or "Entry",
        "def": eng_def.strip(),
        "example_en": ex_en.strip(),
        "example_fa": ex_fa.strip(),
    }
    if fa_equiv.strip():
        USER_MEMORY.setdefault("overrides", {})[key] = fa_equiv.strip()
    save_user_memory()


def delete_taught_entry(term: str) -> None:
    key = term.strip().lower()
    if key in USER_MEMORY.get("entries", {}):
        del USER_MEMORY["entries"][key]
        save_user_memory()


def apply_user_overrides(data: Dict[str, Any], overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Put your manual Persian fixes (USER_MEMORY overrides, keyed by lower-case English text) back
    into a freshly fetched entry. Lookups already honour overrides for translations they make
    themselves; this also covers Persian that came ready-made from a dictionary source (e.g. example
    sentences), so refreshing an entry never undoes a hand edit. Changes `data` in place."""
    if overrides is None:
        overrides = USER_MEMORY.get("overrides", {})
    if not overrides:
        return data

    def fix(en: Any) -> str:
        return overrides.get(en.strip().lower(), "") if isinstance(en, str) else ""

    if not data.get("is_reverse"):
        fa = fix(data.get("headword"))
        if fa:
            data["persian_equivalent"] = fa
    for s in data.get("senses", []):
        fa = fix(s.get("example_en"))
        if fa:
            s["example_fa"] = fa
    for grp in ("collocations", "synonyms", "antonyms", "sentence_breakdown"):
        for item in data.get(grp, []):
            fa = fix(item.get("term"))
            if fa:
                item["fa"] = fa
            fa = fix(item.get("example_en"))
            if fa:
                item["example_fa"] = fa
    return data


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
}

POOLED_SESSION = requests.Session()
POOLED_SESSION.headers.update(HEADERS)
_adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=1)
POOLED_SESSION.mount("https://", _adapter)
POOLED_SESSION.mount("http://", _adapter)

DIRECT_SESSION = requests.Session()
DIRECT_SESSION.trust_env = False
DIRECT_SESSION.headers.update(HEADERS)
DIRECT_SESSION.mount("https://", _adapter)
DIRECT_SESSION.mount("http://", _adapter)

STOPWORDS = {
    "the", "a", "an", "of", "in", "to", "for", "with", "on", "at", "from", "by",
    "about", "as", "into", "like", "through", "after", "over", "between", "out",
    "against", "during", "without", "before", "under", "around", "among", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "will", "would", "shall", "should", "can", "could", "may",
    "might", "must", "that", "this", "these", "those", "it", "its", "they",
    "them", "their", "he", "him", "his", "she", "her", "we", "us", "our", "you",
    "your", "i", "me", "my", "or", "and", "but", "if", "so", "than", "too", "very",
    "such", "more", "most", "other", "some", "any", "no", "not", "only", "own",
    "little", "real", "less", "good", "bad", "new", "old", "big", "small", "great",
    "time", "line", "one", "each", "within", "it's", "its"
}

WEAK_SYNONYMS = {
    "word", "treatment", "thing", "matter", "item", "part", "piece", "act",
    "state", "talk", "talking", "tattle", "sing", "blab", "peach", "babble",
    "short cuts", "press", "gad", "jazz around", "belate", "clingy", "cosy",
    "bantam", "flyweight", "puny"
}

PROTECTED_ROOT_WORDS = {
    "discuss", "discover", "display", "disease", "dispute", "disturb", "distribute",
    "dispose", "distract", "distant", "distinct", "distinguish", "repeat", "report",
    "represent", "require", "request", "research", "reside", "resign", "resist",
    "resolve", "respect", "respond", "restore", "strain", "strict", "retain",
    "retire", "reveal", "revenge", "reverse", "review", "revise", "prepare",
    "prefer", "predict", "prevent", "prevail", "preserve", "preside", "pretend",
    "design", "desire", "deserve", "destroy", "detail", "detect", "determine",
    "develop", "device", "devote", "deliberate", "deliver", "demand", "demonstrate",
    "denote", "deny", "depart", "depend", "depict", "deploy", "deposit", "depress",
    "deprive", "derive", "describe", "desert", "include", "increase", "indicate",
    "inform", "insist", "inspect", "inspire", "install", "instruct", "intend",
    "interest", "invent", "invest", "invite", "involve", "express", "explain", "explore"
}

FALSE_BOUND_STEMS = {
    "cuss", "ease", "play", "cover", "count", "tract", "pose", "part", "port",
    "fine", "side", "sent", "sign", "serve", "ceive", "spect", "ject", "duct",
    "mit", "pel", "scribe", "sist", "stain", "tain", "vert", "vide", "vise",
    "peat", "liberate", "liver", "mand", "note"
}

NOT_FOUND_FA = "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f"  # shown when no translation was found
INVALID_FA_HEADERS = {
    "\u0645\u0639\u0646\u06cc", "\u0645\u0639\u0627\u0646\u06cc", "\u062a\u0631\u062c\u0645\u0647",
    "\u062a\u0644\u0641\u0638", "\u0645\u062a\u0631\u0627\u062f\u0641", "\u0641\u0631\u0647\u0646\u06af \u0644\u063a\u062a",
    "\u0627\u0635\u0637\u0644\u0627\u062d", "\u06a9\u0644\u0645\u0647", "\u0639\u0628\u0627\u0631\u062a",
    "\u06af\u0627\u062f", "\u0633\u0627\u0646\u06af", "\u0628\u06cc \u0644\u06cc\u062a", "\u067e\u0644\u0627\u062a\u0648\u0646",
    "\u06af\u0648\u067e", "\u06af\u0627\u0646\u06a9", "\u06af\u0648"
}

# === SECTION 2: MORPHOLOGICAL AFFIX RULES & CONTRACTION / PRONOUN IPA MAP ===
PREFIX_RULES: List[Tuple[str, str]] = [
    ("inter", "between or among"), ("trans", "across or beyond"), ("super", "above or beyond"),
    ("under", "below or insufficient"), ("counter", "against or opposite"), ("over", "excessive or above"),
    ("anti", "against or opposing"), ("auto", "self or automatic"), ("post", "after or behind"),
    ("semi", "half or partly"), ("multi", "many or multiple"), ("non", "not or absence of"),
    ("pre", "before or prior to"), ("sub", "under or secondary"), ("mis", "wrongly or badly"),
    ("dis", "not or opposite of"), ("un", "not or reversal"), ("re", "again or back"),
    ("in", "not or into"), ("im", "not or into"), ("il", "not"), ("ir", "not"),
    ("de", "reversal or away"), ("co", "together or jointly"),
]

SUFFIX_RULES: List[Tuple[str, str, str]] = [
    ("ation", "Noun", "action, process, or result"), ("ition", "Noun", "action or state"),
    ("sion", "Noun", "state, condition, or process"), ("tion", "Noun", "action, process, or result"),
    ("ment", "Noun", "action or resulting state"), ("ness", "Noun", "state, quality, or condition"),
    ("able", "Adjective", "capable of or worthy of"), ("ible", "Adjective", "capable of being"),
    ("ious", "Adjective", "characterized by or full of"), ("less", "Adjective", "without or lacking"),
    ("ship", "Noun", "state, office, or skill"), ("ence", "Noun", "action, state, or quality"),
    ("ance", "Noun", "action, state, or quality"), ("ity", "Noun", "quality, state, or degree"),
    ("ive", "Adjective", "tending to or having the nature of"), ("ous", "Adjective", "full of or possessing"),
    ("ful", "Adjective", "full of or characterized by"), ("ize", "Verb", "cause to become"),
    ("ise", "Verb", "cause to become"), ("ify", "Verb", "make or become"),
    ("ate", "Verb / Adj", "cause to become"), ("ing", "Gerund / Participle", "ongoing action or process"),
    ("ed", "Past / Adjective", "completed action or state"), ("al", "Adjective / Noun", "relating to"),
    ("ic", "Adjective", "pertaining to"), ("er", "Noun", "person or thing performing an action"),
    ("or", "Noun", "person or thing performing an action"), ("ly", "Adverb", "in a specified manner"),
    ("ion", "Noun", "action, process, or state"),
]

WEAK_FORM_IPA = {
    "a": "\u0259", "an": "\u0259n", "the": "\u00f0\u0259", "to": "t\u0259",
    "of": "\u0259v", "was": "w\u0259z", "were": "w\u0259r", "her": "h\u0259r",
    "for": "f\u0259r", "and": "\u0259nd", "that": "\u00f0\u0259t",
    "us": "\u028cs", "it": "\u026at", "in": "\u026an", "is": "\u026az",
    "don't": "do\u028ant", "doesn't": "\u02c8d\u028cz\u0259nt", "didn't": "\u02c8d\u026ad\u0259nt",
    "won't": "wo\u028ant", "can't": "k\u00e6nt", "couldn't": "\u02c8k\u028ad\u0259nt",
    "shouldn't": "\u02c8\u0283\u028ad\u0259nt", "wouldn't": "\u02c8w\u028ad\u0259nt",
    "isn't": "\u02c8\u026az\u0259nt", "aren't": "\u0251rnt", "wasn't": "\u02c8w\u028cz\u0259nt",
    "weren't": "w\u025crnt", "haven't": "\u02c8h\u00e6v\u0259nt", "hasn't": "\u02c8h\u00e6z\u0259nt",
    "hadn't": "\u02c8h\u00e6d\u0259nt", "i'm": "a\u026am", "i'll": "a\u026al",
    "i've": "a\u026av", "i'd": "a\u026ad", "you're": "j\u028ar", "you'll": "jul",
    "you've": "juv", "you'd": "jud", "we're": "w\u026ar", "we'll": "wil",
    "we've": "wiv", "they're": "\u00f0\u025br", "they'll": "\u00f0e\u026al",
    "they've": "\u00f0e\u026av", "he's": "hiz", "she's": "\u0283iz", "it's": "\u026ats",
    "that's": "\u00f0\u00e6ts", "there's": "\u00f0\u025brz", "what's": "w\u028cts",
    "let's": "l\u025bts",
}


# === SECTION 3: NETWORK, SENTENCE AUTO-CORRECTION & SANITIZATION UTILITIES ===
_OFFLINE_ONLY = False  # Settings > Offline-only mode: no network request of any kind while set


class OfflineModeError(requests.ConnectionError):
    """smart_request refused a request because offline-only mode is on."""


def set_offline_only(enabled: bool) -> None:
    global _OFFLINE_ONLY
    _OFFLINE_ONLY = bool(enabled)


def is_offline_only() -> bool:
    return _OFFLINE_ONLY


def _send_request(method: str, url: str, **kwargs) -> requests.Response:
    """Unblocked hybrid request: tries normal pooled session first, then direct session."""
    kwargs.setdefault("timeout", 3.0)
    try:
        return POOLED_SESSION.request(method, url, **kwargs)
    except Exception:
        return DIRECT_SESSION.request(method, url, **kwargs)


class _InFlight:
    """One request being made right now; identical requests from other threads wait for its outcome."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.response: Optional[requests.Response] = None
        self.error: Optional[BaseException] = None


class RequestCache:
    """Answers identical network requests made during one lookup from memory.

    Thread-safe; identical requests running at the same moment share one call. Only successful
    responses are kept (a failed or rate-limited one is shared with the callers already waiting for it,
    never remembered), at most `max_entries` of them, the least recently used going first.
    POST is keyed by its full body, so only the very same request is ever answered from here."""

    CACHEABLE_METHODS = ("GET", "HEAD", "POST")
    KEY_KWARGS = ("params", "data", "json", "headers", "timeout", "allow_redirects")
    MAX_BODY_BYTES = 1_000_000  # a response bigger than this is passed on but not kept

    def __init__(self, max_entries: int = 128) -> None:
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
        self._entries: "OrderedDict[Any, requests.Response]" = OrderedDict()
        self._in_flight: Dict[Any, _InFlight] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @staticmethod
    def _freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return ("dict", tuple(sorted(((str(k), RequestCache._freeze(v)) for k, v in value.items()), key=repr)))
        if isinstance(value, (list, tuple)):
            return ("seq", tuple(RequestCache._freeze(v) for v in value))
        if isinstance(value, (str, bytes, int, float, bool)) or value is None:
            return value
        raise TypeError("not part of a cache key")

    @classmethod
    def make_key(cls, method: str, url: str, kwargs: Dict[str, Any]) -> Optional[Any]:
        """None when the request must not be cached (unknown arguments, streaming, odd bodies...)."""
        if method.upper() not in cls.CACHEABLE_METHODS:
            return None
        parts = []
        for name, value in kwargs.items():
            if name == "timeout":
                continue  # how long to wait does not change the answer
            if name not in cls.KEY_KWARGS:
                return None
            try:
                parts.append((name, cls._freeze(value)))
            except TypeError:
                return None
        return (method.upper(), url, tuple(sorted(parts, key=repr)))

    def request(self, method: str, url: str, send=_send_request, **kwargs) -> requests.Response:
        key = self.make_key(method, url, kwargs)
        if key is None:
            return send(method, url, **kwargs)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._entries.move_to_end(key)
                self.hits += 1
                return hit
            flight = self._in_flight.get(key)
            leader = flight is None
            if leader:
                flight = self._in_flight[key] = _InFlight()
                self.misses += 1
            else:
                self.hits += 1
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.response
        try:
            response = send(method, url, **kwargs)
            flight.response = response
            if self._worth_keeping(response):
                with self._lock:
                    self._entries[key] = response
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            return response
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._in_flight.pop(key, None)
            flight.done.set()

    @classmethod
    def _worth_keeping(cls, response: Any) -> bool:
        try:
            return bool(response.ok) and len(response.content) <= cls.MAX_BODY_BYTES
        except Exception:
            return False


# The cache of the lookup running in this context, if any. Each lookup (a QThread of its own in the
# app) sets its own, so lookups never share one; worker threads started inside a lookup must be
# given the context with submit_in_context() or they will not see it.
_LOOKUP_CACHE: "contextvars.ContextVar[Optional[RequestCache]]" = contextvars.ContextVar("lookup_cache", default=None)


@contextmanager
def lookup_request_cache(max_entries: int = 128) -> Iterator[RequestCache]:
    """Every smart_request() inside the block, worker threads included, shares one cache that is
    dropped when the block ends. Nested blocks reuse the outer cache."""
    existing = _LOOKUP_CACHE.get()
    if existing is not None:
        yield existing
        return
    cache = RequestCache(max_entries)
    token = _LOOKUP_CACHE.set(cache)
    try:
        yield cache
    finally:
        _LOOKUP_CACHE.reset(token)


def submit_in_context(pool: ThreadPoolExecutor, fn, *args, **kwargs):
    """pool.submit() that runs fn in a copy of the caller's context (executor threads do not inherit it)."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def smart_request(method: str, url: str, **kwargs) -> requests.Response:
    """Network call used by everything: answered from the running lookup's cache when it has one.
    The single choke point for the network: offline-only mode stops every request here, cache or not."""
    if _OFFLINE_ONLY:
        raise OfflineModeError(f"Offline-only mode is on; not contacting {url}")
    cache = _LOOKUP_CACHE.get()
    if cache is None:
        return _send_request(method, url, **kwargs)
    return cache.request(method, url, _send_request, **kwargs)


def normalize_persian(text: str, is_short_term: bool = False, source_en: str = "") -> str:
    if not text:
        return ""
    t = html.unescape(str(text))
    t = t.replace("\r", " ").replace("\n", " ")
    t = re.sub(r"[\u064a\u0649]", "\u06cc", t)
    t = re.sub(r"\u0643", "\u06a9", t)
    t = re.sub(r"\u200c{2,}", "\u200c", t)
    t = re.sub(r"^[0-9\u06f0-\u06f9]+[\.\-\)\s]+", "", t)
    t = re.sub(r"[\.\-\(\s]+[0-9\u06f0-\u06f9]+$", "", t)
    t = re.sub(r"\s*[0-9\u06f0-\u06f9]+\.\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if is_short_term:
        t = t.rstrip(".\u06d4!?\u061f;:\u061b").strip()
        if re.search(r"[a-zA-Z]{2,}", t) or t in INVALID_FA_HEADERS:
            return ""
        if t.startswith("\u0631\u0627 ") and len(source_en.split()) == 1:
            return ""
    return t


def auto_correct_english_input(text: str, is_sentence: bool) -> str:
    s = re.sub(r"[\u2018\u2019\u02bc]", "'", text.strip())
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([.,!?;:])", r"\1", s)
    if not is_sentence:
        return s
    s = re.sub(r"\bi\b", "I", s)
    s = re.sub(r"\bi'(m|ll|ve|d)\b", lambda m: "I'" + m.group(1), s, flags=re.IGNORECASE)
    parts = re.split(r"([.!?]\s+)", s)
    corrected_parts = []
    for p in parts:
        if p and p[0].isalpha():
            corrected_parts.append(p[0].upper() + p[1:])
        else:
            corrected_parts.append(p)
    s = "".join(corrected_parts)
    if s and not s.endswith((".", "!", "?")):
        s += "."
    return s


def clean_english_sentence(text: str) -> str:
    if not text:
        return ""
    s = text.strip()
    s = re.sub(r"[\u2018\u2019\u02bc]", "'", s)
    s = re.sub(r"\(\s*=[^)]+\)", "", s)
    s = re.sub(r"\[=[^\]]+\]", "", s)
    s = re.sub(r"\b(\w+)\s*/\s*\w+\b", r"\1", s)
    s = re.sub(r"--+", " \u2014 ", s)
    s = re.sub(r"\s+([.,!?;:])", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s and not s.endswith((".", "!", "?")):
        s += "."
    return s[0].upper() + s[1:] if s else ""


def sentence_contains_target(sentence: str, target: str) -> bool:
    s_low = sentence.lower()
    words = [w.lower() for w in re.findall(r"[a-zA-Z]+", target) if w.lower() not in STOPWORDS and len(w) >= 3]
    if not words:
        return True
    matches = sum(1 for w in words if w[:4] in s_low)
    return matches >= max(1, len(words) - 1)


def is_spelling_or_root_duplicate(candidate: str, headword: str, root_lemma: str) -> bool:
    c = candidate.strip().lower()
    h = headword.strip().lower()
    r = root_lemma.strip().lower()
    if c in (h, r):
        return True
    norm_c = re.sub(r"is(e|ed|ing|ation)$", r"iz\1", c).replace("our", "or")
    norm_h = re.sub(r"is(e|ed|ing|ation)$", r"iz\1", h).replace("our", "or")
    norm_r = re.sub(r"is(e|ed|ing|ation)$", r"iz\1", r).replace("our", "or")
    if norm_c in (norm_h, norm_r):
        return True
    if len(norm_r) >= 5 and norm_c.startswith(norm_r[:5]):
        return True
    return False


# === SECTION 4: GUARANTEED WORD & FULL-SENTENCE US IPA ENGINE ===
def fallback_word_g2p(word: str) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    rules = [
        ("tion", "\u0283\u0259n"), ("sion", "\u0292\u0259n"), ("ough", "\u0254\u02d0"),
        ("igh", "a\u026a"), ("ph", "f"), ("sh", "\u0283"), ("ch", "t\u0283"),
        ("th", "\u03b8"), ("ng", "\u014b"), ("qu", "kw"), ("ck", "k"),
        ("ee", "i\u02d0"), ("ea", "i\u02d0"), ("oo", "u\u02d0"), ("ou", "a\u028a"),
        ("ow", "o\u028a"), ("ai", "e\u026a"), ("ay", "e\u026a"), ("oi", "\u0254\u026a"),
        ("oy", "\u0254\u026a"), ("ar", "\u0251r"), ("er", "\u0259r"), ("ir", "\u025cr"),
        ("or", "\u0254r"), ("ur", "\u025cr"), ("a", "\u00e6"), ("e", "\u025b"),
        ("i", "\u026a"), ("o", "\u0251"), ("u", "\u028c"), ("y", "i"), ("c", "k"), ("j", "d\u0292"),
    ]
    out = w
    for src, dst in rules:
        out = out.replace(src, dst)
    return out


def convert_token_to_ipa(token: str) -> str:
    norm_tok = re.sub(r"[\u2018\u2019\u02bc]", "'", token)
    clean = re.sub(r"[^a-zA-Z\-']", "", norm_tok).strip("-'")
    if not clean:
        return ""
    low = clean.lower()
    if low in WEAK_FORM_IPA:
        return WEAK_FORM_IPA[low]
    if low.endswith("in'") and len(low) > 4:
        low = low[:-1] + "g"
    if "'" in low:
        parts = low.split("'")
        base_ipa = convert_token_to_ipa(parts[0])
        if len(parts) > 1 and parts[1] == "s":
            return base_ipa + "z"
        return base_ipa
    if "-" in low:
        parts = [convert_token_to_ipa(p) for p in low.split("-") if p]
        return "-".join(p for p in parts if p)
    if ipa_engine:
        try:
            res = ipa_engine.convert(low)
            if res and "*" not in res:
                return res
        except Exception:
            pass
    known = LEXICON.ipa(low)  # Wiktionary covers many words the CMU-based engine doesn't
    if known:
        return known.strip("/").split(",")[0].strip()
    return fallback_word_g2p(low)


def to_ipa_us(text: str) -> str:
    clean = re.sub(r"[\u2018\u2019\u02bc]", "'", text.strip())
    if not clean:
        return "/-/"
    tokens = re.findall(r"[a-zA-Z]+(?:'[a-zA-Z]+)?", clean)
    if not tokens:
        return "/-/"
    converted = [convert_token_to_ipa(tok) for tok in tokens]
    joined = " ".join(c for c in converted if c)
    return f"/{joined}/" if joined else "/-/"


def sentence_to_ipa(sentence: str) -> str:
    norm_sent = re.sub(r"[\u2018\u2019\u02bc]", "'", sentence)
    tokens = re.findall(r"[a-zA-Z]+(?:'[a-zA-Z]+)?", norm_sent)
    if not tokens:
        return ""
    ipa_words = [convert_token_to_ipa(tok) for tok in tokens]
    joined = " ".join(w for w in ipa_words if w)
    return f"/{joined}/" if joined else ""


def clean_ipa_display(ipa_str: str) -> str:
    if not ipa_str or ipa_str == "/-/":
        return "/-/"
    s = re.sub(r"[\u0300-\u036f\u0329\u030d]", "", ipa_str)
    s = s.replace(".", "\u00b7").strip("/")
    return f"/{s}/"


# === SECTION 5: PRECISION MORPHOLOGY, ROOT, AFFIX & POS FAMILY ANALYZER ===
def _is_valid_english_word(word: str) -> bool:
    if not word or len(word) < 3 or not wn:
        return False
    return bool(wn.synsets(word))


def _find_true_morphological_root(word: str) -> Tuple[str, str]:
    low = word.lower().strip()
    if low in PROTECTED_ROOT_WORDS:
        return low, "None (Base Word)"
    if not wn:
        return low, "None"

    if low.endswith("ssion") and len(low) > 6:
        cand = low[:-3]
        if _is_valid_english_word(cand):
            return cand, "-ion  [Noun: action, process, or state]"

    for suf, pos_tag, meaning in SUFFIX_RULES:
        if low.endswith(suf) and len(low) - len(suf) >= 3:
            stem = low[:-len(suf)]
            candidates = [
                stem,
                stem + "e",
                stem[:-1] if len(stem) >= 4 and stem[-1] == stem[-2] else "",
                stem[:-1] + "y" if stem.endswith("i") else "",
                stem + "t" if suf == "tion" else "",
                stem[:-1] + "d" if suf == "sion" and stem.endswith("s") else "",
            ]
            for c in candidates:
                if c and _is_valid_english_word(c) and c[:3] == low[:3]:
                    if low == "discussion" and c == "discus":
                        continue
                    return c, f"-{suf}  [{pos_tag}: {meaning}]"

    if LEMMATIZER:
        for pos in ("v", "n", "a", "r"):
            lem = LEMMATIZER.lemmatize(low, pos=pos)
            if lem != low and len(lem) >= 3:
                return lem, "Inflected form"

    return low, "None (Base Word)"


def analyze_morphology(term: str) -> Dict[str, Any]:
    clean = term.strip()
    words = clean.split()
    if len(words) > 1 or not clean.isalpha():
        return {"root": clean, "structure": clean, "prefix": "None", "suffix": "None", "family": []}

    low = clean.lower()
    root, detected_suffix = _find_true_morphological_root(low)

    detected_prefix = "None"
    if root not in PROTECTED_ROOT_WORDS and low not in PROTECTED_ROOT_WORDS:
        for pref, meaning in PREFIX_RULES:
            if root.startswith(pref) and len(root) - len(pref) >= 4:
                rem = root[len(pref):]
                if rem not in FALSE_BOUND_STEMS and _is_valid_english_word(rem):
                    detected_prefix = f"{pref}-  ({meaning})"
                    root = rem
                    break

    parts = []
    if detected_prefix != "None":
        parts.append(f"{detected_prefix.split()[0]} (Prefix)")
    parts.append(f"{root} (Root)")
    if detected_suffix not in ("None", "None (Base Word)", "Inflected form"):
        parts.append(f"{detected_suffix.split()[0]} (Suffix)")
    structure_str = "  +  ".join(parts) + f"   \u2192   {low}" if len(parts) > 1 else f"{low} (Base Root Word)"

    family: List[Dict[str, str]] = []
    seen_words = set()
    pos_map = {"n": "Noun", "v": "Verb", "a": "Adjective", "s": "Adjective", "r": "Adverb"}
    root_prefix = root[:4].lower()

    if wn:
        candidate_forms = [
            root, low,
            root + "ion", root + "ation", root + "ment", root + "ness",
            root + "er", root + "or", root + "ive", root + "able",
            root + "al", root + "ful", root + "less", root + "ly",
            (root[:-1] + "ion") if root.endswith("e") else "",
            (root[:-1] + "ive") if root.endswith("e") else "",
            (root[:-1] + "able") if root.endswith("e") else "",
        ]
        for cf in candidate_forms:
            if not cf or cf in seen_words:
                continue
            syns = wn.synsets(cf)
            if syns:
                for s in syns:
                    for lem in s.lemmas():
                        w_name = lem.name().replace("_", " ").lower()
                        if w_name == cf and w_name.startswith(root_prefix) and w_name not in ("discus", "cuss"):
                            seen_words.add(w_name)
                            family.append({"pos": pos_map.get(s.pos(), "Word"), "word": w_name})
                            break
                    if cf in seen_words:
                        break

        for seed in (root, low):
            for syn in wn.synsets(seed)[:4]:
                for lem in syn.lemmas():
                    if not lem.name().lower().startswith(root_prefix):
                        continue
                    for rel in lem.derivationally_related_forms():
                        rw = rel.name().replace("_", " ").lower()
                        if rw.startswith(root_prefix) and rw not in seen_words and rw not in ("discus", "cuss") and rw.isalpha():
                            seen_words.add(rw)
                            family.append({"pos": pos_map.get(rel.synset().pos(), "Word"), "word": rw})

    return {
        "root": root,
        "structure": structure_str,
        "prefix": detected_prefix,
        "suffix": detected_suffix,
        "family": family[:6],
    }


# === SECTION 6: TWO-TIER ENGLISH -> PERSIAN TRANSLATOR ===
def clean_definition_for_idiom_meaning(def_text: str) -> str:
    s = def_text.strip().rstrip(".")
    s = re.sub(r"^(To|Being|Feeling|Something that is|A|An)\s+", "", s, flags=re.IGNORECASE)
    s = re.split(r"[;:]|\s+,\s+or\s+|\s+so that\s+|\s+because\s+", s)[0].strip()
    return s


def _clients5(text: str, source: str, target: str) -> str:
    """Raw single-best translation from Google's dictionary-extension endpoint."""
    if _OFFLINE_ONLY:
        return ""
    try:
        r = smart_request(
            "GET",
            "https://clients5.google.com/translate_a/t",
            params={"client": "dict-chrome-ex", "sl": source, "tl": target, "q": text},
            timeout=3.0,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list) and data:
                first = data[0]
                return (first[0] if isinstance(first, list) else str(first)).strip()
    except Exception:
        _log_failure(f"Google clients5 translation ({source}->{target}) failed for {text!r}")
    return ""


def translate_via_clients5(text: str, is_short: bool = False) -> str:
    clean = text.strip()
    if not clean:
        return ""
    raw = _clients5(clean, "en", "fa")
    return normalize_persian(raw, is_short_term=is_short, source_en=clean) if raw else ""


# Where a Persian translation came from (shown on results; "offline" ones are marked in offline-only mode).
FA_SOURCE_EDIT = "your edit"
FA_SOURCE_WIKTIONARY = "Wiktionary"
FA_SOURCE_GOOGLE = "Google Translate"
FA_SOURCE_SLM = "offline language model"
FA_SOURCE_NLLB = "offline translator"
ONLINE_FA_SOURCES = {FA_SOURCE_GOOGLE}


def local_translation(text: str, is_short: bool = False) -> Tuple[str, str]:
    """Tier 1, the local database: (Persian, source) or ("", "").

    A phrase or sentence found whole in Tatoeba or OpenSubtitles takes its human translation: the natural
    everyday rendering ("good morning" -> صبح بخیر), which Wiktionary's lists often lack. A word (or a phrase
    the corpora don't have) takes Wiktionary's translations of its main part of speech (an inflected form, its
    lemma's): the best one for the first sense, plus one for the second sense when the corpora attest it, so
    the money "bank" and the river "bank" are not run together. Among a sense's alternatives, the one used most
    in the corpora's translations of sentences containing the word wins. A lone word never takes a corpus line:
    that is often a name or a shout ("Run!"), not its meaning."""
    clean = text.strip()
    if not clean:
        return "", ""
    words = clean.split()
    if len(words) >= 2:
        fa, corpus = LEXICON.translation_pair(clean)
        fa = normalize_persian(fa, is_short_term=is_short, source_en=clean)
        if fa:
            return fa, "Tatoeba" if corpus == "tatoeba" else "OpenSubtitles"
    if len(words) > 4:
        return "", ""
    entries = LEXICON.entries(clean)
    main_pos = entries[0]["pos"] if entries else ""
    by_sense: Dict[str, List[str]] = {}
    for t in LEXICON.persian(entries[0]["word"] if entries else clean):
        fa = normalize_persian(t["fa"], is_short_term=True, source_en=clean)
        if fa and (not main_pos or t["pos"] == main_pos):  # a noun's translation is no answer for a verb
            by_sense.setdefault(t["sense"], [])
            if fa not in by_sense[t["sense"]]:
                by_sense[t["sense"]].append(fa)
    if not by_sense:
        return "", ""
    pairs = LEXICON.examples(entries[0]["word"] if entries else clean, limit=200, min_words=1, max_words=40)

    def attested(fa: str) -> int:
        return sum(fa in p["fa"] for p in pairs)

    best = [max(cands, key=attested) for cands in list(by_sense.values())[:2]]  # max keeps the first on ties
    chosen = best[:1] + [fa for fa in best[1:] if fa != best[0] and attested(fa)]
    return " \u060c ".join(chosen), FA_SOURCE_WIKTIONARY


def _google_translate(clean: str, is_short: bool) -> str:
    """The optional online layer (never in offline-only mode): Google's gtx endpoint, then clients5."""
    try:
        r = None if _OFFLINE_ONLY else smart_request(
            "GET",
            "https://translate.googleapis.com/translate_a/single",
            params=[("client", "gtx"), ("sl", "en"), ("tl", "fa"), ("dt", "t"), ("dt", "bd"), ("q", clean)],
            timeout=2.8,
        )
        if r is not None and r.ok:
            data = r.json()
            if is_short and len(data) > 1 and isinstance(data[1], list):
                terms = []
                for block in data[1]:
                    if isinstance(block, list) and len(block) > 1 and block[1]:
                        for cand_w in block[1][:2]:
                            cand = normalize_persian(cand_w, is_short_term=True, source_en=clean)
                            if cand and cand not in terms:
                                terms.append(cand)
                if terms:
                    return " \u060c ".join(terms[:2])
            res = "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
            if res and re.search(r"[\u0600-\u06FF]", res):
                norm = normalize_persian(res, is_short_term=is_short, source_en=clean)
                if norm:
                    return norm
    except Exception:
        _log_failure(f"Google translate failed for {clean!r}")
    return translate_via_clients5(clean, is_short=is_short)


def translate_with_source(text: str, is_short: bool = False, use_slm: bool = False) -> Tuple[str, str]:
    """(Persian, source) through the tiers: your edits, the local database, Google when online, then offline
    the language model (with use_slm: for the headword, an idiom or a sentence; it takes a second or two)
    and the NLLB translator. ("", "") when nothing translates it."""
    clean = text.strip()
    if not clean:
        return "", ""
    override = USER_MEMORY.get("overrides", {}).get(clean.lower())
    if override:
        return override, FA_SOURCE_EDIT
    fa, source = local_translation(clean, is_short=is_short)
    if fa:
        return fa, source
    fa = "" if _OFFLINE_ONLY else _google_translate(clean, is_short)
    if fa:
        return fa, FA_SOURCE_GOOGLE
    if use_slm:
        fa = normalize_persian(SLM.translate(clean), is_short_term=is_short, source_en=clean)
        if fa:
            return fa, FA_SOURCE_SLM
    fa = translate_offline_backup(clean, is_short=is_short)
    return (fa, FA_SOURCE_NLLB) if fa else ("", "")


def translate_single_string(text: str, is_short: bool = False) -> str:
    fa, _ = translate_with_source(text, is_short=is_short)
    return fa or (NOT_FOUND_FA if is_short else "")


def fetch_persian_word_meaning(term: str, is_idiom: bool = False, use_slm: Optional[bool] = None) -> Tuple[str, str]:
    """(Persian, source) for a looked-up word or phrase; your taught entries come right after your edits.
    The language model is asked for an idiom unless use_slm says otherwise."""
    low = term.strip().lower()
    taught = USER_MEMORY.get("entries", {}).get(low, {}).get("fa", "")
    if taught and low not in USER_MEMORY.get("overrides", {}):
        return taught, FA_SOURCE_EDIT
    return translate_with_source(term, is_short=not is_idiom, use_slm=is_idiom if use_slm is None else use_slm)


def translate_batch_fast(sentences: List[str], short_terms: List[str]) -> Dict[str, str]:
    """Persian for many strings at once: local first, then one Google request for the remaining sentences, then
    Google for each leftover, then the NLLB translator for the rest in one batch (the language model is too
    slow for a batch)."""
    all_items = [x.strip() for x in dict.fromkeys(sentences + short_terms) if x and x.strip()]
    if not all_items:
        return {}

    short_set = {x.strip() for x in short_terms}
    out: Dict[str, str] = {}
    remaining_sentences: List[str] = []
    remaining_shorts: List[str] = []

    for item in all_items:
        override = USER_MEMORY.get("overrides", {}).get(item.lower())
        local = override or local_translation(item, is_short=item in short_set)[0]
        if local:
            out[item] = local
        elif item in short_set:
            remaining_shorts.append(item)
        else:
            remaining_sentences.append(item)

    if remaining_sentences and not _OFFLINE_ONLY:  # offline: each one goes to the local model below
        joined = "\n".join(remaining_sentences)
        try:
            r = smart_request(
                "POST",
                "https://translate.googleapis.com/translate_a/single",
                params=[("client", "gtx"), ("sl", "en"), ("tl", "fa"), ("dt", "t")],
                data={"q": joined},
                timeout=3.8,
            )
            if r.ok:
                data = r.json()
                full_fa = "".join(seg[0] for seg in data[0] if seg and seg[0])
                lines = [normalize_persian(line, is_short_term=False) for line in full_fa.split("\n")]
                if len(lines) == len(remaining_sentences):
                    for orig, tr in zip(remaining_sentences, lines):
                        if tr:
                            out[orig] = tr
        except Exception:
            _log_failure("Batch translation failed")

    still_missing = [x for x in (remaining_sentences + remaining_shorts) if not out.get(x)]
    if still_missing and not _OFFLINE_ONLY:  # Google, one at a time
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {item: submit_in_context(pool, _google_translate, item, item in short_set) for item in still_missing}
            for item, fut in futs.items():
                out[item] = fut.result()
    still_missing = [x for x in still_missing if not out.get(x)]
    for item, fa in zip(still_missing, _ct2_translate_many(still_missing)):  # the rest offline, in one batch
        out[item] = normalize_persian(fa, is_short_term=item in short_set, source_en=item) if fa else ""
    for item in still_missing:
        if not out[item] and item in short_set:
            out[item] = NOT_FOUND_FA
    return out


# === SECTION 7: SENTENCE KEY-TERM EXTRACTOR & MULTI-DICTIONARY ANALYZERS ===
def first_definition(term: str) -> Tuple[str, str]:
    """(part of speech, definition) of a term's first Wiktionary sense, else its first WordNet sense, else ("", "")."""
    for e in LEXICON.entries(term):
        for sense in e["senses"]:
            if not is_grammatical_form_of_gloss(sense["def"]):
                return e["pos"], clean_english_sentence(sense["def"])
    if wn:
        syns = wn.synsets("_".join(term.lower().split()))
        if syns:
            pos_map = {"n": "Noun", "v": "Verb", "a": "Adjective", "s": "Adjective", "r": "Adverb"}
            return pos_map.get(syns[0].pos(), "Word"), clean_english_sentence(syns[0].definition())
    return "", ""


def extract_sentence_key_terms(sentence: str) -> List[Dict[str, str]]:
    """Up to five terms worth explaining: dictionary phrases of two to four words first (idioms, phrasal
    verbs, compounds), then content words by their base form."""
    tokens = [t for t in re.sub(r"[^a-zA-Z\s\-']", " ", sentence).split() if t]
    low_tokens = [t.lower().strip("-'") for t in tokens]
    extracted: List[Dict[str, str]] = []
    covered: set = set()

    def add(term: str, pos: str, definition: str) -> None:
        extracted.append({
            "term": term,
            "ipa": to_ipa_us(term),
            "pos": pos or "Word",
            "def": definition,
            # Not the language model: at a second or two per term, it is kept for the whole sentence.
            "fa": fetch_persian_word_meaning(term, is_idiom=" " in term, use_slm=False)[0] or NOT_FOUND_FA,
        })

    for n in (4, 3, 2):
        for i in range(len(low_tokens) - n + 1):
            span = range(i, i + n)
            if covered.intersection(span) or low_tokens[i] in ("the", "a", "an") \
                    or all(t in STOPWORDS for t in low_tokens[i:i + n]):
                continue  # "the storm" is just "storm"
            phrase = " ".join(low_tokens[i:i + n])
            if LEXICON.has_entry(phrase) or (wn and wn.synsets(phrase.replace(" ", "_"))):
                pos, definition = first_definition(phrase)
                if definition:
                    covered.update(span)
                    add(phrase, "Idiom / Compound" if n > 2 or pos in ("Idiom / Expression", "Verb") else pos, definition)

    seen = {e["term"] for e in extracted}
    for i, low_t in enumerate(low_tokens):
        if len(extracted) >= 5:
            break
        if i in covered or len(low_t) < 3 or low_t in STOPWORDS:
            continue
        lemma = low_t
        if LEMMATIZER:
            lemma = LEMMATIZER.lemmatize(low_t, "v")
            if lemma == low_t:
                lemma = LEMMATIZER.lemmatize(low_t, "n")
        if lemma in STOPWORDS or lemma in seen:
            continue
        pos, definition = first_definition(lemma)
        if definition:
            seen.add(lemma)
            add(lemma, pos, definition)

    return extracted[:5]


def _dictionary_sources():
    """The dictionary_sources package (imported lazily: its modules import this one for the text helpers)."""
    import dictionary_sources
    return dictionary_sources


def fetch_live_suggestions(prefix: str) -> List[str]:
    """Completions as you type: your taught entries, then dictionary headwords, most frequent first."""
    q = prefix.strip()
    if len(q) < 2 or len(q.split()) > 5 or is_persian_text(q):
        return []
    user_keys = [k for k in USER_MEMORY.get("entries", {}) if k.startswith(q.lower())]
    return list(dict.fromkeys(user_keys + LEXICON.suggestions(q)))[:7]


def fetch_verified_example_for_relation(word: str, target_pos: str = "") -> str:
    """A real example sentence for a synonym or antonym: WordNet's (of the wanted part of speech first),
    then Wiktionary's, then a corpus sentence; "" when none contains the word."""
    clean = word.strip()
    pos_code_map = {"Noun": "n", "Verb": "v", "Adjective": "a", "Adverb": "r"}
    want_pos = pos_code_map.get(target_pos, "")

    if wn:
        try:
            synsets = wn.synsets("_".join(clean.split()))
            ranked = [s for s in synsets if not want_pos or s.pos().replace("s", "a") == want_pos] + synsets[:4]
            for syn in ranked:
                for ex in syn.examples():
                    if sentence_contains_target(ex, clean) and len(ex.split()) >= 5:
                        return clean_english_sentence(ex)
        except Exception:
            _log_failure(f"WordNet example lookup failed for {word!r}")

    entries = LEXICON.entries(clean)
    entries.sort(key=lambda e: e["pos"] != target_pos)
    for e in entries:
        for sense in e["senses"]:
            if sense["example"] and sentence_contains_target(sense["example"], clean):
                return clean_english_sentence(sense["example"])
    for ex in LEXICON.examples(clean, limit=1):
        return ex["en"]
    return ""


def is_grammatical_form_of_gloss(text: str) -> bool:
    """Filters out non-semantic morphological notes like 'Present participle of conduct.'"""
    s = text.strip().lower()
    if len(s.split()) <= 8 and re.match(
        r"^(present participle|past participle|simple past|plural|gerund|third-person singular|"
        r"second-person|first-person|alternative form|alternative spelling|inflection|"
        r"comparative|superlative|archaic form|obsolete form).*?of",
        s,
    ):
        return True
    return False


def is_similar_def(d1: str, d2: str) -> bool:
    w1 = {w for w in re.findall(r"[a-z]+", d1.lower()) if w not in STOPWORDS}
    w2 = {w for w in re.findall(r"[a-z]+", d2.lower()) if w not in STOPWORDS}
    if not w1 or not w2:
        return False
    return (len(w1 & w2) / float(min(len(w1), len(w2)))) >= 0.65


# === SECTION 8: MASTER UNIFIED HYBRID LEXICAL & SENTENCE ANALYZER ===
# === PERSIAN -> ENGLISH (REVERSE) LOOKUP ===
_PERSIAN_LETTER = re.compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")  # harakat, superscript alef, tatweel
MAX_REVERSE_CANDIDATES = 12


def is_persian_text(text: str) -> bool:
    """Mostly Persian/Arabic-script letters (mixed input follows the majority script)."""
    return len(_PERSIAN_LETTER.findall(text)) > len(_LATIN_LETTER.findall(text))


def fa_key(text: str) -> str:
    """Comparison key: Arabic-keyboard letter variants unified, diacritics and edge punctuation removed."""
    text = _ARABIC_DIACRITICS.sub("", text.replace("\u064a", "\u06cc").replace("\u0643", "\u06a9"))
    return " ".join(text.replace("\u200c", " ").split()).strip(" .!?\u061f\u060c,\u061b;:\"'()")


def reverse_lookup_local(fa: str) -> List[str]:
    """English terms whose Persian meaning matches: your fixes first, then Wiktionary's translations.
    Your fixes are compared per comma-separated part."""
    target = fa_key(fa)
    if not target:
        return []
    hits = [
        en for en, fa_val in USER_MEMORY.get("overrides", {}).items()
        if any(fa_key(part) == target for part in re.split(r"[\u060c,]", fa_val or ""))
    ]
    hits += LEXICON.english_for_persian(target)
    return list(dict.fromkeys(hits))


def _google_fa_to_en(text: str) -> Tuple[str, List[Dict[str, str]]]:
    """(full translation, dictionary candidates grouped by part of speech) from Google's gtx endpoint."""
    if _OFFLINE_ONLY:
        return "", []
    try:
        r = smart_request(
            "GET",
            "https://translate.googleapis.com/translate_a/single",
            params=[("client", "gtx"), ("sl", "fa"), ("tl", "en"), ("dt", "t"), ("dt", "bd"), ("q", text)],
            timeout=2.8,
        )
        if not r.ok:
            log.warning("Google fa->en translation for %r: HTTP %s", text, r.status_code)
            return "", []
        return parse_gtx_reverse(r.json())
    except Exception:
        _log_failure(f"Google fa->en translation failed for {text!r}")
        return "", []


def parse_gtx_reverse(data: Any) -> Tuple[str, List[Dict[str, str]]]:
    translation = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).strip() if data else ""
    candidates = []
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
        for block in data[1]:
            if isinstance(block, list) and len(block) > 1 and isinstance(block[1], list):
                pos = str(block[0] or "").strip()
                candidates += [{"term": str(t).strip(), "pos": pos} for t in block[1][:6] if str(t).strip()]
    return translation, candidates


def analyze_persian(raw: str) -> Dict[str, Any]:
    """Reverse entry for Persian input: the English translation plus English candidate words."""
    headword = " ".join(raw.split())
    query = fa_key(headword)
    is_sentence = len(query.split()) >= 5 or headword.endswith((".", "!", "?", "\u061f"))

    translation, candidates = _google_fa_to_en(query)
    if not translation:
        translation = _clients5(query, "fa", "en")
    offline = False
    if not translation:
        translation = _ct2_translate(query, to_english=True)
        offline = bool(translation)

    local = reverse_lookup_local(query)
    machine = translation
    if local and not is_sentence:
        # Your fixes and dictionary translations beat literal machine output,
        # e.g. an idiom Google translates word for word.
        translation = local[0]
    found = [{"term": en, "pos": "your entries & Wiktionary"} for en in local]
    # Offline NLLB turns isolated words into sentences ("It's too soon."), so only online output becomes a word.
    if machine and not offline and not is_sentence and len(machine.split()) <= 4:
        machine_cand = machine.rstrip(".") if " " not in machine else machine
        found.append({"term": machine_cand, "pos": "translation"})
    seen = set()
    merged = []
    for cand in found + candidates:
        key = cand["term"].lower()
        if key not in seen and not is_persian_text(cand["term"]):
            seen.add(key)
            merged.append(cand)

    return {
        "headword": headword,
        "is_reverse": True,
        "is_sentence": is_sentence,
        "is_idiom": False,
        "did_you_mean": "",
        "ipa_us": "",
        "persian_equivalent": headword,
        "english_translation": translation,
        "machine_translation": machine if machine.lower() != translation.lower() else "",
        "translation_offline": offline,
        "candidates": merged[:MAX_REVERSE_CANDIDATES],
        "senses": [],
        "sentence_breakdown": [],
        "collocations": [],
        "morphology": {"root": headword, "structure": headword, "prefix": "None", "suffix": "None", "family": []},
        "synonyms": [],
        "antonyms": [],
    }


def lookup_cefr_level(term: str) -> str:
    """CEFR level of a word or phrase from the bundled list, '' if unlisted. An inflected form that is
    not listed itself ('running', 'mice') takes its WordNet lemma's level."""
    lemmas: List[str] = []
    if LEMMATIZER and term.isalpha():
        try:
            lemmas = [LEMMATIZER.lemmatize(term.lower(), pos=p) for p in ("n", "v", "a", "r")]
        except LookupError:  # the WordNet data is not installed: the exact word is all we can try
            _log_failure(f"WordNet lemmatizer unavailable for the level of {term!r}")
    return word_levels.cefr_level(CEFR_LEVELS_FILE, term, lemmas) or ""


def analyze_with_dictionaries(term: str) -> Dict[str, Any]:
    with lookup_request_cache():  # identical requests within this one lookup reach the network once
        return _analyze_with_dictionaries(term)


def _empty_morphology(term: str) -> Dict[str, Any]:
    return {"root": term, "structure": term, "prefix": "None", "suffix": "None", "family": []}


def _relation_items(items: List[Dict[str, str]], clean: str, root_lemma: str, limit: int, per_sense: int) -> List[Dict[str, str]]:
    """Synonyms or antonyms, unique and grouped by sense: at most `per_sense` from one sense, `limit` in all."""
    out: List[Dict[str, str]] = []
    seen: set = set()
    per: Dict[str, int] = {}
    for it in items:
        term, sense = it["term"], it.get("sense", "")
        if term.lower() in seen or is_spelling_or_root_duplicate(term, clean, root_lemma) or term.lower() in WEAK_SYNONYMS:
            continue
        if per.get(sense, 0) >= per_sense:
            continue
        seen.add(term.lower())
        per[sense] = per.get(sense, 0) + 1
        out.append({"term": term, "sense": sense})
        if len(out) >= limit:
            break
    order = list(dict.fromkeys(i["sense"] for i in out))
    return sorted(out, key=lambda it: order.index(it["sense"]))  # each sense's words together


def _analyze_sentence(clean: str) -> Dict[str, Any]:
    key_terms = extract_sentence_key_terms(clean)
    fa_sent, fa_source = translate_with_source(clean, is_short=False, use_slm=True)
    return {
        "headword": clean,
        "is_sentence": True,
        "is_idiom": True,
        "did_you_mean": "",
        "ipa_us": sentence_to_ipa(clean),
        "persian_equivalent": fa_sent or NOT_FOUND_FA,
        "persian_source": fa_source,
        "senses": [],
        "sentence_breakdown": key_terms,
        "collocations": [],
        "derived_terms": [],
        "morphology": _empty_morphology(clean),
        "synonyms": [],
        "antonyms": [],
    }


def _analyze_with_dictionaries(term: str) -> Dict[str, Any]:
    raw_input = term.strip()
    if is_persian_text(raw_input):
        return analyze_persian(raw_input)
    words = raw_input.split()
    is_full_sentence = len(words) >= 5 or (len(words) >= 4 and raw_input.endswith((".", "!", "?")))
    clean = auto_correct_english_input(raw_input, is_sentence=is_full_sentence)
    low = clean.lower()
    if is_full_sentence:
        return _analyze_sentence(clean)

    # --- WORD OR PHRASE ---
    is_idiom_or_phrase = len(words) >= 2
    morphology = analyze_morphology(clean) if not is_idiom_or_phrase else _empty_morphology(clean)
    root_lemma = morphology.get("root", clean)

    sources = _dictionary_sources()
    ctx = sources.LookupContext(term=clean, is_idiom=is_idiom_or_phrase, root_lemma=root_lemma)
    with ThreadPoolExecutor(max_workers=1) as pool:
        f_fa = submit_in_context(pool, fetch_persian_word_meaning, clean, is_idiom_or_phrase)
        merged = sources.REGISTRY.run(ctx)  # every enabled dictionary source, merged in registry order
        fa_main, fa_source = f_fa.result()

    did_you_mean = ""
    if not merged.senses and len(words) == 1:
        close = LEXICON.close_matches(clean)
        if close and close[0].lower() != low:
            did_you_mean = close[0]

    ipa_us = merged.ipa_us or to_ipa_us(clean)

    priority_senses: List[Dict[str, str]] = []
    if low in USER_MEMORY.get("entries", {}):
        ue = USER_MEMORY["entries"][low]
        if ue.get("def"):
            priority_senses.append({
                "pos": ue.get("pos", "Custom Entry"),
                "def": ue["def"],
                "example_en": ue.get("example_en", ""),
                "example_fa": ue.get("example_fa", ""),
            })

    all_raw_senses = priority_senses + merged.senses
    # Corpus sentences pair an example with its human translation; one containing the term is used for a
    # sense that has no example of its own.
    corpus_examples = [tp for tp in merged.examples if sentence_contains_target(tp["en"], clean)]

    used_examples = set()
    selected_senses: List[Dict[str, str]] = []

    def pick_example(s: Dict[str, str]) -> Tuple[str, str]:
        ex_en = s.get("example_en", "").strip()
        ex_fa = s.get("example_fa", "").strip()
        if ex_en and ex_en.lower() in used_examples:
            ex_en, ex_fa = "", ""
        if not ex_en:
            for tp in corpus_examples:
                if tp["en"].lower() not in used_examples:
                    ex_en, ex_fa = tp["en"], tp["fa"]
                    break
        if ex_en:
            used_examples.add(ex_en.lower())
        return ex_en, ex_fa

    for s in all_raw_senses:
        d_txt = s["def"]
        if is_grammatical_form_of_gloss(d_txt) or any(is_similar_def(d_txt, ex["def"]) for ex in selected_senses):
            continue
        ex_en, ex_fa = pick_example(s)
        selected_senses.append({
            "pos": "Idiom / Expression" if is_idiom_or_phrase and s.get("pos") != "Slang" else s.get("pos", "Noun"),
            "def": d_txt,
            "example_en": ex_en,
            "example_ipa": sentence_to_ipa(ex_en) if ex_en else "",
            "example_fa": ex_fa,
        })
        if len(selected_senses) >= 3:
            break

    # One sense of each further part of speech (a verb sense of "bank", a slang sense), up to two more.
    existing_pos = {s["pos"] for s in selected_senses}
    for s in all_raw_senses:
        s_pos = s.get("pos", "")
        if len(selected_senses) >= 5:
            break
        if s_pos and s_pos not in existing_pos and (not is_idiom_or_phrase or s_pos == "Slang") \
                and not is_grammatical_form_of_gloss(s["def"]):
            ex_en, ex_fa = pick_example(s)
            selected_senses.append({
                "pos": s_pos,
                "def": s["def"],
                "example_en": ex_en,
                "example_ipa": sentence_to_ipa(ex_en) if ex_en else "",
                "example_fa": ex_fa,
            })
            existing_pos.add(s_pos)

    primary_pos = selected_senses[0]["pos"] if selected_senses else "Noun"

    colls_raw: List[Dict[str, str]] = []
    if not is_idiom_or_phrase:
        seen_col = set()
        for c in merged.collocations:
            t = c["term"].strip()
            if t.lower() not in seen_col and t.lower() != low:
                seen_col.add(t.lower())
                example = c["example_en"] or next(iter(ex["en"] for ex in LEXICON.examples(t, limit=1)), "")
                colls_raw.append({"term": t, "example_en": example})
            if len(colls_raw) >= 4:
                break

    syn_items = _relation_items(merged.syns, clean, root_lemma, limit=6, per_sense=2)
    ant_items = _relation_items(merged.ants, clean, root_lemma, limit=3, per_sense=3)
    with ThreadPoolExecutor(max_workers=6) as pool:
        ex_futs = {it["term"]: submit_in_context(pool, fetch_verified_example_for_relation, it["term"], primary_pos)
                   for it in syn_items + ant_items}
        for it in syn_items + ant_items:
            it["example_en"] = ex_futs[it["term"]].result()

    derived = [d for d in dict.fromkeys(merged.derived) if d.lower() != low][:12] if not is_idiom_or_phrase else []

    core_idiom_meaning_en = ""
    if is_idiom_or_phrase and selected_senses:
        core_idiom_meaning_en = clean_definition_for_idiom_meaning(selected_senses[0]["def"])

    sentences_to_tr: List[str] = []
    short_terms_to_tr: List[str] = []
    if not fa_main:
        short_terms_to_tr.append(core_idiom_meaning_en or clean)
    for s in selected_senses:
        if s["example_en"] and not s.get("example_fa"):
            sentences_to_tr.append(s["example_en"])
    for item in colls_raw + syn_items + ant_items:
        short_terms_to_tr.append(item["term"])
        if item["example_en"]:
            sentences_to_tr.append(item["example_en"])

    trans_map = translate_batch_fast(sentences_to_tr, short_terms_to_tr)

    if not fa_main or fa_main in INVALID_FA_HEADERS:
        fa_main = trans_map.get(core_idiom_meaning_en or clean, "")
        fa_source = ""  # whichever tier the batch used
    for s in selected_senses:
        if s["example_en"] and not s.get("example_fa"):
            s["example_fa"] = trans_map.get(s["example_en"], "")

    def related(item: Dict[str, str]) -> Dict[str, str]:
        ex = item["example_en"]
        out = {
            "term": item["term"],
            "ipa": to_ipa_us(item["term"]),
            "fa": trans_map.get(item["term"], ""),
            "example_en": ex,
            "example_ipa": sentence_to_ipa(ex) if ex else "",
            "example_fa": trans_map.get(ex, "") if ex else "",
        }
        if "sense" in item:
            out["sense"] = item["sense"]
        return out

    return {
        "headword": clean,
        "is_sentence": False,
        "is_idiom": is_idiom_or_phrase,
        "did_you_mean": did_you_mean,
        "ipa_us": ipa_us,
        "persian_equivalent": fa_main if fa_main and fa_main not in INVALID_FA_HEADERS else NOT_FOUND_FA,
        "persian_source": fa_source,
        "senses": selected_senses,
        "sentence_breakdown": [],
        "collocations": [related(c) for c in colls_raw],
        "derived_terms": derived,
        "morphology": morphology,
        "cefr_level": lookup_cefr_level(clean),
        "frequency_per_million": merged.frequency,
        "frequency_band": word_levels.frequency_band(merged.frequency),
        "synonyms": [related(it) for it in syn_items],
        "antonyms": [related(it) for it in ant_items],
    }
