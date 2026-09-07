"""Execution gates for scaffolded packages: import them, compile them, call through them.

Every other scaffolding test in this suite asserts on the *text* of a generated
file. That is what let three un-importable packages ship: a test that greps
``_bindings.py`` for ``_lib.thing_add.argtypes`` passes identically whether the
module works or raises ``NameError`` on the line it just matched.

The gates here consume the output instead. A real C library is compiled from a
fixture header, the scaffolder is run against that same header, and the result
has to survive being used:

* the ctypes package must **import** and the call must reach the C function;
* the Nim package must **compile, link and execute** against the same library;
* and with the native library absent the ctypes package must **fail**, which is
  the tripwire invariant in ``AGENTS.md`` §1 -- a tripwire that passes without
  the binary present is a green mirage.

The fixture header is deliberately the smallest one that reaches all three
defects these gates were written for: a tag-less ``typedef enum``, a second one
to prove the first was not a coincidence, a tag-less ``typedef struct`` with a
bit-field, and a function to call.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.ir import SourceUnit
from headerkit.scaffold import ScaffoldOptions, scaffold

FIXTURE_HEADER = textwrap.dedent("""\
    #ifndef PROBE_H
    #define PROBE_H

    typedef enum { FLAG_A = 1, FLAG_D = 1 << 3 } Flags;
    typedef enum { MODE_X, MODE_Y } Mode;
    typedef struct { int a; unsigned b : 3; } Rec;

    int thing_add(int x, int y);
    int rec_a(Rec r);

    #endif
""")

FIXTURE_SOURCE = textwrap.dedent("""\
    #include "probe.h"

    int thing_add(int x, int y) { return x + y; }
    int rec_a(Rec r) { return r.a; }
""")

#: The filename a C library carries on this host, and the flags that build one.
_SHARED_SUFFIX = {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")


def _require(*programs: str) -> str:
    """Return the first of ``programs`` on PATH, or skip.

    The skip is narrow on purpose: a gate that quietly no-ops when its toolchain
    is missing proves nothing while reporting green.
    """
    for candidate in programs:
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip(f"none of {', '.join(programs)} on PATH")


def _backend_name() -> str:
    """Return a parser backend that can read the fixture, or skip."""
    for name in ("libclang", "tree-sitter"):
        if is_backend_available(name):
            return name
    pytest.skip("no parser backend available")


def _parse_fixture() -> SourceUnit:
    return get_backend(_backend_name()).parse(FIXTURE_HEADER, "probe.h")


def _build_c_library(workdir: Path, stem: str) -> Path:
    """Compile the fixture into a real shared library and return its path."""
    compiler = _require("cc", "gcc", "clang")
    (workdir / "probe.h").write_text(FIXTURE_HEADER, encoding="utf-8")
    (workdir / "probe.c").write_text(FIXTURE_SOURCE, encoding="utf-8")
    out = workdir / f"lib{stem}{_SHARED_SUFFIX}"
    argv = [compiler, "-shared", "-fPIC", "probe.c", "-I.", "-o", str(out)]
    if sys.platform == "win32":
        argv.remove("-fPIC")
    result = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"fixture library failed to build:\n{result.stderr}"
    assert out.is_file()
    return out


def _scaffold_to(target: str, workdir: Path, pkg: str) -> Path:
    """Scaffold a package for ``target`` into ``workdir`` and return the root."""
    root = workdir / f"{target}-pkg"
    layout = scaffold(
        _parse_fixture(),
        ScaffoldOptions(package_name=pkg, target_language=target, layout="package"),
    )
    layout.write_to_disk(root)
    return root


def _run_python(script: str, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter so an import failure is observable."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# ctypes
# ---------------------------------------------------------------------------


class TestScaffoldedCtypesPackageRuns:
    def test_package_imports_and_calls_into_the_c_library(self, tmp_path: Path) -> None:
        """The generated package must import and the call must reach the C function.

        ``thing_add(2, 3) == 5`` is the whole point: the value can only be 5 if
        the module imported, the library loaded, the symbol resolved and the
        argument types were right. No part of that is provable from the text of
        the generated file.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe")

        script = textwrap.dedent("""\
            from probe import _bindings

            assert _bindings._lib.thing_add(2, 3) == 5, "the call did not reach the C function"
            assert _bindings.FLAG_D == 8
            assert _bindings.MODE_Y == 1
            r = _bindings.Rec(a=7, b=5)
            assert _bindings._lib.rec_a(r) == 7, "the record did not survive the ABI boundary"
            print("CALLED")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"generated ctypes package is not usable:\n{result.stderr}"
        assert "CALLED" in result.stdout

    def test_generated_typedef_names_are_bound(self, tmp_path: Path) -> None:
        """A ``typedef enum`` must leave a usable name behind, not a self-reference.

        ``Flags = Flags`` raises ``NameError`` at import, so this cannot be
        checked by looking for the name in the file: the name is *there*.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe")

        script = textwrap.dedent("""\
            import ctypes
            from probe import _bindings

            for name in ("Flags", "Mode"):
                alias = getattr(_bindings, name, None)
                assert alias is not None, name + " is not bound in the generated module"
                assert issubclass(alias, ctypes._SimpleCData), name + " is not a ctypes type"
                assert ctypes.sizeof(alias) == ctypes.sizeof(ctypes.c_int)
            print("BOUND")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"typedef'd enums are not usable:\n{result.stderr}"
        assert "BOUND" in result.stdout

    def test_import_fails_when_the_native_library_is_absent(self, tmp_path: Path) -> None:
        """AGENTS.md §1: the tripwire must fail when the binary is missing.

        The generated tripwire imports ``_bindings``, so the import is where the
        absence has to be detected. A module that imports cleanly without its
        library and defers the failure to the first call is the green mirage
        that invariant forbids.
        """
        root = _scaffold_to("ctypes", tmp_path, "nosuchlib_probe")

        result = _run_python(
            "import nosuchlib_probe",
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "NOSUCHLIB_PROBE_LIBRARY": ""},
        )
        assert result.returncode != 0, "the package imported with no native library present"
        # A bare non-zero exit would be satisfied by the module being broken for
        # some unrelated reason -- which is how this file's first run passed
        # while the package could not import at all. The failure has to be
        # *about* the missing library.
        assert "OSError" in result.stderr, f"the import did not fail on the missing library:\n{result.stderr}"
        assert "nosuchlib_probe" in result.stderr.split("OSError", 1)[1], (
            f"the failure does not name the library it could not find:\n{result.stderr}"
        )

    def test_generated_tripwire_passes_against_the_real_library(self, tmp_path: Path) -> None:
        """The tripwire the scaffolder emits must itself run and pass."""
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe")

        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", "tests/test_tripwire.py", "-p", "no:cacheprovider", "-q"],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root / "src"),
                "PROBE_LIBRARY": str(library),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"generated tripwire does not pass:\n{result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# Nim
# ---------------------------------------------------------------------------


class TestScaffoldedNimPackageCompiles:
    def test_bindings_compile_link_and_execute(self, tmp_path: Path) -> None:
        """The generated Nim bindings must reach a running binary.

        Nim only emits an ``importc`` type into its generated C when something
        *uses* it, so the program below declares a variable of every generated
        type. Without that the C never mentions ``enum Flags`` and a bad
        ``importc`` compiles cleanly -- which is exactly how this defect stayed
        invisible.
        """
        nim = _require("nim")
        compiler = _require("cc", "gcc", "clang")
        root = _scaffold_to("nim", tmp_path, "probe")

        work = tmp_path / "nimrun"
        work.mkdir()
        (work / "probe.h").write_text(FIXTURE_HEADER, encoding="utf-8")
        (work / "probe.c").write_text(FIXTURE_SOURCE, encoding="utf-8")
        shutil.copy(root / "src" / "probe" / "bindings.nim", work / "bindings.nim")
        (work / "main.nim").write_text(
            textwrap.dedent("""\
                import bindings

                var f: Flags = FLAG_D
                var m: Mode = MODE_Y
                var r: Rec
                r.a = 7
                doAssert thing_add(2, 3) == 5
                doAssert rec_a(r) == 7
                doAssert ord(f) == 8
                doAssert ord(m) == 1
                echo "RAN"
            """),
            encoding="utf-8",
        )

        obj = subprocess.run(  # noqa: S603
            [compiler, "-c", "probe.c", "-I.", "-o", "probe.o"],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert obj.returncode == 0, f"fixture object failed to build:\n{obj.stderr}"

        build = subprocess.run(  # noqa: S603
            [nim, "c", "--hints:off", "--nimcache:nimcache", "--passC:-I.", "--passL:probe.o", "-o:main", "main.nim"],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, f"generated Nim bindings do not compile:\n{build.stdout}\n{build.stderr}"

        run = subprocess.run(  # noqa: S603
            [str(work / ("main.exe" if sys.platform == "win32" else "main"))],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, f"generated Nim binary failed:\n{run.stdout}\n{run.stderr}"
        assert "RAN" in run.stdout
