"""Core library for AppImage Launcher."""

from appimage_launcher.core.desktop import parse_appstream, parse_desktop_file
from appimage_launcher.core.elf import (
    find_squashfs_offset,
    read_elf_header,
    read_section,
    read_sha256_sig,
    read_upd_info,
)
from appimage_launcher.core.exceptions import (
    DBUS_ERROR_TO_EXCEPTION,
    EXCEPTION_TO_DBUS_ERROR,
    AbortedError,
    AmbiguousError,
    AppImageLauncherError,
    BusyError,
    ConflictPendingError,
    InvalidAppImageError,
    InvalidArgumentError,
    IoError,
    NameCollisionError,
    NotFoundError,
    PolicyForbidsError,
    UnsupportedTypeError,
    map_exception_to_dbus_error,
    raise_dbus_error,
)
from appimage_launcher.core.identity import compute_app_id, compute_fingerprint
from appimage_launcher.core.integration import install_icons, refresh_caches, write_desktop_entry
from appimage_launcher.core.manager import Manager
from appimage_launcher.core.metadata import AppImageMeta
from appimage_launcher.core.naming import classify_collision, render_template, sanitize_name
from appimage_launcher.core.running import RunningTracker, scan_running_appimages
from appimage_launcher.core.squash import (
    extract_desktop_file,
    extract_icon,
    extract_metainfo,
    list_squashfs_contents,
)
from appimage_launcher.core.state import (
    AppRecord,
    BackupRecord,
    EventRecord,
    StateDB,
    TombstoneRecord,
)
from appimage_launcher.core.updates import check_for_updates, parse_upd_info
from appimage_launcher.core.versions import VersionRelation, compare_versions

__all__ = [
    # exceptions
    "AppImageLauncherError",
    "NotFoundError",
    "ConflictPendingError",
    "NameCollisionError",
    "InvalidArgumentError",
    "AmbiguousError",
    "InvalidAppImageError",
    "BusyError",
    "UnsupportedTypeError",
    "PolicyForbidsError",
    "IoError",
    "AbortedError",
    "EXCEPTION_TO_DBUS_ERROR",
    "DBUS_ERROR_TO_EXCEPTION",
    "map_exception_to_dbus_error",
    "raise_dbus_error",
    # elf
    "read_elf_header",
    "find_squashfs_offset",
    "read_section",
    "read_upd_info",
    "read_sha256_sig",
    # squash
    "extract_desktop_file",
    "extract_icon",
    "extract_metainfo",
    "list_squashfs_contents",
    # metadata
    "AppImageMeta",
    "parse_desktop_file",
    "parse_appstream",
    # naming
    "sanitize_name",
    "render_template",
    "classify_collision",
    # identity
    "compute_app_id",
    "compute_fingerprint",
    # versions
    "compare_versions",
    "VersionRelation",
    # state
    "StateDB",
    "AppRecord",
    "TombstoneRecord",
    "BackupRecord",
    "EventRecord",
    # integration
    "write_desktop_entry",
    "install_icons",
    "refresh_caches",
    # manager
    "Manager",
    # updates
    "parse_upd_info",
    "check_for_updates",
    # running
    "RunningTracker",
    "scan_running_appimages",
]
