"""Running AppImage tracker for AppImage Launcher.

Scans /proc/*/environ for APPIMAGE= to detect running AppImages.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RunningApp:
    """Information about a running AppImage."""

    pid: int
    appimage_path: str
    start_time: float
    last_seen: float


class RunningTracker:
    """Tracks running AppImages by scanning /proc."""

    def __init__(
        self,
        manager=None,
        on_self_update: Callable[[str, Path], None] | None = None,
    ):
        self.manager = manager
        self.on_self_update = on_self_update
        self._running: dict[str, RunningApp] = {}  # path -> RunningApp
        self._recently_seen: dict[str, float] = {}  # path -> timestamp
        self._scan_interval = 2.0  # seconds
        self._retention_seconds = 30.0
        self._task: asyncio.Task | None = None
        self._running_flag = False
        self._known_appimages: set[str] = set()  # Track known AppImage paths

    def start(self) -> None:
        """Start the periodic scanner."""
        if self._running_flag:
            return
        self._running_flag = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Running tracker started")

    def stop(self) -> None:
        """Stop the periodic scanner."""
        self._running_flag = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Running tracker stopped")

    async def _run_loop(self) -> None:
        """Main scanning loop."""
        while self._running_flag:
            try:
                self.scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Running tracker scan error", error=str(e))

            await asyncio.sleep(self._scan_interval)

    def scan(self) -> dict[str, RunningApp]:
        """Scan /proc for running AppImages."""
        found: dict[str, RunningApp] = {}
        now = time.time()

        try:
            for pid_dir in Path("/proc").iterdir():
                if not pid_dir.is_dir() or not pid_dir.name.isdigit():
                    continue

                pid = int(pid_dir.name)
                environ_path = pid_dir / "environ"
                if not environ_path.exists():
                    continue

                try:
                    with open(environ_path, "rb") as f:
                        environ_data = f.read()
                except Exception:
                    continue

                # Parse null-separated environment variables
                for entry in environ_data.split(b"\x00"):
                    if entry.startswith(b"APPIMAGE="):
                        appimage_path = entry[9:].decode("utf-8", errors="replace")
                        if appimage_path:
                            found[appimage_path] = RunningApp(
                                pid=pid,
                                appimage_path=appimage_path,
                                start_time=now,  # Approximate
                                last_seen=now,
                            )
        except Exception as e:
            logger.debug("Failed to scan /proc", error=str(e))

        # Detect new AppImages (potential self-updates)
        self._detect_self_updates(found, now)

        # Update internal state
        self._running = found

        # Update recently seen
        for path in found:
            self._recently_seen[path] = now

        # Clean old entries
        self._cleanup(now)

        return self._running.copy()

    def _detect_self_updates(self, found: dict[str, RunningApp], now: float) -> None:
        """Detect potential self-updates by comparing with known AppImages."""
        if not self.manager or not self.on_self_update:
            # Just update known set
            self._known_appimages = set(found.keys())
            return

        # Check for new AppImage paths that weren't known before
        for path in found:
            if path not in self._known_appimages:
                # This is a new AppImage path - check if it's a self-update
                # by looking for a tracked app with similar app_id
                self._check_self_update(path)

        # Update known set
        self._known_appimages = set(found.keys())

    def _check_self_update(self, new_path: str) -> None:
        """Check if a new AppImage path is a self-update of a tracked app."""
        if not self.manager:
            return

        try:
            from appimage_launcher.core.metadata import read_appimage_meta

            meta = read_appimage_meta(Path(new_path))

            # Look for a primary app with the same app_id
            old_app = self.manager.db.get_primary_by_app_id(meta.app_id)
            if old_app:
                # Check if this is a different file (different inode)
                new_stat = Path(new_path).stat()
                if old_app.inode != new_stat.st_ino or old_app.device != new_stat.st_dev:
                    # This looks like a self-update!
                    logger.info(
                        "Potential self-update detected",
                        app_id=meta.app_id,
                        old_path=old_app.path,
                        new_path=new_path,
                    )
                    if self.on_self_update:
                        self.on_self_update(meta.app_id, Path(new_path))
                    return

            # Also check tombstones for recently removed apps with same app_id
            # (spec 6.7: tracked file was removed/replaced within last 30s)
            tombstone = self.manager.db.find_tombstone_by_fingerprint(meta.fingerprint)
            if not tombstone:
                # Try finding by app_id in recent tombstones
                import time

                _ = time.time() - 30  # 30 seconds
                # We need to check recent tombstones for this app_id
                # For now, check if there's a tombstone with matching app_id
                # This would require a new DB method, but we can check via fingerprint
                pass

        except Exception as e:
            logger.debug("Failed to check self-update", path=new_path, error=str(e))

    def check_self_update_via_tombstone(self, app_id: str, new_path: Path) -> bool:
        """Check if a new AppImage at new_path is a self-update based on tombstone.

        Returns True if a matching tombstone was found (file removed/replaced within 30s).
        """
        if not self.manager:
            return False

        try:
            from appimage_launcher.core.metadata import read_appimage_meta

            meta = read_appimage_meta(new_path)

            # Check if there's a recent tombstone for this app_id
            # We need to find tombstones by app_id and check if they're recent
            import time

            cutoff = time.time() - 30  # 30 seconds

            # Get all tombstones and check for matching app_id and recent removal
            # This is a simplified check - in practice we'd want a DB query
            # For now, check fingerprint match
            tombstone = self.manager.db.find_tombstone_by_fingerprint(meta.fingerprint)
            if tombstone and tombstone.removed_at >= cutoff:
                logger.info(
                    "Self-update detected via tombstone",
                    app_id=app_id,
                    new_path=str(new_path),
                    tombstone_removed_at=tombstone.removed_at,
                )
                if self.on_self_update:
                    self.on_self_update(app_id, new_path)
                return True

        except Exception as e:
            logger.debug(
                "Failed to check self-update via tombstone", path=str(new_path), error=str(e)
            )

        return False

    def _cleanup(self, now: float) -> None:
        """Remove old entries from recently_seen."""
        cutoff = now - self._retention_seconds
        to_remove = [path for path, ts in self._recently_seen.items() if ts < cutoff]
        for path in to_remove:
            del self._recently_seen[path]

    def is_running(self, path: str) -> bool:
        """Check if an AppImage is currently running."""
        return path in self._running

    def was_recently_running(self, path: str) -> bool:
        """Check if an AppImage was running in the last 30 seconds."""
        return path in self._recently_seen

    def get_pids(self, path: str) -> list[int]:
        """Get PIDs running a specific AppImage."""
        if path in self._running:
            return [self._running[path].pid]
        return []

    def get_all_running(self) -> dict[str, RunningApp]:
        """Get all currently running AppImages."""
        return self._running.copy()

    def get_recently_seen(self) -> dict[str, float]:
        """Get recently seen AppImages with timestamps."""
        return self._recently_seen.copy()

    def scan_now(self) -> dict[str, RunningApp]:
        """Trigger an immediate scan."""
        return self.scan()


def scan_running_appimages() -> dict[str, RunningApp]:
    """Convenience function to scan for running AppImages."""
    tracker = RunningTracker()
    return tracker.scan()


# Global tracker instance
_tracker: RunningTracker | None = None


def get_running_tracker() -> RunningTracker:
    """Get global RunningTracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = RunningTracker()
    return _tracker
