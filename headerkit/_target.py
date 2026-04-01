"""Target triple detection and resolution for headerkit.

Provides functions to detect the host platform's LLVM target triple,
normalize user-provided triples, and resolve the effective target
using the standard headerkit config precedence.
"""

from __future__ import annotations

import os
import platform as platform_mod
import subprocess
import sys
from pathlib import Path

# Arch aliases: normalize to LLVM canonical names
_ARCH_ALIASES: dict[str, str] = {
    "arm64": "aarch64",
    "amd64": "x86_64",
    "i386": "i686",
    "i586": "i686",
    "i686": "i686",
}

# Platform to triple suffix mapping
_PLATFORM_SUFFIXES: dict[str, str] = {
    "darwin": "apple-darwin",
    "linux": "unknown-linux-gnu",
    "win32": "pc-windows-msvc",
    "freebsd": "unknown-freebsd",
    "openbsd": "unknown-openbsd",
    "netbsd": "unknown-netbsd",
}


def detect_host_triple() -> str:
    """Detect the host platform's LLVM target triple.

    Strategy:
    1. Try ``cc -dumpmachine`` (respects user's configured compiler).
    2. Fall back to constructing from ``sys.platform`` and
       ``platform.machine()``.

    :returns: Normalized LLVM target triple string.
    """
    try:
        result = subprocess.run(
            ["cc", "-dumpmachine"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            if raw:
                return normalize_triple(raw)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return _construct_triple_from_python()


def _construct_triple_from_python() -> str:
    """Build a best-effort LLVM triple from Python platform info.

    :returns: Triple like ``x86_64-unknown-linux-gnu``.
    """
    arch = platform_mod.machine().lower()
    arch = _ARCH_ALIASES.get(arch, arch)

    suffix = _PLATFORM_SUFFIXES.get(sys.platform, f"unknown-{sys.platform}")
    return f"{arch}-{suffix}"


def normalize_triple(triple: str) -> str:
    """Normalize a target triple to canonical LLVM form.

    - Lowercases all components.
    - Normalizes architecture aliases (arm64 -> aarch64, AMD64 -> x86_64).
    - Inserts ``unknown`` vendor for 3-component triples missing the vendor
      (e.g., ``x86_64-linux-gnu`` -> ``x86_64-unknown-linux-gnu``).

    :param triple: Raw triple string.
    :returns: Normalized triple.
    :raises ValueError: If triple has fewer than 3 components.
    """
    parts = triple.strip().lower().split("-")
    if len(parts) < 3:
        raise ValueError(
            f"Invalid target triple {triple!r}: expected at least 3 "
            f"hyphen-separated components (arch-vendor-os or arch-os-env)"
        )

    # Normalize arch
    parts[0] = _ARCH_ALIASES.get(parts[0], parts[0])

    # Insert 'unknown' vendor for 3-component triples where the vendor
    # appears to be missing (e.g., x86_64-linux-gnu -> x86_64-unknown-linux-gnu).
    _KNOWN_VENDORS = {"pc", "apple", "unknown", "none", "ibm", "scei"}
    if len(parts) == 3 and parts[1] not in _KNOWN_VENDORS:
        parts.insert(1, "unknown")

    return "-".join(parts)


def resolve_target(
    *,
    target: str | None = None,
    project_root: Path | None = None,
) -> str:
    """Resolve the effective target triple with config precedence.

    Precedence (highest to lowest):
    1. *target* kwarg
    2. ``HEADERKIT_TARGET`` environment variable
    3. ``[tool.headerkit] target`` in pyproject.toml
    4. :func:`detect_host_triple`

    :param target: Explicit target triple (highest precedence).
    :param project_root: Project root for config file lookup.
    :returns: Resolved and normalized target triple.
    """
    # 1. Explicit kwarg
    if target is not None:
        return normalize_triple(target)

    # 2. Environment variable
    env_val = os.environ.get("HEADERKIT_TARGET")
    if env_val:
        return normalize_triple(env_val)

    # 3. Config file
    if project_root is not None:
        config_target = _read_target_from_config(project_root)
        if config_target is not None:
            return normalize_triple(config_target)

    # 4. Auto-detect
    return detect_host_triple()


def _read_target_from_config(project_root: Path) -> str | None:
    """Read target from pyproject.toml [tool.headerkit] section.

    :param project_root: Project root directory.
    :returns: Target string or None if not configured.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        from headerkit._config import _parse_toml

        raw = _parse_toml(pyproject.read_bytes())
    except (ImportError, OSError, ValueError, RuntimeError):
        return None

    tool = raw.get("tool", {})
    if not isinstance(tool, dict):
        return None
    hk = tool.get("headerkit", {})
    if not isinstance(hk, dict):
        return None
    target_val = hk.get("target")
    if isinstance(target_val, str):
        return target_val
    return None


def short_target(triple: str) -> str:
    """Extract arch and OS for human-readable slug components.

    Examples::

        >>> short_target("x86_64-pc-linux-gnu")
        'x86_64-linux'
        >>> short_target("aarch64-apple-darwin")
        'aarch64-darwin'
        >>> short_target("x86_64-pc-windows-msvc")
        'x86_64-windows'

    :param triple: Normalized target triple.
    :returns: Short ``arch-os`` string.
    """
    parts = triple.split("-")
    arch = parts[0]
    os_part = parts[2]
    # Strip version suffixes (e.g., darwin25.3.0 -> darwin,
    # freebsd14.1 -> freebsd) for readable slugs.
    os_part = os_part.rstrip("0123456789.").rstrip("-") or os_part
    return f"{arch}-{os_part}"
