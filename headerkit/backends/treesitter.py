"""Tree-sitter based parser backend for C and C++ headers."""

from __future__ import annotations

import dataclasses
import logging
import os
import warnings
from collections.abc import Sequence
from typing import Any

from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    Array,
    BaseSpecifier,
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    ParserBackend,
    Pointer,
    Reference,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)

logger = logging.getLogger("headerkit.backends.treesitter")


def _normalize_path(path: str) -> str:
    """Normalize a file path for platform-agnostic comparison."""
    return path.replace("\\", "/").lower()


def _resolve_path(path: str, search_dirs: Sequence[str] = ()) -> str:
    """Resolve a path to an absolute, symlink-free, comparison-ready form.

    Mirrors the libclang backend's allowlist and denylist resolution rule so both backends
    agree on what an allowlist or denylist entry names: an absolute entry is used as-is, a
    relative entry (including a bare basename) is tried against each search
    directory in order, then against the process cwd.
    """
    if not os.path.isabs(path):
        for directory in search_dirs:
            candidate = os.path.join(directory, path)
            if os.path.exists(candidate):
                path = candidate
                break
    return _normalize_path(os.path.realpath(os.path.abspath(path)))


def _names_other_file(entries: Sequence[str], filename: str, include_dirs: Sequence[str] | None) -> bool:
    """True if any allowlist/denylist entry resolves to a file other than ``filename``."""
    parsed_dir = os.path.dirname(os.path.abspath(filename)) or os.getcwd()
    search_dirs = [parsed_dir]
    if include_dirs:
        search_dirs.extend(include_dirs)
    target = _resolve_path(filename)

    def names_target(entry: str) -> bool:
        if _resolve_path(entry, search_dirs) == target:
            return True
        # This backend parses a string, so the parsed file often does not exist on
        # disk and the existence-driven search above cannot reach it.  A bare
        # basename still names the parsed file in that case.
        return not os.path.isabs(entry) and _resolve_path(os.path.join(parsed_dir, entry)) == target

    return any(not names_target(entry) for entry in entries)


_HAS_TREESITTER: bool = False
_HAS_TREESITTER_C: bool = False
_HAS_TREESITTER_CPP: bool = False

try:
    from tree_sitter import Language, Node, Parser

    _HAS_TREESITTER = True
except ImportError:
    Node = Any  # type: ignore[misc,assignment]

try:
    import tree_sitter_c as tsc

    _HAS_TREESITTER_C = True
except ImportError:
    pass

try:
    import tree_sitter_cpp as tscpp

    _HAS_TREESITTER_CPP = True
except ImportError:
    pass


_FOLDABLE_TYPE_QUALIFIERS = frozenset({"const", "volatile"})
_SIGNEDNESS_SPECIFIERS = frozenset({"unsigned", "signed"})


def _node_text(node: Any) -> str:
    if node is None:
        return ""
    raw = getattr(node, "text", None)
    if raw is None:
        return ""
    if isinstance(raw, bytes | bytearray):
        return raw.decode("utf-8")
    return str(raw)


_PACKED_SPELLINGS = frozenset({"packed", "__packed__"})


def _attribute_names(spec: Any) -> set[str]:
    """Collect the attribute names inside one ``attribute_specifier``.

    The names are read from the grammar's own nodes, never from the spelling of
    the specifier as a whole. ``__attribute__((packed))`` puts a bare
    ``identifier`` under the ``argument_list``; ``__attribute__((aligned(16)))``
    puts a ``call_expression`` whose first child is the identifier. Reading the
    specifier's text instead would match ``aligned`` inside a name such as
    ``packed_size`` and would not survive a macro-spelled attribute.
    """
    names: set[str] = set()
    for arglist in (c for c in spec.children if c.type == "argument_list"):
        for item in arglist.children:
            if item.type == "identifier":
                names.add(_node_text(item))
            elif item.type == "call_expression":
                fn = item.child_by_field_name("function")
                target = fn if fn is not None else (item.children[0] if item.children else None)
                if target is not None and target.type == "identifier":
                    names.add(_node_text(target))
    return names


def _record_has_packed_attribute(node: Any) -> bool:
    """Report whether a record carries ``__attribute__((packed))``.

    Both spellings are covered by scanning every ``attribute_specifier`` child:
    the prefix form sits between the ``struct`` keyword and the tag name, the
    suffix form after the field list, and both are children of the same
    ``struct_specifier``.
    """
    return any(
        _PACKED_SPELLINGS & _attribute_names(child) for child in node.children if child.type == "attribute_specifier"
    )


def _split_pragma_arg(text: str) -> list[str]:
    """Split a ``preproc_arg`` payload into words, numbers, and punctuation.

    tree-sitter-c does not descend into a pragma's argument: it hands back one
    opaque ``preproc_arg`` leaf. Only that already-isolated leaf is tokenized
    here, and its contents (``pack(push, 1)``) are a flat token list, not a
    context-free construct. No declaration, type, or scope is recovered from
    source text -- those all still come from the parse tree.
    """
    tokens: list[str] = []
    current = ""
    for ch in text:
        if ch.isalnum() or ch == "_":
            current += ch
            continue
        if current:
            tokens.append(current)
            current = ""
        if not ch.isspace():
            tokens.append(ch)
    if current:
        tokens.append(current)
    return tokens


#: Nodes whose children are mutually exclusive preprocessor branches. Only one
#: of them survives translation, and which one is not knowable without
#: evaluating the condition, so none of them may contribute to a running state.
_CONDITIONAL_PREPROC = frozenset(
    {
        "preproc_if",
        "preproc_ifdef",
        "preproc_elif",
        "preproc_else",
        "preproc_elifdef",
    }
)


def _pack_pragma_words(node: Any) -> list[str] | None:
    """The words of a ``#pragma pack`` directive, or None for any other node."""
    if node.type != "preproc_call":
        return None
    directive = node.child_by_field_name("directive")
    if directive is None or _node_text(directive).strip() != "#pragma":
        return None
    arg = node.child_by_field_name("argument")
    tokens = _split_pragma_arg(_node_text(arg)) if arg is not None else []
    if not tokens or tokens[0] != "pack":
        return None
    return [t for t in tokens[1:] if t not in "(),"]


def _first_conditional_pack(node: Any) -> int | None:
    """Start offset of the first ``#pragma pack`` anywhere under ``node``."""
    if _pack_pragma_words(node) is not None:
        offset: int = node.start_byte
        return offset
    for child in node.children:
        found = _first_conditional_pack(child)
        if found is not None:
            return found
    return None


def _pack_regions(root: Any) -> tuple[list[tuple[int, int | None]], list[tuple[int, int | None]]]:
    """Map source positions to the ``#pragma pack`` alignment in force there.

    Returns ``(start_byte, alignment)`` pairs in ascending order, where
    ``alignment`` is ``None`` for the compiler default, together with the byte
    ranges over which the pack state is unknown. A record is matched to a
    region by its own start offset, which is what gives the pragma its scope:
    ``#pragma pack(1)`` applies to records that carry no attribute of their
    own, and ``#pragma pack()`` or ``pop`` ends that scope.

    Conditional branches are not descended into. ``#ifdef _MSC_VER / #pragma
    pack(push, 1) / #else / #pragma pack(4) / #endif`` has two mutually
    exclusive answers, and walking both would leave whichever ``#endif`` came
    last in force -- an alignment no translation of the header ever has.

    The uncertainty such a block creates is bounded, not permanent. It ends at
    the next pragma that states the pack state outright -- ``pack(N)``,
    ``pack()`` or ``pack(pop)`` -- because from there the alignment is the same
    whichever branch the preprocessor took. Running the doubt to end of file
    instead would put a note on every later record in a header using the
    ordinary ``#ifdef _MSC_VER`` guard, which is most of them.
    """
    regions: list[tuple[int, int | None]] = []
    stack: list[int | None] = []
    current: int | None = None
    unknown: list[tuple[int, int | None]] = []

    def visit(node: Any) -> None:
        nonlocal current
        if node.type in _CONDITIONAL_PREPROC:
            found = _first_conditional_pack(node)
            if found is not None and (not unknown or unknown[-1][1] is not None):
                unknown.append((found, None))
            return
        words = _pack_pragma_words(node)
        if words is not None:
            if unknown and unknown[-1][1] is None:
                unknown[-1] = (unknown[-1][0], node.end_byte)
            if not words:  # pragma pack() -- reset to default
                current = None
            elif words[0] == "push":
                stack.append(current)
                # MSVC allows an identifier between ``push`` and the alignment
                # (``pack(push, mylabel, 1)``), which GCC and Clang accept too
                # and which is pervasive in Windows-targeting headers. The
                # alignment is the numeric argument wherever it sits; a bare
                # ``pack(push)`` carries none and keeps the current value.
                numbers = [w for w in words[1:] if w.isdigit()]
                if numbers:
                    current = int(numbers[-1])
            elif words[0] == "pop":
                current = stack.pop() if stack else None
            elif words[0].isdigit():
                current = int(words[0])
            regions.append((node.end_byte, current))
        for child in node.children:
            visit(child)

    visit(root)
    regions.sort(key=lambda r: r[0])
    return regions, unknown


def _pack_at(regions: list[tuple[int, int | None]], offset: int) -> int | None:
    """Return the ``#pragma pack`` alignment in force at a byte offset."""
    value: int | None = None
    for start, alignment in regions:
        if start > offset:
            break
        value = alignment
    return value


def _pointer_qualifiers(node: Node) -> list[str]:
    """Qualifiers borne by the pointer itself, read off a ``pointer_declarator``.

    In ``char* const p`` the grammar makes ``const`` a child of the
    ``pointer_declarator``, which is what distinguishes a const pointer from a
    pointer to const. Only children of that node are read, so a trailing
    qualifier such as the ``const`` of a C++ ``int f() const`` -- which hangs off
    the ``function_declarator`` -- can never reach here. Qualifiers the IR does
    not model (``_Atomic``, ``restrict``) are also ``type_qualifier`` nodes and
    are dropped, matching :meth:`TreeSitterBackend._qualified_type`.
    """
    return [
        text
        for child in node.children
        if child.type in ("type_qualifier", "const", "volatile")
        and (text := _node_text(child).strip()) in _FOLDABLE_TYPE_QUALIFIERS
    ]


def _recovered_bitfield_clause(field_decl: Node) -> Node | None:
    """The ``ERROR`` node tree-sitter-c emits in place of an unnamed bitfield.

    ``field_declaration`` requires a declarator, so for ``unsigned int : 8`` the
    parser inserts a MISSING ``field_identifier`` and the ``bitfield_clause``
    parses normally. That recovery is unavailable when the type ends in a size
    keyword -- ``unsigned short``, ``unsigned long``, ``unsigned long long``,
    ``long long`` -- because the parser can still accept a further
    ``primitive_type`` at that point and so cannot commit to the missing
    declarator. It emits an ``ERROR`` wrapping the ``:`` and the width instead.
    Both shapes denote the same C11 6.7.2.1p12 padding, and the ``ERROR`` one is
    recognized by its children rather than by its text.

    Returns the ``ERROR`` node when it is shaped exactly like a bitfield clause
    -- ``:`` followed by one expression -- otherwise ``None``. Invalid C also
    yields an ``ERROR`` child holding a number, and reading a width out of one
    would fabricate padding the source never declared.
    """
    for child in field_decl.children:
        if child.type != "ERROR":
            continue
        parts = list(child.children)
        if len(parts) == 2 and parts[0].type == ":" and parts[1].is_named:
            return child
    return None


class TreeSitterBackend:
    """Parser backend using tree-sitter-c and tree-sitter-cpp."""

    supported_classifications: frozenset[str] = frozenset({"header", "source"})

    @property
    def supported_languages(self) -> frozenset[str]:
        langs = set()
        if _HAS_TREESITTER_C:
            langs.add("c")
        if _HAS_TREESITTER_CPP:
            langs.update({"c", "c++", "cpp"})
        return frozenset(langs)

    @property
    def name(self) -> str:
        return "tree-sitter"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return _HAS_TREESITTER_CPP

    def __init__(self) -> None:
        self._defined_records: set[str] = set()
        self._forward_records: dict[str, Struct] = {}
        self._defined_enums: set[str] = set()
        self._forward_enums: dict[str, Enum] = {}
        self._seen_typedefs: set[str] = set()
        self._lifted_declarations: list[Declaration] = []
        self._filled_forward: Struct | None = None
        self._pack_regions: list[tuple[int, int | None]] = []
        #: Byte ranges over which a ``#pragma pack`` inside a conditional
        #: preprocessor branch leaves the alignment unknowable. Each ends at the
        #: next pragma that states the pack state outright, so the doubt covers
        #: the records it can actually affect rather than the rest of the file.
        self._unknown_pack_ranges: list[tuple[int, int | None]] = []

    def is_available(self) -> bool:
        return _HAS_TREESITTER and (_HAS_TREESITTER_C or _HAS_TREESITTER_CPP)

    def _pack_notes(self, node: Any, *, has_packed_attribute: bool, pack_alignment: int | None) -> list[str]:
        """Packing facts about a record that ``is_packed`` cannot carry.

        An attribute on the record settles the question outright, so neither
        note applies to one that carries it -- the same order the libclang
        backend uses.
        """
        if has_packed_attribute:
            return []
        notes: list[str] = []
        if pack_alignment is not None and pack_alignment > 1:
            # The libclang backend reaches the same conclusion from the recorded
            # layout and can compare the members' natural alignment against it;
            # this backend has no layout engine and reports the pragma itself.
            # A record whose members are all narrower than the pragma is
            # therefore noted here and not there.
            notes.append(
                f"Record is under an intermediate '#pragma pack({pack_alignment})', "
                "which squeezes the layout without flattening it; is_packed cannot express that."
            )
        if any(
            start < node.start_byte and (end is None or node.start_byte < end)
            for start, end in self._unknown_pack_ranges
        ):
            notes.append(
                "A '#pragma pack' appears inside a conditional preprocessor branch "
                "earlier in this file. Which branch applies is not knowable without "
                "evaluating the condition, so the packing of this record is unverified."
            )
        return notes

    def _is_cpp_mode(self, code: str, filename: str, extra_args: list[str] | None = None) -> bool:
        if extra_args:
            for i, arg in enumerate(extra_args):
                if arg == "-x" and i + 1 < len(extra_args):
                    if extra_args[i + 1] in ("c++", "cpp"):
                        return True
                    if extra_args[i + 1] == "c":
                        return False
                if arg.startswith("-std=c++") or arg.startswith("-std=gnu++"):
                    return True
                if (arg.startswith("-std=c") or arg.startswith("-std=gnu")) and not (
                    arg.startswith("-std=c++") or arg.startswith("-std=gnu++")
                ):
                    return False
        ext = filename.lower()
        if ext.endswith((".hpp", ".hh", ".hxx", ".h++", ".cpp", ".cc", ".cxx", ".c++", ".cpptest")):
            return True
        if _HAS_TREESITTER_CPP:
            if "class " in code or "namespace " in code or "template<" in code or "template <" in code:
                return True
        return False

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
        """Parse C/C++ code with tree-sitter and return the IR representation.

        This backend parses exactly the ``code`` string it is given. It does not
        read the filesystem and does not follow ``#include`` directives, so
        ``include_dirs``, ``recursive_includes``, ``max_depth``,
        ``project_prefixes``, ``allowlist`` and ``denylist`` have nothing to act
        on.

        :param allowlist: **Not honored by this backend.** An allowlist selects
            which included files keep their declarations; since no declaration
            here can originate from an ``#include``, there is nothing to select.
            An entry naming a file other than the one being parsed raises
            :class:`UserWarning` rather than being discarded silently, because
            such a caller is expecting symbols this backend will never produce.
            Entries that all resolve to the parsed file itself are already
            satisfied and warn nothing. Use the libclang backend when allowlist
            filtering is required.
        :param denylist: **Not honored by this backend**, for the same reason and
            with the same warning. An entry naming another file describes a
            declaration this backend was never going to emit, so honoring it and
            ignoring it are indistinguishable -- which is exactly the silence the
            warning breaks. Entries resolving to the parsed file itself warn
            nothing, because this backend does not deny the parsed file either.
        """
        for label, entries in (("allowlist", allowlist), ("denylist", denylist)):
            if entries and _names_other_file(entries, filename, include_dirs):
                warnings.warn(
                    f"The tree-sitter backend does not follow #include directives, so the "
                    f"{label} {entries!r} cannot be honored and no declarations from "
                    f"those files will appear in the result. Use the libclang backend for "
                    f"{label} support.",
                    UserWarning,
                    stacklevel=2,
                )
        if not self.is_available():
            msg = "tree-sitter is not installed. Install with: pip install 'headerkit[treesitter]'"
            raise RuntimeError(msg)

        is_cpp = self._is_cpp_mode(code, filename, extra_args)

        if is_cpp:
            if not _HAS_TREESITTER_CPP:
                msg = "tree-sitter-cpp is not installed. Install with: pip install 'headerkit[treesitter]'"
                raise RuntimeError(msg)
            language = Language(tscpp.language())
        elif _HAS_TREESITTER_C:
            language = Language(tsc.language())
        elif _HAS_TREESITTER_CPP:
            language = Language(tscpp.language())
        else:
            msg = "tree-sitter-c or tree-sitter-cpp is not installed. Install with: pip install 'headerkit[treesitter]'"
            raise RuntimeError(msg)

        parser = Parser(language)
        tree = parser.parse(code.encode("utf-8"))

        self._defined_records = set()
        self._forward_records = {}
        self._defined_enums = set()
        self._forward_enums = {}
        self._seen_typedefs = set()
        self._lifted_declarations = []
        self._filled_forward = None
        self._pack_regions, self._unknown_pack_ranges = _pack_regions(tree.root_node)

        declarations: list[Declaration] = []
        for child in tree.root_node.children:
            decls = self._convert_top_level(child, filename, is_cpp=is_cpp)
            # A record synthesized for a named member of an anonymous type must
            # precede the record that refers to it, or the reference names a tag
            # that has not been declared yet. An enum hoisted out of a class body
            # rides the same list and lands ahead of its class, matching libclang.
            declarations.extend(self._lifted_declarations)
            self._lifted_declarations = []
            declarations.extend(d for d in decls if self._keep_typedef(d))

        return Header(path=filename, declarations=declarations)

    def _keep_typedef(self, decl: Declaration) -> bool:
        """Report whether a top-level declaration is a typedef not already emitted.

        C++ and C11 both permit a typedef name to be redeclared with the same
        underlying type, so a translation unit can legitimately spell
        ``typedef int T;`` more than once. Each spelling is one declaration in
        the tree, and emitting all of them yields a Cython ``ctypedef`` that
        Cython reports as redeclared. libclang collapses them on a
        ``(kind, namespace, name)`` identity; this mirrors that identity so both
        backends agree.
        """
        if not isinstance(decl, Typedef):
            return True
        key = f"typedef:{decl.namespace or ''}:{decl.name}"
        if key in self._seen_typedefs:
            return False
        self._seen_typedefs.add(key)
        return True

    def _convert_top_level(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        if node.type in ("preproc_ifdef", "preproc_if"):
            results: list[Declaration] = []
            for child in node.children:
                # Do not walk mutually exclusive #elif/#else branches when traversing the primary #if branch
                if child.type in ("preproc_elif", "preproc_else"):
                    continue
                results.extend(
                    self._convert_top_level(
                        child,
                        filename,
                        namespace=namespace,
                        template_params=template_params,
                        is_cpp=is_cpp,
                    )
                )
            return results

        if node.type in ("preproc_elif", "preproc_else"):
            return []

        if node.type in ("linkage_specification", "declaration_list"):
            results = []
            for child in node.children:
                results.extend(
                    self._convert_top_level(
                        child,
                        filename,
                        namespace=namespace,
                        template_params=template_params,
                        is_cpp=is_cpp,
                    )
                )
            return results

        if node.type == "namespace_definition":
            return self._convert_namespace(node, filename, parent_namespace=namespace, is_cpp=is_cpp)

        if node.type == "template_declaration":
            return self._convert_template(node, filename, namespace=namespace, is_cpp=is_cpp)

        if node.type in ("class_specifier", "struct_specifier", "union_specifier"):
            st = self._convert_class_or_struct(
                node,
                filename,
                namespace=namespace,
                template_params=template_params,
                is_cpp=is_cpp,
            )
            return [st] if st else []

        if node.type == "alias_declaration":
            td = self._convert_alias_declaration(node, filename, namespace=namespace)
            return [td] if td else []

        if node.type == "declaration":
            return self._convert_declaration(
                node,
                filename,
                namespace=namespace,
                template_params=template_params,
                is_cpp=is_cpp,
            )

        if node.type == "function_definition":
            return self._convert_function_definition(
                node,
                filename,
                namespace=namespace,
                template_params=template_params,
                is_cpp=is_cpp,
            )

        if node.type == "type_definition":
            return self._convert_type_definition(node, filename, namespace=namespace)

        if node.type == "enum_specifier":
            en = self._convert_enum(node, filename, namespace=namespace)
            return [en] if en else []

        return []

    def _convert_namespace(
        self,
        node: Node,
        filename: str,
        *,
        parent_namespace: str | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        name_node = node.child_by_field_name("name")
        ns_name = _node_text(name_node).strip() if name_node else None
        current_ns = f"{parent_namespace}::{ns_name}" if parent_namespace and ns_name else (ns_name or parent_namespace)

        body_node = node.child_by_field_name("body")
        if not body_node:
            for child in node.children:
                if child.type == "declaration_list":
                    body_node = child
                    break

        results: list[Declaration] = []
        if body_node:
            for child in body_node.children:
                results.extend(
                    self._convert_top_level(
                        child,
                        filename,
                        namespace=current_ns,
                        is_cpp=is_cpp,
                    )
                )
        return results

    def _convert_template(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        t_params: list[str] = []
        param_list = node.child_by_field_name("parameters")
        if param_list:
            for child in param_list.children:
                if child.type in (
                    "type_parameter_declaration",
                    "optional_type_parameter_declaration",
                    "variadic_type_parameter_declaration",
                ):
                    name_child = child.child_by_field_name("name") or child.child_by_field_name("declarator")
                    if not name_child:
                        for sub in child.children:
                            if sub.type in ("type_identifier", "identifier"):
                                name_child = sub
                                break
                    if name_child:
                        t_params.append(_node_text(name_child).strip())
                elif child.type == "parameter_declaration":
                    decl = child.child_by_field_name("declarator")
                    if decl:
                        t_params.append(_node_text(decl).strip())

        results: list[Declaration] = []
        for child in node.children:
            if child.type in ("template", "template_parameter_list", "<", ">", ";"):
                continue
            results.extend(
                self._convert_top_level(
                    child,
                    filename,
                    namespace=namespace,
                    template_params=t_params,
                    is_cpp=is_cpp,
                )
            )
        return results

    def _convert_alias_declaration(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
    ) -> Typedef | None:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        if name_node and type_node:
            name = _node_text(name_node).strip()
            underlying = self._qualified_type(node, type_node, CType("int"))
            loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
            return Typedef(name=name, underlying_type=underlying, namespace=namespace, location=loc)
        return None

    def _apply_single_alias(
        self,
        record: Struct | Enum,
        declarator: Node,
        filename: str,
        *,
        namespace: str | None,
        tagged: bool,
    ) -> list[Declaration]:
        """Attach a one-declarator typedef alias to the record it defines.

        The tag is kept whenever the definition carries one. Renaming the record
        to the alias discards it, and ``typedef struct Foo { ... } FooAlias;``
        leaves ``Foo`` spellable only as ``struct Foo``; the alias then reaches
        the writers as a separate ``Typedef``, which is what libclang produces
        for the same input.

        ``is_typedef`` follows libclang's rule, which differs by declaration
        kind and is pinned by ``tests/test_regression_backend_parity.py``:

        * A **record** sets it when the bare name spells the type -- no tag, or
          an alias repeating the tag.
        * An **enum** sets it only when there is no tag at all. A tagged
          ``typedef enum Switch { ... } Switch;`` really does declare ``enum
          Switch``, and the cffi and Cython writers re-emit that tag; treating
          it as tag-less silently drops it from the regenerated header.
        """
        is_record = isinstance(record, Struct)
        # An enum's underlying spelling keeps the ``enum`` keyword, as libclang's
        # does: a writer that strips it back off gets ``Mode2 = Mode2``, and one
        # that needs the tag has it. A record's does not, matching libclang too.
        base = CType(record.name or "") if is_record else CType(f"enum {record.name}")
        alias_name, underlying_type, ident_node = self._unwrap_declarator(declarator, base)
        if not tagged:
            if alias_name:
                record.name = alias_name
            record.is_typedef = True
            return [record]

        record.is_typedef = is_record and alias_name == record.name
        # A tagged enum keeps its alias as a separate Typedef even when the two
        # spellings match, because ``is_typedef`` stays False for it and nothing
        # else would bind the name. A record in that position carries the alias
        # in the flag instead, and a second declaration would duplicate it.
        if not alias_name or (is_record and alias_name == record.name):
            return [record]
        loc_node = ident_node or declarator
        return [
            record,
            Typedef(
                name=alias_name,
                underlying_type=underlying_type,
                namespace=namespace,
                location=SourceLocation(
                    file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                ),
            ),
        ]

    def _convert_type_definition(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
    ) -> list[Declaration]:
        struct_node = node.child_by_field_name("type")
        declarators = node.children_by_field_name("declarator")
        if not declarators:
            single = node.child_by_field_name("declarator")
            if single:
                declarators = [single]

        if struct_node and struct_node.type in ("struct_specifier", "class_specifier", "union_specifier"):
            body_node = struct_node.child_by_field_name("body")
            if body_node:
                # A `typedef struct { ... } T;` names no tag, so the typedef
                # alias is the only qualifier available for tags synthesized
                # inside it.  Without it two typedefs each holding a member
                # named `pt` would both synthesize `_pt_s`.
                tag_qualifier = None
                if struct_node.child_by_field_name("name") is None and declarators:
                    tag_qualifier = self._unwrap_declarator(declarators[0], CType(""))[0]
                st = self._convert_class_or_struct(
                    struct_node, filename, namespace=namespace, tag_qualifier=tag_qualifier
                )
                # The definition may have been folded into an earlier `struct S;`
                # that is already in the output. The alias still applies to it,
                # but it is emitted once, not twice.
                folded = st is None and self._filled_forward is not None
                if folded:
                    st = self._filled_forward
                if st:
                    if len(declarators) == 1:
                        aliased = self._apply_single_alias(
                            st,
                            declarators[0],
                            filename,
                            namespace=namespace,
                            tagged=struct_node.child_by_field_name("name") is not None,
                        )
                        return aliased[1:] if folded else aliased
                    results: list[Declaration] = [] if folded else [st]
                    for d in declarators:
                        alias_name, underlying_type, ident_node = self._unwrap_declarator(d, CType(st.name or ""))
                        loc_node = ident_node or d
                        loc = SourceLocation(
                            file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                        )
                        if alias_name and alias_name != st.name:
                            results.append(
                                Typedef(
                                    name=alias_name, underlying_type=underlying_type, namespace=namespace, location=loc
                                )
                            )
                    return results
            else:
                st_fwd = self._convert_class_or_struct(struct_node, filename, namespace=namespace)
                base_type = self._qualified_type(node, struct_node, CType("int"))
                fwd_results: list[Declaration] = []
                if st_fwd:
                    fwd_results.append(st_fwd)
                for d in declarators:
                    alias_name, underlying_type, ident_node = self._unwrap_declarator(d, base_type)
                    loc_node = ident_node or d
                    loc = SourceLocation(
                        file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                    )
                    fwd_results.append(
                        Typedef(
                            name=alias_name or "",
                            underlying_type=underlying_type,
                            namespace=namespace,
                            location=loc,
                        )
                    )
                return fwd_results

        if struct_node and struct_node.type == "enum_specifier":
            body_node = struct_node.child_by_field_name("body")
            if body_node:
                en = self._convert_enum(struct_node, filename, namespace=namespace)
                if en:
                    if len(declarators) == 1:
                        return self._apply_single_alias(
                            en,
                            declarators[0],
                            filename,
                            namespace=namespace,
                            tagged=struct_node.child_by_field_name("name") is not None,
                        )
                    res_en: list[Declaration] = [en]
                    for d in declarators:
                        alias_name, underlying_type, ident_node = self._unwrap_declarator(d, CType(en.name or ""))
                        loc_node = ident_node or d
                        loc = SourceLocation(
                            file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                        )
                        if alias_name and alias_name != en.name:
                            res_en.append(
                                Typedef(
                                    name=alias_name, underlying_type=underlying_type, namespace=namespace, location=loc
                                )
                            )
                    return res_en

        if struct_node and declarators:
            base_type = self._qualified_type(node, struct_node, CType("int"))
            td_results: list[Declaration] = []
            for d in declarators:
                alias_name, underlying_type, ident_node = self._unwrap_declarator(d, base_type)
                loc_node = ident_node or d
                loc = SourceLocation(
                    file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                )
                td_results.append(
                    Typedef(name=alias_name or "", underlying_type=underlying_type, namespace=namespace, location=loc)
                )
            return td_results

        return []

    def _convert_function_definition(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        for child in node.children:
            if child.type == "storage_class_specifier" and _node_text(child).strip() == "static":
                return []

        type_node = node.child_by_field_name("type")
        declarator_node = node.child_by_field_name("declarator")

        if declarator_node:
            pointer_depth = 0
            curr: Node | None = declarator_node
            while curr and curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                pointer_depth += 1
                curr = curr.child_by_field_name("declarator")

            if curr and curr.type == "function_declarator":
                return self._convert_function_declarator(
                    self._qualified_type(node, type_node, CType("int")),
                    curr,
                    filename,
                    pointer_depth=pointer_depth,
                    namespace=namespace,
                    template_params=template_params,
                )

        return []

    def _is_function_declaration(self, declarator: Node) -> bool:
        curr: Node | None = declarator
        while curr and curr.type in (
            "pointer_declarator",
            "abstract_pointer_declarator",
            "reference_declarator",
        ):
            child = curr.child_by_field_name("declarator")
            if not child:
                for c in curr.children:
                    if c.type not in ("*", "&", "&&", "type_qualifier", "const", "volatile"):
                        child = c
                        break
            curr = child

        if curr and curr.type == "function_declarator":
            inner = curr.child_by_field_name("declarator")
            if inner and inner.type in (
                "identifier",
                "field_identifier",
                "operator_name",
                "qualified_identifier",
                "destructor_name",
            ):
                return True
            if inner and inner.type == "parenthesized_declarator":
                subs = [c for c in inner.children if c.type not in ("(", ")")]
                if subs and subs[0].type in (
                    "identifier",
                    "field_identifier",
                    "operator_name",
                    "qualified_identifier",
                    "destructor_name",
                ):
                    return True
        return False

    def _unwrap_declarator(
        self,
        node: Node,
        curr_type: TypeExpr,
    ) -> tuple[str | None, TypeExpr, Node | None]:
        """Unwrap a declarator node, wrapping curr_type and finding the identifier node."""
        if node.type == "init_declarator":
            decl = node.child_by_field_name("declarator")
            if not decl:
                for c in node.children:
                    if c.type not in ("=", "initializer_list"):
                        decl = c
                        break
            if decl:
                return self._unwrap_declarator(decl, curr_type)
            return None, curr_type, node

        if node.type == "parenthesized_declarator":
            for c in node.children:
                if c.type not in ("(", ")"):
                    return self._unwrap_declarator(c, curr_type)
            return None, curr_type, node

        if node.type in ("pointer_declarator", "abstract_pointer_declarator"):
            ptr_type: TypeExpr = Pointer(curr_type, qualifiers=_pointer_qualifiers(node))
            inner_decl = node.child_by_field_name("declarator")
            if not inner_decl:
                for c in node.children:
                    if c.type not in ("*", "type_qualifier", "const", "volatile"):
                        inner_decl = c
                        break
            if inner_decl:
                return self._unwrap_declarator(inner_decl, ptr_type)
            return None, ptr_type, node

        if node.type == "reference_declarator":
            is_rval = any(c.type == "&&" for c in node.children)
            ref_type: TypeExpr = Reference(curr_type, is_rvalue=is_rval)
            inner_decl = node.child_by_field_name("declarator")
            if not inner_decl:
                for c in node.children:
                    if c.type not in ("&", "&&"):
                        inner_decl = c
                        break
            if inner_decl:
                return self._unwrap_declarator(inner_decl, ref_type)
            return None, ref_type, node

        if node.type == "array_declarator":
            inner_decl = node.child_by_field_name("declarator")
            size_node = node.child_by_field_name("size")
            size: int | str | None = None
            if size_node:
                s_text = _node_text(size_node).strip()
                try:
                    size = int(s_text, 0)
                except ValueError:
                    size = s_text
            arr_type: TypeExpr = Array(element_type=curr_type, size=size)
            if inner_decl:
                return self._unwrap_declarator(inner_decl, arr_type)
            return None, arr_type, node

        if node.type == "function_declarator":
            params_node = node.child_by_field_name("parameters")
            params: list[Parameter] = []
            is_variadic = False
            if params_node:
                for child in params_node.children:
                    if child.type == "parameter_declaration":
                        param = self._convert_parameter(child)
                        if param:
                            params.append(param)
                    elif child.type in ("...", "variadic_parameter"):
                        is_variadic = True
            fn_type: TypeExpr = FunctionPointer(return_type=curr_type, parameters=params, is_variadic=is_variadic)
            inner_decl = node.child_by_field_name("declarator")
            if inner_decl:
                return self._unwrap_declarator(inner_decl, fn_type)
            return None, fn_type, node

        if node.type in ("identifier", "field_identifier", "type_identifier", "qualified_identifier"):
            return _node_text(node).strip(), curr_type, node

        return _node_text(node).strip(), curr_type, node

    def _convert_declaration(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        type_node = node.child_by_field_name("type")
        declarators = node.children_by_field_name("declarator")
        if not declarators:
            single = node.child_by_field_name("declarator")
            if single:
                declarators = [single]

        results: list[Declaration] = []

        if type_node and type_node.type in ("struct_specifier", "class_specifier", "union_specifier"):
            body_node = type_node.child_by_field_name("body")
            if not declarators or body_node:
                st = self._convert_class_or_struct(
                    type_node,
                    filename,
                    namespace=namespace,
                    template_params=template_params,
                    is_cpp=is_cpp,
                )
                if st:
                    results.append(st)
            if not declarators:
                return results

        elif type_node and type_node.type == "enum_specifier":
            body_node = type_node.child_by_field_name("body")
            if not declarators or body_node:
                en = self._convert_enum(type_node, filename, namespace=namespace)
                if en:
                    results.append(en)
            if not declarators:
                return results

        if not declarators:
            return results

        base_type = self._qualified_type(node, type_node, CType("int"))
        is_deprecated = any(
            "deprecated" in _node_text(c)
            for c in node.children
            if c.type in ("attribute_specifier", "attribute_declaration", "ms_declspec_modifier")
        )

        for decl in declarators:
            if self._is_function_declaration(decl):
                pointer_depth = 0
                curr: Node | None = decl
                while curr and curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                    pointer_depth += 1
                    curr = curr.child_by_field_name("declarator")

                if curr and curr.type == "function_declarator":
                    funcs = self._convert_function_declarator(
                        base_type,
                        curr,
                        filename,
                        pointer_depth=pointer_depth,
                        namespace=namespace,
                        template_params=template_params,
                    )
                    results.extend(funcs)
            else:
                name, var_type, ident_node = self._unwrap_declarator(decl, base_type)
                if name:
                    loc_node = ident_node or decl
                    loc = SourceLocation(
                        file=filename,
                        line=loc_node.start_point[0] + 1,
                        column=loc_node.start_point[1] + 1,
                    )
                    results.append(
                        Variable(
                            name=name,
                            type=var_type,
                            namespace=namespace,
                            is_deprecated=is_deprecated,
                            location=loc,
                        )
                    )

        return results

    def _convert_function_declarator(
        self,
        base_type: TypeExpr,
        declarator_node: Node,
        filename: str,
        *,
        pointer_depth: int = 0,
        namespace: str | None = None,
        template_params: list[str] | None = None,
    ) -> list[Declaration]:
        ret_type: TypeExpr = base_type
        for _ in range(pointer_depth):
            ret_type = Pointer(ret_type)
        ident_node = declarator_node.child_by_field_name("declarator")
        if not ident_node:
            for c in declarator_node.children:
                if c.type in ("identifier", "field_identifier", "operator_name"):
                    ident_node = c
                    break

        func_name = _node_text(ident_node).strip() if ident_node else ""

        params_node = declarator_node.child_by_field_name("parameters")
        parameters: list[Parameter] = []
        is_variadic = False

        if params_node:
            for child in params_node.children:
                if child.type == "parameter_declaration":
                    param = self._convert_parameter(child)
                    if param:
                        parameters.append(param)
                elif child.type in ("...", "variadic_parameter"):
                    is_variadic = True

        loc = SourceLocation(
            file=filename,
            line=declarator_node.start_point[0] + 1,
            column=declarator_node.start_point[1] + 1,
        )
        return [
            Function(
                name=func_name,
                return_type=ret_type,
                parameters=parameters,
                is_variadic=is_variadic,
                namespace=namespace,
                template_params=template_params or [],
                location=loc,
            )
        ]

    def _convert_class_or_struct(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
        nested: bool = False,
        tag_qualifier: str | None = None,
    ) -> Struct | None:
        self._filled_forward = None
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node).strip() if name_node else None
        body_node = node.child_by_field_name("body")

        is_class_keyword = node.type == "class_specifier" or any(c.type == "class" for c in node.children)
        is_union = node.type == "union_specifier" or any(c.type == "union" for c in node.children)

        # A nested record is keyed only by its own tag, which is not unique: a
        # global `struct view` and a class member `struct view` collide. The
        # nested one is scoped to its parent and never reaches the top level, so
        # it neither consults nor updates the translation-unit dedup set.
        # Sharing it silently dropped whichever of the two was seen second.
        #
        # An opaque `struct S;` and its later definition are one entity, so the
        # definition fills the forward declaration already recorded rather than
        # adding a second `cdef struct S` that Cython reports as redeclared. This
        # is the same upgrade-in-place the opaque enum above uses, on the same
        # namespace-qualified key, so either declaration order collapses. A
        # forward declaration that is never defined keeps its empty form: it is
        # the only declaration of that tag in the unit, and it is how an opaque
        # handle type is spelled.
        record_key: str | None = None
        forward_target: Struct | None = None
        if name and not nested:
            record_kind = "union" if is_union else ("class" if is_class_keyword else "struct")
            record_key = f"{record_kind}:{namespace}::{name}" if namespace else f"{record_kind}:{name}"
            if body_node is None:
                if record_key in self._defined_records or record_key in self._forward_records:
                    return None
            else:
                if record_key in self._defined_records:
                    return None
                self._defined_records.add(record_key)
                forward_target = self._forward_records.pop(record_key, None)

        bases: list[BaseSpecifier] = []
        base_clause = None
        for child in node.children:
            if child.type == "base_class_clause":
                base_clause = child
                break

        if base_clause:
            curr_access = "public"
            curr_virt = False
            for child in base_clause.children:
                if child.type == "access_specifier":
                    curr_access = _node_text(child).strip()
                elif child.type == "virtual":
                    curr_virt = True
                elif child.type in ("type_identifier", "qualified_identifier", "template_type"):
                    base_name = _node_text(child).strip()
                    bases.append(BaseSpecifier(name=base_name, access=curr_access, is_virtual=curr_virt))
                    curr_access = "public"
                    curr_virt = False

        fields: list[Field] = []
        methods: list[Function] = []
        constructors: list[Function] = []
        destructor: Function | None = None
        inner_typedefs: dict[str, str] = {}
        nested_records: list[Struct] = []

        current_access = "private" if is_class_keyword else "public"

        if body_node:
            for child in body_node.children:
                if child.type == "access_specifier":
                    spec = _node_text(child.children[0]).strip() if child.children else _node_text(child).strip()
                    if spec.endswith(":"):
                        spec = spec[:-1].strip()
                    if spec in ("public", "protected", "private"):
                        current_access = spec
                    continue

                if child.type == "alias_declaration":
                    u_name_node = child.child_by_field_name("name")
                    u_type_node = child.child_by_field_name("type")
                    if u_name_node and u_type_node:
                        inner_typedefs[_node_text(u_name_node).strip()] = _node_text(u_type_node).strip()
                    continue

                if child.type == "type_definition":
                    tds = self._convert_type_definition(child, filename, namespace=None)
                    for td in tds:
                        if isinstance(td, Typedef):
                            inner_typedefs[td.name] = str(td.underlying_type)
                    continue

                if child.type in ("field_declaration", "declaration"):
                    func_decl, ret_base_type, is_virt, is_stat, is_expl = self._find_function_declarator(child)
                    if func_decl:
                        fn = self._convert_method_declarator(
                            func_decl,
                            ret_base_type,
                            filename,
                            class_name=name,
                            access=current_access,
                            is_virtual=is_virt,
                            is_static=is_stat,
                            is_explicit=is_expl,
                            child_node=child,
                        )
                        if fn:
                            if fn.name.startswith("~") or (name and fn.name == f"~{name}"):
                                destructor = fn
                            elif name and fn.name == name and ret_base_type is None:
                                constructors.append(fn)
                            else:
                                methods.append(fn)
                    else:
                        f_type_node = child.child_by_field_name("type")

                        # A record *defined* in the class body is a nested type,
                        # not merely the type of the member that follows it. It
                        # is captured whether or not a declarator follows, so
                        # that both `struct v { ... };` and `struct v { ... } m;`
                        # declare `v` inside the parent.
                        if (
                            f_type_node is not None
                            and f_type_node.type in ("struct_specifier", "class_specifier", "union_specifier")
                            and f_type_node.child_by_field_name("body") is not None
                            and f_type_node.child_by_field_name("name") is not None
                        ):
                            inner = self._convert_class_or_struct(f_type_node, filename, is_cpp=is_cpp, nested=True)
                            if inner is not None:
                                nested_records.append(inner)

                        # An enum defined in a class body has nowhere to live in
                        # the record IR, so leaving it there loses every
                        # enumerator. libclang reports such an enum as a sibling
                        # of the class, qualified by the enclosing *namespace* --
                        # a class scope is not a namespace. Hoist it on the same
                        # terms. A nested record is not hoisted, so an enum
                        # inside one is not hoisted either.
                        if (
                            f_type_node is not None
                            and f_type_node.type == "enum_specifier"
                            and f_type_node.child_by_field_name("body") is not None
                            and not nested
                        ):
                            hoisted = self._convert_enum(
                                f_type_node, filename, namespace=namespace, enclosing_record=name if is_cpp else None
                            )
                            if hoisted is not None:
                                self._lifted_declarations.append(hoisted)

                        anon_spec = (
                            f_type_node
                            if (
                                f_type_node is not None
                                and f_type_node.type in ("struct_specifier", "class_specifier", "union_specifier")
                                and f_type_node.child_by_field_name("body") is not None
                                and f_type_node.child_by_field_name("name") is None
                            )
                            else None
                        )

                        base_type = self._qualified_type(child, f_type_node, CType("int"))
                        is_static_field = any(
                            c.type == "storage_class_specifier" and _node_text(c).strip() == "static"
                            for c in child.children
                        )
                        bit_width: int | None = None
                        recovered_padding = _recovered_bitfield_clause(child)
                        for c in child.children:
                            if c.type == "bitfield_clause" or c is recovered_padding:
                                num_child = c.child_by_field_name("length") or c.child_by_field_name("width")
                                if not num_child:
                                    for sub in c.children:
                                        if sub.type == "number_literal":
                                            num_child = sub
                                            break
                                if num_child:
                                    try:
                                        bit_width = int(_node_text(num_child).strip(), 0)
                                    except ValueError:
                                        bit_width = None

                        field_decls = child.children_by_field_name("declarator")
                        if not field_decls:
                            for sibling in child.children:
                                if sibling.type in (
                                    "field_identifier",
                                    "identifier",
                                    "pointer_declarator",
                                    "array_declarator",
                                    "function_declarator",
                                ):
                                    field_decls.append(sibling)

                        if anon_spec is not None:
                            inner = self._convert_class_or_struct(anon_spec, filename, is_cpp=is_cpp, nested=True)
                            if inner is None:
                                continue
                            member_name = self._unwrap_declarator(field_decls[0], base_type)[0] if field_decls else None
                            if not member_name:
                                # C11 6.7.2.1p13: a member with no declarator is
                                # transparent, so its members belong to this
                                # record.  The nested Struct rides along for the
                                # writer to flatten.
                                fields.append(
                                    Field(
                                        name="",
                                        type=CType("void"),
                                        access=current_access,
                                        anonymous_struct=inner,
                                        is_anonymous_transparent=True,
                                    )
                                )
                                continue
                            # A declared member needs a tag to refer to, and the
                            # source supplies none.  The synthesized tag is
                            # qualified by the enclosing record so that two
                            # parents declaring the same member name do not
                            # collide at the top level.
                            inner.name = self._anonymous_tag_name(
                                name or tag_qualifier, member_name, is_union=inner.is_union
                            )
                            self._lifted_declarations.append(inner)
                            base_type = CType(f"{'union' if inner.is_union else 'struct'} {inner.name}")

                        if not field_decls and recovered_padding is not None and bit_width is not None:
                            # The ERROR-recovered shape carries no declarator at
                            # all, so the MISSING-node path below never sees it.
                            fields.append(
                                Field(
                                    name="",
                                    type=base_type,
                                    bit_width=bit_width,
                                    access=current_access,
                                    is_padding=True,
                                )
                            )
                            continue

                        for f_decl in field_decls:
                            f_name, f_type, _ = self._unwrap_declarator(f_decl, base_type)
                            # C11 6.7.2.1p12: a bitfield with no declarator is
                            # padding, not a member -- `unsigned : 0` aligns the
                            # next field to a fresh storage unit and `unsigned : 3`
                            # reserves anonymous bits.  The grammar requires a
                            # declarator, so tree-sitter inserts a MISSING
                            # field_identifier node; that node is what
                            # distinguishes padding from a real member.  The Field
                            # is kept, flagged `is_padding`, because a consumer
                            # that reconstructs layout cannot place the following
                            # fields without knowing these bits are spoken for.
                            if not f_name and f_decl.is_missing:
                                if bit_width is None:
                                    continue
                                fields.append(
                                    Field(
                                        name="",
                                        type=f_type,
                                        bit_width=bit_width,
                                        access=current_access,
                                        is_padding=True,
                                    )
                                )
                                continue
                            if f_name or bit_width is not None:
                                fields.append(
                                    Field(
                                        name=f_name or "",
                                        type=f_type,
                                        bit_width=bit_width,
                                        access=current_access,
                                        is_static=is_static_field,
                                    )
                                )

        is_cppclass = is_class_keyword or bool(methods) or bool(bases) or bool(constructors) or (destructor is not None)
        # An attribute on the record wins outright. Failing that the record
        # inherits any ``#pragma pack(1)`` in force at its own position, which
        # is how a record carrying no attribute of its own becomes packed.
        # Only an alignment of exactly 1 is reported: an intermediate
        # ``#pragma pack(2)`` squeezes the record without flattening it, and a
        # boolean cannot say so without overstating the result.
        has_packed_attribute = _record_has_packed_attribute(node)
        pack_alignment = _pack_at(self._pack_regions, node.start_byte)
        is_packed = has_packed_attribute or pack_alignment == 1
        notes = self._pack_notes(node, has_packed_attribute=has_packed_attribute, pack_alignment=pack_alignment)
        loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
        record = Struct(
            name=name,
            fields=fields,
            methods=methods,
            constructors=constructors,
            destructor=destructor,
            bases=bases,
            is_union=is_union,
            is_cppclass=is_cppclass,
            is_packed=is_packed,
            namespace=namespace,
            template_params=template_params or [],
            inner_typedefs=inner_typedefs,
            nested_records=nested_records,
            location=loc,
            notes=notes,
        )

        if forward_target is not None:
            for slot in dataclasses.fields(Struct):
                setattr(forward_target, slot.name, getattr(record, slot.name))
            # The caller may still need the record -- a `typedef struct S { ... } S;`
            # following a `struct S;` has to stamp the alias onto it -- but must
            # not emit it a second time, so it is handed over out of band.
            self._filled_forward = forward_target
            return None
        if record_key is not None and body_node is None:
            self._forward_records[record_key] = record
        return record

    @staticmethod
    def _anonymous_tag_name(parent: str | None, declarator: str, *, is_union: bool) -> str:
        """Build the tag for an anonymous record named by a member declarator."""
        suffix = "_u" if is_union else "_s"
        qualified = f"{parent}_{declarator}" if parent else declarator
        return f"_{qualified}{suffix}"

    def _find_function_declarator(self, node: Node) -> tuple[Node | None, TypeExpr | None, bool, bool, bool]:
        is_virtual = any(c.type == "virtual" for c in node.children)
        is_static = any(
            c.type == "storage_class_specifier" and _node_text(c).strip() == "static" for c in node.children
        )
        is_explicit = any(c.type in ("explicit", "explicit_function_specifier") for c in node.children)
        type_node = node.child_by_field_name("type")
        ret_type = self._qualified_type(node, type_node, CType("void")) if type_node else None

        decl = node.child_by_field_name("declarator")
        if not decl:
            for child in node.children:
                if child.type in (
                    "function_declarator",
                    "pointer_declarator",
                    "reference_declarator",
                ):
                    decl = child
                    break

        if not decl:
            return None, None, False, False, False

        curr: Node | None = decl
        while curr and curr.type in ("pointer_declarator", "abstract_pointer_declarator", "reference_declarator"):
            next_child = curr.child_by_field_name("declarator")
            if not next_child:
                for c in curr.children:
                    if c.type in ("function_declarator", "reference_declarator", "pointer_declarator"):
                        next_child = c
                        break
            curr = next_child

        if curr and curr.type == "function_declarator":
            inner = curr.child_by_field_name("declarator")
            if inner and inner.type == "parenthesized_declarator":
                if any(c.type in ("pointer_declarator", "*") for c in inner.children):
                    return None, None, False, False, False
            return curr, ret_type, is_virtual, is_static, is_explicit

        return None, None, False, False, False

    def _extract_return_type(self, base_type: TypeExpr | None, declarator_root: Node | None) -> TypeExpr:
        if base_type is None:
            base_type = CType("void")
        if not declarator_root:
            return base_type

        curr: Node | None = declarator_root
        wrappers: list[str] = []
        while curr and curr.type != "function_declarator":
            if curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                wrappers.append("*")
                curr = curr.child_by_field_name("declarator")
            elif curr.type == "reference_declarator":
                is_rval = any(c.type == "&&" for c in curr.children)
                wrappers.append("&&" if is_rval else "&")
                curr = curr.child_by_field_name("declarator")
                if not curr:
                    for c in declarator_root.children:
                        if c.type == "function_declarator":
                            curr = c
                            break
            else:
                break

        ret = base_type
        for w in wrappers:
            if w == "*":
                ret = Pointer(ret)
            elif w == "&&":
                ret = Reference(ret, is_rvalue=True)
            elif w == "&":
                ret = Reference(ret, is_rvalue=False)
        return ret

    def _convert_method_declarator(
        self,
        func_decl: Node,
        ret_base_type: TypeExpr | None,
        filename: str,
        *,
        class_name: str | None = None,
        access: str | None = None,
        is_virtual: bool = False,
        is_static: bool = False,
        is_explicit: bool = False,
        child_node: Node | None = None,
    ) -> Function | None:
        ident_node = func_decl.child_by_field_name("declarator")
        if not ident_node:
            for c in func_decl.children:
                if c.type in ("identifier", "field_identifier", "destructor_name", "operator_name"):
                    ident_node = c
                    break

        func_name = _node_text(ident_node).strip() if ident_node else ""

        is_pure_virtual = False
        if child_node:
            has_eq = any(c.type == "=" for c in child_node.children)
            has_zero = any(c.type == "number_literal" and _node_text(c).strip() == "0" for c in child_node.children)
            if has_eq and has_zero:
                is_pure_virtual = True
                is_virtual = True

        is_const = False
        for c in func_decl.children:
            if (c.type == "type_qualifier" and _node_text(c).strip() == "const") or c.type == "const":
                is_const = True

        params_node = func_decl.child_by_field_name("parameters")
        parameters: list[Parameter] = []
        is_variadic = False
        if params_node:
            for child in params_node.children:
                if child.type == "parameter_declaration":
                    param = self._convert_parameter(child)
                    if param:
                        parameters.append(param)
                elif child.type in ("...", "variadic_parameter"):
                    is_variadic = True

        decl_root = child_node.child_by_field_name("declarator") if child_node else None
        if not decl_root and child_node:
            for c in child_node.children:
                if c.type in ("reference_declarator", "pointer_declarator", "function_declarator"):
                    decl_root = c
                    break
        ret_type = self._extract_return_type(ret_base_type, decl_root)

        loc = SourceLocation(
            file=filename,
            line=func_decl.start_point[0] + 1,
            column=func_decl.start_point[1] + 1,
        )

        return Function(
            name=func_name,
            return_type=ret_type,
            parameters=parameters,
            is_variadic=is_variadic,
            is_virtual=is_virtual,
            is_pure_virtual=is_pure_virtual,
            is_static=is_static,
            is_const=is_const,
            is_explicit=is_explicit,
            access=access,
            location=loc,
        )

    def _convert_parameter(self, node: Node) -> Parameter | None:
        p_type_node = node.child_by_field_name("type")
        p_decl_node = node.child_by_field_name("declarator")

        p_type: TypeExpr = self._qualified_type(node, p_type_node, CType("void"))
        p_name: str | None = None

        if p_decl_node:
            curr: Node | None = p_decl_node
            while curr and curr.type in (
                "pointer_declarator",
                "abstract_pointer_declarator",
                "reference_declarator",
            ):
                if curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                    p_type = Pointer(p_type, qualifiers=_pointer_qualifiers(curr))
                elif curr.type == "reference_declarator":
                    is_rval = any(c.type == "&&" for c in curr.children)
                    p_type = Reference(p_type, is_rvalue=is_rval)
                curr = curr.child_by_field_name("declarator")

            if curr and curr.type in ("identifier", "type_identifier", "field_identifier"):
                p_name = _node_text(curr).strip()

        if str(p_type) == "void" and p_name is None:
            return None

        default_val_node = node.child_by_field_name("default_value")
        default_value = _node_text(default_val_node).strip() if default_val_node else None

        return Parameter(name=p_name, type=p_type, default_value=default_value)

    def _parse_array_declarator(self, node: Node, base_type: TypeExpr) -> tuple[str | None, TypeExpr]:
        name_node = node.child_by_field_name("declarator")
        size_node = node.child_by_field_name("size")
        size: int | str | None = None
        if size_node:
            s_text = _node_text(size_node).strip()
            try:
                size = int(s_text, 0)
            except ValueError:
                size = s_text
        arr_type = Array(element_type=base_type, size=size)
        name = _node_text(name_node).strip() if name_node else None
        return name, arr_type

    def _convert_enum(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        enclosing_record: str | None = None,
    ) -> Enum | None:
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node).strip() if name_node else None
        body_node = node.child_by_field_name("body")

        # The C++ grammar spells the scoping keyword as a distinct child token of
        # ``enum_specifier``: ``enum class E`` and ``enum struct E`` carry a
        # ``class``/``struct`` node that a plain ``enum E`` does not. Reading the
        # child node types keeps this structural -- the same distinction taken
        # off the source text would misread ``enum E { classic }``.
        is_scoped = any(child.type in ("class", "struct") for child in node.children)

        values: list[EnumValue] = []
        if body_node:
            current_int = 0
            for child in body_node.children:
                if child.type == "enumerator":
                    e_name_node = child.child_by_field_name("name")
                    e_val_node = child.child_by_field_name("value")

                    if e_name_node:
                        e_name = _node_text(e_name_node).strip()
                        val: int | str
                        if e_val_node:
                            val_str = _node_text(e_val_node).strip()
                            try:
                                parsed_int = int(val_str, 0)
                                val = parsed_int
                                current_int = parsed_int + 1
                            except ValueError:
                                val = val_str
                        else:
                            val = current_int
                            current_int += 1
                        values.append(EnumValue(name=e_name, value=val))

        loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
        # A member enum is hoisted to the top level, which strips the record from
        # its spelling. ``namespace`` cannot carry a record, so the full C++ name
        # is recorded separately; without it the hoisted tag names no type.
        cpp_name = None
        if name and enclosing_record:
            cpp_name = "::".join(filter(None, (namespace, enclosing_record, name)))
        enum = Enum(
            name=name,
            values=values,
            namespace=namespace,
            location=loc,
            is_scoped=is_scoped,
            cpp_name=cpp_name,
        )

        # An opaque `enum E : int;` and its later definition are one entity. Emitting
        # both yields two `cdef enum E` blocks that Cython reports as redeclared, so
        # the definition fills in the forward declaration already emitted rather than
        # adding a second. A lone opaque enum keeps its valueless form: it is the only
        # declaration of that type in the unit and dropping it would lose the tag.
        if name is None:
            return enum
        key = f"enum:{cpp_name}" if cpp_name else (f"enum:{namespace}::{name}" if namespace else f"enum:{name}")
        if body_node is None:
            if key in self._defined_enums or key in self._forward_enums:
                return None
            self._forward_enums[key] = enum
            return enum
        if key in self._defined_enums:
            return None
        self._defined_enums.add(key)
        forward = self._forward_enums.pop(key, None)
        if forward is not None:
            forward.values = values
            return None
        return enum

    def _parse_type_expr(self, node: Node | None) -> TypeExpr:
        if node is None:
            return CType("int")
        text = _node_text(node).strip()
        return self._parse_type_str(text)

    def _qualified_type(self, decl_node: Node, type_node: Node | None, default: TypeExpr) -> TypeExpr:
        """Parse a declaration's base type, folding in its leading type qualifiers.

        The C grammar attaches a declaration's leading `const`/`volatile` as
        `type_qualifier` siblings of the `type` field rather than inside it, so
        reading the `type` field alone loses them. Qualifiers positioned after
        the type node are excluded: those belong to the declarator, as in a C++
        `int f() const` member function, and folding one into the return type
        would be wrong. Qualifiers the IR does not model (`_Atomic`,
        `_Noreturn`) are dropped here, matching the Cython writer.
        """
        if type_node is None:
            return default
        quals = [
            text
            for child in decl_node.children
            if child.type == "type_qualifier"
            and child.start_byte < type_node.start_byte
            and (text := _node_text(child).strip()) in _FOLDABLE_TYPE_QUALIFIERS
        ]
        if not quals:
            return self._parse_type_expr(type_node)
        return self._parse_type_str(" ".join([*quals, _node_text(type_node).strip()]))

    def _parse_type_str(self, text: str) -> TypeExpr:
        text = text.strip()
        if text.endswith("&&"):
            return Reference(target=self._parse_type_str(text[:-2]), is_rvalue=True)
        if text.endswith("&"):
            return Reference(target=self._parse_type_str(text[:-1]), is_rvalue=False)
        if text.endswith("*"):
            return Pointer(pointee=self._parse_type_str(text[:-1]))

        tokens = text.split()
        quals: list[str] = []
        name_parts: list[str] = []

        for token in tokens:
            if token in _FOLDABLE_TYPE_QUALIFIERS or token in _SIGNEDNESS_SPECIFIERS:
                quals.append(token)
            elif token not in ("struct", "enum", "class", "union"):
                name_parts.append(token)

        if name_parts:
            type_name = " ".join(name_parts)
        elif quals and quals[-1] in _SIGNEDNESS_SPECIFIERS:
            # C11 6.7.2p2: `unsigned` and `signed` standing alone name the
            # implicit `int` base type.  Reusing `text` here would repeat the
            # specifier, which already appears in `quals`, and render as
            # `unsigned unsigned`.
            type_name = "int"
        else:
            type_name = text
        return CType(name=type_name, qualifiers=quals)


_BACKEND_INSTANCE = TreeSitterBackend()


@hook("parse_unit", backend="tree-sitter", priority=Priority.STANDARD)
@hook("parse_unit", backend="*", priority=Priority.FALLBACK)
def _treesitter_parse_hook(
    code: str,
    filename: str = "input.h",
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    if not _BACKEND_INSTANCE.is_available():
        return None
    if context and context.language not in (None, "c", "c++", "cpp"):
        return None
    return _BACKEND_INSTANCE.parse(code, filename, **kwargs)


@hook("get_backend", backend="tree-sitter", priority=Priority.STANDARD)
def _treesitter_get_backend_hook(context: PipelineContext | None = None) -> ParserBackend:
    _ = context
    return _BACKEND_INSTANCE


from headerkit.backends import register_backend  # noqa: E402

register_backend("tree-sitter", TreeSitterBackend, is_default=False)
