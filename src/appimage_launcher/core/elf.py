"""ELF parsing for AppImage files.

Reads ELF header, finds squashfs offset, reads section headers
for .upd_info and .sha256_sig sections.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from appimage_launcher.constants import (
    APPIMAGE_MAGIC_AT_OFFSET_8,
    ELF_MAGIC,
    SQUASHFS_MAGIC,
)


class ELFError(Exception):
    """Base exception for ELF parsing errors."""

    pass


class NotAnAppImageError(ELFError):
    """File is not an AppImage (missing ELF or AppImage magic)."""

    pass


class UnsupportedTypeError(ELFError):
    """AppImage type 1 (ISO9660) - not supported."""

    pass


class CorruptAppImageError(ELFError):
    """AppImage appears corrupt (bad squashfs magic, etc.)."""

    pass


@dataclass
class ELFHeader:
    """Parsed ELF header information."""

    is_64bit: bool
    is_little_endian: bool
    e_shoff: int  # section header table offset
    e_shentsize: int  # section header entry size
    e_shnum: int  # number of section header entries
    e_shstrndx: int  # section header string table index


def read_elf_header(file: BinaryIO) -> ELFHeader:
    """Read and parse ELF header from file."""
    # Always seek to start
    file.seek(0)
    # Read first 16 bytes to identify ELF class and endianness
    ident = file.read(16)
    if len(ident) < 16 or ident[:4] != ELF_MAGIC:
        raise NotAnAppImageError("Not an ELF file")

    ei_class = ident[4]  # 1=32-bit, 2=64-bit
    ei_data = ident[5]  # 1=little endian, 2=big endian

    is_64bit = ei_class == 2
    is_little_endian = ei_data == 1

    # Seek to AppImage magic at offset 8 (3 bytes: AI + type)
    file.seek(8)
    appimage_magic = file.read(3)

    if len(appimage_magic) < 3:
        raise NotAnAppImageError("Not an AppImage (truncated magic)")

    if appimage_magic[:2] != APPIMAGE_MAGIC_AT_OFFSET_8:
        raise NotAnAppImageError("Not an AppImage (missing AI magic)")

    if appimage_magic[2] == 0x01:
        raise UnsupportedTypeError("AppImage type 1 (ISO9660) not supported")
    elif appimage_magic[2] != 0x02:
        raise NotAnAppImageError("Not an AppImage (unknown type)")

    # Read rest of ELF header
    if is_64bit:
        # ELF64 header is 64 bytes
        file.seek(0)
        header_data = file.read(64)
        if len(header_data) < 64:
            raise CorruptAppImageError("Truncated ELF header")

        # Parse ELF64 header
        # Format: 16 bytes ident + 2 type + 2 machine + 4 version + 8 entry
        # + 8 phoff + 8 shoff + 4 flags + 2 ehsize + 2 phentsize + 2 phnum
        # + 2 shentsize + 2 shnum + 2 shstrndx
        fmt = "<HHIQQQIHHHHHH" if is_little_endian else ">HHIQQQIHHHHHH"
        # Skip ident (16), then: type(2) machine(2) version(4) entry(8) phoff(8) shoff(8)
        # flags(4) ehsize(2) phentsize(2) phnum(2) shentsize(2) shnum(2) shstrndx(2)
        unpacked = struct.unpack(fmt, header_data[16:])
        e_shoff = unpacked[5]  # shoff (index 5: after type, machine, version, entry, phoff)
        e_shentsize = unpacked[10]  # shentsize
        e_shnum = unpacked[11]  # shnum
        e_shstrndx = unpacked[12]  # shstrndx
    else:
        # ELF32 header is 52 bytes
        file.seek(0)
        header_data = file.read(52)
        if len(header_data) < 52:
            raise CorruptAppImageError("Truncated ELF header")

        fmt = "<HHIIIIIHHHHHH" if is_little_endian else ">HHIIIIIHHHHHH"
        unpacked = struct.unpack(fmt, header_data[16:])
        e_shoff = unpacked[4]  # shoff
        e_shentsize = unpacked[9]  # shentsize
        e_shnum = unpacked[10]  # shnum
        e_shstrndx = unpacked[11]  # shstrndx

    return ELFHeader(
        is_64bit=is_64bit,
        is_little_endian=is_little_endian,
        e_shoff=e_shoff,
        e_shentsize=e_shentsize,
        e_shnum=e_shnum,
        e_shstrndx=e_shstrndx,
    )


def find_squashfs_offset(file: BinaryIO, header: ELFHeader) -> int:
    """Calculate and verify squashfs offset from ELF header."""
    offset = header.e_shoff + header.e_shentsize * header.e_shnum

    # Verify squashfs magic at offset
    file.seek(offset)
    magic = file.read(4)
    if magic != SQUASHFS_MAGIC:
        raise CorruptAppImageError(f"Squashfs magic not found at offset {offset}")

    return offset


@dataclass
class SectionHeader:
    """Parsed section header."""

    name: str
    type: int
    flags: int
    addr: int
    offset: int
    size: int
    link: int
    info: int
    addralign: int
    entsize: int


def read_section_headers(file: BinaryIO, header: ELFHeader) -> list[SectionHeader]:
    """Read all section headers."""
    file.seek(header.e_shoff)
    sections = []

    for _ in range(header.e_shnum):
        if header.is_64bit:
            # ELF64 section header: 64 bytes
            data = file.read(64)
            if len(data) < 64:
                raise CorruptAppImageError("Truncated section header")
            fmt = "<IIQQQQIIQQ" if header.is_little_endian else ">IIQQQQIIQQ"
            (
                sh_name,
                sh_type,
                sh_flags,
                sh_addr,
                sh_offset,
                sh_size,
                sh_link,
                sh_info,
                sh_addralign,
                sh_entsize,
            ) = struct.unpack(fmt, data)
        else:
            # ELF32 section header: 40 bytes
            data = file.read(40)
            if len(data) < 40:
                raise CorruptAppImageError("Truncated section header")
            fmt = "<IIIIIIIIII" if header.is_little_endian else ">IIIIIIIIII"
            (
                sh_name,
                sh_type,
                sh_flags,
                sh_addr,
                sh_offset,
                sh_size,
                sh_link,
                sh_info,
                sh_addralign,
                sh_entsize,
            ) = struct.unpack(fmt, data)

        sections.append(
            SectionHeader(
                name="",  # Will be filled after reading string table
                type=sh_type,
                flags=sh_flags,
                addr=sh_addr,
                offset=sh_offset,
                size=sh_size,
                link=sh_link,
                info=sh_info,
                addralign=sh_addralign,
                entsize=sh_entsize,
            )
        )

    # Read section header string table
    if header.e_shstrndx < len(sections):
        strtab_section = sections[header.e_shstrndx]
        file.seek(strtab_section.offset)
        strtab_data = file.read(strtab_section.size)

        # Fill in names
        for section in sections:
            if section.name == "" and section.type != 0:  # Not NULL section
                section.name if isinstance(section.name, int) else 0
                # Actually sh_name is an offset into string table
                # We need to re-read with proper name offsets
                pass

    # Re-read with proper name resolution
    file.seek(header.e_shoff)
    sections = []
    for _ in range(header.e_shnum):
        if header.is_64bit:
            data = file.read(64)
            fmt = "<IIQQQQIIQQ" if header.is_little_endian else ">IIQQQQIIQQ"
            (
                sh_name,
                sh_type,
                sh_flags,
                sh_addr,
                sh_offset,
                sh_size,
                sh_link,
                sh_info,
                sh_addralign,
                sh_entsize,
            ) = struct.unpack(fmt, data)
        else:
            data = file.read(40)
            fmt = "<IIIIIIIIII" if header.is_little_endian else ">IIIIIIIIII"
            (
                sh_name,
                sh_type,
                sh_flags,
                sh_addr,
                sh_offset,
                sh_size,
                sh_link,
                sh_info,
                sh_addralign,
                sh_entsize,
            ) = struct.unpack(fmt, data)

        # Get name from string table
        name = ""
        if sh_name < len(strtab_data):
            name_end = strtab_data.find(b"\x00", sh_name)
            if name_end >= 0:
                name = strtab_data[sh_name:name_end].decode("utf-8", errors="replace")

        sections.append(
            SectionHeader(
                name=name,
                type=sh_type,
                flags=sh_flags,
                addr=sh_addr,
                offset=sh_offset,
                size=sh_size,
                link=sh_link,
                info=sh_info,
                addralign=sh_addralign,
                entsize=sh_entsize,
            )
        )

    return sections


def read_section(file: BinaryIO, header: ELFHeader, section_name: str) -> bytes | None:
    """Read a specific section by name."""
    sections = read_section_headers(file, header)
    for section in sections:
        if section.name == section_name:
            file.seek(section.offset)
            return file.read(section.size)
    return None


def read_upd_info(file: BinaryIO) -> str | None:
    """Read .upd_info section from AppImage."""
    header = read_elf_header(file)
    data = read_section(file, header, ".upd_info")
    if data:
        return data.decode("utf-8", errors="replace").strip()
    return None


def read_sha256_sig(file: BinaryIO) -> bytes | None:
    """Read .sha256_sig section from AppImage."""
    header = read_elf_header(file)
    return read_section(file, header, ".sha256_sig")


def validate_appimage(path: Path) -> tuple[ELFHeader, int]:
    """Validate an AppImage file and return header and squashfs offset."""
    with open(path, "rb") as f:
        header = read_elf_header(f)
        offset = find_squashfs_offset(f, header)
    return header, offset
