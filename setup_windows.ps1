# One-time Windows setup: venv, Python deps, NLTK WordNet, Kokoro voice, offline translator, fonts.
# Run from this folder:  powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
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
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# lexical_engine.py looks in .\nltk_data first. NLTK_DATA must name the target dir
# or newer NLTK refuses to download there ("Unauthorized path").
$env:NLTK_DATA = Join-Path $PSScriptRoot "nltk_data"
& .\.venv\Scripts\python.exe -m nltk.downloader -d $env:NLTK_DATA wordnet omw-1.4

$kokoroUrl = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
Get-File "$kokoroUrl/kokoro-v1.0.onnx" "kokoro-v1.0.onnx"
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
