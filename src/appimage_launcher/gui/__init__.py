"""GUI module for AppImage Launcher.

Provides helper dialogs for the daemon (conflict, rename, update, error).
The daemon launches these dialogs as separate processes via JSON stdin/stdout protocol.
"""

from __future__ import annotations

__all__ = ["main"]
