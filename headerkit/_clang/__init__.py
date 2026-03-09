"""Vendored clang Python bindings with version-aware loader.

This package vendors clang cindex.py from multiple LLVM release tags
and selects the correct version based on the system's installed LLVM.

Usage::

    from headerkit._clang import get_cindex

    cindex = get_cindex()
    # cindex is the appropriate cindex module for your system LLVM
"""

import ctypes
import importlib
import platform
import warnings
from ctypes import c_void_p
from types import ModuleType

_NEEDS_INTEROP_STRING_PATCH = platform.python_implementation() != "CPython"

_cached_cindex: ModuleType | None = None

VENDORED_VERSIONS = ("18", "19", "20", "21", "22",)
LATEST_VENDORED = "22"
OLDEST_VENDORED = "18"


class _compat_c_interop_string(c_void_p):
    """PyPy-compatible replacement for c_interop_string.

    The vendored cindex.py defines c_interop_string as a subclass of
    ctypes.c_char_p, but PyPy does not support subclassing c_char_p.
    This replacement uses c_void_p as the base and manually manages
    string buffer storage to provide an identical API.
    """

    _buffer: ctypes.Array[ctypes.c_char] | None = None

    def __init__(self, p: str | bytes | None = None) -> None:
        if p is None:
            p = b""
        if isinstance(p, str):
            p = p.encode("utf8")
        buf = ctypes.create_string_buffer(p)
        super().__init__(ctypes.cast(buf, c_void_p).value)
        self._buffer = buf  # prevent GC

    def __str__(self) -> str:
        return self.value or ""

    def _get_raw_ptr(self) -> int | None:
        """Read the raw void pointer value from the c_void_p base."""
        return super().value

    @property
    def value(self) -> str | None:  # type: ignore[override]
        ptr = self._get_raw_ptr()
        if ptr is None:
            return None
        return ctypes.string_at(ptr).decode("utf8")

    @classmethod
    def from_param(cls, param: str | bytes | None) -> "_compat_c_interop_string":
        if isinstance(param, str):
            return cls(param)
        if isinstance(param, bytes):
            return cls(param)
        if param is None:
            return cls(param)
        raise TypeError(f"Cannot convert '{type(param).__name__}' to '{cls.__name__}'")

    @staticmethod
    def to_python_string(x: "_compat_c_interop_string") -> str | None:
        return x.value


def get_cindex() -> ModuleType:
    """Load the appropriate vendored cindex module for the system's LLVM version.

    Detection order:
    1. CIR_CLANG_VERSION env var (explicit override)
    2. llvm-config --version (most reliable)
    3. clang -dM -E to get __clang_major__ (works for Apple clang)
    4. Fallback to latest vendored version with warning

    Returns the cindex module ready for use. The result is cached.
    """
    global _cached_cindex
    if _cached_cindex is not None:
        return _cached_cindex

    from headerkit._clang._version import detect_llvm_version

    version = detect_llvm_version()

    if version is None:
        warnings.warn(
            f"Could not detect LLVM version. Using vendored cindex for LLVM {LATEST_VENDORED}. "
            f"Set CIR_CLANG_VERSION to override.",
            stacklevel=2,
        )
        version = LATEST_VENDORED

    if version not in VENDORED_VERSIONS:
        try:
            v_int = int(version)
        except ValueError:
            warnings.warn(f"Invalid LLVM version {version!r}, falling back to {LATEST_VENDORED}", stacklevel=2)
            version = LATEST_VENDORED
            v_int = int(version)
        oldest_int = int(OLDEST_VENDORED)
        latest_int = int(LATEST_VENDORED)

        if v_int < oldest_int:
            warnings.warn(
                f"LLVM {version} is older than oldest vendored version ({OLDEST_VENDORED}). "
                f"Using {OLDEST_VENDORED}. Set CIR_CLANG_VERSION to override.",
                stacklevel=2,
            )
            version = OLDEST_VENDORED
        elif v_int > latest_int:
            warnings.warn(
                f"LLVM {version} is newer than latest vendored version ({LATEST_VENDORED}). "
                f"Using {LATEST_VENDORED}. Set CIR_CLANG_VERSION to override.",
                stacklevel=2,
            )
            version = LATEST_VENDORED

    cindex = importlib.import_module(f"headerkit._clang.v{version}.cindex")

    if _NEEDS_INTEROP_STRING_PATCH:
        cindex.c_interop_string = _compat_c_interop_string  # type: ignore[attr-defined]

    _cached_cindex = cindex
    return cindex
