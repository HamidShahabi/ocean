import re

from bs4 import BeautifulSoup

import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class LongmanSource(DictionarySource):
    name = "longman"
    label = "Longman Dictionary (LDOCE)"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        slug = "-".join(ctx.term.strip().lower().split())
        r = ctx.request("GET", f"https://www.ldoceonline.com/dictionary/{slug}", timeout=3.0)
        if r.ok:
            self.parse(r.text, ctx.term, entry)

    def parse(self, html: str, term: str, entry: PartialEntry) -> None:
        soup = BeautifulSoup(html, "lxml")

        hwd_el = soup.select_one(".HWD")
        hwd_txt = hwd_el.get_text(strip=True).lower() if hwd_el else ""
        pron = soup.select_one(".PRON")
        if pron and (not hwd_txt or hwd_txt == term.strip().lower()):
            p_txt = pron.get_text(strip=True)
            if p_txt:
                entry.ipa_us = le.clean_ipa_display(p_txt)

        pos_el = soup.select_one(".POS")
        default_pos = pos_el.get_text(strip=True).capitalize() if pos_el else "Entry"

        for sense in soup.select(".Sense")[:5]:
            s_pos_el = sense.select_one(".POS")
            s_pos = s_pos_el.get_text(strip=True).capitalize() if s_pos_el else default_pos
            d_el = sense.select_one(".DEF")
            if not d_el:
                continue
            d_txt = re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s+", " ", d_el.get_text(" ", strip=True))).strip()
            if not d_txt or len(d_txt.split()) < 2 or d_txt.lower().startswith("when you "):
                continue
            if not d_txt.endswith("."):
                d_txt += "."
            d_txt = d_txt[0].upper() + d_txt[1:]

            ex_sent = ""
            for ex_el in sense.select(".EXAMPLE"):
                cand = le.clean_english_sentence(ex_el.get_text(" ", strip=True))
                if cand and len(cand.split()) >= 4 and le.sentence_contains_target(cand, term):
                    ex_sent = cand
                    break

            entry.senses.append({"pos": s_pos, "def": d_txt, "example_en": ex_sent})

        for col_box in soup.select(".ColloBox, .Collocate")[:5]:
            col_term_el = col_box.select_one(".COLLOC, .collo")
            col_ex_el = col_box.select_one(".EXAMPLE")
            if col_term_el:
                c_term = re.sub(r"\s+", " ", col_term_el.get_text(" ", strip=True)).strip()
                c_ex = le.clean_english_sentence(col_ex_el.get_text(" ", strip=True)) if col_ex_el else ""
                if c_term and len(c_term) > 3 and c_ex:
                    entry.collocations.append({"term": c_term, "example_en": c_ex})
