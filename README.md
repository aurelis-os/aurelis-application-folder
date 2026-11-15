# AppImage Launcher

**AppImage Launcher** is a daemon, CLI, and KDE GUI for managing AppImages in `~/Applications`. It treats the folder as a drop-in/drop-out directory: dropping an AppImage installs it, removing it uninstalls it.

## Features

- **Drop-in installation**: Drop an AppImage into `~/Applications` and it's automatically installed with a clean name, desktop entry, and icon
- **Automatic updates**: Detects self-updating AppImages and zsync-updatable apps
- **Conflict handling**: Smart handling of version conflicts (replace, keep both, or ask)
- **CLI**: Full command-line interface for scripting and manual management
- **D-Bus API**: Stable, versioned public API for front-ends (Aurelis Software Center, install wizards, scripts)
- **KDE integration**: Dolphin service menu, notifications with actions, helper dialogs
- **Systemd integration**: Runs as a user service, starts automatically on login

## Installation

```bash
# From source
make install

# This installs system-wide and enables the systemd user service globally
# The observer will start for every user at their next login
```

## Quick Start

```bash
# Install an AppImage
appimage install ~/Downloads/GIMP-2.10.34-x86_64.AppImage

# List installed apps
appimage list

# Check for updates
appimage update --check

# Run the daemon manually (for development)
appimage daemon
```

## Architecture

```
Dolphin/Shell -> appimage (CLI) --+
                                  |  D-Bus (session)
Software Center -> front-end -----+------------------+
                                  |                  v
                          +--------------------------+
                          |  appimage daemon         |
                          |  observer + core Manager |
                          |  notifier (D-Bus)        |
                          |  dialog launcher         |
                          +------------+-------------+
                                       |
                    SQLite state, desktop entries, icons, config (per user)
```

## Documentation

- [Specification](docs/aurelis-application-folder-spec.md) - Full technical specification
- [API Documentation](docs/API.md) - Public D-Bus API reference
- [Decisions](docs/DECISIONS.md) - Design decisions and rationale
- [Architecture](docs/ARCHITECTURE.md) - Architecture overview

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run linting and type checking
make lint

# Run tests
make test

# Run CI pipeline (lint + test + coverage)
make ci

# Run daemon in development mode
make run
```

## License

MIT License - see [LICENSE](LICENSE) for details.