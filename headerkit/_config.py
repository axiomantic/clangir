"""TOML config loading for headerkit.

Finds and loads .headerkit.toml or [tool.headerkit] from pyproject.toml,
walking upward from the current directory. Merges config values with CLI
args (CLI always wins).
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

# TOMLDecodeError for narrowed exception handling in both branches.
if tomllib is not None:
    _TOML_DECODE_ERROR: type[Exception] = tomllib.TOMLDecodeError  # type: ignore[attr-defined]
else:
    _TOML_DECODE_ERROR = Exception  # placeholder; unreachable when tomllib is None


@dataclass
class WriterConfig:
    """Per-writer constructor kwargs loaded from config.

    Stored as a dict[str, object] where keys are constructor parameter names
    and values are whatever TOML parsed (str, int, list[str], etc.).
    """

    options: dict[str, object] = field(default_factory=dict)


@dataclass
class HeaderkitConfig:
    """Merged configuration from config file.

    Fields map directly to CLI flags. None means "not set in config".
    """

    backend: str | None = None
    writers: list[str] | None = None  # bare names only (no :path syntax)
    include_dirs: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    backend_args: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    writer_options: dict[str, WriterConfig] = field(default_factory=dict)
    # Resolved source path for error reporting
    source_path: Path | None = None


def _parse_toml(data: bytes) -> dict[str, object]:
    """Parse TOML bytes. Caller must verify tomllib is available first."""
    if tomllib is None:
        raise RuntimeError("tomllib not available")  # should not reach here
    return cast(dict[str, object], tomllib.load(io.BytesIO(data)))  # type: ignore[arg-type]


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk from `start` (default: CWD) upward, stopping at git root, home, or /.

    Search order per directory:
    1. .headerkit.toml
    2. pyproject.toml (only if it contains [tool.headerkit])

    Returns the Path of the first match, or None if no config found.
    """
    current = start or Path.cwd()
    home = Path.home()

    while True:
        # Check .headerkit.toml first
        headerkit_toml = current / ".headerkit.toml"
        if headerkit_toml.exists():
            return headerkit_toml

        # Check pyproject.toml — only if it has [tool.headerkit] section
        pyproject = current / "pyproject.toml"
        if pyproject.exists() and tomllib is not None:
            try:
                raw = _parse_toml(pyproject.read_bytes())
            except _TOML_DECODE_ERROR:
                raw = {}
            tool = raw.get("tool", {})
            if isinstance(tool, dict) and "headerkit" in tool:
                return pyproject

        # Stop at git root, home directory, or filesystem root
        if (current / ".git").exists() or current == current.parent or current == home:
            break

        current = current.parent

    return None


def _require_str_list(val: object, field_name: str, source: Path) -> list[str]:
    """Validate that val is a list of strings; exit with error if not."""
    if not isinstance(val, list) or not all(isinstance(item, str) for item in val):
        print(
            f"headerkit: config error in {source}: {field_name} must be list[str], got {type(val).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)
    return cast(list[str], val)


def _extract_config(data: dict[str, object], source: Path) -> HeaderkitConfig:
    """Extract and type-check config fields from parsed TOML dict."""
    config = HeaderkitConfig(source_path=source)

    # backend: optional string
    if "backend" in data:
        val = data["backend"]
        if not isinstance(val, str):
            print(
                f"headerkit: config error in {source}: backend must be str, got {type(val).__name__}",
                file=sys.stderr,
            )
            sys.exit(1)
        config.backend = val

    # writers: optional list of strings (no :path syntax)
    if "writers" in data:
        writers = _require_str_list(data["writers"], "writers", source)
        for item in writers:
            if ":" in item:
                print(
                    f"headerkit: config error in {source}: writers must be bare names (no :path syntax), got {item!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
        config.writers = writers

    # include_dirs: optional list of strings
    if "include_dirs" in data:
        config.include_dirs = _require_str_list(data["include_dirs"], "include_dirs", source)

    # defines: optional list of strings
    if "defines" in data:
        config.defines = _require_str_list(data["defines"], "defines", source)

    # backend_args: optional list of strings
    if "backend_args" in data:
        config.backend_args = _require_str_list(data["backend_args"], "backend_args", source)

    # plugins: optional list of strings
    if "plugins" in data:
        config.plugins = _require_str_list(data["plugins"], "plugins", source)

    # writer options: [writer.NAME] sections -> writer_options[NAME].options
    if "writer" in data:
        val = data["writer"]
        if not isinstance(val, dict):
            print(
                f"headerkit: config error in {source}: writer must be a table, got {type(val).__name__}",
                file=sys.stderr,
            )
            sys.exit(1)
        writer_table: dict[str, object] = cast(dict[str, object], val)
        for writer_name, writer_opts in writer_table.items():
            if not isinstance(writer_opts, dict):
                print(
                    f"headerkit: config error in {source}: writer.{writer_name} must be a table, "
                    f"got {type(writer_opts).__name__}",
                    file=sys.stderr,
                )
                sys.exit(1)
            config.writer_options[writer_name] = WriterConfig(options=cast(dict[str, object], writer_opts))

    return config


def load_config(path: Path) -> HeaderkitConfig:
    """Load and validate config from `path`.

    Raises SystemExit(1) with stderr message on:
    - tomllib is None (cannot parse TOML)
    - tomllib.TOMLDecodeError
    - Type validation failures (wrong TOML value types)

    Returns empty HeaderkitConfig if path is a pyproject.toml with no
    [tool.headerkit] section.
    """
    if tomllib is None:
        print(
            f"headerkit: TOML config found at {path} but no TOML parser available.\n"
            "Install: pip install tomli  (Python 3.10 only)",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        raw = _parse_toml(path.read_bytes())
    except _TOML_DECODE_ERROR as exc:
        print(f"headerkit: config parse error in {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Extract the relevant section
    if path.name == "pyproject.toml":
        tool = raw.get("tool", {})
        if not isinstance(tool, dict):
            return HeaderkitConfig()
        headerkit_section = tool.get("headerkit", {})
        if not isinstance(headerkit_section, dict) or not headerkit_section:
            return HeaderkitConfig()
        data: dict[str, object] = cast(dict[str, object], headerkit_section)
    else:
        data = raw

    return _extract_config(data, path)


def merge_config(config: HeaderkitConfig | None, args: argparse.Namespace) -> argparse.Namespace:
    """Apply config values to args namespace, but only where args are unset.

    Mutates `args` in place. CLI flags always win.

    NOTE (F1): This function populates `args.writer_opts` dict for test assertions ONLY.
    It is NOT consumed by `main()`. The `_merge_config_writer_opts()` function in
    `_cli.py` independently reads `config.writer_options` at the WriterSpec level.
    """
    if config is None:
        return args

    # backend: use config value only when CLI did not set one
    if getattr(args, "backend", None) is None and config.backend is not None:
        args.backend = config.backend

    # include_dirs: prepend config dirs (config provides defaults; CLI adds more)
    cli_include_dirs: list[str] = list(getattr(args, "include_dirs", None) or [])
    args.include_dirs = config.include_dirs + cli_include_dirs

    # defines: prepend config defines
    cli_defines: list[str] = list(getattr(args, "defines", None) or [])
    args.defines = config.defines + cli_defines

    # backend_args: prepend config backend_args
    cli_backend_args: list[str] = list(getattr(args, "backend_args", None) or [])
    args.backend_args = config.backend_args + cli_backend_args

    # writers: use config writers only when CLI specified none
    cli_writers: list[str] = list(getattr(args, "writers", None) or [])
    if not cli_writers and config.writers is not None:
        args.writers = list(config.writers)

    # plugins: extend with config plugins, deduplicating
    cli_plugins: list[str] = list(getattr(args, "plugins", None) or [])
    combined: list[str] = list(cli_plugins)
    for plugin in config.plugins:
        if plugin not in combined:
            combined.append(plugin)
    args.plugins = combined

    # writer_opts: merge config writer options (CLI values win per-key)
    # NOTE (F1): Populated for test assertions only. Not consumed by main().
    writer_opts: dict[str, dict[str, object]] = dict(getattr(args, "writer_opts", None) or {})
    for writer_name, writer_cfg in config.writer_options.items():
        if writer_name not in writer_opts:
            writer_opts[writer_name] = {}
        for key, value in writer_cfg.options.items():
            if key not in writer_opts[writer_name]:
                writer_opts[writer_name][key] = value
    args.writer_opts = writer_opts

    return args
