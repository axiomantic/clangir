"""Generate LuaJIT FFI binding files from headerkit IR declarations.

This module converts headerkit IR (Intermediate Representation) objects into
LuaJIT FFI binding files with C declarations inside ``ffi.cdef[[ ... ]]``.

The IR types come from ``headerkit.ir`` and represent parsed C headers.
"""

from __future__ import annotations

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)


def _type_to_c(t: TypeExpr) -> str:
    """Convert a type expression to its C string representation.

    LuaJIT's cdef parser expects standard C declarations, so this
    emits plain C syntax without CFFI-specific tag qualification.
    """
    if isinstance(t, CType):
        return str(t)
    elif isinstance(t, Pointer):
        # Pointer to function pointer: the (*) already provides indirection
        if isinstance(t.pointee, FunctionPointer):
            fp = t.pointee
            params = _format_params(fp.parameters, fp.is_variadic)
            cc = f"__{fp.calling_convention}__ " if fp.calling_convention else ""
            return f"{cc}{_type_to_c(fp.return_type)}(*)({params})"
        # Collect consecutive pointer levels to emit "type **" not "type * *"
        ptr_count = 0
        inner: TypeExpr = t
        while isinstance(inner, Pointer):
            if isinstance(inner.pointee, FunctionPointer):
                break
            ptr_count += 1
            inner = inner.pointee
        if isinstance(inner, FunctionPointer):
            fp = inner
            params = _format_params(fp.parameters, fp.is_variadic)
            cc = f"__{fp.calling_convention}__ " if fp.calling_convention else ""
            stars = "*" * (ptr_count + 1)
            return f"{cc}{_type_to_c(fp.return_type)}({stars})({params})"
        base = _type_to_c(inner)
        return f"{base} {'*' * ptr_count}"
    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{_type_to_c(t.element_type)}[{size_str}]"
    elif isinstance(t, FunctionPointer):
        params = _format_params(t.parameters, t.is_variadic)
        cc = f"__{t.calling_convention}__ " if t.calling_convention else ""
        return f"{cc}{_type_to_c(t.return_type)}(*)({params})"
    else:
        return str(t)


def _format_params(
    parameters: list[Parameter],
    is_variadic: bool,
) -> str:
    """Format function parameter list as a string."""
    if not parameters and not is_variadic:
        return "void"
    parts: list[str] = []
    for p in parameters:
        type_str = _type_to_c(p.type)
        if p.name:
            if isinstance(p.type, Array):
                size_str = str(p.type.size) if p.type.size is not None else ""
                parts.append(f"{_type_to_c(p.type.element_type)} {p.name}[{size_str}]")
            elif isinstance(p.type, FunctionPointer):
                fp = p.type
                fp_params = _format_params(fp.parameters, fp.is_variadic)
                cc = f"__{fp.calling_convention}__ " if fp.calling_convention else ""
                parts.append(f"{cc}{_type_to_c(fp.return_type)} (*{p.name})({fp_params})")
            else:
                parts.append(f"{type_str} {p.name}")
        else:
            parts.append(type_str)
    if is_variadic:
        parts.append("...")
    return ", ".join(parts)


def _format_field(f: Field, indent: str = "    ") -> str:
    """Format a struct/union field declaration."""
    if f.anonymous_struct is not None:
        return _format_anonymous_struct_field(f.anonymous_struct, indent)

    bit_suffix = f" : {f.bit_width}" if f.bit_width is not None else ""

    if isinstance(f.type, Array):
        size_str = str(f.type.size) if f.type.size is not None else ""
        return f"{indent}{_type_to_c(f.type.element_type)} {f.name}[{size_str}]{bit_suffix};"
    elif isinstance(f.type, FunctionPointer):
        fp = f.type
        fp_params = _format_params(fp.parameters, fp.is_variadic)
        cc = f"__{fp.calling_convention}__ " if fp.calling_convention else ""
        return f"{indent}{cc}{_type_to_c(fp.return_type)} (*{f.name})({fp_params});"
    elif isinstance(f.type, Pointer) and isinstance(f.type.pointee, FunctionPointer):
        fp = f.type.pointee
        fp_params = _format_params(fp.parameters, fp.is_variadic)
        cc = f"__{fp.calling_convention}__ " if fp.calling_convention else ""
        return f"{indent}{cc}{_type_to_c(fp.return_type)} (**{f.name})({fp_params});"
    else:
        return f"{indent}{_type_to_c(f.type)} {f.name}{bit_suffix};"


def _format_anonymous_struct_field(anon: Struct, indent: str = "    ") -> str:
    """Format an anonymous struct/union as an inline field block."""
    kind = "union" if anon.is_union else "struct"
    lines = [f"{indent}{kind} {{"]
    for inner_f in anon.fields:
        inner_line = _format_field(inner_f, indent + "    ")
        lines.append(inner_line)
    lines.append(f"{indent}}};")
    return "\n".join(lines)


def _is_anonymous_name(name: str | None) -> bool:
    """Check if a name is a synthesized anonymous name from libclang."""
    if name is None:
        return True
    return "(unnamed" in name or "(anonymous" in name


def _is_integer_constant(value: int | float | str | None) -> bool:
    """Check if a constant value is an integer suitable for cdef."""
    return isinstance(value, int)


def _is_float_constant(value: int | float | str | None) -> bool:
    """Check if a constant value is a float."""
    return isinstance(value, float)


def _is_string_constant(value: int | float | str | None) -> bool:
    """Check if a constant value is a string literal.

    String constants in the IR are stored as strings with quotes,
    e.g., ``'"hello"'``.
    """
    return isinstance(value, str) and value.startswith('"')


def _struct_to_cdef(decl: Struct) -> str | None:
    """Convert a Struct/Union IR node to a C declaration for ffi.cdef."""
    if decl.name is None or _is_anonymous_name(decl.name):
        return None

    kind = "union" if decl.is_union else "struct"

    if not decl.fields:
        # Opaque type: forward declaration
        return f"typedef {kind} {decl.name} {decl.name};"

    lines: list[str] = []
    packed_attr = " __attribute__((packed))" if decl.is_packed else ""
    if decl.is_typedef:
        lines.append(f"typedef {kind}{packed_attr} {{")
    else:
        lines.append(f"{kind}{packed_attr} {{")

    for f in decl.fields:
        lines.append(_format_field(f))

    if decl.is_typedef:
        lines.append(f"}} {decl.name};")
    else:
        lines.append(f"}} {decl.name};")

    return "\n".join(lines)


def _enum_to_cdef(decl: Enum) -> str | None:
    """Convert an Enum IR node to a C declaration for ffi.cdef."""
    if not decl.values:
        return None

    is_anon = _is_anonymous_name(decl.name)

    lines: list[str] = []
    lines.append("typedef enum {")

    for v in decl.values:
        if v.value is not None:
            lines.append(f"    {v.name} = {v.value},")
        else:
            lines.append(f"    {v.name},")

    if not is_anon and decl.name:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return "\n".join(lines)


def _function_to_cdef(decl: Function) -> str:
    """Convert a Function IR node to a C declaration for ffi.cdef."""
    params = _format_params(decl.parameters, decl.is_variadic)
    cc = f"__{decl.calling_convention}__ " if decl.calling_convention else ""
    return f"{cc}{_type_to_c(decl.return_type)} {decl.name}({params});"


def _typedef_to_cdef(decl: Typedef) -> str | None:
    """Convert a Typedef IR node to a C declaration for ffi.cdef."""
    underlying = decl.underlying_type

    # Function pointer typedef: typedef ret (*name)(params);
    if isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
        fp = underlying.pointee
        fp_params = _format_params(fp.parameters, fp.is_variadic)
        cc = f"__{fp.calling_convention}__ " if fp.calling_convention else ""
        return f"typedef {cc}{_type_to_c(fp.return_type)} (*{decl.name})({fp_params});"

    # Direct function pointer typedef
    if isinstance(underlying, FunctionPointer):
        fp_params = _format_params(underlying.parameters, underlying.is_variadic)
        cc = f"__{underlying.calling_convention}__ " if underlying.calling_convention else ""
        return f"typedef {cc}{_type_to_c(underlying.return_type)} (*{decl.name})({fp_params});"

    # Array typedef: typedef int name[10]
    if isinstance(underlying, Array):
        size_str = str(underlying.size) if underlying.size is not None else ""
        return f"typedef {_type_to_c(underlying.element_type)} {decl.name}[{size_str}];"

    # Regular typedef
    return f"typedef {_type_to_c(underlying)} {decl.name};"


def _constant_to_cdef(decl: Constant) -> str | None:
    """Convert an integer Constant to a static const inside cdef.

    Only integer constants can go inside ffi.cdef. Float and string
    constants are handled separately as Lua variables.
    """
    if _is_integer_constant(decl.value):
        return f"static const int {decl.name} = {decl.value};"
    return None


def _constant_to_lua(decl: Constant) -> str | None:
    """Convert a non-integer Constant to a Lua variable assignment.

    Float constants become Lua numbers, string constants become Lua strings.
    """
    if _is_float_constant(decl.value):
        return f"local {decl.name} = {decl.value}"
    if _is_string_constant(decl.value):
        return f"local {decl.name} = {decl.value}"
    return None


def _variable_to_cdef(decl: Variable) -> str:
    """Convert a Variable IR node to a C declaration for ffi.cdef."""
    if isinstance(decl.type, Array):
        size_str = str(decl.type.size) if decl.type.size is not None else ""
        return f"{_type_to_c(decl.type.element_type)} {decl.name}[{size_str}];"
    return f"{_type_to_c(decl.type)} {decl.name};"


def header_to_lua(header: Header) -> str:
    """Convert all declarations in a Header to a LuaJIT FFI binding file.

    :param header: Parsed header IR from headerkit.
    :returns: A complete Lua file with ffi.cdef declarations.
    """
    cdef_sections: dict[str, list[str]] = {
        "constants": [],
        "enums": [],
        "structs": [],
        "opaque": [],
        "callbacks": [],
        "functions": [],
    }
    lua_vars: list[str] = []

    for decl in header.declarations:
        if isinstance(decl, Constant):
            cdef_line = _constant_to_cdef(decl)
            if cdef_line is not None:
                cdef_sections["constants"].append(cdef_line)
            else:
                lua_line = _constant_to_lua(decl)
                if lua_line is not None:
                    lua_vars.append(lua_line)

        elif isinstance(decl, Enum):
            result = _enum_to_cdef(decl)
            if result is not None:
                cdef_sections["enums"].append(result)

        elif isinstance(decl, Struct):
            result = _struct_to_cdef(decl)
            if result is not None:
                if not decl.fields:
                    cdef_sections["opaque"].append(result)
                else:
                    cdef_sections["structs"].append(result)

        elif isinstance(decl, Function):
            result = _function_to_cdef(decl)
            cdef_sections["functions"].append(result)

        elif isinstance(decl, Typedef):
            underlying = decl.underlying_type
            is_fnptr = (
                isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer)
            ) or isinstance(underlying, FunctionPointer)

            result = _typedef_to_cdef(decl)
            if result is not None:
                if is_fnptr:
                    cdef_sections["callbacks"].append(result)
                else:
                    # Regular typedefs go with structs section
                    cdef_sections["structs"].append(result)

        elif isinstance(decl, Variable):
            result = _variable_to_cdef(decl)
            cdef_sections["functions"].append(result)

    # Build the output
    output_lines: list[str] = []
    output_lines.append("-- Auto-generated LuaJIT FFI bindings")
    output_lines.append(f"-- Source: {header.path}")
    output_lines.append("-- Generated by headerkit")
    output_lines.append("")
    output_lines.append('local ffi = require("ffi")')
    output_lines.append("")

    # Emit Lua variables for non-integer constants (before cdef block)
    if lua_vars:
        for lv in lua_vars:
            output_lines.append(lv)
        output_lines.append("")

    output_lines.append("ffi.cdef[[")
    output_lines.append("")

    section_labels = {
        "constants": "Constants",
        "enums": "Enums",
        "structs": "Structs",
        "opaque": "Opaque types",
        "callbacks": "Callback typedefs",
        "functions": "Functions",
    }

    first_section = True
    for key in ("constants", "enums", "structs", "opaque", "callbacks", "functions"):
        items = cdef_sections[key]
        if not items:
            continue
        if not first_section:
            output_lines.append("")
        first_section = False
        output_lines.append(f"/* {section_labels[key]} */")
        for item in items:
            output_lines.append(item)

    output_lines.append("")
    output_lines.append("]]")
    output_lines.append("")
    output_lines.append("return {}")
    output_lines.append("")

    return "\n".join(output_lines)


class LuaWriter:
    """Writer that generates LuaJIT FFI binding files from headerkit IR.

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("lua")
        lua_source = writer.write(header)

        # Or directly:
        from headerkit.writers.lua import LuaWriter
        writer = LuaWriter()
        lua_source = writer.write(header)
    """

    default_output_pattern: str = "{dir}/{stem}_ffi.lua"

    def __init__(self) -> None:
        pass

    def write(self, header: Header) -> str:
        """Convert header IR to a LuaJIT FFI binding file."""
        return header_to_lua(header)

    @property
    def name(self) -> str:
        return "lua"

    @property
    def format_description(self) -> str:
        return "LuaJIT FFI bindings"

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Lua comments."""
        return "-- {line}"


# Uses bottom-of-module self-registration. See headerkit/writers/cffi.py
# for the pattern explanation.
from headerkit.writers import register_writer  # noqa: E402

register_writer("lua", LuaWriter, description="LuaJIT FFI bindings")
