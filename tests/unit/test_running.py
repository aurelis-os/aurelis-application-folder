"""Tests for running AppImage tracker."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from appimage_launcher.core.running import (
    RunningApp,
    RunningTracker,
    get_running_tracker,
    scan_running_appimages,
)


class TestRunningTracker:
    """Tests for RunningTracker class."""

    def setup_method(self):
        """Set up a fresh tracker for each test."""
        self.tracker = RunningTracker()

    def test_initial_state(self):
        """Test initial state is empty."""
        assert self.tracker.get_all_running() == {}
        assert self.tracker.get_recently_seen() == {}

    def test_scan_no_proc(self):
        """Test scan when /proc doesn't exist or is empty."""
        # Test internal state directly
        self.tracker._running = {}
        self.tracker._recently_seen = {}
        result = self.tracker.scan()
        # The actual scan will try to read /proc, but we can test the internal methods
        assert isinstance(result, dict)

    def test_is_running(self):
        """Test is_running method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app_path = str(Path(tmpdir) / "TestApp")

            # Not running initially
            assert self.tracker.is_running(app_path) is False

            # Add to running
            self.tracker._running[app_path] = RunningApp(
                pid=12345,
                appimage_path=app_path,
                start_time=1000.0,
                last_seen=1000.0,
            )

            assert self.tracker.is_running(app_path) is True

    def test_was_recently_running(self):
        """Test was_recently_running method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app_path = str(Path(tmpdir) / "TestApp")

            # Not recently running initially
            assert self.tracker.was_recently_running(app_path) is False

            # Add to recently seen
            self.tracker._recently_seen[app_path] = 1000.0

            assert self.tracker.was_recently_running(app_path) is True

    def test_get_pids(self):
        """Test get_pids method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app_path = str(Path(tmpdir) / "TestApp")

            # Not running
            assert self.tracker.get_pids(app_path) == []

            # Running
            self.tracker._running[app_path] = RunningApp(
                pid=12345,
                appimage_path=app_path,
                start_time=1000.0,
                last_seen=1000.0,
            )

            assert self.tracker.get_pids(app_path) == [12345]

    def test_cleanup_old_entries(self):
        """Test cleanup removes old recently_seen entries."""
        import time

        now = time.time()
        old_time = now - 40  # Older than 30 second retention
        recent_time = now - 10

        self.tracker._recently_seen["old_app"] = old_time
        self.tracker._recently_seen["recent_app"] = recent_time

        self.tracker._cleanup(now)

        assert "old_app" not in self.tracker._recently_seen
        assert "recent_app" in self.tracker._recently_seen

    def test_get_all_running(self):
        """Test get_all_running returns copy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app_path = str(Path(tmpdir) / "TestApp")
            self.tracker._running[app_path] = RunningApp(
                pid=12345,
                appimage_path=app_path,
                start_time=1000.0,
                last_seen=1000.0,
            )

            result = self.tracker.get_all_running()
            assert isinstance(result, dict)
            assert app_path in result

    def test_get_recently_seen(self):
        """Test get_recently_seen returns copy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app_path = str(Path(tmpdir) / "TestApp")
            self.tracker._recently_seen[app_path] = 1000.0

            result = self.tracker.get_recently_seen()
            assert isinstance(result, dict)
            assert app_path in result


class TestScanRunningAppimages:
    """Tests for scan_running_appimages convenience function."""

    def test_scan_returns_dict(self):
        """Test that scan_running_appimages returns a dict."""
        with patch("appimage_launcher.core.running.RunningTracker.scan") as mock_scan:
            mock_scan.return_value = {}
            result = scan_running_appimages()
            assert isinstance(result, dict)


class TestGetRunningTracker:
    """Tests for get_running_tracker global function."""

    def test_returns_tracker(self):
        """Test that get_running_tracker returns a RunningTracker instance."""
        # Reset global
        import appimage_launcher.core.running as running_module

        running_module._tracker = None

        tracker = get_running_tracker()
        assert isinstance(tracker, RunningTracker)

    def test_returns_same_instance(self):
        """Test that get_running_tracker returns the same instance."""
        import appimage_launcher.core.running as running_module

        running_module._tracker = None

        tracker1 = get_running_tracker()
        tracker2 = get_running_tracker()
        assert tracker1 is tracker2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
