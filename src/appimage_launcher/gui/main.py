"""Main entry point for appimage-gui.

This module implements the JSON stdin/stdout protocol for helper dialogs.
The daemon launches this process with a dialog kind and JSON request on stdin,
and reads a JSON response from stdout.

Exit codes:
- 0: Dialog answered successfully
- 1: Dialog cancelled/closed by user
- 124: Dialog timed out
"""

from __future__ import annotations

import argparse
import json
import sys

from appimage_launcher.gui.translations import setup_translations
from appimage_launcher.logging import get_logger, setup_logging

logger = get_logger(__name__)

# Initialize translations
setup_translations()

# Exit codes
EXIT_OK = 0
EXIT_CANCELLED = 1
EXIT_TIMEOUT = 124


def get_dialog_class(kind: str):
    """Get the dialog class for the given kind."""
    if kind == "conflict":
        from appimage_launcher.gui.dialogs.conflict import ConflictDialog

        return ConflictDialog
    elif kind == "rename":
        from appimage_launcher.gui.dialogs.rename import RenameDialog

        return RenameDialog
    elif kind == "update":
        from appimage_launcher.gui.dialogs.update import UpdateDialog

        return UpdateDialog
    elif kind == "error":
        from appimage_launcher.gui.dialogs.error import ErrorDialog

        return ErrorDialog
    else:
        raise ValueError(f"Unknown dialog kind: {kind}")


def run_dialog(kind: str, request: dict, timeout: int | None = None) -> dict:
    """Run a dialog and return the response."""
    try:
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QColor, QPalette
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        logger.error("PyQt6 not available")
        return {"error": "PyQt6 not installed", "cancelled": True}

    # Create QApplication (only one per process)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("appimage-launcher")
    app.setApplicationDisplayName("AppImage Launcher")

    # Enable high-DPI scaling
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    # Apply dark theme if system is using dark mode
    _apply_system_theme(app, QPalette, QColor)

    # Get dialog class
    dialog_class = get_dialog_class(kind)

    # Create dialog
    dialog = dialog_class(request)

    # Set up timeout if provided
    timer = None
    if timeout and timeout > 0:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: dialog.reject())
        timer.start(timeout * 1000)

    # Run dialog
    result = dialog.exec()

    if timer:
        timer.stop()

    if result == dialog.Accepted:
        return dialog.get_result()
    else:
        return {"cancelled": True}


def _apply_system_theme(app, qpalette, qcolor) -> None:
    """Apply system theme (dark/light) to the application."""
    try:
        # Check if system is using dark theme
        palette = app.palette()
        window_color = palette.color(qpalette.ColorRole.Window)
        # If window background is dark, assume dark theme
        if window_color.lightness() < 128:
            # Already dark, ensure consistent styling
            _apply_dark_theme(app, qpalette, qcolor)
        else:
            # Light theme - ensure consistent styling
            _apply_light_theme(app, qpalette, qcolor)
    except Exception:
        # Fallback: no theme applied
        pass


def _apply_dark_theme(app, qpalette, qcolor) -> None:
    """Apply a consistent dark theme."""
    dark_palette = qpalette()
    dark_palette.setColor(qpalette.ColorRole.Window, qcolor(53, 53, 53))
    dark_palette.setColor(qpalette.ColorRole.WindowText, qcolor(255, 255, 255))
    dark_palette.setColor(qpalette.ColorRole.Base, qcolor(35, 35, 35))
    dark_palette.setColor(qpalette.ColorRole.AlternateBase, qcolor(53, 53, 53))
    dark_palette.setColor(qpalette.ColorRole.ToolTipBase, qcolor(255, 255, 255))
    dark_palette.setColor(qpalette.ColorRole.ToolTipText, qcolor(255, 255, 255))
    dark_palette.setColor(qpalette.ColorRole.Text, qcolor(255, 255, 255))
    dark_palette.setColor(qpalette.ColorRole.Button, qcolor(53, 53, 53))
    dark_palette.setColor(qpalette.ColorRole.ButtonText, qcolor(255, 255, 255))
    dark_palette.setColor(qpalette.ColorRole.BrightText, qcolor(255, 0, 0))
    dark_palette.setColor(qpalette.ColorRole.Link, qcolor(42, 130, 218))
    dark_palette.setColor(qpalette.ColorRole.Highlight, qcolor(42, 130, 218))
    dark_palette.setColor(qpalette.ColorRole.HighlightedText, qcolor(35, 35, 35))
    dark_palette.setColor(qpalette.ColorRole.PlaceholderText, qcolor(127, 127, 127))
    app.setPalette(dark_palette)


def _apply_light_theme(app, qpalette, qcolor) -> None:
    """Apply a consistent light theme."""
    light_palette = qpalette()
    light_palette.setColor(qpalette.ColorRole.Window, qcolor(240, 240, 240))
    light_palette.setColor(qpalette.ColorRole.WindowText, qcolor(0, 0, 0))
    light_palette.setColor(qpalette.ColorRole.Base, qcolor(255, 255, 255))
    light_palette.setColor(qpalette.ColorRole.AlternateBase, qcolor(240, 240, 240))
    light_palette.setColor(qpalette.ColorRole.ToolTipBase, qcolor(255, 255, 255))
    light_palette.setColor(qpalette.ColorRole.ToolTipText, qcolor(0, 0, 0))
    light_palette.setColor(qpalette.ColorRole.Text, qcolor(0, 0, 0))
    light_palette.setColor(qpalette.ColorRole.Button, qcolor(240, 240, 240))
    light_palette.setColor(qpalette.ColorRole.ButtonText, qcolor(0, 0, 0))
    light_palette.setColor(qpalette.ColorRole.BrightText, qcolor(255, 0, 0))
    light_palette.setColor(qpalette.ColorRole.Link, qcolor(0, 0, 255))
    light_palette.setColor(qpalette.ColorRole.Highlight, qcolor(0, 120, 215))
    light_palette.setColor(qpalette.ColorRole.HighlightedText, qcolor(255, 255, 255))
    light_palette.setColor(qpalette.ColorRole.PlaceholderText, qcolor(127, 127, 127))
    app.setPalette(light_palette)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="appimage-gui",
        description="AppImage Launcher GUI helper dialogs",
    )
    parser.add_argument(
        "dialog",
        choices=["conflict", "rename", "update", "error"],
        help="Dialog kind to show",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Timeout in seconds (0 = no timeout)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity",
    )

    args = parser.parse_args(argv)

    # Setup logging
    setup_logging(args.verbose)

    # Read JSON request from stdin
    try:
        request_text = sys.stdin.read()
        if not request_text.strip():
            logger.error("Empty request on stdin")
            return EXIT_CANCELLED
        request = json.loads(request_text)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON on stdin", error=str(e))
        return EXIT_CANCELLED
    except Exception as e:
        logger.error("Failed to read stdin", error=str(e))
        return EXIT_CANCELLED

    # Run dialog
    timeout = args.timeout if args.timeout > 0 else None
    response = run_dialog(args.dialog, request, timeout)

    # Write JSON response to stdout
    try:
        sys.stdout.write(json.dumps(response))
        sys.stdout.flush()
    except Exception as e:
        logger.error("Failed to write stdout", error=str(e))
        return EXIT_CANCELLED

    # Return exit code
    if response.get("cancelled"):
        return EXIT_CANCELLED
    if response.get("timeout"):
        return EXIT_TIMEOUT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
