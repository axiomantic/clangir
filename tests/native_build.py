"""Host-portable command builders for the compile-link-execute tests.

Several regression tests prove a generated binding is real by compiling it,
linking a CPython extension module, importing it and calling into it. The
three CI platforms disagree about how that is spelled, and every disagreement
is resolved here so a fourth platform is a single edit:

``-fPIC``
    Required on Linux, where ``ld`` rejects the non-PIC relocations in a
    ``-shared`` object. Already the default on Darwin. Rejected outright by a
    Windows/MSVC-target ``clang++`` (``unsupported option '-fPIC' for target
    'x86_64-pc-windows-msvc'``).

``-undefined dynamic_lookup``
    Darwin only. It tells ``ld`` that the ``Py_*`` symbols will be supplied by
    the interpreter that loads the module.

The Python import library
    A Windows DLL may not carry unresolved symbols, so an extension must link
    against ``pythonXY.lib`` explicitly. ELF and Mach-O both resolve those
    symbols at load time instead and need no such argument.

The module filename
    An extension is only importable under the host's :data:`EXT_SUFFIX`
    (``.pyd``-based on Windows, ``.so``-based elsewhere).
"""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_DARWIN = sys.platform == "darwin"

#: The suffix an importable extension module must carry on this host.
EXTENSION_SUFFIX: str = sysconfig.get_config_var("EXT_SUFFIX") or (".pyd" if IS_WINDOWS else ".so")

#: The CPython headers, needed by every Cython-generated translation unit.
PYTHON_INCLUDE_DIR: str = sysconfig.get_paths()["include"]


def extension_filename(stem: str) -> str:
    """Return the filename ``import <stem>`` will look for on this host."""
    return f"{stem}{EXTENSION_SUFFIX}"


def position_independent_flags() -> list[str]:
    """Return the flags that make object code suitable for a shared object."""
    return [] if IS_WINDOWS else ["-fPIC"]


def _python_import_library() -> str:
    """Return the path to ``pythonXY.lib``, which a Windows DLL must link against.

    :raises RuntimeError: if no import library is found, rather than letting the
        link fail later with an undecipherable pile of unresolved ``Py_*``
        symbols.
    """
    name = f"python{sys.version_info.major}{sys.version_info.minor}.lib"
    roots = [
        sysconfig.get_config_var("installed_base"),
        sysconfig.get_config_var("base"),
        sys.base_prefix,
        sys.prefix,
    ]
    candidates = [Path(root) / "libs" / name for root in roots if root]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    searched = "\n".join(f"  {candidate}" for candidate in candidates)
    raise RuntimeError(f"cannot find the Python import library {name}; searched:\n{searched}")


def _extension_link_flags() -> list[str]:
    """Return the linker flags that turn a translation unit into a loadable module.

    The two ``-static-*`` flags are Windows-only and are about *import*, not
    about linking: the runner's ``c++`` is MinGW, so a ``.pyd`` it links names
    ``libstdc++-6.dll``, ``libgcc_s_seh-1.dll`` and ``libwinpthread-1.dll`` as
    load-time dependencies. Those live in the MinGW ``bin`` directory, which is
    not on the loader's search path for the interpreter that imports the
    module, so the link and the build both succeed and ``import use`` fails
    with "DLL load failed while importing use: The specified module could not
    be found" -- a message that names neither the missing DLL nor MinGW.
    Linking the two runtimes into the module removes the dependency instead of
    relying on the runner's PATH. Only the C++ tests reached this, because a C
    extension names no ``libstdc++``.
    """
    if IS_WINDOWS:
        return ["-shared", "-static-libstdc++", "-static-libgcc"]
    if IS_DARWIN:
        return ["-bundle", "-undefined", "dynamic_lookup"]
    return ["-shared", "-fPIC"]


def _extension_link_libraries() -> list[str]:
    """Return the libraries to place *after* the inputs on the link line.

    Position matters: the Windows runner carries both a MinGW ``cc`` and an
    MSVC-target ``clang++``, and MinGW drives GNU ``ld``, which resolves a
    library only against the objects that precede it. An import library placed
    before the objects is silently discarded and every ``__imp_Py*`` symbol
    comes back undefined.
    """
    return [_python_import_library()] if IS_WINDOWS else []


def compile_object_command(
    compiler: str,
    source: str,
    output: str,
    *,
    includes: Sequence[str | Path] = (),
    extra: Sequence[str] = (),
) -> list[str]:
    """Build the argv that compiles ``source`` to the object file ``output``."""
    return [
        compiler,
        *extra,
        *position_independent_flags(),
        "-c",
        source,
        *(f"-I{include}" for include in includes),
        "-o",
        output,
    ]


def link_extension_command(
    compiler: str,
    inputs: Sequence[str],
    stem: str,
    *,
    includes: Sequence[str | Path] = (),
    extra: Sequence[str] = (),
) -> list[str]:
    """Build the argv that links ``inputs`` into an importable ``stem`` module.

    ``inputs`` may be object files or source files; the driver handles either,
    which is why the one-shot and two-stage call sites share this builder.
    """
    return [
        compiler,
        *extra,
        *_extension_link_flags(),
        *(f"-I{include}" for include in includes),
        *inputs,
        *_extension_link_libraries(),
        "-o",
        extension_filename(stem),
    ]
