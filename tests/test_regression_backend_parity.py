"""Regression pins for backend/writer defects fixed on the libclang-cython-output branch.

Every test here drives the real pipeline -- real C or C++ source through a real
parser backend into a real writer.  Hand-constructed IR is deliberately avoided:
each defect pinned below produced correct-looking IR assertions while the
generated output stayed wrong, so IR-level tests are what let these bugs through.

Scope of this module:

R5  Tag-less ``typedef enum`` must reach Cython as ``ctypedef enum`` and reach the
    cffi writer without an invented tag.
R6  A C tag colliding with a Cython keyword must carry its ``"cname"`` on
    *forward* declarations, not only on definitions.
R7  ``LibclangBackend.parse``'s filtering (``allowlist`` / ``denylist``) narrows
    the merged result in *both* ``recursive_includes`` modes, deny wins over
    allow, and matching is by whole resolved path rather than by substring.
R10 A free function's ``Owner<int>::type`` must not decay to the bare member name
    ``type``, and a C++11 ``using`` alias must be captured like a ``typedef``.
R9  The tree-sitter backend must not drop ``const``/``volatile`` from any
    declaration position, and must not fold a *trailing* C++ qualifier into a
    return type.
R12 libclang must keep an enum tag that is declared but never defined, matching
    how it already keeps an undefined ``struct`` tag.

Cross-backend parity is the last section and is the highest-value part: both
backends feed the same Cython writer, so a divergence between them is a defect in
one of them.  Known divergences are recorded as strict xfails naming the defect so
they stay visible instead of being silently tolerated.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import textwrap
from pathlib import Path

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.ir import Enum
from headerkit.writers import get_writer
from headerkit.writers.cffi import header_to_cffi
from headerkit.writers.cython import write_pxd

libclang = pytest.mark.libclang
treesitter = pytest.mark.treesitter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pxd(backend_name: str, code: str, filename: str = "test.h", extra_args: list[str] | None = None) -> str:
    """Parse ``code`` with a named backend and render it through the Cython writer."""
    backend = get_backend(backend_name)
    return write_pxd(backend.parse(code, filename, extra_args=extra_args or []))


def _require_c_toolchain() -> str:
    """Skip unless both Cython and a C compiler are present; return the compiler path.

    The skip is narrow and explicit on purpose. A compile check that quietly
    no-ops when the toolchain is absent proves nothing while looking green.
    """
    pytest.importorskip("Cython", reason="Cython is required to verify generated C")
    for candidate in ("cc", "gcc", "clang"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no C compiler (cc/gcc/clang) on PATH")


def _cythonize(workdir: Path, header: str, pxd: str, pyx: str, stem: str = "mod") -> str:
    """Write a header/.pxd/.pyx triple, run Cython, and return the generated C source.

    :raises AssertionError: if Cython itself rejects the ``.pxd``/``.pyx`` pair.
    """
    (workdir / "test.h").write_text(header)
    (workdir / "defs.pxd").write_text(pxd)
    (workdir / f"{stem}.pyx").write_text(pyx)

    c_path = workdir / f"{stem}.c"
    result = subprocess.run(
        [sys.executable, "-m", "cython", "-3", f"{stem}.pyx", "-o", str(c_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"cython failed:\n{result.stdout}\n{result.stderr}"
    return c_path.read_text()


def _compile_c(workdir: Path, compiler: str, stem: str = "mod") -> subprocess.CompletedProcess[str]:
    """Compile a generated C file to an object file, without linking."""
    return subprocess.run(
        [
            compiler,
            "-c",
            f"{stem}.c",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{workdir}",
            "-o",
            f"{stem}.o",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# R5 -- tag-less typedef'd enums
# ---------------------------------------------------------------------------


@libclang
class TestR5TaglessTypedefEnum:
    """``typedef enum { ... } Name;`` declares no ``enum Name`` tag.

    The Cython text of this fix is pinned in ``test_integration/test_roundtrip_cython.py``.
    What is pinned here is the consequence that motivated it -- the generated C --
    plus the C++ gate and the cffi writer's half of the same fix.
    """

    def test_cplusplus_enum_output_is_unchanged(self) -> None:
        """C++ has no separate tag namespace, so the C-only fix must not touch C++.

        In C++ ``enum Plain`` and ``Plain`` name the same type, so ``cdef enum``
        is always correct and the tag-less form needs no special case. If the
        discriminator were applied unconditionally, the first case below would
        become ``ctypedef enum``.
        """
        cpp_args = ["-x", "c++"]

        assert _pxd("libclang", "typedef enum { C1, C2 } MyEnumType;", "t.hpp", cpp_args) == textwrap.dedent("""\
            cdef extern from "t.hpp":



                cdef enum MyEnumType:
                    C1
                    C2
        """)

        assert _pxd("libclang", "typedef enum LogLevel { L1, L2 } LogLevel;", "t.hpp", cpp_args) == textwrap.dedent(
            """\
            cdef extern from "t.hpp":



                cdef enum LogLevel:
                    L1
                    L2
        """
        )

        assert _pxd("libclang", "enum Plain { A };", "t.hpp", cpp_args) == textwrap.dedent("""\
            cdef extern from "t.hpp":

                cdef enum Plain:
                    A
        """)

    def test_cffi_tagless_enum_invents_no_tag(self) -> None:
        """The cffi writer must not emit ``typedef enum Switch`` for a tag-less enum."""
        header = get_backend("libclang").parse("typedef enum { OFF, ON } Switch;", "test.h")
        assert header_to_cffi(header) == textwrap.dedent("""\
            typedef enum {
                OFF = 0,
                ON = 1,
            } Switch;""")

    def test_cffi_tagged_enum_keeps_its_real_tag(self) -> None:
        """A TAGGED typedef enum really does declare ``enum Switch``; the tag must survive.

        Sibling of the tag-less case above. ``_find_typedef_enum_pairs`` previously
        selected on ``not decl.is_typedef``, which was True for every enum, so this
        case was collapsed into the tag-less form and lost its ``enum Switch`` tag.
        Pinning both sides is what makes the discriminator load-bearing.
        """
        header = get_backend("libclang").parse("typedef enum Switch { OFF, ON } Switch;", "test.h")
        assert header_to_cffi(header) == textwrap.dedent("""\
            enum Switch {
                OFF = 0,
                ON = 1,
            };
            typedef enum Switch Switch;""")

    @pytest.mark.timeout(300)
    def test_generated_c_completes_the_enum_type(self, tmp_path: Path) -> None:
        """The C-level consequence: ``cdef enum`` yields an incomplete type in the C.

        The negative control is the same pipeline with the single keyword reverted
        to its pre-fix spelling. It must fail to compile -- that is what proves the
        positive case is checking something.
        """
        compiler = _require_c_toolchain()
        source = "typedef enum { C1, C2, C3 } MyEnumType;\n"
        pxd = _pxd("libclang", source)
        assert "ctypedef enum MyEnumType:" in pxd

        pyx = textwrap.dedent("""\
            from defs cimport MyEnumType, C1

            cpdef public int use_it():
                cdef MyEnumType v = C1
                return <int>v
        """)

        good = tmp_path / "good"
        good.mkdir()
        _cythonize(good, source, pxd, pyx)
        result = _compile_c(good, compiler)
        assert result.returncode == 0, f"post-fix output failed to compile:\n{result.stderr}"

        # Negative control: revert the one keyword the fix changed.
        bad = tmp_path / "bad"
        bad.mkdir()
        _cythonize(bad, source, pxd.replace("ctypedef enum", "cdef enum"), pyx)
        reverted = _compile_c(bad, compiler)
        assert reverted.returncode != 0, "pre-fix output compiled cleanly; the compile check proves nothing"
        assert "incomplete type" in reverted.stderr


# ---------------------------------------------------------------------------
# R6 -- keyword-escape cnames on forward declarations
# ---------------------------------------------------------------------------


@libclang
class TestR6KeywordEscapeOnForwardDeclarations:
    """A C tag that collides with a Cython keyword needs its cname everywhere.

    ``_escape_name`` renames ``class`` to ``class_``; the ``"class"`` cname is what
    tells Cython the real C spelling. Omitting it on a forward declaration makes
    Cython emit ``struct class_``, a type no header declares.
    """

    def test_undeclared_record_forward_declarations_carry_cnames(self) -> None:
        """Site 2: a record used through a field but never defined.

        Pre-fix this emitted a bare ``cdef struct class``, which is a Cython
        *syntax* error -- ``class`` is a reserved word.
        """
        source = "struct node { struct class *c; union global *g; };"
        assert _pxd("libclang", source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct class_ "class"
                cdef union global_ "global"

                cdef struct node:
                    class_* c
                    global_* g
        """)

    def test_cycle_forward_declarations_carry_cnames(self) -> None:
        """Site 1: the dependency-cycle forward-declaration path.

        A struct-pointer cycle is not enough to reach this path -- only non-pointer
        use creates a struct->struct edge. The ``class_t`` typedef edge is
        unconditional, so it is what forces the cycle here.
        """
        source = textwrap.dedent("""\
            typedef struct class class_t;
            struct class { class_t *self; int x; };
            struct node { struct class *c; };
        """)
        assert _pxd("libclang", source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct node
                cdef struct class_ "class"

                ctypedef class_ class_t


                cdef struct class_ "class"


                cdef struct node:
                    class_* c

                cdef struct class_ "class":
                    class_t* self
                    int x
        """)

    def test_keyword_typedef_enum_emits_no_self_referential_alias(self) -> None:
        """The circular-typedef guard must compare bare names, not cname-annotated ones.

        ``_escape_name(..., include_c_name=True)`` yields ``with_ "with"``, which never
        equals what ``_format_type`` produces, so the guard missed the self-reference
        and emitted a bogus ``ctypedef with_ with_ "with"`` line.
        """
        result = _pxd("libclang", "typedef enum { ZERO, ONE } with;")
        assert result == textwrap.dedent("""\
            cdef extern from "test.h":



                ctypedef enum with_ "with":
                    ZERO
                    ONE
        """)

    @pytest.mark.timeout(300)
    def test_generated_c_names_the_real_tag(self, tmp_path: Path) -> None:
        """The C-level consequence: the generated C must say ``struct class``.

        The pre-fix pxd still compiles -- ``struct class_`` is merely an incomplete
        type, and C permits pointers to those -- so a compile check alone would pass
        and prove nothing. The discriminating artifact is the generated C text, which
        must name the tag the header actually declares.
        """
        _require_c_toolchain()
        # The records are deliberately never defined: only a record that is used
        # but not defined reaches the forward-declaration path this fix repaired.
        # Adding definitions makes the definitions carry the cnames instead, and
        # the test stops discriminating.
        source = "struct node { struct class *c; union global *g; };\n"
        pxd = _pxd("libclang", source)
        assert 'cdef struct class_ "class"' in pxd
        pyx = textwrap.dedent("""\
            from defs cimport node, class_, global_

            cpdef public int use_it(long addr):
                cdef node *n = <node*>addr
                cdef class_ *c = n.c
                cdef global_ *g = n.g
                return <int>(c != NULL) + <int>(g != NULL)
        """)

        good = tmp_path / "good"
        good.mkdir()
        generated = _cythonize(good, source, pxd, pyx)
        assert "struct class_" not in generated
        assert "union global_" not in generated
        assert "struct class " in generated
        assert "union global " in generated

        # Negative control: strip the cnames the fix added. The generated C then
        # names types that do not exist in the header.
        bad = tmp_path / "bad"
        bad.mkdir()
        reverted = _cythonize(bad, source, pxd.replace(' "class"', "").replace(' "global"', ""), pyx)
        assert "struct class_" in reverted
        assert "struct class " not in reverted


# ---------------------------------------------------------------------------
# R7 -- allowlisted symbols from included headers
# ---------------------------------------------------------------------------


@libclang
class TestR7IncludeAllowlist:
    """``parse(allowlist=...)`` keeps declarations that arrive through ``#include``."""

    @staticmethod
    def _fixture(tmp_path: Path) -> tuple[Path, str]:
        """Create a main header whose entire content is an include, and return it."""
        (tmp_path / "other.h").write_text("struct Widget { int w; };\nint widget_size(struct Widget *x);\n")
        main = tmp_path / "main.h"
        main.write_text('#include "other.h"\n')
        return main, main.read_text()

    @staticmethod
    def _expected(main: Path) -> str:
        return textwrap.dedent(f"""\
            cdef extern from "{main}":

                cdef struct Widget:
                    int w

                int widget_size(Widget* x)
        """)

    @staticmethod
    def _empty(main: Path) -> str:
        return f'cdef extern from "{main}":\n    pass\n'

    def test_recursion_merges_includes_and_an_allowlist_narrows_that(self, tmp_path: Path) -> None:
        """An allowlist narrows the merged result even with traversal on.

        ``recursive_includes`` defaults to True and merges every non-system
        included header.  The allowlist filters that merge, not only the main
        translation unit, so naming ``other.h`` keeps it, naming something that
        is not included keeps nothing, and naming nothing at all keeps
        everything.

        Asserting all three spellings together is what keeps this honest: a
        filter that admitted everything, and a filter that admitted nothing,
        each break exactly one of the three.
        """
        main, source = self._fixture(tmp_path)
        parse = get_backend("libclang").parse

        assert write_pxd(parse(source, str(main))) == self._expected(main)
        assert write_pxd(parse(source, str(main), allowlist=["other.h"])) == self._expected(main)
        assert write_pxd(parse(source, str(main), allowlist=["no_such_file.h"])) == self._empty(main)

    @pytest.mark.parametrize("style", ["basename", "absolute", "dotdot", "symlink"])
    def test_allowlist_entry_resolution(self, tmp_path: Path, style: str) -> None:
        """An allowlist entry is resolved to an absolute, symlink-free path before matching.

        A bare basename resolves against the parsed file's own directory, which is
        the common case; ``..`` segments and symlinks must normalize away rather
        than defeat the match.
        """
        main, source = self._fixture(tmp_path)
        entry = {
            "basename": "other.h",
            "absolute": str(tmp_path / "other.h"),
            "dotdot": str(tmp_path / "sub" / ".." / "other.h"),
        }.get(style)
        if entry is None:
            link = tmp_path / "linked"
            os.symlink(tmp_path, link)
            entry = str(link / "other.h")

        # recursive_includes=False, so traversal cannot supply other.h behind a
        # broken resolution and make this pass for the wrong reason.
        result = write_pxd(
            get_backend("libclang").parse(source, str(main), recursive_includes=False, allowlist=[entry])
        )
        assert result == self._expected(main)

    def test_allowlist_match_is_not_a_substring_test(self, tmp_path: Path) -> None:
        """``er.h`` must not match ``other.h``.

        The pre-fix filter compared unnormalized substrings, so any suffix of a real
        path silently allowed it. Matching is a whole-path comparison.

        Recursion is off so that neither direction can be satisfied by traversal.
        Both directions are asserted in one test so that neither a filter that
        matches nothing nor one that matches everything can satisfy it.
        """
        main, source = self._fixture(tmp_path)
        parse = get_backend("libclang").parse

        no_match = write_pxd(parse(source, str(main), recursive_includes=False, allowlist=["er.h"]))
        assert no_match == self._empty(main)

        exact = write_pxd(parse(source, str(main), recursive_includes=False, allowlist=["other.h"]))
        assert exact == self._expected(main)

    @pytest.mark.parametrize(
        ("recursive", "allowlist", "keeps_included"),
        [
            # An allowlist narrows in both traversal modes.  Before this was
            # fixed, the three recursive rows all kept the include.
            (True, None, True),
            (True, ["other.h"], True),
            (True, ["no_such_file.h"], False),
            (False, None, False),
            (False, ["other.h"], True),
            (False, ["no_such_file.h"], False),
        ],
    )
    def test_recursive_includes_x_allowlist_matrix(
        self, tmp_path: Path, recursive: bool, allowlist: list[str] | None, keeps_included: bool
    ) -> None:
        """Pin the full ``recursive_includes`` x ``allowlist`` matrix.

        The two arguments answer different questions -- whether to descend into an
        included header, and which files may contribute declarations -- so every
        cell has to be stated; no cell follows from another.  The ``None`` rows
        are what distinguish "the allowlist narrows" from "traversal is off".
        """
        main, source = self._fixture(tmp_path)
        result = write_pxd(
            get_backend("libclang").parse(source, str(main), recursive_includes=recursive, allowlist=allowlist)
        )
        assert result == (self._expected(main) if keeps_included else self._empty(main))

    def test_deny_wins_over_allow_when_both_name_the_same_file(self, tmp_path: Path) -> None:
        """A file named by both lists is excluded.

        Stated as its own test because the precedence is the part of this API most
        likely to be inverted by a later change, and because neither single-list
        test can observe it.  The allow-only spelling is asserted alongside so the
        test cannot pass by a denylist that is simply ignored, nor by an allowlist
        that never admits anything.
        """
        main, source = self._fixture(tmp_path)
        parse = get_backend("libclang").parse

        assert write_pxd(parse(source, str(main), allowlist=["other.h"])) == self._expected(main)
        assert write_pxd(parse(source, str(main), allowlist=["other.h"], denylist=["other.h"])) == self._empty(main)

    def test_denylist_without_an_allowlist_means_everything_except_these(self, tmp_path: Path) -> None:
        """A denylist alone subtracts from the full merged result.

        The unfiltered spelling is asserted first: without it, a denylist that
        excluded everything would look identical to one that excluded the right
        file.
        """
        main, source = self._fixture(tmp_path)
        parse = get_backend("libclang").parse

        assert write_pxd(parse(source, str(main))) == self._expected(main)
        assert write_pxd(parse(source, str(main), denylist=["other.h"])) == self._empty(main)
        assert write_pxd(parse(source, str(main), denylist=["no_such_file.h"])) == self._expected(main)

    def test_the_main_file_cannot_be_denied(self, tmp_path: Path) -> None:
        """Denying the parsed file itself does not empty the result.

        A denylist governs included files.  Honoring it for the main file would
        return an empty header for the file the caller explicitly asked to parse,
        with nothing in the result to say why -- a silent failure indistinguishable
        from a successful parse of an empty header.  The included header in the
        same denylist *is* excluded, which proves the list is being read at all.
        """
        (tmp_path / "other.h").write_text("int widget_size(int x);\n")
        main = tmp_path / "main.h"
        main.write_text('#include "other.h"\nint main_fn(void);\n')

        result = write_pxd(get_backend("libclang").parse(main.read_text(), str(main), denylist=[str(main), "other.h"]))
        assert result == textwrap.dedent(f"""\
            cdef extern from "{main}":

                int main_fn()
        """)

    @pytest.mark.parametrize("key", ["allowlist", "denylist"])
    def test_glob_entries_match_by_resolved_path(self, tmp_path: Path, key: str) -> None:
        """A ``*`` entry is an fnmatch pattern over resolved paths, on both lists.

        The non-matching pattern is asserted in the same test so a glob
        implementation that matched everything -- or one that silently matched
        nothing -- fails one of the two halves.  ``o*.h`` and ``z*.h`` differ only
        in whether they can name ``other.h``.
        """
        main, source = self._fixture(tmp_path)
        parse = get_backend("libclang").parse
        on_match, on_miss = (self._expected(main), self._empty(main))
        if key == "denylist":
            on_match, on_miss = on_miss, on_match

        assert write_pxd(parse(source, str(main), **{key: ["o*.h"]})) == on_match
        assert write_pxd(parse(source, str(main), **{key: ["z*.h"]})) == on_miss

    def test_allowlisted_record_referenced_before_definition_is_forward_declared(self, tmp_path: Path) -> None:
        """A record used above its own definition still needs a forward declaration."""
        (tmp_path / "other.h").write_text("int widget_size(struct Widget *x);\nstruct Widget { int w; };\n")
        main = tmp_path / "main.h"
        main.write_text('#include "other.h"\n')

        result = write_pxd(
            get_backend("libclang").parse(main.read_text(), str(main), recursive_includes=False, allowlist=["other.h"])
        )
        assert result == textwrap.dedent(f"""\
            cdef extern from "{main}":

                cdef struct Widget

                int widget_size(Widget* x)

                cdef struct Widget:
                    int w
        """)


# ---------------------------------------------------------------------------
# R10 -- dependent member aliases in free-function signatures
# ---------------------------------------------------------------------------


_DEPENDENT_HEADER = textwrap.dedent("""\
    #pragma once
    namespace types {
    template <typename T> struct remove_reference { typedef T type; };
    template <typename T> struct remove_reference<T&> { typedef T type; };
    template <typename T> struct add_pointer { using type = T*; };
    }

    inline types::remove_reference<int>::type dep_result(int x) { return x * 3; }
    inline int dep_param(types::remove_reference<int>::type x) { return x + 7; }
    inline types::add_pointer<int>::type using_result(int* p) { return p; }
""")


@libclang
class TestR10DependentMemberAliases:
    """``Owner<int>::type`` in a free function's signature must not decay to ``type``.

    libclang reports the declaration cursor of a nested alias with its *bare*
    member spelling, so reading ``decl.spelling`` yields ``"type"`` -- an
    identifier that names nothing at the output's top level.  Class-template
    *methods* were fixed earlier and escape this because they arrive as a
    different ``TypeKind`` carrying the full spelling; free functions did not.

    For a concrete instantiation the canonical type is exact, so it is emitted
    instead: ``types::remove_reference<int>::type`` is ``int``.
    """

    @staticmethod
    def _parse(tmp_path: Path) -> str:
        header = tmp_path / "dep.hpp"
        header.write_text(_DEPENDENT_HEADER)
        result = write_pxd(get_backend("libclang").parse(_DEPENDENT_HEADER, str(header), extra_args=["-std=c++17"]))
        return result.replace(str(header), "dep.hpp")

    def test_dependent_return_and_parameter_types_resolve(self, tmp_path: Path) -> None:
        """Both signature positions lose the owner, so both are pinned.

        The bare name ``type`` is what the defect emitted in each of the three
        declarations below.
        """
        pxd = self._parse(tmp_path)
        assert "    int dep_result(int x)" in pxd
        assert "    int dep_param(int x)" in pxd
        assert "    int* using_result(int* p)" in pxd
        assert " type " not in pxd.split("namespace")[0]

    def test_using_alias_is_captured_like_a_typedef(self, tmp_path: Path) -> None:
        """C++11 ``using type = T*;`` is a ``TYPE_ALIAS_DECL``, not a ``TYPEDEF_DECL``.

        Matching only the latter silently dropped every ``using`` alias from
        ``inner_typedefs``, which is why ``add_pointer`` emitted an empty body.
        """
        header = tmp_path / "dep.hpp"
        header.write_text(_DEPENDENT_HEADER)
        parsed = get_backend("libclang").parse(_DEPENDENT_HEADER, str(header), extra_args=["-std=c++17"])
        by_name = {d.name: d for d in parsed.declarations}
        assert by_name["add_pointer"].inner_typedefs == {"type": "T *"}
        assert by_name["remove_reference"].inner_typedefs == {"type": "T"}

    def test_generated_declarations_compile_link_and_execute(self, tmp_path: Path) -> None:
        """The end-to-end proof, because a cythonize-only check gives false passes here.

        Cython elides an unused ``cdef`` variable, so a translation unit that never
        calls the declarations can compile while the declarations themselves are
        unusable.  This one links a real extension module and calls all three
        functions, asserting the values C++ actually computed.
        """
        pytest.importorskip("Cython", reason="Cython is required to verify generated C++")
        compiler = shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            pytest.skip("no C++ compiler (clang++/g++) on PATH")

        (tmp_path / "dep.hpp").write_text(_DEPENDENT_HEADER)
        (tmp_path / "defs.pxd").write_text(self._parse(tmp_path))
        (tmp_path / "mod.pyx").write_text(
            textwrap.dedent("""\
            # distutils: language = c++
            from defs cimport dep_result, dep_param, using_result

            def call_all():
                cdef int cell = 41
                cdef int r = dep_result(5)
                cdef int p = dep_param(10)
                cdef int* q = using_result(&cell)
                return (r, p, q[0])
        """)
        )

        cython = subprocess.run(
            [sys.executable, "-m", "cython", "-3", "--cplus", "mod.pyx", "-o", "mod.cpp"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert cython.returncode == 0, f"cython failed:\n{cython.stdout}\n{cython.stderr}"

        build = subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-shared",
                "-undefined",
                "dynamic_lookup",
                f"-I{sysconfig.get_paths()['include']}",
                f"-I{tmp_path}",
                "mod.cpp",
                "-o",
                "mod.so",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, f"compile/link failed:\n{build.stdout}\n{build.stderr}"

        run = subprocess.run(
            [sys.executable, "-c", "import mod; print(mod.call_all())"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, f"import/call failed:\n{run.stdout}\n{run.stderr}"
        assert run.stdout.strip() == "(15, 17, 41)"


# ---------------------------------------------------------------------------
# R9 -- tree-sitter const/volatile qualifiers
# ---------------------------------------------------------------------------


@treesitter
class TestR9TreeSitterQualifiers:
    """Leading ``const``/``volatile`` were dropped across many declaration positions.

    The C grammar attaches a declaration's leading qualifiers as siblings of the
    ``type`` field, so reading that field alone silently loses them.
    """

    @pytest.mark.parametrize(
        ("position", "source", "expected_body"),
        [
            ("struct_field", "struct S { const char *name; };", "    cdef struct S:\n        const char* name"),
            (
                "union_field",
                "union U { const char *s; int i; };",
                "    cdef union U:\n        const char* s\n        int i",
            ),
            ("parameter", "void f(const char *s);", "    void f(const char* s)"),
            ("return_type", "const char *g(void);", "    const char* g()"),
            ("typedef", "typedef const char *cstr;", "    ctypedef const char* cstr"),
            ("global", "extern const char *gp;", "    const char* gp"),
            ("array_element", "extern const char *tbl[4];", "    const char* tbl[4]"),
            ("pointer_level_const", "void h(char * const p);", "    void h(char* const p)"),
        ],
    )
    def test_const_survives_in_every_position(self, position: str, source: str, expected_body: str) -> None:
        assert _pxd("tree-sitter", source) == f'cdef extern from "test.h":\n\n{expected_body}\n'

    def test_trailing_cpp_qualifier_is_not_folded_into_the_return_type(self) -> None:
        """A trailing ``const`` qualifies the method, not its return type.

        This is the way the qualifier fix is most likely to break silently: folding
        every ``type_qualifier`` sibling would turn ``int f() const`` into
        ``const int f()``, which is a different signature. The qualifier fold is
        therefore restricted to nodes positioned *before* the type node.
        """
        source = "class C { public: int f() const; const char* g() const; };"
        assert _pxd("tree-sitter", source, "t.hpp", ["-x", "c++"]) == textwrap.dedent("""\
            cdef extern from "t.hpp":

                cdef cppclass C:
                    int f() const
                    const char* g() const
        """)

    def test_only_const_and_volatile_are_folded(self) -> None:
        """``_Atomic`` is a ``type_qualifier`` node too, and must not reach the output.

        The C grammar makes ``_Atomic`` a ``type_qualifier`` sibling exactly like
        ``const``, so an unfiltered fold produces ``CType("_Atomic int")`` -- a type
        name Cython cannot use.

        Two independent layers prevent that: the backend's
        ``_FOLDABLE_TYPE_QUALIFIERS`` allowlist, and the writer's
        ``UNSUPPORTED_TYPE_QUALIFIERS`` strip list. Either alone is sufficient, which
        is why this assertion only goes red when *both* are defeated. That is
        deliberate defense in depth, and the reason this test asserts the observable
        output rather than the internals of either layer.
        """
        assert _pxd("tree-sitter", "extern _Atomic int at;") == textwrap.dedent("""\
            cdef extern from "test.h":

                int at
        """)


# ---------------------------------------------------------------------------
# Cross-backend parity
# ---------------------------------------------------------------------------


def _xfail(reason: str) -> pytest.MarkDecorator:
    """Strict xfail: a divergence that gets fixed must surface as XPASS, not stay hidden."""
    return pytest.mark.xfail(strict=True, reason=reason)


PARITY_CASES = [
    pytest.param("void f(const char *s);", id="const_ptr_param"),
    pytest.param("const char *g(void);", id="const_return"),
    pytest.param("struct S { const char *name; int n; };", id="const_struct_field"),
    pytest.param("union U { const char *s; int i; };", id="const_union_field"),
    pytest.param("typedef const char *cstr;", id="const_typedef"),
    pytest.param("extern const char *gp;", id="const_global"),
    pytest.param("extern const char *tbl[4];", id="const_array"),
    pytest.param("extern const int m[2][3];", id="const_array_2d"),
    pytest.param("void f(const int x);", id="const_value_param"),
    pytest.param("void h(char * const p);", id="pointer_level_const"),
    pytest.param("void h(const char * const p);", id="const_ptr_to_const"),
    pytest.param("void f(const void *p);", id="const_void_ptr"),
    pytest.param("extern const double *dp;", id="const_double_ptr"),
    pytest.param("extern volatile int vg;", id="volatile_global"),
    pytest.param("void r(char * __restrict p);", id="restrict_param"),
    pytest.param("void r(char * __restrict__ p);", id="restrict2_param"),
    pytest.param("struct S { char * __restrict__ p; };", id="restrict2_field"),
    pytest.param("extern _Atomic int at;", id="atomic_global"),
    pytest.param("void pp(char **x);", id="pointer_to_pointer"),
    pytest.param("extern int grid[3][4];", id="array_2d"),
    pytest.param("extern char *argv[8];", id="array_of_pointer"),
    pytest.param("enum Color { RED, GREEN };", id="enum_plain"),
    pytest.param("enum E { A = 5, B = 10 };", id="enum_explicit_values"),
    pytest.param("struct S { int (*cb)(int a); };", id="funcptr_field"),
    pytest.param("typedef void (*handler)(void);", id="funcptr_typedef_no_params"),
    pytest.param("struct P { int x; int y; };", id="struct_simple"),
    pytest.param("typedef struct P { int x; } P;", id="typedef_struct"),
    pytest.param("struct A { int x; }; struct B { struct A a; };", id="nested_struct_value"),
    pytest.param("struct A; struct B { struct A *a; };", id="struct_pointer_field"),
    pytest.param("int add(int a, int b);", id="function_two_params"),
    pytest.param("void nop(void);", id="function_void"),
    pytest.param("int p(const char *fmt, ...);", id="function_variadic"),
    pytest.param("extern unsigned long ul;", id="unsigned_long"),
    pytest.param("extern signed char sc;", id="signed_char"),
    pytest.param("extern long long ll;", id="long_long"),
    pytest.param("extern int a, b, c;", id="multiple_declarators"),
    # -- known divergences, recorded rather than hidden --------------------
    pytest.param(
        "extern volatile const int vc;",
        id="qualifier_ordering",
        marks=_xfail(
            "tree-sitter preserves source qualifier order ('volatile const int') while "
            "libclang normalizes to 'const volatile int'. Semantically identical; cosmetic only."
        ),
    ),
    pytest.param(
        "typedef enum Switch { OFF, ON } Switch;",
        id="tagged_typedef_enum",
        marks=_xfail(
            "DEFECT (tree-sitter): a TAGGED 'typedef enum Switch {...} Switch;' is marked "
            "is_typedef=True, so the writer emits 'ctypedef enum Switch' where libclang "
            "correctly emits 'cdef enum Switch'. This is the R5 defect class, still unfixed "
            "in the tree-sitter backend."
        ),
    ),
    pytest.param(
        "typedef enum { OFF, ON } Switch;",
        id="tagless_typedef_enum_layout",
        marks=_xfail(
            "Blank-line layout only: libclang routes typedef'd enums through the "
            "cycle-detection phases and emits two extra blank lines. Both spell "
            "'ctypedef enum Switch'."
        ),
    ),
    pytest.param("typedef int (*cb)(int a, char b);", id="funcptr_typedef_param_names"),
    pytest.param(
        "void reg(int (*cb)(int));",
        id="funcptr_parameter",
        marks=_xfail(
            "DEFECT (tree-sitter): a function-pointer PARAMETER collapses to its return type "
            "-- 'void reg(int)' -- losing the whole signature that libclang renders as "
            "'void reg(int (*cb)(int))'."
        ),
    ),
    pytest.param(
        "void f(const struct P *p);",
        id="undefined_struct_forward_decl",
        marks=_xfail(
            "DEFECT (tree-sitter): a record named but never defined gets no forward "
            "declaration, so the emitted 'const P* p' references an undeclared type. "
            "libclang emits 'cdef struct P'."
        ),
    ),
    pytest.param(
        "extern short int si;",
        id="short_int_spelling",
        marks=_xfail(
            "libclang canonicalizes 'short int' to 'short'; tree-sitter preserves the source "
            "spelling. Both are valid Cython; cosmetic only."
        ),
    ),
    pytest.param("struct F { unsigned a : 3; unsigned b : 5; };", id="bitfield"),
    pytest.param("struct M { int a; unsigned lo : 4; char c; };", id="bitfield_mixed"),
    pytest.param("struct T { int x; struct { unsigned lo : 4; unsigned hi : 4; }; };", id="bitfield_anon_member"),
    # An unnamed bitfield is pure padding with no name to bind, so neither
    # backend may emit a field for it -- and a zero-width one, which only forces
    # the next field to a fresh storage unit, must not become a field either.
    pytest.param("struct P { unsigned a : 3; unsigned : 0; unsigned b : 5; };", id="bitfield_zero_width_reset"),
    pytest.param("struct Q { unsigned a : 3; unsigned : 3; unsigned b : 5; };", id="bitfield_unnamed_padding"),
    pytest.param("struct R { unsigned : 0; unsigned : 3; };", id="bitfield_only_unnamed"),
    # -- redundant re-declarations must collapse to exactly one declaration ----
    pytest.param("typedef int T; typedef int T;", id="duplicate_typedef_collapse"),
    pytest.param("struct S;\nstruct S { int a; };", id="struct_forward_then_definition"),
    pytest.param("union U;\nunion U { int i; float f; };", id="union_forward_then_definition"),
    # -- a tag declared but never defined is the only mention of that type ----
    pytest.param("enum E;", id="lone_opaque_enum"),
    pytest.param("enum E;\nvoid use(enum E *p);", id="lone_opaque_enum_used"),
    pytest.param("enum E;\nenum E { A, B };", id="enum_forward_then_definition"),
    pytest.param("enum E { A, B };\nenum E;", id="enum_definition_then_forward"),
    # -- member and anonymous aggregates -------------------------------------
    pytest.param("struct S { enum E { A }; };", id="struct_member_enum_hoisting"),
    pytest.param("struct s { int tag; union { int a; float b; }; };", id="anonymous_union_member"),
    pytest.param("struct s { struct { int x; int y; } p; };", id="anonymous_struct_member"),
    pytest.param("extern unsigned u;", id="extern_unsigned"),
]


# These rows pass ``-std=c++17`` explicitly. A bare ``.hpp`` now also selects C++
# in the libclang backend, which is what clang's own driver does; the flag keeps
# the rows independent of that inference and exercises the supported entry point.
CPP_PARITY_CASES = [
    pytest.param("class C { public: enum E { A, B }; };", id="class_member_enum_hoisting"),
    pytest.param("struct S { enum E { A }; };", id="cpp_struct_member_enum_hoisting"),
    # C++ requires a fixed underlying type before an enum tag may be forward-declared.
    pytest.param("enum E : int;", id="cpp_lone_opaque_enum"),
]


@pytest.mark.skipif(
    not (is_backend_available("libclang") and is_backend_available("tree-sitter")),
    reason="cross-backend parity needs both the libclang and tree-sitter backends",
)
@pytest.mark.parametrize("source", CPP_PARITY_CASES)
def test_backends_agree_on_cpp_cython_output(source: str) -> None:
    """C++ sources must reach identical Cython output from both backends."""
    args = ["-std=c++17"]
    assert _pxd("libclang", source, "test.hpp", args) == _pxd("tree-sitter", source, "test.hpp", args)


@pytest.mark.skipif(
    not (is_backend_available("libclang") and is_backend_available("tree-sitter")),
    reason="cross-backend parity needs both the libclang and tree-sitter backends",
)
@pytest.mark.parametrize("source", PARITY_CASES)
def test_backends_agree_on_cython_output(source: str) -> None:
    """Both backends feed the same Cython writer, so their output must match.

    Divergence between the two is precisely how each backend's qualifier and enum
    defects were originally identified, which makes this the most sensitive check
    in the module: it needs no expected value to be maintained by hand, and any new
    asymmetry introduced on either side surfaces here.
    """
    assert _pxd("libclang", source) == _pxd("tree-sitter", source)


# ---------------------------------------------------------------------------
# R11 -- libclang must carry bitfield widths into the IR
# ---------------------------------------------------------------------------


@libclang
class TestR11BitfieldWidthEndToEnd:
    """A width dropped by the backend silently changes what generated bindings do.

    The Cython path cannot show this: ``cdef extern`` re-uses the real C header,
    so the width never reaches the compiler and a compile check would pass either
    way. The ctypes path is where the loss becomes executable behaviour -- the
    writer emits ``("name", type)`` instead of ``("name", type, width)`` -- so the
    proof below builds the generated bindings and runs them against layout ground
    truth taken from a real C compiler.
    """

    SOURCE = "struct bits { unsigned int lo : 4; unsigned int hi : 4; int plain; };"

    @staticmethod
    def _ctypes_namespace(source: str) -> dict[str, object]:
        """Generate ctypes bindings with the libclang backend and execute them."""
        unit = get_backend("libclang").parse(source, "test.h")
        code = get_writer("ctypes").write(unit)
        namespace: dict[str, object] = {}
        exec(compile(code, "generated_ctypes.py", "exec"), namespace)  # noqa: S102
        return namespace

    def test_generated_ctypes_struct_packs_bitfields(self) -> None:
        """Reading back an over-wide value proves the field is bound at width 4.

        ``lo = 0x15`` is 21. A 4-bit field keeps only the low nibble and yields 5;
        a full ``c_uint`` -- what the missing width produced -- yields 21. The
        neighbouring ``hi`` is checked in the same instance so that a width applied
        to the wrong member cannot pass.
        """
        namespace = self._ctypes_namespace(self.SOURCE)
        bits = namespace["bits"]

        instance = bits()  # type: ignore[operator]
        instance.lo = 0x15
        instance.hi = 9
        instance.plain = -77

        assert (instance.lo, instance.hi, instance.plain) == (5, 9, -77)

    def test_generated_ctypes_layout_matches_the_c_compiler(self) -> None:
        """``sizeof`` distinguishes packed bitfields from separate ``unsigned int`` members.

        Without the widths the struct is three 4-byte members (12 bytes); with them
        ``lo`` and ``hi`` share one storage unit (8 bytes). The expected value is
        not hard-coded -- it is measured by compiling and running the equivalent C.
        """
        compiler = _require_c_toolchain()
        namespace = self._ctypes_namespace(self.SOURCE)
        generated_size = ctypes.sizeof(namespace["bits"])  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "probe.c").write_text(
                textwrap.dedent(f"""\
                #include <stdio.h>
                {self.SOURCE}
                int main(void) {{ printf("%zu", sizeof(struct bits)); return 0; }}
                """)
            )
            build = subprocess.run(
                [compiler, "probe.c", "-o", "probe"],
                cwd=workdir,
                capture_output=True,
                text=True,
                check=False,
            )
            assert build.returncode == 0, f"probe failed to build:\n{build.stderr}"
            run = subprocess.run([str(workdir / "probe")], capture_output=True, text=True, check=False)
            assert run.returncode == 0, f"probe failed to run:\n{run.stderr}"
            c_size = int(run.stdout.strip())

        assert generated_size == c_size

    def test_padding_bitfield_reaches_the_ctypes_writer_as_reserved_bits(self) -> None:
        """Padding must survive into ctypes, because ctypes rebuilds the layout itself.

        Dropping it was not a cosmetic simplification: without the ``: 0`` the
        writer placed ``b`` in the first storage unit, so the generated class had
        a different ``sizeof`` and a different bit position than C, and nothing
        raised. The reserved bits appear under a generated ``_pad`` name -- never
        as a nameless entry, which ctypes does reject.
        """
        namespace = self._ctypes_namespace("struct z { unsigned a : 3; unsigned : 0; unsigned b : 5; };")
        z = namespace["z"]

        names = [entry[0] for entry in z._fields_]  # type: ignore[union-attr]
        assert names == ["a", "_pad0", "b"]
        assert ctypes.sizeof(z) == 2 * ctypes.sizeof(ctypes.c_uint)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R12 -- libclang must keep an enum tag that is never defined
# ---------------------------------------------------------------------------


@libclang
class TestR12LoneOpaqueEnum:
    """A forward-declared enum that is never defined is the only mention of that type.

    libclang returned early on every non-definition enum cursor, so an
    ``enum E;`` with no matching definition vanished. The type did not merely
    lose its values -- it lost its declaration, and any function using
    ``enum E *`` rendered as ``E* p`` against an undeclared ``E``. Cython rejects
    that outright, so the defect is a hard failure rather than a cosmetic one.

    The opaque *record* path never had this bug: ``struct S;`` already survived as
    ``cdef struct S``. Enums were the inconsistent case.
    """

    OPAQUE = "enum E;\nvoid use(enum E *p);"

    @staticmethod
    def _enums(source: str) -> list[Enum]:
        unit = get_backend("libclang").parse(source, "test.h")
        return [d for d in unit.declarations if isinstance(d, Enum)]

    def test_undefined_enum_tag_survives_into_the_ir(self) -> None:
        """The declaration reaches the IR with no values, where other writers can use it."""
        enums = self._enums(self.OPAQUE)

        assert [(e.name, e.values, e.is_typedef) for e in enums] == [("E", [], False)]

    def test_undefined_enum_tag_is_declared_in_the_pxd(self) -> None:
        """Without the declaration the emitted ``E*`` references an undeclared type."""
        output = _pxd("libclang", self.OPAQUE)

        assert "cdef enum E:" in output
        assert "void use(E* p)" in output

    def test_forward_then_definition_still_collapses_to_one_declaration(self) -> None:
        """Negative control: the pre-existing collapse must not regress.

        A definition anywhere in the translation unit makes the forward
        declaration redundant, so exactly one ``Enum`` carrying the values is
        emitted -- not a valueless declaration followed by a populated one.
        """
        enums = self._enums("enum E;\nenum E { A, B };")

        assert [(e.name, [v.name for v in e.values]) for e in enums] == [("E", ["A", "B"])]

    def test_definition_then_forward_still_collapses_to_one_declaration(self) -> None:
        """Negative control, reversed order: a trailing re-declaration adds nothing."""
        enums = self._enums("enum E { A, B };\nenum E;")

        assert [(e.name, [v.name for v in e.values]) for e in enums] == [("E", ["A", "B"])]

    def test_repeated_opaque_declaration_is_emitted_once(self) -> None:
        """Two identical forward declarations must not produce two ``cdef enum`` blocks."""
        enums = self._enums("enum E;\nenum E;")

        assert [e.name for e in enums] == ["E"]

    def test_opaque_enum_handle_compiles_links_and_executes(self) -> None:
        """The end-to-end proof, because cythonize-only gives a false pass here.

        An opaque enum is usable only through a pointer, so the probe obtains one
        from C and reads it back. The expected value is derived from the C
        definition -- ``B`` is 9 and ``use`` adds 100 -- so a binding that resolved
        to the wrong symbol, or that read the wrong storage, yields something other
        than 109 rather than merely failing to build.
        """
        compiler = _require_c_toolchain()
        header = textwrap.dedent("""\
            #ifndef T_H
            #define T_H
            enum E;
            enum E *make(void);
            int use(enum E *p);
            #endif
            """)
        pxd = _pxd("libclang", header)
        assert "cdef enum E:" in pxd, f"backend dropped the opaque tag:\n{pxd}"

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "impl.c").write_text(
                textwrap.dedent("""\
                #include "test.h"
                enum E { A = 7, B = 9 };
                static enum E storage = B;
                enum E *make(void) { return &storage; }
                int use(enum E *p) { return (int)(*p) + 100; }
                """)
            )
            pyx = textwrap.dedent("""\
                cimport defs
                def call_it():
                    cdef defs.E* p = defs.make()
                    return defs.use(p)
                """)
            _cythonize(workdir, header, pxd, pyx)

            assert _compile_c(workdir, compiler).returncode == 0
            impl = subprocess.run(
                [compiler, "-fPIC", "-c", "impl.c", f"-I{workdir}", "-o", "impl.o"],
                cwd=workdir,
                capture_output=True,
                text=True,
                check=False,
            )
            assert impl.returncode == 0, f"impl.c failed to build:\n{impl.stderr}"

            link_flags = ["-shared"] if sys.platform != "darwin" else ["-bundle", "-undefined", "dynamic_lookup"]
            link = subprocess.run(
                [compiler, *link_flags, "mod.o", "impl.o", "-o", "mod.so"],
                cwd=workdir,
                capture_output=True,
                text=True,
                check=False,
            )
            assert link.returncode == 0, f"link failed:\n{link.stderr}"

            run = subprocess.run(
                [sys.executable, "-c", "import mod; print(mod.call_it())"],
                cwd=workdir,
                env={**os.environ, "PYTHONPATH": str(workdir)},
                capture_output=True,
                text=True,
                check=False,
            )
            assert run.returncode == 0, f"import/call failed:\n{run.stdout}\n{run.stderr}"

        assert run.stdout.strip() == "109"


# ---------------------------------------------------------------------------
# R13 -- unnamed bitfield padding must reach the ctypes writer
# ---------------------------------------------------------------------------


#: Padding-bearing records, each paired with the members whose position is
#: observable. Every expected figure below is measured from a compiled C probe;
#: nothing here pins what the current writer happens to emit.
_PADDING_CORPUS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("c1_zw", "struct c1_zw { unsigned int a : 3; unsigned int : 0; unsigned int b : 5; };", ("a", "b")),
    ("c2_anon", "struct c2_anon { unsigned int a : 3; unsigned int : 3; unsigned int b : 5; };", ("a", "b")),
    ("c3_start", "struct c3_start { unsigned int : 4; unsigned int a : 3; };", ("a",)),
    ("c4_end", "struct c4_end { unsigned int a : 3; unsigned int : 4; };", ("a",)),
    (
        "c5_consec",
        "struct c5_consec { unsigned int a : 3; unsigned int : 2; unsigned int : 3; unsigned int b : 5; };",
        ("a", "b"),
    ),
    ("c6_onlypad", "struct c6_onlypad { unsigned int : 8; };", ()),
    (
        "c7_zwboundary",
        "struct c7_zwboundary { unsigned int a : 32; unsigned int : 0; unsigned int b : 5; };",
        ("a", "b"),
    ),
    ("c8_nopad", "struct c8_nopad { unsigned int a : 3; unsigned int b : 5; };", ("a", "b")),
    (
        "c9_mixed",
        "struct c9_mixed { unsigned int a : 3; unsigned int : 5; unsigned int b : 8; unsigned int c; };",
        ("a", "b", "c"),
    ),
    (
        "c10_wide",
        "struct c10_wide { unsigned int a : 1; unsigned int : 20; unsigned int b : 11; unsigned int d : 4; };",
        ("a", "b", "d"),
    ),
    (
        "c11_anonmem",
        "struct c11_anonmem { unsigned int top : 2; "
        "struct { unsigned int x : 3; unsigned int : 4; unsigned int y : 5; }; };",
        ("top", "x", "y"),
    ),
)

_PADDING_SOURCE = "\n".join(source for _, source, _ in _PADDING_CORPUS)


def _bit_extent(buffer: bytes) -> tuple[int, int]:
    """Index of the lowest and highest set bit in ``buffer``, or (-1, -1) if none."""
    bits = [i for i in range(len(buffer) * 8) if buffer[i // 8] >> (i % 8) & 1]
    return (bits[0], bits[-1]) if bits else (-1, -1)


@pytest.fixture(scope="module")
def c_layout() -> dict[str, tuple[int, int]]:
    """Ground truth measured by compiling and running the corpus in C.

    Writing a member as all-ones and scanning the byte image locates it exactly,
    which ``offsetof`` cannot do for a bitfield. Every assertion in this section
    is compared against this dict rather than against a literal, so a platform
    where ``unsigned int`` is not 32 bits still tests the real invariant.
    """
    compiler = _require_c_toolchain()
    probes = []
    for name, _, members in _PADDING_CORPUS:
        probes.append(f'    printf("{name} %zu %zu\\n", sizeof(struct {name}), _Alignof(struct {name}));')
        for member in members:
            probes.append(
                f"    {{ struct {name} v; memset(&v, 0, sizeof v); v.{member} = ~0u; "
                f'report("{name}.{member}", (unsigned char *)&v, sizeof v); }}'
            )
    program = textwrap.dedent("""\
        #include <stdio.h>
        #include <string.h>
        {source}
        static void report(const char *label, unsigned char *p, size_t n) {{
            int lo = -1, hi = -1;
            for (size_t i = 0; i < n * 8; i++)
                if (p[i / 8] >> (i % 8) & 1) {{ if (lo < 0) lo = (int)i; hi = (int)i; }}
            printf("%s %d %d\\n", label, lo, hi);
        }}
        int main(void) {{
        {probes}
            return 0;
        }}
        """).format(source=_PADDING_SOURCE, probes="\n".join(probes))

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / "probe.c").write_text(program)
        build = subprocess.run(
            [compiler, "probe.c", "-o", "probe"], cwd=workdir, capture_output=True, text=True, check=False
        )
        assert build.returncode == 0, f"layout probe failed to build:\n{build.stderr}"
        run = subprocess.run([str(workdir / "probe")], capture_output=True, text=True, check=False)
        assert run.returncode == 0, f"layout probe failed to run:\n{run.stderr}"

    measured = {}
    for line in run.stdout.splitlines():
        label, first, second = line.split()
        measured[label] = (int(first), int(second))
    expected_keys = {name for name, _, _ in _PADDING_CORPUS} | {
        f"{name}.{member}" for name, _, members in _PADDING_CORPUS for member in members
    }
    assert set(measured) == expected_keys, "probe did not report every corpus entry"
    return measured


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
@pytest.mark.parametrize(("struct_name", "source", "members"), _PADDING_CORPUS, ids=[c[0] for c in _PADDING_CORPUS])
class TestR13PaddingLayoutMatchesC:
    """Generated ctypes classes must lay out exactly as the C compiler does.

    Both backends dropped unnamed bitfield padding, so they agreed with each
    other and disagreed with C. Backend parity therefore proves nothing here and
    is deliberately not asserted -- every expectation comes from the C probe.
    """

    @staticmethod
    def _generated(backend_name: str, source: str, struct_name: str) -> type:
        if not is_backend_available(backend_name):
            pytest.skip(f"{backend_name} backend unavailable")
        unit = get_backend(backend_name).parse(source, "layout.h")
        code = get_writer("ctypes").write(unit)
        namespace: dict[str, object] = {}
        exec(compile(code, "generated_ctypes.py", "exec"), namespace)  # noqa: S102
        assert struct_name in namespace, f"writer emitted no class for {struct_name}"
        return namespace[struct_name]  # type: ignore[return-value]

    def test_sizeof_and_alignment_match_c(
        self,
        backend_name: str,
        struct_name: str,
        source: str,
        members: tuple[str, ...],
        c_layout: dict[str, tuple[int, int]],
    ) -> None:
        """A dropped padding entry shrinks the record or moves its alignment."""
        record = self._generated(backend_name, source, struct_name)

        assert (ctypes.sizeof(record), ctypes.alignment(record)) == c_layout[struct_name]

    def test_every_member_occupies_the_same_bits_as_in_c(
        self,
        backend_name: str,
        struct_name: str,
        source: str,
        members: tuple[str, ...],
        c_layout: dict[str, tuple[int, int]],
    ) -> None:
        """``sizeof`` alone would miss padding that only shifts a member's position.

        ``c2_anon`` is exactly that case: dropping its ``: 3`` leaves the record
        4 bytes either way and moves ``b`` from bit 6 to bit 3.
        """
        if not members:
            pytest.skip("record has no addressable member to locate")
        record = self._generated(backend_name, source, struct_name)

        for member in members:
            instance = record()
            setattr(instance, member, 0xFFFFFFFF)
            assert _bit_extent(bytes(instance)) == c_layout[f"{struct_name}.{member}"], (
                f"{struct_name}.{member} is not where the C compiler puts it"
            )
