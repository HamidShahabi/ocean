# -NoLaunch: build only (used by CI).
param([switch]$NoLaunch)

Write-Host "Building Fast 100% Offline-Capable Portable Ocean..." -ForegroundColor Cyan
Get-Process -Name "Ocean" -ErrorAction SilentlyContinue | Stop-Process -Force

if ((Test-Path ".\dist\Ocean\lexicon_vault.db") -and (Test-Path ".\lexicon_vault.db")) {
    if ((Get-Item ".\dist\Ocean\lexicon_vault.db").LastWriteTime -gt (Get-Item ".\lexicon_vault.db").LastWriteTime) {
        Copy-Item -Path ".\dist\Ocean\lexicon_vault.db" -Destination ".\lexicon_vault.db" -Force
    }
} elseif (Test-Path ".\dist\Ocean\lexicon_vault.db") {
    Copy-Item -Path ".\dist\Ocean\lexicon_vault.db" -Destination ".\lexicon_vault.db" -Force
}

if ((Test-Path ".\dist\Ocean\user_memory.json") -and (Test-Path ".\user_memory.json")) {
    if ((Get-Item ".\dist\Ocean\user_memory.json").LastWriteTime -gt (Get-Item ".\user_memory.json").LastWriteTime) {
        Copy-Item -Path ".\dist\Ocean\user_memory.json" -Destination ".\user_memory.json" -Force
    }
}

if (-not (Test-Path ".\ocean.ico")) {
    & ".\.venv\Scripts\python.exe" app.py --make-icon
}

& ".\.venv\Scripts\pyinstaller.exe" --noconfirm --noconsole --onedir --paths="." --hidden-import=lexical_engine --hidden-import=dictionary_sources --hidden-import=kokoro_onnx --hidden-import=eng_to_ipa --hidden-import=nltk --hidden-import=ctranslate2 --hidden-import=sentencepiece --name "Ocean" --icon="ocean.ico" app.py

# User data (history, memory, settings) lives in %APPDATA%\Ocean, not in the build.
foreach ($f in @("lexical_engine.py", "cefr_levels.json")) {
    if (Test-Path $f) {
        Copy-Item -Path $f -Destination ".\dist\Ocean\$f" -Force
        Copy-Item -Path $f -Destination ".\dist\Ocean\_internal\$f" -Force
    }
}
# The dictionary database (~250 MB) is read next to Ocean.exe only.
if (Test-Path "ocean_lexicon.db") {
    Copy-Item -Path "ocean_lexicon.db" -Destination ".\dist\Ocean\ocean_lexicon.db" -Force
}

# Ship one voice model: int8 (~88 MB) if present, else the full-size one (~310 MB).
$voiceModel = @("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx") | Where-Object { Test-Path $_ } | Select-Object -First 1
foreach ($vfile in @($voiceModel, "voices-v1.0.bin")) {
    if ($vfile -and (Test-Path $vfile)) {
        Copy-Item -Path $vfile -Destination ".\dist\Ocean\$vfile" -Force
    }
}

$appdataNltk = Join-Path $env:APPDATA "nltk_data"
if (Test-Path ".\nltk_data") {
    Copy-Item -Path ".\nltk_data" -Destination ".\dist\Ocean\nltk_data" -Recurse -Force
} elseif (Test-Path $appdataNltk) {
    Copy-Item -Path $appdataNltk -Destination ".\dist\Ocean\nltk_data" -Recurse -Force
}
if (Test-Path "offline_mt_model") {
    Copy-Item -Path "offline_mt_model" -Destination ".\dist\Ocean\offline_mt_model" -Recurse -Force
}
if (Test-Path "offline_slm_model") {
    Copy-Item -Path "offline_slm_model" -Destination ".\dist\Ocean\offline_slm_model" -Recurse -Force
}

$sitePackages = ".\.venv\Lib\site-packages"
$internalDir = ".\dist\Ocean\_internal"
# llama_cpp: the optional language model runtime (setup_windows.ps1 -WithSlm), copied only when installed.
foreach ($pkg in @("kokoro_onnx", "espeakng_loader", "phonemizer", "segments", "language_tags", "eng_to_ipa", "ctranslate2", "sentencepiece", "llama_cpp")) {
    $src = Join-Path $sitePackages $pkg
    $dst = Join-Path $internalDir $pkg
    if (Test-Path $src) { Copy-Item -Path $src -Destination $dst -Recurse -Force }
}

if (Test-Path "fonts") {
    Copy-Item -Path "fonts" -Destination ".\dist\Ocean\fonts" -Recurse -Force
}

$exePath = Join-Path $PWD "dist\Ocean\Ocean.exe"
if ((Test-Path $exePath) -and -not $NoLaunch) {
    Write-Host "[SUCCESS] Launching 100% Offline-Ready Ocean..." -ForegroundColor Green
    Start-Process -FilePath $exePath -WorkingDirectory ".\dist\Ocean"
}
