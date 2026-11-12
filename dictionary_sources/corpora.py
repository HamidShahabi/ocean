import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry

SUBTITLE_EXAMPLES = 6   # enough to give each shown sense its own
TATOEBA_EXAMPLES = 3


class CorporaSource(DictionarySource):
    """Human-translated English-Persian example sentences and, for a single word, its frequency in English
    subtitles. OpenSubtitles is the primary source of examples (short, natural, complete dialogue lines);
    Tatoeba's textbook-style sentences are the last resort. Both come in quality-filtered (LexiconDB.examples),
    each tagged with its corpus; the engine decides the order of use (subtitles, Wiktionary's own, Tatoeba)."""
    name = "corpora"
    label = "Example sentences (OpenSubtitles, Tatoeba)"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        for corpus, limit in (("opensubtitles", SUBTITLE_EXAMPLES), ("tatoeba", TATOEBA_EXAMPLES)):
            have = set()
            for term in [ctx.term] + ctx.aliases:
                for ex in le.LEXICON.examples(term, corpus, limit=limit):
                    if ex["en"].lower() not in have and len(have) < limit:
                        have.add(ex["en"].lower())
                        entry.examples.append(ex)
        if not ctx.is_idiom:
            entry.frequency = le.LEXICON.frequency(ctx.term)
