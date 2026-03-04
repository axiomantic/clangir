"""Integration tests for the diff writer: full pipeline (libclang -> IR -> diff output)."""

from __future__ import annotations

import json
import textwrap

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.writers.diff import DiffWriter, diff_headers, diff_to_json, diff_to_markdown

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


@pytest.fixture(scope="session")
def backend():
    return get_backend("libclang")


def parse_diff_json(backend, baseline_code: str, target_code: str) -> dict:
    """Parse two C code strings with libclang and return the parsed diff JSON dict."""
    b = backend.parse(baseline_code, "baseline.h")
    t = backend.parse(target_code, "target.h")
    report = diff_headers(b, t)
    return json.loads(diff_to_json(report))


def parse_diff_markdown(backend, baseline_code: str, target_code: str) -> str:
    """Parse two C code strings with libclang and return the diff as Markdown."""
    b = backend.parse(baseline_code, "baseline.h")
    t = backend.parse(target_code, "target.h")
    report = diff_headers(b, t)
    return diff_to_markdown(report)


# =============================================================================
# TestDiffEmpty
# =============================================================================


class TestDiffEmpty:
    """Tests for the zero-change cases: empty headers and identical headers."""

    def test_empty_vs_empty(self, backend):
        """Diffing two empty headers produces a clean zero-entry report.

        ESCAPE: test_empty_vs_empty
          CLAIM: Two empty headers parsed by libclang produce a JSON report with
                 schema_version '1.0', summary.total==0, and an empty entries list.
          PATH:  backend.parse('', ...) -> Header(declarations=[]) x2 -> diff_headers ->
                 DiffReport(entries=[]) -> diff_to_json
          CHECK: schema_version, summary.total, summary.breaking, summary.non_breaking,
                 entries == []
          MUTATION:
            - assert schema_version == '1.0': fails if version bumped to '2.0' silently
            - assert summary.total == 0: fails if diff_headers creates phantom entries for empty input
            - assert summary.breaking == 0: fails if removed-declarations logic runs on empty map
            - assert summary.non_breaking == 0: fails if added-declarations logic runs on empty map
            - assert entries == []: fails if any entry is appended to an empty-to-empty diff
          ESCAPE: An implementation that returns entries=None instead of [] would
                  slip past a len() check but is caught by == [].
        """
        parsed = parse_diff_json(backend, "", "")

        assert parsed["schema_version"] == "1.0"
        assert parsed["baseline"] == "baseline.h"
        assert parsed["target"] == "target.h"
        assert parsed["summary"] == {"total": 0, "breaking": 0, "non_breaking": 0}
        assert parsed["entries"] == []

    def test_identical_headers(self, backend):
        """Diffing identical non-empty headers produces a zero-entry report.

        ESCAPE: test_identical_headers
          CLAIM: Parsing the same C source as both baseline and target yields no diff entries.
          PATH:  backend.parse(same_code, ...) x2 -> diff_headers checks each decl key against
                 the other map -> no mismatches -> DiffReport(entries=[]) -> diff_to_json
          CHECK: summary.total==0, entries==[]
          MUTATION:
            - assert summary.total == 0: fails if diff_headers treats same-named function as changed
            - assert entries == []: fails if a spurious entry is generated for identical declarations
          ESCAPE: An implementation that always appends a 'function_signature_changed' entry
                  even when signatures match would be caught by entries == [].
        """
        code = "void foo(void);"
        parsed = parse_diff_json(backend, code, code)

        assert parsed["summary"] == {"total": 0, "breaking": 0, "non_breaking": 0}
        assert parsed["entries"] == []


# =============================================================================
# TestDiffFunctionChanges
# =============================================================================


class TestDiffFunctionChanges:
    """Tests for function-level API changes detected through the full pipeline."""

    def test_function_added(self, backend):
        """Adding a new function is reported as a single non_breaking function_added entry.

        ESCAPE: test_function_added
          CLAIM: When target adds 'bar' not present in baseline, exactly one entry is produced:
                 kind='function_added', severity='non_breaking', name='bar',
                 detail="function 'bar' added", target='void bar()'.
          PATH:  libclang parses both headers -> IR Function objects -> diff_headers builds maps
                 -> bar missing from baseline_map -> appended as function_added ->
                 diff_to_json serializes the entry including 'target' key
          CHECK: Full entry dict equality; 'baseline' key absent from entry dict.
          MUTATION:
            - assert entry['kind'] == 'function_added': fails if kind is 'function_changed'
            - assert entry['severity'] == 'non_breaking': fails if severity mapped wrongly
            - assert entry['name'] == 'bar': fails if name is wrong or None
            - assert entry['detail'] == "function 'bar' added": fails if detail string changes
            - assert entry['target'] == 'void bar()': fails if IR __str__ changes
            - assert 'baseline' not in entry: fails if a spurious 'baseline' key is emitted
          ESCAPE: An impl that emits kind='function_changed' with severity='non_breaking'
                  would fail the kind assertion.
        """
        parsed = parse_diff_json(backend, "void foo(void);", "void foo(void);\nvoid bar(void);")

        assert parsed["summary"] == {"total": 1, "breaking": 0, "non_breaking": 1}
        assert len(parsed["entries"]) == 1
        entry = parsed["entries"][0]
        assert entry == {
            "kind": "function_added",
            "severity": "non_breaking",
            "name": "bar",
            "detail": "function 'bar' added",
            "target": "void bar()",
        }

    def test_function_removed(self, backend):
        """Removing a function is reported as a single breaking function_removed entry.

        ESCAPE: test_function_removed
          CLAIM: When baseline has 'bar' but target does not, exactly one entry is produced:
                 kind='function_removed', severity='breaking', name='bar',
                 detail="function 'bar' removed", baseline='void bar()'.
          PATH:  libclang parses both -> diff_headers detects bar in baseline_map but not
                 target_map -> appended as function_removed -> diff_to_json serializes with
                 'baseline' key and no 'target' key
          CHECK: Full entry dict equality; 'target' key absent from entry dict.
          MUTATION:
            - assert entry['severity'] == 'breaking': fails if _REMOVED_SEVERITY is wrong for function
            - assert entry['baseline'] == 'void bar()': fails if IR __str__ format changes
            - assert 'target' not in entry: fails if target key is spuriously emitted for removals
          ESCAPE: An impl returning severity='non_breaking' for removals would fail the severity check.
        """
        parsed = parse_diff_json(backend, "void foo(void);\nvoid bar(void);", "void foo(void);")

        assert parsed["summary"] == {"total": 1, "breaking": 1, "non_breaking": 0}
        assert len(parsed["entries"]) == 1
        entry = parsed["entries"][0]
        assert entry == {
            "kind": "function_removed",
            "severity": "breaking",
            "name": "bar",
            "detail": "function 'bar' removed",
            "baseline": "void bar()",
        }

    def test_function_signature_changed(self, backend):
        """Changing a function's parameter count is reported as a breaking signature change.

        ESCAPE: test_function_signature_changed
          CLAIM: 'init' going from (int x) to (int x, int y) produces exactly one entry:
                 kind='function_signature_changed', severity='breaking', with detail describing
                 parameter count change, baseline='void init(int x)', target='void init(int x, int y)'.
          PATH:  libclang parses both -> both have 'init' -> _diff_function compares parameter
                 counts: 1 != 2 -> DiffEntry(kind='function_signature_changed', severity='breaking')
          CHECK: Full entry dict equality.
          MUTATION:
            - assert entry['kind'] == 'function_signature_changed': fails if kind is wrong string
            - assert entry['detail'] == '...from 1 to 2': fails if count reporting changes
            - assert entry['baseline'] == 'void init(int x)': fails if IR str() changes
            - assert entry['target'] == 'void init(int x, int y)': same
          ESCAPE: Returning severity='non_breaking' for signature changes would fail the severity check.
        """
        parsed = parse_diff_json(backend, "void init(int x);", "void init(int x, int y);")

        assert parsed["summary"] == {"total": 1, "breaking": 1, "non_breaking": 0}
        assert len(parsed["entries"]) == 1
        entry = parsed["entries"][0]
        assert entry == {
            "kind": "function_signature_changed",
            "severity": "breaking",
            "name": "init",
            "detail": "parameter count changed from 1 to 2",
            "baseline": "void init(int x)",
            "target": "void init(int x, int y)",
        }


# =============================================================================
# TestDiffStructChanges
# =============================================================================


class TestDiffStructChanges:
    """Tests for struct-level API changes detected through the full pipeline."""

    def test_struct_field_added(self, backend):
        """Adding a field at the end of a struct is reported as non_breaking struct_field_added.

        ESCAPE: test_struct_field_added
          CLAIM: Adding field 'y' to the end of struct Pt produces exactly one entry:
                 kind='struct_field_added', severity='non_breaking', name='Pt',
                 detail="field 'y' added", target='int y'.
          PATH:  libclang parses both -> _diff_struct detects 'y' in target_fields but not
                 baseline_fields -> tidx == last index -> severity='non_breaking' ->
                 DiffEntry appended
          CHECK: Full entry dict equality; 'baseline' key absent from entry dict.
          MUTATION:
            - assert entry['severity'] == 'non_breaking': fails if end-of-struct logic broken
            - assert entry['name'] == 'Pt': fails if struct name is lost
            - assert entry['target'] == 'int y': fails if field IR str() changes
            - assert 'baseline' not in entry: fails if baseline key spuriously added for additions
          ESCAPE: An impl that classifies all field additions as 'breaking' would fail the severity check.
        """
        parsed = parse_diff_json(backend, "struct Pt { int x; };", "struct Pt { int x; int y; };")

        assert parsed["summary"] == {"total": 1, "breaking": 0, "non_breaking": 1}
        assert len(parsed["entries"]) == 1
        entry = parsed["entries"][0]
        assert entry == {
            "kind": "struct_field_added",
            "severity": "non_breaking",
            "name": "Pt",
            "detail": "field 'y' added",
            "target": "int y",
        }

    def test_struct_field_removed(self, backend):
        """Removing a field from a struct is reported as a breaking struct_field_removed entry.

        ESCAPE: test_struct_field_removed
          CLAIM: Removing field 'y' from struct Pt produces exactly one entry:
                 kind='struct_field_removed', severity='breaking', name='Pt',
                 detail="field 'y' removed", baseline='int y'.
          PATH:  libclang parses both -> _diff_struct detects 'y' in baseline_fields but not
                 target_fields -> DiffEntry(kind='struct_field_removed', severity='breaking')
          CHECK: Full entry dict equality; 'target' key absent from entry dict.
          MUTATION:
            - assert entry['severity'] == 'breaking': fails if field removals classified as non_breaking
            - assert entry['baseline'] == 'int y': fails if IR field str() changes
            - assert 'target' not in entry: fails if target key spuriously added for removals
          ESCAPE: An impl that emits severity='non_breaking' for struct_field_removed would
                  fail the severity assertion.
        """
        parsed = parse_diff_json(backend, "struct Pt { int x; int y; };", "struct Pt { int x; };")

        assert parsed["summary"] == {"total": 1, "breaking": 1, "non_breaking": 0}
        assert len(parsed["entries"]) == 1
        entry = parsed["entries"][0]
        assert entry == {
            "kind": "struct_field_removed",
            "severity": "breaking",
            "name": "Pt",
            "detail": "field 'y' removed",
            "baseline": "int y",
        }


# =============================================================================
# TestDiffMultipleChanges
# =============================================================================


class TestDiffMultipleChanges:
    """Tests for headers with multiple simultaneous API changes."""

    def test_multi_change(self, backend):
        """Three simultaneous changes are all reported: function removed, function added, field added.

        ESCAPE: test_multi_change
          CLAIM: Replacing 'old_func' with 'new_func' and adding field 'y' to struct Pt
                 produces exactly 3 entries: 1 breaking (old_func removed), 2 non_breaking
                 (new_func added, Pt.y added).
          PATH:  libclang parses both -> diff_headers runs through removed/added/changed loops ->
                 old_func in baseline_map only -> function_removed; new_func in target_map only ->
                 function_added; Pt in both maps -> _diff_struct detects y added -> 3 entries total
          CHECK: summary counts; exact entry dicts sorted by kind then name.
          MUTATION:
            - assert summary.total == 3: fails if any of the 3 changes is missed
            - assert summary.breaking == 1: fails if function_removed wrongly counted as non_breaking
            - assert summary.non_breaking == 2: fails if either addition misclassified
            - entry dict assertions: each fails if detail or severity is wrong
          ESCAPE: An impl that misses the struct diff entirely would produce total==2,
                  caught by the summary assertion.
        """
        baseline_code = "void old_func(void); struct Pt { int x; };"
        target_code = "void new_func(void); struct Pt { int x; int y; };"
        parsed = parse_diff_json(backend, baseline_code, target_code)

        assert parsed["summary"] == {"total": 3, "breaking": 1, "non_breaking": 2}
        assert len(parsed["entries"]) == 3

        # Sort entries for deterministic comparison (order may vary by dict insertion order)
        entries_by_kind = {e["kind"]: e for e in parsed["entries"]}

        assert entries_by_kind["function_removed"] == {
            "kind": "function_removed",
            "severity": "breaking",
            "name": "old_func",
            "detail": "function 'old_func' removed",
            "baseline": "void old_func()",
        }
        assert entries_by_kind["function_added"] == {
            "kind": "function_added",
            "severity": "non_breaking",
            "name": "new_func",
            "detail": "function 'new_func' added",
            "target": "void new_func()",
        }
        assert entries_by_kind["struct_field_added"] == {
            "kind": "struct_field_added",
            "severity": "non_breaking",
            "name": "Pt",
            "detail": "field 'y' added",
            "target": "int y",
        }


# =============================================================================
# TestDiffMarkdown
# =============================================================================


class TestDiffMarkdown:
    """Tests for the markdown format of the diff writer."""

    def test_markdown_identical_headers(self, backend):
        """Identical headers produce a no-changes Markdown report with the correct exact content.

        ESCAPE: test_markdown_identical_headers
          CLAIM: When baseline == target, diff_to_markdown emits a specific exact string with
                 the path header, summary table (all zeros), and 'No changes detected.' paragraph.
          PATH:  diff_headers -> DiffReport(entries=[]) -> diff_to_markdown builds lines list ->
                 no breaking/non_breaking sections appended -> 'No changes detected.' added
          CHECK: Exact string equality with the known output.
          MUTATION:
            - Full string equality: fails if any character in the template changes, e.g.
              the separator line '|----------|-------|' becomes '|--|--|'
            - Fails if 'No changes detected.' text changes
          ESCAPE: An impl that emits '## Breaking Changes' even with 0 entries would
                  produce a different string, caught by ==.
        """
        code = "void foo(void);"
        b = backend.parse(code, "baseline.h")
        t = backend.parse(code, "target.h")
        from headerkit.writers.diff import diff_headers, diff_to_markdown

        report = diff_headers(b, t)
        md = diff_to_markdown(report)

        expected = textwrap.dedent("""\
            # API Diff: baseline.h -> target.h

            ## Summary

            | Category | Count |
            |----------|-------|
            | Breaking | 0 |
            | Non-breaking | 0 |
            | Total | 0 |

            No changes detected.
        """)
        assert md == expected

    def test_markdown_function_added(self, backend):
        """Adding a function produces correct Markdown with non-breaking section.

        ESCAPE: test_markdown_function_added
          CLAIM: baseline='void foo(void);', target adds bar -> exact Markdown string with
                 the path header, summary table (1 non-breaking), and Non-Breaking Changes section
                 with function_added subsection containing '- **bar**: function 'bar' added'
                 and '  - Target: `void bar()`'.
          PATH:  diff_headers produces 1 non_breaking entry -> diff_to_markdown appends
                 '## Non-Breaking Changes' section grouped by kind
          CHECK: Exact string equality with the known output.
          MUTATION:
            - Full string equality: fails if bold formatting (**bar**) changes
            - Fails if Target/Baseline labels change case
            - Fails if the section header 'function_added' is renamed
          ESCAPE: An impl that omits the Target line for additions would produce a different
                  string, caught by ==.
        """
        b = backend.parse("void foo(void);", "baseline.h")
        t = backend.parse("void foo(void);\nvoid bar(void);", "target.h")
        from headerkit.writers.diff import diff_headers, diff_to_markdown

        report = diff_headers(b, t)
        md = diff_to_markdown(report)

        expected = textwrap.dedent("""\
            # API Diff: baseline.h -> target.h

            ## Summary

            | Category | Count |
            |----------|-------|
            | Breaking | 0 |
            | Non-breaking | 1 |
            | Total | 1 |

            ## Non-Breaking Changes

            ### function_added

            - **bar**: function 'bar' added
              - Target: `void bar()`
        """)
        assert md == expected

    def test_markdown_function_removed(self, backend):
        """Removing a function produces correct Markdown with breaking section.

        ESCAPE: test_markdown_function_removed
          CLAIM: baseline has bar, target does not -> exact Markdown with Breaking Changes section
                 containing function_removed subsection with baseline line.
          PATH:  diff_headers produces 1 breaking entry -> diff_to_markdown appends
                 '## Breaking Changes' section
          CHECK: Exact string equality.
          MUTATION:
            - Full string equality: fails if '## Breaking Changes' label changes
            - Fails if Baseline line is omitted for removals
          ESCAPE: Swapping 'Breaking' and 'Non-breaking' section ordering would produce
                  a different string, caught by ==.
        """
        b = backend.parse("void foo(void);\nvoid bar(void);", "baseline.h")
        t = backend.parse("void foo(void);", "target.h")
        from headerkit.writers.diff import diff_headers, diff_to_markdown

        report = diff_headers(b, t)
        md = diff_to_markdown(report)

        expected = textwrap.dedent("""\
            # API Diff: baseline.h -> target.h

            ## Summary

            | Category | Count |
            |----------|-------|
            | Breaking | 1 |
            | Non-breaking | 0 |
            | Total | 1 |

            ## Breaking Changes

            ### function_removed

            - **bar**: function 'bar' removed
              - Baseline: `void bar()`
        """)
        assert md == expected


# =============================================================================
# TestDiffWriterClass
# =============================================================================


class TestDiffWriterClass:
    """Tests for the DiffWriter class used via the full pipeline."""

    def test_writer_json_default(self, backend):
        """DiffWriter with format='json' produces correct JSON for a function addition.

        ESCAPE: test_writer_json_default
          CLAIM: DiffWriter(baseline=b, format='json').write(t) returns valid JSON with
                 the expected summary and single entry for bar added.
          PATH:  DiffWriter.write -> diff_headers(self._baseline, header) -> diff_to_json
          CHECK: Full parsed dict equality on summary and entry.
          MUTATION:
            - assert parsed['summary'] == ...: fails if DiffWriter silently uses empty baseline
            - Entry dict equality: fails if DiffWriter passes wrong header as baseline vs target
          ESCAPE: An impl where write() swaps baseline and target would produce 'bar' as
                  function_removed instead of function_added, caught by entry kind assertion.
        """
        b = backend.parse("void foo(void);", "baseline.h")
        t = backend.parse("void foo(void);\nvoid bar(void);", "target.h")

        writer = DiffWriter(baseline=b, format="json")
        output = writer.write(t)
        parsed = json.loads(output)

        assert parsed["summary"] == {"total": 1, "breaking": 0, "non_breaking": 1}
        assert len(parsed["entries"]) == 1
        assert parsed["entries"][0] == {
            "kind": "function_added",
            "severity": "non_breaking",
            "name": "bar",
            "detail": "function 'bar' added",
            "target": "void bar()",
        }

    def test_writer_markdown(self, backend):
        """DiffWriter with format='markdown' produces the exact Markdown string for function addition.

        ESCAPE: test_writer_markdown
          CLAIM: DiffWriter(baseline=b, format='markdown').write(t) returns the same exact
                 Markdown string as diff_to_markdown(diff_headers(b, t)).
          PATH:  DiffWriter.write -> diff_headers -> diff_to_markdown (format branch)
          CHECK: Exact string equality with independently constructed expected string.
          MUTATION:
            - Exact string equality: fails if format='markdown' branch falls through to JSON
          ESCAPE: An impl that always uses JSON regardless of format= param would produce
                  a string starting with '{', failing the == check.
        """
        b = backend.parse("void foo(void);", "baseline.h")
        t = backend.parse("void foo(void);\nvoid bar(void);", "target.h")

        writer = DiffWriter(baseline=b, format="markdown")
        md = writer.write(t)

        expected = textwrap.dedent("""\
            # API Diff: baseline.h -> target.h

            ## Summary

            | Category | Count |
            |----------|-------|
            | Breaking | 0 |
            | Non-breaking | 1 |
            | Total | 1 |

            ## Non-Breaking Changes

            ### function_added

            - **bar**: function 'bar' added
              - Target: `void bar()`
        """)
        assert md == expected

    def test_writer_no_baseline_treats_all_as_additions(self, backend):
        """DiffWriter with baseline=None treats all target declarations as non_breaking additions.

        ESCAPE: test_writer_no_baseline_treats_all_as_additions
          CLAIM: When no baseline is provided, DiffWriter uses an empty Header as baseline,
                 producing 2 non_breaking entries for foo and S.
          PATH:  DiffWriter.write -> self._baseline is None -> Header(path='(empty)') used ->
                 diff_headers -> all target decls appear as additions
          CHECK: summary.breaking==0, summary.non_breaking==2, summary.total==2.
          MUTATION:
            - assert summary.breaking == 0: fails if None baseline causes exception/error path
            - assert summary.non_breaking == 2: fails if only one declaration parsed
            - assert summary.total == 2: fails if None is passed directly causing AttributeError
          ESCAPE: An impl that raises when baseline=None would fail with an exception, not pass.
        """
        t = backend.parse("void foo(void);\nstruct S { int x; };", "target.h")
        writer = DiffWriter(baseline=None, format="json")
        output = writer.write(t)
        parsed = json.loads(output)

        assert parsed["summary"] == {"total": 2, "breaking": 0, "non_breaking": 2}
        kinds = {e["kind"] for e in parsed["entries"]}
        assert kinds == {"function_added", "struct_added"}
