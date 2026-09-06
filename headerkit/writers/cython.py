"""IR to Cython ``.pxd`` writer.

This module converts the headerkit IR (Intermediate Representation) to
Cython ``.pxd`` declaration files.

Features
--------
* Keyword escaping -- Python/Cython keywords get ``_`` suffix with C name alias
* stdint type imports -- Automatically adds ``cimport`` for ``libc.stdint`` types
* Full Cython syntax -- Supports all declaration types (structs, enums, functions, etc.)
* C++ support -- Namespaces, templates, cppclass, operator aliasing

Example
-------
::

    from headerkit.writers import get_writer

    writer = get_writer("cython")
    pxd_content = writer.write(header)

    with open("myheader.pxd", "w") as f:
        f.write(pxd_content)
"""

from __future__ import annotations

import re
import textwrap
from collections import defaultdict
from dataclasses import replace
from typing import ClassVar

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
    Reference,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers._cython_keywords import keywords
from headerkit.writers._cython_types import (
    LIBCPP_TYPES,
    get_cython_module_for_type,
    get_libcpp_module_for_type,
    get_stub_module_for_type,
)
from headerkit.writers.base import BaseWriter, WriterOption

# Type qualifiers that Cython doesn't support -- strip from output
UNSUPPORTED_TYPE_QUALIFIERS: set[str] = {
    "_Atomic",
    "__restrict",
    "_Noreturn",
    "__restrict__",
}

# C type names that need to be converted to Cython equivalents
C_TO_CYTHON_TYPE_MAP: dict[str, str] = {
    "_Bool": "bint",  # C99 boolean type -> Cython boolean integer
}

# Nested names that Cython cannot express on a bare template parameter but
# which have a conventional concrete spelling. Cython's own ``libcpp`` headers
# make exactly this substitution and record the deviation in a comment;
# headerkit does the same rather than dropping the declaration.
CONVENTIONAL_DEPENDENT_SPELLINGS: dict[str, str] = {
    "size_type": "size_t",
    "difference_type": "ptrdiff_t",
}

# Cython has no builtin named ``bool``: bare ``bool`` in a ``.pxd`` resolves to
# the *Python* bool object, so a C++ ``bool`` return value is stored straight
# into a ``PyObject*``. That cythonizes, compiles and links, then segfaults on
# call. ``libcpp/__init__.pxd`` declares ``ctypedef bint bool`` inside a
# ``cdef extern from *`` block, which keeps the C spelling ``bool`` while giving
# it integer semantics -- the correct lowering for C++ and, because a C header
# spelling ``bool`` must have included ``<stdbool.h>``, for C too.
_BOOL_CIMPORT_MODULE = "libcpp"


def _strip_namespace_qualifiers(name: str) -> str:
    """Remove every ``ns::`` qualifier from a C++ name.

    Cython has no ``::`` in type expressions; a namespace is carried by the
    enclosing ``cdef extern ... namespace`` block instead.

    Iterates to a fixpoint rather than until ``::`` disappears: a dependent name
    such as ``types::remove_reference<T>::type`` retains a ``>::`` that no
    ``\\w+::`` match can consume, so the latter condition never becomes false and
    the loop spins forever.
    """
    while True:
        stripped = re.sub(r"\b\w+::", "", name)
        if stripped == name:
            return name
        name = stripped


# Operator spellings Cython 3.3 parses in a declaration, determined by
# compiling one declaration per spelling rather than by reading its grammar.
# Membership is about what Cython will *parse*: ``++``, ``--`` and ``,`` are
# listed because their declarations are accepted, even though Python syntax
# offers no way to invoke them from a ``.pyx``. Emitting them is still correct
# -- the binding describes the header -- and omitting them would lose API.
# ``bool`` is the one conversion operator accepted; ``operator int``,
# ``operator double`` and every other conversion are rejected.
_SUPPORTED_OPERATORS: frozenset[str] = frozenset(
    {
        "+", "-", "*", "/", "%",
        "==", "!=", "<", ">", "<=", ">=",
        "[]", "()",
        "++", "--",
        "=", "!", "~",
        "<<", ">>", "&", "|", "^",
        ",",
        "bool",
    }
)  # fmt: skip


def _unsupported_operator_reason(name: str) -> str | None:
    """Return why Cython cannot declare *name*, or None when it can.

    :param name: A function name as the backend spells it, e.g. ``operator+=``.
        A name that is not an operator always returns None.
    """
    if not name.startswith("operator"):
        return None
    rest = name[len("operator") :]
    # ``operators`` is an ordinary method, not ``operator s``. The keyword ends
    # at a symbol or at whitespace before a conversion type name; anything else
    # means the identifier merely starts with the same eight letters.
    if not rest or rest[0].isalnum() or rest[0] == "_":
        return None
    spelling = rest.strip()
    if not spelling:
        return None
    # ``new[]``/``delete[]`` are rejected under the same message as their
    # scalar forms, which is what Cython's own error text reports.
    base = spelling[:-2] if spelling.endswith("[]") and spelling not in {"[]"} else spelling
    if base in _SUPPORTED_OPERATORS:
        return None
    if base in {"new", "delete"}:
        return f"Cython 3.3 rejects an allocation operator: \"Overloading operator '{base}' not yet supported.\""
    if base.isidentifier():
        return (
            f"Cython 3.3 supports only 'operator bool' as a conversion operator: "
            f"\"Overloading operator '{base}' not yet supported.\""
        )
    return f"Cython 3.3 cannot declare it: \"Overloading operator '{base}' not yet supported.\""


def _has_callable_member(struct: Struct) -> bool:
    """Return True when *struct* declares a member Cython can only place in a ``cppclass``.

    Methods, constructors, a destructor and conversion operators are all
    function declarations, and Cython parses a function declaration only inside
    a ``cppclass`` suite. Only a C++ record can carry one, so this never
    misclassifies a plain C struct.
    """
    return bool(struct.methods or struct.constructors or struct.destructor or struct.conversions)


class PxdWriter:
    """Writes IR to Cython ``.pxd`` format.

    Converts a :class:`~headerkit.ir.Header` containing parsed C/C++
    declarations into valid Cython ``.pxd`` syntax. Handles keyword
    escaping, stdint imports, topological sorting, circular dependency
    detection, C++ namespaces, templates, operator aliasing, and
    automatic cimport generation.

    :param header: The parsed header to convert.
    """

    default_output_pattern: str = "{dir}/{stem}.pxd"
    INDENT: str = "    "

    def __init__(self, header: Header, *, stub_cimport_prefix: str | None = "headerkit.stubs") -> None:
        self.header: Header = header
        self.stub_cimport_prefix: str | None = stub_cimport_prefix
        # Track declared struct/union/enum names for type reference cleanup
        self.known_structs: set[str] = set()
        self.known_unions: set[str] = set()
        self.known_enums: set[str] = set()
        self.known_typedefs: set[str] = set()
        # Non-union record declarations, and the subset of them named as a base
        # by another record. Both drive C++ base-class emission.
        self._record_names: set[str] = set()
        self._base_class_names: set[str] = set()
        # Namespace each record is declared in, and the order the extern blocks
        # are emitted in. A base declared in a block that has not been emitted
        # yet cannot be named.
        self._record_namespace: dict[str, str | None] = {}
        self._namespace_order: list[str | None] = []
        # (namespace, name) -> disambiguated Cython name, for a type name that
        # is declared in more than one namespace. See _collect_collisions.
        self._collision_names: dict[tuple[str | None, str], str] = {}
        self._colliding_bare_names: set[str] = set()
        # Namespace of the declaration currently being rendered, so that an
        # unqualified use of a colliding name resolves to its own namespace.
        self._current_namespace: str | None = None
        self._collect_collisions()
        self._collect_known_types()

        # Track used-but-undeclared struct/union types (need forward declarations)
        self.undeclared_structs: set[str] = set()
        self.undeclared_unions: set[str] = set()

        # Track incomplete structs (forward declarations with no fields)
        # Fields using these as value types must be skipped
        self.incomplete_structs: set[str] = set()
        self._collect_incomplete_types()

        # Cimport tracking using registries
        self.cython_cimports: dict[str, set[str]] = {}  # module -> types
        self.libcpp_cimports: dict[str, set[str]] = {}  # module -> types
        self.stub_cimports: dict[str, set[str]] = {}  # stub_module -> types

        # Current struct's inner typedefs for method return type resolution
        self._current_inner_typedefs: dict[str, str] = {}

        # Inner typedefs that cannot be represented in Cython (nested template types)
        self._unsupported_inner_typedefs: set[str] = set()

        # Nested member names of each declared class template, keyed by template
        # name. A dependent name is only emitted when its nested member is
        # actually declared on the qualifier template.
        self._template_members: dict[str, set[str]] = {}
        self._collect_template_members()

        # Template parameters in scope while the current declaration is written.
        # A dependent name rooted at one of these is unrepresentable in Cython.
        self._current_template_params: set[str] = set()

        # Dependent names encountered while formatting the current declaration
        # that Cython cannot express, as (spelling, reason) pairs.
        self._unrepresentable_dependents: list[tuple[str, str]] = []

        # Dependent names replaced by a conventional concrete spelling, as
        # (original, substitute) pairs, recorded so the output states it.
        self._dependent_deviations: list[tuple[str, str]] = []

        # Constructs encountered while rendering the current declaration that
        # Cython cannot express, as (phrase, reason) pairs. Each phrase
        # completes the sentence "<symbol> ..." so the diagnostic reads as prose.
        self._unrepresentable_constructs: list[tuple[str, str]] = []

        # Collect types from all declarations
        self._collect_cimport_types()

    # -----------------------------------------------------------------
    # Topological sorting
    # -----------------------------------------------------------------

    def _sort_declarations(self, decls: list[Declaration]) -> tuple[list[Declaration], set[int]]:
        """Sort declarations topologically to resolve forward references.

        Returns a tuple of (sorted declarations, set of indices that are
        in cycles).
        """
        # Build dependency graph
        dependencies: dict[int, set[int]] = defaultdict(set)
        decl_names: dict[str, list[int]] = defaultdict(list)

        for i, decl in enumerate(decls):
            if isinstance(decl, Struct | Typedef | Enum) and decl.name:
                decl_names[decl.name].append(i)

        # Build typedef->underlying_struct map
        typedef_to_struct: dict[str, str] = {}
        for decl in decls:
            if isinstance(decl, Typedef) and decl.name:
                underlying_names = self._extract_type_names(decl.underlying_type)
                for uname in underlying_names:
                    if uname in decl_names:
                        for idx in decl_names[uname]:
                            if isinstance(decls[idx], Struct):
                                typedef_to_struct[decl.name] = uname
                                break

        # Build dependency edges
        for i, decl in enumerate(decls):
            if isinstance(decl, Typedef):
                deps = self._extract_type_names(decl.underlying_type)
                for dep_name in deps:
                    if dep_name in decl_names:
                        for dep_idx in decl_names[dep_name]:
                            dep_decl = decls[dep_idx]
                            if isinstance(dep_decl, Struct | Enum | Typedef):
                                dependencies[i].add(dep_idx)

            elif isinstance(decl, Struct):
                for fld in decl.fields:
                    is_pointer = isinstance(fld.type, Pointer)
                    deps = self._extract_type_names(fld.type)
                    for dep_name in deps:
                        if dep_name in decl_names:
                            for dep_idx in decl_names[dep_name]:
                                dep_decl = decls[dep_idx]
                                if isinstance(dep_decl, Typedef):
                                    dependencies[i].add(dep_idx)
                                    if not is_pointer and dep_name in typedef_to_struct:
                                        struct_name = typedef_to_struct[dep_name]
                                        if struct_name in decl_names:
                                            for struct_idx in decl_names[struct_name]:
                                                if isinstance(decls[struct_idx], Struct):
                                                    dependencies[i].add(struct_idx)
                                elif isinstance(dep_decl, Struct | Enum):
                                    if not is_pointer:
                                        if self._is_value_type_usage(fld.type, dep_name):
                                            dependencies[i].add(dep_idx)

            elif isinstance(decl, Function):
                all_types: set[str] = set()
                all_types.update(self._extract_type_names(decl.return_type))
                for param in decl.parameters:
                    all_types.update(self._extract_type_names(param.type))
                for dep_name in all_types:
                    if dep_name in decl_names:
                        for dep_idx in decl_names[dep_name]:
                            dep_decl = decls[dep_idx]
                            if isinstance(dep_decl, Typedef):
                                dependencies[i].add(dep_idx)

        # Topological sort (Kahn's algorithm)
        in_degree: dict[int, int] = {i: len(dependencies[i]) for i in range(len(decls))}
        queue = [i for i in range(len(decls)) if in_degree[i] == 0]
        sorted_indices: list[int] = []

        while queue:
            queue.sort()
            idx = queue.pop(0)
            sorted_indices.append(idx)
            for dependent in range(len(decls)):
                if idx in dependencies[dependent]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        cycle_indices: set[int] = set()
        if len(sorted_indices) != len(decls):
            sorted_set = set(sorted_indices)
            unsorted_indices = [i for i in range(len(decls)) if i not in sorted_set]
            cycle_indices = set(unsorted_indices)
            sorted_indices.extend(unsorted_indices)

        return ([decls[i] for i in sorted_indices], cycle_indices)

    def _extract_type_names(self, typ: TypeExpr) -> set[str]:
        """Extract all type names referenced by a type expression."""
        names: set[str] = set()

        if isinstance(typ, CType):
            name = typ.name
            if name.startswith("struct "):
                names.add(name[7:])
            elif name.startswith("union "):
                names.add(name[6:])
            elif name.startswith("enum "):
                names.add(name[5:])
            else:
                names.add(name)

        elif isinstance(typ, Pointer):
            names.update(self._extract_type_names(typ.pointee))

        elif isinstance(typ, Reference):
            names.update(self._extract_type_names(typ.target))

        elif isinstance(typ, Array):
            names.update(self._extract_type_names(typ.element_type))

        elif isinstance(typ, FunctionPointer):
            names.update(self._extract_type_names(typ.return_type))
            for param in typ.parameters:
                names.update(self._extract_type_names(param.type))

        return names

    def _referenced_type_names(self, decl: Declaration) -> set[str]:
        """All type names a declaration references in its signature or fields."""
        names: set[str] = set()
        if isinstance(decl, Function):
            names.update(self._extract_type_names(decl.return_type))
            for param in decl.parameters:
                names.update(self._extract_type_names(param.type))
        elif isinstance(decl, Struct):
            for fld in decl.fields:
                names.update(self._extract_type_names(fld.type))
            for method in decl.methods:
                names.update(self._extract_type_names(method.return_type))
                for param in method.parameters:
                    names.update(self._extract_type_names(param.type))
        elif isinstance(decl, Typedef):
            names.update(self._extract_type_names(decl.underlying_type))
        elif isinstance(decl, Variable):
            names.update(self._extract_type_names(decl.type))
        return names

    def _early_reference_forwards(self, decls: list[Declaration]) -> list[tuple[str, str]]:
        """Record types used before their definition is emitted.

        The topological sort deliberately creates no edge from a function to a
        record it only names through a pointer, so a definition can land after
        its first use. Cython accepts that, but the reference output declares
        the tag up front; these are the ``(kind, name)`` pairs to forward.
        """
        definitions: dict[str, tuple[int, str]] = {}
        for i, decl in enumerate(decls):
            if not isinstance(decl, Struct) or not decl.name or decl.is_typedef:
                continue
            if not decl.fields and not decl.methods:
                continue
            if get_stub_module_for_type(decl.name):
                continue
            definitions.setdefault(decl.name, (i, "union" if decl.is_union else "struct"))

        needed: dict[str, str] = {}
        for i, decl in enumerate(decls):
            for type_name in self._referenced_type_names(decl):
                entry = definitions.get(type_name)
                if entry is not None and i < entry[0]:
                    needed[type_name] = entry[1]
        return sorted(needed.items())

    def _is_value_type_usage(self, typ: TypeExpr, type_name: str) -> bool:
        """Check if a type is used as a value type (not through a pointer)."""
        if isinstance(typ, CType):
            name = typ.name
            if name.startswith("struct "):
                return name[7:] == type_name
            elif name.startswith("union "):
                return name[6:] == type_name
            elif name.startswith("enum "):
                return name[5:] == type_name
            else:
                return name == type_name
        return False

    # -----------------------------------------------------------------
    # Main write entry point
    # -----------------------------------------------------------------

    def write(self) -> str:
        """Convert IR Header to Cython ``.pxd`` string.

        The body is rendered *before* the cimport header. A cimport is only
        correct when the type it names actually reaches the output, and for
        ``bool`` that decision is taken inside :meth:`_format_ctype` while a
        declaration is being formatted. Rendering first is what lets the header
        state what the body really emitted rather than what the IR merely
        mentioned.
        """
        body = self._write_body()
        lines = self._cimport_lines()
        if lines:
            # Blank line between the cimport header and the extern blocks.
            lines.append("")
        lines.extend(body)
        return "\n".join(lines)

    def _cimport_lines(self) -> list[str]:
        """Render the cimport header for every type the output referenced."""
        lines: list[str] = []

        # 1. Cython stdlib cimports (sorted for determinism)
        for module in sorted(self.cython_cimports.keys()):
            types = sorted(self.cython_cimports[module])
            lines.append(f"from {module} cimport {', '.join(types)}")

        # 2. C++ STL cimports
        for module in sorted(self.libcpp_cimports.keys()):
            types = sorted(self.libcpp_cimports[module])
            lines.append(f"from {module} cimport {', '.join(types)}")

        # 3. Stub cimports (only when stub_cimport_prefix is configured)
        if self.stub_cimport_prefix is not None:
            for stub_module in sorted(self.stub_cimports.keys()):
                types = sorted(self.stub_cimports[stub_module])
                lines.append(f"from {self.stub_cimport_prefix}.{stub_module} cimport {', '.join(types)}")

        return lines

    def _write_body(self) -> list[str]:
        """Render every ``cdef extern`` block, without the cimport header."""
        lines: list[str] = []

        # Group declarations by namespace
        by_namespace: dict[str | None, list[Declaration]] = defaultdict(list)
        for decl in self.header.declarations:
            ns: str | None = getattr(decl, "namespace", None)
            # A renamed collision carries a fully qualified cname, which only
            # resolves in a block that has no `namespace` of its own.
            if self._needs_namespace_qualification(decl):
                ns = None
            by_namespace[ns].append(decl)

        # If no declarations at all, still output empty extern block
        if not by_namespace:
            by_namespace[None] = []

        # Sort and detect cycles per namespace
        sorted_by_namespace: dict[str | None, list[Declaration]] = {}
        cycle_indices_by_namespace: dict[str | None, set[int]] = {}

        for ns in by_namespace:
            sorted_decls, cycle_indices = self._sort_declarations(by_namespace[ns])
            sorted_by_namespace[ns] = sorted_decls
            cycle_indices_by_namespace[ns] = cycle_indices

        # Output non-namespaced declarations first, then namespaced (sorted),
        # except that a namespace declaring a base class is moved ahead of the
        # namespaces that inherit from it.
        namespace_order = self._order_namespaces(by_namespace)
        self._namespace_order = namespace_order

        for namespace in namespace_order:
            decls = sorted_by_namespace[namespace]
            cycle_indices = cycle_indices_by_namespace[namespace]

            # Extern block header
            if namespace:
                lines.append(f'cdef extern from "{self.header.path}" namespace "{namespace}":')
            else:
                lines.append(f'cdef extern from "{self.header.path}":')

            # Forward declarations for undeclared types (global namespace only)
            if namespace is None:
                forward_decls: list[str] = []
                for struct_name in sorted(self.undeclared_structs):
                    escaped = self._escape_name(struct_name, include_c_name=True)
                    forward_decls.append(f"{self.INDENT}cdef struct {escaped}")
                for union_name in sorted(self.undeclared_unions):
                    escaped = self._escape_name(union_name, include_c_name=True)
                    forward_decls.append(f"{self.INDENT}cdef union {escaped}")
                if not cycle_indices:
                    # The cycle path emits its own Phase 1 forwards; only the
                    # acyclic path needs these, and never for a name already
                    # forwarded above.
                    already = self.undeclared_structs | self.undeclared_unions
                    for type_name, kind in self._early_reference_forwards(decls):
                        if type_name in already:
                            continue
                        escaped = self._escape_name(type_name, include_c_name=True)
                        forward_decls.append(f"{self.INDENT}cdef {kind} {escaped}")
                if forward_decls:
                    lines.append("")
                    lines.extend(forward_decls)

            # 5-phase output for circular dependencies
            if cycle_indices:
                self._write_cycle_phases(decls, lines)
            else:
                # No cycles -- normal output
                if not decls and not (namespace is None and (self.undeclared_structs or self.undeclared_unions)):
                    lines.append(f"{self.INDENT}pass")
                    lines.append("")
                else:
                    lines.append("")
                    for decl in decls:
                        decl_lines = self._write_declaration(decl)
                        for line in decl_lines:
                            lines.append(f"{self.INDENT}{line}" if line else "")
                        lines.append("")

        return lines

    # -----------------------------------------------------------------
    # Cycle-breaking multi-phase output
    # -----------------------------------------------------------------

    def _write_cycle_phases(
        self,
        decls: list[Declaration],
        lines: list[str],
    ) -> None:
        """Emit declarations in 5 phases to break circular dependencies."""
        typedef_struct_names: set[str] = {
            decl.name for decl in decls if isinstance(decl, Struct) and decl.is_typedef and decl.name
        }

        # Phase 1: Forward declarations for ALL structs with bodies
        forward_struct_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Struct) and (decl.fields or decl.methods):
                if decl.name and get_stub_module_for_type(decl.name):
                    continue
                if decl.is_typedef:
                    continue
                kind = "union" if decl.is_union else "struct"
                name = self._escape_name(decl.name, include_c_name=True)
                forward_struct_decls.append(f"{self.INDENT}cdef {kind} {name}")
        if forward_struct_decls:
            lines.append("")
            lines.extend(forward_struct_decls)

        # Phase 2: ALL typedefs
        typedef_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Typedef):
                decl_lines = self._write_declaration(decl)
                for line in decl_lines:
                    typedef_decls.append(f"{self.INDENT}{line}" if line else "")
                typedef_decls.append("")
        if typedef_decls:
            lines.append("")
            lines.extend(typedef_decls)

        # Phase 3: Enums and forward-declaration-only structs (NOT functions)
        other_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Typedef):
                continue
            if isinstance(decl, Struct) and (decl.fields or decl.methods):
                continue
            if isinstance(decl, Struct) and decl.name in typedef_struct_names:
                continue
            if isinstance(decl, Function):
                continue
            decl_lines = self._write_declaration(decl)
            for line in decl_lines:
                other_decls.append(f"{self.INDENT}{line}" if line else "")
            other_decls.append("")
        if other_decls:
            lines.append("")
            lines.extend(other_decls)

        # Phase 4: ALL struct bodies (topologically sorted among themselves)
        struct_decls_list = [
            d
            for d in decls
            if isinstance(d, Struct) and (d.fields or d.methods) and not (d.name and get_stub_module_for_type(d.name))
        ]

        struct_name_to_idx: dict[str, int] = {}
        for idx, sd in enumerate(struct_decls_list):
            if sd.name:
                struct_name_to_idx[sd.name] = idx

        typedef_to_struct_name: dict[str, str] = {}
        for d in decls:
            if isinstance(d, Typedef) and d.name:
                underlying_names = self._extract_type_names(d.underlying_type)
                for un in underlying_names:
                    if un in struct_name_to_idx:
                        typedef_to_struct_name[d.name] = un
                        break

        struct_deps: dict[int, set[int]] = {i: set() for i in range(len(struct_decls_list))}
        for idx, sd in enumerate(struct_decls_list):
            for fld in sd.fields:
                if isinstance(fld.type, Pointer):
                    continue
                field_types = self._extract_type_names(fld.type)
                for ft in field_types:
                    if ft in struct_name_to_idx and ft != sd.name:
                        struct_deps[idx].add(struct_name_to_idx[ft])
                    if ft in typedef_to_struct_name:
                        target = typedef_to_struct_name[ft]
                        if target in struct_name_to_idx and target != sd.name:
                            struct_deps[idx].add(struct_name_to_idx[target])

        # Topological sort of struct bodies
        in_degree: dict[int, int] = {i: len(struct_deps[i]) for i in range(len(struct_decls_list))}
        queue = [i for i in range(len(struct_decls_list)) if in_degree[i] == 0]
        sorted_struct_indices: list[int] = []

        while queue:
            idx = queue.pop(0)
            sorted_struct_indices.append(idx)
            for dependent in range(len(struct_decls_list)):
                if idx in struct_deps[dependent]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if len(sorted_struct_indices) != len(struct_decls_list):
            remaining = [i for i in range(len(struct_decls_list)) if i not in sorted_struct_indices]
            sorted_struct_indices.extend(remaining)

        struct_bodies: list[str] = []
        for idx in sorted_struct_indices:
            decl = struct_decls_list[idx]
            decl_lines = self._write_declaration(decl)
            for line in decl_lines:
                struct_bodies.append(f"{self.INDENT}{line}" if line else "")
            struct_bodies.append("")
        if struct_bodies:
            lines.append("")
            lines.extend(struct_bodies)

        # Phase 5: Functions
        func_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Function):
                decl_lines = self._write_declaration(decl)
                for line in decl_lines:
                    func_decls.append(f"{self.INDENT}{line}" if line else "")
                func_decls.append("")
        if func_decls:
            lines.append("")
            lines.extend(func_decls)

    def _order_namespaces(self, by_namespace: dict[str | None, list[Declaration]]) -> list[str | None]:
        """Order the ``cdef extern`` blocks so every base precedes its subclass.

        Cython resolves inherited members where the derived class is parsed. A
        base named before its own block has been seen is not an error -- the
        subclass simply gets no inherited members -- so ordering is what keeps
        cross-namespace inheritance correct rather than silently empty.

        The default order (unnamespaced first, then alphabetical) is the
        tiebreak, so a header without cross-namespace inheritance is unaffected.
        """
        default = sorted(by_namespace.keys(), key=lambda x: (x is not None, x or ""))

        declared_in: dict[str, str | None] = {}
        for ns, decls in by_namespace.items():
            for decl in decls:
                if isinstance(decl, Struct) and decl.name and not decl.is_union:
                    declared_in.setdefault(decl.name, ns)

        # ns -> namespaces it must follow
        prerequisites: dict[str | None, set[str | None]] = {ns: set() for ns in by_namespace}
        for ns, decls in by_namespace.items():
            for decl in decls:
                if not isinstance(decl, Struct):
                    continue
                for base in decl.bases:
                    if not base.name:
                        continue
                    head = _strip_namespace_qualifiers(base.name).split("<", 1)[0].strip()
                    base_ns = declared_in.get(head)
                    if head in declared_in and base_ns != ns:
                        prerequisites[ns].add(base_ns)

        ordered: list[str | None] = []
        remaining = list(default)
        while remaining:
            ready = [ns for ns in remaining if not (prerequisites[ns] - set(ordered))]
            if not ready:
                # A cycle across namespaces; keep the default order for the rest
                # and let _format_base_name emit the diagnostic.
                ordered.extend(remaining)
                break
            ordered.append(ready[0])
            remaining.remove(ready[0])
        return ordered

    # -----------------------------------------------------------------
    # Known-type collection
    # -----------------------------------------------------------------

    def _collect_collisions(self) -> None:
        """Map cross-namespace duplicate type names to disambiguated names.

        ``namespace a { struct dup; }`` beside ``namespace b { struct dup; }``
        is idiomatic C++ (a ``detail::Options`` next to a public ``Options``),
        but a ``.pxd`` module namespace is flat. Emitting both as ``dup`` in two
        ``namespace``-qualified extern blocks does not merely fail to compile:
        Cython accepts it, binds every use to whichever came first, and emits
        C++ that names only ``a::dup``. The second declaration is lost with no
        diagnostic from any tool in the chain.

        So a name declared in more than one namespace is renamed to
        ``<ns>_<name>`` and carries an explicit ``"ns::name"`` cname. The cname
        is fully qualified, which requires it be emitted in a block with no
        ``namespace`` of its own -- a ``namespace "a"`` block does *not* qualify
        an explicit cname, and emits a bare, incomplete ``struct dup``.

        A declaration in the global namespace keeps its bare name, so a public
        ``Options`` is unchanged and only ``detail::Options`` is renamed. A name
        unique to one namespace is untouched, which keeps every existing golden
        file stable. The map is keyed and built off sorted input, so the result
        does not depend on declaration order or set iteration order.
        """
        namespaces_by_name: dict[str, set[str | None]] = defaultdict(set)
        for decl in self.header.declarations:
            if isinstance(decl, (Struct, Enum, Typedef, Variable)) and decl.name:
                namespaces_by_name[decl.name].add(self._decl_scope(decl))
            # An enumerator of an unscoped enum is a namespace-scope name in
            # C++, not a member of the tag, so it collides independently of it.
            # ``a::E{X}`` beside ``b::F{X}`` has no tag collision at all, yet
            # both enumerators reach the flat ``.pxd`` module as ``X``.
            if isinstance(decl, Enum):
                # A *scoped* enumerator is a member of its tag, so its scope is
                # the tag and not the namespace. Keying it on the namespace made
                # every bare ``X`` at global scope share one key, so a global
                # ``enum class G { X }`` beside an ``enum U { X }`` was not seen
                # as a duplicate and both reached the module as ``X``.
                scope = self._enumerator_scope(decl)
                for value in decl.values:
                    namespaces_by_name[value.name].add(scope)

        for name in sorted(namespaces_by_name):
            namespaces = namespaces_by_name[name]
            if len(namespaces) < 2:
                continue
            self._colliding_bare_names.add(name)
            for ns in sorted(n for n in namespaces if n is not None):
                self._collision_names[(ns, name)] = f"{ns.replace('::', '_')}_{name}"

    def _decl_scope(self, decl: Declaration) -> str | None:
        """Return the C++ scope enclosing *decl*, or None at global scope.

        For almost every declaration this is its ``namespace``. A member enum is
        the exception: it is hoisted to the top level carrying a record-qualified
        ``cpp_name``, and its enclosing scope is that record, which no
        ``namespace`` records. Keying the duplicate scan on ``namespace`` alone
        put ``C1::E`` and ``C2::E`` in the same scope ``None``, so the scan saw
        one name in one scope and renamed neither -- and two ``cdef enum E``
        blocks reached the flat module, where Cython silently binds every use to
        one of them.
        """
        if isinstance(decl, Enum) and decl.cpp_name and decl.name:
            prefix = decl.cpp_name.removesuffix(f"::{decl.name}")
            return prefix if prefix != decl.cpp_name else None
        return getattr(decl, "namespace", None)

    def _is_renamed_collision(self, decl: Declaration) -> bool:
        """True if *decl* is one of the cross-scope duplicates we rename."""
        name = getattr(decl, "name", None)
        if not name:
            return False
        return (self._decl_scope(decl), name) in self._collision_names

    def _needs_namespace_qualification(self, decl: Declaration) -> bool:
        """True if *decl* must leave its ``namespace`` block and self-qualify.

        A renamed tag is one reason. An enum whose *enumerators* collide is the
        other: the tags may be distinct while the constants they introduce into
        the namespace are not, and qualifying a constant requires the block it
        sits in to carry no ``namespace`` of its own.
        """
        if self._is_renamed_collision(decl):
            return True
        if isinstance(decl, Enum) and decl.namespace is not None:
            # A scoped enumerator is spelled through its tag and a member enum
            # is hoisted out of its record; both need an explicit cname, which a
            # ``namespace`` block does not qualify.
            if decl.is_scoped or decl.cpp_name is not None:
                return True
            scope = self._enumerator_scope(decl)
            return any((scope, value.name) in self._collision_names for value in decl.values)
        return False

    def _resolve_collision(self, namespace: str | None, name: str) -> str:
        """Return the Cython name for *name* as seen from *namespace*."""
        return self._collision_names.get((namespace, name), name)

    def _collision_declared_name(self, decl: Declaration) -> str | None:
        """Return the ``a_dup "a::dup"`` spelling for a renamed duplicate, else None.

        ``write`` moves a renamed duplicate into a block carrying no
        ``namespace`` of its own, because an explicit cname is not qualified by
        one. The declaration must therefore carry the qualification itself --
        omitting it is what let two ``cdef enum E`` blocks reach the output and
        emit unqualified enumerators that no C++ scope declares.
        """
        if not self._is_renamed_collision(decl):
            return None
        name = getattr(decl, "name")  # noqa: B009 -- _is_renamed_collision proved it is set
        scope = self._decl_scope(decl)
        return f'{self._collision_names[(scope, name)]} "{scope}::{name}"'

    def _collect_known_types(self) -> None:
        """Collect all declared struct/union/enum names for type resolution."""
        for decl in self.header.declarations:
            if isinstance(decl, Struct):
                if decl.name:
                    if getattr(decl, "namespace", None) == "std" and decl.name in LIBCPP_TYPES:
                        continue
                    if decl.is_union:
                        self.known_unions.add(decl.name)
                    else:
                        self.known_structs.add(decl.name)
                        self._record_names.add(decl.name)
                        self._record_namespace.setdefault(decl.name, decl.namespace)
                for base in decl.bases:
                    if base.name:
                        head = _strip_namespace_qualifiers(base.name).split("<", 1)[0].strip()
                        if head:
                            self._base_class_names.add(head)
            elif isinstance(decl, Enum):
                if decl.name:
                    self.known_enums.add(decl.name)
            elif isinstance(decl, Typedef):
                if decl.name:
                    self.known_typedefs.add(decl.name)
                    self.known_structs.add(decl.name)

    def _collect_template_members(self) -> None:
        """Record the nested member names declared by each class template.

        ``types::remove_reference<T>::type`` may only be emitted as
        ``remove_reference[T].type`` when ``type`` is genuinely declared inside
        ``remove_reference``. Without this map the writer cannot tell a
        resolvable dependent name from a reference to an undeclared one.
        """
        for decl in self.header.declarations:
            if isinstance(decl, Struct) and decl.name and decl.is_cppclass and decl.inner_typedefs:
                self._template_members.setdefault(decl.name, set()).update(decl.inner_typedefs)

    def _collect_incomplete_types(self) -> None:
        """Collect structs that are forward declarations (no fields)."""
        for decl in self.header.declarations:
            if isinstance(decl, Struct):
                if decl.name and not decl.fields and not decl.methods:
                    self.incomplete_structs.add(decl.name)

    # -----------------------------------------------------------------
    # Cimport collection
    # -----------------------------------------------------------------

    def _collect_cimport_types(self) -> None:
        """Collect all types that need cimport statements."""
        for decl in self.header.declarations:
            self._collect_types_from_declaration(decl)

    def _collect_types_from_declaration(self, decl: Declaration) -> None:
        """Recursively collect types from a declaration."""
        if isinstance(decl, Function):
            self._check_type(decl.return_type)
            for param in decl.parameters:
                self._check_type(param.type)
        elif isinstance(decl, Struct):
            for base in decl.bases:
                if base.name:
                    self._check_type_name(base.name)
            for fld in decl.fields:
                self._check_type(fld.type)
            for method in decl.methods:
                self._check_type(method.return_type)
                for param in method.parameters:
                    self._check_type(param.type)
        elif isinstance(decl, Typedef):
            self._check_type(decl.underlying_type)
        elif isinstance(decl, Variable):
            self._check_type(decl.type)

    def _check_type(self, typ: TypeExpr) -> None:
        """Check if a type needs a cimport and record it."""
        if isinstance(typ, CType):
            self._check_type_name(typ.name)
        elif isinstance(typ, Pointer):
            self._check_type(typ.pointee)
        elif isinstance(typ, Array):
            self._check_type(typ.element_type)
        elif isinstance(typ, FunctionPointer):
            self._check_type(typ.return_type)
            for param in typ.parameters:
                self._check_type(param.type)

    def _check_type_name(self, name: str) -> None:
        """Check a type name against registries."""
        clean_name = name.removeprefix("struct ").removeprefix("class ").removeprefix("union ")

        # If the type is locally declared in this header, do not cimport from external modules or stubs
        if (
            clean_name in self.known_structs
            or clean_name in self.known_unions
            or clean_name in self.known_enums
            or clean_name in self.known_typedefs
        ):
            return

        # Check stubs BEFORE deciding to forward-declare
        stub_module = get_stub_module_for_type(name) or get_stub_module_for_type(clean_name)

        if not stub_module:
            if name.startswith("struct "):
                struct_name = name[7:].removeprefix("struct ")
                if (
                    "(unnamed" not in struct_name
                    and "(anonymous" not in struct_name
                    and not struct_name.startswith("(")
                    and struct_name not in self.known_structs
                ):
                    self.undeclared_structs.add(struct_name)
            elif name.startswith("union "):
                union_name = name[6:].removeprefix("union ")
                if (
                    "(unnamed" not in union_name
                    and "(anonymous" not in union_name
                    and not union_name.startswith("(")
                    and union_name not in self.known_unions
                ):
                    self.undeclared_unions.add(union_name)

        # Strip std:: prefix for C++ types
        cpp_name = clean_name.removeprefix("std::")

        # For template types, extract the base name
        base_name = cpp_name.split("<")[0] if "<" in cpp_name else cpp_name

        # Check Cython stdlib
        module = get_cython_module_for_type(name)
        if module:
            self.cython_cimports.setdefault(module, set()).add(name)
            return

        # Check C++ STL
        module = get_libcpp_module_for_type(base_name)
        if module:
            self.libcpp_cimports.setdefault(module, set()).add(base_name)

        # Collect stub cimports if a prefix is configured
        if self.stub_cimport_prefix is not None and stub_module:
            self.stub_cimports.setdefault(stub_module, set()).add(clean_name)

        # Also check template arguments recursively
        if "<" in cpp_name:
            self._check_template_args(cpp_name)
            return

    def _check_template_args(self, type_str: str) -> None:
        """Recursively check template arguments for types that need cimports."""
        start = type_str.find("<")
        if start == -1:
            return

        depth = 0
        end = -1
        for i in range(start, len(type_str)):
            if type_str[i] == "<":
                depth += 1
            elif type_str[i] == ">":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            return

        args_str = type_str[start + 1 : end]

        args: list[str] = []
        current_arg = ""
        depth = 0
        for char in args_str:
            if char == "<":
                depth += 1
                current_arg += char
            elif char == ">":
                depth -= 1
                current_arg += char
            elif char == "," and depth == 0:
                args.append(current_arg.strip())
                current_arg = ""
            else:
                current_arg += char

        if current_arg.strip():
            args.append(current_arg.strip())

        for arg in args:
            self._check_type_name(arg)

    # -----------------------------------------------------------------
    # Declaration dispatch
    # -----------------------------------------------------------------

    def _write_declaration(self, decl: Declaration) -> list[str]:
        """Write a single declaration.

        The declaration's own namespace is put in scope first, so that an
        unqualified use of a colliding name inside it -- a function in
        ``namespace a`` returning ``alias`` -- resolves to ``a``'s declaration
        rather than staying bare and naming nothing. ``_write_struct``
        overwrites and clears this for its own members.
        """
        self._current_namespace = getattr(decl, "namespace", None)
        if isinstance(decl, Struct):
            return self._write_struct(decl)
        if isinstance(decl, Enum):
            return self._write_enum(decl)
        if isinstance(decl, Function):
            return self._write_function(decl)
        if isinstance(decl, Typedef):
            return self._write_typedef(decl)
        if isinstance(decl, Variable):
            return self._write_variable(decl)
        if isinstance(decl, Constant):
            return self._write_constant(decl)
        return []

    # -----------------------------------------------------------------
    # Struct / union / cppclass
    # -----------------------------------------------------------------

    def _write_struct(self, struct: Struct) -> list[str]:
        """Convert a Struct to Cython struct/union/cppclass definition."""
        if not struct.name or (
            not struct.fields and not struct.methods and ("(anonymous" in struct.name or "(unnamed" in struct.name)
        ):
            return []

        lines: list[str] = []

        # Store inner typedefs context for _format_ctype
        self._current_inner_typedefs = struct.inner_typedefs if struct.is_cppclass else {}
        self._current_template_params = set(struct.template_params)
        self._current_namespace = struct.namespace

        # Track unsupported inner template typedefs
        self._unsupported_inner_typedefs = set()
        if struct.is_cppclass and struct.inner_typedefs:
            for inner_name, inner_type in struct.inner_typedefs.items():
                if "<" in inner_type and ">" in inner_type:
                    base_type = inner_type.split("<")[0].strip()
                    if base_type and base_type in self.known_structs:
                        self._unsupported_inner_typedefs.add(inner_name)

        # Emit notes as comments
        if struct.notes:
            for note in struct.notes:
                lines.append(f"# {note}")

        # Emit packed comment
        if struct.is_packed:
            lines.append("# NOTE: packed struct (Cython does not support __attribute__((packed)))")

        # Only a cppclass may carry an inheritance list, so a record with bases
        # is emitted as one even when the backend did not mark it. Emitting it
        # as a plain struct would drop the base list and, with it, every
        # inherited member. A record *used* as a base must be a cppclass too:
        # Cython crashes outright ("'CStructOrUnionType' object has no attribute
        # 'base_classes'") when a cppclass inherits from a cdef struct.
        #
        # A record declaring a callable member is a cppclass for the same class
        # of reason: Cython accepts a function declaration only inside a
        # cppclass suite, and rejects it in a ``cdef struct`` with "Syntax error
        # in C variable declaration". A callable member is also what makes the
        # record C++ in the first place -- a C struct cannot have one -- so the
        # predicate never promotes a plain C struct, which must stay a
        # ``cdef struct`` because ``cppclass`` is meaningless in a C context.
        is_base = struct.name is not None and struct.name in self._base_class_names
        if struct.is_cppclass or struct.bases or ((is_base or _has_callable_member(struct)) and not struct.is_union):
            kind = "cppclass"
        elif struct.is_union:
            kind = "union"
        else:
            kind = "struct"
        renamed = self._is_renamed_collision(struct)
        if renamed and struct.name is not None:
            name = self._collision_names[(struct.namespace, struct.name)]
        else:
            name = self._escape_name(struct.name, include_c_name=True)

        # Template parameters
        if struct.template_params:
            params = ", ".join(struct.template_params)
            name = f"{name}[{params}]"

        # Base classes for C++ classes
        if struct.bases:
            base_names: list[str] = []
            for base in struct.bases:
                if not base.name:
                    continue
                rendered = self._format_base_name(base.name, struct.namespace)
                if rendered is None:
                    lines.append(
                        f"# UNSUPPORTED: base class '{base.name}' of '{struct.name}' cannot be named in Cython"
                    )
                    lines.append(
                        "# (not declared in this translation unit, or declared after this block); "
                        "inherited members are absent."
                    )
                    continue
                base_names.append(rendered)
            if base_names:
                name = f"{name}({', '.join(base_names)})"

        # C++ name if different
        if renamed:
            name = f'{name} "{struct.namespace}::{struct.name}"'
        elif struct.cpp_name and struct.cpp_name != struct.name:
            name = f'{name} "{struct.cpp_name}"'

        keyword = "ctypedef" if struct.is_typedef else "cdef"

        inner_typedef_lines = self._write_inner_typedefs(struct)
        nested_record_lines = self._write_nested_records(struct)

        # Forward declaration (nothing at all to put in a body)
        if (
            not struct.fields
            and not struct.methods
            and not struct.constructors
            and not struct.conversions
            and not inner_typedef_lines
            and not nested_record_lines
        ):
            lines.append(f"{keyword} {kind} {name}")
            self._clear_struct_context()
            return lines

        header_index = len(lines)
        lines.append(f"{keyword} {kind} {name}:")

        lines.extend(inner_typedef_lines)
        lines.extend(nested_record_lines)
        lines.extend(self._write_fields(struct.fields))

        # Constructors (cppclass)
        ctor_name = name.split(" ", 1)[0].split("[", 1)[0].split("(", 1)[0]
        for ctor in struct.constructors:
            params_str = self._format_params(ctor.parameters, ctor.is_variadic)
            lines.append(f"{self.INDENT}{ctor_name}({params_str})")

        # Methods (cppclass)
        operator_aliases: dict[str, str] = {
            "operator->": "deref",
            "operator()": "call",
        }
        # A conversion operator is filed separately by the backend but is an
        # ordinary member function here: ``_write_function`` already routes an
        # operator Cython cannot declare through the UNSUPPORTED channel, and
        # ``operator bool`` -- the one conversion Cython accepts -- renders as
        # a plain method. Dropping the list outright made a class whose only
        # members were conversions emit a body-less ``cdef cppclass``.
        for method in [*struct.methods, *struct.conversions]:
            return_type_name = method.return_type.name if isinstance(method.return_type, CType) else None
            if return_type_name and return_type_name in self._unsupported_inner_typedefs:
                underlying = self._current_inner_typedefs.get(return_type_name, return_type_name)
                lines.append(
                    f"{self.INDENT}# UNSUPPORTED: {method.name}() returns "
                    f"C++ inner type '{return_type_name}' ({underlying})"
                )
                lines.append(
                    f"{self.INDENT}# Cython cannot represent nested template types. Use the C++ API directly if needed."
                )
                continue

            if method.name in operator_aliases:
                alias = operator_aliases[method.name]
                return_type = self._format_type(method.return_type)
                params_str = self._format_params(method.parameters, method.is_variadic)
                method_line = f'{return_type} {alias} "{method.name}"({params_str})'
                lines.append(f"{self.INDENT}{method_line}")
            else:
                method_lines = self._write_function(method)
                for line in method_lines:
                    lines.append(f"{self.INDENT}{line}")

        self._clear_struct_context()

        # A suite header with no body is a syntax error in Cython. Comments do
        # not count as a body, so a class whose every member was replaced by an
        # UNSUPPORTED diagnostic still needs the explicit ``pass``.
        body = lines[header_index + 1 :]
        if not any(line.strip() and not line.strip().startswith("#") for line in body):
            lines.append(f"{self.INDENT}pass")

        return lines

    def _write_nested_records(self, struct: Struct) -> list[str]:
        """Render records defined inside *struct* as members of its body.

        Cython declares a nested record inside the parent suite without the
        ``cdef`` keyword, and resolves it as ``Parent.Inner``. That is the form
        ``libcpp/vector.pxd`` uses for ``vector[T].iterator``, and it makes the
        C++ qualification implicit -- so no synthesized name can collide with a
        top-level one, and the parent's own fields can name the record directly.
        """
        if not struct.nested_records:
            return []

        # ``_write_struct`` overwrites the per-struct context and clears it on
        # the way out. Rendering a child in the middle of the parent would
        # therefore strip the parent's inner typedefs and template parameters
        # before its fields and methods are written.
        saved = (
            self._current_inner_typedefs,
            self._unsupported_inner_typedefs,
            self._current_template_params,
            self._current_namespace,
        )
        lines: list[str] = []
        try:
            for nested in struct.nested_records:
                # The namespace belongs to the enclosing class, which already
                # sits inside it. Keeping it would emit a cname qualified by the
                # namespace alone (``"doctest::view"``), naming a type that does
                # not exist at that scope.
                rendered = self._write_struct(replace(nested, namespace=None))
                if not rendered:
                    continue
                # The declaration is not always the first line: notes and the
                # packed warning are emitted ahead of it as comments.
                stripped = False
                for line in rendered:
                    body = line
                    if not stripped:
                        for kw in ("cdef ", "ctypedef "):
                            if body.startswith(kw):
                                body = body[len(kw) :]
                                stripped = True
                                break
                    lines.append(f"{self.INDENT}{body}" if body else body)
        finally:
            (
                self._current_inner_typedefs,
                self._unsupported_inner_typedefs,
                self._current_template_params,
                self._current_namespace,
            ) = saved
        return lines

    def _clear_struct_context(self) -> None:
        """Drop the per-struct formatting context.

        Leaving it set made a later free function resolve an unrelated ``type``
        through the previous class's inner typedefs.
        """
        self._current_inner_typedefs = {}
        self._unsupported_inner_typedefs = set()
        self._current_template_params = set()
        self._current_namespace = None

    def _write_inner_typedefs(self, struct: Struct) -> list[str]:
        """Render a C++ class's nested typedefs as ``ctypedef`` members.

        Without these, a dependent name such as ``remove_reference[T].type``
        would reference a member that the emitted ``cppclass`` never declares.
        """
        if not struct.is_cppclass or not struct.inner_typedefs:
            return []

        lines: list[str] = []
        for inner_name, underlying in struct.inner_typedefs.items():
            if inner_name in self._unsupported_inner_typedefs:
                continue
            rendered = self._format_ctype(CType(name=underlying))
            if self._unrepresentable_dependents:
                for spelling, reason in self._unrepresentable_dependents:
                    lines.append(f"{self.INDENT}# UNSUPPORTED: nested type '{inner_name}' ({spelling})")
                    lines.append(f"{self.INDENT}# {reason}")
                self._unrepresentable_dependents.clear()
                continue
            lines.append(f"{self.INDENT}ctypedef {rendered} {self._escape_name(inner_name)}")
        return lines

    def _write_fields(self, fields: list[Field]) -> list[str]:
        """Render struct/union members, flattening C11 anonymous members.

        An anonymous nested struct or union has no name of its own, so its
        members belong to the enclosing record. Emitting them at the same
        indentation is what makes ``outer.b`` resolve, matching C semantics.
        """
        lines: list[str] = []

        for fld in fields:
            if fld.anonymous_struct is not None:
                lines.extend(self._write_fields(fld.anonymous_struct.fields))
                continue

            # Skip anonymous struct/union fields carrying no nested definition
            if fld.is_anonymous_transparent or not fld.name:
                continue

            # Skip fields using incomplete types as values
            if self._is_incomplete_value_type(fld.type):
                continue

            field_name = self._escape_name(fld.name, include_c_name=True)
            start = len(lines)
            self._reset_unsupported()
            self._reject_rvalue("has", fld.type)

            # Bitfield comment (Cython doesn't support bitfields)
            bit_comment = ""
            if fld.bit_width is not None:
                bit_comment = f"  # bitfield: {fld.bit_width} bit{'' if fld.bit_width == 1 else 's'}"

            if isinstance(fld.type, FunctionPointer):
                if self._is_nested_func_ptr(fld.type):
                    lines.append(f"{self.INDENT}void* {field_name}{bit_comment}")
                else:
                    lines.append(f"{self.INDENT}{self._format_func_ptr(fld.type, field_name)}{bit_comment}")
            elif isinstance(fld.type, Pointer) and isinstance(fld.type.pointee, FunctionPointer):
                if self._is_nested_func_ptr(fld.type.pointee):
                    lines.append(f"{self.INDENT}void* {field_name}{bit_comment}")
                else:
                    lines.append(f"{self.INDENT}{self._format_func_ptr(fld.type.pointee, field_name)}{bit_comment}")
            elif isinstance(fld.type, Array):
                field_type = self._format_type(fld.type)
                dims = self._format_array_dims(fld.type)
                lines.append(f"{self.INDENT}{field_type} {field_name}{dims}{bit_comment}")
            else:
                field_type = self._format_type(fld.type)
                lines.append(f"{self.INDENT}{field_type} {field_name}{bit_comment}")

            lines[start:] = self._guard_unsupported(f"field '{fld.name}'", lines[start:], self.INDENT)

        return lines

    def _guard_unsupported(self, symbol: str, rendered: list[str], indent: str = "") -> list[str]:
        """Replace *rendered* with a diagnostic if it used an unrepresentable construct.

        ``_format_ctype`` cannot refuse to return a string, so an unresolvable
        dependent name is recorded on the writer instead. Every caller that
        emits a declaration drains that record here: emitting the C++ spelling
        would produce a ``.pxd`` Cython cannot parse, and dropping it silently
        would hide the loss. Constructs rejected for a reason other than a
        dependent name -- an rvalue reference outside parameter position -- ride
        the same channel so that every skip carries a diagnostic.
        """
        dependents = list(self._unrepresentable_dependents)
        constructs = list(self._unrepresentable_constructs)
        self._reset_unsupported()
        if not dependents and not constructs:
            return rendered
        diagnostics: list[str] = []
        for spelling, reason in dependents:
            diagnostics.append(f"{indent}# UNSUPPORTED: {symbol} uses dependent type '{spelling}'")
            diagnostics.append(f"{indent}# {reason}")
        for phrase, reason in constructs:
            diagnostics.append(f"{indent}# UNSUPPORTED: {symbol} {phrase}")
            diagnostics.append(f"{indent}# {reason}")
        return diagnostics

    def _reset_unsupported(self) -> None:
        """Discard unrepresentable-construct records left over from a prior declaration."""
        self._unrepresentable_dependents.clear()
        self._unrepresentable_constructs.clear()

    def _reject_rvalue(self, position: str, type_expr: TypeExpr) -> bool:
        """Record a diagnostic when *type_expr* is an rvalue reference.

        Cython 3.3 parses ``&&`` only in parameter position; a return type,
        field, typedef, or variable spelled ``T&&`` is a syntax error. Emitting
        ``T&`` instead would compile while silently widening the signature to
        accept lvalues that C++ rejects, so the declaration is skipped instead.

        :returns: True when a diagnostic was recorded and the caller must skip.
        """
        if not (isinstance(type_expr, Reference) and type_expr.is_rvalue):
            return False
        # ``str(Reference)`` renders the libclang spelling, which carries the C
        # elaborated tag (``struct String&&``). The diagnostic names a C++
        # construct, so the tag is dropped to match the source spelling.
        spelling = str(type_expr)
        for tag in ("struct ", "union ", "enum "):
            if spelling.startswith(tag):
                spelling = spelling[len(tag) :]
                break
        self._unrepresentable_constructs.append(
            (
                f"{position} rvalue reference '{spelling}'",
                "Cython supports '&&' only on parameters, not in return, field, typedef, or variable position.",
            )
        )
        return True

    # -----------------------------------------------------------------
    # Enum
    # -----------------------------------------------------------------

    def _enumerator_scope(self, enum: Enum) -> str | None:
        """Return the C++ scope that qualifies *enum*'s enumerators, or None.

        An unscoped enumerator is a *namespace*-scope name -- ``a::E{X}`` is
        spelled ``a::X`` -- so the namespace qualifies it and the tag does not.
        A scoped enumerator is a member of the tag instead, spelled ``a::E::X``;
        ``a::X`` names nothing and clang rejects it. The two cases therefore
        need different prefixes, and the tag's own qualified spelling supplies
        the scoped one.
        """
        if enum.is_scoped or enum.cpp_name:
            return enum.qualified_name
        return enum.namespace

    def _write_enum(self, enum: Enum) -> list[str]:
        """Write an enum declaration."""
        # A renamed duplicate is emitted outside its ``namespace`` block, so
        # the tag *and* every enumerator must carry its own qualified cname.
        # An enumerator of an *unscoped* enum is a namespace-scope name in C++,
        # not a member of the tag, so qualifying the tag alone still emits a
        # bare ``X``.
        collision_name = self._collision_declared_name(enum)
        qualify = self._needs_namespace_qualification(enum)
        self_scoped = enum.is_scoped or enum.cpp_name is not None
        scope = self._enumerator_scope(enum) if (qualify or self_scoped) else None
        tag_cpp_name = enum.qualified_name
        if collision_name is not None:
            name = collision_name
        elif qualify and enum.name and tag_cpp_name:
            # The tag itself is unique, so it keeps its spelling; only the
            # cname has to name the scope the block no longer supplies.
            name = f'{self._escape_name(enum.name)} "{tag_cpp_name}"'
        elif enum.cpp_name and enum.name:
            # A member enum is hoisted out of its record, which no ``namespace``
            # block can restore, so it always carries its own cname.
            name = f'{self._escape_name(enum.name)} "{tag_cpp_name}"'
        else:
            name = self._escape_name(enum.name, include_c_name=True)

        keyword = "ctypedef" if enum.is_typedef else "cdef"
        if enum.name:
            lines = [f"{keyword} enum {name}:"]
        else:
            lines = [f"{keyword} enum:"]

        if enum.values:
            for val in enum.values:
                if scope is not None:
                    # Every enumerator needs the qualification the block no
                    # longer gives it, but only a *colliding* one needs a new
                    # Cython name; a unique one keeps the header's spelling.
                    cython_name = self._collision_names.get((scope, val.name)) or self._escape_name(val.name)
                    val_name = f'{cython_name} "{scope}::{val.name}"'
                else:
                    val_name = self._escape_name(val.name, include_c_name=True)
                lines.append(f"{self.INDENT}{val_name}")
        else:
            lines.append(f"{self.INDENT}pass")

        return lines

    # -----------------------------------------------------------------
    # Function
    # -----------------------------------------------------------------

    def _write_function(self, func: Function) -> list[str]:
        """Write a function declaration.

        A signature carrying a dependent name that Cython cannot express is
        skipped and replaced by a diagnostic naming the symbol and the type. A
        silent drop would leave the binding quietly incomplete.
        """
        lines: list[str] = []
        if func.is_static:
            lines.append("@staticmethod")

        outer_template_params = self._current_template_params
        self._current_template_params = outer_template_params | set(func.template_params)
        self._reset_unsupported()
        self._dependent_deviations.clear()

        # An operator Cython cannot parse rides the unrepresentable-construct
        # channel so the skip carries a diagnostic naming the symbol. Renaming
        # it to an ordinary method would invent an API the header does not
        # declare, and emitting it produces a ``.pxd`` that fails to compile.
        operator_reason = _unsupported_operator_reason(func.name)
        if operator_reason is not None:
            self._unrepresentable_constructs.append(("is an operator Cython cannot overload", operator_reason))

        self._reject_rvalue("returns", func.return_type)
        return_type = self._format_type(func.return_type)
        name = self._escape_name(func.name, include_c_name=True)
        if func.template_params:
            t_params = ", ".join(func.template_params)
            name = f"{name}[{t_params}]"
        params = self._format_params(func.parameters, func.is_variadic)
        const_suffix = " const" if func.is_const else ""

        unrepresentable = list(self._unrepresentable_dependents)
        constructs = list(self._unrepresentable_constructs)
        deviations = list(self._dependent_deviations)
        self._reset_unsupported()
        self._dependent_deviations.clear()
        self._current_template_params = outer_template_params
        if unrepresentable or constructs:
            self._unrepresentable_dependents.extend(unrepresentable)
            self._unrepresentable_constructs.extend(constructs)
            return self._guard_unsupported(f"{func.name}()", lines)
        for spelling, substitute in deviations:
            lines.append(
                f"# NOTE: '{spelling}' emitted as '{substitute}' (Cython cannot defer access on a template argument)"
            )

        # Calling convention comment (Cython doesn't support calling conventions)
        cc_comment = ""
        if func.calling_convention:
            cc_comment = f"  # calling convention: __{func.calling_convention}__"

        lines.append(f"{return_type} {name}({params}){const_suffix}{cc_comment}")
        return lines

    # -----------------------------------------------------------------
    # Typedef
    # -----------------------------------------------------------------

    def _write_typedef(self, typedef: Typedef) -> list[str]:
        """Write a typedef declaration."""
        name = self._collision_declared_name(typedef) or self._escape_name(typedef.name, include_c_name=True)

        # Function pointer typedefs
        if isinstance(typedef.underlying_type, Pointer):
            if isinstance(typedef.underlying_type.pointee, FunctionPointer):
                return self._write_func_ptr_typedef(name, typedef.underlying_type.pointee)
        if isinstance(typedef.underlying_type, FunctionPointer):
            return self._write_func_ptr_typedef(name, typedef.underlying_type)

        self._reset_unsupported()
        self._reject_rvalue("aliases", typedef.underlying_type)
        underlying = self._format_type(typedef.underlying_type)
        if self._unrepresentable_dependents or self._unrepresentable_constructs:
            return self._guard_unsupported(f"typedef '{typedef.name}'", [])

        # Skip circular typedefs. ``name`` carries the C-name annotation for
        # keyword-escaped typedefs (``with_ "with"``), which ``_format_type``
        # never produces, so the comparison uses the bare escaped spelling.
        bare_name = self._escape_name(typedef.name)
        if underlying in (
            bare_name,
            f"struct {bare_name}",
            f"union {bare_name}",
            f"enum {bare_name}",
        ):
            return []

        return [f"ctypedef {underlying} {name}"]

    def _write_func_ptr_typedef(self, name: str, fp: FunctionPointer) -> list[str]:
        """Write a function pointer typedef."""
        is_func_ptr_return = isinstance(fp.return_type, FunctionPointer) or (
            isinstance(fp.return_type, Pointer) and isinstance(fp.return_type.pointee, FunctionPointer)
        )
        if is_func_ptr_return:
            return_type = "void*"
        else:
            return_type = self._format_type(fp.return_type)

        params = self._format_params(fp.parameters, fp.is_variadic)
        return [f"ctypedef {return_type} (*{name})({params})"]

    # -----------------------------------------------------------------
    # Variable
    # -----------------------------------------------------------------

    def _write_variable(self, var: Variable) -> list[str]:
        """Write a variable declaration."""
        name = self._collision_declared_name(var) or self._escape_name(var.name, include_c_name=True)

        func_ptr = self._as_func_ptr(var.type)
        if func_ptr is not None:
            # _format_type yields the *abstract* declarator ``void (*)(int)``,
            # which cannot take a name suffix. Emit a named typedef and declare
            # the variable through it.
            typedef_name = f"_{var.name}_ft"
            return [*self._write_func_ptr_typedef(typedef_name, func_ptr), "", f"{typedef_name} {name}"]

        self._reset_unsupported()
        self._reject_rvalue("has", var.type)
        var_type = self._format_type(var.type)

        if isinstance(var.type, Array):
            dims = self._format_array_dims(var.type)
            name = f"{name}{dims}"

        return self._guard_unsupported(f"variable '{var.name}'", [f"{var_type} {name}"])

    @staticmethod
    def _as_func_ptr(typ: TypeExpr) -> FunctionPointer | None:
        """Return the FunctionPointer a type denotes, whether or not it is wrapped in a Pointer."""
        if isinstance(typ, FunctionPointer):
            return typ
        if isinstance(typ, Pointer) and isinstance(typ.pointee, FunctionPointer):
            return typ.pointee
        return None

    # -----------------------------------------------------------------
    # Constant
    # -----------------------------------------------------------------

    def _write_constant(self, const: Constant) -> list[str]:
        """Write a constant declaration."""
        name = self._escape_name(const.name, include_c_name=True)

        if const.type:
            type_str = self._format_ctype(const.type)
            if const.type.name == "char" and "const" in const.type.qualifiers:
                return [f"const char* {name}"]
            return [f"{type_str} {name}"]

        # Default to int for macros without detected type
        return [f"int {name}"]

    # -----------------------------------------------------------------
    # Type formatting
    # -----------------------------------------------------------------

    def _format_type(self, type_expr: TypeExpr) -> str:
        """Format a type expression as Cython string."""
        if isinstance(type_expr, CType):
            return self._format_ctype(type_expr)
        if isinstance(type_expr, Pointer):
            return self._format_pointer(type_expr)
        if isinstance(type_expr, Reference):
            return self._format_reference(type_expr)
        if isinstance(type_expr, Array):
            return self._format_array(type_expr)
        if isinstance(type_expr, FunctionPointer):
            return self._format_func_ptr(type_expr)
        return "void"

    def _format_reference(self, ref: Reference) -> str:
        """Format a Reference type."""
        target = self._format_type(ref.target)
        quals = f"{' '.join(ref.qualifiers)} " if ref.qualifiers else ""
        ref_symbol = "&&" if ref.is_rvalue else "&"
        return f"{quals}{target}{ref_symbol}"

    def _format_ctype(self, ctype: CType) -> str:
        """Format a CType.

        Strips struct/union/enum prefixes for declared types, strips
        unsupported qualifiers, resolves inner typedefs, and maps C types
        to Cython equivalents.
        """
        name = ctype.name

        # Map C types to Cython equivalents
        if name in C_TO_CYTHON_TYPE_MAP:
            name = C_TO_CYTHON_TYPE_MAP[name]

        # Strip the C elaborated-type-specifier keyword. This function only
        # ever renders a type *use* (field type, parameter, return type), and
        # Cython rejects `struct X` in every one of those positions -- it wants
        # the bare name and takes the keyword only on the declaration itself.
        # The tag is therefore dropped even when the record is not declared in
        # this translation unit, which is exactly the nested-record case
        # (`struct view data` inside a class) that no known_* set can cover.
        # A remainder containing a space is not an identifier (libclang spells
        # anonymous records `struct (anonymous at f.h:1)`); those keep the tag
        # so the existing unrepresentable-name diagnostics still fire on them.
        # This runs before collision resolution below: libclang spells a field's
        # type `struct dup`, which matches no collision key while the tag is
        # attached, so the renamed record would be referenced by a dead name.
        for tag in ("struct ", "union ", "enum "):
            if name.startswith(tag):
                bare = name[len(tag) :]
                if bare and " " not in bare:
                    name = bare
                break

        # C++ dependent names ("A::B<T>::member") need their own conversion:
        # prefix stripping alone leaves a "::" that Cython cannot parse.
        if "::" in name:
            # A qualified use of a renamed collision names its namespace
            # outright, so it resolves without the enclosing-block context.
            qualifier, _, tail = name.rpartition("::")
            if (qualifier, tail) in self._collision_names:
                return self._collision_names[(qualifier, tail)]
            resolved = self._resolve_dependent_name(name)
            if resolved is not None:
                return resolved

        name = _strip_namespace_qualifiers(name)

        # An unqualified use of a colliding name means the one declared in the
        # namespace of the record being rendered.
        if name in self._colliding_bare_names:
            name = self._resolve_collision(self._current_namespace, name)

        # Resolve inner typedefs
        if self._current_inner_typedefs and name in self._current_inner_typedefs:
            name = self._current_inner_typedefs[name]

        # Strip unsupported type qualifiers
        for qual in UNSUPPORTED_TYPE_QUALIFIERS:
            name = name.replace(f"{qual} ", "")
            if name.endswith(f" {qual}"):
                name = name[: -(len(qual) + 1)]
            prefix = f"{qual}("
            if name.startswith(prefix) and name.endswith(")"):
                name = name[len(prefix) : -1]

        # Convert C++ template syntax <> to Cython syntax []
        if "<" in name and ">" in name:
            name = self._convert_template_syntax(name)

        # Escape keywords in type names
        parts = name.split()
        escaped_parts = [self._escape_name(p) for p in parts]
        name = " ".join(escaped_parts)

        if ctype.qualifiers:
            filtered_quals = [q for q in ctype.qualifiers if q not in UNSUPPORTED_TYPE_QUALIFIERS]
            new_quals = []
            for q in filtered_quals:
                if q not in parts:
                    new_quals.append(q)
            if new_quals:
                quals = " ".join(new_quals)
                self._note_bool_spelling(name)
                return f"{quals} {name}"
        self._note_bool_spelling(name)
        return name

    def _note_bool_spelling(self, rendered: str) -> None:
        """Record that a formatted type spells ``bool``, so the cimport is emitted.

        *rendered* may be a bare name, a qualified one, or a Cython template
        subscript such as ``vector[bool]``, so every identifier in it is
        considered rather than the string as a whole.
        """
        if "bool" not in rendered:
            return
        identifier = "".join(ch if ch.isalnum() or ch == "_" else " " for ch in rendered)
        if "bool" in identifier.split():
            self.libcpp_cimports.setdefault(_BOOL_CIMPORT_MODULE, set()).add("bool")

    def _format_pointer(self, ptr: Pointer) -> str:
        """Format a Pointer type."""
        if isinstance(ptr.pointee, FunctionPointer):
            return self._format_func_ptr_as_ptr(ptr.pointee, ptr.qualifiers)

        unwrapped = self._unwrap_func_ptr(ptr.pointee)
        if unwrapped is not None:
            fp, inner_stars = unwrapped
            return_type = self._format_type(fp.return_type)
            params = self._format_params(fp.parameters, fp.is_variadic)
            if not params:
                params = "void"
            result = f"{return_type} ({'*' * (inner_stars + 1)})({params})"
            if ptr.qualifiers:
                quals = " ".join(ptr.qualifiers)
                result = f"{result} {quals}"
            return result

        pointee = self._format_type(ptr.pointee)
        result = f"{pointee}*"
        if ptr.qualifiers:
            quals = " ".join(ptr.qualifiers)
            result = f"{result} {quals}"
        return result

    def _format_array(self, arr: Array) -> str:
        """Format an Array type (element type only; dimensions added by caller)."""
        return self._format_type(arr.element_type)

    def _is_incomplete_value_type(self, typ: TypeExpr) -> bool:
        """Check if a type is an incomplete struct used as a value."""
        if isinstance(typ, CType):
            name = typ.name
            if name.startswith("struct "):
                struct_name = name[7:]
            else:
                struct_name = name
            if struct_name in self.incomplete_structs:
                return True
            if struct_name in self.undeclared_structs:
                return True
        return False

    def _is_nested_func_ptr(self, fp: FunctionPointer) -> bool:
        """Check if a function pointer returns another function pointer."""
        if isinstance(fp.return_type, FunctionPointer):
            return True
        return isinstance(fp.return_type, Pointer) and isinstance(fp.return_type.pointee, FunctionPointer)

    @staticmethod
    def _unwrap_func_ptr(typ: TypeExpr) -> tuple[FunctionPointer, int] | None:
        """Unwrap a pointer chain that bottoms out in a function pointer.

        A bare :class:`FunctionPointer` and a ``Pointer`` wrapping one both mean
        a single-star declarator, so the star count is the pointer depth floored
        at one.

        :returns: The function pointer and the number of stars its declarator
            needs, or ``None`` if ``typ`` is not a function pointer.
        """
        stars = 0
        current = typ
        while isinstance(current, Pointer):
            stars += 1
            current = current.pointee
        if isinstance(current, FunctionPointer):
            return current, max(stars, 1)
        return None

    def _format_func_ptr(self, fp: FunctionPointer, name: str | None = None, stars: int = 1) -> str:
        """Format a FunctionPointer type."""
        return_type = self._format_type(fp.return_type)
        params = self._format_params(fp.parameters, fp.is_variadic)
        declarator = "*" * stars + (name or "")
        return f"{return_type} ({declarator})({params})"

    def _format_func_ptr_as_ptr(self, fp: FunctionPointer, ptr_quals: list[str]) -> str:
        """Format a pointer to function pointer."""
        return_type = self._format_type(fp.return_type)
        params = self._format_params(fp.parameters, fp.is_variadic)
        result = f"{return_type} (*)({params})"
        if ptr_quals:
            quals = " ".join(ptr_quals)
            result = f"{result} {quals}"
        return result

    def _format_params(self, params: list[Parameter], is_variadic: bool) -> str:
        """Format function parameters."""
        parts: list[str] = []
        for param in params:
            if param.name:
                name = self._escape_name(param.name)
                unwrapped = self._unwrap_func_ptr(param.type)
                if unwrapped is not None:
                    fp, stars = unwrapped
                    parts.append(self._format_func_ptr(fp, name, stars))
                elif isinstance(param.type, Array):
                    param_type = self._format_type(param.type)
                    dims = self._format_array_dims(param.type)
                    parts.append(f"{param_type} {name}{dims}")
                else:
                    param_type = self._format_type(param.type)
                    parts.append(f"{param_type} {name}")
            else:
                param_type = self._format_type(param.type)
                parts.append(param_type)

        if is_variadic:
            parts.append("...")

        return ", ".join(parts)

    def _format_array_dims(self, arr: Array) -> str:
        """Format array dimensions for variable/field names."""
        dims: list[str] = []
        current: TypeExpr = arr
        while isinstance(current, Array):
            if current.size is not None:
                dims.append(str(current.size))
            else:
                dims.append("")
            current = current.element_type
        return "".join(f"[{d}]" for d in dims)

    @staticmethod
    def _split_qualified(name: str) -> list[str]:
        """Split a C++ qualified name on ``::`` at template-nesting depth zero.

        Splitting on every ``::`` would cut inside template arguments, so
        ``A<B::C>::d`` must yield ``["A<B::C>", "d"]``, not three parts.
        """
        parts: list[str] = []
        depth = 0
        start = 0
        i = 0
        while i < len(name):
            char = name[i]
            if char == "<":
                depth += 1
            elif char == ">":
                depth -= 1
            elif char == ":" and depth == 0 and name[i + 1 : i + 2] == ":":
                parts.append(name[start:i])
                i += 2
                start = i
                continue
            i += 1
        parts.append(name[start:])
        return parts

    def _resolve_dependent_name(self, name: str) -> str | None:
        """Resolve a C++ dependent name to its Cython spelling.

        ``typename types::remove_reference<T>::type`` becomes
        ``remove_reference[T].type``: ``typename`` is dropped because Cython has
        no such keyword, the namespace qualifier moves to the enclosing
        ``cdef extern ... namespace`` block, ``::`` becomes ``.`` and ``<>``
        becomes ``[]``. This is safe because an ``extern`` block is never
        re-emitted into the generated C++ -- Cython consults it only to resolve
        types at use sites, where every template argument is already concrete.

        :returns: The Cython spelling, or None when *name* is not a dependent
            name or cannot be represented. An unrepresentable name is recorded
            in :attr:`_unrepresentable_dependents` so the caller can skip the
            declaration with a diagnostic.
        """
        stripped = name.removeprefix("typename ").strip()
        segments = self._split_qualified(stripped)
        if len(segments) < 2:
            return None

        member = segments[-1].strip()
        root = segments[-2].strip()

        if "<" not in root:
            if root in self._current_template_params:
                # ``typename T::member``. Cython rejects deferred access on a
                # template argument outright: ``'T' is not a cimported module``.
                conventional = CONVENTIONAL_DEPENDENT_SPELLINGS.get(member)
                if conventional is not None:
                    self._dependent_deviations.append((stripped, conventional))
                    return conventional
                self._unrepresentable_dependents.append(
                    (stripped, f"Cython cannot express a nested name on template parameter '{root}'")
                )
                return None
            # A plain namespace or class qualifier, handled by prefix stripping.
            return None

        base = root[: root.index("<")].strip()
        args = root[root.index("<") :]

        if member not in self._template_members.get(base, set()):
            self._unrepresentable_dependents.append(
                (stripped, f"'{base}' does not declare a nested '{member}' in this translation unit")
            )
            return None

        trailing = "".join(f".{seg.strip()}" for seg in segments[-1:])
        return f"{base}{self._convert_template_syntax(args)}{trailing}"

    def _format_base_name(self, name: str, owner_namespace: str | None) -> str | None:
        """Render a C++ base-class name in its Cython spelling.

        Base names deliberately bypass :meth:`_format_ctype`. A base specifier
        is a class name, never an arbitrary type expression: it carries no
        ``const``, no ``struct``/``enum`` prefix and no C builtin, and it must
        not be rewritten through the *derived* class's inner typedefs, which is
        the context ``_format_ctype`` resolves against while a class body is
        being written. Only three of its transformations apply to a base --
        namespace stripping, ``<>`` to ``[]``, and keyword escaping -- so they
        are applied directly here instead of routing through machinery whose
        remaining steps are at best inert and at worst wrong.

        The namespace qualifier is dropped even when the base lives in a
        *different* namespace from the derived class. An inheritance list in a
        ``.pxd`` is never re-emitted into the generated C++ -- only the derived
        class's own name is -- so Cython uses it solely to resolve inherited
        members against the entry it already declared for that base under its
        own ``namespace`` block.

        :returns: The Cython spelling, or None when the base cannot be named --
            it is not declared in this translation unit and has no known
            cimport, or its ``cdef extern`` block is emitted after the derived
            class's. In either case the caller must emit a diagnostic rather
            than a name that Cython would resolve to nothing.
        """
        stripped = _strip_namespace_qualifiers(name.strip())
        head = stripped.split("<", 1)[0].strip()
        if not head:
            return None
        # Only a record this writer emits as a cppclass, or a cimported C++
        # type, is usable as a base. A ctypedef or a union is not, and naming
        # one would make Cython reject the whole block.
        if not (
            head in self._record_names
            or get_libcpp_module_for_type(head) is not None
            or get_stub_module_for_type(head) is not None
        ):
            return None
        # A base named before its own block has been emitted is not a Cython
        # error: the subclass just silently gets no inherited members.
        # _order_namespaces prevents this except across an inheritance cycle
        # between namespaces, which is what remains to be caught here.
        base_ns = self._record_namespace.get(head, owner_namespace)
        if base_ns != owner_namespace and base_ns in self._namespace_order and owner_namespace in self._namespace_order:
            if self._namespace_order.index(base_ns) > self._namespace_order.index(owner_namespace):
                return None

        escaped = self._escape_name(head)
        return f"{escaped}{self._convert_template_syntax(stripped[len(head) :])}"

    def _convert_template_syntax(self, name: str) -> str:
        """Convert C++ template syntax ``<>`` to Cython syntax ``[]``."""
        result: list[str] = []
        i = 0
        depth = 0

        while i < len(name):
            char = name[i]

            if char in ("(", ")"):
                result.append(char)
                i += 1
            elif char == "<":
                is_template = False
                if i > 0:
                    prev = name[i - 1]
                    if prev.isalnum() or prev == "_" or prev == "]":
                        is_template = True
                else:
                    is_template = True

                if is_template:
                    result.append("[")
                    depth += 1
                else:
                    result.append(char)
                i += 1
            elif char == ">":
                if depth > 0:
                    result.append("]")
                    depth -= 1
                else:
                    result.append(char)
                i += 1
            else:
                result.append(char)
                i += 1

        return "".join(result)

    def _escape_name(self, name: str | None, include_c_name: bool = False) -> str:
        """Escape Python/Cython keywords by adding underscore suffix."""
        if name is None:
            return ""

        if name in keywords:
            if include_c_name:
                return f'{name}_ "{name}"'
            return f"{name}_"

        return name


# =====================================================================
# Public convenience function
# =====================================================================


def write_pxd(header: Header, *, stub_cimport_prefix: str | None = "headerkit.stubs") -> str:
    """Convenience function to convert a Header IR to Cython .pxd format.

    :param header: The Header IR to convert
    :param stub_cimport_prefix: If set, emit cimport lines for stub types
        (e.g., "headerkit.stubs" -> "from headerkit.stubs.stdarg cimport va_list").
        Defaults to "headerkit.stubs". Pass None to suppress stub cimports.
    :return: The generated .pxd file contents as a string
    """
    writer = PxdWriter(header, stub_cimport_prefix=stub_cimport_prefix)
    return writer.write()


# =====================================================================
# WriterBackend wrapper
# =====================================================================


class CythonWriter(BaseWriter):
    """Writer that converts Header IR into Cython .pxd declarations.

    Example::

        from headerkit.writers import get_writer

        writer = get_writer("cython")
        pxd_string = writer.write(header)
    """

    name: str = "cython"
    format_description: str = "Cython .pxd declarations for C/C++ interop"
    default_output_pattern: str = "{dir}/{stem}.pxd"
    default_extension: str = ".pxd"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project")
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
        WriterOption(
            name="stub_cimport_prefix",
            description="Package prefix for Cython stub cimports",
            default="headerkit.stubs",
            type=str,
        ),
    )

    def __init__(self, *, stub_cimport_prefix: str | None = "headerkit.stubs") -> None:
        self.stub_cimport_prefix: str | None = stub_cimport_prefix

    def _render(self, unit: SourceUnit | Header) -> str:
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        writer = PxdWriter(header, stub_cimport_prefix=self.stub_cimport_prefix)
        return writer.write()

    def write(self, header: Header) -> str:
        """Convert header IR to Cython .pxd string."""
        return self._render(self._prepare(header))

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        pxd_code = self._render(unit)

        pyproject = textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=61.0", "Cython>=3.0"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "{pkg}"
            version = "0.1.0"
            description = "Cython bindings for {pkg}"
            requires-python = ">=3.9"

            [tool.setuptools.packages.find]
            where = ["src"]
        """)

        init_py = textwrap.dedent(f"""\
            \"\"\"{pkg} package initialization.\"\"\"
            from {pkg} import {pkg}

            __all__ = ["{pkg}"]
        """)

        pyx_code = textwrap.dedent(f"""\
            # cython: language_level=3
            cimport {pkg}.{pkg} as c_{pkg}

            # High-level Python wrappers can be exposed here
        """)

        files = [
            OutputFile(path="pyproject.toml", content=pyproject),
            OutputFile(path=f"src/{pkg}/__init__.py", content=init_py),
            OutputFile(path=f"src/{pkg}/{pkg}.pxd", content=pxd_code),
            OutputFile(path=f"src/{pkg}/{pkg}.pyx", content=pyx_code),
        ]

        if test_type in ("both", "tripwire"):
            tripwire = textwrap.dedent(f"""\
                import pytest

                @pytest.mark.tripwire
                def test_cython_tripwire():
                    \"\"\"Tripwire: verify cython wrapper import.\"\"\"
                    try:
                        import {pkg}
                    except ImportError:
                        pytest.fail("Failed to import cython wrapper module '{pkg}'")
            """)
            files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

        if test_type in ("both", "unit"):
            unit_test = textwrap.dedent(f"""\
                import inspect
                import {pkg}

                def test_{pkg}_package_structure():
                    \"\"\"Verify package import and module structure.\"\"\"
                    assert inspect.ismodule({pkg})
                    assert hasattr({pkg}, "{pkg}")
            """)
            files.append(OutputFile(path="tests/test_wrapper.py", content=unit_test))

        return ProjectLayout(files=files)

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Cython comments."""
        return "# {line}"


# Uses bottom-of-module self-registration (same pattern as cffi.py).
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "cython",
    CythonWriter,
    description="Cython .pxd declarations for C/C++ interop",
)
