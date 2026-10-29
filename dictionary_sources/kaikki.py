import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class KaikkiSource(DictionarySource):
    """Wiktionary via the Kaikki.org extract: IPA, senses by part of speech with usage examples,
    collocations and derived terms (an inflected form gives its lemma's entry)."""
    name = "kaikki"
    label = "Wiktionary (Kaikki.org)"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        for e in le.LEXICON.entries(ctx.term):
            if e["ipa"] and e["word"].lower() == ctx.term.strip().lower():  # not the lemma's: "ran" is not /ɹʌn/
                entry.ipa_us = entry.ipa_us or le.clean_ipa_display(e["ipa"])
            pos = "Idiom / Expression" if ctx.is_idiom and e["pos"] in ("Verb", "Phrase") else e["pos"]
            entry.primary_pos = entry.primary_pos or pos
            for s in e["senses"]:
                entry.senses.append({
                    "pos": pos,
                    "def": le.clean_english_sentence(s["def"]),
                    "example_en": le.clean_english_sentence(s["example"]) if s["example"] else "",
                })
            if not ctx.is_idiom:
                for phrase in e["collocations"]:
                    if phrase.lower() not in (c["term"].lower() for c in entry.collocations):
                        entry.collocations.append({"term": phrase, "example_en": ""})
                entry.derived += [d for d in e["derived"] if d not in entry.derived]
