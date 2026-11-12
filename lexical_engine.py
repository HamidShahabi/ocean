# === SECTION 1: IMPORTS, OFFLINE DATA PATHS & PERSISTENT SESSIONS ===
import os
import re
import sys
import html
import json
import time
import shutil
import socket
import logging
import sqlite3
import threading
import contextvars
import urllib.parse
import urllib.request
from collections import OrderedDict
from functools import lru_cache
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
import requests
from requests.adapters import HTTPAdapter

import lexicon_db
import lexicon_text as lt
import offline_slm
import translation_cache
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
ENGINE_VERSION = 3  # 3: no WordNet; clean subtitle-first examples, few everyday senses, IPA in one notation, idiom matching, cached translations
#                    2: local datasets (Kaikki, WordNet 3.1, Urban Dictionary, Tatoeba, OpenSubtitles)

USER_MEMORY_FILE = user_data_path("user_memory.json")
CEFR_LEVELS_FILE = os.path.join(ENGINE_BASE_DIR, "cefr_levels.json")
LEXICON_DB_FILE = os.path.join(ENGINE_BASE_DIR, "ocean_lexicon.db")  # built by tools/build_lexicon_db.py
OFFLINE_MT_DIR = os.path.join(ENGINE_BASE_DIR, "offline_mt_model")
OFFLINE_SLM_DIR = os.path.join(ENGINE_BASE_DIR, "offline_slm_model")

try:
    import eng_to_ipa as ipa_engine
except Exception:
    log.warning("eng_to_ipa unavailable: IPA falls back to rule-based G2P", exc_info=True)
    ipa_engine = None

# ctranslate2 and sentencepiece are imported by _load_ct2_model(), in the preload thread after the window is
# shown. HAS_CT2_BACKUP starts optimistic and turns False if the import fails.
ctranslate2: Any = None
spm: Any = None
HAS_CT2_BACKUP = True

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
        if isinstance(exc, requests.ConnectionError):
            note_network_failure()
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
TRANSLATION_CACHE = translation_cache.TranslationCache(os.path.join(USER_DATA_DIR, "translation_cache.db"))


def _load_ct2_model() -> None:
    """Load the CTranslate2 model once; concurrent callers wait for the load in progress."""
    global _CT2_TRANSLATOR, _SP_PROCESSOR, _CT2_IS_NLLB, HAS_CT2_BACKUP, ctranslate2, spm
    with _CT2_LOAD_LOCK:
        if _CT2_TRANSLATOR is not None and _SP_PROCESSOR is not None:
            return
        sp_p = os.path.join(OFFLINE_MT_DIR, "sentencepiece.model")
        md_p = os.path.join(OFFLINE_MT_DIR, "model")
        if not (os.path.exists(sp_p) and os.path.isdir(md_p)):
            return
        if ctranslate2 is None or spm is None:
            try:
                import ctranslate2 as ct2_module
                import sentencepiece as spm_module
            except Exception:
                log.warning("ctranslate2/sentencepiece unavailable: no offline translation", exc_info=True)
                HAS_CT2_BACKUP = False
                return
            ctranslate2, spm = ct2_module, spm_module
        sp_proc = spm.SentencePieceProcessor(model_file=sp_p)
        translator = ctranslate2.Translator(md_p, device="cpu", compute_type="int8")
        vocab_p = os.path.join(md_p, "shared_vocabulary.txt")
        if os.path.exists(vocab_p):
            with open(vocab_p, encoding="utf-8") as vf:
                _CT2_IS_NLLB = any(line.strip() == "pes_Arab" for line in vf)
        _SP_PROCESSOR, _CT2_TRANSLATOR = sp_proc, translator


def preload_offline_translator() -> threading.Thread:
    """Load the offline model in the background so the first offline lookup doesn't stall.
    Not a daemon: quitting while the native model is mid-load crashes the process, so
    interpreter shutdown waits for the load to finish (same as the Kokoro voice model)."""
    def run() -> None:
        is_online()  # learn at once whether there is internet, so the first lookup does not have to wait for the probe
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
_OFFLINE_ONLY = False  # a hard switch (tests, diagnostics): no network request of any kind while set. The app has no
#                        setting for it any more: it notices a missing connection by itself (see is_online).


class OfflineModeError(requests.ConnectionError):
    """smart_request refused a request because offline-only mode is on."""


def set_offline_only(enabled: bool) -> None:
    global _OFFLINE_ONLY
    _OFFLINE_ONLY = bool(enabled)


def is_offline_only() -> bool:
    return _OFFLINE_ONLY


# --- Connectivity: found out once, remembered for a while ---
PROBE_HOST = "translate.googleapis.com"
PROBE_TIMEOUT = 1.0        # seconds a lookup waits to learn that the internet is not there
ONLINE_TTL = 30.0          # a successful probe is trusted this long
OFFLINE_TTL = 60.0         # a failed probe (or a failed connection) is trusted this long: offline lookups never wait again
_NET_LOCK = threading.Lock()
_NET_STATE: Dict[str, Any] = {"online": None, "until": 0.0}


def _probe_target() -> Tuple[str, int]:
    """Where to try a TCP connection: the proxy when the system uses one (a direct connection would fail on
    such a network although requests work), else the translation service itself."""
    try:
        proxy = urllib.request.getproxies().get("https")
        if proxy:
            parsed = urllib.parse.urlparse(proxy if "//" in proxy else "//" + proxy)
            if parsed.hostname:
                return parsed.hostname, parsed.port or 443
    except Exception:
        pass
    return PROBE_HOST, 443


def _probe_connectivity() -> bool:
    """Whether a TCP connection can be made right now, never waiting longer than PROBE_TIMEOUT (the name lookup,
    which has no timeout of its own, runs in a helper thread that is simply left behind)."""
    result: List[bool] = []

    def attempt() -> None:
        try:
            socket.create_connection(_probe_target(), timeout=PROBE_TIMEOUT).close()
            result.append(True)
        except Exception:
            result.append(False)

    thread = threading.Thread(target=attempt, name="connectivity-probe", daemon=True)
    thread.start()
    thread.join(PROBE_TIMEOUT + 0.3)
    return bool(result and result[0])


def is_online() -> bool:
    """Whether the internet can be reached, from a cached probe: a lookup asks this before using an online
    layer, so without a connection the answer takes no time after the first lookup and everything falls back
    to the local resources at once."""
    if _OFFLINE_ONLY:
        return False
    with _NET_LOCK:
        now = time.monotonic()
        if _NET_STATE["online"] is not None and now < _NET_STATE["until"]:
            return bool(_NET_STATE["online"])
        online = _probe_connectivity()
        _NET_STATE.update(online=online, until=time.monotonic() + (ONLINE_TTL if online else OFFLINE_TTL))
        if not online:
            log.info("No internet connection: lookups use the local resources for the next %d seconds", OFFLINE_TTL)
        return online


def note_network_failure() -> None:
    """A request failed to connect: treat the internet as gone for OFFLINE_TTL seconds."""
    with _NET_LOCK:
        _NET_STATE.update(online=False, until=time.monotonic() + OFFLINE_TTL)


def forget_connectivity() -> None:
    """Probe again at the next question (Refresh: the user believes the connection is back)."""
    with _NET_LOCK:
        _NET_STATE.update(online=None, until=0.0)


def network_allowed() -> bool:
    """Whether an online layer may be tried: not forced offline, and the internet is there."""
    return not _OFFLINE_ONLY and is_online()


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


@lru_cache(maxsize=50_000)
def _cmu_ipa(word: str) -> str:
    """eng_to_ipa's CMU transcription, "" if unknown. Cached: each call opens its database (~10 ms)."""
    if not ipa_engine:
        return ""
    try:
        res = ipa_engine.convert(word)
        return res if res and "*" not in res else ""
    except Exception:
        return ""


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
    res = _cmu_ipa(low)
    if res:
        return res
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
    joined = lt.normalize_ipa(" ".join(c for c in converted if c))
    return f"/{joined}/" if joined else "/-/"


def sentence_to_ipa(sentence: str) -> str:
    norm_sent = re.sub(r"[\u2018\u2019\u02bc]", "'", sentence)
    tokens = re.findall(r"[a-zA-Z]+(?:'[a-zA-Z]+)?", norm_sent)
    if not tokens:
        return ""
    ipa_words = [convert_token_to_ipa(tok) for tok in tokens]
    joined = lt.normalize_ipa(" ".join(w for w in ipa_words if w))
    return f"/{joined}/" if joined else ""


def clean_ipa_display(ipa_str: str) -> str:
    """A transcription in the one notation shown everywhere (lexicon_text.normalize_ipa), in slashes."""
    if not ipa_str or ipa_str == "/-/":
        return "/-/"
    s = lt.normalize_ipa(ipa_str)
    return f"/{s}/" if s else "/-/"


# === SECTION 5: PRECISION MORPHOLOGY, ROOT, AFFIX & POS FAMILY ANALYZER ===
def _is_valid_english_word(word: str) -> bool:
    return bool(word) and len(word) >= 3 and LEXICON.has_entry(word)


def lemmatize(word: str) -> str:
    """The base form of a word: Wiktionary's lemma for an inflected form; for a word it has no entry for, what plain
    suffix rules give that it does have; else the word itself."""
    low = word.lower().strip()
    lemmas = LEXICON.lemmas(low)
    if lemmas:
        return lemmas[0]
    if LEXICON.has_entry(low):
        return low
    cands = LEXICON.lemma_candidates(low)
    return cands[0] if cands else low


def lemma_candidates(word: str) -> List[str]:
    """Base forms to try for an inflected word, most likely first (Wiktionary's, then plain suffix rules the
    dictionary confirms; without a dictionary, none)."""
    return LEXICON.lemma_candidates(word.lower().strip())


def _find_true_morphological_root(word: str) -> Tuple[str, str]:
    low = word.lower().strip()
    if low in PROTECTED_ROOT_WORDS:
        return low, "None (Base Word)"
    lemmas = LEXICON.lemmas(low)
    if lemmas:  # Wiktionary says it is an inflected form (or a variant spelling) of another word
        return lemmas[0], "Inflected form"

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

    return low, "None (Base Word)"


def analyze_morphology(term: str) -> Dict[str, Any]:
    """Root, prefix and suffix of a single word from affix rules checked against the dictionary. The word family
    is added by _word_family() once the dictionary entry (its derived and related terms) is known."""
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
                if rem not in FALSE_BOUND_STEMS and _is_valid_english_word(rem) and is_everyday_word(rem, floor=3.0):
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
    return {"root": root, "structure": structure_str, "prefix": detected_prefix, "suffix": detected_suffix, "family": []}


# Common-use floors: an English word or phrase shown as a relative, expression or synonym must be this frequent in
# subtitles (occurrences per million words). Without word frequencies in the database nothing is filtered.
EVERYDAY_PER_MILLION = 1.0
MAX_FAMILY = 5
MAX_COMMON_EXPRESSIONS = 8
MAX_ALTERNATIVES = 3


def is_everyday_word(word: str, floor: float = EVERYDAY_PER_MILLION) -> bool:
    """Whether every word of the term is used in everyday speech: at least `floor` per million words of subtitles.
    True when the database has no frequencies (nothing to judge by)."""
    if not LEXICON.has("word_freq"):
        return True
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", word.lower())
    return bool(tokens) and all((LEXICON.frequency(t) or 0.0) >= floor for t in tokens)


def _word_family(root: str, low: str, derived: List[str], related: List[str], pos_of: Callable[[str], str]) -> List[Dict[str, str]]:
    """Everyday relatives of the word with their part of speech, from Wiktionary's derived and related terms:
    single words that start like the root and that people say (not "birational")."""
    stem = root[:4].lower()
    out: List[Dict[str, str]] = []
    seen = {low, root.lower()}
    for cand in list(derived) + list(related):
        c = cand.strip()
        if not c or " " in c or "-" in c or c.lower() in seen or not c.lower().startswith(stem) or not c.isalpha():
            continue
        if LEXICON.lemmas(c) or not is_everyday_word(c, floor=2.0):
            continue  # an inflection (it belongs to Forms) or a word nobody says
        pos = pos_of(c)
        if pos:
            seen.add(c.lower())
            out.append({"pos": pos, "word": c.lower()})
    return out[:MAX_FAMILY]


def _compact_key(phrase: str) -> str:
    """Spaces and hyphens removed: "cis privilege", "cis-privilege" and "cisprivilege" are one expression."""
    return re.sub(r"[\s\-]+", "", phrase.lower())


MIN_PHRASE_HITS = 3   # a phrase must be said in at least this many subtitle lines to count as common
PHRASE_HITS_CAP = 25


def is_everyday_term(term: str) -> bool:
    """Whether a word or phrase is used in everyday speech: every word is everyday and a phrase (or a hyphenated
    compound) is said in at least MIN_PHRASE_HITS subtitle lines. True when the database has no subtitles to judge by."""
    if not is_everyday_word(term):
        return False
    if (" " in term.strip() or "-" in term) and LEXICON.has("parallel_fts"):
        return LEXICON.phrase_hits(term.replace("-", " "), MIN_PHRASE_HITS) >= MIN_PHRASE_HITS
    return True


def common_expressions(headword: str, derived: List[str], collocations: List[str]) -> List[str]:
    """The commonly used derived terms and phrases (Wiktionary's "derived terms", then collocations): single
    words must be everyday words (no "birational", "extrarational"), phrases must be said in the subtitles
    (the more often the earlier; at least MIN_PHRASE_HITS lines) unless Wiktionary lists them as collocations,
    forms of the headword itself (possessed, possessing) are left to Forms, spelling variants of one expression
    count once (spaced form preferred), at most MAX_COMMON_EXPRESSIONS."""
    low = headword.lower()
    colloc = {c.lower() for c in collocations}
    ranked: Dict[str, Tuple[int, int, str]] = {}  # compact key -> (hits, order, phrase)
    for order, cand in enumerate(list(derived) + list(collocations)):
        c = " ".join(cand.split())
        if not c or c.lower() == low or (LEXICON.lemmas(c) and lemmatize(c) == low):
            continue
        if " " in c or "-" in c:
            hits = LEXICON.phrase_hits(c.replace("-", " "), PHRASE_HITS_CAP) if LEXICON.has("parallel_fts") else PHRASE_HITS_CAP
            if hits < MIN_PHRASE_HITS and c.lower() not in colloc:
                continue
        else:
            if LEXICON.lemmas(c) or not is_everyday_word(c, floor=2.0):
                continue
            hits = PHRASE_HITS_CAP // 2  # a plain derived word: judged by its frequency, ranked after the commonest phrases
        key = _compact_key(c)
        prior = ranked.get(key)
        if prior is None:
            ranked[key] = (hits, order, c)
        elif " " in c and " " not in prior[2]:  # prefer the spaced spelling
            ranked[key] = (max(hits, prior[0]), prior[1], c)
    best = sorted(ranked.values(), key=lambda r: (-r[0], r[1]))[:MAX_COMMON_EXPRESSIONS]
    return [phrase for _, _, phrase in best]


# === SECTION 6: TWO-TIER ENGLISH -> PERSIAN TRANSLATOR ===
def clean_definition_for_idiom_meaning(def_text: str) -> str:
    s = def_text.strip().rstrip(".")
    s = re.sub(r"^(To|Being|Feeling|Something that is|A|An)\s+", "", s, flags=re.IGNORECASE)
    s = re.split(r"[;:]|\s+,\s+or\s+|\s+so that\s+|\s+because\s+", s)[0].strip()
    return s


def _clients5(text: str, source: str, target: str) -> str:
    """Raw single-best translation from Google's dictionary-extension endpoint."""
    if not network_allowed():
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


# Where a Persian translation came from (shown on results; the two "offline" ones are marked as machine output).
FA_SOURCE_EDIT = "your edit"
FA_SOURCE_IDIOMS = "Idiom dictionary"
FA_SOURCE_WIKTIONARY = "Wiktionary"
FA_SOURCE_GOOGLE = "Google Translate"
FA_SOURCE_SLM = "offline language model"
FA_SOURCE_NLLB = "offline translator"
ONLINE_FA_SOURCES = {FA_SOURCE_GOOGLE}

# Cache quality ranks: a better translation replaces a worse one, so offline guesses are upgraded when Google answers.
QUALITY_GOOGLE, QUALITY_SLM, QUALITY_NLLB = 3, 2, 1
# A Wiktionary Persian equivalent is accepted offline only when its rarest content word occurs at least this often in
# the Persian side of the OpenSubtitles corpus: literary or unusual equivalents fall behind everyday ones.
FA_MIN_SUBTITLE_COUNT = 10

# True while a Refresh runs (in this context and the workers it starts): every online layer is asked again and what
# comes back replaces the cached translation.
_REFRESHING: "contextvars.ContextVar[bool]" = contextvars.ContextVar("refreshing", default=False)


def _kind(is_short: bool) -> str:
    return translation_cache.KIND_TERM if is_short else translation_cache.KIND_SENTENCE


def _remember(text: str, fa: str, source: str, quality: int, is_short: bool) -> None:
    TRANSLATION_CACHE.put(text, fa, source, quality, _kind(is_short))


def _usable_equivalent(fa: str) -> bool:
    """Whether a Wiktionary Persian equivalent is an everyday one (see FA_MIN_SUBTITLE_COUNT); true when the
    database has no Persian word frequencies."""
    count = LEXICON.persian_frequency(fa)
    return count is None or count >= FA_MIN_SUBTITLE_COUNT


def local_phrase_translation(text: str, is_short: bool = False) -> Tuple[str, str]:
    """The human translations in the local database for a phrase or sentence: a pre-translated idiom (idioms_fa), or
    the text found whole in Tatoeba or OpenSubtitles (the natural everyday rendering, "good morning" -> صبح بخیر).
    ("", "") for a single word or when there is none."""
    clean = text.strip()
    if len(clean.split()) < 2:
        return "", ""
    idiom = LEXICON.idiom_fa(clean)
    if idiom:
        fa = normalize_persian(idiom["fa"], is_short_term=is_short, source_en=clean)
        if fa:
            return fa, FA_SOURCE_IDIOMS
    fa, corpus = LEXICON.translation_pair(clean)
    fa = normalize_persian(fa, is_short_term=is_short, source_en=clean)
    if fa:
        return fa, "Tatoeba" if corpus == "tatoeba" else "OpenSubtitles"
    return "", ""


def local_word_translation(text: str) -> Tuple[str, str]:
    """Wiktionary's Persian for a word or short phrase: the best equivalent of the first sense of the main part of
    speech (an inflected form takes its lemma's), plus one for the second sense when the corpora attest it, so the
    money "bank" and the river "bank" are not run together. Equivalents that are not everyday words (rare in the
    Persian subtitles) are passed over for commoner ones; among the rest the one used most in the corpora's
    translations of sentences containing the word wins. A lone word never takes a corpus line: that is often a
    name or a shout ("Run!"), not its meaning."""
    clean = text.strip()
    if not clean or len(clean.split()) > 4:
        return "", ""
    entries = LEXICON.entries(clean)
    main_pos = entries[0]["pos"] if entries else ""
    head = entries[0]["word"] if entries else clean
    by_sense: Dict[str, List[str]] = {}
    for t in LEXICON.persian(head):
        fa = normalize_persian(t["fa"], is_short_term=True, source_en=clean)
        if fa and (not main_pos or t["pos"] == main_pos):  # a noun's translation is no answer for a verb
            by_sense.setdefault(t["sense"], [])
            if fa not in by_sense[t["sense"]]:
                by_sense[t["sense"]].append(fa)
    by_sense = {sense: [fa for fa in cands if _usable_equivalent(fa)] for sense, cands in by_sense.items()}
    by_sense = {sense: cands for sense, cands in by_sense.items() if cands}
    if not by_sense:
        return "", ""
    pairs = LEXICON.sentence_pairs(head, limit=200, min_words=1, max_words=40)

    def attested(fa: str) -> int:
        return sum(fa in p["fa"] for p in pairs)

    best = [max(cands, key=lambda fa: (attested(fa), LEXICON.persian_frequency(fa) or 0)) for cands in list(by_sense.values())[:2]]
    chosen = best[:1] + [fa for fa in best[1:] if fa != best[0] and attested(fa)]
    return " ، ".join(chosen), FA_SOURCE_WIKTIONARY


def local_translation(text: str, is_short: bool = False) -> Tuple[str, str]:
    """Tier 1, the local database: (Persian, source) or ("", ""). A phrase's human translation first, then a
    word's Wiktionary equivalents."""
    fa, source = local_phrase_translation(text, is_short)
    return (fa, source) if fa else local_word_translation(text)


def _google_translate(clean: str, is_short: bool) -> str:
    """The online layer: Google's gtx endpoint, then clients5."""
    try:
        r = smart_request(
            "GET",
            "https://translate.googleapis.com/translate_a/single",
            params=[("client", "gtx"), ("sl", "en"), ("tl", "fa"), ("dt", "t"), ("dt", "bd"), ("q", clean)],
            timeout=2.8,
        )
        if r.ok:
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
                    return " ، ".join(terms[:2])
            res = "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
            if res and re.search(r"[؀-ۿ]", res):
                norm = normalize_persian(res, is_short_term=is_short, source_en=clean)
                if norm:
                    return norm
    except Exception:
        _log_failure(f"Google translate failed for {clean!r}")
    return translate_via_clients5(clean, is_short=is_short)


def _online_translation(clean: str, is_short: bool) -> str:
    """Google's translation, remembered forever once it comes. "" without internet, at once (see is_online)."""
    if not network_allowed():
        return ""
    fa = _google_translate(clean, is_short)
    if fa:
        _remember(clean, fa, FA_SOURCE_GOOGLE, QUALITY_GOOGLE, is_short)
    return fa


def quick_translation(text: str, is_short: bool = False) -> Tuple[str, str]:
    """What is known without asking anyone: your edits, the local database's human translations and Wiktionary
    equivalents, anything already cached. For the first, instant view of an entry."""
    clean = text.strip()
    override = USER_MEMORY.get("overrides", {}).get(clean.lower())
    if override:
        return override, FA_SOURCE_EDIT
    fa, source = local_phrase_translation(clean, is_short)
    if fa:
        return fa, source
    cached = TRANSLATION_CACHE.get(clean, _kind(is_short))
    if cached and cached[2] >= QUALITY_GOOGLE:
        return cached[0], cached[1]
    fa, source = local_word_translation(clean)
    if fa:
        return fa, source
    return (cached[0], cached[1]) if cached else ("", "")


def translate_with_source(text: str, is_short: bool = False, use_slm: bool = False) -> Tuple[str, str]:
    """(Persian, source) through the tiers; ("", "") when nothing translates it.

    1. your edits; 2. a pre-translated idiom, or the text found whole in a subtitle/Tatoeba pair (human
       translations: machines are never asked for these); 3. the permanent cache, when it holds Google's answer;
    4. online, Google Translate (cached for good, so the next lookup is instant and works offline); 5. a word's
       Wiktionary equivalents that people actually say; 6. any cached machine translation; 7. offline, the local
       language model (with use_slm: for the headword, an idiom or a sentence; it takes a second or two) and the
       NLLB translator, each cached for good. During a Refresh the cache is not trusted: Google is asked again."""
    clean = text.strip()
    if not clean:
        return "", ""
    override = USER_MEMORY.get("overrides", {}).get(clean.lower())
    if override:
        return override, FA_SOURCE_EDIT
    fa, source = local_phrase_translation(clean, is_short)
    if fa:
        return fa, source
    cached = TRANSLATION_CACHE.get(clean, _kind(is_short))
    if cached and cached[2] >= QUALITY_GOOGLE and not _REFRESHING.get():
        return cached[0], cached[1]
    fa = _online_translation(clean, is_short)
    if fa:
        return fa, FA_SOURCE_GOOGLE
    if cached and cached[2] >= QUALITY_GOOGLE:  # a Refresh that could not reach Google keeps what it had
        return cached[0], cached[1]
    fa, source = local_word_translation(clean)
    if fa:
        return fa, source
    if cached:
        return cached[0], cached[1]
    if use_slm:
        fa = normalize_persian(SLM.translate(clean), is_short_term=is_short, source_en=clean)
        if fa:
            _remember(clean, fa, FA_SOURCE_SLM, QUALITY_SLM, is_short)
            return fa, FA_SOURCE_SLM
    fa = translate_offline_backup(clean, is_short=is_short)
    if fa:
        _remember(clean, fa, FA_SOURCE_NLLB, QUALITY_NLLB, is_short)
        return fa, FA_SOURCE_NLLB
    return "", ""


def translate_single_string(text: str, is_short: bool = False) -> str:
    fa, _ = translate_with_source(text, is_short=is_short)
    return fa or (NOT_FOUND_FA if is_short else "")


def fetch_persian_word_meaning(term: str, is_idiom: bool = False, use_slm: Optional[bool] = None) -> Tuple[str, str]:
    """(Persian, source) for a looked-up word or phrase; your taught entries come right after your edits.
    The language model is asked for an idiom unless use_slm says otherwise (and only when no table had it)."""
    low = term.strip().lower()
    taught = USER_MEMORY.get("entries", {}).get(low, {}).get("fa", "")
    if taught and low not in USER_MEMORY.get("overrides", {}):
        return taught, FA_SOURCE_EDIT
    return translate_with_source(term, is_short=not is_idiom, use_slm=is_idiom if use_slm is None else use_slm)


def translate_batch_fast(sentences: List[str], short_terms: List[str]) -> Dict[str, str]:
    """Persian for many strings at once: your edits and human translations first, then the cache, then Google (one
    request for the remaining sentences, one per leftover; cached for good), then Wiktionary's everyday
    equivalents for words, then the NLLB translator for the rest in one batch (the language model is too slow
    for a batch; its results too are cached)."""
    all_items = [x.strip() for x in dict.fromkeys(sentences + short_terms) if x and x.strip()]
    if not all_items:
        return {}

    short_set = {x.strip() for x in short_terms}
    refresh = _REFRESHING.get()
    out: Dict[str, str] = {}
    cached_low: Dict[str, str] = {}
    pending: List[str] = []

    for item in all_items:
        is_short = item in short_set
        override = USER_MEMORY.get("overrides", {}).get(item.lower())
        local = override or local_phrase_translation(item, is_short)[0]
        if local:
            out[item] = local
            continue
        cached = TRANSLATION_CACHE.get(item, _kind(is_short))
        if cached and cached[2] >= QUALITY_GOOGLE and not refresh:
            out[item] = cached[0]
            continue
        if cached:
            cached_low[item] = cached[0]
        pending.append(item)

    online = bool(pending) and network_allowed()
    remaining_sentences = [x for x in pending if x not in short_set]
    if online and len(remaining_sentences) > 1:
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
                            _remember(orig, tr, FA_SOURCE_GOOGLE, QUALITY_GOOGLE, False)
        except Exception:
            _log_failure("Batch translation failed")

    still_missing = [x for x in pending if not out.get(x)]
    if online and still_missing:  # Google, one at a time
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {item: submit_in_context(pool, _online_translation, item, item in short_set) for item in still_missing}
            for item, fut in futs.items():
                if fut.result():
                    out[item] = fut.result()
    still_missing = [x for x in still_missing if not out.get(x)]
    for item in list(still_missing):  # Wiktionary's everyday equivalents, else what an earlier offline guess cached
        fa = local_word_translation(item)[0] if item in short_set else ""
        fa = fa or cached_low.get(item, "")
        if fa:
            out[item] = fa
    still_missing = [x for x in still_missing if not out.get(x)]
    for item, fa in zip(still_missing, _ct2_translate_many(still_missing)):  # the rest offline, in one batch
        is_short = item in short_set
        out[item] = normalize_persian(fa, is_short_term=is_short, source_en=item) if fa else ""
        if out[item]:
            _remember(item, out[item], FA_SOURCE_NLLB, QUALITY_NLLB, is_short)
    for item in still_missing:
        if not out[item] and item in short_set:
            out[item] = NOT_FOUND_FA
    return out


# === SECTION 7: SENTENCE KEY-TERM EXTRACTOR & MULTI-DICTIONARY ANALYZERS ===
def first_definition(term: str) -> Tuple[str, str]:
    """(part of speech, definition) of a term's most ordinary Wiktionary sense, else ("", "")."""
    best: Optional[Tuple[int, str, str]] = None
    entries = LEXICON.entries(term)
    many_pos = len({e["pos"] for e in entries}) > 1
    for e in entries:
        for sense in e["senses"]:
            if is_grammatical_form_of_gloss(sense["def"]):
                continue
            klass = lt.sense_class(sense.get("tags", []), sense["def"], e["pos"], many_pos)
            if best is None or klass < best[0]:
                best = (klass, e["pos"], clean_english_sentence(lt.display_definition(sense["def"], sense.get("tags", []))))
    return (best[1], best[2]) if best else ("", "")


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
            if LEXICON.has_entry(phrase):
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
        lemma = lemmatize(low_t)
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


def relation_example(word: str, target_pos: str = "") -> Tuple[str, str]:
    """(English, Persian) example for a synonym or antonym, routed like every example: a subtitle line first (with
    its human translation), then a usage example from Wiktionary (of the wanted part of speech first), then a
    Tatoeba sentence. ("", "") when none contains the word; damaged or archaic text is never used."""
    clean = word.strip()
    for ex in LEXICON.examples(clean, "opensubtitles", limit=1):
        return ex["en"], ex["fa"]
    entries = LEXICON.entries(clean)
    entries.sort(key=lambda e: e["pos"] != target_pos)
    is_known = LEXICON.known_word if LEXICON.vocabulary_ok() else None
    for e in entries:
        for sense in e["senses"]:
            ex = lt.clean_example(sense.get("example", ""), is_known=is_known, target=clean)
            if ex and sentence_contains_target(ex, clean):
                return clean_english_sentence(ex), ""
    for ex in LEXICON.examples(clean, "tatoeba", limit=1):
        return ex["en"], ex["fa"]
    return "", ""


def fetch_verified_example_for_relation(word: str, target_pos: str = "") -> str:
    """The English of relation_example()."""
    return relation_example(word, target_pos)[0]


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
    if not network_allowed():
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
    not listed itself ('running', 'mice') takes its lemma's level (Wiktionary's, or by suffix rules)."""
    lemmas = lemma_candidates(term) if term.isalpha() else []
    return word_levels.cefr_level(CEFR_LEVELS_FILE, term, lemmas) or ""


PENDING_FA = "…"  # "…": in a preview, Persian that is still being translated

# What a word page shows (spec 5.C): a few ordinary senses, a few everyday relations.
MAX_SENSES_SHOWN = 3       # one or two senses usually, three at most (a second part of speech may add one)
MAJOR_POS = {"Noun", "Verb", "Adjective", "Adverb"}
MAX_SYNONYMS = 5
MAX_ANTONYMS = 5
RELATIONS_PER_SENSE = 3
EXAMPLE_CORPUS_NAMES = {"opensubtitles": "OpenSubtitles", "tatoeba": "Tatoeba"}


def analyze_with_dictionaries(term: str, on_preview: Optional[Callable[[Dict[str, Any]], None]] = None,
                              refresh: bool = False) -> Dict[str, Any]:
    """The entry for a word, phrase or sentence (English or Persian). For a word or phrase, on_preview gets the
    entry built from local data alone (definitions, examples, IPA, the local Persian) as soon as the sources
    have answered, with PENDING_FA where Persian is still to come; the full entry is returned afterwards.

    A first lookup already returns the best result there is: Google's translation when online (cached for good,
    so the same word is instant and offline next time), the local resources otherwise. With refresh=True the
    online resources are asked again whatever is cached (the connection is probed afresh, Google is asked for
    every translation and its answer replaces the cached one) and the entry carries "refresh_info":
    {"online": bool} (False: there was no internet, the result is the local one again)."""
    token = _REFRESHING.set(refresh)
    try:
        if refresh:
            forget_connectivity()
        with lookup_request_cache():  # identical requests within this one lookup reach the network once
            data = _analyze_with_dictionaries(term, on_preview)
        if refresh:
            data["refresh_info"] = {"online": network_allowed()}
        return data
    finally:
        _REFRESHING.reset(token)


def _empty_morphology(term: str) -> Dict[str, Any]:
    return {"root": term, "structure": term, "prefix": "None", "suffix": "None", "family": []}


def _term_frequency(term: str) -> float:
    """Subtitle frequency of a word, of a phrase's rarest word; 0 when unknown."""
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", term.lower())
    return min((LEXICON.frequency(t) or 0.0) for t in tokens) if tokens else 0.0


def choose_senses(senses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The senses to show, from every sense the sources gave (in their order, each with a "klass"; see
    lexicon_text.sense_class): the most ordinary ones, one or two (three at most). Specialist senses
    (mathematics, linguistics, anatomy, law...) and archaic, obsolete, rare, historical ones or "A surname" are
    left out while anything ordinary exists. The first sense and a second of its part of speech are taken;
    when the word has another common part of speech (noun and verb), that one's first sense is taken as well,
    so at most three come out, usually two. A sense that says what an earlier one says is skipped."""
    cands = [s for s in senses if not is_grammatical_form_of_gloss(s["def"])]
    for ceiling in (1, 2, 3):  # ordinary (and slang) first; specialist only if nothing else; archaic last
        pool = sorted((s for s in cands if s.get("klass", 0) <= ceiling), key=lambda s: s.get("klass", 0))
        if pool:
            break
    else:
        return []
    shown: List[Dict[str, Any]] = []

    def fresh(s: Dict[str, Any]) -> bool:
        return not any(is_similar_def(s["def"], t["def"]) for t in shown)

    forced = [s for s in pool if s.get("klass", 0) < 0]  # your own taught senses always come first
    shown += forced
    first = next((s for s in pool if s.get("klass", 0) >= 0 and fresh(s)), None)
    if first:
        shown.append(first)
        second = next((s for s in pool if s is not first and s["pos"] == first["pos"] and s["pos"] != "Slang"
                       and s.get("klass", 0) == first.get("klass", 0) and fresh(s)), None)
        other = next((s for s in pool if s["pos"] != first["pos"] and s["pos"] in MAJOR_POS and s.get("klass", 0) == 0
                      and not lt.tag_set(s.get("tags", [])) & lt.UNCOMMON_USAGE and fresh(s)), None)
        shown += [s for s in (second, other) if s]
        slang = next((s for s in pool if s["pos"] == "Slang" and s not in shown and fresh(s)), None)
        if slang and len(shown) < MAX_SENSES_SHOWN:
            shown.append(slang)
    return shown[:max(MAX_SENSES_SHOWN, len(forced))]


_BEFORE_NOUN = r"(?:a|an|the|this|that|these|those|my|your|his|her|its|our|their|some|any|no|every|each|one|another)"
_BEFORE_VERB = r"(?:to|i|you|we|they|he|she|it|who|that|will|would|can|could|shall|should|must|may|might|do|does|did|don't|didn't|doesn't|won't|can't|not|never|let's|gonna|wanna)"


def _fits_pos(sentence: str, pos: str, forms: List[str]) -> bool:
    """A rough test that the word is used as the part of speech of a sense in a sentence ("run" as a noun after "a",
    "the"; as a verb after "to", "I", "will" or in an inflected form), so that a verb sense is not shown with a noun's
    sentence. Adjectives, adverbs and the rest are not judged (True)."""
    low = sentence.lower()
    alt = "|".join(re.escape(f) for f in forms if f)
    if not alt or pos not in ("Noun", "Verb"):
        return True
    if pos == "Noun":
        return bool(re.search(rf"\b{_BEFORE_NOUN}\s+(?:\w+\s+)?(?:{alt})s?\b", low) or re.search(rf"\b(?:{alt})s\b", low))
    base = forms[0]
    inflected = [f for f in forms[1:] if f != base]
    if inflected and re.search(rf"\b(?:{'|'.join(re.escape(f) for f in inflected)})\b", low):
        return True
    return bool(re.search(rf"\b{_BEFORE_VERB}\s+(?:\w+\s+)?{re.escape(base)}\b", low))


def _assign_examples(shown: List[Dict[str, Any]], merged: Any, clean: str) -> None:
    """Gives each shown sense its example, in place: a subtitle line (OpenSubtitles, the primary source: natural
    dialogue, with its human Persian translation), else the sense's own Wiktionary example, else a Tatoeba
    sentence; never made up, never reused. Subtitle lines have no sense label, so the one that shares the most
    words with the definition goes to the sense; after the first sense, a subtitle line that shares none loses
    to the sense's own Wiktionary example (which is surely about that sense)."""
    containing = [e for e in merged.examples if sentence_contains_target(e["en"], clean)]
    forms = [clean.lower()] + [f["form"].lower() for f in merged.forms]
    subs = [e for e in containing if e.get("corpus") == "opensubtitles"]
    tato = [e for e in containing if e.get("corpus") != "opensubtitles"]
    used: set = set()
    for idx, s in enumerate(shown):
        s.setdefault("example_fa", "")
        s["example_source"] = ""
        own = s.get("example_en", "").strip()
        if s.get("klass", 0) < 0:  # taught by the user: as given
            s["example_source"] = "your entry" if own else ""
            if own:
                used.add(own.lower())
            continue
        gloss_words = lt.content_words(s["def"])
        free = [e for e in subs if e["en"].lower() not in used]
        best = max(free, key=lambda e: (_fits_pos(e["en"], s.get("pos", ""), forms), len(lt.content_words(e["en"]) & gloss_words)),
                   default=None)  # first on ties
        overlap = len(lt.content_words(best["en"]) & gloss_words) if best else 0
        s["example_en"], s["example_fa"] = "", ""
        if best and (overlap > 0 or idx == 0 or not own or own.lower() in used):
            s["example_en"], s["example_fa"], s["example_source"] = best["en"], best["fa"], "OpenSubtitles"
        elif own and own.lower() not in used:
            s["example_en"], s["example_source"] = own, "Wiktionary"
        else:
            pick = next((e for e in tato if e["en"].lower() not in used), None)
            if pick:
                s["example_en"], s["example_fa"], s["example_source"] = pick["en"], pick["fa"], "Tatoeba"
        if s["example_en"]:
            used.add(s["example_en"].lower())


def _sense_label(s: Dict[str, Any]) -> str:
    return f"{s['pos'].lower()}: {s['def'].rstrip('.')}"


def _is_ordinary_word(term: str) -> bool:
    """False for a word whose Wiktionary entry has only archaic, regional, specialist or name senses ("bonnie",
    "noetic"): it is no everyday synonym even if some subtitle line has it (a name). A term with no entry (a
    phrase) is left to the frequency test."""
    entries = [e for e in LEXICON.entries(term) if e["senses"]]
    if not entries:
        return True
    many_pos = len({e["pos"] for e in entries}) > 1
    return any(lt.sense_class(s.get("tags", []), s["def"], e["pos"], many_pos) < 2 for e in entries for s in e["senses"])


def _shown_relations(items: List[Dict[str, Any]], shown: List[Dict[str, Any]], clean: str, root_lemma: str,
                     limit: int) -> List[Dict[str, str]]:
    """Synonyms or antonyms of the senses actually shown, each group under its sense ("noun: A playing card..."):
    only words Wiktionary lists for that sense (or, for an entry-level word with a hint, for the sense the hint
    describes; without a hint, for the first ordinary sense of its part of speech), everyday words only (those
    Wiktionary labels archaic, rare, dated and the like, or nobody says in subtitles, are dropped), the most
    common first, a few per sense. Never filled up: fewer is fine, none is fine."""
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for it in items:
        idx = None
        if it["raw_def"]:
            idx = next((i for i, s in enumerate(shown) if s.get("raw_def") == it["raw_def"] and s["pos"] == it["pos"]), None)
        else:
            same_pos = [i for i, s in enumerate(shown) if s["pos"] == it["pos"] and s.get("raw_def") is not None]
            if it["hint"]:
                hint = lt.content_words(it["hint"])
                best = max(same_pos, key=lambda i: len(hint & lt.content_words(shown[i]["def"] + " " + shown[i]["raw_def"])), default=None)
                idx = best if best is not None and hint & lt.content_words(shown[best]["def"] + " " + shown[best]["raw_def"]) else None
            else:
                idx = next((i for i in same_pos if shown[i].get("klass", 0) == 0), None)
        if idx is not None:
            groups.setdefault(idx, []).append(it)
    out: List[Dict[str, str]] = []
    seen: set = set()
    for idx in sorted(groups):
        ranked = []
        for it in groups[idx]:
            term = " ".join(it["term"].split())
            if (not term or term.lower() in seen or term.lower() in WEAK_SYNONYMS or is_spelling_or_root_duplicate(term, clean, root_lemma)
                    or not lt.relation_tags_ok(it["tags"].split()) or not is_everyday_term(term) or not _is_ordinary_word(term)):
                continue
            seen.add(term.lower())
            ranked.append((-_term_frequency(term), len(ranked), term))
        for _, _, term in sorted(ranked)[:RELATIONS_PER_SENSE]:
            if len(out) < limit:
                out.append({"term": term, "sense": _sense_label(shown[idx])})
    return out


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
        "common_expressions": [],
        "forms": [],
        "alternatives": [],
        "morphology": _empty_morphology(clean),
        "synonyms": [],
        "antonyms": [],
    }


def _analyze_with_dictionaries(term: str, on_preview: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    raw_input = term.strip()
    if is_persian_text(raw_input):
        return analyze_persian(raw_input)
    words = raw_input.split()
    # Idioms first (spec 6.A): the whole phrase is looked up as typed, then normalised (it/someone/one's, a/the/no,
    # inflections) against the multi-word entries, before anything is split into words or taken for a sentence.
    match = LEXICON.match_phrase(raw_input) if len(words) >= 2 else None
    is_full_sentence = (len(words) >= 5 or (len(words) >= 4 and raw_input.endswith((".", "!", "?")))) and not match
    clean = auto_correct_english_input(raw_input, is_sentence=is_full_sentence)
    if is_full_sentence:
        return _analyze_sentence(clean)

    # --- WORD OR PHRASE ---
    is_idiom_or_phrase = len(words) >= 2
    typed = clean
    phrase_match: Optional[Dict[str, Any]] = None
    if match and lexicon_db.text_key(match["entry"]) != lexicon_db.text_key(clean):
        clean = match["entry"]  # the page is the dictionary's entry, not the typed variation of it
        phrase_match = {"typed": typed, "entry": clean, "dropped": match["dropped"], "negated": match["negated"]}
    morphology = analyze_morphology(clean) if not is_idiom_or_phrase else _empty_morphology(clean)
    root_lemma = morphology.get("root", clean)

    sources = _dictionary_sources()
    ctx = sources.LookupContext(term=clean, is_idiom=is_idiom_or_phrase, root_lemma=root_lemma,
                                typed=typed if phrase_match else "")
    fa_pool = ThreadPoolExecutor(max_workers=1)  # the headword's Persian, possibly slow (Google, the models)
    try:
        f_fa = submit_in_context(fa_pool, fetch_persian_word_meaning, typed, is_idiom_or_phrase)
        merged = sources.REGISTRY.run(ctx)  # every dictionary source that applies, merged in registry order
        data = _build_word_entry(clean, words, is_idiom_or_phrase, morphology, root_lemma, merged, f_fa, on_preview, typed)
        if phrase_match:
            data["phrase_match"] = phrase_match
        return data
    finally:
        fa_pool.shutdown(wait=False)


def _build_word_entry(clean: str, words: List[str], is_idiom_or_phrase: bool, morphology: Dict[str, Any], root_lemma: str,
                      merged: Any, f_fa: Any, on_preview: Optional[Callable[[Dict[str, Any]], None]],
                      typed: str = "") -> Dict[str, Any]:
    low = clean.lower()
    typed = typed or clean

    did_you_mean = ""
    if not merged.senses and len(words) == 1:
        close = LEXICON.close_matches(clean)
        if close and close[0].lower() != low:
            did_you_mean = close[0]

    ipa_us = merged.ipa_us or to_ipa_us(clean)

    priority_senses: List[Dict[str, Any]] = []
    if low in USER_MEMORY.get("entries", {}):
        ue = USER_MEMORY["entries"][low]
        if ue.get("def"):
            priority_senses.append({
                "pos": ue.get("pos", "Custom Entry"),
                "def": ue["def"],
                "example_en": ue.get("example_en", ""),
                "example_fa": ue.get("example_fa", ""),
                "klass": -1,
            })

    shown = choose_senses(priority_senses + merged.senses)
    _assign_examples(shown, merged, clean)
    selected_senses = [{
        "pos": "Idiom / Expression" if is_idiom_or_phrase and s.get("pos") != "Slang" else s.get("pos", "Noun"),
        "def": s["def"],
        "example_en": s["example_en"],
        "example_ipa": "",  # filled in by entry()
        "example_fa": s["example_fa"],
        "example_source": s["example_source"],
    } for s in shown]
    primary_pos = selected_senses[0]["pos"] if selected_senses else "Noun"

    colls_raw: List[Dict[str, str]] = []
    if not is_idiom_or_phrase:
        seen_col = set()
        for c in merged.collocations:
            t = c["term"].strip()
            if t.lower() not in seen_col and t.lower() != low:
                seen_col.add(t.lower())
                ex_en, ex_fa = (c["example_en"], "") if c["example_en"] else relation_example(t)
                colls_raw.append({"term": t, "example_en": ex_en, "example_fa": ex_fa})
            if len(colls_raw) >= 4:
                break

    syn_items = _shown_relations(merged.syns, shown, clean, root_lemma, MAX_SYNONYMS)
    ant_items = _shown_relations(merged.ants, shown, clean, root_lemma, MAX_ANTONYMS)
    with ThreadPoolExecutor(max_workers=6) as pool:
        ex_futs = {it["term"]: submit_in_context(pool, relation_example, it["term"], primary_pos) for it in syn_items + ant_items}
        for it in syn_items + ant_items:
            it["example_en"], it["example_fa"] = ex_futs[it["term"]].result()

    common: List[str] = []
    forms: List[Dict[str, str]] = []
    alternatives: List[str] = []
    if not is_idiom_or_phrase:
        common = common_expressions(clean, merged.derived, [c["term"] for c in merged.collocations])
        forms = [f for f in merged.forms if f["form"].lower() != low][:6]
        alternatives = [a for a in dict.fromkeys(merged.alternatives)
                        if a.isalpha() and a.lower() != low and a.lower() == a and is_everyday_word(a)][:MAX_ALTERNATIVES]
        morphology = dict(morphology, family=_word_family(
            root_lemma, low, merged.derived, merged.related,
            lambda w: next((e["pos"] for e in LEXICON.entries(w) if e["senses"]), "")))

    core_idiom_meaning_en = ""
    if is_idiom_or_phrase and selected_senses:
        core_idiom_meaning_en = clean_definition_for_idiom_meaning(selected_senses[0]["def"])

    def entry(fa_main: str, fa_source: str, trans_map: Dict[str, str], pending: str = "") -> Dict[str, Any]:
        def fa(en: str) -> str:
            return trans_map.get(en, "") or pending

        def related(item: Dict[str, str]) -> Dict[str, str]:
            ex = item["example_en"]
            out = {
                "term": item["term"],
                "ipa": to_ipa_us(item["term"]),
                "fa": fa(item["term"]),
                "example_en": ex,
                "example_ipa": (pending or sentence_to_ipa(ex)) if ex else "",
                "example_fa": (item.get("example_fa") or fa(ex)) if ex else "",
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
            "persian_equivalent": fa_main if fa_main and fa_main not in INVALID_FA_HEADERS else (pending or NOT_FOUND_FA),
            "persian_source": fa_source,
            "senses": [dict(s, example_fa=s.get("example_fa") or (fa(s["example_en"]) if s["example_en"] else ""),
                            example_ipa=(pending or sentence_to_ipa(s["example_en"])) if s["example_en"] else "")
                       for s in selected_senses],
            "sentence_breakdown": [],
            "collocations": [related(c) for c in colls_raw],
            "common_expressions": common,
            "forms": forms,
            "alternatives": alternatives,
            "morphology": morphology,
            "cefr_level": lookup_cefr_level(clean),
            "frequency_per_million": merged.frequency,
            "frequency_band": word_levels.frequency_band(merged.frequency),
            "synonyms": [related(it) for it in syn_items],
            "antonyms": [related(it) for it in ant_items],
        }

    if on_preview is not None:
        if f_fa.done():
            fa_now, source_now = f_fa.result()
        else:  # your edits, the cache and the local tier answer in milliseconds; the rest is still being asked
            fa_now = USER_MEMORY.get("overrides", {}).get(low) or USER_MEMORY.get("entries", {}).get(low, {}).get("fa", "")
            source_now = FA_SOURCE_EDIT if fa_now else ""
            if not fa_now:
                fa_now, source_now = quick_translation(typed, is_short=not is_idiom_or_phrase)
        preview = entry(fa_now, source_now, {tp["en"]: tp["fa"] for tp in merged.examples}, pending=PENDING_FA)
        preview["_preview"] = True
        on_preview(preview)

    fa_main, fa_source = f_fa.result()
    sentences_to_tr: List[str] = []
    short_terms_to_tr: List[str] = []
    if not fa_main:
        short_terms_to_tr.append(core_idiom_meaning_en or typed)
    for s in selected_senses:
        if s["example_en"] and not s.get("example_fa"):
            sentences_to_tr.append(s["example_en"])
    for item in colls_raw + syn_items + ant_items:
        short_terms_to_tr.append(item["term"])
        if item["example_en"] and not item.get("example_fa"):
            sentences_to_tr.append(item["example_en"])

    trans_map = translate_batch_fast(sentences_to_tr, short_terms_to_tr)

    if not fa_main or fa_main in INVALID_FA_HEADERS:
        fa_main = trans_map.get(core_idiom_meaning_en or typed, "")
        fa_source = ""  # whichever tier the batch used
    for s in selected_senses:
        if s["example_en"] and not s.get("example_fa"):
            s["example_fa"] = trans_map.get(s["example_en"], "")

    return entry(fa_main, fa_source, trans_map)
