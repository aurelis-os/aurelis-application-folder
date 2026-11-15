"""Version comparison for AppImage Launcher.

Handles version strings including semantic versions, nightly builds, git hashes, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class VersionRelation(Enum):
    """Result of version comparison."""

    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    SAME = "same"
    UNKNOWN = "unknown"


@dataclass
class ParsedVersion:
    """Parsed version components."""

    major: int = 0
    minor: int = 0
    patch: int = 0
    pre_release: str = ""
    build: str = ""
    raw: str = ""

    def __lt__(self, other: ParsedVersion) -> bool:
        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        if self.patch != other.patch:
            return self.patch < other.patch
        # Pre-release versions are lower than release
        if self.pre_release != other.pre_release:
            if not self.pre_release and other.pre_release:
                return False  # release > pre-release
            if self.pre_release and not other.pre_release:
                return True  # pre-release < release
            return self.pre_release < other.pre_release
        return self.build < other.build

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParsedVersion):
            return NotImplemented
        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.pre_release == other.pre_release
            and self.build == other.build
        )

    def __le__(self, other: ParsedVersion) -> bool:
        return self < other or self == other

    def __gt__(self, other: ParsedVersion) -> bool:
        return not (self <= other)

    def __ge__(self, other: ParsedVersion) -> bool:
        return not (self < other)


def parse_version(version: str | None) -> ParsedVersion:
    """Parse a version string into components.

    Handles:
    - Semantic versions: 1.2.3, 1.2.3-rc1, 1.2.3+build123
    - Date versions: 2024.01.15, 2024.01
    - Simple versions: 1.2, 1
    - Nightly/git: nightly, git-abc123, 1.2.3-nightly
    - Plain strings (fallback)
    """
    if not version:
        return ParsedVersion(raw="")

    raw = version.strip()
    result = ParsedVersion(raw=raw)

    # Try semantic version regex
    # Matches: 1.2.3, 1.2.3-rc1, 1.2.3+build, 1.2.3-rc1+build
    semver_pattern = r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z\-\.]+))?(?:\+([0-9A-Za-z\-\.]+))?$"
    match = re.match(semver_pattern, raw)
    if match:
        result.major = int(match.group(1))
        result.minor = int(match.group(2))
        result.patch = int(match.group(3))
        result.pre_release = match.group(4) or ""
        result.build = match.group(5) or ""
        return result

    # Try two-part version: 1.2, 1.2-rc1
    two_part_pattern = r"^(\d+)\.(\d+)(?:-([0-9A-Za-z\-\.]+))?(?:\+([0-9A-Za-z\-\.]+))?$"
    match = re.match(two_part_pattern, raw)
    if match:
        result.major = int(match.group(1))
        result.minor = int(match.group(2))
        result.pre_release = match.group(3) or ""
        result.build = match.group(4) or ""
        return result

    # Try date-based version: 2024.01.15, 2024.01
    date_pattern = r"^(\d{4})\.(\d{2})(?:\.(\d{2}))?$"
    match = re.match(date_pattern, raw)
    if match:
        result.major = int(match.group(1))
        result.minor = int(match.group(2))
        result.patch = int(match.group(3)) if match.group(3) else 0
        return result

    # Try single number
    if raw.isdigit():
        result.major = int(raw)
        return result

    # Check for nightly/git patterns
    if "nightly" in raw.lower() or raw.startswith("git-") or re.match(r"^[a-f0-9]{7,}$", raw):
        # Treat as pre-release with special handling
        result.pre_release = raw
        return result

    # Fallback: treat as pre-release string for comparison
    result.pre_release = raw
    return result


def compare_versions(old_version: str | None, new_version: str | None) -> VersionRelation:
    """Compare two version strings.

    Returns:
        UPGRADE if new > old
        DOWNGRADE if new < old
        SAME if new == old
        UNKNOWN if either version is None/empty or unparseable
    """
    if not old_version or not new_version:
        return VersionRelation.UNKNOWN

    old_parsed = parse_version(old_version)
    new_parsed = parse_version(new_version)

    # If both are just pre-release strings (no numeric version), compare lexicographically
    if (
        old_parsed.major == 0
        and old_parsed.minor == 0
        and old_parsed.patch == 0
        and new_parsed.major == 0
        and new_parsed.minor == 0
        and new_parsed.patch == 0
    ):
        if old_parsed.pre_release == new_parsed.pre_release:
            return VersionRelation.SAME
        return (
            VersionRelation.UPGRADE
            if new_parsed.pre_release > old_parsed.pre_release
            else VersionRelation.DOWNGRADE
        )

    if new_parsed > old_parsed:
        return VersionRelation.UPGRADE
    elif new_parsed < old_parsed:
        return VersionRelation.DOWNGRADE
    else:
        return VersionRelation.SAME


def version_to_string(version: ParsedVersion) -> str:
    """Convert ParsedVersion back to string representation."""
    if version.raw:
        return version.raw

    parts = [str(version.major), str(version.minor), str(version.patch)]
    result = ".".join(parts)
    if version.pre_release:
        result += f"-{version.pre_release}"
    if version.build:
        result += f"+{version.build}"
    return result
