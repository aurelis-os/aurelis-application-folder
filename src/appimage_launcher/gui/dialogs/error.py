"""Error/Details dialog for AppImage Launcher.

Shows scrollable error text with a copy button.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QTextEdit,
    QVBoxLayout,
)

from appimage_launcher.gui.translations import _
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class ErrorDialog(QDialog):
    """Dialog for showing error details."""

    def __init__(self, request: dict):
        super().__init__()
        self.request = request
        self._result = {"cancelled": True}

        self.setWindowTitle("Error Details")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)
        self.setModal(True)

        # Set window icon
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical))

        self._setup_ui()
        self._populate_data()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Header
        self.header_label = QLabel()
        self.header_label.setWordWrap(True)
        self.header_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #c62828;")
        layout.addWidget(self.header_label)

        # Message
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: #333; margin-bottom: 8px;")
        layout.addWidget(self.message_label)

        # Scrollable details
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.details_text.setStyleSheet("font-family: monospace; font-size: 12px;")
        scroll.setWidget(self.details_text)

        layout.addWidget(scroll, 1)  # Stretch factor

        # Copy button
        self.copy_btn = QPushButton(_("Copy to clipboard"))
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(self.copy_btn)

        # Button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self.button_box.accepted.connect(self.accept)
        layout.addWidget(self.button_box)

    def _populate_data(self) -> None:
        """Populate the dialog with data from the request."""
        title = self.request.get("title", _("Error"))
        message = self.request.get("message", _("An unknown error occurred"))
        details = self.request.get("details", "")

        self.header_label.setText(title)
        self.message_label.setText(message)

        if details:
            self.details_text.setPlainText(details)
        else:
            self.details_text.setPlainText(_("(No additional details)"))

    def _copy_to_clipboard(self) -> None:
        """Copy error details to clipboard."""
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        text = f"{self.header_label.text()}\n\n{self.message_label.text()}\n\n{self.details_text.toPlainText()}"
        clipboard.setText(text)
        self.copy_btn.setText("Copied!")
        # Reset button text after 2 seconds
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(2000, lambda: self.copy_btn.setText("Copy to clipboard"))

    def accept(self) -> None:
        """Handle dialog acceptance."""
        self._result = {"cancelled": False}
        super().accept()

    def reject(self) -> None:
        """Handle dialog rejection."""
        self._result = {"cancelled": True}
        super().reject()

    def get_result(self) -> dict:
        """Get the dialog result."""
        return self._result
