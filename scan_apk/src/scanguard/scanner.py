"""Core scan logic for ScanGuard — detect masquerading / double-extension files.

Same logic as the Windows CLI scanner (scan_suspicious.py):

1. Double-extension masquerades — ``photo.png.exe``, ``invoice.pdf.scr``.
2. Magic-byte mismatches — content doesn't match the displayed extension.

Pure standard library. Reads only the first 16 bytes of each file.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Signatures and extension tables
# ---------------------------------------------------------------------------

# Extensions that can execute / run code when opened.
EXEC_EXTENSIONS = {
    "exe", "scr", "com", "pif", "bat", "cmd", "msi", "msp", "mst",
    "dll", "ocx", "sys", "cpl", "lnk", "jar", "apk", "hta", "gadget",
    "vbs", "vbe", "js", "jse", "wsf", "wsh", "ps1", "psm1", "reg", "inf",
}

# Extensions people trust at a glance — the ones masquerades fake.
HARMLESS_LOOKING_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "ico", "svg",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",
    "txt", "rtf", "csv", "log", "md", "zip", "rar", "7z", "tar", "gz",
    "mp3", "mp4", "mov", "avi", "mkv", "wav", "flac", "ogg",
    "html", "htm", "xml", "json", "sqlite", "db",
}


def _sig(offset: int, *probes: bytes) -> tuple[tuple[int, bytes], ...]:
    """Build a signature entry: probes at a given byte offset."""
    return tuple((offset, p) for p in probes)


# ext -> signature probes.  A file matches its claimed extension if any
# probe matches at its offset.
MAGIC = {
    "png":    _sig(0, b"\x89PNG\r\n\x1a\n"),
    "jpg":    _sig(0, b"\xff\xd8\xff"),
    "jpeg":   _sig(0, b"\xff\xd8\xff"),
    "gif":    _sig(0, b"GIF87a", b"GIF89a"),
    "bmp":    _sig(0, b"BM"),
    "webp":   _sig(0, b"RIFF"),
    "ico":    _sig(0, b"\x00\x00\x01\x00"),
    "tif":    _sig(0, b"II*\x00", b"MM\x00*"),
    "tiff":   _sig(0, b"II*\x00", b"MM\x00*"),
    "pdf":    _sig(0, b"%PDF-"),
    "zip":    _sig(0, b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "docx":   _sig(0, b"PK\x03\x04"),
    "xlsx":   _sig(0, b"PK\x03\x04"),
    "pptx":   _sig(0, b"PK\x03\x04"),
    "jar":    _sig(0, b"PK\x03\x04"),
    "apk":    _sig(0, b"PK\x03\x04"),
    "doc":    _sig(0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    "xls":    _sig(0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    "ppt":    _sig(0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    "msi":    _sig(0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    "rtf":    _sig(0, b"{\\rtf"),
    "rar":    _sig(0, b"Rar!\x1a\x07"),
    "7z":     _sig(0, b"7z\xbc\xaf\x27\x1c"),
    "exe":    _sig(0, b"MZ"),
    "dll":    _sig(0, b"MZ"),
    "scr":    _sig(0, b"MZ"),
    "sys":    _sig(0, b"MZ"),
    "com":    _sig(0, b"MZ"),
    "lnk":    _sig(0, b"\x4c\x00\x00\x00"),
    "mp3":    _sig(0, b"ID3"),
    "mp4":    _sig(4, b"ftyp"),
    "mov":    _sig(4, b"ftyp"),
    "avi":    _sig(0, b"RIFF"),
    "wav":    _sig(0, b"RIFF"),
    "flac":   _sig(0, b"fLaC"),
    "ogg":    _sig(0, b"OggS"),
    "elf":    _sig(0, b"\x7fELF"),
    "html":   _sig(0, b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML", b"<head", b"<HEAD"),
    "htm":    _sig(0, b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML", b"<head", b"<HEAD"),
    "xml":    _sig(0, b"<?xml"),
    "sqlite": _sig(0, b"SQLite format 3\x00"),
}

# Human-readable labels for the actual content, used in reports.
# Ordered most-specific-first so sniffing picks the best match.
MAGIC_LABELS = [
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff",      "JPEG image"),
    (b"GIF87a",            "GIF image"),
    (b"GIF89a",            "GIF image"),
    (b"%PDF-",             "PDF document"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE2 document (old Word/Excel, MSI)"),
    (b"SQLite format 3\x00", "SQLite database"),
    (b"{\\rtf",            "RTF document"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"Rar!\x1a\x07",      "RAR archive"),
    (b"PK\x03\x04",        "ZIP archive"),
    (b"\x7fELF",           "ELF binary (Linux)"),
    (b"\x4c\x00\x00\x00",  "Windows shortcut (LNK)"),
    (b"ID3",               "MP3 audio"),
    (b"fLaC",              "FLAC audio"),
    (b"OggS",              "Ogg audio/video"),
    (b"MZ",                "Windows executable (PE)"),
]

_HEADER_LEN = 16


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def split_extensions(name: str) -> list[str]:
    """Return every dot-suffix of the name, lowercased.

    ``photo.png.exe`` -> ``['png', 'exe']``; ``notes.txt`` -> ``['txt']``.
    """
    parts = name.split(".")
    if len(parts) < 2:
        return []
    return [p.lower() for p in parts[1:]]


def read_header(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read(_HEADER_LEN)
    except OSError:
        return None


def magic_matches(header: bytes, ext: str) -> bool:
    """Does ``header`` match the known signature for ``ext``?"""
    probes = MAGIC.get(ext)
    if probes is None:
        return True  # no known signature — nothing to contradict
    for offset, sig in probes:
        if header[offset : offset + len(sig)] == sig:
            return True
    return False


def sniff_type(header: bytes) -> str | None:
    for sig, label in MAGIC_LABELS:
        if header.startswith(sig):
            return label
    return None


def check_file(path: Path) -> dict | None:
    """Return a finding dict for ``path``, or None if it looks clean."""
    exts = split_extensions(path.name)
    if not exts:
        return None

    real_ext = exts[-1]
    displayed_ext = exts[-2] if len(exts) >= 2 else None
    findings: list[str] = []
    critical = False

    # 1) Double-extension masquerade: executable hidden behind a harmless name.
    if real_ext in EXEC_EXTENSIONS and displayed_ext in HARMLESS_LOOKING_EXTENSIONS:
        critical = True
        findings.append(
            f"executable (.{real_ext}) disguised under a {displayed_ext.upper()} name"
        )

    # 2) Magic-byte check against the claimed extension (the one a user sees).
    claimed_ext = (
        displayed_ext
        if displayed_ext is not None and displayed_ext in MAGIC
        else (real_ext if real_ext in MAGIC else None)
    )
    if claimed_ext is not None:
        header = read_header(path)
        if header is None:
            findings.append("file could not be read")
        elif not magic_matches(header, claimed_ext):
            actual = sniff_type(header)
            if actual:
                note = (
                    f"claims to be {claimed_ext.upper()} "
                    f"but content is {actual}"
                )
                if "executable" in actual or "shortcut" in actual:
                    critical = True
            else:
                note = (
                    f"claims to be {claimed_ext.upper()} "
                    f"but magic bytes {header[:6]!r} don't match"
                )
            findings.append(note)

    if not findings:
        return None
    return {
        "path": str(path),
        "severity": "CRITICAL" if critical else "WARNING",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def scan(root: str | Path, recursive: bool) -> list[dict]:
    results: list[dict] = []
    root = Path(root)
    walker = root.rglob("*") if recursive else root.glob("*")
    for entry in walker:
        if not entry.is_file():
            continue
        try:
            finding = check_file(entry)
        except OSError:
            continue
        if finding:
            results.append(finding)
    return results
