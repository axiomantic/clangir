"""Tests for the IR module."""

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
    ParserBackend,
    Pointer,
    SourceLocation,
    Struct,
    Typedef,
    Variable,
)


class TestCType:
    def test_simple_type(self):
        t = CType("int")
        assert t.name == "int"
        assert t.qualifiers == []
        assert str(t) == "int"

    def test_qualified_type(self):
        t = CType("int", ["const"])
        assert str(t) == "const int"

    def test_multiple_qualifiers(self):
        t = CType("int", ["const", "volatile"])
        assert str(t) == "const volatile int"


class TestPointer:
    def test_simple_pointer(self):
        p = Pointer(CType("int"))
        assert str(p) == "int*"

    def test_const_pointer(self):
        p = Pointer(CType("char", ["const"]))
        assert str(p) == "const char*"

    def test_pointer_to_pointer(self):
        p = Pointer(Pointer(CType("int")))
        assert str(p) == "int**"

    def test_pointer_with_qualifiers(self):
        """Pointer with qualifiers ['const'] prepends qualifiers: 'const int*'."""
        p = Pointer(CType("int"), ["const"])
        assert str(p) == "const int*"
        assert p.qualifiers == ["const"]

    def test_pointer_qualifier_data_model(self):
        # const pointer to int (pointer itself is const)
        const_ptr = Pointer(CType("int"), ["const"])
        assert const_ptr.qualifiers == ["const"]
        assert const_ptr.pointee.qualifiers == []

        # pointer to const int (pointee is const)
        ptr_to_const = Pointer(CType("int", ["const"]))
        assert ptr_to_const.qualifiers == []
        assert ptr_to_const.pointee.qualifiers == ["const"]


class TestArray:
    def test_fixed_size_array(self):
        a = Array(CType("int"), 10)
        assert str(a) == "int[10]"

    def test_flexible_array(self):
        a = Array(CType("char"), None)
        assert str(a) == "char[]"

    def test_expression_size(self):
        a = Array(CType("int"), "SIZE")
        assert str(a) == "int[SIZE]"

    def test_array_of_pointers(self):
        arr = Array(Pointer(CType("int")), 10)
        assert str(arr) == "int*[10]"

    def test_multidimensional_array(self):
        arr = Array(Array(CType("int"), 3), 3)
        assert str(arr) == "int[3][3]"


class TestFunctionPointer:
    def test_simple_function_pointer(self):
        fp = FunctionPointer(CType("int"), [])
        assert str(fp) == "int (*)()"

    def test_function_pointer_with_params(self):
        fp = FunctionPointer(
            CType("void"),
            [Parameter("x", CType("int")), Parameter("y", CType("int"))],
        )
        assert str(fp) == "void (*)(int x, int y)"

    def test_variadic_function_pointer(self):
        fp = FunctionPointer(
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        assert str(fp) == "int (*)(const char* fmt, ...)"

    def test_variadic_function_pointer_no_named_params(self):
        """Variadic function pointer with no named parameters."""
        fp = FunctionPointer(CType("int"), [], is_variadic=True)
        assert str(fp) == "int (*)(...)"

    def test_calling_convention(self):
        fp = FunctionPointer(CType("void"), [], calling_convention="stdcall")
        assert fp.calling_convention == "stdcall"
        assert str(fp) == "void ( __stdcall__*)()"

    def test_calling_convention_default_none(self):
        fp = FunctionPointer(CType("int"), [])
        assert fp.calling_convention is None


class TestField:
    def test_simple_field(self):
        f = Field("x", CType("int"))
        assert f.name == "x"
        assert f.bit_width is None
        assert f.anonymous_struct is None
        assert str(f) == "int x"

    def test_bitfield(self):
        f = Field("flags", CType("unsigned int"), bit_width=3)
        assert f.bit_width == 3
        assert str(f) == "unsigned int flags : 3"

    def test_anonymous_struct_field(self):
        inner = Struct(
            None,
            [
                Field("i", CType("int")),
                Field("f", CType("float")),
            ],
            is_union=True,
        )
        f = Field("", CType(""), anonymous_struct=inner)
        assert f.anonymous_struct is not None
        assert f.anonymous_struct.is_union
        assert len(f.anonymous_struct.fields) == 2
        assert f.anonymous_struct.fields[0].name == "i"
        assert str(f.anonymous_struct.fields[0].type) == "int"
        assert f.anonymous_struct.fields[1].name == "f"
        assert str(f.anonymous_struct.fields[1].type) == "float"


class TestEnum:
    def test_simple_enum(self):
        e = Enum("Color", [EnumValue("RED"), EnumValue("GREEN"), EnumValue("BLUE")])
        assert e.name == "Color"
        assert len(e.values) == 3
        assert str(e) == "enum Color"

    def test_enum_with_values(self):
        e = Enum(
            "Flags",
            [EnumValue("FLAG_A", 1), EnumValue("FLAG_B", 2), EnumValue("FLAG_C", "FLAG_A | FLAG_B")],
        )
        assert str(e.values[0]) == "FLAG_A = 1"
        assert str(e.values[2]) == "FLAG_C = FLAG_A | FLAG_B"

    def test_anonymous_enum(self):
        e = Enum(None, [EnumValue("VALUE", 42)])
        assert str(e) == "enum (anonymous)"

    def test_enum_is_typedef(self):
        enum = Enum("Color", [EnumValue("RED", 0)], is_typedef=True)
        assert enum.is_typedef is True

    def test_enum_default_not_typedef(self):
        enum = Enum("Color", [EnumValue("RED", 0)])
        assert enum.is_typedef is False


class TestStruct:
    def test_simple_struct(self):
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        assert s.name == "Point"
        assert not s.is_union
        assert str(s) == "struct Point"

    def test_union(self):
        u = Struct("Data", [Field("i", CType("int")), Field("f", CType("float"))], is_union=True)
        assert u.is_union
        assert str(u) == "union Data"

    def test_cppclass(self):
        c = Struct("Widget", [], is_cppclass=True)
        assert str(c) == "cppclass Widget"

    def test_struct_with_methods(self):
        """Struct with methods list (C++ class methods)."""
        s = Struct(
            "Widget",
            fields=[Field("width", CType("int"))],
            methods=[
                Function(
                    "resize",
                    CType("void"),
                    [
                        Parameter("w", CType("int")),
                        Parameter("h", CType("int")),
                    ],
                ),
            ],
            is_cppclass=True,
        )
        assert len(s.methods) == 1
        assert s.methods[0].name == "resize"
        assert len(s.methods[0].parameters) == 2
        assert str(s) == "cppclass Widget"

    def test_struct_with_cpp_fields(self):
        s = Struct(
            "MyClass",
            fields=[Field("x", CType("int"))],
            namespace="ns",
            template_params=["T"],
            is_cppclass=True,
            cpp_name="MyClass<int>",
            inner_typedefs={"iterator": "Iterator<T>"},
        )
        assert s.namespace == "ns"
        assert s.template_params == ["T"]
        assert s.cpp_name == "MyClass<int>"
        assert s.inner_typedefs == {"iterator": "Iterator<T>"}

    def test_packed_struct(self):
        s = Struct("Packed", [Field("x", CType("int"))], is_packed=True)
        assert s.is_packed
        assert str(s) == "struct Packed __attribute__((packed))"

    def test_packed_default_false(self):
        s = Struct("Normal", [])
        assert not s.is_packed


class TestFunction:
    def test_simple_function(self):
        f = Function("main", CType("int"), [])
        assert str(f) == "int main()"

    def test_function_with_params(self):
        f = Function(
            "add",
            CType("int"),
            [Parameter("a", CType("int")), Parameter("b", CType("int"))],
        )
        assert str(f) == "int add(int a, int b)"

    def test_variadic_function(self):
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        assert str(f) == "int printf(const char* fmt, ...)"

    def test_calling_convention(self):
        f = Function("WinMain", CType("int"), [], calling_convention="stdcall")
        assert f.calling_convention == "stdcall"
        assert str(f) == "int __stdcall__ WinMain()"

    def test_calling_convention_default_none(self):
        f = Function("main", CType("int"), [])
        assert f.calling_convention is None


class TestTypedef:
    def test_simple_typedef(self):
        t = Typedef("myint", CType("int"))
        assert str(t) == "typedef int myint"

    def test_typedef_pointer(self):
        """Typedef of a pointer type."""
        t = Typedef("int_ptr", Pointer(CType("int")))
        assert str(t) == "typedef int* int_ptr"

    def test_typedef_function_pointer(self):
        """Typedef of a function pointer type."""
        fp = FunctionPointer(
            CType("void"),
            [Parameter("data", Pointer(CType("void")))],
        )
        t = Typedef("callback_t", fp)
        assert str(t) == "typedef void (*)(void* data) callback_t"


class TestVariable:
    def test_simple_variable(self):
        v = Variable("count", CType("int"))
        assert str(v) == "int count"

    def test_variable_with_pointer(self):
        var = Variable("ptr", Pointer(CType("void")))
        assert var.name == "ptr"
        assert isinstance(var.type, Pointer)
        assert str(var) == "void* ptr"

    def test_variable_with_array(self):
        var = Variable("buf", Array(CType("char"), 256))
        assert var.name == "buf"
        assert isinstance(var.type, Array)
        assert str(var) == "char[256] buf"


class TestConstant:
    def test_macro_constant(self):
        c = Constant("SIZE", 100, is_macro=True)
        assert str(c) == "#define SIZE 100"

    def test_const_variable(self):
        c = Constant("MAX", 255, type=CType("int"))
        assert str(c) == "const int MAX = 255"

    def test_const_without_type(self):
        """Non-macro constant with type=None omits type from str."""
        c = Constant("VAL", 42)
        assert str(c) == "const VAL = 42"


class TestHeader:
    def test_header(self):
        h = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int"))]),
                Function("get_point", CType("Point")),
            ],
        )
        assert h.path == "test.h"
        assert len(h.declarations) == 2
        assert isinstance(h.declarations[0], Struct)
        assert h.declarations[0].name == "Point"
        assert isinstance(h.declarations[1], Function)
        assert h.declarations[1].name == "get_point"
        assert str(h) == "Header(test.h, 2 declarations)"

    def test_header_included_headers(self):
        h = Header(path="test.h", declarations=[], included_headers={"stdio.h", "stdlib.h"})
        assert h.included_headers == {"stdio.h", "stdlib.h"}

    def test_header_has_included_headers_attribute(self):
        """Header should always have included_headers attribute.

        Adapted from autopxd2 test_ir.py::TestHeaderIncludedHeaders.
        """
        header = Header(path="test.h", declarations=[])
        assert hasattr(header, "included_headers")

    def test_included_headers_defaults_to_empty_set(self):
        """included_headers defaults to empty set when not provided.

        Adapted from autopxd2 test_ir.py::TestHeaderIncludedHeaders.
        """
        header = Header(path="test.h", declarations=[])
        assert header.included_headers == set()
        assert isinstance(header.included_headers, set)

    def test_included_headers_can_be_mutated(self):
        """included_headers can be populated after construction.

        Adapted from autopxd2 test_ir.py::TestHeaderIncludedHeaders.
        """
        header = Header(path="test.h", declarations=[])
        header.included_headers = {"stdio.h", "stdlib.h", "stdint.h"}
        assert header.included_headers == {"stdio.h", "stdlib.h", "stdint.h"}

    def test_header_constructor_accepts_included_headers(self):
        """Header constructor should accept included_headers parameter.

        Adapted from autopxd2 test_ir.py::TestHeaderIncludedHeaders.
        """
        included = {"stdio.h", "string.h"}
        header = Header(path="test.h", declarations=[], included_headers=included)
        assert header.included_headers == included


class TestSourceLocation:
    def test_location(self):
        loc = SourceLocation("test.h", 42, 10)
        assert loc.file == "test.h"
        assert loc.line == 42
        assert loc.column == 10

    def test_location_without_column(self):
        loc = SourceLocation("test.h", 42)
        assert loc.column is None


class TestParserBackendProtocol:
    def test_protocol_is_runtime_checkable(self):
        """ParserBackend should work as a runtime-checkable Protocol."""

        class MockBackend:
            def parse(
                self,
                code: str,
                filename: str,
                include_dirs=None,
                extra_args=None,
                *,
                use_default_includes=True,
                recursive_includes=True,
                max_depth=10,
                project_prefixes=None,
            ):
                return Header(path=filename)

            @property
            def name(self) -> str:
                return "mock"

            @property
            def supports_macros(self) -> bool:
                return False

            @property
            def supports_cpp(self) -> bool:
                return False

        assert isinstance(MockBackend(), ParserBackend)

    def test_protocol_rejects_non_conforming(self):
        """Objects not conforming to ParserBackend should fail isinstance."""

        class NotABackend:
            pass

        assert not isinstance(NotABackend(), ParserBackend)

    def test_protocol_has_extended_parse_signature(self):
        """ParserBackend.parse should accept keyword-only optional parameters."""
        import inspect

        sig = inspect.signature(ParserBackend.parse)
        param_names = list(sig.parameters.keys())
        assert param_names == [
            "self",
            "code",
            "filename",
            "include_dirs",
            "extra_args",
            "use_default_includes",
            "recursive_includes",
            "max_depth",
            "project_prefixes",
        ]
