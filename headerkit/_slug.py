"""Slug construction for cache directory names.

Human-readable directory names encoding cache key components so
developers can browse .hkcache/ and understand what each entry is.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TypedDict

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


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

logger = logging.getLogger("headerkit.cache")


class IndexEntry(TypedDict):
    """A single entry in the cache index."""

    cache_key: str
    created: str  # ISO 8601 timestamp


class CacheIndex(TypedDict):
    """Top-level structure of index.json."""

    version: int  # always 1
    entries: dict[str, IndexEntry]


def load_index(index_path: Path) -> CacheIndex:
    """Load index.json. Returns empty index if file missing or corrupt."""
    if not index_path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "entries" in data:
            return {"version": data.get("version", 1), "entries": data["entries"]}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Corrupt index.json in %s, rebuilding from metadata: %s",
            index_path,
            exc,
        )
        rebuilt = rebuild_index(index_path.parent)
        with contextlib.suppress(OSError):
            save_index(index_path, rebuilt)
        return rebuilt
    return {"version": 1, "entries": {}}


def save_index(index_path: Path, index: CacheIndex) -> None:
    """Atomically write index.json."""
    tmp_path = index_path.with_suffix(".json.tmp")
    content = json.dumps(dict(index), indent=2, sort_keys=True)
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(str(tmp_path), str(index_path))


def lookup_slug(index: CacheIndex, cache_key: str) -> str | None:
    """Find the slug for a given cache key, or None if not indexed."""
    for slug, entry in index["entries"].items():
        if entry["cache_key"] == cache_key:
            return slug
    return None


def register_slug(
    index: CacheIndex,
    slug: str,
    cache_key: str,
) -> str:
    """Register a slug for a cache key, handling collisions.

    If the slug is already taken by a different cache_key, appends
    -2, -3, etc. until a free slot is found.

    :returns: The actual slug used (may have numeric suffix).
    """
    # Check if this exact key already has a slug
    existing = lookup_slug(index, cache_key)
    if existing is not None:
        return existing

    now = datetime.now(timezone.utc).isoformat()
    candidate = slug
    if candidate not in index["entries"]:
        index["entries"][candidate] = {"cache_key": cache_key, "created": now}
        return candidate

    # Collision: try suffixes
    n = 2
    while True:
        candidate = f"{slug}-{n}"
        if candidate not in index["entries"]:
            index["entries"][candidate] = {"cache_key": cache_key, "created": now}
            return candidate
        if index["entries"][candidate]["cache_key"] == cache_key:
            return candidate
        n += 1


def rebuild_index(layer_dir: Path) -> CacheIndex:
    """Rebuild index.json from on-disk metadata.json files.

    :param layer_dir: e.g., .hkcache/ir/ or .hkcache/output/cffi/
    :returns: Rebuilt CacheIndex.
    """
    index: CacheIndex = {"version": 1, "entries": {}}
    if not layer_dir.is_dir():
        return index
    for entry_dir in sorted(layer_dir.iterdir()):
        if not entry_dir.is_dir():
            continue
        metadata_path = entry_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            ck = meta.get("cache_key", "")
            created = meta.get("created", "")
            if ck:
                index["entries"][entry_dir.name] = {
                    "cache_key": ck,
                    "created": created,
                }
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping corrupt metadata in %s: %s", entry_dir, exc)
    return index
