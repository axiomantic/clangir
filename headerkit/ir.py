"""Intermediate Representation (IR) for C/C++ declarations.

This module defines the IR that all parser backends produce. Writers
consume this IR to generate output in various formats (CFFI cdef, Cython pxd, etc.).

Design Principles
-----------------
* **Parser-agnostic**: Works with pycparser, libclang, tree-sitter, etc.
* **Intuitive composition**: Types compose naturally
  (e.g., ``const char*`` becomes ``Pointer(CType("char", ["const"]))``)
* **Complete coverage**: Represents everything C/C++ headers can express

Type Hierarchy
--------------
Type expressions form a recursive structure:

* :class:`CType` - Base C type (``int``, ``unsigned long``, etc.)
* :class:`Pointer` - Pointer to another type (``int*``, ``char**``)
* :class:`Array` - Fixed or flexible array (``int[10]``, ``char[]``)
* :class:`FunctionPointer` - Function pointer type

Declaration Types
-----------------
* :class:`Enum` - Enumeration with named constants
* :class:`Struct` - Struct or union with fields
* :class:`Function` - Function declaration
* :class:`Typedef` - Type alias
* :class:`Variable` - Global variable
* :class:`Constant` - Compile-time constant or macro

Example
-------
Parse a header and inspect declarations::

    from headerkit.backends import get_backend
    from headerkit.ir import Struct, Function

    backend = get_backend()
    header = backend.parse("struct Point { int x; int y; };", "test.h")

    for decl in header.declarations:
        if isinstance(decl, Struct):
            print(f"Found struct: {decl.name}")
"""

from __future__ import (
    annotations,
)

from dataclasses import (
    dataclass,
    field,
    replace,
)
from typing import (
    Protocol,
    Union,
    runtime_checkable,
)

# =============================================================================
# Source Location
# =============================================================================


@dataclass
class SourceLocation:
    """Location in source file for error reporting and filtering.

    Used to track where declarations originated, enabling:

    * Better error messages during parsing
    * Filtering declarations by file (e.g., exclude system headers)
    * Source mapping for debugging

    :param file: Path to the source file.
    :param line: Line number (1-indexed).
    :param column: Column number (1-indexed), or None if unknown.

    Example
    -------
    ::

        loc = SourceLocation("myheader.h", 42, 5)
        print(f"Declaration at {loc.file}:{loc.line}")
    """

    file: str
    line: int
    column: int | None = None


# =============================================================================
# Type Representations
# =============================================================================


@dataclass
class CType:
    """A C type expression representing a base type with optional qualifiers.

    This is the fundamental building block for all type representations.
    Qualifiers like ``const``, ``volatile``, ``unsigned`` are stored separately
    from the type name for easier manipulation.

    :param name: The base type name (e.g., ``"int"``, ``"long"``, ``"char"``).
    :param qualifiers: Type qualifiers (e.g., ``["const"]``, ``["unsigned"]``).

    Examples
    --------
    Simple types::

        int_type = CType("int")
        unsigned_long = CType("long", ["unsigned"])
        const_int = CType("int", ["const"])

    Composite types with pointers::

        from headerkit.ir import Pointer

        # const char*
        const_char_ptr = Pointer(CType("char", ["const"]))
    """

    name: str
    qualifiers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.qualifiers:
            return f"{' '.join(self.qualifiers)} {self.name}"
        return self.name


@dataclass
class Pointer:
    """Pointer to another type.

    Represents pointer types with optional qualifiers. Pointers can be
    nested to represent multi-level indirection (e.g., ``char**``).

    :param pointee: The type being pointed to.
    :param qualifiers: Qualifiers on the pointer itself (e.g., ``["const"]``
        for a const pointer, not a pointer to const).

    Examples
    --------
    Basic pointer::

        int_ptr = Pointer(CType("int"))  # int*

    Pointer to const::

        const_char_ptr = Pointer(CType("char", ["const"]))  # const char*

    Double pointer::

        char_ptr_ptr = Pointer(Pointer(CType("char")))  # char**

    Const pointer (pointer itself is const)::

        const_ptr = Pointer(CType("int"), ["const"])  # int* const
    """

    pointee: TypeExpr
    qualifiers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        # Qualifiers on the pointer itself follow the ``*`` in C spelling;
        # qualifiers on the pointee are rendered by the pointee's own __str__.
        quals = f" {' '.join(self.qualifiers)}" if self.qualifiers else ""
        return f"{self.pointee}*{quals}"


@dataclass
class Reference:
    """C++ reference to another type.

    Represents lvalue references (``T&``) and rvalue references (``T&&``).

    :param target: The type being referenced.
    :param is_rvalue: True for rvalue reference (``&&``), False for lvalue reference (``&``).
    :param qualifiers: Qualifiers on the reference.
    """

    target: TypeExpr
    is_rvalue: bool = False
    qualifiers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        quals = f"{' '.join(self.qualifiers)} " if self.qualifiers else ""
        ref = "&&" if self.is_rvalue else "&"
        return f"{quals}{self.target}{ref}"


@dataclass
class Array:
    """Fixed-size or flexible array type.

    Represents C array types, which can have a fixed numeric size,
    a symbolic size (macro or constant), or be flexible (incomplete).

    :param element_type: The type of array elements.
    :param size: Array size - an integer for fixed size, a string for
        symbolic/expression size (e.g., ``"MAX_SIZE"``), or None for
        flexible/incomplete arrays.

    Examples
    --------
    Fixed-size array::

        int_arr = Array(CType("int"), 10)

    Flexible array (incomplete)::

        flex_arr = Array(CType("char"), None)

    Symbolic size::

        buf = Array(CType("char"), "BUFFER_SIZE")

    Multi-dimensional array::

        matrix = Array(Array(CType("int"), 3), 3)
    """

    element_type: TypeExpr
    size: Union[int, str] | None = None  # None = flexible, str = expression

    def __str__(self) -> str:
        size_str = str(self.size) if self.size is not None else ""
        return f"{self.element_type}[{size_str}]"


@dataclass
class Parameter:
    """Function parameter declaration.

    Represents a single parameter in a function signature. Parameters
    may be named or anonymous (common in prototypes).

    :param name: Parameter name, or None for anonymous parameters.
    :param type: The parameter's type expression.
    :param default_value: Default argument expression string, or None if none.

    Examples
    --------
    Named parameter::

        x_param = Parameter("x", CType("int"))  # int x

    With default value::

        count_param = Parameter("count", CType("int"), default_value="0")  # int count = 0

    Anonymous parameter::

        anon = Parameter(None, Pointer(CType("void")))  # void*

    Complex type::

        callback = Parameter("fn", FunctionPointer(CType("void"), []))
    """

    name: str | None
    type: TypeExpr
    default_value: str | None = None

    def __str__(self) -> str:
        default_str = f" = {self.default_value}" if self.default_value is not None else ""
        if self.name:
            return f"{self.type} {self.name}{default_str}"
        return f"{self.type}{default_str}"


@dataclass
class FunctionPointer:
    """Function pointer type.

    Represents a pointer to a function with a specific signature.
    Used for callbacks, vtables, and function tables.

    :param return_type: The function's return type.
    :param parameters: List of function parameters.
    :param is_variadic: True if the function accepts variable arguments
        (ends with ``...``).
    :param calling_convention: The calling convention if non-default
        (e.g., ``"stdcall"``, ``"cdecl"``, ``"fastcall"``). None for
        the platform default calling convention.

    Examples
    --------
    Simple function pointer::

        void_fn = FunctionPointer(CType("int"), [])  # int (*)(void)

    With parameters::

        callback = FunctionPointer(
            CType("void"),
            [Parameter("data", Pointer(CType("void")))]
        )  # void (*)(void* data)

    Variadic function pointer::

        printf_fn = FunctionPointer(
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True
        )  # int (*)(const char* fmt, ...)
    """

    return_type: TypeExpr
    parameters: list[Parameter] = field(default_factory=list)
    is_variadic: bool = False
    calling_convention: str | None = None

    def __str__(self) -> str:
        params = ", ".join(str(p) for p in self.parameters)
        if self.is_variadic:
            params = f"{params}, ..." if params else "..."
        cc = f" __{self.calling_convention}__" if self.calling_convention else ""
        return f"{self.return_type} ({cc}*)({params})"


# Type alias for any type expression
TypeExpr = Union[CType, Pointer, Reference, Array, FunctionPointer]


# =============================================================================
# Declarations
# =============================================================================


@dataclass
class BaseSpecifier:
    """C++ base class specifier in class inheritance.

    :param name: Name of the base class.
    :param access: Access specifier ("public", "protected", "private").
    :param is_virtual: True if virtually inherited.
    """

    name: str
    access: str = "public"
    is_virtual: bool = False

    def __str__(self) -> str:
        virt = "virtual " if self.is_virtual else ""
        return f"{self.access} {virt}{self.name}"


@dataclass
class Field:
    """Struct or union field declaration.

    Represents a single field within a struct or union definition.

    :param name: The field name.
    :param type: The field's type expression.
    :param bit_width: C bitfield width in bits, or None for non-bitfield
        fields. For example, ``uint32_t flags : 4`` has ``bit_width=4``.
    :param anonymous_struct: When this field is an anonymous nested
        struct or union, holds the :class:`Struct` IR node for the
        anonymous type. None for regular fields.
    :param access: Access specifier (``"public"``, ``"protected"``, ``"private"``),
        or None for C struct fields / default access.
    :param is_static: True if this is a static data member.
    :param is_padding: True for an unnamed bitfield (``int : 3;``), which
        C17 6.7.2.1p13 gives no member name. Such an entry is not an
        accessible member, but it does occupy bits, so a consumer that
        reconstructs layout (the ctypes writer) needs it. A ``bit_width``
        of 0 is the zero-width form (``int : 0;``), which aligns the next
        field to a fresh storage unit rather than reserving bits.
        Writers that emit C source must skip these: the C compiler lays
        the record out from the original declaration.

    Examples
    --------
    Simple field::

        x_field = Field("x", CType("int"))  # int x

    Pointer field::

        data = Field("data", Pointer(CType("void")))  # void* data

    Array field::

        buffer = Field("buffer", Array(CType("char"), 256))  # char buffer[256]

    Bitfield::

        flags = Field("flags", CType("uint32_t"), bit_width=4)  # uint32_t flags : 4

    Anonymous nested struct::

        inner = Struct(None, [Field("x", CType("int"))], is_union=False)
        field = Field("pos", CType("void"), anonymous_struct=inner)

    Unnamed bitfield padding::

        pad = Field("", CType("unsigned int"), bit_width=3, is_padding=True)
    """

    name: str
    type: TypeExpr
    bit_width: int | None = None
    anonymous_struct: Struct | None = None
    is_anonymous_transparent: bool = False
    access: str | None = None
    is_static: bool = False
    is_padding: bool = False

    def __str__(self) -> str:
        if self.is_padding:
            return f"{self.type} : {self.bit_width}"
        base = f"{self.type} {self.name}"
        if self.bit_width is not None:
            base += f" : {self.bit_width}"
        return base


@dataclass
class EnumValue:
    """Single enumeration constant.

    Represents one named constant within an enum definition.

    :param name: The constant name.
    :param value: The constant's value - an integer for explicit values,
        a string for expressions (e.g., ``"FOO | BAR"``), or None
        for auto-incremented values.

    Examples
    --------
    Explicit value::

        red = EnumValue("RED", 0)

    Auto-increment (implicit value)::

        green = EnumValue("GREEN", None)  # follows previous value

    Expression value::

        mask = EnumValue("MASK", "FLAG_A | FLAG_B")
    """

    name: str
    value: Union[int, str] | None = None  # None = auto, str = expression

    def __str__(self) -> str:
        if self.value is not None:
            return f"{self.name} = {self.value}"
        return self.name


@dataclass
class Enum:
    """Enumeration declaration.

    Represents a C enum type with named constants. Enums may be
    named or anonymous (used in typedefs or inline).

    :param name: The enum tag name, or None for anonymous enums.
    :param values: List of enumeration constants.
    :param is_typedef: True if this enum came from a typedef declaration.
    :param namespace: Enclosing C++ namespace, or None at global scope. Part of
        the enum's identity: ``a::E`` and ``b::E`` are distinct declarations.
    :param location: Source location for error reporting.
    :param is_scoped: True for a C++ ``enum class``/``enum struct``. A scoped
        enumerator is a member of the tag, spelled ``E::X``, and is not
        introduced into the enclosing namespace as ``X``.
    :param cpp_name: Fully-qualified C++ spelling of the tag, when it is not
        derivable from ``namespace`` and ``name``. A member enum is hoisted to
        the top level and loses its enclosing record, so ``class C { enum M; }``
        records ``C::M`` here; ``None`` means ``namespace``-plus-``name`` is the
        whole spelling.
    :param underlying_type: The integer type the enum is represented as, as a C
        type spelling such as ``"unsigned char"`` or ``"long long"``, or None
        when the parser did not report one.

        C leaves this implementation-defined, requiring only that it represent
        every enumerator, and both C++11 forms may fix it explicitly -- on a
        scoped enum (``enum class E : unsigned char``) *and* on an unscoped one
        (``enum E : unsigned long long``). It cannot be reconstructed from the
        enumerators: they constrain the width from below and say nothing about a
        type chosen to be wider, and an opaque declaration such as
        ``enum Fwd : long long;`` has no enumerators at all. A consumer that
        must know the width -- any binding generator, since this is the size and
        signedness of every value crossing the ABI -- has to be told, so it is
        recorded here rather than guessed at downstream.

        ``None`` means *the header declared none*, and is only trustworthy when
        ``underlying_type_known`` is True. See that field.
    :param underlying_type_known: Whether ``underlying_type`` is an observation
        or an absence of one.

        A parser can fail to see a clause that is there. tree-sitter's **C**
        grammar has no production for ``enum E : long long`` -- C23 standardised
        it and both major compilers accepted it as an extension for years -- so
        it parses as an ``ERROR`` node with no ``base`` field, which is
        indistinguishable from a plain ``enum E`` if only ``underlying_type`` is
        consulted. A consumer that reads the resulting ``None`` as "declared
        none" falls back to guessing the width from the enumerators, and
        ``enum E : long long { A = 0 };`` becomes a four-byte type where the
        compiler laid out eight.

        False means the parser found something it could not represent, so the
        width is unknown rather than absent, and a consumer must refuse rather
        than infer. It defaults to True because "declared none" is the ordinary
        case and every parser reports *that* reliably.

    Examples
    --------
    Named enum::

        color = Enum("Color", [
            EnumValue("RED", 0),
            EnumValue("GREEN", 1),
            EnumValue("BLUE", 2),
        ])

    Anonymous enum (typically used with typedef)::

        anon = Enum(None, [EnumValue("FLAG_A", 1), EnumValue("FLAG_B", 2)])
    """

    name: str | None
    values: list[EnumValue] = field(default_factory=list)
    is_typedef: bool = False
    namespace: str | None = None
    location: SourceLocation | None = None
    is_scoped: bool = False
    cpp_name: str | None = None
    underlying_type: str | None = None
    underlying_type_known: bool = True

    @property
    def qualified_name(self) -> str | None:
        """The tag's full C++ spelling, or None if it is anonymous."""
        if self.cpp_name:
            return self.cpp_name
        if not self.name:
            return None
        return f"{self.namespace}::{self.name}" if self.namespace else self.name

    def __str__(self) -> str:
        name_str = self.name or "(anonymous)"
        return f"enum {name_str}"


@dataclass
class Struct:
    """Struct or union declaration.

    Represents a C struct or union type definition. Both use the same
    IR class with ``is_union`` distinguishing between them.

    :param name: The struct/union tag name, or None for anonymous types.
    :param fields: List of member fields.
    :param methods: List of methods (for C++ classes only).
    :param is_union: True for unions, False for structs.
    :param is_cppclass: True for C++ classes (uses ``cppclass`` in Cython).
    :param is_typedef: True if this came from a typedef declaration.
    :param is_packed: True if the struct has ``__attribute__((packed))``,
        which disables padding and alignment. Affects memory layout.
    :param nested_records: Records defined inside this record's body. A C++
        nested class stays here rather than being lifted to the top level,
        because its name is only meaningful when qualified by the enclosing
        scope.
    :param location: Source location for error reporting.

    Examples
    --------
    Simple struct::

        point = Struct("Point", [
            Field("x", CType("int")),
            Field("y", CType("int")),
        ])

    Union::

        data = Struct("Data", [
            Field("i", CType("int")),
            Field("f", CType("float")),
        ], is_union=True)

    C++ class with method::

        widget = Struct("Widget", [
            Field("width", CType("int")),
        ], methods=[
            Function("resize", CType("void"), [
                Parameter("w", CType("int")),
                Parameter("h", CType("int")),
            ])
        ], is_cppclass=True)

    Anonymous struct::

        anon = Struct(None, [Field("value", CType("int"))])
    """

    name: str | None
    fields: list[Field] = field(default_factory=list)
    methods: list[Function] = field(default_factory=list)
    is_union: bool = False
    is_cppclass: bool = False
    is_typedef: bool = False
    is_packed: bool = False
    namespace: str | None = None
    template_params: list[str] = field(default_factory=list)
    cpp_name: str | None = None
    notes: list[str] = field(default_factory=list)
    inner_typedefs: dict[str, str] = field(default_factory=dict)  # name -> underlying_type
    nested_records: list[Struct] = field(default_factory=list)
    bases: list[BaseSpecifier] = field(default_factory=list)
    is_abstract: bool = False
    constructors: list[Function] = field(default_factory=list)
    destructor: Function | None = None
    conversions: list[Function] = field(default_factory=list)
    vtable_entries: list[Function] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    is_deprecated: bool = False
    alignment: int | None = None
    location: SourceLocation | None = None

    def __str__(self) -> str:
        if self.is_cppclass:
            kind = "cppclass"
        elif self.is_union:
            kind = "union"
        else:
            kind = "struct"
        name_str = self.name or "(anonymous)"
        packed_str = " __attribute__((packed))" if self.is_packed else ""
        return f"{kind} {name_str}{packed_str}"


@dataclass
class Function:
    """Function declaration.

    Represents a C function prototype or declaration. Does not include
    the function body (declarations only).

    :param name: The function name.
    :param return_type: The function's return type.
    :param parameters: List of function parameters.
    :param is_variadic: True if the function accepts variable arguments.
    :param calling_convention: The calling convention if non-default
        (e.g., ``"stdcall"``, ``"cdecl"``, ``"fastcall"``). None for
        the platform default calling convention.
    :param template_params: Template parameter names for C++ function templates.
    :param location: Source location for error reporting.

    Examples
    --------
    Simple function::

        exit_fn = Function("exit", CType("void"), [
            Parameter("status", CType("int"))
        ])

    With return value::

        strlen_fn = Function("strlen", CType("size_t"), [
            Parameter("s", Pointer(CType("char", ["const"])))
        ])

    Variadic function::

        printf_fn = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True
        )
    """

    name: str
    return_type: TypeExpr
    parameters: list[Parameter] = field(default_factory=list)
    is_variadic: bool = False
    calling_convention: str | None = None
    namespace: str | None = None
    template_params: list[str] = field(default_factory=list)
    is_static: bool = False
    is_const: bool = False
    is_virtual: bool = False
    is_pure_virtual: bool = False
    is_explicit: bool = False
    access: str | None = None
    is_deleted: bool = False
    is_defaulted: bool = False
    is_noexcept: bool = False
    is_inline: bool = False
    body: str | None = None
    attributes: list[str] = field(default_factory=list)
    is_deprecated: bool = False
    location: SourceLocation | None = None

    def __str__(self) -> str:
        params = ", ".join(str(p) for p in self.parameters)
        if self.is_variadic:
            params = f"{params}, ..." if params else "..."
        cc = f" __{self.calling_convention}__" if self.calling_convention else ""
        t_params = f"<{', '.join(self.template_params)}>" if self.template_params else ""
        return f"{self.return_type}{cc} {self.name}{t_params}({params})"


@dataclass
class Typedef:
    """Type alias declaration.

    Represents a C typedef that creates an alias for another type.
    Common patterns include aliasing primitives, struct tags, and
    function pointer types.

    :param name: The new type name being defined.
    :param underlying_type: The type being aliased.
    :param location: Source location for error reporting.

    Examples
    --------
    Simple alias::

        uint_alias = Typedef("uint", CType("unsigned int"))

    Struct alias::

        point_alias = Typedef("Point", CType("struct Point"))

    Function pointer alias::

        cb_alias = Typedef(
            "Callback",
            Pointer(FunctionPointer(CType("void"), [Parameter("code", CType("int"))]))
        )
    """

    name: str
    underlying_type: TypeExpr
    namespace: str | None = None
    attributes: list[str] = field(default_factory=list)
    is_deprecated: bool = False
    location: SourceLocation | None = None

    def __str__(self) -> str:
        return f"typedef {self.underlying_type} {self.name}"


@dataclass
class Variable:
    """Global or extern variable declaration.

    Represents variable declarations at file scope, including ``extern``
    variables and file-scope data definitions.

    :param name: The variable identifier.
    :param type: The variable's type expression.
    :param location: Source location for error reporting.

    Examples
    --------
    Extern variable::

        errno_var = Variable("errno", CType("int"))

    Const string::

        version = Variable("version", Pointer(CType("char", ["const"])))

    Array variable::

        lookup_table = Variable("table", Array(CType("int"), 256))
    """

    name: str
    type: TypeExpr
    namespace: str | None = None
    attributes: list[str] = field(default_factory=list)
    is_deprecated: bool = False
    alignment: int | None = None
    location: SourceLocation | None = None

    def __str__(self) -> str:
        return f"{self.type} {self.name}"


@dataclass
class Constant:
    """Compile-time constant declaration.

    Represents ``#define`` macros with constant values or ``const``
    variable declarations. Only backends that support macro extraction
    (e.g., libclang) can populate macro constants.

    :param name: The constant name.
    :param value: The constant's value - an integer, float, or string
        expression. None if the value cannot be determined.
    :param evaluated_value: The evaluated numeric or string value if evaluable.
    :param raw_expression: The un-evaluated macro or constant expression string.
    :param type: For typed constants (``const int``), the C type.
        None for macros.
    :param is_macro: True if this is a ``#define`` macro, False for
        ``const`` declarations.
    :param location: Source location for error reporting.

    Examples
    --------
    Numeric macro::

        size = Constant("SIZE", 100, is_macro=True)

    Expression macro::

        mask = Constant("MASK", 16, raw_expression="1 << 4", evaluated_value=16, is_macro=True)

    Typed const::

        max_val = Constant("MAX_VALUE", 255, type=CType("int"))

    String macro::

        version = Constant("VERSION", '"1.0.0"', is_macro=True)
    """

    name: str
    value: Union[int, float, str] | None = None  # None if complex/unknown
    evaluated_value: Union[int, float, str] | None = None
    raw_expression: str | None = None
    type: CType | None = None
    is_macro: bool = False
    location: SourceLocation | None = None

    def __str__(self) -> str:
        if self.is_macro:
            return f"#define {self.name} {self.value}"
        if self.type is not None:
            return f"const {self.type} {self.name} = {self.value}"
        return f"const {self.name} = {self.value}"


# Type alias for any declaration
Declaration = Union[Enum, Struct, Function, Typedef, Variable, Constant]


# =============================================================================
# Input Specification & SourceUnit Container
# =============================================================================


@dataclass(frozen=True)
class InputSpec:
    """Specification of an input source unit, its language, and classification.

    :param path: Path to the source file or virtual file.
    :param language: Language identifier (e.g. 'c', 'cpp', 'nim', 'rust', 'zig').
    :param classification: Classification ('header', 'source', 'interface', 'idl').
    :param content: Optional raw string content.
    """

    path: str
    language: str = "c"
    classification: str = "header"
    content: str | None = None

    @classmethod
    def from_path(
        cls,
        path: str,
        language: str | None = None,
        classification: str | None = None,
        content: str | None = None,
    ) -> InputSpec:
        """Infer language and classification from a file path extension."""
        deduced_lang = language
        deduced_class = classification

        if deduced_lang is None or deduced_class is None:
            lower = path.lower()
            if lower.endswith((".h",)):
                deduced_lang = deduced_lang or "c"
                deduced_class = deduced_class or "header"
            elif lower.endswith((".hpp", ".hxx", ".hh", ".h++")):
                deduced_lang = deduced_lang or "cpp"
                deduced_class = deduced_class or "header"
            elif lower.endswith((".c",)):
                deduced_lang = deduced_lang or "c"
                deduced_class = deduced_class or "source"
            elif lower.endswith((".cpp", ".cxx", ".cc", ".c++")):
                deduced_lang = deduced_lang or "cpp"
                deduced_class = deduced_class or "source"
            elif lower.endswith((".nim",)):
                deduced_lang = deduced_lang or "nim"
                deduced_class = deduced_class or "source"
            elif lower.endswith((".rs",)):
                deduced_lang = deduced_lang or "rust"
                deduced_class = deduced_class or "interface"
            elif lower.endswith((".zig",)):
                deduced_lang = deduced_lang or "zig"
                deduced_class = deduced_class or "source"
            elif lower.endswith((".idl",)):
                deduced_lang = deduced_lang or "idl"
                deduced_class = deduced_class or "interface"
            else:
                deduced_lang = deduced_lang or "c"
                deduced_class = deduced_class or "header"

        return cls(
            path=path,
            language=deduced_lang,
            classification=deduced_class,
            content=content,
        )


@dataclass
class SourceUnit:
    """Container for a parsed source or interface unit.

    This is the top-level result returned by all parser backends.
    It contains the file path, extracted declarations, and input metadata.

    :param path: Path to the original source file.
    :param declarations: List of extracted declarations (structs, functions, etc.).
    :param included_headers: Set of header file basenames included by this unit
                             (populated by libclang backend only).
    :param language: Language identifier (e.g. 'c', 'cpp', 'nim', 'rust').
    :param classification: Source classification (e.g. 'header', 'source', 'interface').

    Example
    -------
    ::

        from headerkit.backends import get_backend
        from headerkit.ir import Struct, Function, SourceUnit

        backend = get_backend()
        unit = backend.parse(code, "myheader.h")

        print(f"Parsed {len(unit.declarations)} declarations from {unit.path}")

        for decl in unit.declarations:
            if isinstance(decl, Function):
                print(f"  Function: {decl.name}")
    """

    path: str
    declarations: list[Declaration] = field(default_factory=list)
    included_headers: set[str] = field(default_factory=set)
    language: str = "c"
    classification: str = "header"

    def __str__(self) -> str:
        return f"SourceUnit({self.path}, {len(self.declarations)} declarations)"


# Backward-compatibility alias during IR evolution
Header = SourceUnit


def strip_padding_fields(unit: SourceUnit) -> SourceUnit:
    """Return a copy of ``unit`` with every unnamed-bitfield padding Field removed.

    Padding is layout information, not API. A writer that emits C source (Cython,
    cffi, cshim, Nim, Lua, Mojo) hands the record back to a C compiler, which
    recomputes the layout from the original declaration; a padding entry in that
    output would be a spurious member. Only a writer that reconstructs the layout
    itself -- ctypes -- consumes them.

    Filtering here rather than at each writer's field loop keeps the rule in one
    place: a writer cannot forget to apply it, and a new writer inherits the safe
    default.
    """

    def _struct(st: Struct) -> Struct:
        return replace(
            st,
            fields=[
                replace(f, anonymous_struct=_struct(f.anonymous_struct) if f.anonymous_struct else None)
                for f in st.fields
                if not f.is_padding
            ],
            nested_records=[_struct(n) for n in st.nested_records],
        )

    return replace(unit, declarations=[_struct(d) if isinstance(d, Struct) else d for d in unit.declarations])


# =============================================================================
# Parser Backend Protocol
# =============================================================================


@runtime_checkable
class ParserBackend(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol defining the interface for parser backends.

    All parser backends must implement this protocol to be usable with headerkit.
    Backends are responsible for translating from their native AST format
    (pycparser, libclang, etc.) to the common :class:`Header` IR format.

    Available Backends
    ------------------
    * ``libclang`` - LLVM clang-based parser with C++ support

    Example
    -------
    ::

        from headerkit.backends import get_backend

        # Get default backend
        backend = get_backend()

        # Get specific backend
        libclang = get_backend("libclang")

        # Parse code
        header = backend.parse("int foo(void);", "test.h")
    """

    # pylint: disable=unnecessary-ellipsis

    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
        allowlist: list[str] | None = None,
        denylist: list[str] | None = None,
    ) -> Header:
        """Parse C/C++ code and return the IR representation.

        Traversal and filtering
        -----------------------
        ``recursive_includes`` governs *traversal*: descending into each
        non-system included header and merging what it declares.
        ``project_prefixes`` decides which paths count as project rather than
        system headers, and ``max_depth`` (with a backend-internal cycle guard)
        bounds the descent.

        ``allowlist`` and ``denylist`` govern *filtering*, and they narrow the
        merged result in **both** traversal modes:

        * ``recursive_includes=True`` with no ``allowlist`` returns declarations
          from every non-system included header.
        * ``recursive_includes=True`` with an ``allowlist`` returns the main
          file's declarations plus those of the named files, and nothing else.
        * ``recursive_includes=False`` with no ``allowlist`` returns the main
          file's declarations alone.
        * **Deny wins over allow.** A file named by both lists is excluded.
        * The main file is never denied; a denylist governs included files only.
        * A list entry matching nothing is not an error.

        :param code: Source code to parse.
        :param filename: Name of the source file. Used for error messages
            and ``#line`` directives. Does not need to exist on disk.
        :param include_dirs: Directories to search for ``#include`` files.
            Only used by backends that handle preprocessing.
        :param extra_args: Additional arguments for the preprocessor/compiler.
            Format is backend-specific.
        :param use_default_includes: If True, add system include directories.
        :param recursive_includes: If True, descend into included project headers
            and merge their declarations into the result. False parses only the
            main file, and is the only way to exclude included declarations
            entirely.
        :param max_depth: Maximum recursion depth for include processing.
        :param project_prefixes: Path prefixes to treat as project headers rather
            than system headers, so that they are descended into.
        :param allowlist: Files whose declarations are kept, alongside the main
            file's own. ``None`` keeps every non-system file reached by traversal.
            Absolute entries are used as-is; relative entries (including a bare
            basename) resolve against the parsed file's directory, then
            ``include_dirs``, then the current working directory. An entry
            containing ``*``, ``?`` or ``[`` is an :mod:`fnmatch` pattern whose
            directory part resolves the same way. Matching is on whole resolved
            paths, never substrings.
        :param denylist: Files whose declarations are dropped, using the same
            resolution and glob rules as ``allowlist``. Deny wins over allow. A
            denylist with no allowlist means "everything except these". The
            parsed file itself cannot be denied.
        :returns: Parsed header containing all extracted declarations.
        :raises RuntimeError: If parsing fails due to syntax errors.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable name of this backend (e.g., ``"pycparser"``)."""
        ...

    @property
    def supports_macros(self) -> bool:
        """Whether this backend can extract ``#define`` constants."""
        ...

    @property
    def supports_cpp(self) -> bool:
        """Whether this backend can parse C++ code."""
        ...

    @property
    def supported_languages(self) -> frozenset[str]:
        """Set of source languages supported by this backend (e.g., ``frozenset({"c", "cpp"})``)."""
        ...

    @property
    def supported_classifications(self) -> frozenset[str]:
        """Set of input classifications supported by this backend (e.g., ``frozenset({"header", "source"})``)."""
        ...
