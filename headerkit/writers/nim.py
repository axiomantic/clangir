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
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Parameter,
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
from headerkit.writers.base import DEDENT_BLOCK, BaseWriter, WriterOption, render_block_template

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


def _c_type_spelling(name: str, is_typedef: bool, tag_keyword: str) -> str:
    """Return the C spelling ``importc`` must use to name this tag.

    ``typedef enum { ... } Flags;`` declares no ``enum Flags`` tag, so an
    ``importc: "enum Flags"`` emits C that names an incomplete type and the
    build fails with ``cast to incomplete type 'enum Flags'``. The same is true
    of a tag-less ``typedef struct { ... } Rec;``.

    ``is_typedef`` is the discriminator the other writers already use for this
    -- ``headerkit.writers.cffi._find_typedef_enum_pairs`` documents it. When it
    is set, the bare name is a valid C type spelling; without it the tag keyword
    is required, since a plain ``struct Rec { ... };`` declares no bare ``Rec``.

    It is *not* simply "a typedef of the same name exists", and the rule differs
    by declaration kind:

    - a **record** sets it when the bare name spells the type -- no tag at all,
      or an alias repeating the tag;
    - an **enum** sets it only when there is no tag at all, because a tagged
      ``typedef enum Switch { ... } Switch;`` really does declare
      ``enum Switch``, and the cffi and Cython writers re-emit that tag.

    So a tagged typedef'd enum has a same-named typedef and ``is_typedef=False``.
    ``tests/test_regression_backend_parity.py`` pins both halves of that split.
    """
    return name if is_typedef else f"{tag_keyword} {name}"


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


#: The Nim declaration, and any companion procs, for each C++ helper type
#: :meth:`NimWriter._format_type` can produce. The renderer records which helpers it
#: emitted and this table turns that record into declarations, so the set declared is
#: the set referenced by construction rather than by a second predicate that has to
#: agree with the first.
CPP_HELPER_DECLARATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "CppString": ('CppString* {.importcpp: "std::string", header: "<string>".} = object', ()),
    "CppVector": ('CppVector*[T] {.importcpp: "std::vector<\'0>", header: "<vector>".} = object', ()),
    "UniquePtr": (
        'UniquePtr*[T] {.importcpp: "std::unique_ptr<\'0>", header: "<memory>".} = object',
        (
            'proc `=copy`*[T](dst: var UniquePtr[T], src: UniquePtr[T]) {.error: "std::unique_ptr cannot be copied in Nim; use std/moves.move() or sink".}',
            'proc move*[T](p: var UniquePtr[T]): UniquePtr[T] {.importcpp: "std::move(@)", header: "<utility>".}',
            'proc get*[T](p: UniquePtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
            'proc reset*[T](p: var UniquePtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
        ),
    ),
    "SharedPtr": (
        'SharedPtr*[T] {.importcpp: "std::shared_ptr<\'0>", header: "<memory>".} = object',
        (
            'proc get*[T](p: SharedPtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
            'proc reset*[T](p: var SharedPtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
            'proc useCount*[T](p: SharedPtr[T]): clong {.importcpp: "#.use_count()", header: "<memory>".}',
        ),
    ),
    "WeakPtr": (
        'WeakPtr*[T] {.importcpp: "std::weak_ptr<\'0>", header: "<memory>".} = object',
        ('proc lock*[T](p: WeakPtr[T]): SharedPtr[T] {.importcpp: "#.lock()", header: "<memory>".}',),
    ),
}

#: Helpers whose companion procs name another helper, which must therefore be
#: declared alongside them.
_CPP_HELPER_REQUIRES: dict[str, tuple[str, ...]] = {"WeakPtr": ("SharedPtr",)}

#: Tag keywords that mark a name as a C record or enumeration rather than a C++ one.
_C_TAG_PREFIXES: tuple[str, ...] = ("struct ", "union ", "enum ")


def _nim_string_path(path: str) -> str:
    """Render ``path`` so it survives inside a Nim string literal.

    Normalised with PureWindowsPath rather than PurePath so the result depends on
    the path rather than on the host that generated it.
    """
    return PureWindowsPath(path).as_posix()


def _cfg_path_flag(flag: str, path: str) -> str:
    """Render a ``-I``/``-L`` flag for ``nim.cfg`` so a path with spaces survives.

    Nim strips the outer quotes from a config value and passes the result to the C
    compiler after word-splitting it, so ``--passC:"-I/opt/na me"`` reaches clang as
    ``-I/opt/na`` and ``me`` and fails with ``no such file or directory: 'me'``. An
    inner quote survives that split and the compiler driver removes it.

    That inner quote must be a **double** quote, escaped. A config file is read by
    Nim's own lexer, where ``'`` opens a character literal, so a single-quoted path
    is a syntax error in the generated file rather than a flag -- ``nim.cfg(5, 18)
    Error: invalid character constant``, on every Nim invocation the package makes.

    The path is emitted with forward slashes for the same reason: a Windows path
    inside a Nim string literal would put ``\\U`` in front of ``Users`` and be read
    as an escape. Every compiler in the matrix accepts ``/`` as a separator on
    Windows, so this costs nothing and removes the escape question entirely.
    """
    if '"' in path:
        raise ValueError(
            f"cannot place {path!r} in nim.cfg: a double quote in an include or library "
            f"path cannot be quoted in Nim's config format"
        )
    # PureWindowsPath, not PurePath: the separator to normalise is a property of the
    # path, not of the host generating the config, and it leaves a POSIX path alone.
    return f'"{flag}\\"{_nim_string_path(path)}\\""'


def _type_matches(t: TypeExpr, pred: Callable[[str], bool]) -> bool:
    """Whether any type name inside ``t`` satisfies ``pred``."""
    if isinstance(t, CType):
        return pred(t.name)
    elif isinstance(t, Pointer):
        return _type_matches(t.pointee, pred)
    elif isinstance(t, Reference):
        return _type_matches(t.target, pred)
    elif isinstance(t, Array):
        return _type_matches(t.element_type, pred)
    elif isinstance(t, FunctionPointer):
        if _type_matches(t.return_type, pred):
            return True
        return any(_type_matches(p.type, pred) for p in t.parameters)
    return False


def _decl_matches(d: object, pred: Callable[[str], bool]) -> bool:
    """Whether any type name reachable from declaration ``d`` satisfies ``pred``."""
    if isinstance(d, Struct):
        for f in d.fields:
            if _type_matches(f.type, pred):
                return True
        for m in d.methods + d.constructors:
            if _type_matches(m.return_type, pred):
                return True
            if any(_type_matches(p.type, pred) for p in m.parameters):
                return True
        if d.destructor and any(_type_matches(p.type, pred) for p in d.destructor.parameters):
            return True
    elif isinstance(d, Function):
        if _type_matches(d.return_type, pred):
            return True
        if any(_type_matches(p.type, pred) for p in d.parameters):
            return True
    elif isinstance(d, Typedef):
        if _type_matches(d.underlying_type, pred):
            return True
    elif isinstance(d, Variable):
        if _type_matches(d.type, pred):
            return True
    return False


def _type_contains(t: TypeExpr, target_prefix: str) -> bool:
    """Check structurally whether a TypeExpr contains target_prefix in its type names."""
    return _type_matches(t, lambda name: target_prefix in name)


def _decl_contains(d: object, target_prefix: str) -> bool:
    """Check structurally whether a Declaration references target_prefix."""
    return _decl_matches(d, lambda name: target_prefix in name)


def _bare_type_head(raw: str) -> str:
    """Strip qualifiers, tag keywords and template arguments down to the head name.

    The tag keyword is stripped here so that the *caller* decides what a tag means,
    rather than the answer depending on this function happening to leave one in
    place. ``_type_name_requires_cpp`` is the one place that decision is made.
    """
    name = raw.strip()
    for qualifier in ("const ", "volatile "):
        while name.startswith(qualifier):
            name = name[len(qualifier) :].strip()
    for tag in _C_TAG_PREFIXES:
        if name.startswith(tag):
            name = name[len(tag) :].strip()
            break
    return name.split("<", 1)[0].rsplit("::", 1)[-1].strip()


def _decl_contains_exact(d: object, head: str) -> bool:
    """Whether ``d`` references a type whose unqualified head is exactly ``head``.

    The substring form cannot serve: libclang reports a ``std::string`` field as
    the bare name ``string``, and a substring test for it also matches ``wstring``
    and any C type containing those letters. The head comparison is exact, and a C
    tag keeps its keyword so it never looks like a library name.
    """
    return _decl_matches(d, lambda name: not name.strip().startswith(_C_TAG_PREFIXES) and _bare_type_head(name) == head)


#: Standard-library names the writer renders as a C++ helper type (``CppString``,
#: ``UniquePtr``, ``SharedPtr``, ``WeakPtr``, ``CppVector``) or as an ``importcpp``
#: base object.
#:
#: These are **unqualified heads**, because that is the shape the parse produces:
#: libclang reports a ``std::string`` field as the bare name ``string``, so a
#: ``std::``-qualified entry here would match nothing a backend ever emits. A C tag
#: is distinguishable from these without ambiguity -- it keeps its ``struct ``,
#: ``union `` or ``enum `` keyword -- so a C header declaring ``struct vector`` is
#: not mistaken for ``std::vector``. See :func:`_type_name_requires_cpp`.
CPP_STDLIB_MARKERS: frozenset[str] = frozenset(
    {
        "exception",
        "string",
        "wstring",
        "basic_string",
        "unique_ptr",
        "shared_ptr",
        "weak_ptr",
        "vector",
    }
)


def _type_name_requires_cpp(raw: str, *, unit_is_cpp: bool) -> bool:
    """Whether a type *name* is one only C++ can spell.

    Two of the three signals are unambiguous and hold in any unit: a ``<`` is a
    template-id and a ``::`` is a qualified name, and C has neither.

    The third -- a bare name in :data:`CPP_STDLIB_MARKERS` -- is not decidable from
    spelling at all: a ``std::string`` field and a C ``typedef struct {...} string;``
    reach the IR as the identical ``CType(name="string")``. A tag keyword does not
    separate them either, because a *use of a typedef* carries no tag. So that
    branch is gated on ``unit_is_cpp``, the language the parser chose for the
    translation unit; in a C unit a bare ``string`` is a C typedef and nothing else.
    No heuristic over the name can substitute: the two are not merely hard to tell
    apart, they are the same object.
    """
    name = raw.strip()
    if "<" in name or "::" in name:
        return True
    if not unit_is_cpp or name.startswith(_C_TAG_PREFIXES):
        return False
    return _bare_type_head(name) in CPP_STDLIB_MARKERS


def _type_requires_cpp(t: TypeExpr, *, unit_is_cpp: bool) -> bool:
    """Whether rendering ``t`` produces Nim that only the C++ backend can build."""
    if isinstance(t, Reference):
        # Rendered as `var T`, which is a C++ reference. C has no such parameter.
        return True
    if isinstance(t, CType):
        return _type_name_requires_cpp(t.name, unit_is_cpp=unit_is_cpp)
    if isinstance(t, Pointer):
        return _type_requires_cpp(t.pointee, unit_is_cpp=unit_is_cpp)
    if isinstance(t, Array):
        return _type_requires_cpp(t.element_type, unit_is_cpp=unit_is_cpp)
    if isinstance(t, FunctionPointer):
        return _type_requires_cpp(t.return_type, unit_is_cpp=unit_is_cpp) or any(
            _type_requires_cpp(p.type, unit_is_cpp=unit_is_cpp) for p in t.parameters
        )
    return False


def _signature_requires_cpp(f: Function, *, unit_is_cpp: bool) -> bool:
    """Whether a function's return type or any parameter type is C++-only."""
    return _type_requires_cpp(f.return_type, unit_is_cpp=unit_is_cpp) or any(
        _type_requires_cpp(p.type, unit_is_cpp=unit_is_cpp) for p in f.parameters
    )


def _struct_requires_cpp(s: Struct) -> bool:
    """Whether this record is rendered with ``importcpp`` rather than ``importc``.

    ``namespace`` belongs here rather than only in :func:`unit_requires_cpp`: a
    record in a namespace has no C tag to import, and the emitter already spells
    its ``importcpp`` pattern as ``ns::Name`` once it takes this branch.
    """
    return bool(
        s.is_cppclass or s.methods or s.bases or s.constructors or s.destructor or s.template_params or s.namespace
    )


def _function_requires_cpp(f: Function, *, unit_is_cpp: bool = True) -> bool:
    """Whether this free function is rendered with ``importcpp`` rather than ``importc``.

    A C++-only *signature* belongs here alongside namespace and template, because
    the pragma and the rendered parameter types have to agree. An ``int&``
    parameter renders as ``var cint``; under ``importc`` Nim passes ``int*`` and
    the C++ compiler rejects the call, while ``importcpp`` passes an lvalue and it
    binds.
    """
    return bool(f.namespace or f.template_params or _signature_requires_cpp(f, unit_is_cpp=unit_is_cpp))


def unit_requires_cpp(unit: SourceUnit | Header) -> bool:
    """Whether the Nim bindings for ``unit`` need the C++ backend to compile.

    The answer is read off the IR, never off the file extension: a ``.h`` may
    declare a class and a ``.hpp`` may declare nothing but C functions.

    This is deliberately a **superset** of the ``importc``/``importcpp`` pragma
    decision, because the two questions are different. A record whose pragma is
    ``importc`` still forces the C++ backend if one of its fields is a
    ``std::string``, since the field renders as ``CppString``; and a free function
    keeps its ``importc`` pragma while taking an ``int&``, which renders as
    ``var cint``. Both are C++-only Nim emitted under a C pragma, so asking only
    "which pragma" would answer C for a unit the C backend cannot build.

    The test is structural rather than an enumerated list of shapes: any
    reference, any template-id, any qualified name, any namespace, any scoped
    enumeration. A shape nobody thought to enumerate is still caught if it is one
    of those. The one test that spelling cannot settle -- a bare ``string`` being
    ``std::string`` or a C typedef of that name -- is settled by the language the
    parser recorded for the unit; see :func:`_type_name_requires_cpp`.
    """
    unit_is_cpp = getattr(unit, "language", "c") == "cpp"
    for decl in unit.declarations:
        if isinstance(decl, Struct):
            if _struct_requires_cpp(decl):
                return True
            if any(_type_requires_cpp(f.type, unit_is_cpp=unit_is_cpp) for f in decl.fields):
                return True
            if any(_signature_requires_cpp(m, unit_is_cpp=unit_is_cpp) for m in decl.methods + decl.constructors):
                return True
        elif isinstance(decl, Function):
            if _function_requires_cpp(decl, unit_is_cpp=unit_is_cpp):
                return True
        elif isinstance(decl, Enum):
            # `enum class` has no C spelling at all, scoped or otherwise.
            if decl.is_scoped or decl.namespace:
                return True
        elif isinstance(decl, Typedef):
            if _type_requires_cpp(decl.underlying_type, unit_is_cpp=unit_is_cpp):
                return True
        elif isinstance(decl, Variable):
            if _type_requires_cpp(decl.type, unit_is_cpp=unit_is_cpp):
                return True
        if getattr(decl, "namespace", None):
            return True
    return False


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
        WriterOption(
            name="library",
            description="Native library to link, without the 'lib' prefix or extension (emits --passL:-l<name>)",
            default=None,
            type=list,
        ),
        WriterOption(
            name="library_dirs",
            description="Directories to search for the native library (emits --passL:-L<dir>)",
            default=None,
            type=list,
        ),
    )

    def __init__(self, *, header_path: str | None = None) -> None:
        self.header_path = header_path
        self._used_helpers: set[str] = set()

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Nim comments."""
        return "# {line}"

    def _render(self, unit: SourceUnit | Header) -> str:
        """Convert parsed header IR to Nim source code."""
        header = unit
        lines: list[str] = []

        # Forward slashes, because this lands inside a Nim string literal in every
        # `{.header: "...".}` pragma below. A Windows path puts `\U` in front of
        # `Users` and `\x` in front of whatever follows, and Nim reads those as
        # escapes: `Error: expected a hex digit, but found: s`. Every compiler
        # accepts `/` on Windows, and this is a no-op for a path that has none.
        header_file = _nim_string_path(self.header_path or header.path or "header.h")

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
        # Reset per render: the helper declarations emitted below are exactly the
        # helpers `_format_type` reports having produced while rendering this unit.
        self._used_helpers = set()

        has_std_exception = any(
            isinstance(decl, Struct) and any("std::exception" in b.name for b in decl.bases)
            for decl in header.declarations
        )

        if has_std_exception:
            types_section.append(
                'std_exception* {.importcpp: "std::exception", header: "<exception>".} = object of RootObj'
            )
            emitted_types.add("std_exception")

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

        # Helper declarations come from the render itself, not from a second
        # predicate over the IR: whatever `_format_type` produced is declared, and
        # nothing else. They are prepended so a helper is declared before the
        # record whose field names it.
        helper_types: list[str] = []
        helper_procs: list[str] = []
        for helper in sorted(self._used_helpers):
            if helper in emitted_types:
                continue
            declaration, companions = CPP_HELPER_DECLARATIONS[helper]
            helper_types.append(declaration)
            emitted_types.add(helper)
            for companion in companions:
                helper_procs.extend(["", companion])
        types_section = helper_types + types_section
        procs_section = helper_procs + procs_section

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

    def _use_helper(self, helper: str) -> str:
        """Record that this render referenced ``helper``, and return its name.

        The declaration set is built from these records, so a helper cannot be
        referenced without also being declared.
        """
        self._used_helpers.add(helper)
        for required in _CPP_HELPER_REQUIRES.get(helper, ()):
            self._used_helpers.add(required)
        return helper

    def _format_type(self, t: TypeExpr, *, in_param: bool = False) -> str:
        """Convert IR TypeExpr to a Nim type representation."""
        if isinstance(t, CType):
            # A name still carrying a C tag keyword is a C record, whatever it is
            # called, so it must not be matched against the standard-library
            # spellings below: `struct string` is not `std::string`.
            has_c_tag = t.name.strip().startswith(_C_TAG_PREFIXES)
            name = t.name.removeprefix("struct ").removeprefix("union ").removeprefix("enum ")
            if "(anonymous" in name or "(unnamed" in name:
                return "pointer"

            # C++ Smart Pointers & Containers mapping
            if has_c_tag:
                pass
            elif name.startswith("std::shared_ptr<") or name.startswith("shared_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('SharedPtr')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::unique_ptr<") or name.startswith("unique_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('UniquePtr')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::weak_ptr<") or name.startswith("weak_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('WeakPtr')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::vector<") or name.startswith("vector<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('CppVector')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::string") or name == "string":
                return self._use_helper("CppString")

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
        is_cpp = _struct_requires_cpp(s)

        if is_cpp:
            cpp_pattern = s.cpp_name or (f"{s.namespace}::{s.name}" if s.namespace else s.name)
            pragma_parts.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')
            pragma_parts.append("bycopy")
        else:
            pragma_parts.append(
                f'importc: "{_c_type_spelling(name, s.is_typedef, "union" if s.is_union else "struct")}", header: "{header_file}"'
            )
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

        spelling = _c_type_spelling(name, e.is_typedef, "enum")
        lines = [f'{e_name}* {{.size: sizeof(cint), importc: "{spelling}", header: "{header_file}".}} = enum']
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
        if not _function_requires_cpp(f):
            pragmas.append(f'importc: "{f.name}", header: "{header_file}"')
        elif f.namespace:
            pragmas.append(f'importcpp: "{f.namespace}::{f.name}(@)", header: "{header_file}"')
        else:
            pragmas.append(f'importcpp: "{f.name}(@)", header: "{header_file}"')

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

    @staticmethod
    def _as_list(value: object) -> list[str]:
        """Normalise a writer option that may arrive as a scalar, list, or ``None``."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, set | frozenset):
            # Sorted, not iteration-ordered: an unordered option would reorder the
            # generated flags between runs and defeat regenerate-and-diff.
            return sorted(str(v) for v in value if str(v))
        if isinstance(value, list | tuple):
            return [str(v) for v in value if str(v)]
        return [str(value)]

    def _build_nim_cfg(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> str:
        """Render ``nim.cfg`` -- the flags the generated package needs to compile.

        Every flag here is either invariant, derived from the IR, or supplied by
        the caller. Nothing is guessed: a package whose library the caller did not
        name links against nothing and says so, rather than carrying a plausible
        ``-l`` that resolves to the wrong library or to none.
        """
        lines = ["--mm:orc", "--threads:on", "--styleCheck:hint"]

        # `$config` is the directory holding this file, so the package builds with a
        # bare `nim c tests/...` and not only under `nimble`, which supplies srcDir.
        lines.append('--path:"$config/src"')

        if unit_requires_cpp(unit):
            # The bindings carry `importcpp` pragmas and `#include` a C++ header.
            # The default C backend hands both to the C compiler, which rejects
            # `class` outright. Set in nim.cfg rather than in the .nimble test task
            # because a config backend selection also overrides a plain `nim c`.
            lines.append("--backend:cpp")

        include_dirs = [str(Path(d).resolve()) for d in self._as_list(options.extra_context.get("include_dirs"))]
        # The header's own directory: the one include path the writer always knows,
        # and the one a header's quoted includes of its siblings need. Compared
        # after resolving both sides, or a relatively-passed `-I` naming the same
        # directory is emitted twice.
        unit_path = getattr(unit, "path", "") or ""
        if unit_path:
            parent = str(Path(unit_path).resolve().parent)
            if parent not in include_dirs:
                include_dirs = [*include_dirs, parent]

        lines.extend(f"--passC:{_cfg_path_flag('-I', d)}" for d in include_dirs)
        lines.extend(f'--passC:"-D{d}"' for d in self._as_list(options.extra_context.get("defines")))

        # Resolved for the same reason include_dirs are: a relative -L resolves
        # against the linker's working directory, not the package's.
        library_dirs = [str(Path(d).resolve()) for d in self._as_list(options.get_option("library_dirs"))]
        libraries = self._as_list(options.get_option("library"))
        lines.extend(f"--passL:{_cfg_path_flag('-L', d)}" for d in library_dirs)
        lines.extend(f'--passL:"-l{lib}"' for lib in libraries)
        if not libraries:
            lines.append(
                "# No native library to link: pass --writer-opt nim:library=<name> "
                "(and nim:library_dirs=<dir>) to add the -l/-L flags this package needs."
            )

        return "\n".join(lines) + "\n"

    def _probe_param(self, type_expr: TypeExpr) -> str:
        """Render ``type_expr`` as the pointer a link probe takes it by.

        A probe takes every argument by pointer so that a call site can name the
        probe with ``nil`` whatever the argument type is. Without that, probing an
        entry point would mean inventing a value of each parameter's type -- and a
        C++ class with no default constructor has no value to invent.
        """
        formatted = self._format_type(type_expr, in_param=True)
        # `ptr (var T)` is not a type; `T` is, and `x[]` on a `ptr T` is the lvalue
        # a `var T` parameter binds to.
        formatted = formatted.removeprefix("var ")
        return f"ptr {formatted}"

    def _link_probe(self, name: str, index: int, self_type: str | None, params: list[Parameter], returns: str) -> str:
        """Render one probe proc that references ``name`` so the linker must resolve it."""
        probe_params: list[str] = []
        call_args: list[str] = []
        if self_type is not None:
            probe_params.append(f"self: ptr {self_type}")
            call_args.append("self[]")
        for i, p in enumerate(params):
            arg = f"a{i}"
            probe_params.append(f"{arg}: {self._probe_param(p.type)}")
            call_args.append(f"{arg}[]")
        call = f"{name}({', '.join(call_args)})"
        body = f"discard {call}" if returns != "void" else call
        return f"proc hkLinkProbe{index}({', '.join(probe_params)}) =\n  {body}"

    @staticmethod
    def _is_probeable(f: Function) -> bool:
        """Whether a link probe may reference this member.

        A probe referencing a non-public member fails to **compile**, not to link,
        which takes the whole generated package down. The writer declares such a
        member regardless of access, so the collector declines instead. An access
        the backend left unset is public, which is what C members are.

        A template is skipped for a different reason: a generic emits no symbol
        until it is instantiated, so probing one establishes nothing.
        """
        if (f.access or "public") != "public":
            return False
        return not (f.template_params or f.name.startswith("operator"))

    def _collect_link_probes(self, unit: SourceUnit | Header) -> tuple[list[str], list[str], list[str]]:
        """Return the probe definitions, their call sites, and the complete C++ types."""
        probes: list[str] = []
        calls: list[str] = []
        complete_types: list[str] = []

        def add(name: str, self_type: str | None, params: list[Parameter], returns: str) -> None:
            index = len(probes)
            probes.append(self._link_probe(name, index, self_type, params, returns))
            args = ["nil"] * (len(params) + (1 if self_type is not None else 0))
            calls.append(f"hkLinkProbe{index}({', '.join(args)})")

        for decl in unit.declarations:
            if isinstance(decl, Function) and not decl.template_params and not decl.is_variadic and decl.name:
                add(_escape_ident(decl.name), None, decl.parameters, self._format_type(decl.return_type))
            elif isinstance(decl, Struct) and _struct_requires_cpp(decl) and decl.name and not decl.template_params:
                struct_type = _escape_ident(decl.name)
                complete_types.append(struct_type)
                for m in decl.methods:
                    if not self._is_probeable(m):
                        continue
                    self_type = None if m.is_static else struct_type
                    add(_escape_ident(m.name), self_type, m.parameters, self._format_type(m.return_type))
                for ctor in decl.constructors:
                    if not self._is_probeable(ctor):
                        continue
                    add(f"construct{decl.name}", None, ctor.parameters, struct_type)

        return probes, calls, complete_types

    def _build_cpp_tripwire(self, pkg: str, unit: SourceUnit | Header) -> str:
        """Render the tripwire for a package whose bindings use ``importcpp``.

        ``loadLib``/``symAddr`` cannot serve a C++ target. It looks for an
        unmangled name in a shared object, and an ``importcpp`` binding has neither:
        the C++ name is mangled, and a header-only or statically-linked library has
        no shared object at all. Such a tripwire fails for a reason unrelated to
        whether the bindings work.

        What this tripwire establishes instead is a build-time property, and the
        build is where it fails: the bindings compile under the C++ backend against
        the real header, every non-generic entry point resolves at link time, and
        every bound C++ class is a complete type rather than a forward declaration.
        It does not establish that a shared library is findable at run time -- a
        statically linked package has none to find.
        """
        probes, calls, complete_types = self._collect_link_probes(unit)

        if not probes and not complete_types:
            return self._build_inconclusive_tripwire(pkg)

        probe_block = "\n\n".join(probes) if probes else ""
        call_block = "\n".join(f"    {call}" for call in calls) if calls else "    discard"
        # No fallback assertion when a unit binds entry points but no complete class:
        # the probes carry the claim, and `check declared(pkg)` is true by
        # construction, so adding it would weaken the suite rather than strengthen it.
        size_checks = "\n".join(f"    check sizeof({t}) > 0" for t in complete_types)
        title = "every bound entry point compiles and links against"
        if not probes:
            title = "every bound class is a complete type in"

        # The template is dedented before the blocks go in: dedent() strips the
        # common prefix of every line it is given, and an interpolated block that
        # starts at column zero would leave it with nothing to strip.
        template = textwrap.dedent("""\
            # Tripwire for a C++ target.
            #
            # The assertion this file makes is its own build. Compiling it proves the
            # bindings are valid C++ against the real header; linking it proves every
            # bound entry point resolves against the real native library. A missing
            # library fails the link with undefined symbols and the test never runs.
            #
            # The probes below are never executed -- `hkRunLinkProbes` is false. They
            # exist so the C++ compiler must emit a reference to each entry point and
            # the linker must resolve it. Every probe argument is a pointer so a call
            # site can pass `nil` without inventing a value of a type that may have no
            # default constructor.
            import std/unittest
            import {pkg}

            var hkRunLinkProbes = false

            {probe_block}

            proc hkForceLinkage() =
              if hkRunLinkProbes:
            {call_block}

            suite "Tripwire Compile & Link Verification":
              test "{title} '{pkg}'":
                hkForceLinkage()
            {size_checks}
            """)
        return template.format(
            pkg=pkg,
            probe_block=probe_block,
            call_block=call_block,
            size_checks=size_checks,
            title=title,
        )

    @staticmethod
    def _build_inconclusive_tripwire(pkg: str) -> str:
        """Render a tripwire for a unit that offers nothing a tripwire can check.

        A unit binding only templates has no symbol to link -- a generic emits none
        until it is instantiated -- and no complete class to size. Reporting
        *skipped* is the only honest outcome: a pass here would assert a linkage
        nothing checked. The reason is echoed so the reader is not left guessing
        why the suite is quiet.
        """
        return textwrap.dedent(f"""\
            # Tripwire for a C++ target that binds no linkable entry point.
            #
            # Every binding in this package is a template or has no complete class
            # behind it. A generic emits no symbol until it is instantiated, so there
            # is nothing here whose linkage a tripwire could establish. This file
            # therefore reports skipped rather than passing: a pass would say the
            # entry points link, and nothing here has checked that.
            #
            # NOTE: this file exits 0. std/unittest counts failures only, and a skip
            # is not one, so a CI step reading the exit status of this tripwire alone
            # learns nothing about linkage. Do not treat it as link verification.
            #
            # Instantiate the generics you use in a test of your own, and that test
            # will establish the linkage this one cannot.
            import std/unittest
            import {pkg}

            suite "Tripwire Compile & Link Verification":
              test "linkage of '{pkg}' is not established by this tripwire":
                echo "TRIPWIRE INCONCLUSIVE: no non-generic entry point and no complete class is bound by '{pkg}'"
                echo "TRIPWIRE INCONCLUSIVE: nothing here establishes that the native library links"
                skip()
            """)

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
        files.append(OutputFile(path="nim.cfg", content=self._build_nim_cfg(unit, options)))

        # 5. Tests
        if test_type in ("tripwire", "both") and unit_requires_cpp(unit):
            files.append(OutputFile(path="tests/test_tripwire.nim", content=self._build_cpp_tripwire(pkg, unit)))
        elif test_type in ("tripwire", "both"):
            stub_lines = []
            for fn in fn_names:
                stub_lines.append(
                    f"    if lib.symAddr(\"{fn}\") == nil:\n      checkpoint \"Entry point '{fn}' missing from native library '{pkg}'\"\n      fail()"
                )
            stubs = "\n".join(stub_lines) if stub_lines else f"    checkpoint \"Verified native library '{pkg}' loads\""

            tripwire = render_block_template(
                f"""\
                import std/[unittest, dynlib]
                import {pkg}

                suite "Tripwire Symbol & ABI Verification":
                  test "verify foreign library entrypoints exist and link":
                    let lib = loadLib("{pkg}")
                    if lib == nil:
                      checkpoint "Native dynamic library '{pkg}' not found in system library path"
                      fail()
                {DEDENT_BLOCK}
            """,
                stubs,
            )
            files.append(OutputFile(path="tests/test_tripwire.nim", content=tripwire))

        if test_type in ("unit", "both"):
            decl_checks = (
                "\n".join(f"    check declared({fn})" for fn in fn_names[:10])
                if fn_names
                else f"    check declared({pkg})"
            )
            unit_test = render_block_template(
                f"""\
                import std/unittest
                import {pkg}

                suite "{pkg} Unit Tests":
                  test "module exports expected declarations":
                {DEDENT_BLOCK}
            """,
                decl_checks,
            )
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
