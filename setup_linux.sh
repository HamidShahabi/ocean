#!/usr/bin/env bash
# One-time Linux setup: venv, Python deps, NLTK WordNet, Kokoro voice model.
set -euo pipefail
cd "$(dirname "$0")"

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# lexical_engine.py looks in ./nltk_data first. NLTK_DATA must name the target dir
# or newer NLTK refuses to download there ("Unauthorized path").
NLTK_DATA="$PWD/nltk_data" .venv/bin/python -m nltk.downloader -d "$PWD/nltk_data" wordnet omw-1.4

KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# int8 model (~88 MB); an existing full-size kokoro-v1.0.onnx is used as is and not replaced.
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
    if [ ! -f "$f" ] && { [ "$f" != kokoro-v1.0.int8.onnx ] || [ ! -f kokoro-v1.0.onnx ]; }; then
        curl -fL --retry 3 -o "$f.part" "$KOKORO_URL/$f" && mv "$f.part" "$f"
    fi
done

# Offline EN->FA backup translator: NLLB-200 distilled 600M, CTranslate2 int8.
# Skipped when a model is already present (e.g. your own), so files from two models never mix.
if [ ! -f offline_mt_model/model/model.bin ]; then
    NLLB_URL="https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8/resolve/main"
    mkdir -p offline_mt_model/model
    curl -fL --retry 3 -o offline_mt_model/sentencepiece.model "$NLLB_URL/sentencepiece.bpe.model"
    # model.bin last: its presence marks a complete download.
    for f in config.json shared_vocabulary.txt model.bin; do
        curl -fL --retry 3 -o "offline_mt_model/model/$f.part" "$NLLB_URL/$f" && mv "offline_mt_model/model/$f.part" "offline_mt_model/model/$f"
    done
fi

# UI fonts (SIL OFL): Inter for English, Vazirmatn for Persian.
if [ ! -f fonts/Vazirmatn-Medium.ttf ] || [ ! -f fonts/Inter-Medium.ttf ]; then
    tmp="$(mktemp -d)"
    curl -fL --retry 3 -o "$tmp/vazirmatn.zip" https://github.com/rastikerdar/vazirmatn/releases/download/v33.003/vazirmatn-v33.003.zip
    curl -fL --retry 3 -o "$tmp/inter.zip" https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
    mkdir -p fonts
    for w in Regular Medium Bold; do
        unzip -o -j -q "$tmp/vazirmatn.zip" "fonts/ttf/Vazirmatn-$w.ttf" -d fonts
        unzip -o -j -q "$tmp/inter.zip" "extras/ttf/Inter-$w.ttf" -d fonts
    done
    rm -rf "$tmp"
fi

echo "Setup complete. Start Ocean with ./run.sh"
