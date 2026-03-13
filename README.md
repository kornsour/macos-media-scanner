# macos-media-scanner

A CLI tool for analyzing and cleaning up large macOS Photos libraries. Finds duplicate and near-duplicate photos/videos, surfaces quality issues, and provides library health insights.

Built on [osxphotos](https://github.com/RhetTbull/osxphotos) (read-only access to your Photos library) — no photos are ever modified or deleted directly. Instead, items you mark for deletion are collected into a Photos album for manual review.

## Prerequisites

- macOS 14+ with Photos.app
- Python 3.11+
- ffmpeg (for video duplicate detection)
- Xcode Command Line Tools (required for PhotoKit album creation — highly recommended)

```bash
brew install ffmpeg
xcode-select --install
```

## Installation

```bash
git clone https://github.com/kornsour/macos-media-scanner.git
python3 -m venv .venv
source .venv/bin/activate
cd media-scanner
pip3 install -e .
```

### PhotoKit setup (first run)

The first time any command creates a Photos album (e.g., `dupes --auto`, `actions --apply`, or merging in the browser UI), the tool compiles a Swift helper app and macOS will show a **Photos access permission prompt**. Click **Allow** to grant access.

If you missed the prompt or denied it, go to **System Settings → Privacy & Security → Photos** and toggle **PhotosBridge** on.

> **Why a .app bundle?** macOS 14+ silently denies Photos access to plain CLI tools. The Swift helper is packaged as a minimal `.app` at `~/.media-scanner/PhotosBridge.app` so macOS will show the permission dialog. If Xcode Command Line Tools aren't installed, album creation falls back to AppleScript automatically (slower but works without any setup).

After installing, the `media-scanner` command is placed in Python's scripts directory which may not be on your PATH. You can either:

**Option A** — Add Python's scripts directory to your PATH (one-time setup):

```bash
# Add to ~/.zshrc to make it permanent
export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:$PATH"
```

**Option B** — Run via `python3 -m` (works immediately, no PATH changes):

```bash
python3 -m media_scanner <command>
```

Both methods are equivalent. The examples below use `media-scanner` but you can substitute `python3 -m media_scanner` anywhere.

## Quick Start

### Manual Review

```bash
# 1. Scan your Photos library (caches metadata locally — takes ~1 min for 50K items)
media-scanner scan

# 2. See what you're working with
media-scanner stats

# 3. Find exact duplicates (opens browser review UI automatically)
media-scanner dupes --exact
```

To continue reviewing after closing the UI tool in the browser, you can run `media-scanner review` to reopen the UI tool with your remaining decisions needed loaded from the cache.

### Automatic Review

Alternatively, you can skip interactive review and let the quality scorer decide automatically (chooses best photo, merges metadata, and drops photos into keep and delete albums immediately):

```bash
media-scanner dupes --exact --auto
```

### Rescanning and Cache

`media-scanner scan` refreshes media items from the Photos library but does not clear duplicate groups or pending actions. To reset duplicate analysis, re-run `media-scanner dupes`. To clear pending actions, use `media-scanner actions --clear`.

## Commands

### `scan`

Reads your entire Photos library via osxphotos and caches metadata in a local SQLite database (`~/.media-scanner/cache.db`). This is the only command that opens your Photos library — all other commands read from the cache, so they run instantly.

```bash
media-scanner scan
media-scanner scan --library ~/Pictures/Photos\ Library.photoslibrary
```

### `dupes`

Multi-stage duplicate detection. Automatically opens a browser-based review UI when duplicates are found.

```bash
media-scanner dupes --exact          # SHA-256 exact matches only
media-scanner dupes --near           # Perceptual hashing (dHash + pHash)
media-scanner dupes --exact --near   # Both
media-scanner dupes --videos         # Include video duplicates
media-scanner dupes --auto           # Auto-accept all quality-scorer recommendations
media-scanner dupes --limit 50       # Review only the first 50 groups
media-scanner dupes --port 9000      # Custom port for the review server
```

**Pipeline Flow:**

| Stage | Method                     | What it catches                              |
| ----- | -------------------------- | -------------------------------------------- |
| 1     | Group by file size         | Eliminates ~70-80% of comparisons            |
| 2     | SHA-256 within size groups | Exact byte-for-byte duplicates               |
| 3     | dHash (perceptual)         | Re-exported, re-compressed, slightly cropped |
| 4     | pHash confirmation         | Reduces false positives from stage 3         |

For videos: groups by duration (within 2 seconds), then SHA-256, then ffmpeg keyframe hashing.

### `stats`

Library overview with health summary.

```bash
media-scanner stats
```

Shows total counts by type, file type distribution, GPS coverage, missing dates, screenshots, and more.

### `similar`

Find visually similar photos that aren't duplicates — same scene from a different angle, burst shots, etc. Uses a wider perceptual hash threshold than `dupes --near`.

```bash
media-scanner similar
media-scanner similar --auto           # Auto-accept recommendations
media-scanner similar --no-review --limit 100
```

**CLI Interactive Review:**

```text
┌─ Similar Group 42/137 ───────────────────────────────────────┐
│                                                               │
│  #   Filename          Size     Res        Date       Score   │
│  [1] IMG_1234.HEIC    4.2 MB   4032x3024  2024-03-15  0.87  │ ← recommended
│  [2] IMG_1235.HEIC    3.8 MB   4032x3024  2024-03-15  0.85  │
│                                                               │
│  [a]ccept  [c]hoose  [k]eep all  [s]kip  [u]ndo  [q]uit    │
└───────────────────────────────────────────────────────────────┘
```

- **accept** — keep the recommended item, mark the rest for deletion
- **choose** — pick which item to keep yourself
- **keep all** — don't delete any in this group
- **skip** — decide later
- **undo** — go back to the previous group

### `missing-meta`

Find photos missing dates, GPS coordinates, faces, or keywords.

```bash
media-scanner missing-meta              # Summary counts
media-scanner missing-meta --show       # Show individual items
media-scanner missing-meta --limit 30   # Limit items per category
```

### `big-files`

Identify the largest files in your library.

```bash
media-scanner big-files
media-scanner big-files --limit 100
```

### `timeline`

Visualize your photo history and find gaps.

```bash
media-scanner timeline                # Monthly breakdown with bar chart
media-scanner timeline --by year      # Yearly breakdown
media-scanner timeline --no-gaps      # Skip gap detection
```

### `quality`

Surface low-quality photos (low resolution, low Apple aesthetic score).

```bash
media-scanner quality
media-scanner quality --limit 100
media-scanner quality --screenshots   # Include screenshots
```

### `review`

Open the interactive browser UI to review duplicate groups found by `dupes`. The interactive server is the default — use `--static` if you want an HTML file instead.

```bash
media-scanner review                    # Interactive review server (default)
media-scanner review --port 9000        # Custom port
media-scanner review --type exact       # Filter by match type (exact, near, all)
media-scanner review --limit 200        # Max groups to include (default: all)
media-scanner review --static           # Generate a static HTML report file
media-scanner review --static -o my-report.html  # Custom output file
```

The interactive browser UI provides:

- Paginated view (50 groups per page) for fast loading even with thousands of groups
- Click any photo to select it as the keeper
- **Merge** button per group — immediately adds duplicates to the "Media Scanner - To Delete" album and the keeper to the "Media Scanner - Keepers" album via PhotoKit
- **Merge All on Page** button — merges all groups on the current page sequentially, then auto-reloads to show the next batch
- Merged groups slide away with a smooth animation
- Metadata (date, GPS) is automatically transferred from duplicates to the keeper before merging

### `actions`

Manage and apply the decisions you made during CLI review.

```bash
media-scanner actions --list    # See pending decisions
media-scanner actions --apply   # Create "Media Scanner - To Delete" album in Photos.app
media-scanner actions --clear   # Discard all pending decisions
media-scanner actions --export ~/Desktop/keepers  # Copy keepers to a folder
```

`--apply` uses PhotoKit (via a compiled Swift .app bundle) for fast, indexed UUID lookups. The app is compiled on first use and cached at `~/.media-scanner/PhotosBridge.app`. If Xcode Command Line Tools aren't installed, it falls back to AppleScript automatically. Keepers are also added to a "Media Scanner - Keepers" album for easy verification.

## How It Works

### Architecture

```text
Photos.app ──osxphotos──▶ scanner.py ──▶ SQLite cache ──▶ analysis commands
                              │                               │
                         (only module                    (all read from
                         that touches                     cached data)
                         Photos library)
                                                              │
                                                              ▼
                                                     actions --apply
                                                              │
                                               PhotoKit (Swift CLI) ──▶ Photos album
                                                   │ fallback
                                               AppleScript ──▶ Photos album
```

Only `core/scanner.py` imports osxphotos. Everything else works with the `MediaItem` dataclass and the SQLite cache, so analysis commands are fast regardless of library size.

Album creation uses a compiled Swift app (`.app` bundle) that talks to PhotoKit directly — `fetchAssets(withLocalIdentifiers:)` does indexed O(k) lookups instead of AppleScript's O(n\*m) iteration over every item in the library. For a 200K-item library with 5K deletions, this is orders of magnitude faster. The `.app` bundle is required because macOS 14+ silently denies Photos access to plain CLI tools.

### Quality Scoring

When duplicates are found, each item is scored to recommend which to keep:

| Factor           | Weight | Logic                               |
| ---------------- | ------ | ----------------------------------- |
| Resolution       | 30%    | Higher pixel count is better        |
| Format           | 20%    | RAW > HEIC > JPEG > GIF             |
| File size        | 15%    | Larger = less compression           |
| Metadata         | 10%    | GPS, faces, keywords, albums        |
| Date originality | 10%    | Earliest date = likely the original |
| Apple score      | 10%    | Built-in aesthetic quality score    |
| Edit status      | 5%     | Edited = user invested effort       |

### Safety

- **Read-only** — osxphotos never modifies your Photos library
- **Interactive merge** — browser-based review (default) applies decisions immediately via PhotoKit
- **Two-phase actions** — CLI review decisions (via `similar`) are stored in SQLite, then applied separately via `actions --apply`
- **Album-based deletion** — duplicates go to "Media Scanner - To Delete", keepers go to "Media Scanner - Keepers" for verification
- **Automatic fallback** — PhotoKit is preferred for speed, but falls back to AppleScript if Xcode tools aren't available or Photos access is denied
- **Undo** — you can undo during CLI review and `--clear` pending actions at any time

## Global Options

```bash
media-scanner --db ~/custom/path.db scan     # Custom cache location
media-scanner --library /path/to/lib scan    # Specific Photos library
media-scanner -v stats                       # Verbose output
```

## Project Structure

```text
src/media_scanner/
├── cli/           # Typer commands (scan, dupes, stats, similar, etc.)
├── core/          # Analysis logic (scanner, hasher, duplicate finder, auto resolver)
├── data/          # Data models, SQLite cache, migrations
├── ui/            # Rich console, progress bars, interactive reviewer, HTML report, review server
└── actions/       # PhotoKit bridge, AppleScript fallback, action log, file exporter
    └── swift/     # Swift source for PhotoKit CLI (compiled on first use)
```
