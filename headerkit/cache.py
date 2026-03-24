"""Hash-based cache staleness detection for headerkit generated output.

This module provides functions to compute deterministic hashes of
generation inputs, store those hashes alongside output files, and
check whether outputs are up-to-date with respect to their inputs.

All functions work without libclang installed, making them suitable
for validation on platforms where libclang is unavailable.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.metadata
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import tomllib  # type: ignore[import-not-found]

logger = logging.getLogger("headerkit.cache")


# =============================================================================
# Public API
# =============================================================================


def compute_hash(
    *,
    header_paths: Sequence[str | Path],
    writer_name: str,
    writer_options: dict[str, Any] | None = None,
    extra_inputs: Sequence[str | Path] | None = None,
) -> str:
    """Compute a deterministic SHA-256 hash of all inputs affecting generation.

    :param header_paths: Paths to C/C++ header files to include in hash.
        Relative paths are resolved against CWD via ``Path(p).resolve()``.
    :param writer_name: Name of the writer (e.g., ``"cffi"``, ``"ctypes"``).
    :param writer_options: Writer configuration options (sorted for determinism).
    :param extra_inputs: Additional file paths to include in the hash.
        Relative paths are resolved against CWD via ``Path(p).resolve()``.
    :returns: Hex-encoded SHA-256 digest string.
    :raises FileNotFoundError: If any header or extra input file does not exist.
    :raises ValueError: If header_paths is empty.
    """
    return _compute_hash_digest(
        header_paths=header_paths,
        writer_name=writer_name,
        writer_options=writer_options,
        extra_inputs=extra_inputs,
    )


# =============================================================================
# Internal Functions
# =============================================================================


def _compute_hash_digest(
    *,
    header_paths: Sequence[str | Path],
    writer_name: str,
    writer_options: dict[str, Any] | None,
    extra_inputs: Sequence[str | Path] | None,
) -> str:
    """Core hash computation. Shared by compute_hash, save_hash, is_up_to_date."""
    if not header_paths:
        raise ValueError("header_paths must not be empty")

    # Resolve and validate header paths
    resolved_headers: list[Path] = []
    for p in header_paths:
        rp = Path(p).resolve()
        if not rp.exists():
            raise FileNotFoundError(f"Header not found: {p}")
        resolved_headers.append(rp)

    # Resolve and validate extra input paths
    resolved_extras: list[Path] = []
    if extra_inputs:
        for p in extra_inputs:
            rp = Path(p).resolve()
            if not rp.exists():
                raise FileNotFoundError(f"Extra input not found: {p}")
            resolved_extras.append(rp)

    hasher = hashlib.sha256()

    # Feed headerkit version
    try:
        version = importlib.metadata.version("headerkit")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
        logger.warning("Could not determine headerkit version; using 'unknown'")
    hasher.update(b"headerkit-version:")
    hasher.update(version.encode("utf-8"))
    hasher.update(b"\x00")

    # Feed writer name
    hasher.update(b"writer:")
    hasher.update(writer_name.encode("utf-8"))
    hasher.update(b"\x00")

    # Feed writer options (sorted by key)
    if writer_options:
        for key in sorted(writer_options.keys()):
            value = writer_options[key]
            hasher.update(b"option:")
            hasher.update(f"{key}={value}".encode())
            hasher.update(b"\x00")

    # Feed header file contents (sorted by resolved absolute path)
    for hp in sorted(resolved_headers, key=lambda p: str(p)):
        content = _read_file_normalized(hp)
        hasher.update(b"header:")
        hasher.update(str(hp).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
        hasher.update(b"\x00")

    # Feed extra input contents (sorted by resolved absolute path)
    for ep in sorted(resolved_extras, key=lambda p: str(p)):
        content = _read_file_normalized(ep)
        hasher.update(b"extra:")
        hasher.update(str(ep).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
        hasher.update(b"\x00")

    return hasher.hexdigest()


def _read_file_normalized(path: Path) -> bytes:
    """Read file, strip BOM, normalize line endings to LF, return bytes."""
    raw = path.read_bytes()
    # Strip UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    # Normalize line endings: CRLF -> LF, then CR -> LF
    raw = raw.replace(b"\r\n", b"\n")
    raw = raw.replace(b"\r", b"\n")
    return raw


def _build_metadata_toml(
    hash_digest: str,
    writer_name: str,
    headerkit_version: str,
) -> str:
    """Hand-serialize cache metadata to a TOML string.

    Uses manual string formatting to avoid a runtime dependency
    on a TOML serialization library.
    """
    generated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lines = [
        "[headerkit-cache]",
        f'hash = "{hash_digest}"',
        f'version = "{headerkit_version}"',
        f'writer = "{writer_name}"',
        f'generated = "{generated}"',
    ]
    return "\n".join(lines) + "\n"


def _parse_embedded_toml(content: str) -> dict[str, Any] | None:
    """Extract [headerkit-cache] TOML block from comment lines in file content.

    Scans lines from the start of the file. For each line, attempts to
    match ``# [headerkit-cache]`` or ``-- [headerkit-cache]``. Once found,
    strips the comment prefix from subsequent lines until a blank line or
    non-comment line is reached. Feeds the stripped lines to tomllib.

    :returns: Parsed TOML dict, or None if no valid block found.
    """
    lines = content.splitlines()
    prefix: str | None = None
    toml_lines: list[str] = []
    collecting = False

    for line in lines:
        if not collecting:
            # Try to detect the start marker
            stripped = line.strip()
            for candidate_prefix in ("#", "--"):
                if stripped == f"{candidate_prefix} [headerkit-cache]":
                    prefix = candidate_prefix
                    toml_lines.append("[headerkit-cache]")
                    collecting = True
                    break
        else:
            # We are collecting TOML lines
            stripped = line.strip()
            if not stripped:
                # Blank line ends the TOML block
                break
            if prefix is not None and stripped.startswith(prefix):
                # Strip the comment prefix
                toml_line = stripped[len(prefix) :].strip()
                if not toml_line:
                    break
                toml_lines.append(toml_line)
            else:
                # Non-comment line ends the block
                break

    if not toml_lines:
        return None

    toml_str = "\n".join(toml_lines) + "\n"
    try:
        parsed: dict[str, Any] = tomllib.loads(toml_str)
        return parsed
    except Exception:
        logger.warning("Failed to parse embedded TOML in output file")
        return None


def _sidecar_path(output_path: Path) -> Path:
    """Return the sidecar path: output_path with .hkcache extension appended."""
    return output_path.parent / (output_path.name + ".hkcache")
