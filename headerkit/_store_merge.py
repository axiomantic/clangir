"""Merge multiple headerkit store directories into one.

Copies entry subdirectories and merges ``index.json`` files so that
platform-specific cache entries collected from CI (e.g., via
cibuildwheel) can be combined into a single ``.headerkit/`` directory.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from headerkit._slug import IndexEntry, load_index, save_index

logger = logging.getLogger("headerkit.store")


@dataclass
class MergeResult:
    """Result of a store merge operation.

    :param new_entries: Number of entry directories copied to the target.
    :param skipped_entries: Number of entries skipped (same slug and cache_key).
    :param overwritten_entries: Number of entries overwritten (same slug, different cache_key).
    :param errors: List of error messages for entries that failed to copy.
    """

    new_entries: int = 0
    skipped_entries: int = 0
    overwritten_entries: int = 0
    errors: list[str] = field(default_factory=list)


def store_merge(
    *,
    sources: list[str | Path],
    target: str | Path,
) -> MergeResult:
    """Merge one or more source store directories into a target store.

    For each source, finds all ``ir/`` and ``output/*/`` layers and:
    - Copies entry subdirectories (slug dirs) into the corresponding
      target layer, skipping entries whose slug already exists with the
      same ``cache_key``.
    - Merges ``index.json`` by combining ``entries`` dicts (later sources
      win on conflict).

    :param sources: Source store directory paths (e.g., ``["/tmp/store-linux"]``).
    :param target: Target store directory path (e.g., ``".headerkit/"``).
    :returns: A :class:`MergeResult` summarizing what was merged.
    :raises FileNotFoundError: If a source directory does not exist.
    """
    target_path = Path(target)
    result = MergeResult()

    for source in sources:
        source_path = Path(source)
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source store directory not found: {source_path}")

        # Merge ir/ layer
        src_ir = source_path / "ir"
        if src_ir.is_dir():
            _merge_layer(
                src_layer=src_ir,
                dst_layer=target_path / "ir",
                result=result,
            )

        # Merge output/*/ layers
        src_output = source_path / "output"
        if src_output.is_dir():
            for writer_dir in sorted(src_output.iterdir()):
                if writer_dir.is_dir():
                    _merge_layer(
                        src_layer=writer_dir,
                        dst_layer=target_path / "output" / writer_dir.name,
                        result=result,
                    )

    return result


def _merge_layer(
    *,
    src_layer: Path,
    dst_layer: Path,
    result: MergeResult,
) -> None:
    """Merge a single layer (ir/ or output/<writer>/) from source to target.

    Copies slug directories and merges index.json.
    """
    dst_layer.mkdir(parents=True, exist_ok=True)

    # Load indexes
    src_index = load_index(src_layer / "index.json")
    dst_index = load_index(dst_layer / "index.json")

    # Iterate over source entry directories
    for entry_dir in sorted(src_layer.iterdir()):
        if not entry_dir.is_dir():
            continue
        # Skip non-entry directories (index.json lives at layer root)
        slug = entry_dir.name

        # Check if this slug has an index entry
        src_entry = src_index["entries"].get(slug)
        if src_entry is None:
            # Entry dir without index entry; check for metadata.json
            meta_path = entry_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                src_cache_key = meta.get("cache_key", "")
            except (json.JSONDecodeError, OSError):
                logger.warning("Skipping entry with corrupt metadata: %s", entry_dir)
                result.errors.append(f"Corrupt metadata: {entry_dir}")
                continue
        else:
            src_cache_key = src_entry["cache_key"]

        dst_entry_dir = dst_layer / slug

        # Check if target already has this slug
        dst_entry = dst_index["entries"].get(slug)
        if dst_entry is not None:
            if dst_entry["cache_key"] == src_cache_key:
                # Same slug, same cache_key: skip
                result.skipped_entries += 1
                logger.debug("Skipping duplicate entry: %s", slug)
                continue
            else:
                # Same slug, different cache_key: overwrite
                result.overwritten_entries += 1
                logger.info(
                    "Overwriting entry %s (cache_key %s -> %s)",
                    slug,
                    dst_entry["cache_key"],
                    src_cache_key,
                )
                if dst_entry_dir.is_dir():
                    shutil.rmtree(dst_entry_dir)
        else:
            result.new_entries += 1
            logger.debug("Copying new entry: %s", slug)

        # Copy entry directory
        try:
            shutil.copytree(str(entry_dir), str(dst_entry_dir))
        except OSError as exc:
            result.errors.append(f"Failed to copy {entry_dir} -> {dst_entry_dir}: {exc}")
            logger.warning("Failed to copy entry %s: %s", slug, exc)
            continue

        # Update destination index with source entry
        if src_entry is not None:
            dst_index["entries"][slug] = src_entry
        else:
            # Reconstruct entry from metadata
            dst_index["entries"][slug] = _index_entry_from_metadata(entry_dir)

    # Save merged index
    save_index(dst_layer / "index.json", dst_index)


def _index_entry_from_metadata(entry_dir: Path) -> IndexEntry:
    """Build an index entry dict from a metadata.json file."""
    meta_path = entry_dir / "metadata.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return IndexEntry(
            cache_key=meta.get("cache_key", ""),
            created=meta.get("created", ""),
        )
    except (json.JSONDecodeError, OSError):
        return IndexEntry(cache_key="", created="")
