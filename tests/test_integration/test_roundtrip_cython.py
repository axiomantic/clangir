"""Integration roundtrip tests: parse C headers with libclang -> IR -> Cython .pxd output.

Each test class covers a distinct declaration category. Assertions use exact equality
against the complete writer output to catch structural regressions (missing lines,
wrong indentation, wrong keyword escaping, etc.).

ESCAPE analysis is documented inline per test class.
"""

from __future__ import annotations

import textwrap

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.writers.cython import write_pxd

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


@pytest.fixture(scope="session")
def backend():
    """Get a libclang backend instance (shared across all tests in the session)."""
    return get_backend("libclang")


def parse_and_cython(backend, code: str) -> str:
    """Parse C code and convert to Cython .pxd string."""
    header = backend.parse(code, "test.h")
    return write_pxd(header)


# ===========================================================================
# Struct roundtrip
#
# ESCAPE analysis (TestCythonStructRoundtrip):
#   CLAIM: Struct declarations survive the libclang -> IR -> cython pipeline.
#   PATH:  libclang parses C -> Struct IR nodes -> PxdWriter._write_struct().
#   CHECK: Exact equality against full .pxd output.
#   MUTATION that each assertion catches:
#     - `assert output == ...` catches: wrong struct keyword (ctypedef vs cdef),
#       missing fields, wrong field type, wrong indentation, missing newlines,
#       wrong extern block header.
#   ESCAPE: A broken implementation that emits field names without types would
#     still fail the exact-equality assertion since "int x" != "x".
# ===========================================================================


class TestCythonStructRoundtrip:
    """Test parsing and converting struct declarations to Cython .pxd."""

    def test_simple_struct(self, backend):
        """Named struct with two int fields."""
        output = parse_and_cython(backend, "struct Point { int x; int y; };")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct Point:
                    int x
                    int y
        """)

    def test_opaque_struct(self, backend):
        """Forward declaration (no fields) -- emits forward-decl form without colon."""
        output = parse_and_cython(backend, "struct Handle;")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct Handle
        """)

    def test_anonymous_struct(self, backend):
        """typedef struct { ... } Name -- anonymous inner struct resolved to typedef name."""
        output = parse_and_cython(backend, "typedef struct { int x; } MyPoint;")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef struct MyPoint:
                    int x
        """)
        assert "(anonymous" not in output
        assert "(unnamed" not in output


# ===========================================================================
# Function roundtrip
#
# ESCAPE analysis (TestCythonFunctionRoundtrip):
#   CLAIM: Function signatures survive libclang -> IR -> cython pipeline.
#   PATH:  libclang parses C -> Function IR nodes -> PxdWriter._write_function().
#   CHECK: Exact equality against full .pxd output.
#   MUTATION:
#     - Wrong return type (e.g. "void" instead of "int") caught by exact match.
#     - Missing parameter name caught by exact match.
#     - Pointer spelled wrong (e.g. "int *data" vs "int* data") caught.
#   ESCAPE: A writer that omits all parameters would produce "int add()" which
#     does not equal "int add(int a, int b)". The exact assertion catches it.
# ===========================================================================


class TestCythonFunctionRoundtrip:
    """Test parsing and converting function declarations to Cython .pxd."""

    def test_simple_function(self, backend):
        """Two-parameter function with int return type."""
        output = parse_and_cython(backend, "int add(int a, int b);")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                int add(int a, int b)
        """)

    def test_void_function(self, backend):
        """void function with void parameter -- libclang strips the 'void' parameter."""
        output = parse_and_cython(backend, "void init(void);")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                void init()
        """)

    def test_pointer_param(self, backend):
        """Function with a pointer parameter -- pointer written as 'int* data' (no space before *)."""
        output = parse_and_cython(backend, "void process(int *data);")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                void process(int* data)
        """)


# ===========================================================================
# Enum roundtrip
#
# ESCAPE analysis (TestCythonEnumRoundtrip):
#   CLAIM: Enum declarations survive the pipeline. The cython writer emits enum
#          names only (not integer values) per Cython .pxd convention.
#   PATH:  libclang -> Enum IR (with values) -> PxdWriter._write_enum().
#   CHECK: Exact equality against full output.
#   MUTATION:
#     - A writer that uses "cpdef enum" instead of "cdef enum" is caught.
#     - A writer that emits "RED = 0" would not match "RED" (exact check).
#     - A writer that drops a value name is caught.
#   ESCAPE: Nothing reasonable escapes; the full output is asserted.
# ===========================================================================


class TestCythonEnumRoundtrip:
    """Test parsing and converting enum declarations to Cython .pxd."""

    def test_simple_enum(self, backend):
        """Named enum -- values are emitted as names only (cython convention)."""
        output = parse_and_cython(backend, "enum Color { RED = 0, GREEN = 1 };")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef enum Color:
                    RED
                    GREEN
        """)

    def test_typedef_enum(self, backend):
        """typedef enum -- libclang may separate the enum body and typedef alias.

        The typedef enum produces a 'cdef enum Switch' block (not 'ctypedef enum').
        The writer emits blank lines between the phases due to cycle-detection layout.
        """
        output = parse_and_cython(backend, "typedef enum { OFF = 0, ON = 1 } Switch;")
        # The typedef enum passes through the cycle-detection path in PxdWriter,
        # which emits blank separators between phases. The exact output has been
        # verified empirically by running the writer.
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":



                cdef enum Switch:
                    OFF
                    ON
        """)


# ===========================================================================
# Typedef roundtrip
#
# ESCAPE analysis (TestCythonTypedefRoundtrip):
#   CLAIM: Typedef declarations (simple and function-pointer) survive the pipeline.
#   PATH:  libclang -> Typedef IR -> PxdWriter._write_typedef().
#   CHECK: Exact equality against full output.
#   MUTATION:
#     - Wrong underlying type ("unsigned int" vs "int") caught.
#     - Missing typedef name caught.
#     - Wrong function pointer syntax caught.
#   ESCAPE: Nothing reasonable escapes; the full output is asserted.
# ===========================================================================


class TestCythonTypedefRoundtrip:
    """Test parsing and converting typedef declarations to Cython .pxd."""

    def test_simple_typedef(self, backend):
        """Simple scalar typedef."""
        output = parse_and_cython(backend, "typedef unsigned int uint32;")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef unsigned int uint32
        """)

    def test_function_pointer_typedef(self, backend):
        """Function pointer typedef -- parameter name may be stripped by libclang."""
        output = parse_and_cython(backend, "typedef void (*Callback)(int status);")
        # libclang strips parameter names from function pointer typedefs in some versions.
        # The writer emits the parameter type only ("int") without name ("status").
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef void (*Callback)(int)
        """)


# ===========================================================================
# Keyword collision roundtrip
#
# ESCAPE analysis (TestCythonKeywordCollision):
#   CLAIM: C identifiers that are Python/Cython keywords get escaped correctly:
#          - Parameters: keyword_ (no C-name alias, since params need no alias)
#          - Fields: keyword_ "keyword" (with C-name alias for correct C binding)
#   PATH:  libclang -> IR -> PxdWriter._escape_name(include_c_name=False/True).
#   CHECK: Exact equality against full output.
#   MUTATION:
#     - A writer that omits escaping ("api" not "api_") caught by exact check.
#     - A writer that adds C-name alias to params ('api_ "api"') caught because
#       output would contain the alias string that the exact check rejects.
#     - A writer that omits C-name alias from fields caught because output would
#       contain "namespace_" not 'namespace_ "namespace"'.
#   ESCAPE: Nothing reasonable escapes; the full output is asserted.
# ===========================================================================


class TestCythonKeywordCollision:
    """Test that C identifiers matching Cython keywords are properly escaped."""

    def test_param_named_api(self, backend):
        """Parameter named 'api' -> 'api_' with NO C-name alias (params don't need alias)."""
        output = parse_and_cython(backend, "void set_api(int api);")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                void set_api(int api_)
        """)
        # Explicit negative assertion: parameters must NOT get the C-name alias form
        assert 'api_ "api"' not in output

    def test_param_named_gil(self, backend):
        """Parameter named 'gil' -> 'gil_' with NO C-name alias."""
        output = parse_and_cython(backend, "void configure(int gil);")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                void configure(int gil_)
        """)

    def test_field_named_namespace(self, backend):
        """Struct field named 'namespace' -> 'namespace_ \"namespace\"' WITH C-name alias."""
        output = parse_and_cython(backend, "struct Config { int namespace; };")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct Config:
                    int namespace_ "namespace"
        """)

    def test_field_named_public(self, backend):
        """Struct field named 'public' -> 'public_ \"public\"' WITH C-name alias.

        'public' is not a C keyword so libclang accepts it as a field name in C mode.
        """
        try:
            output = parse_and_cython(backend, "struct Flags { int public; };")
        except RuntimeError:
            pytest.skip("libclang rejects 'public' as C field name in this config")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct Flags:
                    int public_ "public"
        """)


# ===========================================================================
# Empty and edge cases
#
# ESCAPE analysis (TestCythonEmptyAndEdgeCases):
#   CLAIM: Edge-case inputs (empty header, combined header) produce valid .pxd.
#   PATH:  libclang -> empty/mixed Header -> PxdWriter.write().
#   CHECK: Exact equality for empty header; substring containment checks for
#          the complete header test (which combines multiple declaration types
#          and is sensitive to topological sort order).
#   MUTATION for empty header:
#     - A writer that returns "" caught by exact equality (empty != "cdef extern...").
#     - A writer that emits wrong filename caught by exact equality.
#   MUTATION for complete header:
#     - A writer that drops any declaration type is caught (struct/function/enum
#       names are all asserted present).
#   ESCAPE (complete header): A writer that swaps field names (x vs y) would
#     still pass the complete-header test since we only assert name presence.
#     This is acceptable: the simple_struct test covers exact field output.
# ===========================================================================


class TestCythonEmptyAndEdgeCases:
    """Test edge cases: empty header and multi-declaration header."""

    def test_empty_header(self, backend):
        """Empty C source -> extern block with 'pass'."""
        output = parse_and_cython(backend, "")
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":
                pass
        """)

    def test_complete_header(self, backend):
        """Struct + function + enum in one header -- all three must appear."""
        code = "struct Point { int x; int y; };\nint add(int a, int b);\nenum Color { RED = 0, GREEN = 1 };\n"
        output = parse_and_cython(backend, code)
        assert output == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct Point:
                    int x
                    int y

                int add(int a, int b)

                cdef enum Color:
                    RED
                    GREEN
        """)
