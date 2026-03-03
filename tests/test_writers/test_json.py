"""Tests for the JSON writer."""

import json

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
    SourceLocation,
    Struct,
    Typedef,
    Variable,
)


class TestHeaderToJson:
    """Tests for the header_to_json() function."""

    def test_empty_header(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [])
        result = json.loads(header_to_json(header))
        assert result == {"path": "test.h", "declarations": []}

    def test_function_declaration(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("int"), [Parameter("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "function",
            "name": "foo",
            "return_type": {"kind": "ctype", "name": "int"},
            "parameters": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
            "is_variadic": False,
        }

    def test_struct_with_fields(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Point",
            "fields": [
                {"name": "x", "type": {"kind": "ctype", "name": "int"}},
                {"name": "y", "type": {"kind": "ctype", "name": "int"}},
            ],
        }

    def test_union(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Data", [Field("i", CType("int"))], is_union=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "union",
            "name": "Data",
            "fields": [{"name": "i", "type": {"kind": "ctype", "name": "int"}}],
        }

    def test_enum_with_values(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "enum",
            "name": "Color",
            "values": [
                {"name": "RED", "value": 0},
                {"name": "GREEN", "value": 1},
            ],
        }

    def test_enum_value_auto_increment(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Enum("Flags", [EnumValue("A", None), EnumValue("B", 5)])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "enum",
            "name": "Flags",
            "values": [
                {"name": "A"},
                {"name": "B", "value": 5},
            ],
        }

    def test_typedef(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Typedef("myint", CType("int", ["unsigned"]))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "typedef",
            "name": "myint",
            "underlying_type": {
                "kind": "ctype",
                "name": "int",
                "qualifiers": ["unsigned"],
            },
        }

    def test_variable(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("count", CType("int"))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "variable",
            "name": "count",
            "type": {"kind": "ctype", "name": "int"},
        }

    def test_constant(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("SIZE", 100, is_macro=True)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "constant",
            "name": "SIZE",
            "value": 100,
            "is_macro": True,
        }

    def test_constant_with_type(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("MAX", 255, type=CType("int"))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "constant",
            "name": "MAX",
            "value": 255,
            "type": {"kind": "ctype", "name": "int"},
        }

    def test_constant_no_value(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("UNKNOWN", None)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {"kind": "constant", "name": "UNKNOWN"}

    def test_constant_not_macro_omits_is_macro(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("VAL", 42)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {"kind": "constant", "name": "VAL", "value": 42}

    def test_constant_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Constant("SIZE", 100, location=SourceLocation("test.h", 5))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 5}

    def test_pointer_type(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("ptr", Pointer(CType("int")))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "pointer",
            "pointee": {"kind": "ctype", "name": "int"},
        }

    def test_pointer_with_qualifiers(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("ptr", Pointer(CType("int"), ["const"]))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "pointer",
            "pointee": {"kind": "ctype", "name": "int"},
            "qualifiers": ["const"],
        }

    def test_array_type(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("arr", Array(CType("int"), 10))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "array",
            "element_type": {"kind": "ctype", "name": "int"},
            "size": 10,
        }

    def test_array_flexible(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("arr", Array(CType("char"), None))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "array",
            "element_type": {"kind": "ctype", "name": "char"},
        }

    def test_array_symbolic_size(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("buf", Array(CType("char"), "BUFFER_SIZE"))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "array",
            "element_type": {"kind": "ctype", "name": "char"},
            "size": "BUFFER_SIZE",
        }

    def test_function_pointer_type(self) -> None:
        from headerkit.writers.json import header_to_json

        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))], is_variadic=False)
        header = Header("test.h", [Variable("cb", fp)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "function_pointer",
            "return_type": {"kind": "ctype", "name": "void"},
            "parameters": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
            "is_variadic": False,
        }

    def test_nested_pointer(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("pp", Pointer(Pointer(CType("char"))))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "pointer",
            "pointee": {
                "kind": "pointer",
                "pointee": {"kind": "ctype", "name": "char"},
            },
        }

    def test_included_headers(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [], included_headers={"stdlib.h", "aaa.h"})
        result = json.loads(header_to_json(header))
        assert result["included_headers"] == ["aaa.h", "stdlib.h"]

    def test_indent_none_compact(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [])
        result = header_to_json(header, indent=None)
        assert "\n" not in result

    def test_indent_custom(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [])
        result = header_to_json(header, indent=4)
        # Should be valid JSON with 4-space indent
        parsed = json.loads(result)
        assert parsed == {"path": "test.h", "declarations": []}
        # Verify 4-space indent in raw output
        assert "    " in result

    def test_all_declaration_kinds_serialized(self) -> None:
        """All six declaration types serialize to JSON with correct kind labels."""
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [
                Struct("S", [Field("x", CType("int"))]),
                Enum("E", [EnumValue("A", 0)]),
                Function("f", CType("void"), []),
                Typedef("T", CType("int")),
                Variable("v", CType("int")),
                Constant("C", 42),
            ],
        )
        result = header_to_json(header)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert len(parsed["declarations"]) == 6
        kinds = {d["kind"] for d in parsed["declarations"]}
        assert kinds == {"struct", "enum", "function", "typedef", "variable", "constant"}

    def test_variadic_function(self) -> None:
        from headerkit.writers.json import header_to_json

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
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["is_variadic"] is True

    def test_function_with_namespace(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [], namespace="ns")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["namespace"] == "ns"

    def test_function_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [], location=SourceLocation("test.h", 42, 1))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 42, "column": 1}

    def test_function_omits_namespace_when_none(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "namespace" not in decl

    def test_enum_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", 0)], location=SourceLocation("test.h", 3))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 3}

    def test_typedef_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Typedef("myint", CType("int"), location=SourceLocation("test.h", 7))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 7}

    def test_variable_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Variable("count", CType("int"), location=SourceLocation("test.h", 12, 3))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 12, "column": 3}

    def test_anonymous_parameter(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [Parameter(None, CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        param = result["declarations"][0]["parameters"][0]
        assert "name" not in param
        assert param["type"] == {"kind": "ctype", "name": "int"}


class TestHeaderToJsonDict:
    """Tests for the header_to_json_dict() function."""

    def test_returns_dict(self) -> None:
        from headerkit.writers.json import header_to_json_dict

        header = Header("test.h", [Function("foo", CType("void"), [])])
        result = header_to_json_dict(header)
        assert isinstance(result, dict)
        assert result["path"] == "test.h"
        assert result["declarations"] == [
            {
                "kind": "function",
                "name": "foo",
                "return_type": {"kind": "ctype", "name": "void"},
                "parameters": [],
                "is_variadic": False,
            }
        ]

    def test_dict_has_expected_structure(self) -> None:
        """header_to_json_dict returns a dict with correct top-level structure."""
        from headerkit.writers.json import header_to_json_dict

        header = Header(
            "test.h",
            [
                Function("foo", CType("void"), []),
            ],
        )
        result = header_to_json_dict(header)
        assert result == {
            "path": "test.h",
            "declarations": [
                {
                    "kind": "function",
                    "name": "foo",
                    "return_type": {"kind": "ctype", "name": "void"},
                    "parameters": [],
                    "is_variadic": False,
                }
            ],
        }


class TestJsonWriter:
    """Tests for the JsonWriter class (protocol-compliant wrapper)."""

    def test_writer_protocol_compliance(self) -> None:
        from headerkit.writers import WriterBackend
        from headerkit.writers.json import JsonWriter

        writer = JsonWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self) -> None:
        from headerkit.writers.json import JsonWriter

        writer = JsonWriter()
        assert writer.name == "json"

    def test_writer_format_description(self) -> None:
        from headerkit.writers.json import JsonWriter

        writer = JsonWriter()
        assert writer.format_description == "JSON serialization of IR for inspection and tooling"

    def test_writer_default_indent(self) -> None:
        from headerkit.writers.json import JsonWriter

        header = Header("test.h", [Function("foo", CType("void"), [])])
        writer = JsonWriter()
        result = writer.write(header)
        parsed = json.loads(result)
        assert parsed["declarations"][0] == {
            "kind": "function",
            "name": "foo",
            "return_type": {"kind": "ctype", "name": "void"},
            "parameters": [],
            "is_variadic": False,
        }
        # Default indent=2 means 2-space indentation is present
        assert "  " in result

    def test_writer_custom_indent(self) -> None:
        from headerkit.writers.json import JsonWriter

        header = Header("test.h", [])
        writer = JsonWriter(indent=None)
        result = writer.write(header)
        assert "\n" not in result

    def test_via_registry(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("json")
        header = Header("test.h", [Function("bar", CType("int"), [])])
        result = writer.write(header)
        parsed = json.loads(result)
        assert parsed["declarations"][0] == {
            "kind": "function",
            "name": "bar",
            "return_type": {"kind": "ctype", "name": "int"},
            "parameters": [],
            "is_variadic": False,
        }

    def test_via_registry_with_kwargs(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("json", indent=None)
        header = Header("test.h", [])
        result = writer.write(header)
        assert "\n" not in result


class TestStructSerialization:
    """Detailed struct serialization edge cases."""

    def test_struct_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))], location=SourceLocation("test.h", 10, 5))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "S",
            "fields": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
            "location": {"file": "test.h", "line": 10, "column": 5},
        }

    def test_struct_with_location_no_column(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))], location=SourceLocation("test.h", 10))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "S",
            "fields": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
            "location": {"file": "test.h", "line": 10},
        }

    def test_cppclass(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Widget", [], is_cppclass=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Widget",
            "fields": [],
            "is_cppclass": True,
        }

    def test_struct_with_methods(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [
                Struct(
                    "Obj",
                    [Field("val", CType("int"))],
                    methods=[Function("get", CType("int"), [])],
                )
            ],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Obj",
            "fields": [{"name": "val", "type": {"kind": "ctype", "name": "int"}}],
            "methods": [
                {
                    "kind": "function",
                    "name": "get",
                    "return_type": {"kind": "ctype", "name": "int"},
                    "parameters": [],
                    "is_variadic": False,
                }
            ],
        }

    def test_struct_with_namespace(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Foo", [], namespace="ns")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Foo",
            "fields": [],
            "namespace": "ns",
        }

    def test_struct_with_template_params(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Vec", [], template_params=["T"])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Vec",
            "fields": [],
            "template_params": ["T"],
        }

    def test_struct_with_cpp_name(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Vec", [], cpp_name="std::vector")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Vec",
            "fields": [],
            "cpp_name": "std::vector",
        }

    def test_struct_with_notes(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [], notes=["opaque type"])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "S",
            "fields": [],
            "notes": ["opaque type"],
        }

    def test_struct_with_inner_typedefs(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [], inner_typedefs={"value_type": "int"})],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "S",
            "fields": [],
            "inner_typedefs": {"value_type": "int"},
        }

    def test_struct_omits_empty_optional_fields(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {"kind": "struct", "name": "S", "fields": []}

    def test_struct_is_typedef(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))], is_typedef=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "S",
            "fields": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
            "is_typedef": True,
        }

    def test_struct_anonymous(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct(None, [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": None,
            "fields": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
        }

    def test_struct_is_packed(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Packed",
            "fields": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
            "is_packed": True,
        }

    def test_struct_not_packed_omits_is_packed(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Normal", [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl == {
            "kind": "struct",
            "name": "Normal",
            "fields": [{"name": "x", "type": {"kind": "ctype", "name": "int"}}],
        }


class TestFieldSerialization:
    """Tests for Field serialization with new fields."""

    def test_field_with_bit_width(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Flags", [Field("flags", CType("unsigned int"), bit_width=4)])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert field == {
            "name": "flags",
            "type": {"kind": "ctype", "name": "unsigned int"},
            "bit_width": 4,
        }

    def test_field_without_bit_width_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert field == {"name": "x", "type": {"kind": "ctype", "name": "int"}}

    def test_field_with_anonymous_struct(self) -> None:
        from headerkit.writers.json import header_to_json

        anon = Struct(None, [Field("a", CType("int")), Field("b", CType("float"))])
        header = Header(
            "test.h",
            [Struct("Outer", [Field("inner", CType("int"), anonymous_struct=anon)])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert field == {
            "name": "inner",
            "type": {"kind": "ctype", "name": "int"},
            "anonymous_struct": {
                "kind": "struct",
                "name": None,
                "fields": [
                    {"name": "a", "type": {"kind": "ctype", "name": "int"}},
                    {"name": "b", "type": {"kind": "ctype", "name": "float"}},
                ],
            },
        }

    def test_field_with_anonymous_union(self) -> None:
        from headerkit.writers.json import header_to_json

        anon = Struct(None, [Field("i", CType("int")), Field("f", CType("float"))], is_union=True)
        header = Header(
            "test.h",
            [Struct("Data", [Field("u", CType("int"), anonymous_struct=anon)])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert field == {
            "name": "u",
            "type": {"kind": "ctype", "name": "int"},
            "anonymous_struct": {
                "kind": "union",
                "name": None,
                "fields": [
                    {"name": "i", "type": {"kind": "ctype", "name": "int"}},
                    {"name": "f", "type": {"kind": "ctype", "name": "float"}},
                ],
            },
        }

    def test_field_without_anonymous_struct_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert field == {"name": "x", "type": {"kind": "ctype", "name": "int"}}


class TestUnknownDeclarationFallback:
    """Test the 'unknown' kind fallback path in _decl_to_dict."""

    def test_unknown_declaration_kind(self) -> None:
        """Non-standard declaration types should produce kind='unknown' with repr."""
        from headerkit.writers.json import _decl_to_dict

        # Create a minimal object that is not one of the known IR declaration types.
        # _decl_to_dict checks isinstance for each known type and falls through
        # to the else branch for anything else.
        class CustomDecl:
            """A fake declaration type not in the IR union."""

            def __repr__(self) -> str:
                return "CustomDecl()"

        result = _decl_to_dict(CustomDecl())  # type: ignore[arg-type]
        assert result["kind"] == "unknown"
        assert result["repr"] == "CustomDecl()"


class TestCallingConventionSerialization:
    """Tests for calling convention serialization."""

    def test_function_with_calling_convention(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("WinMain", CType("int"), [], calling_convention="stdcall")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["calling_convention"] == "stdcall"

    def test_function_without_calling_convention_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "calling_convention" not in decl

    def test_function_pointer_with_calling_convention(self) -> None:
        from headerkit.writers.json import header_to_json

        fp = FunctionPointer(CType("void"), [], calling_convention="stdcall")
        header = Header("test.h", [Variable("cb", fp)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"]["calling_convention"] == "stdcall"

    def test_function_pointer_without_calling_convention_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        fp = FunctionPointer(CType("void"), [])
        header = Header("test.h", [Variable("cb", fp)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "calling_convention" not in decl["type"]
