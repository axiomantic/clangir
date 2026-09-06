"""Generate Python ctypes binding modules from headerkit IR declarations.

This module converts headerkit IR (Intermediate Representation) objects into
Python source code that uses the ``ctypes`` standard library to define C type
bindings. The output is a runnable Python file containing struct/union classes,
enum constants, type aliases, callback types, and function prototype annotations.

The IR types come from ``headerkit.ir`` and represent parsed C headers.
"""

from __future__ import annotations

import ctypes
import math
import textwrap
from typing import ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    Field,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions, extract_function_names
from headerkit.writers.base import BaseWriter, WriterOption

# Maps C type names to their ctypes equivalents.
CTYPES_TYPE_MAP: dict[str, str] = {
    "void": "None",
    "char": "ctypes.c_char",
    "signed char": "ctypes.c_byte",
    "unsigned char": "ctypes.c_ubyte",
    "short": "ctypes.c_short",
    "unsigned short": "ctypes.c_ushort",
    "int": "ctypes.c_int",
    "unsigned int": "ctypes.c_uint",
    "long": "ctypes.c_long",
    "unsigned long": "ctypes.c_ulong",
    "long long": "ctypes.c_longlong",
    "unsigned long long": "ctypes.c_ulonglong",
    "float": "ctypes.c_float",
    "double": "ctypes.c_double",
    "long double": "ctypes.c_longdouble",
    "size_t": "ctypes.c_size_t",
    "ssize_t": "ctypes.c_ssize_t",
    "wchar_t": "ctypes.c_wchar",
    "_Bool": "ctypes.c_bool",
    "bool": "ctypes.c_bool",
    "int8_t": "ctypes.c_int8",
    "int16_t": "ctypes.c_int16",
    "int32_t": "ctypes.c_int32",
    "int64_t": "ctypes.c_int64",
    "uint8_t": "ctypes.c_uint8",
    "uint16_t": "ctypes.c_uint16",
    "uint32_t": "ctypes.c_uint32",
    "uint64_t": "ctypes.c_uint64",
}


def _is_anonymous_name(name: str | None) -> bool:
    """Check if a name is a synthesized anonymous name from libclang."""
    if name is None:
        return True
    return "(unnamed" in name or "(anonymous" in name


def _is_const_char_pointer(t: TypeExpr) -> bool:
    """Check if a type is ``const char *`` (maps to ``ctypes.c_char_p``)."""
    if isinstance(t, Pointer) and isinstance(t.pointee, CType):
        if t.pointee.name == "char" and "const" in t.pointee.qualifiers:
            return True
    return False


def _is_void_pointer(t: TypeExpr) -> bool:
    """Check if a type is ``void *`` (maps to ``ctypes.c_void_p``)."""
    if isinstance(t, Pointer) and isinstance(t.pointee, CType):
        if t.pointee.name == "void" and not t.pointee.qualifiers:
            return True
    return False


def _is_char_pointer(t: TypeExpr) -> bool:
    """Check if a type is ``char *`` without const (maps to ``ctypes.c_char_p``)."""
    if isinstance(t, Pointer) and isinstance(t.pointee, CType):
        if t.pointee.name == "char" and not t.pointee.qualifiers:
            return True
    return False


def type_to_ctypes(t: TypeExpr) -> str:
    """Convert a type expression to its ctypes string representation.

    Handles special cases like ``const char *`` -> ``ctypes.c_char_p``,
    ``void *`` -> ``ctypes.c_void_p``, and pointer/array composition.
    """
    # const char * -> c_char_p
    if _is_const_char_pointer(t):
        return "ctypes.c_char_p"
    # void * -> c_void_p
    if _is_void_pointer(t):
        return "ctypes.c_void_p"
    # char * -> c_char_p
    if _is_char_pointer(t):
        return "ctypes.c_char_p"

    if isinstance(t, CType):
        # Strip qualifiers for ctypes mapping (const int -> c_int)
        base_name = t.name
        # Check with qualifiers prepended for compound types like "unsigned int"
        if t.qualifiers:
            # Only use qualified form for type-level qualifiers like "unsigned"
            # not for cv-qualifiers like "const"
            non_cv = [q for q in t.qualifiers if q not in ("const", "volatile", "restrict")]
            if non_cv:
                qualified_name = " ".join(non_cv) + " " + t.name
                if qualified_name in CTYPES_TYPE_MAP:
                    return CTYPES_TYPE_MAP[qualified_name]
        if base_name in CTYPES_TYPE_MAP:
            return CTYPES_TYPE_MAP[base_name]
        # Unknown type: use it as-is (likely a user-defined struct/typedef)
        return base_name

    if isinstance(t, Pointer):
        if isinstance(t.pointee, FunctionPointer):
            return _function_pointer_to_ctypes(t.pointee)
        inner = type_to_ctypes(t.pointee)
        return f"ctypes.POINTER({inner})"

    if isinstance(t, Array):
        element = type_to_ctypes(t.element_type)
        if t.size is not None:
            return f"{element} * {t.size}"
        # Flexible array: use POINTER
        return f"ctypes.POINTER({element})"

    if isinstance(t, FunctionPointer):
        return _function_pointer_to_ctypes(t)

    return str(t)


def _function_pointer_to_ctypes(fp: FunctionPointer) -> str:
    """Convert a FunctionPointer to a ctypes.CFUNCTYPE expression."""
    ret = type_to_ctypes(fp.return_type)
    args = [type_to_ctypes(p.type) for p in fp.parameters]
    all_types = [ret] + args
    return f"ctypes.CFUNCTYPE({', '.join(all_types)})"


def _field_to_ctypes_tuple(f: Field) -> str:
    """Convert a Field to a ctypes _fields_ tuple string.

    Bitfields use the 3-tuple format: ``("name", type, bit_width)``.
    Array fields use: ``("name", type * size)``.
    Regular fields use: ``("name", type)``.
    """
    field_type = type_to_ctypes(f.type)
    if f.bit_width is not None:
        return f'("{f.name}", {field_type}, {f.bit_width})'
    return f'("{f.name}", {field_type})'


def _ctypes_scalar_bits(expr: str) -> tuple[int, int] | None:
    """Size and alignment, in bits, of the ctypes scalar named by ``expr``.

    ``expr`` is a rendered writer expression such as ``"ctypes.c_uint"``. The
    figures come from the ctypes runtime that will lay the generated class out,
    so this reads the answer rather than assuming an ABI. Anything that is not a
    plain ctypes scalar -- a user struct, an array, a function pointer -- yields
    None, which makes the running bit offset unknown.
    """
    if not expr.startswith("ctypes."):
        return None
    obj = getattr(ctypes, expr[len("ctypes.") :], None)
    if not isinstance(obj, type):
        return None
    try:
        return ctypes.sizeof(obj) * 8, ctypes.alignment(obj) * 8
    except TypeError:
        return None


def _round_up(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple


class _StructBody:
    """Accumulates the ctypes class body for one record.

    Tracks the running bit offset so that a zero-width bitfield (``int : 0;``)
    can be turned into the explicit padding that reaches the next storage-unit
    boundary. ctypes rejects a zero-width entry outright, so the boundary has to
    be reached by reserving the remaining bits of the current unit instead.
    """

    def __init__(self, is_union: bool) -> None:
        self.is_union = is_union
        self.nested: list[str] = []
        self.anonymous: list[str] = []
        self.entries: list[str] = []
        self.bit_pos: int | None = 0
        self.pad_index = 0
        self.padding_bits = 0
        self.has_member = False

    def _next_pad(self) -> str:
        name = f"_pad{self.pad_index}"
        self.pad_index += 1
        return name

    def _advance_bitfield(self, expr: str, width: int) -> None:
        if self.is_union:
            return
        info = _ctypes_scalar_bits(expr)
        if info is None or self.bit_pos is None:
            self.bit_pos = None
            return
        unit = info[0]
        if self.bit_pos % unit + width > unit:
            self.bit_pos = _round_up(self.bit_pos, unit)
        self.bit_pos += width

    def _advance_plain(self, expr: str) -> None:
        if self.is_union:
            return
        info = _ctypes_scalar_bits(expr)
        if info is None or self.bit_pos is None:
            self.bit_pos = None
            return
        size, align = info
        self.bit_pos = _round_up(self.bit_pos, align) + size

    def add_padding(self, f: Field) -> bool:
        """Reserve the bits of an unnamed bitfield. False if it cannot be placed."""
        expr = type_to_ctypes(f.type)
        width = f.bit_width or 0
        if width == 0:
            # ``int : 0`` reserves no bits; it moves the next member to a fresh
            # storage unit. Reaching that boundary needs the current offset.
            info = _ctypes_scalar_bits(expr)
            if info is None or self.bit_pos is None:
                return False
            unit = info[0]
            fill = (unit - self.bit_pos % unit) % unit
            if fill == 0:
                return True
            width = fill
        self.entries.append(f'("{self._next_pad()}", {expr}, {width})')
        self._advance_bitfield(expr, width)
        self.padding_bits += width
        return True

    def add_anonymous(self, f: Field, index: int) -> bool:
        """Emit a C11 transparent member as a nested class plus an _anonymous_ entry."""
        inner = f.anonymous_struct
        if inner is None:
            return False
        cls_name = f"_Anon{index}"
        field_name = f"_anon{index}"
        body = _record_body(inner, cls_name)
        if body is None:
            return False
        self.nested.extend(body)
        self.anonymous.append(field_name)
        self.entries.append(f'("{field_name}", {cls_name})')
        self.has_member = True
        # The nested record carries its own alignment, so the offset past it is
        # not derivable from the scalar table.
        self.bit_pos = None
        return True

    def add_member(self, f: Field) -> None:
        expr = type_to_ctypes(f.type)
        self.entries.append(_field_to_ctypes_tuple(f))
        self.has_member = True
        if f.bit_width is not None:
            self._advance_bitfield(expr, f.bit_width)
        else:
            self._advance_plain(expr)


def _record_body(decl: Struct, class_name: str) -> list[str] | None:
    """Render a ctypes class for ``decl``, or None when it cannot be represented."""
    base_class = "ctypes.Union" if decl.is_union else "ctypes.Structure"

    if not decl.fields:
        return [f"class {class_name}({base_class}):", "    pass"]

    body = _StructBody(decl.is_union)
    for index, f in enumerate(decl.fields):
        if f.is_padding:
            if not body.add_padding(f):
                return None
        elif f.anonymous_struct is not None and f.is_anonymous_transparent:
            if not body.add_anonymous(f, index):
                return None
        elif not f.name:
            return None
        else:
            body.add_member(f)

    if not body.entries:
        return [f"class {class_name}({base_class}):", "    pass"]

    lines = [f"class {class_name}({base_class}):"]

    if not body.has_member:
        # A record whose every entry is padding has no member to impose an
        # alignment, so C sizes it to just the bits declared and aligns it to 1.
        # A bitfield carrier would instead drag in its own type's alignment, so
        # the bits are reserved as a byte array.
        nbytes = math.ceil(body.padding_bits / 8)
        if nbytes == 0:
            return [f"class {class_name}({base_class}):", "    pass"]
        lines.append("    _fields_ = [")
        lines.append(f'        ("_pad0", ctypes.c_ubyte * {nbytes}),')
        lines.append("    ]")
        return lines

    if decl.is_packed:
        lines.append("    _pack_ = 1")

    for nested_line in body.nested:
        lines.append(f"    {nested_line}" if nested_line else "")

    if body.anonymous:
        joined = ", ".join(f'"{n}"' for n in body.anonymous)
        suffix = "," if len(body.anonymous) == 1 else ""
        lines.append(f"    _anonymous_ = ({joined}{suffix})")

    lines.append("    _fields_ = [")
    lines.extend(f"        {entry}," for entry in body.entries)
    lines.append("    ]")
    return lines


def _struct_to_ctypes(decl: Struct) -> str | None:
    """Convert a Struct/Union IR node to a ctypes class definition."""
    if decl.name is None or _is_anonymous_name(decl.name):
        return None

    lines = _record_body(decl, decl.name)
    if lines is None:
        return None
    return "\n".join(lines)


def _enum_to_ctypes(decl: Enum) -> str | None:
    """Convert an Enum IR node to module-level integer constants."""
    if not decl.values:
        return None

    lines = []
    comment_name = decl.name if decl.name and not _is_anonymous_name(decl.name) else "anonymous"
    lines.append(f"# enum {comment_name}")

    for v in decl.values:
        if v.value is not None:
            lines.append(f"{v.name} = {v.value}")
        else:
            lines.append(f"# {v.name} = <auto>")

    return "\n".join(lines)


def _constant_to_ctypes(decl: Constant) -> str | None:
    """Convert a Constant IR node to a Python constant assignment."""
    if decl.value is None:
        return None

    if isinstance(decl.value, int | float):
        return f"{decl.name} = {decl.value}"
    elif isinstance(decl.value, str):
        # String constants: use bytes literal
        # Value may already be quoted (e.g., '"hello"') or unquoted
        val = decl.value
        if val.startswith('"') and val.endswith('"'):
            # Strip surrounding quotes and make bytes
            inner = val[1:-1]
            return f'{decl.name} = b"{inner}"'
        return f'{decl.name} = b"{val}"'

    return None


def _function_to_ctypes(decl: Function, lib_name: str) -> str | None:
    """Convert a Function IR node to ctypes prototype annotations."""
    lines = []

    # Calling convention comment
    if decl.calling_convention:
        lines.append(f"# calling convention: {decl.calling_convention}")

    # argtypes
    if decl.parameters:
        arg_types = [type_to_ctypes(p.type) for p in decl.parameters]
        lines.append(f"{lib_name}.{decl.name}.argtypes = [{', '.join(arg_types)}]")
    else:
        lines.append(f"{lib_name}.{decl.name}.argtypes = []")

    # restype
    ret = type_to_ctypes(decl.return_type)
    lines.append(f"{lib_name}.{decl.name}.restype = {ret}")

    return "\n".join(lines)


def _typedef_to_ctypes(decl: Typedef) -> str | None:
    """Convert a Typedef IR node to a ctypes type alias."""
    underlying = decl.underlying_type

    # Function pointer typedef: Name = ctypes.CFUNCTYPE(ret, *args)
    if isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
        return f"{decl.name} = {_function_pointer_to_ctypes(underlying.pointee)}"

    if isinstance(underlying, FunctionPointer):
        return f"{decl.name} = {_function_pointer_to_ctypes(underlying)}"

    # Struct/union typedef alias: Name = OriginalName
    if isinstance(underlying, CType):
        name = underlying.name
        # Strip struct/union/enum prefix for the alias target
        for prefix in ("struct ", "union ", "enum "):
            if name.startswith(prefix):
                target = name[len(prefix) :]
                return f"{decl.name} = {target}"
        # Simple type alias: just a comment
        ctypes_type = type_to_ctypes(underlying)
        if ctypes_type in CTYPES_TYPE_MAP.values() or ctypes_type == "None":
            return f"# typedef {underlying} -> {decl.name}"
        # User-defined type alias
        return f"{decl.name} = {ctypes_type}"

    # Array typedef
    if isinstance(underlying, Array):
        element = type_to_ctypes(underlying.element_type)
        if underlying.size is not None:
            return f"{decl.name} = {element} * {underlying.size}"
        return f"{decl.name} = ctypes.POINTER({element})"

    # Pointer typedef
    if isinstance(underlying, Pointer):
        return f"{decl.name} = {type_to_ctypes(underlying)}"

    return f"# typedef {decl.name} (unsupported)"


def _variable_to_ctypes(decl: Variable, lib_name: str) -> str | None:
    """Convert a Variable IR node to a ctypes global variable annotation."""
    var_type = type_to_ctypes(decl.type)
    return f"# {lib_name}.{decl.name}: {var_type}"


def header_to_ctypes(header: Header, lib_name: str = "_lib") -> str:
    """Convert all declarations in a Header to a Python ctypes module string.

    :param header: Parsed header IR from headerkit.
    :param lib_name: Variable name for the loaded library object. Used in
        function prototype annotations (e.g., ``_lib.func.argtypes = [...]``).
    :returns: A string of Python source code defining ctypes bindings.
    """
    sections: dict[str, list[str]] = {
        "constants": [],
        "enums": [],
        "structs": [],
        "typedefs": [],
        "functions": [],
        "variables": [],
    }

    for decl in header.declarations:
        result: str | None = None
        section: str = ""

        if isinstance(decl, Constant):
            result = _constant_to_ctypes(decl)
            section = "constants"
        elif isinstance(decl, Enum):
            result = _enum_to_ctypes(decl)
            section = "enums"
        elif isinstance(decl, Struct):
            result = _struct_to_ctypes(decl)
            section = "structs"
        elif isinstance(decl, Typedef):
            result = _typedef_to_ctypes(decl)
            section = "typedefs"
        elif isinstance(decl, Function):
            result = _function_to_ctypes(decl, lib_name)
            section = "functions"
        elif isinstance(decl, Variable):
            result = _variable_to_ctypes(decl, lib_name)
            section = "variables"

        if result is not None and section:
            sections[section].append(result)

    # Build output
    output_lines: list[str] = []

    # Module docstring
    output_lines.append(f'"""ctypes bindings generated from {header.path}."""')
    output_lines.append("")

    # Imports
    output_lines.append("import ctypes")
    output_lines.append("import ctypes.util")
    output_lines.append("import sys")
    output_lines.append("")

    # Sections
    section_order = ["constants", "enums", "structs", "typedefs", "functions", "variables"]
    section_headers = {
        "constants": "Constants",
        "enums": "Enums",
        "structs": "Structures and Unions",
        "typedefs": "Typedefs",
        "functions": "Function Prototypes",
        "variables": "Global Variables",
    }

    for section_name in section_order:
        items = sections[section_name]
        if items:
            output_lines.append(f"# {'=' * 60}")
            output_lines.append(f"# {section_headers[section_name]}")
            output_lines.append(f"# {'=' * 60}")
            output_lines.append("")
            for item in items:
                output_lines.append(item)
                output_lines.append("")

    return "\n".join(output_lines)


class CtypesWriter(BaseWriter):
    """Writer that generates Python ctypes binding modules from headerkit IR.

    Options
    -------
    lib_name : str
        Variable name for the loaded library object. Defaults to ``"_lib"``.
        Controls the variable name used in function prototype annotations
        (e.g., ``_lib.func.argtypes = [...]``).

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("ctypes", lib_name="mylib")
        source = writer.write(header)

        # Or directly:
        from headerkit.writers.ctypes import CtypesWriter
        writer = CtypesWriter(lib_name="_lib")
        source = writer.write(header)
    """

    name: str = "ctypes"
    format_description: str = "Python ctypes bindings"
    default_output_pattern: str = "{dir}/{stem}_ctypes.py"
    default_extension: str = ".py"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project")
    #: This writer reconstructs record layout rather than deferring to a C
    #: compiler, so it is the one consumer that needs the padding entries.
    consumes_padding_fields: ClassVar[bool] = True
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
        WriterOption(
            name="lib_name",
            description="Variable name for loaded library instance",
            default="_lib",
            type=str,
        ),
    )

    def __init__(self, lib_name: str = "_lib") -> None:
        self._lib_name = lib_name

    def _render(self, unit: SourceUnit | Header) -> str:
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        return header_to_ctypes(header, lib_name=self._lib_name)

    def write(self, header: Header) -> str:
        """Convert header IR to Python ctypes binding source code."""
        return self._render(header)

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        bindings_code = self._render(unit)
        fn_names = extract_function_names(unit)

        pyproject = textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=61.0"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "{pkg}"
            version = "0.1.0"
            description = "Python ctypes bindings for {pkg}"
            requires-python = ">=3.9"

            [tool.setuptools.packages.find]
            where = ["src"]
        """)

        init_py = textwrap.dedent(f"""\
            \"\"\"{pkg} package initialization.\"\"\"
            from {pkg}._bindings import *

            __version__ = "0.1.0"
        """)

        files = [
            OutputFile(path="pyproject.toml", content=pyproject),
            OutputFile(path=f"src/{pkg}/__init__.py", content=init_py),
            OutputFile(path=f"src/{pkg}/_bindings.py", content=bindings_code),
        ]

        if test_type in ("both", "tripwire"):
            tw_fn_checks = (
                "\n".join(
                    f'    assert hasattr(_bindings, "{name}"), "Symbol {name} missing from ctypes bindings"'
                    for name in fn_names[:10]
                )
                or "    assert _bindings is not None"
            )
            tripwire = textwrap.dedent(f"""\
                import pytest
                from {pkg} import _bindings

                @pytest.mark.tripwire
                def test_ctypes_tripwire():
                    \"\"\"Tripwire: verify binary load and C symbols.\"\"\"
                {tw_fn_checks}
            """)
            files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

        if test_type in ("both", "unit"):
            unit_fn_checks = (
                "\n".join(
                    f'    assert hasattr(_bindings, "{name}"), "Expected declaration \'{name}\' in _bindings"'
                    for name in fn_names[:10]
                )
                if fn_names
                else "    assert inspect.ismodule(_bindings)"
            )
            unit_test = textwrap.dedent(f"""\
                import inspect
                from {pkg} import _bindings

                def test_{pkg}_declarations():
                    \"\"\"Verify generated bindings module exports declarations.\"\"\"
                    assert inspect.ismodule(_bindings)
                {unit_fn_checks}
            """)
            files.append(OutputFile(path="tests/test_bindings.py", content=unit_test))

        return ProjectLayout(files=files)

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Python comments."""
        return "# {line}"


# Uses bottom-of-module self-registration. See headerkit/writers/cffi.py
# for documentation of this managed circular import pattern.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "ctypes",
    CtypesWriter,
    description="Python ctypes bindings",
)
