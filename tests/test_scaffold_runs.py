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
#:
#: ``_lib`` is the second collision axis and the more destructive one: it names
#: nothing in the header's own namespace, but the generated module binds it to
#: the loaded library, and every exported symbol is read from it. Re-exporting
#: a C function of that name replaces the handle with a function pointer, and
#: the next export line dies on it.
COLLISION_HEADER = textwrap.dedent("""\
    #ifndef DUP_H
    #define DUP_H

    struct Dup { int a; };
    int Dup(void);
    int _lib(void);
    int thing_add(int a, int b);

    #endif
""")

COLLISION_SOURCE = textwrap.dedent("""\
    #include "dup.h"

    int Dup(void) { return 11; }
    int _lib(void) { return 13; }
    int thing_add(int a, int b) { return a + b; }
""")

#: Every shape in which an enum can reach a record member or a function
#: signature. An enum binds no ctypes class, so each of these used to reach the
#: generated module as the C spelling: ``("m", enum Colour)`` under libclang,
#: which is a ``SyntaxError``, and ``("m", Colour)`` under tree-sitter, which is
#: a ``NameError`` -- the alias is emitted, but into the typedefs section, which
#: comes *after* the records that use it.
#:
#: ``Tagged``       the ``enum E`` tag spelling, with no typedef on the enum
#: ``Aliased``      an enum typedef whose name differs from the enum's own
#:                  tag, so only the ``Typedef`` node carries that name -- and
#:                  it renders into the typedefs section, which is emitted
#:                  after the record that uses it
#: ``Anon``         a tag-less enum typedef, whose alias the ``Enum`` node
#:                  carries itself, into the enums section
#: ``Choice``       the same member in a union rather than a struct
#: ``Row``          an array of enum, where the spelling is composed into
#:                  ``Colour * 4`` and a bad element type is still a bad name
#: ``colour_next``  an enum as a parameter and as a return type, which is the
#:                  same defect outside a record entirely
#: ``Shadowed``     the negative control. ``enum Tone`` and ``typedef struct
#:                  { ... } Tone`` are both legal in one unit -- a tag and an
#:                  ordinary identifier are separate namespaces in C -- and an
#:                  unprefixed ``Tone`` is the *record*. Resolving every bare
#:                  enum tag to an integer would retype this member from eight
#:                  bytes to four, so this case fails if the fix over-reaches
#:
#: Every *record* here is a tag-less typedef, so no record is ever spelled
#: ``struct X``. That is deliberate: a record named by its tag in a function
#: signature reaches the generated module as ``argtypes = [struct Tagged]``,
#: which is a separate unfixed defect in the same writer. Leaving it in the
#: fixture would make this gate red for a reason it is not about.
#:
#: Each ``*_size`` function reports what the C compiler actually laid out, so the
#: gate compares against the real ABI rather than against a second guess.
ENUM_HEADER = textwrap.dedent("""\
    #ifndef ENUMS_H
    #define ENUMS_H

    enum Colour { COLOUR_RED = 0, COLOUR_BLUE = 1 };
    typedef enum Level { LEVEL_LOW = 0, LEVEL_HIGH = 1 } LevelAlias;
    typedef enum { STATE_OFF = 0, STATE_ON = 1 } State;

    typedef struct { enum Colour m; } Tagged;
    typedef struct { LevelAlias m; } Aliased;
    typedef struct { State m; } Anon;
    typedef union { enum Colour m; int i; } Choice;
    typedef struct { enum Colour arr[4]; } Row;

    enum Tone { TONE_A = 0, TONE_B = 1 };
    typedef struct { int lo; int hi; } Tone;
    typedef struct { Tone t; } Shadowed;

    int tagged_m(Tagged r);
    int aliased_m(Aliased r);
    int anon_m(Anon r);
    int choice_m(Choice u);
    int row_at(Row r, int i);
    int shadowed_lo(Shadowed s);
    enum Colour colour_next(enum Colour c);

    int tagged_size(void);
    int aliased_size(void);
    int anon_size(void);
    int choice_size(void);
    int row_size(void);
    int shadowed_size(void);
    int colour_size(void);

    #endif
""")

ENUM_SOURCE = textwrap.dedent("""\
    #include "enums.h"

    int tagged_m(Tagged r) { return (int)r.m; }
    int aliased_m(Aliased r) { return (int)r.m; }
    int anon_m(Anon r) { return (int)r.m; }
    int choice_m(Choice u) { return (int)u.m; }
    int row_at(Row r, int i) { return (int)r.arr[i]; }
    int shadowed_lo(Shadowed s) { return s.t.lo; }
    enum Colour colour_next(enum Colour c) {
        return c == COLOUR_RED ? COLOUR_BLUE : COLOUR_RED;
    }

    int tagged_size(void) { return (int)sizeof(Tagged); }
    int aliased_size(void) { return (int)sizeof(Aliased); }
    int anon_size(void) { return (int)sizeof(Anon); }
    int choice_size(void) { return (int)sizeof(Choice); }
    int row_size(void) { return (int)sizeof(Row); }
    int shadowed_size(void) { return (int)sizeof(Shadowed); }
    int colour_size(void) { return (int)sizeof(enum Colour); }
""")

#: A C++ ``enum class`` member. The enumerators are scoped to the tag, so the
#: member type is the only place the enum is named -- there is no enumerator
#: constant whose presence could stand in for the member being right.
#:
#: The accessors are reached through a plain ``ctypes.CDLL`` rather than through
#: the generated module: libclang drops an ``extern "C"`` function from the IR
#: altogether, so binding this gate to the generated prototypes would prove
#: nothing under one of the two backends. The record layout is what is under
#: test, and it comes from the generated module either way.
SCOPED_HEADER = textwrap.dedent("""\
    #ifndef SCOPED_H
    #define SCOPED_H

    enum class Scoped : int { SCOPED_LOW = 0, SCOPED_HIGH = 1 };
    struct ScopedRec { Scoped m; };

    extern "C" int scoped_m(ScopedRec r);
    extern "C" int scoped_size(void);

    #endif
""")

SCOPED_SOURCE = textwrap.dedent("""\
    #include "scoped.h"

    extern "C" int scoped_m(ScopedRec r) { return static_cast<int>(r.m); }
    extern "C" int scoped_size(void) { return static_cast<int>(sizeof(ScopedRec)); }
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


def _run_pytest(target: str, *extra_args: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the generated test suite at ``target`` inside the scaffolded package."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", target, "-p", "no:cacheprovider", "-q", *extra_args],
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
        """A function must not clobber a same-named struct or the library handle.

        Both ``struct Dup`` and ``int Dup(void)`` land on the module-level name
        ``Dup``, and the exported-symbol block is emitted last, so re-exporting
        the function unconditionally replaces the struct class with a function
        pointer. Nothing raises: the module imports, and ``Dup(a=1)`` fails much
        later somewhere else.

        ``int _lib(void)`` is the same collision against a name no declaration
        binds -- the loader preamble binds it -- and it is fatal rather than
        silent: it overwrites the library handle that every later export reads
        from, so ``thing_add`` dies with ``AttributeError`` on a ``_FuncPtr``.
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

            assert isinstance(_bindings._lib, ctypes.CDLL), "the library handle was replaced"
            assert _bindings._lib._lib() == 13, "the C function is unreachable through the library object"

            # A function following the collision in the export block still binds.
            # This is what fails first when the handle is destroyed.
            assert _bindings.thing_add(2, 3) == 5, "a later export did not survive the collision"
            print("INTACT")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "DUP_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"the struct class did not survive the export block:\n{result.stderr}"
        assert "INTACT" in result.stdout

    def test_enum_typed_members_and_signatures_match_the_c_abi(self, tmp_path: Path, backend_name: str) -> None:
        """An enum-typed member must be a usable ctypes type of the C enum's width.

        An enum binds no ctypes class, so a member typed by one has to resolve
        to the underlying integer at the point of use. Emitting the C spelling
        instead is fatal in both of the shapes a backend can produce it:
        ``("m", enum Colour)`` does not parse at all, and ``("m", Colour)``
        parses and then raises ``NameError``, because the alias is emitted into
        a section that comes after the records.

        Neither the round-trip nor the width alone is enough. A member of the
        wrong width still round-trips a small value, so the C compiler's own
        ``sizeof`` is the second assertion; and a matching size proves nothing
        about which member the value landed in, so the value is passed through
        a real by-value call to C and read back from there.
        """
        library = _build_c_library(
            tmp_path,
            "enums",
            header=ENUM_HEADER,
            source=ENUM_SOURCE,
            basename="enums",
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "enums",
            backend_name=backend_name,
            header=ENUM_HEADER,
            filename="enums.h",
        )

        script = textwrap.dedent("""\
            import ctypes
            from enums import _bindings as b

            lib = b._lib

            # Width first: a member narrower or wider than the C enum still
            # round-trips COLOUR_BLUE, so the value checks below cannot see it.
            colour = lib.colour_size()
            assert colour == ctypes.sizeof(ctypes.c_int), "fixture assumption: C enum is not int-sized"
            for name, size_fn in (
                ("Tagged", lib.tagged_size),
                ("Aliased", lib.aliased_size),
                ("Anon", lib.anon_size),
                ("Choice", lib.choice_size),
                ("Row", lib.row_size),
                ("Shadowed", lib.shadowed_size),
            ):
                cls = getattr(b, name)
                assert ctypes.sizeof(cls) == size_fn(), (
                    name + ": ctypes lays out " + str(ctypes.sizeof(cls))
                    + " bytes, the C compiler lays out " + str(size_fn())
                )

            # Then the member itself, across the real ABI boundary.
            assert lib.tagged_m(b.Tagged(m=b.COLOUR_BLUE)) == 1, "the tag-spelled member did not survive the call"
            assert lib.aliased_m(b.Aliased(m=b.LEVEL_HIGH)) == 1, "the typedef'd member did not survive the call"
            assert lib.anon_m(b.Anon(m=b.STATE_ON)) == 1, "the tag-less typedef member did not survive the call"
            assert lib.choice_m(b.Choice(m=b.COLOUR_BLUE)) == 1, "the union member did not survive the call"

            row = b.Row(arr=(ctypes.c_int * 4)(0, 1, 1, 0))
            assert [lib.row_at(row, i) for i in range(4)] == [0, 1, 1, 0], "the enum array did not survive the call"

            # Negative control: ``Tone`` names the record, not ``enum Tone``.
            # A fix that resolved every bare enum tag would make this member a
            # four-byte integer, and the size loop above would already be red.
            assert lib.shadowed_lo(b.Shadowed(t=b.Tone(lo=4, hi=9))) == 4, (
                "the typedef shadowing an enum tag was resolved to the enum"
            )

            # An enum as a parameter and as a return type, outside any record.
            assert lib.colour_next(b.COLOUR_RED) == b.COLOUR_BLUE
            assert lib.colour_next(b.COLOUR_BLUE) == b.COLOUR_RED
            print("ABI-MATCH")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "ENUMS_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"enum-typed members are not usable:\n{result.stderr}"
        assert "ABI-MATCH" in result.stdout

    def test_a_scoped_enum_member_matches_the_cpp_abi(self, tmp_path: Path, backend_name: str) -> None:
        """A C++ ``enum class`` member is the same defect with no enumerator to hide it.

        A scoped enumerator is spelled ``Scoped::SCOPED_HIGH`` and is not
        introduced into the enclosing scope, so the member's type is the only
        place the tag is named. The layout is checked against the C++
        compiler's own ``sizeof``, and the member against a real by-value call.
        """
        compiler = _require("c++", "g++", "clang++")
        (tmp_path / "scoped.h").write_text(SCOPED_HEADER, encoding="utf-8")
        (tmp_path / "scoped.cpp").write_text(SCOPED_SOURCE, encoding="utf-8")
        out = tmp_path / shared_library_filename("scoped")
        argv = shared_library_command(compiler, ["scoped.cpp"], str(out), includes=["."])
        built = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, check=False)  # noqa: S603
        assert built.returncode == 0, f"fixture C++ library failed to build:\n{built.stderr}"

        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "scoped",
            backend_name=backend_name,
            header=SCOPED_HEADER,
            filename="scoped.hpp",
        )

        script = textwrap.dedent("""\
            import ctypes
            import os
            from scoped import _bindings as b

            lib = ctypes.CDLL(os.environ["SCOPED_SO"])
            lib.scoped_size.restype = ctypes.c_int
            assert ctypes.sizeof(b.ScopedRec) == lib.scoped_size(), (
                "ctypes lays out " + str(ctypes.sizeof(b.ScopedRec))
                + " bytes, the C++ compiler lays out " + str(lib.scoped_size())
            )

            lib.scoped_m.argtypes = [b.ScopedRec]
            lib.scoped_m.restype = ctypes.c_int
            assert lib.scoped_m(b.ScopedRec(m=1)) == 1, "the scoped-enum member did not survive the call"
            print("SCOPED-MATCH")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={
                "PYTHONPATH": str(root / "src"),
                "SCOPED_LIBRARY": str(out),
                "SCOPED_SO": str(out),
            },
        )
        assert result.returncode == 0, f"the scoped-enum member is not usable:\n{result.stderr}"
        assert "SCOPED-MATCH" in result.stdout

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
        # ``[1]``, not ``[-1]``: on a one-element split ``[-1]`` is the whole
        # stderr, which contains the package name in every traceback path, so
        # the control would pass for a module broken in some unrelated way.
        # The OSError check is what makes the index safe and the failure
        # specific to the library lookup.
        assert "OSError" in fallback.stderr, f"the fallback did not fail on the missing library:\n{fallback.stderr}"
        assert "probe_bindings" in fallback.stderr.split("OSError", 1)[1], (
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
        """Both generated self-checking test files must themselves run and pass.

        Both are named, not just ``test_tripwire.py``: the tripwire and the unit
        test are emitted from separate templates, and naming one leaves the
        other's syntax unexecuted by anything in the repository.

        ``tests/`` as a whole is *not* the target, because the scaffolder also
        emits ``test_workorder.py``, whose Tier 2 and Tier 3 cases fail on
        purpose until a human writes the assertions. A directory-wide run would
        make this gate red for the one reason that means the generator is
        working. The exclusion is not a blind spot: the work-order suite is
        asserted below to collect and to be red, so a work-order file that
        vanished, or that went quietly green, still fails here.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        result = _run_pytest(
            "tests/test_tripwire.py",
            "tests/test_bindings.py",
            root=root,
            env={"PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"generated tests do not pass:\n{result.stdout}\n{result.stderr}"

        # Every file has to have been collected -- a run that found only the
        # tripwire would also exit 0. Asserting the *filenames* rather than a
        # test count says what is actually meant: two tests inside one file
        # would satisfy a count and leave the other file's syntax unexecuted.
        collected = _run_pytest("tests/", "--collect-only", root=root, env={"PROBE_LIBRARY": str(library)})
        assert collected.returncode == 0, f"the generated suite did not collect:\n{collected.stdout}"
        for name in ("test_tripwire.py", "test_bindings.py", "test_workorder.py"):
            assert name in collected.stdout, f"the generated suite did not collect {name}:\n{collected.stdout}"

        # The outstanding work is outstanding. A work-order suite that exits 0
        # has stopped asking for the assertions it exists to ask for.
        work_order = _run_pytest("tests/test_workorder.py", root=root, env={"PROBE_LIBRARY": str(library)})
        assert work_order.returncode != 0, (
            f"the generated work-order suite passes; its stubs no longer fail:\n{work_order.stdout}"
        )

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
