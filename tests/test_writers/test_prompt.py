"""Tests for the prompt writer."""

from __future__ import annotations

import json
import textwrap

import pytest

from headerkit.ir import (
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

# =============================================================================
# Compact mode tests
# =============================================================================


class TestCompactStruct:
    """Struct rendering in compact mode."""

    def test_struct_one_line(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nSTRUCT Point {x:int, y:int}\n"

    def test_opaque_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Struct("Handle", [])])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nSTRUCT Handle (opaque)\n"

    def test_packed_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nSTRUCT __packed Packed {x:int}\n"

    def test_union(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Data",
                    [Field("i", CType("int")), Field("f", CType("float"))],
                    is_union=True,
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nUNION Data {i:int, f:float}\n"

    def test_bitfield(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [Field("flags", CType("uint32"), bit_width=4)],
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nSTRUCT Flags {flags:uint32:4b}\n"


class TestCompactEnum:
    """Enum rendering in compact mode."""

    def test_enum_one_line(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Enum(
                    "Status",
                    [
                        EnumValue("OK", 0),
                        EnumValue("ERROR", 1),
                        EnumValue("TIMEOUT", 2),
                    ],
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nENUM Status: OK=0, ERROR=1, TIMEOUT=2\n"

    def test_enum_with_auto_values(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", None), EnumValue("GREEN", 1)])],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nENUM Color: RED, GREEN=1\n"


class TestCompactFunction:
    """Function rendering in compact mode."""

    def test_function_with_params_and_return(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Function(
                    "open",
                    Pointer(CType("Handle")),
                    [
                        Parameter("path", Pointer(CType("char", ["const"]))),
                        Parameter("flags", CType("int")),
                    ],
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nFUNC open(path:const char*, flags:int) -> Handle*\n"

    def test_variadic_function(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Function(
                    "printf",
                    CType("int"),
                    [Parameter("fmt", Pointer(CType("char", ["const"])))],
                    is_variadic=True,
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nFUNC printf(fmt:const char*, ...) -> int\n"

    def test_void_function(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Function("init", CType("void"), [])])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nFUNC init() -> void\n"


class TestCompactConstant:
    """Constant rendering in compact mode."""

    def test_constant(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("MYLIB_VERSION", 42)])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nCONST MYLIB_VERSION=42\n"

    def test_constant_string_value(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("VERSION", '"1.0"')])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == '// test.h (headerkit compact)\nCONST VERSION="1.0"\n'

    def test_constant_no_value(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("UNKNOWN", None)])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nCONST UNKNOWN=?\n"


class TestCompactTypedef:
    """Typedef rendering in compact mode."""

    def test_typedef_simple(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Typedef("myint", CType("int", ["unsigned"]))],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nTYPEDEF myint = unsigned int\n"

    def test_function_pointer_typedef_as_callback(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Typedef(
                    "EventCb",
                    FunctionPointer(
                        CType("void"),
                        [
                            Parameter("id", CType("int")),
                            Parameter("ctx", Pointer(CType("void"))),
                        ],
                    ),
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nCALLBACK EventCb(id:int, ctx:void*) -> void\n"


class TestCompactPointerToFunctionPointerTypedef:
    """Typedef of Pointer(FunctionPointer(...)) vs direct FunctionPointer."""

    def test_pointer_to_function_pointer_typedef(self) -> None:
        """Typedef of Pointer(FunctionPointer(...)) should be TYPEDEF, not CALLBACK.

        Only a direct FunctionPointer underlying type triggers the CALLBACK
        rendering. A Pointer wrapping a FunctionPointer falls through to
        the generic TYPEDEF path in _typedef_compact.
        """
        from headerkit.writers.prompt import PromptWriter

        fp = FunctionPointer(CType("void"), [Parameter("data", Pointer(CType("void")))])
        td = Typedef("EventCb", Pointer(fp))
        header = Header(path="test.h", declarations=[td])
        writer = PromptWriter(verbosity="compact")
        result = writer.write(header)
        lines = result.splitlines()
        assert lines[0] == "// test.h (headerkit compact)"
        assert lines[1].startswith("TYPEDEF")
        assert "CALLBACK" not in result


class TestCompactVariable:
    """Variable rendering in compact mode."""

    def test_variable(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Variable("count", CType("int"))])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nVAR count:int\n"


class TestCompactHeader:
    """Header-level compact rendering."""

    def test_header_comment(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("mylib.h", [])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output == "// mylib.h (headerkit compact)\n"


# =============================================================================
# Standard mode tests
# =============================================================================


class TestStandardMode:
    """Standard (YAML-like) mode rendering."""

    def test_header_comment(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("mylib.h", [])
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert output == "# mylib.h (headerkit standard)\n"

    def test_sections_grouped_by_type(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Constant("SIZE", 100),
                Enum("Color", [EnumValue("RED", 0)]),
                Struct("Point", [Field("x", CType("int"))]),
                Function("init", CType("void"), []),
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        # Sections appear in order
        const_pos = output.index("constants:")
        enum_pos = output.index("enums:")
        struct_pos = output.index("structs:")
        func_pos = output.index("functions:")
        assert const_pos < enum_pos < struct_pos < func_pos

    def test_empty_sections_omitted(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            functions:
              foo: () -> void
        """)
        assert output == expected

    def test_all_parameter_names_present(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Function(
                    "foo",
                    CType("int"),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("float")),
                    ],
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            functions:
              foo: (x: int, y: float) -> int
        """)
        assert output == expected

    def test_struct_fields_with_types(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            structs:
              Point:
                fields:
                  x: int
                  y: int
        """)
        assert output == expected

    def test_opaque_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Struct("Handle", [])])
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            structs:
              Handle:
                opaque: true
        """)
        assert output == expected

    def test_packed_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            structs:
              Packed:
                packed: true
                fields:
                  x: int
        """)
        assert output == expected

    def test_bitfield_in_standard(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [Field("flags", CType("uint32"), bit_width=4)],
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            structs:
              Flags:
                fields:
                  flags: uint32 (4 bits)
        """)
        assert output == expected

    def test_callback_section(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Typedef(
                    "Callback",
                    FunctionPointer(
                        CType("void"),
                        [Parameter("data", Pointer(CType("void")))],
                    ),
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            callbacks:
              Callback: (data: void*) -> void
        """)
        assert output == expected

    def test_variadic_function_standard(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Function(
                    "printf",
                    CType("int"),
                    [Parameter("fmt", Pointer(CType("char", ["const"])))],
                    is_variadic=True,
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            functions:
              printf: (fmt: const char*, ...) -> int
        """)
        assert output == expected


# =============================================================================
# Verbose mode tests
# =============================================================================


class TestVerboseMode:
    """Verbose mode (JSON with cross-references)."""

    def test_verbose_is_valid_json(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function(
                    "make_point",
                    CType("Point"),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("int")),
                    ],
                ),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)
        assert parsed["path"] == "test.h"
        assert "declarations" in parsed
        assert len(parsed["declarations"]) == 2
        # Verify declaration structure
        point_decl = parsed["declarations"][0]
        assert point_decl["kind"] == "struct"
        assert point_decl["name"] == "Point"
        assert len(point_decl["fields"]) == 2
        fn_decl = parsed["declarations"][1]
        assert fn_decl["kind"] == "function"
        assert fn_decl["name"] == "make_point"

    def test_cross_references_present(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function(
                    "make_point",
                    CType("Point"),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("int")),
                    ],
                ),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)

        # Point struct should have used_in referencing make_point
        point_decl = parsed["declarations"][0]
        assert point_decl["name"] == "Point"
        assert point_decl["used_in"] == ["make_point"]

    def test_cross_references_via_pointer(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Handle", []),
                Function(
                    "get_handle",
                    Pointer(CType("Handle")),
                    [],
                ),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)

        handle_decl = parsed["declarations"][0]
        assert handle_decl["name"] == "Handle"
        assert handle_decl["used_in"] == ["get_handle"]

    def test_no_cross_refs_when_not_referenced(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Unused", [Field("x", CType("int"))]),
                Function("foo", CType("void"), []),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)

        unused_decl = parsed["declarations"][0]
        assert unused_decl["name"] == "Unused"
        assert "used_in" not in unused_decl


# =============================================================================
# General tests
# =============================================================================


class TestPromptWriterGeneral:
    """General PromptWriter behavior."""

    def test_invalid_verbosity_raises(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        with pytest.raises(ValueError, match="Unknown verbosity"):
            PromptWriter(verbosity="extra")

    def test_default_verbosity_is_compact(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("X", 1)])
        writer = PromptWriter()
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nCONST X=1\n"

    def test_writer_protocol_compliance(self) -> None:
        from headerkit.writers import WriterBackend
        from headerkit.writers.prompt import PromptWriter

        writer = PromptWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        writer = PromptWriter()
        assert writer.name == "prompt"

    def test_writer_format_description(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        writer = PromptWriter()
        assert writer.format_description == "Token-optimized output for LLM context"

    def test_via_registry(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("prompt")
        header = Header("test.h", [Constant("X", 1)])
        output = writer.write(header)
        assert output == "// test.h (headerkit compact)\nCONST X=1\n"

    def test_via_registry_with_kwargs(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("prompt", verbosity="standard")
        header = Header("test.h", [Constant("X", 1)])
        output = writer.write(header)
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            constants:
              X: 1
        """)
        assert output == expected

    def test_token_ordering(self) -> None:
        """compact < standard < verbose token count for same header.

        This is a characterization test for emergent ordering, not a strict
        behavioral contract. The three verbosity tiers are designed to produce
        increasingly detailed output, so compact should always be the shortest
        and verbose the longest. If this test fails, it indicates a regression
        in the relative density of the verbosity tiers.
        """
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Constant("SIZE", 100),
                Enum(
                    "Status",
                    [EnumValue("OK", 0), EnumValue("ERROR", 1)],
                ),
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                ),
                Function(
                    "create",
                    Pointer(CType("Point")),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("int")),
                    ],
                ),
                Typedef("PointPtr", Pointer(CType("Point"))),
                Variable("origin", CType("Point")),
            ],
        )
        compact = PromptWriter(verbosity="compact").write(header)
        standard = PromptWriter(verbosity="standard").write(header)
        verbose = PromptWriter(verbosity="verbose").write(header)

        assert len(compact) < len(standard) < len(verbose)

    def _mixed_header(self) -> Header:
        """Build a header with all seven declaration types."""
        return Header(
            "test.h",
            [
                Constant("VER", 1),
                Enum("E", [EnumValue("A", 0)]),
                Struct("S", [Field("x", CType("int"))]),
                Function("f", CType("void"), [Parameter("a", CType("int"))]),
                Typedef("T", CType("int")),
                Variable("v", CType("int")),
                Typedef(
                    "Cb",
                    FunctionPointer(CType("void"), [Parameter("x", CType("int"))]),
                ),
            ],
        )

    def test_mixed_declarations_compact(self) -> None:
        """Compact mode renders all 7 declaration types."""
        from headerkit.writers.prompt import PromptWriter

        output = PromptWriter(verbosity="compact").write(self._mixed_header())
        expected = textwrap.dedent("""\
            // test.h (headerkit compact)
            CONST VER=1
            ENUM E: A=0
            STRUCT S {x:int}
            FUNC f(a:int) -> void
            TYPEDEF T = int
            VAR v:int
            CALLBACK Cb(x:int) -> void
        """)
        assert output == expected

    def test_mixed_declarations_standard(self) -> None:
        """Standard mode renders all section headers for 7 declaration types."""
        from headerkit.writers.prompt import PromptWriter

        output = PromptWriter(verbosity="standard").write(self._mixed_header())
        expected = textwrap.dedent("""\
            # test.h (headerkit standard)

            constants:
              VER: 1

            enums:
              E: {A: 0}

            structs:
              S:
                fields:
                  x: int

            callbacks:
              Cb: (x: int) -> void

            functions:
              f: (a: int) -> void

            typedefs:
              T: int

            variables:
              v: int
        """)
        assert output == expected

    def test_mixed_declarations_verbose(self) -> None:
        """Verbose mode serializes all 7 declaration types as valid JSON."""
        from headerkit.writers.prompt import PromptWriter

        output = PromptWriter(verbosity="verbose").write(self._mixed_header())
        parsed = json.loads(output)
        assert len(parsed["declarations"]) == 7
        names = {d["name"] for d in parsed["declarations"]}
        assert names == {"VER", "E", "S", "f", "T", "v", "Cb"}
