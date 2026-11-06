#!/usr/bin/env bash
# One-time Linux setup: venv, Python deps, WordNet, Kokoro voice model, offline translator, Ocean's local
# dictionary database, fonts.
#   --urban FILE   also import an Urban Dictionary dump (CSV or JSON; see tools/build_lexicon_db.py)
#   --with-slm     also install the optional local language model (llama-cpp-python + Qwen2.5-1.5B, ~1.1 GB;
#                  see "Offline language model" in the README before you do)
set -euo pipefail
cd "$(dirname "$0")"

URBAN=""
WITH_SLM=0
while [ $# -gt 0 ]; do
    case "$1" in
        --urban) URBAN="$(realpath "$2")"; shift 2 ;;
        --with-slm) WITH_SLM=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock

# lexical_engine.py looks in ./nltk_data first. NLTK_DATA must name the target dir
# or newer NLTK refuses to download there ("Unauthorized path").
# WordNet 3.1 gives synonyms and antonyms; it needs the 3.0 data too, which the lemmatizer uses.
NLTK_DATA="$PWD/nltk_data" .venv/bin/python -m nltk.downloader -d "$PWD/nltk_data" wordnet wordnet31 omw-1.4

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

# Ocean's dictionary database (~250 MB) from Kaikki.org (Wiktionary), Tatoeba and OpenSubtitles: a ~750 MB
# download and a few minutes of importing. Skipped when ocean_lexicon.db exists: delete it to rebuild.
if [ ! -f ocean_lexicon.db ]; then
    dl=dataset_downloads
    mkdir -p "$dl/tatoeba"
    get() { [ -f "$2" ] || { curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"; }; }
    get https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz "$dl/kaikki.jsonl.gz"
    for f in eng/eng_sentences.tsv.bz2 pes/pes_sentences.tsv.bz2 eng/eng-pes_links.tsv.bz2; do
        get "https://downloads.tatoeba.org/exports/per_language/$f" "$dl/tatoeba/$(basename "$f")"
    done
    get https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2018/moses/en-fa.txt.zip "$dl/opensubtitles-en-fa.zip"
    .venv/bin/python tools/build_lexicon_db.py --out ocean_lexicon.db --kaikki "$dl/kaikki.jsonl.gz" \
        --tatoeba-dir "$dl/tatoeba" --opensubtitles "$dl/opensubtitles-en-fa.zip" ${URBAN:+--urban "$URBAN"}
    rm -rf "$dl"
fi

# Optional local language model for idioms and sentences offline. llama-cpp-python is built from source
# (needs gcc/g++): its only prebuilt Linux wheel is for musl, not glibc. A failure here is not fatal.
if [ "$WITH_SLM" = 1 ]; then
    if uv pip install --python .venv/bin/python --no-binary llama-cpp-python llama-cpp-python==0.3.19; then
        mkdir -p offline_slm_model
        f=offline_slm_model/qwen2.5-1.5b-instruct-q4_k_m.gguf
        [ -f "$f" ] || { curl -fL --retry 3 -o "$f.part" \
            https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf && mv "$f.part" "$f"; }
    else
        echo "Could not build llama-cpp-python; Ocean uses the NLLB translator offline instead." >&2
    fi
fi

echo "Setup complete. Start Ocean with ./run.sh"
