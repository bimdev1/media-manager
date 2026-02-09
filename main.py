#!/usr/bin/env python3
"""
Media Manager CLI.

Usage:
    media-manager scan [--dry-run] [--component COMPONENT]
    media-manager fix [--dry-run] [--component COMPONENT]
"""

import json
import logging
import sys
from enum import Enum
from io import BytesIO
from pathlib import Path

import click

from src.config import settings
from src.smb_client import SMBClient, UndoLog
from src.metadata import extract_metadata
from src.naming import generate_folder_name, generate_track_filename, analyze_current_name
from src.artwork import ArtworkFetcher, should_replace_cover
from src.lyrics import LyricsFetcher, format_lrc_content

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class Component(str, Enum):
    """Processing component."""

    NAMING = "naming"
    ARTWORK = "artwork"
    LYRICS = "lyrics"
    ALL = "all"


@click.group()
@click.option("--dry-run", is_flag=True, help="Preview changes without modifying files")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx: click.Context, dry_run: bool, verbose: bool) -> None:
    """Media Manager - Automated music library maintenance."""
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    settings.dry_run = dry_run

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.option(
    "--component",
    "-c",
    type=click.Choice(["naming", "artwork", "lyrics", "all"]),
    default="all",
    help="Component to scan",
)
@click.option("--limit", "-n", type=int, default=0, help="Limit number of albums to scan (0=all)")
@click.pass_context
def scan(ctx: click.Context, component: str, limit: int) -> None:
    """Scan library and report issues without making changes."""
    dry_run = ctx.obj.get("dry_run", True)
    logger.info(f"Scanning library (component={component}, dry_run={dry_run})")

    client = SMBClient()
    artwork_fetcher = ArtworkFetcher() if component in ("artwork", "all") else None
    lyrics_fetcher = LyricsFetcher() if component in ("lyrics", "all") else None

    stats = {
        "albums_scanned": 0,
        "tracks_scanned": 0,
        "naming_issues": 0,
        "missing_covers": 0,
        "low_quality_covers": 0,
        "missing_lyrics": 0,
    }

    try:
        albums_processed = 0

        # Walk through artist folders
        for artist_path, album_dirs, _ in client.walk(max_depth=1):
            if artist_path == client.root_path:
                continue

            for album_dir in album_dirs:
                if limit > 0 and albums_processed >= limit:
                    break

                album_path = f"{artist_path}\\{album_dir}"
                logger.info(f"Scanning: {album_dir}")

                # Get tracks in album
                try:
                    items = client.scan_dir(album_path)
                except OSError as e:
                    logger.warning(f"Cannot access {album_path}: {e}")
                    continue

                album_tracks = []
                cover_exists = False
                cover_data = None

                for item in items:
                    if item.name.lower() in ("cover.jpg", "cover.png", "folder.jpg"):
                        cover_exists = True
                        try:
                            cover_data = client.read_file(item.path)
                        except OSError:
                            pass
                    elif any(item.name.lower().endswith(ext) for ext in settings.audio_extensions):
                        album_tracks.append(item)

                if not album_tracks:
                    continue

                stats["albums_scanned"] += 1
                albums_processed += 1

                # Analyze first track for album-level metadata
                try:
                    first_track = album_tracks[0]
                    audio_data = client.read_file(first_track.path)
                    meta = extract_metadata(audio_data, first_track.path)
                except OSError as e:
                    logger.warning(f"Cannot read {first_track.name}: {e}")
                    continue

                if not meta:
                    logger.warning(f"Cannot parse metadata: {first_track.name}")
                    continue

                # Check naming
                if component in ("naming", "all"):
                    result = analyze_current_name(first_track.path, meta)
                    if result.changes:
                        stats["naming_issues"] += 1
                        for change in result.changes:
                            logger.info(f"  [NAMING] {change}")

                # Check artwork
                if component in ("artwork", "all"):
                    if not cover_exists:
                        stats["missing_covers"] += 1
                        logger.info(f"  [ARTWORK] No cover.jpg found")
                    elif cover_data:
                        should_replace, reason = should_replace_cover(cover_data)
                        if should_replace:
                            stats["low_quality_covers"] += 1
                            logger.info(f"  [ARTWORK] {reason}")

                # Check lyrics for each track
                if component in ("lyrics", "all"):
                    for track in album_tracks:
                        lrc_name = Path(track.name).stem + ".lrc"
                        lrc_path = f"{album_path}\\{lrc_name}"
                        if not client.exists(lrc_path):
                            stats["missing_lyrics"] += 1
                            stats["tracks_scanned"] += 1

                if limit > 0 and albums_processed >= limit:
                    break

        # Print summary
        click.echo("\n" + "=" * 50)
        click.echo("SCAN SUMMARY")
        click.echo("=" * 50)
        for key, value in stats.items():
            click.echo(f"  {key.replace('_', ' ').title()}: {value}")

    except Exception as e:
        logger.error(f"Scan failed: {e}")
        raise


@cli.command()
@click.option(
    "--component",
    "-c",
    type=click.Choice(["naming", "artwork", "lyrics", "all"]),
    default="all",
    help="Component to fix",
)
@click.option("--limit", "-n", type=int, default=0, help="Limit number of albums to process (0=all)")
@click.pass_context
def fix(ctx: click.Context, component: str, limit: int) -> None:
    """Fix issues in the library."""
    dry_run = ctx.obj.get("dry_run", True)
    logger.info(f"Fixing library (component={component}, dry_run={dry_run})")

    if dry_run:
        click.echo("DRY RUN MODE - No changes will be made")

    client = SMBClient()
    undo_log = UndoLog()
    artwork_fetcher = ArtworkFetcher() if component in ("artwork", "all") else None
    lyrics_fetcher = LyricsFetcher() if component in ("lyrics", "all") else None

    stats = {
        "albums_processed": 0,
        "tracks_processed": 0,
        "covers_added": 0,
        "covers_replaced": 0,
        "lyrics_added": 0,
        "renames": 0,
        "errors": 0,
    }

    try:
        albums_processed = 0

        for artist_path, album_dirs, _ in client.walk(max_depth=1):
            if artist_path == client.root_path:
                continue

            for album_dir in album_dirs:
                if limit > 0 and albums_processed >= limit:
                    break

                album_path = f"{artist_path}\\{album_dir}"
                logger.info(f"Processing: {album_dir}")

                try:
                    items = list(client.scan_dir(album_path))
                except OSError as e:
                    logger.warning(f"Cannot access {album_path}: {e}")
                    continue

                album_tracks = []
                cover_path = None
                cover_data = None

                for item in items:
                    if item.name.lower() in ("cover.jpg", "cover.png", "folder.jpg"):
                        cover_path = item.path
                        try:
                            cover_data = client.read_file(item.path)
                        except OSError:
                            pass
                    elif any(item.name.lower().endswith(ext) for ext in settings.audio_extensions):
                        album_tracks.append(item)

                if not album_tracks:
                    continue

                stats["albums_processed"] += 1
                albums_processed += 1

                # Get metadata from first track
                try:
                    first_track = album_tracks[0]
                    audio_data = client.read_file(first_track.path)
                    meta = extract_metadata(audio_data, first_track.path)
                except OSError as e:
                    logger.warning(f"Cannot read {first_track.name}: {e}")
                    stats["errors"] += 1
                    continue

                if not meta:
                    logger.warning(f"Cannot parse metadata: {first_track.name}")
                    stats["errors"] += 1
                    continue

                # Fix artwork
                if component in ("artwork", "all") and artwork_fetcher:
                    should_replace, reason = should_replace_cover(cover_data)
                    if should_replace:
                        logger.info(f"  Fetching cover ({reason})")
                        cover = artwork_fetcher.fetch(meta)
                        if cover:
                            target_path = f"{album_path}\\cover.jpg"
                            if not dry_run:
                                undo_log.log_write(target_path, cover_data is not None, len(cover_data) if cover_data else 0)
                                client.write_file(target_path, cover.data)
                            if cover_data:
                                stats["covers_replaced"] += 1
                            else:
                                stats["covers_added"] += 1
                            logger.info(f"    Saved cover.jpg ({cover.width}x{cover.height})")
                        else:
                            logger.info(f"    No cover found online")

                # Fix lyrics
                if component in ("lyrics", "all") and lyrics_fetcher:
                    for track in album_tracks:
                        try:
                            track_audio = client.read_file(track.path)
                            track_meta = extract_metadata(track_audio, track.path)
                        except OSError:
                            continue

                        if not track_meta:
                            continue

                        lrc_name = Path(track.name).stem + ".lrc"
                        lrc_path = f"{album_path}\\{lrc_name}"

                        if not client.exists(lrc_path):
                            lyrics = lyrics_fetcher.fetch(track_meta)
                            if lyrics and (lyrics.synced_lyrics or lyrics.plain_lyrics):
                                lrc_content = format_lrc_content(lyrics)
                                if lrc_content and not dry_run:
                                    undo_log.log_write(lrc_path, False, 0)
                                    client.write_text(lrc_path, lrc_content)
                                stats["lyrics_added"] += 1
                                lyrics_type = "synced" if lyrics.synced_lyrics else "plain"
                                logger.info(f"    Added {lrc_name} ({lyrics_type})")

                        stats["tracks_processed"] += 1

                if limit > 0 and albums_processed >= limit:
                    break

        # Save undo log
        if not dry_run:
            undo_log.save()

        # Print summary
        click.echo("\n" + "=" * 50)
        click.echo("FIX SUMMARY")
        click.echo("=" * 50)
        for key, value in stats.items():
            click.echo(f"  {key.replace('_', ' ').title()}: {value}")

    except Exception as e:
        logger.error(f"Fix failed: {e}")
        raise


@cli.command()
def status() -> None:
    """Show library status and configuration."""
    click.echo("Media Manager Configuration")
    click.echo("=" * 40)
    click.echo(f"SMB Server: {settings.smb_server}")
    click.echo(f"SMB Share: {settings.smb_share}")
    click.echo(f"Username: {settings.smb_username}")
    click.echo(f"Cover min dimension: {settings.cover_min_dimension}px")
    click.echo(f"Cover min size: {settings.cover_min_size // 1024}KB")
    click.echo(f"Dry run: {settings.dry_run}")

    # Test connection
    click.echo("\nTesting SMB connection...")
    try:
        client = SMBClient()
        items = client.list_dir()
        click.echo(f"✓ Connected - found {len(items)} top-level items")
    except Exception as e:
        click.echo(f"✗ Connection failed: {e}")


@cli.command()
@click.option(
    "--interval",
    "-i",
    type=int,
    default=300,
    help="Poll interval in seconds (default: 300 = 5 min)",
)
@click.option(
    "--component",
    "-c",
    type=click.Choice(["naming", "artwork", "lyrics", "all"]),
    default="all",
    help="Component to process on new files",
)
@click.pass_context
def watch(ctx: click.Context, interval: int, component: str) -> None:
    """Watch for new files and process them automatically."""
    from src.watcher import DirectoryWatcher

    dry_run = ctx.obj.get("dry_run", False)
    logger.info(f"Starting watch mode (interval={interval}s, component={component}, dry_run={dry_run})")

    client = SMBClient()
    undo_log = UndoLog()
    artwork_fetcher = ArtworkFetcher() if component in ("artwork", "all") else None
    lyrics_fetcher = LyricsFetcher() if component in ("lyrics", "all") else None

    def process_albums(album_paths: list[str]) -> None:
        """Process affected albums when changes detected."""
        for album_path in album_paths:
            logger.info(f"Processing changed album: {album_path.split(chr(92))[-1]}")

            try:
                items = list(client.scan_dir(album_path))
            except OSError as e:
                logger.warning(f"Cannot access {album_path}: {e}")
                continue

            album_tracks = []
            cover_data = None

            for item in items:
                if item.name.lower() in ("cover.jpg", "cover.png", "folder.jpg"):
                    try:
                        cover_data = client.read_file(item.path)
                    except OSError:
                        pass
                elif any(item.name.lower().endswith(ext) for ext in settings.audio_extensions):
                    album_tracks.append(item)

            if not album_tracks:
                continue

            # Get metadata from first track
            try:
                first_track = album_tracks[0]
                audio_data = client.read_file(first_track.path)
                meta = extract_metadata(audio_data, first_track.path)
            except OSError:
                continue

            if not meta:
                continue

            # Process artwork
            if component in ("artwork", "all") and artwork_fetcher:
                from src.artwork import should_replace_cover

                should_replace, reason = should_replace_cover(cover_data)
                if should_replace:
                    cover = artwork_fetcher.fetch(meta)
                    if cover and not dry_run:
                        target_path = f"{album_path}\\cover.jpg"
                        undo_log.log_write(target_path, cover_data is not None, len(cover_data) if cover_data else 0)
                        client.write_file(target_path, cover.data)
                        logger.info(f"  Added cover.jpg ({cover.width}x{cover.height})")

            # Process lyrics
            if component in ("lyrics", "all") and lyrics_fetcher:
                for track in album_tracks:
                    try:
                        track_audio = client.read_file(track.path)
                        track_meta = extract_metadata(track_audio, track.path)
                    except OSError:
                        continue

                    if not track_meta:
                        continue

                    lrc_name = Path(track.name).stem + ".lrc"
                    lrc_path = f"{album_path}\\{lrc_name}"

                    if not client.exists(lrc_path):
                        lyrics = lyrics_fetcher.fetch(track_meta)
                        if lyrics and (lyrics.synced_lyrics or lyrics.plain_lyrics) and not dry_run:
                            lrc_content = format_lrc_content(lyrics)
                            undo_log.log_write(lrc_path, False, 0)
                            client.write_text(lrc_path, lrc_content)
                            logger.info(f"  Added {lrc_name}")

        if not dry_run:
            undo_log.save()

    watcher = DirectoryWatcher(
        client=client,
        poll_interval=interval,
        process_callback=process_albums,
    )

    click.echo(f"Watching {settings.smb_root}")
    click.echo(f"Poll interval: {interval}s")
    click.echo("Press Ctrl+C to stop")
    click.echo()

    try:
        watcher.run()
    except KeyboardInterrupt:
        watcher.stop()
        click.echo("\nWatcher stopped.")


if __name__ == "__main__":
    cli()
