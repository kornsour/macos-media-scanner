"""Main Typer application with global options."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from media_scanner.config import Config

app = typer.Typer(
    name="media-scanner",
    help="Find duplicates and analyze your macOS Photos library.",
    no_args_is_help=True,
)

# Global state shared across commands
_config = Config()


def get_config() -> Config:
    return _config


@app.callback()
def main(
    db_path: Annotated[
        Optional[Path],
        typer.Option("--db", help="Path to the cache database."),
    ] = None,
    library: Annotated[
        Optional[Path],
        typer.Option("--library", help="Path to Photos library."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose output."),
    ] = False,
    workers: Annotated[
        Optional[int],
        typer.Option("--workers", "-w", help="Max parallel workers for hashing (default: auto)."),
    ] = None,
) -> None:
    """Global options applied to all commands."""
    if db_path:
        _config.db_path = db_path
    if library:
        _config.photos_library = library
    _config.verbose = verbose
    if workers is not None:
        _config.max_workers = max(1, workers)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    _config.ensure_dirs()


# Import and register subcommands
from media_scanner.cli.scan import scan  # noqa: E402
from media_scanner.cli.dupes import dupes  # noqa: E402
from media_scanner.cli.stats import stats  # noqa: E402
from media_scanner.cli.similar import similar  # noqa: E402
from media_scanner.cli.missing import missing_meta  # noqa: E402
from media_scanner.cli.bigfiles import big_files  # noqa: E402
from media_scanner.cli.timeline import timeline  # noqa: E402
from media_scanner.cli.quality import quality  # noqa: E402
from media_scanner.cli.actions import actions  # noqa: E402
from media_scanner.cli.report import review  # noqa: E402
from media_scanner.cli.short_videos import short_videos  # noqa: E402
from media_scanner.cli.small_photos import small_photos  # noqa: E402
from media_scanner.cli.lowres_videos import lowres_videos  # noqa: E402
from media_scanner.cli.rapid_shots import rapid_shots  # noqa: E402

app.command()(scan)
app.command()(dupes)
app.command()(stats)
app.command(name="similar")(similar)
app.command(name="missing-meta")(missing_meta)
app.command(name="big-files")(big_files)
app.command()(timeline)
app.command()(quality)
app.command()(actions)
app.command()(review)
app.command(name="short-videos")(short_videos)
app.command(name="small-photos")(small_photos)
app.command(name="lowres-videos")(lowres_videos)
app.command(name="rapid-shots")(rapid_shots)
