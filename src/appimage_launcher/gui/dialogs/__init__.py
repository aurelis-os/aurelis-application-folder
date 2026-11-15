"""Dialog implementations for AppImage Launcher GUI."""

from __future__ import annotations

from appimage_launcher.gui.dialogs.conflict import ConflictDialog
from appimage_launcher.gui.dialogs.error import ErrorDialog
from appimage_launcher.gui.dialogs.rename import RenameDialog
from appimage_launcher.gui.dialogs.update import UpdateDialog

__all__ = [
    "ConflictDialog",
    "RenameDialog",
    "UpdateDialog",
    "ErrorDialog",
]
