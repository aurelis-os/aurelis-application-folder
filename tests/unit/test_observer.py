"""Tests for InotifyObserver."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from appimage_launcher.daemon.observer import InotifyObserver


class TestInotifyObserver:
    """Tests for InotifyObserver."""

    def test_should_ignore_partial_downloads(self):
        """Test that partial download patterns are ignored."""
        loop = asyncio.new_event_loop()
        try:
            observer = InotifyObserver(Path("/tmp"), lambda x: None, loop=loop)

            # Test patterns from IGNORED_PATTERNS
            assert observer._should_ignore("test.part") is True
            assert observer._should_ignore("test.crdownload") is True
            assert observer._should_ignore("test.partial") is True
            assert observer._should_ignore("test.tmp") is True
            assert observer._should_ignore("test.download") is True
            assert observer._should_ignore("test~") is True
            assert observer._should_ignore("test.zs-old") is True
            assert observer._should_ignore("test.disabled") is True
            assert observer._should_ignore(".hidden") is True

            # Test non-ignored files
            assert observer._should_ignore("test.AppImage") is False
            assert observer._should_ignore("test.txt") is False
            assert observer._should_ignore("MyApp") is False
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_write_file_once_callback_within_3s(self):
        """Test that writing a file once triggers exactly one callback within ~3s."""
        callback_results = []

        def callback(entries):
            callback_results.extend(entries)

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = Path(tmpdir)
            loop = asyncio.get_event_loop()
            observer = InotifyObserver(watch_path, callback, loop=loop)
            observer.start()

            try:
                # Create a test file (write once)
                test_file = watch_path / "test.AppImage"
                test_file.write_bytes(b"test content")

                # Wait for callback - should be called within ~3s (stability_window + buffer)
                for _ in range(60):  # 60 * 0.05 = 3s
                    if len(callback_results) >= 1:
                        break
                    await asyncio.sleep(0.05)

                assert len(callback_results) == 1
                assert callback_results[0].path == test_file
                assert callback_results[0].event_type == "CLOSE_WRITE"
            finally:
                observer.stop()

    @pytest.mark.asyncio
    async def test_keep_appending_no_callback_until_stops(self):
        """Test that continuous appending doesn't trigger callback until writing stops."""
        callback_results = []

        def callback(entries):
            callback_results.extend(entries)

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = Path(tmpdir)
            loop = asyncio.get_event_loop()
            observer = InotifyObserver(watch_path, callback, loop=loop)
            observer.start()

            try:
                test_file = watch_path / "test.AppImage"

                # Keep appending for 3 seconds
                start_time = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - start_time < 3.0:
                    with open(test_file, "ab") as f:
                        f.write(b"x" * 1000)
                    await asyncio.sleep(0.05)

                # Should not have called callback yet (file still being written)
                assert len(callback_results) == 0

                # Wait for stability window to pass
                await asyncio.sleep(2.5)

                # Now callback should have been called exactly once
                assert len(callback_results) == 1
                assert callback_results[0].path == test_file
            finally:
                observer.stop()

    @pytest.mark.asyncio
    async def test_delete_before_stable_no_callback_no_leaked_state(self):
        """Test that deleting a file before it's stable results in no callback and no leaked state."""
        callback_results = []

        def callback(entries):
            callback_results.extend(entries)

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = Path(tmpdir)
            loop = asyncio.get_event_loop()
            observer = InotifyObserver(watch_path, callback, loop=loop)
            observer.start()

            try:
                test_file = watch_path / "test.AppImage"

                # Create file
                test_file.write_bytes(b"initial content")

                # Wait a bit but not enough for stability
                await asyncio.sleep(0.5)

                # Delete the file before stability window
                test_file.unlink()

                # Wait longer than stability window
                await asyncio.sleep(3.0)

                # Should not have called callback
                assert len(callback_results) == 0

                # Check no leaked state
                assert len(observer._file_stats) == 0
                assert len(observer._pending_events) == 0
                assert len(observer._debounce_tasks) == 0
            finally:
                observer.stop()

    @pytest.mark.asyncio
    async def test_rename_tracking(self):
        """Test that rename events (MOVED_FROM + MOVED_TO) are paired."""
        callback_results = []

        def callback(entries):
            callback_results.extend(entries)

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = Path(tmpdir)
            loop = asyncio.get_event_loop()
            observer = InotifyObserver(watch_path, callback, loop=loop)
            observer.start()

            try:
                old_file = watch_path / "old.AppImage"
                new_file = watch_path / "new.AppImage"

                old_file.write_bytes(b"content")
                await asyncio.sleep(0.5)

                # Rename the file
                old_file.rename(new_file)

                # Wait for events
                await asyncio.sleep(3.0)

                # Should have received two events: MOVED_FROM and MOVED_TO
                assert len(callback_results) == 2
                event_types = [e.event_type for e in callback_results]
                assert "MOVED_FROM" in event_types
                assert "MOVED_TO" in event_types
            finally:
                observer.stop()

    @pytest.mark.asyncio
    async def test_attribute_change_emits_attrib_event(self):
        """Test that ATTRIB event is emitted when file attributes change."""
        callback_results = []

        def callback(entries):
            callback_results.extend(entries)

        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = Path(tmpdir)
            loop = asyncio.get_event_loop()
            observer = InotifyObserver(watch_path, callback, loop=loop)
            observer.start()

            try:
                test_file = watch_path / "test.AppImage"
                test_file.write_bytes(b"content")
                await asyncio.sleep(0.5)

                # Change file attributes (remove executable bit)
                test_file.chmod(0o644)
                await asyncio.sleep(0.5)

                # Wait for ATTRIB event (debounce 500ms)
                await asyncio.sleep(1.0)

                # Should have received ATTRIB event
                assert len(callback_results) == 1
                assert callback_results[0].event_type == "ATTRIB"
                assert callback_results[0].path == test_file
            finally:
                observer.stop()
