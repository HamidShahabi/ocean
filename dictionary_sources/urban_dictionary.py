import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry

# A slang definition is "strong" enough to show beside Wiktionary's senses when it has been voted up this much,
# with this share of up votes, and the word is rare in ordinary speech (occurrences per million subtitle words).
STRONG_UP_VOTES = 200
STRONG_RATIO = 0.9
RARE_PER_MILLION = 2.0


class UrbanDictionarySource(DictionarySource):
    """Slang, informal phrasing and pop-culture catchphrases from the bundled slang table (the best-voted Urban
    Dictionary entries). Routed so that ordinary words stay clean: slang is shown when Wiktionary has nothing for
    the word or phrase, or when the slang is the strong, dominant use of a rare word (see the constants above)."""
    name = "urban_dictionary"
    label = "Urban Dictionary (slang)"
    needs = ("kaikki",)

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        rows = []
        for term in [ctx.term] + ctx.aliases:
            rows = le.LEXICON.slang(term)
            if rows:
                break
        if not rows or not self._wanted(ctx, prior, rows[0]):
            return
        for s in rows:
            example = s["example"] if s["example"] and le.sentence_contains_target(s["example"], ctx.term) else ""
            entry.senses.append({
                "pos": "Slang",
                "def": le.clean_english_sentence(s["def"]),
                "example_en": le.clean_english_sentence(example) if example else "",
                "tags": ["slang"],
                "klass": 1,
            })

    @staticmethod
    def _wanted(ctx: LookupContext, prior: PartialEntry, best: dict) -> bool:
        if not any(s.get("klass", 0) < 3 for s in prior.senses):
            return True  # Wiktionary has nothing (usable) for it: slang is the only match
        freq = le.LEXICON.frequency(ctx.term)
        return (best["up"] >= STRONG_UP_VOTES and best["ratio"] >= STRONG_RATIO
                and not ctx.is_idiom and (freq is None or freq < RARE_PER_MILLION))
