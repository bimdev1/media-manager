"""Audio metadata extraction and manipulation using mutagen."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus

from .config import settings


@dataclass
class TrackMetadata:
    """Normalized metadata for a single audio track."""

    # Core identifiers
    title: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""

    # Track info
    track_number: int = 0
    total_tracks: int = 0
    disc_number: int = 1
    total_discs: int = 1

    # Additional
    year: int = 0
    genre: str = ""
    duration: float = 0.0  # seconds

    # Format info
    format: str = ""  # "FLAC", "MP3 320", "AAC", etc.
    bitrate: int = 0  # kbps for lossy
    sample_rate: int = 0  # Hz
    bit_depth: int = 0  # for lossless

    # Source info
    file_path: str = ""

    # Raw tags (for debugging)
    raw_tags: dict[str, Any] = field(default_factory=dict)

    @property
    def is_compilation(self) -> bool:
        """Check if this is a Various Artists compilation."""
        aa = self.album_artist.lower()
        return aa in ("various artists", "various", "va", "compilation", "soundtrack", "ost")

    @property
    def format_tag(self) -> str:
        """Generate format tag for folder naming, e.g., 'FLAC 24-96' or 'MP3 320'."""
        if self.format == "FLAC":
            if self.bit_depth and self.sample_rate:
                sr_khz = self.sample_rate // 1000
                return f"FLAC {self.bit_depth}-{sr_khz}"
            return "FLAC"
        elif self.format == "MP3":
            if self.bitrate:
                return f"MP3 {self.bitrate}"
            return "MP3"
        elif self.format in ("AAC", "M4A"):
            if self.bitrate:
                return f"AAC {self.bitrate}"
            return "AAC"
        elif self.format in ("OGG", "Opus"):
            if self.bitrate:
                return f"{self.format} {self.bitrate}"
            return self.format
        return self.format or "Unknown"


def _get_first(tags: dict, keys: list[str], default: str = "") -> str:
    """Get first matching value from tags."""
    for key in keys:
        if key in tags:
            val = tags[key]
            if isinstance(val, list):
                return str(val[0]) if val else default
            return str(val)
    return default


def _get_int(tags: dict, keys: list[str], default: int = 0) -> int:
    """Get first matching integer value from tags."""
    val = _get_first(tags, keys, "")
    if not val:
        return default
    # Handle "3/12" format for track numbers
    if "/" in val:
        val = val.split("/")[0]
    try:
        return int(val)
    except ValueError:
        return default


def _get_total(tags: dict, keys: list[str], number_keys: list[str], default: int = 0) -> int:
    """Get total count (e.g., total tracks) from tags."""
    # First try explicit total keys
    for key in keys:
        if key in tags:
            val = tags[key]
            if isinstance(val, list):
                val = val[0] if val else ""
            try:
                return int(val)
            except (ValueError, TypeError):
                pass

    # Then try extracting from "3/12" format
    val = _get_first(tags, number_keys, "")
    if "/" in val:
        try:
            return int(val.split("/")[1])
        except (ValueError, IndexError):
            pass

    return default


def extract_metadata(audio_data: bytes | BytesIO, file_path: str = "") -> TrackMetadata | None:
    """
    Extract metadata from audio file data.

    Args:
        audio_data: Raw audio file bytes or BytesIO stream
        file_path: Original file path (for format detection and reference)

    Returns:
        TrackMetadata object or None if parsing fails
    """
    if isinstance(audio_data, bytes):
        audio_data = BytesIO(audio_data)

    try:
        audio = mutagen.File(audio_data)
        if audio is None:
            return None
    except Exception:
        return None

    # Build raw tags dict for inspection
    raw_tags: dict[str, Any] = {}
    if hasattr(audio, "tags") and audio.tags:
        for key in audio.tags.keys():
            raw_tags[key] = audio.tags[key]
    # Also include easy access keys if available
    for key in audio.keys():
        if key not in raw_tags:
            raw_tags[key] = audio[key]

    # Determine format and quality
    format_name = ""
    bitrate = 0
    sample_rate = 0
    bit_depth = 0
    duration = audio.info.length if hasattr(audio.info, "length") else 0.0

    if isinstance(audio, FLAC):
        format_name = "FLAC"
        sample_rate = audio.info.sample_rate
        bit_depth = audio.info.bits_per_sample
    elif isinstance(audio, MP3):
        format_name = "MP3"
        bitrate = audio.info.bitrate // 1000
        sample_rate = audio.info.sample_rate
    elif isinstance(audio, MP4):
        format_name = "AAC"
        bitrate = audio.info.bitrate // 1000 if hasattr(audio.info, "bitrate") else 0
        sample_rate = audio.info.sample_rate if hasattr(audio.info, "sample_rate") else 0
    elif isinstance(audio, OggVorbis):
        format_name = "OGG"
        bitrate = audio.info.bitrate // 1000 if hasattr(audio.info, "bitrate") else 0
        sample_rate = audio.info.sample_rate
    elif isinstance(audio, OggOpus):
        format_name = "Opus"
        sample_rate = 48000  # Opus always uses 48kHz
    else:
        format_name = type(audio).__name__

    # Extract common tags
    meta = TrackMetadata(
        title=_get_first(raw_tags, ["title", "TIT2", "©nam", "TITLE"]),
        artist=_get_first(raw_tags, ["artist", "TPE1", "©ART", "ARTIST"]),
        album_artist=_get_first(
            raw_tags,
            ["albumartist", "album artist", "TPE2", "aART", "ALBUMARTIST"],
        ),
        album=_get_first(raw_tags, ["album", "TALB", "©alb", "ALBUM"]),
        track_number=_get_int(raw_tags, ["tracknumber", "TRCK", "trkn", "TRACKNUMBER"]),
        total_tracks=_get_total(
            raw_tags,
            ["totaltracks", "TRCK", "TRACKTOTAL"],
            ["tracknumber", "TRCK", "trkn"],
        ),
        disc_number=_get_int(raw_tags, ["discnumber", "TPOS", "disk", "DISCNUMBER"], default=1),
        total_discs=_get_total(
            raw_tags,
            ["totaldiscs", "TPOS", "DISCTOTAL"],
            ["discnumber", "TPOS", "disk"],
            default=1,
        ),
        year=_get_int(raw_tags, ["date", "year", "TDRC", "TYER", "©day", "DATE"]),
        genre=_get_first(raw_tags, ["genre", "TCON", "©gen", "GENRE"]),
        duration=duration,
        format=format_name,
        bitrate=bitrate,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        file_path=file_path,
        raw_tags=raw_tags,
    )

    # Fall back album_artist to artist if not set
    if not meta.album_artist and meta.artist:
        meta.album_artist = meta.artist

    return meta


def extract_metadata_from_file(file_path: str | Path) -> TrackMetadata | None:
    """Extract metadata from a local file."""
    with open(file_path, "rb") as f:
        return extract_metadata(f.read(), str(file_path))
