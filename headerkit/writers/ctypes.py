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
import re
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass, field
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
from headerkit.workorder import build_work_order_files
from headerkit.writers.base import (
    DEDENT_BLOCK,
    BaseWriter,
    WriterOption,
    module_level_bindings,
    render_block_template,
)

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


#: Name of the host-ABI flag the generated module defines when it needs one.
_ABI_FLAG = "_HK_UNNAMED_BITFIELD_ALIGNS"

#: Names of the zero-length arrays that carry an alignment a respelled field
#: would otherwise have lost. A zero-length ctypes array occupies no bytes and
#: still imposes its element type's alignment on the record, identically on
#: every CPython measured (3.10 through 3.14) -- which is what lets the field
#: spellings be chosen for position alone.
_ALIGN_FIELD = "_hk_align"
_PAD_ALIGN_FIELD = "_hk_pad_align"

#: Name of the flag saying this host's ctypes already reproduces this host's C
#: compiler for bit-fields, so the declared types can be emitted as written.
_NATIVE_FLAG = "_HK_CTYPES_MATCHES_C_NATIVELY"

#: Definition emitted into a generated module that contains an all-padding
#: record. ``platform.machine`` is consulted at import time on purpose: ctypes
#: lays a record out for the ABI of the host running Python, which need not be
#: the host that generated the module.
ABI_ALIGNMENT_NOTE = """\
# Does a C unnamed bit-field contribute its declared type's alignment to the
# record that contains it? That is an ABI choice. Measured with compiled C
# probes: AAPCS64 (Linux aarch64, gcc 14.2 and clang 19.1) and Windows x86-64
# impose the alignment; x86-64 System V (Linux x86_64, gcc 14.2 and clang
# 19.1) and Darwin arm64 (Apple clang 21) do not. On Windows every toolchain
# on the runner -- MinGW cc, gcc, clang, and clang targeting MSVC -- agreed,
# so this is not a MinGW-versus-MSVC split. Other ABIs were not measured and
# are assumed to follow System V. Resolved on import because ctypes follows
# the ABI of the host running this module, which need not be the host that
# generated it.
_HK_UNNAMED_BITFIELD_ALIGNS = sys.platform.startswith("win") or (
    not sys.platform.startswith(("darwin", "ios"))
    and platform.machine().lower().startswith(("aarch64", "arm"))
)

# Windows is a third layout rule, not a second: MSVC opens a fresh storage
# unit whenever a bit-field's declared type differs from the unit in play, so
# `struct { unsigned char a; unsigned int : 8; }` is 8 bytes there against 4
# under AAPCS64 and 2 under System V. ctypes implements that same MSVC
# algorithm on Windows, so there the declared types can be written out as they
# stand and ctypes reproduces the C compiler on its own. Everywhere else its
# engine changed in 3.14, so the spellings below are chosen for position and
# the alignment is supplied separately.
_HK_CTYPES_MATCHES_C_NATIVELY = sys.platform.startswith("win")"""


#: The ctypes spelling a C enum carries at the ABI boundary. C leaves the
#: underlying type implementation-defined but requires it to represent every
#: enumerator; every ABI headerkit targets uses ``int`` for an enum whose
#: enumerators fit in one.
ENUM_CTYPE = "ctypes.c_int"


@dataclass(frozen=True)
class _TypeTable:
    """Header-local spellings that resolve to something the module actually binds.

    A C type name can reach this writer in several spellings for one type, and
    which one arrives depends on the backend and on how the header wrote it.
    Both kinds of tag-spelled name are unusable as Python: ``enum E`` and
    ``struct Inner`` are two words. Neither can be repaired by a local rewrite,
    because whether a name resolves -- and to *what* -- is a property of the
    whole header, so it is answered once per header and carried in here.

    :param enums: Spellings that resolve to an integer ctypes type, mapped to
        that type. Only enums whose width this writer can establish are listed,
        and the type is per-enum rather than a constant: a declared underlying
        type decides it. See :func:`_enum_type_names`.
    :param records: Spellings that resolve to an emitted ctypes class, mapped to
        that class's name. Unlike an enum there is no default to fall back on --
        the answer is the class or nothing -- so a record this header does not
        declare is simply absent and keeps its C spelling.
    :param record_classes: The class name each record is *emitted* under, keyed
        by its qualified C++ name. Usually the tag itself; a record whose tag is
        contested gets a distinct name instead, so that both meanings of the
        contested spelling survive. See :func:`_record_type_names`.
    :param contested_records: Tags an ordinary identifier in this header also
        binds, mapped to the class the record was emitted under. Reached only
        through ``CType.is_elaborated``: the bare spelling of such a tag means
        the *other* declaration, so the two are told apart by how the use site
        was written rather than by the name, which is identical for both.
    :param scalars: Typedef names that resolve to a ctypes scalar, mapped to it.
        ``typedef unsigned char Level;`` renders as a comment and so binds no
        ``Level`` at all, which made every member declared ``Level v;`` a
        ``NameError`` -- with or without any collision. Resolving the name where
        it is *used* fixes that without inventing a module-level binding whose
        section order would have to be reasoned about.
    """

    enums: Mapping[str, str] = field(default_factory=dict)
    records: Mapping[str, str] = field(default_factory=dict)
    record_classes: Mapping[str, str] = field(default_factory=dict)
    scalars: Mapping[str, str] = field(default_factory=dict)
    contested_records: Mapping[str, str] = field(default_factory=dict)


#: The table for a call with no header context: nothing resolves, every name is
#: left exactly as the C spelling, which is the behaviour a lone type expression
#: had before any of this existed.
_EMPTY_TYPES = _TypeTable()


def _module_docstring(path: object) -> str:
    """The generated module's docstring, with ``path`` safe to embed in it.

    A header path goes into the source verbatim, and on Windows it is full of
    backslashes: ``C:\\Users\\runner\\...`` puts ``\\U`` in a string literal,
    which Python reads as the start of a 32-bit unicode escape and rejects
    before anything in the module runs. The failure is a ``SyntaxError`` on
    line 1 pointing at the docstring, so nothing about it names the real cause,
    and it fires for any path containing ``\\U``, ``\\x``, ``\\N`` or a stray
    quote -- which is to say, on Windows, most absolute paths.

    Escaping the backslashes and any embedded triple quote keeps the docstring
    readable while making it a valid literal whatever the path contains.
    """
    text = str(path).replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""ctypes bindings generated from {text}."""'


def _library_loader(library: str, lib_name: str) -> str:
    """Return the source that binds ``lib_name`` to the native library.

    A generated module annotates ``lib_name.func.argtypes``, so a module that
    does not define ``lib_name`` cannot be imported at all. The loader raises
    rather than degrading to ``None`` on purpose: ``AGENTS.md`` §1 requires a
    tripwire to fail when the native binary is absent, and the tripwire imports
    this module, so the absence has to be fatal here.

    :param library: Base name of the native library, without a ``lib`` prefix
        or a platform suffix.
    :param lib_name: Name to bind the loaded library object to.
    """
    env_var = f"{re.sub(r'[^A-Za-z0-9]', '_', library).upper()}_LIBRARY"
    return textwrap.dedent(f'''\
        #: Base name of the native library these bindings resolve symbols from.
        _LIBRARY_NAME = {library!r}

        #: Environment variable naming an explicit path to that library, for a
        #: build tree or a wheel that ships the binary alongside this module.
        _LIBRARY_PATH_ENV = "{env_var}"


        def _load_library() -> ctypes.CDLL:
            """Locate and load the native library backing these bindings.

            :raises OSError: if the library cannot be found or cannot be loaded.
                Failing here is deliberate: a module that imports without its
                binary present would let a tripwire pass with nothing behind it.
            """
            override = os.environ.get(_LIBRARY_PATH_ENV)
            if override:
                return ctypes.CDLL(override)
            resolved = ctypes.util.find_library(_LIBRARY_NAME)
            if resolved is None:
                raise OSError(
                    f"cannot locate the native library {{_LIBRARY_NAME!r}}; "
                    f"install it or set {{_LIBRARY_PATH_ENV}} to its path"
                )
            return ctypes.CDLL(resolved)


        {lib_name} = _load_library()''')


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


def type_to_ctypes(t: TypeExpr, types: _TypeTable = _EMPTY_TYPES) -> str:
    """Convert a type expression to its ctypes string representation.

    Handles special cases like ``const char *`` -> ``ctypes.c_char_p``,
    ``void *`` -> ``ctypes.c_void_p``, and pointer/array composition.

    :param types: Every spelling this header uses for an enum type, from
        :func:`_enum_type_names`. An enum binds no ctypes class, so a use of one
        has to resolve to :data:`ENUM_CTYPE` here rather than to the C name. The
        tag spelling ``enum E`` is not valid Python at all, and the alias
        spelling ``E`` names something the module may not have assigned yet --
        a ``Typedef`` renders into a section emitted *after* the records that
        use it. Resolving at the use site is what makes the result independent
        of both the spelling the backend chose and the order of the sections.
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

        # The header's own tables come *after* the builtin map, never before it.
        # A tag and an ordinary identifier are separate namespaces in C, so
        # ``struct int8_t { ... };`` may sit beside the standard ``int8_t``, and
        # a bare ``int8_t`` still has to mean the one-byte integer. Consulting
        # the tables first handed it the eight-byte record instead -- silently,
        # since the module imports either way.
        # A tag an ordinary identifier also binds: ``struct Gauge { ... };``
        # beside ``typedef unsigned char Gauge;``. The name alone cannot say
        # which is meant -- C keeps them in separate namespaces and both are
        # legal -- so the spelling at the *use site* decides, and an unrecorded
        # spelling is refused rather than guessed. Getting this wrong is a
        # one-byte scalar where C laid out an eight-byte record, imported
        # cleanly, which is why it does not fall back to either meaning.
        contested = types.contested_records.get(base_name)
        if contested is not None:
            if t.is_elaborated is True:
                return contested
            if t.is_elaborated is not False:
                return base_name

        enum_ctype = types.enums.get(base_name)
        if enum_ctype is not None:
            return enum_ctype
        # A typedef onto a builtin scalar binds no name of its own, so the use
        # site is the only place it can be resolved; see _scalar_typedef_names.
        scalar = types.scalars.get(base_name)
        if scalar is not None:
            return scalar
        # A tag-spelled record is the same defect as a tag-spelled enum -- two
        # words where Python needs one -- but it has a real answer rather than a
        # default: the class this writer emits for that record. A record the
        # header does not declare is absent from the table and keeps its C
        # spelling, which does not parse, rather than being resolved to a guess.
        record = types.records.get(base_name)
        if record is not None:
            return record

        # Unknown type: use it as-is (likely a user-defined struct/typedef)
        return base_name

    if isinstance(t, Pointer):
        if isinstance(t.pointee, FunctionPointer):
            return _function_pointer_to_ctypes(t.pointee, types)
        inner = type_to_ctypes(t.pointee, types)
        return f"ctypes.POINTER({inner})"

    if isinstance(t, Array):
        element = type_to_ctypes(t.element_type, types)
        if t.size is not None:
            return f"{element} * {t.size}"
        # Flexible array: use POINTER
        return f"ctypes.POINTER({element})"

    if isinstance(t, FunctionPointer):
        return _function_pointer_to_ctypes(t, types)

    return str(t)


def _function_pointer_to_ctypes(fp: FunctionPointer, types: _TypeTable) -> str:
    """Convert a FunctionPointer to a ctypes.CFUNCTYPE expression."""
    ret = type_to_ctypes(fp.return_type, types)
    args = [type_to_ctypes(p.type, types) for p in fp.parameters]
    all_types = [ret] + args
    return f"ctypes.CFUNCTYPE({', '.join(all_types)})"


def _field_to_ctypes_tuple(f: Field, types: _TypeTable) -> str:
    """Convert a Field to a ctypes _fields_ tuple string.

    Bitfields use the 3-tuple format: ``("name", type, bit_width)``.
    Array fields use: ``("name", type * size)``.
    Regular fields use: ``("name", type)``.
    """
    field_type = type_to_ctypes(f.type, types)
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


#: ctypes scalars that hold an unsigned bit-field, and so narrow to
#: ``c_ubyte`` rather than ``c_byte``. Signedness is the one property of a
#: bit-field's declared type that narrowing must preserve: it decides whether
#: ctypes sign-extends the stored value on read.
_UNSIGNED_CTYPES: frozenset[str] = frozenset(
    {
        "ctypes.c_bool",
        "ctypes.c_ubyte",
        "ctypes.c_ushort",
        "ctypes.c_uint",
        "ctypes.c_ulong",
        "ctypes.c_ulonglong",
        "ctypes.c_size_t",
        "ctypes.c_uint8",
        "ctypes.c_uint16",
        "ctypes.c_uint32",
        "ctypes.c_uint64",
    }
)


def _narrow_carrier(expr: str) -> str | None:
    """The one-byte carrier that stores the same values as ``expr``."""
    if not expr.startswith("ctypes.") or _ctypes_scalar_bits(expr) is None:
        return None
    return "ctypes.c_ubyte" if expr in _UNSIGNED_CTYPES else "ctypes.c_byte"


def _round_up(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple


def _byte_split(start: int, end: int) -> list[int]:
    """Bit widths that fill ``[start, end)`` with no width crossing a byte.

    Each width is what remains of the current byte, capped by what is left to
    fill. A ``c_ubyte`` bitfield of such a width always lands in the byte the
    running offset is already in, so ctypes never inserts a skip and the chain
    ends at exactly ``end``. One wide entry cannot promise that: ctypes moves a
    bitfield to the next storage unit whenever it would straddle one.
    """
    widths: list[int] = []
    pos = start
    while pos < end:
        width = min(8 - pos % 8, end - pos)
        widths.append(width)
        pos += width
    return widths


class _StructBody:
    """Accumulates the ctypes class body for one record.

    Tracks the running bit offset so that a zero-width bitfield (``int : 0;``)
    can be turned into the explicit padding that reaches the next storage-unit
    boundary. ctypes rejects a zero-width entry outright, so the boundary has to
    be reached by reserving the remaining bits of the current unit instead.
    """

    def __init__(self, is_union: bool, types: _TypeTable) -> None:
        self.is_union = is_union
        self.types = types
        self.nested: list[str] = []
        self.anonymous: list[str] = []
        self.entries: list[str] = []
        #: The same members with every ``: 0`` respelled as a zero-length
        #: array. Emitted where ctypes already reproduces the host's C
        #: compiler; see ``add_padding``.
        self.native_entries: list[str] = []
        #: The same members, with every padding carrier respelled so it cannot
        #: raise the record's alignment. Identical to ``entries`` until a
        #: padding field whose declared type aligns wider than a byte arrives.
        self.flat_entries: list[str] = []
        self.bit_pos: int | None = 0
        self.pad_index = 0
        self.flat_pad_index = 0
        self.padding_bits = 0
        self.has_member = False
        #: Widest alignment, in bits, any padding carrier would impose.
        self.pad_align_bits = 0
        #: The widest-aligning padding carrier, as ``(expression, alignment
        #: bits, storage-unit bits)`` -- ordered so the widest compares
        #: greatest. An all-padding record is respelled entirely in this one
        #: type; see ``aligned_padding_entries``.
        self.pad_carrier: tuple[str, int, int] | None = None
        #: Widest alignment, in bits, that narrowing a named bit-field's
        #: carrier gave up, with the expression that carried it. A zero-length
        #: array of that type puts the alignment back.
        self.narrowed_carrier: tuple[str, int] | None = None
        #: Widest alignment, in bits, the real members impose on their own.
        #: None once a member arrives whose alignment the writer cannot read,
        #: which forces the conservative answer below.
        self.member_align_bits: int | None = 0
        #: Why no alignment-neutral carrier could be built, if that happened.
        self.diagnostic: str | None = None

    @property
    def abi_dependent(self) -> bool:
        """Whether the padding carrier decides the record's alignment.

        Only then do the two ABIs disagree and only then is a branch worth
        emitting. A carrier no wider than a byte can never raise anything, and
        a carrier no wider than what the real members already impose is
        invisible -- ``struct { unsigned a : 3; unsigned : 0; unsigned b : 5; }``
        is aligned to 4 by ``a`` whatever the padding does.
        """
        if self.pad_align_bits <= 8:
            return False
        return self.member_align_bits is None or self.pad_align_bits > self.member_align_bits

    def _note_member_align(self, expr: str) -> None:
        info = _ctypes_scalar_bits(expr)
        if info is None or self.member_align_bits is None:
            self.member_align_bits = None
            return
        self.member_align_bits = max(self.member_align_bits, info[1])

    def _next_pad(self) -> str:
        name = f"_pad{self.pad_index}"
        self.pad_index += 1
        return name

    def _next_flat_pad(self) -> str:
        name = f"_pad{self.flat_pad_index}"
        self.flat_pad_index += 1
        return name

    def _add_both(self, entry: str) -> None:
        self.entries.append(entry)
        self.native_entries.append(entry)
        self.flat_entries.append(entry)

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
        expr = type_to_ctypes(f.type, self.types)
        width = f.bit_width or 0
        is_zero_width = width == 0
        info = _ctypes_scalar_bits(expr)
        if width == 0:
            # ``int : 0`` reserves no bits; it moves the next member to a fresh
            # storage unit. Reaching that boundary needs the current offset.
            if info is None or self.bit_pos is None:
                return False
            unit = info[0]
            fill = (unit - self.bit_pos % unit) % unit
            if fill == 0:
                return True
            width = fill

        if info is not None:
            unit_bits, align_bits = info
            if self.pad_carrier is None or (align_bits, unit_bits) > self.pad_carrier[1:]:
                self.pad_carrier = (expr, align_bits, unit_bits)

        start = self.bit_pos
        pad_name = self._next_pad()
        self.entries.append(f'("{pad_name}", {expr}, {width})')
        # ``: 0`` reserves nothing in C; it only ends the current storage unit
        # and moves the next member to a fresh one. Reaching that boundary with
        # a *fill bit-field* is faithful under the GCC rule, where the fill
        # sits in the unit that is being closed. It is not faithful under the
        # MSVC rule, which gives the fill a unit of its own and then gives the
        # next member yet another: measured on Windows, ``struct { unsigned
        # char a : 3; unsigned int : 0; unsigned char b : 5; }`` is 8 bytes and
        # the fill spelling produced 12. A zero-length array closes the unit
        # and imposes the boundary's alignment while occupying nothing, which
        # is what ``: 0`` actually means.
        if is_zero_width:
            self.native_entries.append(f'("{pad_name}", {expr} * 0)')
        else:
            self.native_entries.append(f'("{pad_name}", {expr}, {width})')
        self._advance_bitfield(expr, width)
        self.padding_bits += width

        # A named bitfield is the only way ctypes can reserve bits, and a named
        # bitfield of ``unsigned int`` aligns its record to 4 -- which C does
        # only under AAPCS64 (see ABI_ALIGNMENT_NOTE). Everywhere else the bits
        # must be reserved without the alignment, so the same span is respelled
        # as byte-granular ``c_ubyte`` bitfields. They occupy the identical bit
        # range, including any storage unit the wide carrier skipped past, so
        # every following member keeps its offset while the record stays
        # byte-alignable.
        if info is None or info[1] <= 8:
            self.flat_entries.append(f'("{self._next_flat_pad()}", {expr}, {width})')
            return True

        self.pad_align_bits = max(self.pad_align_bits, info[1])
        if self.is_union:
            # Union members all start at bit 0, so only the span matters.
            span = _byte_split(0, width)
        elif start is None or self.bit_pos is None:
            self.diagnostic = (
                f"an unnamed bitfield of {width} bits follows a member whose bit offset "
                "the writer cannot track, so the reserved bits cannot be respelled "
                "without the alignment their declared type carries"
            )
            return True
        else:
            span = _byte_split(start, self.bit_pos)
        self.flat_entries.extend(f'("{self._next_flat_pad()}", ctypes.c_ubyte, {w})' for w in span)
        return True

    def aligned_padding_entries(self) -> list[str]:
        """The all-padding carrier, respelled as one chain in the widest type.

        An all-padding record has no addressable member, so only its ``sizeof``
        and alignment are observable; the individual carriers' bit positions
        are not. That freedom is what makes this respelling safe -- and it is
        needed because CPython before 3.14 derives a record's alignment only
        from bit-fields that open a storage unit. ``struct { unsigned char : 4;
        unsigned int : 4; }`` therefore aligned to 1 on 3.10 through 3.13 and
        to 4 on 3.14, while C (AAPCS64) says 4 on every one of them. Laying the
        whole span in the widest carrier makes that carrier open the first unit
        on every Python, so one spelling is right across all of them.

        The chain reproduces C's size as well as its alignment: ``n`` bits in a
        carrier of ``u``-bit units occupy ``ceil(n / u)`` units, which is
        ``round_up(ceil(n / 8), u / 8)`` bytes -- C's rule for a record whose
        alignment is ``u / 8``.
        """
        if self.pad_carrier is None:
            return []
        expr, _align_bits, unit_bits = self.pad_carrier
        total = self.total_padding_bits
        entries = []
        index = 0
        while index * unit_bits < total:
            width = min(unit_bits, total - index * unit_bits)
            entries.append(f'("_pad{index}", {expr}, {width})')
            index += 1
        return entries

    @property
    def total_padding_bits(self) -> int:
        """Bits the padding spans, counting units a carrier skipped past.

        ``bit_pos`` accounts for a carrier that could not fit in the storage
        unit it started in and so moved to the next one; the running sum of
        declared widths does not. The sum is the fallback for the case where
        the offset became untrackable.
        """
        return self.bit_pos if self.bit_pos is not None else self.padding_bits

    def add_anonymous(self, f: Field, index: int) -> bool:
        """Emit a C11 transparent member as a nested class plus an _anonymous_ entry."""
        inner = f.anonymous_struct
        if inner is None:
            return False
        cls_name = f"_Anon{index}"
        field_name = f"_anon{index}"
        body = _record_body(inner, cls_name, self.types)
        if body is None:
            return False
        self.nested.extend(body)
        self.anonymous.append(field_name)
        self._add_both(f'("{field_name}", {cls_name})')
        self.has_member = True
        self.member_align_bits = None
        # The nested record carries its own alignment, so the offset past it is
        # not derivable from the scalar table.
        self.bit_pos = None
        return True

    def _portable_bitfield_carrier(self, expr: str, width: int) -> str:
        """``expr`` narrowed to one byte where that makes the field portable.

        CPython before 3.14 opens a fresh storage unit whenever a bit-field's
        declared type differs in size from the unit it would otherwise land
        in, and aligns that unit to the new type. C does not: it keeps packing
        as long as the field fits the unit it is already in. ``struct {
        unsigned char a; unsigned short b : 5; }`` therefore puts ``b`` at bit
        8 in C and on Python 3.14, and at bit 16 on 3.10 through 3.13.

        A carrier of one byte removes the disagreement, because then no
        declared type ever differs from the unit in play. Narrowing is sound
        only when the field already fits in the byte the running offset is in
        -- otherwise C itself would move the field on, and the byte carrier
        would not follow. The declared type is kept in that case, which is the
        faithful spelling on 3.14 and under the MSVC algorithm ctypes uses on
        Windows.
        """
        info = _ctypes_scalar_bits(expr)
        if info is None or self.is_union or self.bit_pos is None:
            return expr
        _unit_bits, align_bits = info
        if align_bits <= 8 or self.bit_pos % 8 + width > 8:
            return expr
        narrow = _narrow_carrier(expr)
        if narrow is None:
            return expr
        if self.narrowed_carrier is None or align_bits > self.narrowed_carrier[1]:
            self.narrowed_carrier = (expr, align_bits)
        return narrow

    def add_member(self, f: Field) -> None:
        expr = type_to_ctypes(f.type, self.types)
        self.has_member = True
        self._note_member_align(expr)
        if f.bit_width is None:
            self._add_both(_field_to_ctypes_tuple(f, self.types))
            self._advance_plain(expr)
            return
        # Only the byte-granular spelling narrows. ``entries`` stays faithful
        # to the declared types, and mixing a narrowed member into it would
        # change the layout it exists to reproduce.
        self.entries.append(_field_to_ctypes_tuple(f, self.types))
        self.native_entries.append(_field_to_ctypes_tuple(f, self.types))
        carrier = self._portable_bitfield_carrier(expr, f.bit_width)
        self.flat_entries.append(f'("{f.name}", {carrier}, {f.bit_width})')
        # The running offset tracks C, so it advances by the *declared* type.
        self._advance_bitfield(expr, f.bit_width)


def _alignment_entries(body: _StructBody, *, include_padding: bool) -> list[str]:
    """Zero-length arrays restoring alignments the byte-granular spelling gave up.

    Only the ``flat_entries`` spelling gives any alignment up, so this belongs
    to that spelling alone; the faithful ``entries`` list carries its
    alignments in the declared types themselves.
    """
    entries = []
    if body.narrowed_carrier is not None:
        entries.append(f'("{_ALIGN_FIELD}", {body.narrowed_carrier[0]} * 0)')
    if include_padding and body.pad_carrier is not None:
        entries.append(f'("{_PAD_ALIGN_FIELD}", {body.pad_carrier[0]} * 0)')
    return entries


def _record_body(decl: Struct, class_name: str, types: _TypeTable) -> list[str] | None:
    """Render a ctypes class for ``decl``, or None when it cannot be represented."""
    base_class = "ctypes.Union" if decl.is_union else "ctypes.Structure"

    if not decl.fields:
        return [f"class {class_name}({base_class}):", "    pass"]

    body = _StructBody(decl.is_union, types)
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
        # Whether an unnamed bit-field contributes its declared type's alignment
        # to the enclosing record is an ABI choice, not a universal rule. It is
        # imposed under AAPCS64 and not under x86-64 System V or Darwin (see
        # ABI_ALIGNMENT_NOTE). With no member to pin the record down, that
        # choice is the whole layout, and ctypes cannot express an unnamed
        # bit-field to let its own engine decide. So both carriers are emitted
        # and the host resolves the branch when the module is imported: a named
        # bit-field of the same storage type reproduces the imposed alignment,
        # and a byte array reserves the same bits while staying byte-aligned.
        nbytes = math.ceil(body.total_padding_bits / 8)
        aligned_entries = body.aligned_padding_entries()
        if nbytes == 0 or not aligned_entries:
            return [f"class {class_name}({base_class}):", "    pass"]
        lines.append(f"    if {_NATIVE_FLAG}:")
        lines.append("        _fields_ = [")
        lines.extend(f"            {entry}," for entry in body.native_entries)
        lines.append("        ]")
        lines.append(f"    elif {_ABI_FLAG}:")
        lines.append("        _fields_ = [")
        lines.extend(f"            {entry}," for entry in aligned_entries)
        lines.append("        ]")
        lines.append("    else:")
        lines.append("        _fields_ = [")
        lines.append(f'            ("_pad0", ctypes.c_ubyte * {nbytes}),')
        lines.append("        ]")
        return lines

    if decl.is_packed:
        lines.append("    _pack_ = 1")

    for nested_line in body.nested:
        lines.append(f"    {nested_line}" if nested_line else "")

    if body.anonymous:
        joined = ", ".join(f'"{n}"' for n in body.anonymous)
        suffix = "," if len(body.anonymous) == 1 else ""
        lines.append(f"    _anonymous_ = ({joined}{suffix})")

    # ``_pack_ = 1`` already pins the record to byte alignment, so the wide
    # carrier cannot raise it and the two spellings agree. Branching would only
    # duplicate the field list.
    if not body.abi_dependent or decl.is_packed:
        lines.append("    _fields_ = [")
        lines.extend(f"        {entry}," for entry in body.entries)
        lines.append("    ]")
        return lines

    if body.diagnostic is not None:
        lines.append(f"    # HEADERKIT: {class_name} may be over-aligned where C does not")
        lines.append("    # align it, because " + body.diagnostic + ".")
        lines.append("    # Verify this record against your C compiler before relying on it.")
        lines.append("    _fields_ = [")
        lines.extend(f"        {entry}," for entry in body.entries)
        lines.append("    ]")
        return lines

    # Both branches reserve the padding with byte-granular carriers, which is
    # what makes one spelling correct everywhere: a chain of one-byte
    # bit-fields lays out identically under every ctypes engine -- CPython
    # before 3.14, CPython 3.14 and later, and the MSVC algorithm ctypes uses
    # on Windows -- because the three disagree only about what to do when a
    # bit-field's declared type differs from the unit it would land in, and
    # with a single one-byte carrier that case never arises. The branches
    # therefore differ only in alignment, which a zero-length array of the
    # padding's declared type supplies without occupying a byte. That
    # separation is required, not stylistic: before 3.14 ctypes derives a
    # record's alignment only from bit-fields that open a storage unit, so a
    # wide carrier that lands mid-unit raises nothing.
    lines.append(f"    if {_NATIVE_FLAG}:")
    lines.append("        _fields_ = [")
    lines.extend(f"            {entry}," for entry in body.native_entries)
    lines.append("        ]")
    lines.append(f"    elif {_ABI_FLAG}:")
    lines.append("        _fields_ = [")
    lines.extend(f"            {entry}," for entry in _alignment_entries(body, include_padding=True))
    lines.extend(f"            {entry}," for entry in body.flat_entries)
    lines.append("        ]")
    lines.append("    else:")
    lines.append("        _fields_ = [")
    lines.extend(f"            {entry}," for entry in _alignment_entries(body, include_padding=False))
    lines.extend(f"            {entry}," for entry in body.flat_entries)
    lines.append("        ]")
    return lines


def _struct_to_ctypes(decl: Struct, types: _TypeTable) -> str | None:
    """Convert a Struct/Union IR node to a ctypes class definition."""
    if decl.name is None or _is_anonymous_name(decl.name):
        return None

    # Usually the tag; a contested tag is emitted under a distinct name so that
    # the other meaning of the spelling keeps it. See ``_record_type_names``.
    class_name = types.record_classes.get(_record_qualified_name(decl), decl.name)
    lines = _record_body(decl, class_name, types)
    if lines is None:
        return None
    return "\n".join(lines)


def _enum_needs_alias(decl: Enum, typedef_names: frozenset[str]) -> bool:
    """Does this enum have to bind its own name, because no Typedef will?

    ``typedef enum { ... } Flags;`` gives the C program a type named ``Flags``,
    but an enum reaches this writer as loose integer constants and binds no name
    of its own. libclang follows the definition with a ``Typedef`` that supplies
    the alias; tree-sitter folds the alias into the ``Enum`` and emits no
    ``Typedef``, so under that backend the generated module had no ``Flags`` at
    all. Emitting the alias here, and only when no ``Typedef`` already carries
    it, makes the two backends produce the same module.

    An enum this writer cannot size binds nothing, because the alias it would
    bind is ``ENUM_CTYPE`` and that is the wrong width. Emitting it anyway is
    worse than emitting nothing: the alias goes into the ``enums`` section,
    which precedes ``structs``, so a member typed by it would *resolve* to a
    four-byte integer and the package would import and compute wrong answers.
    With no alias the member's name is unbound and the import fails loudly.
    """
    if _enum_ctype(decl) is None:
        return False
    return bool(decl.is_typedef and decl.name and not _is_anonymous_name(decl.name) and decl.name not in typedef_names)


def _enum_to_ctypes(decl: Enum, typedef_names: frozenset[str] = frozenset()) -> str | None:
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

    if _enum_needs_alias(decl, typedef_names):
        # The alias names the same integer the members do, which for an enum
        # with a declared underlying type is not ``ENUM_CTYPE``.
        lines.append(f"{decl.name} = {_enum_ctype(decl)}")

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


def _function_to_ctypes(decl: Function, lib_name: str, types: _TypeTable) -> str | None:
    """Convert a Function IR node to ctypes prototype annotations."""
    lines = []

    # Calling convention comment
    if decl.calling_convention:
        lines.append(f"# calling convention: {decl.calling_convention}")

    # argtypes
    if decl.parameters:
        arg_types = [type_to_ctypes(p.type, types) for p in decl.parameters]
        lines.append(f"{lib_name}.{decl.name}.argtypes = [{', '.join(arg_types)}]")
    else:
        lines.append(f"{lib_name}.{decl.name}.argtypes = []")

    # restype
    ret = type_to_ctypes(decl.return_type, types)
    lines.append(f"{lib_name}.{decl.name}.restype = {ret}")

    return "\n".join(lines)


def _typedef_to_ctypes(decl: Typedef, types: _TypeTable) -> str | None:
    """Convert a Typedef IR node to a ctypes type alias."""
    underlying = decl.underlying_type

    # Function pointer typedef: Name = ctypes.CFUNCTYPE(ret, *args)
    if isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
        return f"{decl.name} = {_function_pointer_to_ctypes(underlying.pointee, types)}"

    if isinstance(underlying, FunctionPointer):
        return f"{decl.name} = {_function_pointer_to_ctypes(underlying, types)}"

    # Struct/union typedef alias: Name = OriginalName
    if isinstance(underlying, CType):
        name = underlying.name
        # An enum reaches this writer as loose integer constants, never as a
        # binding of its own, so aliasing its tag emits ``Name = Name`` -- a
        # self-reference that raises NameError on the first import of the
        # generated module. At the ABI boundary a C enum is an int, and that is
        # what the alias has to name.
        # ``types.enums`` carries the tag spelling, the bare spelling and every
        # alias chained onto one, so this arm also covers ``typedef E1 E2``,
        # which would otherwise fall through to the simple-alias branch below,
        # render as a comment and bind no ``E2`` at all.
        enum_ctype = types.enums.get(name)
        if enum_ctype is not None:
            return f"{decl.name} = {enum_ctype}"
        if name.startswith("enum "):
            # A tag spelling absent from ``types.enums`` is an enum this writer
            # cannot size. An alias to it would itself be the wrong width, and
            # a name that is never bound fails loudly where it is used.
            return f"# typedef {underlying} -> {decl.name} (enum of unprovable width)"
        # Strip struct/union prefix for the alias target
        for prefix in ("struct ", "union "):
            if name.startswith(prefix):
                target = name[len(prefix) :]
                return f"{decl.name} = {target}"
        # Simple type alias: just a comment
        ctypes_type = type_to_ctypes(underlying, types)
        if ctypes_type in CTYPES_TYPE_MAP.values() or ctypes_type == "None":
            return f"# typedef {underlying} -> {decl.name}"
        # User-defined type alias
        return f"{decl.name} = {ctypes_type}"

    # Array typedef
    if isinstance(underlying, Array):
        element = type_to_ctypes(underlying.element_type, types)
        if underlying.size is not None:
            return f"{decl.name} = {element} * {underlying.size}"
        return f"{decl.name} = ctypes.POINTER({element})"

    # Pointer typedef
    if isinstance(underlying, Pointer):
        return f"{decl.name} = {type_to_ctypes(underlying, types)}"

    return f"# typedef {decl.name} (unsupported)"


def _variable_to_ctypes(decl: Variable, lib_name: str, types: _TypeTable) -> str | None:
    """Convert a Variable IR node to a ctypes global variable annotation."""
    var_type = type_to_ctypes(decl.type, types)
    return f"# {lib_name}.{decl.name}: {var_type}"


#: Section each declaration kind renders into, in emission order.
_SECTION_ORDER: tuple[str, ...] = ("constants", "enums", "structs", "typedefs", "functions", "variables")


def _render_declaration(
    decl: object,
    lib_name: str,
    typedef_names: frozenset[str],
    types: _TypeTable,
) -> tuple[str | None, str]:
    """Render one declaration and name the section it belongs in.

    :returns: The rendered source (``None`` when the declaration emits nothing)
        and the section key, or ``""`` for a declaration kind this writer skips.
    """
    if isinstance(decl, Constant):
        return _constant_to_ctypes(decl), "constants"
    if isinstance(decl, Enum):
        return _enum_to_ctypes(decl, typedef_names), "enums"
    if isinstance(decl, Struct):
        return _struct_to_ctypes(decl, types), "structs"
    if isinstance(decl, Typedef):
        return _typedef_to_ctypes(decl, types), "typedefs"
    if isinstance(decl, Function):
        return _function_to_ctypes(decl, lib_name, types), "functions"
    if isinstance(decl, Variable):
        return _variable_to_ctypes(decl, lib_name, types), "variables"
    return None, ""


def _bound_names(decl: object, rendered: str, typedef_names: frozenset[str], types: _TypeTable) -> list[str]:
    """Return the module-level names ``rendered`` assigns for ``decl``.

    Only the sections that precede the exported-symbol block are described. A
    function renders as ``_lib.f.argtypes = ...``, which binds nothing at module
    level, and a variable renders as a comment.
    """
    if isinstance(decl, Constant):
        return [decl.name]
    if isinstance(decl, Enum):
        # An enumerator with no value renders as a comment and binds nothing.
        names = [v.name for v in decl.values if v.value is not None]
        if _enum_needs_alias(decl, typedef_names) and decl.name:
            names.append(decl.name)
        return names
    if isinstance(decl, Struct):
        # A contested tag is emitted under a mangled class name, and that is the
        # name the module actually binds.
        if not decl.name:
            return []
        return [types.record_classes.get(_record_qualified_name(decl), decl.name)]
    if isinstance(decl, Typedef):
        # An unrepresentable or redundant typedef renders as a bare comment.
        return [] if rendered.lstrip().startswith("#") else [decl.name]
    return []


def _typedef_names(header: Header) -> frozenset[str]:
    """Names an explicit ``Typedef`` declaration in this header already binds."""
    return frozenset(d.name for d in header.declarations if isinstance(d, Typedef) and d.name)


#: The inclusive range of a 32-bit signed integer. An enum whose enumerators all
#: fit here is representable as :data:`ENUM_CTYPE` under every ABI headerkit
#: targets; one that does not is widened by the C compiler to something this
#: writer cannot name, so it is not resolved at all.
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1

#: Keywords after which a trailing ``int`` says nothing, so ``unsigned long int``
#: and ``unsigned long`` are one type.
_C_INTEGER_MODIFIERS = frozenset({"unsigned", "signed", "short", "long"})


def _normalised_c_integer(spelling: str) -> str:
    """Collapse a C integer type spelling to the form ``CTYPES_TYPE_MAP`` keys use.

    The two backends spell the same declared type differently -- libclang
    canonicalises through the type system while tree-sitter returns the source
    tokens -- so ``enum E : unsigned`` arrives as ``unsigned int`` from one and
    ``unsigned`` from the other, and ``unsigned long int`` as ``unsigned long``
    and ``unsigned long int``. Left alone, one backend resolves and the other
    refuses: the same header would produce two different modules, which is the
    parity the rest of this writer is built to preserve.

    Only C's own spelling rules are applied -- a bare ``unsigned``/``signed`` is
    ``int``, and a trailing ``int`` is redundant after a size or signedness
    keyword. A typedef is *not* followed: ``u64`` names a width this writer
    cannot establish, and inventing one is what refusal exists to prevent.
    """
    tokens = spelling.split()
    if not tokens:
        return spelling.strip()
    if len(tokens) > 1 and tokens[-1] == "int" and any(t in _C_INTEGER_MODIFIERS for t in tokens[:-1]):
        tokens = tokens[:-1]
    if tokens in (["unsigned"], ["signed"]):
        tokens = ["unsigned", "int"] if tokens == ["unsigned"] else ["int"]
    return " ".join(tokens)


def _enum_ctype(decl: Enum) -> str | None:
    """The ctypes spelling for ``decl``, or None when this writer cannot size it.

    This is the size and signedness of every value of the enum that crosses the
    ABI, so a wrong answer here is a wrong answer everywhere the type appears --
    and one that *imports cleanly*, since a member of the wrong width is still a
    valid member. Refusing is the only safe alternative to knowing.

    Three sources of truth, in order:

    0. **Whether the parser could see the clause at all.** ``underlying_type``
       is ``None`` both when the header declared none and when the grammar could
       not represent what it declared -- tree-sitter's C grammar has no
       production for ``enum E : long long``. ``underlying_type_known`` separates
       the two, and an unknown width is refused rather than inferred from the
       enumerators, which would size that enum at four bytes against a real
       eight.
    1. **The declared underlying type.** C++11 lets any enum fix it, scoped or
       not -- ``enum class E : unsigned char`` and ``enum E : unsigned long long``
       both do -- and it is the only thing that decides the width when present.
       It cannot be reconstructed from the enumerators, which constrain the
       width from below and say nothing about a type chosen to be wider. If it
       names something this writer has no ctypes spelling for, that is a refusal
       rather than a guess.
    2. **No enumerators and no declared type.** There is nothing to reason from,
       so it is refused. Two different headers arrive here and the IR does not
       tell them apart: an *opaque* declaration (C's ``enum E;``, C++'s
       ``enum class E;``), where refusing is right, and a *complete* empty-body
       enum (C++'s ``enum E {};``, whose underlying type is ``int`` and whose
       ``sizeof`` is 4), where refusing is over-conservative. Both reach this
       function as ``values=[] underlying_type=None`` on both backends.

       Refusing both is the safe direction -- the cost is a loud failure on a
       header that could have been sized, not a wrong width on one that could
       not -- and separating them needs the IR to record that a body was present,
       which it does not. The arm is load-bearing regardless: without it the loop
       below runs zero times and returns ``ENUM_CTYPE`` by vacuous truth, a
       four-byte answer produced by having examined nothing. An earlier revision
       shipped exactly that and sized ``enum Fwd : long long;`` as ``c_int``
       against a real eight bytes. No *execution* gate covers it, because a C
       member of an incomplete enum does not compile and the C++ empty-body case
       is precisely the one this refuses; it is pinned by unit assertion instead.
    3. **The enumerators.** With no declared underlying type, C requires only
       that the type represent every enumerator, and every ABI headerkit targets
       uses an int-sized type where they all fit in one. A value the IR does not
       record as an integer -- a string, where a backend left an initialiser
       unevaluated, or ``None`` -- is not something to guess about either.

    A scoped enum is *not* special-cased. C++ fixes its underlying type to
    ``int`` when the header does not, so rule 3 answers it correctly; an earlier
    revision refused every scoped enum and still resolved the unscoped
    ``enum E : unsigned long long`` wrongly, because ``is_scoped`` was never the
    property that mattered.
    """
    if not decl.underlying_type_known:
        return None
    if decl.underlying_type:
        return CTYPES_TYPE_MAP.get(_normalised_c_integer(decl.underlying_type))
    if not decl.values:
        return None
    for v in decl.values:
        if not isinstance(v.value, int):
            return None
        if not _INT32_MIN <= v.value <= _INT32_MAX:
            return None
    return ENUM_CTYPE


def _enum_type_names(header: Header) -> dict[str, str]:
    """Every enum spelling this header can resolve, mapped to its ctypes type.

    An enum binds no ctypes class, so a use of one has to be recognised from the
    type's name and rewritten to the integer it is represented as. Three
    spellings reach this writer for the same enum and all are collected, because
    which one arrives depends on the backend and on how the header spelled it:
    the tag form ``enum E``, the bare form ``E`` (tree-sitter reduces every
    reference to it, and a typedef produces it under either backend), and the
    qualified form ``n::E`` for an enum inside a C++ namespace.

    **Only enums :func:`_enum_ctype` can size appear here.** One it cannot keeps
    its C spelling, which does not import -- a loud failure rather than a member
    of the wrong width that imports and computes wrong answers across the ABI.

    Two shadowing rules keep the bare form from over-reaching. A tag and an
    ordinary identifier are separate namespaces in C, so ``enum Tone { ... };``
    and ``typedef struct { ... } Tone;`` are both legal in one unit and an
    unprefixed ``Tone`` is the *record*; treating it as the enum would retype an
    eight-byte record as a four-byte integer. Any name a record or a non-enum
    typedef binds is therefore withheld, and a tag-less ``typedef struct`` is
    looked for under both kinds: it reaches this writer as a ``Struct`` carrying
    the alias as its name, with no ``Typedef`` node of its own.

    A spelling two declarations disagree about is dropped rather than resolved
    to whichever came last -- ``namespace a { enum E : char ... }`` beside
    ``namespace b { enum E : long long ... }`` leaves bare ``E`` meaning two
    different widths, and there is no answer to give.

    Withholding a name a ``Typedef`` binds is the arm no execution gate covers,
    because the shape that reaches it -- ``typedef struct ToneRec Tone;`` next to
    ``enum Tone`` -- cannot be scaffolded into an importable module today. Two
    separate unfixed defects in this writer stand between that header and a
    gate, both about *records* rather than enums:

    1. a record alias renders via :func:`_typedef_to_ctypes` into the
       ``typedefs`` section, which ``_SECTION_ORDER`` emits *after* ``structs``,
       so a record using the alias raises ``NameError`` at import;
    2. records are emitted in declaration order rather than dependency order.

    Whoever closes either of those should come back and gate this arm. It is
    kept meanwhile because dropping it does not restore that module, it only
    changes how the module fails: the member becomes a four-byte integer where C
    laid out an eight-byte record, so the package imports and computes wrong
    answers instead of raising.

    A bare name whose enum is declared in *another* header is not resolvable
    here at all: this table is header-local, and tree-sitter does not follow
    ``#include``, so it sees neither the tag spelling nor the declaration. That
    case keeps the C spelling and fails loudly.
    """
    spellings: dict[str, set[str]] = {}
    sizable: set[str] = set()
    for d in header.declarations:
        if not isinstance(d, Enum) or not d.name or _is_anonymous_name(d.name):
            continue
        ctype = _enum_ctype(d)
        if ctype is None:
            continue
        sizable.add(d.name)
        qualified = d.qualified_name or d.name
        for key in (d.name, f"enum {d.name}", qualified, f"enum {qualified}"):
            spellings.setdefault(key, set()).add(ctype)

    # A typedef onto a sizable enum names that same integer, and a record may use
    # it before the typedefs section that would assign it has been emitted.
    # Chains resolve to a fixed point: ``typedef enum E E1; typedef E1 E2;``
    # makes ``E2`` one as well.
    targets: dict[str, str] = {
        d.name: d.underlying_type.name
        for d in header.declarations
        if isinstance(d, Typedef) and d.name and isinstance(d.underlying_type, CType)
    }
    # Iterated to a fixed point, because one pass is not enough.
    #
    # The enums are already in ``spellings`` before this runs, so what matters is
    # not where the enum sits but the typedefs' order *relative to each other*:
    # ``typedef W1 W2`` must be visited after ``typedef enum W W1``. C does
    # guarantee that in the source, and this loop does not walk the source -- it
    # walks the IR, and an ``#include`` places the included file's declarations
    # wherever the parser reports them. A header that includes the second hop
    # from another file arrives as ``[Typedef W2, Enum W, Typedef W1, Struct H]``
    # under libclang, and a single pass leaves ``W2`` unresolved: the member
    # renders as ``("m", W2)`` in ``structs`` while ``W2 = ctypes.c_int`` lands
    # in ``typedefs``, which ``_SECTION_ORDER`` emits afterwards, so the module
    # raises ``NameError`` on import.
    #
    # A previous revision replaced this with one pass on exactly that reasoning
    # about C source order. The premise was true and the conclusion was not.
    enum_typedefs: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, target in targets.items():
            if name in enum_typedefs:
                continue
            source = target if target in spellings else target.removeprefix("enum ")
            resolved = spellings.get(source) or spellings.get(f"enum {source}")
            if resolved:
                spellings.setdefault(name, set()).update(resolved)
                enum_typedefs.add(name)
                changed = True

    shadow: set[str] = set()
    for d in header.declarations:
        if isinstance(d, Struct) and d.name and not _is_anonymous_name(d.name):
            shadow.add(d.name)
            shadow.add(_record_qualified_name(d))
        elif isinstance(d, Typedef) and d.name and d.name not in enum_typedefs:
            shadow.add(d.name)

    # A spelling two declarations resolve differently has no answer; drop it.
    return {key: next(iter(kinds)) for key, kinds in spellings.items() if len(kinds) == 1 and key not in shadow}


def _record_qualified_name(decl: Struct) -> str:
    """A record's full C++ spelling. ``Struct`` carries no ``qualified_name``."""
    return decl.cpp_name or (f"{decl.namespace}::{decl.name}" if decl.namespace else decl.name or "")


def _reserved_names(header: Header) -> set[str]:
    """Every module-level name some declaration in this header already wants.

    A mangled class name has to avoid all of them, or resolving one collision
    would manufacture another.
    """
    reserved: set[str] = set()
    for d in header.declarations:
        name = getattr(d, "name", None)
        if name:
            reserved.add(str(name))
        if isinstance(d, Enum):
            reserved.update(v.name for v in d.values)
    return reserved


def _mangled_class_name(decl: Struct, reserved: set[str]) -> str:
    """A distinct, valid Python class name for a record whose tag is contested.

    Preference order, first free name wins: the qualified C++ spelling with
    ``::`` flattened (``a::Inner`` -> ``a_Inner``, which reads as what it is),
    then the tag with its aggregate keyword appended (``Gauge`` ->
    ``Gauge_struct``, for a collision that has no namespace to disambiguate it),
    then that with a counter. Every candidate is checked against
    :func:`_reserved_names` and against the names already handed out, so
    resolving one collision cannot manufacture a second.
    """
    keyword = "union" if decl.is_union else "struct"
    qualified = _record_qualified_name(decl)
    flattened = re.sub(r"[^0-9A-Za-z_]", "_", qualified)
    candidates = [flattened, f"{decl.name}_{keyword}", f"{flattened}_{keyword}"]
    candidates += [f"{decl.name}_{keyword}_{n}" for n in range(2, 100)]
    for candidate in candidates:
        if candidate and candidate not in reserved:
            return candidate
    raise AssertionError(f"no free class name for {qualified}")  # pragma: no cover


def _record_type_names(header: Header) -> tuple[dict[str, str], dict[str, str], frozenset[str]]:
    """Record spellings mapped to classes, the class each record gets, and contested tags.

    ``_struct_to_ctypes`` normally emits ``class <tag>``, so the work here is
    recognising the *other* ways that same record is spelled at a use site.
    libclang keeps C's elaborated form, so a member reaches the writer as
    ``struct Inner`` or ``union U``; a C++ record in a namespace is spelled
    ``n::Inner``, which matches neither.

    Unlike an enum, a record needs no width analysis and admits no default: the
    answer is the class this writer emits or there is no answer. A record the
    header does not declare -- one from an ``#include`` that tree-sitter never
    read, say -- is absent here and keeps its C spelling, which fails loudly
    rather than resolving to something invented.

    **A contested tag is mangled, not dropped.** Two things can want the bare
    name, and C gives each of them a different meaning:

    * two records whose namespaces differ (``a::Inner`` and ``b::Inner``), which
      flatten to one class name so the second would overwrite the first;
    * a record and an ordinary identifier (``struct Gauge { ... };`` beside
      ``typedef unsigned char Gauge;``), which are separate namespaces in C and
      mean an eight-byte record and a one-byte integer respectively.

    In both cases the record is emitted under a distinct class name and only its
    *unambiguous* spellings -- elaborated and qualified -- are mapped to it. The
    bare name is left to whatever else owns it: the typedef, resolved through
    :func:`_scalar_typedef_names`, or nothing at all when two records contest it,
    in which case it stays unbound and fails loudly. Every spelling then means
    what C says it means, and nothing that worked before stops working. One
    mechanism covers both collisions because they are one shape.

    The bare tag of an *uncontested* record maps to itself, which is a no-op. It
    is registered anyway rather than special-cased, because the lookup happens
    after ``CTYPES_TYPE_MAP``: a header may legally declare ``struct int8_t``
    beside the standard ``int8_t``, and a bare ``int8_t`` must keep meaning the
    integer.
    """
    records = [d for d in header.declarations if isinstance(d, Struct) and d.name and not _is_anonymous_name(d.name)]

    # A tag is contested when more than one record flattens onto it, or when a
    # typedef binds the same name to something that is not that record.
    behind_tag: dict[str, set[str]] = {}
    for d in records:
        behind_tag.setdefault(str(d.name), set()).add(_record_qualified_name(d))
    typedef_bound = {
        d.name
        for d in header.declarations
        if isinstance(d, Typedef) and d.name and not _typedef_aliases_its_own_tag(d, header)
    }
    # The two kinds of contest disqualify different spellings, so they are kept
    # apart. A typedef cannot wear ``struct Gauge``, so the elaborated form stays
    # unambiguous there; two records sharing a tag contest that form as well.
    shared_by_records = {tag for tag, qualified in behind_tag.items() if len(qualified) > 1}
    shared_with_typedef = set(behind_tag) & typedef_bound
    contested = shared_by_records | shared_with_typedef

    reserved = _reserved_names(header)
    classes: dict[str, str] = {}
    spellings: dict[str, set[str]] = {}
    for d in records:
        if str(d.name) in contested:
            class_name = _mangled_class_name(d, reserved)
            reserved.add(class_name)
        else:
            class_name = str(d.name)
        classes[_record_qualified_name(d)] = class_name

        keyword = "union" if d.is_union else "struct"
        qualified = _record_qualified_name(d)
        tag = str(d.name)

        keys: list[str] = []
        # A qualified spelling names exactly one record, so it is always safe --
        # but only when it actually carries a namespace. For a record at global
        # scope it *is* the bare tag, and must not smuggle it back in.
        if qualified != tag:
            keys += [qualified, f"{keyword} {qualified}"]
        # The elaborated tag survives a collision with an ordinary identifier and
        # not one with another record.
        if tag not in shared_by_records:
            keys.append(f"{keyword} {tag}")
        # The bare tag survives neither.
        if tag not in contested:
            keys.append(tag)
        for key in keys:
            spellings.setdefault(key, set()).add(class_name)

    # An elaborated spelling two records still share -- same tag, same namespace
    # spelling -- has no answer to give, so it is dropped rather than guessed.
    resolved = {key: next(iter(names)) for key, names in spellings.items() if len(names) == 1}
    return resolved, classes, frozenset(contested)


def _typedef_aliases_its_own_tag(decl: Typedef, header: Header) -> bool:
    """Does ``decl`` give a record the name that record's own tag already has?

    ``typedef struct Gauge Gauge;`` takes nothing away from ``struct Gauge`` --
    both spellings mean one type -- so it does not contest the tag. Every other
    typedef of that name does, and the target's *kind* is not what decides it:
    ``typedef struct Other Gauge;`` is struct-spelled and still makes an
    unprefixed ``Gauge`` mean a different record, one byte against eight in the
    case that found this. An earlier form of this guard excused any
    struct-spelled typedef and so never fired on that one.
    """
    underlying = decl.underlying_type
    if not isinstance(underlying, CType):
        return False
    target = underlying.name.removeprefix("struct ").removeprefix("union ")
    if target != decl.name:
        return False
    return any(isinstance(d, Struct) and d.name == target for d in header.declarations)


def _scalar_typedef_names(header: Header) -> dict[str, str]:
    """Typedef names that resolve to a ctypes scalar, mapped to that scalar.

    ``_typedef_to_ctypes`` renders ``typedef unsigned char Level;`` as a bare
    comment, on the reasoning that ctypes has no distinct type to alias. That is
    true of the *binding* and false of the *use*: a member declared ``Level v;``
    reached the module as ``("v", Level)`` naming something nothing had defined,
    so every such member was a ``NameError`` at import -- with or without any
    collision. Resolving the name where it is used repairs that without adding a
    module-level binding, whose position relative to the records using it would
    then have to be reasoned about.

    Only typedefs onto a builtin scalar are listed. A typedef onto a record or
    an enum is already answered by the other two tables, and one onto anything
    this writer has no spelling for is left alone to fail loudly.

    A name that is also a contested record tag stays listed here. Both backends
    record ``CType.is_elaborated``, so a bare ``Gauge`` is known to mean this
    typedef and an elaborated ``struct Gauge`` the record; see
    :func:`type_to_ctypes`. Before that flag existed neither could be resolved,
    because tree-sitter strips the aggregate keyword and the two arrived
    identical.
    """
    scalars: dict[str, str] = {}
    for d in header.declarations:
        if not isinstance(d, Typedef) or not d.name or not isinstance(d.underlying_type, CType):
            continue
        underlying = d.underlying_type
        non_cv = [q for q in underlying.qualifiers if q not in ("const", "volatile", "restrict")]
        qualified = " ".join([*non_cv, underlying.name]) if non_cv else underlying.name
        ctype = CTYPES_TYPE_MAP.get(qualified) or CTYPES_TYPE_MAP.get(underlying.name)
        if ctype is not None and ctype != "None":
            scalars[d.name] = ctype
    return scalars


def _type_table(header: Header) -> _TypeTable:
    """Resolve, once per header, every spelling the writer can rewrite."""
    record_spellings, record_classes, contested = _record_type_names(header)
    return _TypeTable(
        enums=_enum_type_names(header),
        records=record_spellings,
        record_classes=record_classes,
        scalars=_scalar_typedef_names(header),
        contested_records={
            str(d.name): record_classes[_record_qualified_name(d)]
            for d in header.declarations
            if isinstance(d, Struct) and d.name in contested and _record_qualified_name(d) in record_classes
        },
    )


def declared_binding_names(header: Header, lib_name: str = "_lib") -> list[str]:
    """Return the non-function module-level names ``header_to_ctypes`` binds.

    A header that declares no function still declares *something* worth
    asserting on, and ``AGENTS.md`` §1 forbids falling back to a module
    existence check. This is what a generated test asserts on instead.
    """
    names: list[str] = []
    typedef_names = _typedef_names(header)
    types = _type_table(header)
    for decl in header.declarations:
        rendered, section = _render_declaration(decl, lib_name, typedef_names, types)
        if rendered is None or section in ("", "functions", "variables"):
            continue
        for name in _bound_names(decl, rendered, typedef_names, types):
            if name not in names:
                names.append(name)
    return names


def header_to_ctypes(header: Header, lib_name: str = "_lib", *, library: str | None = None) -> str:
    """Convert all declarations in a Header to a Python ctypes module string.

    :param header: Parsed header IR from headerkit.
    :param lib_name: Variable name for the loaded library object. Used in
        function prototype annotations (e.g., ``_lib.func.argtypes = [...]``).
    :param library: Base name of the native library to load. When given, the
        module defines ``lib_name`` itself and binds each function to a
        module-level name, so the result is importable and callable on its own.
        When omitted the output stays a fragment that expects the caller to
        supply ``lib_name`` -- which is what the single-file writer emits.
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
    #: Functions to re-export as module-level callables. Only populated when a
    #: library is being loaded, since without one there is nothing to bind to.
    bound_symbols: list[str] = []
    #: Module-level names something earlier in the file already assigns: a
    #: declaration, an import, or the loader preamble. C keeps tags and ordinary
    #: identifiers in separate namespaces, so ``struct Rec { ... };`` and
    #: ``int Rec(void);`` are both legal in one translation unit and both reach
    #: Python as ``Rec``. The export block is emitted last, so re-exporting the
    #: function would replace the struct class with a function pointer, silently,
    #: after import. ``int _lib(void);`` is the same collision against the loader
    #: preamble, and destroys the library handle every later export reads from.
    #: Names are added here as they are emitted, so a conditional import is
    #: reserved only when it is actually written. The preamble blocks contribute
    #: via :func:`module_level_bindings`, which reads the text actually emitted
    #: rather than a hand-kept list, so a block that starts binding a new name
    #: reserves it without a second list needing to be updated in step.
    taken_names: set[str] = set()
    typedef_names = _typedef_names(header)
    types = _type_table(header)

    for decl in header.declarations:
        result, section = _render_declaration(decl, lib_name, typedef_names, types)

        if result is not None and section:
            sections[section].append(result)
            taken_names.update(_bound_names(decl, result, typedef_names, types))
            if section == "functions" and library is not None:
                name = getattr(decl, "name", None)
                if name and name not in bound_symbols:
                    bound_symbols.append(name)

    # Build output
    output_lines: list[str] = []

    # Module docstring
    output_lines.append(_module_docstring(header.path))
    output_lines.append("")

    # Imports
    needs_abi_flag = any(_ABI_FLAG in item for item in sections["structs"])

    output_lines.append("import ctypes")
    output_lines.append("import ctypes.util")
    taken_names.add("ctypes")
    if library is not None:
        output_lines.append("import os")
        taken_names.add("os")
    if needs_abi_flag:
        output_lines.append("import platform")
        taken_names.add("platform")
    output_lines.append("import sys")
    taken_names.add("sys")
    output_lines.append("")
    if needs_abi_flag:
        output_lines.append(ABI_ALIGNMENT_NOTE)
        taken_names.update(module_level_bindings(ABI_ALIGNMENT_NOTE))
        output_lines.append("")

    if library is not None:
        output_lines.append(f"# {'=' * 60}")
        output_lines.append("# Native library")
        output_lines.append(f"# {'=' * 60}")
        output_lines.append("")
        loader = _library_loader(library, lib_name)
        output_lines.append(loader)
        output_lines.append("")
        taken_names.update(module_level_bindings(loader))

    # Sections
    section_order = list(_SECTION_ORDER)
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

    if bound_symbols:
        output_lines.append(f"# {'=' * 60}")
        output_lines.append("# Exported Symbols")
        output_lines.append(f"# {'=' * 60}")
        output_lines.append("")
        # Bound after the argtypes/restype annotations above, so each name is a
        # fully configured callable rather than an unconverted raw _FuncPtr.
        for symbol in bound_symbols:
            if symbol in taken_names:
                output_lines.append(
                    f"# {symbol!r} is not re-exported: a declaration above already binds that "
                    f"name. Call it as {lib_name}.{symbol}."
                )
            else:
                output_lines.append(f"{symbol} = {lib_name}.{symbol}")
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
    library : str
        Base name of the *native* library a scaffolded package loads, without a
        ``lib`` prefix or a platform suffix -- what ``ctypes.util.find_library``
        is given. This is not ``lib_name``, which names a Python variable.
        Defaults to the package name, which is right only when the two happen to
        coincide: scaffolding ``probe_bindings`` around ``libprobe`` needs
        ``library="probe"`` or the generated module cannot find its binary.

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
        WriterOption(
            name="library",
            description=(
                "Base name of the native library a scaffolded package loads, without a 'lib' "
                "prefix or platform suffix. Defaults to the package name."
            ),
            default=None,
            type=str,
        ),
    )

    def __init__(self, lib_name: str = "_lib") -> None:
        self._lib_name = lib_name

    def _render(self, unit: SourceUnit | Header, *, library: str | None = None) -> str:
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        return header_to_ctypes(header, lib_name=self._lib_name, library=library)

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
        # The native library rarely shares the Python package's name -- a
        # ``probe_bindings`` package around ``libprobe`` is the ordinary case --
        # so the package name is the last resort, not the answer.
        library = options.get_option("library") or pkg
        # A package is a complete, importable artifact, so it loads its own
        # library. The single-file layout stays a fragment and does not.
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        bindings_code = self._render(unit, library=library)
        fn_names = extract_function_names(unit)
        # A function-less header still declares structs, enums and typedefs. The
        # generated tests assert on those rather than on the module object,
        # which ``AGENTS.md`` §1 prohibits as a sole assertion.
        check_names = fn_names[:10] or declared_binding_names(header, self._lib_name)[:10]

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
            tw_fn_checks = "\n".join(
                f'    assert hasattr(_bindings, "{name}"), "Symbol {name} missing from ctypes bindings"'
                for name in check_names
            ) or (
                # A header declaring nothing at all leaves the load itself as
                # the only thing to assert on. ``_lib`` is the CDLL, not the
                # module, so this is still a claim about the native binary.
                f'    assert _bindings.{self._lib_name} is not None, "the native library did not load"'
            )
            tripwire = render_block_template(
                f"""\
                import pytest
                from {pkg} import _bindings

                @pytest.mark.tripwire
                def test_ctypes_tripwire():
                    \"\"\"Tripwire: verify binary load and C symbols.\"\"\"
                {DEDENT_BLOCK}
            """,
                tw_fn_checks,
            )
            files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

        if test_type in ("both", "unit"):
            # No second ``ismodule`` restatement in the empty case: repeating the
            # assertion already in the body adds a line and no coverage.
            unit_fn_checks = "\n".join(
                f'    assert hasattr(_bindings, "{name}"), "Expected declaration \'{name}\' in _bindings"'
                for name in check_names
            )
            unit_test = render_block_template(
                f"""\
                import inspect
                from {pkg} import _bindings

                def test_{pkg}_declarations():
                    \"\"\"Verify generated bindings module exports declarations.\"\"\"
                    assert inspect.ismodule(_bindings)
                {DEDENT_BLOCK}
            """,
                unit_fn_checks,
            )
            files.append(OutputFile(path="tests/test_bindings.py", content=unit_test))

        if test_type in ("both", "unit"):
            files.extend(build_work_order_files(unit, pkg, "python"))

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
