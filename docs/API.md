# AppImage Launcher D-Bus API (Manager1)

**Interface:** `io.github.appimage_launcher.Manager1`  
**Object Path:** `/io/github/appimage_launcher/Manager`  
**Service Name:** `io.github.appimage_launcher.Manager`  
**Bus:** Session bus  
**API Version:** 1  

This document describes the public D-Bus API exposed by the AppImage Launcher daemon. The API is stable within v1; only additive changes are allowed. The authoritative description is the published introspection XML at `packaging/dbus/io.github.appimage_launcher.Manager1.xml`.

---

## Versioning and Capabilities

| Property | Type | Description |
|----------|------|-------------|
| `APIVersion` | `u` (uint32) | Always `1` for this interface. |
| `Capabilities` | `as` (array of strings) | Optional features available. See [Capabilities](#capabilities). |

### Capabilities

| Capability | Description |
|------------|-------------|
| `inspect` | `Inspect` method is available. |
| `origin` | `origin` option in `Install`, `SetOrigin` method, and `origin` field in app objects. |
| `install-options-v1` | Full `Install` options (`source_action`, `conflict`, `origin`, `silent`, `name`, `update_policy`). |
| `conflict-error` | `conflict=error` option in `Install` and `ConflictPending` signal. |
| `remove-file-action` | `file_action` option in `Remove` (`trash`, `keep`, `purge`). |
| `list-hash` | `hash` option in `List` to compute missing SHA-256 values. |

Clients **must** check `APIVersion >= 1` and the capabilities they need at startup, and disable dependent features with an explanation if one is missing.

---

## Name Resolution

All methods taking a `name` argument resolve it as follows:

1. Exact match on `managed_name` (case-insensitive).
2. Unique case-insensitive prefix of `managed_name`.
3. Exact match on `app_id`.
4. Unique case-insensitive prefix of `app_id`.

If multiple apps match, the method raises `Ambiguous` with the list of candidates in the error message.

---

## App Object

Returned by `List`, `Info`, `Install`, and `SetOrigin`.

```json
{
  "app_id": "org.kde.kate",
  "name": "Kate",
  "managed_name": "Kate",
  "name_origin": "auto",
  "role": "primary",
  "version": "26.08.2-1",
  "path": "/home/user/Applications/Kate",
  "size": 70136910,
  "sha256": null,
  "update_policy": "auto",
  "self_managed": false,
  "has_update_info": true,
  "origin": null,
  "desktop_entry": "/home/user/.local/share/applications/appimage-org.kde.kate.desktop",
  "installed_at": 1731456000,
  "updated_at": 1731456000,
  "running": false,
  "previous": [
    {"version": "26.08.1-1", "size": 70010000, "created_at": 1730000000}
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `app_id` | string | Stable identity across versions. |
| `name` | string | Embedded `Name=` from the AppImage. |
| `managed_name` | string | Current file name in `~/Applications` (no extension). |
| `name_origin` | string | `auto` (tool chose) or `user` (user renamed; never changed again). |
| `role` | string | `primary` (active) or `kept` (older version kept side-by-side). |
| `version` | string | Version from AppImage metadata. |
| `path` | string | Absolute path to the AppImage file. |
| `size` | integer | File size in bytes. |
| `sha256` | string or null | Full-file SHA-256. `null` until computed (cached per `(device, inode, mtime_ns, size)`). |
| `update_policy` | string | `auto`, `manual`, or `pinned`. |
| `self_managed` | boolean | App replaces itself (detected on first self-update). |
| `has_update_info` | boolean | Whether `.upd_info` is present and non-empty. |
| `origin` | object or null | Provenance record (see [Origin Record](#origin-record)). |
| `desktop_entry` | string | Path to the generated `.desktop` file. |
| `installed_at` | integer | Unix timestamp of installation. |
| `updated_at` | integer | Unix timestamp of last update. |
| `running` | boolean | Whether the app is currently running (best effort). |
| `previous` | array | Previous versions in `.previous/` with `version`, `size`, `created_at`. |

### Origin Record

Optional provenance supplied by a front-end (e.g., the Aurelis Software Center).

```json
{
  "source": "aurelis-catalog",
  "catalog_id": "org.kde.kate",
  "version": "26.08.2-1",
  "sha256": "64-hex-chars",
  "changed_at": "2026-11-01T18:20:58Z",
  "url": "https://github.com/..."
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `source` | Yes | String, max 64 chars (e.g., `aurelis-catalog`). |
| `catalog_id` | Yes | String, max 200 chars (e.g., `org.kde.kate`). |
| `version` | No | Catalog version string. |
| `sha256` | No | Expected SHA-256 of the AppImage. |
| `changed_at` | No | ISO 8601 timestamp. |
| `url` | No | Download/source URL. |

Unknown keys are preserved. Total JSON ≤ 4 KiB. Invalid input raises `InvalidArgument`.

---

## Methods

### `Inspect(path: s) → s`

Reads metadata from an AppImage **without installing or executing it**.

**Arguments:**
- `path` (string): Absolute path to the AppImage file.

**Returns:** JSON string with:

```json
{
  "type": 2,
  "name": "Kate",
  "version": "26.08.2-1",
  "app_id": "org.kde.kate",
  "comment": "Advanced text editor",
  "categories": ["Development", "TextEditor"],
  "icon_png_base64": "<base64 or null, max 512 KiB>",
  "icon_ext": "png",
  "update_info": "gh-releases-zsync|...",
  "has_signature": false,
  "size": 70136910,
  "installed": { "...app object..." },
  "relation": "none|upgrade|downgrade|same"
}
```

- `installed`: The primary app with the same `app_id`, or `null`.
- `relation`: Version comparison between the inspected file and the installed app.

**Errors:** `InvalidAppImage`, `UnsupportedType`, `NotFound` (file doesn't exist).

---

### `Install(path: s, options: a{sv}) → s`

Installs an AppImage from any path. Already inside the Applications dir = adopt in place.

**Arguments:**
- `path` (string): Absolute path to the AppImage file.
- `options` (dict): Installation options.

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `source_action` | `copy` \| `move` | config `install.source_action` | Copy or move a file outside the dir. Use `move` for downloaded cache files. |
| `conflict` | `config` \| `replace` \| `keep` \| `cancel` \| `error` \| `ask` | `config` | Behavior when same `app_id` is already primary. `config` uses `install.conflict_policy`. `ask` uses stored preferences (replace/keep) if available, otherwise raises `ConflictPending`. `error` raises `ConflictPending` with JSON payload (no notification), ignoring stored preferences. |
| `origin` | string (JSON) | none | Origin record (see [Origin Record](#origin-record)). |
| `silent` | boolean | false | Suppress success notification. Failures and `ask` conflicts still notify. |
| `name` | string | derived | Managed-name override; sets `name_origin='user'`. |
| `update_policy` | `auto` \| `manual` \| `pinned` | `auto` | Initial update policy. |

**Returns:** JSON string:

```json
{
  "app": { "...app object..." },
  "changed": true,
  "previous": "/home/user/Applications/.previous/Kate-26.08.1-1.AppImage"
}
```

- `changed`: `false` if the same file (by SHA-256) is already installed (idempotent).
- `previous`: Path to the backed-up previous version, or `null`.

**Errors:** `NotFound`, `NameCollision`, `ConflictPending`, `InvalidAppImage`, `UnsupportedType`, `InvalidArgument`, `Busy`, `Io`.

---

### `Remove(name: s, options: a{sv}) → ()`

Uninstalls an app.

**Arguments:**
- `name` (string): Managed name or app_id.
- `options` (dict): Removal options.

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `file_action` | `trash` \| `keep` \| `purge` | `trash` | What to do with the file. `purge` requires `confirm=true`. |
| `confirm` | boolean | false | Required for `purge`. |
| `silent` | boolean | false | Suppress notification. |

**Errors:** `NotFound`, `Ambiguous`, `InvalidArgument`, `Busy`, `Io`.

---

### `Rename(name: s, new_name: s) → s`

Renames an app; sets `name_origin='user'`.

**Arguments:**
- `name` (string): Current managed name or app_id.
- `new_name` (string): New managed name (no extension).

**Returns:** JSON string of the updated app object.

**Errors:** `NotFound`, `Ambiguous`, `NameCollision`, `InvalidArgument`, `Busy`, `Io`.

---

### `List(options: a{sv}) → s`

Lists installed apps.

**Arguments:**
- `options` (dict): Query options.

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `all` | boolean | false | Include `kept` roles. |
| `hash` | boolean | false | Compute missing SHA-256 values (runs in worker, may take seconds). |

**Returns:** JSON array of app objects.

---

### `Info(name: s) → s`

Gets detailed info about an app. Always includes `sha256` (computed if needed).

**Arguments:**
- `name` (string): Managed name or app_id.

**Returns:** JSON string of a single app object.

**Errors:** `NotFound`, `Ambiguous`.

---

### `SetPolicy(name: s, policy: s) → ()`

Sets the update policy for an app.

**Arguments:**
- `name` (string): Managed name or app_id.
- `policy` (string): `auto`, `manual`, or `pinned`.

**Errors:** `NotFound`, `Ambiguous`, `InvalidArgument`, `Busy`.

---

### `CheckUpdates(names: as) → s`

Checks for updates for specified apps.

**Arguments:**
- `names` (array of strings): Managed names or app_ids.

**Returns:** JSON array of update check results:

```json
[
  {
    "app_id": "org.kde.kate",
    "has_update": false,
    "current_version": "26.08.2-1",
    "latest_version": null,
    "skipped": true,
    "skip_reason": "catalog_linked"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `app_id` | string | App identity. |
| `has_update` | boolean | Whether an update is available. |
| `current_version` | string | Currently installed version. |
| `latest_version` | string or null | Available version if `has_update`. |
| `skipped` | boolean | Whether the check was skipped. |
| `skip_reason` | string or null | Reason if skipped: `catalog_linked`, `not_found`, `self_managed`, `pinned`, `no_update_info`. |

**Note:** Apps with an `origin` are skipped when `update.skip_catalog_linked = true` (default).

---

### `Update(name: s) → s`

Triggers a zsync update for an app. Emits `UpdateProgress` signals.

**Arguments:**
- `name` (string): Managed name or app_id.

**Returns:** JSON string with the update result:

```json
{
  "success": true,
  "output_path": "/path/to/updated.AppImage",
  "error": null,
  "signature_verified": true
}
```

Or on failure:

```json
{
  "success": false,
  "output_path": null,
  "error": "Error description",
  "signature_verified": false
}
```

**Fields:**
- `success` (boolean): Whether the update succeeded.
- `output_path` (string or null): Path to the updated AppImage on success.
- `error` (string or null): Error message on failure.
- `signature_verified` (boolean): Whether the updated AppImage's signature was verified.

**Errors:** `NotFound`, `Ambiguous`, `PolicyForbids` (catalog-linked), `Busy`, `Io`.

---

### `Revert(name: s) → ()`

Restores the newest `.previous` version.

**Arguments:**
- `name` (string): Managed name or app_id.

**Errors:** `NotFound`, `Ambiguous`, `InvalidArgument` (no previous version), `Busy`, `Io`.

---

### `ResolveConflict(id: s, decision: s, remember: b) → ()`

Resolves a pending conflict from `Install` with `conflict=error`.

**Arguments:**
- `id` (string): Conflict ID from `ConflictPending` signal.
- `decision` (string): `replace`, `keep`, or `cancel`.
- `remember` (boolean): Remember this choice for this app (stored in config).

**Errors:** `NotFound` (unknown conflict ID), `InvalidArgument`.

---

### `Reindex() → ()`

Triggers a full reconcile (scan Applications dir, match files to records, fix drift).

**Errors:** `Busy`, `Io`.

---

### `SetOrigin(name: s, origin_json: s) → s`

Sets or clears the origin record for an app.

**Arguments:**
- `name` (string): Managed name or app_id.
- `origin_json` (string): JSON origin record, or empty string to clear.

**Returns:** JSON string of the updated app object.

**Errors:** `NotFound`, `Ambiguous`, `InvalidArgument`, `Busy`.

---

## Signals

### `AppsChanged(kind: s, app_id: s, source: s)`

Emitted when apps change.

| Kind | Description |
|------|-------------|
| `installed` | New app installed. |
| `removed` | App uninstalled. |
| `renamed` | App renamed. |
| `updated` | App updated (self-update or explicit). |
| `reverted` | App reverted to previous version. |
| `origin_changed` | Origin record set or cleared. |
| `policy_changed` | Update policy changed. |
| `reindexed` | Full reindex completed. |

| Source | Description |
|--------|-------------|
| `observer` | File system event (drop, rename, delete). |
| `api` | D-Bus API call. |
| `cli` | CLI direct mode (no daemon). |

Clients **must** ignore unknown `kind` values.

---

### `ConflictPending(id: s, json: s)`

Emitted when `Install` is called with `conflict=error` and a conflict exists.

**Payload (JSON):**

```json
{
  "id": "conflict_1234567890",
  "old_app": { "...app object..." },
  "new_meta": {
    "name": "Kate",
    "version": "26.08.3-1",
    "app_id": "org.kde.kate",
    "size": 71000000
  },
  "relation": "upgrade"
}
```

- `relation`: `upgrade`, `downgrade`, or `same`.

---

### `UpdateProgress(name: s, percent: i, state: s)`

Emitted during `Update` operation.

| State | Description |
|-------|-------------|
| `checking` | Checking for updates. |
| `downloading` | Downloading delta. |
| `verifying` | Verifying signature/checksum. |
| `applying` | Applying update. |
| `done` | Update complete. |
| `error` | Update failed. |

---

## Properties

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `ApplicationsDir` | `s` | read | Absolute path to the watched Applications directory. |
| `Version` | `s` | read | AppImage Launcher version string. |
| `APIVersion` | `u` | read | Always `1`. |
| `Capabilities` | `as` | read | List of capability strings. |

---

## Error Names

All errors use the prefix `io.github.appimage_launcher.Error.`:

| Error | Description |
|-------|-------------|
| `NotFound` | App or file not found. |
| `Ambiguous` | Name matches multiple apps. |
| `NameCollision` | Target name conflicts with a non-AppImage file or different app. |
| `ConflictPending` | Conflict requires resolution (see `ConflictPending` signal). |
| `InvalidAppImage` | File is not a valid AppImage (type 2). |
| `UnsupportedType` | AppImage type 1 (ISO9660) or other unsupported format. |
| `InvalidArgument` | Invalid option value, malformed JSON, missing required fields. |
| `PolicyForbids` | Operation forbidden by policy (e.g., catalog-linked app update). |
| `Busy` | Another mutation holds the lock; retry later. |
| `Io` | I/O error (disk full, permission denied, etc.). |
| `Aborted` | Operation aborted (e.g., user cancelled dialog). |

Error details are included in the D-Bus error message as JSON where noted.

---

## Concurrency

- The daemon serializes all mutations (single writer) using an asyncio lock with a 10-second timeout.
- Reads never block on mutations.
- Slow work (hashing, extraction) runs off the event loop in worker threads.
- A mutation that cannot acquire the lock within 10 s raises `Busy`.

---

## Contract Tests

The repository includes a contract test suite in `tests/contract/` that verifies:

1. The daemon's live `Introspect` output equals the published XML.
2. Every option, error, and JSON shape is exercised through a plain `dbus-fast` reference client on a private bus.
3. `AppsChanged` is emitted with the correct `kind` and `source` for observer-, API-, and CLI-originated changes.

Run with: `make test` (includes contract tests).

---

## Reference Client

A reference `dbus-fast` client script is provided at `scripts/dbus_client.py` (or similar). It demonstrates:

- Inspecting a file
- Installing with `origin` and `conflict=error`
- Resolving the conflict with an explicit retry
- Updating an app by installing a newer file (name preserved, `.previous` created)
- `Revert`
- Setting and clearing an origin
- Removing with `file_action=trash`
- Observing `AppsChanged` with correct `source`

This script doubles as documentation for other repositories (e.g., Aurelis Software Center) implementing front-ends.

---

## Example: Install with Origin and Conflict Resolution

```python
import asyncio
import json
from dbus_fast.aio import MessageBus
from dbus_fast import BusType


async def main():
    bus = await MessageBus(bus_type=BusType.SESSION).connect()

    proxy = bus.get_proxy_object(
        "io.github.appimage_launcher.Manager",
        "/io/github/appimage_launcher/Manager",
        await bus.introspect(
            "io.github.appimage_launcher.Manager", "/io/github/appimage_launcher/Manager"
        ),
    )
    iface = proxy.get_interface("io.github.appimage_launcher.Manager1")

    # Install with origin
    origin = json.dumps(
        {"source": "aurelis-catalog", "catalog_id": "org.kde.kate", "version": "26.08.2-1"}
    )

    result = await iface.call_install("/path/to/Kate.AppImage", {"origin": origin})
    result = json.loads(result)
    print(f"Installed: {result['app']['managed_name']}, changed={result['changed']}")

    # Later, install a newer version with conflict=error
    try:
        await iface.call_install("/path/to/Kate-26.08.3.AppImage", {"conflict": "error"})
    except Exception as e:
        if "ConflictPending" in str(e):
            conflict_id = str(e).split(": ")[1]
            print(f"Conflict: {conflict_id}")
            # Resolve with replace
            await iface.call_resolve_conflict(conflict_id, "replace", False)
            print("Resolved: replaced")


asyncio.run(main())
```

---

## Security

- Session bus only; the daemon runs as the user, never as root.
- The daemon never accepts paths it cannot `stat` as the calling user's files.
- D-Bus activation file installed (`SystemdService=appimage-launcher.service`) so clients can start the daemon on demand.

---

## Changelog

| API Version | Date | Changes |
|-------------|------|---------|
| 1 | 2026-11 | Initial stable release. |

Within v1, only additive changes are allowed. A breaking change means a new interface `Manager2`, served in parallel for at least one release.