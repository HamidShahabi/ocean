"""English -> Persian: Tier 1 (the local database), the optional online layer, Tier 2 (the local language model
and the NLLB translator), and the language-model wrapper itself."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

# lexical_engine creates its per-user data folder on import: keep tests away from the real one.
_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_fixture import use_lexicon  # noqa: E402
import lexical_engine as le  # noqa: E402
import offline_slm  # noqa: E402

SPEC_PROMPT = ("You are an expert English-Persian translator. Translate the given English text into natural, idiomatic "
               "Persian. Avoid literal word-by-word translation. If it is an idiom or slang, provide the culturally "
               "equivalent Persian proverb or phrasing.")


class LocalTierTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)

    def test_a_word_gets_one_translation_per_sense(self):
        # The money sense and the river sense, never merged into one; ساحل beats the first-listed کرانه
        # because the corpora use it in a sentence with "bank".
        self.assertEqual(le.local_translation("bank"), ("بانک ، ساحل", le.FA_SOURCE_WIKTIONARY))

    def test_the_alternative_the_corpora_use_wins(self):
        self.assertEqual(le.local_translation("book"), ("کتاب", le.FA_SOURCE_WIKTIONARY))

    def test_a_translation_of_another_part_of_speech_is_not_used(self):
        self.assertEqual(le.local_translation("run"), ("", ""))  # the noun's "دو" is no answer for the verb
        self.assertEqual(le.local_translation("ran"), ("", ""))  # nor for its past tense

    def test_an_inflected_form_takes_its_lemmas_translation(self):
        self.assertEqual(le.local_translation("banks")[0], "بانک ، ساحل")

    def test_a_phrase_takes_the_human_translation_of_the_corpora(self):
        self.assertEqual(le.local_translation("Break a leg!"), ("موفق باشی!", "OpenSubtitles"))
        self.assertEqual(le.local_translation("I love you."), ("دوستت دارم.", "Tatoeba"))

    def test_a_lone_word_never_takes_a_subtitle_line(self):
        with mock.patch.object(le.LEXICON, "translation_pair", wraps=le.LEXICON.translation_pair) as pair:
            le.local_translation("zeitgeist")
        pair.assert_not_called()


class TierOrderTest(unittest.TestCase):
    def setUp(self):
        use_lexicon(self)
        self.google = self._patch("_google_translate", return_value="گوگل")
        self.slm = self._patch(le.SLM, "translate", return_value="مدل")
        self.nllb = self._patch("_ct2_translate", return_value="ان‌ال‌ال‌بی")
        self.nllb_batch = self._patch("_ct2_translate_many", side_effect=lambda texts: ["ان‌ال‌ال‌بی"] * len(texts))
        p = mock.patch.dict(le.USER_MEMORY, {"overrides": {}, "entries": {}})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(le.set_offline_only, False)

    def _patch(self, *args, **kwargs):
        target = (le, *args) if isinstance(args[0], str) else args
        p = mock.patch.object(*target, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def test_your_edit_comes_first(self):
        le.USER_MEMORY["overrides"]["bank"] = "بانک من"
        self.assertEqual(le.translate_with_source("Bank"), ("بانک من", le.FA_SOURCE_EDIT))
        self.google.assert_not_called()

    def test_local_before_google(self):
        self.assertEqual(le.translate_with_source("bank", is_short=True)[1], le.FA_SOURCE_WIKTIONARY)
        self.google.assert_not_called()

    def test_online_google_then_offline_tiers(self):
        self.assertEqual(le.translate_with_source("zeitgeist", is_short=True), ("گوگل", le.FA_SOURCE_GOOGLE))
        le.set_offline_only(True)
        self.google.reset_mock()
        self.assertEqual(le.translate_with_source("raining cats and dogs", use_slm=True), ("مدل", le.FA_SOURCE_SLM))
        self.assertEqual(le.translate_with_source("raining cats and dogs")[1], le.FA_SOURCE_NLLB)  # no model asked
        self.google.assert_not_called()

    def test_the_model_falls_back_to_nllb_when_it_has_no_answer(self):
        le.set_offline_only(True)
        self.slm.return_value = ""
        self.assertEqual(le.translate_with_source("raining cats and dogs", use_slm=True)[1], le.FA_SOURCE_NLLB)

    def test_idioms_and_sentences_use_the_model_words_do_not(self):
        le.set_offline_only(True)
        self.assertEqual(le.fetch_persian_word_meaning("spill the beans", is_idiom=True), ("مدل", le.FA_SOURCE_SLM))
        self.assertEqual(le.fetch_persian_word_meaning("zeitgeist")[1], le.FA_SOURCE_NLLB)
        with mock.patch.object(le, "extract_sentence_key_terms", return_value=[]), mock.patch.object(le, "sentence_to_ipa", return_value=""):
            d = le.analyze_with_dictionaries("The meeting was postponed until next week.")
        self.assertEqual((d["persian_equivalent"], d["persian_source"]), ("مدل", le.FA_SOURCE_SLM))

    def test_a_taught_entry_counts_as_your_edit(self):
        le.USER_MEMORY["entries"]["gizmo"] = {"fa": "ابزارک"}
        self.assertEqual(le.fetch_persian_word_meaning("gizmo"), ("ابزارک", le.FA_SOURCE_EDIT))

    def test_batch_translation_uses_the_local_tier_and_never_the_model(self):
        le.set_offline_only(True)
        out = le.translate_batch_fast(["I love you."], ["bank", "zeitgeist"])
        self.assertEqual(out["I love you."], "دوستت دارم.")
        self.assertEqual(out["bank"], "بانک ، ساحل")
        self.assertEqual(out["zeitgeist"], "ان‌ال‌ال‌بی")
        self.nllb_batch.assert_called_once_with(["zeitgeist"])  # leftovers go to NLLB in one batch
        self.slm.assert_not_called()

    def test_batch_translation_online_tries_google_for_each_leftover_first(self):
        self.google.side_effect = lambda text, is_short: "" if text == "qwerty" else "گوگل"
        with mock.patch.object(le, "smart_request", side_effect=le.OfflineModeError("no batch")):
            out = le.translate_batch_fast([], ["zeitgeist", "qwerty"])
        self.assertEqual(out, {"zeitgeist": "گوگل", "qwerty": "ان‌ال‌ال‌بی"})
        self.nllb_batch.assert_called_once_with(["qwerty"])


class OfflineModelTest(unittest.TestCase):
    def test_the_system_prompt_is_the_specified_one(self):
        self.assertEqual(offline_slm.SYSTEM_PROMPT, SPEC_PROMPT)

    def test_without_a_model_it_answers_empty_and_says_so_once(self):
        slm = offline_slm.OfflineSLM(tempfile.mkdtemp())
        with self.assertLogs("ocean.slm", "INFO") as logs:
            self.assertEqual(slm.translate("Break a leg!"), "")
            self.assertEqual(slm.translate("Again."), "")
        self.assertEqual(len(logs.output), 1)
        self.assertFalse(slm.available())

    def test_prompt_and_reply_with_a_stand_in_llama(self):
        folder = tempfile.mkdtemp()
        open(os.path.join(folder, "qwen2.5-1.5b-instruct-q4_k_m.gguf"), "wb").close()
        calls = []

        class FakeLlama:
            def __init__(self, model_path, **kwargs):
                calls.append(("load", os.path.basename(model_path)))

            def create_chat_completion(self, messages, **kwargs):
                calls.append(("chat", messages))
                return {"choices": [{"message": {"content": "Translation: «موفق باشی!»\n(This means good luck.)"}}]}

        with mock.patch.dict(sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=FakeLlama)}):
            slm = offline_slm.OfflineSLM(folder)
            self.assertEqual(slm.translate("  Break   a leg! "), "موفق باشی!")
            slm.translate("Again.")
        self.assertEqual(calls[0], ("load", "qwen2.5-1.5b-instruct-q4_k_m.gguf"))
        self.assertEqual(sum(c[0] == "load" for c in calls), 1)  # loaded once
        self.assertEqual(calls[1][1], [{"role": "system", "content": SPEC_PROMPT}, {"role": "user", "content": "Break a leg!"}])

    def test_a_failing_model_is_logged_not_raised(self):
        folder = tempfile.mkdtemp()
        open(os.path.join(folder, "m.gguf"), "wb").close()

        class Broken:
            def __init__(self, *a, **k):
                raise ValueError("bad file")

        with mock.patch.dict(sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=Broken)}), self.assertLogs("ocean.slm", "ERROR"):
            self.assertEqual(offline_slm.OfflineSLM(folder).translate("Hi."), "")

    def test_clean_reply(self):
        self.assertEqual(offline_slm.clean_reply('\n"دوستت دارم"\nextra'), "دوستت دارم")
        self.assertEqual(offline_slm.clean_reply("Persian: خوش آمدید"), "خوش آمدید")
        self.assertEqual(offline_slm.clean_reply("ساعت: سه"), "ساعت: سه")  # a colon inside the Persian stays
        self.assertEqual(offline_slm.clean_reply("  \n "), "")
        self.assertEqual(offline_slm.clean_reply("<think>\nThe user wants...\n</think>\n\nچیزی ساده"), "چیزی ساده")
        self.assertEqual(offline_slm.clean_reply("<think>still thinking when the tokens ran out"), "")
        self.assertEqual(offline_slm.clean_reply("ت关دها به انتظار شد"), "")  # stray Chinese: let NLLB answer
        self.assertEqual(offline_slm.clean_reply("Here are a few options:\n1. ..."), "")  # not Persian
        self.assertEqual(offline_slm.OfflineSLM(tempfile.mkdtemp()).translate("x" * 700), "")  # too long to send


if __name__ == "__main__":
    unittest.main()
