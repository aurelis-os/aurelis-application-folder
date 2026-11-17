"""Core exceptions for AppImage Launcher.

These exceptions map directly to D-Bus error names and should be raised
instead of plain Exception. The D-Bus service layer will convert them
to dbus_fast.errors.DBusError with the correct error type.
"""

from __future__ import annotations

from typing import Any

from appimage_launcher.core.state import AppRecord
from appimage_launcher.core.versions import VersionRelation


class AppImageLauncherError(Exception):
    """Base exception for all AppImage Launcher errors."""

    # D-Bus error name (without the io.github.appimage_launcher.Error. prefix)
    DBUS_ERROR_NAME: str = "Error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    @classmethod
    def get_dbus_error_name(cls) -> str:
        """Full D-Bus error name."""
        return f"io.github.appimage_launcher.Error.{cls.DBUS_ERROR_NAME}"

    @property
    def dbus_error_name(self) -> str:
        """Full D-Bus error name (instance property)."""
        return f"io.github.appimage_launcher.Error.{self.DBUS_ERROR_NAME}"


class NotFoundError(AppImageLauncherError):
    """Resource not found."""

    DBUS_ERROR_NAME = "NotFound"


class ConflictPendingError(AppImageLauncherError):
    """Conflict requires user decision."""

    DBUS_ERROR_NAME = "ConflictPending"

    def __init__(
        self,
        conflict_id: str,
        old_app: AppRecord,
        new_meta: Any,
        relation: VersionRelation,
        is_self_update: bool,
        message: str = "Conflict requires decision",
    ):
        super().__init__(message)
        self.conflict_id = conflict_id
        self.old_app = old_app
        self.new_meta = new_meta
        self.relation = relation
        self.is_self_update = is_self_update


class NameCollisionError(AppImageLauncherError):
    """Name collision with different app_id."""

    DBUS_ERROR_NAME = "NameCollision"


class InvalidArgumentError(AppImageLauncherError):
    """Invalid argument provided."""

    DBUS_ERROR_NAME = "InvalidArgument"


class AmbiguousError(AppImageLauncherError):
    """Ambiguous reference (multiple matches)."""

    DBUS_ERROR_NAME = "Ambiguous"


class InvalidAppImageError(AppImageLauncherError):
    """Invalid or corrupt AppImage file."""

    DBUS_ERROR_NAME = "InvalidAppImage"


class BusyError(AppImageLauncherError):
    """Resource busy (lock held, operation in progress)."""

    DBUS_ERROR_NAME = "Busy"


class UnsupportedTypeError(AppImageLauncherError):
    """Unsupported file type."""

    DBUS_ERROR_NAME = "UnsupportedType"


class PolicyForbidsError(AppImageLauncherError):
    """Operation forbidden by policy."""

    DBUS_ERROR_NAME = "PolicyForbids"


class IoError(AppImageLauncherError):
    """I/O error."""

    DBUS_ERROR_NAME = "Io"


class AbortedError(AppImageLauncherError):
    """Operation aborted."""

    DBUS_ERROR_NAME = "Aborted"


# Mapping from exception class to D-Bus error name
EXCEPTION_TO_DBUS_ERROR = {
    NotFoundError: "io.github.appimage_launcher.Error.NotFound",
    ConflictPendingError: "io.github.appimage_launcher.Error.ConflictPending",
    NameCollisionError: "io.github.appimage_launcher.Error.NameCollision",
    InvalidArgumentError: "io.github.appimage_launcher.Error.InvalidArgument",
    AmbiguousError: "io.github.appimage_launcher.Error.Ambiguous",
    InvalidAppImageError: "io.github.appimage_launcher.Error.InvalidAppImage",
    BusyError: "io.github.appimage_launcher.Error.Busy",
    UnsupportedTypeError: "io.github.appimage_launcher.Error.UnsupportedType",
    PolicyForbidsError: "io.github.appimage_launcher.Error.PolicyForbids",
    IoError: "io.github.appimage_launcher.Error.Io",
    AbortedError: "io.github.appimage_launcher.Error.Aborted",
}

# Reverse mapping for convenience
DBUS_ERROR_TO_EXCEPTION = {v: k for k, v in EXCEPTION_TO_DBUS_ERROR.items()}


def raise_dbus_error(exception: AppImageLauncherError) -> None:
    """Raise a dbus_fast.errors.DBusError from an AppImageLauncherError.

    This should be called at the D-Bus service boundary to convert
    core exceptions to proper D-Bus errors.
    """
    from dbus_fast.errors import DBusError

    raise DBusError(exception.dbus_error_name, exception.message)


def map_exception_to_dbus_error(exception: Exception) -> Exception:
    """Map a core exception to a DBusError if it's a known type.

    If the exception is not a known AppImageLauncherError, return it unchanged.
    """
    if isinstance(exception, AppImageLauncherError):
        from dbus_fast.errors import DBusError

        return DBusError(exception.dbus_error_name, exception.message)
    return exception
