"""Tests for JSON IR deserialization (round-trip invariant)."""

from __future__ import annotations

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
from headerkit.writers.json import header_to_json, header_to_json_dict


def _round_trip(header: Header) -> Header:
    """Serialize then deserialize a Header, returning the reconstructed copy."""
    from headerkit._ir_json import json_to_header

    d = header_to_json_dict(header)
    return json_to_header(d)


def _round_trip_json_str(header: Header) -> Header:
    """Round-trip through JSON string (not dict)."""
    from headerkit._ir_json import json_to_header

    s = header_to_json(header)
    return json_to_header(s)


class TestRoundTripTypes:
    """Round-trip tests for each TypeExpr variant."""

    def test_ctype_simple(self) -> None:
        h = Header("t.h", [Variable("x", CType("int"))])
        assert _round_trip(h) == h

    def test_ctype_with_qualifiers(self) -> None:
        h = Header("t.h", [Variable("x", CType("long", ["unsigned", "const"]))])
        assert _round_trip(h) == h

    def test_pointer(self) -> None:
        h = Header("t.h", [Variable("p", Pointer(CType("char")))])
        assert _round_trip(h) == h

    def test_pointer_with_qualifiers(self) -> None:
        h = Header("t.h", [Variable("p", Pointer(CType("int"), ["const"]))])
        assert _round_trip(h) == h

    def test_nested_pointer(self) -> None:
        h = Header("t.h", [Variable("pp", Pointer(Pointer(CType("char"))))])
        assert _round_trip(h) == h

    def test_array_fixed(self) -> None:
        h = Header("t.h", [Variable("a", Array(CType("int"), 10))])
        assert _round_trip(h) == h

    def test_array_flexible(self) -> None:
        h = Header("t.h", [Variable("a", Array(CType("char"), None))])
        assert _round_trip(h) == h

    def test_array_symbolic(self) -> None:
        h = Header("t.h", [Variable("a", Array(CType("char"), "BUFSIZ"))])
        assert _round_trip(h) == h

    def test_function_pointer_simple(self) -> None:
        fp = FunctionPointer(CType("void"), [])
        h = Header("t.h", [Typedef("cb", fp)])
        assert _round_trip(h) == h

    def test_function_pointer_with_params(self) -> None:
        fp = FunctionPointer(
            CType("int"),
            [Parameter("a", CType("int")), Parameter(None, Pointer(CType("void")))],
            is_variadic=True,
            calling_convention="stdcall",
        )
        h = Header("t.h", [Typedef("cb", fp)])
        assert _round_trip(h) == h


class TestRoundTripDeclarations:
    """Round-trip tests for each Declaration variant."""

    def test_struct_simple(self) -> None:
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_anonymous(self) -> None:
        s = Struct(None, [Field("val", CType("int"))])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_union(self) -> None:
        u = Struct("Data", [Field("i", CType("int"))], is_union=True)
        h = Header("t.h", [u])
        assert _round_trip(h) == h

    def test_struct_cppclass_with_methods(self) -> None:
        method = Function("resize", CType("void"), [Parameter("w", CType("int"))])
        s = Struct(
            "Widget",
            [Field("width", CType("int"))],
            methods=[method],
            is_cppclass=True,
            namespace="ui",
            template_params=["T"],
            cpp_name="Widget<T>",
        )
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_packed_with_notes(self) -> None:
        s = Struct(
            "Packed",
            [Field("a", CType("char"))],
            is_packed=True,
            notes=["alignment: 1"],
            inner_typedefs={"size_type": "unsigned int"},
        )
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_typedef(self) -> None:
        s = Struct("S", [], is_typedef=True)
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_field_with_bitwidth(self) -> None:
        s = Struct("Bits", [Field("flags", CType("uint32_t"), bit_width=4)])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_field_with_anonymous_struct(self) -> None:
        inner = Struct(None, [Field("x", CType("int"))], is_union=False)
        s = Struct("Outer", [Field("pos", CType("void"), anonymous_struct=inner)])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_enum_simple(self) -> None:
        e = Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_enum_anonymous(self) -> None:
        e = Enum(None, [EnumValue("A", None)])
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_enum_typedef(self) -> None:
        e = Enum("E", [], is_typedef=True)
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_enum_expression_value(self) -> None:
        e = Enum("Flags", [EnumValue("MASK", "FLAG_A | FLAG_B")])
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_function_simple(self) -> None:
        f = Function("add", CType("int"), [Parameter("a", CType("int"))])
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_function_variadic(self) -> None:
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
            calling_convention="cdecl",
            namespace="std",
        )
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_typedef(self) -> None:
        td = Typedef("size_t", CType("long", ["unsigned"]))
        h = Header("t.h", [td])
        assert _round_trip(h) == h

    def test_variable(self) -> None:
        v = Variable("count", CType("int"))
        h = Header("t.h", [v])
        assert _round_trip(h) == h

    def test_constant_macro(self) -> None:
        c = Constant("SIZE", 100, is_macro=True)
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_typed(self) -> None:
        c = Constant("MAX", 255, type=CType("int"))
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_string_value(self) -> None:
        c = Constant("VER", '"1.0"', is_macro=True)
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_float_value(self) -> None:
        c = Constant("PI", 3.14, type=CType("double"))
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_no_value(self) -> None:
        c = Constant("UNKNOWN", None)
        h = Header("t.h", [c])
        assert _round_trip(h) == h


class TestRoundTripSourceLocation:
    """Round-trip tests for SourceLocation on declarations."""

    def test_location_with_column(self) -> None:
        f = Function("foo", CType("void"), location=SourceLocation("x.h", 10, 5))
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_location_without_column(self) -> None:
        f = Function("foo", CType("void"), location=SourceLocation("x.h", 10))
        h = Header("t.h", [f])
        assert _round_trip(h) == h


class TestRoundTripHeader:
    """Round-trip tests for Header-level fields."""

    def test_included_headers(self) -> None:
        h = Header("t.h", [], included_headers={"stdio.h", "stdlib.h"})
        assert _round_trip(h) == h

    def test_empty_header(self) -> None:
        h = Header("t.h", [])
        assert _round_trip(h) == h

    def test_json_string_round_trip(self) -> None:
        h = Header(
            "t.h",
            [Function("f", CType("void")), Variable("v", CType("int"))],
        )
        assert _round_trip_json_str(h) == h

    def test_comprehensive(self) -> None:
        """Round-trip a Header with every declaration type."""
        h = Header(
            path="all.h",
            declarations=[
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                    location=SourceLocation("all.h", 1, 1),
                ),
                Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)]),
                Function(
                    "add",
                    CType("int"),
                    [Parameter("a", CType("int")), Parameter("b", CType("int"))],
                ),
                Typedef("size_t", CType("long", ["unsigned"])),
                Variable("count", CType("int")),
                Constant("MAX", 1024, is_macro=True),
            ],
            included_headers={"stdio.h"},
        )
        assert _round_trip(h) == h


class TestErrorHandling:
    """Tests for deserialization error cases."""

    def test_invalid_type(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        with pytest.raises(ValueError, match="Expected dict"):
            json_to_header([1, 2, 3])  # type: ignore[arg-type]

    def test_missing_kind_in_type(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {"path": "t.h", "declarations": [{"kind": "variable", "name": "x", "type": {}}]}
        with pytest.raises(ValueError, match="missing 'kind'"):
            json_to_header(d)

    def test_unknown_type_kind(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {
            "path": "t.h",
            "declarations": [{"kind": "variable", "name": "x", "type": {"kind": "bogus"}}],
        }
        with pytest.raises(ValueError, match="Unknown type kind"):
            json_to_header(d)

    def test_unknown_decl_kind(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {"path": "t.h", "declarations": [{"kind": "bogus"}]}
        with pytest.raises(ValueError, match="Unknown declaration kind"):
            json_to_header(d)

    def test_missing_kind_in_decl(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {"path": "t.h", "declarations": [{"name": "x"}]}
        with pytest.raises(ValueError, match="missing 'kind'"):
            json_to_header(d)
