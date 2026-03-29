"""PEP 517 build backend with automatic libclang installation.

This module wraps ``headerkit.build_backend`` and sets the
``HEADERKIT_AUTO_INSTALL_LIBCLANG=1`` environment variable before
delegating to it. Consumer projects that want libclang to be
automatically installed during builds can use this as their build
backend::

    [build-system]
    requires = ["headerkit", "hatchling"]
    build-backend = "headerkit.build_backend_auto"

For projects that do not want automatic installation, use the standard
``headerkit.build_backend`` instead.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar

import headerkit.build_backend as _inner

_F = TypeVar("_F", bound=Callable[..., Any])


def _ensure_auto_install_env(func: _F) -> _F:
    """Decorator: set HEADERKIT_AUTO_INSTALL_LIBCLANG=1 before calling *func*."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        os.environ.setdefault("HEADERKIT_AUTO_INSTALL_LIBCLANG", "1")
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


@_ensure_auto_install_env
def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Get additional requirements for building a wheel."""
    return _inner.get_requires_for_build_wheel(config_settings)


@_ensure_auto_install_env
def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    """Get additional requirements for building an sdist."""
    return _inner.get_requires_for_build_sdist(config_settings)


@_ensure_auto_install_env
def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel with automatic libclang installation."""
    return _inner.build_wheel(wheel_directory, config_settings, metadata_directory)


@_ensure_auto_install_env
def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Build an sdist with automatic libclang installation."""
    return _inner.build_sdist(sdist_directory, config_settings)


@_ensure_auto_install_env
def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Prepare wheel metadata (no generation needed)."""
    return _inner.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


@_ensure_auto_install_env
def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel with automatic libclang installation."""
    return _inner.build_editable(wheel_directory, config_settings, metadata_directory)
