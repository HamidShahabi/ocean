"""A tiny ocean_lexicon.db built by the real import tool from sample dumps in the real file formats
(Kaikki JSON lines .gz, Tatoeba .tsv.bz2, an OPUS Moses zip, an Urban Dictionary CSV). Shared by the tests;
built once per test process.

Also: use_lexicon(test) points the engine at it (or at no database) for one test."""
import bz2
import contextlib
import csv
import io
import gzip
import json
import os
import sys
import tempfile
import zipfile
from typing import Optional
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_lexicon_db  # noqa: E402
import lexicon_db  # noqa: E402

KAIKKI_ROWS = [
    {"word": "bank", "pos": "noun", "lang_code": "en",
     "sounds": [{"ipa": "/baŋk/", "tags": ["UK"]}, {"ipa": "[bæŋk]", "tags": ["US"]}, {"ipa": "/bæŋk/", "tags": ["US"]}],
     "senses": [
         {"glosses": ["An institution where one can place and borrow money."],
          "examples": [{"text": "A long quotation that should not be used because a usage example exists here.", "type": "quotation"},
                       {"text": "I keep my savings in a bank.", "type": "example"}]},
         {"glosses": ["Sloping land beside a river."], "tags": ["countable"],
          "examples": [{"text": "blood bank; data bank", "type": "example", "tags": ["collocation"]}]},
         {"glosses": ["A bench."], "tags": ["obsolete"]},
     ],
     "derived": [{"word": "bank account"}, {"word": "bankroll"}, {"word": "bank account"}],
     "translations": [
         {"code": "fa", "lang": "Persian", "sense": "institution", "word": "بانْک"},
         {"code": "fa", "lang": "Dari", "sense": "institution", "word": "داری"},
         {"code": "fa", "lang": "Persian", "sense": "edge of river", "word": "کرانه"},
         {"code": "fa", "lang": "Iranian Persian", "sense": "edge of river", "word": "ساحل"},
         {"code": "de", "lang": "German", "sense": "institution", "word": "Bank"},
     ]},
    {"word": "bank", "pos": "verb", "lang_code": "en", "sounds": [{"ipa": "/bæŋk/"}],
     "senses": [{"glosses": ["To deposit money in a bank."], "examples": [{"text": "I bank with a local branch.", "type": "example"}]}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "to deposit", "word": "سپردن"}]},
    {"word": "banks", "pos": "noun", "lang_code": "en",
     "senses": [{"glosses": ["plural of bank"], "form_of": [{"word": "bank"}], "tags": ["form-of", "plural"]}]},
    {"word": "Banks", "pos": "name", "lang_code": "en", "senses": [{"glosses": ["A surname."]}]},
    {"word": "run", "pos": "verb", "lang_code": "en", "sounds": [{"ipa": "/ɹʌn/", "tags": ["General-American"]}],
     "senses": [{"glosses": ["To move swiftly on foot."], "examples": [{"text": "She can run very fast.", "type": "example"}]}]},
    {"word": "run", "pos": "noun", "lang_code": "en", "senses": [{"glosses": ["An act of running."]}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "act of running", "word": "دو"}]},
    {"word": "ran", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["simple past of run"], "form_of": [{"word": "run"}], "tags": ["form-of"]}]},
    {"word": "break the ice", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["To start a conversation with strangers."], "tags": ["idiomatic"],
                 "examples": [{"text": "He told a joke to break the ice.", "type": "example"}]},
                {"glosses": ["Used other than figuratively or idiomatically: see break, the, ice."]}]},
    {"word": "book", "pos": "noun", "lang_code": "en", "senses": [{"glosses": ["A collection of printed pages."]}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "printed pages", "word": "نامه"},
                      {"code": "fa", "lang": "Persian", "sense": "printed pages", "word": "کتاب"}]},
    {"word": "bright", "pos": "adj", "lang_code": "en", "sounds": [{"ipa": "/bɹaɪt/"}],
     "senses": [{"glosses": ["Giving off a lot of light."], "tags": ["informal"],
                 "examples": [{"text": "The bright sun hurt my eyes.", "type": "example"}]}]},
    {"word": "heavy", "pos": "adj", "lang_code": "en", "senses": [{"glosses": ["Having great weight."]}]},
    {"word": "-ness", "pos": "suffix", "lang_code": "en", "senses": [{"glosses": ["Forms nouns."]}]},
    {"word": "maison", "pos": "noun", "lang_code": "fr", "senses": [{"glosses": ["house"]}]},
]
TATOEBA = [  # (English, Persian)
    ("I'm going to the bank.", "من دارم به بانک می روم."),
    ("I love you.", "دوستت دارم."),
    ("I read a book yesterday at home.", "من دیروز در خانه یک کتاب خواندم."),
    ("The river bank was muddy after the rain.", "ساحل رودخانه بعد از باران گل آلود بود."),
    ("Hello there, my friend.", "hello"),  # no Persian: dropped
]
SUBTITLES = (
    [("Break a leg!", "موفق باشی!")] * 2 + [("Break a leg!", "پاتو بشکن")]
    + [("Run!", "بدو!")] * 3
    + [("Good morning.", "صبح بخیر")]  # seen once: not enough agreement
    + [("The bank is closed today, sorry.", "امروز بانک تعطیله، ببخشید.")] * 2
    + [("This line has far too many words to be kept as a phrase in the database at all.", "این خط خیلی بلند است")] * 2
    + [("- I love you.", "- دوستت دارم")] * 2
)
URBAN = [
    {"word": "rizz", "definition": "[Charisma], especially in flirting.", "example": "He has so much rizz.", "thumbs_up": "10", "thumbs_down": "2"},
    {"word": "rizz", "definition": "Spam entry.", "example": "", "thumbs_up": "5", "thumbs_down": "3"},
    {"word": "rizz", "definition": "Nobody voted.", "example": "", "thumbs_up": "0", "thumbs_down": "0"},
    {"word": "yeet", "definition": "To throw something with force.", "example": "Yeet the ball!", "thumbs_up": "4", "thumbs_down": "2"},
]

_BUILT: Optional[str] = None


def write_dumps(folder: str) -> dict:
    """The sample datasets as files in their real download formats; returns build()'s arguments."""
    kaikki = os.path.join(folder, "kaikki.jsonl.gz")
    with gzip.open(kaikki, "wt", encoding="utf-8") as f:
        for row in KAIKKI_ROWS:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.write("not json\n\n")
    tatoeba = os.path.join(folder, "tatoeba")
    os.makedirs(tatoeba)
    with bz2.open(os.path.join(tatoeba, "eng_sentences.tsv.bz2"), "wt", encoding="utf-8") as f:
        f.write("".join(f"{i}\teng\t{en}\n" for i, (en, _) in enumerate(TATOEBA, 1)) + "999\teng\tUnlinked sentence.\n")
    with bz2.open(os.path.join(tatoeba, "pes_sentences.tsv.bz2"), "wt", encoding="utf-8") as f:
        f.write("".join(f"{100 + i}\tpes\t{fa}\n" for i, (_, fa) in enumerate(TATOEBA, 1)))
    with bz2.open(os.path.join(tatoeba, "eng-pes_links.tsv.bz2"), "wt", encoding="utf-8") as f:
        f.write("".join(f"{i}\t{100 + i}\n" for i in range(1, len(TATOEBA) + 1)))
    subs = os.path.join(folder, "en-fa.txt.zip")
    with zipfile.ZipFile(subs, "w") as z:
        z.writestr("README", "OPUS")
        z.writestr("OpenSubtitles.en-fa.en", "".join(en + "\n" for en, _ in SUBTITLES))
        z.writestr("OpenSubtitles.en-fa.fa", "".join(fa + "\n" for _, fa in SUBTITLES))
    urban = os.path.join(folder, "urban.csv")
    with open(urban, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(URBAN[0]))
        w.writeheader()
        w.writerows(URBAN)
    return {"kaikki": kaikki, "tatoeba_dir": tatoeba, "opensubtitles": subs, "urban": urban}


def fixture_db() -> str:
    global _BUILT
    if _BUILT is None:
        folder = tempfile.mkdtemp()
        out = os.path.join(folder, "ocean_lexicon.db")
        with contextlib.redirect_stdout(io.StringIO()):  # the tool's progress lines
            build_lexicon_db.build(out, **write_dumps(folder))
        _BUILT = out
    return _BUILT


def use_lexicon(test, path: Optional[str] = "fixture") -> lexicon_db.LexiconDB:
    """Point lexical_engine.LEXICON at the fixture database (or at `path`; None: no database) for one test."""
    import lexical_engine
    db = lexicon_db.LexiconDB(fixture_db() if path == "fixture" else (path or os.path.join(tempfile.mkdtemp(), "none.db")))
    patcher = mock.patch.object(lexical_engine, "LEXICON", db)
    patcher.start()
    test.addCleanup(patcher.stop)
    return db
