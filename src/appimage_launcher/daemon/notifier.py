"""Notification system for AppImage Launcher.

Handles desktop notifications via D-Bus and provides a fake server for testing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from appimage_launcher.constants import (
    NOTIFICATION_APP_NAME,
    NOTIFICATION_DESKTOP_ENTRY,
    NOTIFICATION_THROTTLE_SECONDS,
)
from appimage_launcher.core.manager import Manager
from appimage_launcher.core.state import AppRecord
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Notification:
    """Represents a desktop notification."""

    id: int
    app_id: str
    title: str
    body: str
    actions: list[tuple[str, str]]  # (action_id, label)
    kind: str
    timestamp: float
    replaces_id: int = 0


class Notifier:
    """Sends desktop notifications via D-Bus."""

    def __init__(
        self,
        manager: Manager,
        paths,
        config,
        loop: asyncio.AbstractEventLoop,
    ):
        self.manager = manager
        self.paths = paths
        self.config = config
        self.loop = loop

        self._notification_id = 0
        self._pending_notifications: dict[int, Notification] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._throttle_timestamps: dict[str, float] = {}  # app_id -> last notification time

        # D-Bus connection for notifications
        self._bus = None
        self._notifications_interface = None

        # Register default action handlers
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register default notification action handlers."""
        self._action_handlers = {
            "open": self._action_open,
            "show": self._action_show_in_folder,
            "undo": self._action_undo,
            "details": self._action_details,
            "apply": self._action_apply_update,
            "replace": self._action_replace,
            "keep": self._action_keep_both,
            "cancel": self._action_cancel,
        }

    async def initialize(self) -> None:
        """Initialize D-Bus connection for notifications."""
        try:
            from dbus_fast import BusType
            from dbus_fast.aio import MessageBus

            self._bus = await MessageBus(bus_type=BusType.SESSION).connect()

            # Get the Notifications interface
            introspection = await self._bus.introspect(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
            )

            # We'll call methods directly on the proxy
            self._notifications_proxy = self._bus.get_proxy_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                introspection,
            )
            self._notifications_interface = self._notifications_proxy.get_interface(
                "org.freedesktop.Notifications"
            )

            # Listen for ActionInvoked and NotificationClosed signals
            self._notifications_interface.on_action_invoked(self._on_action_invoked)
            self._notifications_interface.on_notification_closed(self._on_notification_closed)

            logger.info("Notification system initialized")
        except Exception as e:
            logger.warning("Failed to initialize notifications", error=str(e))
            self._bus = None

    def _next_id(self) -> int:
        self._notification_id += 1
        return self._notification_id

    def _should_throttle(self, app_id: str) -> bool:
        """Check if we should throttle notifications for this app."""
        if not app_id:
            return False
        now = time.time()
        last = self._throttle_timestamps.get(app_id, 0)
        if now - last < NOTIFICATION_THROTTLE_SECONDS:
            return True
        self._throttle_timestamps[app_id] = now
        return False

    def _send_notification(
        self,
        title: str,
        body: str,
        actions: list[tuple[str, str]],
        app_id: str,
        kind: str,
        replaces_id: int = 0,
        urgency: int = 1,
    ) -> int:
        """Send a desktop notification."""
        if self._should_throttle(app_id):
            logger.debug("Notification throttled", app_id=app_id, kind=kind)
            return 0

        notification_id = self._next_id()

        # Store for action handling
        notification = Notification(
            id=notification_id,
            app_id=app_id,
            title=title,
            body=body,
            actions=actions,
            kind=kind,
            timestamp=time.time(),
            replaces_id=replaces_id,
        )
        self._pending_notifications[notification_id] = notification

        # Send via D-Bus if available
        if self._notifications_interface:
            try:
                # Format actions for D-Bus: [action_id1, label1, action_id2, label2, ...]
                action_list = []
                for action_id, label in actions:
                    action_list.append(action_id)
                    action_list.append(label)

                hints = {
                    "desktop-entry": NOTIFICATION_DESKTOP_ENTRY,
                    "urgency": urgency,
                    "category": kind,
                }

                self._notifications_interface.Notify(
                    NOTIFICATION_APP_NAME,
                    replaces_id,
                    "",  # icon
                    title,
                    body,
                    action_list,
                    hints,
                    -1,  # timeout (default)
                )

                logger.debug("Notification sent", id=notification_id, title=title, kind=kind)
            except Exception as e:
                logger.error("Failed to send notification", error=str(e))
        else:
            # Fallback: just log
            logger.info("Notification (no D-Bus)", title=title, body=body, actions=actions)

        return notification_id

    def _on_action_invoked(self, notification_id: int, action_id: str) -> None:
        """Handle notification action invocation."""
        notification = self._pending_notifications.get(notification_id)
        if not notification:
            return

        handler = self._action_handlers.get(action_id)
        if handler:
            try:
                handler(notification)
            except Exception as e:
                logger.error("Action handler error", action=action_id, error=str(e))

        # Remove the notification
        self._pending_notifications.pop(notification_id, None)

    def _on_notification_closed(self, notification_id: int, reason: int) -> None:
        """Handle notification closed (dismissed or timeout)."""
        notification = self._pending_notifications.pop(notification_id, None)
        if not notification:
            return

        # Default action on timeout/dismiss is non-destructive (cancel/do nothing)
        logger.debug("Notification closed", id=notification_id, reason=reason)

    # Default action handlers

    def _action_open(self, notification: Notification) -> None:
        """Open the app."""
        if notification.app_id:
            app = self.manager.db.get_primary_by_app_id(notification.app_id)
            if app:
                import subprocess

                subprocess.Popen([app.path], start_new_session=True)

    def _action_show_in_folder(self, notification: Notification) -> None:
        """Show the app file in the file manager."""
        if notification.app_id:
            app = self.manager.db.get_primary_by_app_id(notification.app_id)
            if app:
                import subprocess

                subprocess.Popen(["xdg-open", str(Path(app.path).parent)], start_new_session=True)

    def _action_undo(self, notification: Notification) -> None:
        """Undo the last operation (revert)."""
        if notification.app_id:
            try:
                self.manager.revert(notification.app_id)
            except Exception as e:
                logger.error("Undo failed", error=str(e))

    def _action_details(self, notification: Notification) -> None:
        """Show details dialog."""
        # This would launch the GUI dialog
        logger.info("Details action", app_id=notification.app_id)

    def _action_apply_update(self, notification: Notification) -> None:
        """Apply a waiting update."""
        if notification.app_id:
            # This would trigger the update
            logger.info("Apply update action", app_id=notification.app_id)

    def _action_replace(self, notification: Notification) -> None:
        """Replace in conflict dialog."""
        logger.info("Replace action", app_id=notification.app_id)

    def _action_keep_both(self, notification: Notification) -> None:
        """Keep both in conflict dialog."""
        logger.info("Keep both action", app_id=notification.app_id)

    def _action_cancel(self, notification: Notification) -> None:
        """Cancel in conflict dialog."""
        logger.info("Cancel action", app_id=notification.app_id)

    # Public notification methods

    def notify_installed(self, app: AppRecord, version: str) -> None:
        """Notify that an app was installed."""
        self._send_notification(
            title=f"Installed {app.display_name} {version}",
            body=f"{app.display_name} {version} has been installed.",
            actions=[("open", "Open"), ("show", "Show in folder")],
            app_id=app.app_id,
            kind="installed",
        )

    def notify_updated(self, app: AppRecord, old_version: str, new_version: str) -> None:
        """Notify that an app was updated."""
        self._send_notification(
            title=f"{app.display_name} updated",
            body=f"{app.display_name} updated from {old_version} to {new_version}.",
            actions=[("undo", "Undo")],
            app_id=app.app_id,
            kind="updated",
        )

    def notify_self_updated(self, app: AppRecord, old_version: str, new_version: str) -> None:
        """Notify that an app self-updated."""
        self._send_notification(
            title=f"{app.display_name} updated",
            body=f"{app.display_name} updated from {old_version} to {new_version}.",
            actions=[("undo", "Undo")],
            app_id=app.app_id,
            kind="self_updated",
        )

    def notify_removed(self, app: AppRecord) -> None:
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
            urgency=2,
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

    def notify_conflict(
        self,
        old_app: AppRecord,
        new_name: str,
        new_version: str,
        relation: str,
        conflict_id: str,
    ) -> None:
        """Notify about a conflict (user drop)."""
        self._send_notification(
            title=f"{old_app.display_name} is already installed ({old_app.version})",
            body=f"Replace with {new_name} {new_version}? ({relation})",
            actions=[
                ("replace", "Replace"),
                ("keep", "Keep both"),
                ("cancel", "Cancel"),
                ("details", "Details…"),
            ],
            app_id=old_app.app_id,
            kind="conflict",
            urgency=2,
        )

    def notify_unsupported(self, name: str) -> None:
        """Notify about unsupported AppImage type."""
        self._send_notification(
            title=f"{name} is not supported",
            body=f"{name} is an AppImage type 1 and is not supported.",
            actions=[],
            app_id="",
            kind="unsupported",
        )

    def close_notification(self, notification_id: int) -> None:
        """Close a notification by ID."""
        if self._notifications_interface and notification_id in self._pending_notifications:
            try:
                self._notifications_interface.CloseNotification(notification_id)
            except Exception:
                pass
        self._pending_notifications.pop(notification_id, None)

    def replace_notification(self, old_id: int, **kwargs) -> int:
        """Replace an existing notification."""
        self.close_notification(old_id)
        return self._send_notification(replaces_id=old_id, **kwargs)


class FakeNotificationServer:
    """Fake notification server for testing.

    Records notifications and allows tests to simulate user actions.
    """

    def __init__(self):
        self.notifications: list[Notification] = []
        self.action_invocations: list[tuple[int, str]] = []
        self.closed_notifications: list[tuple[int, int]] = []  # (id, reason)
        self._next_id = 0

    def notify(
        self,
        app_name: str,
        replaces_id: int,
        icon: str,
        title: str,
        body: str,
        actions: list,
        hints: dict,
        timeout: int,
    ) -> int:
        """Mock Notify method."""
        self._next_id += 1
        notification_id = self._next_id

        # Parse actions
        action_list = []
        for i in range(0, len(actions), 2):
            if i + 1 < len(actions):
                action_list.append((actions[i], actions[i + 1]))

        notification = Notification(
            id=notification_id,
            app_id=hints.get("app-id", ""),
            title=title,
            body=body,
            actions=action_list,
            kind=hints.get("category", ""),
            timestamp=time.time(),
            replaces_id=replaces_id,
        )
        self.notifications.append(notification)
        return notification_id

    def close_notification(self, notification_id: int) -> None:
        """Mock CloseNotification method."""
        for i, n in enumerate(self.notifications):
            if n.id == notification_id:
                self.notifications.pop(i)
                break

    def invoke_action(self, notification_id: int, action_id: str) -> None:
        """Simulate user clicking an action."""
        self.action_invocations.append((notification_id, action_id))

    def close_notification_reason(self, notification_id: int, reason: int) -> None:
        """Simulate notification closed."""
        self.closed_notifications.append((notification_id, reason))

    def get_notification(self, notification_id: int) -> Notification | None:
        """Get a notification by ID."""
        for n in self.notifications:
            if n.id == notification_id:
                return n
        return None

    def clear(self) -> None:
        """Clear all recorded data."""
        self.notifications.clear()
        self.action_invocations.clear()
        self.closed_notifications.clear()
        self._next_id = 0
