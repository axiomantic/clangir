"""Tests for C++ class semantics in IR and libclang backend."""

from __future__ import annotations

import json
import textwrap

import pytest

from headerkit._ir_json import json_to_header
from headerkit.backends import get_backend
from headerkit.backends.libclang import is_system_libclang_available
from headerkit.ir import BaseSpecifier, Constant, CType, Enum, Function, Reference, Struct, Typedef, Variable
from headerkit.writers.cython import write_pxd
from headerkit.writers.json import JsonWriter
from tests.skip_policy import LIBCLANG_INSTALL, missing_toolchain

libclang = pytest.mark.libclang


@pytest.fixture(autouse=True)
def _skip_if_no_libclang(request: pytest.FixtureRequest) -> None:
    marker = request.node.get_closest_marker("libclang")
    if marker is not None and not is_system_libclang_available():
        missing_toolchain("the system libclang is not available", LIBCLANG_INSTALL)


@pytest.mark.allow("subprocess")
@libclang
class TestCppClassSemantics:
    """Test parsing C++ class semantics into IR and JSON."""

    def test_probe_header_cpp_class_semantics(self) -> None:
        """Parse probe.hpp with juce::Base and juce::Model and verify full C++ IR semantics."""
        header_code = textwrap.dedent("""\
            namespace juce {
            class Base {
            public:
                virtual ~Base() = default;
            };

            class Model : public Base {
            public:
                Model(int seed);
                virtual int getNumRows() = 0;
                virtual void paintRow(int row) = 0;
                int publicMethod(const char* s) const;
                static int staticMethod();
                operator int() const;
                bool operator==(const Model& o) const;
            private:
                int privateMethod();
                int privateField;
            };
            }
        """)

        backend = get_backend("libclang")
        h = backend.parse(header_code, "probe.hpp", extra_args=["-x", "c++", "-std=c++17"])

        # Find Base and Model declarations
        structs = [d for d in h.declarations if isinstance(d, Struct)]
        base = next(s for s in structs if s.name == "Base")
        model = next(s for s in structs if s.name == "Model")

        # Verify Base
        assert base.is_cppclass is True
        assert base.namespace == "juce"
        assert base.destructor is not None
        assert base.destructor.name == "~Base"
        assert base.destructor.is_virtual is True
        assert base.destructor.is_defaulted is True
        assert base.destructor.access == "public"

        # Verify Model class level
        assert model.is_cppclass is True
        assert model.namespace == "juce"
        assert model.is_abstract is True
        assert len(model.bases) == 1
        assert model.bases[0] == BaseSpecifier(name="Base", access="public", is_virtual=False)

        # Verify Model constructors
        assert len(model.constructors) == 1
        ctor = model.constructors[0]
        assert ctor.name == "Model"
        assert ctor.access == "public"
        assert len(ctor.parameters) == 1
        assert ctor.parameters[0].name == "seed"
        assert ctor.parameters[0].type == CType("int")

        # Verify Model conversions
        assert len(model.conversions) == 1
        conv = model.conversions[0]
        assert conv.name == "operator int"
        assert conv.return_type == CType("int")
        assert conv.is_const is True
        assert conv.access == "public"

        # Verify Model methods
        methods_by_name = {m.name: m for m in model.methods}

        get_num_rows = methods_by_name["getNumRows"]
        assert get_num_rows.is_virtual is True
        assert get_num_rows.is_pure_virtual is True
        assert get_num_rows.access == "public"
        assert get_num_rows.return_type == CType("int")

        paint_row = methods_by_name["paintRow"]
        assert paint_row.is_virtual is True
        assert paint_row.is_pure_virtual is True
        assert paint_row.access == "public"
        assert paint_row.return_type == CType("void")

        public_method = methods_by_name["publicMethod"]
        assert public_method.is_const is True
        assert public_method.is_virtual is False
        assert public_method.access == "public"
        assert public_method.return_type == CType("int")

        static_method = methods_by_name["staticMethod"]
        assert static_method.is_static is True
        assert static_method.access == "public"
        assert static_method.return_type == CType("int")

        op_eq = methods_by_name["operator=="]
        assert op_eq.is_const is True
        assert op_eq.access == "public"
        assert op_eq.return_type == CType("bool")

        private_method = methods_by_name["privateMethod"]
        assert private_method.access == "private"
        assert private_method.return_type == CType("int")

        # Verify Model fields
        fields_by_name = {f.name: f for f in model.fields}
        private_field = fields_by_name["privateField"]
        assert private_field.access == "private"
        assert private_field.type == CType("int")
        assert private_field.is_static is False

        # Verify JSON writer output
        json_writer = JsonWriter()
        json_output = json_writer.write(h)

        data = json.loads(json_output)
        model_json = next(d for d in data["declarations"] if d.get("name") == "Model")
        assert model_json["is_abstract"] is True
        assert model_json["bases"] == [{"name": "Base", "access": "public"}]
        assert len(model_json["constructors"]) == 1
        assert model_json["constructors"][0]["name"] == "Model"
        assert len(model_json["conversions"]) == 1
        assert model_json["conversions"][0]["name"] == "operator int"

    def test_static_fields_and_virtual_bases(self) -> None:
        """Test virtual base inheritance and static field extraction."""
        code = textwrap.dedent("""\
            class VBase {
            public:
                int x;
            };

            class Derived : public virtual VBase {
            public:
                static int instanceCount;
            protected:
                int protectedValue;
            };
        """)

        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])
        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        derived = structs["Derived"]

        assert len(derived.bases) == 1
        assert derived.bases[0] == BaseSpecifier(name="VBase", access="public", is_virtual=True)

        fields_by_name = {f.name: f for f in derived.fields}
        assert fields_by_name["instanceCount"].is_static is True
        assert fields_by_name["instanceCount"].access == "public"
        assert fields_by_name["protectedValue"].is_static is False
        assert fields_by_name["protectedValue"].access == "protected"

    def test_notes_fallback_when_unrepresentable_member(self) -> None:
        """Negative test ensuring note is recorded if a member is skipped."""
        code = textwrap.dedent("""\
            template <typename T, int N>
            class ComplexWithNonTypeParam {
            public:
                int validField;
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])
        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        assert "ComplexWithNonTypeParam" in structs
        complex_struct = structs["ComplexWithNonTypeParam"]
        assert len(complex_struct.fields) == 1
        assert len(complex_struct.notes) == 1
        assert "Template has non-type parameter 'N'" in complex_struct.notes[0]

    def test_enum_forward_declaration_does_not_shadow_definition(self) -> None:
        """Test that enum forward declarations do not shadow definitions."""
        code = textwrap.dedent("""\
            enum Color : int;
            enum Color : int {
                RED = 1,
                GREEN = 2,
                BLUE = 3
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++11"])
        enums = [d for d in h.declarations if isinstance(d, Enum)]
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert len(enums[0].values) == 3
        assert enums[0].values[0].name == "RED"
        assert enums[0].values[0].value == 1

    def test_class_template_semantics_and_deleted_method(self) -> None:
        """Test C++ class semantics in class templates and is_deleted detection."""
        code = textwrap.dedent("""\
            template <typename T>
            class SmartPtr {
            public:
                SmartPtr(T* ptr);
                ~SmartPtr();
                SmartPtr(const SmartPtr&) = delete;
                T* get() const;
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++11"])
        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        assert "SmartPtr" in structs
        s = structs["SmartPtr"]
        assert len(s.constructors) >= 1
        assert s.destructor is not None
        # Check deleted copy constructor / method
        deleted_methods = [m for m in s.methods if m.is_deleted] + [c for c in s.constructors if c.is_deleted]
        assert len(deleted_methods) >= 1

    def test_function_and_method_templates(self) -> None:
        """Test parsing free function templates and class member method templates."""
        code = textwrap.dedent("""\
            template <typename T, typename U>
            T make_pair_first(T a, U b);

            class Builder {
            public:
                template <typename ValType>
                void setField(ValType val);
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])

        functions = {f.name: f for f in h.declarations if isinstance(f, Function)}
        assert "make_pair_first" in functions
        fn = functions["make_pair_first"]
        assert fn.template_params == ["T", "U"]
        assert len(fn.parameters) == 2

        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        builder = structs["Builder"]
        assert len(builder.methods) == 1
        method = builder.methods[0]
        assert method.name == "setField"
        assert method.template_params == ["ValType"]

        # Verify Cython writer outputs template brackets
        cython_out = write_pxd(h)
        assert "make_pair_first[T, U]" in cython_out
        assert "setField[ValType]" in cython_out

        # Verify JSON writer roundtrip preserves template_params
        json_writer = JsonWriter()
        json_output = json_writer.write(h)
        import json

        data = json.loads(json_output)
        fn_json = next(d for d in data["declarations"] if d.get("name") == "make_pair_first")
        assert fn_json["template_params"] == ["T", "U"]

    def test_function_template_coexists_with_non_template(self) -> None:
        """Test that function templates and non-template functions with the same name coexist."""
        code = textwrap.dedent("""\
            int process(int val);
            template <typename T>
            T process(T val);
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++11"])
        funcs = [d for d in h.declarations if isinstance(d, Function) and d.name == "process"]
        assert len(funcs) == 2
        template_funcs = [f for f in funcs if f.template_params]
        non_template_funcs = [f for f in funcs if not f.template_params]
        assert len(template_funcs) == 1
        assert len(non_template_funcs) == 1

    def test_references_default_params_and_noexcept(self) -> None:
        """Test parsing lvalue/rvalue references, default arguments, and noexcept specifications."""
        code = textwrap.dedent("""\
            class Processor {
            public:
                void process(const int& input, int count = 10) noexcept;
                void moveFrom(int&& source) throw();
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])

        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        proc = structs["Processor"]
        assert len(proc.methods) == 2

        m_process = next(m for m in proc.methods if m.name == "process")
        assert m_process.is_noexcept is True
        assert len(m_process.parameters) == 2
        # First parameter: const int&
        p1 = m_process.parameters[0]
        assert isinstance(p1.type, Reference)
        assert p1.type.is_rvalue is False
        assert isinstance(p1.type.target, CType)
        assert p1.type.target.name == "int"
        # Second parameter: int count = 10
        p2 = m_process.parameters[1]
        assert p2.name == "count"
        assert p2.default_value == "10"

        # Second method: moveFrom(int&&) throw()
        m_move = next(m for m in proc.methods if m.name == "moveFrom")
        assert m_move.is_noexcept is True
        assert len(m_move.parameters) == 1
        p_src = m_move.parameters[0]
        assert isinstance(p_src.type, Reference)
        assert p_src.type.is_rvalue is True

        # Verify JSON round-trip
        json_writer = JsonWriter()
        json_output = json_writer.write(h)
        data = json.loads(json_output)
        proc_json = next(d for d in data["declarations"] if d.get("name") == "Processor")
        assert proc_json["methods"][0]["is_noexcept"] is True
        assert proc_json["methods"][0]["parameters"][0]["type"]["kind"] == "reference"
        assert proc_json["methods"][0]["parameters"][1]["default_value"] == "10"
        assert proc_json["methods"][1]["parameters"][0]["type"]["is_rvalue"] is True

    def test_c_attributes_and_deprecated(self) -> None:
        """Test extraction of deprecation attributes and struct alignment."""
        code = textwrap.dedent("""\
            struct __attribute__((aligned(8))) AlignedBuffer {
                int a;
            };

            struct UnalignedBuffer {
                int a;
            };

            __attribute__((deprecated("Use new_fn instead")))
            void old_fn(void);
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.h")

        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        buf = structs["AlignedBuffer"]
        assert buf.alignment == 8
        unaligned = structs["UnalignedBuffer"]
        assert unaligned.alignment is None

        funcs = {f.name: f for f in h.declarations if isinstance(f, Function)}
        old_fn = funcs["old_fn"]
        assert old_fn.is_deprecated is True
        assert len(old_fn.attributes) > 0
        assert any("deprecated" in a.lower() for a in old_fn.attributes)

        # Verify JSON round-trip
        json_writer = JsonWriter()
        json_output = json_writer.write(h)
        data = json.loads(json_output)
        fn_json = next(d for d in data["declarations"] if d.get("name") == "old_fn")
        assert fn_json["is_deprecated"] is True
        assert "attributes" in fn_json and len(fn_json["attributes"]) > 0
        buf_json = next(d for d in data["declarations"] if d.get("name") == "AlignedBuffer")
        assert buf_json["alignment"] == 8
        unaligned_json = next(d for d in data["declarations"] if d.get("name") == "UnalignedBuffer")
        assert "alignment" not in unaligned_json or unaligned_json["alignment"] is None

    def test_unnamed_bitfields_skipped_and_transparent_anonymous_fields(self) -> None:
        """Test that unnamed bitfield padding is skipped and anonymous structs/unions have is_anonymous_transparent."""
        code = textwrap.dedent("""\
            struct BitfieldAndAnon {
                int a: 4;
                int : 4;  // unnamed padding bitfield - must be skipped
                int b: 8;
                union {
                    int x;
                    float y;
                };
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.h")
        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        s = structs["BitfieldAndAnon"]
        # Field 'a', Field 'b', plus the anonymous union field
        field_names = [f.name for f in s.fields]
        assert "a" in field_names
        assert "b" in field_names
        # The padding bitfield is carried, but flagged -- never as a bare
        # nameless member that a writer might mistake for one.
        assert [(f.name, f.bit_width) for f in s.fields if f.is_padding] == [("", 4)]
        assert all(f.name != "" or f.is_anonymous_transparent or f.is_padding for f in s.fields)
        anon_fields = [f for f in s.fields if f.is_anonymous_transparent]
        assert len(anon_fields) == 1
        # The transparent placeholder carries the anonymous union's own members,
        # so writers can flatten them into the enclosing struct (C11 semantics).
        anon_union = anon_fields[0].anonymous_struct
        assert anon_union is not None
        assert anon_union.is_union is True
        assert [f.name for f in anon_union.fields] == ["x", "y"]

        # The Cython writer flattens those members into BitfieldAndAnon.
        pxd = write_pxd(h)
        struct_body = pxd.split("cdef struct BitfieldAndAnon:")[1]
        emitted = [line.strip() for line in struct_body.splitlines() if line.strip()]
        assert "int x" in emitted
        assert "float y" in emitted
        # The C compiler lays this record out from the original header, so the
        # padding must not reappear as a member in emitted C-facing source.
        declared = [line.split("#")[0].strip() for line in emitted]
        assert declared == ["int a", "int b", "int x", "float y"]

    def test_macro_evaluation_and_inline_function(self) -> None:
        """Test constant macro arithmetic/bitwise expression evaluation and inline functions."""
        code = textwrap.dedent("""\
            #define BITMASK (1 << 3 | 0x02)
            #define OFFSET (100 + 20 * 2)
            #define FLOAT_SCALE (2.5f * 4.0f)
            #define INT_DIV (7 / 2)
            #define NEG_MOD (-7 % 3)
            #define BIT_OPS (~0x0F & (0x30 ^ 0x10) >> 2)

            static inline int add(int a, int b) {
                return a + b;
            }
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.h")

        consts = {c.name: c for c in h.declarations if isinstance(c, Constant)}
        assert "NEG_MOD" in consts
        assert consts["NEG_MOD"].evaluated_value == -1

        assert "BIT_OPS" in consts
        assert consts["BIT_OPS"].evaluated_value == 0

        consts = {c.name: c for c in h.declarations if isinstance(c, Constant)}
        assert "BITMASK" in consts
        bitmask = consts["BITMASK"]
        assert bitmask.value == 10  # (1 << 3 | 2) == 8 | 2 == 10
        assert bitmask.evaluated_value == 10
        assert bitmask.raw_expression == "( 1 << 3 | 0x02 )"

        assert "OFFSET" in consts
        offset = consts["OFFSET"]
        assert offset.value == 140
        assert offset.evaluated_value == 140

        assert "INT_DIV" in consts
        int_div = consts["INT_DIV"]
        assert int_div.evaluated_value == 3
        assert isinstance(int_div.evaluated_value, int)

        assert "FLOAT_SCALE" in consts
        float_scale = consts["FLOAT_SCALE"]
        assert float_scale.evaluated_value == 10.0

        funcs = {f.name: f for f in h.declarations if isinstance(f, Function)}
        assert "add" in funcs
        add_fn = funcs["add"]
        assert add_fn.is_inline is True
        assert add_fn.body is not None
        assert "return a + b ;" in add_fn.body

        # Verify full JSON round-trip
        json_writer = JsonWriter()
        json_output = json_writer.write(h)
        reconstructed = json_to_header(json_output)
        assert reconstructed.declarations == h.declarations

    def test_cpp_namespaces_and_vtable_entries(self) -> None:
        """Test C++ namespace tracking and vtable entry collection."""
        code = textwrap.dedent("""\
            namespace graphics {
                namespace render {
                    typedef unsigned int ColorId;
                    typedef struct TagRecord { int x; } RecordAlias;
                    typedef decltype(nullptr) NullptrAlias;
                    int global_dpi = 96;

                    class Shape {
                    public:
                        virtual void draw() = 0;
                        virtual int area() const;
                        void non_virtual_helper();
                    };
                }
            }
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])

        typedefs = {t.name: t for t in h.declarations if isinstance(t, Typedef)}
        assert "ColorId" in typedefs
        assert typedefs["ColorId"].namespace == "graphics::render"
        assert "RecordAlias" in typedefs
        assert typedefs["RecordAlias"].namespace == "graphics::render"
        assert "NullptrAlias" in typedefs
        assert typedefs["NullptrAlias"].namespace == "graphics::render"

        vars_dict = {v.name: v for v in h.declarations if isinstance(v, Variable)}
        assert "global_dpi" in vars_dict
        assert vars_dict["global_dpi"].namespace == "graphics::render"

        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        assert "Shape" in structs
        shape = structs["Shape"]
        assert shape.namespace == "graphics::render"
        assert shape.is_abstract is True
        assert len(shape.vtable_entries) == 2
        vtable_names = [m.name for m in shape.vtable_entries]
        assert "draw" in vtable_names
        assert "area" in vtable_names
        assert "non_virtual_helper" not in vtable_names

        # Verify full JSON round-trip
        json_writer = JsonWriter()
        json_output = json_writer.write(h)
        reconstructed = json_to_header(json_output)
        assert reconstructed.declarations == h.declarations
