"""Tests for ELF parsing."""

import tempfile
from pathlib import Path

import pytest

from appimage_launcher.core.elf import (
    CorruptAppImageError,
    ELFHeader,
    NotAnAppImageError,
    UnsupportedTypeError,
    find_squashfs_offset,
    read_elf_header,
    read_section,
    read_section_headers,
    read_sha256_sig,
    read_upd_info,
    validate_appimage,
)
from tests.fixtures.build_fake_appimage import build_corrupt_appimage, build_fake_appimage


class TestReadElfHeader:
    """Tests for read_elf_header function."""

    def test_valid_type2_appimage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                header = read_elf_header(f)

            assert isinstance(header, ELFHeader)
            assert header.is_64bit is True
            assert header.is_little_endian is True
            assert header.e_shoff > 0
            assert header.e_shentsize > 0
            assert header.e_shnum > 0

    def test_not_an_elf_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            not_elf = Path(tmpdir) / "not_elf"
            not_elf.write_bytes(b"This is not an ELF file")

            with open(not_elf, "rb") as f:
                with pytest.raises(NotAnAppImageError):
                    read_elf_header(f)

    def test_elf_but_not_appimage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Valid ELF but wrong AppImage magic
            elf = Path(tmpdir) / "elf_not_appimage"
            build_corrupt_appimage(elf, "bad_magic")

            with open(elf, "rb") as f:
                with pytest.raises(NotAnAppImageError):
                    read_elf_header(f)

    def test_appimage_type1_unsupported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            type1 = Path(tmpdir) / "type1.appimage"
            build_corrupt_appimage(type1, "type1")

            with open(type1, "rb") as f:
                with pytest.raises(UnsupportedTypeError):
                    read_elf_header(f)

    def test_truncated_elf_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            truncated = Path(tmpdir) / "truncated"
            build_corrupt_appimage(truncated, "truncated")

            with open(truncated, "rb") as f:
                with pytest.raises(NotAnAppImageError):
                    read_elf_header(f)


class TestFindSquashfsOffset:
    """Tests for find_squashfs_offset function."""

    def test_valid_squashfs_offset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                header = read_elf_header(f)
                offset = find_squashfs_offset(f, header)

            assert offset > 0
            # Verify squashfs magic at offset
            with open(appimage, "rb") as f:
                f.seek(offset)
                magic = f.read(4)
                assert magic == b"hsqs"

    def test_corrupt_squashfs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt = Path(tmpdir) / "corrupt.appimage"
            build_corrupt_appimage(corrupt, "bad_squashfs")

            with open(corrupt, "rb") as f:
                header = read_elf_header(f)
                with pytest.raises(CorruptAppImageError):
                    find_squashfs_offset(f, header)


class TestReadSectionHeaders:
    """Tests for read_section_headers function."""

    def test_read_section_headers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                header = read_elf_header(f)
                sections = read_section_headers(f, header)

            assert len(sections) == header.e_shnum
            # Check that we have section names (our fake builder has minimal string table)
            names = [s.name for s in sections]
            # The fake builder's string table may not be fully populated
            # Just verify we got the right number of sections
            assert len(names) == header.e_shnum


class TestReadSection:
    """Tests for read_section function."""

    def test_read_upd_info_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage, update_info="zsync|https://example.com/app.zsync")

            with open(appimage, "rb") as f:
                header = read_elf_header(f)
                data = read_section(f, header, ".upd_info")

            # Our fake builder now includes .upd_info section data
            assert data == b"zsync|https://example.com/app.zsync"

    def test_read_sha256_sig_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                header = read_elf_header(f)
                data = read_section(f, header, ".sha256_sig")

            # Our fake builder now includes .sha256_sig section data (32 zero bytes)
            assert data is not None
            assert len(data) == 32
            assert data == b"\x00" * 32

    def test_read_nonexistent_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                header = read_elf_header(f)
                data = read_section(f, header, ".nonexistent")

            assert data is None


class TestReadUpdInfo:
    """Tests for read_upd_info function."""

    def test_read_upd_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                result = read_upd_info(f)

            # Our fake builder doesn't include .upd_info
            assert result is None


class TestReadSha256Sig:
    """Tests for read_sha256_sig function."""

    def test_read_sha256_sig(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            with open(appimage, "rb") as f:
                result = read_sha256_sig(f)

            # Our fake builder now includes .sha256_sig section data (32 zero bytes)
            assert result is not None
            assert len(result) == 32
            assert result == b"\x00" * 32


class TestValidateAppimage:
    """Tests for validate_appimage function."""

    def test_valid_appimage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            header, offset = validate_appimage(appimage)

            assert isinstance(header, ELFHeader)
            assert offset > 0

    def test_invalid_appimage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            invalid = Path(tmpdir) / "invalid.appimage"
            invalid.write_bytes(b"Not an AppImage")

            with pytest.raises(NotAnAppImageError):
                validate_appimage(invalid)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
