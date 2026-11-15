"""Tests for desktop file and AppStream parsing."""

import tempfile
from pathlib import Path

import pytest

from appimage_launcher.core.desktop import parse_appstream, parse_desktop_file


class TestParseDesktopFile:
    """Tests for parse_desktop_file function."""

    def test_basic_desktop_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".desktop", delete=False) as f:
            f.write("""[Desktop Entry]
Type=Application
Name=TestApp
Comment=A test application
Categories=Utility;Development;
Exec=/path/to/app
Icon=test-icon
""")
            f.flush()
            result = parse_desktop_file(Path(f.name))

        assert result["Type"] == "Application"
        assert result["Name"] == "TestApp"
        assert result["Comment"] == "A test application"
        assert result["Categories"] == "Utility;Development;"
        assert result["Exec"] == "/path/to/app"
        assert result["Icon"] == "test-icon"

    def test_desktop_file_with_localized_names(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".desktop", delete=False) as f:
            f.write("""[Desktop Entry]
Type=Application
Name=TestApp
Name[de]=TestApp Deutsch
Name[fr]=TestApp Français
Comment=A test application
""")
            f.flush()
            result = parse_desktop_file(Path(f.name))

        # Should only get the non-localized Name
        assert result["Name"] == "TestApp"
        assert "Name[de]" not in result
        assert "Name[fr]" not in result

    def test_desktop_file_ignores_other_sections(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".desktop", delete=False) as f:
            f.write("""[Desktop Entry]
Name=TestApp
[Desktop Action Something]
Name=Action
Exec=something
""")
            f.flush()
            result = parse_desktop_file(Path(f.name))

        assert result["Name"] == "TestApp"
        assert "Exec" not in result  # From other section

    def test_desktop_file_empty_and_comments(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".desktop", delete=False) as f:
            f.write("""
# This is a comment

[Desktop Entry]

Name=TestApp

# Another comment
Comment=Test comment
""")
            f.flush()
            result = parse_desktop_file(Path(f.name))

        assert result["Name"] == "TestApp"
        assert result["Comment"] == "Test comment"

    def test_desktop_file_missing_file(self):
        result = parse_desktop_file(Path("/nonexistent/file.desktop"))
        assert result == {}

    def test_desktop_file_malformed_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".desktop", delete=False) as f:
            f.write("""[Desktop Entry]
Name=TestApp
InvalidLineWithoutEquals
KeyOnly=
=ValueOnly
=AlsoInvalid
""")
            f.flush()
            result = parse_desktop_file(Path(f.name))

        assert result["Name"] == "TestApp"
        assert "KeyOnly" in result
        assert result["KeyOnly"] == ""
        assert "=ValueOnly" not in result  # Invalid key


class TestParseAppStream:
    """Tests for parse_appstream function."""

    def test_basic_appstream(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>com.example.testapp</id>
  <name>TestApp</name>
  <summary>A test application</summary>
  <description>
    <p>This is a test application.</p>
  </description>
  <releases>
    <release version="1.0.0" date="2024-01-15"/>
  </releases>
</component>""")
            f.flush()
            result = parse_appstream(Path(f.name))

        assert result["app_id"] == "com.example.testapp"
        assert result["name"] == "TestApp"
        assert result["comment"] == "A test application"
        assert result["version"] == "1.0.0"

    def test_appstream_with_namespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("""<?xml version="1.0" encoding="UTF-8"?>
<component xmlns="http://www.freedesktop.org/standards/appstream" type="desktop">
  <id>com.example.testapp</id>
  <name>TestApp</name>
  <summary>A test application</summary>
  <releases>
    <release version="2.0.0" date="2024-06-01"/>
  </releases>
</component>""")
            f.flush()
            result = parse_appstream(Path(f.name))

        assert result["app_id"] == "com.example.testapp"
        assert result["name"] == "TestApp"
        assert result["version"] == "2.0.0"

    def test_appstream_multiple_releases(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>com.example.testapp</id>
  <name>TestApp</name>
  <releases>
    <release version="1.0.0" date="2024-01-01"/>
    <release version="1.1.0" date="2024-02-01"/>
    <release version="2.0.0" date="2024-06-01"/>
  </releases>
</component>""")
            f.flush()
            result = parse_appstream(Path(f.name))

        # Should get the last release
        assert result["version"] == "2.0.0"

    def test_appstream_missing_elements(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>com.example.testapp</id>
</component>""")
            f.flush()
            result = parse_appstream(Path(f.name))

        assert result["app_id"] == "com.example.testapp"
        assert "name" not in result
        assert "comment" not in result
        assert "version" not in result

    def test_appstream_malformed_xml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>com.example.testapp</id>
  <name>TestApp
</component>""")
            f.flush()
            result = parse_appstream(Path(f.name))

        # Should not crash, return empty or partial result
        assert isinstance(result, dict)

    def test_appstream_missing_file(self):
        result = parse_appstream(Path("/nonexistent/file.xml"))
        assert result == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
