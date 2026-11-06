"""The text rules shared by the importer, the reader and the engine (lexicon_text.py): examples, definitions,
phrases, IPA."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lexicon_text as lt  # noqa: E402

OLD_BOOK = "I am poſſeſt with an adulterate blot, / My bloud is mingled with the crime of luſt: […]"
OCR_NO_LONG_S = "I am poffeft with an adulterate blot, / My bloud is mingled with the crime of luft: […]"
SHAKESPEARE = "VVere lacke Strawe a liuee, And I in as good poſſibility as euer I was, I would lay a ſurer trumpe."


class ExampleTest(unittest.TestCase):
    def test_a_modern_usage_example_is_kept(self):
        self.assertEqual(lt.clean_example("He does not even possess a working telephone."),
                         "He does not even possess a working telephone.")

    def test_old_book_damage_is_rejected_whatever_the_type(self):
        for text in (OLD_BOOK, OCR_NO_LONG_S, SHAKESPEARE):
            self.assertEqual(lt.clean_example(text), "", text)
            self.assertEqual(lt.clean_example(text, quotation=True, year=1999), "", text)

    def test_each_marker_rejects(self):
        bad = {
            "long s": "He loſt the game after all that.",
            "elision": "He lost the game and then […] he left.",
            "ascii elision": "He lost the game and then [...] he left.",
            "editorial note": "This cornet [translating Cornet] was a brave young cavalier.",
            "verse break": "The night was long, / the day was short and cold.",
            "hash": "# Off she went with a trumpety-trump #",
            "wiki links": "He lost the [[game]] after all that.",
            "wiki italics": "He lost the ''game'' after all that.",
            "template": "He lost the {{game}} after all that.",
            "html": "He lost the <b>game</b> after all that.",
            "year": "1999, John Smith, a book. He lost the game after all that.",
            "all caps": "HE LOST THE GAME AFTER ALL THAT",
            "archaic word": "Thou hast lost the game after all that.",
            "ocr capital": "VVhen he lost the game he left.",
            "too short": "He lost.",
            "too long": " ".join(["word"] * 25),
            "other script": "He lost the game after все that.",
        }
        for name, text in bad.items():
            self.assertEqual(lt.clean_example(text), "", name)

    def test_artefacts_are_cleaned_not_rejected(self):
        self.assertEqual(lt.clean_example("Diamonds were declared trump(s)."), "Diamonds were declared trump.")
        self.assertEqual(lt.clean_example("He lost the game [sic] after all that."), "He lost the game after all that.")
        self.assertEqual(lt.clean_example("- He lost the game after all that."), "He lost the game after all that.")

    def test_quotations_need_a_modern_date(self):
        text = "It was pure serendipity that we met."
        self.assertEqual(lt.clean_example(text, quotation=True, year=2007), text)
        self.assertEqual(lt.clean_example(text, quotation=True, year=1812), "")
        self.assertEqual(lt.clean_example(text, quotation=True, year=None), "")
        self.assertEqual(lt.clean_example("A [fine] quotation that has a bracket in it.", quotation=True, year=2001), "")
        self.assertEqual(lt.quotation_year("1997, Erin McKean, speech"), 1997)
        self.assertIsNone(lt.quotation_year("undated"))

    def test_unknown_words_reject_damaged_spelling_when_a_dictionary_can_say(self):
        known = {"i", "am", "with", "an", "the", "of", "blot", "possessed", "he", "lost", "game", "after", "all", "that", "serendipity"}
        text = "He lost the game after all that poffeft."
        self.assertEqual(lt.unknown_words(text, known.__contains__), ["poffeft"])
        self.assertEqual(lt.clean_example(text, is_known=known.__contains__), "")
        self.assertEqual(lt.clean_example("He lost the game after all that.", is_known=known.__contains__), "He lost the game after all that.")
        # Names and sentence-initial words are skipped; a headword's own variants are excused.
        self.assertEqual(lt.unknown_words("Hamlet lost the game.", known.__contains__), [])
        self.assertEqual(lt.unknown_words("He lost the serendipitous game.", known.__contains__, "serendipity"), [])
        self.assertEqual(lt.unknown_words("He don't lose.", (known | {"don't"}).__contains__), ["lose"])
        self.assertEqual(lt.unknown_words("He doesn't go.", lambda w: w in {"he", "doesn", "go"}), [])  # known by its stem


class SubtitleTest(unittest.TestCase):
    def test_a_natural_complete_line_is_kept(self):
        self.assertEqual(lt.clean_subtitle_line("- I can't believe he's gone."), "I can't believe he's gone.")
        self.assertEqual(lt.clean_subtitle_line("<i>You possess a rare talent.</i>", is_verb={"possess"}.__contains__), "You possess a rare talent.")
        self.assertEqual(lt.clean_subtitle_line("Get out of here!"), "Get out of here!")  # an imperative: a known verb-ish word

    def test_artefacts_fragments_and_long_lines_are_not_examples(self):
        bad = {
            "cut-off": "Because I...",
            "continued": "and then he left the house.",
            "no ending": "He left the house",
            "trailing comma": "He left the house,",
            "notes": "♪ I will always love you ♪",
            "sound cue": "(laughs) He left the house.",
            "bracket cue": "[phone rings] He left the house.",
            "speaker tag": "JOHN: He left the house.",
            "two speakers": "Where were you? - At home.",
            "caps": "HE LEFT THE HOUSE!",
            "hash": "# He left the house #",
            "long": "He left the house and then he walked down the long road to the old church by the river today.",
            "no verb": "The old house by the river.",
            "short": "Go home.",
        }
        for name, text in bad.items():
            self.assertEqual(lt.clean_subtitle_line(text), "", name)

    def test_the_dictionary_can_supply_the_verbs(self):
        self.assertEqual(lt.clean_subtitle_line("The garage door opens."), "")
        self.assertEqual(lt.clean_subtitle_line("The garage door opens.", is_verb={"opens"}.__contains__), "The garage door opens.")


class DefinitionTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(lt.sense_class([], "Giving off light."), 0)
        self.assertEqual(lt.sense_class(["mathematics"], "Capable of being expressed as a ratio."), 2)
        self.assertEqual(lt.sense_class([], "(mathematics) A rational number."), 2)
        self.assertEqual(lt.sense_class(["law"], "A doctrine that protects communications."), 2)
        self.assertEqual(lt.sense_class(["ecclesiastical", "historical", "law", "religion"], "An exemption granted by the Pope."), 3)
        for tag in ("archaic", "obsolete", "dated", "rare", "historical"):
            self.assertEqual(lt.sense_class([tag], "A thing."), 3, tag)
        self.assertEqual(lt.sense_class([], "A surname."), 3)
        self.assertEqual(lt.sense_class([], "A male given name from Old English."), 3)
        self.assertEqual(lt.sense_class([], "A city in Texas, United States."), 3)
        self.assertEqual(lt.sense_class([], "The capital of France.", "Proper noun", has_other_pos=False), 0)
        self.assertEqual(lt.sense_class([], "Something.", "Proper noun", has_other_pos=True), 3)
        self.assertEqual(lt.sense_class([], "Of or relating to the study of rocks."), 2)
        self.assertEqual(lt.sense_class(["figuratively", "informal"], "Something that gives an advantage."), 0)
        self.assertEqual(lt.sense_class([], "(archaic) A trumpet."), 3)  # a label in an older import's definition text

    def test_parentheticals_are_stripped_and_clauses_cut(self):
        self.assertEqual(lt.shorten_definition("To have (something) as, or as if as, an owner; to have, to own."),
                         "To have something as, or as if as, an owner; to have, to own.")
        self.assertEqual(lt.shorten_definition("An especially rare or fortunate opportunity; the good fortune (to do something)."),
                         "An especially rare or fortunate opportunity; the good fortune.")
        self.assertEqual(lt.shorten_definition("(informal) (Britain) Giving off a lot of light (as from a star)."), "(informal) Giving off a lot of light.")
        long = ("A particular benefit, advantage, or favor; a right or immunity enjoyed by some but not others; "
                "a prerogative, preferential treatment.")
        self.assertEqual(lt.shorten_definition(long), "A particular benefit, advantage, or favor; a right or immunity enjoyed by some but not others.")
        self.assertEqual(lt.shorten_definition("Short."), "Short.")

    def test_labels_of_tags_come_in_front(self):
        self.assertEqual(lt.display_definition("Giving off light.", ["informal", "mathematics"]), "(informal) Giving off light.")
        self.assertEqual(lt.display_definition("(informal) Giving off light.", []), "(informal) Giving off light.")  # an older import
        self.assertEqual(lt.display_definition("Giving off light.", []), "Giving off light.")
        self.assertEqual(lt.split_labels("(informal, idiomatic) To calm down."), (["informal", "idiomatic"], "To calm down."))


class PhraseTest(unittest.TestCase):
    def test_neutral_words_determiners_and_negations_go(self):
        self.assertEqual(lt.phrase_norm("dial it down".split()), "dial down")
        self.assertEqual(lt.phrase_norm("no bed of roses".split()), "bed of roses")
        self.assertEqual(lt.phrase_norm("bring someone to heel".split()), "bring to heel")
        self.assertEqual(lt.phrase_norm("bring him to heel".split()), "bring to heel")
        self.assertEqual(lt.phrase_norm("take one's time".split()), "take time")
        self.assertEqual(lt.phrase_norm("make your day".split()), "make day")
        self.assertTrue(lt.is_negated("no bed of roses".split()))
        self.assertFalse(lt.is_negated("bed of roses".split()))


class IpaTest(unittest.TestCase):
    def test_one_notation(self):
        self.assertEqual(lt.normalize_ipa("/ˈpɹɪv.ɪ.lɪd͡ʒ/"), "ˈprɪvɪlɪdʒ")     # ɹ -> r, tie bar and syllable dots gone
        self.assertEqual(lt.normalize_ipa("/ˈpɹɪv(ɪ)lɪd͡ʒ/"), "ˈprɪvɪlɪdʒ")    # optional sounds are plain sounds
        self.assertEqual(lt.normalize_ipa("[bɝd]"), "bɜrd")                    # rhotic vowels as vowel + r
        self.assertEqual(lt.normalize_ipa("/ˈbɜːd/"), "ˈbɜrd")                 # the British long vowel, rhotic
        self.assertEqual(lt.normalize_ipa("/ˈlɛtɚ/"), "ˈlɛtər")
        self.assertEqual(lt.normalize_ipa("/ʧɚʧ/"), "tʃərtʃ")                  # the CMU engine's ligatures
        self.assertEqual(lt.normalize_ipa("/ɡɹeɪt/"), "greɪt")                 # script g and ɹ
        self.assertEqual(lt.normalize_ipa("/ˈkɑːɹ/"), "ˈkɑr")                  # length marks go
        self.assertEqual(lt.normalize_ipa("/ɒn/"), "ɑn")
        self.assertEqual(lt.normalize_ipa("//ˈbæŋk//"), "ˈbæŋk")               # doubled slashes
        self.assertEqual(lt.normalize_ipa("ˈsɪs.təm  ˈfɔr"), "ˈsɪstəm ˈfɔr")   # stress marks stay
        self.assertEqual(lt.normalize_ipa(""), "")

    def test_it_is_idempotent_and_leaves_no_dual_notation(self):
        for raw in ("/ˈpɹɪv.ɪ.lɪd͡ʒ/", "bɝd ˈbɜːd", "ʧɚʧ ɡɹeɪt"):
            once = lt.normalize_ipa(raw)
            self.assertEqual(lt.normalize_ipa(once), once)
            for symbol in "ɹɝɚɡʧʤː͡.":
                self.assertNotIn(symbol, once)


class RelationAndFormTest(unittest.TestCase):
    def test_relation_tags(self):
        self.assertTrue(lt.relation_tags_ok([]))
        self.assertTrue(lt.relation_tags_ok("informal".split()))
        for tag in ("rare", "archaic", "obsolete", "dated", "vulgar"):
            self.assertFalse(lt.relation_tags_ok([tag]), tag)

    def test_form_labels(self):
        self.assertEqual(lt.form_label("present singular third-person"), "third-person singular")
        self.assertEqual(lt.form_label("participle present"), "present participle")
        self.assertEqual(lt.form_label("participle past"), "past participle")
        self.assertEqual(lt.form_label("past"), "past")
        self.assertEqual(lt.form_label("plural"), "plural")


if __name__ == "__main__":
    unittest.main()
