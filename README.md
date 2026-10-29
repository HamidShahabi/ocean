# Ocean

An English → Persian learner's dictionary.

## Contents

- [Install](#install)
- [Looking things up](#looking-things-up)
- [Learning](#learning)
- [History and export](#history-and-export)
- [Settings](#settings)
- [Your data](#your-data)
- [Word list credits](#word-list-credits)
- [Development](#development)

## Install

### Windows

1. Install Python 3.12 from <https://www.python.org/downloads/windows/> (keep **py launcher** ticked in the installer).
2. Unzip this folder somewhere without spaces or permission issues, e.g. `C:\Ocean`.
3. Open PowerShell in that folder and run the one-time setup (needs internet, ~800 MB download):

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
| `./setup_linux.sh` | One-time setup |
| `./run.sh` | Start the app |
| `./build_linux.sh` | Optional portable build in `dist/Ocean/` |

## Looking things up

### Persian → English

Type Persian in the search box to find English words. Ocean shows the English translation and clickable English candidates, from your history, your own fixes and taught words, Ocean's idiom list, and Google. Click a candidate to open its full English entry. Offline, the bundled NLLB model translates (lower quality).

### Look up from any app (Ctrl+Alt+O)

Select or copy text in any app, press **Ctrl+Alt+O**, and Ocean shows the lookup. On GNOME Wayland it may show an "Ocean is ready" notice instead of jumping to the front. Ocean runs as a single window: launching it again (or the shortcut) hands the request to the open window.

- **Windows:** works automatically while Ocean is running (copy first, then press Ctrl+Alt+O).
- **Linux (GNOME):** run `./install_shortcut_linux.sh` once (`--remove` to undo). If middle-click paste is off in GNOME, copy (Ctrl+C) first.
- **Other Linux desktops:** bind any key to `<ocean folder>/run.sh --lookup`.

You can also run `run.sh --lookup <text>`.

### Word level and frequency badges

English results show two small badges under the headword:

- **Level (A1–C2, CEFR):** appears when the word is in the bundled word list. It works offline, and an inflected form such as "running" takes the level of its base form ("run").
- **Frequency:** needs internet. It comes from Datamuse, which reports how often the word occurs per million words.

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
- **Offline-only mode** works: lookups then use only your history and Ocean's bundled data.
- **Your text is kept** between sessions, together with your place in it, in `reader_text.json` in your user data folder (see [Your data](#your-data)). **Edit text** replaces it; reading an empty text deletes the file.

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

## Settings

### Voice

Pronunciation uses the Kokoro neural voice. The setup script downloads the small int8 model (`kokoro-v1.0.int8.onnx`, ~88 MB). If you already have the full-size `kokoro-v1.0.onnx` (~310 MB) next to the app it keeps working, but the int8 file is preferred when both are present. Delete the full-size file to save space.

### Dictionary sources

**Menu › Settings › Dictionary sources** lists every source Ocean consults, each with a checkbox. Untick one that is broken, slow or unwanted and click **Apply** (or **Save**): it is never contacted for any lookup, and the choice is remembered.

The bundled sources (idiom list, synonyms, WordNet…) are listed too, marked "(bundled)". Lookups work with any mix switched off. With all of them off, Ocean says so in the results; the Persian translation, pronunciation and your own taught entries still appear.

### Refreshing saved entries

Saved lookups are fetched again when they are older than the period set in **Menu › Settings › Refresh Saved Entries** (Never / 1 week / 1 month / 3 months / 6 months; default 3 months), or when they were produced by an older version of Ocean's lookup engine, so fixes and improvements reach old entries.

- "Never" turns refreshing off.
- Your star, review progress and Persian edits are kept.
- If the refetch fails (for example, you are offline) or finds nothing, the saved copy is shown and refreshed on a later lookup.

### Offline mode

Online dictionaries and Google Translate are used when available. Without internet, Persian translation falls back to the bundled NLLB-200 model in `offline_mt_model\` (downloaded by the setup script). The model is loaded in the background right after startup, so the first offline lookup doesn't wait for it.

**Offline-only mode** (**Menu › Settings › Offline-only mode**, then Apply or Save) makes Ocean send no network request at all, for privacy or for travelling.

- Lookups use only your history, your own entries and fixes, the bundled lexicon, WordNet and the offline NLLB translator. The Kokoro voice (and the system voice fallback) already runs on your computer.
- Suggestions as you type come from your history and entries only.
- Results show an "Offline-only mode" notice.
- Expect fewer examples and synonyms, and lower-quality Persian for words that are not in the bundled lexicon.
- The choice is remembered.

## Your data

History, favourites, your translation fixes, settings, the Reader text and the log file (`ocean.log`) are stored per user, so updating or rebuilding the app never touches them:

| System | Folder |
|---|---|
| Windows | `%APPDATA%\Ocean` |
| Linux | `~/.local/share/Ocean` |

Data from older versions (kept next to the app) is copied there on first start.

## Word list credits

The level badges use `cefr_levels.json` (shipped next to `offline_lexicon.json` in builds):

- **A1–B2:** The CEFR-J Wordlist Version 1.5. Compiled by Yukio Tono, Tokyo University of Foreign Studies. Retrieved from <http://www.cefr-j.org/download.html> on 1/20/2020 (distributed in [openlanguageprofiles/olp-en-cefrj](https://github.com/openlanguageprofiles/olp-en-cefrj)). Free for research and commercial use provided the dataset is cited; copyright Tono Laboratory, Tokyo University of Foreign Studies, no warranty.
- **C1–C2:** Octanove Vocabulary Profile C1/C2 version 1.0 by Octanove Labs (same repository), licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The C1/C2 words in `cefr_levels.json` are redistributed under that licence.

Words listed at several levels (one per part of speech) show their lowest. `tools/build_cefr_levels.py` regenerates the file from the two CSVs.

## Development

Run the tests:

```bash
.venv/bin/python -m unittest discover -s tests
```

### Dependencies

Dependencies are pinned. The setup scripts, `build_linux.sh` and the Windows build workflow install the exact versions in `requirements.lock` (Python 3.12, Linux and Windows, including PyInstaller). `requirements.txt` and `requirements-build.txt` are the human-edited inputs; never edit the lock by hand.

To add a dependency or pick up newer releases, install uv (`pip install uv`), edit `requirements.txt` if needed, then regenerate the lock and commit it:

```bash
uv pip compile --universal --python-version 3.12 requirements-build.txt -o requirements.lock
```

To upgrade just one package, add `--upgrade-package <name>` to that command. Then re-run the tests and the Windows build before merging.
