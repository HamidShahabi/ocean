import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class WordNetSource(DictionarySource):
    """Definitions, synonyms and antonyms from the local NLTK WordNet data (single words only)."""
    name = "wordnet"
    label = "WordNet"
    needs_network = False

    def applies(self, ctx: LookupContext) -> bool:
        return bool(le.wn) and not ctx.is_idiom

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        clean = ctx.term.strip()
        pos_map = {"n": "Noun", "v": "Verb", "a": "Adjective", "s": "Adjective", "r": "Adverb"}
        for syn in le.wn.synsets("_".join(clean.split()))[:4]:
            pos = pos_map.get(syn.pos(), "Entry")
            if not entry.primary_pos:
                entry.primary_pos = pos
            d_txt = le.clean_english_sentence(syn.definition())
            ex_txt = ""
            for ex in syn.examples():
                if le.sentence_contains_target(ex, clean):
                    ex_txt = le.clean_english_sentence(ex)
                    break
            if d_txt:
                entry.senses.append({"pos": pos, "def": d_txt, "example_en": ex_txt})
            for lem in syn.lemmas():
                w_name = lem.name().replace("_", " ")
                if not le.is_spelling_or_root_duplicate(w_name, clean, ctx.root_lemma) and w_name.lower() not in le.WEAK_SYNONYMS:
                    entry.syns.append(w_name)
                for ant in lem.antonyms():
                    ant_name = ant.name().replace("_", " ")
                    if not le.is_spelling_or_root_duplicate(ant_name, clean, ctx.root_lemma):
                        entry.ants.append(ant_name)
