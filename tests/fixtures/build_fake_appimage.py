"""Build fake AppImages for testing.

Creates minimal valid AppImages (type 2) with controllable metadata.
"""

from __future__ import annotations

import base64
import struct
import subprocess
import tempfile
from pathlib import Path

from appimage_launcher.constants import (
    APPIMAGE_MAGIC_TYPE2,
    ELF_MAGIC,
)


def build_fake_appimage(
    output_path: Path,
    name: str = "TestApp",
    version: str = "1.0.0",
    app_id: str = "com.example.testapp",
    comment: str = "A test application",
    categories: list[str] | None = None,
    icon_data: bytes | None = None,
    update_info: str | None = None,
    architecture: str = "x86_64",
) -> Path:
    """Build a minimal valid AppImage for testing.

    Creates:
    - Minimal ELF header with AI\\x02 magic at offset 8
    - Section headers with optional .upd_info and .sha256_sig
    - Squashfs filesystem with .desktop, .DirIcon, and metainfo
    """
    if categories is None:
        categories = ["Utility"]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # 1. Create squashfs contents
        squashfs_dir = tmpdir_path / "squashfs-root"
        squashfs_dir.mkdir()

        # Create .desktop file
        desktop_content = f"""[Desktop Entry]
Type=Application
Name={name}
Version={version}
Comment={comment}
Categories={";".join(categories)};"""
        if icon_data:
            desktop_content += "\nIcon=test-icon"
        desktop_content += f"\nX-AppImage-Version={version}\n"
        (squashfs_dir / f"{name}.desktop").write_text(desktop_content)

        # Create .DirIcon (symlink to icon or actual icon)
        if icon_data:
            icon_path = squashfs_dir / ".DirIcon"
            icon_path.write_bytes(icon_data)
        else:
            # Create a minimal PNG (1x1 transparent)
            minimal_png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
            (squashfs_dir / ".DirIcon").write_bytes(minimal_png)

        # Create metainfo
        metainfo_dir = squashfs_dir / "usr" / "share" / "metainfo"
        metainfo_dir.mkdir(parents=True)
        metainfo_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>{app_id}</id>
  <name>{name}</name>
  <summary>{comment}</summary>
  <description>
    <p>{comment}</p>
  </description>
  <releases>
    <release version="{version}" date="{get_current_date()}"/>
  </releases>
</component>"""
        (metainfo_dir / f"{app_id}.metainfo.xml").write_text(metainfo_content)

        # Create icon in hicolor if provided
        if icon_data:
            icon_dir = squashfs_dir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
            icon_dir.mkdir(parents=True)
            (icon_dir / "test-icon.png").write_bytes(icon_data)

        # 2. Build squashfs
        squashfs_path = tmpdir_path / "appimage.squashfs"
        subprocess.run(
            ["mksquashfs", str(squashfs_dir), str(squashfs_path), "-noappend", "-comp", "gzip"],
            check=True,
            capture_output=True,
        )

        # 3. Build ELF stub (now includes squashfs)
        elf_path = tmpdir_path / "appimage.elf"
        build_elf_stub(elf_path, squashfs_path, update_info)

        # 4. The ELF file now contains the complete AppImage (ELF + section data + squashfs)
        # Just copy it to the output path
        import shutil

        shutil.copy2(elf_path, output_path)
        output_path.chmod(0o755)

    return output_path


def build_elf_stub(elf_path: Path, squashfs_path: Path, update_info: str | None = None) -> None:
    """Build minimal ELF stub with AppImage magic and section headers."""
    _ = squashfs_path.stat().st_size

    # ELF64 header (64 bytes)
    # Offset 0: e_ident (16 bytes)
    e_ident = bytearray(16)
    e_ident[0:4] = ELF_MAGIC  # \x7fELF
    e_ident[4] = 2  # ELFCLASS64
    e_ident[5] = 1  # ELFDATA2LSB (little endian)
    e_ident[6] = 1  # EV_CURRENT
    e_ident[7] = 0  # ELFOSABI_SYSV
    e_ident[8:11] = APPIMAGE_MAGIC_TYPE2  # AI\x02 at offset 8 (3 bytes: AI + type)
    # e_ident[11:16] = padding

    # Determine sections based on update_info
    has_upd_info = update_info is not None
    if has_upd_info:
        # 4 sections: NULL, .upd_info, .sha256_sig, .shstrtab
        num_sections = 4
    else:
        # 3 sections: NULL, .sha256_sig, .shstrtab
        num_sections = 3

    # Build string table and section data
    if has_upd_info:
        shstrtab_data = b"\x00.upd_info\x00.sha256_sig\x00.shstrtab\x00"
        upd_info_bytes = update_info.encode("utf-8")
        upd_info_size = len(upd_info_bytes)
        section_data_size = (
            upd_info_size + 32 + len(shstrtab_data)
        )  # .upd_info + .sha256_sig + .shstrtab
    else:
        shstrtab_data = b"\x00.sha256_sig\x00.shstrtab\x00"
        section_data_size = 32 + len(shstrtab_data)  # .sha256_sig + .shstrtab

    # Layout:
    # - ELF header at offset 0 (64 bytes)
    # - Section data at offset 64 (section_data_size bytes)
    # - Section headers at offset 64 + section_data_size (aligned to 8 bytes)
    # - Squashfs at offset e_shoff + e_shentsize * e_shnum (after section headers)

    section_data_offset = 64  # Right after ELF header
    section_headers_offset = section_data_offset + section_data_size
    # Align section headers to 8 bytes
    section_headers_offset = (section_headers_offset + 7) & ~7

    e_shoff = section_headers_offset
    e_shentsize = 64
    e_shnum = num_sections
    e_shstrndx = num_sections - 1  # Last section is .shstrtab

    # Squashfs offset = end of section headers
    squashfs_offset = e_shoff + e_shentsize * e_shnum

    # Calculate section data offsets (relative to file start)
    if has_upd_info:
        upd_info_offset = section_data_offset
        sig_offset = upd_info_offset + upd_info_size
        shstrtab_offset = sig_offset + 32
    else:
        sig_offset = section_data_offset
        shstrtab_offset = sig_offset + 32

    # Build ELF header
    elf_header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        bytes(e_ident),  # e_ident
        2,  # e_type (ET_EXEC)
        62,  # e_machine (EM_X86_64)
        1,  # e_version
        0,  # e_entry
        0,  # e_phoff (no program headers)
        e_shoff,  # e_shoff
        0,  # e_flags
        64,  # e_ehsize
        0,  # e_phentsize
        0,  # e_phnum
        e_shentsize,  # e_shentsize
        e_shnum,  # e_shnum
        e_shstrndx,  # e_shstrndx
    )

    # Section headers (each 64 bytes)
    # Section 0: NULL
    shdr_null = struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    section_headers = [shdr_null]

    if has_upd_info:
        # Section 1: .upd_info
        shdr_upd_info = struct.pack(
            "<IIQQQQIIQQ",
            1,  # sh_name (offset in .shstrtab)
            1,  # sh_type (SHT_PROGBITS)
            0,  # sh_flags
            0,  # sh_addr
            upd_info_offset,  # sh_offset
            upd_info_size,  # sh_size
            0,  # sh_link
            0,  # sh_info
            1,  # sh_addralign
            0,  # sh_entsize
        )
        section_headers.append(shdr_upd_info)

    # Section for .sha256_sig
    shdr_sha256_sig = struct.pack(
        "<IIQQQQIIQQ",
        1 if not has_upd_info else 10,  # sh_name (offset in .shstrtab)
        1,  # sh_type (SHT_PROGBITS)
        0,  # sh_flags
        0,  # sh_addr
        sig_offset,  # sh_offset
        32,  # sh_size (SHA-256 = 32 bytes)
        0,  # sh_link
        0,  # sh_info
        1,  # sh_addralign
        0,  # sh_entsize
    )
    section_headers.append(shdr_sha256_sig)

    # Section for .shstrtab
    shdr_shstrtab = struct.pack(
        "<IIQQQQIIQQ",
        12 if not has_upd_info else 21,  # sh_name (offset in .shstrtab pointing to ".shstrtab")
        3,  # sh_type (SHT_STRTAB)
        0,  # sh_flags
        0,  # sh_addr
        shstrtab_offset,  # sh_offset
        len(shstrtab_data),  # sh_size
        0,  # sh_link
        0,  # sh_info
        1,  # sh_addralign
        0,  # sh_entsize
    )
    section_headers.append(shdr_shstrtab)

    # Write ELF + section data + section headers + squashfs
    with open(elf_path, "wb") as f:
        # Write ELF header
        f.write(elf_header)

        # Pad to section_data_offset (should be 64)
        current_pos = f.tell()
        if current_pos < section_data_offset:
            f.write(b"\x00" * (section_data_offset - current_pos))

        # Write section data
        if has_upd_info:
            f.write(upd_info_bytes)
        f.write(b"\x00" * 32)  # .sha256_sig
        f.write(shstrtab_data)

        # Pad to section_headers_offset
        current_pos = f.tell()
        if current_pos < section_headers_offset:
            f.write(b"\x00" * (section_headers_offset - current_pos))

        # Write section headers
        for shdr in section_headers:
            f.write(shdr)

        # Pad to squashfs_offset
        current_pos = f.tell()
        if current_pos < squashfs_offset:
            f.write(b"\x00" * (squashfs_offset - current_pos))

        # Append squashfs at the correct offset
        with open(squashfs_path, "rb") as sf:
            f.write(sf.read())


def get_current_date() -> str:
    """Get current date in YYYY-MM-DD format."""
    from datetime import date

    return date.today().isoformat()


def build_corrupt_appimage(output_path: Path, corruption_type: str = "bad_magic") -> Path:
    """Build a corrupt AppImage for testing error handling."""
    if corruption_type == "bad_magic":
        # Valid ELF but wrong AppImage magic
        data = bytearray(64)
        data[0:4] = ELF_MAGIC
        data[4] = 2  # 64-bit
        data[5] = 1  # little endian
        data[8:10] = b"XX"  # Wrong magic
        output_path.write_bytes(data)
    elif corruption_type == "truncated":
        # Truncated ELF header
        output_path.write_bytes(b"\x7fELF")
    elif corruption_type == "bad_squashfs":
        # Valid ELF/AppImage magic but bad squashfs
        data = bytearray(64)
        data[0:4] = ELF_MAGIC
        data[4] = 2
        data[5] = 1
        data[8:10] = APPIMAGE_MAGIC_TYPE2
        # Set e_shoff to point to bad data
        struct.pack_into("<Q", data, 0x28, 100)  # e_shoff
        struct.pack_into("<H", data, 0x3A, 64)  # e_shentsize
        struct.pack_into("<H", data, 0x3C, 1)  # e_shnum
        output_path.write_bytes(data + b"not squashfs")
    elif corruption_type == "type1":
        # AppImage type 1 (ISO9660)
        data = bytearray(64)
        data[0:4] = ELF_MAGIC
        data[4] = 2
        data[5] = 1
        data[8:11] = b"AI\x01"  # Type 1 (3 bytes: AI + type)
        output_path.write_bytes(data)
    else:
        raise ValueError(f"Unknown corruption type: {corruption_type}")

    output_path.chmod(0o755)
    return output_path


if __name__ == "__main__":
    # Test building a fake AppImage
    import sys

    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/test.appimage")
    build_fake_appimage(output)
    print(f"Built fake AppImage at {output}")
