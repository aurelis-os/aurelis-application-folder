"""Inotify observer for the Applications directory.

Watches for file system events and classifies them for processing.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from inotify_simple import INotify, flags

from appimage_launcher.constants import (
    DEBOUNCE_MS,
    EXPECTED_EVENT_TTL_SECONDS,
    IGNORED_DIRS,
    IGNORED_PATTERNS,
    INOTIFY_EVENTS,
    RENAME_GRACE_SECONDS,
)
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class FSEntry:
    """File system entry for tracking."""

    path: Path
    inode: int
    device: int
    event_type: str
    timestamp: float
    cookie: int = 0


@dataclass
class ExpectedEvent:
    """Expected event from our own operations."""

    path: Path
    inode: int
    device: int
    operation: str  # "rename", "remove", "create"
    expires_at: float


@dataclass
class StabilityTracker:
    """Tracks stability state for a file."""

    size: int
    mtime_ns: int
    first_seen: float
    inode: int = 0
    device: int = 0
    recheck_task: asyncio.TimerHandle | None = None


class InotifyObserver:
    """Watches the Applications directory for file system events."""

    def __init__(
        self,
        watch_path: Path,
        callback: Callable[[list[FSEntry]], None],
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.watch_path = watch_path.resolve()
        self.callback = callback
        self.loop = loop or asyncio.get_event_loop()

        self._inotify = INotify()
        self._watch_descriptor = None
        self._running = False
        self._task: asyncio.Task | None = None

        # Debounce/coalesce state
        self._pending_events: dict[str, list[FSEntry]] = defaultdict(list)
        self._debounce_tasks: dict[str, asyncio.Task] = {}

        # Expected events suppression
        self._expected_events: list[ExpectedEvent] = []

        # Rename tracking (MOVED_FROM + MOVED_TO with same cookie)
        self._rename_from: dict[int, FSEntry] = {}

        # Stability tracking: track (size, mtime_ns) and schedule re-check
        self._file_stats: dict[str, StabilityTracker] = {}
        self._stability_window = 2.0  # seconds

    def start(self) -> None:
        """Start watching the directory."""
        if self._running:
            return

        self.watch_path.mkdir(parents=True, exist_ok=True)

        # Parse event flags
        event_flags = 0
        for flag_name in INOTIFY_EVENTS.split("|"):
            event_flags |= getattr(flags, flag_name)

        self._watch_descriptor = self._inotify.add_watch(str(self.watch_path), event_flags)
        self._running = True

        # Start the event loop
        self._task = self.loop.create_task(self._run())
        logger.info("Inotify observer started", path=str(self.watch_path))

    def stop(self) -> None:
        """Stop watching the directory."""
        if not self._running:
            return

        self._running = False

        # Cancel pending debounce tasks
        for task in self._debounce_tasks.values():
            task.cancel()
        self._debounce_tasks.clear()

        # Cancel stability recheck tasks
        for tracker in self._file_stats.values():
            if tracker.recheck_task:
                tracker.recheck_task.cancel()

        if self._task:
            self._task.cancel()
            self._task = None

        if self._watch_descriptor is not None:
            try:
                self._inotify.rm_watch(self._watch_descriptor)
            except Exception:
                pass
            self._watch_descriptor = None

        logger.info("Inotify observer stopped")

    def add_expected_event(self, path: Path, inode: int, device: int, operation: str) -> None:
        """Add an expected event to suppress."""
        expires_at = time.time() + EXPECTED_EVENT_TTL_SECONDS
        self._expected_events.append(
            ExpectedEvent(
                path=path.resolve(),
                inode=inode,
                device=device,
                operation=operation,
                expires_at=expires_at,
            )
        )
        # Clean expired
        self._cleanup_expected_events()

    def _cleanup_expected_events(self) -> None:
        """Remove expired expected events."""
        now = time.time()
        self._expected_events = [e for e in self._expected_events if e.expires_at > now]

    def _is_expected(self, entry: FSEntry) -> bool:
        """Check if an event matches an expected event."""
        self._cleanup_expected_events()

        for expected in self._expected_events:
            if (
                expected.path == entry.path
                and expected.inode == entry.inode
                and expected.device == entry.device
            ):
                # Remove the matched expected event
                self._expected_events.remove(expected)
                return True
        return False

    def _should_ignore(self, name: str) -> bool:
        """Check if a file name should be ignored."""
        # Check ignored patterns
        import fnmatch

        for pattern in IGNORED_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                return True

        # Check ignored directories
        if name in IGNORED_DIRS:
            return True

        return False

    async def _run(self) -> None:
        """Main event loop."""
        while self._running:
            try:
                # Read events with a timeout to allow checking _running
                events = await self.loop.run_in_executor(
                    None, lambda: self._inotify.read(timeout=1000)
                )

                for event in events:
                    self._process_event(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Inotify read error", error=str(e))
                await asyncio.sleep(1)

    def _process_event(self, event) -> None:
        """Process a single inotify event."""
        name = event.name
        if not name:
            return

        # Check if we should ignore this file
        if self._should_ignore(name):
            return

        path = self.watch_path / name

        # Determine event type from mask first (before stat)
        event_type = self._event_type_from_flags(event.mask)

        # Handle MOVED_FROM early - file no longer exists at old path
        if event_type == "MOVED_FROM":
            self._handle_moved_from(event, path, event.cookie)
            return

        # Handle rename pairing: MOVED_TO with matching MOVED_FROM cookie
        # Check the raw mask for MOVED_TO flag since event_type might be CLOSE_WRITE if both flags are set
        if (event.mask & flags.MOVED_TO) and event.cookie in self._rename_from:
            from_entry = self._rename_from.pop(event.cookie)
            # This is a rename within the directory - queue both immediately (no stability needed)
            self._queue_event(from_entry)
            # Get inode/device for new path
            try:
                stat = path.stat()
                inode = stat.st_ino
                device = stat.st_dev
            except Exception:
                inode = 0
                device = 0
            # Create entry for MOVED_TO
            to_entry = FSEntry(
                path=path,
                inode=inode,
                device=device,
                event_type="MOVED_TO",
                timestamp=time.time(),
                cookie=event.cookie,
            )
            self._queue_event(to_entry)
            return

        # Get file stats for inode/device (for other event types)
        try:
            stat = path.stat()
            inode = stat.st_ino
            device = stat.st_dev
            size = stat.st_size
            mtime_ns = stat.st_mtime_ns
        except FileNotFoundError:
            # File was deleted, handle delete event
            self._handle_delete_event(event, path, event.cookie)
            return
        except Exception as e:
            logger.warning("Failed to stat file", path=str(path), error=str(e))
            return

        # Handle events that should trigger stability check
        if event_type in ("CLOSE_WRITE", "MOVED_TO"):
            self._schedule_stability_check(event, path, inode, device, size, mtime_ns, event.cookie)
            return

        # For DELETE, process immediately (no stability needed)
        if event_type == "DELETE":
            self._handle_delete_event(event, path, event.cookie)
            return

        # For ATTRIB and other events, queue for debounce

        # Unknown event type, queue for debounce
        entry = FSEntry(
            path=path,
            inode=inode,
            device=device,
            event_type=event_type,
            timestamp=time.time(),
            cookie=event.cookie,
        )
        self._queue_event(entry)

    def _schedule_stability_check(
        self,
        event,
        path: Path,
        inode: int,
        device: int,
        size: int,
        mtime_ns: int,
        cookie: int,
    ) -> None:
        """Schedule or reschedule a stability check for a file."""
        key = str(path)
        now = time.time()

        if key in self._file_stats:
            tracker = self._file_stats[key]
            if tracker.size != size or tracker.mtime_ns != mtime_ns:
                # File changed, update stats and reschedule
                tracker.size = size
                tracker.mtime_ns = mtime_ns
                tracker.first_seen = now
                if tracker.recheck_task:
                    tracker.recheck_task.cancel()
                tracker.recheck_task = self.loop.call_later(
                    self._stability_window + 0.1, self._recheck_stability, path
                )
                logger.debug("File changed, rescheduled stability check", path=str(path))
            # else: same stat, existing recheck will handle it
        else:
            # First time seeing this file, schedule check
            tracker = StabilityTracker(
                size=size,
                mtime_ns=mtime_ns,
                first_seen=now,
                inode=inode,
                device=device,
            )
            tracker.recheck_task = self.loop.call_later(
                self._stability_window + 0.1, self._recheck_stability, path
            )
            self._file_stats[key] = tracker
            logger.debug("First sighting of file, scheduled stability check", path=str(path))

    def _recheck_stability(self, path: Path) -> None:
        """Recheck file stability after the window has passed."""
        key = str(path)
        if key not in self._file_stats:
            return  # Already processed or deleted

        tracker = self._file_stats[key]
        tracker.recheck_task = None

        try:
            stat = path.stat()
            current_size = stat.st_size
            current_mtime_ns = stat.st_mtime_ns
        except FileNotFoundError:
            # File was deleted, clean up
            self._file_stats.pop(key, None)
            logger.debug("File deleted before stability check completed", path=str(path))
            return
        except Exception as e:
            logger.warning("Failed to stat file during recheck", path=str(path), error=str(e))
            # Reschedule once more
            tracker.recheck_task = self.loop.call_later(
                self._stability_window + 0.1, self._recheck_stability, path
            )
            return

        now = time.time()
        if tracker.size == current_size and tracker.mtime_ns == current_mtime_ns:
            # File is stable and window has passed
            if now - tracker.first_seen >= self._stability_window:
                self._file_stats.pop(key, None)
                # Find the original event type from the event that triggered this
                # We need to process a synthetic event - but we don't have the original event
                # Instead, create an FSEntry with CLOSE_WRITE as the event type
                entry = FSEntry(
                    path=path,
                    inode=stat.st_ino,
                    device=stat.st_dev,
                    event_type="CLOSE_WRITE",
                    timestamp=now,
                    cookie=0,
                )
                self._process_stable_entry(entry)
            else:
                # Window not yet passed, reschedule
                tracker.recheck_task = self.loop.call_later(
                    self._stability_window + 0.1 - (now - tracker.first_seen),
                    self._recheck_stability,
                    path,
                )
        else:
            # File changed during wait, reschedule
            tracker.size = current_size
            tracker.mtime_ns = current_mtime_ns
            tracker.first_seen = now
            tracker.recheck_task = self.loop.call_later(
                self._stability_window + 0.1, self._recheck_stability, path
            )
            logger.debug("File changed during stability wait, rescheduled", path=str(path))

    def _handle_delete_event(self, event, path: Path, cookie: int) -> None:
        """Handle DELETE event - remove from stability tracking and process."""
        key = str(path)
        # Check if this file was being tracked for stability
        tracker = self._file_stats.pop(key, None)
        if tracker and tracker.recheck_task:
            tracker.recheck_task.cancel()

        # If we were tracking this file for stability, it was a file being written
        # that got deleted before stabilizing - don't emit DELETE event
        if tracker:
            logger.debug("Deleted file was pending stability, ignoring", path=str(path))
            return

        # Otherwise, it's a real DELETE event for an already-stable file
        entry = FSEntry(
            path=path,
            inode=0,
            device=0,
            event_type="DELETE",
            timestamp=time.time(),
            cookie=cookie,
        )
        self._queue_event(entry)

    def _handle_moved_from(self, event, path: Path, cookie: int) -> None:
        """Handle MOVED_FROM event - track for rename pairing."""
        key = str(path)
        # Remove from stability tracking and get inode/device from tracker
        tracker = self._file_stats.pop(key, None)
        if tracker:
            inode = tracker.inode
            device = tracker.device
        else:
            inode = 0
            device = 0
        if tracker and tracker.recheck_task:
            tracker.recheck_task.cancel()

        entry = FSEntry(
            path=path,
            inode=inode,
            device=device,
            event_type="MOVED_FROM",
            timestamp=time.time(),
            cookie=cookie,
        )
        self._rename_from[cookie] = entry

    def _process_stable_entry(self, entry: FSEntry) -> None:
        """Process an entry for a file that has passed the stability check."""
        if self._is_expected(entry):
            logger.debug("Suppressed expected event", path=str(entry.path), type=entry.event_type)
            return

        key = str(entry.path)
        self._pending_events[key].append(entry)

        # Cancel any existing debounce task for this key
        if key in self._debounce_tasks:
            self._debounce_tasks[key].cancel()
            self._debounce_tasks.pop(key, None)

        # Coalesce and process immediately
        events = self._pending_events.pop(key, [])
        coalesced = self._coalesce_events(events)

        try:
            self.callback(coalesced)
        except Exception as e:
            logger.error("Callback error", error=str(e))

    def _event_type_from_flags(self, mask: int) -> str:
        """Convert inotify mask to event type string."""
        if mask & flags.CLOSE_WRITE:
            return "CLOSE_WRITE"
        elif mask & flags.MOVED_FROM:
            return "MOVED_FROM"
        elif mask & flags.MOVED_TO:
            return "MOVED_TO"
        elif mask & flags.DELETE:
            return "DELETE"
        elif mask & flags.ATTRIB:
            return "ATTRIB"
        return "UNKNOWN"

    def _queue_event(self, entry: FSEntry) -> None:
        """Queue an event for debounced processing."""
        key = str(entry.path)

        self._pending_events[key].append(entry)

        # Cancel existing debounce task
        if key in self._debounce_tasks:
            self._debounce_tasks[key].cancel()

        # Schedule new debounce task
        self._debounce_tasks[key] = self.loop.create_task(self._debounced_process(key))

    async def _debounced_process(self, key: str) -> None:
        """Process events after debounce delay."""
        try:
            await asyncio.sleep(DEBOUNCE_MS / 1000.0)
        except asyncio.CancelledError:
            return

        # Get and clear pending events for this key
        events = self._pending_events.pop(key, [])
        self._debounce_tasks.pop(key, None)

        if not events:
            return

        # Coalesce events: keep the most relevant one
        coalesced = self._coalesce_events(events)

        # Call the callback with coalesced events
        try:
            self.callback(coalesced)
        except Exception as e:
            logger.error("Callback error", error=str(e))

    def _coalesce_events(self, events: list[FSEntry]) -> list[FSEntry]:
        """Coalesce multiple events for the same path."""
        if len(events) <= 1:
            return events

        # Priority: DELETE > MOVED_FROM > MOVED_TO > CLOSE_WRITE > ATTRIB
        priority = {
            "DELETE": 5,
            "MOVED_FROM": 4,
            "MOVED_TO": 3,
            "CLOSE_WRITE": 2,
            "ATTRIB": 1,
        }

        # Sort by priority (highest first) then by timestamp
        events.sort(key=lambda e: (-priority.get(e.event_type, 0), e.timestamp))

        # Return the highest priority event
        return [events[0]]


class Classifier:
    """Classifies file system events into actions."""

    def __init__(self, manager, notifier, paths, config):
        self.manager = manager
        self.notifier = notifier
        self.paths = paths
        self.config = config

        # Grace period tracking for REMOVED detection
        self._grace_timers: dict[str, asyncio.Task] = {}

    def classify(self, entries: list[FSEntry]) -> None:
        """Classify and handle a batch of events."""
        for entry in entries:
            self._handle_entry(entry)

    def _handle_entry(self, entry: FSEntry) -> None:
        """Handle a single classified entry."""
        # Check if it's a tracked app
        app_by_inode = None
        if entry.inode and entry.device:
            app_by_inode = self.manager.db.get_app_by_inode(entry.device, entry.inode)

        app_by_path = self.manager.db.get_app_by_path(str(entry.path))

        # Determine the app we're dealing with
        tracked_app = app_by_inode or app_by_path

        if entry.event_type in ("MOVED_TO", "CLOSE_WRITE"):
            self._handle_create_or_modify(entry, tracked_app)
        elif entry.event_type == "MOVED_FROM":
            self._handle_moved_from(entry, tracked_app)
        elif entry.event_type == "DELETE":
            self._handle_delete(entry, tracked_app)
        elif entry.event_type == "ATTRIB":
            self._handle_attrib(entry, tracked_app)

    def _handle_create_or_modify(self, entry: FSEntry, tracked_app) -> None:
        """Handle CLOSE_WRITE or MOVED_TO for new/modified file."""
        # Check if it's a valid AppImage
        if not self._is_valid_appimage(entry.path):
            logger.debug("Not a valid AppImage, ignoring", path=str(entry.path))
            return

        # Read metadata
        from appimage_launcher.core.metadata import read_appimage_meta

        try:
            meta = read_appimage_meta(entry.path)
        except Exception as e:
            logger.warning("Failed to read AppImage metadata", path=str(entry.path), error=str(e))
            return

        # Check for tombstone match (RESTORE)
        tombstone = self.manager.db.find_tombstone_by_inode(entry.device, entry.inode)
        if not tombstone:
            tombstone = self.manager.db.find_tombstone_by_fingerprint(meta.fingerprint)

        if tombstone:
            self._handle_restore(entry, meta, tombstone)
            return

        # Check if it matches a tracked app_id
        if tracked_app:
            if tracked_app.role == "primary":
                # Check if same inode (MODIFIED) or different inode (REPLACED)
                if tracked_app.inode == entry.inode and tracked_app.device == entry.device:
                    self._handle_modified(entry, tracked_app, meta)
                else:
                    self._handle_replaced(entry, tracked_app, meta)
            else:
                # Kept app modified - treat as MODIFIED
                self._handle_modified(entry, tracked_app, meta)
        else:
            # New app - DROP
            self._handle_drop(entry, meta)

    def _handle_moved_from(self, entry: FSEntry, tracked_app) -> None:
        """Handle MOVED_FROM - start grace period for REMOVED detection."""
        if not tracked_app:
            return

        # Check if there's a matching MOVED_TO (rename) - handled by observer
        # If not paired within grace period, treat as REMOVED
        key = str(entry.path)

        # Cancel existing grace timer
        if key in self._grace_timers:
            self._grace_timers[key].cancel()

        async def grace_expired():
            await asyncio.sleep(RENAME_GRACE_SECONDS)
            # Check if file still gone
            if not entry.path.exists():
                self._handle_removed(entry, tracked_app)
            self._grace_timers.pop(key, None)

        self._grace_timers[key] = asyncio.create_task(grace_expired())

    def _handle_delete(self, entry: FSEntry, tracked_app) -> None:
        """Handle DELETE event."""
        if tracked_app:
            self._handle_removed(entry, tracked_app)

    def _handle_attrib(self, entry: FSEntry, tracked_app) -> None:
        """Handle ATTRIB (attribute change, e.g., chmod)."""
        if not tracked_app:
            return

        # Check if executable bit was removed
        try:
            stat = entry.path.stat()
            if not (stat.st_mode & 0o100):
                # Fix the executable bit
                entry.path.chmod(stat.st_mode | 0o100)
                logger.info("Fixed executable bit", path=str(entry.path))
        except Exception:
            pass

    def _handle_restore(self, entry: FSEntry, meta, tombstone) -> None:
        """Handle RESTORE from tombstone."""
        logger.info("Restoring app from tombstone", app_id=tombstone.app_id)

        # Re-create the app record with old settings
        import time

        from appimage_launcher.core.state import AppRecord

        now = int(time.time())
        stat = entry.path.stat()

        app = AppRecord(
            id=0,
            app_id=tombstone.app_id,
            role="primary",
            display_name=meta.name,
            managed_name=tombstone.last_name,
            name_origin=tombstone.name_origin,
            orig_filename=None,
            path=str(entry.path),
            device=entry.device,
            inode=entry.inode,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            fingerprint=meta.fingerprint,
            version=meta.version,
            update_info=meta.update_info,
            update_policy=tombstone.update_policy,
            self_managed=bool(tombstone.self_managed),
            origin=None,  # Origin not restored from tombstone
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        app.id = self.manager.db.insert_app(app)

        # Write desktop entry and icon
        from appimage_launcher.core.integration import (
            install_icons,
            refresh_caches,
            write_desktop_entry,
        )

        write_desktop_entry(
            app_id=app.app_id,
            display_name=app.display_name,
            exec_path=entry.path,
            icon_name=f"appimage-{app.app_id}",
            categories=meta.categories,
            comment=meta.comment,
            startup_wm_class=None,
            version=app.version,
            name_origin=app.name_origin,
            update_policy=app.update_policy,
        )

        if meta.icon_bytes and meta.icon_ext:
            install_icons(app.app_id, meta.icon_bytes, meta.icon_ext)

        refresh_caches()

        # Remove tombstone
        self.manager.db.delete_tombstone(tombstone.id)

        self.manager.db.insert_event("restored", app.app_id, f"Restored {app.display_name}")
        self.notifier.notify_installed(app, meta.version)

    def _handle_drop(self, entry: FSEntry, meta) -> None:
        """Handle new app drop (DROP)."""
        logger.info("New app dropped", name=meta.name, path=str(entry.path))

        # Install with default options
        try:
            result = self.manager.install(
                entry.path,
                source_action="copy",  # Already in the dir
                conflict_policy="config",
                silent=False,
            )
            self.notifier.notify_installed(result.app, result.app.version)
        except Exception as e:
            logger.error("Failed to install dropped app", error=str(e))
            self.notifier.notify_error(f"Could not install {meta.name}: {e}")

    def _handle_modified(self, entry: FSEntry, app, meta) -> None:
        """Handle MODIFIED (same inode, metadata changed)."""
        logger.info("App modified", app_id=app.app_id)

        # Update record with new metadata
        stat = entry.path.stat()
        app.size = stat.st_size
        app.mtime_ns = stat.st_mtime_ns
        app.fingerprint = meta.fingerprint
        app.version = meta.version
        app.update_info = meta.update_info
        app.updated_at = int(time.time())
        self.manager.db.update_app(app)

        # Update desktop entry
        from appimage_launcher.core.integration import refresh_caches, write_desktop_entry

        write_desktop_entry(
            app_id=app.app_id,
            display_name=meta.name,
            exec_path=entry.path,
            icon_name=f"appimage-{app.app_id}",
            categories=meta.categories,
            comment=meta.comment,
            startup_wm_class=None,
            version=meta.version,
            name_origin=app.name_origin,
            update_policy=app.update_policy,
        )

        if meta.icon_bytes and meta.icon_ext:
            from appimage_launcher.core.integration import install_icons

            install_icons(app.app_id, meta.icon_bytes, meta.icon_ext)

        refresh_caches()

    def _handle_replaced(self, entry: FSEntry, app, meta) -> None:
        """Handle REPLACED (different inode, same app_id)."""
        logger.info("App replaced", app_id=app.app_id)

        # Check if this is a self-update (spec 6.7):
        # - a process with APPIMAGE=<tracked path> is running or was seen in last 30s
        # - the tracked file was removed/replaced within last 30s (tombstone)
        # - the app is flagged self_managed
        is_self_update = False

        # Check if app is running or was recently running
        old_path = Path(app.path)
        if self.manager.running_tracker:
            if self.manager.running_tracker.is_running(
                str(old_path)
            ) or self.manager.running_tracker.was_recently_running(str(old_path)):
                is_self_update = True
                logger.info("Self-update detected: old app was running recently", app_id=app.app_id)

        # Check if app is flagged self_managed
        if app.self_managed:
            is_self_update = True
            logger.info("Self-update detected: app is self_managed", app_id=app.app_id)

        # Check tombstone for recent removal (within 30s)
        import time

        cutoff = time.time() - 30
        tombstone = self.manager.db.find_tombstone_by_inode(app.device, app.inode)
        if tombstone and tombstone.removed_at >= cutoff:
            is_self_update = True
            logger.info("Self-update detected: recent tombstone match", app_id=app.app_id)

        if is_self_update:
            # Handle as self-update - delegate to daemon's self-update handler
            if hasattr(self.manager, "on_self_update") and self.manager.on_self_update:
                self.manager.on_self_update(app.app_id, entry.path)
            else:
                # Fallback: process directly with after_quit logic
                self._process_self_update_direct(app, entry.path, meta)
        else:
            # This is an external replacement (not self-update)
            # Treat as a conflict with replace policy
            try:
                result = self.manager.install(
                    entry.path,
                    source_action="copy",
                    conflict_policy="replace",
                    silent=False,
                )
                self.notifier.notify_updated(result.app, app.version, result.app.version)
            except Exception as e:
                logger.error("Failed to replace app", error=str(e))

    def _process_self_update_direct(self, old_app, new_path: Path, meta) -> None:
        """Process self-update directly (fallback when no daemon callback)."""
        try:
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
            self.notifier.notify_self_updated(result.app, old_app.version, result.app.version)
        except Exception as e:
            logger.error("Failed to process self-update directly", error=str(e))

    def _handle_removed(self, entry: FSEntry, app) -> None:
        """Handle REMOVED (file deleted/moved out)."""
        logger.info("App removed", app_id=app.app_id, name=app.managed_name)

        # Remove integration but keep the file (observer-triggered)
        from appimage_launcher.core.integration import (
            refresh_caches,
            remove_desktop_entry,
            remove_icons,
        )

        remove_desktop_entry(app.app_id)
        remove_icons(app.app_id)
        refresh_caches()

        # Create tombstone
        import time

        from appimage_launcher.core.state import TombstoneRecord

        tombstone = TombstoneRecord(
            id=0,
            app_id=app.app_id,
            device=app.device,
            inode=app.inode,
            fingerprint=app.fingerprint,
            last_name=app.managed_name,
            name_origin=app.name_origin,
            update_policy=app.update_policy,
            self_managed=app.self_managed,
            origin=app.origin,
            removed_at=int(time.time()),
        )
        self.manager.db.insert_tombstone(tombstone)

        # Delete from database
        self.manager.db.delete_app(app.id)

        self.manager.db.insert_event("removed", app.app_id, f"Removed {app.display_name}")
        self.notifier.notify_removed(app)

    def _is_valid_appimage(self, path: Path) -> bool:
        """Quick check if file is a valid AppImage type 2."""
        try:
            with open(path, "rb") as f:
                magic = f.read(10)
                return magic.startswith(b"\x7fELF") and magic[8:10] == b"AI\x02"
        except Exception:
            return False
