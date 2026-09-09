"""Nim scikit-build wheel packaging template and build helpers."""

from __future__ import annotations

import re
import textwrap

from headerkit.ir import (
    Array,
    CType,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    Reference,
    SourceUnit,
    TypeExpr,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.base import DEDENT_BLOCK, render_block_template
from headerkit.writers.ctypes import CTYPES_TYPE_MAP
from headerkit.writers.nim import C_TO_NIM_PRIMITIVES

_PKG_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_pkg_name(pkg: str) -> None:
    if not _PKG_NAME_RE.match(pkg):
        raise ValueError(f"Invalid package name {pkg!r}: must be a valid identifier matching ^[a-zA-Z_][a-zA-Z0-9_]*$")


def generate_nim_cmake(pkg: str, *, nim_flags: list[str] | None = None) -> str:
    """Generate CMakeLists.txt configured for scikit-build-core compiling a Nim library."""
    _validate_pkg_name(pkg)
    flags = ["--app:lib", "--mm:orc", "--threads:on", "-d:release"] if nim_flags is None else nim_flags
    flags_str = " ".join(flags)

    return textwrap.dedent(f"""\
        cmake_minimum_required(VERSION 3.18)
        project({pkg}_pkg LANGUAGES C)

        find_program(NIM_EXECUTABLE nim REQUIRED)

        set(NIM_SRC "${{CMAKE_CURRENT_SOURCE_DIR}}/src/{pkg}.nim")
        set(NIM_LIB "${{CMAKE_CURRENT_BINARY_DIR}}/${{CMAKE_SHARED_LIBRARY_PREFIX}}{pkg}${{CMAKE_SHARED_LIBRARY_SUFFIX}}")

        add_custom_command(
            OUTPUT "${{NIM_LIB}}"
            COMMAND "${{NIM_EXECUTABLE}}" c {flags_str} --out:"${{NIM_LIB}}" "${{NIM_SRC}}"
            DEPENDS "${{NIM_SRC}}"
            COMMENT "Compiling Nim module with ORC memory management"
        )

        add_custom_target(nim_{pkg} ALL DEPENDS "${{NIM_LIB}}")

        install(
            FILES "${{NIM_LIB}}"
            DESTINATION "{pkg}"
        )
    """)


def generate_nim_pyproject(
    pkg: str,
    *,
    version: str = "0.1.0",
    description: str = "Nim extension packaged with HeaderKit",
) -> str:
    """Generate pyproject.toml configured for scikit-build-core wheel distribution."""
    _validate_pkg_name(pkg)
    desc = description or f"Nim extension {pkg} packaged with HeaderKit"
    return textwrap.dedent(f"""\
        [build-system]
        requires = ["scikit-build-core>=0.9.0"]
        build-backend = "scikit_build_core.build"

        [project]
        name = "{pkg}"
        version = "{version}"
        description = "{desc}"
        readme = "README.md"
        requires-python = ">=3.10"

        [tool.scikit-build]
        cmake.version = ">=3.18"
        wheel.packages = ["{pkg}"]
    """)


def _escape_nim_ident(name: str) -> str:
    from headerkit.writers.nim import _escape_ident

    return _escape_ident(name)


def _c_type_to_nim(c_type_name: str) -> str:
    cleaned = c_type_name.removeprefix("struct ").removeprefix("union ").removeprefix("enum ").strip()
    if cleaned in C_TO_NIM_PRIMITIVES:
        return C_TO_NIM_PRIMITIVES[cleaned]
    if cleaned.endswith("*"):
        base = cleaned[:-1].strip()
        if base in ("char", "const char"):
            return "cstring"
        if base in ("void", "const void"):
            return "pointer"
        return f"ptr {_c_type_to_nim(base)}"
    if cleaned.isidentifier():
        return _escape_nim_ident(cleaned)
    return "cint"


def _ir_type_to_nim(t: TypeExpr) -> str:
    """Convert an IR TypeExpr to its corresponding Nim type declaration."""
    if isinstance(t, CType):
        name = t.name.removeprefix("struct ").removeprefix("union ").removeprefix("enum ").strip()
        if name in C_TO_NIM_PRIMITIVES:
            return C_TO_NIM_PRIMITIVES[name]
        return _c_type_to_nim(name)
    elif isinstance(t, Pointer):
        if isinstance(t.pointee, CType) and t.pointee.name in ("void", "const void"):
            return "pointer"
        if isinstance(t.pointee, CType) and t.pointee.name == "char":
            return "cstring"
        return f"ptr {_ir_type_to_nim(t.pointee)}"
    elif isinstance(t, Reference):
        return f"var {_ir_type_to_nim(t.target)}"
    elif isinstance(t, Array):
        return f"array[{t.size or 0}, {_ir_type_to_nim(t.element_type)}]"
    elif isinstance(t, FunctionPointer):
        return "pointer"
    return "cint"


def _c_type_to_ctypes(c_type_name: str) -> str:
    cleaned = c_type_name.removeprefix("struct ").removeprefix("union ").removeprefix("enum ").strip()
    if cleaned.startswith("const "):
        cleaned = cleaned[6:].strip()
    if cleaned in CTYPES_TYPE_MAP:
        return CTYPES_TYPE_MAP[cleaned]
    if cleaned.endswith("*"):
        base = cleaned[:-1].strip()
        if base == "char":
            return "ctypes.c_char_p"
        if base in ("void", "const void"):
            return "ctypes.c_void_p"
        base_ctypes = _c_type_to_ctypes(base)
        if base_ctypes in ("ctypes.c_void_p", "ctypes.c_char_p"):
            return "ctypes.c_void_p"
        return f"ctypes.POINTER({base_ctypes})"
    if cleaned.isidentifier():
        if cleaned in ("size_t", "uintptr_t"):
            return "ctypes.c_size_t"
        if cleaned in ("ssize_t", "intptr_t"):
            return "ctypes.c_ssize_t"
        return "ctypes.c_void_p"
    return "ctypes.c_int"


def _ir_type_to_ctypes(t: TypeExpr) -> str:
    """Convert an IR TypeExpr to its corresponding ctypes type string."""
    if isinstance(t, CType):
        name = t.name.removeprefix("struct ").removeprefix("union ").removeprefix("enum ").strip()
        if name.startswith("const "):
            name = name[6:].strip()
        if name in CTYPES_TYPE_MAP:
            return CTYPES_TYPE_MAP[name]
        return _c_type_to_ctypes(name)
    elif isinstance(t, Pointer):
        if isinstance(t.pointee, CType) and t.pointee.name == "char":
            return "ctypes.c_char_p"
        if isinstance(t.pointee, CType) and t.pointee.name in ("void", "const void"):
            return "ctypes.c_void_p"
        inner = _ir_type_to_ctypes(t.pointee)
        if inner == "ctypes.c_void_p":
            return "ctypes.c_void_p"
        return f"ctypes.POINTER({inner})"
    elif isinstance(t, Reference):
        inner = _ir_type_to_ctypes(t.target)
        if inner == "ctypes.c_void_p":
            return "ctypes.c_void_p"
        return f"ctypes.POINTER({inner})"
    elif isinstance(t, Array):
        return f"({_ir_type_to_ctypes(t.element_type)} * {t.size or 0})"
    elif isinstance(t, FunctionPointer):
        return "ctypes.c_void_p"
    return "ctypes.c_int"


def _nim_return_stub(nim_type: str) -> str:
    if nim_type == "void":
        return "discard"
    if nim_type in (
        "cint",
        "int",
        "cuint",
        "uint",
        "int8",
        "uint8",
        "int16",
        "uint16",
        "int32",
        "uint32",
        "int64",
        "uint64",
        "cshort",
        "cushort",
        "clong",
        "culong",
        "clonglong",
        "culonglong",
        "csize_t",
    ):
        return "0"
    if nim_type in ("cfloat", "cdouble", "clongdouble"):
        return "0.0"
    if nim_type == "bool":
        return "false"
    if nim_type in ("cstring", "pointer") or nim_type.startswith("ptr "):
        return "nil"
    if nim_type.startswith("array["):
        return f"var r: {nim_type}; r"
    return f"default({nim_type})"


def generate_nim_source(pkg: str, unit: SourceUnit | Header) -> str:
    """Generate src/{pkg}.nim source file implementing C ABI entrypoints."""
    _validate_pkg_name(pkg)
    lines: list[str] = [
        f"# Implementation module for {pkg}",
        "# Compiled with --app:lib --mm:orc",
        "",
        "proc NimMain*() {.cdecl, importc.}",
        "",
    ]

    funcs = [decl for decl in getattr(unit, "declarations", []) if isinstance(decl, Function) and decl.name]

    if not funcs:
        lines.append("# No functions declared in source unit")
        lines.append("proc dummy_ping*(): cint {.exportc, dynlib, cdecl.} =")
        lines.append("  0")
        lines.append("")
        return "\n".join(lines)

    for fn in funcs:
        params_str_list: list[str] = []
        for idx, p in enumerate(fn.parameters):
            p_name = _escape_nim_ident(p.name or f"arg{idx}")
            p_nim = _ir_type_to_nim(p.type)
            params_str_list.append(f"{p_name}: {p_nim}")
        params_sig = ", ".join(params_str_list)

        ret_nim = _ir_type_to_nim(fn.return_type)
        stub = _nim_return_stub(ret_nim)
        fn_nim_name = _escape_nim_ident(fn.name)

        lines.append(f'proc {fn_nim_name}*({params_sig}): {ret_nim} {{.exportc: "{fn.name}", dynlib, cdecl.}} =')
        lines.append(f"  {stub}")
        lines.append("")

    return "\n".join(lines)


def generate_nim_python_wrapper(pkg: str, unit: SourceUnit | Header) -> str:
    """Generate {pkg}/__init__.py Python wrapper binding the compiled Nim library."""
    _validate_pkg_name(pkg)
    funcs = [decl for decl in getattr(unit, "declarations", []) if isinstance(decl, Function) and decl.name]

    lines: list[str] = [
        f'"""Python wrapper for {pkg} compiled Nim extension."""',
        "",
        "from __future__ import annotations",
        "",
        "import ctypes",
        "import sys",
        "from pathlib import Path",
        "",
        "_pkg_dir = Path(__file__).resolve().parent",
        "",
        'if sys.platform == "win32":',
        "    _candidates = [",
        f'        _pkg_dir / "{pkg}.dll",',
        f'        _pkg_dir / "lib{pkg}.dll",',
        "    ]",
        'elif sys.platform == "darwin":',
        "    _candidates = [",
        f'        _pkg_dir / "lib{pkg}.dylib",',
        f'        _pkg_dir / "{pkg}.dylib",',
        "    ]",
        "else:",
        "    _candidates = [",
        f'        _pkg_dir / "lib{pkg}.so",',
        f'        _pkg_dir / "{pkg}.so",',
        "    ]",
        "",
        "_lib: ctypes.CDLL | None = None",
        "for _cand in _candidates:",
        "    if _cand.exists():",
        "        _lib = ctypes.CDLL(str(_cand))",
        "        break",
        "",
        "_nim_initialized = False",
        "",
        "def init_nim() -> None:",
        '    """Initialize the Nim runtime idempotently."""',
        "    global _nim_initialized",
        "    if not _nim_initialized:",
        '        if _lib is not None and hasattr(_lib, "NimMain"):',
        "            _lib.NimMain.argtypes = []",
        "            _lib.NimMain.restype = None",
        "            _lib.NimMain()",
        "        _nim_initialized = True",
        "",
    ]

    lines.append("# Setup ctypes function signatures")
    lines.append("if _lib is not None:")
    lines.append("    init_nim()")
    for fn in funcs:
        ctypes_ret = _ir_type_to_ctypes(fn.return_type)

        arg_types: list[str] = []
        for p in fn.parameters:
            arg_types.append(_ir_type_to_ctypes(p.type))
        argtypes_str = f"[{', '.join(arg_types)}]"

        lines.append(f'    if hasattr(_lib, "{fn.name}"):')
        lines.append(f"        _lib.{fn.name}.argtypes = {argtypes_str}")
        lines.append(f"        _lib.{fn.name}.restype = {ctypes_ret}")

    lines.append("")
    lines.append("# Exported Python wrapper functions")
    for fn in funcs:
        p_names = [p.name or f"arg{idx}" for idx, p in enumerate(fn.parameters)]
        params_sig = ", ".join(p_names)
        args_call = ", ".join(p_names)

        lines.append(f"def {fn.name}({params_sig}):")
        lines.append("    init_nim()")
        lines.append("    if _lib is None:")
        lines.append(f"""        raise RuntimeError("Native dynamic library '{pkg}' could not be loaded")""")
        lines.append(f"    return _lib.{fn.name}({args_call})")
        lines.append("")

    return "\n".join(lines)


def generate_nim_wheel_layout(
    unit: SourceUnit | Header,
    options: ScaffoldOptions,
) -> ProjectLayout:
    """Generate a complete scikit-build-core wheel packaging layout for a Nim library."""
    pkg = options.package_name
    _validate_pkg_name(pkg)
    test_type = options.get_option("test_type", "both")
    funcs = [decl for decl in getattr(unit, "declarations", []) if isinstance(decl, Function) and decl.name]

    files: list[OutputFile] = []

    # 1. pyproject.toml
    pyproject = generate_nim_pyproject(pkg)
    files.append(OutputFile(path="pyproject.toml", content=pyproject))

    # 2. CMakeLists.txt
    cmake = generate_nim_cmake(pkg)
    files.append(OutputFile(path="CMakeLists.txt", content=cmake))

    # 3. Nim source
    nim_source = generate_nim_source(pkg, unit)
    files.append(OutputFile(path=f"src/{pkg}.nim", content=nim_source))

    # 4. Python package wrapper
    py_wrapper = generate_nim_python_wrapper(pkg, unit)
    files.append(OutputFile(path=f"{pkg}/__init__.py", content=py_wrapper))

    # 5. README.md
    readme = textwrap.dedent(f"""\
        # {pkg}

        Nim extension module packaged as a distributable Python binary wheel using `scikit-build-core`.

        ## Building the Wheel

        ```bash
        pip install build
        python -m build --wheel
        ```

        ## Installation

        ```bash
        pip install .
        ```
    """)
    files.append(OutputFile(path="README.md", content=readme))

    # 6. Tests
    if test_type in ("tripwire", "both"):
        tw_checks: list[str] = []
        for fn in funcs:
            tw_checks.append(
                textwrap.dedent(f"""\
                @pytest.mark.tripwire
                def test_entrypoint_{fn.name}() -> None:
                    assert hasattr({pkg}._lib, "{fn.name}"), "Entry point '{fn.name}' missing from compiled library"
                    assert callable(getattr({pkg}, "{fn.name}"))
            """)
            )

        checks_str = "\n".join(tw_checks) if tw_checks else ""

        tripwire = render_block_template(
            f'''\
            """Tripwire test verifying native dynamic library resolution and ABI entrypoints."""

            from __future__ import annotations

            import pytest
            import {pkg}


            @pytest.mark.tripwire
            def test_native_library_loaded() -> None:
                """Tripwire: verify that the compiled Nim dynamic library is present and loaded."""
                assert {pkg}._lib is not None, "Native dynamic library '{pkg}' not found in package directory"


            {DEDENT_BLOCK}
        ''',
            checks_str,
        )
        files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

    if test_type in ("unit", "both"):
        unit_checks: list[str] = []
        for fn in funcs:
            param_names = [p.name or f"arg{idx}" for idx, p in enumerate(fn.parameters)]
            unit_checks.append(
                textwrap.dedent(f"""\
                def test_{fn.name}_wrapper_signature() -> None:
                    assert hasattr({pkg}, "{fn.name}")
                    fn_obj = getattr({pkg}, "{fn.name}")
                    assert callable(fn_obj)
                    sig = inspect.signature(fn_obj)
                    assert len(sig.parameters) == {len(fn.parameters)}
                    assert list(sig.parameters.keys()) == {repr(param_names)}
            """)
            )

        u_checks_str = "\n".join(unit_checks) if unit_checks else ""

        unit_test = render_block_template(
            f'''\
            """Unit tests for {pkg} Python wrapper."""

            from __future__ import annotations

            import inspect
            import {pkg}


            def test_module_structure() -> None:
                assert inspect.ismodule({pkg})
                assert hasattr({pkg}, "init_nim")
                assert callable({pkg}.init_nim)


            {DEDENT_BLOCK}
        ''',
            u_checks_str,
        )
        files.append(OutputFile(path=f"tests/test_{pkg}.py", content=unit_test))

    return ProjectLayout(files=files)
