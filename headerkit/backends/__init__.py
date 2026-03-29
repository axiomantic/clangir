"""Parser backends for headerkit.

This package contains parser backend implementations that convert C/C++
source code into the headerkit IR (Intermediate Representation).

Available Backends
------------------
libclang
    LLVM clang-based parser with full C++ support. Requires system
    libclang library. Uses vendored clang Python bindings.

Example
-------
::

    from headerkit.backends import get_backend, list_backends

    # Get the default backend
    backend = get_backend()

    # Get a specific backend
    backend = get_backend("libclang")

    # List available backends
    for name in list_backends():
        print(name)
"""

from headerkit.ir import (
    ParserBackend,
)

# Registry of available backends
# Backends are registered lazily to avoid import errors if dependencies are missing
_BACKEND_REGISTRY: dict[str, type[ParserBackend]] = {}
_DEFAULT_BACKEND: str | None = None
_BACKENDS_LOADED: bool = False  # Track if we've tried to load all backends

__all__ = [
    "get_backend",
    "get_backend_info",
    "get_default_backend",
    "is_backend_available",
    "list_backends",
    "register_backend",
    "reload_backends",
]


def register_backend(name: str, backend_class: type[ParserBackend], is_default: bool = False) -> None:
    """Register a parser backend.

    Called by backend modules during import to add themselves to the registry.
    The first registered backend becomes the default unless ``is_default`` is
    explicitly set on a later registration.

    :param name: Unique name for the backend (e.g., ``"libclang"``).
    :param backend_class: Class implementing the :class:`~headerkit.ir.ParserBackend` protocol.
    :param is_default: If True, this becomes the default backend for :func:`get_backend`.
    """
    global _DEFAULT_BACKEND  # pylint: disable=global-statement
    _BACKEND_REGISTRY[name] = backend_class
    if is_default or _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = name


def list_backends() -> list[str]:
    """List names of all registered backends.

    :returns: List of backend names that can be passed to :func:`get_backend`.

    Example
    -------
    ::

        from headerkit.backends import list_backends

        for name in list_backends():
            print(f"Available: {name}")
    """
    _ensure_backends_loaded()
    return list(_BACKEND_REGISTRY.keys())


def is_backend_available(name: str) -> bool:
    """Check if a backend is available for use.

    :param name: Backend name to check.
    :returns: True if the backend is registered and can be instantiated.
    """
    _ensure_backends_loaded()
    return name in _BACKEND_REGISTRY


def get_backend_info() -> list[dict[str, str | bool]]:
    """Get information about all known backends.

    :returns: List of dicts with name, available, default, and description.
    """
    _ensure_backends_loaded()

    descriptions = {
        "libclang": "Full C/C++ support via LLVM",
    }

    result: list[dict[str, str | bool]] = []
    for name in _BACKEND_REGISTRY:
        try:
            _BACKEND_REGISTRY[name]()
            available = True
        except Exception:
            available = False
        result.append(
            {
                "name": name,
                "available": available,
                "default": name == _DEFAULT_BACKEND,
                "description": descriptions.get(name, ""),
            }
        )
    return result


def get_backend(name: str | None = None) -> ParserBackend:
    """Get a parser backend instance.

    Returns a new instance of the requested backend. If no name is provided,
    returns the default backend (libclang).

    :param name: Backend name (e.g., ``"libclang"``),
        or None for the default backend.
    :returns: New instance of the requested backend.
    :raises ValueError: If the requested backend is not available.

    Example
    -------
    ::

        from headerkit.backends import get_backend

        # Get default backend
        backend = get_backend()

        # Get libclang backend
        clang = get_backend("libclang")

        # Parse a header
        header = backend.parse(code, "myheader.h")
    """
    _ensure_backends_loaded()

    if name is None:
        if _DEFAULT_BACKEND is None:
            raise ValueError("No backends available")
        name = _DEFAULT_BACKEND

    if name not in _BACKEND_REGISTRY:
        available = ", ".join(_BACKEND_REGISTRY.keys()) or "(none)"
        raise ValueError(f"Unknown backend: {name!r}. Available: {available}")

    return _BACKEND_REGISTRY[name]()


def get_default_backend() -> str:
    """Get the name of the default backend.

    Returns the name of the currently configured default backend.

    :returns: Backend name (e.g., "libclang").
    :raises ValueError: If no backends are available.

    Example
    -------
    ::

        from headerkit.backends import get_default_backend

        default = get_default_backend()
        print(f"Default backend: {default}")
    """
    _ensure_backends_loaded()

    if _DEFAULT_BACKEND is None:
        raise ValueError("No backends available")
    return _DEFAULT_BACKEND


def reload_backends() -> None:
    """Clear cached backend state and re-discover available backends.

    Call after dynamically installing a backend (e.g. via
    :func:`~headerkit.install_libclang.auto_install`) so that
    :func:`get_backend` can find the newly available backend.
    """
    import sys

    global _BACKENDS_LOADED, _DEFAULT_BACKEND  # pylint: disable=global-statement

    _BACKEND_REGISTRY.clear()
    _DEFAULT_BACKEND = None
    _BACKENDS_LOADED = False
    # Force fresh import of backend modules so registration re-runs
    sys.modules.pop("headerkit.backends.libclang", None)
    _ensure_backends_loaded()


def _ensure_backends_loaded() -> None:
    """Lazily load backend modules to populate the registry.

    NOTE: Managed circular import pattern.
    This module and headerkit.backends.libclang have a circular dependency:
    - This module defines the registry functions
    - libclang.py imports register_backend from here at load time
    - _ensure_backends_loaded() in this module imports libclang lazily
    This is intentional. Do not restructure without understanding the full cycle.

    With vendored cindex bindings, the import always succeeds. The failure
    mode is that libclang's shared library is not found during configuration,
    in which case the backend simply does not register itself.
    """
    global _BACKENDS_LOADED  # pylint: disable=global-statement

    if _BACKENDS_LOADED:
        return

    _BACKENDS_LOADED = True

    # Import triggers module-level registration if libclang is available
    try:
        import headerkit.backends.libclang  # noqa: F401 (side effect import)
    except ImportError:
        import logging

        logging.getLogger(__name__).debug("Could not import libclang backend", exc_info=True)

    if not _BACKEND_REGISTRY:
        import sys as _sys

        from headerkit._clang._version import detect_llvm_version

        version = detect_llvm_version()

        if _sys.platform == "win32":
            install_advice = (
                "Download from https://github.com/llvm/llvm-project/releases or run: winget install LLVM.LLVM"
            )
        elif _sys.platform == "darwin":
            install_advice = "brew install llvm"
        else:
            install_advice = (
                f"apt install libclang-{version}-dev (Ubuntu) or dnf install clang-devel (Fedora)"
                if version
                else "apt install libclang-dev (Ubuntu) or dnf install clang-devel (Fedora)"
            )

        if version:
            hint = f"libclang {version} detected but shared library not found.\nInstall: {install_advice}"
        else:
            hint = f"No LLVM/clang installation found.\nInstall: {install_advice}"
        import warnings

        warnings.warn(f"No parser backends available. {hint}", stacklevel=2)


def _load_backend_plugins() -> None:
    """Load backend plugins registered via entry points.

    Called explicitly by the CLI before invoking the backend.
    NOT called from _ensure_backends_loaded() to preserve test hermeticity.

    Plugin authoring contract: the entry point value must be a module path.
    ep.load() imports the module, which calls register_backend() at module bottom.
    Backend registration silently replaces existing backends with the same name.
    """
    import importlib.metadata

    _ensure_backends_loaded()
    for ep in importlib.metadata.entry_points(group="headerkit.backends"):
        try:
            ep.load()
        except (ImportError, ValueError) as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to load backend plugin %r: %s", ep.name, exc)
