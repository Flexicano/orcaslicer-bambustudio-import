# BambuStudio Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An OrcaSlicer Python plugin with a "BambuStudio import" tab that lets the user tick BambuStudio user profiles and import them, once, into a live-reloaded local preset bundle.

**Architecture:** One plugin file (Orca requires exactly one entry file per plugin folder) built from pure, `orca`-free functions (`paths`, `scan`, `classify`, `convert`, `bundle`) plus a thin `ImportCore` message handler and an `ImportPage` Pages capability. `orca` is imported optionally so pytest can import the module without OrcaSlicer.

**Tech Stack:** Python ≥ 3.12 standard library only; pytest for tests; inline HTML/JS for the tab.

**Spec:** `docs/specs/2026-10-28-bambustudio-import-design.md`

## Global Constraints

- Plugin file: `orca_plugins/bambustudio_import/bambustudio_import.py`, PEP 723 header with `requires-python = ">=3.12"`, `dependencies = []`, `[tool.orcaslicer.plugin]` `name = "BambuStudio Import"`, `version = "0.1.0"`, author, description.
- No third-party imports in the plugin.
- Bundle id `bambustudio_import`; bundle dir `<orca>/user/<preset_folder>/_local/bambustudio_import/` with `bundle_metadata.json` exactly `{"id": "bambustudio_import", "name": "BambuStudio import", "version": "1.0.0", "description": "Profielen geïmporteerd uit BambuStudio", "author": "BambuStudio Import plugin"}`.
- Never write under any BambuStudio directory. Only write/delete inside the bundle dir.
- Statuses exactly: `ready`, `missing_parent`, `exists`, `imported`, `invalid`. UI text in Dutch.
- Always-dropped keys: `setting_id`, `base_id`, `user_id`, `updated_time`, `instantiation`. Always kept: `name`, `inherits`, `from` (set to `"User"`), `version`, and the `*_settings_id` key for the kind (`filament_settings_id` / `print_settings_id` / `printer_settings_id`, value = name).
- Unsafe filename chars `/\:*?"<>|` → `_`.
- Log prefix `[bambustudio_import]` to stderr.
- `orca.host.*` is only touched on the UI thread (inside `on_message` / `get_ui`); no worker threads.

## Review Focus

1. A Bambu file whose JSON `name` differs from its filename (Bambu renames on save) — classification and parent lookup must use the JSON `name`, not the filename. Test in Task 2.
2. The same name existing in Orca under a *different* type (a filament and a process both called "X") — must not count as `exists`/parent-found. Test in Task 3.
3. A child ticked without its Bambu-user parent ticked (and parent not already in the bundle) — child is skipped with a reason, rest of the import still happens. Test in Task 6.
4. `orca.host.preset_bundle()` raising `RuntimeError` because Orca is still starting — scan returns an error message, no crash. Test in Task 6.
5. Re-import of an already-imported profile overwrites the bundle file without leaving a `.tmp` behind or duplicating it under a second filename. Test in Task 5.

---

### Task 1: Project skeleton and paths

**Files:**
- Create: `orca_plugins/bambustudio_import/bambustudio_import.py`
- Create: `tests/conftest.py` (adds `orca_plugins/bambustudio_import` to `sys.path`)
- Create: `tests/test_paths.py`
- Create: `pyproject.toml` (only `[tool.pytest.ini_options] testpaths = ["tests"]`)

**Interfaces:**
- Produces:
  - `BUNDLE_ID: str = "bambustudio_import"`
  - `KINDS: tuple[str, ...] = ("filament", "process", "machine")`
  - `orca_data_dir(here: str = HERE) -> str` — walk up ≤6 levels to a dir named `orca_plugins`, return its parent; else OS fallback (`~/Library/Application Support/OrcaSlicer`, `%APPDATA%\OrcaSlicer`, `~/.config/OrcaSlicer`).
  - `bambu_data_dirs(home: str | None = None) -> list[str]` — existing dirs among `BambuStudio`, `BambuStudioBeta` in the OS app-support location (same convention as above).
  - `bambu_accounts(bambu_dir: str) -> list[tuple[str, int]]` — `(account_dir_name, json_count)` for subdirs of `<bambu_dir>/user/` that contain at least one of `filament/`, `process/`, `machine/`; sorted by count descending, then name.
  - `preset_folder_from_file(path: str) -> str | None` — path segment right after `user`, else `None`.
  - `resolve_preset_folder(user_preset_files: list[str], orca_dir: str) -> str` — first non-None `preset_folder_from_file`; else the single non-`default` subdir of `<orca_dir>/user/` if exactly one; else `"default"`.
  - `bundle_dir(orca_dir: str, preset_folder: str) -> str`

- [ ] **Step 1: Init repo and write failing tests** — `git init`; tests:
  - `test_orca_data_dir_walks_up`: `here = tmp/"OrcaSlicer/orca_plugins/bambustudio_import"` → returns `tmp/"OrcaSlicer"`.
  - `test_bambu_accounts_sorted_by_count`: user/`111`/filament with 3 json, user/`default`/process with 1, user/`junk` (no kind dirs) → `[("111", 3), ("default", 1)]`.
  - `test_preset_folder_from_file`: `".../OrcaSlicer/user/5744/filament/x.json"` → `"5744"`; `"/a/b.json"` → `None`.
  - `test_resolve_preset_folder_fallbacks`: no files + `user/{default,abc}` → `"abc"`; no files + `user/{default,a,b}` → `"default"`.
  - `test_bundle_dir`: equals `os.path.join(orca, "user", "abc", "_local", "bambustudio_import")`.
- [ ] **Step 2:** `pytest tests/test_paths.py -v` → FAIL (import error).
- [ ] **Step 3:** Write the plugin file with PEP 723 header, `try: import orca` / `except ImportError: orca = None`, `HERE`, `_log(msg)`, and the functions above.
- [ ] **Step 4:** `pytest tests/test_paths.py -v` → PASS.
- [ ] **Step 5:** `git add -A && git commit -m "feat: plugin skeleton and path resolution"`

### Task 2: Scan BambuStudio profiles

**Files:**
- Modify: `orca_plugins/bambustudio_import/bambustudio_import.py`
- Test: `tests/test_scan.py`

**Interfaces:**
- Produces:
  - `@dataclass SourcePreset(kind: str, name: str, path: str, inherits: str, data: dict | None, error: str)` (as in spec).
  - `scan_account(account_dir: str) -> list[SourcePreset]` — `*.json` in `filament/`, `filament/base/` (kind `filament`), `process/`, `machine/`; ignores `.info`; sorted by `(kind, name.lower())`. `name` = JSON `name` if a non-empty string, else filename stem. Unreadable/invalid JSON or non-object → `data=None`, `error` = `"Ongeldige JSON: <exc>"`, `inherits=""`.

- [ ] **Step 1: Failing tests:**
  - `test_scan_kinds_and_base`: files `filament/a.json`, `filament/base/b.json`, `process/p.json`, `machine/m.json`, `filament/a.info` → kinds `["filament","filament","machine","process"]`, 4 items.
  - `test_scan_uses_json_name_not_filename` (Review Focus 1): `filament/old.json` with `{"name": "New Name", "inherits": "Generic PLA"}` → `name == "New Name"`, `inherits == "Generic PLA"`.
  - `test_scan_invalid_json`: `filament/bad.json` containing `{"name":` → `data is None`, `error.startswith("Ongeldige JSON")`, `name == "bad"`.
  - `test_scan_real_profiles` (skip if `~/Library/Application Support/BambuStudio/user` absent): every item for the largest account has `data is not None`; count == number of `*.json` files found by `glob`.
- [ ] **Step 2:** `pytest tests/test_scan.py -v` → FAIL.
- [ ] **Step 3:** Implement `SourcePreset`, `scan_account`.
- [ ] **Step 4:** `pytest tests/test_scan.py -v` → PASS.
- [ ] **Step 5:** Commit `feat: scan BambuStudio user profiles`.

### Task 3: OrcaIndex and classification

**Files:**
- Modify: plugin file
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `SourcePreset`, `KINDS`.
- Produces:
  - `@dataclass OrcaIndex(names: dict[str, set[str]], keys: dict[str, dict[str, set[str]]], user_files: list[str])` — `names[kind]` preset names; `keys[kind][name]` config keys; `user_files` `file` paths of user presets.
  - `OrcaIndex.from_host(bundle) -> OrcaIndex` — maps `bundle.filaments → "filament"`, `bundle.prints → "process"`, `bundle.printers → "machine"`; iterates each collection, reads `p.name`, `set(p.config_keys())`, and `p.file` when `p.is_user()`. Wrap per-preset access in try/except (skip bad entries).
  - `OrcaIndex.union_keys(kind: str) -> set[str]`
  - `@dataclass Classified(preset: SourcePreset, status: str, reason: str, selectable: bool, default_checked: bool)`
  - `classify(presets: list[SourcePreset], index: OrcaIndex, imported: dict[str, set[str]]) -> list[Classified]` — `imported[kind]` = names already in our bundle. Same order as input. Rules in spec order. Parent lookup is per kind. Rule 2 Bambu-parent resolution: iterate to a fixed point — a parent counts if it is in `index.names[kind]` or is a scanned preset of the same kind whose own status is not `invalid`/`missing_parent`. Rule 4 (`exists`) checks `index.names[kind]` minus `imported[kind]`.
  - Reasons (exact): ready `"Klaar om te importeren"`; missing_parent `"Parent '{inherits}' bestaat niet in OrcaSlicer"`; imported `"Al geïmporteerd — aanvinken overschrijft"`; exists `"Naam bestaat al in OrcaSlicer — import maakt een tweede preset met deze naam"`; invalid = `preset.error`.

- [ ] **Step 1: Failing tests** (build `OrcaIndex` directly; one test for `from_host` with fake objects exposing `filaments/prints/printers` lists of objects with `name`, `file`, `is_user()`, `config_keys()`):
  - `test_ready_when_parent_in_orca`
  - `test_missing_parent` → `selectable is False`, reason contains the parent name.
  - `test_parent_is_other_bambu_user_preset`: child inherits `"My Base"` which inherits `"Generic PLA"` (in Orca) → both `ready`.
  - `test_chain_with_missing_root`: child → `"My Base"` → `"Nope"` → both `missing_parent`.
  - `test_parent_lookup_is_per_kind` (Review Focus 2): process inherits `"X"`; Orca has `"X"` only as filament → `missing_parent`. Filament named `"Y"` while Orca has process `"Y"` → `ready`, not `exists`.
  - `test_imported_beats_exists`: name in both `imported["filament"]` and `index.names["filament"]` → `imported`, `default_checked is False`, `selectable is True`.
  - `test_exists`, `test_invalid`.
  - `test_from_host_maps_collections`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pytest tests/test_classify.py -v` → PASS.
- [ ] **Step 5:** Commit `feat: classify Bambu presets against Orca`.

### Task 4: Convert to Orca JSON

**Files:**
- Modify: plugin file
- Test: `tests/test_convert.py`

**Interfaces:**
- Consumes: `SourcePreset`, `OrcaIndex`.
- Produces:
  - `ALWAYS_DROP: frozenset[str]`, `SETTINGS_ID_KEY: dict[str, str]` (values from Global Constraints).
  - `convert(preset: SourcePreset, index: OrcaIndex, parents: dict[str, SourcePreset]) -> tuple[dict, list[str]]` — `parents` maps names of Bambu-user presets of the same kind (for chained inheritance). Known keys = `index.keys[kind][root]` where `root` is the first ancestor found in Orca (walking through `parents`); if no `inherits`, `index.union_keys(kind)`. Returns new dict (input untouched) and sorted dropped keys (excluding `ALWAYS_DROP`, which are dropped silently).

- [ ] **Step 1: Failing tests:**
  - `test_drops_unknown_keeps_known`: Orca parent keys `{"nozzle_temperature"}`; data has `nozzle_temperature`, `filament_extruder_variant` → result has `nozzle_temperature`, dropped == `["filament_extruder_variant"]`.
  - `test_always_kept_and_from_user`: result has `name`, `inherits`, `version`, `from == "User"`, and `filament_settings_id == [name]` (Bambu stores it as a one-element list; always write `[name]` for filament, plain `name` string for `print_settings_id` / `printer_settings_id` — matching how Orca's own user presets store them).
  - `test_meta_dropped_silently`: `setting_id` in data → not in result, not in dropped.
  - `test_chained_parent_uses_root_keys`: child → Bambu "My Base" → Orca "Generic PLA".
  - `test_no_inherits_uses_union`.
  - `test_input_not_mutated`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat: convert Bambu preset JSON to Orca`.

### Task 5: Bundle writing, listing, clearing, reload

**Files:**
- Modify: plugin file
- Test: `tests/test_bundle.py`

**Interfaces:**
- Produces:
  - `safe_filename(name: str) -> str`
  - `ensure_metadata(bdir: str) -> None` — writes metadata (Global Constraints) if missing.
  - `write_preset(bdir: str, kind: str, data: dict) -> str` — atomic (`.tmp` + `os.replace`), UTF-8, `indent=4`, `ensure_ascii=False`; returns path.
  - `imported_names(bdir: str) -> dict[str, set[str]]` — reads `name` from each json in `<bdir>/<kind>/`; missing dir → empty sets for all `KINDS`.
  - `clear_bundle(bdir: str) -> None` — removes only `<bdir>/{filament,process,machine}` and `<bdir>/bundle_metadata.json`; refuses (raises `ValueError`) if `os.path.basename(bdir) != BUNDLE_ID` or parent basename != `_local`.
  - `reload_bundle(host) -> tuple[bool, str]` — `host` is `orca.host` or a fake. No `host.preset.bundles.reload_local` → `(False, "Herstart OrcaSlicer om de profielen te laden")`. If `list_ids` exists and `BUNDLE_ID` not in it → `(False, "OrcaSlicer ziet de bundle nog niet — herstart OrcaSlicer")`. `RuntimeError e` → `(False, f"Herladen mislukt: {e}")`. Success → `(True, "Profielen geladen")`.

- [ ] **Step 1: Failing tests:**
  - `test_safe_filename`: `safe_filename('a/b:c*?"<>|\\d') == "a_b_c_______d"`; `safe_filename("PLA+ Wit") == "PLA+ Wit"`.
  - `test_write_and_list_roundtrip` incl. a unicode name `"PLA+ Wit — mat"`.
  - `test_rewrite_overwrites_no_tmp_left` (Review Focus 5): write same name twice with different data → one file in dir, content is second version, no `*.tmp`.
  - `test_clear_only_touches_bundle`: sibling file `<_local>/other/x.json` and `<bdir>/notes.txt` survive; kind dirs and metadata gone.
  - `test_clear_refuses_wrong_dir` → `ValueError`.
  - `test_reload_variants`: fakes for missing API, not listed, RuntimeError, success.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat: local bundle write/clear/reload`.

### Task 6: ImportCore message handling

**Files:**
- Modify: plugin file
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `class ImportCore(host, orca_dir: str, bambu_dirs: list[str])`
  - `ImportCore.handle(msg: dict) -> dict` — dispatch on `msg["action"]` ∈ `scan` / `import` / `clear`; unknown → `{"type": "error", "message": "Onbekende actie: X"}`; any exception → `{"type": "error", "message": str(e)}` and `_log`.
  - `scan` → `{"type":"scan","accounts":[{"id": "<bambu_dir_basename>/<account>", "label": "...", "count": n}], "account": id, "items":[item...], "bundle_dir": path}`; `item = {"path","kind","name","inherits","status","reason","selectable","checked"}`. No Bambu dirs → `accounts: []`, `items: []`, `message` = `"BambuStudio niet gevonden in <expected path>"`. `host.preset_bundle()` raising `RuntimeError` → `{"type":"error","message":"OrcaSlicer is nog aan het opstarten, probeer opnieuw"}`.
  - `import` with `{"account", "paths"}` → re-scan + re-classify (never trust client status); for each selected path: skip non-selectable; skip child whose Bambu-user parent is neither selected, in the bundle, nor in Orca with reason `"Overgeslagen: parent '{p}' niet mee geselecteerd"`; otherwise convert + write. Then `reload_bundle`. Reply `{"type":"import","results":[{"name","kind","ok","reason","dropped":[...]}], "reload":{"ok","message"}}`.
  - `clear` → `clear_bundle` + `reload_bundle` → `{"type":"clear","ok","message"}`.

- [ ] **Step 1: Failing tests** with a `FakeHost` (`preset_bundle()` returns fake bundle; `preset.bundles` with `list_ids`/`reload_local` recording calls) and tmp Orca/Bambu dirs:
  - `test_scan_returns_items_and_accounts`
  - `test_scan_host_not_ready` (Review Focus 4)
  - `test_scan_no_bambu`
  - `test_import_writes_and_reloads`: file exists in bundle dir, `reload_local` called with `"bambustudio_import"`.
  - `test_import_child_without_parent_skipped` (Review Focus 3): child skipped with the exact reason, sibling ready preset still written.
  - `test_import_ignores_missing_parent_even_if_sent`
  - `test_clear`
  - `test_unknown_action`
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pytest -v` (full suite) → PASS.
- [ ] **Step 5:** Commit `feat: ImportCore message handling`.

### Task 7: Orca capability and HTML tab

**Files:**
- Modify: plugin file (capability classes inside `if orca is not None:`; `PAGE_HTML` constant)
- Test: `tests/test_page_html.py`

**Interfaces:**
- Consumes: `ImportCore`.
- Produces:
  - `class ImportPage(orca.pages.PagesPluginCapabilityBase)`: `get_name() -> "BambuStudio import"`, `get_ui() -> PAGE_HTML`, `on_message(message)` → parse (dict or JSON string) → `self.post_message(core.handle(data))`; core created lazily with `orca.host`, `orca_data_dir()`, `bambu_data_dirs()`.
  - `@orca.plugin class BambuStudioImportPlugin(orca.base)` with `register_capabilities()`: `orca.request_permissions(fs_read=bambu_data_dirs())` if the attribute exists and the list is non-empty, then `orca.register_capability(ImportPage)`.
  - `PAGE_HTML`: self-contained (no external URLs). Uses `--orca-*` CSS vars with fallbacks. Elements by id: `account`, `rescan`, `search`, `kindFilter`, `printerFilter`, `list`, `checkReady`, `checkNone`, `import` (label `Importeer (n)`), `clear` (with `confirm()`), `report`, `msg`. Posts `{action:"scan"}` on load via `window.orca.postMessage`, listens with `window.orca.onMessage`. Printer filter values from `@…` suffix of names. Rows grouped by kind (Filament / Proces / Printer), non-selectable rows disabled and greyed, status badge per status. Report panel lists per result ok/skip, reason, and a `<details>` with dropped keys.

- [ ] **Step 1: Failing tests** (static checks only; behaviour is verified in Task 8):
  - `test_html_has_required_ids`: every id above appears as `id="<id>"`.
  - `test_html_no_external_resources`: no `http://` or `https://` in `PAGE_HTML`.
  - `test_html_uses_bridge`: contains `window.orca.postMessage` and `window.orca.onMessage`.
  - `test_module_imports_without_orca`: importing the module with `orca` absent works and `ImportCore` exists.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Open `PAGE_HTML` in the browser pane with a stub `window.orca` that answers `scan` with fixture items; check grouping, filters, counter, disabled rows. `pytest -v` → PASS.
- [ ] **Step 5:** Commit `feat: Orca Pages tab UI`.

### Task 8: Install and verify in OrcaSlicer

**Files:**
- Create: `scripts/install.sh` — copies `orca_plugins/bambustudio_import/bambustudio_import.py` to `~/Library/Application Support/OrcaSlicer/orca_plugins/bambustudio_import/` (creates dir; overwrites only that file).
- Create: `README.md` (Dutch): what it does, install, use, removal (`Bundle leegmaken` + delete plugin folder).

- [ ] **Step 1:** `pytest -v` → all PASS.
- [ ] **Step 2:** Quit OrcaSlicer, run `scripts/install.sh`, start OrcaSlicer, accept the read-permission prompt for the BambuStudio folder.
- [ ] **Step 3:** Open the "BambuStudio import" tab: list matches BambuStudio (43 profiles for the account), statuses look plausible; note which P2S parents are missing.
- [ ] **Step 4:** Import two filament and one process preset → they appear in the preset dropdowns without restart; select them and slice a cube successfully. Check `<orca>/log` for `[bambustudio_import]` errors.
- [ ] **Step 5:** `Bundle leegmaken` → presets disappear; only the bundle dir was touched.
- [ ] **Step 6:** Commit `chore: install script and README`.
