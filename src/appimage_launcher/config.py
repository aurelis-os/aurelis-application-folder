"""Configuration management for AppImage Launcher.

Layered configuration with precedence:
1. System default config (/etc/appimage-launcher/config.toml)
2. User config (~/.config/appimage-launcher/config.toml)
3. Environment variables (APPIMAGE_LAUNCHER_*)
4. APPIMAGE_LAUNCHER_HOME redirect (for dev/test)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from appimage_launcher.constants import (
    DEFAULT_APPLICATIONS_DIR,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_CONFIG_DIR,
    DEFAULT_PREVIOUS_VERSIONS_KEEP,
)


@dataclass
class InstallConfig:
    """Install configuration section."""

    source_action: str = "copy"  # copy | move
    conflict_policy: str = "ask"  # ask | replace | keep


@dataclass
class NamingConfig:
    """Naming configuration section."""

    template: str = "{name}"
    keep_template: str = "{name} {version}"


@dataclass
class UpdateConfig:
    """Update configuration section."""

    on_self_update: str = "replace"  # replace | keep | ask
    rename_policy: str = "after_quit"  # after_quit | never
    check_interval: str = DEFAULT_CHECK_INTERVAL
    previous_versions_keep: int = DEFAULT_PREVIOUS_VERSIONS_KEEP
    skip_catalog_linked: bool = True


@dataclass
class NotificationsConfig:
    """Notifications configuration section."""

    enabled: bool = True
    timeout: int = 20


@dataclass
class GuiConfig:
    """GUI configuration section."""

    use_dialogs: bool = True


@dataclass
class Config:
    """Main configuration class."""

    applications_dir: str = DEFAULT_APPLICATIONS_DIR
    install: InstallConfig = field(default_factory=InstallConfig)
    naming: NamingConfig = field(default_factory=NamingConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    conflict_preferences: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create Config from a dictionary."""
        config = cls()

        if "applications_dir" in data:
            config.applications_dir = data["applications_dir"]

        if "install" in data:
            install_data = data["install"]
            config.install = InstallConfig(
                source_action=install_data.get("source_action", "copy"),
                conflict_policy=install_data.get("conflict_policy", "ask"),
            )

        if "naming" in data:
            naming_data = data["naming"]
            config.naming = NamingConfig(
                template=naming_data.get("template", "{name}"),
                keep_template=naming_data.get("keep_template", "{name} {version}"),
            )

        if "update" in data:
            update_data = data["update"]
            config.update = UpdateConfig(
                on_self_update=update_data.get("on_self_update", "replace"),
                rename_policy=update_data.get("rename_policy", "after_quit"),
                check_interval=update_data.get("check_interval", DEFAULT_CHECK_INTERVAL),
                previous_versions_keep=update_data.get(
                    "previous_versions_keep", DEFAULT_PREVIOUS_VERSIONS_KEEP
                ),
                skip_catalog_linked=update_data.get("skip_catalog_linked", True),
            )

        if "notifications" in data:
            notif_data = data["notifications"]
            config.notifications = NotificationsConfig(
                enabled=notif_data.get("enabled", True),
                timeout=notif_data.get("timeout", 20),
            )

        if "gui" in data:
            gui_data = data["gui"]
            config.gui = GuiConfig(
                use_dialogs=gui_data.get("use_dialogs", True),
            )

        if "conflict_preferences" in data:
            config.conflict_preferences = data["conflict_preferences"]

        return config

    def to_dict(self) -> dict[str, Any]:
        """Convert Config to a dictionary for serialization."""
        return {
            "applications_dir": self.applications_dir,
            "install": {
                "source_action": self.install.source_action,
                "conflict_policy": self.install.conflict_policy,
            },
            "naming": {
                "template": self.naming.template,
                "keep_template": self.naming.keep_template,
            },
            "update": {
                "on_self_update": self.update.on_self_update,
                "rename_policy": self.update.rename_policy,
                "check_interval": self.update.check_interval,
                "previous_versions_keep": self.update.previous_versions_keep,
                "skip_catalog_linked": self.update.skip_catalog_linked,
            },
            "notifications": {
                "enabled": self.notifications.enabled,
                "timeout": self.notifications.timeout,
            },
            "gui": {
                "use_dialogs": self.gui.use_dialogs,
            },
            "conflict_preferences": self.conflict_preferences,
        }

    def merge_env_overrides(self) -> None:
        """Apply environment variable overrides.

        Environment variables use the prefix APPIMAGE_LAUNCHER_ with
        double underscore for nested keys, e.g.:
        APPIMAGE_LAUNCHER_APPLICATIONS_DIR
        APPIMAGE_LAUNCHER_INSTALL__SOURCE_ACTION
        """
        # Top-level keys
        if "APPIMAGE_LAUNCHER_APPLICATIONS_DIR" in os.environ:
            self.applications_dir = os.environ["APPIMAGE_LAUNCHER_APPLICATIONS_DIR"]

        # Install section
        if "APPIMAGE_LAUNCHER_INSTALL__SOURCE_ACTION" in os.environ:
            self.install.source_action = os.environ["APPIMAGE_LAUNCHER_INSTALL__SOURCE_ACTION"]
        if "APPIMAGE_LAUNCHER_INSTALL__CONFLICT_POLICY" in os.environ:
            self.install.conflict_policy = os.environ["APPIMAGE_LAUNCHER_INSTALL__CONFLICT_POLICY"]

        # Naming section
        if "APPIMAGE_LAUNCHER_NAMING__TEMPLATE" in os.environ:
            self.naming.template = os.environ["APPIMAGE_LAUNCHER_NAMING__TEMPLATE"]
        if "APPIMAGE_LAUNCHER_NAMING__KEEP_TEMPLATE" in os.environ:
            self.naming.keep_template = os.environ["APPIMAGE_LAUNCHER_NAMING__KEEP_TEMPLATE"]

        # Update section
        if "APPIMAGE_LAUNCHER_UPDATE__ON_SELF_UPDATE" in os.environ:
            self.update.on_self_update = os.environ["APPIMAGE_LAUNCHER_UPDATE__ON_SELF_UPDATE"]
        if "APPIMAGE_LAUNCHER_UPDATE__RENAME_POLICY" in os.environ:
            self.update.rename_policy = os.environ["APPIMAGE_LAUNCHER_UPDATE__RENAME_POLICY"]
        if "APPIMAGE_LAUNCHER_UPDATE__CHECK_INTERVAL" in os.environ:
            self.update.check_interval = os.environ["APPIMAGE_LAUNCHER_UPDATE__CHECK_INTERVAL"]
        if "APPIMAGE_LAUNCHER_UPDATE__PREVIOUS_VERSIONS_KEEP" in os.environ:
            self.update.previous_versions_keep = int(
                os.environ["APPIMAGE_LAUNCHER_UPDATE__PREVIOUS_VERSIONS_KEEP"]
            )
        if "APPIMAGE_LAUNCHER_UPDATE__SKIP_CATALOG_LINKED" in os.environ:
            self.update.skip_catalog_linked = (
                os.environ["APPIMAGE_LAUNCHER_UPDATE__SKIP_CATALOG_LINKED"].lower() == "true"
            )

        # Notifications section
        if "APPIMAGE_LAUNCHER_NOTIFICATIONS__ENABLED" in os.environ:
            self.notifications.enabled = (
                os.environ["APPIMAGE_LAUNCHER_NOTIFICATIONS__ENABLED"].lower() == "true"
            )
        if "APPIMAGE_LAUNCHER_NOTIFICATIONS__TIMEOUT" in os.environ:
            self.notifications.timeout = int(os.environ["APPIMAGE_LAUNCHER_NOTIFICATIONS__TIMEOUT"])

        # GUI section
        if "APPIMAGE_LAUNCHER_GUI__USE_DIALOGS" in os.environ:
            self.gui.use_dialogs = (
                os.environ["APPIMAGE_LAUNCHER_GUI__USE_DIALOGS"].lower() == "true"
            )


# Default system config path
SYSTEM_CONFIG_PATH = Path("/etc/appimage-launcher/config.toml")

# Global config instance (lazy initialization)
_config: Config | None = None


def load_config() -> Config:
    """Load configuration from all sources with proper precedence."""
    config = Config()

    # 1. System default config
    if SYSTEM_CONFIG_PATH.exists():
        try:
            with open(SYSTEM_CONFIG_PATH, "rb") as f:
                system_data = tomllib.load(f)
            config = Config.from_dict(system_data)
        except Exception:
            # Ignore system config errors, use defaults
            pass

    # 2. User config
    user_config_path = Path(DEFAULT_CONFIG_DIR).expanduser() / "config.toml"
    if user_config_path.exists():
        try:
            with open(user_config_path, "rb") as f:
                user_data = tomllib.load(f)
            user_config = Config.from_dict(user_data)
            # Merge user config over system config
            config = _merge_configs(config, user_config)
        except Exception:
            # Ignore user config errors
            pass

    # 3. Environment variable overrides
    config.merge_env_overrides()

    return config


def _merge_configs(base: Config, override: Config) -> Config:
    """Merge override config into base config."""
    result = Config()

    # applications_dir
    result.applications_dir = override.applications_dir or base.applications_dir

    # Install
    result.install = InstallConfig(
        source_action=override.install.source_action or base.install.source_action,
        conflict_policy=override.install.conflict_policy or base.install.conflict_policy,
    )

    # Naming
    result.naming = NamingConfig(
        template=override.naming.template or base.naming.template,
        keep_template=override.naming.keep_template or base.naming.keep_template,
    )

    # Update
    result.update = UpdateConfig(
        on_self_update=override.update.on_self_update or base.update.on_self_update,
        rename_policy=override.update.rename_policy or base.update.rename_policy,
        check_interval=override.update.check_interval or base.update.check_interval,
        previous_versions_keep=override.update.previous_versions_keep
        or base.update.previous_versions_keep,
        skip_catalog_linked=override.update.skip_catalog_linked
        if override.update.skip_catalog_linked is not None
        else base.update.skip_catalog_linked,
    )

    # Notifications
    result.notifications = NotificationsConfig(
        enabled=override.notifications.enabled
        if override.notifications.enabled is not None
        else base.notifications.enabled,
        timeout=override.notifications.timeout or base.notifications.timeout,
    )

    # GUI
    result.gui = GuiConfig(
        use_dialogs=override.gui.use_dialogs
        if override.gui.use_dialogs is not None
        else base.gui.use_dialogs,
    )

    # Conflict preferences
    result.conflict_preferences = {**base.conflict_preferences, **override.conflict_preferences}

    return result


def get_config() -> Config:
    """Get the global Config instance, initializing if needed."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the global config instance (for testing)."""
    global _config
    _config = None


def save_user_config(config: Config) -> None:
    """Save conflict_preferences to user config file.

    Reads the existing user config, updates only [conflict_preferences],
    and writes it back atomically via a temp file + rename.

    Raises:
        tomllib.TOMLDecodeError: If existing config.toml exists but cannot be parsed.
        OSError: If the file cannot be read (permission, I/O error, etc.).
        UnicodeDecodeError: If the file contains invalid UTF-8.
    """
    from appimage_launcher.constants import DEFAULT_CONFIG_DIR

    user_config_path = Path(DEFAULT_CONFIG_DIR).expanduser() / "config.toml"
    user_config_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing user config (not merged config)
    existing_data = {}
    if user_config_path.exists():
        with open(user_config_path, "rb") as f:
            existing_data = tomllib.load(f)

    # Update only conflict_preferences (merge with existing)
    if "conflict_preferences" not in existing_data:
        existing_data["conflict_preferences"] = {}
    existing_data["conflict_preferences"].update(config.conflict_preferences)

    # Write atomically: temp file + rename
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="wb", dir=user_config_path.parent, delete=False, prefix=".config.", suffix=".tmp"
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            tomli_w.dump(existing_data, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            # Atomic rename
            tmp_path.replace(user_config_path)
        except Exception:
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise


def get_default_config_toml() -> str:
    """Get the default configuration as a TOML string."""
    default_config = Config()
    return tomli_w.dumps(default_config.to_dict())
