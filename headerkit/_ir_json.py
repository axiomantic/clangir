"""Deserialize JSON back into headerkit IR objects.

This is the inverse of headerkit.writers.json serialization.
The primary invariant: json_to_header(header_to_json_dict(h)) == h
for any Header h.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)

# =========================================================================
# Type Deserializers
# =========================================================================


def _dict_to_ctype(d: dict[str, Any]) -> CType:
    return CType(
        name=d["name"],
        qualifiers=d.get("qualifiers", []),
        is_elaborated=d.get("is_elaborated"),
    )


def _dict_to_pointer(d: dict[str, Any]) -> Pointer:
    return Pointer(
        pointee=_dict_to_type(d["pointee"]),
        qualifiers=d.get("qualifiers", []),
    )


def _dict_to_reference(d: dict[str, Any]) -> Reference:
    return Reference(
        target=_dict_to_type(d["target"]),
        is_rvalue=d.get("is_rvalue", False),
        qualifiers=d.get("qualifiers", []),
    )


def _dict_to_array(d: dict[str, Any]) -> Array:
    return Array(
        element_type=_dict_to_type(d["element_type"]),
        size=d.get("size"),
    )


def _dict_to_function_pointer(d: dict[str, Any]) -> FunctionPointer:
    return FunctionPointer(
        return_type=_dict_to_type(d["return_type"]),
        parameters=[_dict_to_parameter(p) for p in d.get("parameters", [])],
        is_variadic=d.get("is_variadic", False),
        calling_convention=d.get("calling_convention"),
    )


_TYPE_DESERIALIZERS: dict[str, Callable[[dict[str, Any]], TypeExpr]] = {
    "ctype": _dict_to_ctype,
    "pointer": _dict_to_pointer,
    "reference": _dict_to_reference,
    "array": _dict_to_array,
    "function_pointer": _dict_to_function_pointer,
}


def _dict_to_type(d: dict[str, Any]) -> TypeExpr:
    """Dispatch to the correct type deserializer based on 'kind'."""
    kind = d.get("kind")
    if kind is None:
        raise ValueError(f"Type dict missing 'kind': {d}")
    deserializer = _TYPE_DESERIALIZERS.get(kind)
    if deserializer is None:
        raise ValueError(f"Unknown type kind: {kind!r}")
    return deserializer(d)


# =========================================================================
# Component Deserializers
# =========================================================================


def _dict_to_parameter(d: dict[str, Any]) -> Parameter:
    return Parameter(
        name=d.get("name"),
        type=_dict_to_type(d["type"]),
        default_value=d.get("default_value"),
    )


def _dict_to_base(d: dict[str, Any]) -> BaseSpecifier:
    return BaseSpecifier(
        name=d["name"],
        access=d.get("access", "public"),
        is_virtual=d.get("is_virtual", False),
    )


def _dict_to_field(d: dict[str, Any]) -> Field:
    return Field(
        name=d["name"],
        type=_dict_to_type(d["type"]),
        bit_width=d.get("bit_width"),
        anonymous_struct=(_dict_to_struct(d["anonymous_struct"]) if "anonymous_struct" in d else None),
        is_anonymous_transparent=d.get("is_anonymous_transparent", False),
        access=d.get("access"),
        is_static=d.get("is_static", False),
    )


def _dict_to_enum_value(d: dict[str, Any]) -> EnumValue:
    return EnumValue(
        name=d["name"],
        value=d.get("value"),
    )


def _dict_to_location(d: dict[str, Any]) -> SourceLocation:
    return SourceLocation(
        file=d["file"],
        line=d["line"],
        column=d.get("column"),
    )


# =========================================================================
# Declaration Deserializers
# =========================================================================


def _dict_to_struct(d: dict[str, Any]) -> Struct:
    return Struct(
        name=d.get("name"),
        fields=[_dict_to_field(f) for f in d.get("fields", [])],
        methods=([_dict_to_function(m) for m in d["methods"]] if "methods" in d else []),
        is_union=d.get("kind") == "union",
        is_cppclass=d.get("is_cppclass", False),
        is_typedef=d.get("is_typedef", False),
        is_packed=d.get("is_packed", False),
        namespace=d.get("namespace"),
        template_params=d.get("template_params", []),
        cpp_name=d.get("cpp_name"),
        notes=d.get("notes", []),
        inner_typedefs=d.get("inner_typedefs", {}),
        bases=[_dict_to_base(b) for b in d.get("bases", [])],
        is_abstract=d.get("is_abstract", False),
        constructors=([_dict_to_function(c) for c in d["constructors"]] if "constructors" in d else []),
        destructor=(_dict_to_function(d["destructor"]) if "destructor" in d else None),
        conversions=([_dict_to_function(c) for c in d["conversions"]] if "conversions" in d else []),
        vtable_entries=([_dict_to_function(v) for v in d["vtable_entries"]] if "vtable_entries" in d else []),
        attributes=d.get("attributes", []),
        is_deprecated=d.get("is_deprecated", False),
        alignment=d.get("alignment"),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_enum(d: dict[str, Any]) -> Enum:
    return Enum(
        name=d.get("name"),
        values=[_dict_to_enum_value(v) for v in d.get("values", [])],
        is_typedef=d.get("is_typedef", False),
        location=_dict_to_location(d["location"]) if "location" in d else None,
        underlying_type=d.get("underlying_type"),
        underlying_type_known=d.get("underlying_type_known", True),
    )


def _dict_to_function(d: dict[str, Any]) -> Function:
    return Function(
        name=d["name"],
        return_type=_dict_to_type(d["return_type"]),
        parameters=[_dict_to_parameter(p) for p in d.get("parameters", [])],
        is_variadic=d.get("is_variadic", False),
        calling_convention=d.get("calling_convention"),
        namespace=d.get("namespace"),
        template_params=d.get("template_params", []),
        is_static=d.get("is_static", False),
        is_const=d.get("is_const", False),
        is_virtual=d.get("is_virtual", False),
        is_pure_virtual=d.get("is_pure_virtual", False),
        is_explicit=d.get("is_explicit", False),
        access=d.get("access"),
        is_deleted=d.get("is_deleted", False),
        is_defaulted=d.get("is_defaulted", False),
        is_noexcept=d.get("is_noexcept", False),
        is_inline=d.get("is_inline", False),
        body=d.get("body"),
        attributes=d.get("attributes", []),
        is_deprecated=d.get("is_deprecated", False),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_typedef(d: dict[str, Any]) -> Typedef:
    return Typedef(
        name=d["name"],
        underlying_type=_dict_to_type(d["underlying_type"]),
        namespace=d.get("namespace"),
        attributes=d.get("attributes", []),
        is_deprecated=d.get("is_deprecated", False),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_variable(d: dict[str, Any]) -> Variable:
    return Variable(
        name=d["name"],
        type=_dict_to_type(d["type"]),
        namespace=d.get("namespace"),
        attributes=d.get("attributes", []),
        is_deprecated=d.get("is_deprecated", False),
        alignment=d.get("alignment"),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_constant(d: dict[str, Any]) -> Constant:
    return Constant(
        name=d["name"],
        value=d.get("value"),
        evaluated_value=d.get("evaluated_value"),
        raw_expression=d.get("raw_expression"),
        type=_dict_to_ctype(d["type"]) if "type" in d else None,
        is_macro=d.get("is_macro", False),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


_DECL_DESERIALIZERS: dict[str, Callable[[dict[str, Any]], Declaration]] = {
    "struct": _dict_to_struct,
    "union": _dict_to_struct,
    "enum": _dict_to_enum,
    "function": _dict_to_function,
    "typedef": _dict_to_typedef,
    "variable": _dict_to_variable,
    "constant": _dict_to_constant,
}


def _dict_to_decl(d: dict[str, Any]) -> Declaration:
    """Dispatch to the correct declaration deserializer based on 'kind'."""
    kind = d.get("kind")
    if kind is None:
        raise ValueError(f"Declaration dict missing 'kind': {d}")
    deserializer = _DECL_DESERIALIZERS.get(kind)
    if deserializer is None:
        raise ValueError(f"Unknown declaration kind: {kind!r}")
    return deserializer(d)


# =========================================================================
# Public API
# =========================================================================


def json_to_header(data: str | dict[str, Any]) -> Header:
    """Deserialize a JSON string or dict into a Header IR object.

    Accepts either a JSON string (parsed with json.loads) or the dict
    that json.loads would return. This is the inverse of
    headerkit.writers.json.header_to_json / header_to_json_dict.

    :param data: JSON string or dict from header_to_json / header_to_json_dict.
    :returns: Reconstructed Header IR.
    :raises ValueError: If the JSON structure is invalid or contains
        unknown declaration/type kinds.
    """
    if isinstance(data, str):
        d = json.loads(data)
    else:
        d = data

    if not isinstance(d, dict):
        raise ValueError(f"Expected dict, got {type(d).__name__}")

    return Header(
        path=d["path"],
        declarations=[_dict_to_decl(decl) for decl in d.get("declarations", [])],
        included_headers=set(d.get("included_headers", [])),
        language=d.get("language", "c"),
        classification=d.get("classification", "header"),
    )
