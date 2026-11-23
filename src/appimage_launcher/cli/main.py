"""Main CLI entry point for AppImage Launcher."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from appimage_launcher.config import get_config, reset_config
from appimage_launcher.core.manager import ConflictError, Manager
from appimage_launcher.core.metadata import read_appimage_meta
from appimage_launcher.logging import get_logger, setup_logging
from appimage_launcher.paths import get_paths, reset_paths

logger = get_logger(__name__)

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 4
EXIT_INVALID_APPIMAGE = 5
EXIT_DAEMON_REQUIRED = 6
EXIT_UPDATES_AVAILABLE = 10


class CLIError(Exception):
    """CLI error with exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR):
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


class Colors:
    """ANSI color codes."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @classmethod
    def enabled(cls) -> bool:
        return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"

    @classmethod
    def colorize(cls, text: str, color: str) -> str:
        if cls.enabled():
            return f"{color}{text}{cls.RESET}"
        return text


def print_error(msg: str) -> None:
    """Print error message to stderr."""
    prefix = Colors.colorize("Error:", Colors.RED) if Colors.enabled() else "Error:"
    print(f"{prefix} {msg}", file=sys.stderr)


def print_warning(msg: str) -> None:
    """Print warning message to stderr."""
    prefix = Colors.colorize("Warning:", Colors.YELLOW) if Colors.enabled() else "Warning:"
    print(f"{prefix} {msg}", file=sys.stderr)


def print_info(msg: str) -> None:
    """Print info message."""
    print(msg)


def print_json(data: Any) -> None:
    """Print JSON output."""
    print(json.dumps(data, indent=2, default=str))


def format_size(size: int) -> str:
    """Format size in human-readable IEC units."""
    size_float = float(size)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if size_float < 1024:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024
    return f"{size_float:.1f} PiB"


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser."""
    parser = argparse.ArgumentParser(
        prog="appimage",
        description="AppImage Launcher - Manage AppImages in ~/Applications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  appimage install ~/Downloads/Gimp.appimage
  appimage list
  appimage info Gimp
  appimage update --check
  appimage policy Gimp pinned
  appimage doctor
        """,
    )

    # Global flags
    parser.add_argument(
        "--dir",
        type=Path,
        help="Applications directory (default: ~/Applications)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable format",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress non-error output",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (can use multiple times)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="Force direct mode (don't use D-Bus daemon)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    # install command
    install_parser = subparsers.add_parser("install", help="Install AppImage(s)")
    install_parser.add_argument("files", nargs="+", type=Path, help="AppImage file(s) to install")
    install_parser.add_argument("--name", help="Custom name for the app")
    install_parser.add_argument(
        "--move",
        action="store_const",
        const="move",
        dest="source_action",
        help="Move file instead of copying",
    )
    install_parser.add_argument(
        "--copy",
        action="store_const",
        const="copy",
        dest="source_action",
        help="Copy file (default)",
    )
    install_parser.add_argument(
        "--replace",
        action="store_const",
        const="replace",
        dest="conflict_policy",
        help="Replace existing version",
    )
    install_parser.add_argument(
        "--keep",
        action="store_const",
        const="keep",
        dest="conflict_policy",
        help="Keep both versions",
    )
    install_parser.add_argument(
        "--cancel",
        action="store_const",
        const="cancel",
        dest="conflict_policy",
        help="Cancel on conflict",
    )
    install_parser.add_argument(
        "--conflict-error",
        action="store_const",
        const="error",
        dest="conflict_policy",
        help="Return conflict info as JSON and exit 4",
    )
    install_parser.add_argument("--origin", help="Origin record as JSON string or @file")
    install_parser.add_argument(
        "--silent", action="store_true", help="Suppress success notification"
    )
    install_parser.add_argument(
        "--update-policy",
        choices=["auto", "manual", "pinned"],
        default="auto",
        help="Initial update policy (default: auto)",
    )
    install_parser.add_argument("--yes", action="store_true", help="Assume yes for prompts")

    # remove command
    remove_parser = subparsers.add_parser("remove", help="Remove installed app(s)")
    remove_parser.add_argument("names", nargs="+", help="App name(s) to remove")
    remove_parser.add_argument(
        "--file-action",
        choices=["trash", "keep", "purge"],
        default="trash",
        help="What to do with the file: trash (default), keep, or purge",
    )
    remove_parser.add_argument("--confirm", action="store_true", help="Confirm purge action")
    remove_parser.add_argument(
        "--silent", action="store_true", help="Suppress success notification"
    )
    remove_parser.add_argument("--yes", action="store_true", help="Assume yes for prompts")

    # rename command
    rename_parser = subparsers.add_parser("rename", help="Rename an app")
    rename_parser.add_argument("name", help="Current app name")
    rename_parser.add_argument("new_name", nargs="?", help="New name (omit with --reset)")
    rename_parser.add_argument(
        "--reset", action="store_true", help="Reset to default name from AppImage"
    )

    # list command
    list_parser = subparsers.add_parser("list", help="List installed apps")
    list_parser.add_argument(
        "--sort", choices=["name", "size", "version", "date"], default="name", help="Sort order"
    )
    list_parser.add_argument(
        "--all", action="store_true", help="Include kept and previous versions"
    )
    list_parser.add_argument("--hash", action="store_true", help="Compute and show SHA-256 hashes")

    # info command
    info_parser = subparsers.add_parser("info", help="Show app details")
    info_parser.add_argument("name", help="App name or path to AppImage file")
    info_parser.add_argument("--hash", action="store_true", help="Compute and show SHA-256")

    # update command
    update_parser = subparsers.add_parser("update", help="Check or apply updates")
    update_parser.add_argument("name", nargs="?", help="App name (omit for --all)")
    update_parser.add_argument("--all", action="store_true", help="Check all apps")
    update_parser.add_argument("--check", action="store_true", help="Only check, don't apply")

    # policy command
    policy_parser = subparsers.add_parser("policy", help="Set update policy")
    policy_parser.add_argument("name", help="App name")
    policy_parser.add_argument("policy", choices=["auto", "manual", "pinned"], help="Policy to set")

    # origin command
    origin_parser = subparsers.add_parser("origin", help="Manage origin record")
    origin_parser.add_argument("name", help="App name")
    origin_parser.add_argument("--set", dest="origin_set", help="Set origin (JSON string or @file)")
    origin_parser.add_argument("--clear", action="store_true", help="Clear origin")

    # revert command
    revert_parser = subparsers.add_parser("revert", help="Revert to previous version")
    revert_parser.add_argument("name", help="App name")

    # versions command
    versions_parser = subparsers.add_parser("versions", help="List kept and previous versions")
    versions_parser.add_argument("name", help="App name")

    # disable/enable commands
    disable_parser = subparsers.add_parser("disable", help="Disable app (move to .disabled)")
    disable_parser.add_argument("name", help="App name")

    enable_parser = subparsers.add_parser("enable", help="Enable app (restore from .disabled)")
    enable_parser.add_argument("name", help="App name")

    # run command
    run_parser = subparsers.add_parser("run", help="Launch an app")
    run_parser.add_argument("name", help="App name")
    run_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments to pass to the app")
    run_parser.add_argument(
        "--safe", action="store_true", help="Launch with --appimage-extract-and-run (no FUSE)"
    )

    # browse command
    browse_parser = subparsers.add_parser("browse", help="Browse AppImage contents")
    browse_parser.add_argument("name", help="App name")
    browse_parser.add_argument(
        "--exec-fallback", action="store_true", help="Use --appimage-mount fallback"
    )
    browse_parser.add_argument("--unmount", action="store_true", help="Unmount instead of mounting")

    # extract command
    extract_parser = subparsers.add_parser("extract", help="Extract AppImage squashfs")
    extract_parser.add_argument("name", help="App name")
    extract_parser.add_argument("dest", type=Path, help="Destination directory")

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify AppImage integrity")
    verify_parser.add_argument("name", help="App name")

    # reindex command
    subparsers.add_parser("reindex", help="Rebuild database from Applications dir")

    # doctor command
    subparsers.add_parser("doctor", help="Environment diagnostics")

    # config command
    config_parser = subparsers.add_parser("config", help="Manage configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    get_parser = config_subparsers.add_parser("get", help="Get config value")
    get_parser.add_argument("key")
    set_parser = config_subparsers.add_parser("set", help="Set config value")
    set_parser.add_argument("key")
    set_parser.add_argument("value")
    config_subparsers.add_parser("list", help="List all config")

    # completions command
    completions_parser = subparsers.add_parser("completions", help="Print shell completions")
    completions_parser.add_argument("shell", choices=["bash", "zsh", "fish"])

    # status command
    subparsers.add_parser("status", help="Show daemon status")

    # daemon command
    daemon_parser = subparsers.add_parser("daemon", help="Run the observer daemon")
    daemon_parser.add_argument("--dir", type=Path, help="Applications directory to watch")
    daemon_parser.add_argument("--no-notify", action="store_true", help="Disable notifications")
    daemon_parser.add_argument("--no-dialogs", action="store_true", help="Disable dialogs")
    daemon_parser.add_argument("--dev", action="store_true", help="Development mode")

    # export/import commands
    export_parser = subparsers.add_parser("export", help="Export policies, names and flags")
    export_parser.add_argument("file", nargs="?", type=Path, help="Output file (default: stdout)")

    import_parser = subparsers.add_parser("import", help="Import policies, names and flags")
    import_parser.add_argument("file", type=Path, help="Input file")

    # dedupe command
    dedupe_parser = subparsers.add_parser("dedupe", help="Find duplicate AppImages by hash")
    dedupe_parser.add_argument(
        "dirs", nargs="*", type=Path, help="Directories to scan (default: ~/Downloads)"
    )

    # cleanup command
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Remove old .previous files and orphaned entries/icons"
    )
    cleanup_parser.add_argument(
        "--days", type=int, default=30, help="Remove .previous older than N days (default: 30)"
    )

    return parser


def get_manager(args: argparse.Namespace) -> Manager | DBusClientManager:
    """Get Manager instance, using daemon or direct mode."""
    if args.no_daemon:
        logger.debug("Direct mode forced by --no-daemon")
        return Manager()

    # Try to connect to daemon via D-Bus
    try:
        return DBusClientManager()
    except Exception as e:
        logger.debug("Daemon not available, falling back to direct mode", error=str(e))
        print_warning("daemon not running; using direct mode")
        return Manager()


class DBusClientManager:
    """Manager that forwards operations to the daemon via D-Bus."""

    def __init__(self):
        import asyncio

        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        self._loop = asyncio.new_event_loop()
        self._bus = None
        self._proxy = None
        self._interface = None
        self._MessageBus = MessageBus
        self._BusType = BusType
        self._connect()

    def _connect(self) -> None:
        """Connect to the D-Bus daemon with a 5 second timeout."""
        import asyncio

        from appimage_launcher.constants import (
            DBUS_INTERFACE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_SERVICE_NAME,
        )

        async def _do_connect():
            self._bus = await self._MessageBus(bus_type=self._BusType.SESSION).connect()
            introspection = await self._bus.introspect(DBUS_SERVICE_NAME, DBUS_OBJECT_PATH)
            self._proxy = self._bus.get_proxy_object(
                DBUS_SERVICE_NAME, DBUS_OBJECT_PATH, introspection
            )
            self._interface = self._proxy.get_interface(DBUS_INTERFACE_NAME)

        try:
            self._loop.run_until_complete(asyncio.wait_for(_do_connect(), timeout=5.0))
        except TimeoutError as e:
            raise CLIError("Daemon not available (connection timeout)") from e
        except Exception as e:
            raise CLIError(f"Daemon not available: {e}") from e

    @staticmethod
    def _pascal_to_snake_call(method_name: str) -> str:
        """Convert PascalCase method name to dbus-fast's call_<snake_case> format.

        Examples:
            Install -> call_install
            CheckUpdates -> call_check_updates
            SetOrigin -> call_set_origin
        """
        import re

        snake = re.sub(r"([A-Z])", r"_\1", method_name).lower().lstrip("_")
        return f"call_{snake}"

    @staticmethod
    def _wrap_variants(d: dict) -> dict:
        """Wrap values in D-Bus Variants for a{sv} dictionary."""
        from dbus_fast import Variant

        result = {}
        for k, v in d.items():
            if isinstance(v, bool):
                result[k] = Variant("b", v)
            elif isinstance(v, int):
                result[k] = Variant("i", v)
            elif isinstance(v, str):
                result[k] = Variant("s", v)
            else:
                result[k] = v
        return result

    def _call(self, method_name: str, *args) -> Any:
        """Call a D-Bus method synchronously.

        The dbus-fast proxy exposes methods as call_<snake_case> (e.g., call_install,
        call_check_updates, call_set_origin). This method converts the PascalCase
        D-Bus method name to the correct proxy method name.
        """
        proxy_method_name = self._pascal_to_snake_call(method_name)
        method = getattr(self._interface, proxy_method_name)
        future = method(*args)
        try:
            return self._loop.run_until_complete(future)
        except Exception as e:
            raise CLIError(f"Daemon call {method_name} failed: {e}", EXIT_DAEMON_REQUIRED) from e

    # Delegate methods to D-Bus

    def install(
        self,
        source_path,
        name=None,
        source_action="copy",
        conflict_policy="config",
        origin=None,
        silent=False,
        update_policy="auto",
    ):
        import json

        from appimage_launcher.core.manager import ConflictError, ConflictInfo, InstallResult
        from appimage_launcher.core.versions import VersionRelation

        options = {
            "source_action": source_action,
            "conflict": conflict_policy,
            "silent": silent,
            "update_policy": update_policy,
        }
        if name:
            options["name"] = name
        if origin:
            options["origin"] = origin

        result_json = self._call("Install", str(source_path), self._wrap_variants(options))
        result = json.loads(result_json)

        if result.get("error") == "conflict" or "ConflictPending" in str(result):
            # Handle conflict
            result.get("conflict", result)
            raise ConflictError(
                ConflictInfo(
                    old_app=None,  # Would need to reconstruct
                    new_meta=None,
                    relation=VersionRelation.NONE,
                    is_self_update=False,
                )
            )

        # Reconstruct AppRecord from result
        from appimage_launcher.core.state import AppRecord

        app_data = result["app"]
        app = AppRecord(
            id=0,
            app_id=app_data["app_id"],
            role=app_data["role"],
            display_name=app_data["name"],
            managed_name=app_data["managed_name"],
            name_origin=app_data["name_origin"],
            orig_filename=None,
            path=app_data["path"],
            device=0,
            inode=0,
            size=app_data["size"],
            mtime_ns=0,
            fingerprint="",
            version=app_data["version"],
            update_info=None,
            update_policy=app_data["update_policy"],
            self_managed=app_data["self_managed"],
            origin=json.dumps(app_data["origin"]) if app_data["origin"] else None,
            sha256=app_data.get("sha256"),
            installed_at=app_data["installed_at"],
            updated_at=app_data["updated_at"],
        )

        return InstallResult(app=app, changed=result["changed"], previous=result.get("previous"))

    def remove(self, name, file_action="trash", keep_file=False, purge=False):
        options = {
            "file_action": "purge" if purge else ("keep" if keep_file else file_action),
            "confirm": purge,
            "silent": False,
        }
        self._call("Remove", name, self._wrap_variants(options))

    def rename(self, name, new_name, reset=False):
        if reset:
            # Need to get current app to find default name
            app = self._find_app_by_name(name)
            new_name = app.display_name
        result_json = self._call("Rename", name, new_name)
        import json

        app_data = json.loads(result_json)
        from appimage_launcher.core.state import AppRecord

        return AppRecord(
            id=0,
            app_id=app_data["app_id"],
            role=app_data["role"],
            display_name=app_data["name"],
            managed_name=app_data["managed_name"],
            name_origin=app_data["name_origin"],
            orig_filename=None,
            path=app_data["path"],
            device=0,
            inode=0,
            size=app_data["size"],
            mtime_ns=0,
            fingerprint="",
            version=app_data["version"],
            update_info=None,
            update_policy=app_data["update_policy"],
            self_managed=app_data["self_managed"],
            origin=json.dumps(app_data["origin"]) if app_data["origin"] else None,
            sha256=app_data.get("sha256"),
            installed_at=app_data["installed_at"],
            updated_at=app_data["updated_at"],
        )

    def set_policy(self, name, policy):
        self._call("SetPolicy", name, policy)
        app = self._find_app_by_name(name)
        app.update_policy = policy
        return app

    def set_origin(self, name, origin_json):
        result_json = self._call("SetOrigin", name, origin_json or "")
        import json

        app_data = json.loads(result_json)
        from appimage_launcher.core.state import AppRecord

        return AppRecord(
            id=0,
            app_id=app_data["app_id"],
            role=app_data["role"],
            display_name=app_data["name"],
            managed_name=app_data["managed_name"],
            name_origin=app_data["name_origin"],
            orig_filename=None,
            path=app_data["path"],
            device=0,
            inode=0,
            size=app_data["size"],
            mtime_ns=0,
            fingerprint="",
            version=app_data["version"],
            update_info=None,
            update_policy=app_data["update_policy"],
            self_managed=app_data["self_managed"],
            origin=json.dumps(app_data["origin"]) if app_data["origin"] else None,
            sha256=app_data.get("sha256"),
            installed_at=app_data["installed_at"],
            updated_at=app_data["updated_at"],
        )

    def check_updates(self, name):
        from appimage_launcher.core.updates import UpdateCheckResult

        result_json = self._call("CheckUpdates", [name])
        import json

        results = json.loads(result_json)
        if not results:
            return UpdateCheckResult(
                has_update=False,
                current_version=None,
                latest_version=None,
                update_info=None,
                error="not_found",
            )
        result = results[0]
        return UpdateCheckResult(
            has_update=result.get("has_update", False),
            current_version=result.get("current_version"),
            latest_version=result.get("latest_version"),
            update_info=None,  # Would need to reconstruct
            error=result.get("skip_reason"),
        )

    def revert(self, name):
        self._call("Revert", name)
        return self._find_app_by_name(name)

    def apply_update(self, name, staging_dir=None):
        import json

        from appimage_launcher.core.updates import UpdateApplyResult

        result_json = self._call("Update", name)
        result = json.loads(result_json)
        return UpdateApplyResult(
            success=result.get("success", False),
            output_path=Path(result["output_path"]) if result.get("output_path") else None,
            error=result.get("error"),
            signature_verified=result.get("signature_verified", False),
        )

    def dedupe(self, dirs=None):
        # Not available via D-Bus yet
        return {"duplicates": []}

    def cleanup(self, days=30):
        # Not available via D-Bus yet
        return {
            "removed_previous": 0,
            "removed_previous_size": 0,
            "removed_entries": 0,
            "removed_icons": 0,
        }

    def disable(self, name):
        raise NotImplementedError("disable is not available via D-Bus daemon")

    def enable(self, name):
        raise NotImplementedError("enable is not available via D-Bus daemon")

    def export_data(self):
        raise NotImplementedError("export is not available via D-Bus daemon")

    def import_data(self, data):
        raise NotImplementedError("import is not available via D-Bus daemon")

    def _find_app_by_name(self, name):
        """Find app by name via D-Bus."""
        result_json = self._call("Info", name)
        import json

        app_data = json.loads(result_json)
        from appimage_launcher.core.state import AppRecord

        return AppRecord(
            id=0,
            app_id=app_data["app_id"],
            role=app_data["role"],
            display_name=app_data["name"],
            managed_name=app_data["managed_name"],
            name_origin=app_data["name_origin"],
            orig_filename=None,
            path=app_data["path"],
            device=0,
            inode=0,
            size=app_data["size"],
            mtime_ns=0,
            fingerprint="",
            version=app_data["version"],
            update_info=None,
            update_policy=app_data["update_policy"],
            self_managed=app_data["self_managed"],
            origin=json.dumps(app_data["origin"]) if app_data["origin"] else None,
            sha256=app_data.get("sha256"),
            installed_at=app_data["installed_at"],
            updated_at=app_data["updated_at"],
        )

    @property
    def db(self):
        """Return a DBusDBProxy for database operations."""
        return DBusDBProxy(self)


class DBusDBProxy:
    """Proxy for database operations via D-Bus."""

    def __init__(self, client_manager: DBusClientManager):
        self._client = client_manager

    def list_apps(self, include_kept=False, compute_hash=False):
        options = {"all": include_kept, "hash": compute_hash}
        result_json = self._client._call("List", self._client._wrap_variants(options))
        import json

        apps_data = json.loads(result_json)
        from appimage_launcher.core.state import AppRecord

        return [
            AppRecord(
                id=0,
                app_id=a["app_id"],
                role=a["role"],
                display_name=a["name"],
                managed_name=a["managed_name"],
                name_origin=a["name_origin"],
                orig_filename=None,
                path=a["path"],
                device=0,
                inode=0,
                size=a["size"],
                mtime_ns=0,
                fingerprint="",
                version=a["version"],
                update_info=None,
                update_policy=a["update_policy"],
                self_managed=a["self_managed"],
                origin=json.dumps(a["origin"]) if a["origin"] else None,
                sha256=a.get("sha256"),
                installed_at=a["installed_at"],
                updated_at=a["updated_at"],
            )
            for a in apps_data
        ]

    def get_backups(self, app_id):
        # Not directly available via D-Bus, would need to extend API
        return []

    def insert_event(self, kind, app_id=None, detail=None):
        # Not needed for client
        pass


def load_origin(origin_arg: str | None) -> str | None:
    """Load origin from JSON string or @file."""
    if not origin_arg:
        return None
    if origin_arg.startswith("@"):
        path = Path(origin_arg[1:])
        return path.read_text()
    return origin_arg


def cmd_install(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle install command."""
    for file_path in args.files:
        if not file_path.exists():
            raise CLIError(f"File not found: {file_path}", EXIT_NOT_FOUND)

        origin = load_origin(args.origin)

        try:
            result = manager.install(
                source_path=file_path,
                name=args.name,
                source_action=args.source_action or "copy",
                conflict_policy=args.conflict_policy or "config",
                origin=origin,
                silent=args.silent,
                update_policy=args.update_policy,
            )
        except ConflictError as e:
            if args.json:
                print_json(
                    {
                        "error": "conflict",
                        "old_app": {
                            "app_id": e.conflict_info.old_app.app_id,
                            "name": e.conflict_info.old_app.managed_name,
                            "version": e.conflict_info.old_app.version,
                        },
                        "new_meta": {
                            "name": e.conflict_info.new_meta.name,
                            "version": e.conflict_info.new_meta.version,
                            "app_id": e.conflict_info.new_meta.app_id,
                        },
                        "relation": e.conflict_info.relation.name.lower(),
                    }
                )
            else:
                print_error(f"Conflict: {e.conflict_info.old_app.managed_name} already installed")
                print_error(f"  Old version: {e.conflict_info.old_app.version}")
                print_error(f"  New version: {e.conflict_info.new_meta.version}")
                print_error(f"  Relation: {e.conflict_info.relation.name}")
            return EXIT_CONFLICT
        except ValueError as e:
            raise CLIError(str(e), EXIT_ERROR) from e

        if args.json:
            print_json(
                {
                    "app": {
                        "app_id": result.app.app_id,
                        "name": result.app.display_name,
                        "managed_name": result.app.managed_name,
                        "version": result.app.version,
                        "path": result.app.path,
                    },
                    "changed": result.changed,
                    "previous": result.previous,
                }
            )
        elif not args.quiet:
            if result.changed:
                print_info(f"Installed {result.app.managed_name} {result.app.version or ''}")
            else:
                print_info(f"Already installed: {result.app.managed_name}")

    return EXIT_OK


def cmd_remove(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle remove command."""
    for name in args.names:
        try:
            file_action = args.file_action
            if file_action == "purge" and not args.confirm and not args.yes:
                raise CLIError("Purge requires --confirm or --yes", EXIT_ERROR)

            manager.remove(
                name,
                file_action=file_action,
                keep_file=(file_action == "keep"),
                purge=(file_action == "purge"),
            )
        except ValueError as e:
            raise CLIError(str(e), EXIT_NOT_FOUND) from e

        if not args.quiet and not args.silent:
            print_info(f"Removed {name}")

    return EXIT_OK


def cmd_rename(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle rename command."""
    try:
        app = manager.rename(args.name, args.new_name or "", reset=args.reset)
    except ValueError as e:
        raise CLIError(str(e), EXIT_ERROR) from e

    if args.json:
        print_json(
            {
                "app_id": app.app_id,
                "managed_name": app.managed_name,
                "name_origin": app.name_origin,
            }
        )
    elif not args.quiet:
        print_info(f"Renamed to {app.managed_name}")

    return EXIT_OK


def cmd_list(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle list command."""
    apps = manager.db.list_apps(include_kept=args.all, compute_hash=args.hash)

    # Sort
    if args.sort == "name":
        apps.sort(key=lambda a: a.managed_name.lower())
    elif args.sort == "size":
        apps.sort(key=lambda a: a.size, reverse=True)
    elif args.sort == "version":
        apps.sort(key=lambda a: a.version or "", reverse=True)
    elif args.sort == "date":
        apps.sort(key=lambda a: a.installed_at, reverse=True)

    if args.json:
        print_json(
            [
                {
                    "app_id": a.app_id,
                    "name": a.display_name,
                    "managed_name": a.managed_name,
                    "role": a.role,
                    "version": a.version,
                    "size": a.size,
                    "path": a.path,
                    "update_policy": a.update_policy,
                    "sha256": a.sha256 if args.hash else None,
                }
                for a in apps
            ]
        )
        return EXIT_OK

    if not apps:
        print_info("No apps installed")
        return EXIT_OK

    # Table output
    print(f"{'NAME':<30} {'VERSION':<15} {'SIZE':>10} {'STATE':<8} {'UPDATES':<8} PATH")
    print("-" * 100)

    total_size = 0
    for app in apps:
        size_str = format_size(app.size)
        state = "active" if app.role == "primary" else "kept"
        updates = app.update_policy if app.role == "primary" else "-"
        path_str = app.path
        if len(path_str) > 50:
            path_str = "..." + path_str[-47:]

        print(
            f"{app.managed_name:<30} {app.version or '-':<15} {size_str:>10} {state:<8} {updates:<8} {path_str}"
        )
        total_size += app.size

    print("-" * 100)
    print(f"{len(apps)} apps                {format_size(total_size):>10}")

    return EXIT_OK


def cmd_info(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle info command."""
    # Check if it's a file path
    path = Path(args.name)
    if path.exists() and path.is_file():
        # Inspect file without installing
        try:
            meta = read_appimage_meta(path)
        except Exception as e:
            raise CLIError(f"Invalid AppImage: {e}", EXIT_INVALID_APPIMAGE) from e

        if args.json:
            print_json(
                {
                    "type": meta.type,
                    "name": meta.name,
                    "version": meta.version,
                    "app_id": meta.app_id,
                    "comment": meta.comment,
                    "categories": meta.categories,
                    "update_info": meta.update_info,
                    "has_signature": meta.has_signature,
                    "size": meta.size,
                    "fingerprint": meta.fingerprint,
                }
            )
        else:
            print_info(f"Name: {meta.name}")
            print_info(f"Version: {meta.version or 'unknown'}")
            print_info(f"App ID: {meta.app_id}")
            print_info(f"Comment: {meta.comment or 'none'}")
            print_info(f"Categories: {', '.join(meta.categories) if meta.categories else 'none'}")
            print_info(f"Update info: {meta.update_info or 'none'}")
            print_info(f"Signature: {'yes' if meta.has_signature else 'no'}")
            print_info(f"Size: {format_size(meta.size)}")
        return EXIT_OK

    # Find installed app
    app = manager._find_app_by_name(args.name)
    if not app:
        raise CLIError(f"App not found: {args.name}", EXIT_NOT_FOUND)

    if args.json:
        print_json(
            {
                "app_id": app.app_id,
                "name": app.display_name,
                "managed_name": app.managed_name,
                "name_origin": app.name_origin,
                "role": app.role,
                "version": app.version,
                "path": app.path,
                "size": app.size,
                "sha256": app.sha256,
                "orig_filename": app.orig_filename,
                "update_policy": app.update_policy,
                "self_managed": app.self_managed,
                "origin": app.origin,
                "installed_at": app.installed_at,
                "updated_at": app.updated_at,
            }
        )
    else:
        print_info(f"Name: {app.display_name}")
        print_info(f"Managed name: {app.managed_name}")
        print_info(f"App ID: {app.app_id}")
        print_info(f"Version: {app.version or 'unknown'}")
        print_info(f"Path: {app.path}")
        print_info(f"Size: {format_size(app.size)}")
        print_info(f"Name origin: {app.name_origin}")
        print_info(f"Role: {app.role}")
        print_info(f"Update policy: {app.update_policy}")
        print_info(f"Self-managed: {'yes' if app.self_managed else 'no'}")
        if app.origin:
            print_info(f"Origin: {app.origin}")
        print_info(f"Installed: {app.installed_at}")
        print_info(f"Updated: {app.updated_at}")

    return EXIT_OK


def cmd_update(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle update command."""
    get_config()

    if args.name:
        names = [args.name]
    elif args.all:
        apps = manager.db.list_apps(include_kept=False)
        names = [a.managed_name for a in apps]
    else:
        raise CLIError("Specify an app name or use --all", EXIT_USAGE)

    if args.check:
        # Check for updates
        any_updates = False
        for name in names:
            check_result = manager.check_updates(name)

            if args.json:
                print_json(
                    {
                        "app_id": check_result.update_info.args[0]
                        if check_result.update_info and check_result.update_info.args
                        else "",
                        "has_update": check_result.has_update,
                        "current_version": check_result.current_version,
                        "latest_version": check_result.latest_version,
                        "skipped": check_result.error is not None,
                        "skip_reason": check_result.error,
                    }
                )
            else:
                if check_result.error:
                    if check_result.error == "catalog_linked":
                        print_info(
                            f"Skipping {name}: catalog-linked (updates managed by front-end)"
                        )
                    elif check_result.error == "not_found":
                        print_error(f"App not found: {name}")
                    else:
                        print_info(f"No updates for {name}: {check_result.error}")
                elif check_result.has_update:
                    any_updates = True
                    print_info(
                        f"Update available for {name}: {check_result.current_version} -> {check_result.latest_version or 'unknown'}"
                    )
                else:
                    print_info(
                        f"No updates available for {name} ({check_result.current_version or 'unknown'})"
                    )

        # Exit with code 10 if updates available (for --check)
        if any_updates:
            return EXIT_UPDATES_AVAILABLE
    else:
        # Apply updates
        for name in names:
            apply_result = manager.apply_update(name)

            if args.json:
                print_json(
                    {
                        "success": apply_result.success,
                        "output_path": str(apply_result.output_path)
                        if apply_result.output_path
                        else None,
                        "error": apply_result.error,
                        "signature_verified": apply_result.signature_verified,
                    }
                )
            else:
                if apply_result.success:
                    print_info(f"Successfully updated {name}")
                else:
                    print_error(f"Failed to update {name}: {apply_result.error}")
                    return EXIT_ERROR

    return EXIT_OK


def cmd_policy(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle policy command."""
    try:
        app = manager.set_policy(args.name, args.policy)
    except ValueError as e:
        raise CLIError(str(e), EXIT_ERROR) from e

    if not args.quiet:
        print_info(f"Set policy for {app.managed_name} to {args.policy}")

    return EXIT_OK


def cmd_origin(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle origin command."""
    origin = load_origin(args.origin_set) if args.origin_set else None

    try:
        app = manager.set_origin(args.name, origin)
    except ValueError as e:
        raise CLIError(str(e), EXIT_ERROR) from e

    if args.json:
        print_json({"origin": app.origin})
    elif not args.quiet:
        if app.origin:
            print_info(f"Origin set for {app.managed_name}")
        else:
            print_info(f"Origin cleared for {app.managed_name}")

    return EXIT_OK


def cmd_revert(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle revert command."""
    try:
        app = manager.revert(args.name)
    except ValueError as e:
        raise CLIError(str(e), EXIT_ERROR) from e

    if not args.quiet:
        print_info(f"Reverted {app.managed_name} to version {app.version}")

    return EXIT_OK


def cmd_versions(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle versions command."""
    app = manager._find_app_by_name(args.name)
    if not app:
        raise CLIError(f"App not found: {args.name}", EXIT_NOT_FOUND)

    backups = manager.db.get_backups(app.app_id)

    if args.json:
        print_json(
            [
                {
                    "version": b.version,
                    "size": b.size,
                    "path": b.path,
                    "created_at": b.created_at,
                }
                for b in backups
            ]
        )
    else:
        if not backups:
            print_info("No previous versions")
        else:
            print_info(f"Previous versions for {app.managed_name}:")
            for b in backups:
                print_info(f"  {b.version} - {format_size(b.size)} - {b.path}")

    return EXIT_OK


def cmd_disable(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle disable command."""
    if isinstance(manager, DBusClientManager):
        raise CLIError(
            "disable is not available via D-Bus daemon; use --no-daemon", EXIT_DAEMON_REQUIRED
        )
    try:
        app = manager.disable(args.name)
    except ValueError as e:
        raise CLIError(str(e), EXIT_ERROR) from e

    if not args.quiet:
        print_info(f"Disabled {app.managed_name}")

    return EXIT_OK


def cmd_enable(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle enable command."""
    if isinstance(manager, DBusClientManager):
        raise CLIError(
            "enable is not available via D-Bus daemon; use --no-daemon", EXIT_DAEMON_REQUIRED
        )
    try:
        app = manager.enable(args.name)
    except ValueError as e:
        raise CLIError(str(e), EXIT_ERROR) from e

    if not args.quiet:
        print_info(f"Enabled {app.managed_name}")

    return EXIT_OK


def cmd_run(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle run command."""
    app = manager._find_app_by_name(args.name)
    if not app:
        raise CLIError(f"App not found: {args.name}", EXIT_NOT_FOUND)

    # Execute the AppImage
    import subprocess

    if args.safe:
        cmd = [app.path, "--appimage-extract-and-run"] + args.args
    else:
        cmd = [app.path] + args.args
    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        raise CLIError(f"Failed to run app: {e}", EXIT_ERROR) from e

    return EXIT_OK


def cmd_browse(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle browse command."""
    # TODO: Implement browse with squashfuse
    print_warning("Browse not yet implemented")
    return EXIT_OK


def cmd_extract(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle extract command."""
    # TODO: Implement extract
    print_warning("Extract not yet implemented")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle verify command."""
    # TODO: Implement verify
    print_warning("Verify not yet implemented")
    return EXIT_OK


def cmd_reindex(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle reindex command."""
    # TODO: Implement reindex
    print_warning("Reindex not yet implemented")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle doctor command."""
    print_info("AppImage Launcher Diagnostics")
    print_info("=" * 40)

    # Check tools
    tools = [
        ("unsquashfs", "squashfs-tools"),
        ("squashfuse", "squashfuse"),
        ("appimageupdatetool", "appimageupdatetool"),
        ("zsync", "zsync"),
        ("gio", "glib2"),
        ("kioclient", "kde-cli-tools"),
        ("update-desktop-database", "desktop-file-utils"),
        ("gtk-update-icon-cache", "gtk3"),
        ("kbuildsycoca6", "kf6-kio"),
    ]

    print_info("\nTools:")
    for tool, pkg in tools:
        import shutil

        path = shutil.which(tool)
        status = (
            Colors.colorize("OK", Colors.GREEN) if path else Colors.colorize("MISSING", Colors.RED)
        )
        print_info(f"  {tool:<25} {status} ({pkg})")

    # Check Python packages
    print_info("\nPython packages:")
    packages = ["inotify_simple", "dbus_fast", "PyQt6"]
    for pkg in packages:
        try:
            __import__(pkg.replace("-", "_"))
            status = Colors.colorize("OK", Colors.GREEN)
        except ImportError:
            status = Colors.colorize("MISSING", Colors.RED)
        print_info(f"  {pkg:<20} {status}")

    # Check paths
    paths = get_paths()
    print_info("\nPaths:")
    print_info(f"  Applications dir: {paths.applications_dir}")
    print_info(f"  Config dir: {paths.config_dir}")
    print_info(f"  State dir: {paths.state_dir}")
    print_info(f"  Data dir: {paths.data_dir}")

    # Check daemon
    print_info("\nDaemon:")
    import subprocess

    result = subprocess.run(
        ["systemctl", "--user", "is-active", "appimage-launcher"], capture_output=True
    )
    status = result.stdout.decode().strip()
    color = Colors.GREEN if status == "active" else Colors.RED
    print_info(f"  systemd status: {Colors.colorize(status, color)}")

    # Check D-Bus
    print_info("\nD-Bus:")
    result = subprocess.run(
        [
            "dbus-send",
            "--session",
            "--dest=io.github.appimage_launcher.Manager",
            "--type=method_call",
            "--print-reply",
            "/io/github/appimage_launcher/Manager",
            "io.github.appimage_launcher.Manager1.APIVersion",
        ],
        capture_output=True,
        timeout=5,
    )
    if result.returncode == 0:
        print_info(f"  D-Bus service: {Colors.colorize('REACHABLE', Colors.GREEN)}")
    else:
        print_info(f"  D-Bus service: {Colors.colorize('UNREACHABLE', Colors.RED)}")

    # Check MIME/thumbnail for extensionless AppImages
    print_info("\nMIME/Thumbnail:")
    result = subprocess.run(["kmimetypefinder", "test.appimage"], capture_output=True, timeout=5)
    if result.returncode == 0:
        mime = result.stdout.decode().strip()
        if "application/vnd.appimage" in mime:
            print_info(f"  MIME detection: {Colors.colorize('OK', Colors.GREEN)} ({mime})")
        else:
            print_info(f"  MIME detection: {Colors.colorize('WRONG', Colors.YELLOW)} ({mime})")
    else:
        print_info(
            f"  MIME detection: {Colors.colorize('FAILED', Colors.RED)} (kmimetypefinder not available)"
        )

    # Check thumbnailer
    import os

    thumb_paths = [
        "/usr/share/thumbnailers/appimage.thumbnailer",
        "/usr/lib/qt6/plugins/kio_thumbnail.so",
    ]
    found = any(os.path.exists(p) for p in thumb_paths)
    if found:
        print_info(f"  Thumbnailer: {Colors.colorize('INSTALLED', Colors.GREEN)}")
    else:
        print_info(
            f"  Thumbnailer: {Colors.colorize('MISSING', Colors.YELLOW)} (install kio-extras or appimage-thumbnailer)"
        )

    # Check user namespaces for pinned mode
    print_info("\nNamespaces:")
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone") as f:
            val = f.read().strip()
        if val == "1":
            print_info(
                f"  Unprivileged user namespaces: {Colors.colorize('ENABLED', Colors.GREEN)}"
            )
        else:
            print_info(
                f"  Unprivileged user namespaces: {Colors.colorize('DISABLED', Colors.YELLOW)} (pinned mode will use hardlink fallback)"
            )
    except FileNotFoundError:
        print_info(
            f"  Unprivileged user namespaces: {Colors.colorize('UNKNOWN', Colors.YELLOW)} (kernel param not found)"
        )

    # Check bwrap for pinned mode
    import shutil

    bwrap_path = shutil.which("bwrap")
    if bwrap_path:
        print_info(f"  bubblewrap: {Colors.colorize('AVAILABLE', Colors.GREEN)} ({bwrap_path})")
    else:
        print_info(
            f"  bubblewrap: {Colors.colorize('MISSING', Colors.YELLOW)} (pinned mode will use hardlink fallback)"
        )

    return EXIT_OK


def cmd_config(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle config command."""
    config = get_config()

    if args.config_command == "get":
        # Navigate nested config
        keys = args.key.split(".")
        value = config
        for key in keys:
            if hasattr(value, key):
                value = getattr(value, key)
            else:
                raise CLIError(f"Config key not found: {args.key}", EXIT_ERROR)
        print_info(str(value))

    elif args.config_command == "set":
        # TODO: Implement config set
        print_warning("Config set not yet implemented")

    elif args.config_command == "list":
        # Print all config
        import tomli_w

        print_info(tomli_w.dumps(config.to_dict()))

    return EXIT_OK


def cmd_completions(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle completions command."""
    # Read completion script from packaging
    completion_files = {
        "bash": "packaging/completions/appimage.bash",
        "zsh": "packaging/completions/_appimage",
        "fish": "packaging/completions/appimage.fish",
    }

    path = Path(completion_files[args.shell])
    if path.exists():
        sys.stdout.write(path.read_text())
    else:
        raise CLIError(f"Completion file not found: {path}", EXIT_ERROR)

    return EXIT_OK


def cmd_status(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle status command."""
    print_info("Daemon status: not implemented")
    return EXIT_OK


def cmd_export(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle export command."""
    if isinstance(manager, DBusClientManager):
        raise CLIError(
            "export is not available via D-Bus daemon; use --no-daemon", EXIT_DAEMON_REQUIRED
        )
    data = manager.export_data()

    if args.file:
        args.file.write_text(json.dumps(data, indent=2))
    else:
        print_json(data)

    return EXIT_OK


def cmd_import(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle import command."""
    if isinstance(manager, DBusClientManager):
        raise CLIError(
            "import is not available via D-Bus daemon; use --no-daemon", EXIT_DAEMON_REQUIRED
        )
    import json

    data = json.loads(args.file.read_text())
    imported = manager.import_data(data)
    if not args.quiet:
        print_info(f"Imported {imported} apps")
    return EXIT_OK


def cmd_dedupe(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle dedupe command."""
    dirs = args.dirs if args.dirs else None
    result = manager.dedupe(dirs)

    if args.json:
        print_json(result)
    else:
        duplicates = result.get("duplicates", [])
        if not duplicates:
            print_info("No duplicates found")
        else:
            print_info(f"Found {len(duplicates)} duplicate(s):")
            for dup in duplicates:
                print_info(f"  {dup['installed_app']} ({dup['installed_path']})")
                print_info(f"    Duplicate: {dup['duplicate_path']} ({format_size(dup['size'])})")

    return EXIT_OK


def cmd_cleanup(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle cleanup command."""
    result = manager.cleanup(args.days)

    if args.json:
        print_json(result)
    else:
        print_info(
            f"Removed {result['removed_previous']} previous version(s) ({format_size(result['removed_previous_size'])})"
        )
        print_info(f"Removed {result['removed_entries']} orphaned desktop entry(ies)")
        print_info(f"Removed {result['removed_icons']} orphaned icon(s)")

    return EXIT_OK


def cmd_daemon(args: argparse.Namespace, manager: Manager | DBusClientManager) -> int:
    """Handle daemon command."""
    import asyncio
    from appimage_launcher.daemon.app import run_daemon

    asyncio.run(
        run_daemon(
            applications_dir=args.dir,
            no_notify=args.no_notify,
            no_dialogs=args.no_dialogs,
            dev_mode=args.dev,
        )
    )
    return EXIT_OK


COMMAND_HANDLERS = {
    "install": cmd_install,
    "remove": cmd_remove,
    "rename": cmd_rename,
    "list": cmd_list,
    "info": cmd_info,
    "update": cmd_update,
    "policy": cmd_policy,
    "origin": cmd_origin,
    "revert": cmd_revert,
    "versions": cmd_versions,
    "disable": cmd_disable,
    "enable": cmd_enable,
    "run": cmd_run,
    "browse": cmd_browse,
    "extract": cmd_extract,
    "verify": cmd_verify,
    "reindex": cmd_reindex,
    "doctor": cmd_doctor,
    "config": cmd_config,
    "completions": cmd_completions,
    "status": cmd_status,
    "export": cmd_export,
    "import": cmd_import,
    "dedupe": cmd_dedupe,
    "cleanup": cmd_cleanup,
    "daemon": cmd_daemon,
}


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    # Setup logging
    verbose = args.verbose
    if args.quiet:
        verbose = -1  # Will be handled as ERROR in setup_logging
    setup_logging(verbose)

    # Disable colors if requested
    if args.no_color:
        os.environ["TERM"] = "dumb"

    # Override paths if --dir provided
    if args.dir:
        os.environ["APPIMAGE_LAUNCHER_HOME"] = str(args.dir.parent)
        reset_paths()
        reset_config()

    # Get manager
    manager = get_manager(args)

    # Dispatch command
    handler = COMMAND_HANDLERS.get(args.command)
    if not handler:
        print_error(f"Unknown command: {args.command}")
        return EXIT_USAGE

    try:
        return handler(args, manager)
    except CLIError as e:
        if not args.json:
            print_error(e.message)
        else:
            print_json({"error": e.message})
        return e.exit_code
    except KeyboardInterrupt:
        print_error("Interrupted")
        return EXIT_ERROR
    except Exception as e:
        logger.exception("Unhandled error")
        if not args.json:
            print_error(f"Internal error: {e}")
        else:
            print_json({"error": f"Internal error: {e}"})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
