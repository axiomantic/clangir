"""Target triple detection and resolution for headerkit.

Provides functions to detect the current process's target triple,
normalize user-provided triples, and resolve the effective target
using the standard headerkit config precedence.

Auto-detection uses the most direct signal per platform:

- **POSIX** (Linux, macOS, BSDs):
  ``sysconfig.get_config_var('HOST_GNU_TYPE')`` -- the ``--host``
  value from autoconf, baked into the Python build at compile time.
- **Windows**: Parses ``sysconfig.get_platform()``
  (e.g., ``win-amd64``, ``win32``).

On pre-3.13 Linux, a musl libc sniff corrects ``linux-gnu`` to
``linux-musl`` when the running Python is linked against musl.

For cross-compilation, set ``--target``, ``HEADERKIT_TARGET``, or
``[tool.headerkit] target`` explicitly.
"""

from __future__ import annotations

import os
import platform as platform_mod
import sys
import sysconfig
from pathlib import Path

# Arch aliases: normalize user-provided triples to canonical names.
# Only applied to user input (--target, env var, config), not to
# auto-detected values which are already canonical.
_ARCH_ALIASES: dict[str, str] = {
    "arm64": "aarch64",
    "amd64": "x86_64",
    "i386": "i686",
    "i586": "i686",
    "i686": "i686",
}

# Windows sysconfig.get_platform() arch suffix to canonical arch.
_WINDOWS_ARCH: dict[str, str] = {
    "amd64": "x86_64",
    "arm64": "aarch64",
    "x86": "i686",
}


def _is_musl_linux() -> bool:
    """Detect if the running Python process is linked against musl libc.

    Uses ``os.confstr('CS_GNU_LIBC_VERSION')`` which returns a version
    string on glibc (e.g., ``'glibc 2.35'``) and raises ``ValueError``
    or ``OSError`` on non-glibc systems. This is process-aware: it
    checks what THIS interpreter links against, not what libraries are
    installed on the system.

    :returns: True if on Linux and not linked against glibc (i.e., musl).
    """
    if sys.platform != "linux":
        return False
    try:
        os.confstr("CS_GNU_LIBC_VERSION")
        return False  # glibc responds
    except (ValueError, OSError):
        return True  # not glibc, on Linux = musl
    except AttributeError:
        return False  # os.confstr not available


def detect_process_triple() -> str:
    """Detect the target triple for the current Python process.

    Uses the most direct available signal:

    - **POSIX**: ``HOST_GNU_TYPE`` from sysconfig -- the ``--host``
      value set by autoconf when this Python was built. This is
      inherently process-aware (a 32-bit Python build has a 32-bit
      ``HOST_GNU_TYPE``). On pre-3.13 Linux where ``HOST_GNU_TYPE``
      may incorrectly report ``gnu`` on musl systems, a runtime libc
      sniff corrects it.
    - **Windows**: ``sysconfig.get_platform()`` -- returns ``win-amd64``,
      ``win32``, or ``win-arm64``.

    For cross-compilation, use ``--target``, ``HEADERKIT_TARGET``,
    or ``[tool.headerkit] target`` instead of relying on auto-detection.

    :returns: Target triple string (e.g., ``aarch64-apple-darwin``,
        ``x86_64-pc-linux-gnu``, ``x86_64-pc-windows-msvc``).
    """
    # POSIX: HOST_GNU_TYPE is the triple this Python was built for.
    # Set by autoconf's AC_CANONICAL_HOST at Python build time.
    # Includes vendor, OS, and libc flavor (on 3.13+).
    host_gnu: str | None = sysconfig.get_config_var("HOST_GNU_TYPE")
    if host_gnu:
        triple = host_gnu.strip().lower()
        # Pre-3.13 CPython may report linux-gnu on musl systems
        # (CPython issue #87278, fixed in 3.13 via #95855).
        # Correct using a runtime libc sniff.
        if "linux-gnu" in triple and _is_musl_linux():
            triple = triple.replace("linux-gnu", "linux-musl", 1)
        return triple

    # Windows: HOST_GNU_TYPE is not available (no autoconf).
    # Parse sysconfig.get_platform() which returns win-amd64, win32, etc.
    plat = sysconfig.get_platform().lower()
    if plat == "win32":
        return "i686-pc-windows-msvc"
    if plat.startswith("win"):
        parts = plat.split("-")
        if len(parts) >= 2:
            raw_arch = parts[-1]
            arch = _WINDOWS_ARCH.get(raw_arch, raw_arch)
            return f"{arch}-pc-windows-msvc"

    # Fallback: construct best-effort triple from Python platform info.
    # This path should rarely execute -- it covers non-autoconf POSIX
    # builds and any unrecognized Windows platform tags.
    arch = platform_mod.machine().lower()
    arch = _ARCH_ALIASES.get(arch, arch)
    return f"{arch}-unknown-{sys.platform}"


def normalize_triple(triple: str) -> str:
    """Normalize a user-provided target triple to canonical form.

    Applied only to user input (``--target``, ``HEADERKIT_TARGET``,
    config file). Auto-detected triples from :func:`detect_process_triple`
    are already canonical and do not pass through this function.

    - Lowercases all components.
    - Normalizes architecture aliases (``arm64`` -> ``aarch64``,
      ``AMD64`` -> ``x86_64``).
    - Inserts ``unknown`` vendor for 3-component triples missing the
      vendor (e.g., ``x86_64-linux-gnu`` -> ``x86_64-unknown-linux-gnu``).

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
    4. :func:`detect_process_triple` (auto-detect)

    User-provided triples (sources 1-3) are normalized via
    :func:`normalize_triple`. Auto-detected triples are used as-is.

    :param target: Explicit target triple (highest precedence).
    :param project_root: Project root for config file lookup.
    :returns: Resolved target triple.
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

    :param triple: Target triple.
    :returns: Short ``arch-os`` string.
    """
    parts = triple.split("-")
    arch = parts[0]
    os_part = parts[2] if len(parts) > 2 else parts[-1]
    # Strip version suffixes (e.g., darwin25.3.0 -> darwin,
    # freebsd14.1 -> freebsd) for readable slugs.
    os_part = os_part.rstrip("0123456789.").rstrip("-") or os_part
    return f"{arch}-{os_part}"
