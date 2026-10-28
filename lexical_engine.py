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
from contextlib import closing
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Tuple
import requests
from requests.adapters import HTTPAdapter


def _get_engine_base_dir() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "offline_lexicon.json")):
            return exe_dir
        parent = os.path.abspath(os.path.join(exe_dir, "..", ".."))
        if os.path.exists(os.path.join(parent, "offline_lexicon.json")):
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


USER_MEMORY_FILE = user_data_path("user_memory.json")
OFFLINE_LEXICON_FILE = os.path.join(ENGINE_BASE_DIR, "offline_lexicon.json")
OFFLINE_CORE_DB = os.path.join(ENGINE_BASE_DIR, "ocean_offline_core.db")
OFFLINE_MT_DIR = os.path.join(ENGINE_BASE_DIR, "offline_mt_model")

try:
    import nltk
    for cand_nltk in (
        os.path.join(ENGINE_BASE_DIR, "nltk_data"),
        os.path.join(os.path.dirname(sys.executable), "nltk_data") if getattr(sys, "frozen", False) else "",
        os.path.join(os.path.dirname(sys.executable), "_internal", "nltk_data") if getattr(sys, "frozen", False) else "",
    ):
        if cand_nltk and os.path.isdir(cand_nltk) and cand_nltk not in nltk.data.path:
            nltk.data.path.insert(0, cand_nltk)
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


OFFLINE_LEXICON = load_json_safe(OFFLINE_LEXICON_FILE, {"idioms": {}, "words": {}, "curated_senses": {}})
USER_MEMORY = load_json_safe(USER_MEMORY_FILE, {"entries": {}, "overrides": {}})


def query_offline_core_lexicon(term: str) -> str:
    if not os.path.exists(OFFLINE_CORE_DB):
        return ""
    try:
        with closing(sqlite3.connect(OFFLINE_CORE_DB)) as conn:
            cur = conn.execute("SELECT fa FROM en_fa_lexicon WHERE term = ?", (term.strip().lower(),))
            row = cur.fetchone()
            return row[0] if row else ""
    except Exception:
        _log_failure("Offline core lexicon query failed")
        return ""


def query_offline_bnc_collocations(headword: str) -> List[Dict[str, str]]:
    if not os.path.exists(OFFLINE_CORE_DB):
        return []
    try:
        with closing(sqlite3.connect(OFFLINE_CORE_DB)) as conn:
            cur = conn.execute(
                "SELECT collocation, example_en FROM bnc_collocations WHERE headword = ?",
                (headword.strip().lower(),),
            )
            return [{"term": r[0], "example_en": r[1]} for r in cur.fetchall()]
    except Exception:
        _log_failure("Offline collocation query failed")
        return []


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


def translate_offline_backup(text: str, is_short: bool = False) -> str:
    """Used strictly as a backup when internet connection is unavailable."""
    clean = text.strip()
    if not clean:
        return ""
    db_hit = query_offline_core_lexicon(clean.lower())
    if db_hit:
        return normalize_persian(db_hit, is_short_term=is_short, source_en=clean)
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

INVALID_FA_HEADERS = {
    "\u0645\u0639\u0646\u06cc", "\u0645\u0639\u0627\u0646\u06cc", "\u062a\u0631\u062c\u0645\u0647",
    "\u062a\u0644\u0641\u0638", "\u0645\u062a\u0631\u0627\u062f\u0641", "\u0641\u0631\u0647\u0646\u06af \u0644\u063a\u062a",
    "\u0627\u0635\u0637\u0644\u0627\u062d", "\u06a9\u0644\u0645\u0647", "\u0639\u0628\u0627\u0631\u062a",
    "\u06af\u0627\u062f", "\u0633\u0627\u0646\u06af", "\u0628\u06cc \u0644\u06cc\u062a", "\u067e\u0644\u0627\u062a\u0648\u0646",
    "\u06af\u0648\u067e", "\u06af\u0627\u0646\u06a9", "\u06af\u0648"
}

CURATED_SYNONYMS_MAP = {
    "gallivant": ["roam", "wander", "ramble"],
    "beseech": ["implore", "entreat", "beg"],
    "snug": ["tight", "close-fitting", "cozy"],
    "bashful": ["shy", "timid", "diffident"],
    "platoon": ["squad", "troop", "detachment"],
    "purse": ["handbag", "pouch"],
    "gunk": ["grime", "sludge", "muck"],
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


def smart_request(method: str, url: str, **kwargs) -> requests.Response:
    """Unblocked hybrid request: tries normal pooled session first, then direct session.
    The single choke point for the network: offline-only mode stops every request here."""
    if _OFFLINE_ONLY:
        raise OfflineModeError(f"Offline-only mode is on; not contacting {url}")
    kwargs.setdefault("timeout", 3.0)
    try:
        return POOLED_SESSION.request(method, url, **kwargs)
    except Exception:
        return DIRECT_SESSION.request(method, url, **kwargs)


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
    t = t.replace("\u067e\u0644\u0627\u062a\u0648\u0646", "\u062f\u0633\u062a\u0647 \u0633\u0631\u0628\u0627\u0632\u0627\u0646")
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


# === SECTION 6: WIKIDATA / WIKIPEDIA INTERLANGUAGE LINKS & HYBRID TRANSLATOR ===
def fetch_wikipedia_persian_interlanguage_title(term: str) -> str:
    clean = term.strip()
    if _OFFLINE_ONLY or not clean or len(clean.split()) > 4:
        return ""
    try:
        r = smart_request(
            "GET",
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "titles": clean,
                "prop": "langlinks",
                "lllang": "fa",
                "redirects": "1",
                "format": "json",
            },
            timeout=2.2,
        )
        if r.ok:
            pages = r.json().get("query", {}).get("pages", {})
            for p_info in pages.values():
                lls = p_info.get("langlinks", [])
                if lls and lls[0].get("*"):
                    fa_title = normalize_persian(lls[0]["*"], is_short_term=True)
                    fa_title = re.sub(r"\s*\([^)]*\)", "", fa_title).strip()
                    if fa_title:
                        return fa_title
    except Exception:
        _log_failure(f"Wikipedia title lookup failed for {term!r}")
    return ""


def clean_definition_for_idiom_meaning(def_text: str) -> str:
    s = def_text.strip().rstrip(".")
    s = re.sub(r"^(To|Being|Feeling|Something that is|A|An)\s+", "", s, flags=re.IGNORECASE)
    s = re.split(r"[;:]|\s+,\s+or\s+|\s+so that\s+|\s+because\s+", s)[0].strip()
    return s


def substitute_idiom_in_sentence(sentence: str, idiom: str, meaning_en: str) -> str:
    if not sentence or not idiom or not meaning_en:
        return sentence
    words = idiom.strip().split()
    if len(words) < 2:
        return sentence

    first_w = words[0].lower()
    verb_variants = {
        "bite": r"(?:bite|bites|bit|bitten|biting)",
        "cut": r"(?:cut|cuts|cutting)",
        "spill": r"(?:spill|spills|spilled|spilt|spilling)",
        "let": r"(?:let|lets|letting)",
        "break": r"(?:break|breaks|broke|broken|breaking)",
        "hit": r"(?:hit|hits|hitting)",
        "call": r"(?:call|calls|called|calling)",
        "cost": r"(?:cost|costs|costing)",
        "follow": r"(?:follow|follows|followed|following)",
        "take": r"(?:take|takes|took|taken|taking)",
        "get": r"(?:get|gets|got|gotten|getting)",
        "seek": r"(?:seek|seeks|sought|seeking)",
    }
    first_pat = verb_variants.get(first_w, rf"{re.escape(first_w)}\w*")
    rest_pat = r"\s+".join(rf"{re.escape(w)}\w*" for w in words[1:])
    full_pat = re.compile(rf"\b{first_pat}\s+{rest_pat}\b", re.IGNORECASE)

    if full_pat.search(sentence):
        return full_pat.sub(meaning_en, sentence)
    return sentence


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


def translate_single_string(text: str, is_short: bool = False) -> str:
    clean = text.strip()
    if not clean:
        return ""
    low = clean.lower()

    if low in USER_MEMORY.get("overrides", {}):
        return USER_MEMORY["overrides"][low]
    if low in OFFLINE_LEXICON.get("idioms", {}):
        return OFFLINE_LEXICON["idioms"][low]["fa"]
    if low in OFFLINE_LEXICON.get("words", {}):
        return OFFLINE_LEXICON["words"][low]

    # 1. Primary: Google GTX Bilingual Dictionary & Translator (never in offline-only mode)
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

    # 2. Secondary: Clients5 Endpoint
    c5 = translate_via_clients5(clean, is_short=is_short)
    if c5:
        return c5

    # 3. Third: WordNet Gloss Semantic Translation for slang/rare words
    if is_short and wn and len(clean.split()) <= 2:
        syns = wn.synsets("_".join(clean.split()))
        if syns:
            gloss = syns[0].definition().split(";")[0].strip()
            gloss = re.sub(r"^\([^)]+\)\s*", "", gloss)
            tr_g = translate_offline_backup(gloss) if _OFFLINE_ONLY else translate_via_clients5(gloss, is_short=False)
            if tr_g:
                return normalize_persian(tr_g, is_short_term=True)

    # 4. Fourth: Local Offline SQLite & CTranslate2 Backup (when internet is disconnected)
    off_b = translate_offline_backup(clean, is_short=is_short)
    if off_b:
        return off_b

    return "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f" if is_short else ""


def fetch_persian_word_meaning(term: str, is_idiom: bool = False) -> str:
    clean = term.strip()
    low = clean.lower()

    if low in USER_MEMORY.get("overrides", {}):
        return USER_MEMORY["overrides"][low]
    if low in USER_MEMORY.get("entries", {}):
        return USER_MEMORY["entries"][low].get("fa", "")
    if low in OFFLINE_LEXICON.get("idioms", {}):
        return OFFLINE_LEXICON["idioms"][low]["fa"]
    if low in OFFLINE_LEXICON.get("words", {}):
        return OFFLINE_LEXICON["words"][low]

    if is_idiom and len(clean.split()) <= 3:
        wiki_fa = fetch_wikipedia_persian_interlanguage_title(clean)
        if wiki_fa:
            return wiki_fa

    return translate_single_string(clean, is_short=not is_idiom)


def auto_fix_translation_deep(en_text: str) -> str:
    clean = en_text.strip()
    if not clean:
        return ""

    c5 = translate_via_clients5(clean, is_short=False)
    if c5 and "..." not in c5 and "\u06cc\u0627\u0641\u062a \u0646\u0634\u062f" not in c5:
        record_user_override(clean, c5)
        return c5

    gtx = translate_single_string(clean, is_short=False)
    if gtx and "..." not in gtx and "\u06cc\u0627\u0641\u062a \u0646\u0634\u062f" not in gtx:
        record_user_override(clean, gtx)
        return gtx

    words = clean.split()
    if len(words) <= 3 and wn:
        syns = wn.synsets("_".join(words))
        if syns:
            gloss = syns[0].definition().split(";")[0].strip()
            tr_gloss = translate_via_clients5(gloss, is_short=False) or translate_single_string(gloss, is_short=False)
            if tr_gloss and "\u06cc\u0627\u0641\u062a \u0646\u0634\u062f" not in tr_gloss:
                record_user_override(clean, tr_gloss)
                return tr_gloss

    return "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f"


def translate_batch_fast(sentences: List[str], short_terms: List[str]) -> Dict[str, str]:
    all_items = [x.strip() for x in dict.fromkeys(sentences + short_terms) if x and x.strip()]
    if not all_items:
        return {}

    short_set = {x.strip() for x in short_terms}
    out: Dict[str, str] = {}
    remaining_sentences: List[str] = []
    remaining_shorts: List[str] = []

    for item in all_items:
        low = item.lower()
        if low in USER_MEMORY.get("overrides", {}):
            out[item] = USER_MEMORY["overrides"][low]
        elif low in OFFLINE_LEXICON.get("idioms", {}):
            out[item] = OFFLINE_LEXICON["idioms"][low]["fa"]
        elif low in OFFLINE_LEXICON.get("words", {}):
            out[item] = OFFLINE_LEXICON["words"][low]
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
    if still_missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {item: pool.submit(translate_single_string, item, item in short_set) for item in still_missing}
            for item, fut in futs.items():
                res = fut.result()
                out[item] = res or ("\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f" if item in short_set else "")

    return out


# === SECTION 7: SENTENCE KEY-TERM EXTRACTOR & MULTI-DICTIONARY ANALYZERS ===
def extract_sentence_key_terms(sentence: str) -> List[Dict[str, str]]:
    clean = re.sub(r"[^a-zA-Z\s\-']", " ", sentence).strip()
    tokens = [t for t in clean.split() if t]
    extracted: List[Dict[str, str]] = []
    seen = set()
    low_sent = sentence.lower()

    for idiom_key, info in OFFLINE_LEXICON.get("idioms", {}).items():
        if idiom_key in low_sent and idiom_key not in seen:
            seen.add(idiom_key)
            extracted.append({
                "term": idiom_key,
                "ipa": to_ipa_us(idiom_key),
                "pos": "Idiom / Compound",
                "def": info["def"],
                "fa": info["fa"],
            })

    if wn and len(tokens) >= 2:
        for i in range(len(tokens) - 1):
            pair = f"{tokens[i]} {tokens[i+1]}"
            pair_low = pair.lower()
            if pair_low in seen:
                continue
            syns = wn.synsets(f"{tokens[i].lower()}_{tokens[i+1].lower()}")
            if syns:
                seen.add(pair_low)
                seen.add(tokens[i].lower())
                seen.add(tokens[i+1].lower())
                pos_map = {"n": "Noun", "v": "Verb", "a": "Adjective", "s": "Adjective", "r": "Adverb"}
                fa_eq = fetch_persian_word_meaning(pair_low, is_idiom=True)
                extracted.append({
                    "term": pair_low,
                    "ipa": to_ipa_us(pair_low),
                    "pos": pos_map.get(syns[0].pos(), "Compound Term"),
                    "def": clean_english_sentence(syns[0].definition()),
                    "fa": fa_eq,
                })

    if wn:
        pos_map = {"n": "Noun", "v": "Verb", "a": "Adjective", "s": "Adjective", "r": "Adverb"}
        for tok in tokens:
            low_t = tok.lower().strip("-'")
            if len(low_t) < 3 or low_t in STOPWORDS or low_t in seen:
                continue
            lemma = LEMMATIZER.lemmatize(low_t, "v") if LEMMATIZER else low_t
            if lemma == low_t and LEMMATIZER:
                lemma = LEMMATIZER.lemmatize(low_t, "n")
            if lemma in STOPWORDS or lemma in seen:
                continue
            syns = wn.synsets(lemma) or wn.synsets(low_t)
            if syns:
                seen.add(low_t)
                seen.add(lemma)
                s0 = syns[0]
                fa_w = fetch_persian_word_meaning(lemma, is_idiom=False)
                extracted.append({
                    "term": lemma,
                    "ipa": to_ipa_us(lemma),
                    "pos": pos_map.get(s0.pos(), "Word"),
                    "def": clean_english_sentence(s0.definition()),
                    "fa": fa_w,
                })
            if len(extracted) >= 5:
                break

    return extracted[:5]


def _dictionary_sources():
    """The dictionary_sources package (imported lazily: its modules import this one for the text helpers)."""
    import dictionary_sources
    return dictionary_sources


def fetch_live_suggestions(prefix: str) -> List[str]:
    q = prefix.strip()
    if len(q) < 2 or len(q.split()) > 5:
        return []
    user_keys = [k for k in USER_MEMORY.get("entries", {}) if k.startswith(q.lower())]
    local_idioms = [k for k in OFFLINE_LEXICON.get("idioms", {}) if k.startswith(q.lower())]
    if is_persian_text(q):
        return []  # Datamuse is English-only; the app still offers matching history entries
    sources = _dictionary_sources()
    datamuse = sources.REGISTRY.usable("datamuse", sources.LookupContext(term=q, offline=_OFFLINE_ONLY))
    remote = datamuse.suggest(q) if datamuse else []
    return list(dict.fromkeys(user_keys + local_idioms + remote))[:7]


def generate_pos_matched_example(term: str, pos: str) -> str:
    w = term.strip().lower()
    p = pos.lower()
    if "verb" in p:
        return f"They decided to {w} the matter carefully before acting."
    if "adj" in p:
        return f"The gear felt secure and {w} during the entire journey."
    if "adv" in p:
        return f"She acted {w} to make sure every detail was clear."
    if "idiom" in p or "phrase" in p or " " in w:
        return f"In challenging moments, sometimes you have to {w} and move forward."
    return f"The {w} had to be cleaned thoroughly before we could continue."


def fetch_verified_example_for_relation(word: str, target_pos: str = "") -> str:
    clean = word.strip()
    low = clean.lower()
    if low in OFFLINE_LEXICON.get("idioms", {}):
        return OFFLINE_LEXICON["idioms"][low]["example"]

    pos_code_map = {"Noun": "n", "Verb": "v", "Adjective": "a", "Adverb": "r"}
    want_pos = pos_code_map.get(target_pos, "")

    if wn:
        try:
            synsets = wn.synsets("_".join(clean.split()))
            for syn in synsets:
                if want_pos and syn.pos().replace("s", "a") != want_pos:
                    continue
                for ex in syn.examples():
                    if sentence_contains_target(ex, clean) and len(ex.split()) >= 5 and "'" not in ex:
                        return clean_english_sentence(ex)
            for syn in synsets[:4]:
                for ex in syn.examples():
                    if sentence_contains_target(ex, clean) and len(ex.split()) >= 5 and "'" not in ex:
                        return clean_english_sentence(ex)
        except Exception:
            _log_failure(f"WordNet example lookup failed for {word!r}")

    sources = _dictionary_sources()
    api = sources.REGISTRY.usable("dictionaryapi", sources.LookupContext(term=clean, offline=_OFFLINE_ONLY))
    if api:
        example = api.find_example(clean, target_pos)
        if example:
            return example

    return generate_pos_matched_example(clean, target_pos or "Noun")


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
    """English terms whose stored Persian meaning matches: your fixes and taught words first,
    then Ocean's bundled lexicon. Meanings are compared per comma-separated part."""
    target = fa_key(fa)
    if not target:
        return []
    pairs = list(USER_MEMORY.get("overrides", {}).items())
    pairs += [(en, d.get("fa", "")) for en, d in OFFLINE_LEXICON.get("idioms", {}).items()]
    pairs += [(en, v if isinstance(v, str) else "") for en, v in OFFLINE_LEXICON.get("words", {}).items()]
    hits = []
    for en, fa_val in pairs:
        if any(fa_key(part) == target for part in re.split(r"[\u060c,]", fa_val or "")):
            hits.append(en)
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
        # Curated meanings (your fixes, taught words, Ocean's idiom list) beat literal machine output,
        # e.g. an idiom Google translates word for word.
        translation = local[0]
    found = [{"term": en, "pos": "your entries & Ocean lexicon"} for en in local]
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


def analyze_with_dictionaries(term: str, deep_mode: bool = False) -> Dict[str, Any]:
    raw_input = term.strip()
    if is_persian_text(raw_input):
        return analyze_persian(raw_input)
    words = raw_input.split()
    is_full_sentence = len(words) >= 5 or (len(words) >= 4 and raw_input.endswith((".", "!", "?")))
    clean = auto_correct_english_input(raw_input, is_sentence=is_full_sentence)
    low = clean.lower()

    # --- DEDICATED FULL-SENTENCE MODE ---
    if is_full_sentence:
        key_terms = extract_sentence_key_terms(clean)
        contextual_en = clean
        for kt in key_terms:
            t_low = kt["term"].lower()
            if t_low in OFFLINE_LEXICON.get("idioms", {}):
                meaning_hint = OFFLINE_LEXICON["idioms"][t_low]["meaning_en"]
                contextual_en = re.sub(rf"\b{re.escape(t_low)}\b", meaning_hint, contextual_en, flags=re.IGNORECASE)

        fa_sent = USER_MEMORY.get("overrides", {}).get(low, "")
        if not fa_sent:
            fa_sent = translate_via_clients5(contextual_en, is_short=False) or translate_single_string(contextual_en, is_short=False)

        return {
            "headword": clean,
            "is_sentence": True,
            "is_idiom": True,
            "did_you_mean": "",
            "ipa_us": sentence_to_ipa(clean),
            "persian_equivalent": fa_sent or "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f",
            "senses": [],
            "sentence_breakdown": key_terms,
            "collocations": [],
            "morphology": {"root": clean, "structure": clean, "prefix": "None", "suffix": "None", "family": []},
            "synonyms": [],
            "antonyms": [],
        }

    # --- STANDARD WORD OR IDIOM HYBRID MODE ---
    is_idiom_or_phrase = len(words) >= 2
    morphology = analyze_morphology(clean) if not is_idiom_or_phrase else {
        "root": clean, "structure": clean, "prefix": "None", "suffix": "None", "family": []
    }
    root_lemma = morphology.get("root", clean)

    sources = _dictionary_sources()
    ctx = sources.LookupContext(
        term=clean, is_idiom=is_idiom_or_phrase, deep_mode=deep_mode, root_lemma=root_lemma, offline=_OFFLINE_ONLY
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        f_fa = pool.submit(fetch_persian_word_meaning, clean, is_idiom_or_phrase)
        merged = sources.REGISTRY.run(ctx)  # every enabled dictionary source, merged in registry order
        fa_main = f_fa.result()

    did_you_mean = ""
    if not merged.senses and len(words) == 1:
        sugs = fetch_live_suggestions(clean)
        if sugs and sugs[0].lower() != low:
            did_you_mean = sugs[0]

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
    if low in OFFLINE_LEXICON.get("curated_senses", {}):
        priority_senses.extend(OFFLINE_LEXICON["curated_senses"][low])

    all_raw_senses = priority_senses + merged.senses

    used_examples = set()
    selected_senses: List[Dict[str, str]] = []

    for s in all_raw_senses:
        d_txt = s["def"]
        if is_grammatical_form_of_gloss(d_txt) or any(is_similar_def(d_txt, ex["def"]) for ex in selected_senses):
            continue

        pos_label = "Idiom / Expression" if is_idiom_or_phrase else s.get("pos", "Noun")
        ex_en = s.get("example_en", "").strip()
        ex_fa_preset = s.get("example_fa", "").strip()

        if ex_en and ex_en.lower() in used_examples:
            ex_en = ""
            ex_fa_preset = ""

        if not ex_en and merged.examples:
            for tp in merged.examples:
                if tp["en"].lower() not in used_examples:
                    ex_en = tp["en"]
                    ex_fa_preset = tp["fa"]
                    break

        if not ex_en:
            ex_en = generate_pos_matched_example(clean, pos_label)
        used_examples.add(ex_en.lower())

        selected_senses.append({
            "pos": pos_label,
            "def": d_txt,
            "example_en": ex_en,
            "example_ipa": sentence_to_ipa(ex_en),
            "example_fa": ex_fa_preset,
        })
        if len(selected_senses) >= 3:
            break

    existing_pos = {s["pos"] for s in selected_senses}
    for s in all_raw_senses:
        s_pos = s.get("pos", "")
        if s_pos and s_pos not in existing_pos and not is_idiom_or_phrase:
            ex_en = s.get("example_en", "").strip() or generate_pos_matched_example(clean, s_pos)
            selected_senses.append({
                "pos": s_pos,
                "def": s["def"],
                "example_en": ex_en,
                "example_ipa": sentence_to_ipa(ex_en),
                "example_fa": s.get("example_fa", ""),
            })
            existing_pos.add(s_pos)
            break

    primary_pos = selected_senses[0]["pos"] if selected_senses else "Noun"

    # Collocations: Merge Longman + Local BNC Collocations + Datamuse POS-matched Collocations
    colls_raw: List[Dict[str, str]] = []
    if not is_idiom_or_phrase:
        seen_col = set()
        for c in merged.collocations + query_offline_bnc_collocations(clean):
            t = c["term"].strip()
            if t.lower() not in seen_col and t.lower() != low:
                seen_col.add(t.lower())
                colls_raw.append({"term": t, "example_en": c["example_en"]})

        col_templates = [
            "The situation required a {0} to succeed.",
            "Everyone noticed the {0} right away.",
            "Having a {0} made a significant difference.",
        ]
        for p in merged.collocation_terms:
            if len(colls_raw) >= 3:
                break
            if p.lower() not in seen_col:
                seen_col.add(p.lower())
                ex_c = col_templates[len(colls_raw) % len(col_templates)].format(p)
                colls_raw.append({"term": p, "example_en": ex_c})
        colls_raw = colls_raw[:3]

    syns_words = [
        w for w in dict.fromkeys(merged.syns)
        if not is_spelling_or_root_duplicate(w, clean, root_lemma) and w.lower() not in WEAK_SYNONYMS
    ][:3]
    ants_words = [
        w for w in dict.fromkeys(merged.ants)
        if not is_spelling_or_root_duplicate(w, clean, root_lemma) and w.lower() not in WEAK_SYNONYMS
    ][:3]

    with ThreadPoolExecutor(max_workers=6) as pool:
        syn_ex_futs = {w: pool.submit(fetch_verified_example_for_relation, w, primary_pos) for w in syns_words}
        ant_ex_futs = {w: pool.submit(fetch_verified_example_for_relation, w, primary_pos) for w in ants_words}
        syns_raw = [{"term": w, "example_en": syn_ex_futs[w].result()} for w in syns_words]
        ants_raw = [{"term": w, "example_en": ant_ex_futs[w].result()} for w in ants_words]

    core_idiom_meaning_en = ""
    if is_idiom_or_phrase:
        if low in OFFLINE_LEXICON.get("idioms", {}):
            core_idiom_meaning_en = OFFLINE_LEXICON["idioms"][low]["meaning_en"]
        elif selected_senses:
            core_idiom_meaning_en = clean_definition_for_idiom_meaning(selected_senses[0]["def"])

    sentences_to_tr: List[str] = []
    short_terms_to_tr: List[str] = []
    sentence_sub_map: Dict[str, str] = {}

    if not fa_main:
        if is_idiom_or_phrase and core_idiom_meaning_en:
            short_terms_to_tr.append(core_idiom_meaning_en)
        else:
            short_terms_to_tr.append(clean)

    for s in selected_senses:
        ex_orig = s["example_en"]
        if ex_orig and not s.get("example_fa"):
            if is_idiom_or_phrase and core_idiom_meaning_en:
                s_meaning = clean_definition_for_idiom_meaning(s["def"]) or core_idiom_meaning_en
                ex_sub = substitute_idiom_in_sentence(ex_orig, clean, s_meaning)
                sentence_sub_map[ex_orig] = ex_sub
                sentences_to_tr.append(ex_sub)
            else:
                sentence_sub_map[ex_orig] = ex_orig
                sentences_to_tr.append(ex_orig)

    for c in colls_raw:
        short_terms_to_tr.append(c["term"])
        if c["example_en"]:
            sentences_to_tr.append(c["example_en"])
    for syn in syns_raw:
        short_terms_to_tr.append(syn["term"])
        if syn["example_en"]:
            sentences_to_tr.append(syn["example_en"])
    for ant in ants_raw:
        short_terms_to_tr.append(ant["term"])
        if ant["example_en"]:
            sentences_to_tr.append(ant["example_en"])

    trans_map = translate_batch_fast(sentences_to_tr, short_terms_to_tr)

    if not fa_main or fa_main in INVALID_FA_HEADERS:
        if deep_mode:
            fa_main = auto_fix_translation_deep(clean)
        elif is_idiom_or_phrase and core_idiom_meaning_en:
            fa_main = trans_map.get(core_idiom_meaning_en, "")
        else:
            fa_main = trans_map.get(clean, "")

    for s in selected_senses:
        ex_orig = s["example_en"]
        if ex_orig and not s.get("example_fa"):
            if low in OFFLINE_LEXICON.get("idioms", {}) and ex_orig == OFFLINE_LEXICON["idioms"][low]["example"]:
                s["example_fa"] = OFFLINE_LEXICON["idioms"][low]["example_fa"]
            else:
                sub_key = sentence_sub_map.get(ex_orig, ex_orig)
                s["example_fa"] = trans_map.get(sub_key, "")

    return {
        "headword": clean,
        "is_sentence": False,
        "is_idiom": is_idiom_or_phrase,
        "did_you_mean": did_you_mean,
        "ipa_us": ipa_us,
        "persian_equivalent": fa_main or "\u062a\u0631\u062c\u0645\u0647 \u06cc\u0627\u0641\u062a \u0646\u0634\u062f",
        "senses": selected_senses,
        "sentence_breakdown": [],
        "collocations": [
            {
                "term": c["term"],
                "ipa": to_ipa_us(c["term"]),
                "fa": trans_map.get(c["term"], ""),
                "example_en": c["example_en"],
                "example_ipa": sentence_to_ipa(c["example_en"]),
                "example_fa": trans_map.get(c["example_en"], ""),
            }
            for c in colls_raw
        ],
        "morphology": morphology,
        "synonyms": [
            {
                "term": syn["term"],
                "ipa": to_ipa_us(syn["term"]),
                "fa": trans_map.get(syn["term"], ""),
                "example_en": syn["example_en"],
                "example_ipa": sentence_to_ipa(syn["example_en"]) if syn["example_en"] else "",
                "example_fa": trans_map.get(syn["example_en"], "") if syn["example_en"] else "",
            }
            for syn in syns_raw
        ],
        "antonyms": [
            {
                "term": ant["term"],
                "ipa": to_ipa_us(ant["term"]),
                "fa": trans_map.get(ant["term"], ""),
                "example_en": ant["example_en"],
                "example_ipa": sentence_to_ipa(ant["example_en"]) if ant["example_en"] else "",
                "example_fa": trans_map.get(ant["example_en"], "") if ant["example_en"] else "",
            }
            for ant in ants_raw
        ],
    }
