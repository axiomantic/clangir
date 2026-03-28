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
    )


def _dict_to_pointer(d: dict[str, Any]) -> Pointer:
    return Pointer(
        pointee=_dict_to_type(d["pointee"]),
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
    )


def _dict_to_field(d: dict[str, Any]) -> Field:
    return Field(
        name=d["name"],
        type=_dict_to_type(d["type"]),
        bit_width=d.get("bit_width"),
        anonymous_struct=(_dict_to_struct(d["anonymous_struct"]) if "anonymous_struct" in d else None),
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
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_enum(d: dict[str, Any]) -> Enum:
    return Enum(
        name=d.get("name"),
        values=[_dict_to_enum_value(v) for v in d.get("values", [])],
        is_typedef=d.get("is_typedef", False),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_function(d: dict[str, Any]) -> Function:
    return Function(
        name=d["name"],
        return_type=_dict_to_type(d["return_type"]),
        parameters=[_dict_to_parameter(p) for p in d.get("parameters", [])],
        is_variadic=d.get("is_variadic", False),
        calling_convention=d.get("calling_convention"),
        namespace=d.get("namespace"),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_typedef(d: dict[str, Any]) -> Typedef:
    return Typedef(
        name=d["name"],
        underlying_type=_dict_to_type(d["underlying_type"]),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_variable(d: dict[str, Any]) -> Variable:
    return Variable(
        name=d["name"],
        type=_dict_to_type(d["type"]),
        location=_dict_to_location(d["location"]) if "location" in d else None,
    )


def _dict_to_constant(d: dict[str, Any]) -> Constant:
    return Constant(
        name=d["name"],
        value=d.get("value"),
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
    )
