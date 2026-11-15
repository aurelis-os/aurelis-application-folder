"""Conflict handling for AppImage Launcher.

Implements the conflict flow from section 6.6 of the spec.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appimage_launcher.core.manager import ConflictInfo, Manager
from appimage_launcher.core.state import AppRecord
from appimage_launcher.core.versions import VersionRelation
from appimage_launcher.daemon.notifier import Notifier
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ConflictResolution:
    """Result of a conflict resolution."""

    decision: str  # "replace", "keep", "cancel"
    remember: bool
    new_name: str | None = None  # For "keep" decision


class ConflictHandler:
    """Handles conflicts between existing and new AppImages."""

    def __init__(
        self,
        manager: Manager,
        notifier: Notifier,
        config,
        loop: asyncio.AbstractEventLoop,
    ):
        self.manager = manager
        self.notifier = notifier
        self.config = config
        self.loop = loop

        # Pending conflicts waiting for user decision
        self._pending_conflicts: dict[str, dict] = {}

        # Conflict resolution callbacks (for GUI dialogs)
        self._resolution_callback: Callable[[str, ConflictResolution], None] | None = None

    def set_resolution_callback(self, callback: Callable[[str, ConflictResolution], None]) -> None:
        """Set callback for conflict resolution (used by GUI)."""
        self._resolution_callback = callback

    def handle_conflict(
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
    ) -> Any:
        """Handle a conflict according to policy.

        Returns InstallResult or raises ConflictError for 'error' policy.
        """
        from appimage_launcher.core.manager import ConflictError, InstallResult
        from appimage_launcher.core.versions import compare_versions

        relation = compare_versions(old_app.version, new_meta.version)

        # Check for identical file
        from appimage_launcher.core.identity import compute_full_sha256

        new_sha256 = compute_full_sha256(source_path)
        if old_app.sha256 and old_app.sha256 == new_sha256:
            logger.info("Identical file already installed", app_id=old_app.app_id)
            # Return existing app
            return InstallResult(app=old_app, changed=False, previous=None)

        # Determine effective policy
        if conflict_policy == "config":
            conflict_policy = self.config.install.conflict_policy

        if is_self_update:
            # Self-updates use update.on_self_update policy
            conflict_policy = self.config.update.on_self_update

        logger.info(
            "Handling conflict",
            app_id=old_app.app_id,
            old_version=old_app.version,
            new_version=new_meta.version,
            relation=relation.name,
            policy=conflict_policy,
            is_self_update=is_self_update,
        )

        if conflict_policy == "replace":
            return self._do_replace(
                old_app, new_meta, source_path, source_action, origin, silent, update_policy
            )
        elif conflict_policy == "keep":
            return self._do_keep_both(
                old_app, new_meta, source_path, source_action, origin, silent, update_policy
            )
        elif conflict_policy == "cancel":
            raise ConflictError(ConflictInfo(old_app, new_meta, relation, is_self_update))
        elif conflict_policy == "ask":
            return self._do_ask(
                old_app,
                new_meta,
                source_path,
                source_action,
                origin,
                silent,
                update_policy,
                relation,
                is_self_update,
                name_origin,
            )
        elif conflict_policy == "error":
            raise ConflictError(ConflictInfo(old_app, new_meta, relation, is_self_update))
        else:
            raise ValueError(f"Unknown conflict policy: {conflict_policy}")

    def _do_replace(
        self,
        old_app: AppRecord,
        new_meta: Any,
        source_path: Path,
        source_action: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
    ) -> Any:
        """Replace the existing app with the new version."""
        logger.info("Replacing app", app_id=old_app.app_id)

        # Use the manager's replace logic (keeps user-chosen name)
        result = self.manager.install(
            source_path,
            source_action=source_action,
            conflict_policy="replace",
            origin=origin or old_app.origin,
            silent=silent,
            update_policy=update_policy,
        )

        if not silent:
            self.notifier.notify_updated(result.app, old_app.version, result.app.version)

        return result

    def _do_keep_both(
        self,
        old_app: AppRecord,
        new_meta: Any,
        source_path: Path,
        source_action: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
    ) -> Any:
        """Keep both versions - rename old to kept, install new as primary."""
        logger.info("Keeping both versions", app_id=old_app.app_id)

        result = self.manager.install(
            source_path,
            source_action=source_action,
            conflict_policy="keep",
            origin=origin,
            silent=silent,
            update_policy=update_policy,
        )

        if not silent:
            self.notifier.notify_installed(result.app, result.app.version)

        return result

    def _do_ask(
        self,
        old_app: AppRecord,
        new_meta: Any,
        source_path: Path,
        source_action: str,
        origin: str | None,
        silent: bool,
        update_policy: str,
        relation: VersionRelation,
        is_self_update: bool,
        name_origin: str,
    ) -> Any:
        """Ask the user what to do via notification or dialog."""
        logger.info("Asking user about conflict", app_id=old_app.app_id)

        # Create conflict ID
        conflict_id = f"conflict_{int(time.time() * 1000)}"

        # Store conflict info for resolution
        self._pending_conflicts[conflict_id] = {
            "old_app": old_app,
            "new_meta": new_meta,
            "source_path": source_path,
            "source_action": source_action,
            "origin": origin,
            "silent": silent,
            "update_policy": update_policy,
            "name_origin": name_origin,
            "relation": relation,
            "is_self_update": is_self_update,
            "created_at": time.time(),
        }

        # Send notification with actions
        if not is_self_update:
            self.notifier.notify_conflict(
                old_app,
                new_meta.name,
                new_meta.version,
                relation.name.lower(),
                conflict_id,
            )

        # For self-updates with 'ask' policy, we could also show a dialog
        # For now, we'll use the notification path

        # Return a pending result - the actual install happens when resolved
        # This is a special case - we need to wait for user decision
        raise ConflictPending(conflict_id, old_app, new_meta, relation, is_self_update)

    def resolve_conflict(
        self,
        conflict_id: str,
        decision: str,
        remember: bool = False,
        new_name: str | None = None,
    ) -> Any:
        """Resolve a pending conflict."""
        if conflict_id not in self._pending_conflicts:
            raise ValueError(f"Unknown conflict: {conflict_id}")

        conflict = self._pending_conflicts.pop(conflict_id)

        old_app = conflict["old_app"]
        new_meta = conflict["new_meta"]
        source_path = conflict["source_path"]
        source_action = conflict["source_action"]
        origin = conflict["origin"]
        silent = conflict["silent"]
        update_policy = conflict["update_policy"]
        conflict["name_origin"]

        if decision == "cancel":
            # Leave the file where it is
            logger.info("Conflict cancelled", conflict_id=conflict_id)
            return None

        elif decision == "replace":
            return self._do_replace(
                old_app, new_meta, source_path, source_action, origin, silent, update_policy
            )

        elif decision == "keep":
            # If user provided a custom name for the kept version, use it
            if new_name:
                # We'd need to modify the keep logic to use the custom name
                pass
            return self._do_keep_both(
                old_app, new_meta, source_path, source_action, origin, silent, update_policy
            )

        else:
            raise ValueError(f"Invalid decision: {decision}")

    def get_pending_conflicts(self) -> list[dict]:
        """Get all pending conflicts."""
        result = []
        for conflict_id, conflict in self._pending_conflicts.items():
            result.append(
                {
                    "id": conflict_id,
                    "old_app": {
                        "app_id": conflict["old_app"].app_id,
                        "name": conflict["old_app"].display_name,
                        "version": conflict["old_app"].version,
                        "managed_name": conflict["old_app"].managed_name,
                    },
                    "new_meta": {
                        "name": conflict["new_meta"].name,
                        "version": conflict["new_meta"].version,
                        "app_id": conflict["new_meta"].app_id,
                        "size": conflict["new_meta"].size,
                    },
                    "relation": conflict["relation"].name.lower(),
                    "is_self_update": conflict["is_self_update"],
                }
            )
        return result


class ConflictPending(Exception):
    """Raised when a conflict needs user decision (policy=ask or error)."""

    def __init__(
        self,
        conflict_id: str,
        old_app: AppRecord,
        new_meta: Any,
        relation: VersionRelation,
        is_self_update: bool,
    ):
        self.conflict_id = conflict_id
        self.old_app = old_app
        self.new_meta = new_meta
        self.relation = relation
        self.is_self_update = is_self_update
        super().__init__(f"Conflict pending: {conflict_id}")


def format_conflict_payload(conflict: ConflictPending) -> dict:
    """Format conflict info for D-Bus signal."""
    return {
        "id": conflict.conflict_id,
        "old_app": {
            "app_id": conflict.old_app.app_id,
            "name": conflict.old_app.display_name,
            "version": conflict.old_app.version,
            "managed_name": conflict.old_app.managed_name,
            "path": conflict.old_app.path,
        },
        "new_meta": {
            "name": conflict.new_meta.name,
            "version": conflict.new_meta.version,
            "app_id": conflict.new_meta.app_id,
            "size": conflict.new_meta.size,
        },
        "relation": conflict.relation.name.lower(),
        "is_self_update": conflict.is_self_update,
    }
