"""Header path resolution and output path template expansion."""

from __future__ import annotations

from pathlib import Path


def resolve_headers(
    patterns: list[str],
    exclude_patterns: list[str],
    project_root: Path,
) -> tuple[list[Path], dict[Path, list[str]]]:
    """Resolve header file patterns into concrete paths.

    For each pattern: if it contains glob metacharacters (``*``, ``?``, ``[``),
    use ``project_root.glob(pattern)``; otherwise treat as a literal path
    relative to *project_root*.

    :param patterns: Glob patterns or literal paths relative to project_root.
    :param exclude_patterns: Glob patterns for paths to exclude.
    :param project_root: Project root directory.
    :returns: Tuple of (sorted paths, mapping of path -> list of matching patterns).
    :raises ValueError: If no paths remain after resolution and exclusion.
    """
    matched: set[Path] = set()
    pattern_mapping: dict[Path, list[str]] = {}

    for pattern in patterns:
        if any(ch in pattern for ch in ("*", "?", "[")):
            hits = list(project_root.glob(pattern))
        else:
            literal = project_root / pattern
            try:
                literal.resolve().relative_to(project_root.resolve())
            except ValueError:
                raise ValueError(f"Header path {pattern} is outside project root {project_root}") from None
            hits = [literal]

        for path in hits:
            matched.add(path)
            pattern_mapping.setdefault(path, []).append(pattern)

    # Apply exclusions
    for exclude in exclude_patterns:
        excluded = set(project_root.glob(exclude))
        matched -= excluded
        for path in excluded:
            pattern_mapping.pop(path, None)

    if not matched:
        raise ValueError(f"No headers matched patterns: {patterns!r} (excludes: {exclude_patterns!r})")

    sorted_paths = sorted(matched)
    return sorted_paths, pattern_mapping


def resolve_output_path(
    template: str,
    header_path: Path,
    project_root: Path,
) -> Path:
    """Resolve an output path template for a header file.

    :param template: Path template with {stem}, {name}, {dir} variables.
    :param header_path: Absolute path to the header file.
    :param project_root: Project root directory.
    :returns: Resolved output path relative to project_root.
    :raises ValueError: If template contains unknown variables.
    """
    rel = header_path.absolute().relative_to(project_root.absolute())
    stem = header_path.stem
    name = header_path.name
    dir_part = str(rel.parent)
    if dir_part == ".":
        dir_part = "."
    # Use POSIX separators for cross-platform consistency
    dir_part = dir_part.replace("\\", "/")

    try:
        result = template.format(stem=stem, name=name, dir=dir_part)
    except KeyError as exc:
        raise ValueError(f"Unknown template variable in {template!r}: {exc}") from exc

    return Path(result)


def check_output_collisions(
    resolved_paths: dict[tuple[Path, str], Path],
) -> None:
    """Check for output path collisions across all header/writer combinations.

    :param resolved_paths: Map of (header_path, writer_name) -> resolved output path.
    :raises ValueError: If two or more header/writer combos would write to the same file.
    """
    seen: dict[Path, tuple[Path, str]] = {}
    for (header, writer), output in resolved_paths.items():
        canonical = output.resolve()
        if canonical in seen:
            prev_header, prev_writer = seen[canonical]
            raise ValueError(
                f"Output collision: {header} ({writer}) and {prev_header} ({prev_writer}) both resolve to {output}"
            )
        seen[canonical] = (header, writer)
