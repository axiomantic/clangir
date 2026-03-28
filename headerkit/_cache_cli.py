"""CLI subcommands for cache operations.

Provides ``cache status``, ``cache clear``, ``cache rebuild-index``, and
``cache populate`` subcommands, dispatched from the main CLI via early
dispatch (before argparse construction).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from headerkit._slug import rebuild_index, save_index

if TYPE_CHECKING:
    from headerkit._populate import PopulateResult


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


def cache_populate_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache populate``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache populate",
        description="Generate cache entries for target platforms using Docker.",
    )
    parser.add_argument(
        "input_files",
        nargs="+",
        metavar="FILE",
        help="Header file path(s)",
    )
    parser.add_argument(
        "-w",
        "--writer",
        dest="writers",
        action="append",
        default=[],
        metavar="WRITER",
        help="Writer(s) to generate output for (default: json)",
    )
    parser.add_argument(
        "--platform",
        dest="platforms",
        action="append",
        default=[],
        metavar="PLATFORM",
        help="Docker platform (e.g., linux/amd64)",
    )
    parser.add_argument(
        "--python",
        dest="python_versions",
        action="append",
        default=[],
        metavar="VERSION",
        help="Python version to target (e.g., 3.12)",
    )
    parser.add_argument(
        "--cibuildwheel",
        dest="cibuildwheel",
        action="store_true",
        default=False,
        help="Auto-detect targets from pyproject.toml cibuildwheel config",
    )
    parser.add_argument(
        "--docker-image",
        dest="docker_image",
        default=None,
        metavar="IMAGE",
        help="Docker image override for all platforms",
    )
    parser.add_argument(
        "--headerkit-version",
        dest="headerkit_version",
        default=None,
        metavar="VERSION",
        help=("Install this headerkit version in container instead of mounting source"),
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        default=None,
        metavar="DIR",
        help="Cache directory path (default: .hkcache/)",
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        default=False,
        help="Overwrite existing cache entries",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Show planned operations without executing",
    )
    parser.add_argument(
        "--timeout",
        dest="timeout",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Timeout per container in seconds (default: 300)",
    )
    parser.add_argument(
        "-I",
        "--include-dir",
        dest="include_dirs",
        action="append",
        default=[],
        metavar="DIR",
        help="Include directory",
    )
    parser.add_argument(
        "-D",
        dest="defines",
        action="append",
        default=[],
        metavar="DEFINE",
        help="Preprocessor define",
    )
    parser.add_argument(
        "--backend-arg",
        dest="backend_args",
        action="append",
        default=[],
        metavar="ARG",
        help="Extra backend argument",
    )
    parser.add_argument(
        "--writer-opt",
        dest="writer_opts_raw",
        action="append",
        default=[],
        metavar="WRITER:KEY=VALUE",
        help="Writer option",
    )
    parser.add_argument(
        "--backend",
        dest="backend",
        default="libclang",
        metavar="NAME",
        help="Backend name (default: libclang)",
    )
    args = parser.parse_args(argv)

    # Parse writer options into dict
    writer_options: dict[str, dict[str, object]] | None = None
    if args.writer_opts_raw:
        writer_options = {}
        for item in args.writer_opts_raw:
            colon_pos = item.find(":")
            if colon_pos == -1:
                print(
                    f"headerkit cache populate: malformed --writer-opt: {item!r}; use WRITER:KEY=VALUE format",
                    file=sys.stderr,
                )
                return 1
            writer_name = item[:colon_pos]
            rest = item[colon_pos + 1 :]
            eq_pos = rest.find("=")
            if eq_pos == -1:
                print(
                    f"headerkit cache populate: malformed --writer-opt: {item!r}; expected WRITER:KEY=VALUE",
                    file=sys.stderr,
                )
                return 1
            key = rest[:eq_pos]
            value = rest[eq_pos + 1 :]
            if writer_name not in writer_options:
                writer_options[writer_name] = {}
            writer_options[writer_name][key] = value

    from headerkit._populate import populate

    try:
        result: PopulateResult = populate(
            header_paths=args.input_files,
            writers=args.writers or None,
            platforms=args.platforms or None,
            python_versions=args.python_versions or None,
            from_cibuildwheel=args.cibuildwheel,
            docker_image=args.docker_image,
            headerkit_version=args.headerkit_version,
            include_dirs=args.include_dirs or None,
            defines=args.defines or None,
            backend_args=args.backend_args or None,
            backend_name=args.backend,
            writer_options=writer_options,
            cache_dir=args.cache_dir,
            force=args.force,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Print warnings
    for w in result.warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    if args.dry_run:
        _print_dry_run(result)
        return 0

    _print_results(result)
    return 1 if result.failed > 0 else 0


def _print_dry_run(result: PopulateResult) -> None:
    """Print dry-run output showing planned targets."""
    print("\nPlanned cache population (dry run):\n")
    for i, target in enumerate(result.planned, 1):
        print(f"  Target {i}: {target.docker_platform} + {target.py_impl}")
        print(f"    Image: {target.docker_image}")
        print(f"    Python: {target.python_path}")
        print(f"    Cache key fields: platform={target.sys_platform}, arch={target.machine}, python={target.py_impl}")
        print()

    total = len(result.planned)
    print(f"Total: {total} targets")
    print("\nDry run complete. Run without --dry-run to populate.")


def _print_results(result: PopulateResult) -> None:
    """Print execution results."""
    print("\nCache population complete.")
    print(f"  Succeeded: {result.succeeded}")
    print(f"  Failed: {result.failed}")
    print(f"  Skipped: {result.skipped_count}")
    print(f"  Total: {result.total}")

    if result.failed > 0:
        print("\nFailures:")
        for entry in result.entries:
            if not entry.success and not entry.skipped:
                print(f"  {entry.target.docker_platform} + {entry.target.py_impl} [{entry.writer_name}]: {entry.error}")
