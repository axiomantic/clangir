"""CLI subcommands for cache operations.

Provides ``cache-check`` and ``cache-save`` subcommands, dispatched
from the main CLI via early dispatch (before argparse construction).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from headerkit.cache import _read_stored_hash, is_up_to_date, save_hash


def _parse_writer_options(raw: list[str] | None) -> dict[str, str] | None:
    """Parse --writer-option KEY=VALUE pairs into a dict.

    Splits on the first ``=`` so that values may contain ``=``.
    """
    if not raw:
        return None
    result: dict[str, str] = {}
    for item in raw:
        eq_pos = item.find("=")
        if eq_pos == -1:
            print(
                f"headerkit cache: malformed --writer-option: {item!r}; expected KEY=VALUE",
                file=sys.stderr,
            )
            continue
        key = item[:eq_pos]
        value = item[eq_pos + 1 :]
        result[key] = value
    return result if result else None


def _build_check_parser() -> argparse.ArgumentParser:
    """Build argument parser for cache-check."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache-check",
        description="Check if a generated output file is up-to-date.",
    )
    parser.add_argument(
        "output_file",
        metavar="OUTPUT",
        help="Path to the generated output file",
    )
    parser.add_argument(
        "--header",
        dest="headers",
        action="append",
        required=True,
        metavar="PATH",
        help="Header file path (repeatable)",
    )
    parser.add_argument(
        "--writer-name",
        dest="writer_name",
        required=True,
        metavar="NAME",
        help="Writer name used for generation",
    )
    parser.add_argument(
        "--writer-option",
        dest="writer_options_raw",
        action="append",
        metavar="KEY=VALUE",
        help="Writer option (repeatable)",
    )
    parser.add_argument(
        "--extra-input",
        dest="extra_inputs",
        action="append",
        metavar="PATH",
        help="Additional input file path (repeatable)",
    )
    return parser


def _build_save_parser() -> argparse.ArgumentParser:
    """Build argument parser for cache-save."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache-save",
        description="Save cache hash metadata for a generated output file.",
    )
    parser.add_argument(
        "output_file",
        metavar="OUTPUT",
        help="Path to the generated output file",
    )
    parser.add_argument(
        "--header",
        dest="headers",
        action="append",
        required=True,
        metavar="PATH",
        help="Header file path (repeatable)",
    )
    parser.add_argument(
        "--writer-name",
        dest="writer_name",
        required=True,
        metavar="NAME",
        help="Writer name used for generation",
    )
    parser.add_argument(
        "--writer-option",
        dest="writer_options_raw",
        action="append",
        metavar="KEY=VALUE",
        help="Writer option (repeatable)",
    )
    parser.add_argument(
        "--extra-input",
        dest="extra_inputs",
        action="append",
        metavar="PATH",
        help="Additional input file path (repeatable)",
    )
    parser.add_argument(
        "--writer",
        dest="writer_instance_name",
        metavar="NAME",
        default=None,
        help="Writer instance name for embedded comment support (uses get_writer)",
    )
    return parser


def cache_check_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache-check``."""
    parser = _build_check_parser()
    args = parser.parse_args(argv)

    writer_options = _parse_writer_options(args.writer_options_raw)
    output_path = Path(args.output_file)

    try:
        result = is_up_to_date(
            output_path=output_path,
            header_paths=args.headers,
            writer_name=args.writer_name,
            writer_options=writer_options,
            extra_inputs=args.extra_inputs,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"stale: {output_path} (reason: {exc})")
        return 1

    if result:
        print(f"up-to-date: {output_path}")
        return 0
    else:
        if not output_path.exists():
            reason = "missing output"
        else:
            stored = _read_stored_hash(output_path)
            if stored is None:
                reason = "no stored hash"
            else:
                reason = "hash mismatch"
        print(f"stale: {output_path} (reason: {reason})")
        return 1


def cache_save_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache-save``."""
    parser = _build_save_parser()
    args = parser.parse_args(argv)

    writer_options = _parse_writer_options(args.writer_options_raw)
    output_path = Path(args.output_file)

    # Optionally instantiate writer for embedded support
    writer = None
    if args.writer_instance_name is not None:
        from headerkit.writers import get_writer

        try:
            writer = get_writer(args.writer_instance_name)
        except ValueError as exc:
            print(f"headerkit cache-save: {exc}", file=sys.stderr)
            return 1

    try:
        result_path = save_hash(
            output_path=output_path,
            header_paths=args.headers,
            writer_name=args.writer_name,
            writer_options=writer_options,
            extra_inputs=args.extra_inputs,
            writer=writer,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"headerkit cache-save: {exc}", file=sys.stderr)
        return 1

    storage_type = "embedded" if result_path == output_path else "sidecar"
    print(f"saved: {result_path} ({storage_type})")
    return 0
