# AppImage Launcher: Daemon, CLI and KDE GUI

Status: draft v1.1 | Target: KDE Plasma 6 (KF6 / Qt 6) on systemd-based Linux | Language: Python 3.11+

> **Repository and relationships.** This is its own repository (`appimage-launcher`). It owns the AppImage store (`~/Applications`), the observer daemon, the CLI and the **public D-Bus API (`Manager1`)**. It is useful on its own on any KDE system. The Aurelis Software Center (repository `aurelis-software`) and its install/uninstall wizards are *clients* of the D-Bus API: they never touch `~/Applications` or the state DB directly. The software catalog (repository `aurelis-software-database`) is known to this project only through the optional `origin` record (section 5.3).
>
> **Scope decision:** `~/Applications` contains **AppImage files only**: one file per app, clean names. Flatpak and pacman applications are not represented in this folder.
>
> **Changes in v1.1:** no manager window (that UI lives in the Software Center); new public API surface for front-ends (section 8.1: install options, `origin`, `Inspect`, `SetOrigin`, versioning); new Phase 3B that freezes API v1; update-authority rule for catalog-linked apps (6.9).

---

## 0. How to use this document (instructions for the coding agent)

1. Work **phase by phase** (section 12). Do not start phase N+1 until every acceptance criterion of phase N passes.
2. After every task: run `make lint test`. Before finishing a phase: run `make ci`. All must be green.
3. One commit per task, message format `P<phase>-<task>: <summary>` (e.g. `P1-3: read squashfs offset from ELF`).
4. Never execute an AppImage to read metadata. Metadata is read from the file itself (section 6.1). The only code allowed to execute an AppImage is the explicit `run`/`browse --exec-fallback` paths and the user's own launcher.
5. Never delete or overwrite a user's file before the replacement is verified in place (section 6.7).
6. When the spec is ambiguous, pick the safer, non-destructive option, document it in `docs/DECISIONS.md`, and continue.
7. Everything user-visible has a test. Everything destructive has two.
8. Keep the daemon **headless**: it must never import PyQt6. GUI code lives in a separate process (section 9.3).
9. The D-Bus interface `Manager1` is a **public contract** used by other repositories (section 8.1). Within v1 only additive changes are allowed. Any change to it needs a contract test and an update of `docs/API.md` and the introspection XML in the same commit.
10. Never hash, scan or parse large files on the observer's event path or the D-Bus handler thread: use worker threads and cache results.

---

## 1. Goals and non-goals

### Goals
- Treat `~/Applications` as a drop-in / drop-out folder: dropping an AppImage installs it, removing it uninstalls it.
- Clean file names: `GIMP-8.9.3-34-nightly_x86-64.appimage` becomes `Gimp` (from the embedded `Name=`).
- Handle conflicts (same app, new version) and manual user renames gracefully.
- Recognize self-updating apps and zsync-updatable apps and keep state consistent.
- Provide a CLI, a D-Bus backend, notifications with buttons, and PyQt6 **helper dialogs** for complex flows.
- Expose a **stable, versioned D-Bus API** so other front-ends (the Aurelis Software Center, install/uninstall wizards, scripts) can inspect, install, update, revert and remove apps without touching the folder.
- Let front-ends attach **provenance** (`origin`, section 5.3) to an app, so a catalog can link, update and recognise it.
- Install **system-wide** (`make install`), with a **systemd user service enabled globally** so the observer starts for every user at login.

### Non-goals (v1)
- Installing into system-wide application folders (e.g. `/opt/Applications`) for all users. Each user has their own folder. (Possible v2.)
- AppImage type 1 (ISO9660) support beyond detecting it and reporting "unsupported" cleanly.
- Sandboxing AppImages. Only a best-effort `pinned` mode (section 6.9).
- Non-KDE polish. It should work elsewhere but is only tested on KDE.
- A full application-management window, a catalog or store UI, or downloading apps from a catalog. These belong to the Aurelis Software Center. This project only reacts to files, runs `.upd_info` updates, and serves the API.
- Representing Flatpak or pacman applications in `~/Applications`.

---

## 2. Target environment and dependencies

| Item | Requirement |
|---|---|
| OS | Linux with systemd (user manager), D-Bus session bus |
| Desktop | KDE Plasma 6 (Dolphin, KNotification-compatible notification server) |
| Python | 3.11+ |
| Runtime Python deps | `inotify_simple`, `dbus-fast` (daemon, CLI) ; `PyQt6` (GUI only, optional extra) |
| Runtime tools | `squashfs-tools` (`unsquashfs`), `squashfuse` (browse), optional `appimageupdatetool`, `zsync` |
| Dev deps | `pytest`, `pytest-cov`, `pytest-asyncio`, `ruff`, `mypy`, `pytest-qt` |

Rules:
- Runtime Python deps come from distro packages (`python3-dbus-fast`, `python3-inotify-simple`, `python3-pyqt6`). `make install` must **not** run pip.
- The daemon and CLI must run without PyQt6 installed. `appimage-gui` reports a clear error if PyQt6 is missing.
- Fail soft when optional tools are missing: `appimage doctor` reports them; features depending on them are disabled with an explanation.

---

## 3. Glossary

- **Applications dir**: watched directory, default `~/Applications` (configurable).
- **Managed name**: the file name the tool assigned (no extension), e.g. `Gimp`.
- **name_origin**: `auto` (tool chose the name) or `user` (user renamed it; tool never changes it again).
- **app_id**: stable identity of an application across versions. Resolved in order: AppStream `<id>` from `usr/share/metainfo/*.xml` or `usr/share/appdata/*.xml`; else the stem of the root `.desktop` file; else slug of `Name=`.
- **role**: `primary` (the active install for an app_id) or `kept` (an older version the user chose to keep side by side, e.g. `Gimp 8.9.2`).
- **update_policy**: `auto` | `manual` | `pinned` (section 6.9).
- **self_managed**: app replaces itself (own updater). Set automatically on first observed self-update.
- **Tombstone**: record of a recently uninstalled/removed file kept for 30 days to restore names and correlate self-updates.
- **origin**: optional provenance record attached to an app by a front-end (section 5.3), e.g. "this is catalog entry `org.kde.kate` version `26.08.2-1`". Opaque to the daemon except for `catalog_id` (rule in 6.9).
- **front-end**: any D-Bus client other than the observer itself: CLI, Software Center, wizards, scripts.
- **API caller**: a front-end invoking `Install`/`Remove`/etc. over D-Bus (as opposed to a user dropping a file in the folder).

---

## 4. Architecture

```
                 +--------------------------+
 Dolphin/Shell ->|  appimage (CLI)          |--+
                 +--------------------------+  |  D-Bus (session)         direct lib + flock
 Software Center>|  front-end (D-Bus)       |--+------------------+   (when daemon is down)
 Dolphin menu -->|  service menu -> CLI     |                     v
                 +--------------------------+        +--------------------------+
                                                     |  appimage daemon         |
 inotify  <--- ~/Applications ---------------------->|  observer + core Manager |
 /proc/*/environ (APPIMAGE=) ----------------------->|  notifier (D-Bus)        |
                                                     |  dialog launcher         |
                                                     +------------+-------------+
                                                                  |
                         SQLite state, desktop entries, icons, config (per user)
```

### 4.1 Components
- `core` (library): AppImage reading, naming, identity, state DB, integration (desktop entries/icons), install/remove/rename/update operations. **All mutations go through `core.Manager`.**
- `daemon`: asyncio process. Runs observer, exposes the D-Bus API, sends notifications, launches dialogs.
- `cli`: thin client. Uses the daemon over D-Bus when available; otherwise calls `core.Manager` directly under an exclusive `flock` on `$XDG_RUNTIME_DIR/appimage-launcher.lock`.
- `gui`: PyQt6 process that only shows the **helper dialogs** the daemon asks for (conflict, rename, update, error). No manager window. Talks to the daemon over D-Bus.
- **External front-ends** (other repositories) use only the public `Manager1` API (section 8.1). The daemon is the **single writer** of the Applications dir and the state DB.

### 4.2 Repository layout

```
.
├── Makefile
├── pyproject.toml            # ruff, mypy, pytest config; project metadata
├── SPEC.md
├── README.md
├── docs/
│   ├── DECISIONS.md
│   ├── ARCHITECTURE.md
│   ├── API.md                # public D-Bus contract (Manager1), generated tables + examples
│   └── appimage.1.md         # man page source
├── packaging/
│   ├── systemd/appimage-launcher.service
│   ├── dbus/io.github.appimage_launcher.service
│   ├── dbus/io.github.appimage_launcher.Manager1.xml   # published introspection = the contract
│   ├── kio/appimage-launcher.desktop       # Dolphin service menu
│   ├── applications/appimage-launcher.desktop   # NoDisplay=true, identity for notifications
│   ├── icons/appimage-launcher.svg
│   ├── completions/{appimage.bash,_appimage,appimage.fish}
│   └── config/config.toml                  # system default config
├── src/appimage_launcher/
│   ├── __init__.py  constants.py  config.py  logging.py  paths.py
│   ├── core/
│   │   ├── elf.py            # offset, section reading (.upd_info, .sha256_sig)
│   │   ├── squash.py         # unsquashfs wrapper (desktop, icon, metainfo)
│   │   ├── metadata.py       # AppImageMeta dataclass + parser
│   │   ├── naming.py         # sanitize, templates, collisions
│   │   ├── identity.py       # app_id, fingerprint
│   │   ├── versions.py       # version comparison (up/down/same)
│   │   ├── state.py          # SQLite schema + repository
│   │   ├── integration.py    # .desktop, icons, cache refresh
│   │   ├── manager.py        # install/remove/rename/enable/disable/revert
│   │   ├── updates.py        # upd_info parsing, check, zsync flow
│   │   └── running.py        # /proc scan for APPIMAGE=
│   ├── daemon/
│   │   ├── app.py  observer.py  classifier.py  conflicts.py
│   │   ├── notifier.py  dialogs.py  dbus_service.py  reconcile.py
│   ├── cli/ main.py  commands/*.py  table.py
│   └── gui/ main.py  dialogs/{conflict,rename,update,error}.py     # helper dialogs only
└── tests/
    ├── fixtures/build_fake_appimage.py     # builds tiny valid AppImages for tests
    ├── unit/  integration/  contract/  gui/
```

### 4.3 Paths (per user, XDG compliant)

| Purpose | Path |
|---|---|
| Applications dir | `~/Applications` (config `applications_dir`) |
| Config | `~/.config/appimage-launcher/config.toml` (overrides `/etc/appimage-launcher/config.toml`) |
| State DB | `~/.local/state/appimage-launcher/state.db` |
| Previous versions | `<applications_dir>/.previous/` |
| Staging | `<applications_dir>/.staging/` (same filesystem, keeps renames atomic) |
| Desktop entries | `~/.local/share/applications/appimage-<app_id>.desktop` |
| Icons | `~/.local/share/icons/hicolor/<size>/apps/appimage-<app_id>.<png|svg>` |
| Log | journald (`journalctl --user -u appimage-launcher`) |
| Dev override | env `APPIMAGE_LAUNCHER_HOME=<dir>` redirects config, state, entries and icons (used by `make run` and tests) |

---

## 5. Data model

### 5.1 Config (`config.toml`, all keys optional)

```toml
applications_dir = "~/Applications"

[install]
source_action     = "copy"      # copy | move  (for `appimage install <path>`)
conflict_policy   = "ask"       # ask | replace | keep

[naming]
template      = "{name}"
keep_template = "{name} {version}"      # used when keeping an older version side by side

[update]
on_self_update  = "replace"     # replace | keep | ask
rename_policy   = "after_quit"  # after_quit | never
check_interval  = "1d"          # 0 disables background checks
previous_versions_keep = 1
skip_catalog_linked    = true   # apps with an origin are updated by their front-end, not by the daemon (6.9)

[notifications]
enabled  = true
timeout  = 20                    # seconds; default action on timeout is always non-destructive

[gui]
use_dialogs = true               # false = notifications only
```

### 5.2 SQLite schema (migrations via `PRAGMA user_version`)

```sql
CREATE TABLE apps (
  id               INTEGER PRIMARY KEY,
  app_id           TEXT NOT NULL,
  role             TEXT NOT NULL CHECK (role IN ('primary','kept')) DEFAULT 'primary',
  display_name     TEXT NOT NULL,            -- embedded Name=
  managed_name     TEXT NOT NULL,            -- current file name in applications_dir
  name_origin      TEXT NOT NULL CHECK (name_origin IN ('auto','user')),
  orig_filename    TEXT,
  path             TEXT NOT NULL,
  device           INTEGER NOT NULL,
  inode            INTEGER NOT NULL,
  size             INTEGER NOT NULL,
  mtime_ns         INTEGER NOT NULL,
  fingerprint      TEXT NOT NULL,            -- sha256(first 4 MiB + size + squashfs offset)
  version          TEXT,
  update_info      TEXT,                     -- raw .upd_info string or NULL
  update_policy    TEXT NOT NULL CHECK (update_policy IN ('auto','manual','pinned')) DEFAULT 'auto',
  self_managed     INTEGER NOT NULL DEFAULT 0,
  origin           TEXT,                     -- JSON origin record (5.3) or NULL
  sha256           TEXT,                     -- full-file SHA-256, computed lazily (never on the event path), cached per (device, inode, mtime_ns, size)
  installed_at     INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);
CREATE UNIQUE INDEX apps_primary_uq ON apps(app_id) WHERE role = 'primary';
CREATE UNIQUE INDEX apps_inode_uq   ON apps(device, inode);
CREATE UNIQUE INDEX apps_path_uq    ON apps(path);

CREATE TABLE tombstones (
  id INTEGER PRIMARY KEY, app_id TEXT, device INTEGER, inode INTEGER,
  fingerprint TEXT, last_name TEXT, name_origin TEXT, update_policy TEXT,
  self_managed INTEGER, removed_at INTEGER NOT NULL
);                                           -- purged after 30 days

CREATE TABLE backups (
  id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, path TEXT NOT NULL,
  version TEXT, size INTEGER, created_at INTEGER NOT NULL
);

CREATE TABLE events (                        -- audit log, capped at 1000 rows
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL, app_id TEXT, detail TEXT
);
```

The state DB is a cache plus user choices. **It must be fully rebuildable** from the Applications dir (`appimage reindex`) except `name_origin='user'`, `update_policy`, `self_managed` and `origin` (these are also mirrored into the entry as `X-AppImage-*` keys and an xattr, see 6.3 and 5.3).

### 5.3 Origin record (optional, supplied by front-ends)

An app may carry an **origin**: provenance supplied by a front-end such as the Software Center. The daemon does not interpret it. It stores, mirrors and returns it, and uses `catalog_id` for exactly one rule (6.9: catalog-linked apps are skipped by the daemon's own update checker).

```json
{ "source": "aurelis-catalog", "catalog_id": "org.kde.kate", "version": "26.08.2-1",
  "sha256": "<64 hex>", "changed_at": "2026-11-01T18:20:58Z", "url": "https://github.com/..." }
```

- Required keys: `source` (string, at most 64 chars) and `catalog_id` (string, at most 200 chars). Every other key is optional and unknown keys are preserved. Total JSON at most 4 KiB. Invalid input raises the D-Bus error `InvalidArgument`.
- Storage: `apps.origin` (JSON text), mirrored as desktop-entry keys `X-AppImage-CatalogId` and `X-AppImage-OriginSource`, and as xattr `user.appimage.origin` (best effort). `reindex` restores it from there.
- Lifecycle: set by `Install` (option `origin`) or by `SetOrigin`. A Replace that carries a new `origin` overwrites it; a Replace without one keeps the old record. The origin is kept in the tombstone and restored by RESTORE. It is never derived by the daemon.

---

## 6. Core behavior

### 6.1 Reading metadata without executing the file

1. Validate: ELF magic `\x7fELF` and AppImage magic `AI\x02` at offset 8 (type 2). `AI\x01` = type 1 → `UnsupportedType`. Anything else → `NotAnAppImage`. Detection is by content, never by extension.
2. Squashfs offset = `e_shoff + e_shentsize * e_shnum` (ELF64 header bytes `0x28`, `0x3A`, `0x3C`; handle ELF32 and endianness). Verify the squashfs magic `hsqs` at the offset; if not, fail with `CorruptAppImage`.
3. Extract with `unsquashfs -o <offset> -d <tmpdir> -f <file> '*.desktop' '.DirIcon' 'usr/share/metainfo/*.xml' 'usr/share/appdata/*.xml'` (verify wildcard behavior on the installed squashfs-tools; fall back to `-ll` listing + explicit paths). `.DirIcon` and the icon named by `Icon=` can be symlinks inside the image: resolve them within the extracted tree and refuse anything escaping the tmpdir.
4. Parse the root `.desktop`: plain `Name=` (not `Name[xx]=`), `Comment=`, `Categories=`, `Icon=`, `X-AppImage-Version=`, `StartupWMClass=`. Version fallback: AppStream `<release version=...>`, then a version-looking token in the original filename.
5. `.upd_info` and `.sha256_sig` are read straight from ELF section headers (parse section header table + `.shstrtab`; do not shell out to `objcopy`).
6. Timeouts: every subprocess has a hard 15 s timeout and runs with `nice`/idle IO. Output is capped. All temp dirs are created with `mode 0700` and always removed.

Returned `AppImageMeta` dataclass: `type, offset, name, version, comment, categories, icon_bytes, icon_ext, app_id, update_info, has_signature, size, fingerprint`.

### 6.2 Naming

- Candidate = `naming.template` rendered with `{name}`, `{version}`, `{app_id}`.
- Sanitize: strip control chars, `/`, `\`, leading dots and spaces, trailing dots and spaces; collapse whitespace; max 100 bytes UTF-8 (do not cut in the middle of a codepoint); never produce names ending in `.disabled`, `.part`, `.crdownload`, `.zs-old`, or starting with `.`.
- Empty or unusable `Name=` → fall back to the original filename with its extension stripped.
- Collision rules, in order:
  1. Target is a non-AppImage file or a directory → hard stop, report `NameCollision`, leave the dropped file untouched.
  2. Target is a tracked AppImage with the same `app_id` → conflict flow (6.6).
  3. Target is a tracked AppImage with a *different* `app_id` (two apps called "Studio") → append `" (<app_id short>)"` automatically, no prompt.
- The assigned name is stored in `managed_name`; `name_origin='auto'`.
- Rename is always same-directory `rename(2)` (atomic). The original filename is stored in `orig_filename` and in the xattr `user.appimage.origname` (best effort).

### 6.3 Integration (desktop entry and icons)

Entry `appimage-<app_id>.desktop` (for `kept` roles: `appimage-<app_id>-<version-slug>.desktop`):

```ini
[Desktop Entry]
Type=Application
Name=Gimp                   # role kept: "Gimp (8.9.2)"; name_origin=user: follows the file name (config: integration.menu_name = embedded|filename)
Exec="/home/u/Applications/Gimp" %U
TryExec=/home/u/Applications/Gimp
Icon=appimage-<app_id>
Categories=...              # copied from the AppImage
Comment=...
StartupWMClass=...
X-AppImage-Managed=true
X-AppImage-Id=<app_id>
X-AppImage-Version=8.9.4
X-AppImage-NameOrigin=auto
X-AppImage-UpdatePolicy=auto
X-AppImage-CatalogId=<catalog id>          # only when an origin exists (5.3)
X-AppImage-OriginSource=<origin source>    # only when an origin exists
```

- Write entries atomically (temp + rename). Escape `Exec=` per the Desktop Entry spec (quotes, `%`, spaces).
- Icons: PNG/SVG from the AppImage, installed in the matching `hicolor` size dir (read PNG dimensions; SVG → `scalable`). Remove old icon files of the app on change.
- After changes: run `update-desktop-database ~/.local/share/applications` if present, `gtk-update-icon-cache -t` if present, and `kbuildsycoca6` (non-fatal, rate-limited to once per 5 s, coalesced). Plasma also watches the dirs, so these are belts and braces.
- The file in the Applications dir must be `chmod u+x` (done on install, verified on reconcile).
- `appimage doctor` verifies that Dolphin shows an icon for an extensionless AppImage (MIME `application/vnd.appimage` is matched by magic in shared-mime-info; confirm via `kmimetypefinder`), and reports which thumbnailer/icon source is active.

### 6.4 Event sources and classification

Watch `applications_dir` (non-recursive) with inotify: `IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_DELETE | IN_ATTRIB`. Do **not** use `IN_CREATE` (half-written files). Ignore names: leading `.`, `*.part`, `*.crdownload`, `*.partial`, `*.tmp`, `*~`, `*.zs-old`, `*.disabled`, and the `.previous`/`.staging` dirs. Events caused by the daemon's own operations are suppressed through an **expected-events table** (path + inode + operation, TTL 3 s).

Classifier (debounce 500 ms per path; coalesce bursts):

| Observed | Condition | Class | Action |
|---|---|---|---|
| `MOVED_FROM` + `MOVED_TO` sharing a cookie, both inside the dir | not an expected event | **RENAME** | Update `path`/`managed_name`; if new name differs from the one the tool assigned → `name_origin='user'`; update desktop entry `Exec/TryExec`. No prompts. |
| `MOVED_TO` / `CLOSE_WRITE` for a new path | valid AppImage; inode matches a tombstone | **RESTORE** | Re-create the record with its old name, flags and policy. No rename, no prompt. |
| same as above | valid AppImage; no match; no tracked `app_id` | **DROP** | Install flow (6.5). |
| same as above | `app_id` matches a tracked `primary` | **DROP_CONFLICT or UPDATE** | Decide with 6.6. |
| `CLOSE_WRITE` on tracked path, same inode | | **MODIFIED** | Re-read metadata, refresh record/entry. |
| `CLOSE_WRITE`/`MOVED_TO` at tracked path with a different inode | same `app_id` | **REPLACED** | Update flow (6.7). |
| `MOVED_FROM` unpaired, or `DELETE`, or rename to an ignored name (dot, `.disabled`) or into a subfolder | after 2 s grace with no replacement | **REMOVED** | Uninstall (6.8). Includes Dolphin "Move to Trash". |
| `IN_ATTRIB` removing the exec bit | | **MODIFIED** | Fix the bit (`chmod u+x`), log. |

Non-AppImage files and invalid files are ignored silently (log at debug). A *dropped* file that is a valid AppImage with an unsupported type produces one notification, no further retries.

### 6.5 Install flow (DROP, `appimage install`, GUI)

1. Read metadata (6.1); stop on invalid/unsupported with a clear error.
2. Resolve `app_id`; check conflicts (6.6).
3. Choose the name (6.2). Move/copy into `<applications_dir>/.staging/` first when the source is outside the dir (copy+fsync), then atomic-rename into place. For files already inside the dir the rename is in place.
4. `chmod u+x`, write xattr, write desktop entry and icon, insert the DB row, refresh caches (6.3).
5. Send a notification: "Installed Gimp 8.9.4" (button: **Open**, **Show in folder**), unless the call was made with `silent=true` (6.5a).
Failure at any step rolls back everything done before it (removing the staged file, entry, icon).

### 6.5a API options for `Install` (front-end callers)

Callers other than the observer (CLI, Software Center, wizards) pass options as D-Bus `a{sv}` (CLI flag in brackets):

| Option | Type | Default | Meaning |
|---|---|---|---|
| `source_action` | `copy` or `move` | config `install.source_action` | Copy or move a file that is outside the dir. A file the caller downloaded into its own cache should be `move`. |
| `conflict` | `config`, `replace`, `keep`, `cancel`, `error` | `config` | What to do when the same `app_id` is already a primary. `config` applies `install.conflict_policy` (including the notification). `error` sends **no notification**, changes **nothing** and raises `ConflictPending` with a JSON payload (old meta, new meta, `upgrade`/`downgrade`/`same`), so the caller can ask its own user and call again with an explicit decision. |
| `origin` | JSON string | none | Origin record (5.3). [`--origin JSON\|@FILE`] |
| `silent` | bool | false | Suppress success notifications for this call. Failures, and `ask` conflicts when `conflict=config`, still notify. [`--silent`] |
| `name` | string | derived | Managed-name override; sets `name_origin='user'`. [`--name`] |
| `update_policy` | `auto`, `manual`, `pinned` | `auto` | Initial update policy. |

Rules:
- API callers go through the **same code path** as drops (6.5, 6.6). The Replace operation (6.6 step 3) preserves user-chosen names and creates the `.previous` backup, so a front-end that updates an app by installing a newer file gets `Revert` for free.
- An API-initiated Replace is **not** a self-update (6.7): it never sets `self_managed` and never waits for `after_quit`. The caller asked explicitly, and the swap is safe while the app runs (6.6 step 4).
- The path must be absolute and `stat`-able as the calling user. Relative paths are rejected with `InvalidArgument`.
- A conflict only against a `kept` role is not a conflict (kept versions are left alone).
- **Idempotent**: installing a file with the same SHA-256 as the installed primary returns the existing app with `changed=false`, no prompt, no notification. The source file is left untouched; the caller removes its own cache file.
- The result of a successful `Install` is JSON `{ "app": <app object, 8.1>, "changed": true|false, "previous": "<path or null>" }`.

### 6.6 Conflict flow (same `app_id` already installed as primary)

Inputs: old record, new file meta, who triggered it (user drop vs self-update, see 6.7), config.

1. **Identical file** (same SHA-256): discard the new file after confirmation by notification, no destructive action without telling the user.
2. **User drop** (no self-update evidence): apply `install.conflict_policy`:
   - `replace`: go to the replace operation below.
   - `keep`: old becomes `kept` named with `keep_template` (fallback `<managed_name>.old`, then `.old2`, ...); new takes the managed name.
   - `ask`: notification with up to 3 buttons: **Replace**, **Keep both**, **Cancel**, plus **Details…** opening the PyQt6 conflict dialog (6.10). Text states old → new version and whether it is an **upgrade**, **downgrade** or **same version**. No action or timeout = **Cancel** (new file left where it was dropped, untouched).
3. **Replace operation**: verify the new file is a valid AppImage, move the old file to `.previous/` (same filesystem, keep `previous_versions_keep` newest), then `rename(2)` the new file onto the managed name (user-chosen names are kept), update record (new inode), entry and icons. The old file stays executable and usable while a process runs from it (inode is kept alive).
4. If the old app is running, the swap is safe; show "takes effect on next launch".

If the managed name belongs to a record with `name_origin='user'`, **keep that name** for the new file. The name the user chose is never overwritten by tool logic.

### 6.7 Self-update vs user drop

A new valid AppImage with a tracked `app_id` is a **self-update** when any of these holds:
- a process with `APPIMAGE=<tracked path>` in `/proc/<pid>/environ` is running, or was seen running in the last 30 s (the `running` tracker snapshots every 2 s and on demand);
- the tracked file was removed/replaced within the last 30 s (tombstone with the same `app_id`);
- the app is flagged `self_managed`.

On self-update:
1. Do **not** prompt (apply `update.on_self_update`; `ask` is the opt-in exception).
2. If `update.rename_policy = after_quit`: wait until the new file is stable (no writes for 20 s) **and** no process is running from the old path. A process from the new path is acceptable (the app already relaunched). Waiting has a 10 min cap, then notify "Update waiting for Gimp to close".
3. Replace operation as in 6.6 step 3, keeping the user-chosen or managed name.
4. Set `self_managed=1` on first occurrence; record the event.
5. Notify: "Gimp updated 8.9.2 → 8.9.4" with button **Undo** (restores the `.previous` copy). Undo is available until the next update or for 7 days.
6. Never delete the old file before the new one is renamed into place *and* re-validated (ELF + `AI\x02` + squashfs magic).

`update.rename_policy = never` (for apps that misbehave): leave the app's own file name, point the integration at it, and show the embedded `Name=` in the menu.

### 6.8 Uninstall (REMOVED, `appimage remove`, GUI)

- Triggers: delete, move out of the dir (including Trash), move into a subfolder, rename with leading dot or `.disabled` suffix, or the explicit commands.
- Action: remove desktop entry and icons, delete the DB row, write a tombstone, refresh caches, notify "Gimp removed from the app menu". **Observer-triggered uninstall never touches the file.**
- Explicit `appimage remove <name>`: also moves the file to Trash (`gio trash`, fallback `kioclient trash`, fallback XDG trash spec implementation); `--keep-file` leaves it in place; `--purge` deletes permanently (requires `--yes` when non-interactive). App data directories (`~/.config/<app>`, etc.) are **never** touched.
- Restoring a file (RESTORE in 6.4) re-integrates it with its old name and settings.

### 6.9 Update policy and updates

Policies: `auto` (observe replacements, offer updates), `manual` (never start updates, never interfere), `pinned` (hide update actions, launch through a write-protecting wrapper, keep a hardlink safety copy; best effort, documented as such in the UI).

- `pinned` launch wrapper (Phase 5, optional per distro): desktop `Exec=` becomes `bwrap --dev-bind / / --ro-bind <applications_dir> <applications_dir> -- <file> %U` when `bwrap` is available and unprivileged user namespaces work; otherwise `pinned` falls back to detect-and-revert via a hardlink in `.previous/`. `appimage doctor` shows which mode is active.
- `.upd_info` kinds supported: `zsync|<url>`, `gh-releases-zsync|owner|repo|tag|glob`, `gh-releases-direct|...`, `pling-v1-zsync|...`. `bintray-zsync` is reported as dead/unsupported. Empty = not updatable externally.
- `appimage update` uses `appimageupdatetool` if installed (check-only mode first); if the tool is missing, the update action is hidden and `doctor` says why. Output goes to `.staging/`, is validated, then goes through the replace operation (6.6 step 3), so user-chosen names survive.
- If the AppImage has an embedded signature, verify via the tool and refuse to install on failure.
- Background check: at most once per `check_interval`, jittered, per-app, `GET`s only, with ETag/If-None-Match caching, honoring GitHub's rate limits (60/h unauthenticated); never check on startup.
- Apps with `self_managed=1` are excluded from external update checks unless the user overrides.
- **Update authority.** Apps with an `origin` (5.3) are owned by their front-end's update channel. With `update.skip_catalog_linked = true` (default) they are excluded from the daemon's background `.upd_info` checks and from `appimage update --check` / `--all`. An explicit `appimage update <name>` still works and prints a note. This prevents two engines (catalog and embedded `.upd_info`) from notifying or updating the same app.

### 6.9a Reconcile (startup and `appimage reindex`)

1. Scan the dir for valid AppImages (content-checked, ignoring the ignore list).
2. Match files to records by `(device, inode)`; unmatched ones: by fingerprint (covers restored backups and cross-filesystem copies); then by tombstone; else treat as DROP candidates.
3. Records without a file → REMOVED (tombstone).
4. Fix entries/icons/permissions that drifted. This also catches renames that happened while the daemon was not running.
5. Reconcile never prompts for more than one conflict at a time and never auto-deletes anything.

---

## 7. CLI specification

Binary: `appimage`. Subcommands, not dash flags (`appimage install`, not `appimage -install`). Short aliases may be added in P2. Global flags: `--dir <path>`, `--json`, `-q/--quiet`, `-v/--verbose`, `--no-color`, `--no-daemon` (force direct mode), `--version`, `--help`.

`<name>` matches the managed name (case-insensitive, unique prefix allowed), then `app_id`. Ambiguity → exit 2 with candidates.

| Command | Purpose |
|---|---|
| `appimage daemon` | Run observer + D-Bus service in the foreground (what systemd starts). Flags: `--dir`, `--no-notify`, `--no-dialogs`. |
| `appimage install <file>... [--name N] [--move\|--copy] [--replace\|--keep\|--cancel\|--conflict-error] [--origin JSON\|@FILE] [--silent] [--yes]` | Install from **any** path. Already inside the dir = adopt in place. Non-interactive conflicts without a policy flag exit 4. `--conflict-error` prints the conflict payload as JSON and exits 4 (same as D-Bus `conflict=error`). Options map 1:1 to 6.5a. |
| `appimage remove <name>... [--keep-file] [--purge] [--yes]` | Uninstall (6.8). |
| `appimage rename <name> <new>` | Rename; sets `name_origin='user'`. Fails on collision (exit 4). `--reset` restores the embedded-`Name=` form. |
| `appimage list [--json] [--sort name\|size\|version\|date] [--all]` | Table of installed apps with sizes, ends with totals. `--all` includes kept/previous versions. |
| `appimage info <name\|file> [--json] [--hash]` | Details (below). If the argument is a path to an AppImage that is not installed, prints its metadata without installing or executing it (same as D-Bus `Inspect`, 8.1). |
| `appimage update [<name>\|--all] [--check]` | Check/apply zsync updates. `--check` exits 0 if none, 10 if updates exist. |
| `appimage policy <name> auto\|manual\|pinned` | Set update policy. |
| `appimage origin <name> [--set JSON\|@FILE] [--clear]` | Show, set or clear the origin record (5.3). |
| `appimage revert <name>` | Restore the newest `.previous` version. |
| `appimage versions <name>` | List kept and previous versions. |
| `appimage disable <name>` / `enable <name>` | Convenience for the uninstall/restore convention (moves to/from `<dir>/.disabled/`... equivalent to REMOVED + RESTORE; the tombstone preserves settings). |
| `appimage run <name> [-- args]` | Launch (execs the file). |
| `appimage browse <name> [--exec-fallback]` | Mount read-only with `squashfuse` at `$XDG_RUNTIME_DIR/appimage-launcher/mnt/<name>` and open in Dolphin (`xdg-open`); unmount via `appimage browse --unmount <name>` or when closing; stale mounts are cleaned on daemon start. `--exec-fallback` uses `--appimage-mount`. |
| `appimage extract <name> <dest>` | Extract the squashfs (no execution). |
| `appimage verify <name>` | Re-validate file, signature (if any), desktop entry, icon. |
| `appimage reindex` | Run reconcile (6.9a) now. |
| `appimage doctor` | Environment report: tools, bus, systemd unit state, MIME/thumbnail, notification server capabilities (`actions`), namespaces, permissions. |
| `appimage config get\|set\|list` | Read/write `config.toml`. |
| `appimage status` | Daemon state, watched dir, pending conflicts/updates. |
| `appimage completions bash\|zsh\|fish` | Print completion script. |
| `appimage export [file]` / `import <file>` | Policies, names and flags as JSON (migration to another machine). |

Exit codes: `0` ok, `1` error, `2` usage/ambiguous name, `3` not found, `4` conflict needs a decision, `5` invalid or unsupported AppImage, `6` daemon required but unavailable, `10` updates available (`update --check`).

### `list` output

```
NAME      VERSION   SIZE        STATE   UPDATES   PATH
Gimp      8.9.4     112.3 MiB   active  auto      ~/Applications/Gimp
Krita     5.3.1      98.7 MiB   active  pinned    ~/Applications/Krita
Gimp 8.9.2 8.9.2    111.9 MiB   kept    -         ~/Applications/Gimp 8.9.2
------------------------------------------------------------------------
3 apps                322.9 MiB   (+ 1 previous version: 110.0 MiB in .previous)
```
Human sizes in IEC units (MiB); `--json` returns raw bytes. Table columns auto-fit the terminal width; no external table library.

### `info` output (fields)
Name, App ID, Version, Path, Size, SHA-256 (with `--hash`), Original filename, Name origin, Installed / Updated timestamps, Update info (kind + URL), Update policy, Self-managed, Signature present, Desktop entry path, Icon path, Categories, Comment, Running (PIDs), Previous versions with sizes.

---

## 8. Backend API (D-Bus, session bus)

- Bus name `io.github.appimage_launcher.Manager` (single constant in `constants.py`; **the final name must be decided before Phase 3**, because other repositories depend on it, for example `io.github.<owner>` or an Aurelis-specific name; clients read it from the published introspection XML), object `/io/github/appimage_launcher/Manager`, interface `io.github.appimage_launcher.Manager1`.
- D-Bus activation file installed (`SystemdService=appimage-launcher.service`) so the GUI/CLI can start the daemon on demand.
- Complex return values are JSON strings (`s`) to keep the interface stable.

| Member | Signature | Notes |
|---|---|---|
| `Inspect(path s) → s` | | Reads metadata **without installing or executing** (6.1). JSON shape in 8.1. Never modifies state. |
| `Install(path s, options a{sv}) → s` | | Options in 6.5a. Returns `{app, changed, previous}` JSON. Errors in 8.1. |
| `Remove(name s, options a{sv}) → ()` | | Options in 8.1 (`file_action`, `confirm`, `silent`). |
| `Rename(name s, new s) → s` | | |
| `List(options a{sv}) → s` | | JSON array |
| `Info(name s) → s` | | JSON |
| `SetPolicy(name s, policy s) → ()` | | |
| `CheckUpdates(names as) → s` | | |
| `Update(name s) → ()` | | progress via signal |
| `Revert(name s) → ()` | | |
| `ResolveConflict(id s, decision s, remember b) → ()` | | decision: `replace`, `keep`, `cancel` |
| `Reindex() → ()` | | |
| `SetOrigin(name s, origin_json s) → s` | | Sets the origin record (5.3); an empty string clears it. Returns the app JSON. Emits `AppsChanged(origin_changed)`. |
| Signal `AppsChanged(kind s, app_id s, source s)` | | `kind`: `installed`, `removed`, `renamed`, `updated`, `reverted`, `origin_changed`, `policy_changed`, `reindexed`. `source`: `observer`, `api`, `cli`. Clients must ignore unknown kinds. |
| Signal `ConflictPending(id s, json s)` | | |
| Signal `UpdateProgress(name s, percent i, state s)` | | |
| Properties | `ApplicationsDir s`, `Version s`, `APIVersion u`, `Capabilities as` | read-only. `APIVersion` is `1`. `Capabilities` lists optional features, see 8.1. |

Security: session bus only; the daemon runs as the user, never as root, and never accepts paths it cannot `stat` as the calling user's files.

### 8.1 Public contract details (`Manager1`, API v1)

**Versioning.** `APIVersion = 1`. Within v1 only **additive** changes are allowed: new optional options, new JSON keys, new methods, new `Capabilities` entries, new `AppsChanged` kinds. Clients must ignore unknown JSON keys and unknown signal kinds. A breaking change means a new interface `Manager2`, served in parallel for at least one release. The authoritative description is the published introspection XML `packaging/dbus/io.github.appimage_launcher.Manager1.xml`, plus `docs/API.md`.

**Capabilities (strings).** `inspect`, `origin`, `install-options-v1`, `conflict-error`, `remove-file-action`, `list-hash`. Front-ends check `APIVersion >= 1` and the capabilities they need at startup, and disable the dependent features with an explanation if one is missing.

**Name resolution.** Same as the CLI (managed name, case-insensitive unique prefix, then `app_id`). Ambiguity raises `Ambiguous` with the candidates in the message.

**`List(options)`**: `all` (bool, include `kept` roles; default false), `hash` (bool, compute missing SHA-256 values; default false; runs in a worker, may take seconds). Returns a JSON array of app objects.
**`Info(name)`**: one app object, always with `sha256` (computed if needed).

**App object** (returned by `List`, `Info`, `Install`, `SetOrigin`):

```json
{
  "app_id": "org.kde.kate",
  "name": "Kate",
  "managed_name": "Kate",
  "name_origin": "auto",
  "role": "primary",
  "version": "26.08.2-1",
  "path": "/home/u/Applications/Kate",
  "size": 70136910,
  "sha256": null,
  "update_policy": "auto",
  "self_managed": false,
  "has_update_info": true,
  "origin": null,
  "desktop_entry": "/home/u/.local/share/applications/appimage-org.kde.kate.desktop",
  "installed_at": 1731456000,
  "updated_at": 1731456000,
  "running": false,
  "previous": [{"version": "26.08.1-1", "size": 70010000, "created_at": 1730000000}]
}
```
`sha256` is `null` until computed (cached per `(device, inode, mtime_ns, size)`). `origin` is the record from 5.3 or `null`.

**`Inspect(path)` result:**

```json
{
  "type": 2, "name": "Kate", "version": "26.08.2-1", "app_id": "org.kde.kate",
  "comment": "Advanced text editor", "categories": ["Development", "TextEditor"],
  "icon_png_base64": "<base64 or null, at most 512 KiB>", "icon_ext": "png",
  "update_info": "gh-releases-zsync|...", "has_signature": false, "size": 70136910,
  "installed": { "...app object..." },
  "relation": "none|upgrade|downgrade|same"
}
```
`installed` is the primary with the same `app_id`, or `null`; `relation` compares it with the inspected file (version comparison from `versions.py`). Invalid or unsupported files raise `InvalidAppImage` / `UnsupportedType`. `Inspect` never executes the file.

**`Remove` options:** `file_action` = `trash` (default for API callers; fallback chain `gio trash`, `kioclient trash`, XDG trash spec), `keep`, or `purge` (requires `confirm=true`); `silent` (bool). App data directories are never touched (6.8).

**Errors** (`io.github.appimage_launcher.Error.<Name>`, JSON detail in the message where noted): `NotFound`, `Ambiguous`, `NameCollision`, `ConflictPending` (JSON payload with old/new meta and `relation`), `InvalidAppImage`, `UnsupportedType`, `InvalidArgument`, `PolicyForbids`, `Busy` (another mutation holds the lock; retry later), `Io`, `Aborted`.

**Concurrency.** The daemon serializes all mutations (single writer). Reads never block on mutations. Slow work (hashing, extraction) runs off the event loop. A mutation that cannot get the lock within 10 s raises `Busy`.

**Contract tests** (`tests/contract/`): (1) the daemon's live `Introspect` output equals the published XML; (2) every option, error and JSON shape above is exercised through a plain `dbus-fast` reference client on a private bus; (3) `AppsChanged` is emitted with the right `kind` and `source` for observer-, API- and CLI-originated changes. The reference client script is part of the repository and doubles as documentation for other repositories.

---

## 9. Notifications and dialogs

### 9.1 Notifications (`org.freedesktop.Notifications` over D-Bus)
- Use `Notify` with `actions=[id1,label1,id2,label2,...]`, hints `desktop-entry=appimage-launcher`, `urgency` (1 normal, 2 for conflicts/failures), `category`.
- Handle `ActionInvoked` and `NotificationClosed`; map notification id → pending operation. Closed without action (dismissed/timeout) = the non-destructive default (**Cancel / do nothing**).
- Check `GetCapabilities` for `actions`; if absent, go straight to the dialog or CLI hint.
- Plasma shows at most ~3 action buttons: keep the primary set to ≤3, with "Details…" replacing extra choices.
- Replace the notification in place (`replaces_id`) when state changes (e.g. update progress, "Undo" window).
- Throttle: max 1 notification per app per 5 s; batch bursts ("3 apps installed").
- Notifications text is plain (no markup from AppImage metadata; always escape it).
- API calls with `silent=true` (6.5a, 8.1) produce no success notification, so a front-end that shows its own feedback does not cause duplicates. Failures are never silent. A front-end that passes `conflict=error` never triggers a conflict notification.

### 9.2 Notification catalogue

| Event | Title / body | Buttons |
|---|---|---|
| Installed | "Installed Gimp 8.9.4" | Open, Show in folder |
| Conflict (user drop) | "Gimp is already installed (8.9.2). Replace with 8.9.4?" + upgrade/downgrade note | Replace, Keep both, Cancel (+ Details…) |
| Self-update done | "Gimp updated 8.9.2 → 8.9.4" | Undo |
| Update waiting | "Update waiting for Gimp to close" | Apply now (only if safe) |
| Update available | "Gimp 8.9.5 is available" | Update, Skip, Pin |
| Removed | "Gimp removed from the app menu" | Undo (restore) |
| Failure | "Could not install X: <reason>" | Details… |
| Unsupported type | "X is an AppImage type 1 and is not supported" | none |

### 9.3 PyQt6 dialogs (separate process)
- The daemon launches `appimage-gui dialog <kind> [--timeout N]`, sending a JSON request on stdin and reading a JSON response from stdout. Exit code `0` = answered, `1` = closed/cancelled, `124` = timed out. The daemon never blocks its event loop on a dialog (asyncio subprocess).
- Before spawning, make sure `WAYLAND_DISPLAY`/`DISPLAY` and `XDG_RUNTIME_DIR` are in the daemon environment; if missing, read them with `systemctl --user show-environment`; if still missing, fall back to notifications only.
- Dialogs, all with accessible labels, keyboard navigation and KDE-style layout (QDialogButtonBox, standard icons):
  1. **Conflict**: old vs new comparison (icon, name, version, size, date, up/downgrade badge), radio choices *Replace / Keep both (rename old to "Gimp 8.9.2") / Cancel*, an editable name field for the kept copy, checkbox "Remember my choice for this app", **Show details** (paths, hashes).
  2. **Rename**: live preview and collision validation, "Reset to default name" button.
  3. **Update**: progress, release info link, Cancel; shows signature status.
  4. **Error/details**: scrollable text with a "Copy" button.
- **No manager window.** Listing, searching, installing from a catalog, updating, reverting and removing apps is the job of front-ends (the Aurelis Software Center, the CLI). This project ships only the four helper dialogs above. They are used for **observer-originated** events (a user dropping a file in the folder). A front-end that needs a conflict decision passes `conflict=error` (6.5a) and presents its own UI.
- GUI tests run with `QT_QPA_PLATFORM=offscreen`.

---

## 10. System-wide install and systemd

### 10.1 Makefile contract

Variables: `PREFIX ?= /usr/local`, `DESTDIR ?=`, `SYSCONFDIR ?= /etc`, `PYTHON ?= python3`, `NO_ENABLE ?=`.

| Target | Behavior |
|---|---|
| `make help` | Lists targets (default goal). |
| `make deps-check` | Verifies Python version, runtime tools; prints missing distro packages. |
| `make lint` | `ruff check .`, `ruff format --check .`, `mypy src` (strict for `core/`). |
| `make fmt` | `ruff format .` and `ruff check --fix .`. |
| `make test` | `pytest -q` with unit + integration tests (private `dbus-daemon` session, temp `APPIMAGE_LAUNCHER_HOME`, `QT_QPA_PLATFORM=offscreen`). Skips tests needing missing tools with explicit reasons. `make test COV=1` adds coverage (fail under 85% for `core/`). |
| `make ci` | `deps-check lint test` with coverage, plus `shellcheck` on scripts, `desktop-file-validate` on `.desktop` files, `systemd-analyze --user verify` on the unit if available, and a **dry-run install into a temp `DESTDIR`** asserting every expected file exists with the right mode. Must be exactly what GitHub Actions runs. |
| `make run` | Runs the daemon in the foreground against `./.dev/` (`APPIMAGE_LAUNCHER_HOME=./.dev`, `--dir ./.dev/Applications`, verbose). Creates the dirs. Ctrl-C stops it cleanly. |
| `make install` | System-wide install (below). Honors `DESTDIR` for packaging. |
| `make uninstall` | Disables the global unit and removes everything it installed (never touches user data). |
| `make package` | Builds an sdist/tarball (`dist/`). |
| `make clean` | Removes build/test artifacts and `.dev/`. |

### 10.2 `make install` file list

```
$(PREFIX)/lib/appimage-launcher/python/appimage_launcher/   byte-compiled package (python3 -m compileall)
$(PREFIX)/bin/appimage                                      wrapper: exec python3 with PYTHONPATH set, -m appimage_launcher.cli
$(PREFIX)/bin/appimage-gui                                  wrapper for the PyQt6 GUI
$(PREFIX)/lib/systemd/user/appimage-launcher.service
$(PREFIX)/share/dbus-1/services/io.github.appimage_launcher.Manager.service
$(PREFIX)/share/dbus-1/interfaces/io.github.appimage_launcher.Manager1.xml
$(PREFIX)/share/applications/appimage-launcher.desktop     NoDisplay=true (notification identity only)
$(PREFIX)/share/kio/servicemenus/appimage-launcher.desktop  (Dolphin / KF6)
$(PREFIX)/share/icons/hicolor/scalable/apps/appimage-launcher.svg
$(PREFIX)/share/bash-completion/completions/appimage
$(PREFIX)/share/zsh/site-functions/_appimage
$(PREFIX)/share/fish/vendor_completions.d/appimage.fish
$(PREFIX)/share/man/man1/appimage.1
$(PREFIX)/share/doc/appimage-launcher/{README.md,SPEC.md}
$(SYSCONFDIR)/appimage-launcher/config.toml                 only if it does not exist (never overwrite)
```

After copying (and only if `DESTDIR` is empty and `NO_ENABLE` is unset): `systemctl --global enable appimage-launcher.service`, then print: *"Enabled for all users. It starts at each user's next login. To start now: `systemctl --user enable --now appimage-launcher`."* `make uninstall` runs `systemctl --global disable`.

`/usr/local/lib/systemd/user` and `/usr/local/share/kio/servicemenus` are on the default search paths. Note this in the README for other prefixes.

### 10.3 Service unit

```ini
[Unit]
Description=AppImage Launcher observer
Documentation=man:appimage(1)
After=dbus.socket

[Service]
Type=dbus
BusName=io.github.appimage_launcher.Manager
ExecStart=@PREFIX@/bin/appimage daemon
Restart=on-failure
RestartSec=2
Nice=10
IOSchedulingClass=idle
NoNewPrivileges=yes

[Install]
WantedBy=default.target
```

`systemctl --global enable` creates the `default.target.wants` symlink for every user, so the observer starts whenever a user session (user manager) starts, and each user gets their **own** instance. Per-user instance, never a root system service, because the daemon needs the session bus and the user's files. If the session bus is not ready, the daemon retries connecting (backoff, max 60 s) instead of exiting.

### 10.4 Dolphin service menu (KF6)
- On AppImage files (MIME `application/vnd.appimage`, `application/x-iso9660-appimage`): **Install to Applications** (`appimage install %F`), **Browse AppImage contents** (`appimage browse %f`).
- On files in the Applications dir (by `X-KDE-Protocols=file`, and checked by the command): **Rename with default name**, **Disable (remove from menu)**, **Check for updates**, **Pin / Unpin updates**, **Show package contents**.
- All actions call the CLI; results show as notifications. Validate with `desktop-file-validate`.

---

## 11. Quality: tests, lint, CI

### 11.1 Test strategy
- `tests/fixtures/build_fake_appimage.py`: builds minimal **valid** AppImages (tiny ELF stub with `AI\x02` at offset 8, section headers including `.upd_info`, squashfs from `mksquashfs` appended at the right offset, containing `*.desktop`, `.DirIcon`, metainfo). Parametrized by name, version, app_id, update info, and "corrupt" variants (bad magic, truncated, bad offset, symlink-escape).
- **Unit**: ELF offset (ELF32/64, LE/BE), squashfs extraction, desktop parsing (`Name[xx]`, quotes, malformed), naming/sanitizing (unicode, 100-byte cap, reserved suffixes), version comparison (`8.9.10` > `8.9.9`, `nightly`, git hashes), identity resolution, state migrations, classifier table (every row of 6.4).
- **Integration** (temp dirs, real inotify, private D-Bus session, fake notification server written with `dbus-fast` that records `Notify` and lets tests fire `ActionInvoked`):
  - drop → installed; rename pair → no prompts; trash → uninstall; restore from tombstone;
  - drop conflict → notification → each button → correct filesystem result; timeout → untouched;
  - self-update simulation (fake process env `APPIMAGE=` via a spawned helper), in-place, new-name, and delete-then-create variants;
  - half-written file (`.part` then rename) not processed early; `kill -9` of the daemon mid-install then restart → reconcile heals;
  - CLI direct mode vs daemon mode produce identical results;
  - zsync flow with a local HTTP server and a fake `appimageupdatetool`.
- **GUI** (`pytest-qt`, offscreen): each helper dialog's logic, JSON in/out and exit codes.
- **Contract** (`tests/contract/`, private D-Bus session): see 8.1. Also covers `Inspect`, `Install` with each option combination (`conflict=error` then explicit retry, `source_action=move`, `silent`, `origin`), idempotent re-install, `SetOrigin` set/clear, `Remove` with each `file_action`, `Revert`, `APIVersion`/`Capabilities`, and that `reindex` restores `origin`.
- **Property/robustness**: random file names (unicode, very long, newlines) never crash and never escape the Applications dir.
- **Safety tests**: no test may touch the real `$HOME` (fixture fails the run if `APPIMAGE_LAUNCHER_HOME` is unset).

### 11.2 CI (`.github/workflows/ci.yml`)
- Triggers: push, pull_request. Matrix: Python 3.11, 3.12, 3.13 on `ubuntu-latest`.
- Steps: install `squashfs-tools squashfuse dbus dbus-x11 desktop-file-utils shellcheck libegl1 libxkbcommon0 python3-pyqt6` (or `pip install PyQt6` in a venv for the test job only), then `make ci`.
- Upload coverage and the dry-run install tree as artifacts. Optional nightly job: run against the latest Fedora KDE container with a real `plasmashell` notification server if feasible.

### 11.3 Logging
Structured, one line per event (`key=value`), levels via `-v`, journald when run by systemd. Never log file contents or tokens.

---

## 12. Phases and tasks

### Phase 0: Foundations
- P0-1 Repo scaffold, `pyproject.toml` (ruff, mypy, pytest), license, README skeleton, `docs/DECISIONS.md`.
- P0-2 Makefile with all targets as stubs (`help`, `deps-check`, `lint`, `fmt`, `test`, `ci`, `run`, `install`, `uninstall`, `package`, `clean`).
- P0-3 GitHub Actions workflow running `make ci`.
- P0-4 `paths.py`, `config.py` (layered: system → user → env), `logging.py`, `constants.py`, `APPIMAGE_LAUNCHER_HOME` override.
- P0-5 `tests/fixtures/build_fake_appimage.py` + first tests proving it.
**Accept**: `make ci` green on an empty-but-wired project; fake AppImages are valid per `file` and mount with `squashfuse -o`.

### Phase 1: Core library
- P1-1 `elf.py`: header parse, squashfs offset, section read (`.upd_info`, `.sha256_sig`).
- P1-2 `squash.py`: safe `unsquashfs` wrapper (timeouts, output caps, symlink-escape protection).
- P1-3 `metadata.py` + `identity.py` (+ fingerprint) + `versions.py`.
- P1-4 `naming.py` (sanitize, templates, collision classification).
- P1-5 `state.py`: schema, migrations, repository, tombstones/backups/events, purge job.
- P1-6 `integration.py`: desktop entries (escaping), icons, cache refresh (rate-limited).
- P1-7 `running.py`: `/proc/*/environ` scanner and the "recently seen" tracker.
**Accept**: ≥85% coverage on `core/`; 40+ unit tests; a fuzz test proves hostile AppImages never escape the temp dir or hang.

### Phase 2: Manager operations and CLI MVP
- P2-1 `manager.py`: install (6.5), remove (6.8), rename, enable/disable with rollback on failure, flock for direct mode.
- P2-2 CLI framework (argparse), global flags, exit codes, `--json`, colors, table renderer.
- P2-3 Commands: `install`, `remove`, `rename`, `list`, `info`.
- P2-4 Commands: `reindex`, `verify`, `extract`, `browse`, `run`, `doctor`, `config`, `completions`.
- P2-5 Man page and completions generation.
**Accept**: all of 7 works in direct mode on a temp home; `list` shows sizes and totals; collisions and invalid files give the right exit codes.

### Phase 3: Observer daemon
- P3-1 inotify wrapper with debounce/coalesce and the ignore list.
- P3-2 `classifier.py` implementing the table in 6.4 with the expected-events suppression.
- P3-3 `reconcile.py` (6.9a) on startup and on demand.
- P3-4 D-Bus service (section 8) with signals; daemon lifecycle, clean shutdown, `Type=dbus` readiness.
- P3-5 `notifier.py` (section 9.1) + fake server for tests; `conflicts.py` implementing 6.6 with notification buttons.
- P3-6 CLI client mode over D-Bus with fallback to direct mode.
**Accept**: all integration scenarios in 11.1 for drop, rename, trash, restore, conflict pass; `make run` demonstrates drop-in/drop-out live.

### Phase 3B: Public API for front-ends (freezes API v1)

Depends on P3-4 (D-Bus service) and P3-5 (conflicts). The Software Center's real-daemon integration starts only after this phase; before that it works against a fake built from section 8.1.

- P3B-1 State schema migration (`origin`, `sha256` columns). Origin record: validation, storage, mirroring to the desktop entry and xattr, restore on `reindex` and from tombstones (5.3).
- P3B-2 `Install` options (6.5a): `source_action`, `conflict` incl. `error` with the `ConflictPending` payload, `origin`, `silent`, `name`, `update_policy`; idempotent identical install; result JSON. CLI parity flags (`--origin`, `--silent`, `--conflict-error`).
- P3B-3 `Inspect` (D-Bus and `appimage info <file>`), `SetOrigin` (D-Bus and `appimage origin`), `Remove` options (`file_action`, `confirm`, `silent`), `List`/`Info` app object with lazy, cached, off-thread `sha256`.
- P3B-4 `APIVersion`, `Capabilities`, `AppsChanged(kind, app_id, source)` with all kinds, the error names of 8.1, the `Busy` lock timeout.
- P3B-5 Update-authority rule (6.9): `update.skip_catalog_linked`; implement the filter now (used by the Phase 6 checker), test it with a stub checker.
- P3B-6 Publish `packaging/dbus/io.github.appimage_launcher.Manager1.xml`, write `docs/API.md`, the contract test suite and the reference `dbus-fast` client script.
**Accept**: contract suite green; the reference client can: inspect a file, install with `origin` and `conflict=error`, resolve the conflict with an explicit retry, update an app by installing a newer file (name preserved, `.previous` created), `Revert`, set and clear an origin, remove with `file_action=trash`, and observe `AppsChanged` with correct `source`. Tag the repository `api-v1`; from this point the API changes only additively.

### Phase 4: System install and systemd
- P4-1 Real `make install` / `uninstall` with `DESTDIR` support; wrappers; compileall.
- P4-2 Unit file, D-Bus activation file, global enable; ensure the observer starts on login for any user (test in a container with a second user using `loginctl enable-linger` or `systemd-run --user -M`).
- P4-3 Desktop file, icons, completions, man page, default config install.
- P4-4 `make ci` dry-run install assertions; `desktop-file-validate`, `systemd-analyze verify`.
- P4-5 `doctor` extended: unit state, bus, MIME/thumbnail check for extensionless AppImages in Dolphin.
**Accept**: fresh VM: `make install`, log in as user A and user B → each has a running `appimage-launcher.service`; dropping a file in each user's `~/Applications` installs it for that user only.

### Phase 5: Self-update handling and rollback
- P5-1 Self-update detection (6.7) via running tracker + tombstones.
- P5-2 Replace operation with `.previous` backups, `previous_versions_keep`, undo.
- P5-3 `after_quit` deferral logic and the 10 min cap notification.
- P5-4 `self_managed` auto-flagging, `rename_policy=never`.
- P5-5 `policy` command; `pinned` mode (bwrap wrapper with fallback to hardlink revert) and `doctor` reporting.
- P5-6 CLI: `revert`, `versions`.
**Accept**: simulation tests for in-place, new-name and delete-then-create updates leave exactly one primary, correct name, working `Exec=`, working undo; no prompt appears.

### Phase 6: zsync/external updates
- P6-1 `.upd_info` parser for all supported kinds; unit tests with real-world strings.
- P6-2 `appimageupdatetool` wrapper (check-only, apply into staging, signature handling).
- P6-3 Background checker (interval, jitter, ETag cache, rate-limit awareness).
- P6-4 `appimage update`, notifications **Update / Skip / Pin**, D-Bus `Update` + progress signal.
**Accept**: local HTTP server test updates an app end to end; a user-chosen name survives; a failed/corrupt download leaves the old file intact.

### Phase 7: Helper dialogs and Dolphin integration
- P7-1 `appimage-gui dialog` protocol (JSON stdin/stdout, exit codes) and daemon launcher with environment fallback.
- P7-2 Conflict, rename, update, error dialogs.
- P7-3 Helper-dialog polish: window icons, long-name elision, high-DPI, dark theme, and a manual test that each dialog works when launched from the user service environment (no manager window: that UI lives in the Software Center).
- P7-4 Dolphin service menu and `.desktop` entries; validate on a real Plasma 6 session.
- P7-5 Accessibility/keyboard pass; translations scaffold (`gettext`/Qt `tr`).
**Accept**: pytest-qt suite green; manual script in `docs/MANUAL_TESTS.md` passes on Plasma 6 (Wayland and X11).

### Phase 8: Hardening and release
- P8-1 Fuzz/robustness: hostile file names, huge files, symlink loops, slow disks, full disk during copy.
- P8-2 Performance: reconcile of 200 AppImages < 5 s warm; idle CPU ~0; memory < 60 MB.
- P8-3 Packaging recipes: `.deb`, `.rpm` spec, AUR PKGBUILD (all using `make install DESTDIR=`).
- P8-4 Docs: README with GIF, FAQ (self-updating apps, rename behavior, uninstall rules), troubleshooting from `doctor`.
- P8-5 Release workflow: tag → CI → tarball + checksums.
**Accept**: clean install/upgrade/uninstall on Fedora KDE, Arch/KDE and Kubuntu/Neon; `make uninstall` leaves no files outside user data.

---

## 13. Extra feature ideas (prioritized)

**CLI (cheap, high value)**
1. `appimage update` / `policy` / `revert` / `versions` (already in the phases).
2. `appimage doctor` (cuts support requests dramatically).
3. `appimage enable/disable` for temporarily hiding an app while keeping its settings.
4. `appimage export/import` for moving a setup between machines.
5. `appimage run --safe` (launch with `--appimage-extract-and-run`, useful when FUSE is unavailable).
6. `appimage dedupe` (find identical files by hash across Applications dir and Downloads).
7. `appimage cleanup` (remove `.previous` older than N days, orphaned icons/entries).

**GUI / KDE**
1. (Moved to the Aurelis Software Center) Manager UI with drag & drop install, per-app update policy, previous versions and revert, driven through the `Manager1` API.
2. Dolphin service menu: Show package contents, Install, Disable, Pin.
3. Optional system tray icon / KStatusNotifierItem: "2 updates available".
4. Plasma Krunner is automatic via the desktop entries; add `Keywords=` and `X-KDE-StartupNotify`.
5. Per-app "Open data folder" and "Reset app settings" (confirmation + Trash only), later.
6. Integration with Discover is out of scope, but an `AppStream` catalog export is a possible v2.

**Later**
- Shared `/opt/Applications` for all users, with a root helper (polkit).
- Type 1 AppImage support through ISO9660 reading.
- Sandboxed launch profiles (bubblewrap/Firejail) beyond `pinned`.

---

## 14. Risks and open questions (decide early, record in `docs/DECISIONS.md`)

1. **Self-updater relaunch race**: renaming too early can break the app's own relaunch. Mitigated by `after_quit` + stability window; confirm per app with Electron-updater-based apps (new file name in same dir, old removed) and AppImageUpdate-based apps.
2. **`unsquashfs` wildcard behavior and compression support** vary by version; verify in CI on all matrix distros; fall back to listing + explicit paths.
3. **Extensionless thumbnails/icons in Dolphin**: KIO's AppImage thumbnailer must match by MIME, not glob. `doctor` must verify; if it fails, ship a small thumbnailer plugin or keep a `.AppImage`-less fallback option (`naming.keep_extension = true`).
4. **Notification actions on Plasma**: capabilities and button limits; verified by `GetCapabilities` and tested manually.
5. **`pinned` is best effort** (custom updaters, user namespaces restricted on some distros). The UI must say so.
6. **Environment for dialogs from a user service**: Plasma imports its environment into systemd user manager, but not all setups do. Fallback to notification-only is mandatory.
7. **GitHub API rate limits** for update checks; keep ETag cache and jitter; allow an optional token in config.
8. **Daemon vs. CLI races**: single writer rule (flock + expected-events table). Needs a stress test with parallel CLI calls during daemon event processing.
9. **Name of the D-Bus service / project owner**: placeholder `io.github.appimage_launcher`. Because other repositories depend on it, decide the final name **before Phase 3** (record in `docs/DECISIONS.md`), keep it in one constant, and publish it in the introspection XML.
10. **Two update engines** (front-end catalog vs embedded `.upd_info`). Resolved by the update-authority rule (6.9). Verify with a test that an origin-linked app never receives a daemon update notification.
11. **Public API stability.** Front-ends in other repositories depend on `Manager1`. Freeze v1 at the end of Phase 3B, keep changes additive, and guard with the contract suite.
12. **Full-file hashing cost.** SHA-256 of 500 MB files must not block the daemon: run in a worker thread, cache by `(device, inode, mtime_ns, size)`, never hash on the observer path.
13. **Double-click in the Applications dir.** If a front-end registers a MIME handler for AppImages (for example an install wizard), double-clicking an already installed AppImage inside the Applications dir must still just launch it. The handler decides by path (inside the dir or tracked = run). Document this for front-end authors in `docs/API.md`.

---

## 15. Definition of done (v1.0)

- `make lint test ci install` work on a clean KDE machine; `make uninstall` removes everything it installed.
- Observer starts automatically for every user via the globally enabled user unit; restart-on-failure verified with `kill -9`.
- All commands in section 7 implemented, documented in the man page and completions.
- Drop, rename, trash, restore, conflict, self-update and zsync-update scenarios pass automated tests and the manual checklist.
- No destructive action without either an explicit user command or an explicit user choice in a notification or dialog; timeouts always resolve to "do nothing".
- Coverage ≥85% on `core/`, no `ruff`/`mypy` findings, CI green on all matrix entries.
- The `Manager1` API is documented (`docs/API.md`), published as introspection XML, covered by the contract suite, and tagged `api-v1`. The reference client script works against a fresh install.
