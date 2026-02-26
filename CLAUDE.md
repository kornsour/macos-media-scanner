# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

macOS Photos library analyzer CLI. Finds duplicates, surfaces quality issues, provides library health insights. Read-only access via osxphotos — deletions go to a Photos album for manual review.

## Commands

```bash
# Install (editable)
pip3 install -e .            # production deps
pip3 install -e ".[dev]"     # + pytest, pytest-cov, pytest-mock

# Run
python3 -m media_scanner <command>    # always works
media-scanner <command>               # after install, if scripts dir is on PATH

# Test
pytest                       # all tests
pytest tests/test_hasher.py  # single file
pytest -k "test_groups"      # by name pattern

# No active linter/formatter enforcement — ruff cache exists but no config
```

## Architecture

```
Photos.app ──osxphotos──▶ core/scanner.py ──▶ SQLite cache ──▶ analysis commands
                              │                                       │
                         (ONLY module that                     (all read from
                          imports osxphotos)                     cached data)
                                                                      │
                                                               actions --apply
                                                                      │
                                                         PhotoKit (Swift CLI)
                                                              │ fallback
                                                          AppleScript
```

### Package layout (`src/media_scanner/`)

- **`cli/`** — Typer commands. Each file is one command registered in `cli/app.py`. Global options (`--db`, `--library`, `-v`) set via `app.callback()` → `get_config()`.
- **`core/`** — Analysis algorithms. `scanner.py` is the osxphotos boundary. `hasher.py` does SHA-256/dHash/pHash. `duplicate_finder.py` runs the multi-stage pipeline. `quality_scorer.py` ranks items within groups.
- **`data/`** — `models.py` has the `MediaItem` dataclass (33 fields), `DuplicateGroup`, `ActionRecord`, enums. `cache.py` is the `CacheDB` class (SQLite with WAL). `migrations.py` handles schema versioning (currently v2).
- **`ui/`** — Rich console output. `reviewer.py` is the interactive CLI review loop (accept/choose/keep all/skip/undo/quit). `report.py` generates static and interactive HTML reports with thumbnails. `server.py` is the local HTTP server for browser-based review with immediate merge via PhotoKit.
- **`actions/`** — `photokit.py` compiles a Swift .app bundle (`actions/swift/photos_bridge.swift` + `Info.plist`) cached at `~/.media-scanner/PhotosBridge.app`. Launched via `open --wait-apps` with file-based I/O. `applescript.py` is the fallback (batched, temp-file based). `exporter.py` copies keeper files out. Two album constants: `ALBUM_NAME` ("Media Scanner - To Delete") and `KEEPER_ALBUM_NAME` ("Media Scanner - Keepers").

### Critical invariant

**Only `core/scanner.py` imports osxphotos.** Everything else uses `MediaItem` and `CacheDB`. The `scan` command populates the cache; all other commands read from it.

### Duplicate detection pipeline

Size grouping → SHA-256 (exact) → dHash with hamming distance (near) → pHash confirmation. Videos: duration grouping → SHA-256 → ffmpeg keyframe dHash.

### Two review modes

**CLI review** (`dupes`, `similar`): Decisions stored in SQLite during interactive review. Applied separately via `actions --apply`, which creates Photos albums. Supports undo and `--clear`.

**Browser review** (`report --serve`): Local HTTP server with interactive HTML UI. Merge button applies decisions immediately via PhotoKit — adds duplicates to "Media Scanner - To Delete" album and keeper to "Media Scanner - Keepers" album. Merge All button processes all visible groups sequentially. Groups are removed from the cache on merge. `server.py` handles the HTTP endpoints; `report.py` generates the HTML with embedded JS for the interactive UI.

### PhotoKit bridge

Swift source at `actions/swift/photos_bridge.swift` is compiled on first use via `swiftc` into a `.app` bundle at `~/.media-scanner/PhotosBridge.app`. The .app bundle is required because macOS 14+ silently denies Photos TCC access to plain CLI tools. Key details:
- `Info.plist` with `NSPhotoLibraryUsageDescription` + `CFBundleExecutable` + `LSUIElement`
- Launched via `open --wait-apps` so it runs in its own GUI context (needed for TCC prompts)
- Uses file-based I/O (`--stdin-file`, `--stdout-file`, `--stderr-file`) since `open` doesn't pipe stdio
- NSApplication with `.accessory` activation policy drives the run loop for auth prompts
- First run triggers macOS permission dialog; if denied, user toggles PhotosBridge ON in System Settings → Privacy & Security → Photos
- Returns dict with `{"success": bool, "error": str | None}` — detects `auth_denied` vs other failures

## Testing

- 250 tests across 20 files, all pure unit tests (no Photos library needed)
- Fixtures in `tests/conftest.py`: `sample_item(**overrides)`, `make_group()`, `config`, `cache`, `populated_cache`
- PhotoKit tests mock `_run_bridge`, AppleScript tests mock `subprocess.run`, never call real osxphotos

## Key files

| File | Why it matters |
|------|---------------|
| `cli/app.py` | Typer app setup, global options, command registration |
| `core/scanner.py` | osxphotos boundary — `photo_to_media_item()`, `scan_library()` |
| `data/cache.py` | `CacheDB` — all DB queries, batch upserts, duplicate group storage |
| `data/models.py` | `MediaItem`, `DuplicateGroup`, `ActionRecord`, enums |
| `data/migrations.py` | Schema versioning (add new migrations here) |
| `core/quality_scorer.py` | 7-factor weighted scoring, `rank_group()` |
| `ui/reviewer.py` | Interactive CLI review session with undo stack |
| `ui/report.py` | HTML report generator (static + interactive modes), embedded CSS/JS |
| `ui/server.py` | Local HTTP server for browser review — merge, thumbnails, PhotoKit integration |
| `cli/report.py` | `report` command — `--serve` for interactive, static HTML otherwise |
| `actions/photokit.py` | .app bundle compilation, `open --wait-apps` launch, auth error detection |
| `actions/swift/Info.plist` | .app bundle identity — `CFBundleExecutable`, `NSPhotoLibraryUsageDescription` |
| `config.py` | `Config` dataclass — thresholds, quality weights, paths |
