"""Generate C-ABI shim wrappers (extern "C") around C++ libraries from headerkit IR.

This module converts C++ structs, classes, member methods, constructors,
destructors, and free functions into a C-compatible API with ``extern "C"``
guards and opaque handles (``typedef struct Foo Foo;``).
"""

from __future__ import annotations

import collections
import re
import textwrap
from typing import ClassVar

from headerkit.ir import (
    Array,
    CType,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    SourceUnit,
    Struct,
    TypeExpr,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.base import DEDENT_BLOCK, BaseWriter, WriterOption, render_block_template


def _sanitize_name(name: str, num_params: int | None = None) -> str:
    """Convert C++ symbols (e.g. namespaces, operators, templates) into safe C identifiers."""
    name = name.replace("::", "_")

    # Conversion / cast operators: operator bool, operator int, etc.
    if name.startswith("operator "):
        target_type = name[9:].strip()
        return f"to_{_sanitize_name(target_type)}"

    # Unary operators when num_params == 0 (e.g. unary *, unary -, unary +)
    unary_ops = {
        "operator*": "deref",
        "operator-": "neg",
        "operator+": "pos",
        "operator~": "bnot",
        "operator!": "lnot",
    }
    if num_params == 0 and name in unary_ops:
        return unary_ops[name]

    # Compound & multi-char operators first
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
    # Single-char operators
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
    # Templates, whitespace, pointer/reference markers in type names
    name = re.sub(r"[<>, \t]+", "_", name)
    name = name.replace("&", "")
    name = name.replace("*", "Ptr")
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    return name.strip("_")


def _is_std_string(t: TypeExpr) -> bool:
    """Check if type expression represents by-value or const-ref std::string."""
    if isinstance(t, Reference):
        target = t.target
        if isinstance(target, CType):
            if "const" not in target.qualifiers and not target.name.strip().startswith("const "):
                return False
            t = target
        else:
            return False
    if isinstance(t, CType):
        raw = t.name.strip()
        if raw.startswith("const "):
            raw = raw[6:].strip()
        return raw in ("std::string", "string")
    return False


def _is_std_string_view(t: TypeExpr) -> bool:
    """Check if type expression represents by-value or const-ref std::string_view."""
    if isinstance(t, Reference):
        target = t.target
        if isinstance(target, CType):
            if "const" not in target.qualifiers and not target.name.strip().startswith("const "):
                return False
            t = target
        else:
            return False
    if isinstance(t, CType):
        raw = t.name.strip()
        if raw.startswith("const "):
            raw = raw[6:].strip()
        return raw in ("std::string_view", "string_view")
    return False


def _is_std_vector(t: TypeExpr) -> str | None:
    """Check if type expression represents by-value or const-ref std::vector<T>, returning T if so."""
    if isinstance(t, Reference):
        target = t.target
        if isinstance(target, CType):
            if "const" not in target.qualifiers and not target.name.strip().startswith("const "):
                return None
            t = target
        else:
            return None
    if isinstance(t, CType):
        raw = t.name.strip()
        if raw.startswith("const "):
            raw = raw[6:].strip()
        m = re.match(r"^(?:std::)?vector<\s*(.*?)\s*>$", raw)
        if m:
            return m.group(1).strip()
    return None


def _type_to_c(t: TypeExpr) -> str:
    """Format an IR type expression as a pure C type string."""
    if _is_std_string(t) or _is_std_string_view(t):
        return "const char*"
    if isinstance(t, CType):
        name = t.name
        if "::" in name:
            name = _sanitize_name(name)
        if t.qualifiers:
            return f"{' '.join(t.qualifiers)} {name}"
        return name
    elif isinstance(t, Pointer):
        return f"{_type_to_c(t.pointee)}*"
    elif isinstance(t, Reference):
        # In C-ABI, references become pointers
        target = t.target
        if isinstance(target, CType) and (_is_std_string(target) or _is_std_string_view(target)):
            name = _sanitize_name(target.name)
            return f"{name}*"
        return f"{_type_to_c(t.target)}*"
    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{_type_to_c(t.element_type)}[{size_str}]"
    elif isinstance(t, FunctionPointer):
        param_strs = [_type_to_c(p.type) for p in t.parameters]
        if getattr(t, "is_variadic", False):
            param_strs.append("...")
        params = ", ".join(param_strs) if param_strs else "void"
        return f"{_type_to_c(t.return_type)} (*)({params})"
    return str(t)


def _format_param(p: Parameter, param_name: str | None = None) -> str:
    """Format a single C parameter."""
    name = param_name or p.name or "arg"
    if isinstance(p.type, Array):
        size_str = str(p.type.size) if p.type.size is not None else ""
        return f"{_type_to_c(p.type.element_type)} {name}[{size_str}]"
    elif isinstance(p.type, FunctionPointer):
        params_list = [_format_param(param) for param in p.type.parameters]
        if getattr(p.type, "is_variadic", False):
            params_list.append("...")
        params = ", ".join(params_list) if params_list else "void"
        return f"{_type_to_c(p.type.return_type)} (*{name})({params})"
    type_str = _type_to_c(p.type)
    return f"{type_str} {name}"


def _format_parameters_and_call_args(
    parameters: list[Parameter],
) -> tuple[list[str], list[str]]:
    """Format parameter list for C prototype and corresponding C++ call expressions."""
    params_c: list[str] = []
    call_args: list[str] = []
    for i, p in enumerate(parameters):
        p_name = p.name or f"arg{i}"
        vec_elem = _is_std_vector(p.type)
        if vec_elem:
            elem_c = _type_to_c(CType(vec_elem))
            params_c.append(f"const {elem_c}* {p_name}_data")
            params_c.append(f"size_t {p_name}_count")
            call_args.append(f"std::vector<{vec_elem}>({p_name}_data, {p_name}_data + {p_name}_count)")
        elif _is_std_string(p.type) or _is_std_string_view(p.type):
            params_c.append(f"const char* {p_name}")
            call_args.append(p_name)
        else:
            params_c.append(_format_param(p, p_name))
            call_args.append(p_name)
    return params_c, call_args


class CShimWriter(BaseWriter):
    """Writer that generates C-ABI wrapper shims around C++ headers.

    Emits two parts (or combined header/source):
    - ``extern "C"`` C API declarations
    - C++ implementation wrapping member calls, constructors, and destructors.
    """

    name: str = "cshim"
    format_description: str = "C-ABI shim wrapper generator (extern C)"
    default_output_pattern: str = "{dir}/{stem}_cshim.cpp"
    default_extension: str = ".cpp"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project", "cmake")
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
        WriterOption(
            name="catch_exceptions",
            description="Wrap C++ functions in try-catch blocks for exception safety across ABI boundaries",
            default=False,
            type=bool,
        ),
    )

    def __init__(self, *, catch_exceptions: bool = False) -> None:
        self.catch_exceptions = catch_exceptions

    def _render_parts(self, unit: SourceUnit | Header) -> tuple[str, list[str], list[str]]:
        """Generate C-ABI header code, cpp implementations, and exported symbol names."""
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        lines: list[str] = [
            "// Auto-generated C-ABI shim by HeaderKit",
            "#pragma once",
            "",
            "#ifdef __cplusplus",
            'extern "C" {',
            "#endif",
            "",
        ]

        def _make_catch_stmt(ret_type: str) -> str:
            if ret_type == "void":
                return "return;"
            elif ret_type.endswith("*"):
                return "return nullptr;"
            return f"return static_cast<{ret_type}>(0);"

        # 1. Forward declare opaque handles for classes
        classes = [d for d in header.declarations if isinstance(d, Struct) and (d.is_cppclass or d.methods)]
        classes_by_full_name: dict[str, Struct] = {}
        classes_by_short_name: dict[str, list[Struct]] = collections.defaultdict(list)
        for cls in classes:
            if cls.name:
                classes_by_short_name[cls.name].append(cls)
                full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
                classes_by_full_name[full_name] = cls

        def _resolve_base_class(base_name: str, enclosing_ns: str | None) -> Struct | None:
            # 1. Exact fully-qualified match
            if base_name in classes_by_full_name:
                return classes_by_full_name[base_name]
            # 2. Enclosing namespace prefix
            if enclosing_ns:
                prefixed = f"{enclosing_ns}::{base_name}"
                if prefixed in classes_by_full_name:
                    return classes_by_full_name[prefixed]
            # 3. Unambiguous short name
            candidates = classes_by_short_name.get(base_name, [])
            if len(candidates) == 1:
                return candidates[0]
            # 4. Ambiguous short name: match same enclosing namespace
            for cand in candidates:
                if cand.namespace == enclosing_ns:
                    return cand
            return None

        if classes:
            lines.append("/* Opaque Handle Types */")
            for cls in classes:
                if cls.name:
                    safe_name = _sanitize_name(f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name)
                    lines.append(f"typedef struct {safe_name}_s {safe_name}_t;")
            lines.append("")

        def _collect_base_methods(s: Struct) -> list[Function]:
            inherited: list[Function] = []
            seen: set[str] = set()
            for b in s.bases:
                if b.access in ("private", "protected"):
                    continue
                base_cls = _resolve_base_class(b.name, s.namespace)
                if base_cls:
                    for m in base_cls.methods:
                        if m.access in ("private", "protected"):
                            continue
                        if m.name not in seen:
                            seen.add(m.name)
                            inherited.append(m)
                    for sub_m in _collect_base_methods(base_cls):
                        if sub_m.name not in seen:
                            seen.add(sub_m.name)
                            inherited.append(sub_m)
            return inherited

        # 2. C Shim Prototypes & Implementations
        cpp_lines: list[str] = []
        exported_names: list[str] = []

        for decl in header.declarations:
            if isinstance(decl, Struct) and (decl.is_cppclass or decl.methods):
                cls = decl
                if not cls.name:
                    continue
                cls_full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
                safe_cls_name = _sanitize_name(cls_full_name)

                # Constructors
                for idx, ctor in enumerate(cls.constructors):
                    fn_name = f"{safe_cls_name}_create" if idx == 0 else f"{safe_cls_name}_create_{idx}"
                    params_c, params_call = _format_parameters_and_call_args(ctor.parameters)

                    # Header prototype
                    proto = f"{safe_cls_name}_t* {fn_name}({', '.join(params_c) if params_c else 'void'});"
                    lines.append(proto)
                    exported_names.append(fn_name)

                    # C++ wrapper
                    new_call = f"return reinterpret_cast<{safe_cls_name}_t*>(new (std::nothrow) {cls_full_name}({', '.join(params_call)}));"
                    if self.catch_exceptions:
                        body = render_block_template(
                            f"""\
                            {safe_cls_name}_t* {fn_name}({", ".join(params_c) if params_c else "void"}) {{
                                try {{
                                    {DEDENT_BLOCK}
                                }} catch (...) {{
                                    return nullptr;
                                }}
                            }}
                        """,
                            new_call,
                        )
                    else:
                        body = render_block_template(
                            f"""\
                            {safe_cls_name}_t* {fn_name}({", ".join(params_c) if params_c else "void"}) {{
                                {DEDENT_BLOCK}
                            }}
                        """,
                            new_call,
                        )
                    cpp_lines.append(body)

                # Destructor
                fn_dtor = f"{safe_cls_name}_destroy"
                lines.append(f"void {fn_dtor}({safe_cls_name}_t* self);")
                exported_names.append(fn_dtor)
                if self.catch_exceptions:
                    dtor_body = textwrap.dedent(f"""\
                        void {fn_dtor}({safe_cls_name}_t* self) {{
                            if (self) {{
                                try {{
                                    delete reinterpret_cast<{cls_full_name}*>(self);
                                }} catch (...) {{
                                }}
                            }}
                        }}
                    """)
                else:
                    dtor_body = textwrap.dedent(f"""\
                        void {fn_dtor}({safe_cls_name}_t* self) {{
                            if (self) {{
                                delete reinterpret_cast<{cls_full_name}*>(self);
                            }}
                        }}
                    """)
                cpp_lines.append(dtor_body)

                # Upcast helpers for base classes
                for base in cls.bases:
                    if base.access in ("private", "protected") or not base.name:
                        continue
                    base_cls = _resolve_base_class(base.name, cls.namespace)
                    if base_cls and base_cls.name:
                        base_full_name = (
                            f"{base_cls.namespace}::{base_cls.name}" if base_cls.namespace else base_cls.name
                        )
                    else:
                        base_full_name = base.name
                    safe_base = _sanitize_name(base_full_name)
                    upcast_fn = f"{safe_cls_name}_as_{safe_base}"
                    proto = f"{safe_base}_t* {upcast_fn}({safe_cls_name}_t* self);"
                    lines.append(proto)
                    exported_names.append(upcast_fn)

                    upcast_body = textwrap.dedent(f"""\
                        {safe_base}_t* {upcast_fn}({safe_cls_name}_t* self) {{
                            if (!self) return nullptr;
                            return reinterpret_cast<{safe_base}_t*>(static_cast<{base_full_name}*>(reinterpret_cast<{cls_full_name}*>(self)));
                        }}
                    """)
                    cpp_lines.append(upcast_body)

                # Methods (own methods + flattened inherited base methods)
                cls_method_names = {m.name for m in cls.methods}
                base_methods = [m for m in _collect_base_methods(cls) if m.name not in cls_method_names]
                all_methods = list(cls.methods) + base_methods

                for method in all_methods:
                    if method.access in ("private", "protected"):
                        continue
                    m_safe_name = _sanitize_name(method.name, len(method.parameters))
                    fn_method_name = f"{safe_cls_name}_{m_safe_name}"
                    if fn_method_name == fn_dtor:
                        fn_method_name = f"{safe_cls_name}_method_{m_safe_name}"

                    if fn_method_name in exported_names:
                        idx = 1
                        while f"{fn_method_name}_{idx}" in exported_names:
                            idx += 1
                        fn_method_name = f"{fn_method_name}_{idx}"

                    ret_type_c = _type_to_c(method.return_type)
                    params_c, call_args = _format_parameters_and_call_args(method.parameters)
                    if not method.is_static:
                        params_c.insert(0, f"{safe_cls_name}_t* self")
                    if method.is_variadic:
                        params_c.append("...")

                    proto = f"{ret_type_c} {fn_method_name}({', '.join(params_c) if params_c else 'void'});"
                    lines.append(proto)
                    exported_names.append(fn_method_name)

                    # Implement method
                    if method.is_static:
                        call_expr = f"{cls_full_name}::{method.name}({', '.join(call_args)})"
                    else:
                        call_expr = f"reinterpret_cast<{cls_full_name}*>(self)->{method.name}({', '.join(call_args)})"

                    if ret_type_c == "void":
                        body_stmts = [f"{call_expr};"]
                    elif _is_std_string_view(method.return_type):
                        body_stmts = [
                            "thread_local std::string _ret_str;",
                            f"auto _view = {call_expr};",
                            "_ret_str.assign(_view.data(), _view.size());",
                            "return _ret_str.c_str();",
                        ]
                    elif _is_std_string(method.return_type):
                        body_stmts = [
                            "thread_local std::string _ret_str;",
                            f"_ret_str = {call_expr};",
                            "return _ret_str.c_str();",
                        ]
                    else:
                        body_stmts = [f"return {call_expr};"]

                    params_str = ", ".join(params_c) if params_c else "void"
                    if self.catch_exceptions:
                        catch_stmt = _make_catch_stmt(ret_type_c)
                        inner = "\n        ".join(body_stmts)
                        m_body = (
                            f"{ret_type_c} {fn_method_name}({params_str}) {{\n"
                            f"    try {{\n"
                            f"        {inner}\n"
                            f"    }} catch (...) {{\n"
                            f"        {catch_stmt}\n"
                            f"    }}\n"
                            f"}}\n"
                        )
                    else:
                        inner = "\n    ".join(body_stmts)
                        m_body = f"{ret_type_c} {fn_method_name}({params_str}) {{\n    {inner}\n}}\n"
                    cpp_lines.append(m_body)

            elif isinstance(decl, Function):
                # Free function
                if decl.namespace:
                    fn_full_name = f"{decl.namespace}::{decl.name}"
                    safe_fn_name = _sanitize_name(fn_full_name)
                else:
                    fn_full_name = decl.name
                    safe_fn_name = _sanitize_name(decl.name)

                if safe_fn_name in exported_names:
                    idx = 1
                    while f"{safe_fn_name}_{idx}" in exported_names:
                        idx += 1
                    safe_fn_name = f"{safe_fn_name}_{idx}"

                ret_c = _type_to_c(decl.return_type)
                params_c, call_args = _format_parameters_and_call_args(decl.parameters)
                if decl.is_variadic:
                    params_c.append("...")

                proto = f"{ret_c} {safe_fn_name}({', '.join(params_c) if params_c else 'void'});"
                lines.append(proto)
                exported_names.append(safe_fn_name)

                if ret_c == "void":
                    fn_body_stmts = [f"{fn_full_name}({', '.join(call_args)});"]
                elif _is_std_string_view(decl.return_type):
                    fn_body_stmts = [
                        "thread_local std::string _ret_str;",
                        f"auto _view = {fn_full_name}({', '.join(call_args)});",
                        "_ret_str.assign(_view.data(), _view.size());",
                        "return _ret_str.c_str();",
                    ]
                elif _is_std_string(decl.return_type):
                    fn_body_stmts = [
                        "thread_local std::string _ret_str;",
                        f"_ret_str = {fn_full_name}({', '.join(call_args)});",
                        "return _ret_str.c_str();",
                    ]
                else:
                    fn_body_stmts = [f"return {fn_full_name}({', '.join(call_args)});"]

                params_fn_str = ", ".join(params_c) if params_c else "void"
                if self.catch_exceptions:
                    catch_stmt = _make_catch_stmt(ret_c)
                    inner = "\n        ".join(fn_body_stmts)
                    fn_body = (
                        f"{ret_c} {safe_fn_name}({params_fn_str}) {{\n"
                        f"    try {{\n"
                        f"        {inner}\n"
                        f"    }} catch (...) {{\n"
                        f"        {catch_stmt}\n"
                        f"    }}\n"
                        f"}}\n"
                    )
                else:
                    inner = "\n    ".join(fn_body_stmts)
                    fn_body = f"{ret_c} {safe_fn_name}({params_fn_str}) {{\n    {inner}\n}}\n"
                cpp_lines.append(fn_body)

        lines.append("")
        lines.append("#ifdef __cplusplus")
        lines.append("}")
        lines.append("#endif")

        if any("size_t" in line for line in lines):
            lines.insert(2, "#include <stddef.h>")

        header_code = "\n".join(lines)
        return header_code, cpp_lines, exported_names

    def _render(self, unit: SourceUnit | Header) -> str:
        """Generate C-ABI shim source code from headerkit IR."""
        header_code, cpp_lines, _ = self._render_parts(unit)
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)

        cpp_includes = ["#include <new>"]
        if any("std::string" in stmt or "_ret_str" in stmt for stmt in cpp_lines):
            cpp_includes.append("#include <string>")
        if any("std::vector" in stmt for stmt in cpp_lines):
            cpp_includes.append("#include <vector>")
        if any("size_t" in stmt or "std::vector" in stmt for stmt in cpp_lines):
            cpp_includes.append("#include <cstddef>")
        if self.catch_exceptions:
            cpp_includes.append("#include <exception>")
        if header.path:
            cpp_includes.append(f'#include "{header.path}"')

        lines: list[str] = [
            header_code,
            "",
            "#ifdef __cplusplus",
            *cpp_includes,
            "",
            *cpp_lines,
            "#endif",
            "",
        ]

        return "\n".join(lines)

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        header_content, cpp_lines, fn_names = self._render_parts(unit)

        cpp_header_includes = [
            f'#include "{pkg}_cshim.h"',
            "#include <new>",
        ]
        if any("std::string" in stmt or "_ret_str" in stmt for stmt in cpp_lines):
            cpp_header_includes.append("#include <string>")
        if any("std::vector" in stmt for stmt in cpp_lines):
            cpp_header_includes.append("#include <vector>")
        if any("size_t" in stmt or "std::vector" in stmt for stmt in cpp_lines):
            cpp_header_includes.append("#include <cstddef>")
        if self.catch_exceptions:
            cpp_header_includes.append("#include <exception>")
        if header.path:
            cpp_header_includes.append(f'#include "{header.path}"')

        cpp_code = "\n".join(
            [
                f"// Implementation for {pkg} C-ABI shim",
                *cpp_header_includes,
                "",
                *cpp_lines,
            ]
        )
        if not cpp_code.endswith("\n"):
            cpp_code += "\n"

        if test_type != "none":
            cmake = textwrap.dedent(f"""\
                cmake_minimum_required(VERSION 3.15)
                project({pkg}_cshim C CXX)

                set(CMAKE_CXX_STANDARD 17)
                set(CMAKE_CXX_STANDARD_REQUIRED ON)

                add_library({pkg}_cshim SHARED
                    src/{pkg}_cshim.cpp
                )

                target_include_directories({pkg}_cshim PUBLIC
                    include
                )

                enable_testing()
                add_executable(test_{pkg}_cshim tests/test_cshim.c)
                target_link_libraries(test_{pkg}_cshim PRIVATE {pkg}_cshim ${{CMAKE_DL_LIBS}})
                add_test(NAME test_{pkg}_cshim COMMAND test_{pkg}_cshim)
            """)
        else:
            cmake = textwrap.dedent(f"""\
                cmake_minimum_required(VERSION 3.15)
                project({pkg}_cshim C CXX)

                set(CMAKE_CXX_STANDARD 17)
                set(CMAKE_CXX_STANDARD_REQUIRED ON)

                add_library({pkg}_cshim SHARED
                    src/{pkg}_cshim.cpp
                )

                target_include_directories({pkg}_cshim PUBLIC
                    include
                )
            """)

        files = [
            OutputFile(path="CMakeLists.txt", content=cmake),
            OutputFile(path=f"include/{pkg}_cshim.h", content=header_content + "\n"),
            OutputFile(path=f"src/{pkg}_cshim.cpp", content=cpp_code),
        ]
        if test_type != "none":
            checks = (
                "\n".join(
                    f'    assert(resolve_symbol("{fn}") != NULL && "Entry point \'{fn}\' missing from native library");'
                    for fn in fn_names[:10]
                )
                if fn_names
                else '    printf("Shim header included successfully\\n");'
            )
            test_c = render_block_template(
                f"""\
                #include <stdio.h>
                #include <stdlib.h>
                #include <assert.h>
                #include "{pkg}_cshim.h"

                #ifdef _WIN32
                #include <windows.h>
                static void* resolve_symbol(const char* name) {{
                    HMODULE mod = GetModuleHandleA("{pkg}_cshim.dll");
                    if (!mod) mod = GetModuleHandleA(NULL);
                    return mod ? (void*)GetProcAddress(mod, name) : NULL;
                }}
                #else
                #include <dlfcn.h>
                static void* resolve_symbol(const char* name) {{
                    void* handle = dlopen(NULL, RTLD_NOW);
                    if (!handle) return NULL;
                    return dlsym(handle, name);
                }}
                #endif

                int main(void) {{
                    printf("Testing {pkg} C-ABI shim symbol resolution...\\n");
                {DEDENT_BLOCK}
                    return 0;
                }}
            """,
                checks,
            )
            files.append(OutputFile(path="tests/test_cshim.c", content=test_c))

        return ProjectLayout(files=files)


def write_cshim(header: Header, *, catch_exceptions: bool = False) -> str:
    """Convenience function to generate C-ABI shims from a Header IR."""
    return CShimWriter(catch_exceptions=catch_exceptions).write(header)


# Self-register
from headerkit.writers import register_writer  # noqa: E402

register_writer("cshim", CShimWriter, description="C-ABI shim wrapper generator (extern C)")
