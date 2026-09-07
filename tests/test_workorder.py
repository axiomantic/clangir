"""Tests for tiered work-order test generation."""

from __future__ import annotations

import ctypes
import re
import textwrap
from dataclasses import replace

import pytest

from headerkit.ir import (
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    Parameter,
    Pointer,
    SourceUnit,
    Struct,
)
from headerkit.scaffold import ScaffoldOptions, scaffold
from headerkit.workorder import (
    WORK_ORDER_MARKER,
    Stub,
    WorkOrder,
    analyze_work_order,
    build_work_order_files,
    render_agents_md,
    render_nim_tests,
    render_python_tests,
    render_suggestions_md,
    render_work_order_md,
)


def _unit(*decls: object) -> SourceUnit:
    return SourceUnit(path="probe.h", declarations=list(decls))  # type: ignore[arg-type]


ENUM_MODE = Enum(name="Mode", values=[EnumValue("MODE_X", 0), EnumValue("MODE_Y", 1)])
STRUCT_REC = Struct(
    name="Rec",
    fields=[
        Field(name="a", type=CType("int")),
        Field(name="b", type=CType("unsigned int"), bit_width=3),
    ],
)


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------


def test_enum_becomes_tier1_not_a_stub() -> None:
    """An enum is fully described by the IR, so it earns a real test and no stub."""
    order = analyze_work_order(_unit(ENUM_MODE))
    assert [t.kind for t in order.tier1] == ["enum_coverage"]
    assert order.stubs == []
    assert order.tier1[0].values == (("MODE_X", 0), ("MODE_Y", 1))


def test_enum_with_unresolved_values_is_skipped() -> None:
    """A backend that leaves an expression unevaluated must not be guessed at."""
    order = analyze_work_order(_unit(Enum(name="E", values=[EnumValue("A", "1 << 3"), EnumValue("B", "X")])))
    assert order.tier1 == []


def test_enum_with_aliases_drops_the_distinctness_assertion() -> None:
    """A C enum may deliberately alias, so distinctness is asserted only when observed."""
    aliased = analyze_work_order(_unit(Enum(name="E", values=[EnumValue("A", 1), EnumValue("B", 1)])))
    assert aliased.tier1[0].all_distinct is False
    assert "len(set(observed.values()))" not in render_python_tests(aliased, "pkg")

    distinct = analyze_work_order(_unit(ENUM_MODE))
    assert distinct.tier1[0].all_distinct is True
    assert "len(set(observed.values()))" in render_python_tests(distinct, "pkg")


def test_struct_yields_roundtrip_and_bitfield_tier1() -> None:
    order = analyze_work_order(_unit(STRUCT_REC))
    kinds = [t.kind for t in order.tier1]
    assert kinds == ["struct_roundtrip", "bitfield_bounds"]
    assert order.tier1[1].width == 3
    assert order.stubs == []


def test_signed_bitfield_is_excluded() -> None:
    """Sign extension makes the truncated value ABI-dependent, so the IR does not know it."""
    signed = Struct(name="S", fields=[Field(name="v", type=CType("int"), bit_width=3)])
    order = analyze_work_order(_unit(signed))
    assert [t.kind for t in order.tier1] == []


def test_non_integer_fields_are_excluded_from_roundtrip() -> None:
    """A char, float or struct field cannot be driven with a plain integer."""
    s = Struct(
        name="S",
        fields=[
            Field(name="c", type=CType("char")),
            Field(name="f", type=CType("double")),
            Field(name="nested", type=CType("Other")),
        ],
    )
    assert analyze_work_order(_unit(s)).tier1 == []


def test_function_with_enum_param_is_tier2_one_case_per_enumerator() -> None:
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    order = analyze_work_order(_unit(ENUM_MODE, fn))
    stub = order.stubs[0]
    assert stub.tier == 2
    assert stub.cases == ("MODE_X", "MODE_Y")
    assert stub.enum_type == "Mode"


def test_overload_set_is_tier2_one_case_per_overload() -> None:
    a = Function(name="f", return_type=CType("int"), parameters=[Parameter("x", CType("int"))])
    b = Function(name="f", return_type=CType("int"), parameters=[Parameter("x", CType("double"))])
    stub = analyze_work_order(_unit(a, b)).stubs[0]
    assert stub.tier == 2
    assert len(stub.cases) == 2


def test_pointer_param_adds_a_null_case_without_losing_the_semantics_question() -> None:
    """The NULL case must not displace the more valuable question of what the function is for."""
    fn = Function(name="destroy", return_type=CType("void"), parameters=[Parameter("h", Pointer(CType("Opaque")))])
    stub = analyze_work_order(_unit(fn)).stubs[0]
    assert stub.cases == ("behaviour", "null_h")
    assert "what `destroy` is for" in stub.instruction
    assert "NULL" in stub.instruction


def test_plain_function_is_tier3() -> None:
    fn = Function(name="tick", return_type=CType("int"), parameters=[])
    stub = analyze_work_order(_unit(fn)).stubs[0]
    assert stub.tier == 3
    assert stub.cases == ()


def test_a_function_never_gets_both_a_tier2_and_a_tier3_stub() -> None:
    """Diluting the work order with duplicate entries is how this feature fails."""
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    order = analyze_work_order(_unit(ENUM_MODE, fn))
    assert [s.name for s in order.stubs] == ["test_set_mode"]


def test_records_and_enums_never_produce_stubs() -> None:
    order = analyze_work_order(_unit(ENUM_MODE, STRUCT_REC))
    assert order.stubs == []
    assert len(order.tier1) == 3


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------


def test_python_tier1_enum_test_asserts_real_values() -> None:
    out = render_python_tests(analyze_work_order(_unit(ENUM_MODE)), "pkg")
    assert 'observed = {"MODE_X": _bindings.MODE_X, "MODE_Y": _bindings.MODE_Y}' in out
    assert 'assert observed == {"MODE_X": 0, "MODE_Y": 1}' in out
    assert WORK_ORDER_MARKER not in out.split("def test_enum_Mode_values")[1].split("def ")[0]


def test_python_bitfield_test_asserts_the_derived_bound() -> None:
    out = render_python_tests(analyze_work_order(_unit(STRUCT_REC)), "pkg")
    assert (
        textwrap.dedent("""\
        obj = _bindings.Rec()
            obj.b = 7
            assert obj.b == 7
            obj.b = 8
            assert obj.b == 0""")
        in out
    )


def test_python_stub_fails_and_carries_signature_and_definition_of_done() -> None:
    fn = Function(
        name="parse_config",
        return_type=CType("int"),
        parameters=[Parameter("path", Pointer(CType("const char")))],
    )
    out = render_python_tests(analyze_work_order(_unit(fn)), "pkg")
    assert "pytest.fail(" in out
    assert "int parse_config(const char* path)" in out
    assert "Asserting that it does not raise is insufficient" in out


def test_python_tier2_uses_parametrize_with_one_id_per_case() -> None:
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    out = render_python_tests(analyze_work_order(_unit(ENUM_MODE, fn)), "pkg")
    assert "@pytest.mark.parametrize('case', ['MODE_X', 'MODE_Y'])" in out


@pytest.mark.parametrize("symbol", ["go", "operator==", "operator*", "operator[]", "Foo::bar"])
def test_generated_python_module_is_valid_syntax(symbol: str) -> None:
    """A generated test file that does not parse fails as silently as no file at all.

    A C++ overload or a qualified name pasted into a `def` is a `SyntaxError` that takes
    down every valid test in the same module while the scaffolder reports success.
    """
    fn = Function(name=symbol, return_type=CType("int"), parameters=[Parameter("h", Pointer(CType("Opaque")))])
    out = render_python_tests(analyze_work_order(_unit(ENUM_MODE, STRUCT_REC, fn)), "pkg")
    compile(out, "test_workorder.py", "exec")


def test_symbols_differing_only_in_punctuation_get_distinct_test_names() -> None:
    """Two operators collapsing onto one identifier would silently shadow one test."""
    overloads = [
        Function(name=name, return_type=CType("int"), parameters=[])
        for name in ("operator==", "operator!=", "operator<", "operator>")
    ]
    names = [s.name for s in analyze_work_order(_unit(*overloads)).stubs]
    assert len(set(names)) == len(names), names
    assert all(n.isidentifier() for n in names), names


def test_the_raw_symbol_survives_sanitisation_in_the_failure_message() -> None:
    """The reader needs the spelling the header uses, not the identifier we invented."""
    fn = Function(name="operator==", return_type=CType("int"), parameters=[])
    stub = analyze_work_order(_unit(fn)).stubs[0]
    assert stub.name.isidentifier()
    assert stub.symbol == "operator=="
    assert "`operator==`" in stub.instruction


def _nim_code_only(source: str) -> str:
    """Nim source with doc comments and string literals removed.

    A verbatim C signature in a `##` doc comment, or a symbol quoted inside a
    `checkpoint` message, is documentation and may spell the name any way the header
    does. Only what the compiler reads as an identifier matters.
    """
    lines = [line for line in source.splitlines() if not line.lstrip().startswith("##")]
    return re.sub(r'"[^"]*"', '""', "\n".join(lines))


#: Every route by which a name the target language cannot spell reaches generated
#: source. A guard on one of them is not a defence: `AGENTS.md` section 4 states
#: qualified IR names are the intended direction, so the untouched routes become live
#: the moment a backend starts qualifying.
UNSPELLABLE_UNITS: dict[str, tuple[object, ...]] = {
    # The Tier 1 subject: `obj = _bindings.ns::E` / `var obj: ns::E`.
    "enum_subject": (Enum(name="ns::E", values=[EnumValue("A", 0), EnumValue("B", 1)]),),
    "struct_subject": (Struct(name="ns::Rec", fields=[Field(name="a", type=CType("int"))]),),
    # The Tier 2 enum type: `parametrizedTest("use", ns::E)`, reached through the
    # `enums` map rather than through the Tier 1 loop.
    "enum_type": (
        Enum(name="ns::E", values=[EnumValue("A", 0), EnumValue("B", 1)]),
        Function(name="use", return_type=CType("void"), parameters=[Parameter("m", CType("ns::E"))]),
    ),
    # Member names, which no subject guard reaches.
    "enumerator_name": (Enum(name="E", values=[EnumValue("E::A", 0), EnumValue("E::B", 1)]),),
    "field_name": (Struct(name="Rec", fields=[Field(name="a::b", type=CType("int"))]),),
    "bitfield_name": (Struct(name="Rec", fields=[Field(name="a::b", type=CType("unsigned int"), bit_width=3)]),),
}


@pytest.mark.parametrize("case", sorted(UNSPELLABLE_UNITS))
def test_a_name_no_target_language_can_spell_is_skipped_not_pasted(case: str) -> None:
    """`_bindings.ns::E` is a SyntaxError; there is no reference to emit, so emit none."""
    order = analyze_work_order(_unit(*UNSPELLABLE_UNITS[case]))
    assert order.tier1 == []
    compile(render_python_tests(order, "pkg"), "test_workorder.py", "exec")
    nim = render_nim_tests(order, "pkg")
    assert nim is None or "::" not in _nim_code_only(nim), nim


def test_a_tier2_stub_never_names_an_enum_type_it_cannot_spell() -> None:
    """The `enums` map feeds `parametrizedTest` directly, bypassing the Tier 1 guard."""
    order = analyze_work_order(_unit(*UNSPELLABLE_UNITS["enum_type"]))
    stub = order.stubs[0]
    assert stub.enum_type is None, "a qualified enum type would be emitted verbatim into Nim"
    assert stub.cases == ()


def test_colliding_sanitised_names_are_disambiguated_not_silently_shadowed() -> None:
    """`_identifier` is not injective, and Python accepts a duplicate `def` silently."""
    # `Foo::bar`, `Foo__bar` and `Foo:_bar` all sanitise to `Foo__bar`: a three-way
    # collision, which a scheme that appends only `_2` cannot resolve either.
    collide = [
        Function(name=name, return_type=CType("int"), parameters=[])
        for name in ("operator==", "operator_eq_eq", "Foo::bar", "Foo__bar", "Foo:_bar")
    ]
    order = analyze_work_order(_unit(*collide))
    names = [s.name for s in order.stubs]
    assert len(set(names)) == len(names), names

    out = render_python_tests(order, "pkg")
    defs = [line for line in out.splitlines() if line.startswith("def ")]
    assert len(set(defs)) == len(defs), defs
    assert len(defs) == len(collide), defs
    # The raw symbol survives, so a reader can still tell the two apart.
    assert "`operator==`" in out
    assert "`operator_eq_eq`" in out


def test_the_emitters_disambiguate_a_hand_built_work_order() -> None:
    """`analyze_work_order` is not the only way in; a renderer must defend itself."""
    stub = Stub(name="test_dup", symbol="a", signature="int a(void)", instruction="WORK ORDER: x")
    hand_built = WorkOrder(stubs=[stub, replace(stub, symbol="b")])
    assert [s.name for s in hand_built.stubs] == ["test_dup", "test_dup"]

    out = render_python_tests(hand_built, "pkg")
    defs = [line for line in out.splitlines() if line.startswith("def ")]
    assert len(set(defs)) == len(defs) == 2, defs

    nim = render_nim_tests(hand_built, "pkg")
    assert nim is not None
    md = render_work_order_md(hand_built, "pkg")
    for name in (d.removeprefix("def ").split("(")[0] for d in defs):
        assert f"`{name}`" in md, md


def test_disambiguation_is_idempotent_and_reaches_every_artifact() -> None:
    """Renderers re-apply it, so a hand-built order is safe and suffixes never compound."""
    collide = [
        Function(name=name, return_type=CType("int"), parameters=[]) for name in ("operator==", "operator_eq_eq")
    ]
    order = analyze_work_order(_unit(*collide))
    names = [s.name for s in order.stubs]
    assert names == [s.name for s in analyze_work_order(_unit(*collide)).stubs]
    assert "_3" not in render_python_tests(order, "pkg")

    # Every name the work order advertises must exist in the generated module.
    md = render_work_order_md(order, "pkg")
    out = render_python_tests(order, "pkg")
    for name in names:
        assert f"`{name}`" in md, md
        assert f"def {name}(" in out, out


def test_roundtrip_values_fit_the_declared_field_width() -> None:
    """Field N driven with N+1 overflows a narrow field, so the Tier 1 test fails as generated."""
    wide = Struct(name="Packet", fields=[Field(name=f"f{i}", type=CType("uint8_t")) for i in range(200)])
    values = analyze_work_order(_unit(wide)).tier1[0].values
    assert len(values) == 200
    # Representability, computed rather than restated: every value must survive a
    # round-trip through the narrowest 8-bit ctypes field a writer could choose.
    for name, value in values:
        assert ctypes.c_int8(value).value == value, (name, value)
        assert ctypes.c_uint8(value).value == value, (name, value)


def test_roundtrip_values_are_unbounded_for_wide_fields() -> None:
    """Narrowing the bound must not cost distinctness where the field can hold it."""
    wide = Struct(name="Rec", fields=[Field(name=f"f{i}", type=CType("int")) for i in range(200)])
    values = [v for _, v in analyze_work_order(_unit(wide)).tier1[0].values]
    assert values == list(range(1, 201))


def test_nim_stub_checkpoints_before_failing_and_never_requires() -> None:
    """`require` sets abortOnError and kills the whole run, so it must never be emitted."""
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    out = render_nim_tests(analyze_work_order(_unit(fn)), "pkg")
    assert out.index("checkpoint") < out.index("fail()")
    assert "require" not in out


def test_nim_enum_cases_use_the_compile_time_macro() -> None:
    """Textually enumerating values would break on the holey enums C headers produce."""
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    out = render_nim_tests(analyze_work_order(_unit(ENUM_MODE, fn)), "pkg")
    assert 'parametrizedTest("set_mode", Mode)' in out


def test_nim_skips_bitfield_bounds() -> None:
    """Nim bindings carry no bit-field width, so the bound cannot be expressed."""
    out = render_nim_tests(analyze_work_order(_unit(STRUCT_REC)), "pkg")
    assert out is not None
    assert "bits wide" not in out
    assert 'test "":' not in out


#: A header whose only declaration is a struct of unsigned bit-fields. Everything it
#: produces is Tier 1 bit-field bounds, which the Nim emitter cannot express -- so the
#: order is non-empty while the Nim suite has nothing to put in its body. `STRUCT_REC`
#: does not reach this case: its plain `int` field fills the suite and masks it.
BITFIELD_ONLY = Struct(
    name="Flags",
    fields=[
        Field(name="a", type=CType("unsigned int"), bit_width=3),
        Field(name="b", type=CType("unsigned int"), bit_width=5),
    ],
)


def test_nim_returns_none_when_nothing_survives_the_filter() -> None:
    """A `suite` with no body is `Error: invalid indentation`, not a Nim file."""
    order = analyze_work_order(_unit(BITFIELD_ONLY))
    assert order.is_empty is False, "the order itself is non-empty; only Nim cannot express it"
    assert render_nim_tests(order, "pkg") is None


def test_nim_emits_no_files_when_it_has_no_suite_to_emit() -> None:
    """The markdown must not point the next session at a file that was never written."""
    assert build_work_order_files(_unit(BITFIELD_ONLY), "pkg", "nim") == []
    # The same header is fine in Python, which can express the bound.
    assert [f.path for f in build_work_order_files(_unit(BITFIELD_ONLY), "pkg", "python")] == [
        "tests/test_workorder.py",
        "WORK_ORDER.md",
        "SUGGESTIONS.md",
        "AGENTS.md",
    ]


@pytest.mark.parametrize(
    "decls",
    [
        (ENUM_MODE,),
        (STRUCT_REC,),
        (Function(name="go", return_type=CType("int"), parameters=[]),),
        (ENUM_MODE, STRUCT_REC),
    ],
)
def test_every_emitted_nim_suite_has_a_body(decls: tuple[object, ...]) -> None:
    """Whatever survives the filter, the `suite` line must be followed by indented code."""
    out = render_nim_tests(analyze_work_order(_unit(*decls)), "pkg")
    assert out is not None
    lines = out.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("suite "))
    assert any(line.startswith("  ") for line in lines[header + 1 :]), out


def test_nim_work_order_lists_only_what_nim_actually_emitted() -> None:
    """A bit-field bound named as `Already done` in a Nim project was never written."""
    order = analyze_work_order(_unit(STRUCT_REC))
    md = render_work_order_md(order, "pkg", "nim")
    assert "test_Rec_field_roundtrip" in md
    assert "test_Rec_b_bitfield_bounds" not in md
    assert "test_Rec_b_bitfield_bounds" in render_work_order_md(order, "pkg", "python")


def test_nim_struct_roundtrip_asserts_the_literal_not_a_cancelling_conversion() -> None:
    """`check obj.f == type(obj.f)(1)` holds for any field type, order or layout."""
    out = render_nim_tests(analyze_work_order(_unit(STRUCT_REC)), "pkg")
    assert out is not None
    assert "check obj.a.int == 1" in out
    assert "check obj.a == type(obj.a)(1)" not in out


def test_nim_struct_roundtrip_title_claims_only_what_it_proves() -> None:
    """A Nim field write says nothing about the C ABI, so it must not say it does."""
    out = render_nim_tests(analyze_work_order(_unit(STRUCT_REC)), "pkg")
    assert out is not None
    assert "round-trips through the generated wrapper" not in out
    assert "is declared and holds the value written to it" in out


# ---------------------------------------------------------------------------
# Markdown artifacts
# ---------------------------------------------------------------------------


def test_work_order_lists_every_stub_and_no_tier1_test_as_outstanding() -> None:
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    order = analyze_work_order(_unit(ENUM_MODE, fn))
    md = render_work_order_md(order, "pkg")
    assert "test_go" in md
    assert "Already done" in md
    outstanding = md.split("## Needs semantics")[1]
    assert "test_enum_Mode_values" not in outstanding


def test_suggestions_are_static_and_name_no_symbols() -> None:
    """Suggestions are ideas, not findings; identical text for every header."""
    fn = Function(name="uniquely_named_symbol", return_type=CType("int"), parameters=[])
    a = render_suggestions_md("python")
    b = render_suggestions_md("python")
    assert a == b
    assert "uniquely_named_symbol" not in a
    assert "Nim" not in a
    assert "macro system" in render_suggestions_md("nim")
    assert fn.name not in render_suggestions_md("nim")


def test_generated_agents_md_defers_to_the_user() -> None:
    md = render_agents_md("pkg", "python")
    assert "ask the user" in md.lower()
    assert "WORK_ORDER.md" in md
    assert "SUGGESTIONS.md" in md


def test_generated_agents_md_states_no_count_that_could_go_stale() -> None:
    """State must derive from running the tests, never from a number frozen in prose."""
    md = render_agents_md("pkg", "python")
    assert "pytest -q" in md
    assert not any(f"{n} outstanding" in md for n in range(50))


# ---------------------------------------------------------------------------
# Wiring and regeneration safety
# ---------------------------------------------------------------------------


def test_build_returns_nothing_for_an_empty_unit() -> None:
    assert build_work_order_files(_unit(), "pkg", "python") == []


def test_every_generated_artifact_is_preserved_on_regeneration() -> None:
    """A generator that eats the work it asked for is worse than no generator."""
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    files = build_work_order_files(_unit(fn), "pkg", "python")
    assert files, "expected work-order files"
    assert all(f.preserve_existing for f in files)


@pytest.mark.parametrize(
    ("target", "expected"),
    [("ctypes", "tests/test_workorder.py"), ("nim", "tests/test_workorder.nim")],
)
def test_package_layout_includes_the_work_order(target: str, expected: str) -> None:
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    layout = scaffold(
        _unit(ENUM_MODE, fn),
        ScaffoldOptions(package_name="pkg", target_language=target, layout="package"),
    )
    paths = [f.path for f in layout.files]
    assert expected in paths
    assert "WORK_ORDER.md" in paths
    assert "SUGGESTIONS.md" in paths
    assert "AGENTS.md" in paths


def test_filled_in_stub_survives_a_rewrite_to_disk(tmp_path: object) -> None:
    from pathlib import Path

    base = Path(str(tmp_path))
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    unit = _unit(ENUM_MODE, fn)
    opts = ScaffoldOptions(package_name="pkg", target_language="ctypes", layout="package")

    scaffold(unit, opts).write_to_disk(base)
    stub_file = base / "tests" / "test_workorder.py"
    written = "# a human wrote this\ndef test_written_by_hand():\n    assert 2 + 2 == 4\n"
    stub_file.write_text(written, encoding="utf-8")

    scaffold(unit, opts).write_to_disk(base)
    assert stub_file.read_text(encoding="utf-8") == written
    assert (base / "src" / "pkg" / "_bindings.py").exists()
