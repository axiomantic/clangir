r"""IR to Nim binding writer.

This module converts the headerkit IR (Intermediate Representation) to
Nim declaration files using ``{.importc.}`` and ``{.importcpp.}`` pragmas.

Features
--------
* C and C++ Interop -- Emits ``{.importc.}`` for C and ``{.importcpp: "...".}`` for C++
* C++ Classes & Inheritance -- Maps classes/structs with bases, methods, and constructors
* Function & Type Generics -- Maps templates to Nim generics: ``type Foo[T] = object``, ``proc bar[T](x: T)``
* References & Pointers -- Maps ``Reference`` to ``var T`` / ``byref`` and ``Pointer`` to ``ptr T``
* Identifier Escaping & Style -- Handles Nim keywords with accent quotes (e.g. ``\`type\```)
"""

from __future__ import annotations

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
    Variable,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions, extract_function_names
from headerkit.workorder import build_work_order_files
from headerkit.writers.base import BaseWriter, WriterOption

NIM_KEYWORDS: set[str] = {
    "addr",
    "and",
    "as",
    "asm",
    "bind",
    "block",
    "break",
    "case",
    "cast",
    "concept",
    "const",
    "continue",
    "converter",
    "defer",
    "discard",
    "distinct",
    "div",
    "do",
    "elif",
    "else",
    "end",
    "enum",
    "except",
    "export",
    "finally",
    "for",
    "from",
    "func",
    "if",
    "import",
    "in",
    "include",
    "interface",
    "is",
    "isnot",
    "iterator",
    "let",
    "macro",
    "method",
    "mixin",
    "mod",
    "nil",
    "not",
    "notin",
    "object",
    "of",
    "or",
    "out",
    "proc",
    "ptr",
    "raise",
    "ref",
    "return",
    "shl",
    "shr",
    "static",
    "template",
    "try",
    "tuple",
    "type",
    "using",
    "var",
    "when",
    "while",
    "xor",
    "yield",
}

C_TO_NIM_PRIMITIVES: dict[str, str] = {
    "void": "void",
    "char": "cchar",
    "signed char": "cschar",
    "unsigned char": "uint8",
    "short": "cshort",
    "short int": "cshort",
    "signed short": "cshort",
    "signed short int": "cshort",
    "unsigned short": "cushort",
    "unsigned short int": "cushort",
    "int": "cint",
    "signed int": "cint",
    "signed": "cint",
    "unsigned": "cuint",
    "unsigned int": "cuint",
    "long": "clong",
    "long int": "clong",
    "signed long": "clong",
    "signed long int": "clong",
    "unsigned long": "culong",
    "unsigned long int": "culong",
    "long long": "clonglong",
    "long long int": "clonglong",
    "signed long long": "clonglong",
    "signed long long int": "clonglong",
    "unsigned long long": "culonglong",
    "unsigned long long int": "culonglong",
    "float": "cfloat",
    "double": "cdouble",
    "long double": "clongdouble",
    "bool": "bool",
    "_Bool": "bool",
    "size_t": "csize_t",
    "ssize_t": "int",
    "intptr_t": "int",
    "uintptr_t": "uint",
    "ptrdiff_t": "int",
    "int8_t": "int8",
    "int16_t": "int16",
    "int32_t": "int32",
    "int64_t": "int64",
    "uint8_t": "uint8",
    "uint16_t": "uint16",
    "uint32_t": "uint32",
    "uint64_t": "uint64",
    "cstring": "cstring",
    "void*": "pointer",
}


CPP_OPERATOR_MAP: dict[str, str] = {
    "operator[]": "`[]`",
    "operator()": "`()`",
    "operator+": "`+`",
    "operator-": "`-`",
    "operator*": "`*`",
    "operator/": "`/`",
    "operator==": "`==`",
    "operator!=": "`!=`",
    "operator<": "`<`",
    "operator<=": "`<=`",
    "operator>": "`>`",
    "operator>=": "`>=`",
    "operator+=": "`+=`",
    "operator-=": "`-=`",
    "operator*=": "`*=`",
    "operator/=": "`/=`",
    "operator%=": "`%=`",
    "operator&=": "`&=`",
    "operator|=": "`|=`",
    "operator^=": "`^=`",
    "operator<<=": "`shl=`",
    "operator>>=": "`shr=`",
    "operator=": "`=`",
    "operator%": "`%`",
    "operator&": "`&`",
    "operator|": "`|`",
    "operator^": "`^`",
    "operator~": "`~`",
    "operator!": "`!`",
    "operator&&": "`and`",
    "operator||": "`or`",
    "operator<<": "`shl`",
    "operator>>": "`shr`",
    "operator++": "`inc`",
    "operator--": "`dec`",
    "operator->": "`->`",
}


def _escape_ident(name: str) -> str:
    """Escape Nim keywords, operators, and invalid identifier characters."""
    if not name:
        return "anon"
    if name in CPP_OPERATOR_MAP:
        return CPP_OPERATOR_MAP[name]
    if "::" in name:
        name = name.replace("::", "_")

    # In Nim, identifiers cannot begin or end with underscores, nor contain consecutive underscores.
    # We replace leading underscores with 'u_' and trailing with '_u' to avoid collision between e.g. FOO and _FOO.
    clean = name
    if clean.startswith("_"):
        clean = "u" + clean
    if clean.endswith("_"):
        clean = clean + "u"

    if clean in NIM_KEYWORDS:
        return f"`{clean}`"
    return clean


class NimWriter(BaseWriter):
    """Writer that converts headerkit IR into Nim binding modules."""

    name: str = "nim"
    format_description: str = "Nim bindings with C and C++ interop"
    default_output_pattern: str = "{dir}/{stem}.nim"
    default_extension: str = ".nim"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project", "wheel", "scikit-build")
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
        WriterOption(
            name="header_path",
            description="Header path to reference in {.header.} pragmas",
            default=None,
            type=str,
        ),
    )

    def __init__(self, *, header_path: str | None = None) -> None:
        self.header_path = header_path

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Nim comments."""
        return "# {line}"

    def _render(self, unit: SourceUnit | Header) -> str:
        """Convert parsed header IR to Nim source code."""
        header = unit
        lines: list[str] = []

        header_file = self.header_path or header.path or "header.h"

        lines.append("# Generated by headerkit")
        lines.append("")

        # Collect function names and type names upfront for collision detection
        func_names: set[str] = {decl.name for decl in header.declarations if isinstance(decl, Function) and decl.name}
        known_base_classes: set[str] = {
            b.name.replace("::", "_") for decl in header.declarations if isinstance(decl, Struct) for b in decl.bases
        }
        emitted_types: set[str] = set()
        types_section: list[str] = []
        procs_section: list[str] = []
        consts_section: list[str] = []

        has_std_exception = any(
            isinstance(decl, Struct) and any("std::exception" in b.name for b in decl.bases)
            for decl in header.declarations
        )

        if has_std_exception:
            types_section.append(
                'std_exception* {.importcpp: "std::exception", header: "<exception>".} = object of RootObj'
            )
            emitted_types.add("std_exception")

        def _type_contains(t: TypeExpr, target_prefix: str) -> bool:
            """Check structurally whether a TypeExpr contains target_prefix in its type names."""
            if isinstance(t, CType):
                return target_prefix in t.name
            elif isinstance(t, Pointer):
                return _type_contains(t.pointee, target_prefix)
            elif isinstance(t, Reference):
                return _type_contains(t.target, target_prefix)
            elif isinstance(t, Array):
                return _type_contains(t.element_type, target_prefix)
            elif isinstance(t, FunctionPointer):
                if _type_contains(t.return_type, target_prefix):
                    return True
                return any(_type_contains(p.type, target_prefix) for p in t.parameters)
            return False

        def _decl_contains(d: object, target_prefix: str) -> bool:
            """Check structurally whether a Declaration references target_prefix."""
            if isinstance(d, Struct):
                for f in d.fields:
                    if _type_contains(f.type, target_prefix):
                        return True
                for m in d.methods + d.constructors:
                    if _type_contains(m.return_type, target_prefix):
                        return True
                    if any(_type_contains(p.type, target_prefix) for p in m.parameters):
                        return True
                if d.destructor and any(_type_contains(p.type, target_prefix) for p in d.destructor.parameters):
                    return True
            elif isinstance(d, Function):
                if _type_contains(d.return_type, target_prefix):
                    return True
                if any(_type_contains(p.type, target_prefix) for p in d.parameters):
                    return True
            elif isinstance(d, Typedef):
                if _type_contains(d.underlying_type, target_prefix):
                    return True
            elif isinstance(d, Variable):
                if _type_contains(d.type, target_prefix):
                    return True
            return False

        has_cpp_string = any(_decl_contains(decl, "std::string") for decl in header.declarations)
        if has_cpp_string:
            types_section.append('CppString* {.importcpp: "std::string", header: "<string>".} = object')
            emitted_types.add("CppString")

        has_unique_ptr = any(_decl_contains(decl, "std::unique_ptr") for decl in header.declarations)
        if has_unique_ptr:
            types_section.append('UniquePtr*[T] {.importcpp: "std::unique_ptr<\'0>", header: "<memory>".} = object')
            emitted_types.add("UniquePtr")
            procs_section.extend(
                [
                    "",
                    'proc `=copy`*[T](dst: var UniquePtr[T], src: UniquePtr[T]) {.error: "std::unique_ptr cannot be copied in Nim; use std/moves.move() or sink".}',
                    "",
                    'proc move*[T](p: var UniquePtr[T]): UniquePtr[T] {.importcpp: "std::move(@)", header: "<utility>".}',
                    "",
                    'proc get*[T](p: UniquePtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
                    "",
                    'proc reset*[T](p: var UniquePtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
                ]
            )

        has_shared_ptr = any(_decl_contains(decl, "std::shared_ptr") for decl in header.declarations)
        if has_shared_ptr:
            types_section.append('SharedPtr*[T] {.importcpp: "std::shared_ptr<\'0>", header: "<memory>".} = object')
            emitted_types.add("SharedPtr")
            procs_section.extend(
                [
                    "",
                    'proc get*[T](p: SharedPtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
                    "",
                    'proc reset*[T](p: var SharedPtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
                    "",
                    'proc useCount*[T](p: SharedPtr[T]): clong {.importcpp: "#.use_count()", header: "<memory>".}',
                ]
            )

        for decl in header.declarations:
            if isinstance(decl, Struct):
                name = decl.name or "AnonObject"
                if name not in emitted_types:
                    emitted_types.add(name)
                    t_lines, m_lines = self._write_struct(decl, header_file, known_base_classes)
                    types_section.extend(t_lines)
                    procs_section.extend(m_lines)
            elif isinstance(decl, Enum):
                t_lines, c_lines = self._write_enum(decl, header_file, func_names)
                types_section.extend(t_lines)
                consts_section.extend(c_lines)
            elif isinstance(decl, Typedef):
                t_lines = self._write_typedef(decl, emitted_types)
                if t_lines:
                    emitted_types.add(decl.name)
                    types_section.extend(t_lines)
            elif isinstance(decl, Function):
                procs_section.extend(self._write_function(decl, header_file))
            elif isinstance(decl, Constant):
                consts_section.extend(self._write_constant(decl))
            elif isinstance(decl, Variable):
                procs_section.extend(self._write_variable(decl, header_file))

        if types_section:
            lines.append("type")
            for t_line in types_section:
                lines.append(f"  {t_line}" if t_line else "")
            lines.append("")

        if consts_section:
            lines.append("const")
            for c_line in consts_section:
                lines.append(f"  {c_line}" if c_line else "")
            lines.append("")

        if procs_section:
            lines.extend(procs_section)
            lines.append("")

        output = "\n".join(lines).rstrip() + "\n"
        return output

    def _format_type(self, t: TypeExpr, *, in_param: bool = False) -> str:
        """Convert IR TypeExpr to a Nim type representation."""
        if isinstance(t, CType):
            name = t.name.removeprefix("struct ").removeprefix("union ").removeprefix("enum ")
            if "(anonymous" in name or "(unnamed" in name:
                return "pointer"

            # C++ Smart Pointers & Containers mapping
            if name.startswith("std::shared_ptr<") or name.startswith("shared_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"SharedPtr[{self._format_type(CType(inner))}]"
            elif name.startswith("std::unique_ptr<") or name.startswith("unique_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"UniquePtr[{self._format_type(CType(inner))}]"
            elif name.startswith("std::weak_ptr<") or name.startswith("weak_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"WeakPtr[{self._format_type(CType(inner))}]"
            elif name.startswith("std::vector<") or name.startswith("vector<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"CppVector[{self._format_type(CType(inner))}]"
            elif name.startswith("std::string") or name == "string":
                return "CppString"

            if "::" in name:
                name = name.replace("::", "_")

            if name in C_TO_NIM_PRIMITIVES:
                return C_TO_NIM_PRIMITIVES[name]
            return _escape_ident(name)

        elif isinstance(t, Pointer):
            if isinstance(t.pointee, CType) and t.pointee.name == "void":
                return "pointer"
            if isinstance(t.pointee, CType) and t.pointee.name == "char" and "const" in t.pointee.qualifiers:
                return "cstring"
            if isinstance(t.pointee, FunctionPointer):
                return self._format_type(t.pointee)
            target_type = self._format_type(t.pointee)
            return f"ptr {target_type}"

        elif isinstance(t, Reference):
            target_type = self._format_type(t.target)
            if t.is_rvalue:
                # C++ rvalue reference (move semantics): var or sink in Nim
                return f"sink {target_type}" if in_param else f"var {target_type}"
            return f"var {target_type}"

        elif isinstance(t, Array):
            elem = self._format_type(t.element_type)
            if t.size is not None:
                return f"array[{t.size}, {elem}]"
            return f"UncheckedArray[{elem}]"

        elif isinstance(t, FunctionPointer):
            ret = self._format_type(t.return_type)
            params = [
                f"{_escape_ident(p.name or f'a{i}')}: {self._format_type(p.type, in_param=True)}"
                for i, p in enumerate(t.parameters)
            ]
            params_str = f"({', '.join(params)})" if params else "()"
            ret_str = f": {ret}" if ret != "void" else ""
            return f"proc{params_str}{ret_str} {{.cdecl.}}"

        return "pointer"

    def _write_struct(
        self, s: Struct, header_file: str, known_base_classes: set[str] | None = None
    ) -> tuple[list[str], list[str]]:
        """Render a Struct or class as a Nim type declaration and its methods."""
        name = s.name or "AnonObject"
        t_name = _escape_ident(name)

        # Generics
        if s.template_params:
            t_name = f"{t_name}[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"

        pragma_parts: list[str] = []
        is_cpp = s.is_cppclass or bool(s.methods or s.bases or s.constructors or s.destructor)

        if is_cpp:
            cpp_pattern = s.cpp_name or (f"{s.namespace}::{s.name}" if s.namespace else s.name)
            pragma_parts.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')
            pragma_parts.append("bycopy")
        else:
            tag_prefix = "union " if s.is_union else "struct "
            pragma_parts.append(f'importc: "{tag_prefix}{name}", header: "{header_file}"')
            if s.is_union:
                pragma_parts.append("union")
            else:
                pragma_parts.append("bycopy")

        if s.is_packed:
            pragma_parts.append("packed")

        pragma_str = f" {{.{', '.join(pragma_parts)}.}}" if pragma_parts else ""

        # Inheritance
        base_str = ""
        if s.bases:
            # Single primary base in Nim object inheritance
            base_str = f" of {self._format_type(CType(s.bases[0].name))}"
        elif known_base_classes and s.name in known_base_classes:
            base_str = " of RootObj"

        lines = [f"{t_name}*{pragma_str} = object{base_str}"]

        if not s.fields:
            lines[0] += ""
        else:
            for f in s.fields:
                f_name = _escape_ident(f.name)
                f_type = self._format_type(f.type)
                lines.append(f"  {f_name}*: {f_type}")

        # Methods / Constructors / Iterators attached to struct
        methods_lines: list[str] = []
        for m in s.methods:
            methods_lines.extend(self._write_method(s, m, header_file))

        for ctor in s.constructors:
            methods_lines.extend(self._write_constructor(s, ctor, header_file))

        if s.destructor:
            methods_lines.extend(self._write_destructor(s, s.destructor, header_file))

        # Iterators helper if begin()/end() are available
        has_begin = any(m.name == "begin" for m in s.methods)
        has_end = any(m.name == "end" for m in s.methods)
        if has_begin and has_end:
            if s.template_params:
                struct_type = (
                    f"{_escape_ident(s.name or 'Self')}[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
                )
                t_params = f"[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
            else:
                struct_type = self._format_type(CType(s.name or "Self"))
                t_params = ""
            begin_methods = [m for m in s.methods if m.name == "begin"]
            has_const_begin = any(m.is_const for m in begin_methods)
            this_param = f"this: {struct_type}" if has_const_begin else f"this: var {struct_type}"
            methods_lines.extend(
                [
                    "",
                    f"iterator items*{t_params}({this_param}): auto = {{.inline.}}",
                    "  var it = this.begin()",
                    "  while it != this.end():",
                    "    yield it[]",
                    "    inc it",
                ]
            )

        return lines, methods_lines

    def _write_method(self, s: Struct, m: Function, header_file: str) -> list[str]:
        """Render a C++ member method or operator in Nim."""
        m_name = _escape_ident(m.name)
        params: list[str] = []

        # 'this' parameter
        if s.template_params:
            struct_type = (
                f"{_escape_ident(s.name or 'Self')}[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
            )
        else:
            struct_type = self._format_type(CType(s.name or "Self"))

        if not m.is_static:
            if m.is_const:
                params.append(f"this: {struct_type}")
            else:
                params.append(f"this: var {struct_type}")

        for i, p in enumerate(m.parameters):
            p_name = _escape_ident(p.name or f"a{i}")
            p_type = self._format_type(p.type, in_param=True)
            default_str = f" = {p.default_value}" if p.default_value else ""
            params.append(f"{p_name}: {p_type}{default_str}")

        ret_type = self._format_type(m.return_type)
        ret_str = f": {ret_type}" if ret_type != "void" else ""

        # Pragmas
        pragmas: list[str] = []
        if m.is_static:
            cpp_pattern = f"{s.name}::{m.name}(@)"
        elif m.name == "operator[]":
            cpp_pattern = "#[@]"
        elif m.name.startswith("operator"):
            op_sym = m.name[8:]
            if len(params) == 2:
                cpp_pattern = f"(# {op_sym} @)"
            else:
                cpp_pattern = f"{op_sym}(#)"
        else:
            cpp_pattern = f"#.{(m.name)}(@)"
        pragmas.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')

        # Generic parameters (combine struct and method template parameters)
        all_tp = list(s.template_params) + [tp for tp in m.template_params if tp not in s.template_params]
        t_params = f"[{', '.join(_escape_ident(tp) for tp in all_tp)}]" if all_tp else ""

        decl = f"proc {m_name}*{t_params}({', '.join(params)}){ret_str} {{.{', '.join(pragmas)}.}}"
        return ["", decl]

    def _write_destructor(self, s: Struct, dtor: Function, header_file: str) -> list[str]:
        """Render a C++ destructor as a Nim destroy proc."""
        s_name = s.name or "Object"
        if s.template_params:
            struct_type = f"{_escape_ident(s_name)}[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
            t_params = f"[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
        else:
            struct_type = self._format_type(CType(s_name))
            t_params = ""
        decl = f'proc destroy*{t_params}(this: var {struct_type}) {{.importcpp: "#.~{s_name}()", header: "{header_file}".}}'
        return ["", decl]

    def _write_constructor(self, s: Struct, ctor: Function, header_file: str) -> list[str]:
        """Render a C++ constructor as a Nim constructProc."""
        s_name = s.name or "Object"
        proc_name = f"construct{s_name}"
        params: list[str] = []

        for i, p in enumerate(ctor.parameters):
            p_name = _escape_ident(p.name or f"a{i}")
            p_type = self._format_type(p.type, in_param=True)
            default_str = f" = {p.default_value}" if p.default_value else ""
            params.append(f"{p_name}: {p_type}{default_str}")

        if s.template_params:
            ret_type = f"{_escape_ident(s_name)}[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
            t_params = f"[{', '.join(_escape_ident(tp) for tp in s.template_params)}]"
            t_args = ", ".join(f"'*{i}" for i in range(len(s.template_params)))
            cpp_pattern = f"{s_name}<{t_args}>(@)"
        else:
            ret_type = self._format_type(CType(s_name))
            t_params = ""
            cpp_pattern = f"{s_name}(@)"

        pragma = f'importcpp: "{cpp_pattern}", header: "{header_file}", constructor'

        return ["", f"proc {proc_name}*{t_params}({', '.join(params)}): {ret_type} {{.{pragma}.}}"]

    def _write_enum(self, e: Enum, header_file: str, func_names: set[str] | None = None) -> tuple[list[str], list[str]]:
        """Render an Enum declaration, returning (type_lines, const_lines)."""
        name = e.name or ""
        is_anonymous = not name or "(unnamed" in name or "(anonymous" in name or name.startswith("enum (")

        if is_anonymous:
            # Emit anonymous enum values as constants
            const_lines: list[str] = []
            for v in e.values:
                v_name = _escape_ident(v.name)
                if v.value is not None:
                    const_lines.append(f"{v_name}* = {v.value}")
                else:
                    const_lines.append(f"{v_name}* = 0")
            return [], const_lines

        # Disambiguate if enum name collides with a function
        nim_name = f"{name}_enum" if func_names and name in func_names else name
        e_name = _escape_ident(nim_name)

        lines = [f'{e_name}* {{.size: sizeof(cint), importc: "enum {name}", header: "{header_file}".}} = enum']
        for v in e.values:
            v_name = _escape_ident(v.name)
            if v.value is not None:
                lines.append(f"  {v_name} = {v.value}")
            else:
                lines.append(f"  {v_name}")
        return lines, []

    def _write_typedef(self, t: Typedef, emitted_types: set[str] | None = None) -> list[str]:
        """Render a Typedef declaration."""
        if not t.name:
            return []

        if emitted_types and t.name in emitted_types:
            return []

        # Check self-referential typedefs (e.g. typedef struct foo foo;)
        if isinstance(t.underlying_type, CType):
            raw = t.underlying_type.name
            clean = raw.removeprefix("struct ").removeprefix("union ").removeprefix("enum ").strip()
            if clean == t.name:
                return []

        t_name = _escape_ident(t.name)
        underlying = self._format_type(t.underlying_type)
        return [f"{t_name}* = {underlying}"]

    def _write_function(self, f: Function, header_file: str) -> list[str]:
        """Render a function declaration."""
        f_name = _escape_ident(f.name)
        params: list[str] = []
        for i, p in enumerate(f.parameters):
            p_name = _escape_ident(p.name or f"a{i}")
            p_type = self._format_type(p.type, in_param=True)
            default_str = f" = {p.default_value}" if p.default_value else ""
            params.append(f"{p_name}: {p_type}{default_str}")

        ret_type = self._format_type(f.return_type)
        ret_str = f": {ret_type}" if ret_type != "void" else ""

        pragmas: list[str] = []
        if f.namespace:
            pragmas.append(f'importcpp: "{f.namespace}::{f.name}(@)", header: "{header_file}"')
        elif f.template_params:
            pragmas.append(f'importcpp: "{f.name}(@)", header: "{header_file}"')
        else:
            pragmas.append(f'importc: "{f.name}", header: "{header_file}"')

        if f.is_variadic:
            pragmas.append("varargs")

        if f.calling_convention:
            pragmas.append(f.calling_convention)
        else:
            pragmas.append("cdecl")

        t_params = f"[{', '.join(_escape_ident(tp) for tp in f.template_params)}]" if f.template_params else ""
        return [f"proc {f_name}*{t_params}({', '.join(params)}){ret_str} {{.{', '.join(pragmas)}.}}"]

    def _write_variable(self, v: Variable, header_file: str) -> list[str]:
        """Render an extern global variable."""
        v_name = _escape_ident(v.name)
        v_type = self._format_type(v.type)
        return [f'var {v_name}* {{.importc: "{v.name}", header: "{header_file}".}}: {v_type}']

    def _write_constant(self, c: Constant) -> list[str]:
        """Render a constant."""
        if c.value is None:
            return []
        c_name = _escape_ident(c.name)
        return [f"{c_name}* = {c.value}"]

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        bindings_code = self._render(unit)
        fn_names = extract_function_names(unit)

        files: list[OutputFile] = []

        # 1. Nimble package spec
        nimble = textwrap.dedent(f"""\
            # Package
            version       = "0.1.0"
            author        = "HeaderKit"
            description   = "Nim bindings for {pkg}"
            license       = "MIT"
            srcDir        = "src"
            packageName   = "{pkg}"

            # Dependencies
            requires "nim >= 2.0.0"

            task test, "Run tests":
              exec "nim c -r tests/test_tripwire.nim"
        """)
        files.append(OutputFile(path=f"{pkg}.nimble", content=nimble))

        # 2. Main package re-export
        main_src = textwrap.dedent(f"""\
            # Primary export module for {pkg}
            import {pkg}/bindings
            export bindings
        """)
        files.append(OutputFile(path=f"src/{pkg}.nim", content=main_src))

        # 3. Generated bindings module
        files.append(OutputFile(path=f"src/{pkg}/bindings.nim", content=bindings_code))

        # 4. nim.cfg compiler flags
        nim_cfg = textwrap.dedent("""\
            --mm:orc
            --threads:on
            --styleCheck:hint
        """)
        files.append(OutputFile(path="nim.cfg", content=nim_cfg))

        # 5. Tests
        if test_type in ("tripwire", "both"):
            stub_lines = []
            for fn in fn_names:
                stub_lines.append(
                    f"    if lib.symAddr(\"{fn}\") == nil:\n      checkpoint \"Entry point '{fn}' missing from native library '{pkg}'\"\n      fail()"
                )
            stubs = "\n".join(stub_lines) if stub_lines else f"    checkpoint \"Verified native library '{pkg}' loads\""

            tripwire = textwrap.dedent(f"""\
                import std/[unittest, dynlib]
                import {pkg}

                suite "Tripwire Symbol & ABI Verification":
                  test "verify foreign library entrypoints exist and link":
                    let lib = loadLib("{pkg}")
                    if lib == nil:
                      checkpoint "Native dynamic library '{pkg}' not found in system library path"
                      fail()
                {stubs}
            """)
            files.append(OutputFile(path="tests/test_tripwire.nim", content=tripwire))

        if test_type in ("unit", "both"):
            decl_checks = (
                "\n".join(f"    check declared({fn})" for fn in fn_names[:10])
                if fn_names
                else f"    check declared({pkg})"
            )
            unit_test = textwrap.dedent(f"""\
                import std/unittest
                import {pkg}

                suite "{pkg} Unit Tests":
                  test "module exports expected declarations":
                {decl_checks}
            """)
            files.append(OutputFile(path=f"tests/test_{pkg}.nim", content=unit_test))

        if test_type in ("unit", "both"):
            files.extend(build_work_order_files(unit, pkg, "nim"))

        return ProjectLayout(files=files)

    def _write_custom_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        if options.layout in ("wheel", "scikit-build"):
            from headerkit.packaging.nim import generate_nim_wheel_layout

            return generate_nim_wheel_layout(unit, options)
        return self._write_package_layout(unit, options)


def write_nim(header: Header | SourceUnit, *, header_path: str | None = None) -> str:
    """Convenience function to generate Nim bindings from a Header IR."""
    return NimWriter(header_path=header_path).write(header)


from headerkit.writers import register_writer  # noqa: E402

register_writer("nim", NimWriter, description="Nim bindings with C and C++ interop (importc, importcpp)")
