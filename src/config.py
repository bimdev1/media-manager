"""Configuration management via Pydantic settings."""

from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MM_",
        extra="ignore",
    )

    # SMB Configuration
    smb_server: str = "192.168.8.114"
    smb_share: str = "music"
    smb_username: str = "mediamanager"
    smb_password: SecretStr = SecretStr("Ridgewood")

    # Paths
    compilations_folder: str = "Compilations"
    local_cache_dir: Path = Path("/tmp/media-manager-cache")

    # Thresholds
    cover_min_dimension: int = 800  # pixels
    cover_min_size: int = 500 * 1024  # 500KB in bytes

    # Audio extensions to process
    audio_extensions: tuple[str, ...] = (".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aiff")

    # API settings
    musicbrainz_user_agent: str = "MediaManager/0.1.0 (https://github.com/bimdev1/media-manager)"
    lrclib_base_url: str = "https://lrclib.net/api"
    coverart_base_url: str = "https://coverartarchive.org"

    # Processing
    dry_run: bool = False
    undo_log_path: Path = Path("undo_log.jsonl")

    @property
    def smb_root(self) -> str:
        """Get the SMB root path."""
        return f"\\\\{self.smb_server}\\{self.smb_share}"


# Global settings instance
settings = Settings()
