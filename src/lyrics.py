"""Lyrics fetching from LRCLIB.

LRCLIB is a free, rate-limit-free API for synced lyrics in LRC format.
"""

import logging
from dataclasses import dataclass

import requests

from .config import settings
from .metadata import TrackMetadata

logger = logging.getLogger(__name__)


@dataclass
class LyricsResult:
    """Result from lyrics lookup."""

    synced_lyrics: str | None  # LRC format with timestamps
    plain_lyrics: str | None   # Plain text without timestamps
    track_name: str
    artist_name: str
    album_name: str
    duration: float
    source: str = "lrclib"


class LyricsFetcher:
    """Fetch lyrics from LRCLIB API."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.musicbrainz_user_agent,
        })

    def fetch(self, meta: TrackMetadata) -> LyricsResult | None:
        """
        Fetch lyrics for a track.

        Args:
            meta: Track metadata

        Returns:
            LyricsResult or None if not found
        """
        return self.fetch_by_query(
            artist=meta.artist or meta.album_artist,
            title=meta.title,
            album=meta.album,
            duration=meta.duration,
        )

    def fetch_by_query(
        self,
        artist: str,
        title: str,
        album: str | None = None,
        duration: float | None = None,
    ) -> LyricsResult | None:
        """
        Fetch lyrics by search parameters.

        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional but improves accuracy)
            duration: Track duration in seconds (optional)

        Returns:
            LyricsResult or None
        """
        if not artist or not title:
            logger.debug("Missing artist or title for lyrics search")
            return None

        params: dict[str, str | int] = {
            "artist_name": artist,
            "track_name": title,
        }

        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = int(duration)

        url = f"{settings.lrclib_base_url}/get"

        try:
            resp = self.session.get(url, params=params, timeout=10)

            if resp.status_code == 404:
                logger.debug(f"No lyrics found for {artist} - {title}")
                return None

            resp.raise_for_status()
            data = resp.json()

            return LyricsResult(
                synced_lyrics=data.get("syncedLyrics"),
                plain_lyrics=data.get("plainLyrics"),
                track_name=data.get("trackName", title),
                artist_name=data.get("artistName", artist),
                album_name=data.get("albumName", album or ""),
                duration=data.get("duration", duration or 0),
            )

        except requests.RequestException as e:
            logger.warning(f"LRCLIB request failed: {e}")
            return None

    def search(
        self,
        query: str,
        artist: str | None = None,
        album: str | None = None,
    ) -> list[LyricsResult]:
        """
        Search for lyrics (less precise than fetch).

        Args:
            query: Search query (usually song title)
            artist: Artist name (optional)
            album: Album name (optional)

        Returns:
            List of matching results
        """
        params: dict[str, str] = {"q": query}

        if artist:
            params["artist_name"] = artist
        if album:
            params["album_name"] = album

        url = f"{settings.lrclib_base_url}/search"

        try:
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data:
                results.append(LyricsResult(
                    synced_lyrics=item.get("syncedLyrics"),
                    plain_lyrics=item.get("plainLyrics"),
                    track_name=item.get("trackName", ""),
                    artist_name=item.get("artistName", ""),
                    album_name=item.get("albumName", ""),
                    duration=item.get("duration", 0),
                ))

            return results

        except requests.RequestException as e:
            logger.warning(f"LRCLIB search failed: {e}")
            return []


def format_lrc_content(result: LyricsResult) -> str:
    """
    Format lyrics result as LRC file content.

    Uses synced lyrics if available, falls back to plain.
    """
    if result.synced_lyrics:
        # Already in LRC format
        return result.synced_lyrics

    if result.plain_lyrics:
        # Add basic LRC header for plain lyrics
        lines = [
            f"[ar:{result.artist_name}]",
            f"[ti:{result.track_name}]",
            f"[al:{result.album_name}]",
            "",
        ]
        # Plain lyrics without timestamps
        lines.extend(result.plain_lyrics.split("\n"))
        return "\n".join(lines)

    return ""
