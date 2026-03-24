"""Generate CFFI cdef strings from headerkit IR declarations.

This module converts headerkit IR (Intermediate Representation) objects into
CFFI-compatible C declaration strings suitable for passing to
``ffibuilder.cdef()``.

The IR types come from ``headerkit.ir`` and represent parsed C headers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Declaration,
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

# Maps bare tag names to their kind ("struct", "union", "enum").
# Populated by header_to_cffi before emitting declarations.
TagKinds = dict[str, str]


def _qualify_ctype_name(name: str, tag_kinds: TagKinds) -> str:
    """Add struct/union/enum prefix if `name` is a known tag.

    CFFI's cdef parser (backed by pycparser) requires struct/union/enum
    tags to be prefixed with their kind. The libclang IR sometimes drops
    the prefix when a typedef aliases a struct tag.
    """
    # Already has a struct/union/enum prefix
    if name.startswith(("struct ", "union ", "enum ")):
        return name
    if name in tag_kinds:
        return f"{tag_kinds[name]} {name}"
    return name


def type_to_cffi(t: TypeExpr, tag_kinds: TagKinds | None = None) -> str:
    """Convert a type expression to its CFFI string representation."""
    tags = tag_kinds or {}
    if isinstance(t, CType):
        base = str(t)
        # Qualify bare struct/union/enum names
        if t.qualifiers:
            # Qualifiers are separate: re-qualify the name part
            qualified_name = _qualify_ctype_name(t.name, tags)
            return f"{' '.join(t.qualifiers)} {qualified_name}"
        return _qualify_ctype_name(base, tags)
    elif isinstance(t, Pointer):
        # Pointer to function pointer: the (*) already provides indirection
        if isinstance(t.pointee, FunctionPointer):
            fp = t.pointee
            params = _format_params(fp.parameters, fp.is_variadic, tags)
            return f"{type_to_cffi(fp.return_type, tags)}(*)({params})"
        # Collect consecutive pointer levels to emit "type **" not "type * *"
        ptr_count = 0
        inner: TypeExpr = t
        while isinstance(inner, Pointer):
            # Stop unwinding if we hit a function pointer pointee
            if isinstance(inner.pointee, FunctionPointer):
                break
            ptr_count += 1
            inner = inner.pointee
        if isinstance(inner, FunctionPointer):
            # Pointer(s) wrapping a function pointer
            fp = inner
            params = _format_params(fp.parameters, fp.is_variadic, tags)
            stars = "*" * (ptr_count + 1)  # +1 for the (*) in the fnptr
            return f"{type_to_cffi(fp.return_type, tags)}({stars})({params})"
        base = type_to_cffi(inner, tags)
        return f"{base} {'*' * ptr_count}"
    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{type_to_cffi(t.element_type, tags)}[{size_str}]"
    elif isinstance(t, FunctionPointer):
        params = _format_params(t.parameters, t.is_variadic, tags)
        return f"{type_to_cffi(t.return_type, tags)}(*)({params})"
    else:
        return str(t)


def _format_params(
    parameters: list[Parameter],
    is_variadic: bool,
    tag_kinds: TagKinds | None = None,
) -> str:
    """Format function parameter list as a string."""
    tags = tag_kinds or {}
    if not parameters and not is_variadic:
        return "void"
    parts = []
    for p in parameters:
        type_str = type_to_cffi(p.type, tags)
        if p.name:
            # Arrays: dimension goes after the name
            if isinstance(p.type, Array):
                size_str = str(p.type.size) if p.type.size is not None else ""
                parts.append(f"{type_to_cffi(p.type.element_type, tags)} {p.name}[{size_str}]")
            elif isinstance(p.type, FunctionPointer):
                fp = p.type
                fp_params = _format_params(fp.parameters, fp.is_variadic, tags)
                parts.append(f"{type_to_cffi(fp.return_type, tags)} (*{p.name})({fp_params})")
            else:
                parts.append(f"{type_str} {p.name}")
        else:
            parts.append(type_str)
    if is_variadic:
        parts.append("...")
    return ", ".join(parts)


def _format_field(f: Field, tag_kinds: TagKinds | None = None) -> str:
    """Format a struct/union field declaration."""
    tags = tag_kinds or {}

    if f.anonymous_struct is not None:
        return _format_anonymous_struct_field(f.anonymous_struct, tags)

    bit_suffix = f" : {f.bit_width}" if f.bit_width is not None else ""

    if isinstance(f.type, Array):
        size_str = str(f.type.size) if f.type.size is not None else ""
        return f"    {type_to_cffi(f.type.element_type, tags)} {f.name}[{size_str}]{bit_suffix};"
    elif isinstance(f.type, FunctionPointer):
        fp = f.type
        fp_params = _format_params(fp.parameters, fp.is_variadic, tags)
        return f"    {type_to_cffi(fp.return_type, tags)} (*{f.name})({fp_params});"
    elif isinstance(f.type, Pointer) and isinstance(f.type.pointee, FunctionPointer):
        fp = f.type.pointee
        fp_params = _format_params(fp.parameters, fp.is_variadic, tags)
        return f"    {type_to_cffi(fp.return_type, tags)} (**{f.name})({fp_params});"
    else:
        return f"    {type_to_cffi(f.type, tags)} {f.name}{bit_suffix};"


def _format_anonymous_struct_field(anon: Struct, tag_kinds: TagKinds | None = None) -> str:
    """Format an anonymous struct/union as an inline field block."""
    tags = tag_kinds or {}
    kind = "union" if anon.is_union else "struct"
    lines = [f"    {kind} {{"]
    for inner_f in anon.fields:
        inner_line = _format_field(inner_f, tags)
        lines.append(f"    {inner_line}")
    lines.append("    };")
    return "\n".join(lines)


def decl_to_cffi(
    decl: Declaration,
    exclude_patterns: Sequence[re.Pattern[str]] | None = None,
    tag_kinds: TagKinds | None = None,
) -> str | None:
    """Convert a single IR declaration to a CFFI cdef string.

    Returns None if the declaration should be excluded.
    """
    # Check exclusion patterns against declaration name
    if exclude_patterns:
        name = getattr(decl, "name", None)
        if name:
            for pat in exclude_patterns:
                if pat.search(name):
                    return None

    if isinstance(decl, Struct):
        return _struct_to_cffi(decl, tag_kinds)
    elif isinstance(decl, Enum):
        return _enum_to_cffi(decl)
    elif isinstance(decl, Function):
        return _function_to_cffi(decl, exclude_patterns, tag_kinds)
    elif isinstance(decl, Typedef):
        return _typedef_to_cffi(decl, tag_kinds)
    elif isinstance(decl, Constant):
        return _constant_to_cffi(decl)
    elif isinstance(decl, Variable):
        return _variable_to_cffi(decl, tag_kinds)
    else:
        return None


def _struct_to_cffi(decl: Struct, tag_kinds: TagKinds | None = None) -> str | None:
    """Convert a Struct/Union IR node to CFFI cdef."""
    if decl.name is None or _is_anonymous_name(decl.name):
        return None

    kind = "union" if decl.is_union else "struct"

    if not decl.fields:
        # Opaque type
        if decl.is_typedef:
            return f"typedef {kind} {decl.name} {decl.name};"
        else:
            return f"{kind} {decl.name} {{ ...; }};"

    lines = []
    if decl.is_packed:
        lines.append("/* packed */")
    if decl.is_typedef:
        lines.append(f"typedef {kind} {decl.name} {{")
    else:
        lines.append(f"{kind} {decl.name} {{")

    for f in decl.fields:
        lines.append(_format_field(f, tag_kinds))

    if decl.is_typedef:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return "\n".join(lines)


def _is_anonymous_name(name: str | None) -> bool:
    """Check if a name is a synthesized anonymous name from libclang."""
    if name is None:
        return True
    # libclang synthesizes names like "(unnamed at /path/to/file.h:123:1)"
    return "(unnamed" in name or "(anonymous" in name


def _enum_to_cffi(decl: Enum) -> str | None:
    """Convert an Enum IR node to CFFI cdef."""
    if not decl.values:
        return None

    is_anon = _is_anonymous_name(decl.name)

    lines = []
    if decl.is_typedef and not is_anon:
        lines.append(f"typedef enum {decl.name} {{")
    elif not is_anon:
        lines.append(f"enum {decl.name} {{")
    else:
        lines.append("enum {")

    value_lines = []
    for v in decl.values:
        if v.value is not None:
            value_lines.append(f"    {v.name} = {v.value},")
        else:
            value_lines.append(f"    {v.name},")

    lines.extend(value_lines)

    if decl.is_typedef and not is_anon:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return "\n".join(lines)


def _function_to_cffi(
    decl: Function,
    exclude_patterns: Sequence[re.Pattern[str]] | None = None,
    tag_kinds: TagKinds | None = None,
) -> str | None:
    """Convert a Function IR node to CFFI cdef."""
    if exclude_patterns:
        for pat in exclude_patterns:
            if pat.search(decl.name):
                return None

    params = _format_params(decl.parameters, decl.is_variadic, tag_kinds)
    return f"{type_to_cffi(decl.return_type, tag_kinds)} {decl.name}({params});"


def _typedef_to_cffi(decl: Typedef, tag_kinds: TagKinds | None = None) -> str | None:
    """Convert a Typedef IR node to CFFI cdef."""
    underlying = decl.underlying_type

    # Function pointer typedef: typedef void (*name)(int, char *)
    # In the IR this is Pointer(FunctionPointer(...))
    if isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
        fp = underlying.pointee
        fp_params = _format_params(fp.parameters, fp.is_variadic, tag_kinds)
        return f"typedef {type_to_cffi(fp.return_type, tag_kinds)} (*{decl.name})({fp_params});"

    # Direct function pointer typedef (shouldn't normally happen but handle it)
    if isinstance(underlying, FunctionPointer):
        fp_params = _format_params(underlying.parameters, underlying.is_variadic, tag_kinds)
        return f"typedef {type_to_cffi(underlying.return_type, tag_kinds)} (*{decl.name})({fp_params});"

    # Array typedef: typedef int name[10]
    if isinstance(underlying, Array):
        size_str = str(underlying.size) if underlying.size is not None else ""
        return f"typedef {type_to_cffi(underlying.element_type, tag_kinds)} {decl.name}[{size_str}];"

    # Regular typedef
    return f"typedef {type_to_cffi(underlying, tag_kinds)} {decl.name};"


def _constant_to_cffi(decl: Constant) -> str | None:
    """Convert a Constant IR node to CFFI cdef.

    CFFI's #define only supports integer constants or '...' (resolved at
    compile time as an integer). String macros, expression macros, and
    macros with unknown values are skipped entirely since CFFI cannot
    handle non-integer constant macros.
    """
    if decl.value is not None and isinstance(decl.value, int):
        return f"#define {decl.name} {decl.value}"
    # Skip non-integer constants (strings, expressions, unknown values).
    # Callers can use _extract_defines() for specific integer #defines
    # that libclang couldn't resolve.
    return None


def _variable_to_cffi(decl: Variable, tag_kinds: TagKinds | None = None) -> str | None:
    """Convert a Variable IR node to CFFI cdef."""
    if isinstance(decl.type, Array):
        size_str = str(decl.type.size) if decl.type.size is not None else ""
        return f"{type_to_cffi(decl.type.element_type, tag_kinds)} {decl.name}[{size_str}];"
    return f"{type_to_cffi(decl.type, tag_kinds)} {decl.name};"


def _build_tag_kinds(declarations: list[Declaration]) -> TagKinds:
    """Build a mapping of struct/union/enum tag names to their kind.

    This is needed because the libclang backend may emit typedefs that
    reference struct tags by bare name (without the struct/union/enum prefix).
    CFFI's pycparser-based cdef parser requires the prefix.

    We only add the tag prefix when the tag name differs from any typedef name.
    When a name is both a tag and a typedef (e.g., ``typedef enum nng_pipe_ev
    { ... } nng_pipe_ev``), the real C header may have an anonymous tag, so
    we must NOT prefix it -- the typedef name is what the C compiler knows.
    """
    # Collect all tag names
    tags: TagKinds = {}
    for decl in declarations:
        if isinstance(decl, Struct) and decl.name and not _is_anonymous_name(decl.name):
            kind = "union" if decl.is_union else "struct"
            tags[decl.name] = kind
        elif isinstance(decl, Enum) and decl.name and not _is_anonymous_name(decl.name):
            tags[decl.name] = "enum"

    # Collect all typedef names
    typedef_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            typedef_names.add(decl.name)

    # Only keep tags whose names are NOT also typedef'd with the same name.
    # If "nng_pipe_ev" is both an enum tag AND a typedef name, the original
    # header likely used "typedef enum { ... } nng_pipe_ev;" (anonymous tag),
    # so we should use the bare typedef name, not "enum nng_pipe_ev".
    tag_kinds: TagKinds = {}
    for name, kind in tags.items():
        if name not in typedef_names:
            tag_kinds[name] = kind
    return tag_kinds


def _find_opaque_typedef_structs(declarations: list[Declaration]) -> set[str]:
    """Find opaque struct names that also have matching typedefs.

    For opaque structs (no fields) with a same-name typedef, we should only
    emit the typedef to avoid CFFI trying to compute the struct's size.
    """
    opaque_structs: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Struct) and decl.name and not decl.fields and not decl.is_typedef:
            if not _is_anonymous_name(decl.name):
                opaque_structs.add(decl.name)

    typedef_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            typedef_names.add(decl.name)

    return opaque_structs & typedef_names


def _find_typedef_enum_pairs(declarations: list[Declaration]) -> set[str]:
    """Find enum names that also appear as typedefs.

    When the original C header uses ``typedef enum { ... } name;`` (anonymous
    tag), libclang creates both ``Enum(name='name')`` and
    ``Typedef(name='name', underlying=CType('enum name'))``.  We need to
    combine these back into a single ``typedef enum { ... } name;`` to avoid
    introducing a tag name that doesn't exist in the real header.

    This only applies to enums. Structs/unions always have real tags in C
    (you can't forward-declare an anonymous struct), so struct+typedef pairs
    should be emitted separately.

    Returns a set of enum names that should be emitted as combined
    ``typedef enum { ... } name;`` declarations.
    """
    # Collect non-typedef enum names
    enum_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Enum) and decl.name and not _is_anonymous_name(decl.name):
            if not decl.is_typedef:
                enum_names.add(decl.name)

    # Collect typedef names
    typedef_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            typedef_names.add(decl.name)

    # Enum names that also appear as typedefs
    return enum_names & typedef_names


def header_to_cffi(
    header: Header,
    exclude_patterns: list[str] | None = None,
) -> str:
    """Convert all declarations in a Header to a CFFI cdef string.

    :param header: Parsed header IR from headerkit.
    :param exclude_patterns: List of regex patterns. Declarations with names
        matching any pattern will be excluded.
    :returns: A string suitable for passing to ``ffibuilder.cdef()``.
    """
    compiled_patterns = None
    if exclude_patterns:
        compiled_patterns = [re.compile(p) for p in exclude_patterns]

    # Build tag name map for qualifying bare struct/union/enum references
    tag_kinds = _build_tag_kinds(header.declarations)

    # Find enum names that have matching typedefs -- these need to be
    # emitted as combined "typedef enum { ... } name;" declarations
    # to avoid introducing tag names that don't exist in the real header.
    combined_enum_pairs = _find_typedef_enum_pairs(header.declarations)

    # Find opaque struct names that have matching typedefs.
    # For these, we skip the struct declaration and only emit the typedef,
    # which tells CFFI the type is opaque (always used through pointers).
    opaque_typedef_structs = _find_opaque_typedef_structs(header.declarations)

    lines = []
    for decl in header.declarations:
        # For combined enum+typedef pairs: emit enum as typedef, skip Typedef
        if isinstance(decl, Enum) and decl.name in combined_enum_pairs:
            result = _enum_to_cffi_as_typedef(decl)
            if result is not None:
                lines.append(result)
            continue

        if isinstance(decl, Typedef) and decl.name in combined_enum_pairs:
            # Skip -- already emitted with the enum above
            continue

        # For opaque struct+typedef pairs: skip the struct, keep the typedef
        if isinstance(decl, Struct) and decl.name in opaque_typedef_structs:
            continue

        result = decl_to_cffi(decl, compiled_patterns, tag_kinds)
        if result is not None:
            lines.append(result)

    return "\n".join(lines)


def _enum_to_cffi_as_typedef(decl: Enum) -> str | None:
    """Emit an enum as ``typedef enum { ... } name;`` (no tag name).

    Used when the original header had an anonymous enum with a typedef,
    but libclang assigned the typedef name to the enum tag.
    """
    if not decl.values:
        return None

    lines = ["typedef enum {"]

    for v in decl.values:
        if v.value is not None:
            lines.append(f"    {v.name} = {v.value},")
        else:
            lines.append(f"    {v.name},")

    lines.append(f"}} {decl.name};")
    return "\n".join(lines)


class CffiWriter:
    """Writer that generates CFFI cdef strings from headerkit IR.

    Options
    -------
    exclude_patterns : list[str] | None
        Regex patterns. Declarations with names matching any pattern
        are excluded from output.

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("cffi", exclude_patterns=["__.*"])
        cdef_string = writer.write(header)

        # Or directly:
        from headerkit.writers.cffi import CffiWriter
        writer = CffiWriter(exclude_patterns=["__.*"])
        cdef_string = writer.write(header)
    """

    def __init__(self, exclude_patterns: list[str] | None = None) -> None:
        self._exclude_patterns = exclude_patterns

    def write(self, header: Header) -> str:
        """Convert header IR to CFFI cdef string."""
        return header_to_cffi(header, exclude_patterns=self._exclude_patterns)

    @property
    def name(self) -> str:
        return "cffi"

    @property
    def format_description(self) -> str:
        return "CFFI cdef declarations for ffibuilder.cdef()"

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Python comments."""
        return "# {line}"


# Uses bottom-of-module self-registration. Unlike backends (which import
# register_backend at the top and conditionally register at the bottom),
# writers have no external dependencies so import and registration are
# co-located.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "cffi",
    CffiWriter,
    is_default=True,
    description="CFFI cdef declarations for ffibuilder.cdef()",
)
