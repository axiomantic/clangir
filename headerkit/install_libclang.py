"""Install libclang for the current platform.

Usage::

    python -m headerkit.install_libclang [--version VERSION]

This module installs the libclang shared library so that headerkit's libclang
backend can function. It handles platform-specific installation:

- **Linux (RHEL/Fedora/AlmaLinux)**: ``dnf install clang-devel``
- **Linux (Debian/Ubuntu)**: ``apt-get install libclang-dev``
- **Linux (Alpine)**: ``apk add clang-dev``
- **macOS**: ``brew install llvm`` (via Homebrew)
- **Windows x64**: ``choco install llvm`` (via Chocolatey)
- **Windows ARM64**: Downloads native woa64 installer from LLVM GitHub releases

After installation, verifies that libclang is loadable by headerkit.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile

logger = logging.getLogger("headerkit.install")


def _log_or_print(msg: str, *, quiet: bool) -> None:
    """Route *msg* to :func:`logger.info` when quiet, else :func:`print`."""
    if quiet:
        logger.info(msg)
    else:
        print(msg)


def _run(cmd: list[str], check: bool = True, *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, printing it first for visibility.

    When *quiet* is True, subprocess stdout and stderr are sent to
    ``DEVNULL`` so that external commands (apt-get, brew, choco, etc.)
    do not leak output.
    """
    if quiet:
        logger.info("+ %s", " ".join(cmd))
    else:
        print(f"+ {' '.join(cmd)}", flush=True)
    devnull = subprocess.DEVNULL if quiet else None
    return subprocess.run(cmd, check=check, text=True, stdout=devnull, stderr=devnull)


def _is_command_available(name: str) -> bool:
    return shutil.which(name) is not None


def install_linux(*, quiet: bool = False) -> bool:
    """Install libclang on Linux using the available package manager."""
    # Try dnf first (RHEL/Fedora/AlmaLinux/manylinux_2_28)
    if _is_command_available("dnf"):
        _log_or_print("Detected dnf package manager (RHEL/Fedora/AlmaLinux)", quiet=quiet)
        result = _run(["dnf", "install", "-y", "clang-devel"], check=False, quiet=quiet)
        if result.returncode == 0:
            return True

    # Try apt-get (Debian/Ubuntu)
    if _is_command_available("apt-get"):
        _log_or_print("Detected apt-get package manager (Debian/Ubuntu)", quiet=quiet)
        _run(["apt-get", "update", "-qq"], check=False, quiet=quiet)
        result = _run(["apt-get", "install", "-y", "libclang-dev"], check=False, quiet=quiet)
        if result.returncode == 0:
            return True

    # Try apk (Alpine)
    if _is_command_available("apk"):
        _log_or_print("Detected apk package manager (Alpine)", quiet=quiet)
        result = _run(["apk", "add", "clang-dev"], check=False, quiet=quiet)
        if result.returncode == 0:
            return True

    _log_or_print("ERROR: No supported package manager found (tried dnf, apt-get, apk)", quiet=quiet)
    return False


def install_macos(*, quiet: bool = False) -> bool:
    """Install libclang on macOS using Homebrew."""
    if not _is_command_available("brew"):
        _log_or_print("ERROR: Homebrew not found. Install it from https://brew.sh/", quiet=quiet)
        return False

    _log_or_print("Installing LLVM via Homebrew...", quiet=quiet)
    result = _run(["brew", "install", "llvm"], check=False, quiet=quiet)
    return result.returncode == 0


def install_windows(llvm_version: str, *, quiet: bool = False) -> bool:
    """Install libclang on Windows.

    On ARM64, downloads the native woa64 installer from LLVM GitHub releases.
    On x64, uses Chocolatey with the same pinned LLVM version.
    """
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()

    if arch == "ARM64":
        return _install_windows_arm64(llvm_version, quiet=quiet)
    else:
        return _install_windows_x64(llvm_version, quiet=quiet)


def _install_windows_arm64(llvm_version: str, *, quiet: bool = False) -> bool:
    """Download and install native ARM64 LLVM on Windows."""
    url = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{llvm_version}/LLVM-{llvm_version}-woa64.exe"
    _log_or_print(f"Detected ARM64 Windows, downloading native LLVM {llvm_version}...", quiet=quiet)
    _log_or_print(f"URL: {url}", quiet=quiet)

    installer_path = os.path.join(tempfile.gettempdir(), "llvm-installer.exe")

    # Download using curl (available on modern Windows)
    result = _run(
        ["curl", "-sSL", "-o", installer_path, url],
        check=False,
        quiet=quiet,
    )
    if result.returncode != 0:
        _log_or_print("ERROR: Failed to download LLVM installer", quiet=quiet)
        return False

    # Silent install
    _log_or_print("Installing LLVM silently...", quiet=quiet)
    result = _run(
        ["powershell", "-Command", f"Start-Process '{installer_path}' -ArgumentList '/S' -Wait"],
        check=False,
        quiet=quiet,
    )

    # Clean up installer
    with contextlib.suppress(OSError):
        os.remove(installer_path)

    if result.returncode != 0:
        _log_or_print("ERROR: LLVM installer failed", quiet=quiet)
        return False

    _log_or_print(f"LLVM {llvm_version} ARM64 installed successfully.", quiet=quiet)
    return True


def _install_windows_x64(llvm_version: str, *, quiet: bool = False) -> bool:
    """Install LLVM on x64 Windows using Chocolatey.

    Pins to *llvm_version* so the installed library matches the vendored
    cindex bindings.  Chocolatey's default (unpinned) LLVM may lag behind
    the version that headerkit's bindings expect, causing
    ``LibclangError: function 'clang_getFullyQualifiedName' not found``
    and similar failures at load time.
    """
    if not _is_command_available("choco"):
        _log_or_print("ERROR: Chocolatey not found. Install it from https://chocolatey.org/", quiet=quiet)
        return False

    _log_or_print(f"Detected x64 Windows, installing LLVM {llvm_version} via Chocolatey...", quiet=quiet)
    result = _run(
        ["choco", "install", "llvm", f"--version={llvm_version}", "-y"],
        check=False,
        quiet=quiet,
    )
    return result.returncode == 0


def verify_libclang(*, quiet: bool = False) -> bool:
    """Verify that libclang is now loadable by headerkit."""
    try:
        from headerkit.backends.libclang import is_system_libclang_available

        if is_system_libclang_available():
            if not quiet:
                print("Verification: libclang is available and loadable.")
            return True
        else:
            msg = "libclang was installed but could not be loaded by headerkit."
            if quiet:
                logger.warning(msg)
            else:
                print(f"WARNING: {msg}")
                print("You may need to set your library path or restart your shell.")
            return False
    except (ImportError, OSError, RuntimeError) as e:
        if quiet:
            logger.warning("Could not verify libclang: %s", e)
        else:
            print(f"WARNING: Could not verify libclang: {e}")
        return False


# Latest stable LLVM version known to have ARM64 Windows builds
DEFAULT_LLVM_VERSION = "21.1.8"


def auto_install() -> bool:
    """Quietly auto-install libclang if not already available.

    This is the non-interactive counterpart to ``main()``. It is called by
    ``generate()`` when the libclang backend is needed but unavailable.

    - Idempotent: returns True immediately if libclang is already loadable.
    - Suppresses stdout from the underlying install functions so that
      callers (e.g. ``generate()``) never see unexpected print output.
    - Logs progress via the ``headerkit.install`` logger instead of printing.
    - Always verifies after installation (no ``--skip-verify``).

    :returns: True if libclang is available after this call, False otherwise.
    """
    if verify_libclang(quiet=True):
        return True

    logger.info("libclang not found; attempting automatic installation")
    logger.info("Platform: %s (%s)", sys.platform, platform.machine())

    ok: bool
    if sys.platform == "linux":
        ok = install_linux(quiet=True)
    elif sys.platform == "darwin":
        ok = install_macos(quiet=True)
    elif sys.platform == "win32":
        ok = install_windows(DEFAULT_LLVM_VERSION, quiet=True)
    else:
        logger.warning("Unsupported platform for auto-install: %s", sys.platform)
        return False

    if not ok:
        logger.warning("libclang installation failed")
        return False

    if not verify_libclang(quiet=True):
        logger.warning("libclang installed but could not be loaded")
        return False

    logger.info("libclang auto-installed successfully")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m headerkit.install_libclang",
        description="Install libclang for the current platform.",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_LLVM_VERSION,
        help=f"LLVM version to install (default: {DEFAULT_LLVM_VERSION}, "
        "used for Windows ARM64 direct download; package managers use their default version)",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip post-install verification of libclang loading",
    )
    args = parser.parse_args(argv)

    print(f"Platform: {sys.platform} ({platform.machine()})")

    if sys.platform == "linux":
        ok = install_linux()
    elif sys.platform == "darwin":
        ok = install_macos()
    elif sys.platform == "win32":
        ok = install_windows(args.version)
    else:
        print(f"ERROR: Unsupported platform: {sys.platform}")
        return 1

    if not ok:
        return 1

    if not args.skip_verify:
        if not verify_libclang():
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
