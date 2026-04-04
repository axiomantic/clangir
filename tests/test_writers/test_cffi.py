"""Tests for the CFFI cdef writer."""

import textwrap
from pathlib import Path

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers.cffi import _format_field, _format_params, decl_to_cffi, header_to_cffi, type_to_cffi


class TestTypeToCffi:
    def test_simple_type(self):
        assert type_to_cffi(CType("int")) == "int"

    def test_qualified_type(self):
        assert type_to_cffi(CType("int", ["const"])) == "const int"

    def test_pointer(self):
        assert type_to_cffi(Pointer(CType("int"))) == "int *"

    def test_double_pointer(self):
        assert type_to_cffi(Pointer(Pointer(CType("char")))) == "char **"

    def test_array(self):
        assert type_to_cffi(Array(CType("int"), 10)) == "int[10]"

    def test_flexible_array(self):
        assert type_to_cffi(Array(CType("char"), None)) == "char[]"

    def test_function_pointer(self):
        fp = FunctionPointer(CType("void"), [])
        assert type_to_cffi(fp) == "void(*)(void)"

    def test_function_pointer_with_params(self):
        fp = FunctionPointer(
            CType("int"),
            [Parameter("x", CType("int")), Parameter(None, CType("float"))],
        )
        assert type_to_cffi(fp) == "int(*)(int x, float)"

    def test_pointer_to_function_pointer(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        result = type_to_cffi(Pointer(fp))
        assert result == "void(*)(int x)"

    def test_pointer_to_function_pointer_no_params(self):
        fp = FunctionPointer(CType("void"), [])
        result = type_to_cffi(Pointer(fp))
        assert result == "void(*)(void)"


class TestDeclToCffi:
    def test_struct_with_fields(self):
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        result = decl_to_cffi(s)
        assert result == "struct Point {\n    int x;\n    int y;\n};"

    def test_opaque_struct(self):
        s = Struct("Opaque", [])
        result = decl_to_cffi(s)
        assert result == "struct Opaque { ...; };"

    def test_typedef_struct(self):
        s = Struct("Point", [Field("x", CType("int"))], is_typedef=True)
        result = decl_to_cffi(s)
        assert result == "typedef struct Point {\n    int x;\n} Point;"

    def test_union(self):
        u = Struct("Data", [Field("i", CType("int"))], is_union=True)
        result = decl_to_cffi(u)
        assert result == "union Data {\n    int i;\n};"

    def test_enum(self):
        e = Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])
        result = decl_to_cffi(e)
        assert result == "enum Color {\n    RED = 0,\n    GREEN = 1,\n};"

    def test_function(self):
        f = Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])
        result = decl_to_cffi(f)
        assert result == "int add(int a, int b);"

    def test_variadic_function(self):
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        result = decl_to_cffi(f)
        assert result == "int printf(const char * fmt, ...);"

    def test_typedef(self):
        t = Typedef("myint", CType("unsigned int"))
        result = decl_to_cffi(t)
        assert result == "typedef unsigned int myint;"

    def test_constant_integer(self):
        c = Constant("SIZE", 100, is_macro=True)
        result = decl_to_cffi(c)
        assert result == "#define SIZE 100"

    def test_constant_non_integer_skipped(self):
        c = Constant("STR", '"hello"', is_macro=True)
        result = decl_to_cffi(c)
        assert result is None

    def test_constant_float_returns_none(self):
        """CFFI only supports integer constants; float values should return None."""
        c = Constant("PI", 3.14, is_macro=True)
        result = decl_to_cffi(c)
        assert result is None

    def test_unknown_declaration_type_returns_none(self):
        """decl_to_cffi should return None for unknown declaration types."""

        class FakeDecl:
            name = "fake"

        result = decl_to_cffi(FakeDecl())
        assert result is None

    def test_variable(self):
        v = Variable("count", CType("int"))
        result = decl_to_cffi(v)
        assert result == "int count;"

    def test_anonymous_struct_skipped(self):
        s = Struct(None, [Field("x", CType("int"))])
        result = decl_to_cffi(s)
        assert result is None

    def test_exclude_patterns(self):
        import re

        f = Function("_internal_func", CType("void"), [])
        result = decl_to_cffi(f, exclude_patterns=[re.compile(r"_internal_")])
        assert result is None


class TestFormatField:
    def test_array_field(self):
        f = Field("buf", Array(CType("char"), 256))
        result = _format_field(f)
        assert result == "    char buf[256];"

    def test_function_pointer_field(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        f = Field("callback", fp)
        result = _format_field(f)
        assert result == "    void (*callback)(int x);"

    def test_regular_field(self):
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"


class TestFormatParams:
    def test_array_parameter(self):
        params = [Parameter("buf", Array(CType("char"), 256))]
        result = _format_params(params, False)
        assert result == "char buf[256]"

    def test_function_pointer_parameter(self):
        fp = FunctionPointer(CType("int"), [Parameter("x", CType("float"))])
        params = [Parameter("cb", fp)]
        result = _format_params(params, False)
        assert result == "int (*cb)(float x)"

    def test_empty_params_void(self):
        result = _format_params([], False)
        assert result == "void"

    def test_variadic_only(self):
        result = _format_params([], True)
        assert result == "..."


class TestHeaderToCffi:
    def test_simple_header(self):
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            struct Point {
                int x;
                int y;
            };
            struct Point * get_point(void);""")

    def test_exclude_patterns(self):
        header = Header(
            "test.h",
            [
                Function("public_func", CType("void"), []),
                Function("_private_func", CType("void"), []),
            ],
        )
        result = header_to_cffi(header, exclude_patterns=[r"_private_"])
        assert result == "void public_func(void);"

    def test_opaque_struct_with_typedef_dedup(self):
        """Opaque struct + matching typedef should emit only the typedef."""
        header = Header(
            "test.h",
            [
                Struct("nng_socket", []),  # opaque
                Typedef("nng_socket", CType("struct nng_socket")),
            ],
        )
        result = header_to_cffi(header)
        assert result == "typedef struct nng_socket nng_socket;"

    def test_enum_typedef_pair_combined(self):
        """Enum + matching typedef should be combined into typedef enum."""
        header = Header(
            "test.h",
            [
                Enum("nng_pipe_ev", [EnumValue("NNG_PIPE_EV_ADD", 0), EnumValue("NNG_PIPE_EV_REM", 1)]),
                Typedef("nng_pipe_ev", CType("enum nng_pipe_ev")),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            typedef enum {
                NNG_PIPE_EV_ADD = 0,
                NNG_PIPE_EV_REM = 1,
            } nng_pipe_ev;""")

    def test_tag_kind_qualification(self):
        """Bare struct name in typedef should get 'struct' prefix."""
        header = Header(
            "test.h",
            [
                Struct("inner_s", [Field("x", CType("int"))]),
                Typedef("outer_t", CType("inner_s")),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            struct inner_s {
                int x;
            };
            typedef struct inner_s outer_t;""")


class TestBitfieldFormatting:
    def test_bitfield_regular_type(self):
        f = Field("flags", CType("unsigned int"), bit_width=4)
        result = _format_field(f)
        assert result == "    unsigned int flags : 4;"

    def test_bitfield_single_bit(self):
        f = Field("valid", CType("unsigned int"), bit_width=1)
        result = _format_field(f)
        assert result == "    unsigned int valid : 1;"

    def test_no_bitwidth_omits_suffix(self):
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"
        assert ":" not in result


class TestAnonymousStructField:
    def test_anonymous_struct_inline(self):
        anon = Struct(None, [Field("x", CType("int")), Field("y", CType("int"))])
        f = Field("pos", CType("int"), anonymous_struct=anon)
        result = _format_field(f)
        assert result == "    struct {\n        int x;\n        int y;\n    };"

    def test_anonymous_union_inline(self):
        anon = Struct(None, [Field("i", CType("int")), Field("f", CType("float"))], is_union=True)
        f = Field("data", CType("int"), anonymous_struct=anon)
        result = _format_field(f)
        assert result == "    union {\n        int i;\n        float f;\n    };"


class TestPackedStruct:
    def test_packed_struct_has_comment(self):
        s = Struct("Packed", [Field("x", CType("int"))], is_packed=True)
        result = decl_to_cffi(s)
        assert result == "/* packed */\nstruct Packed {\n    int x;\n};"

    def test_non_packed_struct_no_comment(self):
        s = Struct("Normal", [Field("x", CType("int"))])
        result = decl_to_cffi(s)
        assert result == "struct Normal {\n    int x;\n};"
        assert "/* packed */" not in result


class TestCallingConventionCffi:
    def test_function_calling_convention_omitted(self):
        f = Function("WinMain", CType("int"), [], calling_convention="stdcall")
        result = decl_to_cffi(f)
        assert result is not None
        assert "stdcall" not in result
        assert result == "int WinMain(void);"

    def test_function_pointer_calling_convention_omitted(self):
        fp = FunctionPointer(CType("void"), [], calling_convention="stdcall")
        result = type_to_cffi(fp)
        assert "stdcall" not in result
        assert result == "void(*)(void)"


class TestCffiWriter:
    """Tests for the CffiWriter class (protocol-compliant wrapper)."""

    def test_writer_produces_output_with_expected_content(self):
        """CffiWriter.write() should produce output with expected declarations."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function(
                    "get_point",
                    CType("int"),
                    [
                        Parameter("x", CType("float")),
                    ],
                ),
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
            ],
        )
        writer = CffiWriter()
        result = writer.write(header)
        assert result == textwrap.dedent("""\
            int get_point(float x);
            struct Point {
                int x;
                int y;
            };""")

    def test_writer_with_exclude_patterns(self):
        """CffiWriter should forward exclude_patterns to header_to_cffi."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            "test.h",
            [
                Function("public_func", CType("void"), []),
                Function("_private_func", CType("void"), []),
            ],
        )
        writer = CffiWriter(exclude_patterns=[r"_private_"])
        result = writer.write(header)
        assert result == "void public_func(void);"

    def test_writer_protocol_compliance(self):
        """CffiWriter should satisfy the WriterBackend protocol."""
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        # Verify required attributes exist and have correct types
        assert isinstance(writer.name, str)
        assert writer.name == "cffi"
        assert isinstance(writer.format_description, str)
        assert writer.format_description == "CFFI cdef declarations for ffibuilder.cdef()"
        # Verify write() produces string output
        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        result = writer.write(header)
        assert result == "void foo(void);"

    def test_writer_name(self):
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert writer.name == "cffi"

    def test_writer_format_description(self):
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert writer.format_description == "CFFI cdef declarations for ffibuilder.cdef()"


class TestDefinePatterns:
    """Tests for the define_patterns option on CffiWriter."""

    def test_define_patterns_basic(self):
        """Matched defines are appended as #define NAME ... lines."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("nng_alloc", CType("void"), []),
            ],
        )
        writer = CffiWriter(define_patterns=[r"NNG_FLAG_\w+"])
        writer._matched_defines = ["NNG_FLAG_ALLOC", "NNG_FLAG_NONBLOCK"]
        result = writer.write(header)
        assert "#define NNG_FLAG_ALLOC ..." in result
        assert "#define NNG_FLAG_NONBLOCK ..." in result
        # The function declaration should still be present
        assert "void nng_alloc(void);" in result

    def test_define_patterns_empty_matches(self):
        """When define_patterns is set but no defines match, no #define lines appear."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(define_patterns=[r"NONEXISTENT_\w+"])
        writer._matched_defines = []
        result = writer.write(header)
        assert "#define" not in result
        assert "void foo(void);" in result

    def test_define_patterns_none(self):
        """Without define_patterns, output is normal (no #define lines)."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("bar", CType("int"), [Parameter("x", CType("int"))]),
            ],
        )
        writer = CffiWriter()
        result = writer.write(header)
        assert "#define" not in result
        assert "int bar(int x);" in result


class TestMatchDefinesHelper:
    """Tests for the _match_defines() helper function."""

    def test_match_defines(self):
        """_match_defines scans source for #define names matching patterns."""
        from headerkit._generate import _match_defines

        source = textwrap.dedent("""\
            #define NNG_FLAG_ALLOC 1
            #define NNG_FLAG_NONBLOCK 2
            #define NNG_VERSION "1.0"
            #define OTHER_THING 42
        """)
        result = _match_defines(source, [r"NNG_FLAG_\w+"])
        assert result == ["NNG_FLAG_ALLOC", "NNG_FLAG_NONBLOCK"]

    def test_match_defines_no_matches(self):
        """Returns empty list when no defines match."""
        from headerkit._generate import _match_defines

        source = "#define FOO 1\n#define BAR 2\n"
        result = _match_defines(source, [r"NONEXISTENT_\w+"])
        assert result == []

    def test_match_defines_deduplicates(self):
        """Duplicate #define names are deduplicated."""
        from headerkit._generate import _match_defines

        source = "#define FOO 1\n#define FOO 2\n"
        result = _match_defines(source, [r"FOO"])
        assert result == ["FOO"]

    def test_match_defines_multiple_patterns(self):
        """Multiple patterns are all checked."""
        from headerkit._generate import _match_defines

        source = textwrap.dedent("""\
            #define AAA_ONE 1
            #define BBB_TWO 2
            #define CCC_THREE 3
        """)
        result = _match_defines(source, [r"AAA_\w+", r"CCC_\w+"])
        assert result == ["AAA_ONE", "CCC_THREE"]

    def test_match_defines_indented(self):
        """Indented #define lines are matched."""
        from headerkit._generate import _match_defines

        source = "  #define FOO 1\n\t#define BAR 2\n"
        result = _match_defines(source, [r"FOO", r"BAR"])
        assert result == ["FOO", "BAR"]

    def test_match_defines_space_after_hash(self):
        """Space between # and define is handled."""
        from headerkit._generate import _match_defines

        source = "# define FOO 1\n#  define BAR 2\n"
        result = _match_defines(source, [r"FOO", r"BAR"])
        assert result == ["FOO", "BAR"]


class TestPopulateMatchedDefinesIncludeResolution:
    """Tests for _populate_matched_defines resolving #include directives."""

    def test_resolves_includes_from_code(self, tmp_path: Path):
        """When code= contains #include, resolved files are scanned for defines."""
        from headerkit._generate import _populate_matched_defines
        from headerkit.writers.cffi import CffiWriter

        inc_dir = tmp_path / "include"
        inc_dir.mkdir()
        (inc_dir / "lib.h").write_text("#define LIB_FOO 1\n#define LIB_BAR 2\nint x;\n")

        writer = CffiWriter(define_patterns=[r"LIB_\w+"])
        _populate_matched_defines(
            writer,
            header_path=tmp_path / "umbrella.h",
            code="#include <lib.h>\n",
            include_dirs=[str(inc_dir)],
        )
        assert writer._matched_defines == ["LIB_FOO", "LIB_BAR"]

    def test_includes_and_inline_defines(self, tmp_path: Path):
        """Defines from both the code string and included files are matched."""
        from headerkit._generate import _populate_matched_defines
        from headerkit.writers.cffi import CffiWriter

        inc_dir = tmp_path / "include"
        inc_dir.mkdir()
        (inc_dir / "lib.h").write_text("#define LIB_FROM_HEADER 1\n")

        writer = CffiWriter(define_patterns=[r"LIB_\w+"])
        _populate_matched_defines(
            writer,
            header_path=tmp_path / "umbrella.h",
            code="#define LIB_FROM_CODE 99\n#include <lib.h>\n",
            include_dirs=[str(inc_dir)],
        )
        assert writer._matched_defines == ["LIB_FROM_CODE", "LIB_FROM_HEADER"]

    def test_no_include_dirs_skips_resolution(self):
        """Without include_dirs, #include directives in code are not resolved."""
        from headerkit._generate import _populate_matched_defines
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter(define_patterns=[r"MY_\w+"])
        _populate_matched_defines(
            writer,
            header_path=Path("fake.h"),
            code="#define MY_INLINE 1\n#include <nonexistent.h>\n",
            include_dirs=None,
        )
        assert writer._matched_defines == ["MY_INLINE"]

    def test_quoted_include(self, tmp_path: Path):
        """Quoted #include "file.h" directives are also resolved."""
        from headerkit._generate import _populate_matched_defines
        from headerkit.writers.cffi import CffiWriter

        inc_dir = tmp_path / "include"
        inc_dir.mkdir()
        (inc_dir / "lib.h").write_text("#define QUOTED_FLAG 42\n")

        writer = CffiWriter(define_patterns=[r"QUOTED_\w+"])
        _populate_matched_defines(
            writer,
            header_path=tmp_path / "umbrella.h",
            code='#include "lib.h"\n',
            include_dirs=[str(inc_dir)],
        )
        assert writer._matched_defines == ["QUOTED_FLAG"]


class TestExtraCdef:
    """Tests for the extra_cdef option on CffiWriter."""

    def test_extra_cdef_basic(self):
        """extra_cdef lines are appended verbatim to the output."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(extra_cdef=['extern "Python" void cb(void *);'])
        result = writer.write(header)
        assert 'extern "Python" void cb(void *);' in result
        assert "void foo(void);" in result

    def test_extra_cdef_multiple_lines(self):
        """Multiple extra_cdef lines are all appended."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(
            extra_cdef=[
                'extern "Python" void on_data(void *);',
                'extern "Python" void on_error(int);',
            ]
        )
        result = writer.write(header)
        assert 'extern "Python" void on_data(void *);' in result
        assert 'extern "Python" void on_error(int);' in result

    def test_extra_cdef_none(self):
        """Without extra_cdef, output is unchanged."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter()
        result = writer.write(header)
        assert result == "void foo(void);"
        assert "extern" not in result

    def test_define_patterns_and_extra_cdef_together(self):
        """When both are set: main cdef, then defines, then extra_cdef."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(
            define_patterns=[r"FLAG_\w+"],
            extra_cdef=['extern "Python" void cb(void *);'],
        )
        writer._matched_defines = ["FLAG_ONE", "FLAG_TWO"]
        result = writer.write(header)

        # Verify all parts present
        assert "void foo(void);" in result
        assert "#define FLAG_ONE ..." in result
        assert "#define FLAG_TWO ..." in result
        assert 'extern "Python" void cb(void *);' in result

        # Verify ordering: main output, then defines, then extra_cdef
        func_pos = result.index("void foo(void);")
        define_pos = result.index("#define FLAG_ONE ...")
        extra_pos = result.index('extern "Python" void cb(void *);')
        assert func_pos < define_pos < extra_pos
