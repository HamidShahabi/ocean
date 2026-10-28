# === QUIZ: QUESTION GENERATION (Qt-free) ===
# Quizzes draw on the favourite (saved) words. Each mode is a QuizMode in the MODES registry: a
# generator that turns the saved words into Questions. The quiz page only renders Questions and
# keeps score, so a new mode (fill in the blank, listen and type) is one generator plus register_mode().
#
# Spaced repetition: quiz answers deliberately do NOT touch review.ReviewStore. Picking one of four
# options is recognition, a much weaker signal than the free-recall self-grade SM-2 expects, and a
# 25% guess rate would push wrongly-known words onto long intervals. Review stays the one place
# that schedules cards, so its "due" counts keep their meaning and the quiz stays low-stakes practice.
# Typed modes (fill in the blank) follow the same rule: typing is real recall, but a quiz round has no
# self-grade or "how hard was it" signal, and a blanked sentence is easier than recalling a word cold,
# so it stays practice only. Keeping every quiz mode consistent ("quizzes never schedule") is simpler to explain.
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

import cloze
import lexical_engine

CHOICE_MODE = "choice"
CLOZE_MODE = "cloze"
SESSION_LENGTH = 10
CHOICE_COUNT = 4
EN_TO_FA = "en_fa"
FA_TO_EN = "fa_en"
DIRECTIONS = (EN_TO_FA, FA_TO_EN)
EN_ONLY = "en_en"  # English prompt and English answer (fill in the blank)


def _norm(text: str) -> str:
    return lexical_engine.fa_key(text or "").casefold()


def _persian_parts(persian: str) -> FrozenSet[str]:
    """The comma-separated senses of a Persian meaning, normalised for comparison."""
    return frozenset(p for p in (lexical_engine.fa_key(part) for part in re.split(r"[،,]", persian)) if p)


@dataclass(frozen=True)
class QuizItem:
    """One saved word reduced to what quizzes need; `payload` keeps the full entry for richer modes."""
    query_key: str
    english: str
    persian: str
    payload: Dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def meanings(self) -> FrozenSet[str]:
        return _persian_parts(self.persian)

    def clashes_with(self, other: "QuizItem") -> bool:
        """Same English, or a shared Persian meaning: the two can't both be offered as options."""
        return _norm(self.english) == _norm(other.english) or bool(self.meanings & other.meanings)


def item_from_entry(query_key: str, display_term: str, payload: Dict[str, Any]) -> Optional[QuizItem]:
    """A QuizItem from a stored entry, or None when it has no usable English/Persian pair (e.g. sentences)."""
    if payload.get("is_sentence"):
        return None
    if payload.get("is_reverse"):  # saved Persian lookup: the headword is the Persian side
        english, persian = payload.get("english_translation", ""), payload.get("headword") or display_term
    else:
        english, persian = payload.get("headword") or display_term, payload.get("persian_equivalent", "")
    english, persian = (english or "").strip(), (persian or "").strip()
    if not english or not _persian_parts(persian):
        return None
    return QuizItem(query_key, english, persian, payload)


def items_from_entries(entries: Sequence[Dict[str, Any]]) -> List[QuizItem]:
    """QuizItems from HistoryStore.favorite_entries(); English duplicates collapse to the first."""
    items: List[QuizItem] = []
    seen = set()
    for entry in entries:
        item = item_from_entry(entry["query_key"], entry["display_term"], entry["payload"])
        if item is not None and _norm(item.english) not in seen:
            seen.add(_norm(item.english))
            items.append(item)
    return items


@dataclass(frozen=True)
class Question:
    """What the quiz page shows. `choices` set = pick one; empty = type the answer (the page's typed-answer
    input handles any such question). `accepted` are extra answers that also count (a typed answer is checked
    case-insensitively against `answer` and these); `reveal` is shown once answered (e.g. the full
    sentence); `hint` replaces the page's default instruction line."""
    mode: str
    prompt: str
    answer: str
    query_key: str
    direction: str = EN_TO_FA
    choices: Tuple[str, ...] = ()
    accepted: Tuple[str, ...] = ()
    reveal: str = ""
    hint: str = ""

    @property
    def is_typed(self) -> bool:
        return not self.choices

    @property
    def prompt_is_persian(self) -> bool:
        return self.direction == FA_TO_EN

    @property
    def answer_is_persian(self) -> bool:
        return self.direction == EN_TO_FA

    def is_correct(self, response: str) -> bool:
        key = _norm(response)
        return bool(key) and key in {_norm(a) for a in (self.answer, *self.accepted)}


Generator = Callable[[Sequence[QuizItem], random.Random, int], List[Question]]


@dataclass(frozen=True)
class QuizMode:
    key: str
    label: str
    generate: Generator
    min_items: int          # saved words needed before the mode can run
    requirement: str        # shown on the quiz page when it can't


def generate_choice_questions(items: Sequence[QuizItem], rng: random.Random, count: int = SESSION_LENGTH) -> List[Question]:
    """Four-option questions, alternating English->Persian and Persian->English. Distractors are other
    saved words that share neither English nor any Persian meaning with the answer or each other."""
    pool = list(items)
    rng.shuffle(pool)
    first = rng.randrange(len(DIRECTIONS))
    questions: List[Question] = []
    for item in pool:
        if len(questions) >= count:
            break
        direction = DIRECTIONS[(first + len(questions)) % len(DIRECTIONS)]
        others = [o for o in pool if o is not item]
        rng.shuffle(others)
        distractors: List[QuizItem] = []
        for other in others:
            if not any(other.clashes_with(x) for x in [item, *distractors]):
                distractors.append(other)
            if len(distractors) == CHOICE_COUNT - 1:
                break
        if len(distractors) < CHOICE_COUNT - 1:
            continue
        options = [item, *distractors]
        rng.shuffle(options)
        to_persian = direction == EN_TO_FA
        questions.append(Question(
            mode=CHOICE_MODE,
            prompt=item.english if to_persian else item.persian,
            answer=item.persian if to_persian else item.english,
            query_key=item.query_key,
            direction=direction,
            choices=tuple(o.persian if to_persian else o.english for o in options),
        ))
    return questions


def cloze_candidates(item: QuizItem) -> List[cloze.Cloze]:
    """Every example sentence of a saved word that contains the word or an inflection of it, blanked."""
    comparative = any("adj" in str(s.get("pos", "")).lower() for s in item.payload.get("senses") or [] if isinstance(s, dict))
    found: List[cloze.Cloze] = []
    seen = set()
    # Senses first, then collocation examples; synonym/antonym examples are about other words.
    for key in ("senses", "collocations"):
        for row in item.payload.get(key) or []:
            sentence = row.get("example_en") if isinstance(row, dict) else ""
            if not isinstance(sentence, str) or sentence.strip().lower() in seen:
                continue
            seen.add(sentence.strip().lower())
            blanked = cloze.make_cloze(sentence, item.english, comparative)
            if blanked is not None:
                found.append(blanked)
    return found


def generate_cloze_questions(items: Sequence[QuizItem], rng: random.Random, count: int = SESSION_LENGTH) -> List[Question]:
    """Typed fill-in-the-blank questions: one example sentence per saved word with the word (or an inflected
    form: run/runs/ran/running, "break the ice"/"broke the ice") blanked. Words with no usable example are
    skipped. The answer is the form that was in the sentence; the base word as saved is accepted too."""
    pool = list(items)
    rng.shuffle(pool)
    questions: List[Question] = []
    for item in pool:
        if len(questions) >= count:
            break
        candidates = cloze_candidates(item)
        if not candidates:
            continue
        chosen = rng.choice(candidates)
        questions.append(Question(
            mode=CLOZE_MODE,
            prompt=chosen.blanked,
            answer=chosen.forms[0],
            query_key=item.query_key,
            direction=EN_ONLY,
            accepted=(*chosen.forms[1:], item.english),
            reveal=chosen.sentence,
            hint=f"Type the missing word or phrase · meaning: {item.persian}",
        ))
    return questions


MODES: Dict[str, QuizMode] = {}


def register_mode(mode: QuizMode) -> None:
    MODES[mode.key] = mode


register_mode(QuizMode(
    key=CHOICE_MODE,
    label="Multiple choice",
    generate=generate_choice_questions,
    min_items=CHOICE_COUNT,
    requirement=f"Multiple choice needs at least {CHOICE_COUNT} saved words with different meanings. "
                "Save words with ☆ Save on a result card.",
))


register_mode(QuizMode(
    key=CLOZE_MODE,
    label="Fill in the blank",
    generate=generate_cloze_questions,
    min_items=1,
    requirement="Fill in the blank needs saved words whose example sentence contains the word "
                "(or a form of it, like \"ran\" for \"run\"); other saved words are skipped. "
                "Save words with ☆ Save on a result card.",
))


def new_session(items: Sequence[QuizItem], mode_key: str = CHOICE_MODE, rng: Optional[random.Random] = None,
                count: int = SESSION_LENGTH) -> List[Question]:
    """A fresh run of questions in the given mode; empty when the saved words can't support it."""
    mode = MODES[mode_key]
    if len(items) < mode.min_items:
        return []
    return mode.generate(items, rng or random.Random(), count)


def unavailable_message(mode_key: str, saved_count: int) -> str:
    """Why no quiz can run, for the empty state on the quiz page."""
    mode = MODES[mode_key]
    have = "You have no saved words yet." if saved_count == 0 else f"You have {saved_count} usable saved word{'s' if saved_count != 1 else ''}."
    return f"{mode.requirement}\n{have}"
