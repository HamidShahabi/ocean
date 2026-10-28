from typing import Any

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class DictionaryApiSource(DictionarySource):
    name = "dictionaryapi"
    label = "dictionaryapi.dev"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        r = ctx.request("GET", f"https://api.dictionaryapi.dev/api/v2/entries/en/{ctx.term.strip()}", timeout=2.5)
        if r.ok:
            self.parse(r.json(), ctx.term.strip(), ctx.root_lemma, entry)

    def parse(self, data: Any, term: str, root_lemma: str, entry: PartialEntry) -> None:
        if not (isinstance(data, list) and data):
            return
        for item in data[:2]:
            for p in item.get("phonetics", []):
                if p.get("text") and ("-us" in p.get("audio", "") or not entry.ipa_us):
                    entry.ipa_us = le.clean_ipa_display(p["text"])
            for m in item.get("meanings", []):
                pos = m.get("partOfSpeech", "Entry").capitalize()
                if not entry.primary_pos:
                    entry.primary_pos = pos
                for s in m.get("synonyms", [])[:4]:
                    if not le.is_spelling_or_root_duplicate(s, term, root_lemma) and s.lower() not in le.WEAK_SYNONYMS and len(s) > 2:
                        entry.syns.append(s)
                for a in m.get("antonyms", [])[:4]:
                    if not le.is_spelling_or_root_duplicate(a, term, root_lemma) and len(a) > 2:
                        entry.ants.append(a)
                for d in m.get("definitions", [])[:2]:
                    d_txt = le.clean_english_sentence(d.get("definition", ""))
                    ex_txt = le.clean_english_sentence(d.get("example", ""))
                    if ex_txt and not le.sentence_contains_target(ex_txt, term):
                        ex_txt = ""
                    if d_txt and len(d_txt.split()) >= 2:
                        entry.senses.append({"pos": pos, "def": d_txt, "example_en": ex_txt})

    def find_example(self, word: str, target_pos: str = "") -> str:
        """A real example sentence for `word` (used for its synonyms and antonyms); "" if there is none."""
        clean = word.strip()
        try:
            r = LookupContext(term=clean).request("GET", f"https://api.dictionaryapi.dev/api/v2/entries/en/{clean}", timeout=1.8)
            if r.ok:
                for item in r.json()[:2]:
                    for m in item.get("meanings", []):
                        m_pos = m.get("partOfSpeech", "").capitalize()
                        if target_pos and m_pos and m_pos != target_pos:
                            continue
                        for d in m.get("definitions", []):
                            ex = d.get("example", "")
                            if ex and le.sentence_contains_target(ex, clean) and len(ex.split()) >= 5:
                                return le.clean_english_sentence(ex)
        except Exception:
            le._log_failure(f"Source {self.name!r} example lookup failed for {word!r}")
        return ""
