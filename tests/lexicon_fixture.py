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
import sqlite3
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
    # --- Words and phrases from the review screenshots, in Kaikki's real shape (glosses, tags, topics, examples
    # with type and ref, synonyms and antonyms at entry and sense level, forms, derived, related, translations) ---
    {"word": "possess", "pos": "verb", "lang_code": "en",
     "sounds": [{"ipa": "/pəˈzɛs/", "tags": ["US"]}, {"ipa": "/pəˈzɛs/", "tags": ["UK"]}],
     "forms": [{"form": "possesses", "tags": ["present", "singular", "third-person"]},
               {"form": "possessing", "tags": ["participle", "present"]},
               {"form": "possessed", "tags": ["participle", "past"]}, {"form": "possessed", "tags": ["past"]},
               {"form": "possess", "tags": ["table-tags"]}],
     "senses": [
         {"glosses": ["To have (something) as, or as if as, an owner; to have, to own."],
          "examples": [{"text": "He does not even possess a working telephone.", "type": "example"}]},
         {"glosses": ["Of an idea, thought, etc.: to dominate (someone's mind); to strongly influence."],
          "examples": [{"text": "I am poſſeſt with an adulterate blot, / My bloud is mingled with the crime of luſt: […]",
                        "type": "quotation", "ref": "1599, William Shakespeare, Hamlet"}]},
         {"glosses": ["Of a supernatural entity, especially one regarded as evil: to take control of (an animal or person's body or mind)."],
          "examples": [{"text": "They thought he was possessed by evil spirits.", "type": "example"}]},
     ],
     "synonyms": [{"word": "own", "sense": "to have as an owner"}, {"word": "have", "sense": "to have as an owner"},
                  {"word": "bewitch", "sense": "to take control of someone's mind", "tags": ["dated"]},
                  {"word": "dominate", "sense": "to dominate someone's mind"}],
     "derived": [{"word": "adversely possess"}, {"word": "mispossess"}, {"word": "possessable"}, {"word": "possessible"},
                 {"word": "possessed"}, {"word": "possessee"}, {"word": "possessing"}, {"word": "possessingly"}],
     "related": [{"word": "possession"}, {"word": "possessive"}, {"word": "possessor"}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "to have", "word": "داشتن"},
                      {"code": "fa", "lang": "Persian", "sense": "to have", "word": "دارا بودن"}]},
    {"word": "possesses", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["third-person singular simple present indicative of possess"], "form_of": [{"word": "possess"}]}]},
    {"word": "possessed", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["simple past and past participle of possess"], "form_of": [{"word": "possess"}]}]},
    {"word": "possessing", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["present participle and gerund of possess"], "form_of": [{"word": "possess"}]}]},
    {"word": "trump", "pos": "noun", "lang_code": "en", "sounds": [{"ipa": "/tɹʌmp/", "tags": ["US"]}],
     "forms": [{"form": "trumps", "tags": ["plural"]}],
     "senses": [
         {"glosses": ["The suit, in a game of cards, that outranks all others."], "topics": ["card-games"],
          "examples": [{"text": "Diamonds were declared trump(s).", "type": "example"}]},
         {"glosses": ["A playing card of that suit."], "topics": ["card-games"],
          "examples": [{"text": "He played an even higher trump.", "type": "example"}]},
         {"glosses": ["Something that gives one an advantage, especially one held in reserve."], "tags": ["figuratively"],
          "examples": [{"text": "VVere lacke Strawe a liuee, And I in as good poſſibility as euer I was, I would lay a ſurer trumpe, Ere I would loſe ſo faire a tricke.",
                        "type": "quotation", "ref": "1594, William Shakespeare, Henry VI"}]},
         {"glosses": ["A trumpet."], "tags": ["obsolete"],
          "synonyms": [{"word": "cornet"}, {"word": "horn"}],
          "examples": [{"text": "This cornet [translating Cornet] was a brave young cavalier and not more than two years older than me.",
                        "type": "quotation", "ref": "1905, translated work"}]},
     ],
     "derived": [{"word": "trump card"}, {"word": "trump up"}, {"word": "trumpery"}, {"word": "trumpet"}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "card", "word": "خال"}]},
    {"word": "trump", "pos": "verb", "lang_code": "en", "sounds": [{"ipa": "/tɹʌmp/", "tags": ["US"]}],
     "forms": [{"form": "trumps", "tags": ["present", "singular", "third-person"]},
               {"form": "trumping", "tags": ["participle", "present"]}, {"form": "trumped", "tags": ["past"]}],
     "senses": [
         {"glosses": ["To play on (a card of another suit) with a trump."], "topics": ["card-games"],
          "synonyms": [{"word": "ruff"}],
          "examples": [{"text": "He knew the hand was lost when his ace was trumped.", "type": "example"}]},
         {"glosses": ["To get the better of; to outdo."],
          "synonyms": [{"word": "outdo"}, {"word": "outflank"}, {"word": "beat"}, {"word": "top"}],
          "examples": [{"text": "Her argument trumped everything he had said.", "type": "example"}]},
     ]},
    {"word": "trumped", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["simple past and past participle of trump"], "form_of": [{"word": "trump"}]}]},
    {"word": "Trump", "pos": "name", "lang_code": "en",
     "senses": [{"glosses": ["A surname."], "examples": [{"text": "# Off she went with a trumpety-trump #", "type": "example"}]}]},
    {"word": "privilege", "pos": "noun", "lang_code": "en",
     "sounds": [{"ipa": "/ˈpɹɪv.ɪ.lɪd͡ʒ/", "tags": ["US"]}, {"ipa": "/ˈpɹɪv.(ə.)lɪd͡ʒ/", "tags": ["UK"]}],
     "forms": [{"form": "privileges", "tags": ["plural"]}],
     "senses": [
         {"glosses": ["An exemption from certain laws granted by the Pope."], "topics": ["Catholicism"]},
         {"glosses": ["A particular benefit, advantage, or favor; a right or immunity enjoyed by some but not others; a prerogative, preferential treatment."],
          "examples": [{"text": "All first-year professors here must teach four courses a term, yet you're only teaching one! What entitled you to such a privilege?", "type": "example"}]},
         {"glosses": ["An especially rare or fortunate opportunity; the good fortune (to do something)."]},
     ],
     "derived": [{"word": "absolute privilege"}, {"word": "attorney-client privilege"}, {"word": "black privilege"},
                 {"word": "check your privilege"}, {"word": "cis privilege"}, {"word": "cis-privilege"},
                 {"word": "cisprivilege"}, {"word": "deprivilege"}, {"word": "white privilege"}, {"word": "male privilege"},
                 {"word": "privilegeless"}, {"word": "unprivileged"}, {"word": "privileged"}],
     "related": [{"word": "privileged"}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "benefit", "word": "امتیاز"},
                      {"code": "fa", "lang": "Persian", "sense": "benefit", "word": "حق ویژه"}]},
    {"word": "privilege", "pos": "verb", "lang_code": "en", "sounds": [{"ipa": "/ˈpɹɪv.ɪ.lɪd͡ʒ/", "tags": ["US"]}],
     "senses": [{"glosses": ["To prioritize."]}]},
    {"word": "privileged", "pos": "adj", "lang_code": "en",
     "senses": [{"glosses": ["Having a privilege."]}]},
    {"word": "rational", "pos": "adj", "lang_code": "en", "sounds": [{"ipa": "/ˈɹæʃ.nəl/", "tags": ["US"]}],
     "senses": [
         {"glosses": ["Capable of being expressed as a ratio of two integers."], "tags": ["not-comparable"], "topics": ["mathematics"],
          "examples": [{"text": "The quotient of two rational numbers is again a rational number.", "type": "example"}]},
         {"glosses": ["Having the capacity to reason; able to think clearly."],
          "synonyms": [{"word": "noetic"}, {"word": "lucid"}],
          "examples": [{"text": "Humans are rational animals.", "type": "example"}]},
         {"glosses": ["Not contrary to reason; logical, reasonable, sensible."],
          "examples": [{"text": "It was a perfectly rational decision.", "type": "example"}]},
     ],
     "synonyms": [{"word": "logical"}, {"word": "reasonable", "sense": "not contrary to reason"},
                  {"word": "sensible", "sense": "not contrary to reason"}, {"word": "sane"}, {"word": "noetic"},
                  {"word": "ratiocinative", "tags": ["rare"]}],
     "antonyms": [{"word": "irrational"}, {"word": "illogical"}],
     "derived": [{"word": "birational"}, {"word": "extrarational"}, {"word": "rationally"}, {"word": "nonrational"},
                 {"word": "rational number"}, {"word": "rational choice"}],
     "translations": [{"code": "fa", "lang": "Persian", "sense": "capable of reasoning", "word": "عقلانی"}]},
    {"word": "rational", "pos": "noun", "lang_code": "en",
     "senses": [{"glosses": ["A rational number."], "topics": ["mathematics"]}]},
    {"word": "logical", "pos": "adj", "lang_code": "en", "senses": [{"glosses": ["Following the rules of logic."]}]},
    {"word": "reasonable", "pos": "adj", "lang_code": "en", "senses": [{"glosses": ["Fair and sensible."]}]},
    {"word": "sensible", "pos": "adj", "lang_code": "en", "senses": [{"glosses": ["Having good sense."]}]},
    {"word": "dial", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["To turn a dial."], "examples": [{"text": "He dialed the number twice.", "type": "example"}]}]},
    {"word": "dials", "pos": "verb", "lang_code": "en", "senses": [{"glosses": ["third-person singular of dial"], "form_of": [{"word": "dial"}]}]},
    {"word": "dialed", "pos": "verb", "lang_code": "en", "senses": [{"glosses": ["simple past of dial"], "form_of": [{"word": "dial"}]}]},
    {"word": "dialing", "pos": "verb", "lang_code": "en", "senses": [{"glosses": ["present participle of dial"], "form_of": [{"word": "dial"}]}]},
    {"word": "dial down", "pos": "verb", "lang_code": "en",
     "forms": [{"form": "dials down", "tags": ["present", "singular", "third-person"]},
               {"form": "dialing down", "tags": ["participle", "present"]}, {"form": "dialed down", "tags": ["past"]}],
     "senses": [
         {"glosses": ["To reduce the intensity or level of something."], "tags": ["transitive"],
          "examples": [{"text": "Can you dial down the music a little?", "type": "example"}]},
         {"glosses": ["To calm down; to become less aggressive or excited."], "tags": ["informal", "idiomatic"],
          "examples": [{"text": "You need to dial it down before someone gets hurt.", "type": "example"}]},
     ]},
    {"word": "bed of roses", "pos": "noun", "lang_code": "en",
     "senses": [{"glosses": ["A situation or activity that is comfortable or easy."], "tags": ["idiomatic"],
                 "examples": [{"text": "Life as a student is not exactly a bed of roses.", "type": "example"}]}]},
    {"word": "no bed of roses", "pos": "phrase", "lang_code": "en",
     "senses": [{"glosses": ["Said of a situation or activity that is difficult or unpleasant."]}]},
    {"word": "bring someone to heel", "pos": "verb", "lang_code": "en",
     "senses": [{"glosses": ["To make someone obey or submit."], "tags": ["idiomatic"]}]},
    {"word": "a bird in the hand is worth two in the bush", "pos": "proverb", "lang_code": "en",
     "senses": [{"glosses": ["It is better to have a lesser but certain advantage than to seek a greater but uncertain one."]}]},
    {"word": "color", "pos": "noun", "lang_code": "en", "senses": [{"glosses": ["The property of reflecting light of a particular wavelength."]}]},
    {"word": "colour", "pos": "noun", "lang_code": "en",
     "senses": [{"glosses": ["Alternative spelling of color"], "alt_of": [{"word": "color"}], "tags": ["alt-of", "British"]}]},
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
    # Natural lines for the screenshot words, seen often enough to count as everyday words and examples.
    + [("He possesses a very rare talent.", "او استعداد بسیار نادری دارد.")] * 3
    + [("I don't possess that kind of power.", "من چنین قدرتی ندارم.")] * 3
    + [("They say he was possessed by the devil.", "می گویند شیطان در او رفته بود.")] * 3
    + [("That is a perfectly rational decision.", "این تصمیم کاملا منطقی است.")] * 3
    + [("Let's be reasonable about this.", "بیا در این باره منطقی باشیم.")] * 3
    + [("Try to be logical for once.", "یک بار هم که شده منطقی باش.")] * 3
    + [("It's a sensible plan, you know.", "نقشه معقولی است، می دانی.")] * 3
    + [("Check your privilege before you speak.", "قبل از حرف زدن امتیازت را بسنج.")] * 3
    + [("It was a privilege to meet you.", "افتخار آشنایی با شما را داشتم.")] * 3
    + [("White privilege is a real thing.", "امتیاز سفیدپوستان واقعی است.")] * 3
    + [("He trumped my ace with a king.", "او تک خال مرا با شاه برد.")] * 3
    + [("Can you dial it down a little?", "میشه یه کم آروم تر باشی؟")] * 3
    + [("Life is no bed of roses.", "زندگی کار آسانی نیست.")] * 3
    + [("Put the book on the table, please.", "لطفا کتاب را روی میز بگذار.")] * 3
    + [("We walked along the shore of the lake.", "کنار ساحل دریاچه قدم زدیم.")] * 3
    + [("My savings are in a safe place.", "پس اندازم در بانک است.")] * 3
    + [("I have my own house in the north.", "من خانه خودم را در شمال دارم.")] * 3
    + [("Nobody can outdo him at cards.", "هیچکس در ورق از او بهتر نیست.")] * 3
    + [("That sounds completely irrational to me.", "این برای من کاملا نامعقول به نظر می رسد.")] * 3
    + [("The garage was full of old cars.", "گاراژ پر از ماشین های قدیمی بود.")] * 1
    + [("Because I...", "چون من...")] * 3  # a fragment: never an example
    + [("♪ I will always love you ♪", "من همیشه دوستت خواهم داشت")] * 3  # lyrics
    + [("- Where were you? - At home.", "- کجا بودی؟ - خانه.")] * 3  # two speakers on one line
    + [("This is a possess line that is far too long to be a natural subtitle example sentence for anyone.",
        "این یک خط خیلی بلند است که مثال خوبی نیست.")] * 3
)
IDIOMS_FA = [  # (phrase, Persian, source): what the pre-translated idiom dataset holds
    ("dial it down", "آرام تر باش", "idioms-fa"),
    ("no bed of roses", "کار آسانی نیست", "idioms-fa"),
    ("break the ice", "یخ را شکستن", "idioms-fa"),
    ("spill the beans", "رازی را فاش کردن", "idioms-fa"),
    ("bring someone to heel", "کسی را سر به راه کردن", "idioms-fa"),
]
URBAN = [
    {"word": "rizz", "definition": "[Charisma], especially in flirting.", "example": "He has so much rizz.", "thumbs_up": "10", "thumbs_down": "2"},
    {"word": "rizz", "definition": "Spam entry.", "example": "", "thumbs_up": "5", "thumbs_down": "3"},
    {"word": "rizz", "definition": "Nobody voted.", "example": "", "thumbs_up": "0", "thumbs_down": "0"},
    {"word": "yeet", "definition": "To throw something with force.", "example": "Yeet the ball!", "thumbs_up": "4", "thumbs_down": "2"},
    {"word": "bank", "definition": "To be sure of; as in you can bank on it.", "example": "You can bank on it.", "thumbs_up": "40", "thumbs_down": "3"},
    {"word": "dial it down", "definition": "To tone down intensity or attitude.", "example": "Dial it down, man.", "thumbs_up": "30", "thumbs_down": "2"},
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
    idioms = os.path.join(folder, "idioms_fa.tsv")
    with open(idioms, "w", encoding="utf-8") as f:
        f.write("phrase\tfa\tsource\n" + "".join(f"{en}\t{fa}\t{src}\n" for en, fa, src in IDIOMS_FA))
    return {"kaikki": kaikki, "tatoeba_dir": tatoeba, "opensubtitles": subs, "urban": urban, "idioms_fa": idioms}


def write_slang_db(folder: str) -> str:
    """A compact slang database as a separate script might ship it: its own column names, votes as a ratio."""
    path = os.path.join(folder, "ocean_slang.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE slang (word TEXT, definition TEXT, example TEXT, thumbs_ratio REAL)")
    conn.executemany("INSERT INTO slang VALUES (?, ?, ?, ?)", [
        ("Rizz", "[Charisma], especially in flirting.", "He has so much rizz.", 0.85),
        ("rizz", "Spam entry.", "", 0.4),
        ("simp", "Someone who does too much for a person they like.", "Stop being a simp.", 0.9),
    ])
    conn.commit()
    conn.close()
    return path


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
    """Point lexical_engine.LEXICON at the fixture database (or at `path`; None: no database) for one test.

    Also isolates the test from the machine: a fresh in-memory translation cache (the real one is a permanent file
    that would carry answers from test to test) and no internet (the connectivity probe fails at once; a test that
    wants the online layer patches lexical_engine._probe_connectivity itself)."""
    import lexical_engine
    import translation_cache
    db = lexicon_db.LexiconDB(fixture_db() if path == "fixture" else (path or os.path.join(tempfile.mkdtemp(), "none.db")))
    for patcher in (
        mock.patch.object(lexical_engine, "LEXICON", db),
        mock.patch.object(lexical_engine, "TRANSLATION_CACHE", translation_cache.TranslationCache(":memory:")),
        mock.patch.object(lexical_engine, "_probe_connectivity", return_value=False),
        mock.patch.object(lexical_engine, "FA_MIN_SUBTITLE_COUNT", 2),  # the sample subtitles are tiny
    ):
        patcher.start()
        test.addCleanup(patcher.stop)
    lexical_engine.forget_connectivity()
    test.addCleanup(lexical_engine.forget_connectivity)
    return db
