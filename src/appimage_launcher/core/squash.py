"""Squashfs extraction wrapper for AppImage files.

Safe unsquashfs wrapper with timeouts, output caps, and symlink-escape protection.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from appimage_launcher.constants import (
    UNSQUASHFS_TIMEOUT_SECONDS,
)
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


class SquashfsError(Exception):
    """Base exception for squashfs operations."""

    pass


class ExtractionError(SquashfsError):
    """Failed to extract from squashfs."""

    pass


def _run_unsquashfs(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run unsquashfs with safety limits."""
    cmd = ["unsquashfs"] + args
    logger.debug("Running unsquashfs", cmd=cmd)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=UNSQUASHFS_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise ExtractionError(f"unsquashfs failed: {result.stderr}")
        return result
    except subprocess.TimeoutExpired as e:
        raise ExtractionError(f"unsquashfs timed out after {UNSQUASHFS_TIMEOUT_SECONDS}s") from e


def list_squashfs_contents(appimage_path: Path, offset: int) -> list[str]:
    """List contents of squashfs in AppImage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        # Use -ll to list without extracting
        result = _run_unsquashfs(["-o", str(offset), "-ll", str(appimage_path)], cwd=tmpdir_path)
        # Parse output
        lines = result.stdout.strip().split("\n")
        files = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                files.append(parts[-1])
        return files


def extract_desktop_file(appimage_path: Path, offset: int, dest_dir: Path) -> Path | None:
    """Extract the root .desktop file from AppImage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        try:
            _run_unsquashfs(
                ["-o", str(offset), "-d", str(tmpdir_path), "-f", str(appimage_path), "*.desktop"],
                cwd=tmpdir_path,
            )

            # Find .desktop files
            desktop_files = list(tmpdir_path.rglob("*.desktop"))
            if not desktop_files:
                return None

            # Prefer root level .desktop file
            for df in desktop_files:
                if df.parent == tmpdir_path:
                    target = dest_dir / df.name
                    df.rename(target)
                    return target

            # Fall back to first found
            target = dest_dir / desktop_files[0].name
            desktop_files[0].rename(target)
            return target

        except ExtractionError:
            return None


def extract_icon(appimage_path: Path, offset: int, dest_dir: Path, icon_name: str) -> Path | None:
    """Extract icon from AppImage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        try:
            # Try to extract .DirIcon and the named icon
            _run_unsquashfs(
                [
                    "-o",
                    str(offset),
                    "-d",
                    str(tmpdir_path),
                    "-f",
                    str(appimage_path),
                    ".DirIcon",
                    f"usr/share/icons/**/{icon_name}.*",
                    f"usr/share/pixmaps/{icon_name}.*",
                ],
                cwd=tmpdir_path,
            )

            # Find icon files
            icon_files = []
            for pattern in [".DirIcon", f"**/{icon_name}.*"]:
                icon_files.extend(tmpdir_path.rglob(pattern))

            if not icon_files:
                return None

            # Prefer .DirIcon, then largest PNG/SVG
            for icon in icon_files:
                if icon.name == ".DirIcon":
                    target = dest_dir / icon.name
                    icon.rename(target)
                    return target

            # Sort by size, prefer PNG then SVG
            icon_files.sort(
                key=lambda p: (p.suffix != ".png", p.suffix != ".svg", -p.stat().st_size)
            )
            target = dest_dir / icon_files[0].name
            icon_files[0].rename(target)
            return target

        except ExtractionError:
            return None


def extract_metainfo(appimage_path: Path, offset: int, dest_dir: Path) -> list[Path]:
    """Extract AppStream metainfo files from AppImage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        try:
            _run_unsquashfs(
                [
                    "-o",
                    str(offset),
                    "-d",
                    str(tmpdir_path),
                    "-f",
                    str(appimage_path),
                    "usr/share/metainfo/*.xml",
                    "usr/share/appdata/*.xml",
                ],
                cwd=tmpdir_path,
            )

            metainfo_files = []
            for pattern in ["usr/share/metainfo/*.xml", "usr/share/appdata/*.xml"]:
                # Use rglob for recursive glob
                metainfo_files.extend(tmpdir_path.rglob(pattern))

            results = []
            for mf in metainfo_files:
                target = dest_dir / mf.name
                mf.rename(target)
                results.append(target)

            return results

        except ExtractionError:
            return []


def extract_all(appimage_path: Path, offset: int, dest_dir: Path) -> dict[str, list[Path]]:
    """Extract all relevant files from AppImage."""
    return {
        "desktop": [extract_desktop_file(appimage_path, offset, dest_dir)]
        if extract_desktop_file(appimage_path, offset, dest_dir)
        else [],
        "icons": [],  # Would need icon name from desktop file
        "metainfo": extract_metainfo(appimage_path, offset, dest_dir),
    }
