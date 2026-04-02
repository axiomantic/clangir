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
            hits = [project_root / pattern]

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
    :raises NotImplementedError: Stub, not yet implemented.
    """
    raise NotImplementedError  # implemented in T-10


def check_output_collisions(
    resolved_paths: dict[tuple[Path, str], Path],
) -> None:
    """Check for output path collisions across all header/writer combinations.

    :param resolved_paths: Map of (header_path, writer_name) -> resolved output path.
    :raises NotImplementedError: Stub, not yet implemented.
    """
    raise NotImplementedError  # implemented in T-11
