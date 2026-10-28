from typing import Any

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class TatoebaSource(DictionarySource):
    """English example sentences with a Persian translation."""
    name = "tatoeba"
    label = "Tatoeba"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        clean = ctx.term.strip()
        r = ctx.request(
            "GET",
            "https://tatoeba.org/en/api_v0/search",
            params={"from": "eng", "to": "pes", "query": clean},
            timeout=2.6,
        )
        if r.ok:
            self.parse(r.json(), clean, entry)

    def parse(self, data: Any, term: str, entry: PartialEntry) -> None:
        for item in data.get("results", [])[:5]:
            en_txt = le.clean_english_sentence(item.get("text", ""))
            if not en_txt or not le.sentence_contains_target(en_txt, term) or not (5 <= len(en_txt.split()) <= 20):
                continue
            fa_txt = ""
            for tr_group in item.get("translations", []):
                for tr in tr_group:
                    if tr.get("lang") == "pes" and tr.get("text"):
                        fa_txt = le.normalize_persian(tr["text"])
                        break
                if fa_txt:
                    break
            if en_txt and fa_txt and len(entry.examples) < 3:
                entry.examples.append({"en": en_txt, "fa": fa_txt})
