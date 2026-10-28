"""Sources that read only data bundled with the app (offline_lexicon.json), so they keep working with no network."""
import lexical_engine as le
from .base import DictionarySource, LookupContext, PartialEntry


class OfflineIdiomsSource(DictionarySource):
    name = "offline_idioms"
    label = "Bundled idiom list"
    needs_network = False

    def applies(self, ctx: LookupContext) -> bool:
        return ctx.is_idiom or ctx.deep_mode

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        idiom = le.OFFLINE_LEXICON.get("idioms", {}).get(ctx.term.strip().lower())
        if idiom:
            entry.senses.append({
                "pos": "Idiom / Expression",
                "def": idiom["def"],
                "example_en": idiom["example"],
            })


class IdiomFallbackSource(DictionarySource):
    """Last resort for 'get tired'-style phrases nothing else knows: build a definition from the adjective."""
    name = "idiom_fallback"
    label = "Linking-verb phrase fallback"
    needs_network = False
    needs = ("offline_idioms", "thefreedictionary", "wiktionary")

    def applies(self, ctx: LookupContext) -> bool:
        return ctx.is_idiom or ctx.deep_mode

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        low = ctx.term.strip().lower()
        words = low.split()
        if prior.senses or len(words) < 2:
            return
        linking_verbs = {"get": "To become", "become": "To become", "grow": "To gradually become", "turn": "To turn or become", "feel": "To feel", "go": "To become"}
        first_w = words[0]
        last_w = words[-1]
        if first_w in linking_verbs and le.wn:
            syns = le.wn.synsets(last_w)
            if syns:
                head_def = syns[0].definition()
                comp_def = f"{linking_verbs[first_w]} {head_def}."
                comp_ex = f"Things started to {low} when the plan fell apart."
                entry.senses.append({
                    "pos": "Colloquial Phrase",
                    "def": comp_def[0].upper() + comp_def[1:],
                    "example_en": comp_ex,
                })


class CuratedSynonymsSource(DictionarySource):
    name = "curated_synonyms"
    label = "Curated synonyms"
    needs_network = False

    def fetch(self, ctx: LookupContext, entry: PartialEntry, prior: PartialEntry) -> None:
        low = ctx.term.strip().lower()
        if low in le.CURATED_SYNONYMS_MAP:
            entry.syns.extend(le.CURATED_SYNONYMS_MAP[low])
        if low in le.OFFLINE_LEXICON.get("idioms", {}):
            entry.syns.extend(le.OFFLINE_LEXICON["idioms"][low].get("syns", []))
