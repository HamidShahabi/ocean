import re

from bs4 import BeautifulSoup

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class TheFreeDictionarySource(DictionarySource):
    """Idioms section of thefreedictionary.com: only asked about phrases (or in deep mode)."""
    name = "thefreedictionary"
    label = "TheFreeDictionary (idioms)"

    def applies(self, ctx: LookupContext) -> bool:
        return ctx.is_idiom or ctx.deep_mode

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        slug = "+".join(ctx.term.strip().lower().split())
        r = ctx.request("GET", f"https://idioms.thefreedictionary.com/{slug}", timeout=2.8)
        if r.ok:
            self.parse(r.text, ctx.term, entry)

    def parse(self, html: str, term: str, entry: PartialEntry) -> None:
        clean = term.strip()
        soup = BeautifulSoup(html, "lxml")
        for ds in soup.select("#Definition section .ds-list, #Definition section .ds-single")[:4]:
            ex_texts = []
            for ill in ds.select(".illustration"):
                cand = le.clean_english_sentence(ill.get_text(" ", strip=True))
                if cand and len(cand.split()) >= 4 and le.sentence_contains_target(cand, clean):
                    ex_texts.append(cand)
                ill.decompose()

            raw_def = ds.get_text(" ", strip=True)
            raw_def = re.sub(r"^[0-9a-zA-Z]+\.\s*", "", raw_def)
            raw_def = re.sub(r"\s+", " ", raw_def).strip()
            if raw_def and len(raw_def.split()) >= 2:
                if not raw_def.endswith("."):
                    raw_def += "."
                raw_def = raw_def[0].upper() + raw_def[1:]
                entry.senses.append({
                    "pos": "Idiom / Expression",
                    "def": raw_def,
                    "example_en": ex_texts[0] if ex_texts else "",
                })
