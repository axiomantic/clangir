"""Command-line interface for headerkit."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import sys
from dataclasses import dataclass, field
from pathlib import Path

from headerkit._config import (
    HeaderkitConfig,
    find_config_file,
    load_config,
    merge_config,
)
from headerkit.backends import _load_backend_plugins, get_backend
from headerkit.writers import WriterBackend, _load_writer_plugins, get_writer


def _build_parser() -> argparse.ArgumentParser:
    """Construct and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="headerkit",
        description="Parse C/C++ header files and emit output via configurable writers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_files",
        nargs="+",
        metavar="FILE",
        help="C header file paths",
    )
    parser.add_argument(
        "--backend",
        dest="backend",
        metavar="NAME",
        default=None,
        help="Backend name (default: libclang)",
    )
    parser.add_argument(
        "-I",
        "--include-dir",
        dest="include_dirs",
        action="append",
        metavar="DIR",
        help="Add include directory",
    )
    parser.add_argument(
        "-D",
        dest="defines",
        action="append",
        metavar="DEFINE",
        help="Add preprocessor define",
    )
    parser.add_argument(
        "--backend-arg",
        dest="backend_args",
        action="append",
        metavar="ARG",
        help="Extra backend argument",
    )
    parser.add_argument(
        "-w",
        "--writer",
        dest="writers",
        action="append",
        metavar="WRITER[:PATH]",
        help="Writer spec",
    )
    parser.add_argument(
        "--writer-opt",
        dest="writer_opts_raw",
        action="append",
        metavar="WRITER:KEY=VALUE",
        help="Writer option",
    )
    parser.add_argument(
        "--config",
        dest="config",
        metavar="PATH",
        default=None,
        help="Explicit config file path",
    )
    parser.add_argument(
        "--no-config",
        dest="no_config",
        action="store_true",
        default=False,
        help="Disable config file loading",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('headerkit')}",
    )
    parser.set_defaults(
        backend="libclang",
        include_dirs=[],
        defines=[],
        backend_args=[],
        writers=[],
        writer_opts_raw=[],
        plugins=[],
    )
    return parser


@dataclass
class WriterSpec:
    """Parsed writer specification from -w and --writer-opt flags."""

    name: str
    output_path: str | None  # None means stdout
    options: dict[str, list[str]] = field(default_factory=dict)


def _parse_writer_specs(
    raw_writers: list[str],
    raw_opts: list[str],
) -> list[WriterSpec]:
    """Parse -w and --writer-opt arguments into WriterSpec objects.

    Raises SystemExit(1) on:
    - --writer-opt without WRITER: scope prefix
    - Malformed --writer-opt (missing KEY=VALUE)
    """
    specs: list[WriterSpec] = []
    spec_by_name: dict[str, WriterSpec] = {}

    for item in raw_writers:
        colon_pos = item.find(":")
        if colon_pos == -1:
            name = item
            output_path = None
        else:
            name = item[:colon_pos]
            output_path = item[colon_pos + 1 :]
        spec = WriterSpec(name=name, output_path=output_path)
        specs.append(spec)
        spec_by_name[name] = spec

    for item in raw_opts:
        colon_pos = item.find(":")
        if colon_pos == -1:
            print(
                f"headerkit: unscoped --writer-opt: {item!r}; use WRITER:KEY=VALUE format",
                file=sys.stderr,
            )
            sys.exit(1)
        writer_name = item[:colon_pos]
        rest = item[colon_pos + 1 :]
        eq_pos = rest.find("=")
        if eq_pos == -1:
            print(
                f"headerkit: malformed --writer-opt: {item!r}; expected WRITER:KEY=VALUE",
                file=sys.stderr,
            )
            sys.exit(1)
        key = rest[:eq_pos]
        value = rest[eq_pos + 1 :]
        if writer_name not in spec_by_name:
            print(
                f"headerkit: warning: --writer-opt for unknown writer {writer_name!r}; ignoring",
                file=sys.stderr,
            )
            continue
        spec_by_name[writer_name].options.setdefault(key, []).append(value)

    return specs


def _parse_defines(defines: list[str]) -> list[str]:
    """Convert -D FOO and -D FOO=BAR into extra_args items.

    -D FOO   -> "-DFOO"
    -D FOO=BAR -> "-DFOO=BAR"
    """
    return [f"-D{d.strip()}" for d in defines]


def _build_umbrella(input_files: list[str]) -> tuple[str, str, tuple[str, ...]]:
    """Build synthetic umbrella header for multiple input files.

    Returns (code, filename, project_prefixes).

    - code: '#include "/abs/path/a.h"\\n#include "/abs/path/b.h"\\n...'
    - filename: basename for single file, "<multiple>" for multiple files
    - project_prefixes: tuple of unique parent directories of resolved input paths

    CRITICAL: project_prefixes contains PARENT DIRECTORIES, not file paths.
    """
    resolved: list[Path] = []
    for f in input_files:
        p = Path(f).resolve()
        resolved.append(p)

    project_prefixes: tuple[str, ...]
    if len(resolved) == 1:
        p = resolved[0]
        code = f'#include "{p}"'
        filename = p.name
        project_prefixes = (str(p.parent),)
    else:
        code = "".join(f'#include "{p}"\n' for p in resolved)
        filename = "<multiple>"
        seen: list[str] = []
        for p in resolved:
            parent = str(p.parent)
            if parent not in seen:
                seen.append(parent)
        project_prefixes = tuple(seen)

    return code, filename, project_prefixes


def _instantiate_writer(spec: WriterSpec) -> WriterBackend:
    """Instantiate a writer from a WriterSpec.

    Raises SystemExit(1) on:
    - Unknown writer name (ValueError from get_writer)
    - Constructor type error (TypeError from get_writer)
    """
    kwargs: dict[str, object] = {}
    for key, values in spec.options.items():
        kwargs[key] = values[0] if len(values) == 1 else values

    if spec.name == "diff" and "baseline" not in kwargs:
        print(
            "Warning: diff writer used without a baseline; outputting full current header state"
            " (not a true diff). Use the Python API to supply a baseline Header.",
            file=sys.stderr,
        )

    try:
        return get_writer(spec.name, **kwargs)
    except ValueError as exc:
        print(f"headerkit: {exc}", file=sys.stderr)
        sys.exit(1)
    except TypeError as exc:
        print(f"headerkit: {exc}", file=sys.stderr)
        sys.exit(1)


def _write_output(spec: WriterSpec, content: str) -> None:
    """Write output to stdout or file per spec.output_path."""
    if spec.output_path is None:
        print(content, end="")
    else:
        Path(spec.output_path).write_text(content, encoding="utf-8")


def _load_explicit_plugins(plugins: list[str]) -> None:
    """Import plugin modules listed explicitly in config.

    Each string is a dotted module path. ImportError warns to stderr.
    """
    for module_name in plugins:
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            print(
                f"headerkit: warning: failed to import plugin {module_name!r}: {exc}",
                file=sys.stderr,
            )


def _merge_config_writer_opts(
    config: HeaderkitConfig | None,
    specs: list[WriterSpec],
) -> list[WriterSpec]:
    """Merge config writer options into specs. Reads config.writer_options directly.

    NOTE: This reads config.writer_options, NOT args.writer_opts (which exists for
    test assertions only). CLI options in specs already take precedence (set before
    this call).
    """
    if config is None:
        return specs
    for spec in specs:
        if spec.name in config.writer_options:
            wc = config.writer_options[spec.name]
            for key, val in wc.options.items():
                if key not in spec.options:  # CLI wins
                    spec.options[key] = val if isinstance(val, list) else [str(val)]
    return specs


def main() -> int:
    """CLI entry point. Returns exit code (0 = success, 1 = error)."""
    parser = _build_parser()
    args = parser.parse_args()

    # typed locals (F2: use correct dest= names, not design doc section 10.2 names)
    backend_name: str = args.backend
    include_dirs: list[str] = args.include_dirs
    defines: list[str] = args.defines
    backend_args: list[str] = args.backend_args
    writers_raw: list[str] = args.writers
    writer_opts_raw: list[str] = args.writer_opts_raw
    config_path_raw: str | None = args.config
    no_config: bool = args.no_config
    input_files: list[str] = args.input_files

    # Load config
    config: HeaderkitConfig | None = None
    if not no_config:
        if config_path_raw is not None:
            explicit_config_path = Path(config_path_raw)
            if not explicit_config_path.exists():
                print(f"headerkit: config file not found: {config_path_raw}", file=sys.stderr)
                return 1
            config = load_config(explicit_config_path)
        else:
            discovered_config_path = find_config_file()
            if discovered_config_path is not None:
                config = load_config(discovered_config_path)

    # Merge config into args (CLI wins)
    args = merge_config(config, args)
    # re-extract after merge
    backend_name = args.backend
    include_dirs = args.include_dirs
    defines = args.defines
    backend_args = args.backend_args
    writers_raw = args.writers
    writer_opts_raw = args.writer_opts_raw

    # Load plugins (F3/F7: no --plugins flag; config.plugins loaded here)
    _load_backend_plugins()
    _load_writer_plugins()
    if config is not None and config.plugins:
        _load_explicit_plugins(config.plugins)

    # Parse writer specs
    specs = _parse_writer_specs(writers_raw, writer_opts_raw)
    if not specs:
        specs = [WriterSpec(name="default", output_path=None, options={})]

    # Validate at most one writer sends to stdout
    stdout_count = sum(1 for s in specs if s.output_path is None)
    if stdout_count > 1:
        print(
            "Error: at most one writer may omit an output path (send to stdout)",
            file=sys.stderr,
        )
        return 1

    # Merge config writer options into specs
    specs = _merge_config_writer_opts(config, specs)

    # Validate input files exist
    for f in input_files:
        if not Path(f).exists():
            print(f"Error: input file not found: {f}", file=sys.stderr)
            return 1

    # Build umbrella
    code, filename, project_prefixes = _build_umbrella(input_files)

    # Parse
    extra_args = _parse_defines(defines) + backend_args
    try:
        backend = get_backend(backend_name)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        header = backend.parse(
            code=code,
            filename=filename,
            include_dirs=include_dirs or None,
            extra_args=extra_args or None,
            project_prefixes=project_prefixes or None,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Write outputs
    for spec in specs:
        writer = _instantiate_writer(spec)  # exits on error
        content = writer.write(header)
        _write_output(spec, content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
