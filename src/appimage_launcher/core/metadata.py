"""AppImage metadata parsing."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from appimage_launcher.constants import MAX_ICON_BASE64_BYTES
from appimage_launcher.core.desktop import parse_appstream, parse_desktop_file
from appimage_launcher.core.elf import read_sha256_sig, read_upd_info, validate_appimage
from appimage_launcher.core.identity import compute_app_id, compute_fingerprint
from appimage_launcher.core.squash import extract_desktop_file, extract_icon, extract_metainfo
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AppImageMeta:
    """Parsed AppImage metadata."""

    type: int  # 1 or 2
    offset: int  # squashfs offset
    name: str  # Name from .desktop
    version: str | None
    comment: str | None
    categories: list[str]
    icon_bytes: bytes | None
    icon_ext: str | None
    app_id: str
    update_info: str | None
    has_signature: bool
    size: int
    fingerprint: str


def read_icon_bytes(
    path: Path, max_bytes: int = MAX_ICON_BASE64_BYTES
) -> tuple[bytes, str] | tuple[None, None]:
    """Read icon file and return bytes and extension."""
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            return None, None
        ext = path.suffix.lstrip(".").lower()
        if ext not in ("png", "svg", "jpg", "jpeg", "ico"):
            ext = "png"
        return data, ext
    except Exception:
        return None, None


def extract_version_from_filename(filename: str) -> str | None:
    """Extract version-like string from filename."""
    # Remove extension
    stem = Path(filename).stem
    # Common version patterns
    patterns = [
        r"(\d+\.\d+\.\d+(?:[-+].*)?)",  # 1.2.3, 1.2.3-rc1
        r"(\d+\.\d+(?:[-+].*)?)",  # 1.2, 1.2-beta
        r"(v\d+\.\d+\.\d+(?:[-+].*)?)",  # v1.2.3
        r"(\d{4}\.\d{2}\.\d+(?:[-+].*)?)",  # 2024.01.1
    ]
    for pattern in patterns:
        match = re.search(pattern, stem)
        if match:
            return match.group(1)
    return None


def read_appimage_meta(appimage_path: Path) -> AppImageMeta:
    """Read all metadata from an AppImage file without executing it."""
    # Validate and get ELF header + squashfs offset
    header, offset = validate_appimage(appimage_path)

    # Get file size
    size = appimage_path.stat().st_size

    # Read .upd_info and .sha256_sig
    with open(appimage_path, "rb") as f:
        update_info = read_upd_info(f)
        has_signature = read_sha256_sig(f) is not None

    # Extract files to temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Extract desktop file
        desktop_path = extract_desktop_file(appimage_path, offset, tmpdir_path)
        desktop_data = {}
        if desktop_path:
            desktop_data = parse_desktop_file(desktop_path)

        # Extract metainfo files (needed for app_id and version)
        metainfo_files = extract_metainfo(appimage_path, offset, tmpdir_path)

        # Get name from desktop file
        name = desktop_data.get("Name", "")
        if not name:
            name = appimage_path.stem

        # Get version
        version = desktop_data.get("X-AppImage-Version")
        if not version:
            # Try AppStream
            for mf in metainfo_files:
                appstream_data = parse_appstream(mf)
                if "version" in appstream_data:
                    version = appstream_data["version"]
                    break
        if not version:
            version = extract_version_from_filename(appimage_path.name)

        # Get comment
        comment = desktop_data.get("Comment")

        # Get categories
        categories_str = desktop_data.get("Categories", "")
        categories = [c.strip() for c in categories_str.split(";") if c.strip()]

        # Get icon
        icon_name = desktop_data.get("Icon", "")
        icon_bytes = None
        icon_ext = None
        if icon_name:
            icon_path = extract_icon(appimage_path, offset, tmpdir_path, icon_name)
            if icon_path:
                icon_bytes, icon_ext = read_icon_bytes(icon_path)

        # Compute app_id
        app_id = compute_app_id(desktop_data, metainfo_files, appimage_path.stem)

        # Compute fingerprint
        fingerprint = compute_fingerprint(appimage_path, offset, size)

    return AppImageMeta(
        type=2,  # We only support type 2
        offset=offset,
        name=name,
        version=version,
        comment=comment,
        categories=categories,
        icon_bytes=icon_bytes,
        icon_ext=icon_ext,
        app_id=app_id,
        update_info=update_info,
        has_signature=has_signature,
        size=size,
        fingerprint=fingerprint,
    )
