#!/usr/bin/env python3
"""Report which of the test suite's required toolchains are absent.

This is a *diagnostic*, not the gate. The gate is the pytest session:
``tests/skip_policy.py`` turns a missing toolchain into a failure under CI, and
the session hook in ``tests/conftest.py`` fails the run on any skip it did not
sanction. Both would go red without this script.

What the script buys is the failure arriving at the install step, naming every
missing tool at once, instead of arriving several minutes later inside whichever
test happened to need one first. Because the pytest session remains the
authority, this list drifting out of date makes CI noisier, never falsely green.

Exits 0 when everything is present, 1 otherwise.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _first_on_path(*programs: str) -> str | None:
    for candidate in programs:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def main() -> int:
    findings: list[tuple[str, str | None, str]] = []

    findings.append(
        (
            "Nim compiler",
            _first_on_path("nim"),
            "the Nim install step in .github/workflows/test.yml, or https://nim-lang.org/install.html",
        )
    )
    findings.append(
        (
            "C compiler",
            _first_on_path("cc", "gcc", "clang"),
            "build-essential (Linux), Xcode CLT (macOS), LLVM (Windows)",
        )
    )
    findings.append(
        ("C++ compiler", _first_on_path("clang++", "g++"), "build-essential (Linux), Xcode CLT (macOS), LLVM (Windows)")
    )

    try:
        from headerkit.backends import is_backend_available
        from headerkit.backends.libclang import is_system_libclang_available
    except ImportError as exc:  # pragma: no cover - the package is installed before this runs
        print(f"MISSING: headerkit is not importable ({exc}). Remedy: pip install -e '.[test,treesitter]'")
        return 1

    findings.append(
        (
            "system libclang",
            "present" if is_system_libclang_available() else None,
            "libclang-dev (Linux), brew install llvm (macOS)",
        )
    )
    findings.append(
        (
            "tree-sitter backend",
            "present" if is_backend_available("tree-sitter") else None,
            "pip install -e '.[treesitter]'",
        )
    )
    findings.append(("Cython", "present" if _module_available("Cython") else None, "pip install -e '.[test]'"))

    missing = [(name, remedy) for name, found, remedy in findings if found is None]
    for name, found, _ in findings:
        if found is not None:
            print(f"ok      : {name} -> {found}")
    for name, remedy in missing:
        print(f"MISSING : {name}. Remedy: {remedy}")

    if missing:
        print(
            f"\n{len(missing)} required toolchain(s) absent. The suite would skip the tests that "
            "need them, and a skip is the same colour as a pass, so CI treats this as a failure."
        )
        return 1
    return 0


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


if __name__ == "__main__":
    sys.exit(main())
