"""Update dialog for AppImage Launcher.

Shows update progress, release info, and signature status.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QStyle,
    QTextBrowser,
    QVBoxLayout,
)

from appimage_launcher.gui.translations import _
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class UpdateDialog(QDialog):
    """Dialog for showing update progress."""

    # Signal for progress updates from background thread
    progress_updated = pyqtSignal(int, str, str)  # percent, state, message

    def __init__(self, request: dict):
        super().__init__()
        self.request = request
        self._result = {"cancelled": True}

        self.setWindowTitle("AppImage Update")
        self.setMinimumWidth(500)
        self.setMinimumHeight(300)
        self.setModal(True)

        # Set window icon
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))

        self._setup_ui()
        self._populate_data()

        # Connect progress signal
        self.progress_updated.connect(self._on_progress_updated)

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Header
        self.header_label = QLabel()
        self.header_label.setWordWrap(True)
        layout.addWidget(self.header_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel(_("Checking for updates..."))
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Details area
        self.details_browser = QTextBrowser()
        self.details_browser.setOpenExternalLinks(True)
        self.details_browser.setMaximumHeight(150)
        layout.addWidget(self.details_browser)

        # Signature status
        self.signature_label = QLabel()
        self.signature_label.setWordWrap(True)
        layout.addWidget(self.signature_label)

        # Button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self.button_box)

    def _populate_data(self) -> None:
        """Populate the dialog with data from the request."""
        app_name = self.request.get("app_name", "App")
        current_version = self.request.get("current_version", "Unknown")
        latest_version = self.request.get("latest_version", "Unknown")
        release_url = self.request.get("release_url", "")
        has_signature = self.request.get("has_signature", False)

        self.header_label.setText(
            _(
                """<b>Updating {app_name}</b><br/>Current version: {current_version} → Latest: {latest_version}"""
            ).format(
                app_name=app_name, current_version=current_version, latest_version=latest_version
            )
        )

        if release_url:
            self.details_browser.setHtml(f'<a href="{release_url}">{_("View release notes")}</a>')

        if has_signature:
            self.signature_label.setText(
                _("✓ This AppImage is signed. Signature will be verified.")
            )
            self.signature_label.setStyleSheet("color: #2e7d32;")
        else:
            self.signature_label.setText(_("⚠ This AppImage is not signed. Proceed with caution."))
            self.signature_label.setStyleSheet("color: #f57c00;")

    def _on_progress_updated(self, percent: int, state: str, message: str) -> None:
        """Handle progress update from background thread."""
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

        if state == "done":
            self.header_label.setText("<b>Update complete!</b>")
            self.cancel_btn.setText("Close")
            self.cancel_btn.clicked.disconnect()
            self.cancel_btn.clicked.connect(self.accept)
            self._result = {"cancelled": False, "success": True}
        elif state == "error":
            self.header_label.setText("<b>Update failed</b>")
            self.cancel_btn.setText("Close")
            self.cancel_btn.clicked.disconnect()
            self.cancel_btn.clicked.connect(self.reject)
            self._result = {"cancelled": True, "success": False, "error": message}

    def update_progress(self, percent: int, state: str, message: str) -> None:
        """Update progress from external caller (thread-safe)."""
        self.progress_updated.emit(percent, state, message)

    def reject(self) -> None:
        """Handle dialog rejection."""
        self._result = {"cancelled": True}
        super().reject()

    def get_result(self) -> dict:
        """Get the dialog result."""
        return self._result
