"""Shared constants for AppImage Launcher."""

# D-Bus service identification
DBUS_SERVICE_NAME = "io.github.appimage_launcher.Manager"
DBUS_OBJECT_PATH = "/io/github/appimage_launcher/Manager"
DBUS_INTERFACE_NAME = "io.github.appimage_launcher.Manager1"

# API versioning
API_VERSION = 1

# Capabilities (additive only within API v1)
CAPABILITIES = [
    "inspect",
    "origin",
    "install-options-v1",
    "conflict-error",
    "remove-file-action",
    "list-hash",
]

# Default paths (can be overridden by config)
DEFAULT_APPLICATIONS_DIR = "~/Applications"
DEFAULT_CONFIG_DIR = "~/.config/appimage-launcher"
DEFAULT_STATE_DIR = "~/.local/state/appimage-launcher"
DEFAULT_DATA_DIR = "~/.local/share"

# File names
CONFIG_FILE_NAME = "config.toml"
STATE_DB_FILE_NAME = "state.db"
LOCK_FILE_NAME = "appimage-launcher.lock"

# Directories within applications dir
PREVIOUS_DIR_NAME = ".previous"
STAGING_DIR_NAME = ".staging"
DISABLED_DIR_NAME = ".disabled"

# Desktop entry prefix
DESKTOP_ENTRY_PREFIX = "appimage-"
DESKTOP_ENTRY_SUFFIX = ".desktop"

# Icon prefix
ICON_PREFIX = "appimage-"

# Notification
NOTIFICATION_APP_NAME = "appimage-launcher"
NOTIFICATION_DESKTOP_ENTRY = "appimage-launcher"

# Inotify events to watch
INOTIFY_EVENTS = "CLOSE_WRITE|MOVED_FROM|MOVED_TO|DELETE|ATTRIB"

# Ignored file patterns
IGNORED_PATTERNS = [
    ".*",  # hidden files
    "*.part",  # partial downloads
    "*.crdownload",  # Chrome downloads
    "*.partial",  # partial downloads
    "*.tmp",  # temporary files
    "*.download",  # Firefox downloads
    "*~",  # backup files
    "*.zs-old",  # zsync old files
    "*.disabled",  # disabled apps
]

# Ignored directory names
IGNORED_DIRS = [PREVIOUS_DIR_NAME, STAGING_DIR_NAME, DISABLED_DIR_NAME]

# Debounce and timing
DEBOUNCE_MS = 500
EXPECTED_EVENT_TTL_SECONDS = 3
RENAME_GRACE_SECONDS = 2
SELF_UPDATE_WINDOW_SECONDS = 30
STABILITY_WINDOW_SECONDS = 20
AFTER_QUIT_CAP_MINUTES = 10
NOTIFICATION_THROTTLE_SECONDS = 5
CACHE_REFRESH_COALESCE_SECONDS = 5

# Subprocess timeouts
SUBPROCESS_TIMEOUT_SECONDS = 15
UNSQUASHFS_TIMEOUT_SECONDS = 15

# SHA-256 cache key components
HASH_CACHE_KEY_FIELDS = ("device", "inode", "mtime_ns", "size")

# Database
DB_USER_VERSION = 2
EVENTS_TABLE_MAX_ROWS = 1000
TOMBSTONE_RETENTION_DAYS = 30
UNDO_RETENTION_DAYS = 7

# AppImage magic bytes
APPIMAGE_MAGIC_TYPE2 = b"AI\x02"
APPIMAGE_MAGIC_TYPE1 = b"AI\x01"
# AppImage magic at offset 8 (2 bytes for type detection)
APPIMAGE_MAGIC_AT_OFFSET_8 = b"AI"
ELF_MAGIC = b"\x7fELF"

# Squashfs magic
SQUASHFS_MAGIC = b"hsqs"

# ELF header offsets (64-bit)
ELF64_E_SHOFF = 0x28  # section header table offset
ELF64_E_SHENTSIZE = 0x3A  # section header entry size
ELF64_E_SHNUM = 0x3C  # number of section header entries

# Maximum managed name length (UTF-8 bytes)
MAX_MANAGED_NAME_BYTES = 100

# Reserved suffixes that managed names must not end with
RESERVED_SUFFIXES = [
    ".disabled",
    ".part",
    ".crdownload",
    ".zs-old",
]

# Maximum origin JSON size
MAX_ORIGIN_JSON_BYTES = 4096

# Maximum icon size for notification (base64)
MAX_ICON_BASE64_BYTES = 512 * 1024  # 512 KiB

# Default update check interval
DEFAULT_CHECK_INTERVAL = "1d"

# Previous versions to keep
DEFAULT_PREVIOUS_VERSIONS_KEEP = 1
