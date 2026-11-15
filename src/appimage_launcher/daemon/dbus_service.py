"""D-Bus service implementation for AppImage Launcher.

Exposes the Manager1 interface on the session bus.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

from appimage_launcher.constants import (
    API_VERSION,
    CAPABILITIES,
    DBUS_INTERFACE_NAME,
    DBUS_OBJECT_PATH,
    DBUS_SERVICE_NAME,
)
from appimage_launcher.core.manager import Manager
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class ManagerInterface(ServiceInterface):
    """D-Bus interface for the Manager1 API."""

    def __init__(
        self,
        manager: Manager,
        notifier,
        paths,
        config,
        loop: asyncio.AbstractEventLoop,
    ):
        super().__init__(DBUS_INTERFACE_NAME)
        self.manager = manager
        self.notifier = notifier
        self.paths = paths
        self.config = config
        self.loop = loop

        # Lock for serializing mutations
        self._mutation_lock = asyncio.Lock()
        self._lock_timeout = 10.0

        # Error names (matching the spec)
        self.ERROR_NOT_FOUND = "io.github.appimage_launcher.Error.NotFound"
        self.ERROR_AMBIGUOUS = "io.github.appimage_launcher.Error.Ambiguous"
        self.ERROR_NAME_COLLISION = "io.github.appimage_launcher.Error.NameCollision"
        self.ERROR_CONFLICT_PENDING = "io.github.appimage_launcher.Error.ConflictPending"
        self.ERROR_INVALID_APPIMAGE = "io.github.appimage_launcher.Error.InvalidAppImage"
        self.ERROR_UNSUPPORTED_TYPE = "io.github.appimage_launcher.Error.UnsupportedType"
        self.ERROR_INVALID_ARGUMENT = "io.github.appimage_launcher.Error.InvalidArgument"
        self.ERROR_POLICY_FORBIDS = "io.github.appimage_launcher.Error.PolicyForbids"
        self.ERROR_BUSY = "io.github.appimage_launcher.Error.Busy"
        self.ERROR_IO = "io.github.appimage_launcher.Error.Io"
        self.ERROR_ABORTED = "io.github.appimage_launcher.Error.Aborted"

        # Pending conflicts for ResolveConflict
        self._pending_conflicts: dict[str, dict] = {}

    # Properties

    @dbus_property(access=PropertyAccess.READ)
    def ApplicationsDir(self) -> s:
        return str(self.paths.applications_dir)

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> s:
        from appimage_launcher import __version__

        return __version__

    @dbus_property(access=PropertyAccess.READ)
    def APIVersion(self) -> u:
        return API_VERSION

    @dbus_property(access=PropertyAccess.READ)
    def Capabilities(self) -> "as":
        return CAPABILITIES

    # Methods

    @method()
    def Inspect(self, path: s) -> s:
        """Read metadata from an AppImage without installing it."""
        from appimage_launcher.core.identity import compute_full_sha256
        from appimage_launcher.core.metadata import read_appimage_meta
        from appimage_launcher.core.versions import VersionRelation, compare_versions

        source_path = Path(path).resolve()
        if not source_path.exists():
            raise Exception(f"File not found: {path}")

        meta = read_appimage_meta(source_path)

        # Check if already installed
        sha256 = compute_full_sha256(source_path)
        installed_app = self.manager.db.get_app_by_sha256(sha256)
        relation = VersionRelation.UNKNOWN

        if not installed_app:
            # Try by app_id
            installed_app = self.manager.db.get_primary_by_app_id(meta.app_id)
            if installed_app:
                relation = compare_versions(installed_app.version, meta.version)

        result = {
            "type": meta.type,
            "name": meta.name,
            "version": meta.version,
            "app_id": meta.app_id,
            "comment": meta.comment,
            "categories": meta.categories,
            "icon_png_base64": None,  # TODO: encode icon
            "icon_ext": meta.icon_ext,
            "update_info": meta.update_info,
            "has_signature": meta.has_signature,
            "size": meta.size,
            "installed": self._app_to_dict(installed_app) if installed_app else None,
            "relation": relation.name.lower(),
        }

        return json.dumps(result)

    @method()
    async def Install(self, path: s, options: "a{sv}") -> s:
        """Install an AppImage with options."""
        # Serialize mutations
        async with self._mutation_lock:
            return await self._do_install(path, options)

    async def _do_install(self, path: str, options: dict) -> str:
        """Perform the install operation."""
        source_path = Path(path).resolve()
        if not source_path.exists():
            raise Exception(f"{self.ERROR_NOT_FOUND}: File not found: {path}")

        # Helper to unwrap Variant values
        def unwrap(v):
            if hasattr(v, "value"):
                return v.value
            return v

        # Parse options (unwrap Variant values from D-Bus)
        name = unwrap(options.get("name"))
        source_action = unwrap(options.get("source_action", self.config.install.source_action))
        conflict = unwrap(options.get("conflict", "config"))
        origin = unwrap(options.get("origin"))
        silent = unwrap(options.get("silent", False))
        update_policy = unwrap(options.get("update_policy", "auto"))

        # Validate conflict option
        if conflict not in ("config", "replace", "keep", "cancel", "error"):
            raise Exception(f"{self.ERROR_INVALID_ARGUMENT}: Invalid conflict option: {conflict}")

        # Resolve "config" conflict policy
        if conflict == "config":
            conflict = self.config.install.conflict_policy
            # "ask" requires user interaction - map to "error" for API callers
            # so they can handle it via ConflictPending signal
            if conflict == "ask":
                conflict = "error"

        # Validate origin if provided
        if origin:
            try:
                origin_data = json.loads(origin)
                if "source" not in origin_data or "catalog_id" not in origin_data:
                    raise Exception(
                        f"{self.ERROR_INVALID_ARGUMENT}: Origin must have 'source' and 'catalog_id'"
                    )
                if len(origin) > 4096:
                    raise Exception(
                        f"{self.ERROR_INVALID_ARGUMENT}: Origin JSON too large (max 4 KiB)"
                    )
            except json.JSONDecodeError:
                raise Exception(f"{self.ERROR_INVALID_ARGUMENT}: Invalid origin JSON")

        try:
            result = self.manager.install(
                source_path,
                name=name,
                source_action=source_action,
                conflict_policy=conflict,
                origin=origin,
                silent=silent,
                update_policy=update_policy,
            )

            # Emit signal
            self.AppsChanged("installed", result.app.app_id, "api")

            return json.dumps(
                {
                    "app": self._app_to_dict(result.app),
                    "changed": result.changed,
                    "previous": result.previous,
                }
            )

        except Exception as e:
            # Check if it's a conflict error
            from appimage_launcher.core.manager import ConflictError

            if isinstance(e, ConflictError):
                conflict_id = f"conflict_{int(time.time() * 1000)}"
                self._pending_conflicts[conflict_id] = {
                    "old_app": e.conflict_info.old_app,
                    "new_meta": e.conflict_info.new_meta,
                    "relation": e.conflict_info.relation,
                    "is_self_update": e.conflict_info.is_self_update,
                    "source_path": str(source_path),
                    "options": options,
                }

                # Emit ConflictPending signal
                payload = {
                    "id": conflict_id,
                    "old_app": self._app_to_dict(e.conflict_info.old_app),
                    "new_meta": {
                        "name": e.conflict_info.new_meta.name,
                        "version": e.conflict_info.new_meta.version,
                        "app_id": e.conflict_info.new_meta.app_id,
                        "size": e.conflict_info.new_meta.size,
                    },
                    "relation": e.conflict_info.relation.name.lower(),
                }
                self.ConflictPending(conflict_id, json.dumps(payload))

                raise Exception(f"{self.ERROR_CONFLICT_PENDING}: {conflict_id}") from None

            # Check for other specific errors
            error_msg = str(e)
            if "Name collision" in error_msg:
                raise Exception(f"{self.ERROR_NAME_COLLISION}: {error_msg}") from None
            elif "not found" in error_msg.lower():
                raise Exception(f"{self.ERROR_NOT_FOUND}: {error_msg}") from None
            elif "Invalid AppImage" in error_msg or "Not an AppImage" in error_msg:
                raise Exception(f"{self.ERROR_INVALID_APPIMAGE}: {error_msg}") from None
            elif "Unsupported" in error_msg:
                raise Exception(f"{self.ERROR_UNSUPPORTED_TYPE}: {error_msg}") from None
            elif "lock" in error_msg.lower() or "another operation" in error_msg.lower():
                raise Exception(f"{self.ERROR_BUSY}: {error_msg}") from None

            raise

    @method()
    async def Remove(self, name: s, options: "a{sv}") -> "":
        """Remove an app."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            file_action = options.get("file_action", "trash")
            confirm = options.get("confirm", False)
            silent = options.get("silent", False)

            if file_action == "purge" and not confirm:
                raise Exception(f"{self.ERROR_INVALID_ARGUMENT}: Purge requires confirm=true")

            app = self.manager._find_app_by_name(name)
            if not app:
                raise Exception(f"{self.ERROR_NOT_FOUND}: App not found: {name}")

            app_id = app.app_id
            self.manager.remove(
                name,
                file_action=file_action,
                keep_file=(file_action == "keep"),
                purge=(file_action == "purge"),
            )

            self.AppsChanged("removed", app_id, "api")

            if not silent:
                self.notifier.notify_removed(app)
        finally:
            self._mutation_lock.release()

    @method()
    async def Rename(self, name: s, new_name: s) -> s:
        """Rename an app."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            app = self.manager.rename(name, new_name)
            self.AppsChanged("renamed", app.app_id, "api")
            return json.dumps(self._app_to_dict(app))
        except ValueError as e:
            raise Exception(f"{self.ERROR_NOT_FOUND}: {e}") from None
        finally:
            self._mutation_lock.release()

    @method()
    def List(self, options: "a{sv}") -> s:
        """List installed apps."""
        all_apps = options.get("all", False)
        compute_hash = options.get("hash", False)

        apps = self.manager.db.list_apps(include_kept=all_apps)
        result = [self._app_to_dict(app, include_hash=compute_hash) for app in apps]
        return json.dumps(result)

    @method()
    def Info(self, name: s) -> s:
        """Get detailed info about an app."""
        app = self.manager._find_app_by_name(name)
        if not app:
            raise Exception(f"{self.ERROR_NOT_FOUND}: App not found: {name}")

        # Compute hash if not cached
        if not app.sha256:
            from appimage_launcher.core.identity import compute_full_sha256

            file_path = Path(app.path)
            if file_path.exists():
                app.sha256 = compute_full_sha256(file_path)
                self.manager.db.update_app(app)

        return json.dumps(self._app_to_dict(app, include_hash=True))

    @method()
    async def SetPolicy(self, name: s, policy: s) -> "":
        """Set update policy for an app."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            if policy not in ("auto", "manual", "pinned"):
                raise Exception(f"{self.ERROR_INVALID_ARGUMENT}: Invalid policy: {policy}")

            app = self.manager.set_policy(name, policy)
            self.AppsChanged("policy_changed", app.app_id, "api")
        except ValueError as e:
            raise Exception(f"{self.ERROR_NOT_FOUND}: {e}") from None
        finally:
            self._mutation_lock.release()

    @method()
    async def CheckUpdates(self, names: "as") -> s:
        """Check for updates for specified apps."""
        # This would integrate with the update checker
        # For now return empty results
        results = []
        for name in names:
            app = self.manager._find_app_by_name(name)
            if not app:
                results.append(
                    {
                        "app_id": "",
                        "has_update": False,
                        "current_version": None,
                        "latest_version": None,
                        "skipped": True,
                        "skip_reason": "not_found",
                    }
                )
                continue

            # Check update-authority rule (6.9): skip catalog-linked apps
            if app.origin and self.config.update.skip_catalog_linked:
                results.append(
                    {
                        "app_id": app.app_id,
                        "has_update": False,
                        "current_version": app.version,
                        "latest_version": None,
                        "skipped": True,
                        "skip_reason": "catalog_linked",
                    }
                )
                continue

            results.append(
                {
                    "app_id": app.app_id,
                    "has_update": False,
                    "current_version": app.version,
                    "latest_version": None,
                    "skipped": False,
                }
            )
        return json.dumps(results)

    @method()
    async def Update(self, name: s) -> "":
        """Update an app (trigger zsync update)."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            app = self.manager._find_app_by_name(name)
            if not app:
                raise Exception(f"{self.ERROR_NOT_FOUND}: App not found: {name}")

            # Check update-authority rule (6.9): skip catalog-linked apps
            if app.origin and self.config.update.skip_catalog_linked:
                raise Exception(
                    f"{self.ERROR_POLICY_FORBIDS}: App is catalog-linked; updates managed by front-end"
                )

            # Emit progress signal
            self.UpdateProgress(name, 0, "checking")

            # Run update in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.manager.apply_update, name)

            if result.success:
                self.UpdateProgress(name, 100, "done")
                self.AppsChanged("updated", app.app_id, "api")
            else:
                self.UpdateProgress(name, 0, "error")
                raise Exception(f"{self.ERROR_IO}: {result.error}")

        finally:
            self._mutation_lock.release()

    @method()
    async def Revert(self, name: s) -> "":
        """Revert to previous version."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            app = self.manager.revert(name)
            self.AppsChanged("reverted", app.app_id, "api")
        except ValueError as e:
            raise Exception(f"{self.ERROR_NOT_FOUND}: {e}") from None
        finally:
            self._mutation_lock.release()

    @method()
    async def ResolveConflict(self, conflict_id: s, decision: s, remember: b) -> "":
        """Resolve a pending conflict."""
        if conflict_id not in self._pending_conflicts:
            raise Exception(f"{self.ERROR_NOT_FOUND}: Unknown conflict: {conflict_id}")

        if decision not in ("replace", "keep", "cancel"):
            raise Exception(f"{self.ERROR_INVALID_ARGUMENT}: Invalid decision: {decision}")

        conflict = self._pending_conflicts.pop(conflict_id)
        conflict["old_app"]
        conflict["new_meta"]
        source_path = Path(conflict["source_path"])
        options = conflict["options"]

        if decision == "cancel":
            # Leave the file where it is
            return

        # Re-run install with explicit decision
        options["conflict"] = decision
        result = self.manager.install(
            source_path,
            name=options.get("name"),
            source_action=options.get("source_action", "copy"),
            conflict_policy=decision,
            origin=options.get("origin"),
            silent=options.get("silent", False),
            update_policy=options.get("update_policy", "auto"),
        )

        self.AppsChanged("installed", result.app.app_id, "api")

    @method()
    async def Reindex(self) -> "":
        """Trigger a full reindex/reconcile."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            from appimage_launcher.daemon.reconcile import reconcile

            reconcile(self.manager, self.notifier, self.paths, self.config)
            self.AppsChanged("reindexed", "", "api")
        finally:
            self._mutation_lock.release()

    @method()
    async def SetOrigin(self, name: s, origin_json: s) -> s:
        """Set or clear origin record."""
        # Acquire lock with timeout
        try:
            await asyncio.wait_for(self._mutation_lock.acquire(), timeout=self._lock_timeout)
        except TimeoutError:
            raise Exception(
                f"{self.ERROR_BUSY}: Could not acquire lock within {self._lock_timeout}s"
            )

        try:
            # Validate origin if provided
            if origin_json:
                try:
                    origin_data = json.loads(origin_json)
                    if "source" not in origin_data or "catalog_id" not in origin_data:
                        raise Exception(
                            f"{self.ERROR_INVALID_ARGUMENT}: Origin must have 'source' and 'catalog_id'"
                        )
                    if len(origin_json) > 4096:
                        raise Exception(
                            f"{self.ERROR_INVALID_ARGUMENT}: Origin JSON too large (max 4 KiB)"
                        )
                except json.JSONDecodeError:
                    raise Exception(f"{self.ERROR_INVALID_ARGUMENT}: Invalid origin JSON")

            app = self.manager.set_origin(name, origin_json if origin_json else None)
            self.AppsChanged("origin_changed", app.app_id, "api")
            return json.dumps(self._app_to_dict(app))
        except ValueError as e:
            raise Exception(f"{self.ERROR_NOT_FOUND}: {e}") from None
        finally:
            self._mutation_lock.release()

    # Signals

    @signal()
    def AppsChanged(self, kind: str, app_id: str, source: str) -> None:
        """Emitted when apps change."""
        pass

    @signal()
    def ConflictPending(self, conflict_id: str, json_payload: str) -> None:
        """Emitted when a conflict needs resolution."""
        pass

    @signal()
    def UpdateProgress(self, name: str, percent: int, state: str) -> None:
        """Emitted during update progress."""
        pass

    # Helper methods

    def _app_to_dict(self, app, include_hash: bool = False) -> dict:
        """Convert AppRecord to dictionary."""
        if app is None:
            return None

        result = {
            "app_id": app.app_id,
            "name": app.display_name,
            "managed_name": app.managed_name,
            "name_origin": app.name_origin,
            "role": app.role,
            "version": app.version,
            "path": app.path,
            "size": app.size,
            "sha256": app.sha256 if include_hash else None,
            "update_policy": app.update_policy,
            "self_managed": app.self_managed,
            "has_update_info": bool(app.update_info),
            "origin": json.loads(app.origin) if app.origin else None,
            "desktop_entry": f"{self.paths.data_dir}/applications/appimage-{app.app_id}.desktop",
            "installed_at": app.installed_at,
            "updated_at": app.updated_at,
            "running": False,  # Would need running tracker
            "previous": [],
        }

        # Add previous versions
        backups = self.manager.db.get_backups(app.app_id)
        result["previous"] = [
            {"version": b.version, "size": b.size, "created_at": b.created_at} for b in backups
        ]

        return result


class DBusService:
    """Manages the D-Bus service lifecycle."""

    def __init__(
        self,
        manager: Manager,
        notifier,
        paths,
        config,
        loop: asyncio.AbstractEventLoop,
    ):
        self.manager = manager
        self.notifier = notifier
        self.paths = paths
        self.config = config
        self.loop = loop

        self._bus: MessageBus | None = None
        self._interface: ManagerInterface | None = None
        self._service_name = DBUS_SERVICE_NAME
        self._object_path = DBUS_OBJECT_PATH

    async def start(self) -> None:
        """Connect to the session bus and register the service."""
        self._bus = await MessageBus(bus_type=BusType.SESSION).connect()

        # Request the service name
        await self._bus.request_name(self._service_name)

        # Create and export the interface
        self._interface = ManagerInterface(
            self.manager, self.notifier, self.paths, self.config, self.loop
        )
        self._bus.export(self._object_path, self._interface)

        logger.info("D-Bus service started", service=self._service_name, path=self._object_path)

    async def stop(self) -> None:
        """Stop the D-Bus service."""
        if self._bus:
            # Unexport the interface
            if self._interface:
                self._bus.unexport(self._object_path, self._interface)
                self._interface = None

            # Release the service name
            await self._bus.release_name(self._service_name)

            # Disconnect
            self._bus.disconnect()
            self._bus = None

            logger.info("D-Bus service stopped")

    def emit_apps_changed(self, kind: str, app_id: str, source: str) -> None:
        """Emit AppsChanged signal."""
        if self._interface:
            self._interface.AppsChanged(kind, app_id, source)

    def emit_conflict_pending(self, conflict_id: str, payload: str) -> None:
        """Emit ConflictPending signal."""
        if self._interface:
            self._interface.ConflictPending(conflict_id, payload)

    def emit_update_progress(self, name: str, percent: int, state: str) -> None:
        """Emit UpdateProgress signal."""
        if self._interface:
            self._interface.UpdateProgress(name, percent, state)
