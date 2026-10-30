import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class CorporaSource(DictionarySource):
    """Human-translated English-Persian example sentences (Tatoeba first, then OpenSubtitles) and, for a
    single word, its frequency in English subtitles."""
    name = "corpora"
    label = "Example sentences (Tatoeba, OpenSubtitles)"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        entry.examples += [{"en": ex["en"], "fa": ex["fa"]} for ex in le.LEXICON.examples(ctx.term, limit=4)]
        if not ctx.is_idiom:
            entry.frequency = le.LEXICON.frequency(ctx.term)
