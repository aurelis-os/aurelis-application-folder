"""Desktop file and AppStream parsing for AppImage Launcher."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from appimage_launcher.logging import get_logger

logger = get_logger(__name__)


def parse_desktop_file(path: Path) -> dict[str, str]:
    """Parse a .desktop file and return key-value pairs.

    Only parses the [Desktop Entry] section.
    Ignores localized keys (e.g., Name[de], Comment[fr]).
    Returns empty dict if file doesn't exist or can't be read.
    """
    result = {}
    current_section = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1]
                    continue
                if current_section != "Desktop Entry":
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    # Skip localized keys (e.g., Name[de], Comment[fr])
                    if "[" in key and "]" in key:
                        continue
                    result[key] = value.strip()
    except (OSError, UnicodeDecodeError):
        # Return empty dict for missing/unreadable files
        pass
    return result


def parse_appstream(path: Path) -> dict[str, str]:
    """Parse AppStream metainfo XML for additional metadata."""
    result = {}
    try:
        tree = ET.parse(path)
        root = tree.getroot()

        # Handle namespaces
        ns = {}
        if "}" in root.tag:
            ns[""] = root.tag.split("}")[0].strip("{")

        # Get component id (from root attribute or child element)
        comp_id = root.get("id")
        if not comp_id:
            id_elem = root.find(".//id", namespaces=ns)
            if id_elem is not None:
                comp_id = id_elem.text
        if comp_id:
            result["app_id"] = comp_id.strip()

        # Get name
        name_elem = root.find(".//name", namespaces=ns)
        if name_elem is not None and name_elem.text:
            result["name"] = name_elem.text.strip()

        # Get version from latest release
        releases = root.findall(".//release", namespaces=ns)
        if releases:
            # Get the last release's version attribute
            last_release = releases[-1]
            version = last_release.get("version")
            if version:
                result["version"] = version.strip()

        # Get summary/description
        summary_elem = root.find(".//summary", namespaces=ns)
        if summary_elem is not None and summary_elem.text:
            result["comment"] = summary_elem.text.strip()

    except Exception as e:
        logger.debug("Failed to parse AppStream", path=str(path), error=str(e))

    return result
