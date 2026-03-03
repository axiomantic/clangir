"""Tests for the LuaJIT FFI writer."""

import textwrap

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
from headerkit.writers.lua import (
    LuaWriter,
    _constant_to_cdef,
    _constant_to_lua,
    _enum_to_cdef,
    _format_field,
    _format_params,
    _function_to_cdef,
    _struct_to_cdef,
    _type_to_c,
    _typedef_to_cdef,
    _variable_to_cdef,
    header_to_lua,
)


class TestTypeToC:
    def test_simple_type(self) -> None:
        assert _type_to_c(CType("int")) == "int"

    def test_qualified_type(self) -> None:
        assert _type_to_c(CType("int", ["const"])) == "const int"

    def test_unsigned_type(self) -> None:
        assert _type_to_c(CType("long", ["unsigned"])) == "unsigned long"

    def test_pointer(self) -> None:
        assert _type_to_c(Pointer(CType("int"))) == "int *"

    def test_const_char_pointer(self) -> None:
        assert _type_to_c(Pointer(CType("char", ["const"]))) == "const char *"

    def test_double_pointer(self) -> None:
        result = _type_to_c(Pointer(Pointer(CType("char"))))
        assert result == "char **"

    def test_triple_pointer(self) -> None:
        result = _type_to_c(Pointer(Pointer(Pointer(CType("void")))))
        assert result == "void ***"

    def test_array(self) -> None:
        assert _type_to_c(Array(CType("int"), 10)) == "int[10]"

    def test_flexible_array(self) -> None:
        assert _type_to_c(Array(CType("char"), None)) == "char[]"

    def test_function_pointer(self) -> None:
        fp = FunctionPointer(CType("void"), [])
        assert _type_to_c(fp) == "void(*)(void)"

    def test_function_pointer_with_params(self) -> None:
        fp = FunctionPointer(
            CType("int"),
            [Parameter("x", CType("int")), Parameter(None, CType("float"))],
        )
        assert _type_to_c(fp) == "int(*)(int x, float)"

    def test_pointer_to_function_pointer(self) -> None:
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        result = _type_to_c(Pointer(fp))
        assert result == "void(*)(int x)"

    def test_variadic_function_pointer(self) -> None:
        fp = FunctionPointer(
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        assert _type_to_c(fp) == "int(*)(const char * fmt, ...)"


class TestFormatParams:
    def test_empty_params_void(self) -> None:
        assert _format_params([], False) == "void"

    def test_variadic_only(self) -> None:
        assert _format_params([], True) == "..."

    def test_named_params(self) -> None:
        params = [Parameter("a", CType("int")), Parameter("b", CType("float"))]
        assert _format_params(params, False) == "int a, float b"

    def test_variadic_with_params(self) -> None:
        params = [Parameter("fmt", Pointer(CType("char", ["const"])))]
        assert _format_params(params, True) == "const char * fmt, ..."

    def test_array_parameter(self) -> None:
        params = [Parameter("buf", Array(CType("char"), 256))]
        result = _format_params(params, False)
        assert result == "char buf[256]"

    def test_function_pointer_parameter(self) -> None:
        fp = FunctionPointer(CType("int"), [Parameter("x", CType("float"))])
        params = [Parameter("cb", fp)]
        result = _format_params(params, False)
        assert result == "int (*cb)(float x)"


class TestFormatField:
    def test_simple_field(self) -> None:
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"

    def test_pointer_field(self) -> None:
        f = Field("data", Pointer(CType("void")))
        result = _format_field(f)
        assert result == "    void * data;"

    def test_array_field(self) -> None:
        f = Field("buf", Array(CType("char"), 256))
        result = _format_field(f)
        assert result == "    char buf[256];"

    def test_function_pointer_field(self) -> None:
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        f = Field("callback", fp)
        result = _format_field(f)
        assert result == "    void (*callback)(int x);"

    def test_bitfield(self) -> None:
        f = Field("flags", CType("unsigned int"), bit_width=4)
        result = _format_field(f)
        assert result == "    unsigned int flags : 4;"

    def test_bitfield_single_bit(self) -> None:
        f = Field("valid", CType("unsigned int"), bit_width=1)
        result = _format_field(f)
        assert result == "    unsigned int valid : 1;"

    def test_no_bitwidth_omits_suffix(self) -> None:
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"


class TestAnonymousStructField:
    def test_anonymous_struct_inline(self) -> None:
        anon = Struct(None, [Field("x", CType("int")), Field("y", CType("int"))])
        f = Field("pos", CType("int"), anonymous_struct=anon)
        result = _format_field(f)
        assert result == "    struct {\n        int x;\n        int y;\n    };"

    def test_anonymous_union_inline(self) -> None:
        anon = Struct(
            None,
            [Field("i", CType("int")), Field("f", CType("float"))],
            is_union=True,
        )
        f = Field("data", CType("int"), anonymous_struct=anon)
        result = _format_field(f)
        assert result == "    union {\n        int i;\n        float f;\n    };"


class TestConstantToCdef:
    def test_integer_constant(self) -> None:
        c = Constant("BUFFER_SIZE", 1024, is_macro=True)
        result = _constant_to_cdef(c)
        assert result == "static const int BUFFER_SIZE = 1024;"

    def test_negative_integer_constant(self) -> None:
        c = Constant("ERROR_CODE", -1, is_macro=True)
        result = _constant_to_cdef(c)
        assert result == "static const int ERROR_CODE = -1;"

    def test_zero_constant(self) -> None:
        c = Constant("NULL_VAL", 0, is_macro=True)
        result = _constant_to_cdef(c)
        assert result == "static const int NULL_VAL = 0;"

    def test_float_constant_returns_none(self) -> None:
        c = Constant("PI", 3.14, is_macro=True)
        result = _constant_to_cdef(c)
        assert result is None

    def test_string_constant_returns_none(self) -> None:
        c = Constant("VERSION", '"1.0.0"', is_macro=True)
        result = _constant_to_cdef(c)
        assert result is None

    def test_none_value_returns_none(self) -> None:
        c = Constant("UNKNOWN", None, is_macro=True)
        result = _constant_to_cdef(c)
        assert result is None


class TestConstantToLua:
    def test_float_constant(self) -> None:
        c = Constant("PI", 3.14, is_macro=True)
        result = _constant_to_lua(c)
        assert result == "local PI = 3.14"

    def test_string_constant(self) -> None:
        c = Constant("VERSION", '"1.0.0"', is_macro=True)
        result = _constant_to_lua(c)
        assert result == 'local VERSION = "1.0.0"'

    def test_integer_constant_returns_none(self) -> None:
        c = Constant("SIZE", 100, is_macro=True)
        result = _constant_to_lua(c)
        assert result is None

    def test_expression_constant_returns_none(self) -> None:
        """Non-quoted string expressions are not string literals."""
        c = Constant("MASK", "1 << 4", is_macro=True)
        result = _constant_to_lua(c)
        assert result is None


class TestEnumToCdef:
    def test_enum_with_values(self) -> None:
        e = Enum(
            "Color",
            [EnumValue("RED", 0), EnumValue("GREEN", 1), EnumValue("BLUE", 2)],
        )
        result = _enum_to_cdef(e)
        assert result == textwrap.dedent("""\
            typedef enum {
                RED = 0,
                GREEN = 1,
                BLUE = 2,
            } Color;""")

    def test_enum_with_auto_values(self) -> None:
        e = Enum("Status", [EnumValue("OK", None), EnumValue("ERROR", None)])
        result = _enum_to_cdef(e)
        assert result == textwrap.dedent("""\
            typedef enum {
                OK,
                ERROR,
            } Status;""")

    def test_empty_enum_returns_none(self) -> None:
        e = Enum("Empty", [])
        result = _enum_to_cdef(e)
        assert result is None

    def test_anonymous_enum(self) -> None:
        e = Enum(None, [EnumValue("FLAG_A", 1), EnumValue("FLAG_B", 2)])
        result = _enum_to_cdef(e)
        assert result == textwrap.dedent("""\
            typedef enum {
                FLAG_A = 1,
                FLAG_B = 2,
            };""")


class TestStructToCdef:
    def test_simple_struct(self) -> None:
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            struct {
                int x;
                int y;
            } Point;""")

    def test_typedef_struct(self) -> None:
        s = Struct(
            "Point",
            [Field("x", CType("int")), Field("y", CType("int"))],
            is_typedef=True,
        )
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            typedef struct {
                int x;
                int y;
            } Point;""")

    def test_union(self) -> None:
        u = Struct(
            "Data",
            [Field("i", CType("int")), Field("f", CType("float"))],
            is_union=True,
        )
        result = _struct_to_cdef(u)
        assert result == textwrap.dedent("""\
            union {
                int i;
                float f;
            } Data;""")

    def test_opaque_struct(self) -> None:
        s = Struct("Handle", [])
        result = _struct_to_cdef(s)
        assert result == "typedef struct Handle Handle;"

    def test_opaque_typedef_struct(self) -> None:
        # Opaque structs (no fields) always emit the same forward declaration
        # regardless of is_typedef, because the opaque path in _struct_to_cdef
        # returns early before checking is_typedef. This test documents that
        # intentional behavior: both produce "typedef struct Handle Handle;".
        s = Struct("Handle", [], is_typedef=True)
        result = _struct_to_cdef(s)
        assert result == "typedef struct Handle Handle;"

    def test_packed_struct(self) -> None:
        s = Struct(
            "PackedRecord",
            [Field("tag", CType("uint8_t")), Field("value", CType("uint32_t"))],
            is_packed=True,
        )
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            struct __attribute__((packed)) {
                uint8_t tag;
                uint32_t value;
            } PackedRecord;""")

    def test_packed_typedef_struct(self) -> None:
        s = Struct(
            "PackedRecord",
            [Field("tag", CType("uint8_t"))],
            is_packed=True,
            is_typedef=True,
        )
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            typedef struct __attribute__((packed)) {
                uint8_t tag;
            } PackedRecord;""")

    def test_struct_with_bitfields(self) -> None:
        s = Struct(
            "Flags",
            [
                Field("read", CType("unsigned int"), bit_width=1),
                Field("write", CType("unsigned int"), bit_width=1),
                Field("exec", CType("unsigned int"), bit_width=1),
                Field("reserved", CType("unsigned int"), bit_width=5),
            ],
        )
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            struct {
                unsigned int read : 1;
                unsigned int write : 1;
                unsigned int exec : 1;
                unsigned int reserved : 5;
            } Flags;""")

    def test_anonymous_struct_skipped(self) -> None:
        s = Struct(None, [Field("x", CType("int"))])
        result = _struct_to_cdef(s)
        assert result is None

    def test_struct_with_array_field(self) -> None:
        s = Struct("Buffer", [Field("data", Array(CType("char"), 1024))])
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            struct {
                char data[1024];
            } Buffer;""")

    def test_struct_with_pointer_field(self) -> None:
        s = Struct("Node", [Field("next", Pointer(CType("Node")))])
        result = _struct_to_cdef(s)
        assert result == textwrap.dedent("""\
            struct {
                Node * next;
            } Node;""")


class TestFunctionToCdef:
    def test_simple_function(self) -> None:
        f = Function(
            "add",
            CType("int"),
            [Parameter("a", CType("int")), Parameter("b", CType("int"))],
        )
        result = _function_to_cdef(f)
        assert result == "int add(int a, int b);"

    def test_void_function(self) -> None:
        f = Function("init", CType("void"), [])
        result = _function_to_cdef(f)
        assert result == "void init(void);"

    def test_variadic_function(self) -> None:
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        result = _function_to_cdef(f)
        assert result == "int printf(const char * fmt, ...);"

    def test_function_returning_pointer(self) -> None:
        f = Function("malloc", Pointer(CType("void")), [Parameter("size", CType("size_t"))])
        result = _function_to_cdef(f)
        assert result == "void * malloc(size_t size);"

    def test_calling_convention(self) -> None:
        f = Function("WinMain", CType("int"), [], calling_convention="stdcall")
        result = _function_to_cdef(f)
        assert result == "__stdcall__ int WinMain(void);"

    def test_no_calling_convention(self) -> None:
        f = Function("normal", CType("void"), [])
        result = _function_to_cdef(f)
        assert result == "void normal(void);"


class TestTypedefToCdef:
    def test_simple_typedef(self) -> None:
        t = Typedef("myint", CType("unsigned int"))
        result = _typedef_to_cdef(t)
        assert result == "typedef unsigned int myint;"

    def test_function_pointer_typedef(self) -> None:
        fp = FunctionPointer(
            CType("void"),
            [Parameter("event_id", CType("int")), Parameter("ctx", Pointer(CType("void")))],
        )
        t = Typedef("EventCallback", Pointer(fp))
        result = _typedef_to_cdef(t)
        assert result == "typedef void (*EventCallback)(int event_id, void * ctx);"

    def test_direct_function_pointer_typedef(self) -> None:
        fp = FunctionPointer(CType("int"), [Parameter("x", CType("int"))])
        t = Typedef("IntFunc", fp)
        result = _typedef_to_cdef(t)
        assert result == "typedef int (*IntFunc)(int x);"

    def test_array_typedef(self) -> None:
        t = Typedef("Matrix", Array(CType("float"), 16))
        result = _typedef_to_cdef(t)
        assert result == "typedef float Matrix[16];"


class TestVariableToCdef:
    def test_simple_variable(self) -> None:
        v = Variable("count", CType("int"))
        result = _variable_to_cdef(v)
        assert result == "int count;"

    def test_array_variable(self) -> None:
        v = Variable("table", Array(CType("int"), 256))
        result = _variable_to_cdef(v)
        assert result == "int table[256];"


class TestHeaderToLua:
    def test_output_structure(self) -> None:
        """Output contains the required structural elements."""
        header = Header("test.h", [])
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[


            ]]

            return {}
            """)

    def test_integer_constant_in_cdef(self) -> None:
        header = Header("test.h", [Constant("BUFFER_SIZE", 1024, is_macro=True)])
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Constants */
            static const int BUFFER_SIZE = 1024;

            ]]

            return {}
            """)

    def test_float_constant_as_lua_var(self) -> None:
        header = Header("test.h", [Constant("PI", 3.14, is_macro=True)])
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            local PI = 3.14

            ffi.cdef[[


            ]]

            return {}
            """)

    def test_string_constant_as_lua_var(self) -> None:
        header = Header("test.h", [Constant("VERSION", '"1.0.0"', is_macro=True)])
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            local VERSION = "1.0.0"

            ffi.cdef[[


            ]]

            return {}
            """)

    def test_enum_in_cdef(self) -> None:
        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])],
        )
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Enums */
            typedef enum {
                RED = 0,
                GREEN = 1,
            } Color;

            ]]

            return {}
            """)

    def test_struct_in_cdef(self) -> None:
        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Structs */
            struct {
                int x;
                int y;
            } Point;

            ]]

            return {}
            """)

    def test_opaque_struct_in_cdef(self) -> None:
        header = Header("test.h", [Struct("Handle", [])])
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Opaque types */
            typedef struct Handle Handle;

            ]]

            return {}
            """)

    def test_function_in_cdef(self) -> None:
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
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Functions */
            int add(int a, int b);

            ]]

            return {}
            """)

    def test_variadic_function_in_cdef(self) -> None:
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
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Functions */
            int printf(const char * fmt, ...);

            ]]

            return {}
            """)

    def test_function_pointer_typedef_in_callbacks(self) -> None:
        fp = FunctionPointer(
            CType("void"),
            [Parameter("event_id", CType("int")), Parameter("ctx", Pointer(CType("void")))],
        )
        header = Header("test.h", [Typedef("EventCallback", Pointer(fp))])
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Callback typedefs */
            typedef void (*EventCallback)(int event_id, void * ctx);

            ]]

            return {}
            """)

    def test_calling_convention_on_function(self) -> None:
        header = Header(
            "test.h",
            [Function("WinMain", CType("int"), [], calling_convention="stdcall")],
        )
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Functions */
            __stdcall__ int WinMain(void);

            ]]

            return {}
            """)

    def test_mixed_declarations(self) -> None:
        """Full integration test with multiple declaration types."""
        header = Header(
            "api.h",
            [
                Constant("MAX_SIZE", 4096, is_macro=True),
                Constant("PI", 3.14159, is_macro=True),
                Enum("Status", [EnumValue("OK", 0), EnumValue("ERR", 1)]),
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                    is_typedef=True,
                ),
                Struct("Opaque", []),
                Typedef(
                    "Callback",
                    Pointer(FunctionPointer(CType("void"), [Parameter("data", Pointer(CType("void")))])),
                ),
                Function(
                    "init",
                    CType("void"),
                    [Parameter("cb", Pointer(CType("void")))],
                ),
            ],
        )
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: api.h
            -- Generated by headerkit

            local ffi = require("ffi")

            local PI = 3.14159

            ffi.cdef[[

            /* Constants */
            static const int MAX_SIZE = 4096;

            /* Enums */
            typedef enum {
                OK = 0,
                ERR = 1,
            } Status;

            /* Structs */
            typedef struct {
                int x;
                int y;
            } Point;

            /* Opaque types */
            typedef struct Opaque Opaque;

            /* Callback typedefs */
            typedef void (*Callback)(void * data);

            /* Functions */
            void init(void * cb);

            ]]

            return {}
            """)

    def test_variable_in_output(self) -> None:
        var = Variable("count", CType("int"))
        header = Header("test.h", [var])
        writer = LuaWriter()
        result = writer.write(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Functions */
            int count;

            ]]

            return {}
            """)

    def test_packed_struct_in_output(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "PackedRecord",
                    [Field("tag", CType("uint8_t")), Field("value", CType("uint32_t"))],
                    is_packed=True,
                    is_typedef=True,
                )
            ],
        )
        result = header_to_lua(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Structs */
            typedef struct __attribute__((packed)) {
                uint8_t tag;
                uint32_t value;
            } PackedRecord;

            ]]

            return {}
            """)


class TestLuaWriter:
    """Tests for the LuaWriter class (protocol-compliant wrapper)."""

    def test_writer_protocol_compliance(self) -> None:
        """LuaWriter should satisfy the WriterBackend protocol."""
        from headerkit.writers import WriterBackend

        writer = LuaWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self) -> None:
        writer = LuaWriter()
        assert writer.name == "lua"

    def test_writer_format_description(self) -> None:
        writer = LuaWriter()
        assert writer.format_description == "LuaJIT FFI bindings"

    def test_writer_produces_expected_output(self) -> None:
        """LuaWriter.write() should produce correct LuaJIT FFI bindings."""
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        writer = LuaWriter()
        result = writer.write(header)
        assert result == textwrap.dedent("""\
            -- Auto-generated LuaJIT FFI bindings
            -- Source: test.h
            -- Generated by headerkit

            local ffi = require("ffi")

            ffi.cdef[[

            /* Structs */
            struct {
                int x;
                int y;
            } Point;

            /* Functions */
            Point * get_point(void);

            ]]

            return {}
            """)

    def test_writer_registered(self) -> None:
        """LuaWriter should be registered in the writer registry."""
        from headerkit.writers import is_writer_available

        assert is_writer_available("lua")

    def test_writer_from_registry(self) -> None:
        """get_writer('lua') should return a LuaWriter instance."""
        from headerkit.writers import get_writer

        writer = get_writer("lua")
        assert isinstance(writer, LuaWriter)
        assert writer.name == "lua"
