"""Rename dialog for AppImage Launcher.

Allows the user to rename an installed app with live preview
and collision validation.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from appimage_launcher.gui.translations import _
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class RenameDialog(QDialog):
    """Dialog for renaming an installed app."""

    def __init__(self, request: dict):
        super().__init__()
        self.request = request
        self._result = {"cancelled": True}

        self.setWindowTitle("Rename App")
        self.setMinimumWidth(500)
        self.setModal(True)

        # Set window icon
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))

        self._setup_ui()
        self._populate_data()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Header
        current_name = self.request.get("current_name", "App")
        header = QLabel(_("Rename <b>{name}</b>").format(name=current_name))
        header.setWordWrap(True)
        layout.addWidget(header)

        # Current name display
        current_widget = QWidget()
        current_layout = QHBoxLayout(current_widget)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.addWidget(QLabel(_("Current:")))
        self.current_label = QLabel()
        self.current_label.setStyleSheet("font-weight: bold; color: #666;")
        current_layout.addWidget(self.current_label)
        current_layout.addStretch()
        layout.addWidget(current_widget)

        # New name input
        name_widget = QWidget()
        name_layout = QHBoxLayout(name_widget)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.addWidget(QLabel(_("New name:")))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("Enter new name..."))
        self.name_edit.textChanged.connect(self._on_name_changed)
        name_layout.addWidget(self.name_edit)
        layout.addWidget(name_widget)

        # Preview
        preview_widget = QWidget()
        preview_layout = QHBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(QLabel(_("Preview:")))
        self.preview_label = QLabel()
        self.preview_label.setStyleSheet("font-family: monospace; color: #333;")
        preview_layout.addWidget(self.preview_label)
        preview_layout.addStretch()
        layout.addWidget(preview_widget)

        # Validation message
        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.setStyleSheet("color: #c62828;")
        self.validation_label.hide()
        layout.addWidget(self.validation_label)

        # Reset button
        self.reset_btn = QPushButton(_("Reset to default name"))
        self.reset_btn.clicked.connect(self._reset_to_default)
        layout.addWidget(self.reset_btn)

        # Button box
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        layout.addWidget(self.button_box)

    def _populate_data(self) -> None:
        """Populate the dialog with data from the request."""
        current_name = self.request.get("current_name", "")
        default_name = self.request.get("default_name", "")
        existing_names = self.request.get("existing_names", [])

        self.current_name = current_name
        self.default_name = default_name
        self.existing_names = set(existing_names)

        self.current_label.setText(current_name)
        self.name_edit.setText(current_name)
        self._update_preview(current_name)

    def _on_name_changed(self, text: str) -> None:
        """Handle name input changes."""
        self._update_preview(text)
        self._validate_name(text)

    def _update_preview(self, name: str) -> None:
        """Update the preview label."""
        # Show what the file would be named
        preview = f"~/Applications/{name}"
        self.preview_label.setText(preview)

    def _validate_name(self, name: str) -> None:
        """Validate the new name."""
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)

        if not name.strip():
            self.validation_label.setText(_("Name cannot be empty"))
            self.validation_label.show()
            ok_btn.setEnabled(False)
            return

        # Check for reserved suffixes
        reserved_suffixes = [".disabled", ".part", ".crdownload", ".zs-old"]
        for suffix in reserved_suffixes:
            if name.endswith(suffix):
                self.validation_label.setText(
                    _("Name cannot end with '{suffix}'").format(suffix=suffix)
                )
                self.validation_label.show()
                ok_btn.setEnabled(False)
                return

        # Check for leading/trailing dots and spaces
        if name.startswith(".") or name.startswith(" "):
            self.validation_label.setText(_("Name cannot start with '.' or space"))
            self.validation_label.show()
            ok_btn.setEnabled(False)
            return

        if name.endswith(".") or name.endswith(" "):
            self.validation_label.setText(_("Name cannot end with '.' or space"))
            self.validation_label.show()
            ok_btn.setEnabled(False)
            return

        # Check for collision with existing names (excluding current)
        if name in self.existing_names and name != self.current_name:
            self.validation_label.setText(
                _("An app named '{name}' already exists").format(name=name)
            )
            self.validation_label.show()
            ok_btn.setEnabled(False)
            return

        # Check length (UTF-8 bytes)
        if len(name.encode("utf-8")) > 100:
            self.validation_label.setText(_("Name too long (max 100 bytes)"))
            self.validation_label.show()
            ok_btn.setEnabled(False)
            return

        # Valid
        self.validation_label.hide()
        ok_btn.setEnabled(True)

    def _reset_to_default(self) -> None:
        """Reset to the default name from the AppImage."""
        if self.default_name:
            self.name_edit.setText(self.default_name)

    def accept(self) -> None:
        """Handle dialog acceptance."""
        new_name = self.name_edit.text().strip()
        if new_name and new_name != self.current_name:
            self._result = {
                "new_name": new_name,
                "cancelled": False,
            }
        else:
            self._result = {"cancelled": True}
        super().accept()

    def reject(self) -> None:
        """Handle dialog rejection."""
        self._result = {"cancelled": True}
        super().reject()

    def get_result(self) -> dict:
        """Get the dialog result."""
        return self._result
