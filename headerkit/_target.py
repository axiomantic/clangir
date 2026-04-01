"""Target triple detection and resolution for headerkit.

Provides functions to detect the current process's LLVM target triple,
normalize user-provided triples, and resolve the effective target
using the standard headerkit config precedence.

The detection integrates with Python cross-compilation signals:

- ``_PYTHON_HOST_PLATFORM`` (set by CPython cross-builds, crossenv,
  cibuildwheel)
- ``ARCHFLAGS`` (set by macOS build tools, cibuildwheel)
- ``VSCMD_ARG_TGT_ARCH`` (set by Visual Studio on Windows)
- ``struct.calcsize("P")`` to detect the process pointer width
  (handles 32-bit Python on 64-bit host)
"""

from __future__ import annotations

import os
import platform as platform_mod
import re
import struct
import subprocess
import sys
import sysconfig
from pathlib import Path

# Arch aliases: normalize to LLVM canonical names
_ARCH_ALIASES: dict[str, str] = {
    "arm64": "aarch64",
    "amd64": "x86_64",
    "i386": "i686",
    "i586": "i686",
    "i686": "i686",
}

# 64-bit arch to 32-bit arch mapping for pointer-width correction
_ARCH_64_TO_32: dict[str, str] = {
    "x86_64": "i686",
    "aarch64": "armv7l",
    "ppc64le": "ppc",
    "s390x": "s390",
    "riscv64": "riscv32",
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

# _PYTHON_HOST_PLATFORM platform tag to LLVM triple OS+vendor mapping.
# sysconfig.get_platform() returns strings like "linux-x86_64",
# "macosx-14.0-arm64", "win-amd64", "win-arm64".
_PLAT_TAG_OS: dict[str, str] = {
    "linux": "unknown-linux-gnu",
    "macosx": "apple-darwin",
    "win": "pc-windows-msvc",
    "freebsd": "unknown-freebsd",
    "openbsd": "unknown-openbsd",
    "netbsd": "unknown-netbsd",
}


def _process_pointer_bits() -> int:
    """Return the pointer width of the current Python process in bits."""
    return struct.calcsize("P") * 8


def _correct_arch_for_pointer_width(arch: str) -> str:
    """Downgrade a 64-bit arch to 32-bit if the process is 32-bit.

    This handles the case where ``platform.machine()`` or
    ``cc -dumpmachine`` reports the host arch (e.g., ``x86_64``) but
    the Python process is 32-bit (e.g., 32-bit Python on 64-bit
    Windows via cibuildwheel).

    :param arch: Normalized architecture string.
    :returns: Architecture corrected for process pointer width.
    """
    if _process_pointer_bits() == 32 and arch in _ARCH_64_TO_32:
        return _ARCH_64_TO_32[arch]
    return arch


def _parse_archflags() -> str | None:
    """Extract architecture from the ``ARCHFLAGS`` environment variable.

    ``ARCHFLAGS`` is set by macOS build tools and cibuildwheel to
    communicate the target architecture. Format: ``-arch <name>``.

    :returns: Normalized arch string, or None if not set or ambiguous.
    """
    archflags = os.environ.get("ARCHFLAGS", "")
    if not archflags:
        return None

    # Extract all -arch values
    arches = re.findall(r"-arch\s+['\"]?(\S+?)['\"]?(?:\s|$)", archflags)
    if len(arches) == 1:
        arch: str = arches[0].lower()
        return _ARCH_ALIASES.get(arch, arch)
    # Multiple arches (universal2) or no arches: can't determine single target
    return None


def _parse_vscmd_tgt_arch() -> str | None:
    """Extract architecture from Visual Studio's ``VSCMD_ARG_TGT_ARCH``.

    Set by the Visual Studio Developer Command Prompt and vcvarsall.bat.
    Values: ``x86``, ``x64``, ``arm``, ``arm64``.

    :returns: Normalized arch string, or None if not set.
    """
    vs_arch = os.environ.get("VSCMD_ARG_TGT_ARCH", "")
    if not vs_arch:
        return None

    vs_arch_map: dict[str, str] = {
        "x86": "i686",
        "x64": "x86_64",
        "arm": "armv7l",
        "arm64": "aarch64",
    }
    return vs_arch_map.get(vs_arch.lower())


def _triple_from_platform_tag(plat_tag: str) -> str | None:
    """Convert a sysconfig platform tag to an LLVM target triple.

    Platform tags come from ``sysconfig.get_platform()`` (which respects
    ``_PYTHON_HOST_PLATFORM``) and look like:

    - ``linux-x86_64``
    - ``linux-aarch64``
    - ``macosx-14.0-arm64``
    - ``win-amd64``
    - ``win-arm64``
    - ``freebsd-14.1-RELEASE-amd64``

    :param plat_tag: Platform tag string.
    :returns: Normalized LLVM triple, or None if unparseable.
    """
    # Handle "win32" as a special case (no hyphen-separated arch)
    if plat_tag.lower() == "win32":
        return "i686-pc-windows-msvc"

    parts = plat_tag.split("-")
    if len(parts) < 2:
        return None

    os_name = parts[0].lower()
    # Architecture is always the last component
    raw_arch = parts[-1].lower()

    # "universal2" is a macOS fat binary tag, not a real architecture.
    # Return None so detection falls through to pointer-width-based
    # methods that pick the correct single architecture.
    if raw_arch == "universal2":
        return None

    arch = _ARCH_ALIASES.get(raw_arch, raw_arch)

    suffix = _PLAT_TAG_OS.get(os_name)
    if suffix is None:
        return None

    return f"{arch}-{suffix}"


def detect_process_triple() -> str:
    """Detect the target triple for the current Python process.

    Unlike ``cc -dumpmachine`` (which reports the host compiler's default
    target), this function determines the triple appropriate for the
    running Python interpreter. This handles:

    - 32-bit Python on 64-bit OS (arch downgrade via pointer width)
    - Cross-compilation signals (``_PYTHON_HOST_PLATFORM``,
      ``ARCHFLAGS``, ``VSCMD_ARG_TGT_ARCH``)
    - ``sysconfig.get_platform()`` which integrates several of these

    Strategy (in order):

    1. ``sysconfig.get_platform()`` -- respects ``_PYTHON_HOST_PLATFORM``
       and cross-compilation environments like crossenv.
    2. ``ARCHFLAGS`` -- macOS cross-compilation signal (single arch only).
    3. ``VSCMD_ARG_TGT_ARCH`` -- Visual Studio target arch.
    4. ``cc -dumpmachine`` with pointer-width correction.
    5. Construct from ``sys.platform`` + ``platform.machine()`` with
       pointer-width correction.

    :returns: Normalized LLVM target triple string.
    """
    # 1. sysconfig.get_platform() respects _PYTHON_HOST_PLATFORM and
    #    crossenv monkeypatching. This is the closest thing to a
    #    standard cross-compilation signal in Python.
    plat = sysconfig.get_platform()
    triple = _triple_from_platform_tag(plat)
    if triple is not None:
        return normalize_triple(triple)

    # 2. ARCHFLAGS (macOS)
    archflags_arch = _parse_archflags()
    if archflags_arch is not None:
        suffix = _PLATFORM_SUFFIXES.get(sys.platform, f"unknown-{sys.platform}")
        return normalize_triple(f"{archflags_arch}-{suffix}")

    # 3. VSCMD_ARG_TGT_ARCH (Windows Visual Studio)
    vs_arch = _parse_vscmd_tgt_arch()
    if vs_arch is not None:
        return normalize_triple(f"{vs_arch}-pc-windows-msvc")

    # 4. cc -dumpmachine with pointer-width correction
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
                triple = normalize_triple(raw)
                parts = triple.split("-")
                parts[0] = _correct_arch_for_pointer_width(parts[0])
                return "-".join(parts)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        pass

    # 5. Construct from Python platform info
    return _construct_triple_from_python()


def _construct_triple_from_python() -> str:
    """Build a best-effort LLVM triple from Python platform info.

    Uses ``struct.calcsize("P")`` to determine the correct arch when
    the process pointer width differs from the host machine word size.

    :returns: Triple like ``x86_64-unknown-linux-gnu``.
    """
    arch = platform_mod.machine().lower()
    arch = _ARCH_ALIASES.get(arch, arch)
    arch = _correct_arch_for_pointer_width(arch)

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

    1. *target* kwarg (explicit API parameter)
    2. ``HEADERKIT_TARGET`` environment variable
    3. ``[tool.headerkit] target`` in pyproject.toml
    4. :func:`detect_process_triple` (auto-detect from process and
       cross-compilation signals)

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
    return detect_process_triple()


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
