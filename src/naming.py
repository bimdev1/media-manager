"""File and folder naming utilities.

Handles:
- Scene-release name parsing (Artist.-.Title.YEAR.FLAC-GROUP)
- Compliant folder structure generation (Album Artist / Album (Year) [Format])
- Compliant file naming (DD - Title.ext or D-DD - Title.ext)
"""

import re
from dataclasses import dataclass
from pathlib import PurePath

from .metadata import TrackMetadata
from .config import settings


# Characters not allowed in filenames on various filesystems
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Scene release patterns
SCENE_PATTERN = re.compile(
    r'^(.+?)[\.\-_]+(.+?)[\.\-_]+(\d{4})[\.\-_]+(FLAC|MP3|AAC|OGG|WEB|CD|VINYL|SACD|HDCD)'
    r'.*?[\.\-_]*(\d+[\.\-_]*bit)?[\.\-_]*(\d+[\.\-_]*k?hz)?.*?[-_]([A-Z0-9]+)?$',
    re.IGNORECASE
)
# Dots-as-spaces pattern (common in scene releases)
DOTS_AS_SPACES = re.compile(r'\.(?=[A-Za-z])')


@dataclass
class NamingResult:
    """Result of name generation."""

    folder_path: str  # Relative path: "Album Artist/Album (Year) [Format]"
    file_name: str    # Just the filename: "01 - Track Title.flac"
    full_path: str    # Combined: "Album Artist/Album (Year) [Format]/01 - Track Title.flac"
    changes: list[str]  # Description of what would change


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """Remove or replace invalid filename characters."""
    # Replace invalid chars
    name = INVALID_CHARS.sub(replacement, name)
    # Remove leading/trailing spaces and dots (Windows issues)
    name = name.strip(" .")
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    return name


def clean_scene_name(name: str) -> str:
    """
    Convert scene-release naming to clean format.

    Examples:
        "Artist.-.Title.2024.FLAC-GROUP" -> "Artist - Title"
        "01.Artist.-.Track.Name.flac" -> "01 Artist - Track Name"
    """
    # Replace dots with spaces (but preserve file extension dots)
    ext = ""
    if "." in name:
        parts = name.rsplit(".", 1)
        if len(parts[1]) <= 4 and parts[1].lower() in ("mp3", "flac", "m4a", "ogg", "opus", "wav"):
            name = parts[0]
            ext = f".{parts[1]}"

    # Replace dots and underscores with spaces
    cleaned = DOTS_AS_SPACES.sub(" ", name)
    cleaned = cleaned.replace("_", " ")

    # Remove common scene group suffixes
    cleaned = re.sub(r'\s*[-_]\s*[A-Z0-9]{2,10}\s*$', '', cleaned, flags=re.IGNORECASE)

    # Remove format tags from name
    cleaned = re.sub(
        r'\s*(FLAC|MP3|AAC|OGG|WEB|CD|VINYL|SACD|HDCD|320|256|192|V0|V2|\d+bit|\d+kHz)\s*',
        ' ',
        cleaned,
        flags=re.IGNORECASE
    )

    # Clean up multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned + ext


def generate_folder_name(meta: TrackMetadata) -> str:
    """
    Generate compliant folder path for an album.

    Format: "Album Artist/Album (Year) [Format]"
    Compilations go to: "Compilations/Album (Year) [Format]"
    """
    # Determine artist folder
    if meta.is_compilation:
        artist_folder = settings.compilations_folder
    else:
        artist_folder = sanitize_filename(meta.album_artist or meta.artist or "Unknown Artist")

    # Build album folder name
    album = sanitize_filename(meta.album or "Unknown Album")

    # Add year if available
    if meta.year:
        album_with_year = f"{album} ({meta.year})"
    else:
        album_with_year = album

    # Add format tag
    format_tag = meta.format_tag
    if format_tag:
        album_folder = f"{album_with_year} [{format_tag}]"
    else:
        album_folder = album_with_year

    return f"{artist_folder}/{album_folder}"


def generate_track_filename(meta: TrackMetadata) -> str:
    """
    Generate compliant track filename.

    Format: "DD - Title.ext" for single disc
    Format: "D-DD - Title.ext" for multi-disc
    """
    # Get extension from original path
    if meta.file_path:
        ext = PurePath(meta.file_path).suffix.lower()
    else:
        ext = ".flac"  # Default

    # Sanitize title
    title = sanitize_filename(meta.title or "Unknown Track")

    # Format track number
    track_num = meta.track_number or 1

    # Multi-disc handling
    if meta.total_discs > 1 or meta.disc_number > 1:
        disc = meta.disc_number or 1
        filename = f"{disc}-{track_num:02d} - {title}{ext}"
    else:
        filename = f"{track_num:02d} - {title}{ext}"

    return filename


def generate_lrc_filename(track_filename: str) -> str:
    """Generate LRC filename from track filename."""
    stem = PurePath(track_filename).stem
    return f"{stem}.lrc"


def analyze_current_name(current_path: str, meta: TrackMetadata) -> NamingResult:
    """
    Analyze current path against ideal naming and generate result.

    Args:
        current_path: Current full path to file
        meta: Extracted metadata for the file

    Returns:
        NamingResult with proposed changes
    """
    ideal_folder = generate_folder_name(meta)
    ideal_filename = generate_track_filename(meta)
    ideal_full = f"{ideal_folder}/{ideal_filename}"

    # Parse current path to compare
    path = PurePath(current_path.replace("\\", "/"))
    current_filename = path.name
    # Get the album folder (parent) and artist folder (grandparent)
    current_album_folder = path.parent.name if path.parent else ""
    current_artist_folder = path.parent.parent.name if path.parent.parent else ""
    current_relative = f"{current_artist_folder}/{current_album_folder}/{current_filename}"

    changes = []

    # Check filename
    if current_filename != ideal_filename:
        changes.append(f"Rename file: '{current_filename}' → '{ideal_filename}'")

    # Check folder structure
    ideal_artist, ideal_album = ideal_folder.split("/", 1)
    if current_artist_folder != ideal_artist:
        changes.append(f"Move to artist: '{current_artist_folder}' → '{ideal_artist}'")
    if current_album_folder != ideal_album:
        changes.append(f"Rename album folder: '{current_album_folder}' → '{ideal_album}'")

    return NamingResult(
        folder_path=ideal_folder,
        file_name=ideal_filename,
        full_path=ideal_full,
        changes=changes,
    )


def detect_naming_issues(current_filename: str) -> list[str]:
    """
    Detect common naming issues without metadata.

    Returns list of detected issues.
    """
    issues = []

    # Scene release pattern
    if re.search(r'\.[A-Za-z0-9]+\.[A-Z0-9]{2,10}-[A-Z0-9]+\.', current_filename, re.IGNORECASE):
        issues.append("Scene release naming detected")

    # Dots instead of spaces
    if re.search(r'\.[A-Za-z].*\.[A-Za-z]', current_filename):
        issues.append("Uses dots instead of spaces")

    # Track number in wrong position
    if re.match(r'^[A-Za-z].*\s\d{1,2}[-_.]', current_filename):
        issues.append("Track number not at start")

    # Artist name in filename
    if " - " in current_filename and re.match(r'^\d{1,2}', current_filename):
        # This is probably fine: "01 - Title.flac"
        pass
    elif " - " in current_filename:
        # Might have artist: "Artist - Title.flac"
        issues.append("May contain artist name (should be in folder)")

    # Group tag at end
    if re.search(r'-[A-Z0-9]{2,10}\.[a-z]+$', current_filename, re.IGNORECASE):
        issues.append("Contains release group tag")

    return issues
