"""Reconcile functionality for AppImage Launcher.

Reconciles the Applications directory with the state database on startup and on demand.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from appimage_launcher.constants import IGNORED_DIRS, IGNORED_PATTERNS
from appimage_launcher.core.metadata import read_appimage_meta
from appimage_launcher.core.state import AppRecord, StateDB, TombstoneRecord
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class Reconciler:
    """Reconciles the Applications directory with the state database."""

    def __init__(self, manager, notifier, paths, config):
        self.manager = manager
        self.notifier = notifier
        self.paths = paths
        self.config = config
        self.db: StateDB = manager.db

    def reconcile(self) -> dict[str, int]:
        """Run full reconciliation.

        Returns:
            Dict with counts of actions taken.
        """
        logger.info("Starting reconciliation")
        stats = {
            "scanned": 0,
            "matched_inode": 0,
            "matched_fingerprint": 0,
            "matched_tombstone": 0,
            "new_drops": 0,
            "removed_apps": 0,
            "fixed_entries": 0,
            "fixed_icons": 0,
            "fixed_permissions": 0,
            "conflicts": 0,
        }

        # 1. Scan the directory for valid AppImages
        app_files = self._scan_applications_dir()
        stats["scanned"] = len(app_files)

        # 2. Get current database state
        db_apps = self.db.list_apps(include_kept=True)
        apps_by_inode = {(a.device, a.inode): a for a in db_apps}
        apps_by_path = {a.path: a for a in db_apps}

        # Track which DB apps we've seen
        seen_db_apps = set()

        # 3. Match each file to a database record
        for file_path, meta in app_files:
            stats = self._match_file(
                file_path, meta, apps_by_inode, apps_by_path, seen_db_apps, stats
            )

        # 4. Handle DB records without a file (REMOVED)
        for app in db_apps:
            if app.id not in seen_db_apps:
                self._handle_missing_file(app)
                stats["removed_apps"] += 1

        # 5. Fix drifted entries/icons/permissions
        fix_stats = self._fix_drifted_integration(db_apps)
        stats.update(fix_stats)

        logger.info("Reconciliation complete", **stats)
        return stats

    def _scan_applications_dir(self) -> list[tuple[Path, Any]]:
        """Scan the Applications directory for valid AppImages."""
        import fnmatch

        results = []

        if not self.paths.applications_dir.exists():
            return results

        for entry in self.paths.applications_dir.iterdir():
            if not entry.is_file():
                continue

            name = entry.name

            # Check ignored patterns
            ignored = False
            for pattern in IGNORED_PATTERNS:
                if fnmatch.fnmatch(name, pattern):
                    ignored = True
                    break

            if ignored:
                continue

            if name in IGNORED_DIRS:
                continue

            # Quick AppImage check
            if not self._is_valid_appimage(entry):
                continue

            # Read metadata
            try:
                meta = read_appimage_meta(entry)
                results.append((entry, meta))
            except Exception as e:
                logger.warning(
                    "Failed to read metadata during reconcile", path=str(entry), error=str(e)
                )

        return results

    def _is_valid_appimage(self, path: Path) -> bool:
        """Quick check if file is a valid AppImage type 2."""
        try:
            with open(path, "rb") as f:
                magic = f.read(10)
                return magic.startswith(b"\x7fELF") and magic[8:10] == b"AI\x02"
        except Exception:
            return False

    def _match_file(
        self,
        file_path: Path,
        meta: Any,
        apps_by_inode: dict,
        apps_by_path: dict,
        seen_db_apps: set,
        stats: dict,
    ) -> dict:
        """Match a file to a database record."""
        stat = file_path.stat()
        device = stat.st_dev
        inode = stat.st_ino

        # Try match by (device, inode)
        key = (device, inode)
        if key in apps_by_inode:
            app = apps_by_inode[key]
            seen_db_apps.add(app.id)
            stats["matched_inode"] += 1
            self._update_if_needed(app, file_path, meta)
            return stats

        # Try match by fingerprint
        fingerprint = meta.fingerprint
        for app in apps_by_inode.values():
            if app.fingerprint == fingerprint and app.id not in seen_db_apps:
                seen_db_apps.add(app.id)
                stats["matched_fingerprint"] += 1
                # Update inode/device/path
                app.device = device
                app.inode = inode
                app.path = str(file_path)
                app.size = stat.st_size
                app.mtime_ns = stat.st_mtime_ns
                app.updated_at = int(time.time())
                self.db.update_app(app)
                self._update_integration(app, meta)
                return stats

        # Try match by tombstone
        tombstone = self.db.find_tombstone_by_inode(device, inode)
        if not tombstone:
            tombstone = self.db.find_tombstone_by_fingerprint(fingerprint)

        if tombstone:
            stats["matched_tombstone"] += 1
            self._restore_from_tombstone(file_path, meta, tombstone)
            return stats

        # No match - treat as new drop
        stats["new_drops"] += 1
        self._handle_new_drop(file_path, meta)
        return stats

    def _update_if_needed(self, app: AppRecord, file_path: Path, meta: Any) -> None:
        """Update app record and integration if metadata changed."""
        stat = file_path.stat()

        # Check if anything changed
        changed = False
        if app.size != stat.st_size:
            app.size = stat.st_size
            changed = True
        if app.mtime_ns != stat.st_mtime_ns:
            app.mtime_ns = stat.st_mtime_ns
            changed = True
        if app.fingerprint != meta.fingerprint:
            app.fingerprint = meta.fingerprint
            changed = True
        if app.version != meta.version:
            app.version = meta.version
            changed = True
        if app.update_info != meta.update_info:
            app.update_info = meta.update_info
            changed = True

        if changed:
            app.updated_at = int(time.time())
            self.db.update_app(app)
            self._update_integration(app, meta, file_path)

        # Ensure executable
        if not (stat.st_mode & 0o100):
            file_path.chmod(stat.st_mode | 0o100)

    def _update_integration(self, app: AppRecord, meta: Any, file_path: Path) -> None:
        """Update desktop entry and icons for an app."""
        from appimage_launcher.core.integration import (
            install_icons,
            refresh_caches,
            write_desktop_entry,
        )

        write_desktop_entry(
            app_id=app.app_id,
            display_name=meta.name,
            exec_path=file_path,
            icon_name=f"appimage-{app.app_id}",
            categories=meta.categories,
            comment=meta.comment,
            startup_wm_class=None,
            version=meta.version,
            name_origin=app.name_origin,
            update_policy=app.update_policy,
        )

        if meta.icon_bytes and meta.icon_ext:
            install_icons(app.app_id, meta.icon_bytes, meta.icon_ext)

        refresh_caches()

    def _restore_from_tombstone(
        self, file_path: Path, meta: Any, tombstone: TombstoneRecord
    ) -> None:
        """Restore an app from a tombstone."""
        logger.info("Restoring app from tombstone", app_id=tombstone.app_id)

        stat = file_path.stat()
        now = int(time.time())

        app = AppRecord(
            id=0,
            app_id=tombstone.app_id,
            role="primary",
            display_name=meta.name,
            managed_name=tombstone.last_name,
            name_origin=tombstone.name_origin,
            orig_filename=None,
            path=str(file_path),
            device=stat.st_dev,
            inode=stat.st_ino,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            fingerprint=meta.fingerprint,
            version=meta.version,
            update_info=meta.update_info,
            update_policy=tombstone.update_policy,
            self_managed=bool(tombstone.self_managed),
            origin=tombstone.origin,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        app.id = self.db.insert_app(app)

        # Write desktop entry and icon
        from appimage_launcher.core.integration import (
            install_icons,
            refresh_caches,
            write_desktop_entry,
        )

        write_desktop_entry(
            app_id=app.app_id,
            display_name=app.display_name,
            exec_path=file_path,
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
        self.db.delete_tombstone(tombstone.id)

        self.db.insert_event("restored", app.app_id, f"Restored {app.display_name}")
        self.notifier.notify_installed(app, meta.version)

    def _handle_new_drop(self, file_path: Path, meta: Any) -> None:
        """Handle a new app file dropped in the directory."""
        logger.info("New app found during reconcile", name=meta.name, path=str(file_path))

        try:
            result = self.manager.install(
                file_path,
                source_action="copy",
                conflict_policy="config",
                silent=True,  # Don't notify for reconcile
            )
            # Notify only if it was actually installed (not a duplicate)
            if result.changed:
                self.notifier.notify_installed(result.app, result.app.version)
        except Exception as e:
            logger.error("Failed to install app during reconcile", error=str(e))

    def _handle_missing_file(self, app: AppRecord) -> None:
        """Handle a database record with no corresponding file."""
        logger.info(
            "App file missing, creating tombstone", app_id=app.app_id, name=app.managed_name
        )

        from appimage_launcher.core.integration import (
            refresh_caches,
            remove_desktop_entry,
            remove_icons,
        )

        remove_desktop_entry(app.app_id)
        remove_icons(app.app_id)
        refresh_caches()

        # Create tombstone
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
        self.db.insert_tombstone(tombstone)

        # Delete from database
        self.db.delete_app(app.id)

        self.db.insert_event("removed", app.app_id, f"Removed {app.display_name} (reconcile)")
        self.notifier.notify_removed(app)

    def _fix_drifted_integration(self, db_apps: list[AppRecord]) -> dict[str, int]:
        """Fix desktop entries, icons, and permissions that may have drifted."""
        stats = {
            "fixed_entries": 0,
            "fixed_icons": 0,
            "fixed_permissions": 0,
        }

        from appimage_launcher.core.integration import (
            desktop_entry_exists,
            icon_exists,
            install_icons,
            refresh_caches,
            write_desktop_entry,
        )

        for app in db_apps:
            file_path = Path(app.path)

            # Fix permissions
            if file_path.exists():
                stat = file_path.stat()
                if not (stat.st_mode & 0o100):
                    file_path.chmod(stat.st_mode | 0o100)
                    stats["fixed_permissions"] += 1

            # Check desktop entry
            if not desktop_entry_exists(app.app_id, app.role, app.version):
                # Recreate desktop entry
                # Need to read metadata from file
                if file_path.exists():
                    try:
                        meta = read_appimage_meta(file_path)
                        write_desktop_entry(
                            app_id=app.app_id,
                            display_name=meta.name,
                            exec_path=file_path,
                            icon_name=f"appimage-{app.app_id}",
                            categories=meta.categories,
                            comment=meta.comment,
                            startup_wm_class=None,
                            version=meta.version,
                            name_origin=app.name_origin,
                            update_policy=app.update_policy,
                            role=app.role,
                            version_slug=app.version or "old" if app.role == "kept" else None,
                        )
                        stats["fixed_entries"] += 1
                    except Exception:
                        pass
            else:
                # Verify desktop entry points to correct path
                # This would need more detailed checking
                pass

            # Check icons
            if not icon_exists(app.app_id):
                if file_path.exists():
                    try:
                        meta = read_appimage_meta(file_path)
                        if meta.icon_bytes and meta.icon_ext:
                            install_icons(app.app_id, meta.icon_bytes, meta.icon_ext)
                            stats["fixed_icons"] += 1
                    except Exception:
                        pass

        if any(stats.values()):
            refresh_caches()

        return stats


def reconcile(manager, notifier, paths, config) -> dict[str, int]:
    """Convenience function to run reconciliation."""
    reconciler = Reconciler(manager, notifier, paths, config)
    return reconciler.reconcile()
