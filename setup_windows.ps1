# One-time Windows setup: venv, Python deps, WordNet, Kokoro voice, offline translator, Ocean's local
# dictionary database, fonts.
# Run from this folder:  powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
#   -Urban FILE   also import an Urban Dictionary dump (CSV or JSON; see tools\build_lexicon_db.py)
#   -WithSlm      also install the optional local language model (llama-cpp-python + Qwen2.5-1.5B, ~1.1 GB;
#                 see "Offline language model" in the README before you do)
param([string]$Urban = "", [switch]$WithSlm)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar on
Set-Location $PSScriptRoot

function Get-File($url, $dest) {
    if (Test-Path $dest) { return }
    Write-Host "Downloading $dest ..." -ForegroundColor Cyan
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile "$dest.part"
    Move-Item "$dest.part" $dest -Force
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Creating Python 3.12 virtual environment..." -ForegroundColor Cyan
    $missing = "Python 3.12 not found. Install it from python.org (keep 'py launcher' ticked) and re-run."
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw $missing }
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw $missing }
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# lexical_engine.py looks in .\nltk_data first. NLTK_DATA must name the target dir
# or newer NLTK refuses to download there ("Unauthorized path").
$env:NLTK_DATA = Join-Path $PSScriptRoot "nltk_data"
# WordNet 3.1 gives synonyms and antonyms; it needs the 3.0 data too, which the lemmatizer uses.
& .\.venv\Scripts\python.exe -m nltk.downloader -d $env:NLTK_DATA wordnet wordnet31 omw-1.4

$kokoroUrl = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# int8 model (~88 MB); an existing full-size kokoro-v1.0.onnx is used as is and not replaced.
if (-not (Test-Path "kokoro-v1.0.onnx")) {
    Get-File "$kokoroUrl/kokoro-v1.0.int8.onnx" "kokoro-v1.0.int8.onnx"
}
Get-File "$kokoroUrl/voices-v1.0.bin" "voices-v1.0.bin"

# Offline EN->FA backup translator: NLLB-200 distilled 600M, CTranslate2 int8.
# Skipped when a model is already present (e.g. your own), so files from two models never mix.
if (-not (Test-Path "offline_mt_model\model\model.bin")) {
    $nllbUrl = "https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8/resolve/main"
    New-Item -ItemType Directory -Force -Path "offline_mt_model\model" | Out-Null
    Get-File "$nllbUrl/sentencepiece.bpe.model" "offline_mt_model\sentencepiece.model"
    # model.bin last: its presence marks a complete download.
    foreach ($f in @("config.json", "shared_vocabulary.txt", "model.bin")) {
        Get-File "$nllbUrl/$f" "offline_mt_model\model\$f"
    }
}

# Ocean's dictionary database (~250 MB) from Kaikki.org (Wiktionary), Tatoeba and OpenSubtitles: a ~750 MB
# download and a few minutes of importing. Skipped when ocean_lexicon.db exists: delete it to rebuild.
if (-not (Test-Path "ocean_lexicon.db")) {
    $dl = "dataset_downloads"
    New-Item -ItemType Directory -Force -Path "$dl\tatoeba" | Out-Null
    Get-File "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz" "$dl\kaikki.jsonl.gz"
    foreach ($f in @("eng/eng_sentences.tsv.bz2", "pes/pes_sentences.tsv.bz2", "eng/eng-pes_links.tsv.bz2")) {
        Get-File "https://downloads.tatoeba.org/exports/per_language/$f" "$dl\tatoeba\$(Split-Path $f -Leaf)"
    }
    Get-File "https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2018/moses/en-fa.txt.zip" "$dl\opensubtitles-en-fa.zip"
    $buildArgs = @("tools\build_lexicon_db.py", "--out", "ocean_lexicon.db", "--kaikki", "$dl\kaikki.jsonl.gz",
                   "--tatoeba-dir", "$dl\tatoeba", "--opensubtitles", "$dl\opensubtitles-en-fa.zip")
    if ($Urban) { $buildArgs += @("--urban", (Resolve-Path $Urban).Path) }
    & .\.venv\Scripts\python.exe @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "Building ocean_lexicon.db failed" }
    Remove-Item $dl -Recurse -Force
}

# Optional local language model for idioms and sentences offline (CPU wheel from the llama-cpp-python index).
# A failure here is not fatal: Ocean then uses the NLLB translator offline.
if ($WithSlm) {
    & .\.venv\Scripts\python.exe -m pip install --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu llama-cpp-python==0.3.19
    if ($LASTEXITCODE -eq 0) {
        New-Item -ItemType Directory -Force -Path "offline_slm_model" | Out-Null
        Get-File "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf" "offline_slm_model\qwen2.5-1.5b-instruct-q4_k_m.gguf"
    } else {
        Write-Warning "Could not install llama-cpp-python; Ocean uses the NLLB translator offline instead."
    }
}

# UI fonts (SIL OFL): Inter for English, Vazirmatn for Persian.
if (-not ((Test-Path "fonts\Vazirmatn-Medium.ttf") -and (Test-Path "fonts\Inter-Medium.ttf"))) {
    $tmp = Join-Path $env:TEMP "ocean_fonts"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $tmp, "fonts" | Out-Null
    Invoke-WebRequest -UseBasicParsing "https://github.com/rastikerdar/vazirmatn/releases/download/v33.003/vazirmatn-v33.003.zip" -OutFile "$tmp\vazirmatn.zip"
    Invoke-WebRequest -UseBasicParsing "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip" -OutFile "$tmp\inter.zip"
    Expand-Archive "$tmp\vazirmatn.zip" "$tmp\vazirmatn" -Force
    Expand-Archive "$tmp\inter.zip" "$tmp\inter" -Force
    foreach ($w in @("Regular", "Medium", "Bold")) {
        Copy-Item "$tmp\vazirmatn\fonts\ttf\Vazirmatn-$w.ttf" "fonts\" -Force
        Copy-Item "$tmp\inter\extras\ttf\Inter-$w.ttf" "fonts\" -Force
    }
    Remove-Item $tmp -Recurse -Force
}

if (-not (Test-Path "ocean.ico")) {
    & .\.venv\Scripts\python.exe app.py --make-icon
}

Write-Host "Setup complete. Start Ocean with run.bat, or build Ocean.exe with update_ocean.ps1" -ForegroundColor Green
