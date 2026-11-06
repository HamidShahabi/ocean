"""The contract every dictionary source follows, the partial entry it fills and the registry
that runs the enabled ones and merges what they return."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import lexical_engine as le


@dataclass
class LookupContext:
    """What a source needs to know about the lookup it is part of."""
    term: str                     # what to look up: for an idiom matched by lexicon_db.match_phrase, its dictionary form
    is_idiom: bool = False        # multi-word phrase
    root_lemma: str = ""
    typed: str = ""               # what was typed, when that differs from term ("dial it down" for "dial down")

    def __post_init__(self) -> None:
        if not self.root_lemma:
            self.root_lemma = self.term

    @property
    def aliases(self) -> List[str]:
        """Other spellings of the term to look sentences and slang up under."""
        return [self.typed] if self.typed and self.typed.lower() != self.term.lower() else []


@dataclass
class PartialEntry:
    """The slice of a dictionary entry one source could fill in; merge_entries() combines them."""
    ipa_us: str = ""
    primary_pos: str = ""
    senses: List[Dict[str, Any]] = field(default_factory=list)            # {"pos", "def", "example_en", "klass", "raw_def", "tags"}
    syns: List[Dict[str, Any]] = field(default_factory=list)              # {"term", "pos", "raw_def", "hint", "tags"}: see the engine
    ants: List[Dict[str, Any]] = field(default_factory=list)              # same
    collocations: List[Dict[str, str]] = field(default_factory=list)      # {"term", "example_en"}
    derived: List[str] = field(default_factory=list)                      # derived terms and compounds
    related: List[str] = field(default_factory=list)                      # related terms
    forms: List[Dict[str, str]] = field(default_factory=list)             # {"form", "label"}: inflections
    alternatives: List[str] = field(default_factory=list)                 # alternative spellings and forms
    examples: List[Dict[str, str]] = field(default_factory=list)          # {"en", "fa", "corpus"} sentence pairs
    frequency: Optional[float] = None                                     # occurrences per million words


class DictionarySource:
    """Subclass, set name and label and implement fetch(). Never raises out of lookup()."""
    name = ""                 # short stable id: settings and log lines refer to it
    label = ""                # human-readable name
    needs: Tuple[str, ...] = ()  # sources (registered earlier) whose result fetch() wants as `prior`

    def applies(self, ctx: LookupContext) -> bool:
        """False when this kind of lookup (a phrase, a single word...) is none of this source's business."""
        return True

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        """Fill `entry` in place for ctx.term. `prior` is the merged result of the `needs` sources."""
        raise NotImplementedError

    def lookup(self, ctx: LookupContext, prior: Optional[PartialEntry] = None) -> PartialEntry:
        """fetch() with failures logged under the source's name; whatever was filled in before one is kept."""
        entry = PartialEntry()
        try:
            self.fetch(ctx, entry, prior or PartialEntry())
        except Exception:
            le._log_failure(f"Source {self.name!r} failed for {ctx.term!r}")
        return entry


def merge_entries(entries: Iterable[PartialEntry]) -> PartialEntry:
    """Lists concatenate in the order given; ipa_us, primary_pos and frequency come from the first entry that has one."""
    merged = PartialEntry()
    for e in entries:
        merged.ipa_us = merged.ipa_us or e.ipa_us
        merged.primary_pos = merged.primary_pos or e.primary_pos
        if merged.frequency is None:
            merged.frequency = e.frequency
        merged.senses += e.senses
        merged.syns += e.syns
        merged.ants += e.ants
        merged.collocations += e.collocations
        merged.derived += e.derived
        merged.related += e.related
        merged.forms += e.forms
        merged.alternatives += e.alternatives
        merged.examples += e.examples
    return merged


class SourceRegistry:
    """The ordered list of sources. Order is merge order (earlier sources win ties for ipa_us and
    primary_pos and come first in every list) and a source may only `need` ones registered before it."""

    def __init__(self) -> None:
        self._sources: List[DictionarySource] = []
        self._disabled: Set[str] = set()

    def register(self, source: DictionarySource) -> DictionarySource:
        if not source.name or source.name in self.names():
            raise ValueError(f"Dictionary source name missing or already registered: {source.name!r}")
        for dep in source.needs:
            if dep not in self.names():
                raise ValueError(f"{source.name!r} needs {dep!r}, which must be registered before it")
        self._sources.append(source)
        return source

    def names(self) -> List[str]:
        return [s.name for s in self._sources]

    def all(self) -> List[DictionarySource]:
        return list(self._sources)

    def get(self, name: str) -> DictionarySource:
        for s in self._sources:
            if s.name == name:
                return s
        raise KeyError(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.get(name)  # unknown names are a programming error, not a silent no-op
        (self._disabled.discard if enabled else self._disabled.add)(name)

    def is_enabled(self, name: str) -> bool:
        return name not in self._disabled

    def any_enabled(self) -> bool:
        """False when the user has switched every source off."""
        return any(s.name not in self._disabled for s in self._sources)

    def disabled_names(self) -> List[str]:
        return sorted(self._disabled)

    def active(self, ctx: LookupContext) -> List[DictionarySource]:
        return [s for s in self._sources if s.name not in self._disabled and s.applies(ctx)]

    def run(self, ctx: LookupContext) -> PartialEntry:
        """Look the term up in every active source at once (a source with `needs` waits for those) and merge."""
        active = self.active(ctx)
        if not active:
            return PartialEntry()
        futures: Dict[str, Future] = {}

        def task(source: DictionarySource) -> PartialEntry:
            deps = [d for d in source.needs if d in futures]  # inactive sources are simply absent
            prior = merge_entries(futures[d].result() for d in deps)
            return source.lookup(ctx, prior)

        # Dependencies are submitted first, so a waiting task never starves the one it waits for.
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            for source in active:
                futures[source.name] = le.submit_in_context(pool, task, source)
            return merge_entries(futures[s.name].result() for s in active)
