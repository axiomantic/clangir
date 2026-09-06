import ctypes
import textwrap

import pytest

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.hooks import HookDispatcher, PipelineContext
from headerkit.ir import (
    Array,
    BaseSpecifier,
    CType,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    Reference,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers.ctypes import CtypesWriter
from headerkit.writers.cython import CythonWriter

treesitter = pytest.mark.treesitter


@treesitter
class TestTreeSitterBackend:
    @classmethod
    def setup_class(cls):
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

    def test_availability_and_capabilities(self):
        backend = TreeSitterBackend()
        assert backend.name == "tree-sitter"
        assert backend.is_available() is True
        assert "c" in backend.supported_languages
        try:
            import tree_sitter_cpp  # noqa: F401

            assert backend.supports_cpp is True
            assert "c++" in backend.supported_languages
        except ImportError:
            assert backend.supports_cpp is False

    def test_is_cpp_mode_explicit_override(self):
        backend = TreeSitterBackend()
        # Explicit -x c should force C mode even for .hpp filename
        assert backend._is_cpp_mode("struct Point { int x; };", "test.hpp", ["-x", "c"]) is False
        # Explicit -x c++ should force C++ mode even for .h filename
        assert backend._is_cpp_mode("struct Point { int x; };", "test.h", ["-x", "c++"]) is True
        # Explicit -std=c11 should force C mode even for .hpp filename
        assert backend._is_cpp_mode("struct Point { int x; };", "test.hpp", ["-std=c11"]) is False

    def test_parse_simple_struct(self):
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "test.h")

        assert len(header.declarations) == 1
        st = header.declarations[0]
        assert isinstance(st, Struct)
        assert st.name == "Point"
        assert len(st.fields) == 2
        assert st.fields[0].name == "x"
        assert str(st.fields[0].type) == "int"
        assert st.fields[1].name == "y"
        assert str(st.fields[1].type) == "int"

    def test_parse_function_declaration(self):
        code = "int distance(const Point *a, const Point *b);"
        backend = TreeSitterBackend()
        header = backend.parse(code, "math.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "distance"
        assert str(fn.return_type) == "int"
        assert len(fn.parameters) == 2
        assert fn.parameters[0].name == "a"
        assert isinstance(fn.parameters[0].type, Pointer)
        assert fn.parameters[1].name == "b"
        assert isinstance(fn.parameters[1].type, Pointer)

    def test_parse_typedef_struct(self):
        code = """
        typedef struct Vector3 {
            float x, y, z;
        } Vector3;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "vec.h")

        assert len(header.declarations) >= 1
        st = [d for d in header.declarations if isinstance(d, Struct)][0]
        assert st.name == "Vector3"
        assert st.is_typedef is True
        assert len(st.fields) == 3

    def test_parse_enum(self):
        code = """
        enum Status {
            STATUS_OK = 0,
            STATUS_ERR = 1
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "status.h")

        assert len(header.declarations) == 1
        en = header.declarations[0]
        assert isinstance(en, Enum)
        assert en.name == "Status"
        assert len(en.values) == 2
        assert en.values[0].name == "STATUS_OK"
        assert en.values[0].value == 0
        assert en.values[1].name == "STATUS_ERR"
        assert en.values[1].value == 1

    def test_hook_fallback_dispatch(self):
        dispatcher = HookDispatcher()
        ctx = PipelineContext(backend="tree-sitter", language="c")

        result = dispatcher.first_result("parse_unit", "int add(int a, int b);", "math.h", context=ctx)
        assert isinstance(result, Header)
        assert len(result.declarations) == 1
        fn = result.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "add"

    def test_parse_preprocessor_ifdef_and_linkage(self):
        code = """
        #ifndef FOO_H
        #define FOO_H

        #ifdef __cplusplus
        extern "C" {
        #endif

        int compute(int x);

        #ifdef __cplusplus
        }
        #endif

        #endif
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "foo.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "compute"

    def test_parse_preprocessor_does_not_traverse_else_branch(self):
        code = """
        #if defined(USE_FLOAT)
        float process(float x);
        #else
        double process_alternative(double x);
        #endif
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "compute.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "process"

    def test_parse_pointer_return_and_void_param(self):
        code = """
        char* get_version(void);
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "version.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "get_version"
        assert isinstance(fn.return_type, Pointer)
        assert isinstance(fn.return_type.pointee, CType)
        assert str(fn.return_type) == "char*"
        assert len(fn.parameters) == 0

    def test_parse_multilevel_pointers_in_functions(self):
        code = "char ***get_entries(int **matrix, void **out_handle);"
        backend = TreeSitterBackend()
        header = backend.parse(code, "entries.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "get_entries"

        # Check 3-level pointer return type: char***
        p3 = fn.return_type
        assert isinstance(p3, Pointer)
        assert isinstance(p3.pointee, Pointer)
        assert isinstance(p3.pointee.pointee, Pointer)
        assert isinstance(p3.pointee.pointee.pointee, CType)
        assert p3.pointee.pointee.pointee.name == "char"

        # Check 2-level pointer parameter 1: int **matrix
        assert len(fn.parameters) == 2
        p_matrix = fn.parameters[0]
        assert p_matrix.name == "matrix"
        assert isinstance(p_matrix.type, Pointer)
        assert isinstance(p_matrix.type.pointee, Pointer)
        assert isinstance(p_matrix.type.pointee.pointee, CType)
        assert p_matrix.type.pointee.pointee.name == "int"

        # Check 2-level pointer parameter 2: void **out_handle
        p_handle = fn.parameters[1]
        assert p_handle.name == "out_handle"
        assert isinstance(p_handle.type, Pointer)
        assert isinstance(p_handle.type.pointee, Pointer)
        assert isinstance(p_handle.type.pointee.pointee, CType)
        assert p_handle.type.pointee.pointee.name == "void"

    def test_parse_multilevel_pointers_in_struct_and_typedef(self):
        code = """
        struct MatrixBundle {
            void **buffers;
            char ***labels;
        };

        typedef int **IntGrid;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "bundle.h")

        decls = header.declarations
        assert len(decls) == 2

        st = decls[0]
        assert isinstance(st, Struct)
        assert st.name == "MatrixBundle"
        assert len(st.fields) == 2

        f_buf = st.fields[0]
        assert f_buf.name == "buffers"
        assert isinstance(f_buf.type, Pointer)
        assert isinstance(f_buf.type.pointee, Pointer)
        assert isinstance(f_buf.type.pointee.pointee, CType)
        assert f_buf.type.pointee.pointee.name == "void"

        f_labels = st.fields[1]
        assert f_labels.name == "labels"
        assert isinstance(f_labels.type, Pointer)
        assert isinstance(f_labels.type.pointee, Pointer)
        assert isinstance(f_labels.type.pointee.pointee, Pointer)
        assert isinstance(f_labels.type.pointee.pointee.pointee, CType)
        assert f_labels.type.pointee.pointee.pointee.name == "char"

        td = decls[1]
        assert isinstance(td, Typedef)
        assert td.name == "IntGrid"
        assert isinstance(td.underlying_type, Pointer)
        assert isinstance(td.underlying_type.pointee, Pointer)
        assert isinstance(td.underlying_type.pointee.pointee, CType)
        assert td.underlying_type.pointee.pointee.name == "int"

    def test_parse_cpp_class_and_methods(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        class Widget {
        private:
            int m_id;
        public:
            Widget();
            explicit Widget(int id);
            virtual ~Widget();

            int get_id() const;
            void set_id(int id);
            static Widget create_default();
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "widget.hpp")

        assert len(header.declarations) == 1
        st = header.declarations[0]
        assert isinstance(st, Struct)
        assert st.name == "Widget"
        assert st.is_cppclass is True

        # Fields
        assert len(st.fields) == 1
        assert st.fields[0].name == "m_id"
        assert st.fields[0].access == "private"

        # Constructors
        assert len(st.constructors) == 2
        assert st.constructors[0].name == "Widget"
        assert st.constructors[1].name == "Widget"
        assert len(st.constructors[1].parameters) == 1

        # Destructor
        assert st.destructor is not None
        assert st.destructor.name == "~Widget"

        # Methods
        assert len(st.methods) == 3
        m_map = {m.name: m for m in st.methods}
        assert m_map["get_id"].is_const is True
        assert m_map["create_default"].is_static is True

    def test_parse_cpp_inheritance_and_virtual(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        class Shape {
        public:
            virtual void draw() = 0;
        };

        class Circle : public Shape {
        public:
            Circle();
            void draw() override;
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "shapes.hpp")

        assert len(header.declarations) == 2
        shape, circle = header.declarations[0], header.declarations[1]
        assert isinstance(shape, Struct) and isinstance(circle, Struct)

        assert shape.name == "Shape"
        assert len(shape.methods) == 1
        assert shape.methods[0].name == "draw"
        assert shape.methods[0].is_virtual is True
        assert shape.methods[0].is_pure_virtual is True

        assert circle.name == "Circle"
        assert len(circle.bases) == 1
        assert isinstance(circle.bases[0], BaseSpecifier)
        assert circle.bases[0].name == "Shape"
        assert circle.bases[0].access == "public"
        assert circle.bases[0].is_virtual is False

    def test_parse_cpp_namespaces_and_templates(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        namespace math {
        namespace linalg {

        template <typename T>
        class Vector {
        public:
            using ValueType = T;
            T x;
            T y;
            Vector(T x, T y);
            T get_x() const;
            Vector& operator+=(const Vector& other);
        };

        template <typename T>
        T dot_product(const Vector<T>& a, const Vector<T>& b);

        } // linalg
        } // math
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "linalg.hpp")

        assert len(header.declarations) == 2
        vec, dot = header.declarations[0], header.declarations[1]

        assert isinstance(vec, Struct)
        assert vec.name == "Vector"
        assert vec.namespace == "math::linalg"
        assert vec.template_params == ["T"]
        assert vec.is_cppclass is True
        assert vec.inner_typedefs.get("ValueType") == "T"

        # operator
        op = [m for m in vec.methods if m.name == "operator+="]
        assert len(op) == 1
        assert isinstance(op[0].return_type, Reference)

        assert isinstance(dot, Function)
        assert dot.name == "dot_product"
        assert dot.namespace == "math::linalg"
        assert dot.template_params == ["T"]

    def test_parse_cpp_roundtrip_cython_writer(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        class Calculator {
        public:
            int value;
            void reset();
            int add(int x);
            int multiply(int a, int b);
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "calc.hpp")

        writer = CythonWriter()
        out = writer.write(header)
        assert 'cdef extern from "calc.hpp":' in out
        assert "cdef cppclass Calculator:" in out
        assert "int value" in out
        assert "void reset()" in out
        assert "int add(int x)" in out
        assert "int multiply(int a, int b)" in out

    def test_c_source_function_definitions(self):
        """Extract non-static function definitions from C source code (.c)."""
        code = """
        struct Config {
            int timeout;
            double threshold;
        };

        static int internal_helper(int x) {
            return x * 2;
        }

        int process_data(const struct Config *cfg, double *values, int count) {
            if (!cfg) return -1;
            return 0;
        }
        """
        backend = TreeSitterBackend()
        unit = backend.parse(code, "processor.c")

        structs = [d for d in unit.declarations if isinstance(d, Struct)]
        funcs = [d for d in unit.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert structs[0].name == "Config"
        assert len(structs[0].fields) == 2
        assert structs[0].fields[0].name == "timeout"
        assert str(structs[0].fields[0].type) == "int"

        func_names = [f.name for f in funcs]
        assert "process_data" in func_names
        assert "internal_helper" not in func_names

        fn = next(f for f in funcs if f.name == "process_data")
        assert str(fn.return_type) == "int"
        assert len(fn.parameters) == 3
        assert fn.parameters[0].name == "cfg"
        assert isinstance(fn.parameters[0].type, Pointer)
        assert fn.parameters[1].name == "values"
        assert isinstance(fn.parameters[1].type, Pointer)
        assert fn.parameters[2].name == "count"
        assert str(fn.parameters[2].type) == "int"

    def test_c_variadic_function_declaration(self):
        """Verify C variadic function declarations have is_variadic=True."""
        code = "int printf(const char *format, ...);"
        backend = TreeSitterBackend()
        header = backend.parse(code, "stdio.h")

        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "printf"
        assert fn.is_variadic is True
        assert len(fn.parameters) == 1
        assert fn.parameters[0].name == "format"
        assert isinstance(fn.parameters[0].type, Pointer)

    def test_global_variables_simple_and_pointer(self):
        """Verify global variables including pointers and sized types are parsed."""
        code = """
        int* ptr;
        int arr[10];
        unsigned long count;
        extern int global_flag;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "vars.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}
        assert "ptr" in vars_by_name
        assert isinstance(vars_by_name["ptr"].type, Pointer)
        assert str(vars_by_name["ptr"].type.pointee) == "int"

        assert "arr" in vars_by_name
        assert isinstance(vars_by_name["arr"].type, Array)
        assert vars_by_name["arr"].type.size == 10
        assert str(vars_by_name["arr"].type.element_type) == "int"

        assert "count" in vars_by_name
        assert str(vars_by_name["count"].type) == "unsigned long"

        assert "global_flag" in vars_by_name
        assert str(vars_by_name["global_flag"].type) == "int"

    def test_multiple_declarators_in_single_declaration(self):
        """Verify multiple variables declared in a single statement are all captured."""
        code = "int a, *b, c[5];"
        backend = TreeSitterBackend()
        header = backend.parse(code, "multi.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}
        assert len(vars_by_name) == 3

        assert "a" in vars_by_name
        assert str(vars_by_name["a"].type) == "int"

        assert "b" in vars_by_name
        assert isinstance(vars_by_name["b"].type, Pointer)
        assert str(vars_by_name["b"].type.pointee) == "int"

        assert "c" in vars_by_name
        assert isinstance(vars_by_name["c"].type, Array)
        assert vars_by_name["c"].type.size == 5

    def test_array_of_pointers_and_pointer_to_array(self):
        """Verify complex declarators: array of pointers vs pointer to array."""
        code = """
        char *argv[5];
        char (*row_ptr)[5];
        int matrix[10][20];
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "arrays.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}

        # argv is Array of 5 Pointers to char
        argv = vars_by_name["argv"]
        assert isinstance(argv.type, Array)
        assert argv.type.size == 5
        assert isinstance(argv.type.element_type, Pointer)
        assert str(argv.type.element_type.pointee) == "char"

        # row_ptr is Pointer to Array of 5 chars
        row_ptr = vars_by_name["row_ptr"]
        assert isinstance(row_ptr.type, Pointer)
        assert isinstance(row_ptr.type.pointee, Array)
        assert row_ptr.type.pointee.size == 5
        assert str(row_ptr.type.pointee.element_type) == "char"

        # matrix is Array of 10 Arrays of 20 ints
        matrix = vars_by_name["matrix"]
        assert isinstance(matrix.type, Array)
        assert matrix.type.size == 10
        assert isinstance(matrix.type.element_type, Array)
        assert matrix.type.element_type.size == 20
        assert str(matrix.type.element_type.element_type) == "int"

    def test_function_pointer_variable_and_typedef(self):
        """Verify function pointer variable and typedef parsing."""
        code = """
        int (*handler)(int code, double val);
        typedef void (*callback_t)(const char *msg);
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "callbacks.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}
        assert "handler" in vars_by_name
        handler = vars_by_name["handler"]
        assert isinstance(handler.type, Pointer)
        assert isinstance(handler.type.pointee, FunctionPointer)
        fp = handler.type.pointee
        assert str(fp.return_type) == "int"
        assert len(fp.parameters) == 2
        assert fp.parameters[0].name == "code"
        assert str(fp.parameters[0].type) == "int"
        assert fp.parameters[1].name == "val"
        assert str(fp.parameters[1].type) == "double"

        typedefs = {d.name: d for d in header.declarations if isinstance(d, Typedef)}
        assert "callback_t" in typedefs
        cb = typedefs["callback_t"]
        assert isinstance(cb.underlying_type, Pointer)
        assert isinstance(cb.underlying_type.pointee, FunctionPointer)
        cb_fp = cb.underlying_type.pointee
        assert str(cb_fp.return_type) == "void"
        assert len(cb_fp.parameters) == 1
        assert cb_fp.parameters[0].name == "msg"

    def test_struct_with_callback_and_bitfield(self):
        """Verify struct fields handle function pointers and bitfields."""
        code = """
        struct Device {
            int id;
            int (*read)(void);
            unsigned int flags : 4;
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "dev.h")

        structs = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(structs) == 1
        st = structs[0]
        assert len(st.methods) == 0  # read must NOT be classified as a method
        fields_by_name = {f.name: f for f in st.fields}

        assert "id" in fields_by_name
        assert str(fields_by_name["id"].type) == "int"

        assert "read" in fields_by_name
        read_field = fields_by_name["read"]
        assert isinstance(read_field.type, Pointer)
        assert isinstance(read_field.type.pointee, FunctionPointer)

        assert "flags" in fields_by_name
        assert fields_by_name["flags"].bit_width == 4

    def test_opaque_struct_typedef_and_deduplication(self):
        """Opaque struct inside typedefs emits Struct and Typedef, avoiding duplicate structs."""
        code = """
        typedef struct db_connection db;
        typedef struct db_connection *db_ptr;
        typedef struct db_statement db_stmt;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "db.h")

        struct_names = [d.name for d in header.declarations if isinstance(d, Struct)]
        typedef_names = [d.name for d in header.declarations if isinstance(d, Typedef)]

        assert struct_names == ["db_connection", "db_statement"]
        assert typedef_names == ["db", "db_ptr", "db_stmt"]

        # Struct followed by typedef must not re-emit duplicate struct
        code2 = """
        struct MyStruct { int a; };
        typedef struct MyStruct MyStructAlias;
        """
        header2 = backend.parse(code2, "mystruct.h")
        structs2 = [d for d in header2.declarations if isinstance(d, Struct)]
        typedefs2 = [d for d in header2.declarations if isinstance(d, Typedef)]
        assert len(structs2) == 1
        assert structs2[0].name == "MyStruct"
        assert len(structs2[0].fields) == 1
        assert len(typedefs2) == 1
        assert typedefs2[0].name == "MyStructAlias"


@treesitter
class TestIntegerSpelling:
    """A signedness specifier standing alone names the implicit ``int`` base type.

    C11 6.7.2p2 lists ``unsigned`` and ``signed`` among the multisets that
    designate ``unsigned int`` and ``int``.  The backend collects both tokens as
    qualifiers, so the base type has to be supplied; reusing the source text for
    it repeats the specifier and renders as ``unsigned unsigned``.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("unsigned", "unsigned int"),
            ("signed", "signed int"),
            ("unsigned int", "unsigned int"),
            ("signed int", "signed int"),
            ("int", "int"),
            ("unsigned char", "unsigned char"),
            ("signed char", "signed char"),
            ("char", "char"),
            ("unsigned short", "unsigned short"),
            ("short", "short"),
            ("unsigned long", "unsigned long"),
            ("long", "long"),
            ("unsigned long long", "unsigned long long"),
            ("long long", "long long"),
            ("signed long long", "signed long long"),
            ("long double", "long double"),
            ("short int", "short int"),
            ("long int", "long int"),
            ("unsigned short int", "unsigned short int"),
            ("unsigned long int", "unsigned long int"),
        ],
    )
    def test_field_type_spelling(self, spelling: str, expected: str) -> None:
        code = textwrap.dedent(f"""\
            struct view {{
                {spelling} f;
            }};
        """)
        header = TreeSitterBackend().parse(code, "view.h")
        struct = next(d for d in header.declarations if isinstance(d, Struct))
        assert str(struct.fields[0].type) == expected

    def test_const_bare_unsigned_keeps_both_qualifier_and_base_type(self) -> None:
        """The leading ``const`` must not be mistaken for the missing base type."""
        code = textwrap.dedent("""\
            struct view {
                const unsigned f;
            };
        """)
        header = TreeSitterBackend().parse(code, "view.h")
        struct = next(d for d in header.declarations if isinstance(d, Struct))
        assert str(struct.fields[0].type) == "const unsigned int"

    def test_bare_unsigned_variable_and_return_type(self) -> None:
        """The defect lives in the shared type parser, so every position is covered."""
        code = textwrap.dedent("""\
            extern unsigned counter;
            unsigned total(unsigned n);
        """)
        header = TreeSitterBackend().parse(code, "count.h")
        variable = next(d for d in header.declarations if isinstance(d, Variable))
        function = next(d for d in header.declarations if isinstance(d, Function))
        assert str(variable.type) == "unsigned int"
        assert str(function.return_type) == "unsigned int"
        assert str(function.parameters[0].type) == "unsigned int"


@treesitter
class TestAnonymousRecordMembers:
    """Anonymous struct and union members reach the IR rather than being dropped.

    A member whose type is an anonymous record has no ``name`` field on its
    specifier, and a genuinely transparent one has no declarator either.  Both
    made the member invisible: the record was skipped as "not a nested type" and
    the field loop had nothing to iterate.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

    @pytest.mark.parametrize(
        ("code", "parent_is_union", "inner_is_union", "members"),
        [
            (
                "struct s { int tag; union { int a; float b; }; };",
                False,
                True,
                [("a", "int"), ("b", "float")],
            ),
            (
                "struct s { int tag; struct { int x; int y; }; };",
                False,
                False,
                [("x", "int"), ("y", "int")],
            ),
            (
                "union u { int tag; struct { int x; int y; }; };",
                True,
                False,
                [("x", "int"), ("y", "int")],
            ),
            (
                "union u { int tag; union { int a; float b; }; };",
                True,
                True,
                [("a", "int"), ("b", "float")],
            ),
        ],
        ids=["union_in_struct", "struct_in_struct", "struct_in_union", "union_in_union"],
    )
    def test_a_transparent_member_carries_its_record(
        self,
        code: str,
        parent_is_union: bool,
        inner_is_union: bool,
        members: list[tuple[str, str]],
    ) -> None:
        """C11 6.7.2.1p13: a declarator-less member's members belong to the parent."""
        header = TreeSitterBackend().parse(code, "anon.h")
        records = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(records) == 1, "a transparent member is not lifted to the top level"
        parent = records[0]
        assert parent.is_union is parent_is_union

        transparent = [f for f in parent.fields if f.is_anonymous_transparent]
        assert len(transparent) == 1
        inner = transparent[0].anonymous_struct
        assert inner is not None
        assert inner.name is None
        assert inner.is_union is inner_is_union
        assert [(f.name, str(f.type)) for f in inner.fields] == members

    @pytest.mark.parametrize(
        ("code", "tag", "inner_is_union", "field_type", "members"),
        [
            (
                "struct s { int tag; union { int a; float b; } u; };",
                "_s_u_u",
                True,
                "union _s_u_u",
                [("a", "int"), ("b", "float")],
            ),
            (
                "struct s { int tag; struct { int x; int y; } p; };",
                "_s_p_s",
                False,
                "struct _s_p_s",
                [("x", "int"), ("y", "int")],
            ),
            (
                "union u { int tag; struct { int x; int y; } p; };",
                "_u_p_s",
                False,
                "struct _u_p_s",
                [("x", "int"), ("y", "int")],
            ),
        ],
        ids=["union_in_struct", "struct_in_struct", "struct_in_union"],
    )
    def test_a_named_member_of_an_anonymous_type_gets_a_synthesized_tag(
        self,
        code: str,
        tag: str,
        inner_is_union: bool,
        field_type: str,
        members: list[tuple[str, str]],
    ) -> None:
        """The synthesized tag is qualified by the parent so two parents cannot collide."""
        header = TreeSitterBackend().parse(code, "anon.h")
        records = [d for d in header.declarations if isinstance(d, Struct)]
        assert [r.name for r in records] == [tag, "s" if code.startswith("struct") else "u"]

        lifted, parent = records
        assert lifted.is_union is inner_is_union
        assert [(f.name, str(f.type)) for f in lifted.fields] == members

        member = parent.fields[1]
        assert member.name == code.split("} ")[1].split(";")[0]
        assert str(member.type) == field_type
        assert member.anonymous_struct is None

    def test_the_lifted_record_precedes_the_record_that_names_it(self) -> None:
        """A tag referenced before it is declared is not valid output."""
        code = "struct s { struct { int x; } p; };"
        header = TreeSitterBackend().parse(code, "anon.h")
        names = [d.name for d in header.declarations if isinstance(d, Struct)]
        assert names.index("_s_p_s") < names.index("s")

    def test_a_typedef_alias_qualifies_the_synthesized_tag(self) -> None:
        """A tagless ``typedef struct { ... } T;`` supplies the only usable qualifier.

        Without it two typedefs each holding a member named ``pt`` would both
        synthesize ``_pt_s`` and collide at the top level.
        """
        code = textwrap.dedent("""\
            typedef struct { struct { int x; int y; } pt; } wrapper;
            typedef struct { struct { int u; int v; } pt; } other;
        """)
        header = TreeSitterBackend().parse(code, "anon.h")
        names = [d.name for d in header.declarations if isinstance(d, Struct)]
        assert names == ["_wrapper_pt_s", "wrapper", "_other_pt_s", "other"]

    def test_a_bitfield_bearing_transparent_member_keeps_its_widths(self) -> None:
        """Widths survive inside an anonymous member exactly as they do outside one."""
        code = "struct s { int tag; struct { unsigned lo : 4; unsigned hi : 4; }; };"
        header = TreeSitterBackend().parse(code, "bits.h")
        parent = next(d for d in header.declarations if isinstance(d, Struct))
        inner = parent.fields[1].anonymous_struct
        assert inner is not None
        assert [(f.name, str(f.type), f.bit_width) for f in inner.fields] == [
            ("lo", "unsigned int", 4),
            ("hi", "unsigned int", 4),
        ]

    def test_the_writer_flattens_transparent_members_into_the_parent(self) -> None:
        """The IR shape is only useful if ``outer.a`` resolves in the emitted pxd."""
        code = "struct s { int tag; union { int a; float b; }; };"
        header = TreeSitterBackend().parse(code, "anon.h")
        output = CythonWriter().write(header)
        assert (
            textwrap.dedent("""\
            cdef extern from "anon.h":

                cdef struct s:
                    int tag
                    int a
                    float b
        """)
            == output
        )


@treesitter
class TestEnumNamespaceIdentity:
    """``Enum.namespace`` is part of an enum's identity, as it is for records.

    ``_deduplicate_declarations`` keys on ``(type, name, namespace)``, so an enum
    that reports ``None`` for its namespace collides with a same-named enum from
    another namespace and one of the two is dropped.
    """

    @classmethod
    def setup_class(cls) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_cpp")

    def test_an_enum_in_a_named_namespace_records_it(self) -> None:
        code = "namespace X { enum E { A }; }\n"
        header = TreeSitterBackend().parse(code, "ns.hpp")
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert (enum.name, enum.namespace) == ("E", "X")

    def test_a_nested_namespace_is_recorded_qualified(self) -> None:
        code = "namespace a { namespace b { enum E { A }; } }\n"
        header = TreeSitterBackend().parse(code, "ns.hpp")
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert (enum.name, enum.namespace) == ("E", "a::b")

    def test_an_enum_at_global_scope_has_no_namespace(self) -> None:
        code = "enum E { A };\n"
        header = TreeSitterBackend().parse(code, "ns.hpp")
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert (enum.name, enum.namespace) == ("E", None)

    def test_an_anonymous_enum_still_records_its_namespace(self) -> None:
        code = "namespace X { enum { A }; }\n"
        header = TreeSitterBackend().parse(code, "ns.hpp")
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert (enum.name, enum.namespace) == (None, "X")

    def test_same_named_enums_in_two_namespaces_stay_distinct(self) -> None:
        """Both survive a ``(type, name, namespace)`` dedup only if the namespaces differ."""
        code = textwrap.dedent("""\
            namespace X { enum E { A = 11 }; }
            namespace Y { enum E { B = 22 }; }
        """)
        header = TreeSitterBackend().parse(code, "ns.hpp")
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        assert [(e.name, e.namespace, [(v.name, v.value) for v in e.values]) for e in enums] == [
            ("E", "X", [("A", 11)]),
            ("E", "Y", [("B", 22)]),
        ]

    def test_a_hoisted_class_member_enum_carries_the_enclosing_namespace(self) -> None:
        """A class scope is not a namespace.

        A member enum is hoisted to the top level carrying the class's ENCLOSING
        namespace, because ``namespace`` names a C++ namespace and a class is not
        one. libclang reports ``"X"`` here, and the hoist must not quietly invent
        ``"X::C"``.
        """
        code = "namespace X { class C { public: enum E { A }; }; }\n"
        header = TreeSitterBackend().parse(code, "ns.hpp")
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert (enum.name, enum.namespace) == ("E", "X")
        record = next(d for d in header.declarations if isinstance(d, Struct))
        assert (record.name, record.namespace) == ("C", "X")


@treesitter
class TestDuplicateDeclarationCollapse:
    """Genuine duplicates collapse; declarations in different namespaces survive."""

    @classmethod
    def setup_class(cls) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

    def test_a_repeated_typedef_is_emitted_once(self) -> None:
        """C11 and C++ both permit the repetition; Cython reports the second as redeclared."""
        code = textwrap.dedent("""\
            typedef int T;
            typedef int T;
        """)
        header = TreeSitterBackend().parse(code, "dup.h")
        assert [(d.name, str(d.underlying_type)) for d in header.declarations if isinstance(d, Typedef)] == [
            ("T", "int")
        ]

    def test_same_named_typedefs_in_two_namespaces_both_survive(self) -> None:
        code = textwrap.dedent("""\
            namespace X { typedef int T; }
            namespace Y { typedef int T; }
        """)
        header = TreeSitterBackend().parse(code, "dup.hpp")
        assert [(d.name, d.namespace) for d in header.declarations if isinstance(d, Typedef)] == [
            ("T", "X"),
            ("T", "Y"),
        ]

    def test_an_opaque_enum_and_its_definition_are_one_declaration(self) -> None:
        code = textwrap.dedent("""\
            enum E : int;
            enum E : int { A };
        """)
        header = TreeSitterBackend().parse(code, "enum.hpp")
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        assert [(e.name, [v.name for v in e.values]) for e in enums] == [("E", ["A"])]

    def test_a_definition_followed_by_an_opaque_redeclaration_is_one_declaration(self) -> None:
        code = textwrap.dedent("""\
            enum E : int { A };
            enum E : int;
        """)
        header = TreeSitterBackend().parse(code, "enum.hpp")
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        assert [(e.name, [v.name for v in e.values]) for e in enums] == [("E", ["A"])]

    def test_a_lone_opaque_enum_keeps_its_valueless_declaration(self) -> None:
        """Nothing else declares the tag, so dropping it would lose the type."""
        code = "enum E : int;\n"
        header = TreeSitterBackend().parse(code, "enum.hpp")
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        assert [(e.name, e.values) for e in enums] == [("E", [])]

    def test_same_named_opaque_enums_in_two_namespaces_both_survive(self) -> None:
        code = textwrap.dedent("""\
            namespace X { enum E : int; enum E : int { A }; }
            namespace Y { enum E : int; enum E : int { B }; }
        """)
        header = TreeSitterBackend().parse(code, "enum.hpp")
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        assert [(e.name, e.namespace, [v.name for v in e.values]) for e in enums] == [
            ("E", "X", ["A"]),
            ("E", "Y", ["B"]),
        ]

    def test_the_writer_emits_one_enum_block_for_an_opaque_plus_definition(self) -> None:
        """The IR collapse is only useful if Cython stops seeing a redeclaration."""
        code = textwrap.dedent("""\
            enum E : int;
            enum E : int { A };
        """)
        output = TreeSitterBackend().parse(code, "enum.hpp")
        assert CythonWriter().write(output) == textwrap.dedent("""\
            cdef extern from "enum.hpp":

                cdef enum E:
                    A
        """)


@treesitter
class TestClassMemberEnumHoisting:
    """An enum defined in a class body reaches the top level or its enumerators are lost.

    The record IR has no slot for a member enum, so before the hoist the whole
    enum -- tag and every enumerator -- vanished from the output and could not be
    named from generated bindings. libclang emits it as a sibling of the class,
    and each case below is pinned to what libclang produces for the same source.
    """

    @classmethod
    def setup_class(cls) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_cpp")

    @staticmethod
    def _enums(code: str) -> list[tuple[str | None, str | None, list[str]]]:
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        return [(d.name, d.namespace, [v.name for v in d.values]) for d in header.declarations if isinstance(d, Enum)]

    def test_an_enum_in_a_class_is_hoisted_with_its_enumerators(self) -> None:
        code = "namespace X { class C { public: enum E { A, B }; }; }\n"
        assert self._enums(code) == [("E", "X", ["A", "B"])]

    def test_an_enum_in_a_struct_is_hoisted(self) -> None:
        code = "namespace X { struct S { enum E { A, B }; }; }\n"
        assert self._enums(code) == [("E", "X", ["A", "B"])]

    def test_a_hoisted_enum_keeps_explicit_enumerator_values(self) -> None:
        """The hoist is worthless if it carries the tag but not the constants."""
        code = "class Cfg { public: enum Mode { FAST = 7, SLOW = 9 }; };\n"
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert [(v.name, v.value) for v in enum.values] == [("FAST", 7), ("SLOW", 9)]

    def test_an_anonymous_member_enum_is_hoisted_untagged(self) -> None:
        code = "namespace X { class C { public: enum { A, B }; }; }\n"
        assert self._enums(code) == [(None, "X", ["A", "B"])]

    def test_a_member_enum_with_an_explicit_underlying_type_is_hoisted(self) -> None:
        code = "namespace X { class C { public: enum E : int { A }; }; }\n"
        assert self._enums(code) == [("E", "X", ["A"])]

    def test_a_scoped_enum_member_is_hoisted_like_an_unscoped_one(self) -> None:
        """``Enum`` models no scoping, and libclang reports ``enum class`` the same way.

        The enumerators of a scoped enum are spelled ``E::A`` in C++, so the
        rendered name still needs qualification. That is a writer concern; the
        backend's job is to stop discarding the constants.
        """
        code = "namespace X { class C { public: enum class E { A, B }; }; }\n"
        assert self._enums(code) == [("E", "X", ["A", "B"])]

    def test_a_member_enum_of_a_global_class_has_no_namespace(self) -> None:
        code = "class C { public: enum E { A }; };\n"
        assert self._enums(code) == [("E", None, ["A"])]

    def test_a_member_enum_declaring_a_field_hoists_the_enum_and_keeps_the_field(self) -> None:
        """``enum G { X } m;`` declares a type and a member; neither may be dropped."""
        code = "class C { public: enum G { X } m; };\n"
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        assert self._enums(code) == [("G", None, ["X"])]
        record = next(d for d in header.declarations if isinstance(d, Struct))
        assert [f.name for f in record.fields] == ["m"]

    def test_a_hoisted_enum_precedes_the_class_it_came_from(self) -> None:
        """A declaration that names the enum must not appear before its tag."""
        code = "class C { public: enum E { A }; };\n"
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        kinds = [type(d).__name__ for d in header.declarations]
        assert kinds == ["Enum", "Struct"]

    def test_two_classes_declaring_the_same_enum_tag_both_survive(self) -> None:
        """``C1::E`` and ``C2::E`` are two tags, and neither may discard the other.

        These once collapsed to the first, silently losing ``C2``'s enumerators.
        The dedup key was the hoisted bare name, which is the same ``E`` for
        both; keying it on the record-qualified ``cpp_name`` separates them.
        ``renders_two_same_named_member_enums_with_distinct_cnames`` covers what
        the writer then does with the pair.
        """
        code = textwrap.dedent("""\
            class C1 { public: enum E { A }; };
            class C2 { public: enum E { B }; };
        """)
        assert self._enums(code) == [("E", None, ["A"]), ("E", None, ["B"])]

    def test_a_member_enum_records_its_record_qualified_cpp_name(self) -> None:
        """``namespace`` cannot carry a record, so the hoisted tag needs ``cpp_name``."""
        code = "namespace N { class C { public: enum E { A }; }; }\n"
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        enum = next(d for d in header.declarations if isinstance(d, Enum))
        assert (enum.name, enum.namespace, enum.cpp_name) == ("E", "N", "N::C::E")

    def test_renders_two_same_named_member_enums_with_distinct_cnames(self) -> None:
        """Two ``cdef enum E`` blocks would be one name Cython binds to arbitrarily."""
        code = textwrap.dedent("""\
            class C1 { public: enum E { A }; };
            class C2 { public: enum E { B }; };
        """)
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        assert CythonWriter().write(header) == textwrap.dedent("""\
            cdef extern from "member.hpp":

                cdef enum C1_E "C1::E":
                    A "C1::E::A"

                cdef cppclass C1

                cdef enum C2_E "C2::E":
                    B "C2::E::B"

                cdef cppclass C2
        """)

    def test_same_named_member_enums_in_two_namespaces_both_survive(self) -> None:
        """The dedup key is namespace-qualified, so a clash needs the same namespace."""
        code = textwrap.dedent("""\
            namespace A { class C { public: enum E { P }; }; }
            namespace B { class D { public: enum E { Q }; }; }
        """)
        assert self._enums(code) == [("E", "A", ["P"]), ("E", "B", ["Q"])]

    def test_an_enum_inside_a_nested_class_is_not_hoisted(self) -> None:
        """A nested record is not lifted to the top level, so its enum is not either.

        Hoisting it would emit a bare ``E`` whose only real spelling is
        ``Outer::Inner::E``. libclang emits nothing here.
        """
        code = "namespace X { class Outer { public: class Inner { public: enum E { A }; }; }; }\n"
        assert self._enums(code) == []

    def test_the_writer_renders_the_hoisted_enum_as_a_top_level_block(self) -> None:
        """The hoist is only useful if the enumerators reach the generated ``.pxd``."""
        code = "class Cfg { public: enum Mode { FAST, SLOW }; };\n"
        header = TreeSitterBackend().parse(code, "member.hpp", extra_args=["-x", "c++"])
        assert CythonWriter().write(header) == textwrap.dedent("""\
            cdef extern from "member.hpp":

                cdef enum Mode "Cfg::Mode":
                    FAST "Cfg::Mode::FAST"
                    SLOW "Cfg::Mode::SLOW"

                cdef cppclass Cfg
        """)


@treesitter
class TestRecordForwardDeclarationCollapse:
    """``struct S; struct S { ... };`` is one type, and Cython rejects two blocks for it.

    This mirrors the opaque-enum collapse: the definition fills the forward
    declaration already recorded rather than adding a second declaration, keyed
    by the same namespace-qualified identity. A forward declaration that is never
    defined is a different thing -- an opaque handle type -- and survives whole.
    """

    @classmethod
    def setup_class(cls) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

    @staticmethod
    def _records(code: str, filename: str = "fwd.h") -> list[tuple[str | None, str | None, list[str]]]:
        header = TreeSitterBackend().parse(code, filename)
        return [(d.name, d.namespace, [f.name for f in d.fields]) for d in header.declarations if isinstance(d, Struct)]

    def test_a_forward_declaration_then_its_definition_is_one_declaration(self) -> None:
        code = textwrap.dedent("""\
            struct S;
            struct S { int a; };
        """)
        assert self._records(code) == [("S", None, ["a"])]

    def test_a_definition_then_a_forward_redeclaration_is_one_declaration(self) -> None:
        code = textwrap.dedent("""\
            struct S { int a; };
            struct S;
        """)
        assert self._records(code) == [("S", None, ["a"])]

    def test_repeated_forward_declarations_before_a_definition_collapse(self) -> None:
        code = textwrap.dedent("""\
            struct S;
            struct S;
            struct S { int a; };
        """)
        assert self._records(code) == [("S", None, ["a"])]

    def test_a_union_forward_declaration_and_its_definition_collapse(self) -> None:
        code = textwrap.dedent("""\
            union U;
            union U { int i; float f; };
        """)
        assert self._records(code) == [("U", None, ["i", "f"])]

    def test_a_union_definition_then_a_forward_redeclaration_collapses(self) -> None:
        code = textwrap.dedent("""\
            union U { int i; float f; };
            union U;
        """)
        assert self._records(code) == [("U", None, ["i", "f"])]

    def test_an_undefined_forward_declaration_survives_intact(self) -> None:
        """NEGATIVE CONTROL. An opaque handle type is nothing but its forward declaration.

        ``typedef struct Handle Handle;`` names a type whose definition lives in
        another translation unit. Dropping the record would leave the typedef
        pointing at nothing.
        """
        code = "typedef struct Handle Handle;\n"
        header = TreeSitterBackend().parse(code, "fwd.h")
        assert self._records(code) == [("Handle", None, [])]
        assert [d.name for d in header.declarations if isinstance(d, Typedef)] == ["Handle"]

    def test_an_opaque_forward_declaration_survives_beside_a_collapsed_pair(self) -> None:
        """The collapse must key on the tag, not fire for every forward declaration."""
        code = textwrap.dedent("""\
            struct Opaque;
            struct S;
            struct S { int a; };
        """)
        assert self._records(code) == [("Opaque", None, []), ("S", None, ["a"])]

    def test_same_named_forward_declarations_in_two_namespaces_both_survive(self) -> None:
        code = textwrap.dedent("""\
            namespace A { struct S; }
            namespace B { struct S; }
        """)
        assert self._records(code, "fwd.hpp") == [("S", "A", []), ("S", "B", [])]

    def test_same_named_pairs_in_two_namespaces_collapse_independently(self) -> None:
        code = textwrap.dedent("""\
            namespace A { struct S; struct S { int a; }; }
            namespace B { struct S; struct S { int b; }; }
        """)
        assert self._records(code, "fwd.hpp") == [("S", "A", ["a"]), ("S", "B", ["b"])]

    def test_a_typedef_definition_after_a_forward_declaration_is_one_record(self) -> None:
        """The alias still applies to the record the forward declaration already put out."""
        code = textwrap.dedent("""\
            struct S;
            typedef struct S { int a; } S;
        """)
        header = TreeSitterBackend().parse(code, "fwd.h")
        records = [d for d in header.declarations if isinstance(d, Struct)]
        assert [(r.name, [f.name for f in r.fields], r.is_typedef) for r in records] == [("S", ["a"], True)]
        assert [d.name for d in header.declarations if isinstance(d, Typedef)] == []

    def test_the_writer_emits_one_definition_block_for_a_collapsed_pair(self) -> None:
        """The IR collapse is only useful if Cython stops seeing a redeclaration."""
        code = textwrap.dedent("""\
            struct S;
            struct S { int a; };
        """)
        header = TreeSitterBackend().parse(code, "fwd.h")
        assert CythonWriter().write(header) == textwrap.dedent("""\
            cdef extern from "fwd.h":

                cdef struct S:
                    int a
        """)

    def test_the_writer_still_emits_an_undefined_forward_declaration(self) -> None:
        """NEGATIVE CONTROL at the writer. An opaque handle must reach the ``.pxd``."""
        code = "typedef struct Handle Handle;\n"
        header = TreeSitterBackend().parse(code, "fwd.h")
        assert CythonWriter().write(header) == textwrap.dedent("""\
            cdef extern from "fwd.h":



                cdef struct Handle
        """)


@treesitter
class TestUnnamedBitfieldPadding:
    """An unnamed bitfield is C padding (C11 6.7.2.1p12), not an accessible member.

    tree-sitter's grammar requires a declarator, so it inserts a MISSING
    ``field_identifier`` for ``unsigned : 0``.  The Field is kept and flagged
    ``is_padding``: dropping it left the ctypes writer unable to reconstruct the
    layout, so it computed a different one than C without ever failing.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

    @staticmethod
    def _fields(code: str) -> list[tuple[str, str, int | None, bool]]:
        header = TreeSitterBackend().parse(code, "bits.h")
        record = next(d for d in header.declarations if isinstance(d, Struct))
        return [(f.name, str(f.type), f.bit_width, f.is_padding) for f in record.fields]

    def test_a_zero_width_unnamed_bitfield_is_carried_as_padding(self) -> None:
        """``unsigned : 0`` names nothing but does move the next member."""
        code = "struct s { unsigned a : 3; unsigned : 0; unsigned b : 5; };"
        assert self._fields(code) == [
            ("a", "unsigned int", 3, False),
            ("", "unsigned int", 0, True),
            ("b", "unsigned int", 5, False),
        ]

    def test_an_anonymous_padding_bitfield_is_carried_as_padding(self) -> None:
        """``unsigned : 3`` reserves bits under no name."""
        code = "struct s { unsigned a : 3; unsigned : 3; unsigned b : 5; };"
        assert self._fields(code) == [
            ("a", "unsigned int", 3, False),
            ("", "unsigned int", 3, True),
            ("b", "unsigned int", 5, False),
        ]

    def test_a_named_bitfield_keeps_its_width(self) -> None:
        """NEGATIVE CONTROL. Carrying padding must not touch declared members."""
        code = "struct s { unsigned a : 3; unsigned b : 5; };"
        assert self._fields(code) == [
            ("a", "unsigned int", 3, False),
            ("b", "unsigned int", 5, False),
        ]

    def test_a_struct_of_only_padding_has_only_padding_fields(self) -> None:
        """Every entry is padding, and none of them is a member."""
        code = "struct s { unsigned : 0; unsigned : 3; };"
        fields = self._fields(code)
        assert fields == [("", "unsigned int", 0, True), ("", "unsigned int", 3, True)]

    def test_padding_inside_an_anonymous_member_is_carried_too(self) -> None:
        """The transparent-member path walks the same field loop."""
        code = "struct s { int x; struct { unsigned a : 2; unsigned : 4; unsigned b : 2; }; };"
        header = TreeSitterBackend().parse(code, "bits.h")
        parent = next(d for d in header.declarations if isinstance(d, Struct))
        inner = parent.fields[1].anonymous_struct
        assert inner is not None
        assert [(f.name, str(f.type), f.bit_width, f.is_padding) for f in inner.fields] == [
            ("a", "unsigned int", 2, False),
            ("", "unsigned int", 4, True),
            ("b", "unsigned int", 2, False),
        ]

    def test_the_ctypes_writer_reserves_the_padded_bits(self) -> None:
        """The writer the defect actually broke.

        A Cython-only check would not catch this: ``cdef extern`` defers layout
        to the C header, while ctypes reconstructs it from the field list. The
        previous output dropped the padding and placed ``b`` at bit 3 instead of
        bit 32, and reading back ``instance.b`` still answered 1 -- the wrong
        layout was invisible from the Python side. Only the byte image shows it.
        """
        code = "struct s { unsigned a : 3; unsigned : 0; unsigned b : 5; };"
        header = TreeSitterBackend().parse(code, "bits.h")
        output = CtypesWriter().write(header)
        assert '("", ' not in output

        namespace: dict[str, object] = {}
        exec(compile(output, "generated.py", "exec"), namespace)  # noqa: S102
        record = namespace["s"]

        instance = record()
        instance.a = 5
        instance.b = 1
        assert (instance.a, instance.b) == (5, 1)
        # `: 0` pushes b into the next storage unit, so the record spans two
        # units and b's bits land in the second, not alongside a. Deriving the
        # unit width from ctypes keeps this true where `unsigned` is not 32 bits.
        unit = ctypes.sizeof(ctypes.c_uint)
        assert ctypes.sizeof(record) == 2 * unit
        assert bytes(instance) == (5).to_bytes(unit, "little") + (1).to_bytes(unit, "little")
