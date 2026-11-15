"""Main daemon application for AppImage Launcher.

Coordinates the observer, D-Bus service, reconciliation, and notifications.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path

from appimage_launcher.config import get_config
from appimage_launcher.constants import (
    AFTER_QUIT_CAP_MINUTES,
    STABILITY_WINDOW_SECONDS,
)
from appimage_launcher.core.manager import Manager
from appimage_launcher.core.running import RunningTracker
from appimage_launcher.core.state import get_state_db, reset_state_db
from appimage_launcher.core.updates import UpdateCache
from appimage_launcher.daemon.dbus_service import DBusService
from appimage_launcher.daemon.observer import InotifyObserver
from appimage_launcher.daemon.reconcile import reconcile
from appimage_launcher.logging import get_logger, setup_logging
from appimage_launcher.paths import get_paths, set_dev_mode

logger = get_logger(__name__)


class DaemonApp:
    """Main daemon application."""

    def __init__(
        self,
        applications_dir: Path | None = None,
        no_notify: bool = False,
        no_dialogs: bool = False,
        dev_mode: bool = False,
    ):
        self.applications_dir = applications_dir
        self.no_notify = no_notify
        self.no_dialogs = no_dialogs
        self.dev_mode = dev_mode

        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Components
        self.manager: Manager | None = None
        self.running_tracker: RunningTracker | None = None
        self.observer: InotifyObserver | None = None
        self.dbus_service: DBusService | None = None
        self.notifier = None  # Will be set up later

        # Self-update tracking
        self._self_update_candidates: dict[str, dict] = {}

        # Background update checker
        self._update_check_task: asyncio.Task | None = None
        self._update_cache: UpdateCache | None = None

    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the daemon."""
        if self._running:
            return

        self._loop = loop or asyncio.get_event_loop()
        self._running = True

        # Set up paths and config
        if self.dev_mode:
            set_dev_mode(True)

        self.config = get_config()
        if self.applications_dir:
            self.config.applications_dir = str(self.applications_dir.resolve())

        paths = get_paths(self.config)

        # Set up logging
        setup_logging(1)  # INFO level

        logger.info(
            "Starting AppImage Launcher daemon: applications_dir=%s dev_mode=%s",
            str(paths.applications_dir),
            self.dev_mode,
        )

        # Initialize database
        db = get_state_db()

        # Initialize manager
        self.manager = Manager(db)

        # Initialize running tracker
        self.running_tracker = RunningTracker(
            self.manager,
            self._on_self_update_detected,
        )

        # Set running tracker and callback on manager
        self.manager.running_tracker = self.running_tracker
        self.manager.on_self_update = self._on_self_update_detected

        # Initialize notifier (placeholder for now)
        self.notifier = Notifier(
            self.manager, paths, self.config, self._loop, no_dialogs=self.no_dialogs
        )

        # Run initial reconciliation
        logger.info("Running initial reconciliation")
        reconcile(self.manager, self.notifier, paths, self.config)

        # Start running tracker
        self.running_tracker.start()

        # Start D-Bus service
        self.dbus_service = DBusService(self.manager, self.notifier, paths, self.config, self._loop)
        await self.dbus_service.start()

        # Start observer
        self.observer = InotifyObserver(
            paths.applications_dir,
            self._on_fs_events,
            self._loop,
        )
        self.observer.start()

        # Initialize update cache
        self._update_cache = UpdateCache(paths.state_dir / "update_cache")

        # Start background update checker
        self._start_background_update_checker()

        # Set up signal handlers
        self._setup_signals()

        logger.info("Daemon started successfully")

    async def stop(self) -> None:
        """Stop the daemon."""
        if not self._running:
            return

        logger.info("Stopping daemon")
        self._running = False

        # Stop background update checker
        if self._update_check_task:
            self._update_check_task.cancel()
            try:
                await self._update_check_task
            except asyncio.CancelledError:
                pass
            self._update_check_task = None

        # Stop observer
        if self.observer:
            self.observer.stop()
            self.observer = None

        # Stop running tracker
        if self.running_tracker:
            self.running_tracker.stop()
            self.running_tracker = None

        # Stop D-Bus service
        if self.dbus_service:
            await self.dbus_service.stop()
            self.dbus_service = None

        # Close database
        reset_state_db()

        self._shutdown_event.set()
        logger.info("Daemon stopped")

    def _setup_signals(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

    def _signal_handler(self) -> None:
        """Handle shutdown signals."""
        logger.info("Received shutdown signal")
        asyncio.create_task(self.stop())

    def _on_fs_events(self, entries: list) -> None:
        """Handle file system events from the observer."""
        # The observer's classifier handles the events
        # This is called from the observer's callback
        pass  # Classifier is inside observer

    def _on_self_update_detected(self, app_id: str, new_path: Path) -> None:
        """Handle self-update detection from running tracker."""
        logger.info("Self-update detected", app_id=app_id, new_path=str(new_path))

        # Track the candidate for after-quit processing
        self._self_update_candidates[app_id] = {
            "new_path": new_path,
            "detected_at": asyncio.get_event_loop().time(),
        }

        # Schedule after-quit check
        asyncio.create_task(self._process_self_update(app_id))

    async def _process_self_update(self, app_id: str) -> None:
        """Process a self-update after the app quits."""
        candidate = self._self_update_candidates.get(app_id)
        if not candidate:
            return

        new_path = candidate["new_path"]

        # Wait for stability (no writes for STABILITY_WINDOW_SECONDS)
        await self._wait_for_stability(new_path)

        # Wait for app to quit (with cap)
        await self._wait_for_quit(app_id, new_path)

        # Perform the replacement
        await self._replace_self_update(app_id, new_path)

        # Clean up
        self._self_update_candidates.pop(app_id, None)

    async def _wait_for_stability(self, path: Path) -> None:
        """Wait for file to be stable (no writes)."""
        last_mtime = 0
        stable_count = 0

        while stable_count < STABILITY_WINDOW_SECONDS:
            try:
                stat = path.stat()
                if stat.st_mtime_ns == last_mtime:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_mtime = stat.st_mtime_ns
            except FileNotFoundError:
                # File disappeared
                return

            await asyncio.sleep(1)

    async def _wait_for_quit(self, app_id: str, new_path: Path) -> None:
        """Wait for the old app to quit."""
        # Check rename_policy - if "never", don't wait, just update integration
        old_app = self.manager.db.get_primary_by_app_id(app_id)
        if not old_app:
            return

        if self.config.update.rename_policy == "never":
            logger.info(
                "rename_policy=never: skipping wait for quit, updating integration directly",
                app_id=app_id,
            )
            # Update the integration to point to the new file
            await self._update_integration_for_rename_policy_never(old_app, new_path)
            return

        cap_seconds = AFTER_QUIT_CAP_MINUTES * 60
        waited = 0

        while waited < cap_seconds:
            # Check if any process is running from the old path
            old_app = self.manager.db.get_primary_by_app_id(app_id)
            if not old_app:
                break

            old_path = Path(old_app.path)
            running = self.running_tracker.is_running(old_path)

            # Also check if new path is running (app relaunched)
            new_running = self.running_tracker.is_running(new_path)

            if not running or new_running:
                break

            await asyncio.sleep(1)
            waited += 1

        if waited >= cap_seconds:
            logger.warning("Self-update wait capped", app_id=app_id)
            self.notifier.notify_update_waiting(app_id)

    async def _update_integration_for_rename_policy_never(self, old_app, new_path: Path) -> None:
        """Update integration to point to new file when rename_policy=never."""
        try:
            from appimage_launcher.core.integration import (
                install_icons,
                refresh_caches,
                write_desktop_entry,
            )
            from appimage_launcher.core.metadata import read_appimage_meta

            meta = read_appimage_meta(new_path)

            # Update the app record to point to the new file
            stat = new_path.stat()
            old_app.path = str(new_path)
            old_app.device = stat.st_dev
            old_app.inode = stat.st_ino
            old_app.size = stat.st_size
            old_app.mtime_ns = stat.st_mtime_ns
            old_app.fingerprint = meta.fingerprint
            old_app.version = meta.version
            old_app.update_info = meta.update_info
            old_app.updated_at = int(time.time())
            self.manager.db.update_app(old_app)

            # Update desktop entry to point to new file
            write_desktop_entry(
                app_id=old_app.app_id,
                display_name=meta.name,  # Use embedded Name=
                exec_path=new_path,
                icon_name=f"appimage-{old_app.app_id}",
                categories=meta.categories,
                comment=meta.comment,
                startup_wm_class=None,
                version=meta.version,
                name_origin=old_app.name_origin,
                update_policy=old_app.update_policy,
            )

            # Install new icon
            if meta.icon_bytes and meta.icon_ext:
                install_icons(old_app.app_id, meta.icon_bytes, meta.icon_ext)

            refresh_caches()

            logger.info("Updated integration for rename_policy=never", app_id=old_app.app_id)

        except Exception as e:
            logger.error("Failed to update integration for rename_policy=never", error=str(e))

    async def _replace_self_update(self, app_id: str, new_path: Path) -> None:
        """Replace the old app with the self-updated version."""
        old_app = self.manager.db.get_primary_by_app_id(app_id)
        if not old_app:
            return

        logger.info("Replacing app with self-update", app_id=app_id)

        try:
            # Read metadata from new file
            from appimage_launcher.core.metadata import read_appimage_meta

            meta = read_appimage_meta(new_path)

            # Replace using manager's internal method with is_self_update=True
            # This keeps user-chosen name and marks as self_managed
            result = self.manager._replace_app(
                old_app,
                meta,
                new_path,
                "copy",
                old_app.origin,
                False,  # silent
                old_app.update_policy,
                is_self_update=True,
            )

            # Notify with undo option
            self.notifier.notify_self_updated(result.app, old_app.version, result.app.version)

        except Exception as e:
            logger.error("Failed to process self-update", app_id=app_id, error=str(e))

    def _start_background_update_checker(self) -> None:
        """Start the background update checker task."""
        if self.config.update.check_interval == "0":
            logger.info("Background update checks disabled (check_interval=0)")
            return

        # Parse check interval
        interval_str = self.config.update.check_interval
        interval_seconds = self._parse_interval(interval_str)
        if interval_seconds <= 0:
            logger.info("Background update checks disabled")
            return

        logger.info("Starting background update checker", interval_seconds=interval_seconds)
        self._update_check_task = asyncio.create_task(
            self._background_update_checker(interval_seconds)
        )

    def _parse_interval(self, interval_str: str) -> int:
        """Parse interval string like '1d', '6h', '30m' to seconds."""
        interval_str = interval_str.strip().lower()
        if interval_str.endswith("d"):
            return int(interval_str[:-1]) * 86400
        elif interval_str.endswith("h"):
            return int(interval_str[:-1]) * 3600
        elif interval_str.endswith("m"):
            return int(interval_str[:-1]) * 60
        elif interval_str.endswith("s"):
            return int(interval_str[:-1])
        else:
            # Assume seconds
            try:
                return int(interval_str)
            except ValueError:
                return 86400  # Default 1 day

    async def _background_update_checker(self, interval_seconds: int) -> None:
        """Background task that periodically checks for updates."""
        # Initial jitter: wait a random time up to 10% of interval
        initial_jitter = random.uniform(0, interval_seconds * 0.1)
        logger.debug("Background update checker: initial jitter", seconds=initial_jitter)
        await asyncio.sleep(initial_jitter)

        while self._running:
            try:
                await self._check_all_updates()
            except Exception as e:
                logger.error("Background update check failed", error=str(e))

            # Wait for next interval with jitter (±10%)
            jitter = random.uniform(-interval_seconds * 0.1, interval_seconds * 0.1)
            next_interval = max(60, interval_seconds + jitter)  # At least 60 seconds
            logger.debug("Background update checker: next check in", seconds=next_interval)
            await asyncio.sleep(next_interval)

    async def _check_all_updates(self) -> None:
        """Check for updates for all primary apps."""
        if not self.manager:
            return

        apps = self.manager.db.list_apps(include_kept=False)
        if not apps:
            return

        logger.debug("Checking updates for apps", count=len(apps))

        for app in apps:
            if not self._running:
                break

            # Check update-authority rule (6.9): skip catalog-linked apps
            if app.origin and self.config.update.skip_catalog_linked:
                logger.debug("Skipping catalog-linked app", app_id=app.app_id)
                continue

            # Check if app has update info
            if not app.update_info:
                continue

            # Check for updates
            result = self.manager.check_updates(app.managed_name)

            if result.has_update:
                logger.info(
                    "Update available",
                    app_id=app.app_id,
                    current=result.current_version,
                    latest=result.latest_version,
                )
                # Send notification
                self.notifier.notify_update_available(app, result.latest_version or "unknown")

            # Small delay between checks to avoid hammering servers
            await asyncio.sleep(2)

    async def run_forever(self) -> None:
        """Run the daemon until stopped."""
        await self._shutdown_event.wait()


class Notifier:
    """Handles desktop notifications and dialog launching."""

    def __init__(self, manager, paths, config, loop, no_dialogs: bool = False):
        self.manager = manager
        self.paths = paths
        self.config = config
        self.loop = loop
        self.no_dialogs = no_dialogs
        self._notification_id = 0
        self._pending_notifications: dict[int, dict] = {}

    def _next_id(self) -> int:
        self._notification_id += 1
        return self._notification_id

    def _get_gui_env(self) -> dict:
        """Get environment variables for GUI dialogs."""
        env = os.environ.copy()

        # Ensure WAYLAND_DISPLAY, DISPLAY, XDG_RUNTIME_DIR are set
        # If missing, try to get from systemd user environment
        for var in ["WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR"]:
            if var not in env:
                try:
                    result = subprocess.run(
                        ["systemctl", "--user", "show-environment"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    for line in result.stdout.strip().split("\n"):
                        if line.startswith(f"{var}="):
                            env[var] = line.split("=", 1)[1]
                            break
                except Exception:
                    pass

        return env

    async def _launch_dialog(self, kind: str, request: dict, timeout: int = 30) -> dict:
        """Launch a GUI dialog and return the response."""
        if self.no_dialogs:
            logger.info("Dialogs disabled, skipping %s dialog", kind)
            return {"cancelled": True}

        try:
            # Find appimage-gui binary
            gui_path = self._find_gui_binary()
            if not gui_path:
                logger.warning("appimage-gui not found, falling back to notifications")
                return {"cancelled": True}

            # Prepare request JSON
            request_json = json.dumps(request)

            # Launch dialog process
            proc = await asyncio.create_subprocess_exec(
                str(gui_path),
                "dialog",
                kind,
                f"--timeout={timeout}",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._get_gui_env(),
            )

            # Send request and get response
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=request_json.encode()),
                    timeout=timeout + 5,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("Dialog %s timed out", kind)
                return {"cancelled": True, "timeout": True}

            if proc.returncode == 124:
                return {"cancelled": True, "timeout": True}
            elif proc.returncode != 0:
                logger.warning(
                    "Dialog %s exited with code %d: %s", kind, proc.returncode, stderr.decode()
                )
                return {"cancelled": True}

            # Parse response
            try:
                response = json.loads(stdout.decode())
                return response
            except json.JSONDecodeError:
                logger.error("Invalid JSON from dialog: %s", stdout.decode())
                return {"cancelled": True}

        except Exception as e:
            logger.error("Failed to launch dialog %s: %s", kind, e)
            return {"cancelled": True}

    def _find_gui_binary(self) -> Path | None:
        """Find the appimage-gui binary."""
        # Check common locations
        candidates = [
            Path("/usr/bin/appimage-gui"),
            Path("/usr/local/bin/appimage-gui"),
            Path(sys.prefix) / "bin" / "appimage-gui",
        ]

        # Also check if we're running from source
        import appimage_launcher

        src_gui = Path(appimage_launcher.__file__).parent.parent / "gui" / "main.py"
        if src_gui.exists():
            candidates.insert(0, src_gui)

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return None

    def notify_installed(self, app, version: str) -> None:
        """Notify that an app was installed."""
        self._send_notification(
            title=f"Installed {app.display_name} {version}",
            body=f"{app.display_name} {version} has been installed.",
            actions=[("open", "Open"), ("show", "Show in folder")],
            app_id=app.app_id,
            kind="installed",
        )

    def notify_updated(self, app, old_version: str, new_version: str) -> None:
        """Notify that an app was updated."""
        self._send_notification(
            title=f"{app.display_name} updated",
            body=f"{app.display_name} updated from {old_version} to {new_version}.",
            actions=[("undo", "Undo")],
            app_id=app.app_id,
            kind="updated",
        )

    def notify_self_updated(self, app, old_version: str, new_version: str) -> None:
        """Notify that an app self-updated."""
        self._send_notification(
            title=f"{app.display_name} updated",
            body=f"{app.display_name} updated from {old_version} to {new_version}.",
            actions=[("undo", "Undo")],
            app_id=app.app_id,
            kind="self_updated",
        )

    def notify_removed(self, app) -> None:
        """Notify that an app was removed."""
        self._send_notification(
            title=f"{app.display_name} removed",
            body=f"{app.display_name} has been removed from the app menu.",
            actions=[("undo", "Undo")],
            app_id=app.app_id,
            kind="removed",
        )

    def notify_error(self, message: str) -> None:
        """Notify of an error."""
        self._send_notification(
            title="AppImage Launcher Error",
            body=message,
            actions=[("details", "Details")],
            app_id="",
            kind="error",
        )

    def notify_update_waiting(self, app_id: str) -> None:
        """Notify that an update is waiting for app to close."""
        app = self.manager.db.get_primary_by_app_id(app_id)
        if not app:
            return

        self._send_notification(
            title=f"Update waiting for {app.display_name} to close",
            body=f"An update for {app.display_name} is ready but the app is still running.",
            actions=[("apply", "Apply now")],
            app_id=app_id,
            kind="update_waiting",
        )

    def notify_update_available(self, app, latest_version: str) -> None:
        """Notify that an update is available."""
        self._send_notification(
            title=f"Update available for {app.display_name}",
            body=f"{app.display_name} {latest_version} is available.",
            actions=[("update", "Update"), ("skip", "Skip"), ("pin", "Pin")],
            app_id=app.app_id,
            kind="update_available",
        )

    def _send_notification(
        self,
        title: str,
        body: str,
        actions: list[tuple[str, str]],
        app_id: str,
        kind: str,
    ) -> None:
        """Send a desktop notification via D-Bus."""
        # This would use the org.freedesktop.Notifications D-Bus interface
        # For now, just log
        logger.info("Notification", title=title, body=body, actions=actions, kind=kind)

        # TODO: Implement actual D-Bus notification sending
        # This requires connecting to the session bus and calling
        # org.freedesktop.Notifications.Notify

    async def show_conflict_dialog(
        self,
        old_app,
        new_meta: dict,
        relation: str,
        source_path: Path,
        is_self_update: bool = False,
    ) -> dict:
        """Show conflict dialog and return user decision."""
        request = {
            "old_app": {
                "name": old_app.display_name,
                "managed_name": old_app.managed_name,
                "version": old_app.version,
                "size": old_app.size,
                "path": old_app.path,
                "icon_path": getattr(old_app, "icon_path", None),
            },
            "new_meta": new_meta,
            "relation": relation,
            "source_path": str(source_path),
            "is_self_update": is_self_update,
        }
        return await self._launch_dialog("conflict", request, timeout=60)

    async def show_rename_dialog(
        self,
        current_name: str,
        default_name: str,
        existing_names: list[str],
    ) -> dict:
        """Show rename dialog and return new name."""
        request = {
            "current_name": current_name,
            "default_name": default_name,
            "existing_names": existing_names,
        }
        return await self._launch_dialog("rename", request, timeout=60)

    async def show_update_dialog(
        self,
        app_name: str,
        current_version: str,
        latest_version: str,
        release_url: str = "",
        has_signature: bool = False,
    ) -> dict:
        """Show update dialog and return result."""
        request = {
            "app_name": app_name,
            "current_version": current_version,
            "latest_version": latest_version,
            "release_url": release_url,
            "has_signature": has_signature,
        }
        return await self._launch_dialog("update", request, timeout=300)

    async def show_error_dialog(
        self,
        title: str,
        message: str,
        details: str = "",
    ) -> dict:
        """Show error dialog and return result."""
        request = {
            "title": title,
            "message": message,
            "details": details,
        }
        return await self._launch_dialog("error", request, timeout=60)


async def run_daemon(
    applications_dir: Path | None = None,
    no_notify: bool = False,
    no_dialogs: bool = False,
    dev_mode: bool = False,
) -> None:
    """Run the daemon."""
    app = DaemonApp(
        applications_dir=applications_dir,
        no_notify=no_notify,
        no_dialogs=no_dialogs,
        dev_mode=dev_mode,
    )

    try:
        await app.start()
        await app.run_forever()
    finally:
        await app.stop()


def main() -> None:
    """Main entry point for the daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="AppImage Launcher daemon")
    parser.add_argument("--dir", type=Path, help="Applications directory to watch")
    parser.add_argument("--no-notify", action="store_true", help="Disable notifications")
    parser.add_argument("--no-dialogs", action="store_true", help="Disable dialogs")
    parser.add_argument("--dev", action="store_true", help="Development mode")

    args = parser.parse_args()

    asyncio.run(
        run_daemon(
            applications_dir=args.dir,
            no_notify=args.no_notify,
            no_dialogs=args.no_dialogs,
            dev_mode=args.dev,
        )
    )


if __name__ == "__main__":
    main()
