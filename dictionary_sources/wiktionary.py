from typing import Any

import requests
from bs4 import BeautifulSoup

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class WiktionarySource(DictionarySource):
    """Fills in when the bundled idiom list and TheFreeDictionary gave fewer than two senses."""
    name = "wiktionary"
    label = "Wiktionary"
    needs = ("offline_idioms", "thefreedictionary")

    def applies(self, ctx: LookupContext) -> bool:
        return ctx.is_idiom

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        if len(prior.senses) >= 2:
            return
        wk_slug = "_".join(ctx.term.strip().split())
        r = ctx.request("GET", f"https://en.wiktionary.org/api/rest_v1/page/definition/{requests.utils.quote(wk_slug)}", timeout=2.5)
        if r.ok:
            self.parse(r.json(), entry)

    def parse(self, data: Any, entry: PartialEntry) -> None:
        for b in data.get("en", [])[:2]:
            pos = b.get("partOfSpeech", "Expression").capitalize()
            for df in b.get("definitions", [])[:2]:
                raw_d = BeautifulSoup(df.get("definition", ""), "lxml").get_text(" ", strip=True)
                if raw_d and len(raw_d.split()) >= 2:
                    exs = df.get("examples", [])
                    ex_s = BeautifulSoup(exs[0], "lxml").get_text(" ", strip=True) if exs else ""
                    entry.senses.append({
                        "pos": pos,
                        "def": le.clean_english_sentence(raw_d),
                        "example_en": le.clean_english_sentence(ex_s) if ex_s else "",
                    })
