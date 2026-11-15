"""Conflict dialog for AppImage Launcher.

Shows a comparison between the installed app and the new AppImage,
allowing the user to choose: Replace, Keep both, or Cancel.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from appimage_launcher.gui.translations import _
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class ConflictDialog(QDialog):
    """Dialog for resolving AppImage installation conflicts."""

    def __init__(self, request: dict):
        super().__init__()
        self.request = request
        self._result = {"cancelled": True}

        self.setWindowTitle("AppImage Conflict")
        self.setMinimumWidth(600)
        self.setModal(True)

        # Set window icon
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion))

        self._setup_ui()
        self._populate_data()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Header
        old_name = self.request.get("old_app", {}).get("name", "App")
        header = QLabel(
            _(
                """<b>{name}</b> is already installed. A new version was dropped in the Applications folder."""
            ).format(name=old_name)
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Comparison table
        comparison = self._create_comparison_widget()
        layout.addWidget(comparison)

        # Relation badge
        relation = self.request.get("relation", "unknown")
        relation_label = QLabel(
            _("<b>Change:</b> {relation}").format(relation=relation.capitalize())
        )
        relation_label.setStyleSheet(self._get_relation_style(relation))
        layout.addWidget(relation_label)

        # Options
        options_group = QGroupBox(_("What would you like to do?"))
        options_layout = QVBoxLayout(options_group)

        self.replace_radio = QPushButton(_("Replace"))
        self.replace_radio.setCheckable(True)
        self.replace_radio.setChecked(True)
        self.replace_radio.clicked.connect(lambda: self._set_choice("replace"))
        options_layout.addWidget(self.replace_radio)

        self.keep_radio = QPushButton(_("Keep both (rename old)"))
        self.keep_radio.setCheckable(True)
        self.keep_radio.clicked.connect(lambda: self._set_choice("keep"))
        options_layout.addWidget(self.keep_radio)

        # Editable name for kept version
        self.kept_name_edit = QLineEdit()
        self.kept_name_edit.setPlaceholderText(_("Name for the kept version (e.g., 'Gimp 8.9.2')"))
        self.kept_name_edit.setEnabled(False)
        options_layout.addWidget(self.kept_name_edit)

        self.cancel_radio = QPushButton(_("Cancel"))
        self.cancel_radio.setCheckable(True)
        self.cancel_radio.clicked.connect(lambda: self._set_choice("cancel"))
        options_layout.addWidget(self.cancel_radio)

        layout.addWidget(options_group)

        # Remember choice checkbox
        self.remember_check = QCheckBox(_("Remember my choice for this app"))
        layout.addWidget(self.remember_check)

        # Details button
        details_btn = QPushButton(_("Show details…"))
        details_btn.clicked.connect(self._show_details)
        layout.addWidget(details_btn)

        # Button box
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        # Store choice
        self._choice = "replace"

    def _create_comparison_widget(self) -> QWidget:
        """Create the comparison widget showing old vs new."""
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # Old app
        old_group = QGroupBox(_("Currently Installed"))
        old_layout = QFormLayout(old_group)

        self.old_icon_label = QLabel()
        self.old_icon_label.setFixedSize(64, 64)
        self.old_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.old_icon_label.setStyleSheet("border: 1px solid #ccc; background: #f5f5f5;")
        old_layout.addRow(_("Icon:"), self.old_icon_label)

        self.old_name_label = QLabel()
        old_layout.addRow(_("Name:"), self.old_name_label)

        self.old_version_label = QLabel()
        old_layout.addRow(_("Version:"), self.old_version_label)

        self.old_size_label = QLabel()
        old_layout.addRow(_("Size:"), self.old_size_label)

        self.old_path_label = QLabel()
        self.old_path_label.setWordWrap(True)
        self.old_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        old_layout.addRow(_("Path:"), self.old_path_label)

        layout.addWidget(old_group)

        # Arrow
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setStyleSheet("font-size: 24px; font-weight: bold; color: #666;")
        layout.addWidget(arrow)

        # New app
        new_group = QGroupBox(_("New AppImage"))
        new_layout = QFormLayout(new_group)

        self.new_icon_label = QLabel()
        self.new_icon_label.setFixedSize(64, 64)
        self.new_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.new_icon_label.setStyleSheet("border: 1px solid #ccc; background: #f5f5f5;")
        new_layout.addRow(_("Icon:"), self.new_icon_label)

        self.new_name_label = QLabel()
        new_layout.addRow(_("Name:"), self.new_name_label)

        self.new_version_label = QLabel()
        new_layout.addRow(_("Version:"), self.new_version_label)

        self.new_size_label = QLabel()
        new_layout.addRow(_("Size:"), self.new_size_label)

        self.new_path_label = QLabel()
        self.new_path_label.setWordWrap(True)
        self.new_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        new_layout.addRow(_("Path:"), self.new_path_label)

        layout.addWidget(new_group)

        return widget

    def _populate_data(self) -> None:
        """Populate the dialog with data from the request."""
        old_app = self.request.get("old_app", {})
        new_meta = self.request.get("new_meta", {})

        # Old app
        self.old_name_label.setText(old_app.get("name", "Unknown"))
        self.old_version_label.setText(old_app.get("version", "Unknown"))
        self.old_size_label.setText(self._format_size(old_app.get("size", 0)))
        self.old_path_label.setText(old_app.get("path", "Unknown"))

        # New app
        self.new_name_label.setText(new_meta.get("name", "Unknown"))
        self.new_version_label.setText(new_meta.get("version", "Unknown"))
        self.new_size_label.setText(self._format_size(new_meta.get("size", 0)))
        self.new_path_label.setText(self.request.get("source_path", "Unknown"))

        # Load icons if available
        self._load_icon(old_app.get("icon_path"), self.old_icon_label)
        self._load_icon(new_meta.get("icon_path"), self.new_icon_label)

        # Set default kept name
        old_name = old_app.get("managed_name", "App")
        old_version = old_app.get("version", "")
        if old_version:
            self.kept_name_edit.setText(f"{old_name} {old_version}")
        else:
            self.kept_name_edit.setText(f"{old_name}.old")

    def _load_icon(self, icon_path: str | None, label: QLabel) -> None:
        """Load an icon into a label."""
        if icon_path and Path(icon_path).exists():
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    64,
                    64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                label.setPixmap(pixmap)
                return
        # Fallback: show text
        label.setText("📦")

    def _format_size(self, size: int) -> str:
        """Format size in human-readable units."""
        size_float = float(size)
        for unit in ["B", "KiB", "MiB", "GiB"]:
            if size_float < 1024:
                return f"{size_float:.1f} {unit}"
            size_float /= 1024
        return f"{size_float:.1f} TiB"

    def _get_relation_style(self, relation: str) -> str:
        """Get CSS style for relation badge."""
        styles = {
            "upgrade": "color: #2e7d32; background: #e8f5e9; padding: 4px 8px; border-radius: 4px;",
            "downgrade": "color: #c62828; background: #ffebee; padding: 4px 8px; border-radius: 4px;",
            "same": "color: #1565c0; background: #e3f2fd; padding: 4px 8px; border-radius: 4px;",
        }
        return styles.get(
            relation, "color: #666; background: #f5f5f5; padding: 4px 8px; border-radius: 4px;"
        )

    def _set_choice(self, choice: str) -> None:
        """Set the user's choice."""
        self._choice = choice
        # Update radio buttons
        self.replace_radio.setChecked(choice == "replace")
        self.keep_radio.setChecked(choice == "keep")
        self.cancel_radio.setChecked(choice == "cancel")
        # Enable/disable kept name edit
        self.kept_name_edit.setEnabled(choice == "keep")

    def _show_details(self) -> None:
        """Show detailed information dialog."""
        details = {
            "Old App": self.request.get("old_app", {}),
            "New AppImage": self.request.get("new_meta", {}),
            "Relation": self.request.get("relation", "unknown"),
            "Source Path": self.request.get("source_path", "Unknown"),
            "Is Self-Update": self.request.get("is_self_update", False),
        }
        msg = QMessageBox(self)
        msg.setWindowTitle(_("Conflict Details"))
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(_("<b>Conflict Details</b>"))
        msg.setDetailedText(json.dumps(details, indent=2, default=str))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def accept(self) -> None:
        """Handle dialog acceptance."""
        if self._choice == "cancel":
            self._result = {"cancelled": True}
        elif self._choice == "replace":
            self._result = {
                "decision": "replace",
                "remember": self.remember_check.isChecked(),
            }
        elif self._choice == "keep":
            kept_name = self.kept_name_edit.text().strip()
            if not kept_name:
                kept_name = self.kept_name_edit.placeholderText()
            self._result = {
                "decision": "keep",
                "kept_name": kept_name,
                "remember": self.remember_check.isChecked(),
            }
        super().accept()

    def reject(self) -> None:
        """Handle dialog rejection."""
        self._result = {"cancelled": True}
        super().reject()

    def get_result(self) -> dict:
        """Get the dialog result."""
        return self._result
