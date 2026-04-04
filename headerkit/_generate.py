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
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headerkit._cache_key import (
    ParsedArgs,
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
from headerkit._config import _TOML_DECODE_ERROR, _find_project_root, _parse_toml
from headerkit._resolve import check_output_collisions, resolve_headers, resolve_output_path
from headerkit._slug import build_slug, load_index, lookup_slug
from headerkit._target import resolve_target
from headerkit.backends import LibclangUnavailableError, get_backend, is_backend_available
from headerkit.install_libclang import auto_install
from headerkit.ir import Header
from headerkit.writers import get_writer

logger = logging.getLogger("headerkit.cache")


def _is_auto_install_allowed(
    project_root: Path,
    auto_install_libclang: bool | None = None,
) -> bool:
    """Check whether auto-install of libclang is allowed.

    The feature is **opt-in by default**: auto-install is disabled unless
    explicitly enabled through one of the configuration layers below.

    Precedence (highest first):

    1. ``auto_install_libclang`` kwarg -- ``True`` or ``False`` from the
       caller (e.g. ``generate(auto_install_libclang=True)``).  When set,
       all other layers are skipped.
    2. ``HEADERKIT_AUTO_INSTALL_LIBCLANG`` environment variable -- set to
       ``"1"`` to enable, any other value to disable.
    3. ``auto_install_libclang`` key in the ``[tool.headerkit]`` section of
       the project's ``pyproject.toml`` -- ``true`` to enable, ``false`` to
       disable.
    4. Default: ``False`` (auto-install disabled).

    :param project_root: Project root directory to search for pyproject.toml.
    :param auto_install_libclang: Explicit kwarg override from caller.
    :returns: True if auto-install is allowed, False otherwise.
    """
    # Layer 1: explicit kwarg
    if auto_install_libclang is not None:
        logger.debug(
            "Auto-install %s by explicit kwarg",
            "enabled" if auto_install_libclang else "disabled",
        )
        return auto_install_libclang

    # Layer 2: environment variable
    env_val = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG")
    if env_val is not None:
        enabled = env_val == "1"
        logger.debug(
            "Auto-install %s by HEADERKIT_AUTO_INSTALL_LIBCLANG=%s",
            "enabled" if enabled else "disabled",
            env_val,
        )
        return enabled

    # Layer 3: pyproject.toml config
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            raw = _parse_toml(pyproject.read_bytes())
            tool = raw.get("tool", {})
            section = tool.get("headerkit", {}) if isinstance(tool, dict) else {}
            if isinstance(section, dict) and "auto_install_libclang" in section:
                config_val = bool(section["auto_install_libclang"])
                logger.debug(
                    "Auto-install %s by auto_install_libclang=%s in %s",
                    "enabled" if config_val else "disabled",
                    section["auto_install_libclang"],
                    pyproject,
                )
                return config_val
        except _TOML_DECODE_ERROR as exc:
            logger.warning("Malformed TOML in %s; ignoring headerkit config: %s", pyproject, exc)
        except (FileNotFoundError, KeyError, RuntimeError) as exc:
            logger.warning("Could not read auto_install_libclang config from %s: %s", pyproject, exc)

    # Layer 4: default (opt-in, so False)
    logger.debug("Auto-install disabled by default (opt-in)")
    return False


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


@dataclass
class BatchResult:
    """Result from batch_generate()."""

    results: list[GenerateResult]
    headers_processed: int
    headers_skipped: int


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


def _writer_default_output_pattern(writer: Any, writer_name: str) -> str:
    """Get the default output path template for a writer."""
    pattern: str | None = getattr(writer, "default_output_pattern", None)
    if pattern is not None:
        return pattern
    # Fallback for plugin writers that don't declare the attribute
    ext = _WRITER_EXTENSIONS.get(writer_name, ".txt")
    return "{dir}/{stem}" + ext


def _get_ir(
    *,
    backend_name: str,
    header_path: Path,
    project_root: Path,
    parsed_args: ParsedArgs,
    code: str | None,
    resolved_cache_dir: Path | None,
    use_ir_cache: bool,
    project_prefixes: tuple[str, ...] | None,
    target: str,
) -> tuple[Header, str, str, bool]:
    """Resolve IR from cache or by parsing with the backend.

    :returns: (header, ir_cache_key, ir_slug, ir_from_cache)
    """
    ir_cache_key = compute_ir_cache_key(
        backend_name=backend_name,
        header_path=header_path,
        project_root=project_root,
        parsed_args=parsed_args,
        code=code,
        target=target,
    )

    ir_slug = build_slug(
        backend_name=backend_name,
        header_path=str(header_path),
        defines=parsed_args.defines,
        includes=parsed_args.includes,
        other_args=parsed_args.other_args,
        target=target,
    )

    header: Header | None = None
    ir_from_cache = False

    if use_ir_cache:
        assert resolved_cache_dir is not None
        ir_index_path = resolved_cache_dir / "ir" / "index.json"
        if ir_index_path.exists():
            ir_index = load_index(ir_index_path)
            existing_slug = lookup_slug(ir_index, ir_cache_key)
            if existing_slug is not None:
                header = read_ir_entry(cache_dir=resolved_cache_dir, slug=existing_slug)
                if header is not None:
                    ir_from_cache = True

    if header is None:
        backend = get_backend(backend_name)
        parse_code = code if code is not None else header_path.read_text(encoding="utf-8")
        all_args: list[str] = ["-target", target]
        for d in parsed_args.defines:
            all_args.append(f"-D{d}")
        for inc in parsed_args.includes:
            all_args.append(f"-I{inc}")
        all_args.extend(parsed_args.other_args)
        header = backend.parse(
            parse_code,
            str(header_path),
            [],
            all_args,
            project_prefixes=project_prefixes,
        )

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
                    target=target,
                )
            except OSError as exc:
                logger.warning("Failed to write IR cache entry: %s", exc)

    return header, ir_cache_key, ir_slug, ir_from_cache


def _match_defines(source: str, patterns: list[str]) -> list[str]:
    """Scan *source* for ``#define`` names matching any of *patterns*.

    Each pattern is compiled as a regex and tested against every
    ``#define NAME`` token found in the source.  Returns a
    deduplicated, stable-order list of matching names.
    """
    compiled = [re.compile(p) for p in patterns]
    define_re = re.compile(r"^\s*#\s*define\s+(\w+)", re.MULTILINE)
    seen: set[str] = set()
    result: list[str] = []
    for m in define_re.finditer(source):
        name = m.group(1)
        if name in seen:
            continue
        for pat in compiled:
            if pat.search(name):
                seen.add(name)
                result.append(name)
                break
    return result


def _populate_matched_defines(
    writer_inst: Any,
    header_path: Path,
    code: str | None,
    include_dirs: list[str] | None = None,
) -> None:
    """If *writer_inst* has ``define_patterns``, scan source and populate ``_matched_defines``.

    When *code* is provided and contains ``#include`` directives, the
    referenced files are resolved against *include_dirs* and scanned as
    well so that ``define_patterns`` can match macros defined in included
    headers (e.g. the umbrella-header pattern).
    """
    define_patterns: list[str] | None = getattr(writer_inst, "define_patterns", None)
    if not define_patterns:
        return

    sources: list[str] = []
    if code is not None:
        sources.append(code)
        # Resolve #include directives so define_patterns can match macros
        # defined in the included headers (umbrella-header pattern).
        if include_dirs:
            for m in re.finditer(r'#include\s*[<"]([^>"]+)[>"]', code):
                include_name = m.group(1)
                for inc_dir in include_dirs:
                    full_path = Path(inc_dir) / include_name
                    if full_path.is_file():
                        sources.append(full_path.read_text(encoding="utf-8"))
                        break
    else:
        sources.append(header_path.read_text(encoding="utf-8"))

    combined = "\n".join(sources)
    writer_inst._matched_defines = _match_defines(combined, define_patterns)


def _get_output(
    *,
    header: Header,
    writer_name: str,
    writer_options: dict[str, object],
    ir_cache_key: str,
    ir_slug: str,
    resolved_cache_dir: Path | None,
    use_output_cache: bool,
    header_path: Path,
    code: str | None,
    include_dirs: list[str] | None = None,
) -> tuple[str, bool]:
    """Resolve output from cache or by running the writer.

    :returns: (output, output_from_cache)
    """
    writer_inst: Any = None
    output: str | None = None
    output_from_cache = False
    output_cache_key = ""
    output_ext = ""

    if use_output_cache:
        assert resolved_cache_dir is not None
        writer_inst, output_cache_key, output_ext = _compute_output_cache_info(
            ir_cache_key=ir_cache_key,
            writer_name=writer_name,
            writer_options=writer_options,
        )
        output = _read_cached_output(
            cache_dir=resolved_cache_dir,
            writer_name=writer_name,
            output_cache_key=output_cache_key,
            output_ext=output_ext,
        )
        if output is not None:
            output_from_cache = True

    if writer_inst is None:
        writer_inst = get_writer(writer_name, **writer_options)

    effective_output_cache = use_output_cache and _should_cache_output(writer_inst)

    if output is None:
        _populate_matched_defines(writer_inst, header_path, code, include_dirs=include_dirs)
        output = writer_inst.write(header)

        if effective_output_cache:
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

    return output, output_from_cache


def _compute_output_cache_info(
    *,
    ir_cache_key: str,
    writer_name: str,
    writer_options: dict[str, object],
) -> tuple[Any, str, str]:
    """Instantiate the writer and derive its output cache key and extension.

    Pure computation with no I/O -- separated from cache lookup so callers
    that only need the key (e.g. ``_try_output_cache_fallback``) are not
    forced to accept unused return values.

    :returns: (writer_inst, output_cache_key, output_ext)
    """
    writer_inst = get_writer(writer_name, **writer_options)
    output_cache_key = compute_output_cache_key(
        ir_cache_key=ir_cache_key,
        writer_name=writer_name,
        writer_options=writer_options,
        writer_cache_version=_writer_cache_version(writer_inst),
    )
    output_ext = _writer_output_ext(writer_inst, writer_name)
    return writer_inst, output_cache_key, output_ext


def _read_cached_output(
    *,
    cache_dir: Path,
    writer_name: str,
    output_cache_key: str,
    output_ext: str,
) -> str | None:
    """Read a cached output entry from the index, if present.

    :returns: The cached output string, or ``None`` on miss.
    """
    writer_index_path = cache_dir / "output" / writer_name / "index.json"
    if writer_index_path.exists():
        writer_index = load_index(writer_index_path)
        existing_out_slug = lookup_slug(writer_index, output_cache_key)
        if existing_out_slug is not None:
            return read_output_entry(
                cache_dir=cache_dir,
                writer_name=writer_name,
                slug=existing_out_slug,
                output_extension=output_ext,
            )
    return None


def _write_output_file(output_path: str | Path, output: str) -> None:
    """Write generated output to *output_path*, creating parents as needed."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(output, encoding="utf-8")


def _try_output_cache_fallback(
    *,
    cache_dir: Path,
    backend_name: str,
    header_path: Path,
    parsed_args: ParsedArgs,
    project_root: Path,
    writer_name: str,
    writer_options: dict[str, object],
    code: str | None,
    target: str,
) -> str | None:
    """Try to serve from the output cache without needing a backend.

    Computes cache keys (which don't require the backend) and checks the
    output index. Returns the cached output string if found, or None.
    """
    ir_cache_key = compute_ir_cache_key(
        backend_name=backend_name,
        header_path=header_path,
        project_root=project_root,
        parsed_args=parsed_args,
        code=code,
        target=target,
    )

    _writer_inst, output_cache_key, output_ext = _compute_output_cache_info(
        ir_cache_key=ir_cache_key,
        writer_name=writer_name,
        writer_options=writer_options,
    )
    return _read_cached_output(
        cache_dir=cache_dir,
        writer_name=writer_name,
        output_cache_key=output_cache_key,
        output_ext=output_ext,
    )


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
    store_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
    project_prefixes: tuple[str, ...] | None = None,
    auto_install_libclang: bool | None = None,
    target: str | None = None,
    _result_meta: dict[str, object] | None = None,
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
    :param store_dir: Store directory. When ``None``, falls back to the
        ``HEADERKIT_STORE_DIR`` environment variable, then auto-detects
        ``.headerkit/`` in the project root.
    :param no_cache: Disable all caching.
    :param no_ir_cache: Disable IR cache only.
    :param no_output_cache: Disable output cache only.
    :param project_prefixes: Tuple of project prefix directories for backend.
    :param auto_install_libclang: Explicitly enable (True) or disable (False)
        automatic libclang installation. When None, falls back to the
        ``HEADERKIT_AUTO_INSTALL_LIBCLANG`` env var, then pyproject.toml
        config, then defaults to False.
    :param target: Target triple for cross-compilation (e.g.,
        ``"aarch64-apple-darwin"``). When ``None``, auto-detects the host
        triple.
    :returns: Generated output string.
    :raises FileNotFoundError: If header_path does not exist and code is not provided.
    """
    header_path = Path(header_path)
    if code is None and not header_path.exists():
        raise FileNotFoundError(f"Header file not found: {header_path}")

    backend_name = backend_name or "libclang"
    writer_name = writer_name or "json"
    writer_options = writer_options or {}

    # Resolve store_dir: explicit param > HEADERKIT_STORE_DIR env var > auto-detect
    if store_dir is None:
        if env_store_dir := os.environ.get("HEADERKIT_STORE_DIR"):
            store_dir = env_store_dir

    resolved_cache_dir: Path | None = None
    if not no_cache:
        if store_dir is not None:
            resolved_cache_dir = Path(store_dir)
            resolved_cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            resolved_cache_dir = find_cache_dir(header_path.parent)

    parsed_args = parse_extra_args(extra_args, include_dirs, defines)
    project_root = _find_project_root(header_path.parent)

    resolved_target = resolve_target(target=target, project_root=project_root)

    use_ir_cache = not no_cache and not no_ir_cache and resolved_cache_dir is not None
    use_output_cache = not no_cache and not no_output_cache and resolved_cache_dir is not None

    def _resolve_ir() -> tuple[Header, str, str, bool]:
        return _get_ir(
            backend_name=backend_name,
            header_path=header_path,
            project_root=project_root,
            parsed_args=parsed_args,
            code=code,
            resolved_cache_dir=resolved_cache_dir,
            use_ir_cache=use_ir_cache,
            project_prefixes=project_prefixes,
            target=resolved_target,
        )

    # For libclang, check availability before parsing to enable the
    # output-cache fallback and auto-install flow.  Other backends skip
    # this check and let parse errors propagate naturally.
    if backend_name == "libclang" and not is_backend_available(backend_name):
        # Library not loadable -- try output cache first (cheaper than install)
        if use_output_cache:
            assert resolved_cache_dir is not None  # guaranteed by use_output_cache
            cached_output = _try_output_cache_fallback(
                cache_dir=resolved_cache_dir,
                backend_name=backend_name,
                header_path=header_path,
                parsed_args=parsed_args,
                project_root=project_root,
                writer_name=writer_name,
                writer_options=writer_options,
                code=code,
                target=resolved_target,
            )
            if cached_output is not None:
                if output_path is not None:
                    _write_output_file(output_path, cached_output)
                if _result_meta is not None:
                    _result_meta["from_cache"] = True
                return cached_output

        # Output cache miss -- auto-install if allowed.
        # After auto_install() puts libclang on disk, _resolve_ir() will
        # call get_backend() which returns LibclangBackend (always registered),
        # and its parse() calls _configure_libclang() which re-searches
        # for the library without caching failure.  No reload needed.
        if _is_auto_install_allowed(project_root, auto_install_libclang):
            auto_install()

        # Still not available? Raise clear error.
        if not is_backend_available(backend_name):
            raise LibclangUnavailableError(
                "libclang shared library not found after auto-install attempt. "
                "Install libclang manually or use a pre-populated cache."
            )

    header, ir_cache_key, ir_slug, ir_from_cache = _resolve_ir()

    output, output_from_cache = _get_output(
        header=header,
        writer_name=writer_name,
        writer_options=writer_options,
        ir_cache_key=ir_cache_key,
        ir_slug=ir_slug,
        resolved_cache_dir=resolved_cache_dir,
        use_output_cache=use_output_cache,
        header_path=header_path,
        code=code,
        include_dirs=include_dirs,
    )

    if output_path is not None:
        _write_output_file(output_path, output)

    if _result_meta is not None:
        _result_meta["from_cache"] = ir_from_cache or output_from_cache

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
    store_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
    auto_install_libclang: bool | None = None,
    target: str | None = None,
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
    :param store_dir: Store directory. When ``None``, falls back to the
        ``HEADERKIT_STORE_DIR`` environment variable, then auto-detects
        ``.headerkit/`` in the project root.
    :param no_cache: Disable all caching.
    :param no_ir_cache: Disable IR cache only.
    :param no_output_cache: Disable output cache only.
    :param auto_install_libclang: Explicitly enable (True) or disable (False)
        automatic libclang installation. Passed through to ``generate()``.
    :param target: Target triple for cross-compilation. Passed through
        to ``generate()``.
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

        meta: dict[str, object] = {}
        output = generate(
            header_path=header_path,
            writer_name=wname,
            backend_name=backend_name,
            include_dirs=include_dirs,
            defines=defines,
            extra_args=extra_args,
            writer_options=wopts,
            output_path=opath,
            store_dir=store_dir,
            no_cache=no_cache,
            no_ir_cache=no_ir_cache,
            no_output_cache=no_output_cache,
            auto_install_libclang=auto_install_libclang,
            target=target,
            _result_meta=meta,
        )

        results.append(
            GenerateResult(
                writer_name=wname,
                output=output,
                output_path=Path(opath) if opath else None,
                from_cache=bool(meta.get("from_cache", False)),
            )
        )

    return results


@dataclass
class _MergedOverrides:
    """Per-header overrides merged from all matching patterns."""

    defines: list[str] | None
    include_dirs: list[str] | None
    backend: str | None
    target: str | None
    extra_args: list[str] | None
    writer_options: dict[str, dict[str, object]]
    output_templates: dict[str, str]


def _merge_pattern_overrides(
    header_path: Path,
    pattern_mapping: dict[Path, list[str]],
    header_overrides: dict[str, dict[str, object]],
    *,
    defaults_defines: list[str] | None,
    defaults_include_dirs: list[str] | None,
    defaults_backend: str | None,
    defaults_target: str | None,
    defaults_extra_args: list[str] | None,
) -> _MergedOverrides:
    """Merge per-pattern overrides for a single header path.

    Combines overrides from all patterns that matched *header_path*,
    applying them on top of the provided defaults.
    """
    merged: dict[str, object] = {}
    matching_patterns = pattern_mapping.get(header_path, [])
    for pat in matching_patterns:
        if pat in header_overrides:
            merged.update(header_overrides[pat])

    # Extract override fields
    override_defines = merged.get("defines")
    effective_defines = list(override_defines) if isinstance(override_defines, list) else defaults_defines
    override_include_dirs = merged.get("include_dirs")
    effective_include_dirs = (
        list(override_include_dirs) if isinstance(override_include_dirs, list) else defaults_include_dirs
    )
    override_backend = merged.get("backend")
    effective_backend = str(override_backend) if isinstance(override_backend, str) else defaults_backend
    override_target = merged.get("target")
    effective_target = str(override_target) if isinstance(override_target, str) else defaults_target
    override_extra_args = merged.get("extra_args")
    effective_extra_args = list(override_extra_args) if isinstance(override_extra_args, list) else defaults_extra_args

    # Extract per-pattern writer_options overrides
    override_writer_options: dict[str, dict[str, object]] = {}
    raw_wo = merged.get("writer_options")
    if isinstance(raw_wo, dict):
        for wname, wopts in raw_wo.items():
            if isinstance(wopts, dict):
                override_writer_options[str(wname)] = dict(wopts)

    # Extract per-pattern output overrides
    pattern_output_templates: dict[str, str] = {}
    raw_output = merged.get("output")
    if isinstance(raw_output, dict):
        for wname, tmpl in raw_output.items():
            if isinstance(tmpl, str):
                pattern_output_templates[str(wname)] = tmpl

    return _MergedOverrides(
        defines=effective_defines,
        include_dirs=effective_include_dirs,
        backend=effective_backend,
        target=effective_target,
        extra_args=effective_extra_args,
        writer_options=override_writer_options,
        output_templates=pattern_output_templates,
    )


def batch_generate(
    *,
    patterns: list[str],
    exclude_patterns: list[str] | None = None,
    writers: list[str] | None = None,
    backend_name: str | None = None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
    extra_args: list[str] | None = None,
    writer_options: dict[str, dict[str, object]] | None = None,
    output_templates: dict[str, str] | None = None,
    store_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
    auto_install_libclang: bool | None = None,
    target: str | None = None,
    project_root: Path | None = None,
    header_overrides: dict[str, dict[str, object]] | None = None,
) -> BatchResult:
    """Generate output for multiple headers resolved from glob patterns.

    Resolves header paths from patterns, applies per-pattern overrides,
    checks for output collisions, then generates output for each
    header/writer combination.

    :param patterns: Glob patterns or literal paths for header selection.
    :param exclude_patterns: Glob patterns for paths to exclude.
    :param writers: List of writer names. Default: ["json"].
    :param backend_name: Backend to use (default: "libclang").
    :param include_dirs: Include directories for parsing.
    :param defines: Preprocessor defines (without -D prefix).
    :param extra_args: Additional backend args.
    :param writer_options: Per-writer kwargs, keyed by writer name.
    :param output_templates: Per-writer output path templates (highest priority).
    :param store_dir: Store directory. When ``None``, falls back to the
        ``HEADERKIT_STORE_DIR`` environment variable, then auto-detects
        ``.headerkit/`` in the project root.
    :param no_cache: Disable all caching.
    :param no_ir_cache: Disable IR cache only.
    :param no_output_cache: Disable output cache only.
    :param auto_install_libclang: Explicitly enable or disable auto-install.
    :param target: Target triple for cross-compilation.
    :param project_root: Project root directory. Auto-detected if not provided.
    :param header_overrides: Per-pattern override dicts (pattern -> config dict).
    :returns: BatchResult with results for each header/writer combo.
    :raises ValueError: If no headers match, output paths collide, or no
        output template is resolvable for a header/writer combo.
    """
    writers = writers or ["json"]
    writer_options = writer_options or {}
    output_templates = output_templates or {}
    header_overrides = header_overrides or {}
    exclude_patterns = exclude_patterns or []

    if project_root is None:
        project_root = _find_project_root(Path.cwd())

    # Resolve header paths from patterns
    sorted_paths, pattern_mapping = resolve_headers(
        patterns=patterns,
        exclude_patterns=exclude_patterns,
        project_root=project_root,
    )

    # Fetch writer instances and their default output patterns once (constant per writer)
    writer_default_patterns: dict[str, str] = {}
    for wn in writers:
        if wn not in output_templates:
            wi = get_writer(wn)
            writer_default_patterns[wn] = _writer_default_output_pattern(wi, wn)

    # Compute per-header overrides once (reused for collision check and generation)
    header_overrides_cache: dict[Path, _MergedOverrides] = {}
    for header_path in sorted_paths:
        header_overrides_cache[header_path] = _merge_pattern_overrides(
            header_path,
            pattern_mapping,
            header_overrides,
            defaults_defines=defines,
            defaults_include_dirs=include_dirs,
            defaults_backend=backend_name,
            defaults_target=target,
            defaults_extra_args=extra_args,
        )

    # Pre-resolve all output paths to check for collisions
    all_resolved_outputs: dict[tuple[Path, str], Path] = {}

    for header_path in sorted_paths:
        overrides = header_overrides_cache[header_path]

        for writer_name in writers:
            # Output template precedence:
            # 1. output_templates arg (CLI -o)
            # 2. per-pattern override "output"
            # 3. writer default via _writer_default_output_pattern()
            template: str | None = output_templates.get(writer_name)
            if template is None:
                template = overrides.output_templates.get(writer_name)
            if template is None:
                template = writer_default_patterns[writer_name]

            resolved = resolve_output_path(template, header_path, project_root)
            all_resolved_outputs[(header_path, writer_name)] = project_root / resolved

    # Check for collisions before any generation
    check_output_collisions(all_resolved_outputs)

    # Generate outputs
    results: list[GenerateResult] = []
    headers_processed = 0

    for header_path in sorted_paths:
        overrides = header_overrides_cache[header_path]

        for writer_name in writers:
            # Merge writer options: global < per-pattern override
            effective_wopts: dict[str, object] = dict(writer_options.get(writer_name, {}))
            if writer_name in overrides.writer_options:
                effective_wopts.update(overrides.writer_options[writer_name])

            # Resolve output path (same precedence as collision check)
            output_path = all_resolved_outputs[(header_path, writer_name)]

            meta: dict[str, object] = {}
            output = generate(
                header_path=header_path,
                writer_name=writer_name,
                code=None,
                backend_name=overrides.backend,
                include_dirs=([str(d) for d in overrides.include_dirs] if overrides.include_dirs is not None else None),
                defines=([str(d) for d in overrides.defines] if overrides.defines is not None else None),
                extra_args=([str(a) for a in overrides.extra_args] if overrides.extra_args is not None else None),
                writer_options=effective_wopts or None,
                output_path=output_path,
                store_dir=store_dir,
                no_cache=no_cache,
                no_ir_cache=no_ir_cache,
                no_output_cache=no_output_cache,
                project_prefixes=(str(header_path.parent),),
                auto_install_libclang=auto_install_libclang,
                target=overrides.target,
                _result_meta=meta,
            )

            results.append(
                GenerateResult(
                    writer_name=writer_name,
                    output=output,
                    output_path=output_path,
                    from_cache=bool(meta.get("from_cache", False)),
                )
            )

        headers_processed += 1

    return BatchResult(
        results=results,
        headers_processed=headers_processed,
        headers_skipped=len(sorted_paths) - headers_processed,
    )
