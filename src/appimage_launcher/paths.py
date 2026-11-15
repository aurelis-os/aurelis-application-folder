"""Path resolution for AppImage Launcher.

All paths are computed from config and the APPIMAGE_LAUNCHER_HOME environment variable.
The APPIMAGE_LAUNCHER_HOME override redirects config, state, entries and icons
for development and testing (used by `make run` and tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from appimage_launcher.config import Config
from appimage_launcher.constants import (
    CONFIG_FILE_NAME,
    DEFAULT_CONFIG_DIR,
    DEFAULT_DATA_DIR,
    DEFAULT_STATE_DIR,
    DESKTOP_ENTRY_PREFIX,
    DESKTOP_ENTRY_SUFFIX,
    DISABLED_DIR_NAME,
    ICON_PREFIX,
    LOCK_FILE_NAME,
    PREVIOUS_DIR_NAME,
    STAGING_DIR_NAME,
    STATE_DB_FILE_NAME,
)


@dataclass(frozen=True)
class Paths:
    """All resolved paths for the current user/context."""

    # Base directories
    home: Path
    applications_dir: Path
    config_dir: Path
    state_dir: Path
    data_dir: Path
    runtime_dir: Path

    # Config and state files
    config_file: Path
    state_db: Path
    lock_file: Path

    # Subdirectories within applications_dir
    previous_dir: Path
    staging_dir: Path
    disabled_dir: Path

    # Desktop entries and icons
    applications_data_dir: Path
    icons_data_dir: Path

    @classmethod
    def from_config(cls, config: Config) -> Paths:
        """Create Paths from a Config instance."""
        # Determine base home directory
        # APPIMAGE_LAUNCHER_HOME overrides everything for dev/test
        env_home = os.environ.get("APPIMAGE_LAUNCHER_HOME")
        if env_home:
            home = Path(env_home).expanduser().resolve()
        else:
            home = Path.home()

        # Resolve applications_dir from config (with ~ expansion)
        applications_dir = Path(config.applications_dir).expanduser()
        if not applications_dir.is_absolute():
            applications_dir = home / applications_dir
        applications_dir = applications_dir.resolve()

        # Config directory
        config_dir = Path(DEFAULT_CONFIG_DIR).expanduser()
        if env_home:
            config_dir = home / ".config" / "appimage-launcher"
        config_dir = config_dir.resolve()

        # State directory
        state_dir = Path(DEFAULT_STATE_DIR).expanduser()
        if env_home:
            state_dir = home / ".local" / "state" / "appimage-launcher"
        state_dir = state_dir.resolve()

        # Data directory (for desktop entries and icons)
        data_dir = Path(DEFAULT_DATA_DIR).expanduser()
        if env_home:
            data_dir = home / ".local" / "share"
        data_dir = data_dir.resolve()

        # Runtime directory (for lock file, mounts)
        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))

        return cls(
            home=home,
            applications_dir=applications_dir,
            config_dir=config_dir,
            state_dir=state_dir,
            data_dir=data_dir,
            runtime_dir=runtime_dir,
            config_file=config_dir / CONFIG_FILE_NAME,
            state_db=state_dir / STATE_DB_FILE_NAME,
            lock_file=runtime_dir / LOCK_FILE_NAME,
            previous_dir=applications_dir / PREVIOUS_DIR_NAME,
            staging_dir=applications_dir / STAGING_DIR_NAME,
            disabled_dir=applications_dir / DISABLED_DIR_NAME,
            applications_data_dir=data_dir / "applications",
            icons_data_dir=data_dir / "icons" / "hicolor",
        )

    def desktop_entry_path(self, app_id: str, version_slug: str | None = None) -> Path:
        """Get the desktop entry path for an app."""
        if version_slug:
            name = f"{DESKTOP_ENTRY_PREFIX}{app_id}-{version_slug}{DESKTOP_ENTRY_SUFFIX}"
        else:
            name = f"{DESKTOP_ENTRY_PREFIX}{app_id}{DESKTOP_ENTRY_SUFFIX}"
        return self.applications_data_dir / name

    def icon_path(self, app_id: str, size: int | str = "scalable", ext: str = "png") -> Path:
        """Get the icon path for an app."""
        if size == "scalable":
            size_dir = "scalable"
        else:
            size_dir = f"{size}x{size}"
        return self.icons_data_dir / size_dir / "apps" / f"{ICON_PREFIX}{app_id}.{ext}"

    def ensure_dirs(self) -> None:
        """Create all necessary directories."""
        for path in [
            self.applications_dir,
            self.config_dir,
            self.state_dir,
            self.data_dir,
            self.previous_dir,
            self.staging_dir,
            self.disabled_dir,
            self.applications_data_dir,
            self.icons_data_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


# Global paths instance (lazy initialization)
_paths: Paths | None = None


def get_paths(config: Config | None = None) -> Paths:
    """Get the global Paths instance, initializing if needed."""
    global _paths
    if _paths is None:
        if config is None:
            from appimage_launcher.config import get_config

            config = get_config()
        _paths = Paths.from_config(config)
    return _paths


def reset_paths() -> None:
    """Reset the global paths instance (for testing)."""
    global _paths
    _paths = None


# Dev mode flag
_dev_mode = False


def set_dev_mode(enabled: bool) -> None:
    """Enable or disable dev mode (uses APPIMAGE_LAUNCHER_HOME)."""
    global _dev_mode
    _dev_mode = enabled
    if enabled:
        import os
        import tempfile

        # Create a temporary directory for dev mode
        tmpdir = tempfile.mkdtemp(prefix="appimage-launcher-dev-")
        os.environ["APPIMAGE_LAUNCHER_HOME"] = tmpdir
    else:
        import os

        if "APPIMAGE_LAUNCHER_HOME" in os.environ:
            del os.environ["APPIMAGE_LAUNCHER_HOME"]
    reset_paths()


def is_dev_mode() -> bool:
    """Check if dev mode is enabled."""
    return _dev_mode
