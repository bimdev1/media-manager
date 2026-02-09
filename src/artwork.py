"""Album artwork fetching and management.

Uses MusicBrainz + Cover Art Archive to fetch high-quality album art.
"""

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import requests
from PIL import Image

from .config import settings
from .metadata import TrackMetadata

logger = logging.getLogger(__name__)


@dataclass
class CoverInfo:
    """Information about album cover."""

    path: str
    width: int
    height: int
    size_bytes: int
    needs_replacement: bool
    reason: str = ""


@dataclass
class FetchedCover:
    """Successfully fetched cover art."""

    data: bytes
    width: int
    height: int
    source_url: str
    mbid: str


class ArtworkFetcher:
    """Fetch album artwork from MusicBrainz / Cover Art Archive."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.musicbrainz_user_agent,
            "Accept": "application/json",
        })

    def _search_musicbrainz(
        self, artist: str, album: str
    ) -> str | None:
        """
        Search MusicBrainz for release-group MBID.

        Returns MBID or None if not found.
        """
        query = f'artist:"{artist}" AND releasegroup:"{album}"'
        url = "https://musicbrainz.org/ws/2/release-group"

        try:
            resp = self.session.get(
                url,
                params={"query": query, "fmt": "json", "limit": 5},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            release_groups = data.get("release-groups", [])
            if not release_groups:
                logger.debug(f"No release groups found for {artist} - {album}")
                return None

            # Return first match
            return release_groups[0]["id"]

        except requests.RequestException as e:
            logger.warning(f"MusicBrainz search failed: {e}")
            return None

    def _fetch_cover_art(self, mbid: str) -> FetchedCover | None:
        """
        Fetch cover art from Cover Art Archive.

        Args:
            mbid: MusicBrainz release-group ID

        Returns:
            FetchedCover or None if not found
        """
        url = f"{settings.coverart_base_url}/release-group/{mbid}"

        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 404:
                logger.debug(f"No cover art for MBID {mbid}")
                return None
            resp.raise_for_status()

            data = resp.json()
            images = data.get("images", [])

            # Find front cover
            front_image = None
            for img in images:
                if img.get("front"):
                    front_image = img
                    break

            if not front_image and images:
                front_image = images[0]

            if not front_image:
                return None

            # Download the image
            image_url = front_image.get("image", "")
            if not image_url:
                return None

            img_resp = self.session.get(image_url, timeout=30)
            img_resp.raise_for_status()

            # Get dimensions
            img = Image.open(BytesIO(img_resp.content))
            width, height = img.size

            return FetchedCover(
                data=img_resp.content,
                width=width,
                height=height,
                source_url=image_url,
                mbid=mbid,
            )

        except requests.RequestException as e:
            logger.warning(f"Cover Art Archive fetch failed: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error processing cover image: {e}")
            return None

    def fetch(self, meta: TrackMetadata) -> FetchedCover | None:
        """
        Fetch album art for a track's album.

        Args:
            meta: Track metadata

        Returns:
            FetchedCover or None
        """
        artist = meta.album_artist or meta.artist
        album = meta.album

        if not artist or not album:
            logger.debug("Missing artist or album for cover search")
            return None

        mbid = self._search_musicbrainz(artist, album)
        if not mbid:
            return None

        return self._fetch_cover_art(mbid)

    def fetch_by_query(self, artist: str, album: str) -> FetchedCover | None:
        """Fetch album art by artist and album name."""
        mbid = self._search_musicbrainz(artist, album)
        if not mbid:
            return None
        return self._fetch_cover_art(mbid)


def analyze_cover(data: bytes) -> CoverInfo:
    """
    Analyze cover image data.

    Returns CoverInfo with quality assessment.
    """
    try:
        img = Image.open(BytesIO(data))
        width, height = img.size
        size_bytes = len(data)

        needs_replacement = False
        reason = ""

        # Check dimensions
        min_dim = min(width, height)
        if min_dim < settings.cover_min_dimension:
            needs_replacement = True
            reason = f"Too small: {width}x{height} (min {settings.cover_min_dimension}px)"

        # Check file size
        elif size_bytes < settings.cover_min_size:
            needs_replacement = True
            reason = f"Low quality: {size_bytes // 1024}KB (min {settings.cover_min_size // 1024}KB)"

        return CoverInfo(
            path="",
            width=width,
            height=height,
            size_bytes=size_bytes,
            needs_replacement=needs_replacement,
            reason=reason,
        )

    except Exception as e:
        return CoverInfo(
            path="",
            width=0,
            height=0,
            size_bytes=len(data),
            needs_replacement=True,
            reason=f"Invalid image: {e}",
        )


def should_replace_cover(existing_data: bytes | None) -> tuple[bool, str]:
    """
    Determine if existing cover should be replaced.

    Returns (should_replace, reason)
    """
    if existing_data is None:
        return True, "No cover exists"

    info = analyze_cover(existing_data)
    if info.needs_replacement:
        return True, info.reason

    return False, f"Cover is good: {info.width}x{info.height}, {info.size_bytes // 1024}KB"
