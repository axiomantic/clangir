"""Serialize headerkit IR to JSON.

Converts a Header and its declarations to a JSON string suitable for
inspection, debugging, inter-process communication, or as input to
custom code generators.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from headerkit.ir import (
    Array,
    BaseSpecifier,
    Constant,
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.base import BaseWriter, WriterOption


def _type_to_dict(t: TypeExpr) -> dict[str, Any]:
    """Convert a TypeExpr to a JSON-serializable dict."""
    if isinstance(t, CType):
        d: dict[str, Any] = {"kind": "ctype", "name": t.name}
        if t.qualifiers:
            d["qualifiers"] = list(t.qualifiers)
        # Tri-state, so ``None`` is a value and not an absence: it means no
        # parser recorded the spelling, which consumers must refuse on rather
        # than default. Omitted only when it is already None, which is what the
        # reader restores.
        if t.is_elaborated is not None:
            d["is_elaborated"] = t.is_elaborated
        return d
    elif isinstance(t, Pointer):
        d = {"kind": "pointer", "pointee": _type_to_dict(t.pointee)}
        if t.qualifiers:
            d["qualifiers"] = list(t.qualifiers)
        return d
    elif isinstance(t, Reference):
        d = {"kind": "reference", "target": _type_to_dict(t.target)}
        if t.is_rvalue:
            d["is_rvalue"] = True
        if t.qualifiers:
            d["qualifiers"] = list(t.qualifiers)
        return d
    elif isinstance(t, Array):
        d = {"kind": "array", "element_type": _type_to_dict(t.element_type)}
        if t.size is not None:
            d["size"] = t.size
        return d
    elif isinstance(t, FunctionPointer):
        d = {
            "kind": "function_pointer",
            "return_type": _type_to_dict(t.return_type),
            "parameters": [_param_to_dict(p) for p in t.parameters],
            "is_variadic": t.is_variadic,
        }
        if t.calling_convention:
            d["calling_convention"] = t.calling_convention
        return d
    else:
        return {"kind": "unknown", "repr": repr(t)}


def _location_to_dict(loc: SourceLocation) -> dict[str, Any]:
    """Convert a SourceLocation to a JSON-serializable dict."""
    d: dict[str, Any] = {"file": loc.file, "line": loc.line}
    if loc.column is not None:
        d["column"] = loc.column
    return d


def _param_to_dict(p: Parameter) -> dict[str, Any]:
    """Convert a Parameter to a dict."""
    d: dict[str, Any] = {"type": _type_to_dict(p.type)}
    if p.name:
        d["name"] = p.name
    if p.default_value is not None:
        d["default_value"] = p.default_value
    return d


def _base_to_dict(b: BaseSpecifier) -> dict[str, Any]:
    """Convert a BaseSpecifier to a dict."""
    d: dict[str, Any] = {"name": b.name, "access": b.access}
    if b.is_virtual:
        d["is_virtual"] = True
    return d


def _field_to_dict(f: Field) -> dict[str, Any]:
    """Convert a Field to a dict."""
    d: dict[str, Any] = {"name": f.name, "type": _type_to_dict(f.type)}
    if f.bit_width is not None:
        d["bit_width"] = f.bit_width
    if f.anonymous_struct is not None:
        d["anonymous_struct"] = _decl_to_dict(f.anonymous_struct)
    if f.is_anonymous_transparent:
        d["is_anonymous_transparent"] = True
    if f.access is not None:
        d["access"] = f.access
    if f.is_static:
        d["is_static"] = True
    return d


def _decl_to_dict(decl: Declaration) -> dict[str, Any]:
    """Convert a Declaration to a JSON-serializable dict."""
    if isinstance(decl, Struct):
        d: dict[str, Any] = {
            "kind": "union" if decl.is_union else "struct",
            "name": decl.name,
            "fields": [_field_to_dict(f) for f in decl.fields] if decl.fields else [],
        }
        if decl.is_typedef:
            d["is_typedef"] = True
        if decl.methods:
            d["methods"] = [_decl_to_dict(m) for m in decl.methods]
        if decl.is_cppclass:
            d["is_cppclass"] = True
        if decl.namespace:
            d["namespace"] = decl.namespace
        if decl.template_params:
            d["template_params"] = decl.template_params
        if decl.cpp_name:
            d["cpp_name"] = decl.cpp_name
        if decl.is_packed:
            d["is_packed"] = True
        if decl.notes:
            d["notes"] = decl.notes
        if decl.inner_typedefs:
            d["inner_typedefs"] = decl.inner_typedefs
        if decl.bases:
            d["bases"] = [_base_to_dict(b) for b in decl.bases]
        if decl.is_abstract:
            d["is_abstract"] = True
        if decl.constructors:
            d["constructors"] = [_decl_to_dict(c) for c in decl.constructors]
        if decl.destructor is not None:
            d["destructor"] = _decl_to_dict(decl.destructor)
        if decl.conversions:
            d["conversions"] = [_decl_to_dict(c) for c in decl.conversions]
        if decl.vtable_entries:
            d["vtable_entries"] = [_decl_to_dict(v) for v in decl.vtable_entries]
        if decl.attributes:
            d["attributes"] = decl.attributes
        if decl.is_deprecated:
            d["is_deprecated"] = True
        if decl.alignment is not None:
            d["alignment"] = decl.alignment
        if decl.location is not None:
            d["location"] = _location_to_dict(decl.location)
        return d
    elif isinstance(decl, Enum):
        d = {
            "kind": "enum",
            "name": decl.name,
            "values": [_enum_value_to_dict(v) for v in decl.values],
        }
        if decl.underlying_type is not None:
            d["underlying_type"] = decl.underlying_type
        if not decl.underlying_type_known:
            d["underlying_type_known"] = False
        if decl.is_typedef:
            d["is_typedef"] = True
        if decl.location is not None:
            d["location"] = _location_to_dict(decl.location)
        return d
    elif isinstance(decl, Function):
        d = {
            "kind": "function",
            "name": decl.name,
            "return_type": _type_to_dict(decl.return_type),
            "parameters": [_param_to_dict(p) for p in decl.parameters],
            "is_variadic": decl.is_variadic,
        }
        if decl.calling_convention:
            d["calling_convention"] = decl.calling_convention
        if decl.namespace:
            d["namespace"] = decl.namespace
        if decl.template_params:
            d["template_params"] = decl.template_params
        if decl.is_static:
            d["is_static"] = True
        if decl.is_const:
            d["is_const"] = True
        if decl.is_virtual:
            d["is_virtual"] = True
        if decl.is_pure_virtual:
            d["is_pure_virtual"] = True
        if decl.is_explicit:
            d["is_explicit"] = True
        if decl.access is not None:
            d["access"] = decl.access
        if decl.is_deleted:
            d["is_deleted"] = True
        if decl.is_defaulted:
            d["is_defaulted"] = True
        if decl.is_noexcept:
            d["is_noexcept"] = True
        if decl.is_inline:
            d["is_inline"] = True
        if decl.body is not None:
            d["body"] = decl.body
        if decl.attributes:
            d["attributes"] = decl.attributes
        if decl.is_deprecated:
            d["is_deprecated"] = True
        if decl.location is not None:
            d["location"] = _location_to_dict(decl.location)
        return d
    elif isinstance(decl, Typedef):
        d = {
            "kind": "typedef",
            "name": decl.name,
            "underlying_type": _type_to_dict(decl.underlying_type),
        }
        if decl.namespace:
            d["namespace"] = decl.namespace
        if decl.attributes:
            d["attributes"] = decl.attributes
        if decl.is_deprecated:
            d["is_deprecated"] = True
        if decl.location is not None:
            d["location"] = _location_to_dict(decl.location)
        return d
    elif isinstance(decl, Variable):
        d = {
            "kind": "variable",
            "name": decl.name,
            "type": _type_to_dict(decl.type),
        }
        if decl.namespace:
            d["namespace"] = decl.namespace
        if decl.attributes:
            d["attributes"] = decl.attributes
        if decl.is_deprecated:
            d["is_deprecated"] = True
        if decl.alignment is not None:
            d["alignment"] = decl.alignment
        if decl.location is not None:
            d["location"] = _location_to_dict(decl.location)
        return d
    elif isinstance(decl, Constant):
        d = {
            "kind": "constant",
            "name": decl.name,
        }
        if decl.value is not None:
            d["value"] = decl.value
        if decl.evaluated_value is not None:
            d["evaluated_value"] = decl.evaluated_value
        if decl.raw_expression is not None:
            d["raw_expression"] = decl.raw_expression
        if decl.is_macro:
            d["is_macro"] = True
        if decl.type is not None:
            d["type"] = _type_to_dict(decl.type)
        if decl.location is not None:
            d["location"] = _location_to_dict(decl.location)
        return d
    else:
        return {"kind": "unknown", "repr": repr(decl)}


def _enum_value_to_dict(v: EnumValue) -> dict[str, Any]:
    """Convert an EnumValue to a dict."""
    d: dict[str, Any] = {"name": v.name}
    if v.value is not None:
        d["value"] = v.value
    return d


def header_to_json(header: Header, indent: int | None = 2) -> str:
    """Convert a Header IR to a JSON string.

    :param header: Parsed header IR.
    :param indent: JSON indentation level. None for compact output.
    :returns: JSON string representing the header and all declarations.
    """
    return json.dumps(header_to_json_dict(header), indent=indent)


def header_to_json_dict(header: Header) -> dict[str, Any]:
    """Convert a Header IR to a JSON-serializable dict (no string encoding).

    :param header: Parsed header IR.
    :returns: Dict representation of the header suitable for ``json.dumps()``.
    """
    data: dict[str, Any] = {
        "path": header.path,
        "declarations": [_decl_to_dict(d) for d in header.declarations],
    }
    if header.included_headers:
        data["included_headers"] = sorted(header.included_headers)
    return data


class JsonWriter(BaseWriter):
    """Writer that serializes headerkit IR to JSON.

    Options
    -------
    indent : int | None
        JSON indentation level. Defaults to 2. None for compact output.

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("json", indent=4)
        json_string = writer.write(header)
    """

    name: str = "json"
    format_description: str = "JSON serialization of IR for inspection and tooling"
    default_output_pattern: str = "{dir}/{stem}.json"
    default_extension: str = ".json"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption("indent", "JSON indentation level", default=2, type=int),
    )

    def __init__(self, indent: int | None = 2) -> None:
        self._indent = indent

    def _write_single_file_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        indent = options.get_option("indent", self._indent)
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        content = header_to_json(header, indent=indent)
        filename = f"{options.package_name}{self.default_extension}"
        return ProjectLayout(files=[OutputFile(path=filename, content=content)])

    def _render(self, unit: SourceUnit | Header) -> str:
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        return header_to_json(header, indent=self._indent)

    def write(self, header: Header) -> str:
        """Convert header IR to JSON string."""
        return self._render(self._prepare(header))


# Uses bottom-of-module self-registration. Unlike backends (which import
# register_backend at the top and conditionally register at the bottom),
# writers have no external dependencies so import and registration are
# co-located.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "json",
    JsonWriter,
    description="JSON serialization of IR for inspection and tooling",
)
