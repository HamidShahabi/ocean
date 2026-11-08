"""Text rules shared by the import tool (tools/build_lexicon_db.py), the database reader (lexicon_db.py) and the
lookup engine: what makes a usable example sentence, how a definition is shortened and ranked, how an idiom
is reduced to a key that matches its dictionary form, and the one IPA notation shown everywhere.

Pure functions, no Qt, no database and no lexical_engine: the import tool applies them when it writes a
sense, the engine applies them again to what it reads (a database built by an older version still holds
whatever its importer let through).
"""
import re
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

# --- Example sentences ------------------------------------------------------------------------
MAX_EXAMPLE_WORDS = 20        # a Wiktionary usage example
MAX_SUBTITLE_WORDS = 16       # a subtitle line: short, natural dialogue
MIN_EXAMPLE_WORDS = 3
MIN_QUOTATION_YEAR = 1900     # older quotations carry long-s OCR damage and old spelling
_NOT_ENGLISH_SCRIPT = re.compile("[" + "".join(chr(a) + "-" + chr(b) for a, b in (
    (0x370, 0x3ff), (0x400, 0x4ff), (0x590, 0x6ff), (0x900, 0xdff), (0x3040, 0x9fff), (0xac00, 0xd7af))) + "]")  # Greek, Cyrillic, Hebrew, Arabic, Indic, CJK, Hangul
_MARKUP = re.compile(r"#|\[\[|\]\]|\{\{|\}\}|''|<[^>]*>|\|")
_VERSE_BREAK = re.compile(r"\s/\s|/\s*$|^\s*/")
_ELLIPSIS_NOTE = re.compile(r"\[\s*(?:…|\.{2,}|…)\s*\]|\[\s*translat", re.IGNORECASE)
_BRACKET_NOTE = re.compile(r"\s*\[[^\]]{0,40}\]")
_PLURAL_ARTIFACT = re.compile(r"(?<=\w)\((?:s|es|d|ed|ing)\)")
_OCR_CAPITAL_W = re.compile(r"\bVV[a-z]")
_YEAR_PREFIX = re.compile(r"\s*\(?\d{4}\b")
_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
ARCHAIC_WORDS = {
    "thou", "thee", "thy", "thine", "hath", "doth", "dost", "hast", "wilt", "shalt", "canst", "wouldst", "couldst",
    "shouldst", "ere", "wherefore", "whither", "hither", "thither", "whence", "nay", "yea", "ye", "verily", "oft",
    "methinks", "prithee", "anon", "forsooth", "betwixt", "doeth", "saith", "goeth", "'tis", "'twas", "'twere", "o'er", "e'er", "ne'er",
}
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def quotation_year(ref: str) -> Optional[int]:
    """The year a Wiktionary quotation reference ("1997, Erin McKean, speech ...") gives, None when it has none."""
    m = _YEAR.search(str(ref or ""))
    return int(m.group(1)) if m else None


def _upper_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def clean_example(text: str, quotation: bool = False, year: Optional[int] = None, max_words: int = MAX_EXAMPLE_WORDS,
                  is_known: Optional[Callable[[str], bool]] = None, target: str = "") -> str:
    """The text as an example sentence to show (and to translate), or "" when it is not one.

    Rejected: quotations without a modern date, long-s and other OCR damage (ſ, "VVere"), old-English words,
    elisions of text ("[…]", "[translating X]"), verse breaks ("/"), wiki markup (#, [[, '', {{), "(s)" style
    placeholders are cleaned instead, ALL-CAPS, text in another script, fragments and over-long text. With
    `is_known` (does the dictionary know this lower-case word?) any other unknown word rejects it too: garbled
    spelling ("poffeft", "loſe" read as "lofe") is exactly that. Words sharing their first four letters with
    `target` are excused, so a rare headword's own variants are fine."""
    s = " ".join(str(text or "").split())
    if not s or "ſ" in s or _NOT_ENGLISH_SCRIPT.search(s) or _OCR_CAPITAL_W.search(s):
        return ""
    if quotation and (year is None or year < MIN_QUOTATION_YEAR):
        return ""
    if _MARKUP.search(s) or _VERSE_BREAK.search(s) or _ELLIPSIS_NOTE.search(s) or _YEAR_PREFIX.match(s):
        return ""
    if quotation and ("[" in s or "]" in s):
        return ""
    s = _PLURAL_ARTIFACT.sub("", s)
    s = _BRACKET_NOTE.sub("", s)  # "[sic]"-style notes in a usage example
    s = s.strip(" -–—*•")
    words = s.split()
    if not (MIN_EXAMPLE_WORDS <= len(words) <= max_words) or len(s) > 12 * max_words:
        return ""
    if _upper_ratio(s) > 0.6 and sum(c.isalpha() for c in s) >= 6:
        return ""
    if {w.lower() for w in _WORD.findall(s)} & ARCHAIC_WORDS:
        return ""
    if is_known is not None and unknown_words(s, is_known, target):
        return ""
    return s


def unknown_words(text: str, is_known: Callable[[str], bool], target: str = "") -> List[str]:
    """Lower-case words of three or more letters the dictionary does not know (names and sentence-initial words
    are skipped; so are words that start like the target, whatever their ending)."""
    excused = {w.lower()[:4] for w in _WORD.findall(target) if len(w) >= 4}
    out = []
    for tok in re.findall(r"[A-Za-z]+(?:'[a-z]+)?", text):
        if tok[0].isupper() or len(tok) < 3 or tok.lower()[:4] in excused:
            continue
        low = tok.lower()
        if "'" in low and is_known(low.split("'")[0]):
            continue  # "don't", "she's": known by their stem
        if not is_known(low):
            out.append(low)
    return out


# Strong profanity: a line with it is a poor example for a dictionary page, so such lines come last.
PROFANITY = re.compile(r"\b(?:fuck\w*|motherfuck\w*|shit\w*|bullshit|bitch\w*|asshole\w*|cunt\w*|dick(?:head)?s?|cock|pussy|"
                       r"nigg\w+|fag\w*|whore\w*|slut\w*|bastard\w*|goddamn\w*)\b", re.IGNORECASE)
# Words that make a line a sentence rather than a fragment, for when the dictionary cannot tell verbs from nouns.
AUXILIARIES = {
    "is", "are", "was", "were", "am", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "can", "could", "shall", "should", "may", "might", "must", "ain't", "don't", "doesn't", "didn't",
    "won't", "can't", "isn't", "aren't", "wasn't", "weren't", "haven't", "hasn't", "hadn't", "couldn't",
    "wouldn't", "shouldn't", "let's", "gonna", "wanna", "gotta", "get", "got", "go", "know", "see", "want", "need",
    "think", "say", "make", "take", "come", "give", "tell", "love", "like", "look", "let", "try", "keep",
}
_CONTRACTED_VERB = re.compile(r"\w+'(?:m|re|ll|ve|d|s)\b")
_SPEAKER_TAG = re.compile(r"^[A-Z0-9][A-Z0-9 .'-]{1,24}:\s")
_HTML = re.compile(r"<[^>]*>")
_DIALOGUE_DASH = re.compile(r"\s[-–—]\s+[A-Z]")
_SOUND_CUE = re.compile(r"[\[\](){}♪♫#*_]|[♪♫]")


def has_verb(words: Sequence[str], is_verb: Optional[Callable[[str], bool]] = None) -> bool:
    """Whether a line has a verb-ish word: an auxiliary or contraction, an -ed/-ing form, or (when the
    dictionary can say) a word it lists as a verb or a verb form."""
    for w in words:
        low = w.lower().strip(".,!?;:\"'")
        if low in AUXILIARIES or _CONTRACTED_VERB.fullmatch(low):
            return True
        if len(low) > 4 and (low.endswith("ed") or low.endswith("ing")):
            return True
        if is_verb is not None and low.isalpha() and is_verb(low):
            return True
    return False


def clean_subtitle_line(text: str, max_words: int = MAX_SUBTITLE_WORDS, is_verb: Optional[Callable[[str], bool]] = None) -> str:
    """A subtitle line as an example sentence, or "" for fragments (a continued or cut-off line), lines that are
    too long, have no verb, hold subtitle artefacts (leading dashes are removed; ♪, speaker tags, sound cues,
    two speakers' lines run together, HTML tags) or shout in capitals."""
    s = _HTML.sub("", str(text or ""))
    s = " ".join(s.split())
    s = re.sub(r"^[-–—\s]+", "", s)
    if not s or _SPEAKER_TAG.match(s) or _SOUND_CUE.search(s) or _DIALOGUE_DASH.search(s):
        return ""
    if "..." in s or "…" in s or s.endswith(("-", "–", ",", ":", ";")):
        return ""
    if not (s[0].isupper() or s[0].isdigit()) or not re.search(r"[.!?][\"'”’)]?$", s):
        return ""
    if _upper_ratio(s) > 0.6 or "ſ" in s or _NOT_ENGLISH_SCRIPT.search(s) or _MARKUP.search(s):
        return ""
    words = s.split()
    if not (MIN_EXAMPLE_WORDS <= len(words) <= max_words) or not has_verb(words, is_verb):
        return ""
    return s


# --- Definitions --------------------------------------------------------------------------------
USAGE_LABELS = ["informal", "slang", "colloquial", "formal", "figuratively", "idiomatic", "British", "US",
                "derogatory", "vulgar", "offensive", "humorous"]
ARCHAIC_TAGS = {"archaic", "obsolete", "dated", "rare", "historical", "uncommon", "nonstandard", "dialectal",
                "poetic", "no-longer-productive", "obsolete-spelling", "archaic-spelling", "rare-sense"}
TECHNICAL_TOPICS = {
    "mathematics", "linguistics", "anatomy", "medicine", "biology", "chemistry", "physics", "geometry", "algebra",
    "statistics", "logic", "philosophy", "grammar", "phonetics", "phonology", "morphology", "semantics", "geology",
    "astronomy", "botany", "zoology", "ecology", "genetics", "biochemistry", "pathology", "pharmacology",
    "engineering", "electronics", "computing", "mathematical-analysis", "set-theory", "number-theory", "calculus",
    "topology", "taxonomy", "mineralogy", "entomology", "ornithology", "ichthyology", "law", "religion",
    "christianity", "catholicism", "theology", "heraldry", "falconry", "typography", "printing", "textiles",
    "numismatics", "medieval", "ecclesiastical", "chess", "cricket", "baseball", "military", "nautical",
    "mining", "metallurgy", "architecture", "music-theory", "prosody", "rhetoric", "psychology", "economics",
}
REGIONAL_TAGS = {
    "scotland", "ireland", "northern-ireland", "wales", "northern-england", "southern-england", "midlands-england",
    "cockney", "yorkshire", "lancashire", "geordie", "australia", "new-zealand", "south-africa", "india", "pakistan",
    "singapore", "malaysia", "hong-kong", "jamaica", "caribbean", "nigeria", "kenya", "philippines", "canada",
    "newfoundland", "appalachia", "new-england", "new-york", "texas", "southern-us", "midwestern-us", "aave",
    "african-american-vernacular", "internet", "text-messaging", "eye-dialect", "sms-speak", "leetspeak",
}
KEPT_TAGS = set(USAGE_LABELS) | ARCHAIC_TAGS | TECHNICAL_TOPICS | REGIONAL_TAGS | {"proper-noun"}
# Usage labels that make a sense a poor representative of a second part of speech ("(informal) to become happy").
UNCOMMON_USAGE = {"informal", "slang", "colloquial", "dialectal", "humorous", "derogatory", "vulgar", "offensive", "figuratively"}
_NAME_GLOSS = re.compile(
    r"^(a|an)\s+((male|female|unisex|masculine|feminine|habitational|topographic|occupational|patronymic|"
    r"metonymic|English|Scottish|Irish|Welsh|common)\s+)*(surname|given name|first name|family name|place ?name|"
    r"toponym|nickname|diminutive|hypocorism)\b|^(a|an|the)\s+(city|town|village|county|state|river|lake|"
    r"country|borough|district|island|settlement|parish|hamlet|suburb|commune|municipality|province|"
    r"unincorporated community)\b", re.IGNORECASE)
_DOMAIN_PREFIX = re.compile(r"^\(\s*([a-z][a-z \-,]*?)\s*[,)]", re.IGNORECASE)
_LEADING_LABELS = re.compile(r"^((?:\([^()]*\)\s*)+)")
_PLACEHOLDER_PAREN = re.compile(r"\((something|someone|somebody|one's|oneself|someone's|something's|a person|"
                                r"a thing|anything|anyone)\)", re.IGNORECASE)
MAX_CLAUSE_WORDS = 18


def tag_set(*groups: Iterable[str]) -> Set[str]:
    """Lower-case, hyphenated forms of Kaikki tags and topics ("Card games" -> "card-games")."""
    out: Set[str] = set()
    for g in groups:
        for t in g or []:
            out.add("-".join(str(t).lower().split()))
    return out


def split_labels(definition: str) -> Tuple[List[str], str]:
    """("(informal) Giving off light." -> (["informal"], "Giving off light.")): the usage labels a definition
    starts with, as an importer of an older version stored them in the definition itself."""
    m = _LEADING_LABELS.match(definition.strip())
    if not m:
        return [], definition.strip()
    labels = [p.strip().lower() for g in re.findall(r"\(([^()]*)\)", m.group(1)) for p in g.split(",") if p.strip()]
    return labels, definition.strip()[m.end():].strip()


def sense_class(tags: Iterable[str], definition: str, pos: str = "", has_other_pos: bool = True) -> int:
    """0 an ordinary sense, 2 a specialist one (mathematics, linguistics, anatomy, law, a religion...), 3 one to
    hide below everything else (archaic, obsolete, dated, rare, historical; "A surname"; a place name)."""
    labels, bare = split_labels(definition)
    t = tag_set(tags, labels)
    if t & ARCHAIC_TAGS or re.match(r"^(obsolete|archaic|dated|rare)\b", bare, re.IGNORECASE):
        return 3
    if _NAME_GLOSS.match(bare) or (pos == "Proper noun" and has_other_pos):
        return 3
    if t & TECHNICAL_TOPICS or t & REGIONAL_TAGS:
        return 2
    first_group = _DOMAIN_PREFIX.match(definition.strip())
    if first_group and "-".join(first_group.group(1).lower().split()) in TECHNICAL_TOPICS:
        return 2
    if re.match(r"^of,? or (relating|pertaining) to\b", bare, re.IGNORECASE):
        return 2
    return 0


def shorten_definition(definition: str) -> str:
    """A definition without its parenthetical asides: "(something)" placeholders lose their brackets, other
    parentheses go (usage labels at the start stay), and a long run of semicolon-separated clauses is cut after
    the second, or after the first when that is long already."""
    labels_part = ""
    m = _LEADING_LABELS.match(definition.strip())
    body = definition.strip()
    if m:
        kept = [g for g in re.findall(r"\([^()]*\)", m.group(1))
                if all(p.strip().lower() in USAGE_LABELS for p in g[1:-1].split(","))]
        labels_part = " ".join(kept) + (" " if kept else "")
        body = body[m.end():]
    body = _PLACEHOLDER_PAREN.sub(r"\1", body)
    body = re.sub(r"\s*\([^()]*\)", "", body)
    body = re.sub(r"\s+([,;:.])", r"\1", body)
    body = " ".join(body.split())
    clauses = [c.strip() for c in body.split(";") if c.strip()]
    if len(clauses) > 1:
        kept_clauses = [clauses[0]]
        if len(clauses[0].split()) + len(clauses[1].split()) <= MAX_CLAUSE_WORDS:
            kept_clauses.append(clauses[1])
        body = "; ".join(kept_clauses)
    if definition.rstrip().endswith(".") and not body.endswith("."):
        body += "."
    return (labels_part + body).strip()


def display_definition(definition: str, tags: Iterable[str] = ()) -> str:
    """A sense's definition as shown: shortened, with the usage labels of its tags in front ("(informal) ...")."""
    labels, bare = split_labels(definition)
    have = [l for l in USAGE_LABELS if l in tag_set(tags, labels)]
    short = shorten_definition(bare)
    return (f"({', '.join(have)}) " if have else "") + short


def content_words(text: str) -> Set[str]:
    """Lower-case words of four or more letters, trimmed to a four-letter stem: a rough "same topic" measure."""
    return {w.lower()[:5] for w in re.findall(r"[A-Za-z]{4,}", text)}


# --- Idioms and phrases -------------------------------------------------------------------------
NEUTRAL_WORDS = {
    "it", "its", "something", "someone", "somebody", "one", "one's", "oneself", "someone's", "somebody's",
    "something's", "your", "my", "his", "her", "their", "our", "yours", "you", "me", "him", "them", "us",
    "yourself", "myself", "himself", "herself", "itself", "themselves", "ourselves", "anything", "anyone",
    "anybody", "whatever", "whoever",
}
DETERMINERS = {"a", "an", "the", "no", "not", "any", "n't"}


def phrase_norm(words: Iterable[str]) -> str:
    """A phrase reduced to what identifies the idiom: neutral pronouns (it, someone, one's, your, ...) and
    determiners or negations (a, the, no, not) removed ("bring someone to heel" and "bring him to heel" both
    become "bring to heel"). The words must already be lower case keys (lexicon_db.text_key)."""
    return " ".join(w for w in words if w not in NEUTRAL_WORDS and w not in DETERMINERS)


def is_negated(words: Iterable[str]) -> bool:
    """Whether lower-case key words contain a negation (no, not, never, n't): normalising a phrase drops it, so a
    pre-translated idiom is matched only when both sides agree on it."""
    return bool({"no", "not", "never", "n't", "nothing", "nobody", "none"} & set(words))


# --- IPA ----------------------------------------------------------------------------------------
_IPA_MAP = str.maketrans({
    "ɹ": "r", "ɻ": "r", "ɡ": "g", "ɫ": "l", "ʧ": "tʃ", "ʤ": "dʒ", "ɝ": "ɜr", "ɚ": "ər", "ɒ": "ɑ",
    "ː": "", "ˑ": "", ".": "", "·": "", "‿": "", "(": "", ")": "", "ʰ": "",
})
_IPA_COMBINING = re.compile("[" + chr(0x300) + "-" + chr(0x36f) + "]")  # tie bars, syllabic and other marks
_IPA_NO_RHOTIC = re.compile(r"ɜ(?!r)")


def normalize_ipa(ipa: str) -> str:
    """One notation for every transcription shown: the symbols of Wiktionary (ɹ, ɡ, ɝ, ɚ, ɜː, tie bars, syllable
    dots, optional-sound brackets) and of the CMU-based engine (ʧ, ʤ) become the same ones: r for ɹ, plain g,
    "ɜr"/"ər" for the rhotic vowels, tʃ and dʒ without a tie bar, no length marks, no syllable dots.
    Stress marks stay. ɾ (a narrow flap) is left alone: phonemic transcriptions do not contain it and it
    cannot be mapped to t or d without knowing the word. Returns the symbols without slashes or brackets."""
    s = str(ipa or "").strip()
    s = re.sub(r"^[/\[]+|[/\]]+$", "", s.strip()).strip()
    s = _IPA_COMBINING.sub("", s)
    s = s.translate(_IPA_MAP).replace("əʊ", "oʊ")
    s = _IPA_NO_RHOTIC.sub("ɜr", s)
    return " ".join(s.split())


# --- Relations ----------------------------------------------------------------------------------
def relation_tags_ok(tags: Iterable[str]) -> bool:
    """False for a synonym Wiktionary labels archaic, obsolete, dated, rare, poetic or the like."""
    return not (tag_set(tags) & (ARCHAIC_TAGS | {"literary", "vulgar", "offensive", "derogatory"}))


# --- Forms --------------------------------------------------------------------------------------
INFLECTION_TAGS = {"singular", "plural", "present", "past", "participle", "third-person", "comparative", "superlative",
                   "gerund", "infinitive"}
ALTERNATIVE_TAG_OK = {"alternative", "us", "british", "canada", "australia", "ireland", "commonwealth"}


def classify_form(tags: str) -> str:
    """What a Kaikki form is: "form" (an everyday inflection: plural, past, past participle, comparative...), "alt" (an
    alternative spelling, labelled nothing but alternative or a national variety), or "" (archaic, obsolete,
    dated or rare forms, second-person "-est" forms, dialect, Internet spellings and the like: never shown)."""
    t = set(str(tags).lower().split()) - {"table-tags"}
    if not t:
        return ""
    if t <= ALTERNATIVE_TAG_OK and "alternative" in t:
        return "alt"
    if t <= INFLECTION_TAGS:
        return "form"
    return ""


# Wiktionary's alternative-form senses that are not alternative spellings of the word.
NOT_AN_ALTERNATIVE = {"abbreviation", "initialism", "acronym", "clipping", "contraction", "ellipsis", "misspelling",
                      "nonstandard", "obsolete", "archaic", "dated", "rare", "error", "eye-dialect",
                      "pronunciation-spelling", "alt-of-abbreviation", "short-for"}
def form_label(tags: str) -> str:
    """A readable label for Kaikki's tags of an inflected form ("present singular third-person" ->
    "third-person singular")."""
    t = set(str(tags).split()) - {"table-tags"}
    if {"third-person", "singular", "present"} <= t:
        return "third-person singular"
    if "participle" in t and "present" in t:
        return "present participle"
    if "participle" in t and "past" in t:
        return "past participle"
    for name in ("past", "plural", "comparative", "superlative", "gerund", "diminutive"):
        if name in t:
            return name
    return " ".join(sorted(t))
