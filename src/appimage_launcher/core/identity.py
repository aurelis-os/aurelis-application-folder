"""AppImage identity computation.

Computes stable app_id and fingerprint for AppImages.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from appimage_launcher.core.desktop import parse_appstream
from appimage_launcher.core.naming import sanitize_name
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


def compute_app_id(
    desktop_data: dict[str, str],
    metainfo_files: list[Path],
    fallback_name: str,
) -> str:
    """Compute stable app_id for an AppImage.

    Resolution order:
    1. AppStream <id> from metainfo
    2. Stem of root .desktop file (without .desktop)
    3. Slug of Name= from .desktop
    """
    # 1. Try AppStream id from metainfo files
    for mf in metainfo_files:
        appstream_data = parse_appstream(mf)
        if "app_id" in appstream_data:
            return sanitize_name(appstream_data["app_id"]).lower()

    # 2. Try .desktop file stem (if we have the filename)
    # This would need the actual filename, for now use fallback

    # 3. Slug of Name=
    name = desktop_data.get("Name", fallback_name)
    if name:
        # Convert to lowercase, replace spaces/special chars with hyphens
        slug = sanitize_name(name).lower()
        slug = slug.replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c in "-.")
        # Remove duplicate hyphens
        import re

        slug = re.sub(r"-+", "-", slug).strip("-")
        if slug:
            return slug

    # Ultimate fallback
    return sanitize_name(fallback_name).lower()


def compute_fingerprint(appimage_path: Path, offset: int, size: int) -> str:
    """Compute fingerprint for an AppImage.

    Fingerprint = SHA256(first 4 MiB + size (8 bytes LE) + squashfs offset (8 bytes LE))
    This allows quick comparison without full file hashing.
    """
    hasher = hashlib.sha256()

    # Read first 4 MiB
    with open(appimage_path, "rb") as f:
        first_4mb = f.read(4 * 1024 * 1024)
        hasher.update(first_4mb)

    # Add size as 8-byte little-endian
    hasher.update(size.to_bytes(8, "little"))

    # Add squashfs offset as 8-byte little-endian
    hasher.update(offset.to_bytes(8, "little"))

    return hasher.hexdigest()


def compute_full_sha256(appimage_path: Path) -> str:
    """Compute full SHA-256 of an AppImage file."""
    hasher = hashlib.sha256()
    with open(appimage_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
