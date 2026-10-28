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

& ".\.venv\Scripts\pyinstaller.exe" --noconfirm --noconsole --onedir --paths="." --hidden-import=lexical_engine --hidden-import=bs4 --hidden-import=lxml --hidden-import=kokoro_onnx --hidden-import=eng_to_ipa --hidden-import=nltk --hidden-import=ctranslate2 --hidden-import=sentencepiece --name "Ocean" --icon="ocean.ico" app.py

# User data (history, memory, settings) lives in %APPDATA%\Ocean, not in the build.
foreach ($f in @("lexical_engine.py", "offline_lexicon.json", "ocean_offline_core.db")) {
    if (Test-Path $f) {
        Copy-Item -Path $f -Destination ".\dist\Ocean\$f" -Force
        Copy-Item -Path $f -Destination ".\dist\Ocean\_internal\$f" -Force
    }
}

foreach ($vfile in @("kokoro-v1.0.onnx", "voices-v1.0.bin")) {
    if (Test-Path $vfile) {
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

$sitePackages = ".\.venv\Lib\site-packages"
$internalDir = ".\dist\Ocean\_internal"
foreach ($pkg in @("kokoro_onnx", "espeakng_loader", "phonemizer", "segments", "language_tags", "eng_to_ipa", "ctranslate2", "sentencepiece")) {
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
