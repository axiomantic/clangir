"""Tests for tiered work-order test generation."""

from __future__ import annotations

import textwrap

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
    analyze,
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
    order = analyze(_unit(ENUM_MODE))
    assert [t.kind for t in order.tier1] == ["enum_coverage"]
    assert order.stubs == []
    assert order.tier1[0].values == (("MODE_X", 0), ("MODE_Y", 1))


def test_enum_with_unresolved_values_is_skipped() -> None:
    """A backend that leaves an expression unevaluated must not be guessed at."""
    order = analyze(_unit(Enum(name="E", values=[EnumValue("A", "1 << 3"), EnumValue("B", "X")])))
    assert order.tier1 == []


def test_enum_with_aliases_drops_the_distinctness_assertion() -> None:
    """A C enum may deliberately alias, so distinctness is asserted only when observed."""
    aliased = analyze(_unit(Enum(name="E", values=[EnumValue("A", 1), EnumValue("B", 1)])))
    assert aliased.tier1[0].all_distinct is False
    assert "len(set(observed.values()))" not in render_python_tests(aliased, "pkg")

    distinct = analyze(_unit(ENUM_MODE))
    assert distinct.tier1[0].all_distinct is True
    assert "len(set(observed.values()))" in render_python_tests(distinct, "pkg")


def test_struct_yields_roundtrip_and_bitfield_tier1() -> None:
    order = analyze(_unit(STRUCT_REC))
    kinds = [t.kind for t in order.tier1]
    assert kinds == ["struct_roundtrip", "bitfield_bounds"]
    assert order.tier1[1].width == 3
    assert order.stubs == []


def test_signed_bitfield_is_excluded() -> None:
    """Sign extension makes the truncated value ABI-dependent, so the IR does not know it."""
    signed = Struct(name="S", fields=[Field(name="v", type=CType("int"), bit_width=3)])
    order = analyze(_unit(signed))
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
    assert analyze(_unit(s)).tier1 == []


def test_function_with_enum_param_is_tier2_one_case_per_enumerator() -> None:
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    order = analyze(_unit(ENUM_MODE, fn))
    stub = order.stubs[0]
    assert stub.tier == 2
    assert stub.cases == ("MODE_X", "MODE_Y")
    assert stub.enum_type == "Mode"


def test_overload_set_is_tier2_one_case_per_overload() -> None:
    a = Function(name="f", return_type=CType("int"), parameters=[Parameter("x", CType("int"))])
    b = Function(name="f", return_type=CType("int"), parameters=[Parameter("x", CType("double"))])
    stub = analyze(_unit(a, b)).stubs[0]
    assert stub.tier == 2
    assert len(stub.cases) == 2


def test_pointer_param_adds_a_null_case_without_losing_the_semantics_question() -> None:
    """The NULL case must not displace the more valuable question of what the function is for."""
    fn = Function(name="destroy", return_type=CType("void"), parameters=[Parameter("h", Pointer(CType("Opaque")))])
    stub = analyze(_unit(fn)).stubs[0]
    assert stub.cases == ("behaviour", "null_h")
    assert "what `destroy` is for" in stub.instruction
    assert "NULL" in stub.instruction


def test_plain_function_is_tier3() -> None:
    fn = Function(name="tick", return_type=CType("int"), parameters=[])
    stub = analyze(_unit(fn)).stubs[0]
    assert stub.tier == 3
    assert stub.cases == ()


def test_a_function_never_gets_both_a_tier2_and_a_tier3_stub() -> None:
    """Diluting the work order with duplicate entries is how this feature fails."""
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    order = analyze(_unit(ENUM_MODE, fn))
    assert [s.name for s in order.stubs] == ["test_set_mode"]


def test_records_and_enums_never_produce_stubs() -> None:
    order = analyze(_unit(ENUM_MODE, STRUCT_REC))
    assert order.stubs == []
    assert len(order.tier1) == 3


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------


def test_python_tier1_enum_test_asserts_real_values() -> None:
    out = render_python_tests(analyze(_unit(ENUM_MODE)), "pkg")
    assert 'observed = {"MODE_X": _bindings.MODE_X, "MODE_Y": _bindings.MODE_Y}' in out
    assert 'assert observed == {"MODE_X": 0, "MODE_Y": 1}' in out
    assert WORK_ORDER_MARKER not in out.split("def test_enum_Mode_values")[1].split("def ")[0]


def test_python_bitfield_test_asserts_the_derived_bound() -> None:
    out = render_python_tests(analyze(_unit(STRUCT_REC)), "pkg")
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
    out = render_python_tests(analyze(_unit(fn)), "pkg")
    assert "pytest.fail(" in out
    assert "int parse_config(const char* path)" in out
    assert "Asserting that it does not raise is insufficient" in out


def test_python_tier2_uses_parametrize_with_one_id_per_case() -> None:
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    out = render_python_tests(analyze(_unit(ENUM_MODE, fn)), "pkg")
    assert "@pytest.mark.parametrize('case', ['MODE_X', 'MODE_Y'])" in out


def test_generated_python_module_is_valid_syntax() -> None:
    """A generated test file that does not parse fails as silently as no file at all."""
    fn = Function(name="go", return_type=CType("int"), parameters=[Parameter("h", Pointer(CType("Opaque")))])
    out = render_python_tests(analyze(_unit(ENUM_MODE, STRUCT_REC, fn)), "pkg")
    compile(out, "test_workorder.py", "exec")


def test_nim_stub_checkpoints_before_failing_and_never_requires() -> None:
    """`require` sets abortOnError and kills the whole run, so it must never be emitted."""
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    out = render_nim_tests(analyze(_unit(fn)), "pkg")
    assert out.index("checkpoint") < out.index("fail()")
    assert "require" not in out


def test_nim_enum_cases_use_the_compile_time_macro() -> None:
    """Textually enumerating values would break on the holey enums C headers produce."""
    fn = Function(name="set_mode", return_type=CType("void"), parameters=[Parameter("m", CType("Mode"))])
    out = render_nim_tests(analyze(_unit(ENUM_MODE, fn)), "pkg")
    assert 'parametrizedTest("set_mode", Mode)' in out


def test_nim_skips_bitfield_bounds() -> None:
    """Nim bindings carry no bit-field width, so the bound cannot be expressed."""
    out = render_nim_tests(analyze(_unit(STRUCT_REC)), "pkg")
    assert "bits wide" not in out
    assert 'test "":' not in out


# ---------------------------------------------------------------------------
# Markdown artifacts
# ---------------------------------------------------------------------------


def test_work_order_lists_every_stub_and_no_tier1_test_as_outstanding() -> None:
    fn = Function(name="go", return_type=CType("int"), parameters=[])
    order = analyze(_unit(ENUM_MODE, fn))
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
    stub_file.write_text("# a human wrote this\nassert True is not False\n", encoding="utf-8")

    scaffold(unit, opts).write_to_disk(base)
    assert stub_file.read_text(encoding="utf-8") == "# a human wrote this\nassert True is not False\n"
    assert (base / "src" / "pkg" / "_bindings.py").exists()
