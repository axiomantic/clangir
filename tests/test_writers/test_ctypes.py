"""Tests for the ctypes binding writer."""

import textwrap

import pytest

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
from headerkit.writers.base import module_level_bindings
from headerkit.writers.ctypes import (
    ABI_ALIGNMENT_NOTE,
    CTYPES_TYPE_MAP,
    CtypesWriter,
    _library_loader,
    header_to_ctypes,
    type_to_ctypes,
)


class TestTypeMapping:
    """Test C type -> ctypes type mapping for all common types."""

    def test_void(self) -> None:
        assert type_to_ctypes(CType("void")) == "None"

    def test_char(self) -> None:
        assert type_to_ctypes(CType("char")) == "ctypes.c_char"

    def test_signed_char(self) -> None:
        assert type_to_ctypes(CType("signed char")) == "ctypes.c_byte"

    def test_unsigned_char(self) -> None:
        assert type_to_ctypes(CType("unsigned char")) == "ctypes.c_ubyte"

    def test_short(self) -> None:
        assert type_to_ctypes(CType("short")) == "ctypes.c_short"

    def test_unsigned_short(self) -> None:
        assert type_to_ctypes(CType("unsigned short")) == "ctypes.c_ushort"

    def test_int(self) -> None:
        assert type_to_ctypes(CType("int")) == "ctypes.c_int"

    def test_unsigned_int(self) -> None:
        assert type_to_ctypes(CType("unsigned int")) == "ctypes.c_uint"

    def test_unsigned_int_via_qualifiers(self) -> None:
        assert type_to_ctypes(CType("int", ["unsigned"])) == "ctypes.c_uint"

    def test_long(self) -> None:
        assert type_to_ctypes(CType("long")) == "ctypes.c_long"

    def test_unsigned_long(self) -> None:
        assert type_to_ctypes(CType("unsigned long")) == "ctypes.c_ulong"

    def test_long_long(self) -> None:
        assert type_to_ctypes(CType("long long")) == "ctypes.c_longlong"

    def test_unsigned_long_long(self) -> None:
        assert type_to_ctypes(CType("unsigned long long")) == "ctypes.c_ulonglong"

    def test_float(self) -> None:
        assert type_to_ctypes(CType("float")) == "ctypes.c_float"

    def test_double(self) -> None:
        assert type_to_ctypes(CType("double")) == "ctypes.c_double"

    def test_long_double(self) -> None:
        assert type_to_ctypes(CType("long double")) == "ctypes.c_longdouble"

    def test_size_t(self) -> None:
        assert type_to_ctypes(CType("size_t")) == "ctypes.c_size_t"

    def test_ssize_t(self) -> None:
        assert type_to_ctypes(CType("ssize_t")) == "ctypes.c_ssize_t"

    def test_wchar_t(self) -> None:
        assert type_to_ctypes(CType("wchar_t")) == "ctypes.c_wchar"

    def test_bool_underscore(self) -> None:
        assert type_to_ctypes(CType("_Bool")) == "ctypes.c_bool"

    def test_bool(self) -> None:
        assert type_to_ctypes(CType("bool")) == "ctypes.c_bool"

    def test_int8_t(self) -> None:
        assert type_to_ctypes(CType("int8_t")) == "ctypes.c_int8"

    def test_int16_t(self) -> None:
        assert type_to_ctypes(CType("int16_t")) == "ctypes.c_int16"

    def test_int32_t(self) -> None:
        assert type_to_ctypes(CType("int32_t")) == "ctypes.c_int32"

    def test_int64_t(self) -> None:
        assert type_to_ctypes(CType("int64_t")) == "ctypes.c_int64"

    def test_uint8_t(self) -> None:
        assert type_to_ctypes(CType("uint8_t")) == "ctypes.c_uint8"

    def test_uint16_t(self) -> None:
        assert type_to_ctypes(CType("uint16_t")) == "ctypes.c_uint16"

    def test_uint32_t(self) -> None:
        assert type_to_ctypes(CType("uint32_t")) == "ctypes.c_uint32"

    def test_uint64_t(self) -> None:
        assert type_to_ctypes(CType("uint64_t")) == "ctypes.c_uint64"

    def test_const_int_maps_to_c_int(self) -> None:
        """const qualifier should be stripped for ctypes mapping."""
        assert type_to_ctypes(CType("int", ["const"])) == "ctypes.c_int"

    def test_unknown_type_passthrough(self) -> None:
        """Unknown types (user-defined) should pass through as-is."""
        assert type_to_ctypes(CType("MyStruct")) == "MyStruct"

    def test_type_map_completeness(self) -> None:
        """Verify the type map has all expected entries."""
        expected_types = {
            "void",
            "char",
            "signed char",
            "unsigned char",
            "short",
            "unsigned short",
            "int",
            "unsigned int",
            "long",
            "unsigned long",
            "long long",
            "unsigned long long",
            "float",
            "double",
            "long double",
            "size_t",
            "ssize_t",
            "wchar_t",
            "_Bool",
            "bool",
            "int8_t",
            "int16_t",
            "int32_t",
            "int64_t",
            "uint8_t",
            "uint16_t",
            "uint32_t",
            "uint64_t",
        }
        assert set(CTYPES_TYPE_MAP.keys()) == expected_types


class TestPointerTypes:
    def test_simple_pointer(self) -> None:
        assert type_to_ctypes(Pointer(CType("int"))) == "ctypes.POINTER(ctypes.c_int)"

    def test_double_pointer(self) -> None:
        result = type_to_ctypes(Pointer(Pointer(CType("int"))))
        assert result == "ctypes.POINTER(ctypes.POINTER(ctypes.c_int))"

    def test_const_char_pointer(self) -> None:
        """const char * should map to c_char_p."""
        result = type_to_ctypes(Pointer(CType("char", ["const"])))
        assert result == "ctypes.c_char_p"

    def test_char_pointer(self) -> None:
        """char * should map to c_char_p."""
        result = type_to_ctypes(Pointer(CType("char")))
        assert result == "ctypes.c_char_p"

    def test_void_pointer(self) -> None:
        """void * should map to c_void_p."""
        result = type_to_ctypes(Pointer(CType("void")))
        assert result == "ctypes.c_void_p"

    def test_struct_pointer(self) -> None:
        result = type_to_ctypes(Pointer(CType("MyStruct")))
        assert result == "ctypes.POINTER(MyStruct)"

    def test_pointer_to_function_pointer(self) -> None:
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        result = type_to_ctypes(Pointer(fp))
        assert result == "ctypes.CFUNCTYPE(None, ctypes.c_int)"


class TestArrayTypes:
    def test_fixed_array(self) -> None:
        result = type_to_ctypes(Array(CType("int"), 10))
        assert result == "ctypes.c_int * 10"

    def test_char_array(self) -> None:
        result = type_to_ctypes(Array(CType("char"), 64))
        assert result == "ctypes.c_char * 64"

    def test_flexible_array(self) -> None:
        result = type_to_ctypes(Array(CType("int"), None))
        assert result == "ctypes.POINTER(ctypes.c_int)"


class TestFunctionPointerTypes:
    def test_simple_function_pointer(self) -> None:
        fp = FunctionPointer(CType("void"), [])
        result = type_to_ctypes(fp)
        assert result == "ctypes.CFUNCTYPE(None)"

    def test_function_pointer_with_params(self) -> None:
        fp = FunctionPointer(
            CType("int"),
            [Parameter("a", CType("int")), Parameter("b", CType("float"))],
        )
        result = type_to_ctypes(fp)
        assert result == "ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_float)"

    def test_function_pointer_returning_pointer(self) -> None:
        fp = FunctionPointer(
            Pointer(CType("void")),
            [Parameter("size", CType("size_t"))],
        )
        result = type_to_ctypes(fp)
        assert result == "ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t)"


class TestStructToCtypes:
    def test_struct_with_simple_fields(self) -> None:
        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Point(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                    ("y", ctypes.c_int),
                ]
            """)
        assert result == expected

    def test_struct_with_bitfields(self) -> None:
        """Bitfields should use the 3-tuple format."""
        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [
                        Field("a", CType("unsigned int"), bit_width=4),
                        Field("b", CType("unsigned int"), bit_width=1),
                    ],
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Flags(ctypes.Structure):
                _fields_ = [
                    ("a", ctypes.c_uint, 4),
                    ("b", ctypes.c_uint, 1),
                ]
            """)
        assert result == expected

    def test_packed_struct(self) -> None:
        """Packed structs should have _pack_ = 1."""
        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Packed(ctypes.Structure):
                _pack_ = 1
                _fields_ = [
                    ("x", ctypes.c_int),
                ]
            """)
        assert result == expected

    def test_non_packed_struct_no_pack(self) -> None:
        header = Header(
            "test.h",
            [Struct("Normal", [Field("x", CType("int"))])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Normal(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                ]
            """)
        assert result == expected

    def test_union(self) -> None:
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
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Data(ctypes.Union):
                _fields_ = [
                    ("i", ctypes.c_int),
                    ("f", ctypes.c_float),
                ]
            """)
        assert result == expected

    def test_opaque_struct(self) -> None:
        """Opaque struct (no fields) should use 'pass'."""
        header = Header("test.h", [Struct("Opaque", [])])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Opaque(ctypes.Structure):
                pass
            """)
        assert result == expected

    def test_opaque_union(self) -> None:
        header = Header("test.h", [Struct("OpaqueU", [], is_union=True)])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class OpaqueU(ctypes.Union):
                pass
            """)
        assert result == expected

    def test_array_field(self) -> None:
        header = Header(
            "test.h",
            [Struct("Buf", [Field("data", Array(CType("char"), 64))])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Buf(ctypes.Structure):
                _fields_ = [
                    ("data", ctypes.c_char * 64),
                ]
            """)
        assert result == expected

    def test_pointer_field(self) -> None:
        header = Header(
            "test.h",
            [Struct("Node", [Field("next", Pointer(CType("Node")))])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Node(ctypes.Structure):
                _fields_ = [
                    ("next", ctypes.POINTER(Node)),
                ]
            """)
        assert result == expected

    def test_anonymous_struct_skipped(self) -> None:
        header = Header("test.h", [Struct(None, [Field("x", CType("int"))])])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys
            """)
        assert result == expected


class TestEnumToCtypes:
    def test_enum_as_constants(self) -> None:
        header = Header(
            "test.h",
            [
                Enum(
                    "Color",
                    [EnumValue("RED", 0), EnumValue("GREEN", 1), EnumValue("BLUE", 2)],
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Enums
            # ============================================================

            # enum Color
            RED = 0
            GREEN = 1
            BLUE = 2
            """)
        assert result == expected

    def test_anonymous_enum(self) -> None:
        header = Header(
            "test.h",
            [Enum(None, [EnumValue("FLAG_A", 1), EnumValue("FLAG_B", 2)])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Enums
            # ============================================================

            # enum anonymous
            FLAG_A = 1
            FLAG_B = 2
            """)
        assert result == expected

    def test_enum_auto_value(self) -> None:
        enum = Enum("AutoEnum", [EnumValue("FIRST", None), EnumValue("SECOND", 1)])
        result = header_to_ctypes(Header("test.h", [enum]))
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Enums
            # ============================================================

            # enum AutoEnum
            # FIRST = <auto>
            SECOND = 1
            """)
        assert result == expected

    def test_empty_enum_skipped(self) -> None:
        header = Header("test.h", [Enum("Empty", [])])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys
            """)
        assert result == expected


class TestFunctionPrototypes:
    def test_simple_function(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "add",
                    CType("int"),
                    [Parameter("a", CType("int")), Parameter("b", CType("int"))],
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.add.argtypes = [ctypes.c_int, ctypes.c_int]
            _lib.add.restype = ctypes.c_int
            """)
        assert result == expected

    def test_void_return(self) -> None:
        header = Header(
            "test.h",
            [Function("init", CType("void"), [])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.init.argtypes = []
            _lib.init.restype = None
            """)
        assert result == expected

    def test_no_args(self) -> None:
        header = Header(
            "test.h",
            [Function("get_count", CType("int"), [])],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.get_count.argtypes = []
            _lib.get_count.restype = ctypes.c_int
            """)
        assert result == expected

    def test_variadic_function(self) -> None:
        """Variadic functions should only annotate fixed args."""
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
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.printf.argtypes = [ctypes.c_char_p]
            _lib.printf.restype = ctypes.c_int
            """)
        assert result == expected

    def test_const_char_pointer_param(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "puts",
                    CType("int"),
                    [Parameter("s", Pointer(CType("char", ["const"])))],
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.puts.argtypes = [ctypes.c_char_p]
            _lib.puts.restype = ctypes.c_int
            """)
        assert result == expected

    def test_pointer_param(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "process",
                    CType("void"),
                    [Parameter("data", Pointer(CType("int")))],
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.process.argtypes = [ctypes.POINTER(ctypes.c_int)]
            _lib.process.restype = None
            """)
        assert result == expected

    def test_custom_lib_name(self) -> None:
        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = header_to_ctypes(header, lib_name="mylib")
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            mylib.foo.argtypes = []
            mylib.foo.restype = None
            """)
        assert result == expected

    def test_calling_convention_comment(self) -> None:
        header = Header(
            "test.h",
            [Function("WinMain", CType("int"), [], calling_convention="stdcall")],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            # calling convention: stdcall
            _lib.WinMain.argtypes = []
            _lib.WinMain.restype = ctypes.c_int
            """)
        assert result == expected


class TestFunctionPointerTypedef:
    def test_function_pointer_typedef(self) -> None:
        """Function pointer typedef should use CFUNCTYPE."""
        header = Header(
            "test.h",
            [
                Typedef(
                    "Callback",
                    Pointer(
                        FunctionPointer(
                            CType("void"),
                            [Parameter("data", Pointer(CType("void")))],
                        )
                    ),
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Typedefs
            # ============================================================

            Callback = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
            """)
        assert result == expected

    def test_direct_function_pointer_typedef(self) -> None:
        """Direct FunctionPointer typedef (without wrapping Pointer) should also work."""
        header = Header(
            "test.h",
            [
                Typedef(
                    "Handler",
                    FunctionPointer(
                        CType("int"),
                        [Parameter("code", CType("int"))],
                    ),
                )
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Typedefs
            # ============================================================

            Handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
            """)
        assert result == expected


class TestTypedefToCtypes:
    def test_struct_typedef_alias(self) -> None:
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int"))]),
                Typedef("Point_t", CType("struct Point")),
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Point(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                ]

            # ============================================================
            # Typedefs
            # ============================================================

            Point_t = Point
            """)
        assert result == expected

    def test_simple_type_alias_comment(self) -> None:
        header = Header(
            "test.h",
            [Typedef("myint", CType("int"))],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Typedefs
            # ============================================================

            # typedef int -> myint
            """)
        assert result == expected

    def test_pointer_typedef(self) -> None:
        header = Header(
            "test.h",
            [Typedef("intptr", Pointer(CType("int")))],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Typedefs
            # ============================================================

            intptr = ctypes.POINTER(ctypes.c_int)
            """)
        assert result == expected

    def test_array_typedef(self) -> None:
        header = Header(
            "test.h",
            [Typedef("Buffer", Array(CType("char"), 256))],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Typedefs
            # ============================================================

            Buffer = ctypes.c_char * 256
            """)
        assert result == expected


class TestConstants:
    def test_integer_constant(self) -> None:
        header = Header("test.h", [Constant("SIZE", 100, is_macro=True)])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Constants
            # ============================================================

            SIZE = 100
            """)
        assert result == expected

    def test_float_constant(self) -> None:
        header = Header("test.h", [Constant("PI", 3.14, is_macro=True)])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Constants
            # ============================================================

            PI = 3.14
            """)
        assert result == expected

    def test_string_constant(self) -> None:
        header = Header("test.h", [Constant("VERSION", '"1.0.0"', is_macro=True)])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Constants
            # ============================================================

            VERSION = b"1.0.0"
            """)
        assert result == expected

    def test_string_constant_unquoted(self) -> None:
        header = Header("test.h", [Constant("NAME", "hello", is_macro=True)])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Constants
            # ============================================================

            NAME = b"hello"
            """)
        assert result == expected

    def test_none_value_skipped(self) -> None:
        header = Header("test.h", [Constant("UNKNOWN", None, is_macro=True)])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys
            """)
        assert result == expected


class TestModuleStructure:
    def test_docstring_contains_path(self) -> None:
        header = Header("myheader.h", [])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from myheader.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys
            """)
        assert result == expected

    def test_imports(self) -> None:
        header = Header("test.h", [])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys
            """)
        assert result == expected

    def test_section_headers_present(self) -> None:
        header = Header(
            "test.h",
            [
                Constant("SIZE", 10),
                Enum("Color", [EnumValue("RED", 0)]),
                Struct("Point", [Field("x", CType("int"))]),
                Typedef("myint", CType("int")),
                Function("foo", CType("void"), []),
            ],
        )
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Constants
            # ============================================================

            SIZE = 10

            # ============================================================
            # Enums
            # ============================================================

            # enum Color
            RED = 0

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Point(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                ]

            # ============================================================
            # Typedefs
            # ============================================================

            # typedef int -> myint

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.foo.argtypes = []
            _lib.foo.restype = None
            """)
        assert result == expected


class TestCtypesWriter:
    """Tests for the CtypesWriter class (protocol-compliant wrapper)."""

    def test_writer_produces_output_with_expected_content(self) -> None:
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        writer = CtypesWriter()
        result = writer.write(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Point(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                    ("y", ctypes.c_int),
                ]

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.get_point.argtypes = []
            _lib.get_point.restype = ctypes.POINTER(Point)
            """)
        assert result == expected

    def test_writer_custom_lib_name(self) -> None:
        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        writer = CtypesWriter(lib_name="mylib")
        result = writer.write(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            mylib.foo.argtypes = []
            mylib.foo.restype = None
            """)
        assert result == expected

    def test_writer_protocol_compliance(self) -> None:
        writer = CtypesWriter()
        # Verify required attributes exist and have correct types
        assert isinstance(writer.name, str)
        assert len(writer.name) > 0
        assert isinstance(writer.format_description, str)
        assert len(writer.format_description) > 0
        # Verify write() produces string output
        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = writer.write(header)
        assert isinstance(result, str)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.foo.argtypes = []
            _lib.foo.restype = None
            """)
        assert result == expected

    def test_writer_name(self) -> None:
        writer = CtypesWriter()
        assert writer.name == "ctypes"

    def test_writer_format_description(self) -> None:
        writer = CtypesWriter()
        assert writer.format_description == "Python ctypes bindings"

    def test_writer_registered(self) -> None:
        from headerkit.writers import is_writer_available

        assert is_writer_available("ctypes")

    def test_writer_via_get_writer(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("ctypes")
        assert writer.name == "ctypes"


class TestVariables:
    def test_variable_as_comment(self) -> None:
        header = Header("test.h", [Variable("count", CType("int"))])
        result = header_to_ctypes(header)
        expected = textwrap.dedent("""\
            \"\"\"ctypes bindings generated from test.h.\"\"\"

            import ctypes
            import ctypes.util
            import sys

            # ============================================================
            # Global Variables
            # ============================================================

            # _lib.count: ctypes.c_int
            """)
        assert result == expected


class TestLoaderCollisionSeed:
    """The collision set must know every name the module itself binds.

    A C function may legally be named ``_lib``, ``_load_library`` or ``os``. The
    export block is emitted last, so any such name that is not reserved gets
    re-exported over the thing it collides with.
    """

    def test_the_loader_binds_exactly_these_names(self) -> None:
        """Pin the loader preamble's module-level bindings.

        The writer reserves whatever :func:`module_level_bindings` reports, so
        nothing here can drift out of step with the seeding. What this pins is
        the *expected* set: a template that starts binding a new name fails here
        and has to be acknowledged, rather than silently widening what the
        export block refuses to emit.
        """
        assert module_level_bindings(_library_loader("probe", "_lib")) == {
            "_LIBRARY_NAME",
            "_LIBRARY_PATH_ENV",
            "_load_library",
            "_lib",
        }

    def test_the_abi_note_binds_exactly_these_names(self) -> None:
        """Pin the ABI-alignment preamble's module-level bindings.

        These two flags are read only by struct definitions emitted *above* the
        export block, so a collision here is milder than one on ``_lib``: the
        module still imports and every export still works. They are reserved
        anyway, because ``_HK_UNNAMED_BITFIELD_ALIGNS = _lib.<sym>`` would
        replace a resolved ABI decision with a function pointer.
        """
        assert module_level_bindings(ABI_ALIGNMENT_NOTE) == {
            "_HK_UNNAMED_BITFIELD_ALIGNS",
            "_HK_CTYPES_MATCHES_C_NATIVELY",
        }

    def test_a_function_named_after_an_abi_flag_is_not_re_exported(self) -> None:
        """The ABI note's names are reachable end to end, so they are gated.

        An unnamed bit-field is what emits the note at all, so the header needs
        one for this collision to exist.
        """
        header = Header(
            "collide.h",
            [
                Struct(
                    "Packed",
                    [
                        Field("c", CType("unsigned char"), bit_width=1),
                        Field("", CType("unsigned int"), bit_width=0, is_padding=True),
                        Field("flag", CType("unsigned char"), bit_width=1),
                    ],
                ),
                Function("_HK_UNNAMED_BITFIELD_ALIGNS", CType("int"), []),
                Function("thing_add", CType("int"), []),
            ],
        )
        result = header_to_ctypes(header, library="collide")

        assert "_HK_UNNAMED_BITFIELD_ALIGNS = _lib._HK_UNNAMED_BITFIELD_ALIGNS" not in result
        assert "'_HK_UNNAMED_BITFIELD_ALIGNS' is not re-exported" in result
        assert "thing_add = _lib.thing_add" in result, "a later export did not survive"

    def test_a_function_named_lib_is_not_re_exported(self) -> None:
        """``int _lib(void);`` must not overwrite the library handle."""
        header = Header("collide.h", [Function("_lib", CType("int"), [])])
        result = header_to_ctypes(header, library="collide")

        assert "_lib = _lib._lib" not in result
        assert "_lib = _load_library()" in result
        assert "'_lib' is not re-exported" in result

    @pytest.mark.parametrize("name", ["ctypes", "os", "sys"])
    def test_a_function_named_after_an_import_is_not_re_exported(self, name: str) -> None:
        """``int os(void);`` must not overwrite the imported module.

        Every import the module writes unconditionally is covered, not one
        representative: each is reserved by its own statement, so a test naming
        only ``os`` leaves the others' reservations unguarded.
        """
        header = Header("collide.h", [Function(name, CType("int"), [])])
        result = header_to_ctypes(header, library="collide")

        assert f"import {name}" in result, "the import this reservation guards is not written"
        assert f"{name} = _lib.{name}" not in result
        assert f"'{name}' is not re-exported" in result

    def test_a_conditional_import_is_reserved_only_when_written(self) -> None:
        """``platform`` is imported only for a bit-field struct.

        Reserving a name the module never binds would suppress a legitimate
        export, so the seed follows the same conditionals the emission does.
        Both halves are asserted here: reserving unconditionally passes the
        collision half and fails this one.
        """
        fn = Function("platform", CType("int"), [])
        # A zero-width unnamed bit-field on a wider carrier is what pulls in
        # ``import platform``: whether it raises the record's alignment differs
        # between the MSVC and Itanium ABIs, so the layout needs a runtime branch.
        bitfield = Struct(
            "Packed",
            [
                Field("c", CType("unsigned char"), bit_width=1),
                Field("", CType("unsigned int"), bit_width=0, is_padding=True),
                Field("flag", CType("unsigned char"), bit_width=1),
            ],
        )

        without = header_to_ctypes(Header("plain.h", [fn]), library="probe")
        assert "import platform" not in without
        assert "platform = _lib.platform" in without, "a name the module never binds must stay exportable"

        with_abi = header_to_ctypes(Header("packed.h", [bitfield, fn]), library="probe")
        assert "import platform" in with_abi
        assert "platform = _lib.platform" not in with_abi
        assert "'platform' is not re-exported" in with_abi
