import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class UrbanDictionarySource(DictionarySource):
    """Slang, informal phrasing and pop-culture catchphrases from the imported Urban Dictionary dump
    (only well-voted entries were imported)."""
    name = "urban_dictionary"
    label = "Urban Dictionary (slang)"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        for s in le.LEXICON.slang(ctx.term):
            example = s["example"] if s["example"] and le.sentence_contains_target(s["example"], ctx.term) else ""
            entry.senses.append({
                "pos": "Slang",
                "def": le.clean_english_sentence(s["def"]),
                "example_en": le.clean_english_sentence(example) if example else "",
            })
