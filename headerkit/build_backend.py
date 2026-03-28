"""PEP 517 build backend that generates bindings before packaging.

Consumer projects declare this as their build backend. When pip install
or python -m build is run, headerkit generates bindings from cached IR
(or parses with libclang on cache miss), then delegates actual packaging
to an inner backend (hatchling by default).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

logger = logging.getLogger("headerkit.build")

_INNER_BACKEND_MODULE = "hatchling.build"


def _config_bool(config_settings: dict[str, Any] | None, key: str) -> bool:
    """Check if a config_settings key is truthy."""
    if not config_settings:
        return False
    return str(config_settings.get(key, "")).lower() in ("true", "1")


def _get_inner_backend(config_settings: dict[str, Any] | None = None) -> Any:
    """Import and return the inner build backend module.

    Reads inner-backend from config_settings, defaulting to hatchling.build.
    """
    module_name = _INNER_BACKEND_MODULE
    if config_settings and "inner-backend" in config_settings:
        module_name = config_settings["inner-backend"]
    return importlib.import_module(module_name)


def _load_headerkit_config(pyproject_path: Path | None = None) -> dict[str, Any]:
    """Parse [tool.headerkit] from consumer's pyproject.toml.

    Reads pyproject.toml from CWD (or given path), returns the
    [tool.headerkit] section as a dict. Returns empty dict if
    section is missing or file is unreadable.
    """
    if pyproject_path is None:
        pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        return {}
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        tool: dict[str, Any] = data.get("tool", {})
        headerkit_cfg: dict[str, Any] = tool.get("headerkit", {})
        return headerkit_cfg
    except PermissionError as exc:
        logger.warning("Permission denied reading pyproject.toml: %s", exc)
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to read pyproject.toml: %s", exc)
        return {}


def _run_generation(config_settings: dict[str, Any] | None = None) -> None:
    """Run generate_all() for each header in [tool.headerkit.headers].

    Reads config, resolves cache settings from config_settings keys
    (no-cache, no-ir-cache, no-output-cache), and calls generate_all()
    for each header entry.
    """
    from headerkit._generate import generate_all

    config = _load_headerkit_config()
    headers = config.get("headers", {})
    if not headers:
        return

    backend_name = config.get("backend", "libclang")
    writers: list[str] | None = config.get("writers") or None
    global_include_dirs: list[str] = config.get("include_dirs", [])
    global_defines: list[str] = config.get("defines", [])

    no_cache = _config_bool(config_settings, "no-cache")
    no_ir_cache = _config_bool(config_settings, "no-ir-cache")
    no_output_cache = _config_bool(config_settings, "no-output-cache")

    for header_path, entry_config in headers.items():
        entry_defines = global_defines + entry_config.get("defines", [])
        entry_includes = global_include_dirs + entry_config.get("include_dirs", [])
        generate_all(
            header_path=header_path,
            writers=writers,
            backend_name=backend_name,
            include_dirs=entry_includes or None,
            defines=entry_defines or None,
            no_cache=no_cache,
            no_ir_cache=no_ir_cache,
            no_output_cache=no_output_cache,
        )


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Get additional requirements for building a wheel."""
    inner = _get_inner_backend(config_settings)
    if hasattr(inner, "get_requires_for_build_wheel"):
        return inner.get_requires_for_build_wheel(config_settings)  # type: ignore[no-any-return]
    return []


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Get additional requirements for building an sdist."""
    inner = _get_inner_backend(config_settings)
    if hasattr(inner, "get_requires_for_build_sdist"):
        return inner.get_requires_for_build_sdist(config_settings)  # type: ignore[no-any-return]
    return []


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel, generating bindings first."""
    _run_generation(config_settings)
    inner = _get_inner_backend(config_settings)
    return inner.build_wheel(wheel_directory, config_settings, metadata_directory)  # type: ignore[no-any-return]


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Build an sdist, generating bindings first if possible."""
    try:
        _run_generation(config_settings)
    except Exception as exc:
        logger.warning("Generation failed during sdist build (may rely on cache): %s", exc)
    inner = _get_inner_backend(config_settings)
    return inner.build_sdist(sdist_directory, config_settings)  # type: ignore[no-any-return]


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Prepare wheel metadata (no generation needed)."""
    inner = _get_inner_backend(config_settings)
    return inner.prepare_metadata_for_build_wheel(metadata_directory, config_settings)  # type: ignore[no-any-return]


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel, generating bindings first."""
    _run_generation(config_settings)
    inner = _get_inner_backend(config_settings)
    return inner.build_editable(wheel_directory, config_settings, metadata_directory)  # type: ignore[no-any-return]
