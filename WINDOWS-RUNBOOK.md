# Ocean on Windows: runbook

Goal: run Ocean from source on a real Windows machine, build `Ocean.exe`, check the Windows-only behaviour
(nobody has tried this branch on Windows yet), and send back the results.

Code: `main` of https://github.com/HamidShahabi/ocean (commit `733e311`, "Ocean upgrade 2").
If you cannot clone it, ask for a zip of the repo and unzip it anywhere (e.g. `C:\Ocean`).

## 0. What you need

- Windows 10 or 11, 64-bit, with internet (setup downloads about 750 MB of datasets plus the voice and
  translation models, then builds the dictionary database).
- About 8 GB of free disk space (virtual environment, models, database and the built app together; this is
  an estimate, the exact size is printed in step 4).
- **Python 3.12** from python.org. Tick "py launcher" in the installer. Check: `py -3.12 --version`.
- Git (optional, only for cloning) and 7-Zip (optional, for the zip in step 4).
- Allow 20 to 45 minutes for the first setup. Most of it is downloading and the database import.

## 1. Get the code

```powershell
git clone https://github.com/HamidShahabi/ocean.git
cd ocean
```

## 2. One-time setup

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

This creates `.venv`, installs the pinned dependencies (`requirements.lock`), downloads the Kokoro voice and
the offline NLLB translator, and builds `ocean_lexicon.db` (it picks up `ocean_slang.db` and
`data\idioms_fa.tsv.gz` from the repo by itself). It ends with "Setup complete".

- If a download stops, run the script again: finished files are skipped.
- If `ocean_lexicon.db` is half-built, delete it and run the script again.
- Do not use `-WithSlm`: the optional language model is not needed.

## 3. Run it (no build needed)

```powershell
.\run.bat
```

Ocean opens. This is the quickest way to test everything in section 6.

## 4. Optional: build `Ocean.exe` and a zip

```powershell
.\update_ocean.ps1 -NoLaunch
Test-Path .\dist\Ocean\Ocean.exe          # must print True
Copy-Item packaging\READ-ME-FIRST.txt dist\Ocean\
7z a -tzip -mx=5 Ocean-windows-x64.zip .\dist\Ocean     # or right-click dist\Ocean > Send to > Compressed folder
Get-Item Ocean-windows-x64.zip | Select-Object Name, @{n="MB"; e={[math]::Round($_.Length / 1MB)}}
```

(`Compress-Archive` can fail on archives over 2 GB in Windows PowerShell 5.1, hence 7-Zip.)
Windows may warn "Windows protected your PC" the first time, because the app is not signed: More info > Run anyway.
Antivirus tools sometimes flag PyInstaller apps; if so, note which one.

Smoke test of the built app (same as the automatic check that normally runs):

```powershell
Start-Process .\dist\Ocean\Ocean.exe -WorkingDirectory .\dist\Ocean
Start-Sleep 20
.\dist\Ocean\Ocean.exe --lookup serendipity      # must hand the word to the open window and exit
Get-Content "$env:APPDATA\Ocean\ocean.log" -Tail 40
```

The log must contain "External lookup" and must not contain "Could not register" or "lexicon database".

## 5. Unit tests (optional)

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

About 438 tests. `test_label_only_scrolls_when_the_text_overflows` (marquee) is timing based and can fail on a
busy machine: run it again alone before reporting it.

## 6. What to check by hand (this is the point of the exercise)

Tick each one and note what happened.

| # | Check | Expected |
|---|---|---|
| 1 | Start `run.bat` (and later `Ocean.exe`) | The window appears in a couple of seconds; Material dark theme; fonts look right; Persian text is Vazirmatn |
| 2 | Look up `bright`, `break the ice`, `rizz`, `She found the book by pure chance yesterday` | Word, idiom, slang and sentence results all render; examples are short film-dialogue lines; no crashes |
| 3 | Select a word in another app (Notepad, a browser), press **Ctrl+C twice** | Ocean comes to the front and looks it up |
| 4 | Copy a word, press **Alt+Q** | Same |
| 5 | **Ctrl+Alt+O** | Still works as the fallback |
| 6 | Look up from another app while Ocean is minimised or behind other windows | The window gets keyboard focus (the `AttachThreadInput` fix); you can type at once |
| 7 | With Word open: does Alt+Q still do "Tell me"? | Known: Ocean takes Alt+Q while it runs. Just note it |
| 8 | Click a speaker button | Audio plays at once; a second click cuts the first off |
| 9 | Press **Refresh** on a result, then **Back** | The refreshed result replaces it; Back returns to the previous one. Try it with Wi-Fi off too: the old result stays |
| 10 | History: tick several, Select all, Delete Selected | Asks for confirmation, then deletes |
| 11 | "!" button next to Save, then Menu > Reports | A report is stored; the export zip opens |
| 12 | Menu > Sources | Nine rows, in this order: Idioms, Wiktionary, Urban Dictionary, OpenSubtitles, Tatoeba, translation cache, Google, local language model, NLLB |
| 13 | Look up `dial it down` and `under the weather` with internet off | Persian appears at once (pre-translated idioms), no waiting |
| 14 | Start Ocean twice | The second start hands over to the first and exits (single instance) |

## 7. Send back

- Which checks passed or failed, with a screenshot for each failure.
- `%APPDATA%\Ocean\ocean.log` (the whole file).
- Anything that failed during setup or the build: copy the last 30 lines of the PowerShell window.
- The zip size from step 4 and how long setup took.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Python 3.12 not found" | Install Python 3.12 from python.org with the py launcher, open a new PowerShell, run setup again |
| Script is blocked | Use the exact command in step 2 (`-ExecutionPolicy Bypass`) |
| `pip install` fails | Re-run; if it keeps failing, send the error text |
| Ocean says the dictionary database cannot be opened | `ocean_lexicon.db` must sit next to `app.py` (or next to `Ocean.exe`). Delete it and re-run setup |
| No sound | Check the Kokoro files exist (`kokoro-v1.0.int8.onnx`, `voices-v1.0.bin`); Ocean falls back to the Windows voice |
| Shortcuts do nothing | Check `%APPDATA%\Ocean\ocean.log` for "Could not register"; another program may own the key |
