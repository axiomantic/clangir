"""Regression tests for libclang -> Cython ``.pxd`` output.

Every case here pins a defect that headerkit's own suite could not see: the
existing writer tests build IR by hand, so a parser that produced wrong IR --
clang's internal ``(unnamed at file:line:col)`` spellings, dropped members,
stripped ``const`` -- rendered "correctly" from hand-built nodes while the real
pipeline emitted invalid Cython.

Two rules therefore apply to this module:

* Drive the real pipeline. Parse C text with the libclang backend and render
  with :func:`headerkit.writers.cython.write_pxd`. Never hand-build IR.
* Assert the full output with ``==``. A substring check cannot see a dropped
  declaration, a duplicated block, or a wrong ordering, which is what these
  defects were.

Where Cython and a C compiler are installed, the generated ``.pxd`` is also
cythonized and the resulting C compiled against the original header. The
``.pyx`` must *use* the declarations -- read fields, call through pointers --
because a bare ``cimport`` emits no C for them and so cannot detect a wrong C
spelling. :class:`TestCompileHarness` proves the harness can fail.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
import textwrap
from pathlib import Path
from typing import Any

import pytest

from headerkit.backends import get_backend
from headerkit.backends.libclang import _detect_cplus
from headerkit.ir import CType, Enum, Struct, Typedef, Variable
from headerkit.writers.cython import PxdWriter, _unsupported_operator_reason, write_pxd
from tests.native_build import (
    PYTHON_INCLUDE_DIR,
    describe_load_dependencies,
    extension_filename,
    link_extension_command,
)

pytestmark = pytest.mark.libclang


@pytest.fixture(scope="module")
def backend() -> Any:
    return get_backend("libclang")


def render(backend: Any, code: str) -> str:
    """Parse C source and render it as a Cython ``.pxd``."""
    return write_pxd(backend.parse(code, "test.h"))


# ---------------------------------------------------------------------------
# Compilation harness
# ---------------------------------------------------------------------------

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_HAS_CYTHON = importlib.util.find_spec("Cython") is not None

requires_toolchain = pytest.mark.skipif(
    not _HAS_CYTHON or _CC is None,
    reason=f"needs Cython (present={_HAS_CYTHON}) and a C compiler (found={_CC})",
)


class ToolchainError(Exception):
    """Raised when cythonizing or compiling the generated ``.pxd`` fails."""


def cythonize_and_compile(tmp_path: Path, header: str | None, pxd: str, pyx: str) -> str:
    """Cythonize ``pyx`` against ``pxd`` and compile the generated C.

    ``header`` is written as ``test.h`` next to the sources; passing ``None``
    omits it, which is how the negative control forces the C step to fail.

    :returns: The generated C source.
    :raises ToolchainError: If either the Cython or the C compilation step fails.
    """
    if header is not None:
        (tmp_path / "test.h").write_text(header)
    (tmp_path / "m.pxd").write_text(pxd)
    (tmp_path / "use.pyx").write_text(pyx)

    cython = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cython", "-3", "use.pyx", "-o", "use.c"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if cython.returncode != 0:
        raise ToolchainError(f"cython failed:\n{cython.stdout}\n{cython.stderr}")

    assert _CC is not None
    compile_proc = subprocess.run(  # noqa: S603
        [
            _CC,
            "-c",
            "use.c",
            "-I",
            sysconfig.get_paths()["include"],
            "-I",
            str(tmp_path),
            "-Werror=implicit-function-declaration",
            "-Werror=incompatible-pointer-types",
            "-o",
            "use.o",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if compile_proc.returncode != 0:
        raise ToolchainError(f"C compilation failed:\n{compile_proc.stdout}\n{compile_proc.stderr}")

    return (tmp_path / "use.c").read_text()


def build(backend: Any, tmp_path: Path, header: str, pyx: str) -> str:
    """Parse ``header``, render it, and compile a ``.pyx`` that consumes it."""
    return cythonize_and_compile(tmp_path, header, render(backend, header), pyx)


# ---------------------------------------------------------------------------
# R1 -- anonymous records
# ---------------------------------------------------------------------------

ANON_STRUCT_VAR = "struct { int a; int b; } my_anon_struct[10];\n"


class TestAnonymousRecordNaming:
    """clang's ``(unnamed at file:line:col)`` spelling must never reach the output.

    It was both invalid Cython and non-reproducible: it embedded a source
    position, so inserting a blank line above the declaration changed the
    generated binding.
    """

    def test_anonymous_struct_takes_its_declarator_name(self, backend: Any) -> None:
        assert render(backend, ANON_STRUCT_VAR) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _my_anon_struct_s:
                    int a
                    int b

                _my_anon_struct_s my_anon_struct[10]
        """)

    def test_anonymous_union_takes_its_declarator_name(self, backend: Any) -> None:
        assert render(backend, "union { int a; float b; } get(void);") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef union _get_u:
                    int a
                    float b

                _get_u get()
        """)

    def test_output_is_invariant_to_source_position(self, backend: Any) -> None:
        """The generated tag must not encode file:line:col.

        Shifting the declaration down the file and putting a comment above it
        changes nothing about the C interface, so it must change nothing about
        the binding.
        """
        shifted = textwrap.dedent("""\


            /* A comment that moves the declaration
               several lines down the file. */

            struct { int a; int b; } my_anon_struct[10];
        """)
        assert render(backend, shifted) == render(backend, ANON_STRUCT_VAR)

    def test_unbound_anonymous_record_uses_a_counter_slug(self, backend: Any) -> None:
        """No declarator names this record, so the fallback slug is used.

        The counter is deliberate: a source-location slug would reintroduce the
        formatting dependency that :meth:`test_output_is_invariant_to_source_position`
        forbids.
        """
        assert render(backend, "void f(struct { int a; } *);") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _anon_struct_1


                void f(_anon_struct_1*)
        """)

    @pytest.mark.parametrize(
        "source",
        [
            ANON_STRUCT_VAR,
            "union { int a; float b; } get(void);",
            "void f(struct { int a; } *);",
        ],
    )
    def test_no_clang_internal_spelling_reaches_the_output(self, backend: Any, source: str) -> None:
        """Only sources that previously leaked the spelling are listed.

        Constructs that were *dropped* rather than mis-spelled would satisfy
        this check vacuously; they are pinned by exact-output tests instead.
        """
        output = render(backend, source)
        assert "(unnamed at" not in output
        assert "(anonymous" not in output

    @requires_toolchain
    def test_anonymous_struct_binding_compiles(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct { int a; int b; } my_anon_struct[10];
        """)
        # The generated tag names a type that does not exist in C, so the .pyx
        # reaches the members through the variable rather than declaring one.
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                my_anon_struct[3].a = 1
                my_anon_struct[3].b = 2
                return my_anon_struct[3].a + my_anon_struct[3].b
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "my_anon_struct[3]" in c_source


# ---------------------------------------------------------------------------
# R2 -- function-pointer variables
# ---------------------------------------------------------------------------


class TestFunctionPointerVariables:
    """``_format_type`` yields an abstract declarator that cannot take a name.

    ``void (*)(int, char)`` followed by a variable name is not valid Cython, so
    the writer emits a named ``ctypedef`` and declares the variable through it.
    """

    def test_function_pointer_variable_gets_a_named_typedef(self, backend: Any) -> None:
        assert render(backend, "void (*my_func)(int a, char b);") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef void (*_my_func_ft)(int a, char b)

                _my_func_ft my_func
        """)

    def test_unprototyped_function_pointer_variable(self, backend: Any) -> None:
        """FUNCTIONNOPROTO has no argument list and no variadic flag."""
        assert render(backend, "const int* (*p)();") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef const int* (*_p_ft)()

                _p_ft p
        """)

    def test_parameter_names_are_recovered(self, backend: Any) -> None:
        """clang's FUNCTIONPROTO type carries no argument names.

        They come from the declaring cursor's PARM_DECL children. Losing them
        is silent -- the output stays valid Cython, it just stops documenting
        the interface.
        """
        assert render(backend, "void h(void (*cb)(int a, int b));") == textwrap.dedent("""\
            cdef extern from "test.h":

                void h(void (*cb)(int a, int b))
        """)

    @requires_toolchain
    def test_function_pointer_variable_compiles(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            void (*my_func)(int a, char b);
        """)
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                if my_func is not NULL:
                    my_func(1, <char>2)
                    return 1
                return 0
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "my_func(" in c_source

    def test_named_typedef_keeps_parameter_names_and_array_extents(self, backend: Any) -> None:
        """The struct-member path recovered both; the named-typedef path recovered neither.

        The extent loss is the severe half: a parameter declared ``int c[3][4]``
        has adjusted type ``int (*)[4]``, so rendering it as ``int`` accepts
        callers that C rejects.
        """
        source = textwrap.dedent("""\
            typedef void (*cb_t)(int a, char *b, int c[3][4]);
            struct s { void (*m)(int a, char *b, int c[3][4]); };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef void (*cb_t)(int a, char* b, int c[3][4])

                cdef struct s:
                    void (*m)(int a, char* b, int c[3][4])
        """)

    @requires_toolchain
    def test_named_typedef_callback_compiles(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            typedef void (*cb_t)(int a, char *b, int c[3][4]);
            void invoke(cb_t f);
        """)
        pyx = textwrap.dedent("""\
            from m cimport cb_t, invoke

            cdef void handler(int a, char* b, int c[3][4]) noexcept:
                pass

            cpdef public int exercise():
                cdef cb_t f = handler
                invoke(f)
                return 0
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "invoke(" in c_source


# ---------------------------------------------------------------------------
# R3 -- dropped declarations and empty bodies
# ---------------------------------------------------------------------------


class TestDroppedDeclarationsAndEmptyBodies:
    def test_named_member_of_an_anonymous_struct_type_is_not_flattened(self, backend: Any) -> None:
        """``struct { ... } css;`` has a declarator, so C11 6.7.2.1p13 does not apply.

        Flattening it dropped ``css`` entirely -- no caller could reach
        ``outer.css`` -- and hoisted ``v`` and ``g`` into ``outer``. The
        anonymous type must instead get its own bodied declaration, named for
        the enclosing record so two records may both hold a ``css``.
        """
        source = "struct outer { int a; struct { int v; int g; } css; };"
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _outer_css_s:
                    int v
                    int g

                cdef struct outer:
                    int a
                    _outer_css_s css
        """)

    def test_named_anonymous_struct_members_of_two_records_do_not_collide(self, backend: Any) -> None:
        """An unqualified ``_css_s`` would bind whichever record clang saw first."""
        source = textwrap.dedent("""\
            struct left { struct { int v; } css; };
            struct right { struct { char w; } css; };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _left_css_s:
                    int v

                cdef struct left:
                    _left_css_s css

                cdef struct _right_css_s:
                    char w

                cdef struct right:
                    _right_css_s css
        """)

    def test_bitfields_in_a_named_anonymous_struct_member_keep_the_member(self, backend: Any) -> None:
        """The ``xnvme_opts`` shape that exposed the over-flattening downstream.

        Cython has no bitfield syntax, so the width survives only as a comment
        on the member; the members must still be reachable through ``opts.css``.
        """
        source = textwrap.dedent("""\
            typedef unsigned int uint32_t;
            struct xnvme_opts {
                int nsid;
                struct { uint32_t value : 31; uint32_t given : 1; } css;
            };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef unsigned int uint32_t

                cdef struct _xnvme_opts_css_s:
                    uint32_t value  # bitfield: 31 bits
                    uint32_t given  # bitfield: 1 bit

                cdef struct xnvme_opts:
                    int nsid
                    _xnvme_opts_css_s css
        """)

    @requires_toolchain
    def test_named_anonymous_struct_member_is_reachable_from_cython(self, backend: Any, tmp_path: Path) -> None:
        """Reading through ``o.css`` is what the flattening made impossible."""
        header = "struct outer { int a; struct { int v; int g; } css; };\n"
        pyx = textwrap.dedent("""\
            from m cimport outer

            cpdef public int exercise():
                cdef outer o
                o.a = 1
                o.css.v = 2
                o.css.g = 3
                return o.a + o.css.v + o.css.g
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert ".css.v" in c_source

    def test_anonymous_member_is_flattened_into_its_parent(self, backend: Any) -> None:
        """C11 makes ``b`` and ``c`` members of ``outer_s``; they were dropped."""
        source = "struct outer_s { int a; struct { int b; int c; }; };"
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct outer_s:
                    int a
                    int b
                    int c
        """)

    def test_anonymous_enum_emits_its_values(self, backend: Any) -> None:
        """This produced an extern block with no declarations at all."""
        assert render(backend, "enum { C1, C2, C3 };") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef enum:
                    C1
                    C2
                    C3
        """)

    def test_struct_with_a_nested_enum_has_a_body(self, backend: Any) -> None:
        """A suite header with no body is a Cython syntax error.

        This emitted ``cdef struct nested_enum_struct:`` followed by nothing.
        """
        source = "struct nested_enum_struct { enum { X } e; };"
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef enum _nested_enum_struct_e_e:
                    X

                cdef struct nested_enum_struct:
                    _nested_enum_struct_e_e e
        """)

    def test_nested_anonymous_enums_of_two_records_do_not_collide(self, backend: Any) -> None:
        """An unqualified ``_x_e`` tag made the second record reuse the first's enum.

        Both members were typed ``_x_e``, which is the wrong type for the
        second, and its enumerators ``C`` and ``D`` were emitted nowhere at all.
        """
        source = textwrap.dedent("""\
            struct outer { enum { A, B } x; struct { int q; } y; };
            struct other { enum { C, D } x; };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef enum _outer_x_e:
                    A
                    B

                cdef struct _outer_y_s:
                    int q

                cdef struct outer:
                    _outer_x_e x
                    _outer_y_s y

                cdef enum _other_x_e:
                    C
                    D

                cdef struct other:
                    _other_x_e x
        """)

    @requires_toolchain
    def test_colliding_nested_anonymous_enums_compile(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct outer { enum { A, B } x; struct { int q; } y; };
            struct other { enum { C, D } x; };
        """)
        pyx = textwrap.dedent("""\
            from m cimport outer, other, A, B, C, D

            cpdef public int exercise():
                cdef outer o
                cdef other t
                o.x = A
                o.y.q = 1
                t.x = D
                return <int>o.x + <int>t.x + <int>B + <int>C
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "exercise" in c_source

    def test_struct_whose_fields_are_all_filtered_emits_pass(self, backend: Any) -> None:
        """``struct timespec`` comes from a system header, so it is forward-declared only.

        A field using it by value is therefore filtered out, leaving the record
        with no members to emit; ``pass`` keeps the suite well-formed.
        """
        source = textwrap.dedent("""\
            #include <time.h>
            struct holder_s { struct timespec ts; };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct timespec

                cdef struct holder_s:
                    pass
        """)

    @requires_toolchain
    def test_flattened_members_and_nested_enum_compile(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct outer_s { int a; struct { int b; int c; }; };
            struct nested_enum_struct { enum { X } e; };
            enum { C1, C2, C3 };
        """)
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                cdef outer_s o
                o.a = C1
                o.b = C2
                o.c = C3
                cdef nested_enum_struct n
                n.e = X
                return o.a + o.b + o.c + <int>n.e
        """)
        c_source = build(backend, tmp_path, header, pyx)
        # The flattened members must reach the C as direct member accesses.
        assert "->b" in c_source or ".b" in c_source


# ---------------------------------------------------------------------------
# R4 -- qualifiers
# ---------------------------------------------------------------------------

CONST_FIELDS = "struct s { char* const f; const char* const h; const char* const* const i; const char** const j; };"


class TestQualifiers:
    """``const`` was stripped at four distinct positions.

    Pointer-level, pointee-level, multi-level, and on record/typedef pointees
    are separate code paths in the converter; each is pinned separately.
    """

    def test_const_at_every_pointer_level(self, backend: Any) -> None:
        assert render(backend, CONST_FIELDS) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct s:
                    char* const f
                    const char* const h
                    const char* const* const i
                    const char** const j
        """)

    def test_distinct_const_placements_render_distinctly(self, backend: Any) -> None:
        """``const char* const* const`` and ``const char** const`` are different C types.

        Both previously printed as ``const char**``, collapsing the distinction.
        """
        output = render(backend, CONST_FIELDS)
        i_line = next(line.strip() for line in output.splitlines() if line.endswith(" i"))
        j_line = next(line.strip() for line in output.splitlines() if line.endswith(" j"))
        assert i_line == "const char* const* const i"
        assert j_line == "const char** const j"
        assert i_line[:-2] != j_line[:-2]

    def test_const_on_record_pointees(self, backend: Any) -> None:
        source = textwrap.dedent("""\
            struct my_struct { int x; };
            union my_union { int y; };
            void my_func_6(const struct my_struct* s, const union my_union* const u);
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct my_struct:
                    int x

                cdef union my_union:
                    int y

                void my_func_6(const my_struct* s, const my_union* const u)
        """)

    def test_const_on_typedef_pointees(self, backend: Any) -> None:
        source = textwrap.dedent("""\
            typedef struct my_struct { int x; } my_struct;
            typedef union my_union { int y; } my_union;
            void my_func_6(const my_struct* s, const my_union* const u);
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef struct my_struct:
                    int x

                ctypedef union my_union:
                    int y

                void my_func_6(const my_struct* s, const my_union* const u)
        """)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "_Atomic int at_var;",
                'cdef extern from "test.h":\n\n    int at_var\n',
            ),
            (
                "void rf(int* __restrict a);",
                'cdef extern from "test.h":\n\n    void rf(int* a)\n',
            ),
            (
                "_Noreturn void nr(void);",
                'cdef extern from "test.h":\n\n    void nr()\n',
            ),
        ],
    )
    def test_unsupported_qualifiers_stay_stripped(self, backend: Any, source: str, expected: str) -> None:
        """Cython has no ``_Atomic``/``__restrict``/``_Noreturn``.

        Recovering ``const`` must not license emitting these; they are dropped
        on purpose.
        """
        assert render(backend, source) == expected

    @requires_toolchain
    def test_const_qualified_fields_compile(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct s { char* const f; const char* const h; const char* const* const i; const char** const j; };
            extern struct s g_s;
        """)
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                cdef char* a = g_s.f
                cdef const char* b = g_s.h
                cdef const char* const* c = g_s.i
                cdef const char** d = g_s.j
                return (a is not NULL) + (b is not NULL) + (c is not NULL) + (d is not NULL)
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "g_s" in c_source


# ---------------------------------------------------------------------------
# R5 -- records defined inside another record body
# ---------------------------------------------------------------------------


NESTED_UNION = textwrap.dedent("""\
    struct my_s {
      union my_nested_u {
        char c;
        int i;
      } n;
      unsigned u;
    };
""")


class TestNestedTaggedRecords:
    """A tagged record defined inside another record body must keep its body.

    The backend dropped the nested definition entirely, so the writer saw only
    an undeclared tag and emitted a bare forward declaration. The containing
    struct then used that incomplete type *by value*, which Cython rejects with
    ``Variable type 'my_nested_u' is incomplete``.
    """

    def test_nested_union_body_is_emitted_before_the_parent(self, backend: Any) -> None:
        assert render(backend, NESTED_UNION) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef union my_nested_u:
                    char c
                    int i

                cdef struct my_s:
                    my_nested_u n
                    unsigned int u
        """)

    def test_doubly_nested_records_are_hoisted_innermost_first(self, backend: Any) -> None:
        """Each level must precede the level that uses it by value."""
        source = textwrap.dedent("""\
            typedef struct my_s {
              union my_nested_u {
                char c;
                struct my_nested_s { int i; } n;
                int i;
              } n;
              unsigned u;
            } my_t;
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct my_nested_s:
                    int i

                cdef union my_nested_u:
                    char c
                    my_nested_s n
                    int i

                cdef struct my_s:
                    my_nested_u n
                    unsigned int u

                ctypedef my_s my_t
        """)

    def test_pointer_only_nested_tag_stays_a_forward_declaration(self, backend: Any) -> None:
        """A tag introduced by a pointer member has no body to hoist.

        This is the negative control for the fix: only *definitions* are
        hoisted, so this must not gain a spurious empty body.
        """
        assert render(backend, "struct node { struct peer *p; };") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct peer

                cdef struct node:
                    peer* p
        """)

    @requires_toolchain
    def test_nested_union_compiles(self, backend: Any, tmp_path: Path) -> None:
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                cdef my_s v
                v.n.i = 7
                v.u = 3
                return v.n.i + <int>v.u
        """)
        c_source = build(backend, tmp_path, NESTED_UNION, pyx)
        assert "struct my_s" in c_source


# ---------------------------------------------------------------------------
# R6 -- pointer-to-function-pointer parameters
# ---------------------------------------------------------------------------


DOUBLE_FUNC_PTR_PARAM = "void reg(void (**pxFunc)(int, char), void **ppArg);\n"


class TestPointerToFunctionPointerParameters:
    """A named ``void (**p)(...)`` parameter must keep its name inside the declarator.

    Only single-level function-pointer parameters were routed through the
    declarator formatter. A double pointer fell through to the generic type
    formatter, which produced the abstract ``void (**)(int, char)`` and then
    appended the name *after* it -- ``void (**)(int, char) pxFunc`` -- which
    Cython rejects with ``Expected ')', found 'pxFunc'``. Reduced from
    sqlite3's ``xFindFunction``.
    """

    def test_double_pointer_parameter_name_sits_inside_the_declarator(self, backend: Any) -> None:
        assert render(backend, DOUBLE_FUNC_PTR_PARAM) == textwrap.dedent("""\
            cdef extern from "test.h":

                void reg(void (**pxFunc)(int, char), void** ppArg)
        """)

    def test_triple_pointer_parameter(self, backend: Any) -> None:
        """The star count follows the pointer depth rather than a fixed case."""
        assert render(backend, "void f(void (***p)(int));") == textwrap.dedent("""\
            cdef extern from "test.h":

                void f(void (***p)(int))
        """)

    def test_single_pointer_parameter_is_unchanged(self, backend: Any) -> None:
        assert render(backend, "void reg(void (*pxFunc)(int, char));") == textwrap.dedent("""\
            cdef extern from "test.h":

                void reg(void (*pxFunc)(int, char))
        """)

    def test_named_double_pointer_parameter_inside_a_typedef_keeps_its_name(self, backend: Any) -> None:
        """The name sits inside the ``(**inner)`` declarator, not after the type."""
        assert render(backend, "typedef int (*cb)(void (**inner)(int), int n);") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef int (*cb)(void (**inner)(int), int n)
        """)

    def test_unnamed_double_pointer_parameter_stays_abstract(self, backend: Any) -> None:
        assert render(backend, "typedef int (*cb)(void (**)(int), int);") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef int (*cb)(void (**)(int), int)
        """)

    @requires_toolchain
    def test_double_pointer_parameter_compiles(self, backend: Any, tmp_path: Path) -> None:
        pyx = textwrap.dedent("""\
            from m cimport *

            cdef void impl(int a, char b) noexcept:
                pass

            cpdef public int exercise():
                cdef void (*fp)(int, char) noexcept
                fp = impl
                cdef void **arg = NULL
                reg(&fp, arg)
                return 1
        """)
        c_source = build(backend, tmp_path, DOUBLE_FUNC_PTR_PARAM, pyx)
        assert "reg(" in c_source


# ---------------------------------------------------------------------------
# Negative controls for the harness itself
# ---------------------------------------------------------------------------


class TestCompileHarness:
    """A compile check that has never failed is a claim, not a mechanism."""

    @requires_toolchain
    def test_missing_header_fails_the_c_step(self, tmp_path: Path) -> None:
        pxd = 'cdef extern from "test.h":\n\n    int a_symbol\n'
        pyx = "from m cimport *\n\ncpdef public int exercise():\n    return a_symbol\n"
        with pytest.raises(ToolchainError) as exc:
            cythonize_and_compile(tmp_path, None, pxd, pyx)
        assert "C compilation failed" in str(exc.value)
        assert "test.h" in str(exc.value)

    @requires_toolchain
    def test_bodyless_suite_fails_the_cython_step(self, tmp_path: Path) -> None:
        """The exact shape the nested-enum struct emitted before the fix."""
        pxd = 'cdef extern from "test.h":\n\n    cdef struct broken_s:\n\n    int after\n'
        pyx = "from m cimport *\n\ncpdef public int exercise():\n    return 0\n"
        with pytest.raises(ToolchainError) as exc:
            cythonize_and_compile(tmp_path, "struct broken_s { int b; };\nint after;\n", pxd, pyx)
        assert "cython failed" in str(exc.value)
        assert "indentation" in str(exc.value).lower()

    @requires_toolchain
    def test_wrong_field_spelling_fails_the_c_step(self, tmp_path: Path) -> None:
        """Proves the C step sees through the .pxd to the real header."""
        pxd = 'cdef extern from "test.h":\n\n    cdef struct real_s:\n        int not_a_field\n'
        pyx = "from m cimport *\n\ncpdef public int exercise():\n    cdef real_s v\n    return v.not_a_field\n"
        with pytest.raises(ToolchainError) as exc:
            cythonize_and_compile(tmp_path, "struct real_s { int actual; };\n", pxd, pyx)
        assert "C compilation failed" in str(exc.value)
        # "C compilation failed" is this harness's own prefix, so alone it says only
        # that *something* failed. The two compilers word this defect differently but
        # both name the member and the struct, so the identifiers are what is checked.
        # gcc quotes them with U+2018/U+2019 or ASCII depending on locale, so the
        # quote characters themselves are deliberately left out of the match.
        assert "not_a_field" in str(exc.value)
        assert "real_s" in str(exc.value)


# ---------------------------------------------------------------------------
# R9 -- reproducible output
# ---------------------------------------------------------------------------

#: Struct tags in deliberately non-alphabetical source order. ``consume`` names
#: every one of them through a pointer before any is defined, which is the shape
#: that feeds ``_early_reference_forwards``: that helper collects the names from
#: a ``set``, so without an explicit sort the forward-declaration block inherits
#: a per-process hash order.
_EARLY_REF_TAGS = (
    "zeta",
    "alpha",
    "omega",
    "delta",
    "kappa",
    "sigma",
    "gamma",
    "theta",
    "lambda_t",
    "beta",
    "epsilon",
    "psi",
)

_EARLY_REF_SOURCE = "void consume({});\n{}".format(
    ", ".join(f"struct {tag} *p{i}" for i, tag in enumerate(_EARLY_REF_TAGS)),
    "".join(f"struct {tag} {{ int v{i}; }};\n" for i, tag in enumerate(_EARLY_REF_TAGS)),
)

_RENDER_PROBE = textwrap.dedent("""\
    import sys

    from headerkit.backends import get_backend
    from headerkit.writers.cython import write_pxd

    sys.stdout.write(write_pxd(get_backend("libclang").parse(sys.stdin.read(), "test.h")))
""")


def _forward_declared_tags(pxd: str) -> list[str]:
    """Tags from the forward-declaration block, in the order emitted.

    A forward declaration is ``cdef struct <tag>`` with no trailing colon; the
    colon is what distinguishes it from the later full definition.
    """
    tags = []
    for line in pxd.splitlines():
        stripped = line.strip()
        if stripped.startswith("cdef struct ") and not stripped.endswith(":"):
            tags.append(stripped.removeprefix("cdef struct "))
    return tags


class TestOutputIsReproducibleAcrossProcesses:
    """Generated bindings must not depend on the interpreter's hash seed.

    ``_early_reference_forwards`` builds its result by iterating the ``set``
    returned by ``_referenced_type_names``. Python randomizes string hashing per
    process, so before that helper sorted its result the same header rendered to
    a different ``.pxd`` in every process -- six seeds produced six distinct
    outputs for ``examples/headers/clap/clap.h``. That silently undermines every
    golden-file test here and downstream: the suite would pass or fail on the
    seed CI happened to draw.

    Generating twice inside one process cannot see this, because the seed is
    fixed for the life of the process. The structural check below is the fast
    guard; :meth:`test_subprocesses_with_different_hash_seeds_agree` is the
    end-to-end one.
    """

    def test_forward_declarations_are_emitted_in_sorted_order(self, backend: Any) -> None:
        tags = _forward_declared_tags(render(backend, _EARLY_REF_SOURCE))
        # Assert the block is complete first: an empty or truncated block would
        # satisfy the ordering assertion vacuously.
        assert sorted(tags) == sorted(_EARLY_REF_TAGS)
        assert tags == sorted(_EARLY_REF_TAGS)

    def test_source_order_is_preserved_for_the_definitions(self, backend: Any) -> None:
        """Only the forward block is sorted; definitions keep declaration order.

        Sorting the definitions too would be a different -- and wrong -- fix.
        """
        pxd = render(backend, _EARLY_REF_SOURCE)
        definitions = [
            line.strip().removeprefix("cdef struct ").removesuffix(":")
            for line in pxd.splitlines()
            if line.strip().startswith("cdef struct ") and line.strip().endswith(":")
        ]
        assert definitions == list(_EARLY_REF_TAGS)

    def test_subprocesses_with_different_hash_seeds_agree(self) -> None:
        seeds = ("0", "1", "2", "3", "4")
        outputs = {}
        for seed in seeds:
            proc = subprocess.run(
                [sys.executable, "-c", _RENDER_PROBE],
                input=_EARLY_REF_SOURCE,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
                check=False,
            )
            assert proc.returncode == 0, f"seed {seed} failed:\n{proc.stderr}"
            # A probe that silently emitted nothing would make every output
            # compare equal, so require real content before comparing.
            assert "cdef struct alpha" in proc.stdout, f"seed {seed} produced no bindings"
            outputs[seed] = proc.stdout

        distinct = set(outputs.values())
        assert len(distinct) == 1, f"{len(distinct)} distinct outputs across seeds {seeds}"


# ---------------------------------------------------------------------------
# R9 -- C++ dependent names
# ---------------------------------------------------------------------------

_CXX = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")

requires_cxx_toolchain = pytest.mark.skipif(
    not _HAS_CYTHON or _CXX is None,
    reason=f"needs Cython (present={_HAS_CYTHON}) and a C++ compiler (found={_CXX})",
)


def render_cpp(backend: Any, code: str) -> str:
    """Parse C++ source and render it as a Cython ``.pxd``."""
    return write_pxd(backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"]))


def build_and_run_cpp(tmp_path: Path, header: str, pxd: str, pyx: str) -> Any:
    """Cythonize, compile, link, import and call a C++ extension.

    Cythonizing alone is not enough to prove a dependent name works: Cython
    elides unused declarations, so a ``.pyx`` that merely ``cimport``s can pass
    while emitting no C++ at all. The ``.pyx`` must therefore call ``run()``,
    and this helper returns what ``run()`` actually returned.
    """
    (tmp_path / "test.hpp").write_text(header)
    (tmp_path / "m.pxd").write_text(pxd)
    (tmp_path / "use.pyx").write_text(pyx)

    cython = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cython", "-3", "--cplus", "use.pyx", "-o", "use.cpp"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if cython.returncode != 0:
        raise ToolchainError(f"cython failed:\n{cython.stdout}\n{cython.stderr}")

    assert _CXX is not None
    build = subprocess.run(  # noqa: S603
        link_extension_command(
            _CXX,
            ["use.cpp"],
            "use",
            includes=[PYTHON_INCLUDE_DIR, tmp_path],
            extra=["-std=c++17"],
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise ToolchainError(f"C++ build failed:\n{build.stdout}\n{build.stderr}")

    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import use; print(repr(use.run()))"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = describe_load_dependencies(tmp_path / extension_filename("use"))
        raise ToolchainError(f"import/execute failed:\n{probe.stdout}\n{probe.stderr}{detail}")
    return probe.stdout.strip()


def cythonize_cpp_only(tmp_path: Path, header: str, pxd: str, pyx: str) -> None:
    """Run only the Cython parse step of a C++ binding.

    Used where the fixture is deliberately not instantiable in C++, so that
    only the ``.pxd`` parse is a meaningful signal. Prefer
    :func:`build_and_run_cpp` everywhere else.
    """
    (tmp_path / "test.hpp").write_text(header)
    (tmp_path / "m.pxd").write_text(pxd)
    (tmp_path / "use.pyx").write_text(pyx)
    cython = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cython", "-3", "--cplus", "use.pyx", "-o", "use.cpp"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if cython.returncode != 0:
        raise ToolchainError(f"cython failed:\n{cython.stdout}\n{cython.stderr}")


_DEPENDENT_HEADER = textwrap.dedent("""\
    #pragma once
    namespace types {

    template <class T> struct remove_reference { typedef T type; };

    template <class T>
    struct Box {
        T v;
        Box(T x) : v(x) {}
        typename types::remove_reference<T>::type get() { return v; }
        void put(typename types::remove_reference<T>::type x) { v = x; }
        T twice(typename types::remove_reference<T>::type x) { return x + x; }
    };

    }
""")


class TestDependentNames:
    """C++ dependent names must be emitted in a spelling Cython accepts.

    ``typename types::remove_reference<T>::type`` was emitted verbatim apart
    from the template brackets, giving ``typename remove_reference[T]::type``,
    which Cython rejects with "Expected an identifier or literal". Cython has no
    ``typename`` keyword and spells nested access with ``.``, so the correct
    emission is ``remove_reference[T].type``. This is sound because an
    ``extern`` block is never re-emitted into the generated C++; Cython consults
    it only at use sites, where every template argument is already concrete.
    """

    def test_dependent_return_parameter_and_method_are_emitted(self, backend: Any) -> None:
        """All three positions render, and the qualifier declares its member.

        The whole block is asserted: a substring check could not see that
        ``remove_reference`` still lacked the nested ``type`` it is indexed for,
        nor that the namespace moved onto the ``cdef extern`` line.
        """
        assert render_cpp(backend, _DEPENDENT_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "types":

                cdef cppclass remove_reference[T]:
                    ctypedef T type

                cdef cppclass Box[T]:
                    T v
                    Box(T x)
                    remove_reference[T].type get()
                    void put(remove_reference[T].type x)
                    T twice(remove_reference[T].type x)
        """)

    def test_namespace_is_carried_on_the_block_not_the_type(self, backend: Any) -> None:
        """``types.remove_reference[T].type`` fails with "'types' is not declared".

        The qualifier must therefore lose its namespace, which has to reappear
        on the enclosing block or the declaration would name the wrong entity.
        """
        pxd = render_cpp(backend, _DEPENDENT_HEADER)
        assert 'namespace "types"' in pxd
        assert "types.remove_reference" not in pxd
        assert "types::" not in pxd

    @requires_cxx_toolchain
    def test_dependent_names_compile_link_and_execute(self, tmp_path: Path) -> None:
        """The generated binding builds and its functions return correct values.

        Executing is the point: a dependent name that merely cythonizes proves
        nothing, because Cython emits no C++ for a declaration nobody uses.
        """
        pxd = render_cpp(get_backend("libclang"), _DEPENDENT_HEADER)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Box

            def run():
                cdef Box[int] *bi = new Box[int](3)
                cdef int got = bi.get()
                bi.put(4)
                cdef int after = bi.get()
                cdef Box[double] *bd = new Box[double](1.25)
                cdef double doubled = bd.twice(1.25)
                del bi
                del bd
                return (got, after, doubled)
        """)
        assert build_and_run_cpp(tmp_path, _DEPENDENT_HEADER, pxd, pyx) == "(3, 4, 2.5)"


_EDGE_HEADER = textwrap.dedent("""\
    namespace nm {

    template <class T> struct Alloc { typedef T value_type; };

    template <class T>
    struct Vec {
        typename T::size_type count();
        typename T::widget_kind kind();
    };

    template <class T>
    struct Uses {
        typename nm::Alloc<T>::missing_member grab();
    };

    }
""")


class TestUnrepresentableDependentNames:
    """A dependent name Cython cannot express is reported, never emitted.

    Two cases are unrepresentable. A nested name on a *bare* template parameter
    (``typename T::foo``) is rejected by Cython outright -- its own
    ``Includes/libcpp`` headers say so -- and a nested name on a qualifier that
    does not declare that member would reference an undeclared symbol. Both are
    replaced by a diagnostic naming the symbol and the type; a silent drop would
    leave the binding quietly incomplete.
    """

    def test_conventional_nested_names_are_substituted_with_a_note(self, backend: Any) -> None:
        """``T::size_type`` becomes ``size_t``, and the output says so."""
        pxd = render_cpp(backend, _EDGE_HEADER)
        assert "size_t count()" in pxd
        assert "# NOTE: 'T::size_type' emitted as 'size_t'" in pxd

    def test_bare_template_parameter_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        """``T::widget_kind`` has no conventional spelling, so it is reported."""
        pxd = render_cpp(backend, _EDGE_HEADER)
        assert "# UNSUPPORTED: kind() uses dependent type 'T::widget_kind'" in pxd
        assert "template parameter 'T'" in pxd
        assert "widget_kind" not in pxd.replace("'T::widget_kind'", "")

    def test_absent_qualifier_member_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        """``Alloc`` declares no ``missing_member``, so nothing may reference it."""
        pxd = render_cpp(backend, _EDGE_HEADER)
        assert "# UNSUPPORTED: grab() uses dependent type 'nm::Alloc<T>::missing_member'" in pxd
        assert "does not declare a nested 'missing_member'" in pxd
        assert "Alloc[T].missing_member" not in pxd

    def test_a_class_whose_members_are_all_unsupported_emits_pass(self) -> None:
        """A body of only comments is empty to Cython and needs ``pass``.

        ``Uses`` loses its single method to a diagnostic, so without the
        explicit ``pass`` the block fails with "Expected an increase in
        indentation level" -- the fix would have traded one parse error for
        another.
        """
        pxd = render_cpp(get_backend("libclang"), _EDGE_HEADER)
        block = pxd.split("cdef cppclass Uses[T]:")[1]
        assert block.splitlines()[3].strip() == "pass"

    @requires_cxx_toolchain
    def test_the_generated_pxd_parses(self, tmp_path: Path) -> None:
        """The whole edge-case block is valid Cython.

        Only the parse step runs: these fixtures are deliberately not
        instantiable in C++ (``int::widget_kind`` and
        ``Alloc<int>::missing_member`` do not exist), which is precisely why
        their declarations cannot be emitted. The parse is therefore the only
        step that carries information here.
        """
        pyx = "# distutils: language = c++\nfrom m cimport Vec, Uses, Alloc\n"
        cythonize_cpp_only(tmp_path, _EDGE_HEADER, render_cpp(get_backend("libclang"), _EDGE_HEADER), pyx)


_CONVENTIONAL_HEADER = textwrap.dedent("""\
    #pragma once
    #include <cstddef>
    namespace nm {

    struct Sized { typedef unsigned long size_type; };

    template <class T>
    struct Counter {
        typename T::size_type n;
        Counter(typename T::size_type start) : n(start) {}
        typename T::size_type bump() { return ++n; }
    };

    }
""")


class TestConventionalDependentSpelling:
    """The ``size_type`` substitution must be ABI-correct, not merely parseable.

    Emitting ``size_t`` for ``typename T::size_type`` is a deviation: it is
    right only where the nested type really is ``size_t``-shaped. Executing the
    binding is what distinguishes a correct substitution from one that happens
    to compile.
    """

    @requires_cxx_toolchain
    def test_substituted_binding_executes_with_correct_values(self, tmp_path: Path) -> None:
        pxd = render_cpp(get_backend("libclang"), _CONVENTIONAL_HEADER)
        assert "size_t bump()" in pxd
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Counter, Sized

            def run():
                cdef Counter[Sized] *c = new Counter[Sized](41)
                cdef size_t bumped = c.bump()
                del c
                return bumped
        """)
        assert build_and_run_cpp(tmp_path, _CONVENTIONAL_HEADER, pxd, pyx) == "42"


# ---------------------------------------------------------------------------
# R10 -- C++ base-class lists
# ---------------------------------------------------------------------------

_PLAIN_BASE_HEADER = textwrap.dedent("""\
    #pragma once
    namespace types {
    struct false_type { int v; int getv() { return v; } };
    struct deferred_false : types::false_type { int w; };
    }
""")

_TEMPLATE_BASE_HEADER = textwrap.dedent("""\
    #pragma once
    namespace types {
    template <class A, class B> struct conjunction { int v; int getv() { return v; } };
    template <class T> struct deferred : types::conjunction<T, T> { int w; };
    }
""")

_MULTIPLE_BASE_HEADER = textwrap.dedent("""\
    #pragma once
    namespace types {
    struct base_a { int a; int geta() { return a; } };
    template <class T> struct base_b { int b; };
    struct derived : types::base_a, types::base_b<int> { int c; };
    }
""")

_SAME_NAMESPACE_HEADER = textwrap.dedent("""\
    #pragma once
    namespace types {
    struct base_a { int a; int geta() { return a; } };
    struct derived : base_a { int c; };
    }
""")

#: The base's namespace sorts *after* the derived class's, so the default
#: alphabetical block order puts the base second. A test using ``other``/``types``
#: would be inert: alphabetical order already happens to be correct there.
_CROSS_NAMESPACE_HEADER = textwrap.dedent("""\
    #pragma once
    namespace zzz { struct far_base { int a; int geta() { return a; } }; }
    namespace aaa { struct derived : zzz::far_base { int c; }; }
""")

#: ``std::exception`` is not declared by this translation unit's own output and
#: has no libcpp cimport registered, so it cannot be named at all.
_UNRESOLVABLE_BASE_HEADER = textwrap.dedent("""\
    #pragma once
    #include <exception>
    struct my_error : std::exception { int code; };
""")

#: ``b::B1`` inherits from ``a::A1`` and ``a::A2`` from ``b::B1``, so no order
#: of the two ``cdef extern`` blocks can put both bases before their subclass.
_NAMESPACE_CYCLE_HEADER = textwrap.dedent("""\
    #pragma once
    namespace a { struct A1 { int x; }; }
    namespace b { struct B1 : a::A1 { int y; }; }
    namespace a { struct A2 : b::B1 { int z; }; }
""")


class TestBaseClassLists:
    """Base-class names must be emitted in a spelling Cython can resolve.

    Base names bypass ``_format_ctype``, so the namespace stripping that every
    other type position gets never reached them: ``struct deferred_false :
    types::false_type`` emitted ``cdef cppclass deferred_false(types::false_type)``,
    and Cython has no ``::`` in a type expression.

    Two further defects sit behind that one and are pinned here as well, because
    fixing only the spelling leaves the output wrong in a way that does not
    error. A base list is legal only on a ``cppclass``, so a record the backend
    did not mark ``is_cppclass`` dropped its bases entirely; and Cython resolves
    inherited members where the derived class is parsed, so a base named before
    its own ``cdef extern`` block yields a subclass with no inherited members at
    all -- silently.
    """

    def test_plain_qualified_base_loses_its_namespace(self, backend: Any) -> None:
        assert render_cpp(backend, _PLAIN_BASE_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "types":

                cdef cppclass false_type:
                    int v
                    int getv()

                cdef cppclass deferred_false(false_type):
                    int w
        """)

    def test_templated_qualified_base_uses_cython_brackets(self, backend: Any) -> None:
        assert render_cpp(backend, _TEMPLATE_BASE_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "types":

                cdef cppclass conjunction[A, B]:
                    int v
                    int getv()

                cdef cppclass deferred[T](conjunction[T, T]):
                    int w
        """)

    def test_multiple_inheritance_mixes_plain_and_templated_bases(self, backend: Any) -> None:
        assert render_cpp(backend, _MULTIPLE_BASE_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "types":

                cdef cppclass base_a:
                    int a
                    int geta()

                cdef cppclass base_b[T]:
                    int b

                cdef cppclass derived(base_a, base_b[int]):
                    int c
        """)

    def test_unqualified_base_in_the_same_namespace_is_preserved(self, backend: Any) -> None:
        """The base carries no qualifier here, so only the dropped-base defect applies.

        Asserting the whole block is what shows ``base_a`` became a ``cppclass``:
        a ``cdef struct`` base makes Cython crash rather than reject the file.
        """
        assert render_cpp(backend, _SAME_NAMESPACE_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "types":

                cdef cppclass base_a:
                    int a
                    int geta()

                cdef cppclass derived(base_a):
                    int c
        """)

    def test_cross_namespace_base_is_stripped_and_its_block_moved_first(self, backend: Any) -> None:
        """Stripping is correct across namespaces, and ordering is what makes it work.

        An inheritance list in a ``.pxd`` is never re-emitted into the generated
        C++ -- only the derived class's own name is -- so Cython needs the base
        only to resolve inherited members against the entry it already declared
        under that base's own ``namespace`` block. The ``zzz`` block therefore
        has to precede ``aaa``, which reverses the default alphabetical order.
        """
        assert render_cpp(backend, _CROSS_NAMESPACE_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "zzz":

                cdef cppclass far_base:
                    int a
                    int geta()

            cdef extern from "test.hpp" namespace "aaa":

                cdef cppclass derived(far_base):
                    int c
        """)

    def test_unorderable_cross_namespace_base_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        """No block order satisfies both edges, so one base cannot be named.

        Emitting it anyway would compile and produce a subclass silently missing
        its inherited members, which is the worst available outcome; the
        diagnostic names the symbol instead.
        """
        pxd = render_cpp(backend, _NAMESPACE_CYCLE_HEADER)
        assert "# UNSUPPORTED: base class 'b::B1' of 'A2' cannot be named in Cython" in pxd
        # The resolvable edge must still be emitted -- a fix that dropped every
        # base in the cycle would satisfy the assertion above vacuously.
        assert "cdef cppclass B1(A1):" in pxd
        assert "cdef cppclass A2:" in pxd

    def test_base_that_cannot_be_named_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        """A base absent from the output must not be emitted as a bare name.

        ``cdef cppclass my_error(exception)`` parses, so Cython would accept it
        and resolve ``exception`` to nothing. The class must still be emitted --
        dropping it as well would lose ``code`` too.
        """
        pxd = render_cpp(backend, _UNRESOLVABLE_BASE_HEADER)
        assert "# UNSUPPORTED: base class 'std::exception' of 'my_error' cannot be named in Cython" in pxd
        assert "cdef cppclass my_error:" in pxd
        assert "int code" in pxd
        assert "exception" not in pxd.replace("std::exception", "")

    @pytest.mark.parametrize(
        "header",
        [
            _PLAIN_BASE_HEADER,
            _TEMPLATE_BASE_HEADER,
            _MULTIPLE_BASE_HEADER,
            _CROSS_NAMESPACE_HEADER,
        ],
    )
    def test_no_cxx_scope_operator_reaches_a_declaration(self, backend: Any, header: str) -> None:
        """Only the ``namespace "..."`` string literal may contain ``::``."""
        for line in render_cpp(backend, header).splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("cdef extern from "):
                continue
            assert "::" not in stripped, line

    @requires_cxx_toolchain
    @pytest.mark.parametrize(
        ("header", "body", "expected"),
        [
            (_PLAIN_BASE_HEADER, "cdef deferred_false d\nd.v = 3\nd.w = 4\nreturn d.getv() + d.w", "7"),
            (_TEMPLATE_BASE_HEADER, "cdef deferred[int] d\nd.v = 3\nd.w = 4\nreturn d.getv() + d.w", "7"),
            (_MULTIPLE_BASE_HEADER, "cdef derived d\nd.a = 1\nd.b = 2\nd.c = 3\nreturn d.geta() + d.b + d.c", "6"),
            (_SAME_NAMESPACE_HEADER, "cdef derived d\nd.a = 8\nd.c = 1\nreturn d.geta() + d.c", "9"),
            (_CROSS_NAMESPACE_HEADER, "cdef derived d\nd.a = 7\nd.c = 5\nreturn d.geta() + d.c", "12"),
        ],
        ids=["plain", "templated", "multiple", "same_namespace", "cross_namespace"],
    )
    def test_inherited_members_are_reachable_from_a_built_extension(
        self, backend: Any, tmp_path: Path, header: str, body: str, expected: str
    ) -> None:
        """Every case is compiled, linked, imported and called.

        ``run()`` reads and writes an *inherited* field and calls an *inherited*
        method, so a binding whose base list was dropped fails to cythonize
        rather than passing with no C++ emitted.
        """
        indented = textwrap.indent(textwrap.dedent(body), "    ")
        pyx = f"# distutils: language = c++\nfrom m cimport *\n\ndef run():\n{indented}\n"
        pxd = render_cpp(backend, header)
        assert build_and_run_cpp(tmp_path, header, pxd, pyx) == expected


# ---------------------------------------------------------------------------
# Cross-namespace name collisions, and the C `struct` keyword in field position
# ---------------------------------------------------------------------------

# Two holders, in the first and last namespace alphabetically. One holder alone
# is inert: binding every unqualified `dup` to the first namespace is wrong but
# still yields `a_dup`, so the bug survives the test.
_COLLIDING_NAMESPACES_HEADER = textwrap.dedent("""\
    namespace a { struct dup { int x; }; struct a_holder { dup d; }; }
    namespace b { struct dup { double y; }; }
    namespace c { struct dup { char z; }; struct c_holder { dup d; }; }
""")

_NESTED_UNION_FIELD_HEADER = textwrap.dedent("""\
    namespace ns {
    class String {
    private:
        struct view { char* ptr; unsigned size; };
        union {
            char buf[24];
            view data;
        };
    public:
        unsigned size() const;
    };
    }
""")


class TestCrossNamespaceCollision:
    """A type name declared in several namespaces must not lose declarations.

    Emitting both as ``dup`` in two ``namespace``-qualified extern blocks is
    not caught by any tool in the chain: Cython accepts it, binds every use to
    whichever block came first, and generates C++ naming only ``a::dup``. It
    cythonizes and it compiles. Only running it reveals the loss, so these
    cases assert the emitted names *and* execute the result.

    Both backends are covered. libclang used to collapse the collision in the
    IR before the writer ever saw it, because ``self._seen`` keyed a record on
    its bare name; these cases would have been vacuous on that backend. They
    now pin the two backends to the same output.
    """

    @pytest.fixture(params=["libclang", "treesitter"])
    def backend(self, request: pytest.FixtureRequest) -> Any:
        """Override the module's libclang-only fixture to cover both backends."""
        return get_backend(request.param)

    def test_every_colliding_declaration_survives(self, backend: Any) -> None:
        """Assert the whole output: a substring check cannot see a dropped block."""
        assert render_cpp(backend, _COLLIDING_NAMESPACES_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef struct a_dup "a::dup":
                    int x

                cdef struct b_dup "b::dup":
                    double y

                cdef struct c_dup "c::dup":
                    char z

            cdef extern from "test.hpp" namespace "a":

                cdef struct a_holder:
                    a_dup d

            cdef extern from "test.hpp" namespace "c":

                cdef struct c_holder:
                    c_dup d
            """)

    def test_each_colliding_name_keeps_its_own_members(self, backend: Any) -> None:
        """The members must not be merged onto one surviving declaration.

        A rename that kept three names but pointed them at one record would
        satisfy a name-only assertion.
        """
        pxd = render_cpp(backend, _COLLIDING_NAMESPACES_HEADER)
        assert pxd.count('cdef struct a_dup "a::dup":\n        int x\n') == 1
        assert pxd.count('cdef struct b_dup "b::dup":\n        double y\n') == 1
        assert pxd.count('cdef struct c_dup "c::dup":\n        char z\n') == 1

    def test_unqualified_use_resolves_to_its_own_namespace(self, backend: Any) -> None:
        """Each holder's ``dup`` member is the one from that holder's namespace.

        Two holders in different namespaces are required. With one, resolving
        every use to the alphabetically first namespace is wrong yet still
        produces the expected ``a_dup``.
        """
        pxd = render_cpp(backend, _COLLIDING_NAMESPACES_HEADER)
        assert "cdef struct a_holder:\n        a_dup d\n" in pxd
        assert "cdef struct c_holder:\n        c_dup d\n" in pxd

    def test_a_renamed_declaration_leaves_its_namespace_block(self, backend: Any) -> None:
        """A fully qualified cname only resolves outside a ``namespace`` block.

        Inside ``namespace "a"``, ``cdef struct a_dup "a::dup"`` emits a bare,
        incomplete ``struct dup`` -- the block does not qualify an explicit
        cname. A non-colliding neighbour must still keep its block.
        """
        pxd = render_cpp(backend, _COLLIDING_NAMESPACES_HEADER)
        unqualified, _, namespaced = pxd.partition('cdef extern from "test.hpp" namespace "a":')
        assert '"a::dup"' in unqualified
        assert '"c::dup"' in unqualified
        assert "dup" not in namespaced.replace("a_dup", "").replace("c_dup", "")
        assert "cdef struct a_holder:" in namespaced
        assert "cdef struct c_holder:" in namespaced

    def test_names_do_not_depend_on_declaration_order(self, backend: Any) -> None:
        """Golden files and downstream fixtures require a stable spelling."""
        reordered = textwrap.dedent("""\
            namespace c { struct dup { char z; }; struct c_holder { dup d; }; }
            namespace b { struct dup { double y; }; }
            namespace a { struct dup { int x; }; struct a_holder { dup d; }; }
        """)
        rendered = render_cpp(backend, reordered)
        for name in ("a_dup", "b_dup", "c_dup"):
            assert f'"{name.replace("_", "::")}"' in rendered

    def test_a_name_unique_to_one_namespace_is_not_renamed(self, backend: Any) -> None:
        """Renaming unconditionally would churn every existing golden file."""
        pxd = render_cpp(
            backend,
            "namespace a { struct solo { int x; }; }\nnamespace b { struct other { int y; }; }\n",
        )
        assert "cdef struct solo:" in pxd
        assert "cdef struct other:" in pxd
        assert "a_solo" not in pxd
        assert "b_other" not in pxd

    @requires_cxx_toolchain
    def test_colliding_types_are_distinct_in_a_built_extension(self, backend: Any, tmp_path: Path) -> None:
        """Each field is unique to its own record, so a collapse cannot compile.

        Reading ``ha.d.x`` and ``hc.d.z`` additionally proves each unqualified
        member reference resolved to its own namespace's ``dup`` -- ``.z`` does
        not exist on ``a::dup``, so binding both to the first namespace fails.
        """
        pxd = render_cpp(backend, _COLLIDING_NAMESPACES_HEADER)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport *

            def run():
                cdef a_dup va
                cdef b_dup vb
                cdef c_dup vc
                cdef a_holder ha
                cdef c_holder hc
                va.x = 41
                vb.y = 2.5
                vc.z = b'Q'
                ha.d.x = 7
                hc.d.z = b'Z'
                return va.x, vb.y, vc.z, ha.d.x, hc.d.z
            """)
        assert build_and_run_cpp(tmp_path, _COLLIDING_NAMESPACES_HEADER, pxd, pyx) == "(41, 2.5, 81, 7, 90)"


class TestCrossNamespaceRecordIdentityInTheIr:
    """``self._seen`` in the libclang backend must key a record by namespace.

    The writer half of this fix is inert on libclang unless both declarations
    reach the IR. Keying on the bare name skipped ``b::dup`` at insertion, with
    no diagnostic, so the writer had nothing to rename. Widening a dedup key
    risks the opposite defect, so the collapsing cases are pinned alongside the
    surviving ones.
    """

    @staticmethod
    def _records(backend: Any, code: str) -> list[tuple[str | None, str | None, list[str]]]:
        header = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])
        return [
            (decl.namespace, decl.name, [field.name for field in decl.fields])
            for decl in header.declarations
            if isinstance(decl, Struct)
        ]

    def test_same_name_in_two_namespaces_both_survive(self, backend: Any) -> None:
        """The defect itself: ``b::dup`` vanished from the IR entirely."""
        assert self._records(
            backend,
            textwrap.dedent("""\
                namespace a { struct dup { int x; }; }
                namespace b { struct dup { double y; }; }
            """),
        ) == [("a", "dup", ["x"]), ("b", "dup", ["y"])]

    def test_the_same_record_reached_twice_does_not_duplicate(self, backend: Any) -> None:
        """A widened key must still collapse a genuine repeat.

        Two ``namespace a`` blocks are the single-file stand-in for one header
        reached through two include paths.
        """
        assert self._records(
            backend,
            textwrap.dedent("""\
                namespace a { struct dup { int x; }; }
                namespace a { struct dup; }
            """),
        ) == [("a", "dup", ["x"])]

    def test_a_forward_declaration_and_its_definition_collapse(self, backend: Any) -> None:
        """The definition replaces the opaque entry rather than joining it."""
        assert self._records(
            backend,
            textwrap.dedent("""\
                namespace a { struct dup; }
                namespace a { struct dup { int x; }; }
            """),
        ) == [("a", "dup", ["x"])]

    def test_a_definition_does_not_evict_a_forward_declaration_next_door(self, backend: Any) -> None:
        """``_remove_forward_declaration`` matched on the bare name too.

        Three declarations are required. With only ``a`` forward-declared, the
        eviction never fires and a namespace-blind scan looks correct. Once
        ``b`` is forward-declared as well, the scan for ``b``'s definition
        reaches ``a::dup`` first and pops it, leaving ``a::dup`` dropped and
        ``b::dup`` present twice.
        """
        assert self._records(
            backend,
            textwrap.dedent("""\
                namespace a { struct dup; }
                namespace b { struct dup; }
                namespace b { struct dup { double y; }; }
            """),
        ) == [("a", "dup", []), ("b", "dup", ["y"])]

    def test_anonymous_records_are_unaffected(self, backend: Any) -> None:
        """Synthesized tags are parent-qualified, so they collide across namespaces too."""
        assert self._records(
            backend,
            textwrap.dedent("""\
                namespace a { struct outer { struct { int i; } anon; }; }
                namespace b { struct outer { struct { double d; } anon; }; }
            """),
        ) == [
            ("a", "_outer_anon_s", ["i"]),
            ("a", "outer", ["anon"]),
            ("b", "_outer_anon_s", ["d"]),
            ("b", "outer", ["anon"]),
        ]

    def test_a_global_record_coexists_with_a_namespaced_one(self, backend: Any) -> None:
        """The global one keeps its bare name; only the namespaced key gains a prefix."""
        assert self._records(
            backend,
            textwrap.dedent("""\
                struct dup { char c; };
                namespace a { struct dup { int x; }; }
            """),
        ) == [(None, "dup", ["c"]), ("a", "dup", ["x"])]


@requires_cxx_toolchain
class TestCrossNamespaceCollisionReachesCxx:
    """The libclang end-to-end proof, read off the generated C++.

    The earlier output for this case cythonized, compiled and linked cleanly
    while binding both variables to ``a::dup``; the wrong assignment was
    lowered to a dict setattr. A cythonize-only or compile-only check passes on
    that defect, so the generated C++ is grepped for both mangled symbols and
    the extension is executed for its values.
    """

    _HEADER = textwrap.dedent("""\
        namespace a { struct dup { int x; }; }
        namespace b { struct dup { double y; }; }
    """)

    _PYX = textwrap.dedent("""\
        # distutils: language = c++
        from m cimport *

        def run():
            cdef a_dup va
            cdef b_dup vb
            va.x = 41
            vb.y = 2.5
            return va.x, vb.y
        """)

    def test_both_records_are_named_in_the_generated_cxx_and_execute(self, tmp_path: Path) -> None:
        pxd = render_cpp(get_backend("libclang"), self._HEADER)
        assert build_and_run_cpp(tmp_path, self._HEADER, pxd, self._PYX) == "(41, 2.5)"

        generated = (tmp_path / "use.cpp").read_text()
        assert "a::dup" in generated
        assert "b::dup" in generated


class TestStructKeywordInFieldPosition:
    """Cython takes ``struct`` on the declaration only, never on a type use.

    ``struct view data`` is a C elaborated-type-specifier. In a ``cppclass``
    body it is ``Syntax error in C++ class definition``; in a ``cdef struct``
    body, ``Syntax error in C variable declaration``; in a parameter list,
    ``Expected ')'``. There is no field position in which it is accepted.
    """

    def test_nested_record_field_drops_the_struct_keyword(self, backend: Any) -> None:
        """The doctest ``String::view`` shape: a nested record in an anon union.

        The record is declared inside the class, so it is in no ``known_*`` set
        and the old "strip only when declared" rule left the keyword on.
        """
        assert render_cpp(backend, _NESTED_UNION_FIELD_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "ns":

                cdef cppclass String:
                    struct view:
                        char* ptr
                        unsigned int size
                    char buf[24]
                    view data
                    unsigned int size() const
            """)

    @requires_cxx_toolchain
    def test_the_field_line_is_no_longer_a_syntax_error(self, backend: Any, tmp_path: Path) -> None:
        """Cythonize the real output and assert the parse error is gone."""
        (tmp_path / "test.hpp").write_text(_NESTED_UNION_FIELD_HEADER)
        (tmp_path / "m.pxd").write_text(render_cpp(backend, _NESTED_UNION_FIELD_HEADER))
        (tmp_path / "use.pyx").write_text("# distutils: language = c++\nfrom m cimport *\n")
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "cython", "-3", "--cplus", "use.pyx", "-o", "use.cpp"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        # The nested record is now declared, so the whole unit compiles: the
        # weaker "no Syntax error" check passed even while 'view' was named but
        # never declared.
        assert result.returncode == 0, result.stdout + result.stderr

    def test_an_anonymous_record_keeps_its_tag(self, backend: Any) -> None:
        """``struct (anonymous at f.h:1)`` is not an identifier.

        Stripping the tag there would manufacture a name from the parenthesised
        spelling instead of leaving it for the existing diagnostics.
        """
        writer = PxdWriter(
            backend.parse(_NESTED_UNION_FIELD_HEADER, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])
        )
        formatted = writer._format_ctype(CType(name="struct (anonymous at test.hpp:1:1)"))
        assert formatted.startswith("struct (anonymous")


_RVALUE_HEADER = textwrap.dedent("""\
    class String {
    public:
        String();
        String(String&& other);
        String& operator=(String&& other);
        void adopt(String&& other);
        void borrow(const String&& other);
    };
    void sink(String&& in_);
    String&& give();
""")


class TestRvalueReferences:
    """Cython 3.3 parses ``&&`` in parameter position only.

    Measured against Cython 3.3.0: a parameter spelled ``T&&`` compiles and
    lowers to a real ``cython_std::move``; ``T&&`` as a return type, field,
    typedef, or variable is ``Syntax error in C variable declaration``. The
    unrepresentable positions are skipped with a diagnostic rather than
    downgraded to ``T&``, which would compile while widening the signature to
    bind lvalues that C++ rejects.
    """

    def test_rvalue_parameters_keep_the_double_ampersand(self, backend: Any) -> None:
        """A free function, a method, a move constructor and a move assignment."""
        rendered = render_cpp(backend, _RVALUE_HEADER)
        assert "void sink(String&& in_)" in rendered
        assert "String(String&& other)" in rendered
        assert "String& operator=(String&& other)" in rendered
        assert "void adopt(String&& other)" in rendered

    def test_a_const_rvalue_parameter_keeps_both_qualifiers(self, backend: Any) -> None:
        assert "void borrow(const String&& other)" in render_cpp(backend, _RVALUE_HEADER)

    def test_an_rvalue_return_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        """The doctest ``String&& toString(String&&)`` shape."""
        rendered = render_cpp(backend, _RVALUE_HEADER)
        assert "# UNSUPPORTED: give() returns rvalue reference 'String&&'" in rendered
        assert "String&& give()" not in rendered

    def test_an_rvalue_return_is_never_downgraded_to_an_lvalue_reference(self, backend: Any) -> None:
        """``String& give()`` would compile while accepting what C++ refuses."""
        rendered = render_cpp(backend, _RVALUE_HEADER)
        assert "give()" not in rendered.replace("# UNSUPPORTED: give() returns rvalue reference 'String&&'", "")

    def test_an_rvalue_return_from_a_method_is_skipped(self, backend: Any) -> None:
        rendered = render_cpp(
            backend,
            textwrap.dedent("""\
                class String { public: String(); };
                class Holder { public: String&& release(); };
            """),
        )
        assert "# UNSUPPORTED: release() returns rvalue reference 'String&&'" in rendered
        assert "release()" not in rendered.replace("# UNSUPPORTED: release() returns rvalue reference 'String&&'", "")

    def test_an_rvalue_field_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        rendered = render_cpp(
            backend,
            textwrap.dedent("""\
                class String { public: String(); };
                class Holder { public: String&& held; };
            """),
        )
        assert "# UNSUPPORTED: field 'held' has rvalue reference 'String&&'" in rendered
        assert "held" not in rendered.replace("# UNSUPPORTED: field 'held' has rvalue reference 'String&&'", "")

    def test_an_rvalue_typedef_is_skipped_with_a_diagnostic(self, backend: Any) -> None:
        rendered = render_cpp(
            backend,
            textwrap.dedent("""\
                class String { public: String(); };
                typedef String&& StringRef;
            """),
        )
        assert "# UNSUPPORTED: typedef 'StringRef' aliases rvalue reference 'String&&'" in rendered
        assert "ctypedef" not in rendered

    def test_every_diagnostic_states_the_reason(self, backend: Any) -> None:
        """A skip without a reason is indistinguishable from a silent drop."""
        rendered = render_cpp(backend, _RVALUE_HEADER)
        assert (
            "# Cython supports '&&' only on parameters, "
            "not in return, field, typedef, or variable position." in rendered
        )

    _E2E_HEADER = textwrap.dedent("""\
        #include <string>
        class String {
        public:
            std::string s;
            String() : s("") {}
            String(const char* c) : s(c) {}
            String(String&& o) : s(std::move(o.s)) { o.s = "MOVED-FROM"; }
            String& operator=(String&& o) { s = std::move(o.s); o.s = "MOVED-FROM"; return *this; }
            const char* c_str() const { return s.c_str(); }
        };
        inline int sink(String&& x) { String tmp(static_cast<String&&>(x)); return (int)tmp.s.size(); }
    """)

    _E2E_PYX = textwrap.dedent("""\
        # distutils: language = c++
        from libcpp.utility cimport move
        from m cimport String, sink

        def run():
            cdef String a = String(b"hello-world")
            cdef int n = sink(move(a))
            return n, a.c_str()
    """)

    @requires_cxx_toolchain
    def test_an_rvalue_parameter_really_moves_the_argument(self, tmp_path: Path) -> None:
        """Execute it: the source must be observably moved from.

        Cython warns "Rvalue-reference as function argument not supported" and
        still emits a correct ``cython_std::move``. Only running the code
        distinguishes that warning from a silent downgrade to a copy.
        """
        pxd = render_cpp(get_backend("libclang"), self._E2E_HEADER)
        assert build_and_run_cpp(tmp_path, self._E2E_HEADER, pxd, self._E2E_PYX) == "(11, b'MOVED-FROM')"

        generated = (tmp_path / "use.cpp").read_text()
        assert "cython_std::move<String>" in generated


_NESTED_CLASS_HEADER = textwrap.dedent("""\
    struct view { long decoy; };
    class Outer {
    public:
        struct view { int ptr; unsigned int size; };
        view data;
    };
""")


class TestNestedRecordInAClass:
    """A record defined in a C++ class body is declared inside the parent.

    Lifting it to the top level would flatten an inner symbol into the global
    namespace, which AGENTS.md forbids and which a same-named global would
    collide with. Cython declares a nested record inside the parent suite
    without ``cdef`` and resolves it as ``Parent.Inner`` -- the form
    ``libcpp/vector.pxd`` uses for ``vector[T].iterator`` -- so the C++
    qualification is implicit and no synthesized name is needed.
    """

    @pytest.fixture(params=["libclang", "treesitter"])
    def backend(self, request: pytest.FixtureRequest) -> Any:
        """Override the module's libclang-only fixture to cover both backends."""
        return get_backend(request.param)

    def test_the_nested_record_is_declared_inside_the_parent(self, backend: Any) -> None:
        """Assert the whole output: a substring check cannot see a dropped block."""
        assert render_cpp(backend, _NESTED_CLASS_HEADER) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef struct view:
                    long decoy

                cdef cppclass Outer:
                    struct view:
                        int ptr
                        unsigned int size
                    view data
            """)

    def test_the_nested_record_is_not_lifted_to_the_top_level(self, backend: Any) -> None:
        """Scope integrity, both halves: ``view`` is declared, and only inside ``Outer``.

        Asserting only the absence at top level is satisfied by dropping the
        record altogether, so the presence inside the parent is asserted with
        it. Only the decoy global ``view`` may appear at top level.
        """
        rendered = render_cpp(backend, _NESTED_CLASS_HEADER)
        top_level = [ln for ln in rendered.splitlines() if ln.startswith("    cdef ")]
        assert "    cdef struct view:" in top_level
        assert sum(1 for ln in top_level if "view" in ln) == 1
        assert "        struct view:" in rendered.splitlines()

    def test_the_colliding_global_record_still_carries_its_own_field(self, backend: Any) -> None:
        """The two ``view`` records have different layouts; neither may win."""
        rendered = render_cpp(backend, _NESTED_CLASS_HEADER)
        assert "long decoy" in rendered
        assert "int ptr" in rendered

    _E2E_PYX = textwrap.dedent("""\
        # distutils: language = c++
        from m cimport Outer

        def run():
            cdef Outer o
            o.data.ptr = 7
            o.data.size = 42
            cdef Outer.view v = o.data
            return v.ptr, v.size
    """)

    @requires_cxx_toolchain
    def test_the_generated_cxx_names_the_qualified_type_and_executes(self, tmp_path: Path) -> None:
        """Grep the C++: binding the decoy global would compile and link too."""
        pxd = render_cpp(get_backend("libclang"), _NESTED_CLASS_HEADER)
        assert build_and_run_cpp(tmp_path, _NESTED_CLASS_HEADER, pxd, self._E2E_PYX) == "(7, 42)"

        generated = (tmp_path / "use.cpp").read_text()
        assert "Outer::view" in generated


_CROSS_NS_ENUM_TYPEDEF_VAR_HEADER = textwrap.dedent("""\
    namespace a { enum E { X = 1 }; typedef int T; extern int v; }
    namespace b { enum E { Y = 2 }; typedef char T; extern double v; }
""")


class TestCrossNamespaceEnumTypedefAndVariableIdentityInTheIr:
    """``self._seen`` must key enums, typedefs and variables by namespace too.

    The record fix pinned by :class:`TestCrossNamespaceRecordIdentityInTheIr`
    left three sibling keys namespace-blind, so ``b::E``, ``b::T`` and ``b::v``
    were dropped at insertion with no diagnostic. Widening a dedup key risks
    the opposite defect, so each collapsing case is pinned beside its
    surviving one.
    """

    @pytest.fixture(params=["libclang", "treesitter"])
    def backend(self, request: pytest.FixtureRequest) -> Any:
        """Override the module's libclang-only fixture to cover both backends."""
        return get_backend(request.param)

    @staticmethod
    def _parse(backend: Any, code: str) -> Any:
        return backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])

    @classmethod
    def _enums(cls, backend: Any, code: str) -> list[tuple[str | None, list[str]]]:
        return [
            (decl.name, [value.name for value in decl.values])
            for decl in cls._parse(backend, code).declarations
            if isinstance(decl, Enum)
        ]

    @classmethod
    def _typedefs(cls, backend: Any, code: str) -> list[tuple[str | None, str, str]]:
        return [
            (decl.namespace, decl.name, str(decl.underlying_type))
            for decl in cls._parse(backend, code).declarations
            if isinstance(decl, Typedef)
        ]

    @classmethod
    def _variables(cls, backend: Any, code: str) -> list[tuple[str | None, str, str]]:
        return [
            (decl.namespace, decl.name, str(decl.type))
            for decl in cls._parse(backend, code).declarations
            if isinstance(decl, Variable)
        ]

    def test_same_named_enums_in_two_namespaces_both_survive(self, backend: Any) -> None:
        """The defect itself: ``b::E`` vanished from the IR entirely.

        The enumerator names carry the identity, so a collapse cannot pass by
        emitting the surviving enum twice.
        """
        assert self._enums(backend, _CROSS_NS_ENUM_TYPEDEF_VAR_HEADER) == [("E", ["X"]), ("E", ["Y"])]

    def test_same_named_typedefs_in_two_namespaces_both_survive(self, backend: Any) -> None:
        """``b::T`` aliases ``char``; a collapse leaves only the ``int`` one."""
        assert self._typedefs(backend, _CROSS_NS_ENUM_TYPEDEF_VAR_HEADER) == [
            ("a", "T", "int"),
            ("b", "T", "char"),
        ]

    def test_same_named_variables_in_two_namespaces_both_survive(self, backend: Any) -> None:
        """``b::v`` is a ``double``; a collapse leaves only the ``int`` one."""
        assert self._variables(backend, _CROSS_NS_ENUM_TYPEDEF_VAR_HEADER) == [
            ("a", "v", "int"),
            ("b", "v", "double"),
        ]

    def test_the_same_declarations_reached_twice_do_not_duplicate(self) -> None:
        """A widened key must still collapse a genuine repeat.

        Two ``namespace a`` blocks are the single-file stand-in for one header
        reached through two include paths. This is libclang-only: the
        tree-sitter backend keeps no ``_seen`` set and emits the repeat twice,
        which is a separate pre-existing divergence.
        """
        backend = get_backend("libclang")
        code = textwrap.dedent("""\
            namespace a { enum E { X = 1 }; typedef int T; extern int v; }
            namespace a { typedef int T; extern int v; }
        """)

        assert self._enums(backend, code) == [("E", ["X"])]
        assert self._typedefs(backend, code) == [("a", "T", "int")]
        assert self._variables(backend, code) == [("a", "v", "int")]

    def test_an_opaque_enum_declaration_and_its_definition_collapse(self) -> None:
        """The definition is the single surviving entry, with its enumerators.

        Libclang-only for the same reason: tree-sitter emits the opaque
        declaration as its own valueless enum.
        """
        backend = get_backend("libclang")
        code = textwrap.dedent("""\
            namespace a { enum E : int; }
            namespace a { enum E : int { X = 1 }; }
        """)

        assert self._enums(backend, code) == [("E", ["X"])]

    def test_the_typedef_struct_pattern_still_collapses(self, backend: Any) -> None:
        """``typedef struct {...} Foo;`` stays one entry, not a Struct plus a Typedef."""
        code = textwrap.dedent("""\
            typedef struct { int a; } Foo;
        """)

        assert self._typedefs(backend, code) == []
        assert [
            (decl.namespace, decl.name, [field.name for field in decl.fields])
            for decl in self._parse(backend, code).declarations
            if isinstance(decl, Struct)
        ] == [(None, "Foo", ["a"])]

    def test_global_declarations_keep_their_bare_names(self, backend: Any) -> None:
        """A global declaration coexists with a namespaced one of the same name."""
        code = textwrap.dedent("""\
            enum E { G = 0 };
            typedef long T;
            extern short v;
            namespace a { enum E { X = 1 }; typedef int T; extern int v; }
        """)

        assert self._enums(backend, code) == [("E", ["G"]), ("E", ["X"])]
        assert self._typedefs(backend, code) == [(None, "T", "long"), ("a", "T", "int")]
        assert self._variables(backend, code) == [(None, "v", "short"), ("a", "v", "int")]


class TestCrossNamespaceEnumNamespaceIsRecordedByLibclang:
    """``Enum`` carries a ``namespace``, so the merge-path dedup can tell two apart.

    Keying ``self._seen`` alone was not enough for enums:
    ``_deduplicate_declarations`` re-collapses them on the include-merge path
    because it keys ``(type, name, namespace)`` and ``Enum.namespace`` did not
    exist. The tree-sitter backend does not populate the field yet, so this
    case is libclang-only.
    """

    def test_enum_namespace_survives_an_include_merge(self, tmp_path: Path) -> None:
        """Two same-named enums arriving from two files must both be kept."""
        (tmp_path / "leaf.hpp").write_text("namespace b { enum E { Y = 2 }; }\n")
        top = tmp_path / "top.hpp"
        top.write_text(
            textwrap.dedent("""\
            #include "leaf.hpp"
            namespace a { enum E { X = 1 }; }
        """)
        )

        header = get_backend("libclang").parse(top.read_text(), str(top), [str(tmp_path)])

        assert sorted(
            (decl.namespace, decl.name, tuple(value.name for value in decl.values))
            for decl in header.declarations
            if isinstance(decl, Enum)
        ) == [("a", "E", ("X",)), ("b", "E", ("Y",))]


# ---------------------------------------------------------------------------
# R-cppclass -- a record with a callable member must not be a ``cdef struct``
# ---------------------------------------------------------------------------

_CALLABLE_MEMBER_HEADER = textwrap.dedent("""\
    #pragma once

    struct PlainC { int a; int b; };

    struct WithMethod {
        int v;
        int get() const { return v; }
    };

    struct WithCtor {
        int v;
        WithCtor(int x) : v(x) {}
    };

    struct WithOperator {
        int v;
        WithOperator operator+(const WithOperator& o) const {
            WithOperator r; r.v = v + o.v; return r;
        }
    };
""")


class TestCallableMemberForcesCppclass:
    """Cython parses a function declaration only inside a ``cppclass`` suite.

    A C++ ``struct`` carrying a method, a constructor or an operator was
    emitted as ``cdef struct``, so the member became a C variable declaration
    and Cython rejected the whole file with "Syntax error in C variable
    declaration". That is ordinary C++, not a quirk of one header, so it broke
    every class-bearing C++ header at once.

    The boundary matters as much as the fix: a plain C struct has no callable
    member and must stay a ``cdef struct``, because ``cppclass`` is meaningless
    in a C context.
    """

    def test_method_constructor_and_operator_each_become_cppclass(self, backend: Any) -> None:
        """Each callable-member shape is promoted, and the field-only struct is not."""
        pxd = render_cpp(backend, _CALLABLE_MEMBER_HEADER)

        assert "cdef struct PlainC:" in pxd
        assert "cdef cppclass WithMethod:" in pxd
        assert "cdef cppclass WithCtor:" in pxd
        assert "cdef cppclass WithOperator:" in pxd

    def test_plain_c_struct_is_never_promoted(self, backend: Any) -> None:
        """A C header of field-only structs keeps every record a ``cdef struct``.

        Parsed as C, so nothing in the input could carry a callable member.
        """
        pxd = render(backend, "struct P { int a; };\nstruct Q { double d; char* s; };\n")

        assert "cppclass" not in pxd
        assert "cdef struct P:" in pxd
        assert "cdef struct Q:" in pxd

    @requires_cxx_toolchain
    def test_promoted_records_compile_link_and_execute(self, tmp_path: Path) -> None:
        """The promoted records build and their members return correct values.

        Cythonizing alone would not prove the promotion is right: Cython emits
        no C++ for a declaration nobody uses. The ``.pyx`` therefore constructs
        each record, calls the method, and invokes the operator.
        """
        pxd = render_cpp(get_backend("libclang"), _CALLABLE_MEMBER_HEADER)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport PlainC, WithMethod, WithCtor, WithOperator

            def run():
                cdef PlainC p
                p.a = 10
                p.b = 5
                cdef WithMethod wm
                wm.v = 7
                cdef WithCtor *wc = new WithCtor(3)
                cdef int wcv = wc.v
                del wc
                cdef WithOperator o1
                cdef WithOperator o2
                o1.v = 20
                o2.v = 22
                cdef WithOperator o3 = o1 + o2
                return (p.a + p.b, wm.get(), wcv, o3.v)
        """)

        assert build_and_run_cpp(tmp_path, _CALLABLE_MEMBER_HEADER, pxd, pyx) == "(15, 7, 3, 42)"


# ---------------------------------------------------------------------------
# R-operators -- an operator Cython cannot overload must be skipped, not emitted
# ---------------------------------------------------------------------------

_OPERATOR_HEADER = textwrap.dedent("""\
    #pragma once
    #include <new>

    struct Ops {
        int v;
        Ops() : v(0) {}
        Ops(int x) : v(x) {}

        Ops operator+(const Ops& o) const { return Ops(v + o.v); }
        Ops operator-(const Ops& o) const { return Ops(v - o.v); }
        Ops operator*(const Ops& o) const { return Ops(v * o.v); }
        bool operator==(const Ops& o) const { return v == o.v; }
        bool operator<(const Ops& o) const { return v < o.v; }
        int& operator[](int i) { (void)i; return v; }
        Ops operator<<(int n) const { return Ops(v << n); }
        bool operator!() const { return v == 0; }

        Ops& operator+=(const Ops& o) { v += o.v; return *this; }
        bool operator&&(const Ops& o) const { return v && o.v; }
        Ops& operator<<=(int n) { v <<= n; return *this; }
        // ``decltype(sizeof(0))`` is ``size_t`` on every target and needs no header.
        // Spelling it ``unsigned long`` is a hard error on LLP64 Windows, where
        // ``size_t`` is ``unsigned long long``: C++ requires this parameter to be
        // exactly ``size_t``, so the whole translation unit fails to parse.
        void* operator new(decltype(sizeof(0)) n) { return ::operator new(n); }
        operator int() const { return v; }
    };
""")


class TestUnsupportedOperatorsAreSkippedWithADiagnostic:
    """Cython 3.3 declares only some operator overloads; the rest must not be emitted.

    Which ones was settled by compiling one declaration per spelling, not by
    reading Cython's grammar. Emitting a rejected spelling makes the whole
    ``.pxd`` uncompilable ("Overloading operator '+=' not yet supported."),
    while dropping it silently would hide the loss and renaming it to an
    ordinary method would invent an API the header does not declare.
    """

    def test_supported_operators_are_emitted(self, backend: Any) -> None:
        """Every spelling Cython accepts survives to the output."""
        pxd = render_cpp(backend, _OPERATOR_HEADER)

        for spelling in ("operator+", "operator-", "operator*", "operator==", "operator<", "operator[]", "operator!"):
            assert f"{spelling}(" in pxd, spelling
        assert "operator<<(int n)" in pxd

    def test_unsupported_operators_are_replaced_by_a_named_diagnostic(self, backend: Any) -> None:
        """Each rejected spelling is absent, and says so naming itself and the reason."""
        pxd = render_cpp(backend, _OPERATOR_HEADER)

        assert "operator+=(" not in pxd.replace("# UNSUPPORTED: operator+=()", "")
        assert "# UNSUPPORTED: operator+=() is an operator Cython cannot overload" in pxd
        assert "\"Overloading operator '+=' not yet supported.\"" in pxd
        assert "# UNSUPPORTED: operator&&() is an operator Cython cannot overload" in pxd
        assert "\"Overloading operator '&&' not yet supported.\"" in pxd
        assert "# UNSUPPORTED: operator<<=() is an operator Cython cannot overload" in pxd
        assert "# UNSUPPORTED: operator new() is an operator Cython cannot overload" in pxd
        assert "Cython 3.3 rejects an allocation operator" in pxd

    def test_conversion_operators_are_classified_by_the_empirical_table(self) -> None:
        """``operator bool`` is the one conversion Cython accepts; the rest are not.

        Asserted on :func:`_unsupported_operator_reason` directly. The writer
        never reaches these: libclang files a conversion under
        ``Struct.conversions``, which the Cython writer does not emit at all.
        That is a separate, pre-existing gap -- this pins the classification so
        the rule is right whenever conversions are wired up.
        """
        assert _unsupported_operator_reason("operator bool") is None

        for spelling in ("operator int", "operator double"):
            reason = _unsupported_operator_reason(spelling)
            assert reason is not None, spelling
            assert "only 'operator bool' as a conversion operator" in reason

    def test_non_operator_names_are_never_classified(self) -> None:
        """A plain method is not an operator, whatever its name begins with."""
        assert _unsupported_operator_reason("operators") is None
        assert _unsupported_operator_reason("get") is None
        assert _unsupported_operator_reason("operator") is None

    def test_free_operator_is_guarded_on_the_same_rule(self, backend: Any) -> None:
        """A namespace-level operator is subject to the identical restriction."""
        code = textwrap.dedent("""\
            struct T { int v; };
            bool operator==(const T& a, const T& b);
            T& operator+=(T& a, const T& b);
        """)
        pxd = render_cpp(backend, code)

        assert "bool operator==(const T& a, const T& b)" in pxd
        assert "# UNSUPPORTED: operator+=() is an operator Cython cannot overload" in pxd

    @requires_cxx_toolchain
    def test_supported_operators_compile_link_and_execute(self, tmp_path: Path) -> None:
        """The emitted operators build and evaluate to the values C++ computes.

        Executing is what separates a correct binding from one that merely
        parses: a wrong operator spelling can still cythonize and link.
        """
        pxd = render_cpp(get_backend("libclang"), _OPERATOR_HEADER)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Ops

            def run():
                cdef Ops a = Ops(20)
                cdef Ops b = Ops(22)
                cdef Ops s = a + b
                cdef Ops d = b - a
                cdef Ops m = Ops(6)
                cdef Ops sevens = Ops(7)
                cdef Ops prod = m * sevens
                cdef Ops one = Ops(1)
                cdef Ops sh = one << 3
                return (s.v, d.v, prod.v, sh.v, a[0])
        """)

        assert build_and_run_cpp(tmp_path, _OPERATOR_HEADER, pxd, pyx) == "(42, 2, 42, 8, 20)"

    @requires_cxx_toolchain
    def test_bool_returning_operators_execute(self, tmp_path: Path) -> None:
        """A comparison operator must return a C++ bool, not a Python object.

        Asserted by executing rather than on the emitted text, because
        executing is the only step that could ever see the defect: without
        ``from libcpp cimport bool`` the bare ``bool`` bound to the Python bool
        *object*, the C++ bool went straight into a ``PyObject*``, and the
        binding cythonized, compiled and linked cleanly before segfaulting on
        this call.
        """
        pxd = render_cpp(get_backend("libclang"), _OPERATOR_HEADER)
        assert "from libcpp cimport bool" in pxd
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Ops

            def run():
                cdef Ops a = Ops(20)
                cdef Ops b = Ops(22)
                return (a == a, a < b)
        """)

        assert build_and_run_cpp(tmp_path, _OPERATOR_HEADER, pxd, pyx) == "(True, True)"


# ---------------------------------------------------------------------------
# R-bool -- C++ ``bool`` must not bind to the Python bool object
# ---------------------------------------------------------------------------

_BOOL_HEADER = textwrap.dedent("""\
    #pragma once
    struct Flags {
        bool on;
        bool toggled(bool force) const;
    };
    typedef bool bool_alias;
    typedef bool (*pred_t)(bool);
    bool is_set(bool a, bool* b);
    extern bool enabled;
""")


class TestCppBoolBindsToTheCppType:
    """Cython has no builtin ``bool``, so a bare ``bool`` names the Python object.

    The generated C++ then stores a C++ bool straight into a ``PyObject*``.
    That cythonizes, compiles and links without a diagnostic from any tool in
    the chain, and segfaults on the first call. ``from libcpp cimport bool``
    brings in ``ctypedef bint bool``, which keeps the C spelling and gives it
    integer semantics.
    """

    def test_every_bool_position_keeps_the_spelling_and_gains_the_cimport(self, backend: Any) -> None:
        """Return type, parameter, field, typedef target, pointee and variable at once."""
        assert render_cpp(backend, _BOOL_HEADER) == textwrap.dedent("""\
            from libcpp cimport bool

            cdef extern from "test.hpp":

                cdef cppclass Flags:
                    bool on
                    bool toggled(bool force) const

                ctypedef bool bool_alias

                ctypedef bool (*pred_t)(bool)

                bool is_set(bool a, bool* b)

                bool enabled
        """)

    def test_a_header_without_bool_gains_no_cimport(self, backend: Any) -> None:
        """Negative control: the cimport tracks what the body emitted, not the language."""
        pxd = render_cpp(backend, "struct P { int a; };\n")

        assert "libcpp" not in pxd

    def test_c_bool_becomes_bint_and_needs_no_cimport(self, backend: Any) -> None:
        """The C-vs-C++ distinction.

        ``<stdbool.h>``'s ``bool`` is a macro for ``_Bool``, which libclang
        canonicalises. ``bint`` is Cython's own spelling for it, so the C path
        never emits a bare ``bool`` and must not acquire the cimport.
        """
        source = textwrap.dedent("""\
            #include <stdbool.h>
            struct Flags { bool on; };
            typedef bool bool_alias;
            bool is_set(bool a, bool* b);
        """)
        pxd = render(backend, source)

        assert "cimport" not in pxd
        assert "bool" not in pxd.replace("bool_alias", "")
        assert "bint on" in pxd
        assert "ctypedef bint bool_alias" in pxd
        assert "bint is_set(bint a, bint* b)" in pxd

    @requires_cxx_toolchain
    def test_bool_valued_members_execute(self, tmp_path: Path) -> None:
        """Executing is the only step that could see this defect.

        A field read, a method call and a free function are each exercised, and
        the returned values are checked -- a wrong binding here returns garbage
        or crashes rather than failing to build.
        """
        header = textwrap.dedent("""\
            #pragma once
            struct Flags {
                bool on;
                bool toggled(bool force) const { return force ? !on : on; }
            };
            inline bool is_set(bool a) { return a; }
        """)
        pxd = render_cpp(get_backend("libclang"), header)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Flags, is_set

            def run():
                cdef Flags f
                f.on = True
                return (f.on, f.toggled(True), f.toggled(False), is_set(False))
        """)

        assert build_and_run_cpp(tmp_path, header, pxd, pyx) == "(True, False, True, False)"

    @requires_cxx_toolchain
    def test_bool_function_pointer_typedef_executes(self, tmp_path: Path) -> None:
        """``bint (*)(bint)`` would lower to ``int (*)(int)``, a different C++ type.

        Spelling the typedef ``bool`` rather than ``bint`` is what lets the
        address of a real ``bool(bool)`` function be assigned to it.
        """
        header = textwrap.dedent("""\
            #pragma once
            typedef bool (*pred_t)(bool);
            inline bool negate(bool a) { return !a; }
            inline bool apply(pred_t p, bool a) { return p(a); }
        """)
        pxd = render_cpp(get_backend("libclang"), header)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport pred_t, negate, apply

            def run():
                cdef pred_t p = negate
                return (apply(p, True), apply(p, False))
        """)

        assert build_and_run_cpp(tmp_path, header, pxd, pyx) == "(False, True)"


# ---------------------------------------------------------------------------
# R-conversions -- a conversion operator must never vanish
# ---------------------------------------------------------------------------

_CONVERSION_HEADER = textwrap.dedent("""\
    #pragma once
    struct Conv {
        int v;
        Conv() : v(0) {}
        Conv(int x) : v(x) {}
        operator bool() const { return v != 0; }
        operator int() const { return v; }
        Conv operator,(const Conv& o) const { return Conv(v * 100 + o.v); }
    };
    struct OnlyConversions {
        operator bool() const { return true; }
    };
""")


class TestConversionOperatorsAreEmittedOrDiagnosed:
    """libclang files a conversion under ``Struct.conversions``, which the writer never read.

    The declaration therefore vanished with no diagnostic, and a record whose
    only members were conversions came out as a body-less ``cdef cppclass``.
    ``operator bool`` is the one conversion Cython declares; the rest ride the
    same UNSUPPORTED channel as every other operator it cannot overload.
    """

    def test_operator_bool_is_emitted(self, backend: Any) -> None:
        pxd = render_cpp(backend, _CONVERSION_HEADER)

        assert "bool operator bool() const" in pxd

    def test_other_conversions_name_themselves_in_a_diagnostic(self, backend: Any) -> None:
        """A silent drop is the defect; the reason comes from the shared classifier."""
        pxd = render_cpp(backend, _CONVERSION_HEADER)

        assert "operator int() const" not in pxd
        assert "# UNSUPPORTED: operator int() is an operator Cython cannot overload" in pxd
        assert "only 'operator bool' as a conversion operator" in pxd

    def test_a_record_of_only_conversions_gets_a_body(self, backend: Any) -> None:
        """A body-less ``cdef cppclass B`` was the visible symptom of the drop."""
        pxd = render_cpp(backend, _CONVERSION_HEADER)

        assert "cdef cppclass OnlyConversions:\n        bool operator bool() const" in pxd

    def test_comma_operator_is_aliased(self, backend: Any) -> None:
        """``operator,`` rides the same alias channel as ``operator->`` and ``operator()``.

        Cython parses the unaliased declaration, so emitting it looked correct,
        but no ``.pyx`` syntax reached it: ``a.operator,(b)`` is rejected with
        "has no attribute 'operator'" and ``a, b`` builds a tuple, generating
        zero call sites in the C++. The quoted C name keeps the spelling in the
        output while giving the member a name a ``.pyx`` can call.
        """
        pxd = render_cpp(backend, _CONVERSION_HEADER)

        assert 'Conv comma "operator,"(const Conv& o)' in pxd
        assert "Conv operator,(" not in pxd

    @requires_cxx_toolchain
    def test_comma_operator_alias_calls_the_c_plus_plus_body(self, tmp_path: Path) -> None:
        """The alias must reach ``Conv::operator,``, not merely parse.

        Cythonizing, compiling and linking all succeed for an unaliased
        declaration that no call site ever references, so the assertion is on
        the value the C++ body computes: ``v * 100 + o.v`` distinguishes it
        from any other member that could plausibly answer.
        """
        pxd = render_cpp(get_backend("libclang"), _CONVERSION_HEADER)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Conv

            def run():
                cdef Conv a = Conv(3)
                cdef Conv b = Conv(7)
                return a.comma(b).v
        """)

        assert build_and_run_cpp(tmp_path, _CONVERSION_HEADER, pxd, pyx) == "307"

    @requires_cxx_toolchain
    def test_operator_bool_converts_at_runtime(self, tmp_path: Path) -> None:
        """The emitted conversion is the one C++ actually calls.

        ``<bint>c`` compiles either way if the declaration is merely parsed, so
        the assertion is on the values two different ``v`` produce.
        """
        pxd = render_cpp(get_backend("libclang"), _CONVERSION_HEADER)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport Conv

            def run():
                cdef Conv truthy = Conv(3)
                cdef Conv falsy = Conv(0)
                return (truthy.v, <bint>truthy, <bint>falsy)
        """)

        assert build_and_run_cpp(tmp_path, _CONVERSION_HEADER, pxd, pyx) == "(3, True, False)"


# ---------------------------------------------------------------------------
# R-collisions -- cross-namespace duplicates of every declaration kind
# ---------------------------------------------------------------------------

_COLLIDING_NAMESPACES = textwrap.dedent("""\
    #pragma once
    namespace a {
        inline int v = 111;
        enum E { X = 1 };
        typedef int alias;
        inline alias mk() { return 7; }
    }
    namespace b {
        inline double v = 222.5;
        enum E { Y = 2 };
        typedef double alias;
        inline alias mk() { return 2.5; }
    }
""")


class TestCrossNamespaceCollisionsCoverEveryKind:
    """A ``.pxd`` module namespace is flat; only records were being disambiguated.

    Cython accepts two same-named declarations and binds every use to whichever
    came first, so the second was lost with a warning at most. For an enum it
    was worse than a silent loss: both tags were moved out of their
    ``namespace`` blocks without a cname, so the enumerators came out
    unqualified and the generated C++ did not compile.
    """

    def test_variables_enums_and_typedefs_all_carry_a_qualified_cname(self, backend: Any) -> None:
        assert render_cpp(backend, _COLLIDING_NAMESPACES) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                int a_v "a::v"

                cdef enum a_E "a::E":
                    X "a::X"

                ctypedef int a_alias "a::alias"

                double b_v "b::v"

                cdef enum b_E "b::E":
                    Y "b::Y"

                ctypedef double b_alias "b::alias"

            cdef extern from "test.hpp" namespace "a":

                a_alias mk()

            cdef extern from "test.hpp" namespace "b":

                b_alias mk()
        """)

    def test_a_name_unique_to_one_namespace_is_never_renamed(self, backend: Any) -> None:
        """Negative control: renaming everything namespaced would move every golden.

        Only a name declared in more than one namespace is disambiguated, and a
        namespaced declaration Cython already qualifies correctly is left alone.
        """
        source = textwrap.dedent("""\
            #pragma once
            namespace a { inline int only_a = 5; enum Solo { S = 1 }; typedef int solo_t; }
        """)
        assert render_cpp(backend, source) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "a":

                int only_a

                cdef enum Solo:
                    S

                ctypedef int solo_t
        """)

    def test_a_global_declaration_keeps_its_bare_name(self, backend: Any) -> None:
        """The global one is the public API; only the namespaced twin is renamed."""
        source = textwrap.dedent("""\
            #pragma once
            inline int v = 1;
            namespace detail { inline int v = 2; }
        """)
        pxd = render_cpp(backend, source)

        assert "int v\n" in pxd
        assert 'int detail_v "detail::v"' in pxd

    @requires_cxx_toolchain
    def test_both_declarations_of_every_kind_execute(self, tmp_path: Path) -> None:
        """Each of the four symbols is read, and its own value checked.

        The variable case is why executing is not enough on its own: before the
        fix the binding built, imported and ran, returning a plausible
        ``222.5`` from ``b::v`` while ``a::v`` was unreachable. The generated
        C++ is therefore also checked for all four qualified names.
        """
        pxd = render_cpp(get_backend("libclang"), _COLLIDING_NAMESPACES)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport a_v, b_v, X, Y

            def run():
                return (a_v, b_v, <int>X, <int>Y)
        """)

        assert build_and_run_cpp(tmp_path, _COLLIDING_NAMESPACES, pxd, pyx) == "(111, 222.5, 1, 2)"

        generated = (tmp_path / "use.cpp").read_text()
        for qualified in ("a::v", "b::v", "a::X", "b::Y"):
            assert qualified in generated, qualified


_COLLIDING_ENUMERATORS = textwrap.dedent("""\
    #pragma once
    namespace a { enum E { X = 7, UNIQ = 3 }; }
    namespace b { enum F { X = 9 }; }
""")

_ENUMERATOR_VERSUS_VARIABLE = textwrap.dedent("""\
    #pragma once
    namespace a { enum E { v = 11 }; }
    namespace b { inline int v = 5; }
""")


class TestCollidingEnumeratorsAreQualifiedIndependentlyOfTheirTag:
    """An enumerator of an unscoped enum is a namespace-scope name, not a tag member.

    ``a::E{X}`` beside ``b::F{X}`` has no tag collision, so neither enum was
    renamed and both enumerators reached the flat ``.pxd`` as a bare ``X``.
    That is not a compile error: Cython binds every use to whichever came
    first. Measured before the fix, ``a::E{v = 11}`` beside ``b::v = 5``
    cythonized, compiled, linked, imported and returned ``5`` -- a plausible
    value read from the wrong symbol, with no diagnostic from any tool.
    """

    def test_an_enumerator_colliding_across_namespaces_is_renamed(self, backend: Any) -> None:
        assert render_cpp(backend, _COLLIDING_ENUMERATORS) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum E "a::E":
                    a_X "a::X"
                    UNIQ "a::UNIQ"

                cdef enum F "b::F":
                    b_X "b::X"
        """)

    def test_the_tag_keeps_its_name_and_gains_only_a_cname(self, backend: Any) -> None:
        """``E`` and ``F`` do not collide, so only the block they lost is restored.

        Renaming the tags too would churn the public API for a collision that
        is not theirs.
        """
        pxd = render_cpp(backend, _COLLIDING_ENUMERATORS)
        assert 'cdef enum E "a::E":' in pxd
        assert 'cdef enum F "b::F":' in pxd
        assert "a_E" not in pxd
        assert "b_F" not in pxd

    def test_an_enumerator_unique_to_one_namespace_is_not_renamed(self, backend: Any) -> None:
        """Negative control. ``UNIQ`` is qualified because its block is gone, not renamed."""
        pxd = render_cpp(backend, _COLLIDING_ENUMERATORS)
        assert 'UNIQ "a::UNIQ"' in pxd
        assert "a_UNIQ" not in pxd

    def test_an_enum_with_no_collision_at_all_keeps_its_namespace_block(self, backend: Any) -> None:
        """Negative control: the scan must not pull every namespaced enum out of its block."""
        source = textwrap.dedent("""\
            #pragma once
            namespace a { enum Solo { S = 1 }; }
        """)
        assert render_cpp(backend, source) == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "a":

                cdef enum Solo:
                    S
        """)

    def test_a_global_enumerator_keeps_its_bare_name(self, backend: Any) -> None:
        """Negative control: the global enum is the public API; only the twin moves."""
        source = textwrap.dedent("""\
            #pragma once
            enum G { X = 1 };
            namespace a { enum E { X = 7 }; }
        """)
        assert render_cpp(backend, source) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum G:
                    X

                cdef enum E "a::E":
                    a_X "a::X"
        """)

    def test_an_enumerator_colliding_with_a_variable_is_disambiguated(self, backend: Any) -> None:
        """The enumerator collides with a non-enum declaration of the same name."""
        assert render_cpp(backend, _ENUMERATOR_VERSUS_VARIABLE) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum E "a::E":
                    a_v "a::v"

                int b_v "b::v"
        """)

    @requires_cxx_toolchain
    def test_colliding_enumerators_execute_with_their_own_values(self, tmp_path: Path) -> None:
        """Both enumerators are read and each value checked against its own header.

        Executing is not enough on its own here: before the fix this shape ran
        and returned a value, just not the right one. The generated C++ is
        therefore also checked for both qualified spellings.
        """
        pxd = render_cpp(get_backend("libclang"), _COLLIDING_ENUMERATORS)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport E, F, a_X, b_X, UNIQ

            def run():
                return (<int>a_X, <int>b_X, <int>UNIQ)
        """)

        assert build_and_run_cpp(tmp_path, _COLLIDING_ENUMERATORS, pxd, pyx) == "(7, 9, 3)"

        generated = (tmp_path / "use.cpp").read_text()
        for qualified in ("a::X", "b::X", "a::UNIQ"):
            assert qualified in generated, qualified

    @requires_cxx_toolchain
    def test_an_enumerator_and_a_variable_reach_their_own_symbols(self, tmp_path: Path) -> None:
        """The measured silent-wrong-value case: ``v`` returned ``5``, never ``11``."""
        pxd = render_cpp(get_backend("libclang"), _ENUMERATOR_VERSUS_VARIABLE)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport E, a_v, b_v

            def run():
                return (<int>a_v, b_v)
        """)

        assert build_and_run_cpp(tmp_path, _ENUMERATOR_VERSUS_VARIABLE, pxd, pyx) == "(11, 5)"

        generated = (tmp_path / "use.cpp").read_text()
        for qualified in ("a::v", "b::v"):
            assert qualified in generated, qualified

    @requires_cxx_toolchain
    def test_a_scoped_enumerator_is_spelled_through_its_tag(self, tmp_path: Path) -> None:
        """A scoped enumerator does not leak into its namespace, so ``a::X`` does not exist.

        This is not a regression from qualifying enumerators: measured on the
        unfixed writer, the plain ``namespace "a"`` block emitted a bare ``X``
        that Cython lowered to the same non-existent ``a::X``. Both paths failed
        identically, and both needed ``Enum.is_scoped``.

        The Cython names are tag-derived rather than namespace-derived. A scoped
        enumerator's scope *is* its tag, so keying the duplicate scan on the
        namespace put every bare ``X`` at global scope under one key and missed
        real duplicates; see
        ``test_a_global_scoped_enumerator_does_not_collide_with_an_unscoped_one``.
        """
        source = textwrap.dedent("""\
            #pragma once
            namespace a { enum class E { X = 7 }; }
            namespace b { enum class F { X = 9 }; }
        """)
        pxd = render_cpp(get_backend("libclang"), source)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport a_E_X, b_F_X

            def run():
                return (<int>a_E_X, <int>b_F_X)
        """)

        assert build_and_run_cpp(tmp_path, source, pxd, pyx) == "(7, 9)"

        generated = (tmp_path / "use.cpp").read_text()
        for qualified in ("a::E::X", "b::F::X"):
            assert qualified in generated, qualified


class TestScopedEnumsAreSpelledThroughTheirTag:
    """``enum class E { X }`` introduces ``E::X`` and no ``X``.

    Every case renders through both backends, because an IR flag only helps if
    both populate it. The unscoped case is the negative control: it must keep
    the plain ``namespace``-block form it had before ``is_scoped`` existed.
    """

    @staticmethod
    def _render(code: str) -> str:
        rendered = {name: render_cpp(get_backend(name), code) for name in ("libclang", "treesitter")}
        assert rendered["libclang"] == rendered["treesitter"], rendered
        return rendered["libclang"]

    def test_a_scoped_enum_in_a_namespace_qualifies_through_the_tag(self) -> None:
        assert self._render("namespace a { enum class E { X = 7 }; }\n") == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum E "a::E":
                    X "a::E::X"
        """)

    def test_an_unscoped_enum_in_a_namespace_is_unchanged(self) -> None:
        """Negative control: an unscoped enumerator *is* a namespace-scope name.

        ``a::E{X}`` is spelled ``a::X``, which the ``namespace`` block already
        supplies, so this must keep the bare form and gain no cname.
        """
        assert self._render("namespace a { enum E { X = 7 }; }\n") == textwrap.dedent("""\
            cdef extern from "test.hpp" namespace "a":

                cdef enum E:
                    X
        """)

    def test_a_scoped_enum_at_global_scope_qualifies_through_the_tag(self) -> None:
        assert self._render("enum class G { X = 3 };\n") == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum G:
                    X "G::X"
        """)

    def test_a_scoped_enum_with_an_explicit_underlying_type(self) -> None:
        """The underlying type does not change the spelling, and must not lose it."""
        assert self._render("enum class U : short { X = 4 };\n") == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum U:
                    X "U::X"
        """)

    def test_a_scoped_member_enum_qualifies_through_its_record_and_tag(self) -> None:
        """A hoisted member enum needs the record too: ``C::M::X``, not ``M::X``."""
        assert self._render("class C { public: enum class M { X = 5 }; };\n") == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum M "C::M":
                    X "C::M::X"

                cdef cppclass C
        """)

    def test_two_scoped_enums_in_different_namespaces_are_scoped_by_tag(self) -> None:
        """``a::E::X`` and ``b::F::X`` are unrelated names in C++.

        They do not collide *as C++*, so neither may be spelled ``a::X``. They
        do both reach the flat ``.pxd`` module as ``X``, where Cython binds
        every use to one of them without a diagnostic, so each still needs a
        distinct Cython name -- derived from its tag, which is its real scope.
        """
        code = textwrap.dedent("""\
            namespace a { enum class E { X = 7 }; }
            namespace b { enum class F { X = 9 }; }
        """)
        assert self._render(code) == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum E "a::E":
                    a_E_X "a::E::X"

                cdef enum F "b::F":
                    b_F_X "b::F::X"
        """)

    def test_a_global_scoped_enumerator_does_not_collide_with_an_unscoped_one(self) -> None:
        """``G::X`` and ``X`` are distinct in C++ and must stay distinct in the module.

        Both sit at global scope, so a duplicate scan keyed on the namespace put
        them under one key and renamed neither, and Cython reported ``'X'
        redeclared``.
        """
        assert self._render("enum class G { X = 11 };\nenum U { X = 13 };\n") == textwrap.dedent("""\
            cdef extern from "test.hpp":

                cdef enum G:
                    G_X "G::X"

                cdef enum U:
                    X
        """)

    @requires_cxx_toolchain
    def test_every_scoped_spelling_executes_with_its_own_value(self, tmp_path: Path) -> None:
        """Each enumerator is read and checked against the value its header declares.

        Executing alone proves little here: a wrong spelling that still resolves
        returns a plausible value from the wrong symbol. The values are all
        distinct and the generated C++ is checked for each qualified spelling.
        """
        source = textwrap.dedent("""\
            #pragma once
            namespace a { enum class E { X = 7 }; }
            enum class G : short { X = 11 };
            enum U { X = 13 };
            class D { public: enum class M { X = 31 }; };
        """)
        pxd = render_cpp(get_backend("libclang"), source)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport a_E_X, G_X, X, D_M_X

            def run():
                return (<int>a_E_X, <int>G_X, <int>X, <int>D_M_X)
        """)

        assert build_and_run_cpp(tmp_path, source, pxd, pyx) == "(7, 11, 13, 31)"

        generated = (tmp_path / "use.cpp").read_text()
        for qualified in ("a::E::X", "G::X", "D::M::X"):
            assert qualified in generated, qualified

    @requires_cxx_toolchain
    def test_both_member_enums_of_two_classes_execute(self, tmp_path: Path) -> None:
        """``C2::E`` was dropped entirely, so its enumerator could not be read at all."""
        source = textwrap.dedent("""\
            #pragma once
            class C1 { public: enum E { A = 21 }; };
            class C2 { public: enum E { B = 22 }; };
        """)
        pxd = render_cpp(get_backend("libclang"), source)
        pyx = textwrap.dedent("""\
            # distutils: language = c++
            from m cimport A, B

            def run():
                return (<int>A, <int>B)
        """)

        assert build_and_run_cpp(tmp_path, source, pxd, pyx) == "(21, 22)"

        generated = (tmp_path / "use.cpp").read_text()
        for qualified in ("C1::E::A", "C2::E::B"):
            assert qualified in generated, qualified


class TestCppModeFollowsTheFileExtension:
    """clang infers C++ from a ``.hpp``, and the backend must agree with it.

    Deriving C++ mode from the flags alone made a ``.hpp`` parse as C++ while
    the backend believed it held C, so every C++-only guard stayed shut. The
    observable consequence is ``_enum_is_typedef_only``: C++ has no separate tag
    namespace, so a ``typedef enum { ... } Name;`` is a ``cdef enum`` there and a
    ``ctypedef enum`` in C.
    """

    SOURCE = "typedef enum { A = 1 } Name;\n"

    @staticmethod
    def _enum_line(filename: str, extra_args: list[str] | None = None) -> str:
        header = get_backend("libclang").parse(
            TestCppModeFollowsTheFileExtension.SOURCE, filename, extra_args=extra_args
        )
        return next(line.strip() for line in write_pxd(header).splitlines() if "enum" in line)

    @pytest.mark.parametrize("extension", [".hpp", ".hh", ".hxx", ".H"])
    def test_a_cpp_extension_parses_as_cpp_without_any_flag(self, extension: str) -> None:
        assert self._enum_line(f"m{extension}") == "cdef enum Name:"

    def test_a_dot_h_stays_c(self) -> None:
        """Negative control: ``.h`` is ambiguous and clang reads it as C."""
        assert self._enum_line("m.h") == "ctypedef enum Name:"

    def test_an_explicit_x_cpp_still_promotes_a_dot_h(self) -> None:
        assert self._enum_line("m.h", ["-x", "c++"]) == "cdef enum Name:"

    def test_an_explicit_x_c_forces_c_on_a_cpp_extension(self) -> None:
        """A flag wins over the extension in *both* directions, as clang's driver does."""
        assert self._enum_line("m.hpp", ["-x", "c"]) == "ctypedef enum Name:"

    def test_detection_is_independent_of_a_directory_named_like_a_header(self) -> None:
        """Only the file's own suffix decides; a ``.hpp`` parent directory does not."""
        assert _detect_cplus("/tmp/proj.hpp/m.h", None) is False
        assert _detect_cplus("/tmp/proj.h/m.hpp", None) is True


# ---------------------------------------------------------------------------
# Macro classification -- compile, import and execute
# ---------------------------------------------------------------------------

requires_toolchain_and_run = pytest.mark.skipif(
    not _HAS_CYTHON or _CC is None,
    reason=f"needs Cython (present={_HAS_CYTHON}) and a C compiler (found={_CC})",
)


def _cc_is_clang() -> bool:
    """Whether ``_CC`` is clang, so a clang-specific diagnostic may be asserted.

    ``cc`` is clang on macOS and real GCC on the Linux runners, and the two word
    the same error differently. A test that pins one compiler's sentence fails --
    it does not skip -- everywhere the wording differs, so the wording is only
    asserted where it is known to apply.
    """
    if _CC is None:
        return False
    probe = subprocess.run([_CC, "--version"], capture_output=True, text=True, check=False)  # noqa: S603
    return probe.returncode == 0 and "clang" in probe.stdout.lower()


_CC_IS_CLANG = _cc_is_clang()


def build_and_run_c(tmp_path: Path, header: str, pxd: str, pyx: str) -> Any:
    """Cythonize, compile, link, import and call a C extension.

    The C counterpart of :func:`build_and_run_cpp`.  Compiling alone would not
    settle a macro's classification: Cython emits no C for a constant nothing
    reads, so the ``.pyx`` must return the values and this helper returns what
    ``run()`` actually produced.
    """
    (tmp_path / "test.h").write_text(header)
    (tmp_path / "m.pxd").write_text(pxd)
    (tmp_path / "use.pyx").write_text(pyx)

    cython = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cython", "-3", "use.pyx", "-o", "use.c"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if cython.returncode != 0:
        raise ToolchainError(f"cython failed:\n{cython.stdout}\n{cython.stderr}")

    assert _CC is not None
    build = subprocess.run(  # noqa: S603
        link_extension_command(_CC, ["use.c"], "use", includes=[PYTHON_INCLUDE_DIR, tmp_path]),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise ToolchainError(f"C build failed:\n{build.stdout}\n{build.stderr}")

    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import use; print(repr(use.run()))"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = describe_load_dependencies(tmp_path / extension_filename("use"))
        raise ToolchainError(f"import/execute failed:\n{probe.stdout}\n{probe.stderr}{detail}")
    return probe.stdout.strip()


# The macros carry an ``HK_`` prefix because the generated extension includes
# ``Python.h``, which defines ``PyMODINIT_FUNC``, ``SIZEOF_INT`` and ``PyObject``
# itself; a collision would make the C step fail for a reason that has nothing to
# do with the classification under test.
_MACRO_HEADER = textwrap.dedent("""\
    typedef struct HKObject HKObject;
    #define HK_MODINIT __declspec(dllexport) HKObject *  /* PyMODINIT_FUNC, renamed */
    #define HK_REAL_CONST 42
    #define HK_CAST_CONST ((int)0x1F)
    #define HK_SIZEOF_INT sizeof(int)
    #define HK_MASKED ((unsigned)1)
    static int answer(void) { return 41; }
""")

_MACRO_PYX = textwrap.dedent("""\
    from m cimport HK_CAST_CONST, HK_MASKED, HK_REAL_CONST, HK_SIZEOF_INT, answer

    def run():
        return (HK_CAST_CONST, HK_SIZEOF_INT, HK_MASKED, HK_REAL_CONST, answer())
""")


class TestMacroClassificationCompiles:
    """The macro classification is settled by the C compiler, not by the IR.

    A declaration-specifier macro emitted as an ``int`` constant makes Cython
    generate ``__Pyx_PyLong_From_int(HK_MODINIT)``, which expands to
    ``__declspec(dllexport) HKObject *`` in expression position and the C
    compiler rejects.  A cast or ``sizeof`` macro emitted the same way compiles
    and yields the right value, so the two cannot be told apart by "contains a
    keyword" -- only by whether the tokens form an expression.
    """

    @requires_toolchain_and_run
    def test_cast_and_sizeof_macros_reach_python_with_correct_values(self, tmp_path: Path) -> None:
        """``((int)0x1F)``, ``sizeof(int)`` and ``((unsigned)1)`` survive and are right.

        Executing is the point: the values come from the compiled C, so a macro
        that had been dropped from the ``.pxd`` would fail to cimport, and one
        emitted with a wrong spelling would fail to compile.
        """
        pxd = render(get_backend("libclang"), _MACRO_HEADER)
        assert "HK_MODINIT" not in pxd
        assert build_and_run_c(tmp_path, _MACRO_HEADER, pxd, _MACRO_PYX) == "(31, 4, 1, 42, 41)"

    @requires_toolchain_and_run
    def test_declaration_specifier_macro_would_fail_to_compile(self, tmp_path: Path) -> None:
        """The planted failure: declaring the macro breaks the build.

        Without this the passing test above would prove only that the toolchain
        runs.  The ``.pxd`` is hand-written precisely because the backend no
        longer produces this line -- that is what makes it a control.
        """
        pxd = textwrap.dedent("""\
            cdef extern from "test.h":

                int HK_MODINIT
        """)
        pyx = textwrap.dedent("""\
            from m cimport HK_MODINIT

            def run():
                return HK_MODINIT
        """)
        with pytest.raises(ToolchainError) as exc:
            build_and_run_c(tmp_path, _MACRO_HEADER, pxd, pyx)
        message = str(exc.value)
        # The cause is named, so the control cannot pass for an unrelated reason --
        # a header collision, a missing symbol, a broken toolchain. The *cause* is
        # portable; the sentence is not. Measured on the same source: Apple clang 21
        # says "use of undeclared identifier 'dllexport'"; the same clang under
        # -fms-extensions says "expected expression" and never names the identifier
        # in the error line; GCC 16.2 says "'dllexport' undeclared here (not in a
        # function)". `cc` is clang on macOS and real GCC on the Linux runners, so a
        # test pinning one of those sentences fails -- it does not skip -- on the
        # other. All three do name `dllexport` somewhere in the expansion context.
        assert "C build failed" in message
        assert "dllexport" in message
        if _CC_IS_CLANG:
            assert "use of undeclared identifier 'dllexport'" in message

    @requires_toolchain_and_run
    def test_generated_c_reads_the_macro_as_an_int_not_a_sizeof(self, tmp_path: Path) -> None:
        """Names the mechanism exactly, so the docstrings above cannot drift.

        Cython lowers a ``cdef extern`` int constant through
        ``__Pyx_PyLong_From_int``; it emits no ``sizeof`` of the macro at all.
        """
        pxd = textwrap.dedent("""\
            cdef extern from "test.h":

                int HK_MODINIT
        """)
        pyx = textwrap.dedent("""\
            from m cimport HK_MODINIT

            def run():
                return HK_MODINIT
        """)
        (tmp_path / "test.h").write_text(_MACRO_HEADER)
        (tmp_path / "m.pxd").write_text(pxd)
        (tmp_path / "use.pyx").write_text(pyx)
        cython = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "cython", "-3", "use.pyx", "-o", "use.c"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert cython.returncode == 0, cython.stderr
        generated = (tmp_path / "use.c").read_text()
        assert "__Pyx_PyLong_From_int(HK_MODINIT)" in generated
        assert "sizeof(HK_MODINIT)" not in generated
