"""File watcher for SMB shares via polling.

SMB doesn't support inotify, so we poll for changes periodically.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import settings
from .smb_client import SMBClient

logger = logging.getLogger(__name__)


@dataclass
class FileState:
    """State of a file for change detection."""

    path: str
    size: int
    modified: float  # timestamp


@dataclass
class WatchState:
    """Persistent state for the watcher."""

    last_scan: datetime = field(default_factory=datetime.now)
    known_files: dict[str, FileState] = field(default_factory=dict)
    state_file: Path = field(default_factory=lambda: Path(".watch_state.json"))

    def save(self) -> None:
        """Persist state to disk."""
        data = {
            "last_scan": self.last_scan.isoformat(),
            "known_files": {
                path: {"path": f.path, "size": f.size, "modified": f.modified}
                for path, f in self.known_files.items()
            },
        }
        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, state_file: Path | None = None) -> "WatchState":
        """Load state from disk."""
        path = state_file or Path(".watch_state.json")
        if not path.exists():
            return cls(state_file=path)

        try:
            with open(path) as f:
                data = json.load(f)

            known = {}
            for p, info in data.get("known_files", {}).items():
                known[p] = FileState(
                    path=info["path"],
                    size=info["size"],
                    modified=info["modified"],
                )

            return cls(
                last_scan=datetime.fromisoformat(data.get("last_scan", datetime.now().isoformat())),
                known_files=known,
                state_file=path,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Could not load watch state: {e}")
            return cls(state_file=path)


class DirectoryWatcher:
    """Watch SMB directory for new files via polling."""

    def __init__(
        self,
        client: SMBClient | None = None,
        poll_interval: int = 300,  # 5 minutes
        process_callback: Callable[[list[str]], None] | None = None,
    ):
        self.client = client or SMBClient()
        self.poll_interval = poll_interval
        self.process_callback = process_callback
        self.state = WatchState.load()
        self._running = False

    def _scan_for_changes(self) -> tuple[list[str], list[str], list[str]]:
        """
        Scan directory and detect changes.

        Returns:
            (new_files, modified_files, deleted_files)
        """
        new_files: list[str] = []
        modified_files: list[str] = []
        current_files: set[str] = set()

        # Scan all audio files
        for dirpath, _, filenames in self.client.walk():
            for filename in filenames:
                if not any(filename.lower().endswith(ext) for ext in settings.audio_extensions):
                    continue

                full_path = f"{dirpath}\\{filename}"
                current_files.add(full_path)

                try:
                    from smbclient import stat

                    file_stat = stat(full_path)
                    current_state = FileState(
                        path=full_path,
                        size=file_stat.st_size,
                        modified=file_stat.st_mtime,
                    )
                except OSError:
                    continue

                if full_path not in self.state.known_files:
                    new_files.append(full_path)
                    self.state.known_files[full_path] = current_state
                else:
                    old_state = self.state.known_files[full_path]
                    if current_state.size != old_state.size or current_state.modified != old_state.modified:
                        modified_files.append(full_path)
                        self.state.known_files[full_path] = current_state

        # Detect deleted files
        known_paths = set(self.state.known_files.keys())
        deleted_files = list(known_paths - current_files)
        for path in deleted_files:
            del self.state.known_files[path]

        return new_files, modified_files, deleted_files

    def _get_albums_from_files(self, file_paths: list[str]) -> set[str]:
        """Extract unique album paths from file paths."""
        albums = set()
        for path in file_paths:
            # Album is parent directory
            parts = path.rsplit("\\", 1)
            if len(parts) == 2:
                albums.add(parts[0])
        return albums

    def run_once(self) -> dict:
        """Run a single scan cycle."""
        logger.info("Scanning for changes...")

        new_files, modified_files, deleted_files = self._scan_for_changes()

        result = {
            "new_files": len(new_files),
            "modified_files": len(modified_files),
            "deleted_files": len(deleted_files),
            "albums_affected": 0,
        }

        # Process affected albums
        files_to_process = new_files + modified_files
        if files_to_process:
            albums = self._get_albums_from_files(files_to_process)
            result["albums_affected"] = len(albums)

            if self.process_callback:
                self.process_callback(list(albums))

        # Update state
        self.state.last_scan = datetime.now()
        self.state.save()

        logger.info(
            f"Scan complete: {result['new_files']} new, "
            f"{result['modified_files']} modified, "
            f"{result['deleted_files']} deleted"
        )

        return result

    def run(self) -> None:
        """Run continuous watch loop."""
        self._running = True
        logger.info(f"Starting watcher (poll interval: {self.poll_interval}s)")

        while self._running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Watch cycle failed: {e}")

            logger.info(f"Sleeping for {self.poll_interval}s...")
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the watch loop."""
        self._running = False
        logger.info("Watcher stopped")
