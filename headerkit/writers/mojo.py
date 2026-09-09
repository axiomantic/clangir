"""Generate Mojo FFI bindings with sys.ffi.DLHandle and CShim support.

This module converts headerkit IR (Intermediate Representation) objects into
Mojo declaration files and client structs leveraging ``sys.ffi.DLHandle``.
It supports:
- C primitive types, structs, enums, typedefs, constants, and function bindings.
- C++ classes via flat C-ABI shims (opaque handles and OOP wrapper structs).
"""

from __future__ import annotations

import re
import textwrap
from typing import ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    Reference,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.base import DEDENT_BLOCK, BaseWriter, WriterOption, render_block_template

MOJO_KEYWORDS: set[str] = {
    "alias",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "borrowed",
    "break",
    "case",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "False",
    "finally",
    "fn",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "inout",
    "is",
    "lambda",
    "let",
    "mut",
    "None",
    "not",
    "or",
    "owned",
    "pass",
    "raise",
    "raises",
    "ref",
    "return",
    "self",
    "struct",
    "trait",
    "True",
    "try",
    "type",
    "var",
    "while",
    "with",
    "yield",
}

PRIMITIVE_MAP: dict[str, str] = {
    "void": "None",
    "bool": "Bool",
    "_Bool": "Bool",
    "char": "Int8",
    "signed char": "Int8",
    "unsigned char": "UInt8",
    "short": "Int16",
    "short int": "Int16",
    "signed short": "Int16",
    "unsigned short": "UInt16",
    "int": "Int32",
    "signed int": "Int32",
    "signed": "Int32",
    "unsigned int": "UInt32",
    "unsigned": "UInt32",
    "long": "Int64",
    "long int": "Int64",
    "signed long": "Int64",
    "unsigned long": "UInt64",
    "long long": "Int64",
    "signed long long": "Int64",
    "unsigned long long": "UInt64",
    "int8_t": "Int8",
    "uint8_t": "UInt8",
    "int16_t": "Int16",
    "uint16_t": "UInt16",
    "int32_t": "Int32",
    "uint32_t": "UInt32",
    "int64_t": "Int64",
    "uint64_t": "UInt64",
    "size_t": "Int",
    "ssize_t": "Int",
    "ptrdiff_t": "Int",
    "intptr_t": "Int",
    "uintptr_t": "UInt",
    "float": "Float32",
    "double": "Float64",
}


def _escape_identifier(name: str) -> str:
    """Escape Mojo reserved keywords by appending an underscore."""
    if name in MOJO_KEYWORDS:
        return f"{name}_"
    return name


def _sanitize_name(name: str) -> str:
    """Convert C/C++ symbols into safe Mojo identifiers."""
    name = name.replace("::", "_")
    name = name.replace("operator[]", "get_item")
    name = name.replace("operator()", "call")
    name = name.replace("operator->*", "arrow_star")
    name = name.replace("operator->", "arrow")
    name = name.replace("operator+=", "add_assign")
    name = name.replace("operator-=", "sub_assign")
    name = name.replace("operator*=", "mul_assign")
    name = name.replace("operator/=", "div_assign")
    name = name.replace("operator%=", "mod_assign")
    name = name.replace("operator<<=", "shl_assign")
    name = name.replace("operator>>=", "shr_assign")
    name = name.replace("operator&=", "and_assign")
    name = name.replace("operator|=", "or_assign")
    name = name.replace("operator^=", "xor_assign")
    name = name.replace("operator==", "eq")
    name = name.replace("operator!=", "ne")
    name = name.replace("operator<=>", "spaceship")
    name = name.replace("operator<=", "le")
    name = name.replace("operator>=", "ge")
    name = name.replace("operator<<", "shl")
    name = name.replace("operator>>", "shr")
    name = name.replace("operator++", "inc")
    name = name.replace("operator--", "dec")
    name = name.replace("operator&&", "land")
    name = name.replace("operator||", "lor")
    name = name.replace("operator<", "lt")
    name = name.replace("operator>", "gt")
    name = name.replace("operator+", "add")
    name = name.replace("operator-", "sub")
    name = name.replace("operator*", "mul")
    name = name.replace("operator/", "div")
    name = name.replace("operator%", "mod")
    name = name.replace("operator~", "bnot")
    name = name.replace("operator!", "lnot")
    name = name.replace("operator&", "band")
    name = name.replace("operator|", "bor")
    name = name.replace("operator^", "bxor")
    name = name.replace("operator=", "assign")
    name = re.sub(r"[<>, \t]+", "_", name)
    name = name.replace("&", "")
    name = name.replace("*", "Ptr")
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    clean = name.strip("_")
    return _escape_identifier(clean)


def _type_to_mojo(t: TypeExpr) -> str:
    """Format an IR type expression as a Mojo type string."""
    if isinstance(t, CType):
        raw_name = t.name
        if "::" in raw_name:
            raw_name = _sanitize_name(raw_name)

        if t.qualifiers:
            non_cv = [q for q in t.qualifiers if q not in ("const", "volatile", "restrict")]
            if non_cv:
                qualified = f"{' '.join(non_cv)} {raw_name}"
                if qualified in PRIMITIVE_MAP:
                    return PRIMITIVE_MAP[qualified]

        if raw_name in PRIMITIVE_MAP:
            return PRIMITIVE_MAP[raw_name]
        return _escape_identifier(raw_name)
    elif isinstance(t, Pointer):
        if isinstance(t.pointee, CType) and t.pointee.name == "void":
            return "UnsafePointer[NoneType]"
        if isinstance(t.pointee, FunctionPointer):
            return _type_to_mojo(t.pointee)
        return f"UnsafePointer[{_type_to_mojo(t.pointee)}]"
    elif isinstance(t, Reference):
        if isinstance(t.target, CType) and t.target.name == "void":
            return "UnsafePointer[NoneType]"
        if isinstance(t.target, FunctionPointer):
            return _type_to_mojo(t.target)
        return f"UnsafePointer[{_type_to_mojo(t.target)}]"
    elif isinstance(t, Array):
        elem = _type_to_mojo(t.element_type)
        if t.size is not None:
            return f"InlineArray[{elem}, {t.size}]"
        return f"UnsafePointer[{elem}]"
    elif isinstance(t, FunctionPointer):
        params_str = ", ".join(_type_to_mojo(p.type) for p in t.parameters)
        ret_mojo = _type_to_mojo(t.return_type)
        return f"fn({params_str}) -> {ret_mojo}"
    return str(t)


def _extract_function_signatures(unit: SourceUnit | Header) -> list[tuple[str, str]]:
    """Extract symbol names and corresponding Mojo FFI function signatures."""
    signatures: list[tuple[str, str]] = []

    # 1. Free functions
    free_fns = [d for d in getattr(unit, "declarations", []) if isinstance(d, Function) and d.name]
    for fn in free_fns:
        types_sig = ", ".join(_type_to_mojo(p.type) for p in fn.parameters)
        ret_mojo = _type_to_mojo(fn.return_type)
        signatures.append((fn.name, f"fn({types_sig}) -> {ret_mojo}"))

    # 2. C++ Class Functions (constructors, destructors, methods)
    classes = [d for d in getattr(unit, "declarations", []) if isinstance(d, Struct) and (d.is_cppclass or d.methods)]
    for cls in classes:
        if not cls.name:
            continue
        cls_full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
        safe_cls_name = _sanitize_name(cls_full_name)

        for idx, ctor in enumerate(cls.constructors):
            fn_name = f"{safe_cls_name}_create" if idx == 0 else f"{safe_cls_name}_create_{idx}"
            param_types = [_type_to_mojo(p.type) for p in ctor.parameters]
            signatures.append((fn_name, f"fn({', '.join(param_types)}) -> UnsafePointer[NoneType]"))

        if cls.destructor or cls.methods:
            fn_dtor = f"{safe_cls_name}_destroy"
            signatures.append((fn_dtor, "fn(UnsafePointer[NoneType]) -> None"))

        for method in cls.methods:
            if method.access in ("private", "protected"):
                continue
            m_safe = _sanitize_name(method.name)
            fn_method_name = f"{safe_cls_name}_{m_safe}"
            if fn_method_name == f"{safe_cls_name}_destroy":
                fn_method_name = f"{safe_cls_name}_method_{m_safe}"

            param_types = []
            if not method.is_static:
                param_types.append("UnsafePointer[NoneType]")
            for p in method.parameters:
                param_types.append(_type_to_mojo(p.type))
            ret_m = _type_to_mojo(method.return_type)
            signatures.append((fn_method_name, f"fn({', '.join(param_types)}) -> {ret_m}"))

    return signatures


class MojoWriter(BaseWriter):
    """Writer that generates Mojo FFI bindings with sys.ffi.DLHandle and CShim support."""

    name: str = "mojo"
    format_description: str = "Mojo FFI binding generator (sys.ffi.DLHandle and CShim)"
    default_output_pattern: str = "{dir}/{stem}.mojo"
    default_extension: str = ".mojo"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project")
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
        WriterOption(
            name="library_name",
            description="Name of the shared library to load via DLHandle",
            default="Library",
            type=str,
        ),
        WriterOption(
            name="emit_classes",
            description="Emit class wrappers and handle aliases",
            default=True,
            type=bool,
        ),
    )

    def __init__(
        self,
        *,
        library_name: str = "Library",
        emit_classes: bool = True,
    ) -> None:
        self.library_name = library_name
        self.emit_classes = emit_classes

    def _render(self, header: Header | SourceUnit) -> str:
        """Convert headerkit IR to Mojo FFI declarations and wrapper client."""
        lines: list[str] = [
            "# Auto-generated Mojo bindings by HeaderKit",
            "from collections import InlineArray",
            "from sys.ffi import DLHandle",
            "from memory import UnsafePointer",
            "",
        ]

        # 1. Forward declare opaque handle types for C++ classes
        classes = [d for d in header.declarations if isinstance(d, Struct) and (d.is_cppclass or d.methods)]
        if classes:
            lines.append("# =============================================================================")
            lines.append("# Opaque Handles for C++ Classes")
            lines.append("# =============================================================================")
            for cls in classes:
                if cls.name:
                    full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
                    safe_name = _sanitize_name(full_name)
                    lines.append(f"alias {safe_name}_t = UnsafePointer[NoneType]")
            lines.append("")

        # 2. Constants and Typedefs
        typedefs = [d for d in header.declarations if isinstance(d, Typedef)]
        constants = [d for d in header.declarations if isinstance(d, Constant)]
        if typedefs or constants:
            lines.append("# =============================================================================")
            lines.append("# Type Aliases and Constants")
            lines.append("# =============================================================================")
            for td in typedefs:
                safe_td_name = _escape_identifier(td.name)
                target_type = _type_to_mojo(td.underlying_type)
                lines.append(f"alias {safe_td_name} = {target_type}")
            for c in constants:
                safe_c_name = _escape_identifier(c.name)
                t_str = _type_to_mojo(c.type) if c.type else "Int32"
                val_str = str(c.value)
                lines.append(f"alias {safe_c_name}: {t_str} = {val_str}")
            lines.append("")

        # 3. Enums
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        if enums:
            lines.append("# =============================================================================")
            lines.append("# Enumerations")
            lines.append("# =============================================================================")
            for enum in enums:
                enum_name = _escape_identifier(enum.name) if enum.name else "AnonymousEnum"
                lines.append("@value")
                lines.append('@register_passable("trivial")')
                lines.append(f"struct {enum_name}:")
                lines.append("    var value: Int32")
                lines.append("")
                for val in enum.values:
                    v_name = _escape_identifier(val.name)
                    lines.append(f"    alias {v_name} = {enum_name}({val.value})")
                lines.append("")

        # 4. Plain C Structs
        c_structs = [d for d in header.declarations if isinstance(d, Struct) and not (d.is_cppclass or d.methods)]
        if c_structs:
            lines.append("# =============================================================================")
            lines.append("# C Structs")
            lines.append("# =============================================================================")
            for st in c_structs:
                st_name = _escape_identifier(st.name) if st.name else "AnonymousStruct"
                lines.append("@value")
                lines.append('@register_passable("trivial")')
                lines.append(f"struct {st_name}:")
                if not st.fields:
                    lines.append("    var _opaque: UnsafePointer[NoneType]")
                else:
                    for field in st.fields:
                        f_name = _escape_identifier(field.name)
                        f_type = _type_to_mojo(field.type)
                        lines.append(f"    var {f_name}: {f_type}")
                lines.append("")

        # 5. Library Client Struct (DLHandle)
        lines.append("# =============================================================================")
        lines.append(f"# {self.library_name} Client")
        lines.append("# =============================================================================")
        lines.append(f"struct {self.library_name}:")
        lines.append("    var handle: DLHandle")
        lines.append("")
        lines.append("    fn __init__(out self, path: String) raises:")
        lines.append("        self.handle = DLHandle(path)")
        lines.append("")
        lines.append("    fn close(mut self):")
        lines.append("        self.handle.close()")
        lines.append("")

        # Free functions
        free_fns = [d for d in header.declarations if isinstance(d, Function)]
        for fn in free_fns:
            fn_sym_name = fn.name
            full_fn_name = f"{fn.namespace}::{fn.name}" if fn.namespace else fn.name
            safe_fn_name = _sanitize_name(full_fn_name)

            if getattr(fn, "is_variadic", False):
                lines.append(f"    # Warning: '{fn.name}' is a C variadic function; only fixed parameters are bound.")

            ret_mojo = _type_to_mojo(fn.return_type)
            ret_sig = f" -> {ret_mojo}" if ret_mojo != "None" else ""

            param_defs: list[str] = []
            param_types: list[str] = []
            param_names: list[str] = []
            for i, p in enumerate(fn.parameters):
                p_name = _escape_identifier(p.name or f"arg{i}")
                p_type = _type_to_mojo(p.type)
                param_defs.append(f"{p_name}: {p_type}")
                param_types.append(p_type)
                param_names.append(p_name)

            all_params = ["self"] + param_defs
            types_sig = ", ".join(param_types)
            names_call = ", ".join(param_names)

            lines.append(f"    fn {safe_fn_name}({', '.join(all_params)}){ret_sig}:")
            lines.append(f'        var f = self.handle.get_function[fn({types_sig}) -> {ret_mojo}]("{fn_sym_name}")')
            if ret_mojo != "None":
                lines.append(f"        return f({names_call})")
            else:
                lines.append(f"        f({names_call})")
            lines.append("")

        # Shimmed C++ Class Functions on Library
        for cls in classes:
            if not cls.name:
                continue
            cls_full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
            safe_cls_name = _sanitize_name(cls_full_name)

            # Constructors
            for idx, ctor in enumerate(cls.constructors):
                fn_name = f"{safe_cls_name}_create" if idx == 0 else f"{safe_cls_name}_create_{idx}"
                param_defs = []
                param_types = []
                param_names = []
                for i, p in enumerate(ctor.parameters):
                    p_name = _escape_identifier(p.name or f"arg{i}")
                    p_type = _type_to_mojo(p.type)
                    param_defs.append(f"{p_name}: {p_type}")
                    param_types.append(p_type)
                    param_names.append(p_name)

                all_params = ["self"] + param_defs
                lines.append(f"    fn {fn_name}({', '.join(all_params)}) -> UnsafePointer[NoneType]:")
                lines.append(
                    f'        var f = self.handle.get_function[fn({", ".join(param_types)}) -> UnsafePointer[NoneType]]("{fn_name}")'
                )
                lines.append(f"        return f({', '.join(param_names)})")
                lines.append("")

            # Destructor
            fn_dtor = f"{safe_cls_name}_destroy"
            lines.append(f"    fn {fn_dtor}(self, self_ptr: UnsafePointer[NoneType]):")
            lines.append(f'        var f = self.handle.get_function[fn(UnsafePointer[NoneType]) -> None]("{fn_dtor}")')
            lines.append("        f(self_ptr)")
            lines.append("")

            # Methods
            for method in cls.methods:
                if method.access in ("private", "protected"):
                    continue
                if getattr(method, "is_variadic", False):
                    lines.append(
                        f"    # Warning: '{method.name}' is a C variadic method; only fixed parameters are bound."
                    )
                m_safe = _sanitize_name(method.name)
                fn_method_name = f"{safe_cls_name}_{m_safe}"
                if fn_method_name == fn_dtor:
                    fn_method_name = f"{safe_cls_name}_method_{m_safe}"

                ret_m = _type_to_mojo(method.return_type)
                ret_sig = f" -> {ret_m}" if ret_m != "None" else ""

                param_defs = []
                param_types = []
                param_names = []

                if not method.is_static:
                    param_defs.append("self_ptr: UnsafePointer[NoneType]")
                    param_types.append("UnsafePointer[NoneType]")
                    param_names.append("self_ptr")

                for i, p in enumerate(method.parameters):
                    p_name = _escape_identifier(p.name or f"arg{i}")
                    p_type = _type_to_mojo(p.type)
                    param_defs.append(f"{p_name}: {p_type}")
                    param_types.append(p_type)
                    param_names.append(p_name)

                all_params = ["self"] + param_defs
                lines.append(f"    fn {fn_method_name}({', '.join(all_params)}){ret_sig}:")
                lines.append(
                    f'        var f = self.handle.get_function[fn({", ".join(param_types)}) -> {ret_m}]("{fn_method_name}")'
                )
                if ret_m != "None":
                    lines.append(f"        return f({', '.join(param_names)})")
                else:
                    lines.append(f"        f({', '.join(param_names)})")
                lines.append("")

        # 6. High-level OOP Struct Wrappers for C++ Classes
        if self.emit_classes and classes:
            lines.append("# =============================================================================")
            lines.append("# High-level C++ Class Wrappers")
            lines.append("# =============================================================================")
            for cls in classes:
                if not cls.name:
                    continue
                cls_full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
                safe_cls_name = _sanitize_name(cls_full_name)

                lines.append(f"struct {safe_cls_name}:")
                lines.append("    var handle: UnsafePointer[NoneType]")
                lines.append("")
                lines.append("    fn __init__(out self, handle: UnsafePointer[NoneType]):")
                lines.append("        self.handle = handle")
                lines.append("")

                # Constructors
                for idx, ctor in enumerate(cls.constructors):
                    fn_create = f"{safe_cls_name}_create" if idx == 0 else f"{safe_cls_name}_create_{idx}"
                    ctor_name = "create" if idx == 0 else f"create_{idx}"
                    param_defs = [f"lib: {self.library_name}"]
                    call_names = []
                    for i, p in enumerate(ctor.parameters):
                        p_name = _escape_identifier(p.name or f"arg{i}")
                        p_type = _type_to_mojo(p.type)
                        param_defs.append(f"{p_name}: {p_type}")
                        call_names.append(p_name)

                    lines.append("    @staticmethod")
                    lines.append(f"    fn {ctor_name}({', '.join(param_defs)}) -> Self:")
                    lines.append(f"        return Self(lib.{fn_create}({', '.join(call_names)}))")
                    lines.append("")

                # Destructor
                fn_dtor = f"{safe_cls_name}_destroy"
                lines.append(f"    fn destroy(mut self, lib: {self.library_name}):")
                lines.append(f"        lib.{fn_dtor}(self.handle)")
                lines.append("")

                # Methods
                for method in cls.methods:
                    if method.access in ("private", "protected"):
                        continue
                    if getattr(method, "is_variadic", False):
                        lines.append(
                            f"    # Warning: '{method.name}' is a C variadic method; only fixed parameters are bound."
                        )
                    m_safe = _sanitize_name(method.name)
                    fn_method_name = f"{safe_cls_name}_{m_safe}"
                    if fn_method_name == fn_dtor:
                        fn_method_name = f"{safe_cls_name}_method_{m_safe}"

                    ret_m = _type_to_mojo(method.return_type)
                    ret_sig = f" -> {ret_m}" if ret_m != "None" else ""

                    if method.is_static:
                        param_defs = [f"lib: {self.library_name}"]
                        call_args = []
                    else:
                        param_defs = ["self", f"lib: {self.library_name}"]
                        call_args = ["self.handle"]

                    for i, p in enumerate(method.parameters):
                        p_name = _escape_identifier(p.name or f"arg{i}")
                        p_type = _type_to_mojo(p.type)
                        param_defs.append(f"{p_name}: {p_type}")
                        call_args.append(p_name)

                    if method.is_static:
                        lines.append("    @staticmethod")
                    lines.append(f"    fn {m_safe}({', '.join(param_defs)}){ret_sig}:")
                    if ret_m != "None":
                        lines.append(f"        return lib.{fn_method_name}({', '.join(call_args)})")
                    else:
                        lines.append(f"        lib.{fn_method_name}({', '.join(call_args)})")
                    lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        bindings_code = self._render(unit)

        files: list[OutputFile] = []

        # 1. mojoproject.toml
        mojo_proj = textwrap.dedent(f"""\
            [project]
            name = "{pkg}"
            version = "0.1.0"
            description = "Mojo bindings for {pkg}"
        """)
        files.append(OutputFile(path="mojoproject.toml", content=mojo_proj))

        # 2. Package init
        init_mojo = textwrap.dedent("""\
            from .bindings import *
        """)
        files.append(OutputFile(path=f"src/{pkg}/__init__.mojo", content=init_mojo))

        # 3. Bindings
        files.append(OutputFile(path=f"src/{pkg}/bindings.mojo", content=bindings_code))

        # 4. Tests
        if test_type in ("tripwire", "both"):
            signatures = _extract_function_signatures(unit)
            tw_lines = [f'    _ = handle.get_function[{sig}]("{sym}")' for sym, sig in signatures]
            tw_body = "\n".join(tw_lines) if tw_lines else "    # Library opened successfully"

            tripwire = render_block_template(
                f"""\
                from sys.ffi import DLHandle

                fn test_tripwire_bindings() raises:
                    # Tripwire: verifies foreign dynamic library can be opened and symbols resolved
                    var handle = DLHandle("{pkg}")
                {DEDENT_BLOCK}
                    handle.close()

                fn main() raises:
                    test_tripwire_bindings()
            """,
                tw_body,
            )
            files.append(OutputFile(path="tests/test_tripwire.mojo", content=tripwire))

        if test_type in ("unit", "both"):
            unit_test = textwrap.dedent(f"""\
                from {pkg}.bindings import Library

                fn test_{pkg}_binding_types():
                    # Verify Library struct type is defined and accessible
                    var ptr_size = sizeof[Library]()
                    if ptr_size == 0:
                        print("Warning: unexpected 0-sized Library struct")

                fn main():
                    test_{pkg}_binding_types()
            """)
            files.append(OutputFile(path=f"tests/test_{pkg}.mojo", content=unit_test))

        return ProjectLayout(files=files)


def write_mojo(
    header: Header | SourceUnit,
    *,
    library_name: str = "Library",
    emit_classes: bool = True,
) -> str:
    """Convenience function to generate Mojo bindings from Headerkit IR."""
    return MojoWriter(library_name=library_name, emit_classes=emit_classes).write(header)


# Self-register
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "mojo",
    MojoWriter,
    description="Mojo FFI binding generator (sys.ffi.DLHandle and CShim)",
)
