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

REVIEW (SPACED REPETITION)
  Words you save with the star button become flashcards. Open Review from the
  home screen or the menu: Space shows the answer, keys 1-4 grade it
  (Again / Hard / Good / Easy). Esc ends the session. Words come back for review
  at growing intervals (SM-2 scheduling); un-starring a word pauses it.

EXPORT TO ANKI
  Menu > History > Export to Anki writes the listed entries (all searches, or
  saved words only, following the filter) to a text file. In Anki (2.1.55+)
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

YOUR DATA
  History, favourites, your translation fixes, settings and the log file (ocean.log)
  are stored per user, so updating or rebuilding the app never touches them:
      Windows: %APPDATA%\Ocean        Linux: ~/.local/share/Ocean
  Data from older versions (kept next to the app) is copied there on first start.

OFFLINE MODE
  Online dictionaries and Google Translate are used when available. Without
  internet, Persian translation falls back to the bundled NLLB-200 model in
  offline_mt_model\ (downloaded by the setup script). The model is loaded in the
  background right after startup, so the first offline lookup doesn't wait for it.

WORD LEVEL AND FREQUENCY BADGES
  English results show two small badges under the headword. The level badge
  (A1-C2, CEFR) appears when the word is in the bundled word list; it works
  offline, and an inflected form such as "running" takes the level of its base
  form ("run"). The frequency badge (Very common / Common / Uncommon / Rare)
  needs internet: it comes from Datamuse, which reports how often the word
  occurs per million words (Very common: 100 or more, Common: 10-99,
  Uncommon: 1-9, Rare: under 1). Hover a badge for the details. Learn the A1-B1
  words first; the higher levels and rare words can wait.

  The word list is cefr_levels.json (next to offline_lexicon.json in builds):
    A1-B2: The CEFR-J Wordlist Version 1.5. Compiled by Yukio Tono, Tokyo
      University of Foreign Studies. Retrieved from
      http://www.cefr-j.org/download.html on 1/20/2020 (distributed in
      github.com/openlanguageprofiles/olp-en-cefrj). Free for research and
      commercial use provided the dataset is cited; copyright Tono Laboratory,
      Tokyo University of Foreign Studies, no warranty.
    C1-C2: Octanove Vocabulary Profile C1/C2 version 1.0 by Octanove Labs
      (same repository), licensed CC BY-SA 4.0
      (https://creativecommons.org/licenses/by-sa/4.0/). The C1/C2 words in
      cefr_levels.json are redistributed under that licence.
  Words listed at several levels (one per part of speech) show their lowest.
  tools/build_cefr_levels.py regenerates the file from the two CSVs.

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
