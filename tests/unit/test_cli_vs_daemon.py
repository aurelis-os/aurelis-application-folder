"""Tests for CLI commands against a real daemon process.

These tests start a real daemon in a subprocess with its own D-Bus session bus,
then run the `appimage` CLI commands against it via subprocess.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.fixtures.build_fake_appimage import build_fake_appimage


def start_daemon_with_private_bus(tmpdir_path, applications_dir, config_dir):
    """Start daemon with a private D-Bus session bus, return (proc, dbus_addr, cli_env)."""
    # Create a script that starts the daemon and writes the bus address to a file
    bus_addr_file = tmpdir_path / "bus_addr.txt"
    ready_file = tmpdir_path / "daemon_ready.txt"

    # Daemon startup script
    script = f"""#!/bin/bash
set -e
export APPIMAGE_LAUNCHER_HOME="{tmpdir_path}"
export APPIMAGE_LAUNCHER_CONFIG_DIR="{config_dir}"
# Write the session bus address to a file
echo "$DBUS_SESSION_BUS_ADDRESS" > "{bus_addr_file}"
# Signal that daemon is starting
touch "{ready_file}"
# Start daemon
exec {sys.executable} -m appimage_launcher.daemon.app \\
    --dir "{applications_dir}" \\
    --no-notify --no-dialogs --dev
"""
    script_file = tmpdir_path / "start_daemon.sh"
    script_file.write_text(script)
    script_file.chmod(0o755)

    # Start dbus-run-session with our script
    proc = subprocess.Popen(
        ["dbus-run-session", str(script_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for ready file and bus address file
    for _ in range(30):
        if ready_file.exists() and bus_addr_file.exists():
            break
        time.sleep(0.2)
    else:
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(f"Daemon failed to start: {stderr.decode()}")

    # Read the bus address
    dbus_addr = bus_addr_file.read_text().strip()
    if not dbus_addr:
        raise RuntimeError("Empty DBUS_SESSION_BUS_ADDRESS")

    # Create env for CLI commands
    env = os.environ.copy()
    env["APPIMAGE_LAUNCHER_HOME"] = str(tmpdir_path)
    env["DBUS_SESSION_BUS_ADDRESS"] = dbus_addr

    return proc, env, dbus_addr


@pytest.fixture(scope="function")
def daemon_environment():
    """Start a test daemon in a subprocess with its own D-Bus session."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        applications_dir = tmpdir_path / "Applications"
        applications_dir.mkdir()
        config_dir = tmpdir_path / "config"
        config_dir.mkdir()
        state_dir = tmpdir_path / "state"
        state_dir.mkdir()
        data_dir = tmpdir_path / "data"
        data_dir.mkdir()

        # Create test config
        import tomli_w

        from appimage_launcher.config import Config

        test_config = Config()
        test_config.applications_dir = str(applications_dir)
        test_config.naming.template = "{name}"
        test_config.naming.keep_template = "{name} {version}"
        test_config.install.conflict_policy = "replace"
        test_config.install.source_action = "copy"
        test_config.update.previous_versions_keep = 1
        test_config.update.skip_catalog_linked = False
        test_config.update.check_interval = "0"

        config_file = config_dir / "config.toml"
        config_file.write_text(tomli_w.dumps(test_config.to_dict()))

        # Start daemon with private bus
        proc, env, dbus_addr = start_daemon_with_private_bus(
            tmpdir_path, applications_dir, config_dir
        )

        # Wait a bit more for daemon to fully start
        time.sleep(2)

        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            raise RuntimeError(f"Daemon failed to start: {stderr.decode()}")

        yield {
            "proc": proc,
            "tmpdir": tmpdir_path,
            "applications_dir": applications_dir,
            "config_dir": config_dir,
            "state_dir": state_dir,
            "data_dir": data_dir,
            "env": env,
            "dbus_addr": dbus_addr,
        }

        # Stop daemon
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def run_appimage_cli(args, env, cwd=None):
    """Run the appimage CLI command with the given environment."""
    cli_path = shutil.which("appimage")
    if not cli_path:
        cmd = [sys.executable, "-m", "appimage_launcher.cli"] + args
    else:
        cmd = [cli_path] + args

    result = subprocess.run(
        cmd,
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


class TestCLIAgainstDaemon:
    """Tests for CLI commands against a real daemon."""

    def _create_fake_appimage(
        self, tmpdir_path, name="TestApp", version="1.0.0", app_id="com.example.test"
    ):
        """Create a fake AppImage for testing."""
        appimage = tmpdir_path / f"{name}.appimage"
        build_fake_appimage(appimage, name=name, version=version, app_id=app_id)
        return appimage

    def test_install_and_list(self, daemon_environment):
        """Test installing an AppImage and listing it."""
        env = daemon_environment["env"].copy()
        tmpdir = daemon_environment["tmpdir"]
        appimage = self._create_fake_appimage(
            tmpdir, "InstallTestApp", "1.0.0", "com.example.installtest"
        )

        # Install
        result = run_appimage_cli(["install", str(appimage)], env)
        assert result.returncode == 0, f"Install failed: {result.stderr}"
        assert "Installed" in result.stdout or "installed" in result.stdout.lower()

        # List
        result = run_appimage_cli(["list"], env)
        assert result.returncode == 0
        assert "InstallTestApp" in result.stdout
        assert "1.0.0" in result.stdout

    def test_info(self, daemon_environment):
        """Test getting info about an installed app."""
        env = daemon_environment["env"].copy()
        tmpdir = daemon_environment["tmpdir"]
        appimage = self._create_fake_appimage(
            tmpdir, "InfoTestApp", "2.0.0", "com.example.infotest"
        )

        # Install
        result = run_appimage_cli(["install", str(appimage)], env)
        assert result.returncode == 0

        # Info
        result = run_appimage_cli(["info", "InfoTestApp"], env)
        assert result.returncode == 0
        assert "InfoTestApp" in result.stdout
        assert "2.0.0" in result.stdout
        assert "com.example.infotest" in result.stdout

    def test_remove(self, daemon_environment):
        """Test removing an installed app."""
        env = daemon_environment["env"].copy()
        tmpdir = daemon_environment["tmpdir"]
        appimage = self._create_fake_appimage(
            tmpdir, "RemoveTestApp", "1.0.0", "com.example.removetest"
        )

        # Install
        result = run_appimage_cli(["install", str(appimage)], env)
        assert result.returncode == 0

        # Verify it exists
        result = run_appimage_cli(["list"], env)
        assert "RemoveTestApp" in result.stdout

        # Remove
        result = run_appimage_cli(["remove", "RemoveTestApp"], env)
        assert result.returncode == 0
        assert "Removed" in result.stdout or "removed" in result.stdout.lower()

        # Verify it's gone
        result = run_appimage_cli(["list"], env)
        assert "RemoveTestApp" not in result.stdout

    def test_check_updates(self, daemon_environment):
        """Test checking for updates."""
        env = daemon_environment["env"].copy()
        tmpdir = daemon_environment["tmpdir"]
        appimage = self._create_fake_appimage(
            tmpdir, "UpdateTestApp", "1.0.0", "com.example.updatetest"
        )

        # Install
        result = run_appimage_cli(["install", str(appimage)], env)
        assert result.returncode == 0

        # Check updates
        result = run_appimage_cli(["update", "--check", "UpdateTestApp"], env)
        assert result.returncode in (0, 10)

    def test_apply_update(self, daemon_environment):
        """Test applying an update (will fail since no real update server)."""
        env = daemon_environment["env"].copy()
        tmpdir = daemon_environment["tmpdir"]
        appimage = self._create_fake_appimage(
            tmpdir, "ApplyUpdateTestApp", "1.0.0", "com.example.applyupdate"
        )

        # Install
        result = run_appimage_cli(["install", str(appimage)], env)
        assert result.returncode == 0

        # Apply update - should fail gracefully
        result = run_appimage_cli(["update", "ApplyUpdateTestApp"], env)
        # Should not hang
        output = result.stdout + result.stderr
        assert "error" in output.lower() or "failed" in output.lower() or "Error" in output


class TestCLINoDaemon:
    """Tests for CLI behavior when no daemon is running."""

    def test_cli_fails_fast_without_daemon(self):
        """Test that CLI falls back to direct mode (exit 0, <10s) when no daemon is running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            applications_dir = tmpdir_path / "Applications"
            applications_dir.mkdir()

            env = os.environ.copy()
            env["APPIMAGE_LAUNCHER_HOME"] = str(tmpdir_path)
            env.pop("DBUS_SESSION_BUS_ADDRESS", None)

            start = time.time()
            result = run_appimage_cli(["--json", "list"], env)
            elapsed = time.time() - start

            assert elapsed < 10, f"CLI took {elapsed:.1f}s to respond, expected < 10s"
            assert result.returncode == 0, (
                f"Expected exit 0, got {result.returncode}: {result.stderr}"
            )
            assert "daemon not running; using direct mode" in result.stderr

    def test_list_uses_daemon_without_warning(self, daemon_environment):
        """Test that CLI uses the daemon (no fallback warning) when a daemon is running."""
        env = daemon_environment["env"].copy()
        result = run_appimage_cli(["--json", "list"], env)
        assert result.returncode == 0, f"List failed: {result.stderr}"
        assert "daemon not running; using direct mode" not in result.stderr
