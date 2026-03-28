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
from headerkit._config import _TOML_DECODE_ERROR, _parse_toml
from headerkit._slug import build_slug, load_index, lookup_slug
from headerkit.backends import get_backend
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


def _find_project_root(start: Path) -> Path:
    """Find project root by walking up from *start* looking for ``.git``.

    Falls back to *start* itself if no ``.git`` marker is found before
    reaching the filesystem root or the user's home directory.
    """
    current = start.resolve()
    home = Path.home().resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent or current == home:
            return start
        current = current.parent


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
    )

    ir_slug = build_slug(
        backend_name=backend_name,
        header_path=str(header_path),
        defines=parsed_args.defines,
        includes=parsed_args.includes,
        other_args=parsed_args.other_args,
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
        all_args: list[str] = []
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
                )
            except OSError as exc:
                logger.warning("Failed to write IR cache entry: %s", exc)

    return header, ir_cache_key, ir_slug, ir_from_cache


def _get_output(
    *,
    header: Header,
    writer_name: str,
    writer_options: dict[str, object],
    ir_cache_key: str,
    ir_slug: str,
    resolved_cache_dir: Path | None,
    use_output_cache: bool,
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
    cache_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
    project_prefixes: tuple[str, ...] | None = None,
    auto_install_libclang: bool | None = None,
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
    :param cache_dir: Cache directory (default: .hkcache/ in project root).
    :param no_cache: Disable all caching.
    :param no_ir_cache: Disable IR cache only.
    :param no_output_cache: Disable output cache only.
    :param project_prefixes: Tuple of project prefix directories for backend.
    :param auto_install_libclang: Explicitly enable (True) or disable (False)
        automatic libclang installation. When None, falls back to the
        ``HEADERKIT_AUTO_INSTALL_LIBCLANG`` env var, then pyproject.toml
        config, then defaults to False.
    :returns: Generated output string.
    :raises FileNotFoundError: If header_path does not exist and code is not provided.
    """
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

    parsed_args = parse_extra_args(extra_args, include_dirs, defines)
    project_root = _find_project_root(header_path.parent)
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
        )

    try:
        header, ir_cache_key, ir_slug, ir_from_cache = _resolve_ir()
    except ValueError:
        # Backend unavailable -- try output cache before giving up
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
            )
            if cached_output is not None:
                if output_path is not None:
                    _write_output_file(output_path, cached_output)
                if _result_meta is not None:
                    _result_meta["from_cache"] = True
                return cached_output

        # Output cache miss -- attempt auto-install for libclang backend
        if (
            backend_name == "libclang"
            and _is_auto_install_allowed(project_root, auto_install_libclang)
            and auto_install()
        ):
            logger.info("libclang auto-installed; retrying backend")
            header, ir_cache_key, ir_slug, ir_from_cache = _resolve_ir()
        else:
            raise

    output, output_from_cache = _get_output(
        header=header,
        writer_name=writer_name,
        writer_options=writer_options,
        ir_cache_key=ir_cache_key,
        ir_slug=ir_slug,
        resolved_cache_dir=resolved_cache_dir,
        use_output_cache=use_output_cache,
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
    cache_dir: str | Path | None = None,
    no_cache: bool = False,
    no_ir_cache: bool = False,
    no_output_cache: bool = False,
    auto_install_libclang: bool | None = None,
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
    :param auto_install_libclang: Explicitly enable (True) or disable (False)
        automatic libclang installation. Passed through to ``generate()``.
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
            cache_dir=cache_dir,
            no_cache=no_cache,
            no_ir_cache=no_ir_cache,
            no_output_cache=no_output_cache,
            auto_install_libclang=auto_install_libclang,
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
