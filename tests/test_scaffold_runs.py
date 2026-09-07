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
* the generated tests must themselves **run**, both of them;
* and with the native library absent both the package and its tripwire must
  **fail**, which is the tripwire invariant in ``AGENTS.md`` §1 -- a tripwire
  that passes without the binary present is a green mirage.

Every gate runs against each installed parser backend. The ``importc`` spelling
the Nim writer emits is chosen from ``is_typedef``, which the two backends
derive by different routes, so a gate bound to one backend would leave the other
one's output unproven.

The fixture header is the smallest one reaching every discrimination these gates
were written for. Each declaration earns its place:

``typedef enum { ... } Flags;``
    Tag-less. Declares no ``enum Flags``, so ``importc: "enum Flags"`` names an
    incomplete type, and ``Flags = Flags`` in ctypes raises ``NameError``. Its
    enumerators are written as plain integers rather than as ``1 << 3``: the
    tree-sitter backend passes such an initialiser through verbatim, and Nim
    spells that operator ``shl``, which is a separate defect this file is not
    the gate for.
``typedef enum { ... } Mode;``
    A second tag-less enum, so the first cannot pass by coincidence.
``typedef enum Mode2 { ... } Mode2;``
    Tagged, alias repeating the tag. ``enum Mode2`` really is declared here.
``enum Bare { ... };``
    A tag with no typedef at all: the bare name is *not* a type spelling.
``typedef struct { ... } Rec;``
    Tag-less, and carries a bit-field so the record's layout is exercised.
``typedef struct Foo { ... } FooAlias;``
    Tagged, alias differing from the tag -- the case where the tag must survive
    into the emitted C rather than being replaced by the alias.
``thing_add`` / ``rec_a`` / ``foo_a``
    Something to call. Two or more functions also make the generated test files
    multi-line, which is the shape that broke their indentation.
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
from tests.native_build import shared_library_command, shared_library_filename

#: Every parser backend the writers can be driven from. Both are exercised
#: because the Nim ``importc`` spelling depends on IR each one fills in itself.
BACKENDS = ("libclang", "tree-sitter")

FIXTURE_HEADER = textwrap.dedent("""\
    #ifndef PROBE_H
    #define PROBE_H

    typedef enum { FLAG_A = 1, FLAG_D = 8 } Flags;
    typedef enum { MODE_X, MODE_Y } Mode;
    typedef enum Mode2 { MODE2_X, MODE2_Y } Mode2;
    enum Bare { BARE_A, BARE_B };
    typedef struct { int a; unsigned b : 3; } Rec;
    typedef struct Foo { int f; } FooAlias;

    int thing_add(int x, int y);
    int rec_a(Rec r);
    int foo_a(FooAlias v);

    #endif
""")

FIXTURE_SOURCE = textwrap.dedent("""\
    #include "probe.h"

    int thing_add(int x, int y) { return x + y; }
    int rec_a(Rec r) { return r.a; }
    int foo_a(FooAlias v) { return v.f; }
""")

#: C keeps tags and ordinary identifiers in separate namespaces, so a struct tag
#: and a function may share a spelling. Both reach Python as ``Dup``.
COLLISION_HEADER = textwrap.dedent("""\
    #ifndef DUP_H
    #define DUP_H

    struct Dup { int a; };
    int Dup(void);

    #endif
""")

COLLISION_SOURCE = textwrap.dedent("""\
    #include "dup.h"

    int Dup(void) { return 11; }
""")


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


@pytest.fixture(params=BACKENDS)
def backend_name(request: pytest.FixtureRequest) -> str:
    """Run the gate once per installed parser backend."""
    name = str(request.param)
    if not is_backend_available(name):
        pytest.skip(f"{name} backend not available")
    return name


def _parse(backend_name: str, source: str, filename: str) -> SourceUnit:
    return get_backend(backend_name).parse(source, filename)


def _build_c_library(
    workdir: Path,
    stem: str,
    *,
    header: str = FIXTURE_HEADER,
    source: str = FIXTURE_SOURCE,
    basename: str = "probe",
) -> Path:
    """Compile ``source`` into a real shared library and return its path."""
    compiler = _require("cc", "gcc", "clang")
    (workdir / f"{basename}.h").write_text(header, encoding="utf-8")
    (workdir / f"{basename}.c").write_text(source, encoding="utf-8")
    out = workdir / shared_library_filename(stem)
    argv = shared_library_command(compiler, [f"{basename}.c"], str(out), includes=["."])
    result = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"fixture library failed to build:\n{result.stderr}"
    assert out.is_file()
    return out


def _scaffold_to(
    target: str,
    workdir: Path,
    pkg: str,
    *,
    backend_name: str,
    header: str = FIXTURE_HEADER,
    filename: str = "probe.h",
    options: dict[str, object] | None = None,
) -> Path:
    """Scaffold a package for ``target`` into ``workdir`` and return the root."""
    root = workdir / f"{target}-{pkg}"
    layout = scaffold(
        _parse(backend_name, header, filename),
        ScaffoldOptions(
            package_name=pkg,
            target_language=target,
            layout="package",
            options=dict(options or {}),
        ),
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


def _run_pytest(target: str, *, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the generated test suite at ``target`` inside the scaffolded package."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", target, "-p", "no:cacheprovider", "-q"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src"), **env},
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# ctypes
# ---------------------------------------------------------------------------


class TestScaffoldedCtypesPackageRuns:
    def test_package_imports_and_calls_into_the_c_library(self, tmp_path: Path, backend_name: str) -> None:
        """The generated package must import and the call must reach the C function.

        ``thing_add(2, 3) == 5`` is the whole point: the value can only be 5 if
        the module imported, the library loaded, the symbol resolved and the
        argument types were right. No part of that is provable from the text of
        the generated file.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        script = textwrap.dedent("""\
            from probe import _bindings

            assert _bindings._lib.thing_add(2, 3) == 5, "the call did not reach the C function"
            assert _bindings.thing_add(2, 3) == 5, "the re-exported name is not the configured callable"
            assert _bindings.FLAG_D == 8
            assert _bindings.MODE_Y == 1
            assert _bindings.MODE2_Y == 1
            assert _bindings.BARE_B == 1
            r = _bindings.Rec(a=7, b=5)
            assert _bindings._lib.rec_a(r) == 7, "the record did not survive the ABI boundary"
            assert _bindings._lib.foo_a(_bindings.FooAlias(f=9)) == 9, "the tagged typedef record is unusable"
            print("CALLED")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"generated ctypes package is not usable:\n{result.stderr}"
        assert "CALLED" in result.stdout

    def test_generated_typedef_names_are_bound(self, tmp_path: Path, backend_name: str) -> None:
        """A ``typedef enum`` must leave a usable name behind, not a self-reference.

        ``Flags = Flags`` raises ``NameError`` at import, so this cannot be
        checked by looking for the name in the file: the name is *there*.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        script = textwrap.dedent("""\
            import ctypes
            from probe import _bindings

            for name in ("Flags", "Mode", "Mode2"):
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

    def test_a_function_does_not_clobber_a_same_named_struct(self, tmp_path: Path, backend_name: str) -> None:
        """``struct Dup { ... }; int Dup(void);`` must leave the class intact.

        Both land on the module-level name ``Dup``, and the exported-symbol
        block is emitted last, so re-exporting the function unconditionally
        replaces the struct class with a function pointer. Nothing raises: the
        module imports, and ``Dup(a=1)`` fails much later somewhere else.
        """
        library = _build_c_library(
            tmp_path,
            "dup",
            header=COLLISION_HEADER,
            source=COLLISION_SOURCE,
            basename="dup",
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "dup",
            backend_name=backend_name,
            header=COLLISION_HEADER,
            filename="dup.h",
        )

        script = textwrap.dedent("""\
            import ctypes
            from dup import _bindings

            assert issubclass(_bindings.Dup, ctypes.Structure), "the struct class was replaced"
            assert ctypes.sizeof(_bindings.Dup) == ctypes.sizeof(ctypes.c_int)
            assert _bindings.Dup(a=3).a == 3
            assert _bindings._lib.Dup() == 11, "the C function is unreachable through the library object"
            print("INTACT")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "DUP_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"the struct class did not survive the export block:\n{result.stderr}"
        assert "INTACT" in result.stdout

    def test_the_library_name_is_separable_from_the_package_name(self, tmp_path: Path, backend_name: str) -> None:
        """Scaffolding ``probe_bindings`` around ``libprobe`` must produce a usable package.

        The two names coincide only by accident, and a gate whose fixture makes
        them equal cannot see the difference at all. The default is asserted in
        the same test: without the option the package looks for a library named
        after itself and fails, which is what makes the option load-bearing
        rather than decorative.
        """
        library = _build_c_library(tmp_path, "probe")

        named = _scaffold_to(
            "ctypes",
            tmp_path,
            "probe_bindings",
            backend_name=backend_name,
            options={"library": "probe"},
        )
        result = _run_python(
            "import probe_bindings; assert probe_bindings.thing_add(2, 3) == 5; print('CALLED')",
            cwd=tmp_path,
            env={"PYTHONPATH": str(named / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"the package could not load the library it was told to:\n{result.stderr}"
        assert "CALLED" in result.stdout

        # Negative control: the package name is the fallback, and it is wrong here.
        defaulted = _scaffold_to("ctypes", tmp_path, "probe_bindings", backend_name=backend_name)
        fallback = _run_python(
            "import probe_bindings",
            cwd=tmp_path,
            env={"PYTHONPATH": str(defaulted / "src"), "PROBE_BINDINGS_LIBRARY": ""},
        )
        assert fallback.returncode != 0, "the package found a library that is not installed under that name"
        assert "probe_bindings" in fallback.stderr.split("OSError", 1)[-1], (
            f"the fallback did not look for the package's own name:\n{fallback.stderr}"
        )

    def test_import_fails_when_the_native_library_is_absent(self, tmp_path: Path, backend_name: str) -> None:
        """AGENTS.md §1: the tripwire must fail when the binary is missing.

        The generated tripwire imports ``_bindings``, so the import is where the
        absence has to be detected. A module that imports cleanly without its
        library and defers the failure to the first call is the green mirage
        that invariant forbids.
        """
        root = _scaffold_to("ctypes", tmp_path, "nosuchlib_probe", backend_name=backend_name)

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

    def test_generated_tests_pass_against_the_real_library(self, tmp_path: Path, backend_name: str) -> None:
        """Both generated test files must themselves run and pass.

        The whole ``tests/`` directory is the target, not ``test_tripwire.py``:
        the tripwire and the unit test are emitted from separate templates, and
        naming one file leaves the other's syntax unexecuted by anything in the
        repository.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        result = _run_pytest("tests/", root=root, env={"PROBE_LIBRARY": str(library)})
        assert result.returncode == 0, f"generated tests do not pass:\n{result.stdout}\n{result.stderr}"
        # Both files have to have been collected. A run that found only the
        # tripwire would also exit 0.
        assert "2 passed" in result.stdout, f"the generated suite did not run both files:\n{result.stdout}"

    def test_generated_tripwire_fails_when_the_native_library_is_absent(
        self, tmp_path: Path, backend_name: str
    ) -> None:
        """The §1 invariant, run rather than inferred.

        The two neighbouring gates prove the tripwire passes *with* the library
        and that ``import <pkg>`` fails *without* it. Neither runs the emitted
        tripwire in the failure direction the invariant is actually about, so
        this does. The failure arrives as a collection error, not a test
        failure, because the import sits at module scope -- pinned here so a
        template that moved the import into the test body, and thereby turned a
        hard failure into a reported one, is visible as a change.
        """
        root = _scaffold_to("ctypes", tmp_path, "nosuchlib_probe", backend_name=backend_name)

        result = _run_pytest("tests/test_tripwire.py", root=root, env={"NOSUCHLIB_PROBE_LIBRARY": ""})
        assert result.returncode != 0, "the generated tripwire passed with no native library present"
        assert result.returncode == 2, (
            f"expected pytest's collection-error exit status, got {result.returncode}:\n{result.stdout}"
        )
        assert "error during collection" in result.stdout, f"the tripwire was collected and then ran:\n{result.stdout}"
        assert "OSError" in result.stdout, f"the tripwire did not fail on the missing library:\n{result.stdout}"


# ---------------------------------------------------------------------------
# Nim
# ---------------------------------------------------------------------------


class TestScaffoldedNimPackageCompiles:
    def test_bindings_compile_link_and_execute(self, tmp_path: Path, backend_name: str) -> None:
        """The generated Nim bindings must reach a running binary.

        Nim only emits an ``importc`` type into its generated C when something
        *uses* it, so the program below declares a variable of every generated
        type. Without that the C never mentions ``enum Flags`` and a bad
        ``importc`` compiles cleanly -- which is exactly how this defect stayed
        invisible.
        """
        nim = _require("nim")
        compiler = _require("cc", "gcc", "clang")
        root = _scaffold_to("nim", tmp_path, "probe", backend_name=backend_name)

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
                var m2: Mode2 = MODE2_Y
                var b: Bare = BARE_B
                var r: Rec
                var v: FooAlias
                r.a = 7
                v.f = 9
                doAssert thing_add(2, 3) == 5
                doAssert rec_a(r) == 7
                doAssert foo_a(v) == 9
                doAssert ord(f) == 8
                doAssert ord(m) == 1
                doAssert ord(m2) == 1
                doAssert ord(b) == 1
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

    @pytest.mark.parametrize("generated", ["tests/test_tripwire.nim", "tests/test_probe.nim"])
    def test_generated_nim_tests_compile(self, tmp_path: Path, backend_name: str, generated: str) -> None:
        """Both emitted Nim test files must be syntactically valid Nim.

        They are produced by separate templates, and the indentation defect that
        made them unparseable shows up only once the header declares more than
        one function -- so nothing short of handing them to the compiler
        detects it. Compilation stops at ``--compileOnly``: the tripwire wants
        the shared library at *runtime*, which the ctypes gates already cover.
        """
        nim = _require("nim")
        root = _scaffold_to("nim", tmp_path, "probe", backend_name=backend_name)
        (root / "probe.h").write_text(FIXTURE_HEADER, encoding="utf-8")

        build = subprocess.run(  # noqa: S603
            [
                nim,
                "c",
                "--hints:off",
                "--compileOnly",
                "--nimcache:nimcache",
                "--path:src",
                "--passC:-I.",
                generated,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, f"generated {generated} does not compile:\n{build.stdout}\n{build.stderr}"
