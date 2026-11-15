"""Core Manager for AppImage Launcher.

All mutations go through this class. Handles install, remove, rename, enable/disable, revert.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appimage_launcher.config import get_config
from appimage_launcher.core.identity import compute_full_sha256
from appimage_launcher.core.integration import (
    ensure_executable,
    install_icons,
    refresh_caches,
    remove_desktop_entry,
    remove_icons,
    write_desktop_entry,
)
from appimage_launcher.core.metadata import read_appimage_meta
from appimage_launcher.core.naming import (
    classify_collision,
    generate_kept_name,
    render_template,
    sanitize_name,
)
from appimage_launcher.core.state import (
    AppRecord,
    BackupRecord,
    StateDB,
    TombstoneRecord,
    get_state_db,
)
from appimage_launcher.core.updates import (
    UpdateApplyResult,
    UpdateCheckResult,
    apply_update,
    check_for_updates_detailed,
    has_signature,
    parse_upd_info,
)
from appimage_launcher.core.versions import VersionRelation, compare_versions
from appimage_launcher.logging import get_logger
from appimage_launcher.paths import get_paths

logger = get_logger(__name__)


@dataclass
class InstallResult:
    """Result of an install operation."""

    app: AppRecord
    changed: bool
    previous: str | None


@dataclass
class ConflictInfo:
    """Information about a conflict."""

    old_app: AppRecord
    new_meta: Any  # AppImageMeta
    relation: VersionRelation
    is_self_update: bool


class Manager:
    """Core manager for AppImage operations."""

    def __init__(self, db: StateDB | None = None):
        self.db = db or get_state_db()
        self.paths = get_paths()
        self.config = get_config()
        self._lock_file: Any = None
        self.running_tracker = None  # Will be set by daemon
        self.on_self_update = None  # Callback for self-update detection

    @contextmanager
    def _exclusive_lock(self):
        """Acquire exclusive lock for direct mode operations."""
        lock_path = self.paths.lock_file
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(lock_path, "w")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except BlockingIOError as e:
            raise RuntimeError("Could not acquire lock (another operation in progress)") from e
        finally:
            if self._lock_file:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
                self._lock_file = None

    def _get_existing_apps_dict(self) -> dict[str, dict]:
        """Get existing apps as dict for collision detection."""
        apps = self.db.list_apps(include_kept=True)
        return {app.managed_name: {"app_id": app.app_id, "record": app} for app in apps}

    def install(
        self,
        source_path: Path,
        name: str | None = None,
        source_action: str = "copy",
        conflict_policy: str = "config",
        origin: str | None = None,
        silent: bool = False,
        update_policy: str = "auto",
    ) -> InstallResult:
        """Install an AppImage from source path."""
        source_path = source_path.resolve()

        # Read metadata
        meta = read_appimage_meta(source_path)

        # Check for identical file already installed
        sha256 = compute_full_sha256(source_path)
        existing_by_hash = self._find_by_sha256(sha256)
        if existing_by_hash:
            logger.info("Identical file already installed", app_id=existing_by_hash.app_id)
            return InstallResult(app=existing_by_hash, changed=False, previous=None)

        # Check collisions
        target_name = name or render_template(
            self.config.naming.template,
            meta.name,
            meta.version,
            meta.app_id,
        )
        target_name = sanitize_name(target_name)
        target_path = self.paths.applications_dir / target_name

        existing_apps = self._get_existing_apps_dict()
        collision = classify_collision(target_path, meta.app_id, existing_apps)

        if collision.type == "non_appimage":
            raise ValueError(f"Name collision with non-AppImage: {target_name}")
        elif collision.type == "same_app_id":
            # Handle conflict
            return self._handle_conflict(
                existing_apps[target_name]["record"],
                meta,
                source_path,
                source_action,
                conflict_policy,
                origin,
                silent,
                update_policy,
                name_origin="user" if name else "auto",
                is_self_update=False,  # Regular install is not a self-update
            )
        elif collision.type == "different_app_id":
            # Auto-rename
            target_name = collision.suggested_name or target_name
            target_path = self.paths.applications_dir / target_name

        # Perform installation
        return self._do_install(
            source_path,
            target_path,
            meta,
            source_action,
            origin,
            silent,
            update_policy,
            name_origin="user" if name else "auto",
        )

    def _find_by_sha256(self, sha256: str) -> AppRecord | None:
        """Find app by SHA-256 (checks primary apps)."""
        return self.db.get_app_by_sha256(sha256)

    def _handle_conflict(
        self,
        old_app: AppRecord,
        new_meta: Any,
        source_path: Path,
        source_action: str,
        conflict_policy: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
        name_origin: str,
        is_self_update: bool = False,
    ) -> InstallResult:
        """Handle conflict with existing app."""
        relation = compare_versions(old_app.version, new_meta.version)

        if conflict_policy == "config":
            conflict_policy = self.config.install.conflict_policy

        if conflict_policy == "replace":
            return self._replace_app(
                old_app,
                new_meta,
                source_path,
                source_action,
                origin,
                silent,
                update_policy,
                is_self_update,
            )
        elif conflict_policy == "keep":
            return self._keep_both(
                old_app, new_meta, source_path, source_action, origin, silent, update_policy
            )
        elif conflict_policy == "cancel":
            raise ValueError("Install cancelled due to conflict")
        elif conflict_policy == "error":
            # Return conflict info for caller to handle
            raise ConflictError(ConflictInfo(old_app, new_meta, relation, is_self_update))
        else:
            raise ValueError(f"Unknown conflict policy: {conflict_policy}")

    def _replace_app(
        self,
        old_app: AppRecord,
        new_meta: Any,
        source_path: Path,
        source_action: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
        is_self_update: bool = False,
    ) -> InstallResult:
        """Replace existing app with new version."""
        # Move old to .previous
        previous_path = self._move_to_previous(old_app)

        # Install new at old location (keeping user-chosen name)
        target_path = Path(old_app.path)
        result = self._do_install(
            source_path,
            target_path,
            new_meta,
            source_action,
            origin or old_app.origin,
            silent,
            update_policy,
            name_origin=old_app.name_origin,
            previous_path=previous_path,
        )

        # If this is a self-update, mark the new app as self_managed
        if is_self_update:
            result.app.self_managed = True
            self.db.update_app(result.app)

        return result

    def _keep_both(
        self,
        old_app: AppRecord,
        new_meta: Any,
        source_path: Path,
        source_action: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
    ) -> InstallResult:
        """Keep both versions - rename old to kept, install new as primary."""
        # Generate kept name for old app
        existing_names = set(self._get_existing_apps_dict().keys())
        kept_name = generate_kept_name(old_app.managed_name, old_app.version, existing_names)
        kept_path = self.paths.applications_dir / kept_name

        # Rename old file
        old_path = Path(old_app.path)
        if old_path.exists():
            old_path.rename(kept_path)

        # Update old app record to kept
        old_app.role = "kept"
        old_app.managed_name = kept_name
        old_app.path = str(kept_path)
        stat = kept_path.stat()
        old_app.device = stat.st_dev
        old_app.inode = stat.st_ino
        old_app.size = stat.st_size
        old_app.mtime_ns = stat.st_mtime_ns
        self.db.update_app(old_app)

        # Update desktop entry for kept
        write_desktop_entry(
            app_id=old_app.app_id,
            display_name=old_app.display_name,
            exec_path=kept_path,
            icon_name=f"appimage-{old_app.app_id}",
            categories=[],  # Would need to preserve
            comment=None,
            startup_wm_class=None,
            version=old_app.version,
            name_origin=old_app.name_origin,
            update_policy=old_app.update_policy,
            role="kept",
            version_slug=old_app.version or "old",
        )

        # Install new as primary
        target_name = old_app.managed_name  # Keep the original name
        target_path = self.paths.applications_dir / target_name
        return self._do_install(
            source_path,
            target_path,
            new_meta,
            source_action,
            origin,
            silent,
            update_policy,
            name_origin=old_app.name_origin,
        )

    def _do_install(
        self,
        source_path: Path,
        target_path: Path,
        meta: Any,
        source_action: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
        name_origin: str,
        previous_path: str | None = None,
    ) -> InstallResult:
        """Perform the actual installation."""
        # Stage the file
        if source_path != target_path:
            if source_action == "move":
                # Move to staging first
                staging_path = self.paths.staging_dir / target_path.name
                self.paths.staging_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_path), str(staging_path))
                staging_path.rename(target_path)
            else:
                # Copy to staging then atomic rename
                staging_path = self.paths.staging_dir / target_path.name
                self.paths.staging_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, staging_path)
                staging_path.rename(target_path)
        else:
            # Already in place (adopting)
            pass

        # Ensure executable
        ensure_executable(target_path)

        # Get file stats
        stat = target_path.stat()

        # Compute SHA-256 for idempotent installs
        sha256 = compute_full_sha256(target_path)

        # Write xattr for original name
        try:
            import xattr

            xattr.setxattr(str(target_path), "user.appimage.origname", source_path.name.encode())
            if origin:
                xattr.setxattr(str(target_path), "user.appimage.origin", origin.encode())
        except Exception:
            pass

        # Write desktop entry
        write_desktop_entry(
            app_id=meta.app_id,
            display_name=meta.name,
            exec_path=target_path,
            icon_name=f"appimage-{meta.app_id}",
            categories=meta.categories,
            comment=meta.comment,
            startup_wm_class=None,  # Would need from desktop
            version=meta.version,
            name_origin=name_origin,
            update_policy=update_policy,
            catalog_id=json.loads(origin).get("catalog_id") if origin else None,
            origin_source=json.loads(origin).get("source") if origin else None,
        )

        # Install icon
        if meta.icon_bytes and meta.icon_ext:
            install_icons(meta.app_id, meta.icon_bytes, meta.icon_ext)

        # Create database record
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id=meta.app_id,
            role="primary",
            display_name=meta.name,
            managed_name=target_path.name,
            name_origin=name_origin,
            orig_filename=source_path.name,
            path=str(target_path),
            device=stat.st_dev,
            inode=stat.st_ino,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            fingerprint=meta.fingerprint,
            version=meta.version,
            update_info=meta.update_info,
            update_policy=update_policy,
            self_managed=False,
            origin=origin,
            sha256=sha256,  # Store SHA-256 for idempotent installs
            installed_at=now,
            updated_at=now,
        )
        app.id = self.db.insert_app(app)

        # Record backup if replacing
        if previous_path:
            backup = BackupRecord(
                id=0,
                app_id=meta.app_id,
                path=previous_path,
                version=app.version,
                size=stat.st_size,
                created_at=now,
            )
            self.db.insert_backup(backup)

        # Refresh caches
        refresh_caches()

        # Log event
        self.db.insert_event(
            "installed", meta.app_id, f"Installed {meta.name} {meta.version or ''}"
        )

        logger.info("Installed app", app_id=meta.app_id, path=str(target_path))
        return InstallResult(app=app, changed=True, previous=previous_path)

    def _move_to_previous(self, app: AppRecord) -> str:
        """Move an app file to .previous directory."""
        self.paths.previous_dir.mkdir(parents=True, exist_ok=True)
        source = Path(app.path)
        if not source.exists():
            return ""

        # Generate unique name in .previous
        timestamp = int(time.time() * 1000)
        dest_name = f"{app.managed_name}-{timestamp}"
        dest = self.paths.previous_dir / dest_name

        source.rename(dest)

        # Keep only N previous versions
        self._prune_previous(app.app_id)

        return str(dest)

    def _prune_previous(self, app_id: str) -> None:
        """Keep only configured number of previous versions."""
        keep = self.config.update.previous_versions_keep
        backups = self.db.get_backups(app_id)
        if len(backups) > keep:
            for backup in backups[keep:]:
                try:
                    Path(backup.path).unlink(missing_ok=True)
                except Exception:
                    pass
                self.db.delete_backup(backup.id)

    def remove(
        self, name: str, file_action: str = "trash", keep_file: bool = False, purge: bool = False
    ) -> bool:
        """Remove an app by managed name."""
        app = self._find_app_by_name(name)
        if not app:
            raise ValueError(f"App not found: {name}")

        # Remove desktop entry and icons
        remove_desktop_entry(app.app_id)
        remove_icons(app.app_id)

        # Handle file
        file_path = Path(app.path)
        if not keep_file and file_path.exists():
            if purge:
                file_path.unlink()
            elif file_action == "trash":
                self._trash_file(file_path)
            # else keep in place

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

        # Refresh caches
        refresh_caches()

        # Log event
        self.db.insert_event("removed", app.app_id, f"Removed {app.display_name}")

        logger.info("Removed app", app_id=app.app_id)
        return True

    def _trash_file(self, path: Path) -> None:
        """Move file to trash."""
        for cmd in [["gio", "trash"], ["kioclient", "trash"]]:
            try:
                subprocess.run(cmd + [str(path)], timeout=10, check=True)
                return
            except Exception:
                continue
        # Fallback: XDG trash spec
        # TODO: Implement XDG trash
        path.unlink()

    def rename(self, name: str, new_name: str, reset: bool = False) -> AppRecord:
        """Rename an app."""
        app = self._find_app_by_name(name)
        if not app:
            raise ValueError(f"App not found: {name}")

        if reset:
            new_name = sanitize_name(app.display_name)
        else:
            new_name = sanitize_name(new_name)

        if new_name == app.managed_name:
            return app

        # Check collision
        target_path = self.paths.applications_dir / new_name
        existing_apps = self._get_existing_apps_dict()
        collision = classify_collision(target_path, app.app_id, existing_apps)
        if collision.type != "none":
            raise ValueError(f"Name collision: {new_name}")

        # Rename file
        old_path = Path(app.path)
        if old_path.exists():
            old_path.rename(target_path)

        # Update record
        old_managed_name = app.managed_name
        app.managed_name = new_name
        app.path = str(target_path)
        app.name_origin = "user"
        stat = target_path.stat()
        app.device = stat.st_dev
        app.inode = stat.st_ino
        app.size = stat.st_size
        app.mtime_ns = stat.st_mtime_ns
        app.updated_at = int(time.time())
        self.db.update_app(app)

        # Update desktop entry
        write_desktop_entry(
            app_id=app.app_id,
            display_name=app.display_name,
            exec_path=target_path,
            icon_name=f"appimage-{app.app_id}",
            categories=[],  # Would need to preserve
            comment=None,
            startup_wm_class=None,
            version=app.version,
            name_origin="user",
            update_policy=app.update_policy,
        )

        # Refresh caches
        refresh_caches()

        # Log event
        self.db.insert_event("renamed", app.app_id, f"Renamed {old_managed_name} -> {new_name}")

        logger.info("Renamed app", app_id=app.app_id, old=old_managed_name, new=new_name)
        return app

    def set_policy(self, name: str, policy: str) -> AppRecord:
        """Set update policy for an app."""
        app = self._find_app_by_name(name)
        if not app:
            raise ValueError(f"App not found: {name}")

        if policy not in ("auto", "manual", "pinned"):
            raise ValueError(f"Invalid policy: {policy}")

        app.update_policy = policy
        app.updated_at = int(time.time())
        self.db.update_app(app)

        # Update desktop entry
        write_desktop_entry(
            app_id=app.app_id,
            display_name=app.display_name,
            exec_path=Path(app.path),
            icon_name=f"appimage-{app.app_id}",
            categories=[],
            comment=None,
            startup_wm_class=None,
            version=app.version,
            name_origin=app.name_origin,
            update_policy=policy,
        )

        refresh_caches()
        self.db.insert_event("policy_changed", app.app_id, f"Policy set to {policy}")
        return app

    def set_origin(self, name: str, origin_json: str | None) -> AppRecord:
        """Set or clear origin record."""
        app = self._find_app_by_name(name)
        if not app:
            raise ValueError(f"App not found: {name}")

        if origin_json:
            # Validate JSON
            try:
                origin_data = json.loads(origin_json)
                if "source" not in origin_data or "catalog_id" not in origin_data:
                    raise ValueError("Origin must have 'source' and 'catalog_id'")
            except json.JSONDecodeError as e:
                raise ValueError("Invalid JSON") from e

            app.origin = origin_json
        else:
            app.origin = None

        app.updated_at = int(time.time())
        self.db.update_app(app)

        # Update xattr
        try:
            import xattr

            if app.origin:
                xattr.setxattr(str(app.path), "user.appimage.origin", app.origin.encode())
            else:
                xattr.removexattr(str(app.path), "user.appimage.origin")
        except Exception:
            pass

        # Update desktop entry
        catalog_id = None
        origin_source = None
        if app.origin:
            try:
                data = json.loads(app.origin)
                catalog_id = data.get("catalog_id")
                origin_source = data.get("source")
            except Exception:
                pass

        write_desktop_entry(
            app_id=app.app_id,
            display_name=app.display_name,
            exec_path=Path(app.path),
            icon_name=f"appimage-{app.app_id}",
            categories=[],
            comment=None,
            startup_wm_class=None,
            version=app.version,
            name_origin=app.name_origin,
            update_policy=app.update_policy,
            catalog_id=catalog_id,
            origin_source=origin_source,
        )

        refresh_caches()
        self.db.insert_event("origin_changed", app.app_id, "Origin updated")
        return app

    def revert(self, name: str) -> AppRecord:
        """Revert to previous version."""
        app = self._find_app_by_name(name)
        if not app:
            raise ValueError(f"App not found: {name}")

        backups = self.db.get_backups(app.app_id)
        if not backups:
            raise ValueError("No previous version to revert to")

        latest_backup = backups[0]
        backup_path = Path(latest_backup.path)
        if not backup_path.exists():
            raise ValueError("Backup file not found")

        # Move current to staging
        current_path = Path(app.path)
        staging_path = self.paths.staging_dir / f"{app.managed_name}.current"
        self.paths.staging_dir.mkdir(parents=True, exist_ok=True)
        if current_path.exists():
            current_path.rename(staging_path)

        # Move backup to current location
        backup_path.rename(current_path)

        # Update record
        app.path = str(current_path)
        app.version = latest_backup.version
        app.updated_at = int(time.time())
        stat = current_path.stat()
        app.device = stat.st_dev
        app.inode = stat.st_ino
        app.size = stat.st_size
        app.mtime_ns = stat.st_mtime_ns
        self.db.update_app(app)

        # Update desktop entry
        write_desktop_entry(
            app_id=app.app_id,
            display_name=app.display_name,
            exec_path=current_path,
            icon_name=f"appimage-{app.app_id}",
            categories=[],
            comment=None,
            startup_wm_class=None,
            version=app.version,
            name_origin=app.name_origin,
            update_policy=app.update_policy,
        )

        refresh_caches()
        self.db.insert_event("reverted", app.app_id, f"Reverted to {app.version}")
        return app

    def _find_app_by_name(self, name: str) -> AppRecord | None:
        """Find app by managed name (case-insensitive, prefix match)."""
        apps = self.db.list_apps(include_kept=True)
        matches = [a for a in apps if a.managed_name.lower() == name.lower()]
        if len(matches) == 1:
            return matches[0]

        # Try prefix match
        matches = [a for a in apps if a.managed_name.lower().startswith(name.lower())]
        if len(matches) == 1:
            return matches[0]

        # Try app_id match
        matches = [a for a in apps if a.app_id.lower() == name.lower()]
        if len(matches) == 1:
            return matches[0]

        return None

    def disable(self, name: str) -> AppRecord:
        """Disable an app (move to .disabled directory)."""
        app = self._find_app_by_name(name)
        if not app:
            raise ValueError(f"App not found: {name}")

        # Create .disabled directory
        disabled_dir = self.paths.applications_dir / ".disabled"
        disabled_dir.mkdir(parents=True, exist_ok=True)

        # Move file to .disabled
        source_path = Path(app.path)
        if source_path.exists():
            dest_path = disabled_dir / app.managed_name
            source_path.rename(dest_path)

        # Remove desktop entry and icons
        remove_desktop_entry(app.app_id)
        remove_icons(app.app_id)

        # Create tombstone to preserve settings
        import time
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

        # Refresh caches
        refresh_caches()

        # Log event
        self.db.insert_event("disabled", app.app_id, f"Disabled {app.display_name}")

        logger.info("Disabled app", app_id=app.app_id)
        return app

    def enable(self, name: str) -> AppRecord:
        """Enable an app (restore from .disabled directory)."""
        # First check tombstones for a disabled app
        tombstones = self.db.list_tombstones()
        tombstone = None
        for t in tombstones:
            if t.last_name.lower() == name.lower() or t.app_id.lower() == name.lower():
                tombstone = t
                break

        if not tombstone:
            raise ValueError(f"No disabled app found: {name}")

        disabled_dir = self.paths.applications_dir / ".disabled"
        source_path = disabled_dir / tombstone.last_name

        if not source_path.exists():
            raise ValueError(f"Disabled file not found: {source_path}")

        # Read metadata from the file
        meta = read_appimage_meta(source_path)

        # Move file back to Applications dir
        target_path = self.paths.applications_dir / tombstone.last_name
        source_path.rename(target_path)

        # Ensure executable
        ensure_executable(target_path)

        # Get file stats
        stat = target_path.stat()

        # Compute SHA-256
        sha256 = compute_full_sha256(target_path)

        # Write desktop entry
        write_desktop_entry(
            app_id=meta.app_id,
            display_name=meta.name,
            exec_path=target_path,
            icon_name=f"appimage-{meta.app_id}",
            categories=meta.categories,
            comment=meta.comment,
            startup_wm_class=None,
            version=meta.version,
            name_origin=tombstone.name_origin,
            update_policy=tombstone.update_policy,
            catalog_id=json.loads(tombstone.origin).get("catalog_id") if tombstone.origin else None,
            origin_source=json.loads(tombstone.origin).get("source") if tombstone.origin else None,
        )

        # Install icon
        if meta.icon_bytes and meta.icon_ext:
            install_icons(meta.app_id, meta.icon_bytes, meta.icon_ext)

        # Create database record
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id=meta.app_id,
            role="primary",
            display_name=meta.name,
            managed_name=tombstone.last_name,
            name_origin=tombstone.name_origin,
            orig_filename=tombstone.last_name,
            path=str(target_path),
            device=stat.st_dev,
            inode=stat.st_ino,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            fingerprint=meta.fingerprint,
            version=meta.version,
            update_info=meta.update_info,
            update_policy=tombstone.update_policy,
            self_managed=tombstone.self_managed,
            origin=tombstone.origin,
            sha256=sha256,
            installed_at=now,
            updated_at=now,
        )
        app.id = self.db.insert_app(app)

        # Remove tombstone
        self.db.delete_tombstone(tombstone.id)

        # Refresh caches
        refresh_caches()

        # Log event
        self.db.insert_event("enabled", app.app_id, f"Enabled {app.display_name}")

        logger.info("Enabled app", app_id=app.app_id)
        return app

    def check_updates(self, name: str) -> UpdateCheckResult:
        """Check for updates for a specific app."""
        app = self._find_app_by_name(name)
        if not app:
            return UpdateCheckResult(
                has_update=False,
                current_version=None,
                latest_version=None,
                update_info=None,
                error="App not found",
            )

        # Check update-authority rule (6.9): skip catalog-linked apps
        if app.origin and self.config.update.skip_catalog_linked:
            return UpdateCheckResult(
                has_update=False,
                current_version=app.version,
                latest_version=None,
                update_info=None,
                error="catalog_linked",
            )

        # Check if app has update info
        if not app.update_info:
            return UpdateCheckResult(
                has_update=False,
                current_version=app.version,
                latest_version=None,
                update_info=None,
                error="No .upd_info section",
            )

        update_info = parse_upd_info(app.update_info)
        if not update_info:
            return UpdateCheckResult(
                has_update=False,
                current_version=app.version,
                latest_version=None,
                update_info=None,
                error="Unsupported .upd_info format",
            )

        # Check for updates
        app_path = Path(app.path)
        if not app_path.exists():
            return UpdateCheckResult(
                has_update=False,
                current_version=app.version,
                latest_version=None,
                update_info=update_info,
                error="AppImage file not found",
            )

        return check_for_updates_detailed(app_path, app.version)

    def check_updates_all(self) -> list[UpdateCheckResult]:
        """Check for updates for all primary apps."""
        apps = self.db.list_apps(include_kept=False)
        results = []
        for app in apps:
            result = self.check_updates(app.managed_name)
            results.append(result)
        return results

    def apply_update(self, name: str, staging_dir: Path | None = None) -> UpdateApplyResult:
        """Apply update to an app.

        Args:
            name: Managed name or app_id of the app
            staging_dir: Optional staging directory (uses default if not provided)

        Returns:
            UpdateApplyResult with success status
        """
        app = self._find_app_by_name(name)
        if not app:
            return UpdateApplyResult(
                success=False,
                output_path=None,
                error="App not found",
            )

        # Check update-authority rule (6.9): skip catalog-linked apps
        if app.origin and self.config.update.skip_catalog_linked:
            return UpdateApplyResult(
                success=False,
                output_path=None,
                error="App is catalog-linked; updates managed by front-end",
            )

        # Check if app has update info
        if not app.update_info:
            return UpdateApplyResult(
                success=False,
                output_path=None,
                error="No .upd_info section",
            )

        update_info = parse_upd_info(app.update_info)
        if not update_info:
            return UpdateApplyResult(
                success=False,
                output_path=None,
                error="Unsupported .upd_info format",
            )

        # Check if app has signature
        app_path = Path(app.path)
        if not app_path.exists():
            return UpdateApplyResult(
                success=False,
                output_path=None,
                error="AppImage file not found",
            )

        verify_sig = has_signature(app_path)

        # Determine output path (staging)
        if staging_dir is None:
            staging_dir = self.paths.staging_dir
        staging_dir.mkdir(parents=True, exist_ok=True)

        output_path = staging_dir / f"{app.managed_name}.updated"

        # Apply update
        result = apply_update(app_path, update_info, output_path, verify_signature=verify_sig)

        if result.success and result.output_path:
            # Verify the updated file is a valid AppImage
            try:
                meta = read_appimage_meta(result.output_path)
                # Update the app record with new version
                old_version = app.version
                app.version = meta.version
                app.updated_at = int(time.time())
                stat = result.output_path.stat()
                app.device = stat.st_dev
                app.inode = stat.st_ino
                app.size = stat.st_size
                app.mtime_ns = stat.st_mtime_ns
                app.fingerprint = meta.fingerprint
                app.update_info = meta.update_info
                self.db.update_app(app)

                # Move updated file to final location (atomic rename)
                result.output_path.rename(app_path)

                # Update desktop entry
                write_desktop_entry(
                    app_id=app.app_id,
                    display_name=meta.name,
                    exec_path=app_path,
                    icon_name=f"appimage-{app.app_id}",
                    categories=meta.categories,
                    comment=meta.comment,
                    startup_wm_class=None,
                    version=meta.version,
                    name_origin=app.name_origin,
                    update_policy=app.update_policy,
                )

                # Install new icon if changed
                if meta.icon_bytes and meta.icon_ext:
                    install_icons(app.app_id, meta.icon_bytes, meta.icon_ext)

                refresh_caches()
                self.db.insert_event(
                    "updated", app.app_id, f"Updated {old_version} -> {meta.version}"
                )

                logger.info(
                    "Updated app",
                    app_id=app.app_id,
                    old_version=old_version,
                    new_version=meta.version,
                )
            except Exception as e:
                logger.error("Failed to finalize update", error=str(e))
                # Clean up staging file
                try:
                    result.output_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return UpdateApplyResult(
                    success=False,
                    output_path=None,
                    error=f"Failed to finalize update: {e}",
                )

        return result

    def export_data(self) -> dict:
        """Export policies, names and flags as JSON."""
        apps = self.db.list_apps(include_kept=True)
        data = {
            "apps": [
                {
                    "app_id": a.app_id,
                    "managed_name": a.managed_name,
                    "name_origin": a.name_origin,
                    "update_policy": a.update_policy,
                    "origin": a.origin,
                }
                for a in apps
            ],
        }
        return data

    def import_data(self, data: dict) -> int:
        """Import policies, names and flags from JSON."""
        imported = 0
        for app_data in data.get("apps", []):
            app = self._find_app_by_name(app_data.get("managed_name", ""))
            if not app:
                # Try by app_id
                apps = self.db.list_apps(include_kept=True)
                for a in apps:
                    if a.app_id == app_data.get("app_id"):
                        app = a
                        break

            if not app:
                continue

            # Update name if provided and different
            if app_data.get("managed_name") and app_data["managed_name"] != app.managed_name:
                try:
                    self.rename(app.managed_name, app_data["managed_name"])
                except ValueError:
                    pass  # Ignore rename conflicts

            # Update policy if provided
            if app_data.get("update_policy") and app_data["update_policy"] != app.update_policy:
                try:
                    self.set_policy(app.managed_name, app_data["update_policy"])
                except ValueError:
                    pass

            # Update origin if provided
            if app_data.get("origin") is not None and app_data["origin"] != app.origin:
                try:
                    self.set_origin(app.managed_name, app_data["origin"])
                except ValueError:
                    pass

            imported += 1

        return imported

    def dedupe(self, other_dirs: list[Path] | None = None) -> dict:
        """Find identical files by hash across Applications dir and other directories."""
        from appimage_launcher.core.identity import compute_full_sha256

        # Get all installed apps with their hashes
        apps = self.db.list_apps(include_kept=True)
        app_hashes = {}
        for app in apps:
            if app.sha256:
                app_hashes[app.sha256] = app
            else:
                # Compute hash if not cached
                path = Path(app.path)
                if path.exists():
                    sha256 = compute_full_sha256(path)
                    app.sha256 = sha256
                    self.db.update_app(app)
                    app_hashes[sha256] = app

        # Scan other directories
        if other_dirs is None:
            other_dirs = [Path.home() / "Downloads"]

        duplicates = []
        for other_dir in other_dirs:
            if not other_dir.exists():
                continue
            for file_path in other_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in (".appimage", ""):
                    try:
                        # Quick check: must be executable or have AppImage magic
                        if not os.access(file_path, os.X_OK):
                            continue
                        sha256 = compute_full_sha256(file_path)
                        if sha256 in app_hashes:
                            duplicates.append({
                                "installed_app": app_hashes[sha256].managed_name,
                                "installed_path": app_hashes[sha256].path,
                                "duplicate_path": str(file_path),
                                "size": file_path.stat().st_size,
                            })
                    except Exception:
                        pass

        return {"duplicates": duplicates}

    def cleanup(self, days: int = 30) -> dict:
        """Remove .previous older than N days, orphaned icons/entries."""
        now = time.time()
        cutoff = now - (days * 86400)

        removed_previous = 0
        removed_size = 0

        # Clean .previous directory
        if self.paths.previous_dir.exists():
            for file_path in self.paths.previous_dir.iterdir():
                if file_path.is_file():
                    try:
                        mtime = file_path.stat().st_mtime
                        if mtime < cutoff:
                            size = file_path.stat().st_size
                            file_path.unlink()
                            removed_previous += 1
                            removed_size += size
                    except Exception:
                        pass

        # Clean orphaned desktop entries
        removed_entries = 0
        apps_dir = Path.home() / ".local" / "share" / "applications"
        if apps_dir.exists():
            for entry in apps_dir.glob("appimage-*.desktop"):
                try:
                    # Check if it references an existing app
                    content = entry.read_text()
                    app_id = None
                    for line in content.split("\n"):
                        if line.startswith("X-AppImage-Id="):
                            app_id = line.split("=", 1)[1]
                            break
                    if app_id:
                        # Check if app exists in database
                        apps = self.db.list_apps(include_kept=True)
                        found = any(a.app_id == app_id for a in apps)
                        if not found:
                            entry.unlink()
                            removed_entries += 1
                except Exception:
                    pass

        # Clean orphaned icons
        removed_icons = 0
        icons_dir = Path.home() / ".local" / "share" / "icons" / "hicolor"
        if icons_dir.exists():
            for icon in icons_dir.rglob("appimage-*"):
                try:
                    # Extract app_id from icon name
                    name = icon.stem
                    if name.startswith("appimage-"):
                        app_id = name[9:]  # Remove "appimage-"
                        apps = self.db.list_apps(include_kept=True)
                        found = any(a.app_id == app_id for a in apps)
                        if not found:
                            icon.unlink()
                            removed_icons += 1
                except Exception:
                    pass

        return {
            "removed_previous": removed_previous,
            "removed_previous_size": removed_size,
            "removed_entries": removed_entries,
            "removed_icons": removed_icons,
        }


class ConflictError(Exception):
    """Raised when conflict policy is 'error'."""

    def __init__(self, conflict_info: ConflictInfo):
        self.conflict_info = conflict_info
        super().__init__("Conflict requires decision")
