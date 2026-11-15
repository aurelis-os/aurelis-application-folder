"""Naming utilities for AppImage Launcher.

Sanitization, template rendering, and collision classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from appimage_launcher.constants import (
    MAX_MANAGED_NAME_BYTES,
    RESERVED_SUFFIXES,
)
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CollisionResult:
    """Result of collision classification."""

    type: Literal["none", "non_appimage", "same_app_id", "different_app_id"]
    existing_app_id: str | None = None
    suggested_name: str | None = None


def sanitize_name(name: str) -> str:
    """Sanitize a name for use as a managed file name.

    Rules:
    - Strip control characters
    - Strip / and \
    - Strip leading/trailing dots and spaces
    - Collapse whitespace
    - Max 100 bytes UTF-8 (don't cut in middle of codepoint)
    - Never produce names ending in reserved suffixes
    - Never produce names starting with .
    """
    if not name:
        return "Unnamed"

    # Strip control characters (including \t, \n, \r)
    name = "".join(ch for ch in name if ord(ch) >= 32)

    # Strip path separators
    name = name.replace("/", "").replace("\\", "")

    # Strip leading/trailing dots and spaces
    name = name.strip(". ")

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name)

    # Truncate to max bytes (UTF-8 safe)
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_MANAGED_NAME_BYTES:
        encoded = encoded[:MAX_MANAGED_NAME_BYTES]
        # Find valid UTF-8 boundary
        while encoded and (encoded[-1] & 0xC0) == 0x80:
            encoded = encoded[:-1]
        name = encoded.decode("utf-8", errors="ignore")

    # Ensure not empty
    if not name:
        name = "Unnamed"

    # Check reserved suffixes
    for suffix in RESERVED_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].rstrip(". ")
            if not name:
                name = "Unnamed"

    # Ensure doesn't start with .
    if name.startswith("."):
        name = name.lstrip(".")
        if not name:
            name = "Unnamed"

    return name


def render_template(
    template: str, name: str, version: str | None = None, app_id: str | None = None
) -> str:
    """Render a naming template with variables."""
    result = template
    result = result.replace("{name}", name or "")
    result = result.replace("{version}", version or "")
    result = result.replace("{app_id}", app_id or "")
    return sanitize_name(result)


def classify_collision(
    target_path: Path,
    new_app_id: str,
    existing_apps: dict[str, dict],  # managed_name -> app record
) -> CollisionResult:
    """Classify a collision when installing a new AppImage.

    Returns CollisionResult with type and suggested action.
    """
    target_name = target_path.name

    # Check if target exists
    if not target_path.exists():
        return CollisionResult(type="none")

    # Check if it's a tracked AppImage
    if target_name in existing_apps:
        existing = existing_apps[target_name]
        existing_app_id = existing.get("app_id")
        if existing_app_id == new_app_id:
            return CollisionResult(
                type="same_app_id",
                existing_app_id=existing_app_id,
            )
        else:
            # Different app_id, same name - suggest appending app_id
            short_id = new_app_id.split(".")[-1] if "." in new_app_id else new_app_id[:8]
            suggested = f"{target_name} ({short_id})"
            suggested = sanitize_name(suggested)
            return CollisionResult(
                type="different_app_id",
                existing_app_id=existing_app_id,
                suggested_name=suggested,
            )

    # Target exists but not tracked - could be non-AppImage file or directory
    if target_path.is_dir():
        return CollisionResult(type="non_appimage")

    # Check if it's an AppImage by trying to read metadata
    # For now, treat as non-AppImage collision
    return CollisionResult(type="non_appimage")


def generate_kept_name(base_name: str, version: str | None, existing_names: set[str]) -> str:
    """Generate a unique name for a kept version."""
    from appimage_launcher.config import get_config

    config = get_config()
    template = config.naming.keep_template

    name = render_template(template, base_name, version)
    if name not in existing_names:
        return name

    # Try with counter
    counter = 2
    while True:
        candidate = f"{name}.old{counter}" if counter > 2 else f"{name}.old"
        candidate = sanitize_name(candidate)
        if candidate not in existing_names:
            return candidate
        counter += 1
