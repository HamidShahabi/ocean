import re

from bs4 import BeautifulSoup

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class CambridgeSource(DictionarySource):
    name = "cambridge"
    label = "Cambridge Dictionary"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        slug = "-".join(ctx.term.strip().lower().split())
        r = ctx.request("GET", f"https://dictionary.cambridge.org/us/dictionary/english/{slug}", timeout=3.0)
        if r.ok:
            self.parse(r.text, ctx.term, entry)

    def parse(self, html: str, term: str, entry: PartialEntry) -> None:
        soup = BeautifulSoup(html, "lxml")

        hw_el = soup.select_one(".hw.dhw")
        hw_txt = hw_el.get_text(strip=True).lower() if hw_el else ""
        us_span = soup.select_one(".us.dpron-i .ipa, .dpron-i .ipa")
        if us_span and (not hw_txt or hw_txt == term.strip().lower()):
            ipa_txt = us_span.get_text("", strip=True)
            if ipa_txt:
                entry.ipa_us = le.clean_ipa_display(ipa_txt)

        entry_blocks = soup.select(".pr.entry-body__el") or [soup]
        for eb in entry_blocks[:4]:
            pos_el = eb.select_one(".pos.dpos")
            eb_pos = pos_el.get_text(strip=True).capitalize() if pos_el else "Entry"

            for block in eb.select(".def-block.ddef_block")[:3]:
                def_el = block.select_one(".def.ddef_d.db")
                if not def_el:
                    continue
                d_text = re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s+", " ", def_el.get_text(" ", strip=True).rstrip(":"))).strip()
                if not d_text or len(d_text.split()) < 2 or d_text.lower().startswith("when you "):
                    continue
                if "in a pub" in d_text.lower():
                    continue
                if not d_text.endswith("."):
                    d_text += "."
                d_text = d_text[0].upper() + d_text[1:]

                example_sent = ""
                for ex_el in block.select(".examp.dexamp .eg.deg"):
                    cand = le.clean_english_sentence(ex_el.get_text(" ", strip=True))
                    if cand and len(cand.split()) >= 4 and le.sentence_contains_target(cand, term):
                        example_sent = cand
                        break

                entry.senses.append({
                    "pos": eb_pos,
                    "def": d_text,
                    "example_en": example_sent,
                })

            for syn_el in eb.select(".synonym .lcm, .thesaurus .lcm")[:4]:
                s_txt = syn_el.get_text(" ", strip=True).strip()
                if s_txt and s_txt.lower() != term.strip().lower() and s_txt.lower() not in le.WEAK_SYNONYMS:
                    entry.syns.append(s_txt)
