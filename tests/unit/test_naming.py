"""Tests for naming utilities."""

import pytest

from appimage_launcher.core.naming import classify_collision, render_template, sanitize_name


class TestSanitizeName:
    """Tests for sanitize_name function."""

    def test_basic_sanitization(self):
        assert sanitize_name("My App") == "My App"
        assert sanitize_name("My  App") == "My App"  # collapse whitespace
        assert sanitize_name("  My App  ") == "My App"  # strip spaces
        assert sanitize_name("..My App..") == "My App"  # strip dots

    def test_strip_control_chars(self):
        assert sanitize_name("My\x00App") == "MyApp"
        assert sanitize_name("My\nApp") == "MyApp"
        assert sanitize_name("My\tApp") == "MyApp"

    def test_strip_path_separators(self):
        assert sanitize_name("My/App") == "MyApp"
        assert sanitize_name("My\\App") == "MyApp"

    def test_max_bytes(self):
        # 100 byte limit
        long_name = "A" * 200
        result = sanitize_name(long_name)
        assert len(result.encode("utf-8")) <= 100

    def test_utf8_boundary(self):
        # Ensure we don't cut in middle of codepoint
        name = "🎉" * 50  # Each emoji is 4 bytes
        result = sanitize_name(name)
        # Should be valid UTF-8
        result.encode("utf-8")

    def test_reserved_suffixes(self):
        assert sanitize_name("MyApp.disabled") == "MyApp"
        assert sanitize_name("MyApp.part") == "MyApp"
        assert sanitize_name("MyApp.crdownload") == "MyApp"
        assert sanitize_name("MyApp.zs-old") == "MyApp"

    def test_no_leading_dot(self):
        assert sanitize_name(".hidden") == "hidden"
        assert sanitize_name("..double") == "double"

    def test_empty_name(self):
        assert sanitize_name("") == "Unnamed"
        assert sanitize_name("   ") == "Unnamed"
        assert sanitize_name("...") == "Unnamed"


class TestRenderTemplate:
    """Tests for render_template function."""

    def test_basic_template(self):
        result = render_template("{name}", "MyApp")
        assert result == "MyApp"

    def test_version_template(self):
        result = render_template("{name} {version}", "MyApp", "1.0.0")
        assert result == "MyApp 1.0.0"

    def test_app_id_template(self):
        result = render_template("{app_id}", "MyApp", app_id="com.example.app")
        assert result == "com.example.app"

    def test_missing_values(self):
        result = render_template("{name} {version}", "MyApp", version=None)
        assert result == "MyApp"

    def test_sanitization_applied(self):
        result = render_template("{name}", "My/App")
        assert result == "MyApp"


class TestClassifyCollision:
    """Tests for classify_collision function."""

    def test_no_collision(self):
        from pathlib import Path

        result = classify_collision(Path("/tmp/nonexistent"), "com.example.app", {})
        assert result.type == "none"

    def test_same_app_id(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "MyApp"
            target.touch()
            existing = {"MyApp": {"app_id": "com.example.app", "record": None}}
            result = classify_collision(target, "com.example.app", existing)
            assert result.type == "same_app_id"

    def test_different_app_id(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "MyApp"
            target.touch()
            existing = {"MyApp": {"app_id": "com.other.app", "record": None}}
            result = classify_collision(target, "com.example.app", existing)
            assert result.type == "different_app_id"
            assert result.suggested_name is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
