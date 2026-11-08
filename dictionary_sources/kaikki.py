import lexical_engine as le
import lexicon_text as lt
from .base import DictionarySource, LookupContext, PartialEntry

MAX_FORMS = 6


class KaikkiSource(DictionarySource):
    """Wiktionary via the Kaikki.org extract: IPA, senses by part of speech with usage examples, synonyms and
    antonyms (kept with the sense or hint they belong to), forms, alternative spellings, collocations and derived
    terms (an inflected form gives its lemma's entry).

    Every sense is passed on with a class (lt.sense_class: 0 ordinary, 2 specialist, 3 archaic or a bare name) and
    its tags; the engine chooses which few to show. Example sentences that are damaged or not modern (an old book's
    long-s OCR text, text with "[...]" or verse breaks) are dropped here, whatever the database holds."""
    name = "kaikki"
    label = "Wiktionary (Kaikki.org)"

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        db = le.LEXICON
        entries = db.entries(ctx.term)
        many_pos = len({e["pos"] for e in entries if e["senses"]}) > 1
        is_known = db.known_word if db.vocabulary_ok() else None
        forms: dict = {}
        alternatives: list = []
        for e in entries:
            if e["ipa"] and e["word"].lower() == ctx.term.strip().lower():  # not the lemma's: "ran" is not /ɹʌn/
                entry.ipa_us = entry.ipa_us or le.clean_ipa_display(e["ipa"])
            pos = "Idiom / Expression" if ctx.is_idiom and e["pos"] in ("Verb", "Phrase") else e["pos"]
            entry.primary_pos = entry.primary_pos or pos
            for s in e["senses"]:
                tags, raw = s.get("tags", []), s["def"]
                example = lt.clean_example(s.get("example", ""), is_known=is_known, target=ctx.term)
                entry.senses.append({
                    "pos": pos,
                    "def": le.clean_english_sentence(lt.display_definition(raw, tags)),
                    "example_en": le.clean_english_sentence(example) if example else "",
                    "raw_def": raw,
                    "tags": tags,
                    "klass": lt.sense_class(tags, raw, e["pos"], many_pos),
                })
                for kind, target in (("syn", entry.syns), ("ant", entry.ants)):
                    for item in s.get(kind, []):
                        target.append(_relation(item, pos, raw, ""))
            for item in e["synonyms"]:
                entry.syns.append(_relation(item, pos, "", item[1] if len(item) > 1 else ""))
            for item in e["antonyms"]:
                entry.ants.append(_relation(item, pos, "", item[1] if len(item) > 1 else ""))
            for form, tags in e["forms"]:
                kind = lt.classify_form(tags)  # archaic, dialect and Internet forms are neither shown nor alternatives
                if kind == "form":
                    forms.setdefault(form, [])
                    label = lt.form_label(tags)
                    if label and label not in forms[form]:
                        forms[form].append(label)
                elif kind == "alt" and form not in alternatives:
                    alternatives.append(form)
            if not ctx.is_idiom:
                for phrase in e["collocations"]:
                    if phrase.lower() not in (c["term"].lower() for c in entry.collocations):
                        entry.collocations.append({"term": phrase, "example_en": ""})
                entry.derived += [d for d in e["derived"] if d not in entry.derived]
                entry.related += [d for d in e["related"] if d not in entry.related]
        entry.forms = [{"form": f, "label": ", ".join(labels)} for f, labels in forms.items()][:MAX_FORMS]
        if not ctx.is_idiom:
            entry.alternatives = alternatives + [a for a in db.alternatives(ctx.term) if a not in alternatives]


def _relation(item, pos: str, raw_def: str, hint: str) -> dict:
    """A synonym or antonym as stored ([word, sense hint, tags]); raw_def names the sense it was listed under
    (empty for an entry-level one, which carries a hint of the sense instead, or nothing)."""
    word = item[0] if isinstance(item, (list, tuple)) else item.get("word", "")
    tags = item[2] if isinstance(item, (list, tuple)) and len(item) > 2 else ""
    return {"term": word, "pos": pos, "raw_def": raw_def, "hint": hint, "tags": tags}
