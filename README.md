# Ocean

An English → Persian learner's dictionary.

## Contents

- [Install](#install)
- [Looking things up](#looking-things-up)
- [Learning](#learning)
- [History and export](#history-and-export)
- [Pronounce](#pronounce)
- [Settings](#settings)
- [Dictionary data](#dictionary-data)
- [Your data](#your-data)
- [Credits and licences](#credits-and-licences)
- [Development](#development)

## Install

### Windows

1. Install Python 3.12 from <https://www.python.org/downloads/windows/> (keep **py launcher** ticked in the installer).
2. Unzip this folder somewhere without spaces or permission issues, e.g. `C:\Ocean`.
3. Open PowerShell in that folder and run the one-time setup (needs internet, ~1.6 GB download, then a few minutes building Ocean's dictionary database; see [Dictionary data](#dictionary-data) for the options):

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
   ```

4. Start the app: double-click `run.bat`.
5. Optional: build a portable `Ocean.exe` (output in `dist\Ocean\`):

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\update_ocean.ps1
   ```

### Linux

| Command | What it does |
|---|---|
| `./setup_linux.sh` | One-time setup (~1.6 GB download; options in [Dictionary data](#dictionary-data)) |
| `./run.sh` | Start the app |
| `./build_linux.sh` | Optional portable build in `dist/Ocean/` |

## Looking things up

### Persian → English

Type Persian in the search box to find English words. Ocean shows the English translation and clickable English candidates, from your history, your own fixes, Wiktionary's English–Persian translations and Google. Click a candidate to open its full English entry. Offline, the NLLB model translates sentences (lower quality).

### Look up from any app (Ctrl+Alt+O)

Select or copy text in any app, press **Ctrl+Alt+O**, and Ocean shows the lookup. On GNOME Wayland it may show an "Ocean is ready" notice instead of jumping to the front. Ocean runs as a single window: launching it again (or the shortcut) hands the request to the open window.

- **Windows:** works automatically while Ocean is running (copy first, then press Ctrl+Alt+O).
- **Linux (GNOME):** run `./install_shortcut_linux.sh` once (`--remove` to undo). If middle-click paste is off in GNOME, copy (Ctrl+C) first.
- **Other Linux desktops:** bind any key to `<ocean folder>/run.sh --lookup`.

You can also run `run.sh --lookup <text>`.

### Word level and frequency badges

English results show two small badges under the headword:

- **Level (A1–C2, CEFR):** appears when the word is in the bundled word list. It works offline, and an inflected form such as "running" takes the level of its base form ("run").
- **Frequency:** works offline. It is how often the word occurs per million words of English film and TV subtitles (OpenSubtitles), which tracks everyday spoken English well.

| Band | Occurrences per million words |
|---|---|
| Very common | 100 or more |
| Common | 10–99 |
| Uncommon | 1–9 |
| Rare | under 1 |

Hover a badge for the details. Learn the A1–B1 words first; the higher levels and rare words can wait. See [Word list credits](#word-list-credits) for where the levels come from.

### Read examples aloud

The speaker button next to an example sentence reads it aloud and highlights each word as it is spoken. With the Kokoro neural voice the highlight follows an estimate of each word's timing (the voice gives no word timestamps), so it can be slightly off on long or unusual words. The fallback voices (Windows SAPI, or spd-say / espeak on Linux) play the sentence without highlighting. Playing another clip clears the highlight.

### Reader

Open **Reader** from the home screen or the menu, paste or type an English text and press **Read**. The text is shown as plain, wrapped text that you can scroll; it works for long texts too (a longer paste is cut after 300,000 characters, about 50,000 words).

- **Look up a word:** click it. An inflected form is looked up as its base word ("ran" → "run", "studies" → "study"; a word that is in the level list is looked up as written). "don't", "it's" and "John's" are looked up as "do", "it" and "John". To look up a phrase, select several words with the mouse (up to 30 words; a selection that cuts a word in half covers the whole word).
- **The result opens beside the text,** in a panel on the right. The text stays exactly where it is, so you never lose your place. Related words clicked inside the panel are also looked up in the panel, and its **‹ Back** button returns to the previous result.
- **Saved words are marked** in blue, including any form of them ("run" marks ran and running) and saved phrases of two to four words ("break the ice" marks "broke the ice"). **Words above your level are marked** in amber with a dashed underline. Pick the level in **Mark level … and above** at the top; **Off** marks only your saved words. The choice is remembered. The marks are readable in both themes and need no internet.
- **Words that are not in the bundled level list are not marked.** These are mostly names, rare words and typos, and marking them would light up every name; click one to look it up. Words in the list below your level are not marked either.
- **Save a word in one step:** press the **☆ Save** star in the result panel, or right-click a word or selected phrase and choose **Save word**. A word that was never looked up is looked up first and saved when the result arrives. The right-click menu also has **Look up**, and **Remove … from saved words** for a saved one. Words you look up here appear in History like any other lookup.
- **Offline-only mode** works: lookups then use only your history and Ocean's local data.
- **Your text is kept** between sessions, together with your place in it, in `reader_text.json` in your user data folder (see [Your data](#your-data)). **Edit text** replaces it; reading an empty text deletes the file.

### Where a result comes from

Every source is on your computer (see [Dictionary data](#dictionary-data)):

- **Definitions** come from Wiktionary, by part of speech, with usage examples, collocations, derived terms and IPA. An inflected form ("ran", "banks") shows its base word's entry. Words Wiktionary lacks fall back to WordNet.
- **Synonyms and antonyms** come from WordNet 3.1, grouped under the sense they belong to, so the money "bank" and the river "bank" get separate groups.
- **Slang** comes from Urban Dictionary, shown as a "Slang" sense (only if you imported it).
- **Example sentences** with human Persian translations come from Tatoeba and OpenSubtitles. Ocean never makes up an example: a sense without a real one shows none.
- **The Persian translation** is tried in this order:
  1. Your own edits and taught words.
  2. Ocean's database. A word takes Wiktionary's translation of its main part of speech, one per sense for the first two senses; a phrase or sentence found whole in Tatoeba or OpenSubtitles takes its human translation.
  3. Google Translate, when you are online and Offline-only mode is off.
  4. Offline: the optional local language model for the looked-up phrase or sentence, if installed (see [Offline language model](#offline-language-model)), then the NLLB translator.

## Learning

### Review (spaced repetition)

Words you save with the star button become flashcards. Open **Review** from the home screen or the menu.

| Key | Action |
|---|---|
| Space | Show the answer |
| 1–4 | Grade it: Again / Hard / Good / Easy |
| Esc | End the session |

Words come back for review at growing intervals (SM-2 scheduling). Un-starring a word pauses it.

### Quiz

Open **Quiz** from the home screen or the menu to test yourself on your saved words. A picker next to the title switches between the modes. No quiz mode changes your Review schedule: only Review, where you recall a word and grade yourself, schedules cards.

#### Multiple choice

Ten four-choice questions, English → Persian and Persian → English, with wrong options drawn from your other saved words. Keys 1–4 answer, Space moves on, and the session ends with a score (Esc ends early). You need at least four saved words with different meanings.

#### Fill in the blank

Shows an example sentence of a saved word with the word blanked out, and you type it. Every form of the word is blanked: "run" also blanks runs, ran and running; "break the ice" also blanks "broke the ice".

- Press Enter to check, then Enter or Space for the next one. Keys 1–4 and Space type normally while the answer box is focused.
- Answers ignore upper/lower case. Either the form used in the sentence ("ran") or the word as you saved it ("run") counts.
- The full sentence is shown after each answer.
- Saved words without an example sentence that contains the word are skipped.

#### Listen and type

Ocean says one of your saved words out loud, with the voice chosen in Settings, and you type it.

- The word plays by itself when each question appears. Press **Ctrl+R** or the **Replay** button to hear it again (Ctrl+R works while you are typing).
- The screen never shows the word or its meaning before you answer. Afterwards it shows the word, its IPA (if saved) and the Persian meaning.
- Answers ignore upper/lower case and extra spaces.
- This is a spelling exercise, so a typo is not accepted. For words of five or more letters, an answer one typo away (a missing, extra or wrong letter, or two neighbouring letters swapped) is marked "Almost - correct spelling: …" but still counts as a miss.
- Only single words and short phrases (up to four words) are used.
- The mode needs a voice: the Kokoro voice, or a system voice (Windows speech, spd-say or espeak-ng on Linux). Without one it explains why instead of starting. While Kokoro is still loading at start-up, it waits and begins by itself.
- Speech stops when you leave the quiz.

## History and export

### Search and sorting

**Menu › History** has a search box that filters as you type, in English or Persian. It matches the word and its Persian meaning, and Arabic and Persian letter variants such as ي/ی and ك/ک count as the same.

Sort by **Most Recent** or **Most Looked Up**. The search and the sort both combine with **Saved Favorites Only**.

Each lookup you make (typing a word, picking a suggestion, clicking a history row or candidate, the Ctrl+Alt+O hotkey) adds one to that word's counter, shown as ×2, ×3… beside it. Existing history starts at 1. Refresh, back/forward and re-showing a result do not count.

### Export to Anki

**Menu › History › Export to Anki** writes the listed entries (all searches, or saved words only, narrowed by any search text) to a text file. In Anki 2.1.55 or later choose **File › Import** and pick it: notes land in the "Ocean" deck with Persian, IPA, definitions and examples. Importing again updates them.

## Pronounce

**Menu › Pronounce** reads any English word, phrase or text aloud with the voice chosen in Settings and shows its US IPA underneath. Nothing is translated or explained there. Type or paste the text and press **Pronounce** (Ctrl+Enter); **Stop** or leaving the page silences it. With the Kokoro voice each word is highlighted as it is spoken. A single word shows its Wiktionary transcription; a longer text is transcribed word by word. Up to 5,000 characters (about 800 words) are read in one go.

## Settings

### Voice

Pronunciation uses the Kokoro neural voice. The setup script downloads the small int8 model (`kokoro-v1.0.int8.onnx`, ~88 MB). If you already have the full-size `kokoro-v1.0.onnx` (~310 MB) next to the app it keeps working, but the int8 file is preferred when both are present. Delete the full-size file to save space.

The int8 model has not had a side-by-side listening test against the full-size one. If you hear a difference, delete `kokoro-v1.0.int8.onnx` and download `kokoro-v1.0.onnx` from the same release: Ocean then uses the full-size model.

### Dictionary sources

**Menu › Settings › Dictionary sources** lists Ocean's four sources (Wiktionary, WordNet 3.1, Urban Dictionary, and the Tatoeba/OpenSubtitles example sentences), each with a checkbox. Untick one you don't want and click **Apply** (or **Save**): it is not read for any lookup, and the choice is remembered. Sources you had switched off in an older version, such as Cambridge or Longman, no longer exist and are ignored.

Lookups work with any mix switched off. With all of them off, Ocean says so in the results; the Persian translation, pronunciation and your own taught entries still appear.

### Refreshing saved entries

Saved lookups are fetched again when they are older than the period set in **Menu › Settings › Refresh Saved Entries** (Never / 1 week / 1 month / 3 months / 6 months; default 3 months), or when they were produced by an older version of Ocean's lookup engine, so fixes and improvements reach old entries.

- "Never" turns refreshing off.
- Your star, review progress and Persian edits are kept.
- If the refetch fails (for example, you are offline) or finds nothing, the saved copy is shown and refreshed on a later lookup.

### Offline mode

Ocean's dictionary works without internet. Online, the only network use is Google Translate, for Persian that Ocean's database doesn't have. Without internet that falls back to the NLLB-200 model in `offline_mt_model\` (downloaded by the setup script). The model and WordNet are loaded in the background right after startup, so the first lookup doesn't wait for them.

**Offline-only mode** (**Menu › Settings › Offline-only mode**, then Apply or Save) makes Ocean send no network request at all, for privacy or for travelling.

- Lookups use only your history, your own entries and fixes, Ocean's database, WordNet, the offline language model (if installed) and the NLLB translator. The Kokoro voice (and the system voice fallback) already runs on your computer.
- Suggestions as you type come from your history, your entries and Ocean's database.
- Results show an "Offline-only mode" notice saying where the result came from: your history (a saved lookup), or Ocean's internal dictionary, with "Persian by the offline translator" when the machine translator produced the Persian.
- Expect lower-quality Persian for words and sentences that Ocean's database doesn't translate.
- The choice is remembered.

### Offline language model

The setup scripts can also install a small local language model (Qwen2.5-1.5B-Instruct, GGUF, run by llama-cpp-python) for idioms and sentences offline: `./setup_linux.sh --with-slm` or `setup_windows.ps1 -WithSlm`. It is off by default because, measured on this app's job, it does worse than the NLLB translator it would replace:

| English | NLLB (default) | Qwen2.5-1.5B |
|---|---|---|
| The meeting was postponed because the manager was sick. | جلسه به خاطر اینکه مدیر بیمار بود تأخیر شد. (0.7 s) | garbled, with Chinese characters (2.9 s) |
| Break a leg! | پاهاشو شکسته! (literal) | برای نجات بگذار! (wrong) |
| It is raining cats and dogs. | literal | wrong |

It also peaked at 1.8 GB of RAM. Qwen3-1.7B and Gemma-3-1B, the other models of that size, were no better: they explain or reason at length (40–50 s) instead of translating. Ocean rejects a model reply that isn't Persian, so a bad answer falls back to NLLB instead of being shown. If a stronger small model appears, put its `.gguf` file in `offline_slm_model/` (and install llama-cpp-python); Ocean uses the first one there. On Linux, llama-cpp-python is built from source (needs gcc/g++), because its only prebuilt Linux wheel is for musl. The Windows wheel comes from the project's CPU index and has not been tested.

## Dictionary data

Ocean's dictionary is one file, `ocean_lexicon.db` (~250 MB), next to the app. The setup scripts build it from these downloads (~750 MB, deleted afterwards; the import takes about five minutes):

| Dataset | What Ocean uses | Size in the database |
|---|---|---|
| [Kaikki.org](https://kaikki.org/dictionary/English/) English extract of Wiktionary | Definitions by part of speech, examples, collocations, derived terms, IPA, inflected forms, English–Persian translations | ~780,000 entries |
| [Tatoeba](https://tatoeba.org/) English–Persian sentence pairs | Example sentences and translations | ~8,500 pairs |
| [OPUS OpenSubtitles](https://opus.nlpl.eu/OpenSubtitles/en&fa/v2018/OpenSubtitles) v2018 English–Persian | Translations of everyday phrases and lines (kept when at least two subtitle lines agree on the same rendering; up to 12 words), example sentences, word frequencies | ~64,000 pairs, ~180,000 word frequencies |
| [WordNet 3.1](https://wordnet.princeton.edu/) (NLTK data, in `nltk_data/`) | Synonyms and antonyms by sense | — |
| Urban Dictionary (optional) | Slang senses | — |

Urban Dictionary publishes no official dump, so it is not downloaded. To add slang, get a dump with word, definition and vote columns (for example the "Urban Dictionary Words and Definitions" CSV on Kaggle) and pass it to the setup script: `./setup_linux.sh --urban FILE` or `setup_windows.ps1 -Urban FILE`. Only entries with at least twice as many upvotes as downvotes (and at least one upvote) are imported, at most three per word.

To rebuild the database (for example with a newer Kaikki extract, or to add the Urban Dictionary dump later), delete `ocean_lexicon.db` and run the setup script again, or run `tools/build_lexicon_db.py` yourself (`--help` lists the options). The file is replaced only when a build completes. Without the database Ocean still works, with WordNet definitions and machine translation only.

## Your data

History, favourites, your translation fixes, settings, the Reader text and the log file (`ocean.log`) are stored per user, so updating or rebuilding the app never touches them:

| System | Folder |
|---|---|
| Windows | `%APPDATA%\Ocean` |
| Linux | `~/.local/share/Ocean` |

Data from older versions (kept next to the app) is copied there on first start.

## Credits and licences

The dictionary database (see [Dictionary data](#dictionary-data)) redistributes:

- **Wiktionary** content via [Kaikki.org](https://kaikki.org/) (Tatu Ylonen, "Wiktextract: Wiktionary as Machine-Readable Structured Data", LREC 2022), licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) and GFDL.
- **Tatoeba** sentences, licensed [CC BY 2.0 FR](https://creativecommons.org/licenses/by/2.0/fr/); © the Tatoeba contributors.
- **OpenSubtitles** via OPUS: P. Lison and J. Tiedemann, "OpenSubtitles2016: Extracting Large Parallel Corpora from Movie and TV Subtitles", LREC 2016 (<http://www.opensubtitles.org/>), and J. Tiedemann, "Parallel Data, Tools and Interfaces in OPUS", LREC 2012.
- **WordNet 3.1**, Princeton University, under the [WordNet License](https://wordnet.princeton.edu/license-and-commercial-use).
- **Urban Dictionary** definitions, only when you import a dump yourself; check the terms of the dump you use.

The level badges use `cefr_levels.json` (shipped next to `ocean_lexicon.db` in builds):

- **A1–B2:** The CEFR-J Wordlist Version 1.5. Compiled by Yukio Tono, Tokyo University of Foreign Studies. Retrieved from <http://www.cefr-j.org/download.html> on 1/20/2020 (distributed in [openlanguageprofiles/olp-en-cefrj](https://github.com/openlanguageprofiles/olp-en-cefrj)). Free for research and commercial use provided the dataset is cited; copyright Tono Laboratory, Tokyo University of Foreign Studies, no warranty.
- **C1–C2:** Octanove Vocabulary Profile C1/C2 version 1.0 by Octanove Labs (same repository), licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The C1/C2 words in `cefr_levels.json` are redistributed under that licence.

Words listed at several levels (one per part of speech) show their lowest. `tools/build_cefr_levels.py` regenerates the file from the two CSVs.

## Development

Run the tests (the lookup tests build a small sample database of their own instead of using `ocean_lexicon.db`):

```bash
.venv/bin/python -m unittest discover -s tests
```

`app.py` only starts the app. The lookup engine is `lexical_engine.py` (morphology, IPA, the translation tiers); `dictionary_sources/` has one module per dataset; `lexicon_db.py` reads `ocean_lexicon.db`, which `tools/build_lexicon_db.py` builds; `offline_slm.py` wraps the optional language model; `ui/` holds one module per page. Errors from every part, including uncaught ones, go to `ocean.log` in your user data folder.

### Dependencies

Dependencies are pinned. The setup scripts, `build_linux.sh` and the Windows build workflow install the exact versions in `requirements.lock` (Python 3.12, Linux and Windows, including PyInstaller). `requirements.txt` and `requirements-build.txt` are the human-edited inputs; never edit the lock by hand.

To add a dependency or pick up newer releases, install uv (`pip install uv`), edit `requirements.txt` if needed, then regenerate the lock and commit it:

```bash
uv pip compile --universal --python-version 3.12 requirements-build.txt -o requirements.lock
```

To upgrade just one package, add `--upgrade-package <name>` to that command. Then re-run the tests and the Windows build before merging.
