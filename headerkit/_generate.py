"""Cache-aware generation pipeline.

Orchestrates the two-layer cache: IR cache (parsed header) and
output cache (writer output). Provides generate() for single-writer
and generate_all() for multi-writer generation.

The 10-step flow for generate():
  1. Resolve cache_dir via find_cache_dir
  2. Read header file content
  3. Parse extra_args into ParsedArgs
  4. Compute IR cache key
  5. Build IR slug, check IR cache
  6. Cache hit -> json_to_header; Cache miss -> backend.parse() -> write IR to cache
  7. For each writer: check cache_output attr, compute output cache key, build output slug
  8. Output cache hit -> read cached; Output cache miss -> writer.write() -> write to cache
  9. Optionally write output to output_path
  10. Return result
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headerkit._cache_key import (
    compute_ir_cache_key,
    compute_output_cache_key,
    parse_extra_args,
)
from headerkit._cache_store import (
    find_cache_dir,
    read_ir_entry,
    read_output_entry,
    write_ir_entry,
    write_output_entry,
)
from headerkit._slug import build_slug, load_index, lookup_slug
from headerkit.backends import get_backend
from headerkit.ir import Header
from headerkit.writers import get_writer

logger = logging.getLogger("headerkit.cache")

_WRITER_EXTENSIONS: dict[str, str] = {
    "cffi": ".py",
    "ctypes": ".py",
    "cython": ".pxd",
    "json": ".json",
    "lua": ".lua",
    "prompt": ".txt",
    "diff": ".json",
}


@dataclass
class GenerateResult:
    """Result of a single writer's generation."""

    writer_name: str
    output: str
    output_path: Path | None
    from_cache: bool


def _should_cache_output(writer: Any) -> bool:
    """Check if writer opts in to output caching (default: True)."""
    return bool(getattr(writer, "cache_output", True))


def _writer_cache_version(writer: Any) -> str | None:
    """Get writer's cache version for invalidation (default: None)."""
    return getattr(writer, "cache_version", None)


def _writer_output_ext(writer: Any, writer_name: str) -> str:
    """Get file extension for a writer's output.

    Checks writer for output_extension attribute first (plugin support),
    then falls back to built-in mapping, then defaults to .txt.
    """
    ext: str | None = getattr(writer, "output_extension", None)
    if ext is not None:
        return ext
    return _WRITER_EXTENSIONS.get(writer_name, ".txt")


def generate(
    header_path: str | Path,
    writer_name: str | None = None,
    *,
    code: str | None = None,
    backend_name: str | None = None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
    extra_args: list[str] | None = None,
    writer_options: dict[str, object] | None = None,
    output_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
    project_prefixes: tuple[str, ...] | None = None,
) -> str:
    """Parse a C/C++ header and generate output using a single writer.

    Uses the two-layer cache: first checks IR cache, then output cache.
    Falls back to parsing with the backend if IR cache misses.

    :param header_path: Path to the C/C++ header file (used for cache key
        even when *code* is provided).
    :param writer_name: Writer to use (default: "json").
    :param code: Raw code string. When provided, this content is parsed
        instead of reading *header_path* from disk.
    :param backend_name: Backend to use (default: "libclang").
    :param include_dirs: Include directories for parsing.
    :param defines: Preprocessor defines (without -D prefix).
    :param extra_args: Additional backend args.
    :param writer_options: Writer constructor kwargs.
    :param output_path: If provided, write output to this file.
    :param cache_dir: Cache directory (default: .hkcache/ in project root).
    :param no_cache: Disable all caching.
    :param no_ir_cache: Disable IR cache only.
    :param no_output_cache: Disable output cache only.
    :param project_prefixes: Tuple of project prefix directories for backend.
    :returns: Generated output string.
    :raises FileNotFoundError: If header_path does not exist and code is not provided.
    """
    # ---- Step 1: Resolve cache_dir ----
    header_path = Path(header_path)
    if code is None and not header_path.exists():
        raise FileNotFoundError(f"Header file not found: {header_path}")

    backend_name = backend_name or "libclang"
    writer_name = writer_name or "json"
    writer_options = writer_options or {}

    resolved_cache_dir: Path | None = None
    if not no_cache:
        if cache_dir is not None:
            resolved_cache_dir = Path(cache_dir)
            resolved_cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            resolved_cache_dir = find_cache_dir(header_path.parent)

    # ---- Step 2: Read header file content (for cache key) ----
    # (content is read lazily by compute_ir_cache_key)

    # ---- Step 3: Parse extra_args into ParsedArgs ----
    parsed_args = parse_extra_args(extra_args, include_dirs, defines)

    # ---- Step 4: Compute IR cache key ----
    project_root = resolved_cache_dir.parent if resolved_cache_dir else header_path.parent
    ir_cache_key = compute_ir_cache_key(
        backend_name=backend_name,
        header_path=header_path,
        project_root=project_root,
        parsed_args=parsed_args,
        code=code,
    )

    # ---- Step 5: Build IR slug, check IR cache ----
    ir_slug = build_slug(
        backend_name=backend_name,
        header_path=str(header_path),
        defines=parsed_args.defines,
        includes=parsed_args.includes,
        other_args=parsed_args.other_args,
    )

    header: Header | None = None

    # ---- Step 6: Cache hit -> json_to_header; Cache miss -> backend.parse() ----
    use_ir_cache = not no_cache and not no_ir_cache and resolved_cache_dir is not None
    if use_ir_cache:
        assert resolved_cache_dir is not None
        ir_index_path = resolved_cache_dir / "ir" / "index.json"
        if ir_index_path.exists():
            ir_index = load_index(ir_index_path)
            existing_slug = lookup_slug(ir_index, ir_cache_key)
            if existing_slug is not None:
                header = read_ir_entry(cache_dir=resolved_cache_dir, slug=existing_slug)

    if header is None:
        # Cache miss: parse with backend
        backend = get_backend(backend_name)
        parse_code = code if code is not None else header_path.read_text(encoding="utf-8")
        all_args: list[str] = []
        for d in parsed_args.defines:
            all_args.append(f"-D{d}")
        for inc in parsed_args.includes:
            all_args.append(f"-I{inc}")
        all_args.extend(parsed_args.other_args)
        header = backend.parse(
            parse_code,
            str(header_path),
            include_dirs or [],
            all_args,
            project_prefixes=project_prefixes,
        )

        # Write IR to cache
        if use_ir_cache:
            assert resolved_cache_dir is not None
            try:
                write_ir_entry(
                    cache_dir=resolved_cache_dir,
                    slug=ir_slug,
                    cache_key=ir_cache_key,
                    header=header,
                    backend_name=backend_name,
                    header_path=str(header_path),
                    defines=parsed_args.defines,
                    includes=parsed_args.includes,
                    other_args=parsed_args.other_args,
                )
            except OSError as exc:
                logger.warning("Failed to write IR cache entry: %s", exc)

    # ---- Step 7: Check cache_output attr, compute output cache key ----
    writer_inst = get_writer(writer_name, **writer_options)
    should_cache = _should_cache_output(writer_inst)
    use_output_cache = not no_cache and not no_output_cache and should_cache and resolved_cache_dir is not None

    output_cache_key = compute_output_cache_key(
        ir_cache_key=ir_cache_key,
        writer_name=writer_name,
        writer_options=writer_options or None,
        writer_cache_version=_writer_cache_version(writer_inst),
    )
    output_ext = _writer_output_ext(writer_inst, writer_name)

    output: str | None = None

    # ---- Step 8: Output cache hit -> read cached; miss -> writer.write() ----
    if use_output_cache:
        assert resolved_cache_dir is not None
        writer_index_path = resolved_cache_dir / "output" / writer_name / "index.json"
        if writer_index_path.exists():
            writer_index = load_index(writer_index_path)
            existing_out_slug = lookup_slug(writer_index, output_cache_key)
            if existing_out_slug is not None:
                output = read_output_entry(
                    cache_dir=resolved_cache_dir,
                    writer_name=writer_name,
                    slug=existing_out_slug,
                    output_extension=output_ext,
                )

    if output is None:
        output = writer_inst.write(header)

        # Write output to cache
        if use_output_cache:
            assert resolved_cache_dir is not None
            try:
                write_output_entry(
                    cache_dir=resolved_cache_dir,
                    writer_name=writer_name,
                    slug=ir_slug,
                    cache_key=output_cache_key,
                    ir_cache_key=ir_cache_key,
                    output=output,
                    writer_options=writer_options or {},
                    writer_cache_version=_writer_cache_version(writer_inst),
                    output_extension=output_ext,
                )
            except OSError as exc:
                logger.warning("Failed to write output cache entry: %s", exc)

    # ---- Step 9: Optionally write output to output_path ----
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(output, encoding="utf-8")

    # ---- Step 10: Return result ----
    return output


def generate_all(
    header_path: str | Path,
    writers: list[str] | None = None,
    *,
    backend_name: str | None = None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
    extra_args: list[str] | None = None,
    writer_options: dict[str, dict[str, object]] | None = None,
    output_dir: str | Path | None = None,
    output_paths: dict[str, str | Path] | None = None,
    cache_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
) -> list[GenerateResult]:
    """Parse a C/C++ header and generate output for multiple writers.

    Parses the header once (or loads from IR cache), then runs each
    writer (or loads from output cache).

    :param header_path: Path to the C/C++ header file.
    :param writers: List of writer names. Default: ["json"].
    :param backend_name: Backend to use (default: "libclang").
    :param include_dirs: Include directories for parsing.
    :param defines: Preprocessor defines (without -D prefix).
    :param extra_args: Additional backend args.
    :param writer_options: Per-writer kwargs, keyed by writer name.
    :param output_dir: Base directory for output files (auto-named).
    :param output_paths: Explicit output paths per writer name.
    :param cache_dir: Cache directory (default: .hkcache/ in project root).
    :param no_cache: Disable all caching.
    :param no_ir_cache: Disable IR cache only.
    :param no_output_cache: Disable output cache only.
    :returns: List of GenerateResult, one per writer.
    :raises FileNotFoundError: If header_path does not exist.
    """
    writers = writers or ["json"]
    writer_options = writer_options or {}
    output_paths = output_paths or {}

    results: list[GenerateResult] = []
    for wname in writers:
        wopts = writer_options.get(wname, {})
        opath: str | Path | None = output_paths.get(wname)
        if opath is None and output_dir is not None:
            ext = _WRITER_EXTENSIONS.get(wname, ".txt")
            stem = Path(header_path).stem
            opath = Path(output_dir) / f"{stem}{ext}"

        output = generate(
            header_path=header_path,
            writer_name=wname,
            backend_name=backend_name,
            include_dirs=include_dirs,
            defines=defines,
            extra_args=extra_args,
            writer_options=wopts,
            output_path=opath,
            cache_dir=cache_dir,
            no_cache=no_cache,
            no_ir_cache=no_ir_cache,
            no_output_cache=no_output_cache,
        )

        results.append(
            GenerateResult(
                writer_name=wname,
                output=output,
                output_path=Path(opath) if opath else None,
                from_cache=False,  # Simplified; actual cache-hit tracking
                # would need to be threaded through generate()
            )
        )

    return results
