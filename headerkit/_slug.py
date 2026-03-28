"""Slug construction for cache directory names.

Human-readable directory names encoding cache key components so
developers can browse .hkcache/ and understand what each entry is.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

_MAX_SLUG_LENGTH = 120
_COLLISION_BUDGET = 4  # Reserve for "-NNN" suffix


def build_slug(
    *,
    backend_name: str,
    header_path: str,
    defines: list[str],
    includes: list[str],
    other_args: list[str],
) -> str:
    """Build a human-readable slug for a cache entry.

    :param backend_name: Parser backend name.
    :param header_path: Path to the header file.
    :param defines: Sorted -D values (without the -D prefix).
    :param includes: Sorted -I values (without the -I prefix).
    :param other_args: Sorted remaining extra_args.
    :returns: Slug string suitable for use as a directory name.
    """
    # Backend: lowercased, sanitized
    backend_part = _sanitize(backend_name.lower())

    # Header stem: basename without extension, lowercased, sanitized
    stem = PurePosixPath(header_path).stem
    header_part = _sanitize(stem.lower())

    components = [backend_part, header_part]

    # Build variable groups
    groups: list[tuple[str, list[str]]] = []
    if defines:
        groups.append(("d", sorted(defines)))
    if includes:
        basenames = sorted(PurePosixPath(p).name for p in includes)
        groups.append(("i", basenames))
    if other_args:
        groups.append(("args", sorted(other_args)))

    effective_limit = _MAX_SLUG_LENGTH - _COLLISION_BUDGET

    # First pass: build with full values
    full_group_parts: list[tuple[str, str]] = []
    for prefix, values in groups:
        joined = "_".join(values)
        full_group_parts.append((prefix, joined))

    candidate_parts = list(components)
    for prefix, joined in full_group_parts:
        candidate_parts.append(prefix)
        candidate_parts.append(joined)

    candidate = ".".join(candidate_parts)

    if len(candidate) <= effective_limit:
        return candidate

    # Second pass: hash groups that overflow individually
    group_entries: list[tuple[str, list[str], bool]] = []
    result_parts = list(components)
    for prefix, values in groups:
        joined = "_".join(values)
        test_parts = list(result_parts) + [prefix, joined]
        test_slug = ".".join(test_parts)
        if len(test_slug) > effective_limit:
            result_parts.append(prefix)
            result_parts.append(_hash_group(values))
            group_entries.append((prefix, values, True))
        else:
            result_parts.append(prefix)
            result_parts.append(joined)
            group_entries.append((prefix, values, False))

    # Third pass: if cumulative slug still exceeds the limit,
    # progressively hash the longest unhashed group until it fits.
    slug = ".".join(result_parts)
    while len(slug) > effective_limit:
        # Find the longest unhashed group
        longest_idx = -1
        longest_len = -1
        for i, (_prefix, values, hashed) in enumerate(group_entries):
            if not hashed:
                joined_len = len("_".join(values))
                if joined_len > longest_len:
                    longest_len = joined_len
                    longest_idx = i
        if longest_idx == -1:
            break  # All groups already hashed
        group_entries[longest_idx] = (
            group_entries[longest_idx][0],
            group_entries[longest_idx][1],
            True,
        )
        # Rebuild slug from components and group_entries
        result_parts = list(components)
        for prefix, values, hashed in group_entries:
            result_parts.append(prefix)
            if hashed:
                result_parts.append(_hash_group(values))
            else:
                result_parts.append("_".join(values))
        slug = ".".join(result_parts)

    return slug


def _sanitize(component: str) -> str:
    """Sanitize a single slug component.

    - Replace . with -
    - Replace / and \\ with -
    - Replace spaces with -
    - Collapse consecutive - into one
    - Strip leading/trailing -
    """
    result = component
    result = result.replace(".", "-")
    result = result.replace("/", "-")
    result = result.replace("\\", "-")
    result = result.replace(" ", "-")
    result = re.sub(r"-{2,}", "-", result)
    result = result.strip("-")
    return result


def _hash_group(values: list[str]) -> str:
    """SHA-256 of sorted values, truncated to 8 hex chars."""
    h = hashlib.sha256()
    for v in sorted(values):
        h.update(v.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:8]
