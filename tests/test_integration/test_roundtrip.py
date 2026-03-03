"""Integration roundtrip tests: parse C headers with libclang -> IR -> writer output.

These tests verify the complete pipeline from C source code through the
libclang parser backend to CFFI-compatible cdef output and JSON dict output.
"""

from __future__ import annotations

import textwrap

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.writers.cffi import header_to_cffi
from headerkit.writers.json import header_to_json_dict

# Skip entire module if libclang is not available
pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


@pytest.fixture
def backend():
    """Get a libclang backend instance."""
    return get_backend("libclang")


def parse_and_convert(backend, code: str, exclude_patterns=None) -> str:
    """Parse C code and convert to CFFI cdef string."""
    header = backend.parse(code, "test.h")
    return header_to_cffi(header, exclude_patterns=exclude_patterns)


def parse_and_json_dict(backend, code: str) -> dict:
    """Parse C code and convert to JSON dict."""
    header = backend.parse(code, "test.h")
    return header_to_json_dict(header)


class TestVariableRoundtrip:
    """Test parsing and converting variable declarations."""

    def test_int_variable(self, backend):
        cdef = parse_and_convert(backend, "int x;")
        assert cdef == "int x;"

    def test_pointer_variable(self, backend):
        cdef = parse_and_convert(backend, "char *name;")
        assert cdef == "char * name;"


class TestStructRoundtrip:
    """Test parsing and converting struct declarations."""

    def test_simple_struct(self, backend):
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            struct Point {
                int x;
                int y;
            };""")

    def test_struct_with_pointer_field(self, backend):
        code = """
        struct Node {
            int value;
            struct Node *next;
        };
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            struct Node {
                int value;
                struct Node * next;
            };""")

    def test_typedef_struct(self, backend):
        code = """
        typedef struct {
            float r;
            float g;
            float b;
        } Color;
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            typedef struct Color {
                float r;
                float g;
                float b;
            } Color;""")


class TestFunctionRoundtrip:
    """Test parsing and converting function declarations."""

    def test_simple_function(self, backend):
        code = "int add(int a, int b);"
        cdef = parse_and_convert(backend, code)
        assert cdef == "int add(int a, int b);"

    def test_void_function(self, backend):
        code = "void do_nothing(void);"
        cdef = parse_and_convert(backend, code)
        assert cdef == "void do_nothing(void);"

    def test_pointer_return(self, backend):
        code = "char *get_name(void);"
        cdef = parse_and_convert(backend, code)
        assert cdef == "char * get_name(void);"

    def test_variadic_function(self, backend):
        code = "int printf(const char *fmt, ...);"
        cdef = parse_and_convert(backend, code)
        assert cdef == "int printf(const char * fmt, ...);"


class TestEnumRoundtrip:
    """Test parsing and converting enum declarations."""

    def test_simple_enum(self, backend):
        code = """
        enum Color {
            RED = 0,
            GREEN = 1,
            BLUE = 2
        };
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            enum Color {
                RED = 0,
                GREEN = 1,
                BLUE = 2,
            };""")

    def test_typedef_enum(self, backend):
        code = """
        typedef enum {
            OFF = 0,
            ON = 1
        } Switch;
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            typedef enum {
                OFF = 0,
                ON = 1,
            } Switch;""")


class TestTypedefRoundtrip:
    """Test parsing and converting typedef declarations."""

    def test_simple_typedef(self, backend):
        code = "typedef unsigned int uint32;"
        cdef = parse_and_convert(backend, code)
        assert cdef == "typedef unsigned int uint32;"

    def test_pointer_typedef(self, backend):
        code = "typedef void *handle_t;"
        cdef = parse_and_convert(backend, code)
        assert cdef == "typedef void * handle_t;"

    def test_function_pointer_typedef(self, backend):
        code = "typedef void (*callback_fn)(int status);"
        cdef = parse_and_convert(backend, code)
        # libclang does not preserve parameter names in function pointer typedefs;
        # the parameter name "status" is dropped and only the type "int" is kept.
        assert cdef == "typedef void (*callback_fn)(int);"


class TestUnionRoundtrip:
    """Test parsing and converting union declarations."""

    def test_simple_union(self, backend):
        code = """
        union Data {
            int i;
            float f;
            char c;
        };
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            union Data {
                int i;
                float f;
                char c;
            };""")


class TestMacroConstantRoundtrip:
    """Test parsing and converting macro constants."""

    def test_integer_macro(self, backend):
        code = "#define MAX_SIZE 1024\nvoid func(void);"
        cdef = parse_and_convert(backend, code)
        assert cdef == "#define MAX_SIZE 1024\nvoid func(void);"

    def test_non_integer_macro_skipped(self, backend):
        code = '#define VERSION "1.0"\nvoid func(void);'
        cdef = parse_and_convert(backend, code)
        # String macros are not supported by CFFI and must be omitted.
        assert cdef == "void func(void);"


class TestFunctionPointerTypedefRoundtrip:
    """Test parsing and converting function pointer typedefs."""

    def test_function_pointer_typedef(self, backend):
        code = "typedef int (*comparator_fn)(const void *a, const void *b);"
        cdef = parse_and_convert(backend, code)
        # libclang does not preserve parameter names in function pointer typedefs;
        # only the types (const void *) are retained.
        assert cdef == "typedef int (*comparator_fn)(const void *, const void *);"


class TestMultipleDeclarations:
    """Test parsing headers with multiple declarations."""

    def test_complete_header(self, backend):
        code = """
        typedef unsigned int mysize_t;

        struct Buffer {
            char *data;
            mysize_t length;
        };

        enum Status {
            OK = 0,
            ERROR = 1
        };

        struct Buffer *buffer_create(mysize_t size);
        void buffer_destroy(struct Buffer *buf);
        int buffer_write(struct Buffer *buf, const char *data, mysize_t len);
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            typedef unsigned int mysize_t;
            struct Buffer {
                char * data;
                mysize_t length;
            };
            enum Status {
                OK = 0,
                ERROR = 1,
            };
            struct Buffer * buffer_create(mysize_t size);
            void buffer_destroy(struct Buffer * buf);
            int buffer_write(struct Buffer * buf, const char * data, mysize_t len);""")

    def test_interdependent_types(self, backend):
        code = """
        struct Config {
            int flags;
        };

        typedef struct Config Config;

        void init(Config *cfg);
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            typedef struct Config {
                int flags;
            } Config;
            void init(struct Config * cfg);""")


class TestExcludePatterns:
    """Test that exclude_patterns work in the roundtrip pipeline."""

    def test_exclude_by_prefix(self, backend):
        code = """
        void public_func(void);
        void _private_func(void);
        void __internal_func(void);
        """
        cdef = parse_and_convert(backend, code, exclude_patterns=[r"^_"])
        assert cdef == "void public_func(void);"

    def test_exclude_by_pattern(self, backend):
        code = """
        void nng_open(void);
        void nng_close(void);
        void debug_print(void);
        """
        cdef = parse_and_convert(backend, code, exclude_patterns=[r"^debug_"])
        assert cdef == "void nng_open(void);\nvoid nng_close(void);"

    def test_no_exclude(self, backend):
        code = """
        void func_a(void);
        void func_b(void);
        """
        cdef = parse_and_convert(backend, code)
        assert cdef == "void func_a(void);\nvoid func_b(void);"


# =============================================================================
# JSON Writer Roundtrip Tests
# =============================================================================


class TestJsonVariableRoundtrip:
    """Test parsing and converting variable declarations to JSON."""

    def test_int_variable(self, backend):
        result = parse_and_json_dict(backend, "int x;")
        decls = result["declarations"]
        variables = [d for d in decls if d["kind"] == "variable"]
        assert len(variables) >= 1
        x_var = next(v for v in variables if v["name"] == "x")
        assert x_var["type"]["kind"] == "ctype"
        assert x_var["type"]["name"] == "int"

    def test_pointer_variable(self, backend):
        result = parse_and_json_dict(backend, "char *name;")
        decls = result["declarations"]
        variables = [d for d in decls if d["kind"] == "variable"]
        assert len(variables) >= 1
        name_var = next(v for v in variables if v["name"] == "name")
        assert name_var["type"]["kind"] == "pointer"
        assert name_var["type"]["pointee"]["kind"] == "ctype"
        assert name_var["type"]["pointee"]["name"] == "char"


class TestJsonStructRoundtrip:
    """Test parsing and converting struct declarations to JSON."""

    def test_simple_struct(self, backend):
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        structs = [d for d in decls if d["kind"] == "struct"]
        assert len(structs) >= 1
        point = next(s for s in structs if s["name"] == "Point")
        assert len(point["fields"]) == 2
        assert point["fields"][0]["name"] == "x"
        assert point["fields"][0]["type"]["name"] == "int"
        assert point["fields"][1]["name"] == "y"
        assert point["fields"][1]["type"]["name"] == "int"

    def test_struct_with_pointer_field(self, backend):
        code = """
        struct Node {
            int value;
            struct Node *next;
        };
        """
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        structs = [d for d in decls if d["kind"] == "struct"]
        assert len(structs) >= 1
        node = next(s for s in structs if s["name"] == "Node")
        assert len(node["fields"]) == 2
        assert node["fields"][0]["name"] == "value"
        assert node["fields"][0]["type"]["name"] == "int"
        assert node["fields"][1]["name"] == "next"
        assert node["fields"][1]["type"]["kind"] == "pointer"

    def test_typedef_struct(self, backend):
        code = """
        typedef struct {
            float r;
            float g;
            float b;
        } Color;
        """
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        structs = [d for d in decls if d["kind"] == "struct"]
        assert len(structs) >= 1
        color = next(s for s in structs if s["name"] == "Color")
        assert color["is_typedef"] is True
        assert len(color["fields"]) == 3
        field_names = [f["name"] for f in color["fields"]]
        assert field_names == ["r", "g", "b"]
        for f in color["fields"]:
            assert f["type"]["name"] == "float"


class TestJsonFunctionRoundtrip:
    """Test parsing and converting function declarations to JSON."""

    def test_simple_function(self, backend):
        code = "int add(int a, int b);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        functions = [d for d in decls if d["kind"] == "function"]
        assert len(functions) >= 1
        add_fn = next(f for f in functions if f["name"] == "add")
        assert add_fn["return_type"]["name"] == "int"
        assert len(add_fn["parameters"]) == 2
        assert add_fn["parameters"][0]["name"] == "a"
        assert add_fn["parameters"][0]["type"]["name"] == "int"
        assert add_fn["parameters"][1]["name"] == "b"
        assert add_fn["parameters"][1]["type"]["name"] == "int"
        assert add_fn["is_variadic"] is False

    def test_void_function(self, backend):
        code = "void do_nothing(void);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        functions = [d for d in decls if d["kind"] == "function"]
        assert len(functions) >= 1
        fn = next(f for f in functions if f["name"] == "do_nothing")
        assert fn["return_type"]["name"] == "void"
        assert len(fn["parameters"]) == 0
        assert fn["is_variadic"] is False

    def test_pointer_return(self, backend):
        code = "char *get_name(void);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        functions = [d for d in decls if d["kind"] == "function"]
        assert len(functions) >= 1
        fn = next(f for f in functions if f["name"] == "get_name")
        assert fn["return_type"]["kind"] == "pointer"
        assert fn["return_type"]["pointee"]["name"] == "char"

    def test_variadic_function(self, backend):
        code = "int printf(const char *fmt, ...);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        functions = [d for d in decls if d["kind"] == "function"]
        assert len(functions) >= 1
        fn = next(f for f in functions if f["name"] == "printf")
        assert fn["return_type"]["name"] == "int"
        assert fn["is_variadic"] is True
        assert len(fn["parameters"]) >= 1
        assert fn["parameters"][0]["type"]["kind"] == "pointer"
        pointee = fn["parameters"][0]["type"]["pointee"]
        assert pointee["kind"] == "ctype"
        assert pointee["name"] == "char"
        assert "const" in pointee.get("qualifiers", [])


class TestJsonEnumRoundtrip:
    """Test parsing and converting enum declarations to JSON."""

    def test_simple_enum(self, backend):
        code = """
        enum Color {
            RED = 0,
            GREEN = 1,
            BLUE = 2
        };
        """
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        enums = [d for d in decls if d["kind"] == "enum"]
        assert len(enums) >= 1
        color = next(e for e in enums if e["name"] == "Color")
        assert len(color["values"]) == 3
        value_map = {v["name"]: v["value"] for v in color["values"]}
        assert value_map["RED"] == 0
        assert value_map["GREEN"] == 1
        assert value_map["BLUE"] == 2

    def test_typedef_enum(self, backend):
        code = """
        typedef enum {
            OFF = 0,
            ON = 1
        } Switch;
        """
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        enums = [d for d in decls if d["kind"] == "enum"]
        assert len(enums) >= 1
        switch_enum = next(e for e in enums if e["name"] == "Switch")
        assert len(switch_enum["values"]) == 2
        value_map = {v["name"]: v["value"] for v in switch_enum["values"]}
        assert value_map["OFF"] == 0
        assert value_map["ON"] == 1
        # libclang emits a separate typedef declaration for typedef enums
        # alongside the enum itself. Both must be present.
        typedefs = [d for d in decls if d["kind"] == "typedef"]
        switch_td = [t for t in typedefs if t["name"] == "Switch"]
        assert len(switch_td) == 1, (
            f"Expected typedef 'Switch' in declarations, got typedefs: {[t['name'] for t in typedefs]}"
        )
        assert switch_td[0]["underlying_type"]["name"] == "enum Switch"


class TestJsonTypedefRoundtrip:
    """Test parsing and converting typedef declarations to JSON."""

    def test_simple_typedef(self, backend):
        code = "typedef unsigned int uint32;"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        typedefs = [d for d in decls if d["kind"] == "typedef"]
        assert len(typedefs) >= 1
        td = next(t for t in typedefs if t["name"] == "uint32")
        assert td["underlying_type"]["kind"] == "ctype"
        assert td["underlying_type"]["name"] == "unsigned int"

    def test_pointer_typedef(self, backend):
        code = "typedef void *handle_t;"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        typedefs = [d for d in decls if d["kind"] == "typedef"]
        assert len(typedefs) >= 1
        td = next(t for t in typedefs if t["name"] == "handle_t")
        assert td["underlying_type"]["kind"] == "pointer"
        assert td["underlying_type"]["pointee"]["name"] == "void"

    def test_function_pointer_typedef(self, backend):
        code = "typedef void (*callback_fn)(int status);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        typedefs = [d for d in decls if d["kind"] == "typedef"]
        assert len(typedefs) >= 1
        td = next(t for t in typedefs if t["name"] == "callback_fn")
        # Function pointer typedefs are represented as pointer -> function_pointer
        assert td["underlying_type"]["kind"] == "pointer"
        fn_ptr = td["underlying_type"]["pointee"]
        assert fn_ptr["kind"] == "function_pointer"
        assert fn_ptr["return_type"]["name"] == "void"
        assert len(fn_ptr["parameters"]) >= 1
        assert fn_ptr["is_variadic"] is False


class TestJsonUnionRoundtrip:
    """Test parsing and converting union declarations to JSON."""

    def test_simple_union(self, backend):
        code = """
        union Data {
            int i;
            float f;
            char c;
        };
        """
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        unions = [d for d in decls if d["kind"] == "union"]
        assert len(unions) >= 1
        data = next(u for u in unions if u["name"] == "Data")
        assert len(data["fields"]) == 3
        field_names = [f["name"] for f in data["fields"]]
        assert field_names == ["i", "f", "c"]
        # Verify field types
        field_map = {f["name"]: f["type"]["name"] for f in data["fields"]}
        assert field_map["i"] == "int"
        assert field_map["f"] == "float"
        assert field_map["c"] == "char"


class TestJsonMacroConstantRoundtrip:
    """Test parsing and converting macro constants to JSON."""

    def test_integer_macro(self, backend):
        code = "#define MAX_SIZE 1024\nvoid func(void);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        constants = [d for d in decls if d["kind"] == "constant"]
        assert len(constants) >= 1
        max_size = next(c for c in constants if c["name"] == "MAX_SIZE")
        assert max_size["value"] == 1024
        assert max_size["is_macro"] is True

    def test_non_integer_macro(self, backend):
        code = '#define VERSION "1.0"\nvoid func(void);'
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        constants = [d for d in decls if d["kind"] == "constant"]
        # The libclang backend extracts string macros as Constants with
        # type CType("char", ["const"]) and the raw string token as value.
        # JSON writer includes all Constants, so VERSION must appear.
        version_constants = [c for c in constants if c["name"] == "VERSION"]
        assert len(version_constants) == 1, (
            f"Expected string macro VERSION in JSON output, got constants: {[c['name'] for c in constants]}"
        )
        assert version_constants[0]["is_macro"] is True
        assert version_constants[0]["value"] == '"1.0"'


class TestJsonFunctionPointerTypedefRoundtrip:
    """Test parsing and converting function pointer typedefs to JSON."""

    def test_function_pointer_typedef(self, backend):
        code = "typedef int (*comparator_fn)(const void *a, const void *b);"
        result = parse_and_json_dict(backend, code)
        decls = result["declarations"]
        typedefs = [d for d in decls if d["kind"] == "typedef"]
        assert len(typedefs) >= 1
        td = next(t for t in typedefs if t["name"] == "comparator_fn")
        # Function pointer typedefs are represented as pointer -> function_pointer
        assert td["underlying_type"]["kind"] == "pointer"
        fn_ptr = td["underlying_type"]["pointee"]
        assert fn_ptr["kind"] == "function_pointer"
        assert fn_ptr["return_type"]["name"] == "int"
        assert len(fn_ptr["parameters"]) == 2
        assert fn_ptr["is_variadic"] is False
        # Verify parameter types are pointers to const void
        for param in fn_ptr["parameters"]:
            assert param["type"]["kind"] == "pointer"
            pointee = param["type"]["pointee"]
            assert pointee["kind"] == "ctype"
            assert pointee["name"] == "void"
            assert "const" in pointee.get("qualifiers", [])


@pytest.mark.libclang
class TestComplexPatternRoundtrip:
    """Test roundtrip for complex C patterns through CFFI output."""

    def test_bitfield_struct(self, backend):
        code = "struct Flags { unsigned int a : 3; unsigned int b : 5; };"
        cdef = parse_and_convert(backend, code)
        # The libclang backend does not extract bitfield widths (Field.bit_width
        # is never populated in _convert_field), so bitfield annotations are
        # absent from the CFFI output.
        assert cdef == textwrap.dedent("""\
            struct Flags {
                unsigned int a;
                unsigned int b;
            };""")

    def test_array_in_struct_field(self, backend):
        code = "struct Buffer { char data[256]; int sizes[4]; };"
        cdef = parse_and_convert(backend, code)
        assert cdef == textwrap.dedent("""\
            struct Buffer {
                char data[256];
                int sizes[4];
            };""")

    def test_nested_struct_field(self, backend):
        code = "struct Outer { struct Inner { int x; } inner; };"
        cdef = parse_and_convert(backend, code)
        # Inner is a nested type definition; the libclang backend does not hoist it
        # to a separate top-level declaration, so only Outer is emitted.
        assert cdef == textwrap.dedent("""\
            struct Outer {
                struct Inner inner;
            };""")
