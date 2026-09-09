"""libclang-based parser backend.

This backend uses libclang (LLVM's C/C++ parser) to parse header files.
It provides full C/C++ support including templates, namespaces, and classes.

Requirements
------------
* System libclang library must be installed
* headerkit includes vendored clang Python bindings that are version-matched
  automatically to the system LLVM version

The backend class is always registered.  If system libclang is not
available, the error surfaces when the backend is actually used (e.g.
when ``parse()`` is called), not at import time.

Advantages
----------
* Full C++ support (classes, templates, namespaces)
* Handles complex preprocessor constructs
* Uses the same parser as production compilers
* Better error messages with source locations

Limitations
-----------
* Macro extraction is limited due to Python bindings constraints
* Requires system libclang installation

Example
-------
::

    from headerkit.backends.libclang import LibclangBackend

    backend = LibclangBackend()
    header = backend.parse(code, "myheader.hpp", extra_args=["-std=c++17"])
"""

from __future__ import annotations

import contextlib
import fnmatch
import functools
import glob
import os
import re
import subprocess
import sys
from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from headerkit._clang import get_cindex as _get_cindex
from headerkit.backends import (
    register_backend,
)
from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    Array,
    BaseSpecifier,
    Constant,
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    ParserBackend,
    Pointer,
    Reference,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)

# Module-level references populated by _configure_libclang()
_cindex: Any = None
CursorKind: Any = None
TypeKind: Any = None

#: How far :meth:`ClangASTConverter._member_natural_align` recurses through
#: nested aggregates before giving up. Only a pathological header nests deeper,
#: and a bound is what keeps a cyclic canonical type from looping.
_NATURAL_ALIGN_MAX_DEPTH = 8

#: Returned when the recursion bound above was reached, so the caller can tell
#: "no alignment to impose" from "did not finish looking". They are not the
#: same answer and collapsing them reports a packed record as unpacked.
_NATURAL_ALIGN_UNKNOWN = -1

#: Probe used to measure whether the target gives an unnamed bit-field's
#: declared type alignment to the enclosing record. ``a`` and ``b`` pin the
#: record's own alignment to 1, so anything above 1 came from the bit-field.
_UNNAMED_BITFIELD_PROBE = "struct _hk_abi_probe { char a; unsigned int : 8; char b; };\n"

#: Index owning every ABI probe translation unit. Held for the life of the
#: process because clang frees a TU's memory with the index that made it.
_PROBE_INDEX: Any = None


def _get_probe_index() -> Any:
    """The shared index for ABI probes, created on first use."""
    global _PROBE_INDEX
    if _PROBE_INDEX is None:
        _PROBE_INDEX = _cindex.Index.create()
    return _PROBE_INDEX


@functools.lru_cache(maxsize=256)
def _unnamed_bitfields_impose_alignment(args: tuple[str, ...], is_cplus: bool) -> bool | None:
    """Whether this target gives an unnamed bit-field's alignment to its record.

    Measured with clang rather than assumed, because the answer is an ABI
    choice and does not follow from the architecture alone. Compiled with one
    clang, ``struct { char a; unsigned int : 8; char b; }`` is size 3 align 1
    under the Itanium C++ ABI (x86-64 System V, Darwin arm64, riscv64,
    powerpc64le) and size 4 align 4 under AAPCS (aarch64-linux-gnu,
    armv7-linux-gnueabihf, arm-none-eabi).

    Getting this wrong is not merely cosmetic. On a target that does impose the
    alignment, a ``#pragma pack(1)`` record with an anonymous bit-field really
    is packed -- it measures 3 where the unpacked record measures 4 -- and
    excluding the bit-field puts the natural figure at 1, hides the packing,
    and emits a binding whose members are at the wrong offsets.

    Returns None when the probe could not be measured -- a parse error, or a
    clang that will not accept the arguments -- which the caller records rather
    than silently taking one ABI's answer for the other's. The result is cached
    per argument set, so a wrong answer would otherwise be pinned for the life
    of the process.
    """
    try:
        name = "_hk_abi_probe.cpp" if is_cplus else "_hk_abi_probe.c"
        # The index must outlive the translation unit it produces: clang frees
        # a TU's memory with its index, so a temporary one leaves the cursors
        # below pointing into freed storage. Measured as a segmentation fault
        # after roughly three subsequent parses in the same process.
        tu = _get_probe_index().parse(
            name,
            args=list(args),
            unsaved_files=[(name, _UNNAMED_BITFIELD_PROBE)],
        )
        if any(d.severity >= _cindex.Diagnostic.Error for d in tu.diagnostics):
            return None
        for cursor in tu.cursor.get_children():
            if cursor.spelling == "_hk_abi_probe":
                return bool(cursor.type.get_align() > 1)
    except Exception:  # noqa: BLE001 -- any clang failure means "not measured"
        return None
    return None


def normalize_path(path: str) -> str:
    """Normalize a file path for consistent comparison.

    Converts backslashes to forward slashes and lowercases the entire
    path. This enables platform-agnostic path comparison, particularly
    for Windows paths where separators and case are inconsistent.

    :param path: File path to normalize.
    :returns: Normalized path string.
    """
    return path.replace("\\", "/").lower()


def _resolve_path(path: str, search_dirs: Sequence[str] = ()) -> str:
    """Resolve a path to an absolute, symlink-free, comparison-ready form.

    An absolute path is resolved directly.  A relative path is tried against each
    entry of ``search_dirs`` in order and resolved against the first directory
    where it names an existing file; if none does, it is resolved against the
    current working directory.  Case and separators are normalized last so the
    result compares correctly on Windows.

    :param path: Path to resolve; may be absolute or relative.
    :param search_dirs: Directories to try, in priority order, for a relative path.
    :returns: Normalized absolute path.
    """
    if not os.path.isabs(path):
        for directory in search_dirs:
            candidate = os.path.join(directory, path)
            if os.path.exists(candidate):
                path = candidate
                break
    return normalize_path(os.path.realpath(os.path.abspath(path)))


def _filter_search_dirs(filename: str, include_dirs: Sequence[str] | None) -> list[str]:
    """Directories a relative allowlist/denylist entry is resolved against, in priority order.

    The main file's own directory comes first, because an entry naming a bare
    header basename (the common case) means "the header sitting next to the file
    being parsed".  The include search path follows, then the process cwd via the
    fallback in :func:`_resolve_path`.
    """
    dirs = [os.path.dirname(os.path.abspath(filename)) or os.getcwd()]
    if include_dirs:
        dirs.extend(include_dirs)
    return dirs


_GLOB_METACHARACTERS = ("*", "?", "[")


class _PathSet:
    """A resolved set of allowlist/denylist entries, matched against resolved paths.

    An entry containing a glob metacharacter (``*``, ``?``, ``[``) becomes an
    :mod:`fnmatch` pattern; every other entry becomes one exact path.  Both are
    resolved by the rule in :func:`_resolve_path` before any comparison, so a
    match is always between two absolute, symlink-free, normalized paths and is
    never a substring test: ``er.h`` does not match ``other.h``.

    A glob entry's *directory* part is resolved the same way a plain entry is;
    only its final component stays a pattern.  Resolving the directory rather
    than pattern-matching it is what lets a pattern survive a symlinked parent
    (``/tmp`` -> ``/private/tmp`` on macOS), where a pattern built from the
    unresolved spelling would silently match nothing.
    """

    def __init__(self, entries: Sequence[str], search_dirs: Sequence[str]) -> None:
        self.exact: set[str] = set()
        self.patterns: list[str] = []
        for entry in entries:
            head, tail = os.path.split(entry)
            if any(char in tail for char in _GLOB_METACHARACTERS):
                bases = [_resolve_path(head, search_dirs)] if head else [_resolve_path(d) for d in search_dirs]
                self.patterns.extend(normalize_path(os.path.join(base, tail)) for base in bases)
            else:
                self.exact.add(_resolve_path(entry, search_dirs))

    def matches(self, resolved_path: str) -> bool:
        """True if ``resolved_path`` (already run through :func:`_resolve_path`) is named."""
        if resolved_path in self.exact:
            return True
        return any(fnmatch.fnmatchcase(resolved_path, pattern) for pattern in self.patterns)


def _get_xcrun_libclang_paths() -> list[str]:
    """Use xcrun to locate libclang on macOS.

    ``xcrun --find clang`` returns the path to the active clang binary, which
    lives alongside libclang inside the active Xcode toolchain.  This handles
    non-standard Xcode install locations (e.g. ``Xcode_16.2.app`` on GitHub
    Actions runners) that static glob patterns miss.
    """
    import shutil

    if not shutil.which("xcrun"):
        return []

    try:
        result = subprocess.run(
            ["xcrun", "--find", "clang"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            clang_path = result.stdout.strip()
            if not clang_path:
                return []
            # clang_path is e.g. /.../usr/bin/clang; libclang.dylib is in /.../usr/lib/
            bin_dir = os.path.dirname(clang_path)
            lib_dir = os.path.normpath(os.path.join(bin_dir, "..", "lib"))
            candidate = os.path.join(lib_dir, "libclang.dylib")
            if candidate not in ("",):
                return [candidate]
    except (subprocess.SubprocessError, OSError):
        pass

    return []


def _add_versioned_so_paths(paths: list[str], base_dir: str) -> None:
    """Append versioned libclang .so candidates from *base_dir* to *paths*.

    Searches for ``libclang.so.*`` and ``libclang-*.so`` (sorted newest-first),
    then appends the unversioned ``libclang.so`` as a fallback.

    Uses ``/`` explicitly since these are always Linux paths.
    """
    for pattern in ("libclang.so.*", "libclang-*.so"):
        paths.extend(sorted(glob.glob(f"{base_dir}/{pattern}"), reverse=True))
    paths.append(f"{base_dir}/libclang.so")


def _get_libclang_search_paths() -> list[str]:
    """Get platform-specific paths to search for libclang.

    Returns a list of candidate paths where libclang might be installed,
    ordered by preference (most common/preferred locations first).
    """
    paths: list[str] = []

    if sys.platform == "darwin":
        # Homebrew on Apple Silicon (most common modern setup)
        paths.append("/opt/homebrew/opt/llvm/lib/libclang.dylib")
        # Homebrew versioned installs on Apple Silicon (sorted newest first)
        paths.extend(sorted(glob.glob("/opt/homebrew/Cellar/llvm/*/lib/libclang.dylib"), reverse=True))
        # Homebrew on Intel Macs
        paths.append("/usr/local/opt/llvm/lib/libclang.dylib")
        paths.extend(sorted(glob.glob("/usr/local/Cellar/llvm/*/lib/libclang.dylib"), reverse=True))
        # Xcode Command Line Tools
        paths.append("/Library/Developer/CommandLineTools/usr/lib/libclang.dylib")
        # Xcode.app (canonical path)
        paths.append(
            "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libclang.dylib"
        )
        # Xcode versioned app bundles (e.g. Xcode_16.2.app on GitHub Actions runners)
        paths.extend(
            sorted(
                glob.glob(
                    "/Applications/Xcode*.app/Contents/Developer/Toolchains"
                    "/XcodeDefault.xctoolchain/usr/lib/libclang.dylib"
                ),
                reverse=True,
            )
        )
        # xcrun-based discovery (works for any Xcode installation)
        paths.extend(_get_xcrun_libclang_paths())

    elif sys.platform == "linux":
        # Debian/Ubuntu versioned LLVM packages (sorted newest first)
        paths.extend(sorted(glob.glob("/usr/lib/llvm-*/lib/libclang.so*"), reverse=True))
        # RHEL/Fedora/CentOS (64-bit) -- versioned names from clang-libs
        _add_versioned_so_paths(paths, "/usr/lib64")
        # Generic Linux -- also check versioned
        _add_versioned_so_paths(paths, "/usr/lib")
        paths.append("/usr/local/lib/libclang.so")

    elif sys.platform == "win32":
        # Official LLVM installer (use env vars, not hardcoded paths)
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        paths.append(os.path.join(program_files, "LLVM", "bin", "libclang.dll"))
        paths.append(os.path.join(program_files_x86, "LLVM", "bin", "libclang.dll"))

        # Scoop package manager (per-user install)
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            paths.append(os.path.join(userprofile, "scoop", "apps", "llvm", "current", "bin", "libclang.dll"))

        # MSYS2 environments (default install location; MSYS2 does not expose
        # a reliable environment variable for its install dir outside MSYS2 shells)
        for msys2_env in ("mingw64", "ucrt64", "clang64"):
            paths.append(os.path.join("C:\\msys64", msys2_env, "bin", "libclang.dll"))

    # pip install libclang: the PyPI package installs to site-packages/clang/native/
    try:
        import importlib.util

        spec = importlib.util.find_spec("clang")
        if spec and spec.submodule_search_locations:
            native_dir = os.path.join(spec.submodule_search_locations[0], "native")
            if os.path.isdir(native_dir):
                lib_patterns = {"win32": "libclang*.dll", "darwin": "libclang*.dylib"}
                pattern = lib_patterns.get(sys.platform, "libclang*.so*")
                paths.extend(glob.glob(os.path.join(native_dir, pattern)))
    except (ImportError, ValueError):
        pass

    return paths


def _reset_cindex_config() -> None:
    """Reset vendored cindex Config state so library search re-runs.

    The vendored cindex caches library state in several places:
    - ``Config.loaded`` -- marks library as loaded
    - ``Config.library_file`` / ``Config.library_path`` -- cached location
    - ``conf.lib`` -- CachedProperty holding the CDLL handle

    This function clears all of them so a subsequent
    ``_configure_libclang()`` call searches from scratch.  This is needed
    after ``auto_install()`` puts libclang on disk -- without the reset,
    the vendored cindex would still think no library is available.

    DO NOT modify vendored code; this works around it externally.
    """
    from headerkit._clang import _cached_cindex

    if _cached_cindex is None:
        return

    _cindex_conf = _cached_cindex.conf
    if "lib" in _cindex_conf.__dict__:
        del _cindex_conf.__dict__["lib"]

    _cached_cindex.Config.loaded = False
    _cached_cindex.Config.library_file = None
    _cached_cindex.Config.library_path = None


def _configure_libclang() -> bool:
    """Configure vendored cindex to find libclang library.

    Loads the vendored cindex module (triggering version detection),
    then attempts default loading first (respects DYLD_LIBRARY_PATH,
    LD_LIBRARY_PATH, etc.), then searches common platform-specific locations.

    Iterates through all candidate paths until one loads successfully.  This
    handles cross-architecture scenarios (e.g. an x86_64 process on an Apple
    Silicon Mac where the first candidate is an arm64-only Homebrew dylib that
    passes ``os.path.isfile()`` but fails ``cdll.LoadLibrary()``).

    Disables the compatibility check so that minor version mismatches between
    the vendored bindings and the system libclang do not cause hard failures.
    Functions present in the bindings but absent from the loaded library are
    silently skipped; they raise AttributeError only if actually called.

    This function does NOT cache its result.  Each call re-checks whether
    the library is loadable (resetting vendored cindex state first if the
    library was not already loaded).  This allows ``auto_install()`` to
    put libclang on disk and have the next call naturally find it.

    :returns: True if libclang is available and configured, False otherwise.
    """
    global _cindex, CursorKind, TypeKind

    _cindex = _get_cindex()

    # If the library is already loaded, just return True -- the library
    # will not disappear during the process lifetime.
    if _cindex.Config.loaded:
        CursorKind = _cindex.CursorKind
        TypeKind = _cindex.TypeKind
        return True

    # Reset any stale cindex state from a previous failed attempt so that
    # set_library_file() and get_cindex_library() can be called again.
    _reset_cindex_config()

    # Re-fetch cindex after reset (module reference is stable, but refresh
    # to be safe).
    _cindex = _get_cindex()

    # Disable the strict compatibility check before the library is loaded.
    # headerkit already selects the closest vendored bindings for the detected
    # LLVM version, but minor version differences (e.g., v21 bindings with a
    # v19 libclang) can introduce functions that exist in the bindings but not
    # in the library.  With the check disabled, those functions are silently
    # skipped during registration and only raise if actually called.
    _cindex.Config.set_compatibility_check(False)

    CursorKind = _cindex.CursorKind
    TypeKind = _cindex.TypeKind

    # First, try default loading (respects environment variables)
    try:
        _cindex.Config().get_cindex_library()
        return True
    except _cindex.LibclangError:
        pass

    # Default failed, iterate through all candidate paths and try loading each.
    # A path may exist on disk but fail to load (e.g. arm64-only dylib loaded
    # from an x86_64 process under Rosetta), so we must try the next candidate.
    for candidate in _get_libclang_search_paths():
        if not os.path.isfile(candidate):
            continue
        # On Windows, register the DLL's directory so dependent DLLs can be found
        if sys.platform == "win32":
            candidate_dir = os.path.dirname(candidate)
            with contextlib.suppress(OSError):
                os.add_dll_directory(candidate_dir)
        _cindex.Config.set_library_file(candidate)
        try:
            _cindex.Config().get_cindex_library()
            return True
        except _cindex.LibclangError:
            # Reset library_file so the next candidate can be tried.
            # Config.loaded is only set when the .lib property is accessed
            # (i.e. on success), so set_library_file() remains callable here.
            _cindex.Config.library_file = None
            continue

    return False


def is_system_libclang_available() -> bool:
    """Check if the system libclang library is available.

    The vendored cindex bindings are always present, but they require the
    system libclang shared library (libclang.so/dylib) to function.
    This checks if that library can be loaded.

    If libclang is not in the default library search path, this function
    automatically searches common platform-specific locations:

    - macOS: Homebrew (Apple Silicon and Intel), Xcode Command Line Tools
    - Linux: /usr/lib/llvm-*/lib, /usr/lib64, /usr/lib, /usr/local/lib
    - Windows: C:\\Program Files\\LLVM\\bin

    :returns: True if system libclang is available and can be used.
    """
    return _configure_libclang()


# Cache for system include directories (computed once per process)
_system_include_cache_c: list[str] | None = None
_system_include_cache_cxx: list[str] | None = None


def get_system_include_dirs(cplus: bool = False) -> list[str]:
    """Get system include directories by querying the system clang compiler.

    This runs ``clang -v -x c -E /dev/null`` (or ``-x c++`` for C++) and
    parses the include paths from its output. The result is cached for
    subsequent calls.

    :param cplus: If True, query for C++ includes (includes libc++ paths).
    :returns: List of ``-I<path>`` arguments for system include directories.
        Returns empty list if clang is not available or detection fails.
    """
    global _system_include_cache_c, _system_include_cache_cxx  # noqa: PLW0603

    cache = _system_include_cache_cxx if cplus else _system_include_cache_c
    if cache is not None:
        return cache

    result_cache: list[str] = []

    try:
        # Use /dev/null on Unix, NUL on Windows
        null_file = "NUL" if sys.platform == "win32" else "/dev/null"
        lang = "c++" if cplus else "c"
        result = subprocess.run(
            ["clang", "-v", "-x", lang, "-E", null_file],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Parse the include paths from stderr
        in_includes = False
        for line in result.stderr.splitlines():
            if "#include <...> search starts here:" in line:
                in_includes = True
                continue
            if in_includes:
                if line.startswith("End of search list"):
                    break
                path = line.strip()
                if path and not path.endswith("(framework directory)"):
                    # Use -isystem for system includes to give local includes priority
                    result_cache.append(f"-isystem{path}")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if cplus:
        _system_include_cache_cxx = result_cache
    else:
        _system_include_cache_c = result_cache

    return result_cache


def _location_is_in_system_header(tu: Any, file_obj: Any) -> bool:
    """Ask clang whether it treated ``file_obj`` as a system header.

    Backed by ``clang_Location_isInSystemHeader``, which reports how clang itself
    classified the include search path the file was found on. The vendored bindings
    expose ``is_in_system_header`` on every supported LLVM version; a binding that
    does not answers False and leaves the decision to the path heuristic.

    :param tu: Translation unit the file belongs to.
    :param file_obj: A cindex ``File`` from the translation unit.
    :returns: True if clang classified the file as a system header.
    """
    try:
        location = _cindex.SourceLocation.from_offset(tu, file_obj, 0)
        return bool(location.is_in_system_header)
    except (AttributeError, TypeError, ValueError):
        return False


def _matches_project_prefix(header_path: str, project_prefixes: tuple[str, ...] | None) -> bool:
    """Check whether a header was explicitly allowlisted as a project header.

    An allowlisted path overrides every system classification, including
    clang's own, so that an umbrella header installed under a system
    location can still be descended into.

    :param header_path: Path to the header file.
    :param project_prefixes: Path prefixes to treat as project, or None.
    :returns: True if the path is under one of the prefixes.
    """
    if not project_prefixes:
        return False

    path_str = normalize_path(str(header_path))
    for prefix in project_prefixes:
        normalized_prefix = normalize_path(prefix)
        if path_str == normalized_prefix or path_str.startswith(normalized_prefix.rstrip("/") + "/"):
            return True
    return False


# Directory names that mark a compiler's own bundled include tree. The resource
# directory is versioned (``lib/clang/19/include``, ``gcc/x86_64-linux-gnu/13/include``),
# so the marker and the ``include`` component are not adjacent and a fixed
# ``clang/include`` fragment never matches on Linux.
_COMPILER_RESOURCE_MARKERS = frozenset({"clang", "gcc", "g++", "c++"})

# How many components may separate the marker from the ``include`` component.
# ``gcc/<triple>/<version>/include`` is the longest real form.
_COMPILER_RESOURCE_WINDOW = 3

_COMPILER_INCLUDE_DIRS = frozenset({"include", "include-fixed"})


def _is_compiler_resource_dir(path_str: str) -> bool:
    """Check whether a normalized path lies inside a compiler's bundled include tree.

    :param path_str: Path already passed through :func:`normalize_path`.
    :returns: True if a compiler marker directory precedes an include directory.
    """
    parts = path_str.split("/")
    for index, part in enumerate(parts):
        if part not in _COMPILER_INCLUDE_DIRS:
            continue
        window_start = max(0, index - _COMPILER_RESOURCE_WINDOW)
        if _COMPILER_RESOURCE_MARKERS & set(parts[window_start:index]):
            return True
    return False


def _is_system_header(header_path: str, project_prefixes: tuple[str, ...] | None = None) -> bool:
    """Check if a header path is a system header.

    System headers are identified by:
    - Being in /usr/include, /usr/local/include
    - Being in SDK paths (MacOSX.sdk, etc.)
    - Being in a compiler's own versioned resource directory
      (``lib/clang/19/include``, ``lib/gcc/<triple>/13/include``)
    - Being in framework directories

    This is a path heuristic and therefore always one platform behind. When a
    translation unit is available, :meth:`LibclangBackend._is_system_include`
    consults clang's own classification first and falls back to this function.

    Headers can be allowlisted as "project" headers using project_prefixes.
    This is useful for umbrella headers where the library is installed in
    a system location but we want to recursively parse its sub-headers.

    :param header_path: Path to the header file
    :param project_prefixes: Optional tuple of path prefixes to treat as project (not system)
    :returns: True if this is a system header
    """
    path_str = normalize_path(str(header_path))

    # Check project prefixes first - if path matches, it's NOT a system header
    if _matches_project_prefix(header_path, project_prefixes):
        return False

    # System header locations that are absolute path prefixes
    system_path_prefixes = (
        "/usr/include",
        "/usr/local/include",
        "/opt/homebrew/",
        "/opt/local/",
        "/system/library/frameworks",
        "/library/developer/commandlinetools",
        # Linux distribution toolchains ship their resource dirs here.
        "/usr/lib/gcc/",
        "/usr/lib64/gcc/",
        "/usr/lib/clang/",
        "/usr/lib64/clang/",
    )

    # System header path fragments that can appear anywhere in the path
    system_path_fragments = (
        ".sdk/",
        # Windows: LLVM and Windows SDK paths
        "program files/llvm/",
        "program files (x86)/llvm/",
        "windows kits/",
        "/vc/tools/msvc/",
        "microsoft visual studio/",
        # MSYS2 environments
        "msys64/mingw64/",
        "msys64/ucrt64/",
        "msys64/clang64/",
    )

    for prefix in system_path_prefixes:
        normalized = prefix.rstrip("/") + "/"
        if path_str.startswith(normalized) or path_str == prefix:
            return True

    for fragment in system_path_fragments:
        if fragment in path_str:
            return True

    return _is_compiler_resource_dir(path_str)


def _deduplicate_declarations(declarations: list[Declaration]) -> list[Declaration]:
    """Remove duplicate declarations, keeping the first occurrence.

    Duplicates are identified by:
    - Same type (Struct, Function, Typedef, etc.)
    - Same name
    - Same namespace (for C++)

    Special handling for typedef struct pattern:
    - `typedef struct Foo {...} Foo;` creates both Struct and Typedef
    - We keep only the Struct (with typedef flag set) and remove the Typedef

    Special handling for typedef enum pattern:
    - `typedef enum [Foo] {...} Foo;` creates both Enum and Typedef
    - We keep only the Enum and remove the redundant Typedef

    :param declarations: List of declarations to deduplicate
    :returns: List with duplicates removed, preserving order
    """
    seen: set[tuple[type, str | None, str | None]] = set()
    unique: list[Declaration] = []

    enum_names: set[str | None] = {decl.name for decl in declarations if isinstance(decl, Enum)}

    # First pass: collect struct names that have typedef'd versions
    typedef_struct_names: set[str | None] = set()
    typedef_enum_names: set[str | None] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            # Check if this typedef aliases a struct with the same name
            underlying = decl.underlying_type
            if isinstance(underlying, CType):
                # Handle both "struct Foo" and "Foo" patterns
                type_name = underlying.name
                if type_name.startswith("struct "):
                    struct_name = type_name[7:]
                else:
                    struct_name = type_name

                if struct_name == decl.name:
                    typedef_struct_names.add(decl.name)

                # `typedef enum [Tag] {...} Name;` yields both an Enum and a
                # redundant self-referential Typedef.  Drop the Typedef the same
                # way the struct pattern above does, but only when the Enum it
                # would alias is actually present.
                enum_name = type_name[5:] if type_name.startswith("enum ") else type_name
                if enum_name == decl.name and decl.name in enum_names:
                    typedef_enum_names.add(decl.name)

    # Second pass: filter declarations and mark typedef'd structs
    for decl in declarations:
        # Build a key: (type, name, namespace)
        decl_type = type(decl)
        decl_name = getattr(decl, "name", None)
        decl_ns = getattr(decl, "namespace", None)

        key = (decl_type, decl_name, decl_ns)

        # Skip typedef if it's a typedef struct pattern
        if isinstance(decl, Typedef) and decl_name in typedef_struct_names:
            continue

        # Skip typedef if it's a redundant typedef enum pattern
        if isinstance(decl, Typedef) and decl_name in typedef_enum_names:
            continue

        # Mark struct as typedef'd if it has a matching typedef
        if isinstance(decl, Struct) and decl_name in typedef_struct_names:
            # Create a new Struct with is_typedef=True
            # (dataclasses are immutable by default, need to replace)
            decl = replace(decl, is_typedef=True)

        if key not in seen:
            seen.add(key)
            unique.append(decl)

    return unique


def _mangle_specialization_name(cpp_name: str) -> str:
    """Convert C++ template specialization to valid Python identifier.

    Examples:
        Container<int> -> Container_int
        Map<int, double> -> Map_int_double
        Foo<int*> -> Foo_int_ptr
    """
    name = cpp_name.replace(" ", "")
    name = name.replace("<", "_").replace(">", "")
    name = name.replace(",", "_")
    name = name.replace("::", "_")
    name = name.replace("*", "_ptr")
    name = name.replace("&", "_ref")
    return name


_MACRO_PROBE_PREFIX = "__headerkit_macro_probe_"


# Extensions clang's own driver maps to C++.  ``.h`` is deliberately absent: it
# is ambiguous, and clang treats it as C unless a flag says otherwise.
CPP_HEADER_EXTENSIONS = (".hpp", ".hh", ".hxx", ".h++", ".H", ".tcc", ".tpp")


def _detect_cplus(filename: str, extra_args: Sequence[str] | None) -> bool:
    """Return True when the translation unit is C++, as clang's driver decides it.

    Flags win over the extension, in both directions: an explicit ``-x c``
    forces C even for a ``.hpp``.  Otherwise the extension decides, because
    clang infers C++ from it and the backend must agree with the compiler it is
    driving.  Deriving this from the flags alone let a ``.hpp`` parse as C++
    while the backend believed it held C, so every C++-only guard stayed shut on
    a translation unit that clang had already read as C++.
    """
    if extra_args:
        for i, arg in enumerate(extra_args):
            if arg.startswith("-std=c++"):
                return True
            if arg == "-x" and i + 1 < len(extra_args):
                return extra_args[i + 1] == "c++"
    return filename.endswith(CPP_HEADER_EXTENSIONS)


# Operator spellings admissible in a C constant expression.  ``(`` and ``)`` are
# handled structurally by :func:`_is_constant_expression_shape` rather than being
# listed here, because their meaning depends on position: grouping or a cast in
# operand position, a function call in operator position.
_EXPR_UNARY_OPERATORS = frozenset({"+", "-", "~", "!"})
_EXPR_BINARY_OPERATORS = frozenset({"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", "<", ">", "?", ":"})

# Keywords that may legitimately appear inside a constant expression.  A cast
# operand and a ``sizeof`` operand are type names, so the type specifiers and the
# three tag keywords belong to the expression grammar even though every other
# keyword -- ``static``, ``extern``, ``inline``, ``__cdecl``, ``true`` -- marks a
# declaration or a value this backend cannot type.
_TYPE_SPECIFIER_KEYWORDS = frozenset(
    {
        "void",
        "char",
        "short",
        "int",
        "long",
        "float",
        "double",
        "signed",
        "unsigned",
        "_Bool",
        # C++ spells these as keywords where C leaves them identifiers or macros.
        "bool",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
    }
)
# ``const`` and ``volatile`` float: they may sit anywhere among the specifiers.
_TYPE_QUALIFIER_KEYWORDS = frozenset({"const", "volatile"})
# ``restrict`` does not float.  It qualifies a *pointer*, so it is legal only to
# the right of a ``*`` -- ``int * restrict`` is a type, ``int restrict`` is not.
_POINTER_QUALIFIER_KEYWORDS = frozenset({"restrict", "__restrict", "__restrict__"})
# ``_Complex`` modifies a floating specifier rather than joining the multiset, so
# it is stripped before the combination is looked up.
_COMPLEX_KEYWORDS = frozenset({"_Complex", "_Imaginary", "__complex__"})
_TAG_KEYWORDS = frozenset({"struct", "union", "enum"})
_TYPE_NAME_KEYWORDS = (
    _TYPE_SPECIFIER_KEYWORDS
    | _TYPE_QUALIFIER_KEYWORDS
    | _POINTER_QUALIFIER_KEYWORDS
    | _COMPLEX_KEYWORDS
    | _TAG_KEYWORDS
)
# ``sizeof``/``alignof`` yield an integer constant, so they are operators here
# rather than declaration specifiers.
_SIZEOF_KEYWORDS = frozenset({"sizeof", "alignof", "_Alignof", "__alignof__"})
_EXPRESSION_KEYWORDS = _TYPE_NAME_KEYWORDS | _SIZEOF_KEYWORDS
# The floating specifiers ``_Complex`` may modify.
_COMPLEX_BASE_MULTISETS = frozenset({("float",), ("double",), ("double", "long")})
# What may appear to the right of the specifiers in a type name.
_POINTER_RUN_TOKENS = frozenset({"*"}) | _TYPE_QUALIFIER_KEYWORDS | _POINTER_QUALIFIER_KEYWORDS

# Type-specifier combinations that name a type, as sorted multisets.  ``int char``
# and ``int void`` are each built from admissible keywords and name nothing, and
# differential fuzzing against the C compiler reached them.
#
# This is the UNION of the C and C++ spellings, so it is a superset of either
# language taken alone rather than an exact closure of both: measured with
# ``-pedantic-errors``, which matters because a permissive invocation accepts
# ``signed signed``, C11 admits 31 of these, C23 32, C++17 34 and C++20 35, while
# the table holds 36.  The extras are the four spellings the other language lacks
# -- ``_Bool`` in C++, and ``bool``/``wchar_t``/``char8_t``/``char16_t``/
# ``char32_t`` in C -- plus ``char8_t``, which is C++20 and not C++17.  What the
# measurement does establish, in every one of those four modes, is the direction
# that matters: nothing a compiler admits is missing here.  Erring wide costs a
# macro that the C compiler would reject anyway; erring narrow would drop a real
# constant, which is the defect this whole predicate exists to avoid.
_VALID_TYPE_SPECIFIER_MULTISETS = frozenset(
    tuple(sorted(combination))
    for combination in (
        ("void",),
        ("_Bool",),
        # C++ only; each stands alone and admits no signedness or width modifier.
        ("bool",),
        ("wchar_t",),
        ("char8_t",),
        ("char16_t",),
        ("char32_t",),
        ("float",),
        ("double",),
        ("long", "double"),
        ("char",),
        ("signed", "char"),
        ("unsigned", "char"),
        ("short",),
        ("signed", "short"),
        ("unsigned", "short"),
        ("short", "int"),
        ("signed", "short", "int"),
        ("unsigned", "short", "int"),
        ("int",),
        ("signed",),
        ("unsigned",),
        ("signed", "int"),
        ("unsigned", "int"),
        ("long",),
        ("signed", "long"),
        ("unsigned", "long"),
        ("long", "int"),
        ("signed", "long", "int"),
        ("unsigned", "long", "int"),
        ("long", "long"),
        ("signed", "long", "long"),
        ("unsigned", "long", "long"),
        ("long", "long", "int"),
        ("signed", "long", "long", "int"),
        ("unsigned", "long", "long", "int"),
    )
)


def _has_non_expression_keyword(tokens: list[Any]) -> bool:
    """Report whether any token is a keyword that cannot occur in a constant expression.

    Declaration specifiers -- ``static``, ``extern``, ``inline``, ``struct`` used
    as a declaration head, and the calling-convention keywords ``__cdecl`` /
    ``__stdcall`` / ``__fastcall`` -- are keywords, so clang's own token
    classification separates them from constants without a denylist of spellings
    that would forever trail the next compiler extension.

    The gate is narrowed to keywords outside :data:`_EXPRESSION_KEYWORDS` because
    a cast and a ``sizeof`` are ordinary constant expressions: ``((int)0x1F)``,
    ``((unsigned long)-1)`` and ``sizeof(int)`` are values, and rejecting every
    keyword outright deleted them.  A type keyword that is *not* part of a cast or
    a ``sizeof`` -- ``const char *``, ``unsigned int`` -- is rejected structurally
    by :func:`_is_constant_expression_shape` instead, which is where the
    distinction actually lives.
    """
    for token in tokens:
        kind = getattr(getattr(token, "kind", None), "name", None)
        if kind == "KEYWORD" and getattr(token, "spelling", None) not in _EXPRESSION_KEYWORDS:
            return True
    return False


def _matching_paren(spellings: list[str], open_index: int) -> int | None:
    """Return the index of the ``)`` closing the ``(`` at ``open_index``, or None."""
    depth = 0
    for index in range(open_index, len(spellings)):
        if spellings[index] == "(":
            depth += 1
        elif spellings[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _is_type_name(spellings: list[str], *, as_cast: bool) -> bool:
    """Report whether the spellings form a type name -- a cast or ``sizeof`` operand.

    A lone identifier counts.  ``size_t``, ``uint32_t`` and every project typedef
    reach this function as one identifier, and they are commoner in real headers
    than the spellings built from keywords, so refusing them would drop
    ``((size_t)1)`` and ``((uint32_t)0x1F)``.  Reading a parenthesised identifier
    as a cast is safe in the position this is asked from, and safe for two
    independent reasons:

    * the caller only treats a parenthesised run as a cast when a token follows
      it, so ``#define B (A)`` -- nothing after the ``)`` -- is grouping and
      keeps its value;
    * where a token does follow, grouping is not a possible reading. ``(A) B``
      would be two adjacent operands, which C has no rule for, so a cast is the
      only interpretation that parses.

    The specifiers must *combine* into a type: ``int char`` and ``int void`` are
    each built from admissible keywords and name nothing.  Two further shapes are
    excluded: qualifiers alone (``( const ) A`` is implicit int, which C99
    removed) and, for a cast only, ``void`` with no pointer declarator, because
    ``( void ) A`` discards its operand rather than yielding one.  ``sizeof(void)``
    is a different question and is admitted -- it is a GNU extension the compiler
    accepts in its default mode, where it yields 1.  All of these were found by
    differential fuzzing against the C compiler.

    Two families are knowingly accepted, both reachable only from a header that is
    already invalid in its own language, so no header that compiles reaches them.
    ``( float _Complex _Complex ) 1`` is an error under ``-pedantic-errors`` and
    accepted by clang in its default mode -- the same mode the ``sizeof(void)``
    reasoning above appeals to.  ``( wchar_t const const ) 1`` and
    ``( wchar_t * restrict ) 1`` mix the languages: a duplicate qualifier is an
    error in C++ but a warning in C11, and ``restrict`` is not a C++ keyword at
    all.  The specifier table is a C/C++ union while the qualifier rules are
    language-agnostic, so the seam between them is where these sit.

    Qualifier *placement* is checked rather than ignored, because the two kinds of
    qualifier do not go in the same places.  ``const`` and ``volatile`` float
    freely among the specifiers, so ``const int *``, ``int const *`` and
    ``int * const`` are all the same type.  ``restrict`` does not float: it
    qualifies a pointer and is legal only to the right of a ``*``.  Stripping all
    qualifiers wherever they appear would accept ``int restrict`` and reject
    ``int * restrict``, which is the wrong answer in both directions, so the
    trailing pointer run is parsed instead of discarded.
    """
    if not spellings:
        return False
    core = list(spellings)
    # Split off the trailing declarator run -- the ``*``s and the qualifiers that
    # bind to them -- and require every pointer qualifier in it to follow a ``*``.
    run_start = len(core)
    while run_start and core[run_start - 1] in _POINTER_RUN_TOKENS:
        run_start -= 1
    run, core = core[run_start:], core[:run_start]
    pointer = False
    for token in run:
        if token == "*":
            pointer = True
        elif token in _POINTER_QUALIFIER_KEYWORDS and not pointer:
            return False
    # Belt and braces: the run above consumes every trailing ``*`` and pointer
    # qualifier, and fuzzing found no input where this guard changes the verdict.
    # It states the invariant the code below relies on rather than earning its
    # place by catching something, so a surviving mutant here is expected.
    if any(token in _POINTER_QUALIFIER_KEYWORDS or token == "*" for token in core):
        return False
    tag = next((i for i, s in enumerate(core) if s in _TAG_KEYWORDS), None)
    if tag is not None:
        # The tag keyword and its name are adjacent; a qualifier may precede or
        # follow the pair but never sit between them (``struct const S`` is not C).
        name = core[tag + 1] if tag + 1 < len(core) else None
        if name is None or name in _EXPRESSION_KEYWORDS or not name.isidentifier():
            return False
        if any(s not in _TYPE_QUALIFIER_KEYWORDS for i, s in enumerate(core) if i not in (tag, tag + 1)):
            return False
        # C has no cast to a struct, union or enum type, only to a pointer to one.
        # ``sizeof ( struct S )`` is unaffected, which is why the caller says which
        # it is asking about.
        return pointer or not as_cast
    core = [s for s in core if s not in _TYPE_QUALIFIER_KEYWORDS]
    if not core:
        return False
    if len(core) == 1 and core[0] not in _EXPRESSION_KEYWORDS and core[0].isidentifier():
        return True
    is_complex = any(s in _COMPLEX_KEYWORDS for s in core)
    rest = [s for s in core if s not in _COMPLEX_KEYWORDS]
    if not all(s in _TYPE_SPECIFIER_KEYWORDS for s in rest):
        return False
    specifiers = tuple(sorted(rest))
    if is_complex:
        return specifiers in _COMPLEX_BASE_MULTISETS
    if specifiers not in _VALID_TYPE_SPECIFIER_MULTISETS:
        return False
    return specifiers != ("void",) or pointer or not as_cast


def _is_constant_expression_shape(spellings: list[str]) -> bool:
    """Report whether token spellings form a well-formed constant expression.

    Walks the tokens tracking whether an operand or an operator is expected,
    which rejects the shapes that declaration specifiers produce but constant
    expressions never do:

    * two adjacent operands (``__declspec ( dllexport ) PyObject``, ``PyObject``
      following a closing paren) -- a C expression requires an operator between
      operands;
    * a trailing binary operator (``PyObject *``, ``PyObject &``) -- a pointer or
      reference declarator, not a value;
    * call syntax (``__declspec ( dllimport )``) -- a ``(`` in operator position.
      A constant expression contains no function calls.
    * a type name outside a cast or a ``sizeof`` (``const char *``,
      ``unsigned int``, ``struct PyObject``) -- declaration specifiers.

    A parenthesised type name in *operand* position with something after it is a
    cast, which consumes no operand and so leaves the walker still expecting one.
    ``sizeof`` and ``alignof`` take either a parenthesised type name, which is a
    complete operand, or an ordinary expression operand.

    Caller has already established that every non-operator token is a numeric
    literal or an identifier, so operands need no further validation here.
    """
    expect_operand = True
    depth = 0
    index = 0
    count = len(spellings)
    while index < count:
        spelling = spellings[index]
        if expect_operand:
            if spelling in _SIZEOF_KEYWORDS:
                close = _matching_paren(spellings, index + 1) if spellings[index + 1 : index + 2] == ["("] else None
                if close is not None and _is_type_name(spellings[index + 2 : close], as_cast=False):
                    # ``sizeof ( int )`` is a complete operand.
                    expect_operand = False
                    index = close + 1
                    continue
                # ``sizeof X`` / ``sizeof ( X + 1 )``: a unary operator.
                index += 1
                continue
            if spelling == "(":
                close = _matching_paren(spellings, index)
                if (
                    close is not None
                    and close + 1 < count
                    and _is_type_name(spellings[index + 1 : close], as_cast=True)
                ):
                    # A cast: it yields no operand, so one is still expected.
                    index = close + 1
                    continue
                depth += 1
                index += 1
                continue
            if spelling in _TYPE_NAME_KEYWORDS:
                return False
            if spelling in _EXPR_UNARY_OPERATORS:
                index += 1
                continue
            if spelling == ")" or spelling in _EXPR_BINARY_OPERATORS:
                return False
            expect_operand = False
            index += 1
            continue
        if spelling == ")":
            depth -= 1
            if depth < 0:
                return False
            index += 1
            continue
        if spelling == "(":
            # Call syntax: an operand immediately followed by an argument list.
            return False
        if spelling in _EXPR_BINARY_OPERATORS:
            expect_operand = True
            index += 1
            continue
        # Two adjacent operands, or a type name where an operator belongs.
        return False
    return depth == 0 and not expect_operand


def _is_function_like_macro(tokens: list[Any]) -> bool:
    """Report whether a macro definition's tokens describe a function-like macro.

    The C preprocessor distinguishes ``#define F(a) ...`` from ``#define X (1+2)``
    purely by whether the ``(`` is immediately adjacent to the macro name, with no
    intervening whitespace. Token extents give that adjacency exactly, so the
    replacement list never has to be inspected -- which is what lets an
    empty-bodied function-like macro such as ``#define F(v)`` be recognised.
    """
    if len(tokens) < 2 or tokens[1].spelling != "(":
        return False
    try:
        return bool(tokens[0].extent.end.offset == tokens[1].extent.start.offset)
    except AttributeError:
        return False


def _same_macro_value(a: Constant, b: Constant) -> bool:
    """Report whether two macro Constants carry the same value and type."""
    return (a.value, a.evaluated_value, a.raw_expression, a.type) == (
        b.value,
        b.evaluated_value,
        b.raw_expression,
        b.type,
    )


class ClangASTConverter:
    """Converts libclang cursors to headerkit IR.

    This class walks a libclang translation unit and produces the
    equivalent headerkit IR declarations. It handles C and C++ constructs
    including structs, unions, enums, typedefs, functions, classes, and variables.

    :param filename: Source filename for filtering declarations.
        Only declarations from this file are included (system headers excluded).
    :param project_prefixes: Optional tuple of path prefixes to treat as project headers.
        Declarations from these paths will be included in addition to the main file.
    :param is_cplus: True when the translation unit is C++.  C++ has no separate enum
        tag namespace, which changes how tag-less typedef'd enums are detected.
    :param allowlist_paths: Resolved entries (see :class:`_PathSet`) naming the
        included files whose declarations are kept alongside the main file's.
        ``None`` means "no allowlist was supplied" and admits nothing extra.
    :param denylist_paths: Resolved entries whose declarations are dropped even
        when an allowlist or a project prefix names them -- deny wins over allow.
        The main file itself is never denied.

    Note
    ----
    This class is internal to the libclang backend. Use
    :class:`LibclangBackend` for the public API.
    """

    def __init__(
        self,
        filename: str,
        project_prefixes: tuple[str, ...] | None = None,
        *,
        is_cplus: bool = False,
        allowlist_paths: _PathSet | None = None,
        denylist_paths: _PathSet | None = None,
        parse_args: tuple[str, ...] = (),
    ) -> None:
        self.filename = filename
        self.project_prefixes = project_prefixes
        self.is_cplus = is_cplus
        self.allowlist_paths = allowlist_paths
        self.denylist_paths = denylist_paths
        #: Whether this translation unit's target gives an unnamed bit-field's
        #: alignment to its record. Measured once per argument set; None when
        #: the probe could not be measured at all, which is reported on any
        #: record it could have changed the answer for rather than guessed.
        self._measured_bitfield_align = _unnamed_bitfields_impose_alignment(parse_args, is_cplus)
        self._unnamed_bitfields_align = bool(self._measured_bitfield_align)
        self.declarations: list[Declaration] = []
        # Track seen declarations to avoid duplicates
        self._seen: set[str] = set()
        # Macro name -> the Constant currently emitted for it. A later #define of
        # the same name supersedes an earlier one, so the previous Constant is
        # withdrawn from ``declarations`` rather than the redefinition ignored.
        self._macro_decls: dict[str, Constant] = {}
        # Current namespace context (for nested namespace support)
        self._namespace_stack: list[str] = []
        # Store translation unit for dependency resolution
        self._tu: Any = None
        # Anonymous record/enum declaration key -> stable generated tag name
        self._anon_names: dict[str, str] = {}
        # Counter backing the fallback slug for anonymous tags no declarator names
        self._anon_counter: int = 0

    @property
    def _current_namespace(self) -> str | None:
        """Get current namespace as '::'-joined string, or None if global."""
        return "::".join(self._namespace_stack) if self._namespace_stack else None

    def _record_key(self, kind: str, name: str | None) -> str:
        """Build the ``_seen`` identity for a record, qualified by namespace.

        Mirrors the ``(type, name, namespace)`` identity that
        :func:`_deduplicate_declarations` uses.  Without the namespace component
        ``a::dup`` and ``b::dup`` collide and the second one is silently dropped.
        """
        return f"{kind}:{self._current_namespace or ''}:{name}"

    def _remove_forward_declaration(self, name: str | None, kind: str) -> None:
        """Remove a forward declaration from declarations list.

        Called when we encounter a full definition after having emitted
        a forward declaration. We need to remove the forward declaration
        so it can be replaced by the complete definition.
        """
        if name is None:
            return

        # Find and remove the forward declaration
        for i, decl in enumerate(self.declarations):
            if isinstance(decl, Struct):
                if decl.name == name and decl.namespace == self._current_namespace:
                    # Check if it's a forward declaration (no fields, no methods)
                    if not decl.fields and not decl.methods:
                        # Verify the kind matches
                        is_match = (
                            (kind == "struct" and not decl.is_union and not decl.is_cppclass)
                            or (kind == "union" and decl.is_union)
                            or (kind == "class" and decl.is_cppclass)
                        )
                        if is_match:
                            self.declarations.pop(i)
                            return

    def convert(self, tu: Any) -> Header:
        """Convert a libclang TranslationUnit to our IR Header.

        Uses smart dependency resolution to include typedefs from included
        headers when they define types used in the main file.
        """
        self._tu = tu

        # Phase 1: Collect main file cursors and identify used/defined types
        main_cursors: list[Any] = []
        used_types: set[str] = set()
        defined_types: set[str] = set()

        for child in tu.cursor.get_children():
            if not self._is_from_target_file(child):
                continue
            main_cursors.append(child)

            # Collect types used by this cursor
            used_types.update(self._collect_used_types(child))

            # Collect types defined by this cursor
            defined_types.update(self._collect_defined_types(child))

        # Phase 1b: Bind anonymous records/enums to the declarators that name them.
        # This must run before any declaration is processed so that a record and
        # every reference to it resolve to the same generated tag.
        self._prescan_anonymous_names(main_cursors)

        # Phase 2: Calculate needed types (used but not defined in main file)
        needed_types = used_types - defined_types

        # Remove built-in C types that don't need definitions
        # These are either keywords, provided by Cython/libc, or platform-specific
        builtin_types = {
            # C keywords and basic types
            "void",
            "char",
            "short",
            "int",
            "long",
            "float",
            "double",
            "signed",
            "unsigned",
            "bool",
            "bint",
            # stddef.h / stdint.h types (provided by libc.stddef, libc.stdint)
            "size_t",
            "ssize_t",
            "ptrdiff_t",
            "wchar_t",
            "int8_t",
            "int16_t",
            "int32_t",
            "int64_t",
            "uint8_t",
            "uint16_t",
            "uint32_t",
            "uint64_t",
            "intptr_t",
            "uintptr_t",
            "intmax_t",
            "uintmax_t",
            # stdio.h types
            "FILE",
            "fpos_t",
            # stdarg.h types
            "va_list",
            # time.h types
            "time_t",
            "clock_t",
            # sys/types.h common types
            "off_t",
            "pid_t",
            "uid_t",
            "gid_t",
            "mode_t",
            "dev_t",
            "ino_t",
            "nlink_t",
            "blksize_t",
            "blkcnt_t",
            # Platform-specific internal types (should not be exposed)
            "__int64_t",
            "__uint64_t",
            "__int32_t",
            "__uint32_t",
            "__int16_t",
            "__uint16_t",
            "__int8_t",
            "__uint8_t",
            "__darwin_off_t",
            "__darwin_size_t",
            "__darwin_ssize_t",
            "__darwin_time_t",
            "__darwin_clock_t",
            "__darwin_pid_t",
            "__darwin_uid_t",
            "__darwin_gid_t",
            "__darwin_mode_t",
            "__darwin_dev_t",
            "__darwin_ino_t",
            "__darwin_ino64_t",
            # Linux internal types
            "__off_t",
            "__off64_t",
            "__pid_t",
            "__uid_t",
            "__gid_t",
            "__mode_t",
            "__dev_t",
            "__ino_t",
            "__ino64_t",
            "__time_t",
            "__clock_t",
            "__ssize_t",
        }
        needed_types -= builtin_types
        # Also filter out types that start with __ (internal/reserved)
        needed_types = {t for t in needed_types if not t.startswith("__")}

        # Phase 3: Find and process typedefs from included files for needed types
        if needed_types:
            self._resolve_dependencies(tu.cursor, needed_types)

        # Phase 4: Process main file declarations
        for cursor in main_cursors:
            self._process_cursor(cursor)

        return Header(path=self.filename, declarations=self.declarations)

    def _collect_used_types(self, cursor: Any) -> set[str]:
        """Recursively collect all typedef names used by a cursor."""
        used: set[str] = set()

        # Check the cursor's type
        if cursor.type.kind == TypeKind.TYPEDEF:
            decl = cursor.type.get_declaration()
            if decl.spelling:
                used.add(decl.spelling)

        # Check result type for functions
        if cursor.kind == CursorKind.FUNCTION_DECL:
            used.update(self._extract_typedef_names_from_type(cursor.result_type))
            for arg in cursor.get_arguments():
                used.update(self._extract_typedef_names_from_type(arg.type))

        # Recursively check children
        for child in cursor.get_children():
            # Check field types
            if child.kind == CursorKind.FIELD_DECL or child.kind == CursorKind.PARM_DECL:
                used.update(self._extract_typedef_names_from_type(child.type))
            # Recurse
            used.update(self._collect_used_types(child))

        return used

    def _extract_typedef_names_from_type(self, clang_type: Any) -> set[str]:
        """Extract typedef names from a type, including through pointers/arrays."""
        names: set[str] = set()
        kind = clang_type.kind

        if kind == TypeKind.TYPEDEF:
            decl = clang_type.get_declaration()
            if decl.spelling:
                names.add(decl.spelling)
            # Also check the underlying type for chained typedefs
            underlying = clang_type.get_canonical()
            if underlying.kind == TypeKind.TYPEDEF:
                udecl = underlying.get_declaration()
                if udecl.spelling:
                    names.add(udecl.spelling)
        elif kind == TypeKind.POINTER:
            pointee = clang_type.get_pointee()
            names.update(self._extract_typedef_names_from_type(pointee))
        elif kind in (
            TypeKind.CONSTANTARRAY,
            TypeKind.INCOMPLETEARRAY,
            TypeKind.VARIABLEARRAY,
            TypeKind.DEPENDENTSIZEDARRAY,
        ):
            element = clang_type.element_type
            names.update(self._extract_typedef_names_from_type(element))
        elif kind == TypeKind.ELABORATED:
            named = clang_type.get_named_type()
            names.update(self._extract_typedef_names_from_type(named))

        return names

    def _collect_defined_types(self, cursor: Any) -> set[str]:
        """Collect type names defined by a cursor."""
        defined: set[str] = set()
        kind = cursor.kind

        if (
            kind == CursorKind.TYPEDEF_DECL
            or kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL)
            or kind == CursorKind.ENUM_DECL
            or kind in (CursorKind.CLASS_DECL, CursorKind.CLASS_TEMPLATE)
        ):
            if cursor.spelling:
                defined.add(cursor.spelling)

        return defined

    def _resolve_dependencies(
        self,
        root_cursor: Any,
        needed_types: set[str],
    ) -> None:
        """Find and process typedefs from included files for needed types."""
        # Build a map of typedef name -> cursor for all non-main-file typedefs
        typedef_map: dict[str, Any] = {}

        for child in root_cursor.get_children():
            if child.kind == CursorKind.TYPEDEF_DECL:
                if not self._is_from_target_file(child) and child.spelling:
                    typedef_map[child.spelling] = child

        # Build dependency graph and process in topological order
        def get_dependencies(type_name: str) -> set[str]:
            """Get types that this typedef depends on.

            This includes:
            1. Types referenced directly in the typedef's underlying type
            2. Types used by structs/unions that the typedef aliases
            """
            if type_name not in typedef_map:
                return set()
            cursor = typedef_map[type_name]
            underlying = cursor.underlying_typedef_type
            deps = self._extract_typedef_names_from_type(underlying)

            # If the underlying type is a struct/union, also collect types used by it
            # This handles cases like: typedef struct foo_s { bar_t field; } foo_t;
            # where bar_t needs to be resolved before foo_t
            decl = underlying.get_declaration()
            if decl.kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL):
                deps.update(self._collect_used_types(decl))

            # Only return deps that are also in typedef_map (defined in included files)
            return deps & set(typedef_map.keys())

        # System types that should not be emitted but can satisfy dependencies
        system_types_not_to_emit = {
            "size_t",
            "ssize_t",
            "ptrdiff_t",
            "wchar_t",
            "int8_t",
            "int16_t",
            "int32_t",
            "int64_t",
            "uint8_t",
            "uint16_t",
            "uint32_t",
            "uint64_t",
            "intptr_t",
            "uintptr_t",
            "intmax_t",
            "uintmax_t",
            "off_t",
            "time_t",
            "clock_t",
            "pid_t",
            "uid_t",
            "gid_t",
            "mode_t",
            "dev_t",
            "ino_t",
            "nlink_t",
            "blksize_t",
            "blkcnt_t",
        }

        # Expand needed_types to include all transitive dependencies
        all_needed: set[str] = set()
        to_expand = list(needed_types)
        while to_expand:
            type_name = to_expand.pop()
            if type_name in all_needed:
                continue
            if type_name in typedef_map:
                all_needed.add(type_name)
                deps = get_dependencies(type_name)
                to_expand.extend(deps - all_needed)

        # Filter out system types that shouldn't be emitted
        all_needed -= system_types_not_to_emit

        # Process in dependency order using simple topological sort
        processed: set[str] = set(system_types_not_to_emit)  # Treat system types as already processed
        # Sort alphabetically for deterministic output
        to_process = deque(sorted(all_needed))
        max_iterations = len(to_process) * len(to_process) + 1  # Safety limit

        iterations = 0
        while to_process and iterations < max_iterations:
            iterations += 1
            type_name = to_process.popleft()
            if type_name in processed:
                continue

            # Check if all dependencies are processed (system types count as processed)
            deps = get_dependencies(type_name)
            unmet_deps = deps - processed - system_types_not_to_emit
            if unmet_deps:
                # Re-queue and try again later
                to_process.append(type_name)
                continue

            # All dependencies met, process this typedef
            processed.add(type_name)
            if type_name in typedef_map:
                self._process_typedef(typedef_map[type_name])

        if iterations >= max_iterations:
            import logging

            logging.getLogger(__name__).warning(
                "Topological sort hit iteration limit (%d); %d declarations may be missing",
                max_iterations,
                len(to_process),
            )

    def _process_children(self, cursor: Any) -> None:
        """Process all children of a cursor."""
        for child in cursor.get_children():
            # Only process declarations from the target file
            if not self._is_from_target_file(child):
                continue
            self._process_cursor(child)

    def _is_from_target_file(self, cursor: Any) -> bool:
        """Check if cursor is from the target file or an allowed project path.

        Returns True if cursor is from:
        1. The main target file (self.filename), OR
        2. A path under one of the project_prefixes (for umbrella headers), OR
        3. One of the allowlisted files

        and is not named by the denylist.  The main file is exempt from the
        denylist: denying the file the caller asked to parse would return an
        empty result with nothing to explain it, which is a silent failure
        wearing the costume of a successful parse.

        Cases 2 and 3 compare absolute, symlink-resolved paths.  clang reports a
        location as the path it was included by -- typically relative, e.g.
        ``./tux_foo.h`` -- so comparing the raw spelling against a caller-supplied
        absolute path never matches.
        """
        loc = cursor.location
        if loc.file is None:
            return False

        file_path = str(loc.file.name)

        # Check main file.  Deliberately ahead of the denylist: the main file
        # cannot be denied out of existence.
        if normalize_path(file_path) == normalize_path(self.filename):
            return True

        if not self.project_prefixes and not self.allowlist_paths and not self.denylist_paths:
            return False

        resolved = _resolve_path(file_path)

        # Deny wins over allow.
        if self.denylist_paths is not None and self.denylist_paths.matches(resolved):
            return False

        # Check allowlisted files
        if self.allowlist_paths is not None and self.allowlist_paths.matches(resolved):
            return True

        # Check project prefixes (for umbrella headers)
        for prefix in self.project_prefixes or ():
            resolved_prefix = _resolve_path(prefix).rstrip("/")
            if resolved == resolved_prefix or resolved.startswith(resolved_prefix + "/"):
                return True

        return False

    def _process_cursor(self, cursor: Any) -> None:
        """Process a top-level cursor."""
        kind = cursor.kind

        if kind == CursorKind.STRUCT_DECL:
            self._process_struct(cursor, is_union=False)
        elif kind == CursorKind.UNION_DECL:
            self._process_struct(cursor, is_union=True)
        elif kind == CursorKind.ENUM_DECL:
            self._process_enum(cursor)
        elif kind in (CursorKind.FUNCTION_DECL, CursorKind.FUNCTION_TEMPLATE):
            self._process_function(cursor)
        elif kind == CursorKind.TYPEDEF_DECL:
            self._process_typedef(cursor)
        elif kind == CursorKind.VAR_DECL:
            self._process_variable(cursor)
        elif kind == CursorKind.CLASS_DECL:
            # C++ class - uses cppclass in Cython
            self._process_struct(cursor, is_union=False, is_cppclass=True)
        elif kind == CursorKind.CLASS_TEMPLATE:
            # C++ class template
            self._process_class_template(cursor)
        elif kind == CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION:
            # C++ partial template specialization - emit comment explaining limitation
            self._process_partial_specialization(cursor)
        elif kind == CursorKind.NAMESPACE:
            # C++ namespace - recurse into it with namespace context
            self._process_namespace(cursor)
        elif kind == CursorKind.MACRO_DEFINITION:
            # #define macro - extract numeric constants
            self._process_macro(cursor)

    def _process_namespace(self, cursor: Any) -> None:
        """Process a C++ namespace declaration."""
        ns_name = cursor.spelling
        if ns_name:
            self._namespace_stack.append(ns_name)
            self._process_children(cursor)
            self._namespace_stack.pop()

    def _process_macro(self, cursor: Any) -> None:
        """Process a #define macro declaration.

        Extracts various macro types as Constants:
        - Simple integers: ``#define SIZE 100``
        - Integers with suffixes: ``#define SIZE 100ULL``
        - Hex/octal/binary: ``#define MASK 0xFF``
        - Floating point: ``#define PI 3.14159``
        - String literals: ``#define VERSION "1.0"``
        - Expression macros: ``#define TOTAL (A + B)``

        Function-like macros (with parameters) and macros with an empty
        replacement list are skipped: neither denotes a value, so emitting
        either as a variable declaration produces code that does not compile.

        A later ``#define`` of a name supersedes an earlier one. When the
        superseding definition is not emittable, the earlier Constant is
        withdrawn.
        """
        name = cursor.spelling
        if not name:
            return

        constant = self._build_macro_constant(cursor, name)

        previous = self._macro_decls.get(name)
        if previous is not None and constant is not None and _same_macro_value(previous, constant):
            # Identical redefinition - keep the original declaration and its position.
            return

        self._macro_decls.pop(name, None)
        if previous is not None:
            # A redefinition replaces the earlier value; drop the stale Constant.
            self.declarations = [d for d in self.declarations if d is not previous]

        if constant is not None:
            self._macro_decls[name] = constant
            self.declarations.append(constant)

    def _build_macro_constant(self, cursor: Any, name: str) -> Constant | None:
        """Build the :class:`Constant` for a ``#define``, or None if it denotes no value.

        Function-like macros and macros with an empty replacement list are
        rejected: neither has a type or a value, so emitting either as a variable
        declaration yields code that does not compile.
        """
        # First token is the macro name, the rest is the replacement list.
        tokens = list(cursor.get_tokens())

        if _is_function_like_macro(tokens):
            return None

        if len(tokens) < 2:
            # Empty replacement list (e.g. #define FLAG) - a flag, not a constant.
            return None

        macro_type, value, evaluated_value, raw_expr = self._analyze_macro_tokens(tokens[1:])
        if macro_type is None:
            return None

        loc = cursor.location
        location = SourceLocation(
            file=loc.file.name if loc.file else self.filename,
            line=loc.line,
            column=loc.column,
        )

        return Constant(
            name=name,
            value=value if value is not None else evaluated_value,
            evaluated_value=evaluated_value,
            raw_expression=raw_expr,
            type=macro_type,
            is_macro=True,
            location=location,
        )

    def _analyze_macro_tokens(
        self, tokens: list[Any]
    ) -> tuple[CType | None, int | float | str | None, int | float | str | None, str | None]:
        """Analyze macro value tokens to determine type, raw expression, and evaluated value.

        Returns:
            Tuple of (CType, value, evaluated_value, raw_expr) or (None, None, None, None) if unsupported.
        """
        if len(tokens) == 1:
            ctype, val = self._analyze_single_token(tokens[0].spelling)
            return ctype, val, val, tokens[0].spelling

        # Multiple tokens - analyze as expression
        return self._analyze_expression_tokens(tokens)

    def _analyze_single_token(self, token: str) -> tuple[CType | None, int | float | str | None]:
        """Analyze a single-token macro value."""
        # String literal
        if token.startswith('"') and token.endswith('"'):
            return CType("char", ["const"]), token

        # Character literal
        if token.startswith("'") and token.endswith("'"):
            return CType("char"), token

        # Try numeric with suffix stripping
        value, is_float = self._parse_numeric_with_suffix(token)
        if value is not None:
            if is_float:
                return CType("double"), value
            return CType("int"), value

        return None, None

    def _parse_numeric_with_suffix(self, token: str) -> tuple[int | float | None, bool]:
        """Parse a numeric token, stripping type suffixes.

        Returns:
            Tuple of (value, is_float) or (None, False) if not numeric.
        """
        # Check for float first (has decimal point or exponent)
        if "." in token or "e" in token.lower():
            # Strip float suffixes: f, F, l, L
            if token.endswith(("f", "F", "l", "L")):
                token = token[:-1]
            try:
                return float(token), True
            except ValueError:
                return None, False

        # Integer - strip suffixes: ULL, LL, UL, LU, U, L (case insensitive)
        upper = token.upper()
        for suffix in ("ULL", "LLU", "LL", "UL", "LU", "U", "L"):
            if upper.endswith(suffix):
                token = token[: -len(suffix)]
                break

        # Try to parse as integer
        try:
            if token.startswith(("0x", "0X")):
                return int(token, 16), False
            if token.startswith(("0b", "0B")):
                return int(token, 2), False
            if token.startswith("0") and len(token) > 1 and token[1:].isdigit():
                return int(token, 8), False
            return int(token), False
        except ValueError:
            return None, False

    def _analyze_expression_tokens(
        self, tokens: list[Any]
    ) -> tuple[CType | None, int | float | str | None, int | float | str | None, str | None]:
        """Analyze a multi-token expression macro.

        For expressions like (1 << 4), we detect the type and safely evaluate
        the expression to a concrete constant value if possible.
        """
        # Collect all token spellings
        spellings = [t.spelling for t in tokens]
        raw_expr = " ".join(spellings)

        # Check for string concatenation or complex string expressions
        has_string = any(s.startswith('"') for s in spellings)
        if has_string:
            # String expression - skip for now (complex to handle)
            return None, None, None, None

        # Check if expression contains float indicators
        has_float = False
        for s in spellings:
            if "." in s or "e" in s.lower():
                # Could be a float literal
                val, is_float = self._parse_numeric_with_suffix(s)
                if is_float:
                    has_float = True
                    break

        # A keyword that cannot occur in an expression means declaration specifiers.
        if _has_non_expression_keyword(tokens):
            return None, None, None, None

        # Valid expression tokens for integer/float expressions
        valid_operators = {"+", "-", "*", "/", "%", "&", "|", "^", "~", "<<", ">>", "(", ")", "<", ">", "!", "?", ":"}

        for spelling in spellings:
            # Skip operators and parentheses
            if spelling in valid_operators:
                continue
            # Skip numeric literals (including with suffixes)
            val, _ = self._parse_numeric_with_suffix(spelling)
            if val is not None:
                continue
            # Skip identifiers (other macro references)
            if spelling.isidentifier():
                continue
            # Unknown token - not a simple expression
            return None, None, None, None

        # Every token is individually admissible; require that they also compose
        # into an expression rather than a sequence of declaration specifiers.
        if not _is_constant_expression_shape(spellings):
            return None, None, None, None

        # Expression looks valid - try to safely evaluate
        eval_val = self._evaluate_expression(raw_expr)

        if has_float:
            return CType("double"), eval_val, eval_val, raw_expr
        return CType("int"), eval_val, eval_val, raw_expr

    def _get_access_specifier(self, cursor: Any) -> str | None:
        """Map libclang AccessSpecifier enum to string ('public', 'protected', 'private') or None."""
        try:
            acc = cursor.access_specifier
            if acc is not None:
                val = getattr(acc, "value", acc)
                access_map = {1: "public", 2: "protected", 3: "private"}
                return access_map.get(val)
        except Exception:
            pass
        return None

    def _is_noexcept(self, cursor: Any) -> bool:
        """Check if a function or method is declared noexcept or throw()."""
        try:
            # Check cursor exception specification kind
            # BASIC_NOEXCEPT = 4, COMPUTED_NOEXCEPT = 5, NOTHROW = 9, DYNAMIC_NONE = 1
            exc_kind = cursor.exception_specification_kind
            val = getattr(exc_kind, "value", exc_kind)
            if val in (1, 4, 5, 9):
                return True
        except Exception:
            pass
        return False

    def _is_inline_function(self, cursor: Any) -> tuple[bool, str | None]:
        """Check if a function is declared inline and extract body tokens if available."""
        is_inline = False
        body_str: str | None = None
        with contextlib.suppress(Exception):
            is_inline = bool(cursor.is_inline_function())

        try:
            tokens = [t.spelling for t in cursor.get_tokens()]
            if not is_inline and "inline" in tokens:
                is_inline = True
            if "{" in tokens and "}" in tokens:
                open_idx = tokens.index("{")
                close_idx = len(tokens) - 1 - tokens[::-1].index("}")
                body_tokens = tokens[open_idx : close_idx + 1]
                body_str = " ".join(body_tokens)
        except Exception:
            pass
        return is_inline, body_str

    def _get_default_argument(self, arg_cursor: Any) -> str | None:
        """Extract default argument expression from a PARM_DECL cursor."""
        try:
            tokens = [t.spelling for t in arg_cursor.get_tokens()]
            if "=" in tokens:
                eq_idx = tokens.index("=")
                default_tokens = tokens[eq_idx + 1 :]
                if default_tokens:
                    return "".join(default_tokens)
        except Exception:
            pass
        return None

    def _evaluate_expression(self, expr_str: str) -> int | float | None:
        """Safely evaluate a constant arithmetic / bitwise expression."""
        import ast

        class SafeEvaluator(ast.NodeVisitor):
            def visit(self, node: ast.AST) -> int | float:
                if isinstance(node, ast.Constant):
                    if isinstance(node.value, int | float):
                        return node.value
                    raise ValueError("Non-numeric constant")
                elif isinstance(node, ast.UnaryOp):
                    operand = self.visit(node.operand)
                    if isinstance(node.op, ast.UAdd):
                        return +operand
                    elif isinstance(node.op, ast.USub):
                        return -operand
                    elif isinstance(node.op, ast.Invert):
                        return ~int(operand)
                elif isinstance(node, ast.BinOp):
                    left = self.visit(node.left)
                    right = self.visit(node.right)
                    if isinstance(node.op, ast.Add):
                        return left + right
                    elif isinstance(node.op, ast.Sub):
                        return left - right
                    elif isinstance(node.op, ast.Mult):
                        return left * right
                    elif isinstance(node.op, ast.Div):
                        if isinstance(left, int) and isinstance(right, int):
                            return int(left / right)  # C-style integer division truncating towards zero
                        return left / right
                    elif isinstance(node.op, ast.FloorDiv):
                        return left // right
                    elif isinstance(node.op, ast.Mod):
                        if isinstance(left, int) and isinstance(right, int):
                            return left - int(left / right) * right  # C99 remainder semantics
                        return left % right
                    elif isinstance(node.op, ast.BitOr):
                        return int(left) | int(right)
                    elif isinstance(node.op, ast.BitAnd):
                        return int(left) & int(right)
                    elif isinstance(node.op, ast.BitXor):
                        return int(left) ^ int(right)
                    elif isinstance(node.op, ast.LShift):
                        return int(left) << int(right)
                    elif isinstance(node.op, ast.RShift):
                        return int(left) >> int(right)
                raise ValueError("Unsupported AST node")

        try:
            # Clean C-style suffixes from float and integer literals (e.g., 3.14f -> 3.14, 1ULL -> 1, 0xFFL -> 0xFF)
            cleaned = re.sub(r"(\b\d+\.\d*(?:[eE][+-]?\d+)?|\b\d+[eE][+-]?\d+)[fFlL]\b", r"\1", expr_str)
            cleaned = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", cleaned)
            parsed = ast.parse(cleaned, mode="eval")
            evaluator = SafeEvaluator()
            return evaluator.visit(parsed.body)
        except Exception:
            return None

    def _get_attributes(self, cursor: Any) -> tuple[list[str], bool]:
        """Extract attribute strings and check if deprecated."""
        attrs: list[str] = []
        is_deprecated = False
        try:
            # Check libclang cursor availability
            # 1 = AvailabilityKind.DEPRECATED
            avail = getattr(cursor, "availability", None)
            val = getattr(avail, "value", avail)
            if val == 1:
                is_deprecated = True
        except Exception:
            pass

        try:
            for child in cursor.get_children():
                # Extract attribute cursors if kind is attribute (UNEXPOSED_ATTR, etc.)
                if "ATTR" in child.kind.name:
                    spelling = child.spelling
                    if "deprecated" in child.kind.name.lower() or (spelling and "deprecated" in spelling.lower()):
                        is_deprecated = True
                    if spelling and spelling not in attrs:
                        attrs.append(spelling)
        except Exception:
            pass

        try:
            tokens = [t.spelling for t in cursor.get_tokens()]
            tok_str = " ".join(tokens)
            if "__attribute__" in tok_str or "[[" in tok_str:
                for m in re.finditer(r"__attribute__\s*\(\s*\(\s*(.*?)\s*\)\s*\)", tok_str):
                    inner = m.group(1).strip()
                    for attr in inner.split(","):
                        attr_clean = attr.strip()
                        if attr_clean and attr_clean not in attrs:
                            attrs.append(attr_clean)
                for m in re.finditer(r"\[\[\s*(.*?)\s*\]\]", tok_str):
                    inner = m.group(1).strip()
                    for attr in inner.split(","):
                        attr_clean = attr.strip()
                        if attr_clean and attr_clean not in attrs:
                            attrs.append(attr_clean)
                if any("deprecated" in a.lower() for a in attrs):
                    is_deprecated = True
        except Exception:
            pass

        return attrs, is_deprecated

    def _is_packed(self, cursor: Any) -> bool:
        """Return True when a record's layout drops natural field padding.

        Two independent signals are consulted because clang exposes the two
        spellings differently. ``__attribute__((packed))`` -- in either the
        prefix or the suffix position -- arrives as a ``PACKED_ATTR`` child
        cursor. ``#pragma pack(1)`` arrives as no cursor at all: the pragma is
        consumed by the preprocessor and survives only in the recorded layout.

        The layout signal is deliberately narrow. It fires only when the record
        ends up byte-aligned while a member wanted more, which is exactly the
        condition under which re-emitting ``__attribute__((packed))``
        reproduces the original layout. An intermediate ``#pragma pack(2)``
        leaves alignment at 2, is not expressible as a boolean, and is
        therefore not reported here -- see ``_pack_note``.

        Whether an anonymous bit-field contributes its type's alignment is a
        property of the target, not a universal rule, so it is measured rather
        than assumed -- see :func:`_unnamed_bitfields_impose_alignment`.
        """
        with contextlib.suppress(Exception):
            if any("PACKED" in child.kind.name for child in cursor.get_children()):
                return True

        natural, actual = self._layout_alignments(cursor)
        if natural is None or actual is None:
            return False
        return actual == 1 and natural > 1

    def _pack_note(self, cursor: Any) -> str | None:
        """Describe an intermediate ``#pragma pack(N)`` the IR cannot express.

        ``is_packed`` is a boolean, so a record squeezed to an alignment
        between 1 and its natural alignment has no faithful representation.
        Reporting it as packed would understate the offsets; reporting nothing
        would lose the fact silently. A note keeps it visible.
        """
        with contextlib.suppress(Exception):
            if any("PACKED" in child.kind.name for child in cursor.get_children()):
                return None
        natural, actual = self._layout_alignments(cursor)
        if natural is None or actual is None:
            return None
        if 1 < actual < natural:
            return (
                f"Record is laid out with alignment {actual} but its members "
                f"require {natural}; this is an intermediate '#pragma pack({actual})' "
                "that is_packed cannot express."
            )
        return None

    def _unmeasured_bitfield_align_note(self, cursor: Any) -> str | None:
        """Report a record whose packing turned on an ABI question clang would not answer.

        Only records carrying an unnamed bit-field are affected: for every other
        record the two ABIs agree, so an unmeasured probe changes nothing and a
        note would be noise. Where it does matter, saying so is the difference
        between a wrong answer and a known-unknown.
        """
        if self._measured_bitfield_align is not None:
            return None
        with contextlib.suppress(Exception):
            if not any(f.is_bitfield() and not f.spelling for f in cursor.type.get_fields()):
                return None
            return (
                "Record contains an unnamed bit-field and clang could not be asked whether "
                "this target gives such a field its type's alignment, so is_packed was "
                "resolved with the Itanium C++ ABI's answer and is unverified here."
            )
        return None

    def _member_natural_align(self, member_type: Any, depth: int = 0) -> int:
        """Alignment ``member_type`` would impose if nothing had been packed.

        A record that is itself packed reports an alignment of 1, and a
        container whose widest member is such a record therefore looks
        unpacked: ``#pragma pack(1)`` wrapped around a family of records -- the
        standard binary-format header idiom -- packs the inner ones first and
        leaves nothing on the outer one to detect. Recursing to the leaf
        scalars restores the figure the members would have imposed.
        """
        if depth > _NATURAL_ALIGN_MAX_DEPTH:
            # Not an alignment of "none". Returning 0 here left the natural
            # figure at 1, the packed test False and the record emitted at the
            # unpacked layout with nothing said, which is the collapse of
            # "unjudgeable" into "correct" that this branch removes elsewhere.
            return _NATURAL_ALIGN_UNKNOWN
        with contextlib.suppress(Exception):
            canonical = member_type.get_canonical()
            while canonical.kind == TypeKind.CONSTANTARRAY:
                canonical = canonical.get_array_element_type().get_canonical()
            declaration = canonical.get_declaration()
            record_kinds = (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.CLASS_DECL)
            if declaration is not None and declaration.kind in record_kinds:
                best = 0
                for f in canonical.get_fields():
                    if f.is_bitfield() and not f.spelling and not self._unnamed_bitfields_align:
                        continue
                    member = self._member_natural_align(f.type, depth + 1)
                    if member == _NATURAL_ALIGN_UNKNOWN:
                        return _NATURAL_ALIGN_UNKNOWN
                    best = max(best, member)
                if best > 0:
                    return best
            return int(canonical.get_align())
        return 0

    def _natural_align_unresolved(self, cursor: Any) -> bool:
        """Whether the members' natural alignment could not be resolved.

        True only when the nesting bound was reached. That is a different
        answer from "the members impose nothing", and reporting it is what
        stops a deeply nested packed record from being emitted at the unpacked
        layout with nothing said about it.
        """
        with contextlib.suppress(Exception):
            for f in cursor.type.get_fields():
                if f.is_bitfield() and not f.spelling and not self._unnamed_bitfields_align:
                    continue
                if self._member_natural_align(f.type) == _NATURAL_ALIGN_UNKNOWN:
                    return True
        return False

    def _unresolved_alignment_note(self, cursor: Any) -> str | None:
        """Report a record whose natural alignment the writer stopped short of."""
        if not self._natural_align_unresolved(cursor):
            return None
        return (
            f"Record nests aggregates more than {_NATURAL_ALIGN_MAX_DEPTH} deep, so the natural "
            "alignment of its members was not resolved and is_packed could not be decided "
            "from the layout; it is reported false here and is unverified."
        )

    def _layout_alignments(self, cursor: Any) -> tuple[int | None, int | None]:
        """Return (natural alignment of the members, recorded alignment)."""
        natural: int | None = None
        actual: int | None = None
        with contextlib.suppress(Exception):
            align = cursor.type.get_align()
            if align > 0:
                actual = int(align)
        with contextlib.suppress(Exception):
            best = 1
            saw_member = False
            for f in cursor.type.get_fields():
                # Whether an anonymous bit-field imposes its type's alignment is
                # a target property; the measured answer decides.
                if f.is_bitfield() and not f.spelling and not self._unnamed_bitfields_align:
                    continue
                fa = self._member_natural_align(f.type)
                if fa == _NATURAL_ALIGN_UNKNOWN:
                    return None, actual
                if fa > 0:
                    saw_member = True
                    best = max(best, fa)
            if saw_member:
                natural = best
        return natural, actual

    def _get_alignment(self, cursor: Any) -> int | None:
        """Extract explicit byte alignment from a cursor or type if specified."""
        has_explicit = False
        with contextlib.suppress(Exception):
            has_explicit = any("ALIGNED" in child.kind.name for child in cursor.get_children())
        if not has_explicit:
            with contextlib.suppress(Exception):
                has_explicit = any(t.spelling in ("aligned", "alignas", "_Alignas") for t in cursor.get_tokens())
        if not has_explicit:
            return None

        with contextlib.suppress(Exception):
            align = cursor.type.get_align()
            if align > 0:
                return int(align)
        return None

    # -----------------------------------------------------------------
    # Anonymous tag naming
    # -----------------------------------------------------------------

    @staticmethod
    def _anon_tag_suffix(kind: Any) -> str | None:
        """Return the generated-name suffix for an anonymous tag of this kind."""
        if kind == CursorKind.STRUCT_DECL:
            return "_s"
        if kind == CursorKind.UNION_DECL:
            return "_u"
        if kind == CursorKind.ENUM_DECL:
            return "_e"
        return None

    @staticmethod
    def _anon_key(cursor: Any) -> str:
        """Return a stable identity for an anonymous record/enum declaration.

        The USR is preferred because the same anonymous declaration is reachable
        from several cursors (a top-level sibling and a child of the declarator
        that uses it) and must resolve to one name from every path.
        """
        usr = ""
        with contextlib.suppress(Exception):
            usr = cursor.get_usr() or ""
        if usr:
            return usr
        loc = cursor.location
        if loc.file:
            return f"{loc.file.name}:{loc.line}:{loc.column}"
        return f"anon:{id(cursor)}"

    @staticmethod
    def _is_anonymous_decl(cursor: Any) -> bool:
        """Check whether a record/enum declaration cursor has no C tag name.

        ``cursor.spelling`` is not usable as the test: clang fabricates
        ``struct (unnamed at file:line:col)`` and ``struct (anonymous at ...)``
        spellings, which are truthy.
        """
        with contextlib.suppress(Exception):
            if cursor.is_anonymous():
                return True
        spelling = cursor.spelling or ""
        return not spelling or "(unnamed" in spelling or "(anonymous" in spelling

    def _resolve_tag_declaration(self, clang_type: Any) -> Any | None:
        """Peel arrays, pointers and elaborations off a type to reach its tag declaration."""
        current = clang_type
        for _ in range(16):
            try:
                kind = current.kind
                if kind == TypeKind.ELABORATED:
                    current = current.get_named_type()
                elif kind == TypeKind.POINTER:
                    current = current.get_pointee()
                elif kind in (TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY, TypeKind.VARIABLEARRAY):
                    current = current.element_type
                elif kind in (TypeKind.RECORD, TypeKind.ENUM):
                    return current.get_declaration()
                else:
                    return None
            except Exception:
                return None
        return None

    def _prescan_anonymous_names(self, cursors: list[Any]) -> None:
        """Bind every anonymous record/enum to the declarator that names it.

        An anonymous ``struct { ... } var;`` produces a STRUCT_DECL that is a
        *sibling* of the VAR_DECL, so the binding cannot be discovered while
        processing the record itself. This pre-pass walks the declarators first
        so both the definition and every reference agree on one generated tag.
        """
        for cursor in cursors:
            self._prescan_cursor_for_anon_names(cursor)

    def _prescan_cursor_for_anon_names(self, cursor: Any) -> None:
        kind = cursor.kind
        if kind == CursorKind.FIELD_DECL:
            self._bind_anon_name(cursor.spelling, cursor.type, qualifier=self._record_qualifier(cursor))
        elif kind in (
            CursorKind.VAR_DECL,
            CursorKind.PARM_DECL,
            CursorKind.TYPEDEF_DECL,
        ):
            self._bind_anon_name(cursor.spelling, cursor.type)
        elif kind == CursorKind.FUNCTION_DECL:
            with contextlib.suppress(Exception):
                self._bind_anon_name(cursor.spelling, cursor.result_type)

        with contextlib.suppress(Exception):
            for child in cursor.get_children():
                self._prescan_cursor_for_anon_names(child)

    @staticmethod
    def _record_qualifier(cursor: Any) -> str:
        """Return the enclosing record's name, used to qualify a field's tag.

        Two records in one translation unit may each hold a member named
        ``css``; deriving the tag from the member alone would collide. The
        enclosing record's name disambiguates, giving ``_outer_css_s``.
        Returns ``""`` when the parent is itself unnamed, leaving the tag
        unqualified rather than embedding clang's internal spelling.
        """
        parent = None
        with contextlib.suppress(Exception):
            parent = cursor.semantic_parent
        if parent is None or parent.kind not in (
            CursorKind.STRUCT_DECL,
            CursorKind.UNION_DECL,
            CursorKind.CLASS_DECL,
        ):
            return ""
        spelling = parent.spelling
        if not spelling or "(" in spelling:
            return ""
        return str(spelling)

    def _bind_anon_name(self, declarator: str, clang_type: Any, qualifier: str = "") -> None:
        if not declarator:
            return
        decl = self._resolve_tag_declaration(clang_type)
        if decl is None or not self._is_anonymous_decl(decl):
            return
        suffix = self._anon_tag_suffix(decl.kind)
        if suffix is None:
            return
        if qualifier and decl.kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.ENUM_DECL):
            declarator = f"{qualifier}_{declarator}"
        self._anon_names.setdefault(self._anon_key(decl), f"_{declarator}{suffix}")

    def _normalize_anon_name(self, cursor: Any) -> str | None:
        """Return the IR tag name for a record/enum declaration.

        Clang's internal ``(unnamed at ...)`` / ``(anonymous at ...)`` spellings
        never reach the IR. An anonymous declaration resolves to the name bound
        from its declarator, or to None when nothing references it (a genuinely
        nameless declaration, which every writer can render).
        """
        if not self._is_anonymous_decl(cursor):
            return cursor.spelling or None
        return self._anon_names.get(self._anon_key(cursor))

    def _require_anon_name(self, cursor: Any) -> str:
        """Return a tag name for an anonymous declaration used in type position.

        A type reference must name something, so an unbound declaration falls
        back to a per-translation-unit counter slug. The counter is used instead
        of the source location because file:line:col makes output depend on
        formatting.
        """
        key = self._anon_key(cursor)
        existing = self._anon_names.get(key)
        if existing is not None:
            return existing
        self._anon_counter += 1
        kind_word = {
            CursorKind.UNION_DECL: "union",
            CursorKind.ENUM_DECL: "enum",
        }.get(cursor.kind, "struct")
        generated = f"_anon_{kind_word}_{self._anon_counter}"
        self._anon_names[key] = generated
        return generated

    def _process_struct(
        self, cursor: Any, is_union: bool, is_cppclass: bool = False, *, emit: bool = True
    ) -> Struct | None:
        """Process a struct/union/class declaration.

        :param emit: When False the record is returned without being appended to
            the translation unit's declarations. A C++ nested class uses this:
            it belongs inside its parent's body, not at the top level.
        :returns: The record, or None when the cursor yields no declaration.
        """
        name = self._normalize_anon_name(cursor)

        # A C11 anonymous member -- ``struct { ... };`` with no declarator -- is
        # flattened into the parent's field list, not emitted separately. A
        # *named* member whose type happens to be an anonymous struct
        # (``struct { ... } css;``) is a different construct: it keeps its
        # member and needs its own tag, so ``name`` is bound and it falls
        # through to normal emission.
        if name is None:
            try:
                parent = cursor.semantic_parent
                if (
                    parent
                    and parent.kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.CLASS_DECL)
                    and self._is_anonymous_decl(cursor)
                ):
                    return None
            except Exception:
                pass

        # Check if this is a template specialization
        # Method 1: Check specialized_template attribute (reliable when available)
        is_specialization = False
        try:
            specialized_template = cursor.specialized_template
            if specialized_template is not None and specialized_template != cursor:
                is_specialization = True
        except AttributeError:
            pass

        # Method 2: Fallback detection using displayname pattern
        # If cursor is CLASS_DECL/STRUCT_DECL (not CLASS_TEMPLATE) but displayname
        # contains template args like "Vector<bool>", it's a specialization
        if not is_specialization and is_cppclass:
            displayname = cursor.displayname
            if "<" in displayname and ">" in displayname:
                # This is a specialization - displayname has template args but
                # it's not a CLASS_TEMPLATE (which would be the primary template)
                if cursor.kind != CursorKind.CLASS_TEMPLATE:
                    is_specialization = True

        # Determine the key prefix for deduplication
        if is_cppclass:
            key_prefix = "class"
        elif is_union:
            key_prefix = "union"
        else:
            key_prefix = "struct"

        # For specializations, use display name for deduplication key
        if is_specialization:
            key = self._record_key(key_prefix, cursor.displayname)
        else:
            key = self._record_key(key_prefix, name)

        # Forward declarations have no definition - output as opaque type
        is_forward_decl = not cursor.is_definition()

        # Handle seen tracking:
        # - If we've seen a definition, skip any subsequent declarations
        # - If we've only seen a forward declaration, a definition should replace it
        definition_key = f"{key}:definition"
        # A nested record is keyed only by its own tag, which is not unique: a
        # global `struct view` and a class member `struct view` collide. The
        # nested one is scoped to its parent and never reaches the top level, so
        # it neither consults nor updates the translation-unit dedup set.
        # Sharing it silently dropped whichever of the two was seen second.
        if emit:
            if definition_key in self._seen:
                # Already have a definition, skip this
                return None

            if is_forward_decl:
                # Only emit forward declaration if we haven't seen this type at all
                if key in self._seen:
                    return None
                self._seen.add(key)
            else:
                # This is a definition - mark it and remove any prior forward declaration
                self._seen.add(definition_key)
                if key in self._seen:
                    # We previously emitted a forward declaration - need to remove it
                    # and replace with the definition
                    self._remove_forward_declaration(name, key_prefix)
                self._seen.add(key)

        fields: list[Field] = []
        methods: list[Function] = []
        bases: list[BaseSpecifier] = []
        constructors: list[Function] = []
        destructor: Function | None = None
        conversions: list[Function] = []
        notes: list[str] = []
        nested_records: list[Struct] = []

        is_abstract = False
        if not is_forward_decl and (is_cppclass or cursor.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL)):
            try:
                is_abstract = bool(cursor.is_abstract_record())
            except Exception:
                is_abstract = False

        if not is_forward_decl:
            for child in cursor.get_children():
                if child.kind == CursorKind.CXX_BASE_SPECIFIER:
                    base_name = child.spelling or (child.type.spelling if child.type else "")
                    if base_name.startswith("class "):
                        base_name = base_name[6:]
                    elif base_name.startswith("struct "):
                        base_name = base_name[7:]
                    access = self._get_access_specifier(child) or "public"
                    is_virt = False
                    with contextlib.suppress(Exception):
                        is_virt = bool(child.is_virtual_base())
                    if not is_virt:
                        with contextlib.suppress(Exception):
                            is_virt = any(t.spelling == "virtual" for t in child.get_tokens())
                    bases.append(BaseSpecifier(name=base_name, access=access, is_virtual=is_virt))
                elif child.kind == CursorKind.FIELD_DECL:
                    field = self._convert_field(child)
                    if field:
                        fields.append(field)
                    elif not self._is_padding_bitfield(child):
                        notes.append(
                            f"Field '{child.spelling}' skipped: unable to represent type '{child.type.spelling}'"
                        )
                elif child.kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL):
                    if not self._is_anonymous_decl(child):
                        # A tagged record *defined* inside another record body
                        # still needs a top-level definition; a field using it
                        # by value would otherwise name an incomplete type.
                        # Non-definitions are left alone: a bare ``struct x *p``
                        # member introduces the tag without a body, and the
                        # writer already emits those forward declarations.
                        # A C++ nested class is NOT lifted to the top level:
                        # its name is only meaningful when qualified by the
                        # enclosing class, and flattening it would violate
                        # scope integrity. It is kept as a nested record, which
                        # the writer renders inside the parent's body where the
                        # qualification is implicit.
                        if child.is_definition():
                            if is_cppclass:
                                nested = self._process_struct(
                                    child,
                                    is_union=child.kind == CursorKind.UNION_DECL,
                                    emit=False,
                                )
                                if nested is not None:
                                    nested_records.append(nested)
                            else:
                                self._process_struct(child, is_union=child.kind == CursorKind.UNION_DECL)
                    elif self._normalize_anon_name(child) is None:
                        nested = self._build_anonymous_record(child)
                        if nested is not None:
                            fields.append(self._make_anonymous_field(nested))
                    else:
                        # A named member's anonymous type still needs a
                        # top-level definition; the member that follows
                        # refers to it by the tag bound during the prescan.
                        self._process_struct(child, is_union=child.kind == CursorKind.UNION_DECL)
                elif child.kind == CursorKind.ENUM_DECL:
                    # An enum declared inside a record body still needs a
                    # top-level declaration; fields referring to it would
                    # otherwise name an undeclared type.
                    self._process_enum(child)
                elif child.kind == CursorKind.VAR_DECL and (is_cppclass or cursor.kind == CursorKind.STRUCT_DECL):
                    # Static member variable
                    field = self._convert_field(child)
                    if field:
                        field.is_static = True
                        fields.append(field)
                    else:
                        notes.append(
                            f"Static field '{child.spelling}' skipped: unable to represent type '{child.type.spelling}'"
                        )
                elif child.kind == CursorKind.CONSTRUCTOR:
                    ctor = self._convert_method(child, is_constructor=True)
                    if ctor:
                        constructors.append(ctor)
                    else:
                        notes.append(f"Constructor '{child.spelling}' skipped: unable to represent parameter types")
                elif child.kind == CursorKind.DESTRUCTOR:
                    dtor = self._convert_method(child, is_destructor=True)
                    if dtor:
                        destructor = dtor
                    else:
                        notes.append(f"Destructor '{child.spelling}' skipped: unable to represent parameter types")
                elif child.kind == CursorKind.CONVERSION_FUNCTION:
                    conv = self._convert_method(child, is_conversion=True)
                    if conv:
                        conversions.append(conv)
                    else:
                        notes.append(
                            f"Conversion function '{child.spelling}' skipped: unable to represent return type '{child.result_type.spelling}'"
                        )
                elif child.kind in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_TEMPLATE) and (
                    is_cppclass or cursor.kind == CursorKind.STRUCT_DECL
                ):
                    method = self._convert_method(child)
                    if method:
                        methods.append(method)
                    else:
                        notes.append(f"Method '{child.spelling}' skipped: unable to represent signature")

        # Handle template specialization
        cpp_name = None
        if is_specialization:
            cpp_name = cursor.displayname
            name = _mangle_specialization_name(cpp_name)

        attrs, is_deprecated = self._get_attributes(cursor)
        alignment = self._get_alignment(cursor)
        is_packed = self._is_packed(cursor)
        pack_note = self._pack_note(cursor)
        if pack_note is not None:
            notes.append(pack_note)
        unmeasured_note = self._unmeasured_bitfield_align_note(cursor)
        if unmeasured_note is not None:
            notes.append(unmeasured_note)
        unresolved_note = self._unresolved_alignment_note(cursor)
        if unresolved_note is not None:
            notes.append(unresolved_note)
        vtable_entries = [m for m in methods if m.is_virtual or m.is_pure_virtual]

        struct = Struct(
            name=name,
            fields=fields,
            methods=methods,
            is_union=is_union,
            is_cppclass=is_cppclass,
            is_packed=is_packed,
            namespace=self._current_namespace,
            cpp_name=cpp_name,
            notes=notes,
            bases=bases,
            is_abstract=is_abstract,
            constructors=constructors,
            destructor=destructor,
            conversions=conversions,
            vtable_entries=vtable_entries,
            attributes=attrs,
            is_deprecated=is_deprecated,
            alignment=alignment,
            location=self._get_location(cursor),
            nested_records=nested_records,
        )
        if emit:
            self.declarations.append(struct)
        return struct

    def _process_class_template(self, cursor: Any) -> None:
        """Process a C++ class template declaration."""
        name = cursor.spelling or None
        if not name:
            return

        # Skip if already processed
        key = f"template:{name}"
        if key in self._seen:
            return
        self._seen.add(key)

        # Extract template type parameters and track non-type parameters
        template_params: list[str] = []
        nontype_params: list[tuple[str, str]] = []
        fields: list[Field] = []
        methods: list[Function] = []
        bases: list[BaseSpecifier] = []
        constructors: list[Function] = []
        destructor: Function | None = None
        conversions: list[Function] = []
        notes: list[str] = []
        inner_typedefs: dict[str, str] = {}

        is_abstract = False
        try:
            is_abstract = bool(cursor.is_abstract_record())
        except Exception:
            is_abstract = False

        for child in cursor.get_children():
            if child.kind == CursorKind.TEMPLATE_TYPE_PARAMETER:
                param_name = child.spelling or f"T{len(template_params)}"
                template_params.append(param_name)
            elif child.kind == CursorKind.TEMPLATE_NON_TYPE_PARAMETER:
                # Non-type template parameters (e.g., template<int N>)
                # Cython doesn't support these directly, so track for note
                param_name = child.spelling or "N"
                param_type = child.type.spelling
                nontype_params.append((param_name, param_type))
            elif child.kind == CursorKind.CXX_BASE_SPECIFIER:
                base_name = child.spelling or (child.type.spelling if child.type else "")
                if base_name.startswith("class "):
                    base_name = base_name[6:]
                elif base_name.startswith("struct "):
                    base_name = base_name[7:]
                access = self._get_access_specifier(child) or "public"
                is_virt = False
                with contextlib.suppress(Exception):
                    is_virt = bool(child.is_virtual_base())
                if not is_virt:
                    with contextlib.suppress(Exception):
                        is_virt = any(t.spelling == "virtual" for t in child.get_tokens())
                bases.append(BaseSpecifier(name=base_name, access=access, is_virtual=is_virt))
            elif child.kind in (CursorKind.TYPEDEF_DECL, CursorKind.TYPE_ALIAS_DECL):
                # Inner aliases, in both spellings C++ offers: the classic
                # ``typedef Iterator<T, PT> iterator`` (TYPEDEF_DECL) and the
                # C++11 ``using type = T;`` (TYPE_ALIAS_DECL).  They mean the
                # same thing and both expose ``underlying_typedef_type``, but
                # clang reports them as distinct cursor kinds, so matching only
                # the first silently loses every ``using`` alias.
                typedef_name = child.spelling
                underlying = child.underlying_typedef_type.spelling
                if typedef_name and underlying:
                    inner_typedefs[typedef_name] = underlying
            elif child.kind == CursorKind.FIELD_DECL:
                field = self._convert_field(child)
                if field:
                    fields.append(field)
                elif not self._is_padding_bitfield(child):
                    notes.append(f"Field '{child.spelling}' skipped: unable to represent type '{child.type.spelling}'")
            elif child.kind == CursorKind.VAR_DECL:
                field = self._convert_field(child)
                if field:
                    field.is_static = True
                    fields.append(field)
                else:
                    notes.append(
                        f"Static field '{child.spelling}' skipped: unable to represent type '{child.type.spelling}'"
                    )
            elif child.kind == CursorKind.CONSTRUCTOR:
                ctor = self._convert_method(child, is_constructor=True)
                if ctor:
                    constructors.append(ctor)
                else:
                    notes.append(f"Constructor '{child.spelling}' skipped: unable to represent parameter types")
            elif child.kind == CursorKind.DESTRUCTOR:
                dtor = self._convert_method(child, is_destructor=True)
                if dtor:
                    destructor = dtor
                else:
                    notes.append(f"Destructor '{child.spelling}' skipped: unable to represent parameter types")
            elif child.kind == CursorKind.CONVERSION_FUNCTION:
                conv = self._convert_method(child, is_conversion=True)
                if conv:
                    conversions.append(conv)
                else:
                    notes.append(
                        f"Conversion function '{child.spelling}' skipped: unable to represent return type '{child.result_type.spelling}'"
                    )
            elif child.kind in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_TEMPLATE):
                method = self._convert_method(child)
                if method:
                    methods.append(method)
                else:
                    notes.append(f"Method '{child.spelling}' skipped: unable to represent signature")

        if not template_params:
            # No template parameters found - treat as regular class
            return

        # Add note if non-type parameters exist
        if nontype_params:
            for param_name, param_type in nontype_params:
                notes.append(
                    f"NOTE: Template has non-type parameter '{param_name}' ({param_type}). "
                    "Cython does not support non-type template parameters. "
                    "Use specific instantiations as needed."
                )

        vtable_entries = [m for m in methods if m.is_virtual or m.is_pure_virtual]

        struct = Struct(
            name=name,
            fields=fields,
            methods=methods,
            is_union=False,
            is_cppclass=True,
            namespace=self._current_namespace,
            template_params=template_params,
            notes=notes,
            inner_typedefs=inner_typedefs,
            bases=bases,
            is_abstract=is_abstract,
            constructors=constructors,
            destructor=destructor,
            conversions=conversions,
            vtable_entries=vtable_entries,
            location=self._get_location(cursor),
        )
        self.declarations.append(struct)

    def _process_partial_specialization(self, cursor: Any) -> None:
        """Process a C++ partial template specialization.

        Partial specializations cannot be represented in Cython, but we emit
        a comment/note to note their existence so users are aware.
        """
        display_name = cursor.displayname or cursor.spelling or "unknown"

        # Get the base template name (e.g., "Container" from "Container<T*>")
        base_name = cursor.spelling or None

        # Skip if no name
        if not base_name:
            return

        # Create a unique key for this partial specialization
        key = f"partial_spec:{display_name}"
        if key in self._seen:
            return
        self._seen.add(key)

        # Check if the base template is already in declarations and attach note
        for d in self.declarations:
            if isinstance(d, Struct) and d.name == base_name:
                note = (
                    f"NOTE: Partial specialization {display_name} exists in C++ "
                    "but cannot be declared in Cython. Use specific instantiations."
                )
                if note not in d.notes:
                    d.notes.append(note)
                return

        # Emit comment declaration as fallback
        comment = Constant(
            name=f"/* Partial template specialization: {display_name} */",
            value="",
            type=CType("void"),
            location=self._get_location(cursor),
        )
        self.declarations.append(comment)

    def _enum_is_typedef_only(self, cursor: Any) -> bool:
        """Return True when the enum has no C tag and exists only under a typedef name.

        ``typedef enum { ... } Name;`` introduces no ``enum Name`` tag, so Cython must
        emit ``ctypedef enum Name`` -- ``cdef enum Name`` would reference a tag that
        does not exist and yield an incomplete type in the generated C.

        clang spells the cursor's type as ``enum <tag>`` exactly when a real tag
        exists, and as the bare name when the enum is tag-less. C++ has no separate
        tag namespace and never spells the ``enum`` prefix, so the check applies to C
        only; C++ enums keep their existing ``cdef enum`` form.
        """
        if self.is_cplus:
            return False
        return not str(cursor.type.spelling).startswith("enum ")

    def _enum_cpp_name(self, cursor: Any, name: str | None) -> str | None:
        """Return the tag's full C++ spelling when a record encloses it, else None.

        A member enum is hoisted to the top level, which drops the record from
        its spelling: ``class C { enum M; }`` reaches the writer as a bare ``M``
        that names no type.  ``namespace`` cannot carry the record, so the
        qualification is recorded separately.  When no record encloses the tag,
        ``namespace`` plus ``name`` is already the whole spelling and this
        returns None so the existing output is unchanged.
        """
        if not name:
            return None
        record_kinds = (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.CLASS_TEMPLATE)
        records: list[str] = []
        parent = cursor.semantic_parent
        while parent is not None and parent.kind in record_kinds:
            if not parent.spelling:
                return None
            records.append(parent.spelling)
            parent = parent.semantic_parent
        if not records:
            return None
        scopes = [*records[::-1], name]
        if self._current_namespace:
            scopes.insert(0, self._current_namespace)
        return "::".join(scopes)

    def _process_enum(self, cursor: Any) -> None:
        """Process an enum declaration."""
        name = self._normalize_anon_name(cursor)

        # A forward declaration is redundant when the tag is also defined in this
        # translation unit -- the definition carries the values, so it alone is
        # emitted and both source orderings collapse to one declaration.
        #
        # When the tag is never defined, the forward declaration is the *only*
        # mention of the type, and dropping it loses the type entirely: a
        # `void use(enum E *p);` alongside it renders as `void use(E* p)` with `E`
        # undeclared, which Cython rejects with "'E' is not a type identifier".
        # The opaque record path above already keeps such declarations, so
        # dropping them here made enums inconsistent with structs and unions.
        if not cursor.is_definition() and cursor.get_definition() is not None:
            return

        # Skip if already processed. Unnamed enums are keyed by declaration
        # identity so that a nested enum reached from both the record body and
        # the field that uses it is still emitted once.
        # A member enum is keyed by its record-qualified spelling, so a
        # ``C1::E`` beside a ``C2::E`` are two tags rather than one that
        # silently discards the second class's enumerators.
        cpp_name = self._enum_cpp_name(cursor, name) if self.is_cplus else None
        key = self._record_key("enum", cpp_name or (name if name else self._anon_key(cursor)))
        if key in self._seen:
            return
        self._seen.add(key)

        values: list[EnumValue] = []
        for child in cursor.get_children():
            if child.kind == CursorKind.ENUM_CONSTANT_DECL:
                values.append(EnumValue(name=child.spelling, value=child.enum_value))

        enum = Enum(
            name=name,
            values=values,
            is_typedef=self._enum_is_typedef_only(cursor),
            namespace=self._current_namespace,
            location=self._get_location(cursor),
            is_scoped=bool(self.is_cplus and cursor.is_scoped_enum()),
            cpp_name=cpp_name,
        )
        self.declarations.append(enum)

    def _process_function(self, cursor: Any) -> None:
        """Process a function or function template declaration."""
        name = cursor.spelling
        if not name:
            return

        # Skip if already processed (distinguish function templates from standard functions and overloads)
        is_template = cursor.kind == CursorKind.FUNCTION_TEMPLATE
        prefix = "template_function" if is_template else "function"
        key = f"{prefix}:{self._current_namespace or ''}:{name}:{cursor.type.spelling}"
        if key in self._seen:
            return
        self._seen.add(key)

        template_params: list[str] = []
        for child in cursor.get_children():
            if child.kind == CursorKind.TEMPLATE_TYPE_PARAMETER:
                template_params.append(child.spelling or f"T{len(template_params)}")

        return_type = self._convert_type(cursor.result_type)
        if not return_type:
            return

        parameters: list[Parameter] = []
        try:
            is_variadic = cursor.type.is_function_variadic()
        except Exception:
            is_variadic = False

        args = list(cursor.get_arguments())
        if not args:
            args = [c for c in cursor.get_children() if c.kind == CursorKind.PARM_DECL]

        for arg in args:
            param_type = self._convert_type(arg.type)
            if param_type:
                # Skip void parameter
                if isinstance(param_type, CType) and param_type.name == "void":
                    continue
                self._apply_param_names(param_type, arg)
                default_val = self._get_default_argument(arg)
                parameters.append(Parameter(name=arg.spelling or None, type=param_type, default_value=default_val))

        is_noexcept = self._is_noexcept(cursor)
        is_inline, body = self._is_inline_function(cursor)
        attrs, is_deprecated = self._get_attributes(cursor)

        func = Function(
            name=name,
            return_type=return_type,
            parameters=parameters,
            is_variadic=is_variadic,
            namespace=self._current_namespace,
            template_params=template_params,
            is_noexcept=is_noexcept,
            is_inline=is_inline,
            body=body,
            attributes=attrs,
            is_deprecated=is_deprecated,
            location=self._get_location(cursor),
        )
        self.declarations.append(func)

    def _convert_method(
        self,
        cursor: Any,
        *,
        is_constructor: bool = False,
        is_destructor: bool = False,
        is_conversion: bool = False,
    ) -> Function | None:
        """Convert a C++ method, constructor, destructor, or conversion function to a Function IR node."""
        name = cursor.spelling
        if not name:
            return None

        template_params: list[str] = []
        for child in cursor.get_children():
            if child.kind == CursorKind.TEMPLATE_TYPE_PARAMETER:
                template_params.append(child.spelling or f"T{len(template_params)}")

        return_type: TypeExpr | None
        if is_constructor or is_destructor:
            return_type = CType("void")
        else:
            return_type = self._convert_type(cursor.result_type)

        if return_type is None:
            return None

        parameters: list[Parameter] = []
        try:
            is_variadic = cursor.type.is_function_variadic()
        except Exception:
            is_variadic = False

        args = list(cursor.get_arguments())
        if not args:
            args = [c for c in cursor.get_children() if c.kind == CursorKind.PARM_DECL]

        for arg in args:
            param_type = self._convert_type(arg.type)
            if param_type:
                # Skip void parameter
                if isinstance(param_type, CType) and param_type.name == "void":
                    continue
                self._apply_param_names(param_type, arg)
                default_val = self._get_default_argument(arg)
                parameters.append(Parameter(name=arg.spelling or None, type=param_type, default_value=default_val))
            else:
                # Parameter type could not be converted
                return None

        # Extract C++ method attributes
        is_static = False
        is_const = False
        is_virtual = False
        is_pure_virtual = False
        is_explicit = False
        is_deleted = False
        is_defaulted = False

        with contextlib.suppress(Exception):
            is_static = bool(cursor.is_static_method())
        with contextlib.suppress(Exception):
            is_const = bool(cursor.is_const_method())
        with contextlib.suppress(Exception):
            is_virtual = bool(cursor.is_virtual_method())
        with contextlib.suppress(Exception):
            is_pure_virtual = bool(cursor.is_pure_virtual_method())
        with contextlib.suppress(Exception):
            is_explicit = bool(cursor.is_explicit_method())
        with contextlib.suppress(Exception):
            is_deleted = bool(cursor.is_deleted_method())
        with contextlib.suppress(Exception):
            is_defaulted = bool(cursor.is_default_method())

        is_noexcept = self._is_noexcept(cursor)
        is_inline, body = self._is_inline_function(cursor)
        access = self._get_access_specifier(cursor)
        attrs, is_deprecated = self._get_attributes(cursor)

        return Function(
            name=name,
            return_type=return_type,
            parameters=parameters,
            is_variadic=is_variadic,
            template_params=template_params,
            is_static=is_static,
            is_const=is_const,
            is_virtual=is_virtual,
            is_pure_virtual=is_pure_virtual,
            is_explicit=is_explicit,
            access=access,
            is_deleted=is_deleted,
            is_defaulted=is_defaulted,
            is_noexcept=is_noexcept,
            is_inline=is_inline,
            body=body,
            attributes=attrs,
            is_deprecated=is_deprecated,
            location=self._get_location(cursor),
        )

    def _process_typedef(self, cursor: Any) -> None:
        """Process a typedef declaration."""
        name = cursor.spelling
        if not name:
            return

        # Skip if already processed
        key = self._record_key("typedef", name)
        if key in self._seen:
            return
        self._seen.add(key)

        underlying = cursor.underlying_typedef_type

        # Skip typedefs that reference compiler builtin types
        # These are internal to GCC/Clang and cannot be used in Cython
        underlying_spelling = underlying.spelling
        if underlying_spelling.startswith("__builtin_"):
            return

        # Special handling for struct/union typedefs that have inline definitions
        # e.g., typedef struct foo { int x; } foo_t;
        # We need to emit the struct definition first, then the typedef
        if underlying.kind in (TypeKind.RECORD, TypeKind.ELABORATED):
            # Get the actual record type
            record_type = underlying
            if underlying.kind == TypeKind.ELABORATED:
                record_type = underlying.get_named_type()

            if record_type.kind == TypeKind.RECORD:
                decl = record_type.get_declaration()
                # Check if this is a struct/union with a definition (not forward decl)
                if decl.is_definition():
                    struct_name = decl.spelling
                    # Only emit the struct if we haven't emitted a definition
                    key_prefix = "union" if decl.kind == CursorKind.UNION_DECL else "struct"
                    struct_key = self._record_key(key_prefix, struct_name)
                    definition_key = f"{struct_key}:definition"

                    # Check if this is typedef struct Foo {...} Foo; pattern
                    is_typedef_pattern = struct_name == name

                    # Check if we already have a definition - if so, update it
                    if definition_key in self._seen:
                        # Struct was already processed - update its is_typedef flag if needed
                        if is_typedef_pattern:
                            # Find and update the existing struct
                            for i, existing_decl in enumerate(self.declarations):
                                if (
                                    isinstance(existing_decl, Struct)
                                    and existing_decl.name == struct_name
                                    and existing_decl.namespace == self._current_namespace
                                ):
                                    # Replace with typedef'd version
                                    self.declarations[i] = replace(existing_decl, is_typedef=True)
                                    break
                        # Don't create another struct, but might still need typedef
                    else:
                        # First time seeing this struct - create it
                        self._seen.add(struct_key)
                        self._seen.add(definition_key)  # Mark definition as seen
                        is_union = decl.kind == CursorKind.UNION_DECL

                        fields: list[Field] = []
                        for child in decl.get_children():
                            if child.kind == CursorKind.FIELD_DECL:
                                field = self._convert_field(child)
                                if field:
                                    fields.append(field)

                        struct = Struct(
                            name=struct_name or None,
                            fields=fields,
                            methods=[],
                            is_union=is_union,
                            is_cppclass=False,
                            namespace=self._current_namespace,
                            location=self._get_location(decl),
                            is_typedef=is_typedef_pattern,
                        )
                        self.declarations.append(struct)

                    # If struct name == typedef name, we've already handled it above
                    # Only create separate typedef if names differ
                    if struct_name and struct_name != name:
                        underlying_type: TypeExpr = CType(name=struct_name)  # Use just the name, not "struct name"
                        attrs, is_deprecated = self._get_attributes(cursor)
                        typedef = Typedef(
                            name=name,
                            underlying_type=underlying_type,
                            namespace=self._current_namespace,
                            attributes=attrs,
                            is_deprecated=is_deprecated,
                            location=self._get_location(cursor),
                        )
                        self.declarations.append(typedef)
                    return

        # Resolve compile-time expressions (decltype, sizeof) to canonical types
        # This handles cases like: typedef decltype(nullptr) nullptr_t;
        underlying_spelling = underlying.spelling
        if "decltype(" in underlying_spelling or "sizeof(" in underlying_spelling:
            # Try to resolve to canonical type
            canonical = underlying.get_canonical()
            canonical_type = self._convert_type(canonical)

            if canonical_type:
                # Successfully resolved - use canonical type
                attrs, is_deprecated = self._get_attributes(cursor)
                typedef = Typedef(
                    name=name,
                    underlying_type=canonical_type,
                    namespace=self._current_namespace,
                    attributes=attrs,
                    is_deprecated=is_deprecated,
                    location=self._get_location(cursor),
                )
                self.declarations.append(typedef)
                return

        attrs, is_deprecated = self._get_attributes(cursor)

        # Standard typedef handling
        standard_underlying_type = self._convert_type(underlying)
        if not standard_underlying_type:
            return

        # A typedef's declarator carries the PARM_DECL children that clang's
        # FUNCTIONPROTO type omits, exactly as a field or variable declarator does.
        self._apply_param_names(standard_underlying_type, cursor)

        typedef = Typedef(
            name=name,
            underlying_type=standard_underlying_type,
            namespace=self._current_namespace,
            attributes=attrs,
            is_deprecated=is_deprecated,
            location=self._get_location(cursor),
        )
        self.declarations.append(typedef)

    def _process_variable(self, cursor: Any) -> None:
        """Process a variable declaration."""
        name = cursor.spelling
        if not name:
            return

        # Skip if already processed
        key = self._record_key("var", name)
        if key in self._seen:
            return
        self._seen.add(key)

        var_type = self._convert_type(cursor.type)
        if not var_type:
            return

        self._apply_param_names(var_type, cursor)

        attrs, is_deprecated = self._get_attributes(cursor)
        alignment = self._get_alignment(cursor)

        var = Variable(
            name=name,
            type=var_type,
            namespace=self._current_namespace,
            attributes=attrs,
            is_deprecated=is_deprecated,
            alignment=alignment,
            location=self._get_location(cursor),
        )
        self.declarations.append(var)

    def _convert_field(self, cursor: Any) -> Field | None:
        """Convert a field cursor to IR Field."""
        name = cursor.spelling
        is_transparent = False

        # An unnamed bitfield (``int : 4;``) is padding, not a member. It is
        # still carried in the IR: a consumer that reconstructs layout cannot
        # place the following fields without it.
        if self._is_padding_bitfield(cursor):
            pad_type = self._convert_type(cursor.type)
            if not pad_type:
                return None
            return Field(
                name="",
                type=pad_type,
                bit_width=self._get_bitfield_width(cursor),
                access=self._get_access_specifier(cursor),
                is_padding=True,
            )

        # Only a field with no name of its own is transparent. ``cursor.is_anonymous()``
        # is also True for a *named* field whose type happens to be an anonymous
        # record or enum (``enum { X } e;``), which is an ordinary named member.
        if not name or name.startswith("(unnamed") or "(anonymous" in name:
            is_transparent = True
            name = ""

        field_type = self._convert_type(cursor.type)
        if not field_type:
            return None

        self._apply_param_names(field_type, cursor)

        access = self._get_access_specifier(cursor)
        return Field(
            name=name,
            type=field_type,
            bit_width=self._get_bitfield_width(cursor),
            is_anonymous_transparent=is_transparent,
            access=access,
        )

    @staticmethod
    def _is_padding_bitfield(cursor: Any) -> bool:
        """True for an unnamed bitfield (``int : 3;``), which is padding, not a member.

        C17 6.7.2.1p13 gives such a declarator no member name, so it is not
        addressable. The Field carries ``is_padding=True`` so that writers
        emitting C source skip it -- a nameless member in their output would
        be wrong -- while the ctypes writer, which must reproduce the layout
        itself, can name the bits it has to reserve.
        """
        if cursor.spelling:
            return False
        with contextlib.suppress(Exception):
            return bool(cursor.is_bitfield())
        return False

    @staticmethod
    def _get_bitfield_width(cursor: Any) -> int | None:
        """Bit width of a bitfield member, or None when the field is not a bitfield.

        ``clang_getFieldDeclBitWidth`` answers -1 for a non-bitfield cursor rather
        than failing, so the sign is the discriminator. Pairing it with an
        ``is_bitfield()`` pre-check would add a branch that can never decide the
        outcome, hiding a regression in either one behind the other.
        """
        with contextlib.suppress(Exception):
            width = int(cursor.get_bitfield_width())
            return width if width >= 0 else None
        return None

    def _apply_param_names(self, type_expr: TypeExpr | None, cursor: Any) -> None:
        """Recover function-pointer parameter names from a declarator's PARM_DECL children.

        Clang's FUNCTIONPROTO *type* carries no argument names, so a function
        pointer built from the type alone renders as ``(int, char)``. The
        declaring cursor does carry them, as PARM_DECL children.
        """
        func_ptr = type_expr
        if isinstance(func_ptr, Pointer):
            func_ptr = func_ptr.pointee
        if not isinstance(func_ptr, FunctionPointer):
            return

        names: list[str] = []
        with contextlib.suppress(Exception):
            names = [child.spelling for child in cursor.get_children() if child.kind == CursorKind.PARM_DECL]

        # A mismatch means the PARM_DECL children do not belong to this
        # signature (for example a function pointer returning a function
        # pointer, whose children are flattened). Leave the names unset rather
        # than pairing them wrongly.
        if len(names) != len(func_ptr.parameters):
            return

        for param, param_name in zip(func_ptr.parameters, names, strict=True):
            if param_name and not param.name:
                param.name = param_name

    def _build_anonymous_record(self, cursor: Any) -> Struct | None:
        """Build the IR Struct for an anonymous record nested inside another record.

        The result is carried on ``Field.anonymous_struct`` so that writers can
        flatten its members into the enclosing record, which is what C11
        transparent members mean.
        """
        fields: list[Field] = []
        for child in cursor.get_children():
            if child.kind == CursorKind.FIELD_DECL:
                field = self._convert_field(child)
                if field:
                    fields.append(field)
            elif (
                child.kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL)
                and self._is_anonymous_decl(child)
                and self._normalize_anon_name(child) is None
            ):
                nested = self._build_anonymous_record(child)
                if nested is not None:
                    fields.append(self._make_anonymous_field(nested))
            elif child.kind == CursorKind.ENUM_DECL:
                self._process_enum(child)

        if not fields:
            return None
        return Struct(name=None, fields=fields, is_union=cursor.kind == CursorKind.UNION_DECL)

    @staticmethod
    def _make_anonymous_field(nested: Struct) -> Field:
        return Field(name="", type=CType(name="void"), is_anonymous_transparent=True, anonymous_struct=nested)

    @staticmethod
    def _extract_quals(clang_type: Any) -> list[str]:
        """Extract cv-qualifiers carried directly by a clang type.

        Only ``const`` and ``volatile`` are reported. The qualifiers the Cython
        writer deliberately drops (``_Atomic``, ``__restrict``, ``_Noreturn``)
        are not clang cv-qualifiers and are unaffected by this.
        """
        quals: list[str] = []
        with contextlib.suppress(Exception):
            if clang_type.is_const_qualified():
                quals.append("const")
        with contextlib.suppress(Exception):
            if clang_type.is_volatile_qualified():
                quals.append("volatile")
        return quals

    @staticmethod
    def _merge_quals(type_expr: TypeExpr, quals: list[str]) -> None:
        """Add qualifiers to an already-built type expression, without duplicating."""
        existing = getattr(type_expr, "qualifiers", None)
        if existing is None:
            return
        for qual in quals:
            if qual not in existing:
                existing.append(qual)

    def _resolve_member_alias(self, clang_type: Any, decl: Any) -> TypeExpr | None:
        """Resolve ``Owner<int>::type`` to the type it actually names.

        A ``typedef`` or C++11 ``using`` alias declared *inside* a class is
        reached from the outside through a qualified name, but libclang reports
        its declaration cursor's spelling as the bare member name -- ``type``.
        Emitting that bare name loses the owner entirely and produces a
        dangling identifier that names nothing at the output's top level.

        The qualified spelling is not usable either: ``types::remove_reference<int>::type``
        has no valid spelling in a Cython declaration.  For a *concrete*
        instantiation the canonical type is exact and fully resolved -- clang has
        already done the substitution -- so it carries the same meaning with a
        name the writer can emit.  That is what this returns.

        Returns ``None`` (leaving the caller's bare-name behaviour intact) when
        the alias is not a class member, or when the canonical type is still
        dependent, which is the case inside an uninstantiated template where the
        bare member name is the correct thing to emit.
        """
        # Cursor kinds whose members are reached through a qualified name
        # (``Owner::member``) rather than by the bare member name.  Built here
        # rather than at class scope because ``CursorKind`` is bound lazily,
        # after the libclang shared library is located.
        record_parent_kinds = (
            CursorKind.STRUCT_DECL,
            CursorKind.UNION_DECL,
            CursorKind.CLASS_DECL,
            CursorKind.CLASS_TEMPLATE,
            CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
        )
        parent = decl.semantic_parent
        if parent is None or parent.kind not in record_parent_kinds:
            return None

        canonical = clang_type.get_canonical()
        # A canonical type that is still unexposed/dependent has not been
        # substituted, so it carries no more information than the bare name.
        if canonical.kind in (TypeKind.UNEXPOSED, TypeKind.INVALID, TypeKind.DEPENDENT):
            return None
        # Guard against a canonical type that loops straight back to this same
        # typedef; converting it would recurse without making progress.
        if canonical.kind == TypeKind.TYPEDEF:
            return None

        resolved = self._convert_type(canonical)
        if resolved is None:
            return None
        self._merge_quals(resolved, self._extract_quals(clang_type))
        return resolved

    def _convert_type(self, clang_type: Any) -> TypeExpr | None:
        """Convert a libclang Type to our IR type expression."""
        # Get canonical type for consistency
        kind = clang_type.kind

        # Handle pointer types
        if kind == TypeKind.POINTER:
            pointee = clang_type.get_pointee()
            ptr_quals = self._extract_quals(clang_type)

            # Check for function pointer. FUNCTIONNOPROTO covers unprototyped
            # ``()`` functions, which are legal C and must not fall through to
            # the opaque basic-type tail.
            if pointee.kind in (TypeKind.FUNCTIONPROTO, TypeKind.FUNCTIONNOPROTO):
                func_ptr = self._convert_function_type(pointee)
                if func_ptr:
                    return Pointer(pointee=func_ptr, qualifiers=ptr_quals)
                return None

            pointee_type = self._convert_type(pointee)
            if pointee_type:
                return Pointer(pointee=pointee_type, qualifiers=ptr_quals)
            return None

        # Handle array types
        if kind in (TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY, TypeKind.VARIABLEARRAY):
            element_type = self._convert_type(clang_type.element_type)
            if not element_type:
                return None

            size: int | str | None = None
            if kind == TypeKind.CONSTANTARRAY:
                size = clang_type.element_count
            # INCOMPLETEARRAY has no size (flexible array)
            # VARIABLEARRAY size is runtime-determined

            return Array(element_type=element_type, size=size)

        # Handle dependent-sized arrays (template parameter dependent)
        # These appear in templates like: template<int N> class Foo { T data[N]; };
        # Cython cannot represent these, so we convert to pointers
        if kind == TypeKind.DEPENDENTSIZEDARRAY:
            element_type = self._convert_type(clang_type.element_type)
            if not element_type:
                return None
            # Return as pointer since Cython can't represent dependent array sizes
            return Pointer(pointee=element_type)

        # Handle function types (for function pointer types)
        if kind == TypeKind.FUNCTIONPROTO:
            return self._convert_function_type(clang_type)

        # Handle elaborated types (struct X, enum Y, etc.)
        if kind == TypeKind.ELABORATED:
            # Read qualifiers off the elaborated type first: get_named_type()
            # drops them.
            quals = self._extract_quals(clang_type)
            named_type = clang_type.get_named_type()
            result = self._convert_type(named_type)
            if result is not None:
                self._merge_quals(result, quals)
            return result

        # Handle record (struct/union) types
        if kind == TypeKind.RECORD:
            decl = clang_type.get_declaration()
            quals = self._extract_quals(clang_type)
            name = self._normalize_anon_name(decl) or self._require_anon_name(decl)
            if decl.kind == CursorKind.UNION_DECL:
                return CType(name=f"union {name}", qualifiers=quals)
            return CType(name=f"struct {name}", qualifiers=quals)

        # Handle enum types
        if kind == TypeKind.ENUM:
            decl = clang_type.get_declaration()
            quals = self._extract_quals(clang_type)
            name = self._normalize_anon_name(decl) or self._require_anon_name(decl)
            return CType(name=f"enum {name}", qualifiers=quals)

        # Handle typedef types
        if kind == TypeKind.TYPEDEF:
            decl = clang_type.get_declaration()
            member_alias = self._resolve_member_alias(clang_type, decl)
            if member_alias is not None:
                return member_alias
            return CType(name=decl.spelling, qualifiers=self._extract_quals(clang_type))

        # Handle C++ reference types
        if kind == TypeKind.LVALUEREFERENCE:
            target = self._convert_type(clang_type.get_pointee())
            if target:
                return Reference(target=target, is_rvalue=False)
            return None
        if kind == TypeKind.RVALUEREFERENCE:
            target = self._convert_type(clang_type.get_pointee())
            if target:
                return Reference(target=target, is_rvalue=True)
            return None

        # Handle nullptr_t type (C++11)
        # std::nullptr_t resolves to TypeKind.NULLPTR
        # Map to void* since Cython doesn't have a nullptr_t type
        if kind == TypeKind.NULLPTR:
            return Pointer(pointee=CType(name="void"))

        # Handle basic types
        spelling = clang_type.spelling

        # Extract qualifiers
        qualifiers = self._extract_quals(clang_type)

        # Clean up the spelling to get base type
        base_type = spelling
        for qual in qualifiers:
            base_type = re.sub(r"\b" + re.escape(qual) + r"\b", "", base_type).strip()
        # Clean up any double spaces
        base_type = re.sub(r"\s+", " ", base_type).strip()

        return CType(name=base_type, qualifiers=qualifiers)

    def _convert_function_type(self, clang_type: Any) -> FunctionPointer | None:
        """Convert a function type to FunctionPointer."""
        result_type = self._convert_type(clang_type.get_result())
        if not result_type:
            return None

        parameters: list[Parameter] = []
        # An unprototyped ``()`` function (FUNCTIONNOPROTO) has neither a
        # variadic flag nor an argument list.
        is_variadic = False
        with contextlib.suppress(Exception):
            is_variadic = clang_type.is_function_variadic()

        arg_types: list[Any] = []
        with contextlib.suppress(Exception):
            arg_types = list(clang_type.argument_types())

        for arg_type in arg_types:
            param_type = self._convert_type(arg_type)
            if param_type:
                # Clang's function *type* carries no argument names; they are
                # recovered from the declaring cursor by _apply_param_names().
                parameters.append(Parameter(name=None, type=param_type))

        return FunctionPointer(
            return_type=result_type,
            parameters=parameters,
            is_variadic=is_variadic,
        )

    def _get_location(self, cursor: Any) -> SourceLocation | None:
        """Get source location from a cursor."""
        loc = cursor.location
        if loc.file:
            return SourceLocation(file=loc.file.name, line=loc.line, column=loc.column)
        return None


class LibclangBackend:
    """Parser backend using libclang.

    Uses LLVM's libclang to parse C and C++ code. This backend supports
    the full C++ language including templates, classes, and namespaces.

    Properties
    ----------
    name : str
        Returns ``"libclang"``.
    supports_macros : bool
        Returns ``False`` - macro extraction is limited in Python bindings.
    supports_cpp : bool
        Returns ``True`` - full C++ support.

    Example
    -------
    ::

        from headerkit.backends.libclang import LibclangBackend

        backend = LibclangBackend()

        # Parse C++ code with specific standard
        header = backend.parse(
            code,
            "myheader.hpp",
            extra_args=["-std=c++17", "-DDEBUG=1"]
        )
    """

    supported_languages: frozenset[str] = frozenset({"c", "cpp"})
    supported_classifications: frozenset[str] = frozenset({"header", "source"})

    def __init__(self) -> None:
        self._index: Any = None
        # Cache for parsed headers (path -> Header) to avoid re-parsing
        self._parse_cache: dict[str, Header] = {}
        # Visited set to prevent circular includes
        self._visited: set[str] = set()
        # Normalized paths clang itself classified as system headers, accumulated
        # across every translation unit this backend has parsed.
        self._clang_system_headers: set[str] = set()

    def _is_system_include(self, header_path: str, project_prefixes: tuple[str, ...] | None) -> bool:
        """Decide whether an included header is a system header.

        clang's answer is authoritative: it knows which search paths it treated
        as system (``-isystem``, its resource directory, the platform defaults),
        which no path heuristic can track across platforms. The heuristic in
        :func:`_is_system_header` remains as the fallback for paths clang has
        not classified in this process.

        :param header_path: Path to the included header.
        :param project_prefixes: Path prefixes that override every system classification.
        :returns: True if the header must not be descended into.
        """
        if _matches_project_prefix(header_path, project_prefixes):
            return False
        if normalize_path(str(header_path)) in self._clang_system_headers:
            return True
        return _is_system_header(header_path, project_prefixes)

    @property
    def name(self) -> str:
        return "libclang"

    @property
    def supports_macros(self) -> bool:
        # Supports simple numeric macros (#define NAME 123)
        # Complex macros (expressions, function-like) are not supported
        return True

    @property
    def supports_cpp(self) -> bool:
        return True

    def is_available(self) -> bool:
        return _configure_libclang()

    def _get_index(self) -> Any:
        """Get or create the clang index."""
        if self._index is None:
            self._index = _cindex.Index.create()
        return self._index

    def _resolve_include_path(
        self,
        include_path: str,
        base_dir: str,
        include_dirs: list[str],
    ) -> str | None:
        """Resolve an include path to an absolute path.

        :param include_path: The include path as it appears in the header
        :param base_dir: Directory of the including file
        :param include_dirs: List of include search directories
        :returns: Absolute path to the header file, or None if not found
        """
        # If already absolute, return as-is
        if os.path.isabs(include_path):
            if os.path.exists(include_path):
                return os.path.abspath(include_path)
            return None

        # Try relative to base directory first
        candidate = os.path.join(base_dir, include_path)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)

        # Try each include directory
        for inc_dir in include_dirs:
            candidate = os.path.join(inc_dir, include_path)
            if os.path.exists(candidate):
                return os.path.abspath(candidate)

        return None

    def _parse_header_file(
        self,
        header_path: str,
        include_dirs: list[str],
        extra_args: list[str],
        use_default_includes: bool,
    ) -> Header:
        """Parse a single header file.

        :param header_path: Absolute path to header file
        :param include_dirs: Include directories
        :param extra_args: Extra compiler arguments
        :param use_default_includes: Whether to use system includes
        :returns: Parsed Header IR
        """
        # Check cache
        if header_path in self._parse_cache:
            return self._parse_cache[header_path]

        # Read the file
        with open(header_path, encoding="utf-8", errors="replace") as f:
            code = f.read()

        # Parse using the main parse method
        # Use the basename for the filename to match expected behavior
        filename = os.path.basename(header_path)
        header = self.parse(
            code,
            filename,
            include_dirs=include_dirs,
            extra_args=extra_args,
            use_default_includes=use_default_includes,
            recursive_includes=False,  # Prevent infinite recursion
        )

        # Cache the result
        self._parse_cache[header_path] = header
        return header

    def _parse_recursively(
        self,
        main_header: Header,
        main_path: str,
        include_dirs: list[str],
        extra_args: list[str],
        use_default_includes: bool,
        max_depth: int,
        current_depth: int = 0,
        project_prefixes: tuple[str, ...] | None = None,
        allowlist_paths: _PathSet | None = None,
        denylist_paths: _PathSet | None = None,
    ) -> Header:
        """Recursively parse included headers and combine declarations.

        :param main_header: The main header that was parsed
        :param main_path: Path to the main header file
        :param include_dirs: Include directories
        :param extra_args: Extra compiler arguments
        :param use_default_includes: Whether to use system includes
        :param max_depth: Maximum recursion depth
        :param current_depth: Current recursion depth
        :param project_prefixes: Optional tuple of path prefixes to treat as project (not system)
        :param allowlist_paths: Resolved allowlist entries, or None when the caller supplied
            none. Included headers not named here are not descended into.
        :param denylist_paths: Resolved denylist entries, or None. Named headers are not
            descended into even when the allowlist names them -- deny wins over allow.
        :returns: Combined Header with declarations from all includes
        """
        if current_depth >= max_depth:
            return main_header

        all_declarations: list[Declaration] = list(main_header.declarations)
        main_dir = os.path.dirname(os.path.abspath(main_path))

        # Process each included header
        for include_path in main_header.included_headers:
            # Skip system headers (unless allowlisted via project_prefixes)
            if self._is_system_include(include_path, project_prefixes):
                continue

            # Get absolute path
            abs_path = self._resolve_include_path(
                include_path,
                main_dir,
                include_dirs,
            )

            if abs_path is None:
                # Could not resolve - skip
                continue

            # Check if already visited (circular include)
            if abs_path in self._visited:
                continue

            # The allowlist and denylist narrow the *merged* result, not only the
            # main translation unit: an included header the caller did not allow,
            # or explicitly denied, is never descended into and so contributes
            # nothing to the merge.  Deny wins over allow.
            resolved_include = _resolve_path(abs_path)
            if denylist_paths is not None and denylist_paths.matches(resolved_include):
                continue
            if allowlist_paths is not None and not allowlist_paths.matches(resolved_include):
                continue

            self._visited.add(abs_path)

            try:
                # Parse the included header
                sub_header = self._parse_header_file(
                    abs_path,
                    include_dirs,
                    extra_args,
                    use_default_includes,
                )

                # Recursively process its includes
                sub_header = self._parse_recursively(
                    sub_header,
                    abs_path,
                    include_dirs,
                    extra_args,
                    use_default_includes,
                    max_depth,
                    current_depth + 1,
                    project_prefixes,
                    allowlist_paths,
                    denylist_paths,
                )

                # Add declarations from sub-header
                all_declarations.extend(sub_header.declarations)

            except (OSError, RuntimeError, ValueError):
                import logging

                logging.getLogger(__name__).debug("Failed to parse included header: %s", abs_path, exc_info=True)
                continue

        # Deduplicate declarations
        unique_declarations = _deduplicate_declarations(all_declarations)

        # Return combined header
        return Header(
            path=main_header.path,
            declarations=unique_declarations,
            included_headers=main_header.included_headers,
        )

    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
        allowlist: list[str] | None = None,
        denylist: list[str] | None = None,
    ) -> Header:
        """Parse C/C++ code using libclang.

        Handles raw (unpreprocessed) code and performs preprocessing internally.

        Traversal and filtering
        -----------------------
        ``recursive_includes`` (True by default) governs *traversal*: it descends
        into each non-system header named by an ``#include``, parses it as its own
        translation unit, and merges what it declares.  ``project_prefixes``
        (through :func:`_is_system_header`) decides which paths count as project
        rather than system headers, ``max_depth`` bounds the descent, and an
        internal visited set stops mutually including headers from recursing
        forever.

        ``allowlist`` and ``denylist`` govern *filtering*, and they narrow the
        merged result in **both** modes.  A header the allowlist does not name is
        neither descended into nor admitted out of ``filename``'s own translation
        unit; a header the denylist names is excluded by both routes as well.

        * ``recursive_includes=True`` with no ``allowlist`` emits the declarations
          of every non-system included header.
        * ``recursive_includes=True`` with ``allowlist=["other.h"]`` emits
          ``filename``'s own declarations and ``other.h``'s, and nothing else.
        * An allowlist naming nothing that is actually included yields
          ``filename``'s declarations alone.  This is not an error.
        * ``recursive_includes=False`` with no ``allowlist`` yields ``filename``'s
          declarations alone.
        * **Deny wins over allow.**  A file named by both lists is excluded.
        * The main file is never denied.  ``filename`` always contributes its own
          declarations, because returning an empty result for the file the caller
          asked to parse is a silent failure with nothing to explain it.

        :param code: C/C++ source code to parse (raw, not preprocessed).
        :param filename: Source filename for error messages and location tracking.
        :param include_dirs: Additional include directories (converted to ``-I`` flags).
        :param extra_args: Additional compiler arguments (e.g., ``["-std=c++17"]``).
        :param use_default_includes: If True (default), automatically detect and add
            system include directories by querying the system clang compiler.
            Set to False to disable this behavior.
        :param recursive_includes: If True (default), descend into every non-system
            included header and merge its declarations into the result. System
            headers are always skipped. Set to False to parse only the main file,
            which is the only way to keep included declarations out entirely.
        :param max_depth: Maximum recursion depth for include processing (default 10).
            Prevents infinite recursion from circular includes.
        :param project_prefixes: Optional tuple of path prefixes to treat as project
            headers (not system). Use this for umbrella headers of libraries installed
            in system locations (e.g., ``("/opt/homebrew/include/sodium",)``).
        :param allowlist: Files whose declarations are kept, alongside ``filename``'s
            own.  ``None`` keeps every non-system file reached by traversal; a list
            narrows the result to exactly ``filename`` plus the files it names, in
            both ``recursive_includes`` modes.  An absolute entry is used as-is; a
            relative entry (including a bare basename) is resolved against the
            directory of ``filename`` first, then each of ``include_dirs``, then the
            current working directory.  An entry containing ``*``, ``?`` or ``[`` is
            an :mod:`fnmatch` pattern whose directory part is resolved the same way.
            Matching is on whole absolute, symlink-resolved paths -- never on
            substrings, so ``er.h`` does not match ``other.h``.
        :param denylist: Files whose declarations are dropped, using the same
            resolution and glob rules as ``allowlist``.  Deny wins over allow: a
            file named by both lists is excluded.  A denylist with no allowlist
            means "everything except these".  An entry matching nothing is not an
            error.  ``filename`` itself cannot be denied.
        :returns: :class:`~headerkit.ir.Header` containing parsed declarations.
        :raises RuntimeError: If parsing fails with errors.

        Example
        -------
        ::

            # Basic usage
            header = backend.parse(
                code,
                "myheader.hpp",
                include_dirs=["/usr/local/include"],
                extra_args=["-std=c++17", "-DNDEBUG"]
            )

            # Umbrella header (all-includes) pattern: descend into the includes
            # and keep everything they declare.  This is the default.
            header = backend.parse(
                code,
                "LibraryAll.h",
                include_dirs=["./include"],
                recursive_includes=True
            )

            # Keep only one included header's declarations: no descent, and
            # exactly one file admitted from the main translation unit.
            header = backend.parse(
                code,
                "main.h",
                recursive_includes=False,
                allowlist=["other.h"]
            )

            # Main file only -- nothing reached through #include survives
            header = backend.parse(
                code,
                "main.h",
                recursive_includes=False
            )

            # Umbrella header in system location
            header = backend.parse(
                code,
                "sodium.h",
                include_dirs=["/opt/homebrew/include"],
                project_prefixes=("/opt/homebrew/include/sodium",)  # Allowlist sodium/*
            )
        """
        # Ensure libclang is configured before parsing.  This is a no-op
        # after the first successful load (short-circuits on Config.loaded).
        if not _configure_libclang():
            from headerkit.backends import LibclangUnavailableError

            raise LibclangUnavailableError(
                "libclang shared library not found. Install LLVM/clang or run: pip install libclang"
            )

        args: list[str] = []

        is_cplus = _detect_cplus(filename, extra_args)

        # Add user-specified include directories FIRST
        # This is important for C++ where user headers may need to come before system libc++
        if include_dirs:
            for inc_dir in include_dirs:
                args.append(f"-I{inc_dir}")

        # Add system include directories if enabled
        # Always add them when use_default_includes=True, regardless of other -I flags
        if use_default_includes:
            args.extend(get_system_include_dirs(cplus=is_cplus))

        # Add extra arguments
        if extra_args:
            args.extend(extra_args)

        # Parse the code with detailed preprocessing record for macro extraction
        index = self._get_index()
        tu = index.parse(
            filename,
            args=args,
            unsaved_files=[(filename, code)],
            options=_cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
        )

        # Check for fatal errors
        for diag in tu.diagnostics:
            if diag.severity >= _cindex.Diagnostic.Error:
                raise RuntimeError(f"Parse error: {diag.spelling}")

        # Collect included headers
        included_headers: set[str] = set()
        for inclusion in tu.get_includes():
            # inclusion.include is a File with name attribute
            header_path = str(inclusion.include.name)
            # Store full path - caller can extract basename if needed
            included_headers.add(header_path)
            # Ask clang whether it treated this file as a system header. This is the
            # authoritative answer -- clang knows its own resource directory and
            # -isystem search paths, which vary per platform and per toolchain
            # version, so the path heuristic alone leaks libc internals on Linux.
            if _location_is_in_system_header(tu, inclusion.include):
                self._clang_system_headers.add(normalize_path(header_path))

        # Convert to IR
        search_dirs = _filter_search_dirs(filename, include_dirs)
        allowlist_paths = None if allowlist is None else _PathSet(allowlist, search_dirs)
        denylist_paths = None if denylist is None else _PathSet(denylist, search_dirs)
        converter = ClangASTConverter(
            filename,
            project_prefixes=project_prefixes,
            is_cplus=is_cplus,
            allowlist_paths=allowlist_paths,
            denylist_paths=denylist_paths,
            parse_args=tuple(args),
        )
        header = converter.convert(tu)

        # Attach included headers to the IR
        header.included_headers = included_headers

        # Check if we should do recursive include processing
        # ``recursive_includes`` is the caller's answer, so nothing here second-guesses
        # it from the header's shape. Scope is bounded by _is_system_header /
        # project_prefixes, allowlist, max_depth, and self._visited.
        has_project_include = any(not self._is_system_include(h, project_prefixes) for h in header.included_headers)
        if recursive_includes and has_project_include:
            # Reset visited set for each top-level parse
            self._visited = set()
            # Add current file to visited
            if os.path.exists(filename):
                abs_filename = os.path.abspath(filename)
            else:
                # For in-memory code, use filename as-is
                abs_filename = filename
            self._visited.add(abs_filename)

            # Recursively parse included headers
            header = self._parse_recursively(
                header,
                abs_filename,
                include_dirs or [],
                extra_args or [],
                use_default_includes,
                max_depth,
                project_prefixes=project_prefixes,
                allowlist_paths=allowlist_paths,
                denylist_paths=denylist_paths,
            )

        # Included headers are parsed in isolation, so a macro that a *later*
        # header redefines is otherwise emitted with the stale value it had in
        # the header that first defined it. The main translation unit's
        # preprocessing record is the only place the real, ordered macro history
        # is visible, so it decides the final state.
        self._apply_final_macro_state(header, tu, converter)
        self._drop_undefined_macros(header, filename, code, args)

        return header

    def _drop_undefined_macros(self, header: Header, filename: str, code: str, args: list[str]) -> None:
        """Remove macro Constants for names that are no longer defined at end of translation.

        libclang's preprocessing record carries no entry for ``#undef``, so a
        macro a header undefines is still reported as defined and would be
        emitted as a declaration of a symbol the compiler cannot see. Re-parsing
        the same source with an ``#ifdef`` probe appended per candidate asks the
        preprocessor itself which names survive, so the answer cannot disagree
        with the compiler.
        """
        candidates = [d.name for d in header.declarations if isinstance(d, Constant) and d.is_macro]
        if not candidates:
            return

        probe = [code, "\n"]
        probe.extend(
            f"#ifdef {name}\nint {_MACRO_PROBE_PREFIX}{index};\n#endif\n" for index, name in enumerate(candidates)
        )

        try:
            probe_tu = self._get_index().parse(
                filename,
                args=args,
                unsaved_files=[(filename, "".join(probe))],
                options=_cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES,
            )
        except _cindex.TranslationUnitLoadError:
            return

        if any(diag.severity >= _cindex.Diagnostic.Error for diag in probe_tu.diagnostics):
            # The probe did not compile; its silence is not evidence of an #undef.
            return

        defined: set[str] = set()
        for cur in probe_tu.cursor.get_children():
            if cur.kind != CursorKind.VAR_DECL:
                continue
            spelling = cur.spelling
            if spelling.startswith(_MACRO_PROBE_PREFIX):
                index = spelling[len(_MACRO_PROBE_PREFIX) :]
                if index.isdigit():
                    defined.add(candidates[int(index)])

        header.declarations = [
            d for d in header.declarations if not (isinstance(d, Constant) and d.is_macro and d.name not in defined)
        ]

    @staticmethod
    def _final_macro_state(tu: Any, converter: ClangASTConverter) -> dict[str, Constant | None]:
        """Map each macro name to the Constant its LAST ``#define`` yields, or None.

        The preprocessing record lists ``#define`` directives in the order the
        preprocessor met them and omits directives in branches it did not take,
        so iterating it and letting later entries win reproduces the macro table
        as it stands at the end of the translation unit.
        """
        state: dict[str, Constant | None] = {}
        for cur in tu.cursor.get_children():
            if cur.kind != CursorKind.MACRO_DEFINITION:
                continue
            if cur.location.file is None:
                # Compiler builtin, not written in any source file.
                continue
            name = cur.spelling
            if name:
                state[name] = converter._build_macro_constant(cur, name)
        return state

    @classmethod
    def _apply_final_macro_state(cls, header: Header, tu: Any, converter: ClangASTConverter) -> None:
        """Drop or correct macro Constants that the final macro table contradicts."""
        state = cls._final_macro_state(tu, converter)
        if not state:
            return

        kept: list[Declaration] = []
        for decl in header.declarations:
            if isinstance(decl, Constant) and decl.is_macro and decl.name in state:
                final = state[decl.name]
                if final is None:
                    # Last definition has no value - emitting it would not compile.
                    continue
                if not _same_macro_value(decl, final):
                    kept.append(final)
                    continue
            kept.append(decl)
        header.declarations = kept


@hook("parse_unit", backend="libclang", priority=Priority.STANDARD)
def _libclang_parse_hook(
    code: str,
    filename: str = "input.h",
    include_dirs: list[str] | None = None,
    extra_args: list[str] | None = None,
    *,
    use_default_includes: bool = True,
    recursive_includes: bool = True,
    max_depth: int = 10,
    project_prefixes: tuple[str, ...] | None = None,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    _ = (context, kwargs)
    backend = LibclangBackend()
    return backend.parse(
        code,
        filename,
        include_dirs=include_dirs,
        extra_args=extra_args,
        use_default_includes=use_default_includes,
        recursive_includes=recursive_includes,
        max_depth=max_depth,
        project_prefixes=project_prefixes,
        allowlist=allowlist,
        denylist=denylist,
    )


@hook("get_backend", backend="libclang", priority=Priority.STANDARD)
def _libclang_get_backend_hook(context: PipelineContext | None = None) -> ParserBackend:
    _ = context
    return LibclangBackend()


# Always register the backend class.  The class is Python code and always
# importable; the "is the library loadable?" check happens at first use
# (inside parse() via _configure_libclang()), not at import time.  This
# means get_backend("libclang") always returns a LibclangBackend instance,
# and failures surface when the backend is actually used.
register_backend("libclang", LibclangBackend)
