"""Desktop entry and icon integration for AppImage Launcher."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from appimage_launcher.config import get_config
from appimage_launcher.constants import (
    CACHE_REFRESH_COALESCE_SECONDS,
    DESKTOP_ENTRY_PREFIX,
    DESKTOP_ENTRY_SUFFIX,
    ICON_PREFIX,
)
from appimage_launcher.logging import get_logger
from appimage_launcher.paths import get_paths

logger = get_logger(__name__)

# Rate limiting for cache refresh
_last_cache_refresh = 0.0


def escape_desktop_value(value: str) -> str:
    """Escape a value for use in a desktop entry."""
    # Per Desktop Entry spec: escape backslash, semicolon, comma, equals, newline
    result = value.replace("\\", "\\\\")
    result = result.replace(";", "\\;")
    result = result.replace(",", "\\,")
    result = result.replace("=", "\\=")
    result = result.replace("\n", "\\n")
    result = result.replace("\r", "\\r")
    return result


def _get_pinned_exec_wrapper(exec_path: Path, applications_dir: Path) -> str | None:
    """Get the pinned mode exec wrapper command.

    Returns the bwrap command if available and user namespaces work,
    otherwise returns None to indicate hardlink fallback should be used.
    """
    # Check if bwrap is available
    bwrap_path = shutil.which("bwrap")
    if not bwrap_path:
        return None

    # Check if unprivileged user namespaces are enabled
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone") as f:
            val = f.read().strip()
        if val != "1":
            return None
    except FileNotFoundError:
        return None

    # Return bwrap command
    return f"{bwrap_path} --dev-bind / / --ro-bind {applications_dir} {applications_dir} -- {exec_path} %U"


def write_desktop_entry(
    app_id: str,
    display_name: str,
    exec_path: Path,
    icon_name: str,
    categories: list[str],
    comment: str | None,
    startup_wm_class: str | None,
    version: str | None,
    name_origin: str,
    update_policy: str,
    catalog_id: str | None = None,
    origin_source: str | None = None,
    role: str = "primary",
    version_slug: str | None = None,
) -> Path:
    """Write a desktop entry file atomically."""
    paths = get_paths()
    _ = get_config()

    # Determine entry name
    if role == "kept" and version_slug:
        entry_name = f"{DESKTOP_ENTRY_PREFIX}{app_id}-{version_slug}{DESKTOP_ENTRY_SUFFIX}"
    else:
        entry_name = f"{DESKTOP_ENTRY_PREFIX}{app_id}{DESKTOP_ENTRY_SUFFIX}"

    entry_path = paths.applications_data_dir / entry_name
    entry_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine menu name
    # config.integration doesn't exist, so just use display_name
    menu_name = display_name

    if role == "kept" and version_slug:
        menu_name = f"{menu_name} ({version_slug})"

    # Build exec command - use pinned wrapper if policy is pinned
    exec_cmd = str(exec_path)
    if update_policy == "pinned":
        wrapper = _get_pinned_exec_wrapper(exec_path, paths.applications_dir)
        if wrapper:
            exec_cmd = wrapper
        else:
            # Fallback: hardlink approach - we'll create a hardlink in .previous
            # and use that as the exec path
            pass  # The hardlink creation is handled elsewhere

    # Build desktop entry content
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={escape_desktop_value(menu_name)}",
        f"Exec={escape_desktop_value(exec_cmd)} %U",
        f"TryExec={escape_desktop_value(str(exec_path))}",
        f"Icon={icon_name}",
    ]

    if categories:
        lines.append(f"Categories={';'.join(categories)};")

    if comment:
        lines.append(f"Comment={escape_desktop_value(comment)}")

    if startup_wm_class:
        lines.append(f"StartupWMClass={escape_desktop_value(startup_wm_class)}")

    # AppImage-specific keys
    lines.append("X-AppImage-Managed=true")
    lines.append(f"X-AppImage-Id={app_id}")
    if version:
        lines.append(f"X-AppImage-Version={escape_desktop_value(version)}")
    lines.append(f"X-AppImage-NameOrigin={name_origin}")
    lines.append(f"X-AppImage-UpdatePolicy={update_policy}")

    if catalog_id:
        lines.append(f"X-AppImage-CatalogId={catalog_id}")
    if origin_source:
        lines.append(f"X-AppImage-OriginSource={origin_source}")

    content = "\n".join(lines) + "\n"

    # Write atomically
    temp_path = entry_path.with_suffix(".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.rename(entry_path)

    logger.debug("Wrote desktop entry", path=str(entry_path))
    return entry_path


def remove_desktop_entry(app_id: str, version_slug: str | None = None) -> bool:
    """Remove desktop entry for an app."""
    paths = get_paths()

    if version_slug:
        entry_name = f"{DESKTOP_ENTRY_PREFIX}{app_id}-{version_slug}{DESKTOP_ENTRY_SUFFIX}"
    else:
        entry_name = f"{DESKTOP_ENTRY_PREFIX}{app_id}{DESKTOP_ENTRY_SUFFIX}"

    entry_path = paths.applications_data_dir / entry_name
    if entry_path.exists():
        entry_path.unlink()
        logger.debug("Removed desktop entry", path=str(entry_path))
        return True
    return False


def install_icons(
    app_id: str,
    icon_bytes: bytes,
    icon_ext: str,
    size: int | str = "scalable",
) -> Path | None:
    """Install icon to hicolor theme."""
    paths = get_paths()

    if size == "scalable":
        size_dir = "scalable"
    else:
        size_dir = f"{size}x{size}"

    icon_dir = paths.icons_data_dir / size_dir / "apps"
    icon_dir.mkdir(parents=True, exist_ok=True)

    icon_name = f"{ICON_PREFIX}{app_id}.{icon_ext}"
    icon_path = icon_dir / icon_name

    # Remove old icons for this app (different extensions/sizes)
    for old_icon in paths.icons_data_dir.rglob(f"{ICON_PREFIX}{app_id}.*"):
        try:
            old_icon.unlink()
        except Exception:
            pass

    # Write new icon
    icon_path.write_bytes(icon_bytes)
    logger.debug("Installed icon", path=str(icon_path))
    return icon_path


def remove_icons(app_id: str) -> int:
    """Remove all icons for an app."""
    paths = get_paths()
    count = 0
    for icon in paths.icons_data_dir.rglob(f"{ICON_PREFIX}{app_id}.*"):
        try:
            icon.unlink()
            count += 1
        except Exception:
            pass
    if count:
        logger.debug("Removed icons", app_id=app_id, count=count)
    return count


def refresh_caches(force: bool = False) -> None:
    """Refresh desktop database and icon cache (rate-limited)."""
    global _last_cache_refresh

    now = time.time()
    if not force and now - _last_cache_refresh < CACHE_REFRESH_COALESCE_SECONDS:
        return

    _last_cache_refresh = now
    paths = get_paths()

    # update-desktop-database
    try:
        subprocess.run(
            ["update-desktop-database", str(paths.applications_data_dir)],
            timeout=10,
            capture_output=True,
        )
    except Exception as e:
        logger.debug("update-desktop-database failed", error=str(e))

    # gtk-update-icon-cache
    try:
        subprocess.run(
            ["gtk-update-icon-cache", "-t", str(paths.icons_data_dir.parent)],
            timeout=10,
            capture_output=True,
        )
    except Exception as e:
        logger.debug("gtk-update-icon-cache failed", error=str(e))

    # kbuildsycoca6 (non-fatal)
    try:
        subprocess.run(
            ["kbuildsycoca6"],
            timeout=15,
            capture_output=True,
        )
    except Exception as e:
        logger.debug("kbuildsycoca6 failed", error=str(e))

    logger.debug("Refreshed caches")


def ensure_executable(path: Path) -> None:
    """Ensure file is executable by owner."""
    try:
        current = path.stat().st_mode
        if not (current & 0o100):
            path.chmod(current | 0o100)
            logger.debug("Made executable", path=str(path))
    except Exception as e:
        logger.warning("Failed to make executable", path=str(path), error=str(e))


def desktop_entry_exists(
    app_id: str, role: str = "primary", version_slug: str | None = None
) -> bool:
    """Check if desktop entry exists for an app."""
    paths = get_paths()

    if role == "kept" and version_slug:
        entry_name = f"{DESKTOP_ENTRY_PREFIX}{app_id}-{version_slug}{DESKTOP_ENTRY_SUFFIX}"
    else:
        entry_name = f"{DESKTOP_ENTRY_PREFIX}{app_id}{DESKTOP_ENTRY_SUFFIX}"

    entry_path = paths.applications_data_dir / entry_name
    return entry_path.exists()


def icon_exists(app_id: str) -> bool:
    """Check if any icon exists for an app."""
    paths = get_paths()
    for _icon in paths.icons_data_dir.rglob(f"{ICON_PREFIX}{app_id}.*"):
        return True
    return False
