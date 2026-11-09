# Ocean upgrade 2: handoff

This file is the same on `upgrade-2`, `upgrade/d-engine` and `upgrade/e-data`, so merging those branches never conflicts on it.
Delete it in the last commit before `upgrade-2` is merged into `main`.

The spec is `ocean_upgrade_specification_2.md` and the screenshots are `OceanU2.pdf` (pages 001–008). Both were shared in chat and are not in the repo.
If a session needs them, attach them again.

## Where things stand (2026-11-08)

| Branch | Base | State |
|---|---|---|
| `upgrade-2` | `main` | Integration branch, about 70% of the spec. `upgrade/d-engine` is merged (2026-11-09); all 435 tests pass. |
| `upgrade/d-engine` | `main` (**not** `upgrade-2`) | Engine rewrite, merged into `upgrade-2`. |
| `upgrade/e-data` | `upgrade-2` | Merged into `upgrade-2`. Idiom list (9,383 rows) and `tools/build_idioms.py`. Persian translations and the slang table are not done. |

### Done on `upgrade-2`
- **Removed** Reader, Quiz, Review, Pronounce, Teach Ocean, Anki export, the lookup counter, offline-only mode, the source toggles and word highlighting.
  - Files deleted: `reader.py`, `quiz.py`, `review.py`, `cloze.py`, `anki_export.py`, `speech_timing.py`, `ui/reader_page.py`, `ui/quiz_page.py`, `ui/review_page.py`, `ui/pronounce_page.py`, `ui/teach_page.py`, `ui/highlight.py`.
  - `storage.py` drops the `reviews` table.
- **Themes:** Material 3 light/dark (`ui/theme.py`), fonts Google Sans Flex, Vazirmatn and Charis SIL for IPA (`make_ipa_font`, `QLabel#IpaText`).
- **Motion:** fades, a fading stacked widget and a drawer slide (`ui/motion.py`). `OCEAN_REDUCE_MOTION=1` turns it off.
- **Marquee** for overflowing text in dropdowns and labels (`ui/marquee.py`).
- **Home:** new placeholder; Back, Forward and the Ocean button are hidden on home (`_update_nav_visibility` in `ui/main_window.py`).
- **History:** centred star, checkboxes, Select all, bulk delete, "Favorites Only" (`ui/history_page.py`).
- **Refresh** re-runs the lookup with `refresh=True`, and Back returns to the previous output.
  - Nav entries are `{"term", "payload"}`.
  - `refresh` is only passed if `analyze_with_dictionaries` accepts it (checked with `inspect.signature`), so it works before and after the engine merge.
- **Reports:** a "!" button beside Save, a Reports page, a `reports` table (`ReportStore` in `storage.py`), and an export zip with README, `reports.json` and snapshots (`report_export.py`).
- **Read-only Sources page** (`ui/sources_page.py`).
- **Audio:** 24 MB WAV cache plus prefetch when a speaker button is created, so playback starts at once (`ui/speaker.py`).
- **Shortcut:**
  - Windows: Ctrl+C twice, or Alt+Q. Ctrl+Alt+O is kept as a fallback (`desktop_lookup.py`).
  - Linux: `install_shortcut_linux.sh` binds Alt+Q and Ctrl+Alt+O.
  - Known issue: Alt+Q takes over Word's "Tell me" while Ocean runs.
- **Section names:** "Forms, Roots & Alternatives", and "Derived Terms" becomes "Common Expressions" (`ui/results_page.py`).

### Done on `upgrade/d-engine` (not yet in `upgrade-2`)
- **Examples:** clean, subtitle-first examples. Old quotations are rejected (long-s OCR, `[…]`, `#`, verse `/`), which fixes screenshots 006–008. See `lexicon_text.py`.
- **Senses:** 1–3 everyday senses. Mathematics, Linguistics, Anatomy and Archaic senses are pushed down, and parentheticals are stripped.
- **Synonyms and antonyms** come from Kaikki, capped at 3–5. WordNet and nltk are removed and `requirements.lock` is regenerated.
- **IPA** uses one notation (ɹ→r).
- **Idioms:** exact phrase match first, then fuzzy and lemma matching, plus a hook for an `idioms_fa` table.
- **Persian:** cached translation (`translation_cache.py`) and automatic offline detection.
- **Engine entry** takes `refresh=True`.
- **Importer:** `tools/build_lexicon_db.py` is updated.

### Not done yet
1. ~~Merge `upgrade/d-engine` into `upgrade-2`.~~ Done. Conflicts were README and three tests; the engine keeps `set_offline_only` as a hard switch, so `tests/test_offline_mode.py` stays.
2. ~~Merge `upgrade/e-data` into `upgrade-2`.~~ Done (only HANDOFF.md conflicted).
3. ~~Match `ui/results_page.py` to the engine payload.~~ Done: reads `common_expressions` only, shows `forms`, `alternatives` and the `phrase_match` note, IPA labels go through `theme.style_ipa_label`. The `derived_terms` alias is gone (item 8).
4. ~~Match the Sources page to the engine's real routing.~~ Done: dictionary sources in `REGISTRY` order, then idioms, cache, Google, optional local model, NLLB; `tests/test_pages_ui.py` checks the order.
5. **Persian for the idiom list (in progress: 1,500 of 9,382 done).**
   - Done: `part-01.tsv` to `part-03.tsv` in `data/idioms_fa_parts/` cover rows 1–1500 of `data/idioms_en.tsv.gz`; `data/idioms_fa.tsv.gz` is rebuilt with `tools/build_idioms.py merge-fa`.
   - Next: `part-04.tsv` = the next 500 rows without Persian (rows 1501–2000), same format, then re-run `merge-fa --idioms data/idioms_en.tsv.gz --fa-dir data/idioms_fa_parts --out data/idioms_fa.tsv.gz`.
   - `tools/build_lexicon_db.py` reads the merged file by column name (it used to take the key column as Persian), and both setup scripts pick up `data/idioms_fa.tsv.gz`. An existing `ocean_lexicon.db` has no `idioms_fa` until it is rebuilt.
   - The engine reads `idioms_fa` first and never calls NLLB or the local model for an idiom that has Persian (`tests/test_translation_tiers.py`).
6. **Urban Dictionary slang table:**
   - The top 10–15k entries in a small SQLite table, bundled the same way as the lexicon.
   - Read-only, with no network at runtime.
   - `api.urbandictionary.com` was reachable from the cloud environment.
   - **Blocked (2026-11-09):** `api.urbandictionary.com` answers from here, but `thumbs_up` and `thumbs_down` are 0 for every definition, so the API cannot rank entries or drop jokes by votes, and there is no "top" endpoint. A real dump with votes (as of May 2016, no examples column) exists as `opensporks/urban_dict_word_defs` on Hugging Face (`urbandict-word-defs.csv`, 538 MB, a mirror of the Kaggle "Urban Dictionary Words and Definitions" set). Nothing is built yet; it needs a decision on downloading that file.
7. ~~**Startup:** defer slow imports and preloads in `app.py`.~~ Done: `kokoro_onnx`/`numpy` load with the voice model (started after the window is shown, `ui/speaker.py`) and `ctranslate2`/`sentencepiece` load with the NLLB model (`_load_ct2_model`). Time to window shown (offscreen, 5 runs): about 1.5-1.8 s before, 1.1-1.4 s after; `import app` alone 1.2 s to 0.7 s. `requests` (about 0.18 s) is still imported at start: `OfflineModeError` subclasses its exception and the sessions are built at import.
8. ~~**Remove leftover dead code:** `record_taught_entry`, `REGISTRY.set_enabled`, `tests/test_source_toggles.py`, and anything still pointing at removed pages.~~ Done, together with `is_enabled`/`disabled_names`/`any_enabled` and the engine's `derived_terms` alias. The two settings tests moved to `tests/test_ui_smoke.py` (`RemovedSettingsTest`). `delete_taught_entry` has no caller either but was not on the list; saved `entries` are still read.
9. ~~**Database tidy-up:** `VACUUM`/`ANALYZE` after the build, and check indexes on the lookup columns.~~ Done: the build runs `ANALYZE` then `VACUUM`; every column `lexicon_db.py` looks up by already had an index; `tests/test_lexicon_db.py` checks the query plans and `sqlite_stat1` on the fixture database.
10. **Windows build:** dispatch `.github/workflows/windows-build.yml` on `upgrade-2` (Actions tab, "Run workflow") and fix anything it reports.
11. **Open a PR** from `upgrade-2` to `main` with a short list of what changed and the new shortcut.

## Pitfalls
- **The `upgrade/d-engine` merge:**
  - It forked from `main` before the removals.
  - In conflicts, keep the deletions from `upgrade-2` and take the engine side for engine files: `lexical_engine.py`, `lexicon_db.py`, `lexicon_text.py`, `translation_cache.py`, `dictionary_sources/*`, `tools/build_lexicon_db.py`.
  - In `README.md` and `packaging/READ-ME-FIRST.txt`, keep the new shortcut text and drop any mention of removed features.
  - `requirements.lock`: take the engine side (nltk removed). Don't hand-edit it; regenerate with `uv pip compile` if you need to.
- **Old databases** still have a `lookup_count` column. It is harmless; leave it.
- **Tests:** they must not touch the network.
  - Run: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests`.
  - Also run `pyflakes` on the changed files.
- **Nothing has been tried on real Windows.** The Ctrl+C-twice watcher and the focus fix (`AttachThreadInput`) need a manual check there.
- **Google Translate** returned HTTP 429 from the cloud environment. Keep the cache and the offline fallbacks working without it.

## Prompts for new Claude sessions

Run one prompt per session. Each one is self-contained. Keep sessions short; that is what keeps them cheap.

### 1. Merge the engine
```
In the Ocean repo, check out upgrade-2 and merge origin/upgrade/d-engine. Read HANDOFF.md first ("Pitfalls" says how to resolve the conflicts: keep upgrade-2's removals, take the engine side for engine files). Then run QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests and pyflakes on changed files, fix what fails without skipping tests, update the "Where things stand" table in HANDOFF.md, commit and push upgrade-2. Don't start subagents.
```

### 2. Results page matches the engine
```
In the Ocean repo on upgrade-2 (engine already merged), read HANDOFF.md item 3. Compare the dict that lexical_engine.analyze_with_dictionaries returns with what ui/results_page.py reads; make the page show the engine's examples, synonyms/antonyms (3–5), common expressions, idioms and IPA (IPA labels use theme.style_ipa_label). Remove reads of keys the engine no longer sends. Add or update a UI smoke test, run the tests, tick item 3 in HANDOFF.md, commit and push. Don't start subagents.
```

### 3. Merge the idiom data and the Sources page
```
In the Ocean repo on upgrade-2, merge origin/upgrade/e-data, then do HANDOFF.md item 4: make ui/sources_page.py list the sources in the order the engine really uses them. Run the tests, tick items 2 and 4 in HANDOFF.md, commit and push. Don't start subagents.
```

### 4. Persian for the idioms (can be split over several sessions)
```
In the Ocean repo on upgrade-2, do HANDOFF.md item 5. Read tools/build_idioms.py's docstring. Take the next 500 rows of data/idioms_en.tsv.gz that have no Persian yet and write natural, idiomatic Persian (not word-for-word; what an Iranian would actually say) into data/idioms_fa_parts/part-NN.tsv as key<TAB>fa. Then run tools/build_idioms.py merge-fa, check the engine uses idioms_fa before NLLB (add a test if missing), commit and push. Stop after one part.
```

### 5. Urban Dictionary slang table
```
In the Ocean repo on upgrade-2, do HANDOFF.md item 6: add a step to tools/ (or extend tools/build_lexicon_db.py) that builds a compact SQLite table of the top 10–15k Urban Dictionary entries (term, short cleaned definition, one example, thumbs score; drop offensive-only and joke entries), bundled read-only like the lexicon. Make the engine's slang reading use it with no network at runtime. Tests must use a small fixture, never the network. Commit and push.
```

### 6. Cleanup and speed
```
In the Ocean repo on upgrade-2, do HANDOFF.md items 7, 8 and 9: defer slow imports/preloads in app.py so the window appears fast, remove the dead code listed there, and VACUUM/ANALYZE plus index checks for the lexicon build. Run tests and pyflakes, tick the items, commit and push.
```

### 7. Windows build and PR
```
In the Ocean repo, dispatch .github/workflows/windows-build.yml on upgrade-2 and wait for it. Fix any failure and re-run until green. Then delete HANDOFF.md in one commit and open a PR upgrade-2 -> main (do not merge) listing what changed, the new shortcut (Ctrl+C twice or Alt+Q on Windows, Ctrl+Alt+O fallback; Alt+Q overrides Word's "Tell me" while Ocean runs) and what still needs a manual check on Windows.
```
