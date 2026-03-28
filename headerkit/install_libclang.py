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


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, printing it first for visibility."""
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=check, text=True)


def _is_command_available(name: str) -> bool:
    return shutil.which(name) is not None


def install_linux() -> bool:
    """Install libclang on Linux using the available package manager."""
    # Try dnf first (RHEL/Fedora/AlmaLinux/manylinux_2_28)
    if _is_command_available("dnf"):
        print("Detected dnf package manager (RHEL/Fedora/AlmaLinux)")
        result = _run(["dnf", "install", "-y", "clang-devel"], check=False)
        if result.returncode == 0:
            return True

    # Try apt-get (Debian/Ubuntu)
    if _is_command_available("apt-get"):
        print("Detected apt-get package manager (Debian/Ubuntu)")
        _run(["apt-get", "update", "-qq"], check=False)
        result = _run(["apt-get", "install", "-y", "libclang-dev"], check=False)
        if result.returncode == 0:
            return True

    # Try apk (Alpine)
    if _is_command_available("apk"):
        print("Detected apk package manager (Alpine)")
        result = _run(["apk", "add", "clang-dev"], check=False)
        if result.returncode == 0:
            return True

    print("ERROR: No supported package manager found (tried dnf, apt-get, apk)")
    return False


def install_macos() -> bool:
    """Install libclang on macOS using Homebrew."""
    if not _is_command_available("brew"):
        print("ERROR: Homebrew not found. Install it from https://brew.sh/")
        return False

    print("Installing LLVM via Homebrew...")
    result = _run(["brew", "install", "llvm"], check=False)
    return result.returncode == 0


def install_windows(llvm_version: str) -> bool:
    """Install libclang on Windows.

    On ARM64, downloads the native woa64 installer from LLVM GitHub releases.
    On x64, uses Chocolatey with the same pinned LLVM version.
    """
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()

    if arch == "ARM64":
        return _install_windows_arm64(llvm_version)
    else:
        return _install_windows_x64(llvm_version)


def _install_windows_arm64(llvm_version: str) -> bool:
    """Download and install native ARM64 LLVM on Windows."""
    url = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{llvm_version}/LLVM-{llvm_version}-woa64.exe"
    print(f"Detected ARM64 Windows, downloading native LLVM {llvm_version}...")
    print(f"URL: {url}")

    installer_path = os.path.join(tempfile.gettempdir(), "llvm-installer.exe")

    # Download using curl (available on modern Windows)
    result = _run(
        ["curl", "-sSL", "-o", installer_path, url],
        check=False,
    )
    if result.returncode != 0:
        print("ERROR: Failed to download LLVM installer")
        return False

    # Silent install
    print("Installing LLVM silently...")
    result = _run(
        ["powershell", "-Command", f"Start-Process '{installer_path}' -ArgumentList '/S' -Wait"],
        check=False,
    )

    # Clean up installer
    with contextlib.suppress(OSError):
        os.remove(installer_path)

    if result.returncode != 0:
        print("ERROR: LLVM installer failed")
        return False

    print(f"LLVM {llvm_version} ARM64 installed successfully.")
    return True


def _install_windows_x64(llvm_version: str) -> bool:
    """Install LLVM on x64 Windows using Chocolatey.

    Pins to *llvm_version* so the installed library matches the vendored
    cindex bindings.  Chocolatey's default (unpinned) LLVM may lag behind
    the version that headerkit's bindings expect, causing
    ``LibclangError: function 'clang_getFullyQualifiedName' not found``
    and similar failures at load time.
    """
    if not _is_command_available("choco"):
        print("ERROR: Chocolatey not found. Install it from https://chocolatey.org/")
        return False

    print(f"Detected x64 Windows, installing LLVM {llvm_version} via Chocolatey...")
    result = _run(
        ["choco", "install", "llvm", f"--version={llvm_version}", "-y"],
        check=False,
    )
    return result.returncode == 0


def verify_libclang() -> bool:
    """Verify that libclang is now loadable by headerkit."""
    try:
        from headerkit.backends.libclang import is_system_libclang_available

        if is_system_libclang_available():
            print("Verification: libclang is available and loadable.")
            return True
        else:
            print("WARNING: libclang was installed but could not be loaded by headerkit.")
            print("You may need to set your library path or restart your shell.")
            return False
    except (ImportError, OSError, RuntimeError) as e:
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
    if verify_libclang():
        return True

    logger.info("libclang not found; attempting automatic installation")
    logger.info("Platform: %s (%s)", sys.platform, platform.machine())

    ok: bool
    # Redirect stdout to devnull so that print() calls inside the
    # platform-specific installers (and _run / verify_libclang) do not
    # leak output to callers that expect a quiet API.
    with contextlib.redirect_stdout(open(os.devnull, "w")):  # noqa: SIM115
        if sys.platform == "linux":
            ok = install_linux()
        elif sys.platform == "darwin":
            ok = install_macos()
        elif sys.platform == "win32":
            ok = install_windows(DEFAULT_LLVM_VERSION)
        else:
            logger.warning("Unsupported platform for auto-install: %s", sys.platform)
            return False

        if not ok:
            logger.warning("libclang installation failed")
            return False

        if not verify_libclang():
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
