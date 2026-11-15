"""Pytest configuration for AppImage Launcher tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_environment():
    """Isolate test environment by setting APPIMAGE_LAUNCHER_HOME."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["APPIMAGE_LAUNCHER_HOME"] = tmpdir
        yield
        # Clean up
        if "APPIMAGE_LAUNCHER_HOME" in os.environ:
            del os.environ["APPIMAGE_LAUNCHER_HOME"]


@pytest.fixture
def temp_home():
    """Provide a temporary home directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def fake_appimage(temp_home):
    """Create a fake AppImage for testing."""
    from tests.fixtures.build_fake_appimage import build_fake_appimage

    appimage_path = temp_home / "test.appimage"
    build_fake_appimage(appimage_path)
    return appimage_path


# Ensure APPIMAGE_LAUNCHER_HOME is set for all tests
def pytest_configure(config):
    """Configure pytest."""
    if "APPIMAGE_LAUNCHER_HOME" not in os.environ:
        os.environ["APPIMAGE_LAUNCHER_HOME"] = tempfile.mkdtemp()
