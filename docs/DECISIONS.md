# Design Decisions

This document records significant design decisions made during development, especially where the specification was ambiguous or multiple valid approaches existed.

## Decision Format

Each decision entry includes:
- **Date**: When the decision was made
- **Context**: The situation requiring a decision
- **Options Considered**: Alternative approaches
- **Decision**: What was chosen
- **Rationale**: Why this option was selected
- **Impact**: Consequences for the codebase

---

## 2024-11-14: D-Bus Service Name

**Context**: The spec uses `io.github.appimage_launcher.Manager` as a placeholder for the D-Bus service name (section 8, risk #9). Other repositories (Aurelis Software Center) depend on this name.

**Options Considered**:
1. Keep `io.github.appimage_launcher` (GitHub-based)
2. Use `org.aurelis.appimage_launcher` (project-specific domain)
3. Use `com.github.appimage_launcher` (reverse GitHub)

**Decision**: Use `io.github.appimage_launcher` as specified in the placeholder.

**Rationale**: The spec explicitly states this is a placeholder and the final name must be decided before Phase 3. Since this is the initial implementation and no external consumers exist yet, we'll use the placeholder and document that it should be changed if the project moves to a different hosting/organization.

**Impact**: The constant `DBUS_SERVICE_NAME` in `constants.py` uses this value. All D-Bus interfaces, service files, and introspection XML reference this name.

---

## 2024-11-14: Python Package Structure

**Context**: The spec defines the repository layout in section 4.2 with `src/appimage_launcher/` as the package root.

**Options Considered**:
1. Flat structure at repository root
2. `src/` layout (as specified)
3. `appimage_launcher/` at root

**Decision**: Use `src/appimage_launcher/` layout as specified.

**Rationale**: The spec explicitly defines this layout. The `src/` layout is a best practice for Python packaging (avoids import issues during development, works better with `pip install -e`).

**Impact**: `pyproject.toml` configured with `tool.setuptools.packages.find` pointing to `src/`. All imports use `appimage_launcher.*`.

---

## 2024-11-14: Configuration Layering

**Context**: Section 5.1 defines config with system → user → env layering. Section 4.3 mentions `APPIMAGE_LAUNCHER_HOME` env override for dev/test.

**Options Considered**:
1. Simple TOML file read
2. Layered config with explicit precedence
3. Use a config library (pydantic-settings, dynaconf)

**Decision**: Implement custom layered config in `config.py` with precedence: system default → user config → environment variables → `APPIMAGE_LAUNCHER_HOME` redirect.

**Rationale**: The spec explicitly defines the layering. A custom implementation avoids extra dependencies and gives full control over the `APPIMAGE_LAUNCHER_HOME` redirect behavior which is critical for testing and `make run`.

**Impact**: `config.py` handles all config loading. `paths.py` uses config values and `APPIMAGE_LAUNCHER_HOME` to compute all paths.

---

## 2024-11-14: Fake AppImage Builder for Tests

**Context**: Section 11.1 requires `tests/fixtures/build_fake_appimage.py` that builds minimal valid AppImages for testing.

**Options Considered**:
1. Use real AppImages as test fixtures
2. Build minimal AppImages programmatically
3. Mock all AppImage reading

**Decision**: Build minimal valid AppImages programmatically using `mksquashfs` and a minimal ELF stub.

**Rationale**: The spec explicitly requires this approach. Real AppImages are large, vary in structure, and would make tests slow and brittle. Mocking doesn't test the actual parsing code. Building minimal valid AppImages tests the real code paths while being fast and deterministic.

**Impact**: `tests/fixtures/build_fake_appimage.py` creates AppImages with controllable metadata (name, version, app_id, update_info, etc.) for unit and integration tests.

---

## 2024-11-14: Logging Approach

**Context**: Section 11.3 requires structured logging, one line per event, key=value format, levels via `-v`, journald integration.

**Options Considered**:
1. Standard library `logging` with custom formatter
2. `structlog` library
3. Custom lightweight implementation

**Decision**: Use standard library `logging` with a custom `StructuredFormatter` that outputs `key=value` format.

**Rationale**: No extra dependencies. Standard library `logging` integrates with systemd/journald automatically when running as a service. Custom formatter is simple and meets the spec requirements.

**Impact**: `logging.py` provides `setup_logging()` and `get_logger()` functions. All modules use `get_logger(__name__)`.

---

## 2024-11-14: Path Resolution and APPIMAGE_LAUNCHER_HOME

**Context**: Section 4.3 defines per-user paths and the `APPIMAGE_LAUNCHER_HOME` env override for dev/test.

**Options Considered**:
1. Hardcode paths with `~` expansion
2. Compute all paths dynamically from config + env
3. Use `pathlib` with a `Paths` class

**Decision**: Use a `Paths` class in `paths.py` that computes all paths from config and `APPIMAGE_LAUNCHER_HOME`.

**Rationale**: The spec defines specific paths that depend on config (`applications_dir`) and the dev override. A centralized `Paths` class ensures consistency and makes testing easy (just set the env var).

**Impact**: All modules import `get_paths()` from `paths.py` to get a `Paths` instance with all resolved paths as `Path` objects.

---

## 2024-11-14: Constants Module

**Context**: The spec references several constants (D-Bus service name, object path, interface name, API version, etc.) in multiple places.

**Options Considered**:
1. Scatter constants across modules
2. Central `constants.py` module
3. Use a config file for constants

**Decision**: Central `constants.py` module with all shared constants.

**Rationale**: Single source of truth for D-Bus names, API version, default paths, etc. Makes it easy to change the D-Bus service name in one place (per risk #9 in spec).

**Impact**: All modules import from `appimage_launcher.constants`.

---

## 2026-11-16: Stored Conflict Preferences Scope

**Context**: The `conflict_preferences` config section stores user decisions ("replace" or "keep") for specific app_ids when a conflict occurs during installation with `conflict="ask"` or when the config's `install.conflict_policy` is "ask". The question arose whether these stored preferences should also apply when a caller explicitly passes `conflict="error"`.

**Options Considered**:
1. Apply stored preferences for all conflict policies (including explicit "error")
2. Apply stored preferences only for "config"/"ask", never for explicit "error"

**Decision**: Stored preferences apply only for conflict "config" and "ask", never for explicit "error".

**Rationale**: The Software Center (and other front-ends) uses `conflict="error"` to explicitly request a user prompt for every conflict, regardless of past decisions. If stored preferences applied to "error", the Software Center could not guarantee a prompt appears. By restricting stored preferences to "config"/"ask", we ensure:
- `conflict="config"` (default): Uses config's `install.conflict_policy`; if that is "ask", stored preferences apply
- `conflict="ask"`: Explicitly asks, using stored preferences if available
- `conflict="error"`: Always prompts, ignoring any stored preference

**Impact**: `Manager._handle_conflict` in `core/manager.py` only checks `conflict_preferences` when `conflict_policy` is "ask" (after resolving "config" to "ask"). When `conflict_policy` is "error" (explicit or from "ask" with no stored preference), it raises `ConflictPendingError` without consulting preferences.