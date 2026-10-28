# === CLOZE: BLANKING A WORD (AND ITS INFLECTED FORMS) IN AN EXAMPLE SENTENCE (Qt-free) ===
# Fill-in-the-blank quizzes need to find "run" in "She ran home" without a dictionary: forms are generated
# from spelling rules (-s/-es/-ies, -ed/-ied, -ing, doubled final consonant, dropped -e) plus a small table
# of irregulars. Deliberately no WordNet/NLTK here, since that data may be missing on a given install.
# The rules over-generate a little (e.g. "hopeing"); that is harmless because a made-up form can't appear
# in a sentence, and it keeps the rules short. Matching is whole-word and case-insensitive.
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

BLANK = "_____"
MIN_STEM_LEN = 3  # shorter words (is, as, us, go...) only match themselves and their irregular-table forms

# lemma followed by its irregular forms (the regular -s/-ed/-ing forms are generated, so only list what rules miss)
_IRREGULAR_LINES = """
be am is are was were been being
have has had having
do does did done doing
go goes went gone going
say said
make made
take took taken
come came
see saw seen
know knew known
get got gotten
give gave given
find found
think thought
tell told
become became
leave left
feel felt
bring brought
begin began begun
keep kept
hold held
write wrote written
stand stood
hear heard
mean meant
meet met
run ran
pay paid
sit sat
speak spoke spoken
lie lay lain lying
lead led
read
grow grew grown
lose lost
fall fell fallen
send sent
build built
understand understood
draw drew drawn
break broke broken
spend spent
rise rose risen
drive drove driven
buy bought
wear wore worn
choose chose chosen
seek sought
throw threw thrown
catch caught
deal dealt
win won
forget forgot forgotten
lend lent
sell sold
fight fought
teach taught
eat ate eaten
sing sang sung
swim swam swum
drink drank drunk
ring rang rung
sleep slept
hide hid hidden
shake shook shaken
steal stole stolen
fly flies flew flown
bite bit bitten
blow blew blown
burn burnt
dig dug
feed fed
hang hung
lay laid
ride rode ridden
shoot shot
sink sank sunk
slide slid
swing swung
tear tore torn
wake woke woken
bear bore born borne
bend bent
bind bound
bleed bled
breed bred
creep crept
dream dreamt
flee fled
forgive forgave forgiven
freeze froze frozen
kneel knelt
light lit
shine shone
shrink shrank shrunk
smell smelt
stick stuck
sting stung
strike struck stricken
swear swore sworn
sweep swept
weep wept
wind wound
man men
woman women
child children
person people
foot feet
tooth teeth
mouse mice
goose geese
ox oxen
life lives
knife knives
wife wives
leaf leaves
half halves
wolf wolves
shelf shelves
thief thieves
good better best
bad worse worst
far farther further farthest furthest
little less least
many more most
much more most
"""


def _build_table() -> Dict[str, FrozenSet[str]]:
    table: Dict[str, set] = {}
    for line in _IRREGULAR_LINES.strip().splitlines():
        lemma, *forms = line.split()
        table.setdefault(lemma, set()).update(forms)
    return {lemma: frozenset(forms) for lemma, forms in table.items()}


IRREGULAR: Dict[str, FrozenSet[str]] = _build_table()
# Reverse index so a saved inflected word ("broke") still finds its other forms ("break", "broken").
_LEMMAS_OF: Dict[str, List[str]] = {}
for _lemma, _forms in IRREGULAR.items():
    for _form in _forms:
        _LEMMAS_OF.setdefault(_form, []).append(_lemma)

_VOWELS = "aeiou"


def regular_forms(word: str, comparative: bool = False) -> FrozenSet[str]:
    """Spelling-rule inflections of `word`: -s/-es/-ies/-ves, -ed/-ied, -ing, doubled consonant (run ->
    running), dropped -e (make -> making), -c -> -ck (panic -> panicking). `comparative` adds -er/-est for
    adjectives; it is off by default because "run" must not blank "runner"."""
    w = word.lower()
    if len(w) < MIN_STEM_LEN:
        return frozenset()
    stems = {w}                      # what -ed/-ing/-er/-est attach to
    out = {w + "s"}
    if w.endswith(("s", "x", "z", "ch", "sh", "o")):
        out.add(w + "es")
    if w.endswith("y") and w[-2] not in _VOWELS:
        out |= {w[:-1] + "ies", w[:-1] + "ied"}
        stems.add(w[:-1] + "i")
    if w.endswith("e"):
        stems.add(w[:-1])
    if w.endswith("ie"):
        out.add(w[:-2] + "ying")
    if w.endswith("f"):
        out.add(w[:-1] + "ves")
    elif w.endswith("fe"):
        out.add(w[:-2] + "ves")
    if len(w) >= 3 and w[-1] not in _VOWELS + "wxy" and w[-2] in _VOWELS and w[-3] not in _VOWELS:
        stems.add(w + w[-1])         # run -> runn-
    if w.endswith("c"):
        stems.add(w + "k")           # panic -> panick-
    for stem in stems:
        out |= {stem + "ed", stem + "ing"}
        if comparative:
            out |= {stem + "er", stem + "est"}
    if w.endswith("e"):
        out.add(w + "d")
        if comparative:
            out |= {w + "r", w + "st"}
    return frozenset(out)


def word_forms(word: str, comparative: bool = False) -> FrozenSet[str]:
    """Every form of one word that should count as "the same word" in a sentence, the word itself included."""
    w = word.lower()
    forms = {w} | regular_forms(w, comparative)
    for lemma in [w] if w in IRREGULAR else _LEMMAS_OF.get(w, []):
        forms |= {lemma} | IRREGULAR.get(lemma, frozenset()) | regular_forms(lemma, comparative)
    return frozenset(forms)


def _term_tokens(term: str) -> List[str]:
    return [t for t in (tok.strip(".,;:!?\"()[]") for tok in term.replace("’", "'").split()) if t]


def term_pattern(term: str, comparative: bool = False) -> Optional["re.Pattern[str]"]:
    """Regex matching `term` (a word or a phrase) in any inflection, as whole words, ignoring case.
    Phrases inflect word by word, so "break the ice" matches "broke the ice" and "breaking the ice"."""
    tokens = _term_tokens(term)
    if not tokens:
        return None
    parts = []
    for tok in tokens:
        forms = sorted(word_forms(tok, comparative), key=len, reverse=True)
        parts.append("(?:" + "|".join(re.escape(f) for f in forms) + ")")
    return re.compile(r"(?<![\w'-])" + r"\s+".join(parts) + r"(?![\w-])", re.IGNORECASE)


@dataclass(frozen=True)
class Cloze:
    sentence: str           # the original sentence
    blanked: str            # the sentence with every occurrence replaced by BLANK
    forms: Tuple[str, ...]  # the distinct surface forms that were blanked, in order of appearance


def make_cloze(sentence: str, term: str, comparative: bool = False) -> Optional[Cloze]:
    """Blank `term` (any inflection, every occurrence) in `sentence`. None when the sentence doesn't
    contain it, or has too little left around the blank to be a sensible prompt."""
    sentence = " ".join((sentence or "").split())
    pattern = term_pattern(term, comparative)
    if pattern is None or not sentence:
        return None
    # ’ -> ' keeps every index the same, so spans found here are valid in the original text.
    matches = list(pattern.finditer(sentence.replace("’", "'")))
    if not matches:
        return None
    pieces: List[str] = []
    forms: List[str] = []
    last = 0
    for m in matches:
        pieces += [sentence[last:m.start()], BLANK]
        last = m.end()
        text = sentence[m.start():m.end()]
        if text.lower() not in (f.lower() for f in forms):
            forms.append(text)
    pieces.append(sentence[last:])
    blanked = "".join(pieces)
    if len(re.findall(r"\w+", blanked.replace(BLANK, " "))) < 2:
        return None
    return Cloze(sentence, blanked, tuple(forms))
