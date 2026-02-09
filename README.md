# Media Manager

Automated music library maintenance for Navidrome/Plex/Symfonium.

## Features

- **Naming cleanup** — Detects scene-release patterns, normalizes to `Album Artist / Album (Year) [Format] / Track.ext`
- **Album artwork** — Fetches high-res covers from MusicBrainz/Cover Art Archive, replaces low-quality (<800px)
- **Synced lyrics** — Downloads LRC files from LRCLIB for sing-along support
- **Undo log** — All changes recorded for rollback

## Installation

```bash
# Clone and setup
git clone https://github.com/bimdev1/media-manager.git
cd media-manager
python3 -m venv .venv
source .venv/bin/activate  # or activate.fish for fish shell
pip install -e .

# Configure (copy and edit)
cp .env.example .env
```

## Configuration

Edit `.env`:

```bash
MM_SMB_SERVER=192.168.8.114
MM_SMB_SHARE=music
MM_SMB_USERNAME=mediamanager
MM_SMB_PASSWORD=yourpassword
```

## Usage

```bash
# Check connection and config
python main.py status

# Scan library (dry-run, no changes)
python main.py --dry-run scan --component all

# Scan specific component
python main.py --dry-run scan --component artwork --limit 10

# Fix issues
python main.py fix --component lyrics --limit 5

# Fix everything (use with caution)
python main.py fix --component all
```

### Commands

| Command | Description |
|---------|-------------|
| `status` | Show config and test SMB connection |
| `scan` | Report issues without making changes |
| `fix` | Apply fixes to the library |
| `watch` | Watch for new files and auto-process |

### Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--component` | `naming`, `artwork`, `lyrics`, or `all` |
| `--limit N` | Process only N albums (0 = all) |
| `--interval N` | Poll interval in seconds (watch mode, default: 300) |
| `-v, --verbose` | Enable debug logging |

## Watch Mode

Two ways to run automatically:

### 1. Foreground (manual)

```bash
python main.py watch --interval 300 --component all
```

### 2. Systemd Service (auto-start)

```bash
# Install service
sudo cp media-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable media-manager
sudo systemctl start media-manager

# Check status
sudo systemctl status media-manager
sudo journalctl -u media-manager -f
```

## Naming Convention

Follows MusicBrainz Picard / audiophile archival standards:

```text
Music/
├── Album Artist/
│   └── Album (Year) [Format]/
│       ├── cover.jpg
│       ├── 01 - Track Title.flac
│       └── 01 - Track Title.lrc
└── Compilations/
    └── Soundtrack (Year) [Format]/
        └── ...
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## License

MIT
