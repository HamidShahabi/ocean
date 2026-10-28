from typing import List, Optional

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class DatamuseSource(DictionarySource):
    """Synonyms, antonyms and adjective-noun collocations for single words and phrases that are not idioms."""
    name = "datamuse"
    label = "Datamuse"
    needs = ("dictionaryapi", "wordnet")  # which collocation to ask for depends on the part of speech they found

    def applies(self, ctx: LookupContext) -> bool:
        return not ctx.is_idiom

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        clean = ctx.term.strip()
        low = clean.lower()
        words = clean.split()
        root_lemma = ctx.root_lemma

        r_syn = ctx.request("GET", "https://api.datamuse.com/words", params={"rel_syn": clean, "md": "fp", "max": 6}, timeout=2.0)
        if r_syn.ok:
            for item in r_syn.json():
                w = item.get("word", "").strip()
                if w and not le.is_spelling_or_root_duplicate(w, clean, root_lemma) and w.lower() not in le.WEAK_SYNONYMS and len(w) > 2:
                    entry.syns.append(w)

        r_ant = ctx.request("GET", "https://api.datamuse.com/words", params={"rel_ant": clean, "md": "fp", "max": 6}, timeout=2.0)
        if r_ant.ok:
            for item in r_ant.json():
                w = item.get("word", "").strip()
                if w and not le.is_spelling_or_root_duplicate(w, clean, root_lemma) and len(w) > 2:
                    entry.ants.append(w)

        if len(words) == 1:
            if prior.primary_pos == "Adjective":
                r_col = ctx.request("GET", "https://api.datamuse.com/words", params={"rel_jja": clean, "md": "f", "max": 6}, timeout=2.0)
                if r_col.ok:
                    for item in r_col.json():
                        noun = item.get("word", "").lower()
                        if noun and noun not in le.STOPWORDS and len(noun) > 2 and noun.isalpha():
                            entry.collocation_terms.append(f"{low} {noun}")
            elif prior.primary_pos == "Noun":
                r_col = ctx.request("GET", "https://api.datamuse.com/words", params={"rel_jjb": clean, "md": "f", "max": 6}, timeout=2.0)
                if r_col.ok:
                    for item in r_col.json():
                        adj = item.get("word", "").lower()
                        if adj and adj not in le.STOPWORDS and len(adj) > 3 and adj.isalpha():
                            entry.collocation_terms.append(f"{adj} {low}")

    def suggest(self, prefix: str) -> List[str]:
        """Autocomplete suggestions for a prefix; [] on any failure."""
        try:
            r = LookupContext(term=prefix).request("GET", "https://api.datamuse.com/sug", params={"s": prefix, "max": 7}, timeout=1.2)
            if r.ok:
                return [item["word"] for item in r.json() if item.get("word")]
        except Exception:
            le._log_failure(f"Source {self.name!r} suggestions failed for {prefix!r}")
        return []


class DatamuseFrequencySource(DictionarySource):
    """How common the word is (occurrences per million words, Datamuse's `f:` tag). None of the lookups
    above returns the headword itself, so this asks for it; it runs beside them, adding no wait."""
    name = "datamuse_frequency"
    label = "Datamuse word frequency"

    def applies(self, ctx: LookupContext) -> bool:
        return not ctx.is_idiom and len(ctx.term.split()) == 1 and ctx.term.isalpha()

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        r = ctx.request("GET", "https://api.datamuse.com/words", params={"sp": ctx.term.strip(), "md": "f", "max": 1}, timeout=2.0)
        if r.ok:
            entry.frequency = self.parse(r.json(), ctx.term.strip())

    @staticmethod
    def parse(items: List[dict], term: str) -> Optional[float]:
        """The `f:` tag of the item that is the term itself (`sp` also returns near spellings)."""
        for item in items:
            if item.get("word", "").lower() != term.lower():
                continue
            for tag in item.get("tags", []):
                if tag.startswith("f:"):
                    try:
                        return float(tag[2:])
                    except ValueError:
                        return None
        return None
