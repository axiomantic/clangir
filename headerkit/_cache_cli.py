"""CLI subcommands for cache operations.

Provides ``cache status``, ``cache clear``, and ``cache rebuild-index``
subcommands, dispatched from the main CLI via early dispatch (before
argparse construction).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from headerkit._slug import rebuild_index, save_index


def _count_entry_dirs(parent: Path) -> int:
    """Count subdirectories containing metadata.json."""
    if not parent.is_dir():
        return 0
    return sum(1 for p in parent.iterdir() if p.is_dir() and (p / "metadata.json").exists())


def cache_status_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache status``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache status",
        description="Show cache statistics.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        required=True,
        metavar="DIR",
        help="Cache directory path",
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(
            f"headerkit cache status: cache directory not found: {cache_dir}",
            file=sys.stderr,
        )
        return 1

    ir_count = _count_entry_dirs(cache_dir / "ir")

    output_dir = cache_dir / "output"
    writer_counts: dict[str, int] = {}
    total_output = 0
    if output_dir.is_dir():
        for writer_dir in sorted(output_dir.iterdir()):
            if writer_dir.is_dir():
                count = _count_entry_dirs(writer_dir)
                if count > 0:
                    writer_counts[writer_dir.name] = count
                    total_output += count

    print(f"Cache directory: {cache_dir}")
    print(f"IR entries: {ir_count}")
    print(f"Output entries: {total_output}")
    for writer_name, count in sorted(writer_counts.items()):
        print(f"  {writer_name}: {count}")

    return 0


def cache_clear_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache clear``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache clear",
        description="Clear cache entries.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        required=True,
        metavar="DIR",
        help="Cache directory path",
    )
    parser.add_argument(
        "--ir",
        dest="ir_only",
        action="store_true",
        default=False,
        help="Clear only IR cache entries",
    )
    parser.add_argument(
        "--output",
        dest="output_only",
        action="store_true",
        default=False,
        help="Clear only output cache entries",
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(
            f"headerkit cache clear: cache directory not found: {cache_dir}",
            file=sys.stderr,
        )
        return 1

    clear_ir = not args.output_only  # Clear IR unless --output only
    clear_output = not args.ir_only  # Clear output unless --ir only

    if clear_ir:
        ir_dir = cache_dir / "ir"
        if ir_dir.is_dir():
            shutil.rmtree(ir_dir)
            ir_dir.mkdir()

    if clear_output:
        output_dir = cache_dir / "output"
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
            output_dir.mkdir()

    if args.ir_only:
        print("Cleared IR cache entries.")
    elif args.output_only:
        print("Cleared output cache entries.")
    else:
        print("Cleared all cache entries.")

    return 0


def cache_rebuild_index_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache rebuild-index``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache rebuild-index",
        description="Rebuild index.json files from metadata.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        required=True,
        metavar="DIR",
        help="Cache directory path",
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(
            f"headerkit cache rebuild-index: cache directory not found: {cache_dir}",
            file=sys.stderr,
        )
        return 1

    # Rebuild IR index
    ir_dir = cache_dir / "ir"
    if ir_dir.is_dir():
        index = rebuild_index(ir_dir)
        index_path = ir_dir / "index.json"
        save_index(index_path, index)
        print(f"Rebuilt index: {index_path} ({len(index['entries'])} entries)")

    # Rebuild output writer indexes
    output_dir = cache_dir / "output"
    if output_dir.is_dir():
        for writer_dir in sorted(output_dir.iterdir()):
            if writer_dir.is_dir():
                index = rebuild_index(writer_dir)
                index_path = writer_dir / "index.json"
                save_index(index_path, index)
                print(f"Rebuilt index: {index_path} ({len(index['entries'])} entries)")

    return 0
