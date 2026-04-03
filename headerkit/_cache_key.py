"""Cache key computation for headerkit's two-layer cache.

IR cache key: SHA-256 of (ir_schema_version, backend, target,
header content, defines, includes, other_args).

Output cache key: SHA-256 of (ir_cache_key, writer_name, writer_options,
writer_cache_version).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# Bump when IR dataclass structure changes in a way that invalidates
# cached JSON. This is NOT the headerkit version.
_IR_SCHEMA_VERSION = "3"


@dataclass
class ParsedArgs:
    """Structured representation of extra_args."""

    defines: list[str]
    includes: list[str]
    other_args: list[str]


def parse_extra_args(
    extra_args: list[str] | None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
) -> ParsedArgs:
    """Parse extra_args into structured components.

    Merges explicit include_dirs and defines with those found in extra_args.
    All include paths are resolved to absolute. All lists are sorted and
    deduplicated for determinism.

    :param extra_args: Raw extra_args from backend.parse() call.
    :param include_dirs: Explicit include directories.
    :param defines: Explicit defines (without -D prefix).
    :returns: Parsed and sorted args.
    """
    found_defines: set[str] = set()
    found_includes: set[str] = set()
    found_other: set[str] = set()

    if defines:
        for d in defines:
            found_defines.add(d)

    if include_dirs:
        for inc in include_dirs:
            found_includes.add(str(Path(inc).resolve()))

    if extra_args:
        args_iter = iter(extra_args)
        for arg in args_iter:
            if arg.startswith("-D"):
                found_defines.add(arg[2:])
            elif arg == "-I":
                try:
                    path = next(args_iter)
                    found_includes.add(str(Path(path).resolve()))
                except StopIteration:
                    pass
            elif arg.startswith("-I"):
                found_includes.add(str(Path(arg[2:]).resolve()))
            else:
                found_other.add(arg)

    return ParsedArgs(
        defines=sorted(found_defines),
        includes=sorted(found_includes),
        other_args=sorted(found_other),
    )


def _relative_header_path(header_path: Path, project_root: Path) -> str:
    """Return header path relative to the project root for cache key hashing.

    :param header_path: Absolute or relative path to the header file.
    :param project_root: Project root directory (contains .headerkit/ or is git root).
    :returns: POSIX-style relative path string.
    :raises ValueError: If header_path is not under project_root.
    """
    resolved = header_path.resolve()
    return resolved.relative_to(project_root.resolve()).as_posix()


def compute_ir_cache_key(
    *,
    backend_name: str,
    target: str,
    header_path: Path,
    project_root: Path,
    parsed_args: ParsedArgs,
    code: str | None = None,
) -> str:
    """Compute SHA-256 cache key for IR layer.

    :param backend_name: Parser backend name.
    :param target: Normalized LLVM target triple.
    :param header_path: Path to the C/C++ header file.
    :param project_root: Project root directory (contains .headerkit/ or is git root).
    :param parsed_args: Structured representation of extra_args.
    :param code: Raw code string. If provided, used instead of reading header_path.
    :returns: Hex digest string.
    """
    hasher = hashlib.sha256()

    hasher.update(f"ir-schema:{_IR_SCHEMA_VERSION}\0".encode())
    hasher.update(f"backend:{backend_name}\0".encode())
    hasher.update(f"target:{target}\0".encode())

    # Header content -- use path relative to project root for portability
    content = code if code is not None else header_path.read_text(encoding="utf-8")
    rel_path = _relative_header_path(header_path, project_root)
    hasher.update(f"header:{rel_path}\0".encode())
    hasher.update(content.encode("utf-8"))
    hasher.update(b"\0")

    for d in sorted(parsed_args.defines):
        hasher.update(f"define:{d}\0".encode())

    for inc in sorted(parsed_args.includes):
        hasher.update(f"include:{inc}\0".encode())

    for arg in sorted(parsed_args.other_args):
        hasher.update(f"arg:{arg}\0".encode())

    return hasher.hexdigest()


def compute_output_cache_key(
    *,
    ir_cache_key: str,
    writer_name: str,
    writer_options: dict[str, object] | None = None,
    writer_cache_version: str | None = None,
) -> str:
    """Compute SHA-256 cache key for output layer.

    :returns: Hex digest string.
    """
    hasher = hashlib.sha256()

    hasher.update(f"ir-key:{ir_cache_key}\0".encode())
    hasher.update(f"writer:{writer_name}\0".encode())

    if writer_options:
        for key in sorted(writer_options.keys()):
            canonical = json.dumps(writer_options[key], sort_keys=True, separators=(",", ":"))
            hasher.update(f"writer-opt:{key}={canonical}\0".encode())

    if writer_cache_version is not None:
        hasher.update(f"writer-version:{writer_cache_version}\0".encode())

    return hasher.hexdigest()
