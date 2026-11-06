# Ocean

An English → Persian learner's dictionary.

## Contents

- [Install](#install)
- [Looking things up](#looking-things-up)
- [History](#history)
- [Refresh and Back](#refresh-and-back)
- [Reports](#reports)
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

### Look up from any app (Ctrl+C twice, or Alt+Q)

With Ocean running, select text in any app and look it up without switching windows:

- **Windows: press Ctrl+C twice.** Hold Ctrl and tap **C** two times within about half a second on the same selection (the first press copies as usual). Ocean comes to the front and shows the lookup. Nothing else changes: one Ctrl+C is an ordinary copy.
- **Windows: Alt+Q** looks up whatever is on the clipboard (copy first). Use it if an app does not refresh the clipboard on the second copy. **Ctrl+Alt+O** keeps working as the older alternative. If another program already owns one of these keys, Ocean logs "Could not register …" in `ocean.log` and the other shortcuts still work.
- **Linux (GNOME):** run `./install_shortcut_linux.sh` once to bind **Alt+Q** (and Ctrl+Alt+O) to the lookup; `--remove` undoes it. It reads the highlighted text, so no copy is needed; if middle-click paste is off in GNOME, copy (Ctrl+C) first. Ctrl+C twice is Windows-only: Wayland does not let a background app see other apps' clipboard changes.
- **Other Linux desktops:** bind any key to `<ocean folder>/run.sh --lookup`.

How "Ctrl+C twice" works: Ocean listens for clipboard changes, which Windows reports to every app, so it installs no keyboard hook (antivirus and game anti-cheat tools dislike those). A change counts only when Ctrl is held, Ocean did not write it, and it has text; two such changes with the same text, 0.08 to 0.6 seconds apart, trigger the lookup. Copies marked as secret by password managers are never read. Ocean runs as a single window: launching it again (or the shortcut) hands the request to the open window. On GNOME Wayland it may show an "Ocean is ready" notice instead of jumping to the front. You can also run `run.sh --lookup <text>`.

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

### Read aloud

The speaker button next to a word, phrase or example sentence reads it aloud, with the voice and speed chosen in Settings. Ocean prepares the audio of the buttons on a page in the background as soon as the page appears, so a click starts the sound at once; a clip it has not prepared yet (a very long text, or a click within a second of start-up) takes about a second to make first. A click on another button cuts the current clip off. The fallback voices (Windows SAPI, or spd-say / espeak on Linux) are used when the Kokoro voice is missing.

### Where a result comes from

Every source is on your computer (see [Dictionary data](#dictionary-data)), so a word's entry appears at once. Persian that is still being translated shows as "…" for a few seconds, then fills in.

- **Definitions** come from Wiktionary, by part of speech, with usage examples, collocations, derived terms and IPA. An inflected form ("ran", "banks") shows its base word's entry. Words Wiktionary lacks fall back to WordNet.
- **Synonyms and antonyms** come from WordNet 3.1, grouped under the sense they belong to, so the money "bank" and the river "bank" get separate groups.
- **Slang** comes from Urban Dictionary, shown as a "Slang" sense (only if you imported it).
- **Example sentences** with human Persian translations come from Tatoeba and OpenSubtitles. Ocean never makes up an example: a sense without a real one shows none.
- **The Persian translation** is tried in this order:
  1. Your own edits.
  2. Ocean's database. A word takes Wiktionary's translation of its main part of speech, one per sense for the first two senses; a phrase or sentence found whole in Tatoeba or OpenSubtitles takes its human translation.
  3. Google Translate, when you are online.
  4. Offline: the optional local language model for the looked-up phrase or sentence, if installed (see [Offline language model](#offline-language-model)), then the NLLB translator.

## History

**Menu › History** lists your lookups, newest first, and has a search box that filters as you type, in English or Persian. It matches the word and its Persian meaning, and Arabic and Persian letter variants such as ي/ی and ك/ک count as the same. The search combines with **Favorites Only**. Looking a word up again moves it to the top.

Tick the checkbox next to entries (or **Select all**) and press **Delete Selected** to remove them all at once; Ocean asks for confirmation first, and says how many of them are favourites. A deleted entry is gone from the history and the favourites together.

## Refresh and Back

A first search always shows the best result Ocean has locally. **Refresh** (top right of the first card) looks the entry up again with the online resources; the result on screen stays until the improved one is ready. If Refresh finds nothing better (or you are offline) the current result is kept. After a refresh that changed the entry, the top-left **Back** button returns to the output you saw before the refresh, and **Forward** to the refreshed one.

## Reports

Next to **Save** on every word, sentence, idiom or Persian result there is a small **!** button. One click reports the entry: Ocean stores it (the full result as shown, a picture of the page, the date and the Ocean and engine versions) in your database and fills the button in. A small box offers an optional one-line note (what looks wrong); clicking **!** again lets you edit the note or remove the report.

**Menu › Reports** lists them (term, date, note). Click one to open the entry as it was reported; **✕** deletes it after a confirmation. **Export for Claude** saves everything as one `.zip` that you attach to a chat when you next update Ocean:

- `README.md`: tells Claude how to use the bundle (find the root cause, fix it system-wide, add tests),
- `reports.json`: the environment (OS, versions, size and build statistics of the lexicon database) and every report with its note and full result,
- `snapshots/`: one PNG of each reported page.

## Settings

### Theme, fonts and motion

**Dark Mode** and **Light Mode** are a hand-tuned Material 3 palette (Google-style neutral surfaces, one blue accent) in `ui/theme.py`; the saved values `Dark Mode` / `Light Mode` did not change. A ready-made Qt style sheet was not used: PyQtDarkTheme (MIT) was last released in December 2022 and requires Python below 3.12, which the build uses; QDarkStyle (MIT) was last released in November 2023; qt-material (BSD-2) is maintained but bundles its own fonts and themes. All three restyle every widget and would fight Ocean's custom frames, capsule dropdowns and cards, so the palette is plain data in the repository with no extra dependency to bundle.

Fonts are in `fonts/` (all SIL OFL, licence files included): **Google Sans Flex** for English (static Regular/Medium/SemiBold/Bold instances cut from the variable font, because variable-font axes are unreliable in Qt on Windows), **Vazirmatn** for Persian, and **Charis SIL** for pronunciations only (it covers every IPA symbol; Google Sans Flex has none). To regenerate the Google Sans Flex files: download `GoogleSansFlex[ROND,opsz,slnt,wdth,wght].ttf` from `google/fonts` (`ofl/googlesansflex`), and with `fonttools` run `fontTools.varLib.instancer` for `wght` 400/500/600/700, `wdth=100`, `opsz=18`, `ROND=0`, `slnt=0`.

Windows, pages and the menu fade for about 0.2 seconds. Set the environment variable `OCEAN_REDUCE_MOTION=1`, or turn off "Show animations in Windows", to switch the fades off. Text that is wider than its box (a long dropdown value or option) ends in an ellipsis and scrolls sideways while the pointer is over it.

### Voice

Pronunciation uses the Kokoro neural voice. The setup script downloads the small int8 model (`kokoro-v1.0.int8.onnx`, ~88 MB). If you already have the full-size `kokoro-v1.0.onnx` (~310 MB) next to the app it keeps working, but the int8 file is preferred when both are present. Delete the full-size file to save space. Speech speed is about the same with either model (the int8 one loads faster), so the model choice does not change how soon a click is heard.

The int8 model has not had a side-by-side listening test against the full-size one. If you hear a difference, delete `kokoro-v1.0.int8.onnx` and download `kokoro-v1.0.onnx` from the same release: Ocean then uses the full-size model.

### Dictionary sources

There is nothing to switch: every source is always used. **Menu › Sources** lists them read-only, with their priority (the order Ocean uses them) and what each is used for. The list is the `SOURCES` table in `ui/sources_page.py`; edit it when a source is added or removed.

### Refreshing saved entries

Saved lookups are fetched again when they are older than the period set in **Menu › Settings › Refresh Saved Entries** (Never / 1 week / 1 month / 3 months / 6 months; default 3 months), or when they were produced by an older version of Ocean's lookup engine, so fixes and improvements reach old entries.

- "Never" turns refreshing off.
- Your star and Persian edits are kept.
- If the refetch fails (for example, you are offline) or finds nothing, the saved copy is shown and refreshed on a later lookup.

### Working offline

Ocean's dictionary works without internet, and it switches to its local data by itself when there is no connection: no setting is needed. Online, the only network use is Google Translate, for Persian that Ocean's database doesn't have. Without internet that falls back to the NLLB-200 model in `offline_mt_model\` (downloaded by the setup script), so expect lower-quality Persian for words and sentences that Ocean's database doesn't translate. The model and WordNet are loaded in the background right after startup, so the first lookup doesn't wait for them. The Kokoro voice (and the system voice fallback) already runs on your computer.

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

History, favourites, your reports, your translation fixes, settings and the log file (`ocean.log`) are stored per user, so updating or rebuilding the app never touches them:

| System | Folder |
|---|---|
| Windows | `%APPDATA%\Ocean` |
| Linux | `~/.local/share/Ocean` |

Data from older versions (kept next to the app) is copied there on first start.

## Credits and licences

Fonts: [Google Sans Flex](https://github.com/googlefonts/googlesans-flex) (Google), [Vazirmatn](https://github.com/rastikerdar/vazirmatn) (Saber Rastikerdar) and [Charis SIL](https://software.sil.org/charis/) (SIL International), all under the [SIL Open Font License 1.1](https://openfontlicense.org); the licence texts are in `fonts/`.

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
