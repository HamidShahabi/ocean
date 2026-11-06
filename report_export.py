# Qt-free export of the saved issue reports ("!" button) into one self-contained .zip.
#
# The zip is meant to be attached to a chat with Claude when Ocean is next updated:
#   README.md        what the bundle is and how to work from it
#   reports.json     environment info plus every report: term, kind, date, note, versions and the full payload
#   snapshots/*.png  the result page exactly as it looked when it was reported
import json
import os
import pathlib
import platform
import re
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional

import lexical_engine

BUNDLE_FORMAT = 1

README = """\
# Ocean issue reports

This bundle was exported from the **Reports** page of Ocean, a desktop English-learning dictionary
(PyQt6, local SQLite datasets: Wiktionary/Kaikki, OpenSubtitles, Tatoeba, Urban Dictionary, an idioms
table, Google Translate cache, NLLB). The owner pressed the "!" button on each result below because
something in it was wrong: a definition, example, synonym, translation, IPA, layout or something technical.

## Contents

- `reports.json`: `environment` (OS, versions, size and build statistics of the local lexicon database)
  and `reports`, a list with one object per report:
  `id`, `term`, `kind` (word / idiom / sentence / persian), `created_at`, `note` (the owner's optional
  one-line remark, may be empty), `app_version`, `engine_version`, `snapshot` (path of the PNG inside this
  zip, or null) and `payload`, the full lookup result exactly as the app showed it (definitions with
  examples and Persian, synonyms, morphology, common expressions, level badges, ...).
- `snapshots/`: a picture of each result page as it was rendered. Look at it: some problems (cut-off text,
  garbled characters, wrong order) are only visible there, not in the payload.

## How to use it

1. Read `reports.json` and look at every snapshot. Use `note` as the owner's hint, but check the whole
   entry: a report can hide several problems.
2. For every report work out the **root cause in the code or data pipeline** (source routing, filtering,
   ranking, normalisation, translation tier, import script, rendering ...). Do not patch the single word.
3. **Fix the cause system-wide**, so every similar word, idiom or sentence improves. Group reports that share
   a cause and fix them together. Prefer a general rule or a better data source over an exception list.
4. Add regression tests that cover the class of problem (not only the reported term) and run the full test
   suite. If the rendering was at fault, check it with an offscreen screenshot.
5. Afterwards list which report was fixed by which change, and anything you could not fix and why. The owner
   can then delete the handled reports in the app (Menu > Reports).

`engine_version` and `app_version` tell which build produced each result; results from an older engine may
already be fixed in the current code, so reproduce a problem before changing anything.
"""


@lru_cache(maxsize=1)
def app_version() -> str:
    """Short git revision of a source checkout (an installed build has none: then just the engine version)."""
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=base, capture_output=True, text=True,
                              timeout=3, check=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def lexicon_meta(path: Optional[str] = None) -> Dict[str, Any]:
    """Build statistics of the local lexicon database (its `meta` table), read-only; {} if there is none."""
    path = path or lexical_engine.LEXICON_DB_FILE
    info: Dict[str, Any] = {"path_exists": os.path.exists(path)}
    if not info["path_exists"]:
        return info
    info["size_mb"] = round(os.path.getsize(path) / 1e6, 1)
    try:
        conn = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            info["meta"] = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
        finally:
            conn.close()
    except sqlite3.Error:
        info["meta"] = {}
    return info


def environment_info(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "frozen_build": bool(getattr(sys, "frozen", False)),
        "app_version": app_version(),
        "engine_version": lexical_engine.ENGINE_VERSION,
        "lexicon_db": lexicon_meta(),
    }
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR
        env["pyqt"] = PYQT_VERSION_STR
    except ImportError:
        pass
    if settings:
        keep = ("theme", "font_scale", "learning_mode", "refresh_period")
        env["settings"] = {k: settings[k] for k in keep if k in settings}
    return env


def _slug(term: str) -> str:
    return re.sub(r"[^\w]+", "-", term.lower(), flags=re.UNICODE).strip("-")[:40] or "entry"


def write_bundle(path: str, reports: List[Dict[str, Any]], settings: Optional[Dict[str, Any]] = None) -> int:
    """Write the zip for `reports` (ReportStore.all_full() rows) to `path`; returns the number of reports."""
    items: List[Dict[str, Any]] = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in reports:
            snap = None
            if r.get("snapshot"):
                snap = f"snapshots/{r['id']:03d}-{_slug(r['term'])}.png"
                z.writestr(snap, r["snapshot"])
            items.append({
                "id": r["id"], "term": r["term"], "kind": r["kind"], "created_at": r["created_at"],
                "note": r["note"], "app_version": r["app_version"], "engine_version": r["engine_version"],
                "snapshot": snap, "payload": r["payload"],
            })
        doc = {"format": BUNDLE_FORMAT, "environment": environment_info(settings), "reports": items}
        z.writestr("reports.json", json.dumps(doc, ensure_ascii=False, indent=2, default=str))
        z.writestr("README.md", README)
    return len(items)


def default_filename() -> str:
    return f"ocean-reports-{datetime.now():%Y-%m-%d}.zip"
