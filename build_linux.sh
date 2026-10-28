#!/usr/bin/env bash
# Linux counterpart of update_ocean.ps1: builds a portable dist/Ocean/ folder with PyInstaller.
set -euo pipefail
cd "$(dirname "$0")"

pkill -x Ocean 2>/dev/null || true

# Older versions kept history/memory next to the exe: save it before the rebuild wipes dist/ (migrated on next start).
for f in lexicon_vault.db user_memory.json; do
    if [ -f "dist/Ocean/$f" ] && { [ ! -f "$f" ] || [ "dist/Ocean/$f" -nt "$f" ]; }; then
        cp -f "dist/Ocean/$f" "$f"
    fi
done

uv pip install --python .venv/bin/python -r requirements.lock   # includes the pinned pyinstaller

collect=()
for pkg in kokoro_onnx espeakng_loader phonemizer segments language_tags eng_to_ipa ctranslate2 sentencepiece; do
    collect+=(--collect-all "$pkg")
done

.venv/bin/pyinstaller --noconfirm --windowed --onedir --paths=. \
    --hidden-import=lexical_engine --hidden-import=bs4 --hidden-import=lxml --hidden-import=nltk \
    "${collect[@]}" --name Ocean app.py

# Data files live next to the executable (app.get_base_dir / lexical_engine._get_engine_base_dir look there).
# User data (history, memory, settings) lives in ~/.local/share/Ocean, not in the build.
for f in offline_lexicon.json ocean_offline_core.db \
         kokoro-v1.0.onnx voices-v1.0.bin; do
    [ -f "$f" ] && cp -f "$f" dist/Ocean/
done
for d in nltk_data offline_mt_model fonts; do
    [ -d "$d" ] && cp -rf "$d" dist/Ocean/
done

echo "Built: $PWD/dist/Ocean/Ocean"
