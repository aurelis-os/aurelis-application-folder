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
import sys
from typing import Any

from dbus_fast import BusType
from dbus_fast.aio import MessageBus

# Constants from the spec
DBUS_SERVICE_NAME = "io.github.appimage_launcher.Manager"
DBUS_OBJECT_PATH = "/io/github/appimage_launcher/Manager"
DBUS_INTERFACE_NAME = "io.github.appimage_launcher.Manager1"


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
        print(f"[Signal] AppsChanged: kind={kind}, app_id={app_id}, source={source}")

    def _on_conflict_pending(self, conflict_id: str, json_payload: str):
        self._signals.append(
            {"signal": "ConflictPending", "id": conflict_id, "payload": json.loads(json_payload)}
        )
        print(f"[Signal] ConflictPending: id={conflict_id}, payload={json_payload}")

    def _on_update_progress(self, name: str, percent: int, state: str):
        self._signals.append(
            {"signal": "UpdateProgress", "name": name, "percent": percent, "state": state}
        )
        print(f"[Signal] UpdateProgress: name={name}, percent={percent}, state={state}")

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
        result_json = await self.iface.call_install(path, options)
        return json.loads(result_json)

    async def remove(self, name: str, options: dict[str, Any] | None = None) -> None:
        if options is None:
            options = {}
        await self.iface.call_remove(name, options)

    async def rename(self, name: str, new_name: str) -> dict[str, Any]:
        result_json = await self.iface.call_rename(name, new_name)
        return json.loads(result_json)

    async def list(
        self, all_apps: bool = False, compute_hash: bool = False
    ) -> list[dict[str, Any]]:
        options = {"all": all_apps, "hash": compute_hash}
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

    async def update(self, name: str) -> None:
        await self.iface.call_update(name)

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
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def cmd_inspect(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print("Usage: inspect <path>")
        return 1
    result = await client.inspect(args[0])
    print_json(result)
    return 0


async def cmd_install(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print(
            "Usage: install <path> [--origin JSON|@file] [--conflict config|replace|keep|cancel|error] [--source-action copy|move] [--silent] [--name NAME] [--update-policy auto|manual|pinned]"
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
                print("--origin requires a value")
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
                print("--conflict requires a value")
                return 1
            options["conflict"] = args[i]
        elif arg == "--source-action":
            i += 1
            if i >= len(args):
                print("--source-action requires a value")
                return 1
            options["source_action"] = args[i]
        elif arg == "--silent":
            options["silent"] = True
        elif arg == "--name":
            i += 1
            if i >= len(args):
                print("--name requires a value")
                return 1
            options["name"] = args[i]
        elif arg == "--update-policy":
            i += 1
            if i >= len(args):
                print("--update-policy requires a value")
                return 1
            options["update_policy"] = args[i]
        else:
            print(f"Unknown option: {arg}")
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
            print(f"Conflict pending: {conflict_id}")
            print("Use 'resolve-conflict' to resolve it.")
        else:
            print(f"Error: {error_str}")
        return 1


async def cmd_remove(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print("Usage: remove <name> [--file-action trash|keep|purge] [--confirm] [--silent]")
        return 1

    name = args[0]
    options = {}

    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--file-action":
            i += 1
            if i >= len(args):
                print("--file-action requires a value")
                return 1
            options["file_action"] = args[i]
        elif arg == "--confirm":
            options["confirm"] = True
        elif arg == "--silent":
            options["silent"] = True
        else:
            print(f"Unknown option: {arg}")
            return 1
        i += 1

    await client.remove(name, options)
    print(f"Removed: {name}")
    return 0


async def cmd_rename(client: ManagerClient, args: list[str]):
    if len(args) < 2:
        print("Usage: rename <name> <new_name>")
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
        print("Usage: info <name>")
        return 1
    result = await client.info(args[0])
    print_json(result)
    return 0


async def cmd_set_policy(client: ManagerClient, args: list[str]):
    if len(args) < 2:
        print("Usage: set-policy <name> <auto|manual|pinned>")
        return 1
    await client.set_policy(args[0], args[1])
    print(f"Policy set to {args[1]} for {args[0]}")
    return 0


async def cmd_check_updates(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print("Usage: check-updates <name>...")
        return 1
    result = await client.check_updates(args)
    print_json(result)
    return 0


async def cmd_update(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print("Usage: update <name>")
        return 1
    await client.update(args[0])
    print(f"Update triggered for {args[0]}")
    return 0


async def cmd_revert(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print("Usage: revert <name>")
        return 1
    await client.revert(args[0])
    print(f"Reverted {args[0]}")
    return 0


async def cmd_resolve_conflict(client: ManagerClient, args: list[str]):
    if len(args) < 2:
        print("Usage: resolve-conflict <conflict_id> <replace|keep|cancel> [--remember]")
        return 1

    conflict_id = args[0]
    decision = args[1]
    remember = "--remember" in args

    await client.resolve_conflict(conflict_id, decision, remember)
    print(f"Conflict {conflict_id} resolved with decision: {decision}")
    return 0


async def cmd_reindex(client: ManagerClient, args: list[str]):
    await client.reindex()
    print("Reindex completed")
    return 0


async def cmd_set_origin(client: ManagerClient, args: list[str]):
    if len(args) < 1:
        print("Usage: set-origin <name> [json|@file]")
        print("  Omit json to clear origin")
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
    print("Monitoring signals... Press Ctrl+C to stop")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped monitoring")
    return 0


async def cmd_props(client: ManagerClient, args: list[str]):
    print(f"APIVersion: {await client.get_api_version()}")
    print(f"Capabilities: {await client.get_capabilities()}")
    print(f"ApplicationsDir: {await client.get_applications_dir()}")
    print(f"Version: {await client.get_version()}")
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
        print(__doc__)
        return 1

    command = sys.argv[1]
    args = sys.argv[2:]

    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        return 1

    # Connect to session bus
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    client = ManagerClient(bus)

    try:
        await client.connect()
        return await COMMANDS[command](client, args)
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        bus.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
