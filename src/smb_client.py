"""SMB client for remote file operations."""

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from smbclient import (
    open_file,
    listdir,
    scandir,
    mkdir,
    rename,
    remove,
    rmdir,
    stat,
    register_session,
)

from .config import settings


@dataclass
class FileInfo:
    """Metadata about a remote file."""

    path: str
    name: str
    is_dir: bool
    size: int
    modified: datetime | None = None


class SMBClient:
    """Client for interacting with SMB shares."""

    def __init__(
        self,
        server: str | None = None,
        share: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self.server = server or settings.smb_server
        self.share = share or settings.smb_share
        self.username = username or settings.smb_username
        self.password = password or settings.smb_password.get_secret_value()
        self._session_registered = False

    def _ensure_session(self) -> None:
        """Register SMB session if not already done."""
        if not self._session_registered:
            register_session(self.server, username=self.username, password=self.password)
            self._session_registered = True

    @property
    def root_path(self) -> str:
        """Get the root SMB path."""
        return f"\\\\{self.server}\\{self.share}"

    def list_dir(self, path: str | None = None) -> list[str]:
        """List contents of a directory."""
        self._ensure_session()
        full_path = path or self.root_path
        return listdir(full_path)

    def scan_dir(self, path: str | None = None) -> Iterator[FileInfo]:
        """Scan directory with file metadata."""
        self._ensure_session()
        full_path = path or self.root_path
        for entry in scandir(full_path):
            try:
                file_stat = stat(entry.path)
                yield FileInfo(
                    path=entry.path,
                    name=entry.name,
                    is_dir=entry.is_dir(),
                    size=file_stat.st_size if not entry.is_dir() else 0,
                    modified=datetime.fromtimestamp(file_stat.st_mtime),
                )
            except OSError:
                # Skip files we can't stat
                continue

    def walk(
        self, path: str | None = None, max_depth: int = -1
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        """
        Walk directory tree, similar to os.walk.

        Yields (dirpath, dirnames, filenames) tuples.
        """
        self._ensure_session()
        root = path or self.root_path

        def _walk_recursive(current_path: str, depth: int) -> Iterator[tuple[str, list[str], list[str]]]:
            if max_depth >= 0 and depth > max_depth:
                return

            dirs: list[str] = []
            files: list[str] = []

            try:
                for entry in scandir(current_path):
                    if entry.is_dir():
                        dirs.append(entry.name)
                    else:
                        files.append(entry.name)
            except OSError:
                return

            yield current_path, dirs, files

            for dirname in dirs:
                subdir_path = f"{current_path}\\{dirname}"
                yield from _walk_recursive(subdir_path, depth + 1)

        yield from _walk_recursive(root, 0)

    def read_file(self, path: str) -> bytes:
        """Read file contents into memory."""
        self._ensure_session()
        with open_file(path, mode="rb") as f:
            return f.read()

    def write_file(self, path: str, data: bytes) -> None:
        """Write data to a file."""
        self._ensure_session()
        with open_file(path, mode="wb") as f:
            f.write(data)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read file as text."""
        return self.read_file(path).decode(encoding)

    def write_text(self, path: str, text: str, encoding: str = "utf-8") -> None:
        """Write text to a file."""
        self.write_file(path, text.encode(encoding))

    def exists(self, path: str) -> bool:
        """Check if a path exists."""
        self._ensure_session()
        try:
            stat(path)
            return True
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        """Check if path is a directory."""
        self._ensure_session()
        try:
            from smbclient._os import is_dir
            return is_dir(path)
        except (OSError, ImportError):
            # Fallback: try to list it
            try:
                listdir(path)
                return True
            except OSError:
                return False

    def get_size(self, path: str) -> int:
        """Get file size in bytes."""
        self._ensure_session()
        return stat(path).st_size

    def mkdir(self, path: str, parents: bool = True) -> None:
        """Create a directory."""
        self._ensure_session()
        if parents:
            parts = path.replace(self.root_path, "").strip("\\").split("\\")
            current = self.root_path
            for part in parts:
                current = f"{current}\\{part}"
                if not self.exists(current):
                    mkdir(current)
        else:
            mkdir(path)

    def rename(self, src: str, dst: str) -> None:
        """Rename/move a file or directory."""
        self._ensure_session()
        rename(src, dst)

    def remove(self, path: str) -> None:
        """Remove a file."""
        self._ensure_session()
        remove(path)

    def rmdir(self, path: str, recursive: bool = False) -> None:
        """Remove a directory."""
        self._ensure_session()
        if recursive:
            for dirpath, dirnames, filenames in self.walk(path):
                for filename in filenames:
                    remove(f"{dirpath}\\{filename}")
            # Remove dirs in reverse order
            dirs_to_remove = []
            for dirpath, dirnames, _ in self.walk(path):
                dirs_to_remove.append(dirpath)
            for d in reversed(dirs_to_remove):
                rmdir(d)
        else:
            rmdir(path)


class UndoLog:
    """Log file operations for potential rollback."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.undo_log_path
        self._entries: list[dict] = []

    def log_rename(self, src: str, dst: str) -> None:
        """Log a rename operation."""
        self._entries.append({
            "timestamp": datetime.now().isoformat(),
            "operation": "rename",
            "src": src,
            "dst": dst,
        })

    def log_write(self, path: str, had_previous: bool, previous_size: int = 0) -> None:
        """Log a file write operation."""
        self._entries.append({
            "timestamp": datetime.now().isoformat(),
            "operation": "write",
            "path": path,
            "had_previous": had_previous,
            "previous_size": previous_size,
        })

    def log_delete(self, path: str) -> None:
        """Log a delete operation."""
        self._entries.append({
            "timestamp": datetime.now().isoformat(),
            "operation": "delete",
            "path": path,
        })

    def save(self) -> None:
        """Append entries to log file."""
        with open(self.path, "a") as f:
            for entry in self._entries:
                f.write(json.dumps(entry) + "\n")
        self._entries.clear()

    def read_all(self) -> list[dict]:
        """Read all entries from log file."""
        if not self.path.exists():
            return []
        entries = []
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
        return entries


@contextmanager
def smb_session():
    """Context manager for SMB operations."""
    client = SMBClient()
    client._ensure_session()
    yield client
