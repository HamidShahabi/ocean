# === ANKI EXPORT: LEXICON ENTRIES -> ANKI-READY TAB-SEPARATED TEXT ===
# The header lines are Anki's file directives (Anki 2.1.55+): File > Import picks up the separator,
# note type, deck, field mapping and tag column with no manual setup. Front holds only the headword so
# re-importing updates existing notes instead of duplicating them (Anki matches on the first field).
import csv
import html
import io
from typing import Any, Dict, Iterable, List, Tuple

DECK_NAME = "Ocean"
MAX_SENSES = 3


def _esc(text: str) -> str:
    return html.escape(" ".join(str(text).split()))  # also folds tabs/newlines into spaces


def entry_kind(payload: Dict[str, Any]) -> str:
    if payload.get("is_reverse"):
        return "persian"
    if payload.get("is_sentence"):
        return "sentence"
    return "idiom" if len(str(payload.get("headword", "")).split()) >= 2 else "word"


def note_fields(payload: Dict[str, Any]) -> Tuple[str, str, str]:
    """(Front, Back, Tags) for one saved lexicon entry."""
    front = _esc(payload.get("headword", ""))
    kind = entry_kind(payload)
    if kind == "persian":
        # Persian -> English card: the translation, then English candidate words.
        back = f"<b>{_esc(payload.get('english_translation', ''))}</b>"
        words = [c.get("term", "") for c in payload.get("candidates", [])[:6] if c.get("term")]
        if words:
            back += f"<p>{_esc(', '.join(words))}</p>"
        return front, back, "ocean ocean::persian"

    back: List[str] = []
    ipa = payload.get("ipa_us", "")
    if ipa and kind != "sentence":
        back.append(f"<div style='color:#888'>{_esc(ipa)}</div>")
    fa = payload.get("persian_equivalent", "")
    if fa:
        back.append(f"<div dir='rtl' style='font-size:1.4em'>{_esc(fa)}</div>")
    for sense in payload.get("senses", [])[:MAX_SENSES]:
        line = _esc(sense.get("def", ""))
        if sense.get("pos"):
            line = f"<b>{_esc(sense['pos'])}</b> — {line}"
        if sense.get("example_en"):
            line += f"<br><i>{_esc(sense['example_en'])}</i>"
        if sense.get("example_fa"):
            line += f"<div dir='rtl'>{_esc(sense['example_fa'])}</div>"
        back.append(f"<p>{line}</p>")
    return front, "".join(back), f"ocean ocean::{kind}"


def build_anki_tsv(payloads: Iterable[Dict[str, Any]], deck: str = DECK_NAME) -> Tuple[str, int]:
    """Anki import text and the number of notes in it; entries without a headword are skipped."""
    out = io.StringIO()
    out.write("#separator:Tab\n#html:true\n#notetype:Basic\n")
    out.write(f"#deck:{deck}\n#columns:Front\tBack\tTags\n#tags column:3\n")
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    count = 0
    for payload in payloads:
        front, back, tags = note_fields(payload)
        if front:
            writer.writerow([front, back, tags])
            count += 1
    return out.getvalue(), count
