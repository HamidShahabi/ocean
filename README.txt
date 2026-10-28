OCEAN - English -> Persian learner's dictionary
=============================================

WINDOWS
  1. Install Python 3.12 from https://www.python.org/downloads/windows/
     (keep "py launcher" ticked in the installer).
  2. Unzip this folder somewhere without spaces/permission issues, e.g. C:\Ocean
  3. Open PowerShell in that folder and run (one time, needs internet, ~1 GB download):
         powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
  4. Start the app:  double-click run.bat
  5. Optional - build a portable Ocean.exe (output in dist\Ocean\):
         powershell -ExecutionPolicy Bypass -File .\update_ocean.ps1

LINUX
  ./setup_linux.sh      one-time setup
  ./run.sh              start the app
  ./build_linux.sh      optional portable build in dist/Ocean/

PERSIAN -> ENGLISH
  Type Persian in the search box to find English words: Ocean shows the
  English translation and clickable English candidates (from your history,
  your own fixes and taught words, Ocean's idiom list, and Google). Click a
  candidate to open its full English entry. Offline, the bundled NLLB model
  translates (lower quality).

READ EXAMPLES ALOUD
  The speaker button next to an example sentence reads it aloud and highlights
  each word as it is spoken. With the Kokoro neural voice the highlight follows
  an estimate of each word's timing (the voice gives no word timestamps), so it
  can be slightly off on long or unusual words. The fallback voices (Windows
  SAPI, or spd-say / espeak on Linux) play the sentence without highlighting.
  Playing another clip clears the highlight.

REVIEW (SPACED REPETITION)
  Words you save with the star button become flashcards. Open Review from the
  home screen or the menu: Space shows the answer, keys 1-4 grade it
  (Again / Hard / Good / Easy). Esc ends the session. Words come back for review
  at growing intervals (SM-2 scheduling); un-starring a word pauses it.

QUIZ
  Open Quiz from the home screen or the menu to test yourself on your saved
  words: ten four-choice questions, English -> Persian and Persian -> English,
  with wrong options drawn from your other saved words. Keys 1-4 answer, Space
  moves on, and the session ends with a score (Esc ends early). You need at
  least four saved words with different meanings. Quiz answers do not change
  your Review schedule: picking from four options is a much weaker signal than
  recalling a word and grading yourself, so only Review schedules cards.

HISTORY SEARCH AND SORTING
  Menu > History has a search box that filters as you type, in English or
  Persian (it matches the word and its Persian meaning; Arabic and Persian
  letter variants such as ي/ی and ك/ک count as the same). Sort by Most Recent
  or Most Looked Up; the search and the sort both combine with "Saved
  Favorites Only". Each lookup you make (typing a word, picking a suggestion,
  clicking a history row or candidate, the Ctrl+Alt+O hotkey) adds one to that
  word's counter, shown as x2, x3... beside it; existing history starts at 1.
  Refresh, Deep search, back/forward and re-showing a result do not count.

EXPORT TO ANKI
  Menu > History > Export to Anki writes the listed entries (all searches, or
  saved words only, narrowed by any search text) to a text file. In Anki (2.1.55+)
  choose File > Import and pick it: notes land in the "Ocean" deck with
  Persian, IPA, definitions and examples. Importing again updates them.

LOOK UP FROM ANY APP (Ctrl+Alt+O)
  Select or copy text in any app, press Ctrl+Alt+O, and Ocean shows the lookup
  (on GNOME Wayland it may show an "Ocean is ready" notice instead of jumping
  to the front). Ocean runs as a single window: launching it again (or the
  shortcut) hands the request to the open window.
    Windows: works automatically while Ocean is running (copy first, then
             press Ctrl+Alt+O).
    Linux (GNOME): run ./install_shortcut_linux.sh once (--remove to undo).
             If middle-click paste is off in GNOME, copy (Ctrl+C) first.
    Other Linux desktops: bind any key to  <ocean folder>/run.sh --lookup
  You can also run:  run.sh --lookup <text>

REFRESHING SAVED ENTRIES
  Saved lookups are fetched again when they are older than the period set in
  Menu > Settings > Refresh Saved Entries (Never / 1 week / 1 month / 3 months /
  6 months; default 3 months), or when they were produced by an older version of
  Ocean's lookup engine, so fixes and improvements reach old entries. "Never"
  turns refreshing off. Your star, review progress and Persian edits are kept.
  If the refetch fails (e.g. you are offline) or finds nothing, the saved copy is
  shown and refreshed on a later lookup.

YOUR DATA
  History, favourites, your translation fixes, settings and the log file (ocean.log)
  are stored per user, so updating or rebuilding the app never touches them:
      Windows: %APPDATA%\Ocean        Linux: ~/.local/share/Ocean
  Data from older versions (kept next to the app) is copied there on first start.

DICTIONARY SOURCES
  Menu > Settings > Dictionary sources lists every source Ocean consults, each
  with a checkbox. Untick one that is broken, slow or unwanted and click Apply
  (or Save): it is never contacted for any lookup, and the choice is remembered.
  The bundled sources (idiom list, synonyms, WordNet...) are listed too, marked
  "(bundled)". Lookups work with any mix switched off; with all of them off Ocean
  says so in the results (the Persian translation, pronunciation and your own
  taught entries still appear).

OFFLINE MODE
  Online dictionaries and Google Translate are used when available. Without
  internet, Persian translation falls back to the bundled NLLB-200 model in
  offline_mt_model\ (downloaded by the setup script). The model is loaded in the
  background right after startup, so the first offline lookup doesn't wait for it.

  Offline-only mode (Menu > Settings > "Offline-only mode", then Apply or Save)
  makes Ocean send no network request at all, for privacy or for travelling.
  Lookups use only your history, your own entries and fixes, the bundled
  lexicon, WordNet and the offline NLLB translator; the Kokoro voice (and the
  system voice fallback) already runs on your computer. Suggestions as you type
  come from your history and entries only. Results show an "Offline-only mode"
  notice. Expect fewer examples and synonyms, and lower-quality Persian for
  words that are not in the bundled lexicon. The choice is remembered.

DEVELOPMENT
  Run the tests:  .venv/bin/python -m unittest discover -s tests

  Dependencies are pinned: the setup scripts, build_linux.sh and the Windows
  build workflow install the exact versions in requirements.lock (Python 3.12,
  Linux and Windows, including PyInstaller). requirements.txt and
  requirements-build.txt are the human-edited inputs; never edit the lock by hand.
  To add a dependency or pick up newer releases, install uv (pip install uv),
  edit requirements.txt if needed, then regenerate the lock and commit it:
      uv pip compile --universal --python-version 3.12 requirements-build.txt -o requirements.lock
  Upgrade just one package with:  ... --upgrade-package <name>
  Then re-run the tests and the Windows build before merging.
