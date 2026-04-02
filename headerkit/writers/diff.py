"""Compare two Header objects and produce API compatibility reports.

Computes a structural diff between a baseline and target header,
classifying each change as breaking or non-breaking, and renders
the result as JSON or Markdown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from headerkit.ir import (
    Constant,
    Declaration,
    Enum,
    Function,
    Header,
    Struct,
    Typedef,
    Variable,
)

# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class DiffEntry:
    """A single difference between baseline and target headers.

    :param kind: Category of change (e.g., ``"function_added"``,
        ``"struct_field_removed"``).
    :param severity: Either ``"breaking"`` or ``"non_breaking"``.
    :param name: Affected declaration name.
    :param detail: Human-readable description of the change.
    :param baseline: Baseline signature or value, if applicable.
    :param target: Target signature or value, if applicable.
    """

    kind: str
    severity: str
    name: str
    detail: str
    baseline: str | None = None
    target: str | None = None


@dataclass
class DiffReport:
    """Complete diff report between two headers.

    :param baseline_path: Path of the baseline header file.
    :param target_path: Path of the target header file.
    :param entries: List of detected differences.
    """

    baseline_path: str
    target_path: str
    entries: list[DiffEntry] = field(default_factory=list)

    @property
    def breaking_count(self) -> int:
        """Number of breaking changes."""
        return sum(1 for e in self.entries if e.severity == "breaking")

    @property
    def non_breaking_count(self) -> int:
        """Number of non-breaking changes."""
        return sum(1 for e in self.entries if e.severity == "non_breaking")


# =============================================================================
# Declaration key helpers
# =============================================================================

# Map IR declaration types to string kind labels used as keys.
_DECL_KIND_MAP: dict[type[Declaration], str] = {
    Function: "function",
    Struct: "struct",
    Enum: "enum",
    Typedef: "typedef",
    Variable: "variable",
    Constant: "constant",
}


def _decl_kind(decl: Declaration) -> str:
    """Return a short string label for a declaration type."""
    for cls, label in _DECL_KIND_MAP.items():
        if isinstance(decl, cls):
            return label
    return "unknown"  # pragma: no cover


def _decl_name(decl: Declaration) -> str | None:
    """Return the name of a declaration, or None for anonymous types."""
    if isinstance(decl, Function | Typedef | Variable | Constant):
        return decl.name
    if isinstance(decl, Struct | Enum):
        return decl.name
    return None  # pragma: no cover


def _decl_key(decl: Declaration) -> tuple[str, str | None]:
    """Return a (kind, name) tuple used for matching declarations."""
    return (_decl_kind(decl), _decl_name(decl))


# =============================================================================
# Per-type change detection
# =============================================================================


def _diff_function(name: str, baseline: Function, target: Function) -> list[DiffEntry]:
    """Compare two Function declarations."""
    entries: list[DiffEntry] = []

    # Return type
    if baseline.return_type != target.return_type:
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=f"return type changed from '{baseline.return_type}' to '{target.return_type}'",
                baseline=str(baseline.return_type),
                target=str(target.return_type),
            )
        )

    # Parameter count
    if len(baseline.parameters) != len(target.parameters):
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=(f"parameter count changed from {len(baseline.parameters)} to {len(target.parameters)}"),
                baseline=str(baseline),
                target=str(target),
            )
        )
    else:
        # Compare each parameter
        for i, (bp, tp) in enumerate(zip(baseline.parameters, target.parameters, strict=True)):
            if bp.type != tp.type:
                entries.append(
                    DiffEntry(
                        kind="function_signature_changed",
                        severity="breaking",
                        name=name,
                        detail=(f"parameter {i} type changed from '{bp.type}' to '{tp.type}'"),
                        baseline=str(bp.type),
                        target=str(tp.type),
                    )
                )
            if bp.name != tp.name:
                entries.append(
                    DiffEntry(
                        kind="function_parameter_renamed",
                        severity="non_breaking",
                        name=name,
                        detail=(f"parameter {i} renamed from '{bp.name}' to '{tp.name}'"),
                        baseline=bp.name,
                        target=tp.name,
                    )
                )

    # Variadic
    if baseline.is_variadic != target.is_variadic:
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=(f"variadic changed from {baseline.is_variadic} to {target.is_variadic}"),
                baseline=str(baseline),
                target=str(target),
            )
        )

    # Calling convention
    if baseline.calling_convention != target.calling_convention:
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=(
                    f"calling convention changed from '{baseline.calling_convention}' to '{target.calling_convention}'"
                ),
                baseline=str(baseline.calling_convention),
                target=str(target.calling_convention),
            )
        )

    return entries


def _diff_struct(name: str, baseline: Struct, target: Struct) -> list[DiffEntry]:
    """Compare two Struct declarations."""
    entries: list[DiffEntry] = []
    display_name = name or "(anonymous)"

    baseline_fields = {f.name: (i, f) for i, f in enumerate(baseline.fields)}
    target_fields = {f.name: (i, f) for i, f in enumerate(target.fields)}

    # Removed fields
    for fname in baseline_fields:
        if fname not in target_fields:
            entries.append(
                DiffEntry(
                    kind="struct_field_removed",
                    severity="breaking",
                    name=display_name,
                    detail=f"field '{fname}' removed",
                    baseline=str(baseline_fields[fname][1]),
                )
            )

    # Added fields
    for fname in target_fields:
        if fname not in baseline_fields:
            # Added at the end is non-breaking; added in the middle is breaking
            tidx = target_fields[fname][0]
            if tidx == len(target.fields) - 1 or all(
                tn not in baseline_fields for tn in list(target_fields.keys())[tidx:]
            ):
                severity = "non_breaking"
            else:
                # Check if there are existing fields after this one
                has_existing_after = any(tn in baseline_fields for tn in list(target_fields.keys())[tidx + 1 :])
                severity = "breaking" if has_existing_after else "non_breaking"
            entries.append(
                DiffEntry(
                    kind="struct_field_added",
                    severity=severity,
                    name=display_name,
                    detail=f"field '{fname}' added",
                    target=str(target_fields[fname][1]),
                )
            )

    # Changed fields (present in both)
    for fname in baseline_fields:
        if fname in target_fields:
            bidx, bf = baseline_fields[fname]
            tidx, tf = target_fields[fname]

            if bf.type != tf.type:
                entries.append(
                    DiffEntry(
                        kind="struct_field_type_changed",
                        severity="breaking",
                        name=display_name,
                        detail=(f"field '{fname}' type changed from '{bf.type}' to '{tf.type}'"),
                        baseline=str(bf.type),
                        target=str(tf.type),
                    )
                )

            if bidx != tidx:
                entries.append(
                    DiffEntry(
                        kind="struct_field_reordered",
                        severity="breaking",
                        name=display_name,
                        detail=(f"field '{fname}' moved from index {bidx} to {tidx}"),
                    )
                )

    # is_packed changed
    if baseline.is_packed != target.is_packed:
        entries.append(
            DiffEntry(
                kind="struct_layout_changed",
                severity="breaking",
                name=display_name,
                detail=(f"packed attribute changed from {baseline.is_packed} to {target.is_packed}"),
            )
        )

    # is_union changed
    if baseline.is_union != target.is_union:
        entries.append(
            DiffEntry(
                kind="struct_layout_changed",
                severity="breaking",
                name=display_name,
                detail=(
                    f"kind changed from "
                    f"{'union' if baseline.is_union else 'struct'} to "
                    f"{'union' if target.is_union else 'struct'}"
                ),
            )
        )

    return entries


def _diff_enum(name: str, baseline: Enum, target: Enum) -> list[DiffEntry]:
    """Compare two Enum declarations."""
    entries: list[DiffEntry] = []
    display_name = name or "(anonymous)"

    baseline_vals = {v.name: v for v in baseline.values}
    target_vals = {v.name: v for v in target.values}

    # Removed values
    for vname, bv in baseline_vals.items():
        if vname not in target_vals:
            entries.append(
                DiffEntry(
                    kind="enum_value_removed",
                    severity="breaking",
                    name=display_name,
                    detail=f"enum value '{vname}' removed",
                    baseline=str(bv),
                )
            )

    # Added values
    for vname, tv in target_vals.items():
        if vname not in baseline_vals:
            entries.append(
                DiffEntry(
                    kind="enum_value_added",
                    severity="non_breaking",
                    name=display_name,
                    detail=f"enum value '{vname}' added",
                    target=str(tv),
                )
            )

    # Changed values
    for vname in baseline_vals:
        if vname in target_vals:
            bv = baseline_vals[vname]
            tv = target_vals[vname]
            if bv.value != tv.value:
                entries.append(
                    DiffEntry(
                        kind="enum_value_changed",
                        severity="breaking",
                        name=display_name,
                        detail=(f"enum value '{vname}' changed from {bv.value} to {tv.value}"),
                        baseline=str(bv.value),
                        target=str(tv.value),
                    )
                )

    return entries


def _diff_typedef(name: str, baseline: Typedef, target: Typedef) -> list[DiffEntry]:
    """Compare two Typedef declarations."""
    entries: list[DiffEntry] = []

    if baseline.underlying_type != target.underlying_type:
        entries.append(
            DiffEntry(
                kind="typedef_changed",
                severity="breaking",
                name=name,
                detail=(f"underlying type changed from '{baseline.underlying_type}' to '{target.underlying_type}'"),
                baseline=str(baseline.underlying_type),
                target=str(target.underlying_type),
            )
        )

    return entries


def _diff_variable(name: str, baseline: Variable, target: Variable) -> list[DiffEntry]:
    """Compare two Variable declarations."""
    entries: list[DiffEntry] = []

    if baseline.type != target.type:
        entries.append(
            DiffEntry(
                kind="variable_type_changed",
                severity="breaking",
                name=name,
                detail=(f"type changed from '{baseline.type}' to '{target.type}'"),
                baseline=str(baseline.type),
                target=str(target.type),
            )
        )

    return entries


def _diff_constant(name: str, baseline: Constant, target: Constant) -> list[DiffEntry]:
    """Compare two Constant declarations."""
    entries: list[DiffEntry] = []

    if baseline.value != target.value:
        entries.append(
            DiffEntry(
                kind="constant_changed",
                severity="non_breaking",
                name=name,
                detail=(f"value changed from {baseline.value!r} to {target.value!r}"),
                baseline=repr(baseline.value),
                target=repr(target.value),
            )
        )

    return entries


# =============================================================================
# Main diff computation
# =============================================================================

# Severity for added/removed declarations by kind
_ADDED_SEVERITY: dict[str, str] = {
    "function": "non_breaking",
    "struct": "non_breaking",
    "enum": "non_breaking",
    "typedef": "non_breaking",
    "variable": "non_breaking",
    "constant": "non_breaking",
}

_REMOVED_SEVERITY: dict[str, str] = {
    "function": "breaking",
    "struct": "breaking",
    "enum": "breaking",
    "typedef": "breaking",
    "variable": "breaking",
    "constant": "breaking",
}


def diff_headers(baseline: Header, target: Header) -> DiffReport:
    """Compare two Header objects and produce a DiffReport.

    Declarations are matched by (kind, name). Anonymous declarations
    (name is None) are skipped since they cannot be reliably matched.

    :param baseline: The original/old header.
    :param target: The new/updated header.
    :returns: A DiffReport with all detected changes.
    """
    entries: list[DiffEntry] = []

    # Build maps: (kind, name) -> declaration
    baseline_map: dict[tuple[str, str], Declaration] = {}
    for decl in baseline.declarations:
        key = _decl_key(decl)
        if key[1] is not None:
            baseline_map[(key[0], key[1])] = decl

    target_map: dict[tuple[str, str], Declaration] = {}
    for decl in target.declarations:
        key = _decl_key(decl)
        if key[1] is not None:
            target_map[(key[0], key[1])] = decl

    # Removed declarations
    for key, decl in baseline_map.items():
        if key not in target_map:
            kind_label, decl_name_str = key
            severity = _REMOVED_SEVERITY.get(kind_label, "breaking")
            entries.append(
                DiffEntry(
                    kind=f"{kind_label}_removed",
                    severity=severity,
                    name=decl_name_str,
                    detail=f"{kind_label} '{decl_name_str}' removed",
                    baseline=str(decl),
                )
            )

    # Added declarations
    for key, decl in target_map.items():
        if key not in baseline_map:
            kind_label, decl_name_str = key
            severity = _ADDED_SEVERITY.get(kind_label, "non_breaking")
            entries.append(
                DiffEntry(
                    kind=f"{kind_label}_added",
                    severity=severity,
                    name=decl_name_str,
                    detail=f"{kind_label} '{decl_name_str}' added",
                    target=str(decl),
                )
            )

    # Changed declarations
    for key in baseline_map:
        if key in target_map:
            b_decl = baseline_map[key]
            t_decl = target_map[key]
            name_str = key[1]

            if isinstance(b_decl, Function) and isinstance(t_decl, Function):
                entries.extend(_diff_function(name_str, b_decl, t_decl))
            elif isinstance(b_decl, Struct) and isinstance(t_decl, Struct):
                entries.extend(_diff_struct(name_str, b_decl, t_decl))
            elif isinstance(b_decl, Enum) and isinstance(t_decl, Enum):
                entries.extend(_diff_enum(name_str, b_decl, t_decl))
            elif isinstance(b_decl, Typedef) and isinstance(t_decl, Typedef):
                entries.extend(_diff_typedef(name_str, b_decl, t_decl))
            elif isinstance(b_decl, Variable) and isinstance(t_decl, Variable):
                entries.extend(_diff_variable(name_str, b_decl, t_decl))
            elif isinstance(b_decl, Constant) and isinstance(t_decl, Constant):
                entries.extend(_diff_constant(name_str, b_decl, t_decl))

    return DiffReport(
        baseline_path=baseline.path,
        target_path=target.path,
        entries=entries,
    )


# =============================================================================
# Output formatters
# =============================================================================


def _entry_to_dict(entry: DiffEntry) -> dict[str, Any]:
    """Convert a DiffEntry to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "kind": entry.kind,
        "severity": entry.severity,
        "name": entry.name,
        "detail": entry.detail,
    }
    if entry.baseline is not None:
        d["baseline"] = entry.baseline
    if entry.target is not None:
        d["target"] = entry.target
    return d


def diff_to_json(report: DiffReport, indent: int | None = 2) -> str:
    """Serialize a DiffReport to JSON.

    :param report: The diff report.
    :param indent: JSON indentation level. None for compact output.
    :returns: JSON string.
    """
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "baseline": report.baseline_path,
        "target": report.target_path,
        "summary": {
            "total": len(report.entries),
            "breaking": report.breaking_count,
            "non_breaking": report.non_breaking_count,
        },
        "entries": [_entry_to_dict(e) for e in report.entries],
    }
    return json.dumps(data, indent=indent)


def diff_to_markdown(report: DiffReport) -> str:
    """Render a DiffReport as human-readable Markdown.

    :param report: The diff report.
    :returns: Markdown string.
    """
    lines: list[str] = []
    lines.append(f"# API Diff: {report.baseline_path} -> {report.target_path}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    lines.append(f"| Breaking | {report.breaking_count} |")
    lines.append(f"| Non-breaking | {report.non_breaking_count} |")
    lines.append(f"| Total | {len(report.entries)} |")
    lines.append("")

    breaking = [e for e in report.entries if e.severity == "breaking"]
    non_breaking = [e for e in report.entries if e.severity == "non_breaking"]

    if breaking:
        lines.append("## Breaking Changes")
        lines.append("")
        # Group by kind
        by_kind: dict[str, list[DiffEntry]] = {}
        for entry in breaking:
            by_kind.setdefault(entry.kind, []).append(entry)
        for kind, kind_entries in by_kind.items():
            lines.append(f"### {kind}")
            lines.append("")
            for entry in kind_entries:
                lines.append(f"- **{entry.name}**: {entry.detail}")
                if entry.baseline is not None:
                    lines.append(f"  - Baseline: `{entry.baseline}`")
                if entry.target is not None:
                    lines.append(f"  - Target: `{entry.target}`")
            lines.append("")

    if non_breaking:
        lines.append("## Non-Breaking Changes")
        lines.append("")
        by_kind_nb: dict[str, list[DiffEntry]] = {}
        for entry in non_breaking:
            by_kind_nb.setdefault(entry.kind, []).append(entry)
        for kind, kind_entries in by_kind_nb.items():
            lines.append(f"### {kind}")
            lines.append("")
            for entry in kind_entries:
                lines.append(f"- **{entry.name}**: {entry.detail}")
                if entry.baseline is not None:
                    lines.append(f"  - Baseline: `{entry.baseline}`")
                if entry.target is not None:
                    lines.append(f"  - Target: `{entry.target}`")
            lines.append("")

    if not breaking and not non_breaking:
        lines.append("No changes detected.")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# Writer class
# =============================================================================


class DiffWriter:
    """Writer that compares two headers and produces API compatibility reports.

    Compares a target header against a baseline and produces a structured
    diff report in either JSON or Markdown format.

    Options
    -------
    baseline : Header | None
        The baseline header to compare against. If None, an empty header
        is used (all declarations appear as additions).
    format : str
        Output format: ``"json"`` (default) or ``"markdown"``.

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("diff", baseline=old_header, format="markdown")
        report = writer.write(new_header)
    """

    default_output_pattern: str = "{dir}/{stem}_diff.json"
    cache_output: bool = False

    def __init__(self, baseline: Header | None = None, format: str = "json") -> None:
        self._baseline = baseline
        self._format = format

    def write(self, header: Header) -> str:
        """Compare header against baseline and produce a diff report.

        :param header: The target header to compare.
        :returns: Diff report as JSON or Markdown string.
        """
        if self._baseline is None:
            baseline = Header(path="(empty)", declarations=[])
        else:
            baseline = self._baseline
        report = diff_headers(baseline, header)
        if self._format == "markdown":
            return diff_to_markdown(report)
        return diff_to_json(report)

    @property
    def name(self) -> str:
        """Human-readable name of this writer."""
        return "diff"

    @property
    def format_description(self) -> str:
        """Short description of the output format."""
        return "API compatibility diff reports (JSON or Markdown)"


# Uses bottom-of-module self-registration. See headerkit/writers/json.py
# for explanation of the managed circular import pattern.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "diff",
    DiffWriter,
    description="API compatibility diff reports (JSON or Markdown)",
)
