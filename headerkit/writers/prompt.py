"""Token-optimized output for LLM context.

Converts headerkit IR to condensed text representations designed to
minimize token usage when embedding C/C++ API information into LLM
prompts. Three verbosity tiers are available: compact, standard, and
verbose.
"""

from __future__ import annotations

import json

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

# =============================================================================
# Type formatting helpers
# =============================================================================


def _type_to_str(t: TypeExpr) -> str:
    """Render a TypeExpr as a terse C-like type string."""
    if isinstance(t, CType):
        if t.qualifiers:
            return f"{' '.join(t.qualifiers)} {t.name}"
        return t.name
    elif isinstance(t, Pointer):
        pointee = _type_to_str(t.pointee)
        quals = f" {' '.join(t.qualifiers)}" if t.qualifiers else ""
        return f"{pointee}*{quals}"
    elif isinstance(t, Array):
        elem = _type_to_str(t.element_type)
        size_str = str(t.size) if t.size is not None else ""
        return f"{elem}[{size_str}]"
    elif isinstance(t, FunctionPointer):
        ret = _type_to_str(t.return_type)
        params = ", ".join(_param_compact(p) for p in t.parameters)
        if t.is_variadic:
            params = f"{params}, ..." if params else "..."
        return f"{ret} (*)({params})"
    else:
        return repr(t)


def _param_compact(p: Parameter) -> str:
    """Render a parameter in compact form: name:type or just type."""
    t = _type_to_str(p.type)
    if p.name:
        return f"{p.name}:{t}"
    return t


def _param_standard(p: Parameter) -> str:
    """Render a parameter in standard form: name: type or type."""
    t = _type_to_str(p.type)
    if p.name:
        return f"{p.name}: {t}"
    return t


# =============================================================================
# Compact mode
# =============================================================================


def _constant_compact(decl: Constant) -> str:
    """Render a constant in compact form."""
    val = decl.value if decl.value is not None else "?"
    return f"CONST {decl.name}={val}"


def _enum_compact(decl: Enum) -> str:
    """Render an enum in compact form."""
    name = decl.name or "(anon)"
    vals = ", ".join(f"{v.name}={v.value}" if v.value is not None else v.name for v in decl.values)
    return f"ENUM {name}: {vals}" if vals else f"ENUM {name}"


def _field_compact(f: Field) -> str:
    """Render a struct/union field in compact form."""
    t = _type_to_str(f.type)
    if f.bit_width is not None:
        return f"{f.name}:{t}:{f.bit_width}b"
    return f"{f.name}:{t}"


def _struct_compact(decl: Struct) -> str:
    """Render a struct/union in compact form."""
    if decl.is_union:
        keyword = "UNION"
    else:
        keyword = "STRUCT"

    packed_prefix = "__packed " if decl.is_packed else ""
    name = decl.name or "(anon)"

    # Opaque struct: no fields
    if not decl.fields:
        return f"{keyword} {packed_prefix}{name} (opaque)"

    fields = ", ".join(_field_compact(f) for f in decl.fields)
    return f"{keyword} {packed_prefix}{name} {{{fields}}}"


def _function_compact(decl: Function) -> str:
    """Render a function in compact form."""
    params_parts = [_param_compact(p) for p in decl.parameters]
    if decl.is_variadic:
        params_parts.append("...")
    params = ", ".join(params_parts)
    ret = _type_to_str(decl.return_type)
    return f"FUNC {decl.name}({params}) -> {ret}"


def _extract_function_pointer(type_expr: TypeExpr) -> FunctionPointer | None:
    """Extract a FunctionPointer from a TypeExpr, handling Pointer wrappers.

    libclang represents `typedef void (*fn)(int);` as
    Typedef(underlying=Pointer(FunctionPointer(...))). Hand-constructed IR or
    other backends may use Typedef(underlying=FunctionPointer(...)) directly.
    Both forms are handled.
    """
    if isinstance(type_expr, FunctionPointer):
        return type_expr
    if isinstance(type_expr, Pointer) and isinstance(type_expr.pointee, FunctionPointer):
        return type_expr.pointee
    return None


def _typedef_compact(decl: Typedef) -> str:
    """Render a typedef in compact form."""
    fp = _extract_function_pointer(decl.underlying_type)
    if fp is not None:
        # Function pointer typedef -> CALLBACK
        params_parts = [_param_compact(p) for p in fp.parameters]
        if fp.is_variadic:
            params_parts.append("...")
        params = ", ".join(params_parts)
        ret = _type_to_str(fp.return_type)
        return f"CALLBACK {decl.name}({params}) -> {ret}"
    return f"TYPEDEF {decl.name} = {_type_to_str(decl.underlying_type)}"


def _variable_compact(decl: Variable) -> str:
    """Render a variable in compact form."""
    return f"VAR {decl.name}:{_type_to_str(decl.type)}"


def _header_to_compact(header: Header) -> str:
    """Render a header in compact mode."""
    lines: list[str] = []
    lines.append(f"// {header.path} (headerkit compact)")

    for decl in header.declarations:
        if isinstance(decl, Constant):
            lines.append(_constant_compact(decl))
        elif isinstance(decl, Enum):
            lines.append(_enum_compact(decl))
        elif isinstance(decl, Struct):
            lines.append(_struct_compact(decl))
        elif isinstance(decl, Function):
            lines.append(_function_compact(decl))
        elif isinstance(decl, Typedef):
            lines.append(_typedef_compact(decl))
        elif isinstance(decl, Variable):
            lines.append(_variable_compact(decl))

    return "\n".join(lines) + "\n"


# =============================================================================
# Standard mode
# =============================================================================


def _header_to_standard(header: Header) -> str:
    """Render a header in standard (YAML-like) mode."""
    lines: list[str] = []
    lines.append(f"# {header.path} (headerkit standard)")

    # Group declarations by type
    constants: list[Constant] = []
    enums: list[Enum] = []
    structs: list[Struct] = []
    functions: list[Function] = []
    typedefs: list[Typedef] = []
    variables: list[Variable] = []
    callbacks: list[Typedef] = []

    for decl in header.declarations:
        if isinstance(decl, Constant):
            constants.append(decl)
        elif isinstance(decl, Enum):
            enums.append(decl)
        elif isinstance(decl, Struct):
            structs.append(decl)
        elif isinstance(decl, Function):
            functions.append(decl)
        elif isinstance(decl, Typedef):
            if _extract_function_pointer(decl.underlying_type) is not None:
                callbacks.append(decl)
            else:
                typedefs.append(decl)
        elif isinstance(decl, Variable):
            variables.append(decl)

    if constants:
        lines.append("")
        lines.append("constants:")
        for c in constants:
            val = c.value if c.value is not None else "?"
            lines.append(f"  {c.name}: {val}")

    if enums:
        lines.append("")
        lines.append("enums:")
        for e in enums:
            name = e.name or "(anon)"
            vals = ", ".join(f"{v.name}: {v.value}" if v.value is not None else v.name for v in e.values)
            lines.append(f"  {name}: {{{vals}}}")

    if structs:
        lines.append("")
        lines.append("structs:")
        for s in structs:
            name = s.name or "(anon)"
            lines.append(f"  {name}:")
            if s.is_packed:
                lines.append("    packed: true")
            if s.is_union:
                lines.append("    union: true")
            if not s.fields:
                lines.append("    opaque: true")
            else:
                lines.append("    fields:")
                for f in s.fields:
                    t = _type_to_str(f.type)
                    if f.bit_width is not None:
                        lines.append(f"      {f.name}: {t} ({f.bit_width} bits)")
                    else:
                        lines.append(f"      {f.name}: {t}")

    if callbacks:
        lines.append("")
        lines.append("callbacks:")
        for td in callbacks:
            fp = _extract_function_pointer(td.underlying_type)
            assert fp is not None
            params_parts = [_param_standard(p) for p in fp.parameters]
            if fp.is_variadic:
                params_parts.append("...")
            params = ", ".join(params_parts)
            ret = _type_to_str(fp.return_type)
            lines.append(f"  {td.name}: ({params}) -> {ret}")

    if functions:
        lines.append("")
        lines.append("functions:")
        for fn in functions:
            params_parts = [_param_standard(p) for p in fn.parameters]
            if fn.is_variadic:
                params_parts.append("...")
            params = ", ".join(params_parts)
            ret = _type_to_str(fn.return_type)
            lines.append(f"  {fn.name}: ({params}) -> {ret}")

    if typedefs:
        lines.append("")
        lines.append("typedefs:")
        for td in typedefs:
            lines.append(f"  {td.name}: {_type_to_str(td.underlying_type)}")

    if variables:
        lines.append("")
        lines.append("variables:")
        for v in variables:
            lines.append(f"  {v.name}: {_type_to_str(v.type)}")

    return "\n".join(lines) + "\n"


# =============================================================================
# Verbose mode (JSON with cross-references)
# =============================================================================


def _collect_type_names(t: TypeExpr) -> set[str]:
    """Recursively collect all named type references from a TypeExpr."""
    names: set[str] = set()
    if isinstance(t, CType):
        # Only collect struct/union/enum references, not primitives
        # Heuristic: names that start with uppercase or contain underscore
        # and are not basic C types
        _PRIMITIVES = {
            "void",
            "int",
            "char",
            "short",
            "long",
            "float",
            "double",
            "signed",
            "unsigned",
            "size_t",
            "ssize_t",
            "ptrdiff_t",
            "intptr_t",
            "uintptr_t",
            "int8_t",
            "int16_t",
            "int32_t",
            "int64_t",
            "uint8_t",
            "uint16_t",
            "uint32_t",
            "uint64_t",
            "bool",
            "_Bool",
        }
        if t.name not in _PRIMITIVES:
            names.add(t.name)
    elif isinstance(t, Pointer):
        names |= _collect_type_names(t.pointee)
    elif isinstance(t, Array):
        names |= _collect_type_names(t.element_type)
    elif isinstance(t, FunctionPointer):
        names |= _collect_type_names(t.return_type)
        for p in t.parameters:
            names |= _collect_type_names(p.type)
    return names


def _get_decl_name(decl: Declaration) -> str | None:
    """Get the name of a declaration."""
    if isinstance(decl, Struct | Enum | Function | Typedef | Variable | Constant):
        return decl.name
    return None


def _get_decl_referenced_types(decl: Declaration) -> set[str]:
    """Get all type names referenced by a declaration."""
    names: set[str] = set()
    if isinstance(decl, Function):
        names |= _collect_type_names(decl.return_type)
        for p in decl.parameters:
            names |= _collect_type_names(p.type)
    elif isinstance(decl, Struct):
        for f in decl.fields:
            names |= _collect_type_names(f.type)
        for m in decl.methods:
            names |= _get_decl_referenced_types(m)
    elif isinstance(decl, Typedef):
        names |= _collect_type_names(decl.underlying_type)
    elif isinstance(decl, Variable):
        names |= _collect_type_names(decl.type)
    elif isinstance(decl, Constant):
        if decl.type is not None:
            names |= _collect_type_names(decl.type)
    return names


def _compute_cross_refs(header: Header) -> dict[str, list[str]]:
    """Map type names to declarations that use them.

    Type names from the IR may carry struct/union/enum prefixes (e.g.
    'struct Config') because libclang preserves the elaborated type spelling.
    Declaration dicts use bare names (e.g. 'Config'). Strip the prefix so
    cross-ref keys match declaration names. Results are sorted for determinism.
    """
    refs: dict[str, set[str]] = {}
    for decl in header.declarations:
        decl_name = _get_decl_name(decl)
        if decl_name is None:
            continue
        referenced = _get_decl_referenced_types(decl)
        for type_name in referenced:
            bare_name = type_name
            for prefix in ("struct ", "union ", "enum "):
                if bare_name.startswith(prefix):
                    bare_name = bare_name[len(prefix) :]
                    break
            refs.setdefault(bare_name, set()).add(decl_name)
    return {k: sorted(v) for k, v in refs.items()}


def _header_to_verbose(header: Header) -> str:
    """Render a header in verbose mode (JSON with cross-references)."""
    from headerkit.writers.json import header_to_json_dict

    data = header_to_json_dict(header)
    cross_refs = _compute_cross_refs(header)

    # Add used_in fields to declarations
    for decl_dict in data["declarations"]:
        name = decl_dict.get("name")
        if name and name in cross_refs:
            decl_dict["used_in"] = sorted(cross_refs[name])

    return json.dumps(data, indent=2)


# =============================================================================
# PromptWriter class
# =============================================================================


class PromptWriter:
    """Writer that produces token-optimized output for LLM context.

    Three verbosity tiers control the output density:

    - **compact**: C-like one-liners, most token-efficient
    - **standard**: YAML-like structured text with type relationships
    - **verbose**: Full JSON with cross-reference metadata

    Options
    -------
    verbosity : str
        Output verbosity tier. One of "compact", "standard", or "verbose".
        Defaults to "compact".

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("prompt", verbosity="compact")
        output = writer.write(header)
    """

    cache_output: bool = False

    def __init__(self, verbosity: str = "compact") -> None:
        if verbosity not in ("compact", "standard", "verbose"):
            raise ValueError(f"Unknown verbosity: {verbosity!r}. Use 'compact', 'standard', or 'verbose'.")
        self._verbosity = verbosity

    def write(self, header: Header) -> str:
        """Convert header IR to token-optimized string."""
        if self._verbosity == "compact":
            return _header_to_compact(header)
        elif self._verbosity == "standard":
            return _header_to_standard(header)
        else:
            return _header_to_verbose(header)

    @property
    def name(self) -> str:
        """Human-readable name of this writer."""
        return "prompt"

    @property
    def format_description(self) -> str:
        """Short description of the output format."""
        return "Token-optimized output for LLM context"


# Uses bottom-of-module self-registration pattern.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "prompt",
    PromptWriter,
    description="Token-optimized output for LLM context",
)
