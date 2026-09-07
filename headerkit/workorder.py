"""Tiered test generation: real tests where the IR knows the answer, failing stubs where it does not.

A scaffolded project's tests split three ways:

* **Tier 1** -- the IR fully determines both the call and the expected result, so a
  genuine passing test is emitted (struct field round-trip, unsigned bit-field
  bounds, enum value coverage). These are not work-order items.
* **Tier 2** -- the IR knows the *cases* but not the expectation, so a parameterized
  failing stub is emitted with one case per enumerator, per overload, or a NULL case.
* **Tier 3** -- pure semantics. One failing stub per remaining function.

Stubs fail loudly and carry their instruction at the point of failure, because a note
at the top of a file is skimmed and a message on the failing assertion is read. The
test run is the progress meter: a stub that has been written turns from red to green.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from headerkit.ir import (
    Array,
    CType,
    Declaration,
    Enum,
    Function,
    Header,
    Pointer,
    SourceUnit,
    Struct,
    TypeExpr,
)
from headerkit.scaffold import OutputFile

#: Marker opening every stub failure message. One string, both languages, so a
#: reader can tell an unwritten stub from a genuine regression at a glance.
WORK_ORDER_MARKER = "WORK ORDER"

#: Definition of done, repeated on every stub because this is the sentence that
#: distinguishes a written test from one that merely stopped failing.
DEFINITION_OF_DONE = "Done means: call it and assert on the result. Asserting that it does not raise is insufficient."

_UNSIGNED_PREFIXES = ("unsigned", "uint", "_Bool", "bool")


@dataclass(frozen=True)
class Tier1Test:
    """A test whose call and expected value are both derived from the IR."""

    name: str
    kind: Literal["struct_roundtrip", "bitfield_bounds", "enum_coverage"]
    subject: str
    description: str
    #: ``(name, value)`` pairs the test drives: enumerators for an enum, or fields
    #: paired with the distinct value written into each for a round-trip.
    values: tuple[tuple[str, int], ...] = ()
    #: Bit-field width, for ``bitfield_bounds`` only.
    width: int | None = None
    #: True when the generator observed every enumerator value to be distinct, which
    #: a C enum with deliberate aliases is not.
    all_distinct: bool = False


@dataclass(frozen=True)
class Stub:
    """A failing test standing in for behaviour no IR can describe."""

    name: str
    symbol: str
    signature: str
    instruction: str
    #: Tier 2 case labels. Empty for a Tier 3 stub.
    cases: tuple[str, ...] = ()
    #: Nim enum type driving ``parametrizedTest``, when the cases came from an enum.
    enum_type: str | None = None

    @property
    def tier(self) -> int:
        """2 when the IR supplied the cases, 3 when it supplied nothing but the name."""
        return 2 if self.cases else 3


@dataclass
class WorkOrder:
    """Everything the tiering pass derived from one source unit."""

    tier1: list[Tier1Test] = field(default_factory=list)
    stubs: list[Stub] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to emit for either tier."""
        return not self.tier1 and not self.stubs


def _declarations(unit: SourceUnit | Header) -> list[Declaration]:
    decls: list[Declaration] = getattr(unit, "declarations", [])
    return decls


def _render_type(t: TypeExpr) -> str:
    return str(t)


def render_signature(fn: Function) -> str:
    """Render a C signature from the IR, for verbatim inclusion in a stub docstring."""
    params = [f"{_render_type(p.type)} {p.name}" if p.name else _render_type(p.type) for p in fn.parameters]
    if fn.is_variadic:
        params.append("...")
    inner = ", ".join(params) if params else "void"
    return f"{_render_type(fn.return_type)} {fn.name}({inner})"


def _is_unsigned(t: TypeExpr) -> bool:
    if not isinstance(t, CType):
        return False
    spelling = " ".join([*t.qualifiers, t.name]).strip()
    return any(spelling.startswith(p) or f" {p}" in f" {spelling}" for p in _UNSIGNED_PREFIXES)


#: Base spellings a round-trip test can drive with a plain Python integer. A struct-,
#: char-, bool- or float-typed field is excluded: the first is not assignable from an
#: int, the second wants bytes, and the last two do not round-trip an arbitrary small
#: integer exactly. Narrow and true beats broad and wrong.
_ROUNDTRIP_INTEGER_NAMES: frozenset[str] = frozenset(
    {
        "int",
        "short",
        "short int",
        "long",
        "long int",
        "long long",
        "long long int",
        "size_t",
        "ssize_t",
        "ptrdiff_t",
        "intptr_t",
        "uintptr_t",
        *(f"{s}int{w}_t" for s in ("", "u") for w in (8, 16, 32, 64)),
    }
)


def _is_plain_integer(t: TypeExpr) -> bool:
    """True for a scalar C integer type a round-trip test can drive with an int."""
    if not isinstance(t, CType):
        return False
    spelling = " ".join(w for w in t.name.split() if w not in ("const", "volatile", "unsigned", "signed"))
    return spelling.strip() in _ROUNDTRIP_INTEGER_NAMES


def _enum_int_values(enum: Enum) -> list[tuple[str, int]]:
    """Enumerators whose value the backend resolved to an integer.

    A backend that leaves a value as an unevaluated expression string yields nothing
    here, so the enum is skipped rather than tested against a guess.
    """
    out: list[tuple[str, int]] = []
    for v in enum.values:
        if isinstance(v.value, int) and not isinstance(v.value, bool):
            out.append((v.name, v.value))
    return out


def _roundtrip_fields(struct: Struct) -> list[tuple[str, int]]:
    """Scalar integer fields of a struct, paired with a distinct value to drive them."""
    out: list[tuple[str, int]] = []
    for i, f in enumerate(struct.fields):
        if f.is_padding or not f.name or f.is_static:
            continue
        if f.bit_width is not None:
            continue
        if not _is_plain_integer(f.type):
            continue
        out.append((f.name, i + 1))
    return out


def _bitfield_bounds(struct: Struct) -> list[tuple[str, int]]:
    """Unsigned bit-fields whose width pins an exact maximum.

    Signed bit-fields are excluded: sign extension makes the truncated value
    ABI-dependent, so the IR does not in fact determine the expectation.
    """
    out: list[tuple[str, int]] = []
    for f in struct.fields:
        if f.is_padding or not f.name or f.bit_width is None:
            continue
        if f.bit_width < 1 or f.bit_width > 31:
            continue
        if not _is_unsigned(f.type):
            continue
        out.append((f.name, f.bit_width))
    return out


def _enum_param(fn: Function, enums: dict[str, Enum]) -> tuple[str, Enum] | None:
    for p in fn.parameters:
        if isinstance(p.type, CType) and p.type.name in enums:
            return (p.name or "value", enums[p.type.name])
    return None


def _pointer_param(fn: Function) -> str | None:
    for p in fn.parameters:
        if isinstance(p.type, Pointer | Array):
            return p.name or "ptr"
    return None


def analyze(unit: SourceUnit | Header) -> WorkOrder:
    """Assign every declaration in ``unit`` to a tier.

    A function reached by Tier 2 never also receives a Tier 3 stub, and records and
    enums never receive stubs at all, because Tier 1 tests them completely. Diluting
    the work order with items that are already covered is the way this feature fails:
    the reader stops reading it carefully.
    """
    decls = _declarations(unit)
    order = WorkOrder()

    enums: dict[str, Enum] = {}
    for d in decls:
        if isinstance(d, Enum) and d.name:
            enums[d.name] = d

    for d in decls:
        if isinstance(d, Enum) and d.name:
            pairs = _enum_int_values(d)
            if len(pairs) >= 2:
                order.tier1.append(
                    Tier1Test(
                        name=f"test_enum_{d.name}_values",
                        kind="enum_coverage",
                        subject=d.name,
                        description=f"every enumerator of `enum {d.name}` is importable and holds its declared value",
                        values=tuple(pairs),
                        all_distinct=len({v for _, v in pairs}) == len(pairs),
                    )
                )
        elif isinstance(d, Struct) and d.name:
            if _roundtrip_fields(d):
                order.tier1.append(
                    Tier1Test(
                        name=f"test_{d.name}_field_roundtrip",
                        kind="struct_roundtrip",
                        subject=d.name,
                        description=f"every scalar field of `{d.name}` round-trips through the generated wrapper",
                        values=tuple(_roundtrip_fields(d)),
                    )
                )
            for fname, width in _bitfield_bounds(d):
                order.tier1.append(
                    Tier1Test(
                        name=f"test_{d.name}_{fname}_bitfield_bounds",
                        kind="bitfield_bounds",
                        subject=f"{d.name}.{fname}",
                        description=f"`{d.name}.{fname}` is {width} bits wide, so it holds {(1 << width) - 1} and truncates {1 << width}",
                        values=((fname, width),),
                        width=width,
                    )
                )

    functions = [d for d in decls if isinstance(d, Function) and d.name]
    by_name: dict[str, list[Function]] = {}
    for fn in functions:
        by_name.setdefault(fn.name, []).append(fn)

    for name, overloads in by_name.items():
        primary = overloads[0]
        signature = render_signature(primary)

        if len(overloads) >= 2:
            order.stubs.append(
                Stub(
                    name=f"test_{name}",
                    symbol=name,
                    signature="\n".join(render_signature(o) for o in overloads),
                    instruction=(
                        f"{WORK_ORDER_MARKER}: `{name}` is an overload set with {len(overloads)} overloads. "
                        f"Write one assertion per overload proving it resolves to the intended one. {DEFINITION_OF_DONE}"
                    ),
                    cases=tuple(f"overload_{i}" for i in range(len(overloads))),
                )
            )
            continue

        enum_case = _enum_param(primary, enums)
        if enum_case is not None:
            pname, enum = enum_case
            labels = tuple(v.name for v in enum.values)
            if labels:
                order.stubs.append(
                    Stub(
                        name=f"test_{name}",
                        symbol=name,
                        signature=signature,
                        instruction=(
                            f"{WORK_ORDER_MARKER}: call `{name}` with `{pname}` set to this enumerator and assert "
                            f"what it should do for that value. {DEFINITION_OF_DONE}"
                        ),
                        cases=labels,
                        enum_type=enum.name,
                    )
                )
                continue

        # A pointer parameter adds a NULL case; it does not replace the question of what
        # the function is FOR, which is the more valuable of the two. Emitting only the
        # NULL question would bury the real one under boilerplate, since nearly every C
        # function in a typical header takes a pointer.
        ptr = _pointer_param(primary)
        if ptr is not None:
            order.stubs.append(
                Stub(
                    name=f"test_{name}",
                    symbol=name,
                    signature=signature,
                    instruction=(
                        f"{WORK_ORDER_MARKER}: for the `behaviour` case, describe what `{name}` is for and "
                        f"assert it. For the `null_{ptr}` case, decide what it does when `{ptr}` is NULL -- "
                        f"an error, or undefined and therefore untestable? {DEFINITION_OF_DONE}"
                    ),
                    cases=("behaviour", f"null_{ptr}"),
                )
            )
            continue

        order.stubs.append(
            Stub(
                name=f"test_{name}",
                symbol=name,
                signature=signature,
                instruction=(
                    f"{WORK_ORDER_MARKER}: describe what `{name}` is for, then assert it. "
                    f"No IR can tell you this; read the header docs or the library source. {DEFINITION_OF_DONE}"
                ),
            )
        )

    return order


# =============================================================================
# Emitters
# =============================================================================

_PY_HEADER = '''\
"""Generated by headerkit.

Tier 1 tests below are real and should pass. The remaining tests are stubs that
fail on purpose: each one names work that no header can describe. Turning a red
stub green is the unit of progress. See WORK_ORDER.md.
"""

import pytest

from {pkg} import _bindings
'''

_NIM_DSL = """\
## Generated by headerkit.
##
## One discrete `test` per enumerator, so a failing case neither swallows nor blocks
## its neighbours. A runtime `for` loop cannot serve here: every iteration collapses
## into a single reported result, and iterating a holey enum -- which a C header
## produces almost universally -- does not compile.

import std/[unittest, macros]

macro parametrizedTest*(name: static string, T: typedesc[enum], body: untyped): untyped =
  ## Expand to one `test` block per enumerator of `T`, with `it` bound to the value.
  let impl = T.getTypeInst[1].getTypeImpl
  result = newStmtList()
  for child in impl:
    if child.kind == nnkEmpty: continue
    let valSym = if child.kind == nnkEnumFieldDef: child[0] else: child
    let caseName = newLit(name & "[" & valSym.strVal & "]")
    let itSym = ident("it")
    result.add quote do:
      test `caseName`:
        let `itSym` {.inject, used.} = `valSym`
        `body`

macro parametrizedTestOver*(name: static string, values: untyped, body: untyped): untyped =
  ## Expand to one `test` block per element of the `values` array literal.
  result = newStmtList()
  for v in values:
    let caseName = newLit(name & "[" & v.repr & "]")
    let itSym = ident("it")
    result.add quote do:
      test `caseName`:
        let `itSym` {.inject, used.} = `v`
        `body`
"""


def render_nim_dsl() -> str:
    """The dependency-free parameterized-test DSL dropped into a scaffolded Nim project."""
    return _NIM_DSL


def _py_docstring(stub: Stub, indent: str = "    ") -> str:
    lines = [f'{indent}"""{stub.signature.splitlines()[0]}']
    for extra in stub.signature.splitlines()[1:]:
        lines.append(f"{indent}{extra}")
    lines.append("")
    lines.append(f"{indent}{stub.instruction}")
    lines.append(f'{indent}"""')
    return "\n".join(lines)


def render_python_tests(order: WorkOrder, package_name: str) -> str:
    """Render the generated pytest module for a scaffolded Python project."""
    parts = [_PY_HEADER.format(pkg=package_name)]

    for t in order.tier1:
        parts.append(_render_python_tier1(t))

    for stub in order.stubs:
        parts.append(_render_python_stub(stub))

    return "\n".join(parts)


def _render_python_tier1(t: Tier1Test) -> str:
    body: list[str] = []
    if t.kind == "enum_coverage":
        observed = ", ".join(f'"{n}": _bindings.{n}' for n, _ in t.values)
        expected = ", ".join(f'"{n}": {v}' for n, v in t.values)
        body.append(f"    observed = {{{observed}}}")
        body.append(f"    assert observed == {{{expected}}}")
        if t.all_distinct:
            body.append("    assert len(set(observed.values())) == len(observed)")
    elif t.kind == "struct_roundtrip":
        body.append(f"    obj = _bindings.{t.subject}()")
        for fname, value in t.values:
            body.append(f"    obj.{fname} = {value}")
        for fname, value in t.values:
            body.append(f"    assert obj.{fname} == {value}")
    elif t.kind == "bitfield_bounds":
        struct, fname = t.subject.split(".", 1)
        width = t.width or 0
        body.append(f"    obj = _bindings.{struct}()")
        body.append(f"    obj.{fname} = {(1 << width) - 1}")
        body.append(f"    assert obj.{fname} == {(1 << width) - 1}")
        body.append(f"    obj.{fname} = {1 << width}")
        body.append(f"    assert obj.{fname} == 0")

    joined = "\n".join(body)
    summary = t.description[:1].upper() + t.description[1:]
    return f'\ndef {t.name}():\n    """{summary}."""\n{joined}\n'


def _render_python_stub(stub: Stub) -> str:
    doc = _py_docstring(stub)
    fail = f"    pytest.fail({stub.instruction!r})"
    if not stub.cases:
        return f"\ndef {stub.name}():\n{doc}\n{fail}\n"

    ids = ", ".join(repr(c) for c in stub.cases)
    return (
        f"\n@pytest.mark.parametrize('case', [{ids}])\n"
        f"def {stub.name}(case):\n"
        f"{doc}\n"
        f"    pytest.fail(f{stub.instruction + ' [case: {case}]'!r})\n"
    )


def render_nim_tests(order: WorkOrder, package_name: str) -> str:
    """Render the generated unittest module for a scaffolded Nim project."""
    lines = [
        "## Generated by headerkit.",
        "##",
        "## Tier 1 tests are real and should pass. The rest are stubs that fail on",
        "## purpose; each names work no header can describe. See WORK_ORDER.md.",
        "",
        "import std/unittest",
        f"import {package_name}",
        "import ./workorder_dsl",
        "",
        f'suite "{package_name} generated tests":',
    ]

    for t in order.tier1:
        # The Nim writer emits no bit-field width, so a Nim binding cannot express a
        # truncation bound. Skipping is the honest response; emitting an empty test
        # would be a vacuous assertion, and emitting a guessed bound would be worse.
        if t.kind == "bitfield_bounds":
            continue
        lines.extend(_render_nim_tier1(t))

    for stub in order.stubs:
        lines.extend(_render_nim_stub(stub))

    return "\n".join(lines) + "\n"


def _render_nim_tier1(t: Tier1Test) -> list[str]:
    out = [f'  test "{t.description}":']
    if t.kind == "enum_coverage":
        for name, value in t.values:
            out.append(f"    check ord({name}) == {value}")
    elif t.kind == "struct_roundtrip":
        out.append(f"    var obj: {t.subject}")
        for fname, value in t.values:
            out.append(f"    obj.{fname} = type(obj.{fname})({value})")
        for fname, value in t.values:
            out.append(f"    check obj.{fname} == type(obj.{fname})({value})")
    out.append("")
    return out


def _nim_doc(stub: Stub) -> list[str]:
    out = [f"    ## {line}" for line in stub.signature.splitlines()]
    out.append("    ##")
    out.append(f"    ## {stub.instruction}")
    return out


def _render_nim_stub(stub: Stub) -> list[str]:
    message = stub.instruction.replace('"', "'")
    if not stub.cases:
        return [
            f'  test "{stub.symbol}":',
            *_nim_doc(stub),
            f'    checkpoint "{message}"',
            "    fail()",
            "",
        ]

    if stub.enum_type:
        return [
            f'  parametrizedTest("{stub.symbol}", {stub.enum_type}):',
            *_nim_doc(stub),
            f'    checkpoint "{message} [case: " & $it & "]"',
            "    fail()",
            "",
        ]

    values = ", ".join(f'"{c}"' for c in stub.cases)
    return [
        f'  parametrizedTestOver("{stub.symbol}", [{values}]):',
        *_nim_doc(stub),
        f'    checkpoint "{message} [case: " & it & "]"',
        "    fail()",
        "",
    ]


# =============================================================================
# Markdown artifacts
# =============================================================================

_TEST_COMMANDS = {
    "python": "pytest -q",
    "nim": "nim c -r tests/test_workorder.nim",
}


def render_work_order_md(order: WorkOrder, package_name: str, language: str = "python") -> str:
    """Render the human- and LLM-readable list of what still needs writing."""
    command = _TEST_COMMANDS.get(language, _TEST_COMMANDS["python"])
    lines = [
        f"# Work order: {package_name}",
        "",
        "headerkit generated this project's tests in tiers. The tests it could write",
        "completely, it wrote. Everything below needs a human or an LLM, because it",
        "depends on what the library *means*, which no header records.",
        "",
        f"Each entry is a test that fails right now. Run `{command}` to see them.",
        "The failure message repeats the instruction, so you do not need this file open",
        "while you work. Delete a line here once its test passes.",
        "",
    ]

    if order.tier1:
        lines += [
            "## Already done (no action needed)",
            "",
            "These were derived from the header and should pass as generated:",
            "",
        ]
        lines += [f"- `{t.name}` -- {t.description}" for t in order.tier1]
        lines.append("")

    tier2 = [s for s in order.stubs if s.tier == 2]
    tier3 = [s for s in order.stubs if s.tier == 3]

    if tier2:
        lines += [
            "## Needs an expectation per case",
            "",
            "The cases are known; what each one should do is not.",
            "",
        ]
        for s in tier2:
            lines.append(f"- `{s.name}` ({len(s.cases)} cases: {', '.join(s.cases)})")
            lines.append(f"  - `{s.signature.splitlines()[0]}`")
            lines.append(f"  - {s.instruction}")
        lines.append("")

    if tier3:
        lines += [
            "## Needs semantics",
            "",
            "Nothing in the header says what these do. Read the library's documentation.",
            "",
        ]
        for s in tier3:
            lines.append(f"- `{s.name}`")
            lines.append(f"  - `{s.signature.splitlines()[0]}`")
            lines.append(f"  - {s.instruction}")
        lines.append("")

    if not order.stubs:
        lines += ["## Nothing outstanding", "", "No stubs were generated for this header.", ""]

    lines += [
        "---",
        "",
        "See `SUGGESTIONS.md` for optional ideas about making the wrapper nicer to use.",
    ]
    return "\n".join(lines) + "\n"


_SUGGESTIONS_COMMON = """\
# Suggestions

These are ideas, not findings. headerkit did not analyse this library to produce
them; it emits the same list every time. Read them once against the generated
bindings, take what fits, and ignore the rest. Ignoring all of it is fine.

Look at the wrapped API and consider whether any of these would make it nicer to use:

- Iterators over function pairs shaped like `count`/`get` or `first`/`next`.
- A wrapper that scopes paired acquire/release functions, so callers cannot leak.
- A class or object wrapping an opaque handle together with its lifecycle functions.
- Mapping error-code return values onto the language's own error reporting.
- Async wrappers around polling or callback-based functions.
- Native string and sequence types in place of pointer-plus-length parameter pairs.
- Sets in place of enums whose values are powers of two.
"""

_SUGGESTIONS_PYTHON = """\
Python specifically: a context manager (`with`) is the natural home for an
acquire/release pair, `enum.Flag` for a bitmask enum, and raising an exception is
usually kinder than returning an error code.
"""

_SUGGESTIONS_NIM = """\
Nim specifically: `defer` handles an acquire/release pair inside a single scope, and
`set[T]` is a natural fit for a power-of-two enum. Nim's macro system also makes small
DSLs cheap -- consider whether one would make this library's common usage read well.
"""


def render_suggestions_md(language: str = "python") -> str:
    """Render the static, header-independent list of wrapper design ideas."""
    tail = _SUGGESTIONS_NIM if language == "nim" else _SUGGESTIONS_PYTHON
    return f"{_SUGGESTIONS_COMMON}\n{tail}"


def render_agents_md(package_name: str, language: str = "python") -> str:
    """Render the scaffolded project's AGENTS.md."""
    command = _TEST_COMMANDS.get(language, _TEST_COMMANDS["python"])
    return f"""\
# Agents

Instructions for AI coding agents working on `{package_name}`.

This project was scaffolded by headerkit from a C/C++ header. The bindings are
generated; the tests are partly generated and partly outstanding.

## Outstanding work

`WORK_ORDER.md` lists tests that headerkit could not write, because they depend on
what this library means rather than on what its header declares. Every one of them is
a test that fails right now, and each failure message carries its own instruction.

Run `{command}` to see the current state. The failures are the list; `WORK_ORDER.md`
is the same list in prose.

**Before working through them, ask the user whether they want that done.** It is
optional work, it can be large, and the user may have scaffolded this project only to
get the bindings. Do not start on it unattended.

When every stub is written, `WORK_ORDER.md` has served its purpose -- ask the user
whether to delete it.

## Suggestions

`SUGGESTIONS.md` holds generic ideas for making the generated wrapper nicer to use.
It is informational and identical for every scaffolded project. Ignoring it entirely
is fine.

## Regenerating

Re-running headerkit will not overwrite `WORK_ORDER.md`, `SUGGESTIONS.md`, this file,
or any generated test file that already exists on disk, so work written into a stub
survives regeneration. The generated bindings module *is* overwritten.
"""


def build_work_order_files(
    unit: SourceUnit | Header,
    package_name: str,
    language: str = "python",
) -> list[OutputFile]:
    """Build the tiered test file and its two markdown companions for a scaffolded project.

    Every file returned is marked ``preserve_existing``: these are the artifacts a
    human edits, and regeneration must not eat the work it asked for.
    """
    order = analyze(unit)
    if order.is_empty:
        return []

    files: list[OutputFile] = []
    if language == "nim":
        files.append(
            OutputFile(
                path="tests/workorder_dsl.nim",
                content=render_nim_dsl(),
                preserve_existing=True,
            )
        )
        files.append(
            OutputFile(
                path="tests/test_workorder.nim",
                content=render_nim_tests(order, package_name),
                preserve_existing=True,
            )
        )
    else:
        files.append(
            OutputFile(
                path="tests/test_workorder.py",
                content=render_python_tests(order, package_name),
                preserve_existing=True,
            )
        )

    files.append(
        OutputFile(
            path="WORK_ORDER.md",
            content=render_work_order_md(order, package_name, language),
            preserve_existing=True,
        )
    )
    files.append(
        OutputFile(
            path="SUGGESTIONS.md",
            content=render_suggestions_md(language),
            preserve_existing=True,
        )
    )
    files.append(
        OutputFile(
            path="AGENTS.md",
            content=render_agents_md(package_name, language),
            preserve_existing=True,
        )
    )
    return files
