"""Tests for the diff writer."""

from __future__ import annotations

import json

from headerkit.ir import (
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers import WriterBackend, get_writer
from headerkit.writers.diff import (
    DiffReport,
    DiffWriter,
    diff_headers,
    diff_to_json,
    diff_to_markdown,
)

# =============================================================================
# Helpers
# =============================================================================


def _empty_header(path: str = "(empty)") -> Header:
    return Header(path=path, declarations=[])


def _header_with(
    *decls: Enum | Struct | Function | Typedef | Variable | Constant,
    path: str = "test.h",
) -> Header:
    return Header(path=path, declarations=list(decls))


# =============================================================================
# DiffReport properties
# =============================================================================


class TestDiffReport:
    """Tests for DiffReport.breaking_count and non_breaking_count."""

    def test_empty_report(self) -> None:
        report = DiffReport("a.h", "b.h", [])
        assert report.breaking_count == 0
        assert report.non_breaking_count == 0

    def test_mixed_report(self) -> None:
        baseline = _header_with(
            Function("old_fn", CType("void"), []),
            path="a.h",
        )
        target = _header_with(
            Function("new_fn", CType("void"), []),
            path="b.h",
        )
        report = diff_headers(baseline, target)
        # old_fn removed (breaking), new_fn added (non_breaking)
        assert report.breaking_count == 1
        assert report.non_breaking_count == 1


# =============================================================================
# Empty / identical diffs
# =============================================================================


class TestEmptyAndIdentical:
    """Tests for edge cases: empty-to-populated, populated-to-empty, identical."""

    def test_empty_to_populated(self) -> None:
        """All declarations in target show as additions."""
        target = _header_with(
            Function("foo", CType("int"), []),
            Struct("Bar", [Field("x", CType("int"))]),
            Enum("Color", [EnumValue("RED", 0)]),
            path="new.h",
        )
        report = diff_headers(_empty_header(), target)
        assert report.breaking_count == 0
        assert report.non_breaking_count == 3
        kinds = {e.kind for e in report.entries}
        assert kinds == {"function_added", "struct_added", "enum_added"}

    def test_populated_to_empty(self) -> None:
        """All declarations in baseline show as removals (breaking)."""
        baseline = _header_with(
            Function("foo", CType("int"), []),
            Struct("Bar", [Field("x", CType("int"))]),
            path="old.h",
        )
        report = diff_headers(baseline, _empty_header("new.h"))
        assert report.breaking_count == 2
        assert report.non_breaking_count == 0
        kinds = {e.kind for e in report.entries}
        assert kinds == {"function_removed", "struct_removed"}

    def test_identical_headers(self) -> None:
        """No changes when baseline and target are the same."""
        h = _header_with(
            Function("foo", CType("int"), []),
            Struct("Bar", [Field("x", CType("int"))]),
        )
        report = diff_headers(h, h)
        assert len(report.entries) == 0
        assert report.breaking_count == 0
        assert report.non_breaking_count == 0


# =============================================================================
# Function diffs
# =============================================================================


class TestFunctionDiff:
    """Tests for function add/remove/change detection."""

    def test_function_added(self) -> None:
        baseline = _empty_header()
        target = _header_with(Function("new_fn", CType("void"), []))
        report = diff_headers(baseline, target)
        assert len(report.entries) == 1
        assert report.entries[0].kind == "function_added"
        assert report.entries[0].severity == "non_breaking"
        assert report.entries[0].name == "new_fn"

    def test_function_removed(self) -> None:
        baseline = _header_with(Function("old_fn", CType("void"), []))
        target = _empty_header()
        report = diff_headers(baseline, target)
        assert len(report.entries) == 1
        assert report.entries[0].kind == "function_removed"
        assert report.entries[0].severity == "breaking"

    def test_return_type_changed(self) -> None:
        baseline = _header_with(Function("f", CType("int"), []))
        target = _header_with(Function("f", CType("void"), []))
        report = diff_headers(baseline, target)
        breaking = [e for e in report.entries if e.severity == "breaking"]
        assert len(breaking) == 1
        assert breaking[0].kind == "function_signature_changed"
        assert breaking[0].detail == "return type changed from 'int' to 'void'"

    def test_parameter_added(self) -> None:
        baseline = _header_with(Function("f", CType("int"), []))
        target = _header_with(Function("f", CType("int"), [Parameter("x", CType("int"))]))
        report = diff_headers(baseline, target)
        breaking = [e for e in report.entries if e.severity == "breaking"]
        assert len(breaking) == 1
        assert breaking[0].kind == "function_signature_changed"
        assert breaking[0].detail == "parameter count changed from 0 to 1"

    def test_parameter_type_changed(self) -> None:
        baseline = _header_with(Function("f", CType("void"), [Parameter("x", CType("int"))]))
        target = _header_with(Function("f", CType("void"), [Parameter("x", CType("float"))]))
        report = diff_headers(baseline, target)
        breaking = [e for e in report.entries if e.severity == "breaking"]
        assert len(breaking) == 1
        assert breaking[0].kind == "function_signature_changed"
        assert breaking[0].detail == "parameter 0 type changed from 'int' to 'float'"

    def test_parameter_renamed_is_non_breaking(self) -> None:
        baseline = _header_with(Function("f", CType("void"), [Parameter("x", CType("int"))]))
        target = _header_with(Function("f", CType("void"), [Parameter("y", CType("int"))]))
        report = diff_headers(baseline, target)
        assert report.breaking_count == 0
        renamed = [e for e in report.entries if e.kind == "function_parameter_renamed"]
        assert len(renamed) == 1
        assert renamed[0].severity == "non_breaking"

    def test_variadic_changed(self) -> None:
        baseline = _header_with(
            Function(
                "f",
                CType("int"),
                [Parameter("fmt", Pointer(CType("char", ["const"])))],
                is_variadic=False,
            )
        )
        target = _header_with(
            Function(
                "f",
                CType("int"),
                [Parameter("fmt", Pointer(CType("char", ["const"])))],
                is_variadic=True,
            )
        )
        report = diff_headers(baseline, target)
        breaking = [e for e in report.entries if e.severity == "breaking"]
        assert len(breaking) == 1
        assert breaking[0].detail == "variadic changed from False to True"

    def test_calling_convention_changed(self) -> None:
        baseline = _header_with(Function("f", CType("int"), [], calling_convention=None))
        target = _header_with(Function("f", CType("int"), [], calling_convention="stdcall"))
        report = diff_headers(baseline, target)
        breaking = [e for e in report.entries if e.severity == "breaking"]
        assert len(breaking) == 1
        assert breaking[0].detail == "calling convention changed from 'None' to 'stdcall'"


# =============================================================================
# Struct diffs
# =============================================================================


class TestStructDiff:
    """Tests for struct field add/remove/type change detection."""

    def test_struct_added(self) -> None:
        report = diff_headers(
            _empty_header(),
            _header_with(Struct("S", [Field("x", CType("int"))])),
        )
        assert len(report.entries) == 1
        assert report.entries[0].kind == "struct_added"
        assert report.entries[0].severity == "non_breaking"

    def test_struct_removed(self) -> None:
        report = diff_headers(
            _header_with(Struct("S", [Field("x", CType("int"))])),
            _empty_header(),
        )
        assert len(report.entries) == 1
        assert report.entries[0].kind == "struct_removed"
        assert report.entries[0].severity == "breaking"

    def test_field_added_at_end(self) -> None:
        baseline = _header_with(Struct("S", [Field("x", CType("int"))]))
        target = _header_with(Struct("S", [Field("x", CType("int")), Field("y", CType("int"))]))
        report = diff_headers(baseline, target)
        added = [e for e in report.entries if e.kind == "struct_field_added"]
        assert len(added) == 1
        assert added[0].severity == "non_breaking"

    def test_field_removed(self) -> None:
        baseline = _header_with(Struct("S", [Field("x", CType("int")), Field("y", CType("int"))]))
        target = _header_with(Struct("S", [Field("x", CType("int"))]))
        report = diff_headers(baseline, target)
        removed = [e for e in report.entries if e.kind == "struct_field_removed"]
        assert len(removed) == 1
        assert removed[0].severity == "breaking"

    def test_field_type_changed(self) -> None:
        baseline = _header_with(Struct("S", [Field("x", CType("int"))]))
        target = _header_with(Struct("S", [Field("x", CType("float"))]))
        report = diff_headers(baseline, target)
        changed = [e for e in report.entries if e.kind == "struct_field_type_changed"]
        assert len(changed) == 1
        assert changed[0].severity == "breaking"
        assert changed[0].detail == "field 'x' type changed from 'int' to 'float'"

    def test_field_reordered(self) -> None:
        baseline = _header_with(Struct("S", [Field("x", CType("int")), Field("y", CType("float"))]))
        target = _header_with(Struct("S", [Field("y", CType("float")), Field("x", CType("int"))]))
        report = diff_headers(baseline, target)
        reordered = [e for e in report.entries if e.kind == "struct_field_reordered"]
        assert len(reordered) == 2  # both fields moved
        assert all(e.severity == "breaking" for e in reordered)

    def test_packed_changed(self) -> None:
        baseline = _header_with(Struct("S", [], is_packed=False))
        target = _header_with(Struct("S", [], is_packed=True))
        report = diff_headers(baseline, target)
        layout = [e for e in report.entries if e.kind == "struct_layout_changed"]
        assert len(layout) == 1
        assert layout[0].severity == "breaking"

    def test_union_to_struct_changed(self) -> None:
        baseline = _header_with(Struct("U", [Field("x", CType("int"))], is_union=True))
        target = _header_with(Struct("U", [Field("x", CType("int"))], is_union=False))
        report = diff_headers(baseline, target)
        layout = [e for e in report.entries if e.kind == "struct_layout_changed"]
        assert len(layout) == 1
        assert layout[0].severity == "breaking"


# =============================================================================
# Enum diffs
# =============================================================================


class TestEnumDiff:
    """Tests for enum value add/remove/change detection."""

    def test_enum_added(self) -> None:
        report = diff_headers(
            _empty_header(),
            _header_with(Enum("E", [EnumValue("A", 0)])),
        )
        assert report.entries[0].kind == "enum_added"
        assert report.entries[0].severity == "non_breaking"

    def test_enum_removed(self) -> None:
        report = diff_headers(
            _header_with(Enum("E", [EnumValue("A", 0)])),
            _empty_header(),
        )
        assert report.entries[0].kind == "enum_removed"
        assert report.entries[0].severity == "breaking"

    def test_enum_value_added(self) -> None:
        baseline = _header_with(Enum("E", [EnumValue("A", 0)]))
        target = _header_with(Enum("E", [EnumValue("A", 0), EnumValue("B", 1)]))
        report = diff_headers(baseline, target)
        added = [e for e in report.entries if e.kind == "enum_value_added"]
        assert len(added) == 1
        assert added[0].severity == "non_breaking"
        assert added[0].name == "E"

    def test_enum_value_removed(self) -> None:
        baseline = _header_with(Enum("E", [EnumValue("A", 0), EnumValue("B", 1)]))
        target = _header_with(Enum("E", [EnumValue("A", 0)]))
        report = diff_headers(baseline, target)
        removed = [e for e in report.entries if e.kind == "enum_value_removed"]
        assert len(removed) == 1
        assert removed[0].severity == "breaking"

    def test_enum_value_changed(self) -> None:
        baseline = _header_with(Enum("E", [EnumValue("A", 0)]))
        target = _header_with(Enum("E", [EnumValue("A", 42)]))
        report = diff_headers(baseline, target)
        changed = [e for e in report.entries if e.kind == "enum_value_changed"]
        assert len(changed) == 1
        assert changed[0].severity == "breaking"
        assert changed[0].detail == "enum value 'A' changed from 0 to 42"


# =============================================================================
# Typedef diffs
# =============================================================================


class TestTypedefDiff:
    """Tests for typedef change detection."""

    def test_typedef_added(self) -> None:
        report = diff_headers(
            _empty_header(),
            _header_with(Typedef("MyInt", CType("int"))),
        )
        assert report.entries[0].kind == "typedef_added"
        assert report.entries[0].severity == "non_breaking"

    def test_typedef_removed(self) -> None:
        report = diff_headers(
            _header_with(Typedef("MyInt", CType("int"))),
            _empty_header(),
        )
        assert report.entries[0].kind == "typedef_removed"
        assert report.entries[0].severity == "breaking"

    def test_typedef_changed(self) -> None:
        baseline = _header_with(Typedef("MyInt", CType("int")))
        target = _header_with(Typedef("MyInt", CType("long")))
        report = diff_headers(baseline, target)
        changed = [e for e in report.entries if e.kind == "typedef_changed"]
        assert len(changed) == 1
        assert changed[0].severity == "breaking"
        assert changed[0].name == "MyInt"


# =============================================================================
# Constant diffs
# =============================================================================


class TestConstantDiff:
    """Tests for constant add/remove/change detection."""

    def test_constant_added(self) -> None:
        report = diff_headers(
            _empty_header(),
            _header_with(Constant("SIZE", 100, is_macro=True)),
        )
        assert report.entries[0].kind == "constant_added"
        assert report.entries[0].severity == "non_breaking"

    def test_constant_removed(self) -> None:
        report = diff_headers(
            _header_with(Constant("SIZE", 100, is_macro=True)),
            _empty_header(),
        )
        assert report.entries[0].kind == "constant_removed"
        assert report.entries[0].severity == "breaking"

    def test_constant_changed(self) -> None:
        baseline = _header_with(Constant("SIZE", 100, is_macro=True))
        target = _header_with(Constant("SIZE", 200, is_macro=True))
        report = diff_headers(baseline, target)
        changed = [e for e in report.entries if e.kind == "constant_changed"]
        assert len(changed) == 1
        assert changed[0].severity == "non_breaking"


# =============================================================================
# Variable diffs
# =============================================================================


class TestVariableDiff:
    """Tests for variable add/remove/type change detection."""

    def test_variable_added(self) -> None:
        report = diff_headers(
            _empty_header(),
            _header_with(Variable("g_count", CType("int"))),
        )
        assert report.entries[0].kind == "variable_added"
        assert report.entries[0].severity == "non_breaking"

    def test_variable_removed(self) -> None:
        report = diff_headers(
            _header_with(Variable("g_count", CType("int"))),
            _empty_header(),
        )
        assert report.entries[0].kind == "variable_removed"
        assert report.entries[0].severity == "breaking"

    def test_variable_type_changed(self) -> None:
        baseline = _header_with(Variable("g_count", CType("int")))
        target = _header_with(Variable("g_count", CType("long")))
        report = diff_headers(baseline, target)
        changed = [e for e in report.entries if e.kind == "variable_type_changed"]
        assert len(changed) == 1
        assert changed[0].severity == "breaking"


# =============================================================================
# JSON output
# =============================================================================


class TestJsonOutput:
    """Tests for diff_to_json output."""

    def test_valid_json(self) -> None:
        report = diff_headers(
            _header_with(Function("f", CType("int"), []), path="a.h"),
            _header_with(Function("g", CType("void"), []), path="b.h"),
        )
        output = diff_to_json(report)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)
        assert parsed["schema_version"] == "1.0"
        assert isinstance(parsed["entries"], list)

    def test_schema_version(self) -> None:
        report = DiffReport("a.h", "b.h", [])
        parsed = json.loads(diff_to_json(report))
        assert parsed["schema_version"] == "1.0"

    def test_summary_counts(self) -> None:
        report = diff_headers(
            _header_with(
                Function("old", CType("void"), []),
                path="a.h",
            ),
            _header_with(
                Function("new", CType("void"), []),
                path="b.h",
            ),
        )
        parsed = json.loads(diff_to_json(report))
        assert parsed["summary"]["breaking"] == 1
        assert parsed["summary"]["non_breaking"] == 1
        assert parsed["summary"]["total"] == 2

    def test_entries_structure(self) -> None:
        report = diff_headers(
            _empty_header("a.h"),
            _header_with(Function("f", CType("int"), []), path="b.h"),
        )
        parsed = json.loads(diff_to_json(report))
        assert len(parsed["entries"]) == 1
        entry = parsed["entries"][0]
        assert entry == {
            "kind": "function_added",
            "severity": "non_breaking",
            "name": "f",
            "detail": "function 'f' added",
            "target": "int f()",
        }

    def test_baseline_target_paths(self) -> None:
        report = DiffReport("old.h", "new.h", [])
        parsed = json.loads(diff_to_json(report))
        assert parsed["baseline"] == "old.h"
        assert parsed["target"] == "new.h"


# =============================================================================
# Markdown output
# =============================================================================


class TestMarkdownOutput:
    """Tests for diff_to_markdown output."""

    def test_has_title(self) -> None:
        report = DiffReport("a.h", "b.h", [])
        md = diff_to_markdown(report)
        lines = md.splitlines()
        assert lines[0] == "# API Diff: a.h -> b.h"

    def test_has_summary_table(self) -> None:
        report = diff_headers(
            _header_with(Function("f", CType("int"), []), path="a.h"),
            _header_with(
                Function("f", CType("int"), []),
                Function("g", CType("void"), []),
                path="b.h",
            ),
        )
        md = diff_to_markdown(report)
        expected = (
            "# API Diff: a.h -> b.h\n"
            "\n"
            "## Summary\n"
            "\n"
            "| Category | Count |\n"
            "|----------|-------|\n"
            "| Breaking | 0 |\n"
            "| Non-breaking | 1 |\n"
            "| Total | 1 |\n"
            "\n"
            "## Non-Breaking Changes\n"
            "\n"
            "### function_added\n"
            "\n"
            "- **g**: function 'g' added\n"
            "  - Target: `void g()`\n"
        )
        assert md == expected

    def test_breaking_section(self) -> None:
        report = diff_headers(
            _header_with(Function("f", CType("int"), []), path="a.h"),
            _empty_header("b.h"),
        )
        md = diff_to_markdown(report)
        expected = (
            "# API Diff: a.h -> b.h\n"
            "\n"
            "## Summary\n"
            "\n"
            "| Category | Count |\n"
            "|----------|-------|\n"
            "| Breaking | 1 |\n"
            "| Non-breaking | 0 |\n"
            "| Total | 1 |\n"
            "\n"
            "## Breaking Changes\n"
            "\n"
            "### function_removed\n"
            "\n"
            "- **f**: function 'f' removed\n"
            "  - Baseline: `int f()`\n"
        )
        assert md == expected

    def test_non_breaking_section(self) -> None:
        report = diff_headers(
            _empty_header("a.h"),
            _header_with(Function("f", CType("int"), []), path="b.h"),
        )
        md = diff_to_markdown(report)
        expected = (
            "# API Diff: a.h -> b.h\n"
            "\n"
            "## Summary\n"
            "\n"
            "| Category | Count |\n"
            "|----------|-------|\n"
            "| Breaking | 0 |\n"
            "| Non-breaking | 1 |\n"
            "| Total | 1 |\n"
            "\n"
            "## Non-Breaking Changes\n"
            "\n"
            "### function_added\n"
            "\n"
            "- **f**: function 'f' added\n"
            "  - Target: `int f()`\n"
        )
        assert md == expected

    def test_no_changes_message(self) -> None:
        report = DiffReport("a.h", "b.h", [])
        md = diff_to_markdown(report)
        expected = (
            "# API Diff: a.h -> b.h\n"
            "\n"
            "## Summary\n"
            "\n"
            "| Category | Count |\n"
            "|----------|-------|\n"
            "| Breaking | 0 |\n"
            "| Non-breaking | 0 |\n"
            "| Total | 0 |\n"
            "\n"
            "No changes detected.\n"
        )
        assert md == expected


# =============================================================================
# DiffWriter class
# =============================================================================


class TestDiffWriter:
    """Tests for the DiffWriter class."""

    def test_protocol_compliance(self) -> None:
        writer = DiffWriter()
        assert isinstance(writer, WriterBackend)

    def test_name_property(self) -> None:
        writer = DiffWriter()
        assert writer.name == "diff"

    def test_format_description_property(self) -> None:
        writer = DiffWriter()
        assert writer.format_description == "API compatibility diff reports (JSON or Markdown)"

    def test_write_json_default(self) -> None:
        baseline = _header_with(Function("f", CType("int"), []))
        writer = DiffWriter(baseline=baseline, format="json")
        target = _header_with(
            Function("f", CType("int"), []),
            Function("g", CType("void"), []),
        )
        output = writer.write(target)
        parsed = json.loads(output)
        assert parsed["summary"]["non_breaking"] == 1

    def test_write_markdown(self) -> None:
        baseline = _header_with(Function("f", CType("int"), []))
        writer = DiffWriter(baseline=baseline, format="markdown")
        target = _header_with(Function("f", CType("void"), []))
        output = writer.write(target)
        lines = output.splitlines()
        breaking_section_lines = [line for line in lines if line.startswith("## ")]
        assert breaking_section_lines == ["## Summary", "## Breaking Changes"]

    def test_no_baseline_all_additions(self) -> None:
        writer = DiffWriter(baseline=None, format="json")
        target = _header_with(
            Function("f", CType("int"), []),
            Struct("S", [Field("x", CType("int"))]),
        )
        output = writer.write(target)
        parsed = json.loads(output)
        assert parsed["summary"]["breaking"] == 0
        assert parsed["summary"]["non_breaking"] == 2

    def test_get_writer_integration(self) -> None:
        """get_writer('diff') returns a DiffWriter from the registry."""
        writer = get_writer("diff")
        assert isinstance(writer, WriterBackend)
        assert isinstance(writer, DiffWriter)

    def test_get_writer_with_baseline(self) -> None:
        """get_writer('diff', baseline=...) passes through to constructor."""
        baseline = _header_with(Function("f", CType("int"), []))
        writer = get_writer("diff", baseline=baseline, format="markdown")
        assert isinstance(writer, DiffWriter)
        target = _header_with(Function("f", CType("void"), []))
        output = writer.write(target)
        lines = output.splitlines()
        breaking_section_lines = [line for line in lines if line.startswith("## ")]
        assert breaking_section_lines == ["## Summary", "## Breaking Changes"]

    def test_anonymous_declarations_skipped(self) -> None:
        anon_struct = Struct(None, [Field("x", CType("int"))])
        baseline = Header("test.h", [anon_struct])
        target = Header("test.h", [anon_struct])
        writer = DiffWriter(baseline=baseline)
        output = writer.write(target)
        parsed = json.loads(output)
        assert len(parsed["entries"]) == 0


# =============================================================================
# Severity classification comprehensive check
# =============================================================================


class TestSeverityClassification:
    """Verify severity is correct for each change kind."""

    def test_all_removal_kinds_are_breaking(self) -> None:
        """Every _removed kind should be breaking."""
        baseline = _header_with(
            Function("f", CType("int"), []),
            Struct("S", [Field("x", CType("int"))]),
            Enum("E", [EnumValue("A", 0)]),
            Typedef("T", CType("int")),
            Variable("v", CType("int")),
            Constant("C", 42, is_macro=True),
        )
        report = diff_headers(baseline, _empty_header())
        for entry in report.entries:
            assert entry.severity == "breaking", f"{entry.kind} should be breaking but was {entry.severity}"

    def test_all_addition_kinds_are_non_breaking(self) -> None:
        """Every _added kind should be non_breaking."""
        target = _header_with(
            Function("f", CType("int"), []),
            Struct("S", [Field("x", CType("int"))]),
            Enum("E", [EnumValue("A", 0)]),
            Typedef("T", CType("int")),
            Variable("v", CType("int")),
            Constant("C", 42, is_macro=True),
        )
        report = diff_headers(_empty_header(), target)
        for entry in report.entries:
            assert entry.severity == "non_breaking", f"{entry.kind} should be non_breaking but was {entry.severity}"

    def test_constant_value_change_is_non_breaking(self) -> None:
        baseline = _header_with(Constant("C", 1, is_macro=True))
        target = _header_with(Constant("C", 2, is_macro=True))
        report = diff_headers(baseline, target)
        assert report.entries[0].severity == "non_breaking"

    def test_function_signature_change_is_breaking(self) -> None:
        baseline = _header_with(Function("f", CType("int"), []))
        target = _header_with(Function("f", CType("void"), []))
        report = diff_headers(baseline, target)
        assert report.entries[0].severity == "breaking"

    def test_struct_field_type_change_is_breaking(self) -> None:
        baseline = _header_with(Struct("S", [Field("x", CType("int"))]))
        target = _header_with(Struct("S", [Field("x", CType("double"))]))
        report = diff_headers(baseline, target)
        type_changes = [e for e in report.entries if e.kind == "struct_field_type_changed"]
        assert len(type_changes) == 1
        assert type_changes[0].severity == "breaking"

    def test_enum_value_change_is_breaking(self) -> None:
        baseline = _header_with(Enum("E", [EnumValue("A", 0)]))
        target = _header_with(Enum("E", [EnumValue("A", 99)]))
        report = diff_headers(baseline, target)
        assert report.entries[0].severity == "breaking"

    def test_typedef_change_is_breaking(self) -> None:
        baseline = _header_with(Typedef("T", CType("int")))
        target = _header_with(Typedef("T", CType("long")))
        report = diff_headers(baseline, target)
        assert report.entries[0].severity == "breaking"

    def test_variable_type_change_is_breaking(self) -> None:
        baseline = _header_with(Variable("v", CType("int")))
        target = _header_with(Variable("v", Pointer(CType("int"))))
        report = diff_headers(baseline, target)
        assert report.entries[0].severity == "breaking"


# =============================================================================
# No-change invariant tests
# =============================================================================


class TestNoChangeInvariant:
    """Identical declarations should produce zero diff entries."""

    def test_identical_functions_produce_no_diff(self) -> None:
        """Unchanged functions should produce zero diff entries."""
        f = Function("test", CType("int"), [Parameter("x", CType("int"))])
        baseline = Header(path="a.h", declarations=[f])
        target = Header(path="b.h", declarations=[f])
        report = diff_headers(baseline, target)
        assert len(report.entries) == 0

    def test_identical_structs_produce_no_diff(self) -> None:
        """Unchanged structs should produce zero diff entries."""
        s = Struct("S", [Field("x", CType("int")), Field("y", CType("float"))])
        baseline = Header(path="a.h", declarations=[s])
        target = Header(path="b.h", declarations=[s])
        report = diff_headers(baseline, target)
        assert len(report.entries) == 0

    def test_identical_enums_produce_no_diff(self) -> None:
        """Unchanged enums should produce zero diff entries."""
        e = Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])
        baseline = Header(path="a.h", declarations=[e])
        target = Header(path="b.h", declarations=[e])
        report = diff_headers(baseline, target)
        assert len(report.entries) == 0


# =============================================================================
# Struct field ordering edge cases
# =============================================================================


class TestStructFieldMiddleInsertion:
    """Test that inserting a field in the middle is classified as breaking."""

    def test_field_added_in_middle_is_breaking(self) -> None:
        """A field inserted between existing fields is a breaking change."""
        baseline = _header_with(Struct("S", [Field("x", CType("int")), Field("z", CType("int"))]))
        target = _header_with(
            Struct(
                "S",
                [
                    Field("x", CType("int")),
                    Field("y", CType("int")),
                    Field("z", CType("int")),
                ],
            )
        )
        report = diff_headers(baseline, target)
        added = [e for e in report.entries if e.kind == "struct_field_added"]
        assert len(added) == 1
        assert added[0].severity == "breaking"
        assert added[0].detail == "field 'y' added"
