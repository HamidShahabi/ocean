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
  offline_mt_model\ (downloaded by the setup script).

DEVELOPMENT
  Run the tests:  .venv/bin/python -m unittest discover -s tests
