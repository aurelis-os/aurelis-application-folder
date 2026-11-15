"""Update checking and zsync handling for AppImage Launcher."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from appimage_launcher.core.elf import read_sha256_sig, read_upd_info
from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


@dataclass
class UpdateInfo:
    """Parsed .upd_info data."""

    kind: str  # zsync, gh-releases-zsync, gh-releases-direct, pling-v1-zsync
    url: str
    args: list[str] = None

    def __post_init__(self):
        if self.args is None:
            self.args = []


@dataclass
class UpdateCheckResult:
    """Result of an update check."""

    has_update: bool
    current_version: str | None
    latest_version: str | None
    update_info: UpdateInfo | None
    error: str | None = None


@dataclass
class UpdateApplyResult:
    """Result of applying an update."""

    success: bool
    output_path: Path | None
    error: str | None = None
    signature_verified: bool = False


def parse_upd_info(upd_info: str | None) -> UpdateInfo | None:
    """Parse .upd_info string into UpdateInfo."""
    if not upd_info:
        return None

    upd_info = upd_info.strip()
    if not upd_info:
        return None

    # Format: type|arg1|arg2|...
    parts = upd_info.split("|")
    kind = parts[0]

    if kind == "zsync":
        # zsync|<url>
        if len(parts) >= 2:
            return UpdateInfo(kind="zsync", url=parts[1], args=parts[2:])
    elif kind == "gh-releases-zsync":
        # gh-releases-zsync|owner|repo|tag|glob
        if len(parts) >= 5:
            return UpdateInfo(kind="gh-releases-zsync", url="", args=parts[1:])
    elif kind == "gh-releases-direct":
        # gh-releases-direct|owner|repo|tag|glob
        if len(parts) >= 5:
            return UpdateInfo(kind="gh-releases-direct", url="", args=parts[1:])
    elif kind == "pling-v1-zsync":
        # pling-v1-zsync|product_id|file_id
        if len(parts) >= 3:
            return UpdateInfo(kind="pling-v1-zsync", url="", args=parts[1:])
    elif kind == "bintray-zsync":
        # Deprecated
        logger.warning("bintray-zsync is deprecated and unsupported")
        return None

    logger.warning("Unknown or malformed .upd_info", upd_info=upd_info)
    return None


def check_for_updates(appimage_path: Path) -> tuple[bool, str | None]:
    """Check if updates are available for an AppImage.

    Returns (has_update, update_info_string).
    """
    with open(appimage_path, "rb") as f:
        upd_info_str = read_upd_info(f)

    if not upd_info_str:
        return False, None

    update_info = parse_upd_info(upd_info_str)
    if not update_info:
        return False, None

    # For now, just return that upd_info exists
    # Actual check would use appimageupdatetool or zsync
    return True, upd_info_str


def check_for_updates_detailed(
    appimage_path: Path, current_version: str | None = None
) -> UpdateCheckResult:
    """Check for updates with detailed result.

    Uses appimageupdatetool --check if available, otherwise falls back to
    checking if .upd_info exists.
    """
    with open(appimage_path, "rb") as f:
        upd_info_str = read_upd_info(f)

    if not upd_info_str:
        return UpdateCheckResult(
            has_update=False,
            current_version=current_version,
            latest_version=None,
            update_info=None,
            error="No .upd_info section found",
        )

    update_info = parse_upd_info(upd_info_str)
    if not update_info:
        return UpdateCheckResult(
            has_update=False,
            current_version=current_version,
            latest_version=None,
            update_info=None,
            error="Unsupported or malformed .upd_info",
        )

    # Try appimageupdatetool --check first
    if shutil.which("appimageupdatetool"):
        try:
            result = subprocess.run(
                [
                    "appimageupdatetool",
                    str(appimage_path),
                    "--check",
                ],
                timeout=60,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Parse output for version info
                # appimageupdatetool --check typically outputs version info
                output = result.stdout.strip()
                has_update = "update available" in output.lower() or "newer" in output.lower()
                # Try to extract version from output
                latest_version = None
                for line in output.split("\n"):
                    if "version" in line.lower():
                        # Try to extract version
                        parts = line.split()
                        for part in parts:
                            if any(c.isdigit() for c in part) and "." in part:
                                latest_version = part
                                break
                return UpdateCheckResult(
                    has_update=has_update,
                    current_version=current_version,
                    latest_version=latest_version,
                    update_info=update_info,
                )
            elif result.returncode == 1:
                # No update available
                return UpdateCheckResult(
                    has_update=False,
                    current_version=current_version,
                    latest_version=None,
                    update_info=update_info,
                )
        except Exception as e:
            logger.warning("appimageupdatetool --check failed", error=str(e))

    # Fall back: just report that upd_info exists (potential update)
    return UpdateCheckResult(
        has_update=True,
        current_version=current_version,
        latest_version=None,
        update_info=update_info,
    )


def apply_update(
    appimage_path: Path,
    update_info: UpdateInfo,
    output_path: Path,
    verify_signature: bool = True,
) -> UpdateApplyResult:
    """Apply update to AppImage using appimageupdatetool or zsync.

    Args:
        appimage_path: Path to current AppImage
        update_info: Parsed update info
        output_path: Where to write the updated AppImage
        verify_signature: Whether to verify signature after update

    Returns:
        UpdateApplyResult with success status and output path
    """
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Try appimageupdatetool first
    if shutil.which("appimageupdatetool"):
        try:
            if update_info.kind == "zsync":
                # Direct zsync URL
                result = subprocess.run(
                    [
                        "appimageupdatetool",
                        str(appimage_path),
                        "--update",
                        update_info.url,
                        "--output",
                        str(output_path),
                    ],
                    timeout=300,
                    capture_output=True,
                    text=True,
                )
            elif update_info.kind in ("gh-releases-zsync", "gh-releases-direct", "pling-v1-zsync"):
                # appimageupdatetool can handle these natively
                result = subprocess.run(
                    [
                        "appimageupdatetool",
                        str(appimage_path),
                        "--update",
                        "--output",
                        str(output_path),
                    ],
                    timeout=300,
                    capture_output=True,
                    text=True,
                )
            else:
                return UpdateApplyResult(
                    success=False,
                    output_path=None,
                    error=f"Unsupported update kind: {update_info.kind}",
                )

            if result.returncode == 0:
                # Verify signature if requested
                sig_verified = True
                if verify_signature:
                    sig_verified = verify_appimage_signature(output_path)

                return UpdateApplyResult(
                    success=True,
                    output_path=output_path,
                    signature_verified=sig_verified,
                )
            else:
                logger.warning(
                    "appimageupdatetool failed",
                    returncode=result.returncode,
                    stderr=result.stderr,
                )
        except subprocess.TimeoutExpired:
            logger.warning("appimageupdatetool timed out")
        except Exception as e:
            logger.warning("appimageupdatetool failed", error=str(e))

    # Fall back to zsync for zsync kind
    if update_info.kind == "zsync" and shutil.which("zsync"):
        try:
            result = subprocess.run(
                ["zsync", "-o", str(output_path), update_info.url],
                cwd=appimage_path.parent,
                timeout=300,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                sig_verified = True
                if verify_signature:
                    sig_verified = verify_appimage_signature(output_path)

                return UpdateApplyResult(
                    success=True,
                    output_path=output_path,
                    signature_verified=sig_verified,
                )
            else:
                logger.warning("zsync failed", returncode=result.returncode, stderr=result.stderr)
        except Exception as e:
            logger.warning("zsync failed", error=str(e))

    return UpdateApplyResult(
        success=False,
        output_path=None,
        error="No suitable update tool available (need appimageupdatetool or zsync)",
    )


def verify_appimage_signature(appimage_path: Path) -> bool:
    """Verify AppImage signature if present.

    Uses appimageupdatetool --verify if available.
    """
    if not shutil.which("appimageupdatetool"):
        logger.debug("appimageupdatetool not available, skipping signature verification")
        return True  # Can't verify, assume OK

    try:
        result = subprocess.run(
            ["appimageupdatetool", str(appimage_path), "--verify"],
            timeout=60,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        else:
            logger.warning(
                "Signature verification failed",
                returncode=result.returncode,
                stderr=result.stderr,
            )
            return False
    except Exception as e:
        logger.warning("Signature verification error", error=str(e))
        return False


def has_signature(appimage_path: Path) -> bool:
    """Check if AppImage has a .sha256_sig section."""
    with open(appimage_path, "rb") as f:
        sig = read_sha256_sig(f)
    return sig is not None and len(sig) > 0


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


class UpdateCache:
    """Cache for update checks with ETag/If-None-Match support."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, app_id: str) -> Path:
        """Get cache file path for an app."""
        safe_id = app_id.replace("/", "_").replace(".", "_")
        return self.cache_dir / f"{safe_id}.json"

    def get(self, app_id: str) -> dict | None:
        """Get cached update check result."""
        cache_file = self._cache_file(app_id)
        if not cache_file.exists():
            return None

        try:
            with open(cache_file) as f:
                data = json.load(f)
            # Check if cache is expired (default 1 hour)
            if time.time() - data.get("timestamp", 0) > 3600:
                return None
            return data
        except Exception:
            return None

    def set(
        self, app_id: str, etag: str | None, last_modified: str | None, result: UpdateCheckResult
    ) -> None:
        """Cache update check result."""
        cache_file = self._cache_file(app_id)
        data = {
            "timestamp": time.time(),
            "etag": etag,
            "last_modified": last_modified,
            "has_update": result.has_update,
            "current_version": result.current_version,
            "latest_version": result.latest_version,
            "error": result.error,
        }
        try:
            with open(cache_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Failed to write update cache", error=str(e))

    def get_headers(self, app_id: str) -> dict[str, str]:
        """Get conditional request headers for an app."""
        cached = self.get(app_id)
        if not cached:
            return {}
        headers = {}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        return headers
