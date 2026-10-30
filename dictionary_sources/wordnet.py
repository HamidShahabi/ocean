import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry

POS_NAMES = {"n": "Noun", "v": "Verb", "a": "Adjective", "s": "Adjective", "r": "Adverb"}
MAX_SENSES = 3


class WordNetSource(DictionarySource):
    """Princeton WordNet 3.1: synonyms and antonyms grouped by synset, i.e. by one exact sense (the money
    "bank" and the river "bank" are separate groups), synsets of the main part of speech first. Its own
    definitions are used only when Wiktionary had none."""
    name = "wordnet"
    label = "WordNet 3.1"
    needs = ("kaikki",)

    def applies(self, ctx: LookupContext) -> bool:
        return bool(le.wn)

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        clean = ctx.term.strip()
        synsets = le.wn.synsets("_".join(clean.lower().split()))
        main_pos = prior.primary_pos
        synsets.sort(key=lambda s: POS_NAMES.get(s.pos()) != main_pos)  # stable: WordNet's frequency order otherwise
        for syn in synsets:
            pos = POS_NAMES.get(syn.pos(), "Entry")
            gloss = le.clean_english_sentence(syn.definition())
            if not prior.senses and len(entry.senses) < MAX_SENSES:
                entry.primary_pos = entry.primary_pos or pos
                example = next((ex for ex in syn.examples() if le.sentence_contains_target(ex, clean)), "")
                entry.senses.append({"pos": pos, "def": gloss, "example_en": le.clean_english_sentence(example) if example else ""})
            sense = f"{pos.lower()}: {gloss.rstrip('.')}"
            for lem in syn.lemmas():
                name = lem.name().replace("_", " ")
                if not le.is_spelling_or_root_duplicate(name, clean, ctx.root_lemma):
                    entry.syns.append({"term": name, "sense": sense})
                for ant in lem.antonyms():
                    ant_name = ant.name().replace("_", " ")
                    if not le.is_spelling_or_root_duplicate(ant_name, clean, ctx.root_lemma):
                        entry.ants.append({"term": ant_name, "sense": sense})
