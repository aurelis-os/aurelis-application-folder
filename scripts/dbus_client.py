#!/usr/bin/env python3
"""
Reference dbus-fast client for the AppImage Launcher Manager1 API.

This script demonstrates the full API surface and serves as documentation
for other repositories (e.g., Aurelis Software Center) implementing front-ends.

Usage:
    python scripts/dbus_client.py <command> [args...]

Commands:
    inspect <path>                    - Inspect an AppImage file
    install <path> [options...]       - Install an AppImage
    remove <name> [options...]        - Remove an app
    rename <name> <new_name>          - Rename an app
    list [--all] [--hash]             - List installed apps
    info <name>                       - Get detailed info
    set-policy <name> <policy>        - Set update policy
    check-updates <name>...           - Check for updates
    update <name>                     - Update an app
    revert <name>                     - Revert to previous version
    set-origin <name> [json|@file]    - Set or clear origin
    reindex                           - Trigger full reindex
    monitor                           - Monitor AppsChanged signals
"""

import asyncio
import builtins
import json
import logging
import sys
from typing import Any

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus

logger = logging.getLogger(__name__)

# Constants from the spec
DBUS_SERVICE_NAME = "io.github.appimage_launcher.Manager"
DBUS_OBJECT_PATH = "/io/github/appimage_launcher/Manager"
DBUS_INTERFACE_NAME = "io.github.appimage_launcher.Manager1"


def _wrap_variants(d: dict[str, Any]) -> dict[str, Any]:
    """Wrap boolean values in Variant for D-Bus a{sv} dict."""
    result = {}
    for k, v in d.items():
        if isinstance(v, bool):
            result[k] = Variant("b", v)
        elif isinstance(v, int):
            result[k] = Variant("i", v)
        elif isinstance(v, str):
            result[k] = Variant("s", v)
        else:
            result[k] = v
    return result


class ManagerClient:
    """Async client for the Manager1 D-Bus interface."""

    def __init__(self, bus: MessageBus):
        self.bus = bus
        self.proxy = None
        self.iface = None
        self._signals: list[dict[str, Any]] = []

    async def connect(self):
        """Introspect and create proxy."""
        introspection = await self.bus.introspect(DBUS_SERVICE_NAME, DBUS_OBJECT_PATH)
        self.proxy = self.bus.get_proxy_object(DBUS_SERVICE_NAME, DBUS_OBJECT_PATH, introspection)
        self.iface = self.proxy.get_interface(DBUS_INTERFACE_NAME)

        # Subscribe to signals
        self.iface.on_apps_changed(self._on_apps_changed)
        self.iface.on_conflict_pending(self._on_conflict_pending)
        self.iface.on_update_progress(self._on_update_progress)

    def _on_apps_changed(self, kind: str, app_id: str, source: str):
        self._signals.append(
            {"signal": "AppsChanged", "kind": kind, "app_id": app_id, "source": source}
        )
        logger.info("AppsChanged signal", kind=kind, app_id=app_id, source=source)

    def _on_conflict_pending(self, conflict_id: str, json_payload: str):
        self._signals.append(
            {"signal": "ConflictPending", "id": conflict_id, "payload": json.loads(json_payload)}
        )
        logger.info("ConflictPending signal", conflict_id=conflict_id, payload=json_payload)

    def _on_update_progress(self, name: str, percent: int, state: str):
        self._signals.append(
            {"signal": "UpdateProgress", "name": name, "percent": percent, "state": state}
        )
        logger.info("UpdateProgress signal", name=name, percent=percent, state=state)

    def get_signals(self) -> list[dict[str, Any]]:
        return self._signals

    def clear_signals(self):
        self._signals.clear()

    # Properties
    async def get_api_version(self) -> int:
        props = self.proxy.get_interface("org.freedesktop.DBus.Properties")
        result = await props.call_get(DBUS_INTERFACE_NAME, "APIVersion")
        return result.value

    async def get_capabilities(self) -> list[str]:
        props = self.proxy.get_interface("org.freedesktop.DBus.Properties")
        result = await props.call_get(DBUS_INTERFACE_NAME, "Capabilities")
        return list(result.value)

    async def get_applications_dir(self) -> str:
        props = self.proxy.get_interface("org.freedesktop.DBus.Properties")
        result = await props.call_get(DBUS_INTERFACE_NAME, "ApplicationsDir")
        return result.value

    async def get_version(self) -> str:
        props = self.proxy.get_interface("org.freedesktop.DBus.Properties")
        result = await props.call_get(DBUS_INTERFACE_NAME, "Version")
        return result.value

    # Methods
    async def inspect(self, path: str) -> dict[str, Any]:
        result_json = await self.iface.call_inspect(path)
        return json.loads(result_json)

    async def install(self, path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if options is None:
            options = {}
        result_json = await self.iface.call_install(path, _wrap_variants(options))
        return json.loads(result_json)

    async def remove(self, name: str, options: dict[str, Any] | None = None) -> None:
        if options is None:
            options = {}
        await self.iface.call_remove(name, _wrap_variants(options))

    async def rename(self, name: str, new_name: str) -> dict[str, Any]:
        result_json = await self.iface.call_rename(name, new_name)
        return json.loads(result_json)

    async def list(
        self, all_apps: bool = False, compute_hash: bool = False
    ) -> list[dict[str, Any]]:
        options = _wrap_variants({"all": all_apps, "hash": compute_hash})
        result_json = await self.iface.call_list(options)
        return json.loads(result_json)

    async def info(self, name: str) -> dict[str, Any]:
        result_json = await self.iface.call_info(name)
        return json.loads(result_json)

    async def set_policy(self, name: str, policy: str) -> None:
        await self.iface.call_set_policy(name, policy)

    async def check_updates(self, names: builtins.list[str]) -> builtins.list[dict[str, Any]]:
        result_json = await self.iface.call_check_updates(names)
        return json.loads(result_json)

    async def update(self, name: str) -> dict[str, Any]:
        result_json = await self.iface.call_update(name)
        return json.loads(result_json)

    async def revert(self, name: str) -> None:
        await self.iface.call_revert(name)

    async def resolve_conflict(
        self, conflict_id: str, decision: str, remember: bool = False
    ) -> None:
        await self.iface.call_resolve_conflict(conflict_id, decision, remember)

    async def reindex(self) -> None:
        await self.iface.call_reindex()

    async def set_origin(self, name: str, origin_json: str) -> dict[str, Any]:
        result_json = await self.iface.call_set_origin(name, origin_json)
        return json.loads(result_json)


def print_json(data: Any):
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


async def cmd_inspect(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write("Usage: inspect <path>\n")
        return 1
    result = await client.inspect(args[0])
    print_json(result)
    return 0


async def cmd_install(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write(
            "Usage: install <path> [--origin JSON|@file] [--conflict config|replace|keep|cancel|error] [--source-action copy|move] [--silent] [--name NAME] [--update-policy auto|manual|pinned]\n"
        )
        return 1

    path = args[0]
    options = {}

    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--origin":
            i += 1
            if i >= len(args):
                sys.stderr.write("--origin requires a value\n")
                return 1
            val = args[i]
            if val.startswith("@"):
                with open(val[1:]) as f:
                    options["origin"] = f.read()
            else:
                options["origin"] = val
        elif arg == "--conflict":
            i += 1
            if i >= len(args):
                sys.stderr.write("--conflict requires a value\n")
                return 1
            options["conflict"] = args[i]
        elif arg == "--source-action":
            i += 1
            if i >= len(args):
                sys.stderr.write("--source-action requires a value\n")
                return 1
            options["source_action"] = args[i]
        elif arg == "--silent":
            options["silent"] = True
        elif arg == "--name":
            i += 1
            if i >= len(args):
                sys.stderr.write("--name requires a value\n")
                return 1
            options["name"] = args[i]
        elif arg == "--update-policy":
            i += 1
            if i >= len(args):
                sys.stderr.write("--update-policy requires a value\n")
                return 1
            options["update_policy"] = args[i]
        else:
            sys.stderr.write(f"Unknown option: {arg}\n")
            return 1
        i += 1

    try:
        result = await client.install(path, options)
        print_json(result)
        return 0
    except Exception as e:
        error_str = str(e)
        if "ConflictPending" in error_str:
            conflict_id = error_str.split(": ")[-1]
            sys.stderr.write(f"Conflict pending: {conflict_id}\n")
            sys.stderr.write("Use 'resolve-conflict' to resolve it.\n")
        else:
            sys.stderr.write(f"Error: {error_str}\n")
        return 1


async def cmd_remove(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write(
            "Usage: remove <name> [--file-action trash|keep|purge] [--confirm] [--silent]\n"
        )
        return 1

    name = args[0]
    options = {}

    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--file-action":
            i += 1
            if i >= len(args):
                sys.stderr.write("--file-action requires a value\n")
                return 1
            options["file_action"] = args[i]
        elif arg == "--confirm":
            options["confirm"] = True
        elif arg == "--silent":
            options["silent"] = True
        else:
            sys.stderr.write(f"Unknown option: {arg}\n")
            return 1
        i += 1

    await client.remove(name, options)
    sys.stdout.write(f"Removed: {name}\n")
    return 0


async def cmd_rename(client: ManagerClient, args: list[str]):
    if len(args) < 2:
        sys.stderr.write("Usage: rename <name> <new_name>\n")
        return 1

    result = await client.rename(args[0], args[1])
    print_json(result)
    return 0


async def cmd_list(client: ManagerClient, args: list[str]):
    all_apps = "--all" in args
    compute_hash = "--hash" in args
    result = await client.list(all_apps, compute_hash)
    print_json(result)
    return 0


async def cmd_info(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write("Usage: info <name>\n")
        return 1
    result = await client.info(args[0])
    print_json(result)
    return 0


async def cmd_set_policy(client: ManagerClient, args: list[str]):
    if len(args) < 2:
        sys.stderr.write("Usage: set-policy <name> <auto|manual|pinned>\n")
        return 1
    await client.set_policy(args[0], args[1])
    sys.stdout.write(f"Policy set to {args[1]} for {args[0]}\n")
    return 0


async def cmd_check_updates(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write("Usage: check-updates <name>...\n")
        return 1
    result = await client.check_updates(args)
    print_json(result)
    return 0


async def cmd_update(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write("Usage: update <name>\n")
        return 1
    await client.update(args[0])
    sys.stdout.write(f"Update triggered for {args[0]}\n")
    return 0


async def cmd_revert(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write("Usage: revert <name>\n")
        return 1
    await client.revert(args[0])
    sys.stdout.write(f"Reverted {args[0]}\n")
    return 0


async def cmd_resolve_conflict(client: ManagerClient, args: list[str]):
    if len(args) < 2:
        sys.stderr.write(
            "Usage: resolve-conflict <conflict_id> <replace|keep|cancel> [--remember]\n"
        )
        return 1

    conflict_id = args[0]
    decision = args[1]
    remember = "--remember" in args

    await client.resolve_conflict(conflict_id, decision, remember)
    sys.stdout.write(f"Conflict {conflict_id} resolved with decision: {decision}\n")
    return 0


async def cmd_reindex(client: ManagerClient, args: list[str]):
    await client.reindex()
    sys.stdout.write("Reindex completed\n")
    return 0


async def cmd_set_origin(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        sys.stderr.write("Usage: set-origin <name> [json|@file]\n")
        sys.stderr.write("  Omit json to clear origin\n")
        return 1

    name = args[0]
    origin_json = ""

    if len(args) > 1:
        val = args[1]
        if val.startswith("@"):
            with open(val[1:]) as f:
                origin_json = f.read()
        else:
            origin_json = val

    result = await client.set_origin(name, origin_json)
    print_json(result)
    return 0


async def cmd_monitor(client: ManagerClient, args: list[str]):
    sys.stdout.write("Monitoring signals... Press Ctrl+C to stop\n")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped monitoring\n")
    return 0


async def cmd_props(client: ManagerClient, args: list[str]):
    sys.stdout.write(f"APIVersion: {await client.get_api_version()}\n")
    sys.stdout.write(f"Capabilities: {await client.get_capabilities()}\n")
    sys.stdout.write(f"ApplicationsDir: {await client.get_applications_dir()}\n")
    sys.stdout.write(f"Version: {await client.get_version()}\n")
    return 0


COMMANDS = {
    "inspect": cmd_inspect,
    "install": cmd_install,
    "remove": cmd_remove,
    "rename": cmd_rename,
    "list": cmd_list,
    "info": cmd_info,
    "set-policy": cmd_set_policy,
    "check-updates": cmd_check_updates,
    "update": cmd_update,
    "revert": cmd_revert,
    "resolve-conflict": cmd_resolve_conflict,
    "reindex": cmd_reindex,
    "set-origin": cmd_set_origin,
    "monitor": cmd_monitor,
    "props": cmd_props,
}


async def main():
    if len(sys.argv) < 2:
        sys.stdout.write(__doc__)
        return 1

    command = sys.argv[1]
    args = sys.argv[2:]

    if command not in COMMANDS:
        sys.stderr.write(f"Unknown command: {command}\n")
        sys.stderr.write(f"Available: {', '.join(COMMANDS.keys())}\n")
        return 1

    # Connect to session bus
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    client = ManagerClient(bus)

    try:
        await client.connect()
        return await COMMANDS[command](client, args)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        bus.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
