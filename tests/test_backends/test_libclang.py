"""Tests for the libclang backend."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from unittest.mock import patch

import bigfoot
import pytest
from dirty_equals import AnyThing

import headerkit.backends.libclang as mod
from headerkit.backends import reload_backends
from headerkit.backends.libclang import (
    LibclangBackend,
    _configure_libclang,
    _deduplicate_declarations,
    _get_libclang_search_paths,
    _is_system_header,
    _is_umbrella_header,
    _mangle_specialization_name,
    get_system_include_dirs,
    is_system_libclang_available,
    normalize_path,
)
from headerkit.ir import (
    Constant,
    CType,
    Enum,
    Field,
    Function,
    Header,
    Pointer,
    Struct,
    Typedef,
    Variable,
)

# Mark all tests that require libclang
libclang = pytest.mark.libclang


@pytest.fixture(autouse=True)
def _skip_if_no_libclang(request: pytest.FixtureRequest) -> None:
    """Skip tests in @libclang-marked classes when system libclang is not available.

    This fixture is autouse but only activates for tests within classes
    that carry the ``@libclang`` marker.
    """
    marker = request.node.get_closest_marker("libclang")
    if marker is not None and not is_system_libclang_available():
        pytest.skip("System libclang not available")


class TestImportability:
    """Tests that the module can be imported and basic functions work."""

    def test_module_imports(self):
        """The libclang backend module exposes the expected public API."""
        assert hasattr(mod, "LibclangBackend")
        assert callable(mod.LibclangBackend)
        assert hasattr(mod, "is_system_libclang_available")
        assert callable(mod.is_system_libclang_available)
        assert hasattr(mod, "get_system_include_dirs")
        assert callable(mod.get_system_include_dirs)
        assert hasattr(mod, "normalize_path")
        assert callable(mod.normalize_path)

    def test_is_system_libclang_available_returns_bool(self):
        """is_system_libclang_available() returns a boolean."""
        result = is_system_libclang_available()
        assert isinstance(result, bool)

    def test_libclang_backend_class_exists(self):
        """LibclangBackend class has expected attributes and methods."""
        assert LibclangBackend.__name__ == "LibclangBackend"
        assert hasattr(LibclangBackend, "parse")
        assert callable(LibclangBackend.parse)
        assert hasattr(LibclangBackend, "name")
        assert hasattr(LibclangBackend, "supports_macros")
        assert hasattr(LibclangBackend, "supports_cpp")


class TestHelperFunctions:
    """Tests for helper functions that don't require libclang."""

    @pytest.mark.allow("subprocess")
    def test_get_libclang_search_paths_returns_list(self):
        """_get_libclang_search_paths returns a non-empty list of strings on supported platforms."""
        import sys

        paths = _get_libclang_search_paths()
        assert isinstance(paths, list)
        # _get_libclang_search_paths() returns hardcoded candidate paths derived from
        # platform-specific install conventions (Homebrew, APT, LLVM releases, etc.).
        # Exact paths vary by OS and installed LLVM versions, so we cannot enumerate
        # the complete expected set. We assert count >=1 and verify each entry's structure.
        assert len(paths) >= 1, "Expected at least one libclang search path"
        for path in paths:
            assert isinstance(path, str)
            assert len(path) > 0, "Search path should not be empty"
            # Each path should contain a libclang library filename
            if sys.platform == "win32":
                assert "libclang" in path.lower(), f"Expected libclang in path: {path}"
            else:
                assert "libclang" in path, f"Expected libclang in path: {path}"

    def test_is_system_header_usr_include(self):
        """System header detection for /usr/include."""
        assert _is_system_header("/usr/include/stdio.h") is True

    def test_is_system_header_usr_local(self):
        """System header detection for /usr/local/include."""
        assert _is_system_header("/usr/local/include/mylib.h") is True

    def test_is_system_header_homebrew(self):
        """System header detection for Homebrew paths."""
        assert _is_system_header("/opt/homebrew/include/nng/nng.h") is True

    def test_is_system_header_sdk(self):
        """System header detection for SDK paths."""
        assert _is_system_header("/path/to/MacOSX.sdk/usr/include/stdio.h") is True

    def test_is_system_header_project_file(self):
        """Non-system header detection for project files."""
        assert _is_system_header("/home/user/project/include/mylib.h") is False

    def test_is_system_header_project_prefix_overrides(self):
        """project_prefixes can whitelist paths that would otherwise be system."""
        path = "/opt/homebrew/include/sodium/crypto_auth.h"
        assert _is_system_header(path) is True
        assert _is_system_header(path, project_prefixes=("/opt/homebrew/include/sodium",)) is False

    def test_is_system_header_case_insensitive_with_backslashes(self):
        """System header detection works with Windows-style backslash paths."""
        assert _is_system_header(r"C:\some\path\clang\include\stddef.h") is True

    def test_is_umbrella_header_true(self):
        """Umbrella header detection when many includes, few declarations.

        Threshold mechanics (default threshold=3):
        - Umbrella if: non-system project includes >= threshold AND declarations < threshold
        - _is_system_header filters out system paths (e.g. /usr/include/*)
        - All 4 paths here are non-system (/home/user/lib/*), so project_includes=4 >= 3
        - declarations=0 < 3, so this is an umbrella header
        """
        header = Header(
            path="umbrella.h",
            declarations=[],
            included_headers={
                "/home/user/lib/a.h",
                "/home/user/lib/b.h",
                "/home/user/lib/c.h",
                "/home/user/lib/d.h",
            },
        )
        assert _is_umbrella_header(header) is True

    def test_is_umbrella_header_false_many_decls(self):
        """Non-umbrella header with declarations.

        Threshold mechanics (default threshold=3):
        - project includes=1 (only /home/user/lib/a.h), which is < 3
        - declarations=4, which is >= 3
        - Fails both criteria, so NOT an umbrella header
        """
        header = Header(
            path="normal.h",
            declarations=[
                Struct("A", [Field("x", CType("int"))]),
                Struct("B", [Field("y", CType("int"))]),
                Struct("C", [Field("z", CType("int"))]),
                Function("foo", CType("void"), []),
            ],
            included_headers={"/home/user/lib/a.h"},
        )
        assert _is_umbrella_header(header) is False

    def test_is_umbrella_header_system_headers_excluded(self):
        """_is_system_header influences umbrella detection by filtering system includes.

        System headers (e.g. /usr/include/*) are not counted as project includes,
        so a header that includes many system headers but few project headers
        is NOT considered an umbrella header.
        """
        header = Header(
            path="not_umbrella.h",
            declarations=[],
            included_headers={
                "/usr/include/stdio.h",
                "/usr/include/stdlib.h",
                "/usr/include/string.h",
                "/usr/include/math.h",
            },
        )
        # All includes are system headers, so project_includes=0, not >= threshold
        assert _is_umbrella_header(header) is False

    def test_deduplicate_declarations_removes_duplicates(self):
        """_deduplicate_declarations removes duplicate declarations."""
        decls = [
            Struct("Point", [Field("x", CType("int"))]),
            Struct("Point", [Field("x", CType("int"))]),  # duplicate
            Function("foo", CType("void"), []),
        ]
        result = _deduplicate_declarations(decls)
        assert len(result) == 2
        names = [getattr(d, "name", None) for d in result]
        assert "Point" in names
        assert "foo" in names
        assert isinstance(result[0], Struct)
        assert isinstance(result[1], Function)

    def test_deduplicate_declarations_typedef_struct_pattern(self):
        """_deduplicate_declarations handles typedef struct pattern."""
        decls = [
            Struct("Foo", [Field("x", CType("int"))]),
            Typedef("Foo", CType("Foo")),  # should be removed, struct gets is_typedef=True
        ]
        result = _deduplicate_declarations(decls)
        assert len(result) == 1
        assert isinstance(result[0], Struct)
        assert result[0].is_typedef is True

    def test_mangle_specialization_name(self):
        """_mangle_specialization_name converts template names."""
        assert _mangle_specialization_name("Container<int>") == "Container_int"
        assert _mangle_specialization_name("Map<int, double>") == "Map_int_double"
        assert _mangle_specialization_name("Foo<int*>") == "Foo_int_ptr"
        assert _mangle_specialization_name("std::vector<int>") == "std_vector_int"


class TestNormalizePath:
    """Tests for normalize_path() cross-platform path normalization."""

    def test_backslash_to_forward_slash(self):
        assert normalize_path(r"C:\Program Files\LLVM") == "c:/program files/llvm"

    def test_lowercase(self):
        assert normalize_path("/USR/INCLUDE") == "/usr/include"

    def test_already_normalized(self):
        assert normalize_path("/usr/include/stdio.h") == "/usr/include/stdio.h"

    def test_mixed_separators(self):
        assert normalize_path(r"C:\Program Files/LLVM\bin") == "c:/program files/llvm/bin"

    def test_empty_string(self):
        assert normalize_path("") == ""


class TestIsSystemHeaderWindows:
    """Tests for Windows-specific system header classification."""

    def test_windows_llvm_install(self):
        assert _is_system_header(r"C:\Program Files\LLVM\lib\clang\18\include\stddef.h") is True

    def test_windows_llvm_x86_install(self):
        assert _is_system_header(r"C:\Program Files (x86)\LLVM\lib\clang\18\include\stddef.h") is True

    def test_windows_sdk_ucrt(self):
        """Matched via 'windows kits/' fragment."""
        assert _is_system_header(r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\ucrt\stdio.h") is True

    def test_windows_sdk_um(self):
        """Matched via 'windows kits/' fragment."""
        assert _is_system_header(r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\um\windows.h") is True

    def test_windows_sdk_shared(self):
        """Matched via 'windows kits/' fragment."""
        assert _is_system_header(r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\shared\windef.h") is True

    def test_msvc_toolchain(self):
        assert (
            _is_system_header(
                r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.38.33130\include\vcruntime.h"
            )
            is True
        )

    def test_visual_studio_headers(self):
        assert (
            _is_system_header(r"C:\Program Files\Microsoft Visual Studio\2022\Community\include\some_header.h") is True
        )

    def test_msys2_mingw64_headers(self):
        assert _is_system_header(r"C:\msys64\mingw64\include\stdio.h") is True

    def test_msys2_ucrt64_headers(self):
        assert _is_system_header(r"C:\msys64\ucrt64\include\stdio.h") is True

    def test_msys2_clang64_headers(self):
        assert _is_system_header(r"C:\msys64\clang64\include\stdio.h") is True

    def test_project_file_on_windows(self):
        assert _is_system_header(r"C:\Users\dev\project\include\mylib.h") is False

    def test_project_prefix_overrides_windows_system(self):
        path = r"C:\Program Files\LLVM\include\myproject\myheader.h"
        assert _is_system_header(path) is True
        assert (
            _is_system_header(
                path,
                project_prefixes=(r"C:\Program Files\LLVM\include\myproject",),
            )
            is False
        )


class TestLibclangSearchPathsWindows:
    """Tests for Windows-specific libclang search paths."""

    def test_includes_programfiles_path(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [normalize_path(p) for p in paths]
            assert any("program files/llvm/bin/libclang.dll" in p for p in normalized)

    def test_includes_programfiles_x86_path(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [normalize_path(p) for p in paths]
            assert any("program files (x86)/llvm/bin/libclang.dll" in p for p in normalized)

    def test_includes_scoop_path(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                    "USERPROFILE": r"C:\Users\testuser",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [normalize_path(p) for p in paths]
            assert any("scoop/apps/llvm" in p for p in normalized)

    def test_includes_msys2_paths(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [normalize_path(p) for p in paths]
            assert any("msys64/mingw64/bin/libclang.dll" in p for p in normalized)
            assert any("msys64/ucrt64/bin/libclang.dll" in p for p in normalized)
            assert any("msys64/clang64/bin/libclang.dll" in p for p in normalized)

    def test_uses_env_var_not_hardcoded_paths(self):
        """PROGRAMFILES env var is respected over hardcoded C:\\Program Files."""
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"D:\Custom\Programs",
                    "PROGRAMFILES(X86)": r"D:\Custom\Programs (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [normalize_path(p) for p in paths]
            assert any("d:/custom/programs/llvm/bin/libclang.dll" in p for p in normalized)


@pytest.mark.allow("subprocess")
@libclang
class TestLibclangBackendProperties:
    """Tests for LibclangBackend properties (require libclang)."""

    def test_backend_name(self):
        backend = LibclangBackend()
        assert backend.name == "libclang"

    def test_backend_supports_macros(self):
        backend = LibclangBackend()
        assert backend.supports_macros is True

    def test_backend_supports_cpp(self):
        backend = LibclangBackend()
        assert backend.supports_cpp is True


@pytest.mark.allow("subprocess")
@libclang
class TestLibclangParsing:
    """Tests for parsing C code with the libclang backend."""

    @pytest.fixture
    def backend(self):
        return LibclangBackend()

    def test_parse_simple_variable(self, backend):
        """Parse a simple variable declaration."""
        header = backend.parse("int x;", "test.h")
        assert isinstance(header, Header)
        assert len(header.declarations) == 1
        var_decls = [d for d in header.declarations if isinstance(d, Variable)]
        assert len(var_decls) == 1
        assert var_decls[0].name == "x"
        assert isinstance(var_decls[0].type, CType)
        assert var_decls[0].type.name == "int"

    def test_parse_function(self, backend):
        """Parse a function declaration."""
        header = backend.parse("int add(int a, int b);", "test.h")
        func_decls = [d for d in header.declarations if isinstance(d, Function)]
        assert len(func_decls) == 1
        func = func_decls[0]
        assert func.name == "add"
        assert isinstance(func.return_type, CType)
        assert func.return_type.name == "int"
        assert len(func.parameters) == 2
        assert func.parameters[0].name == "a"
        assert func.parameters[1].name == "b"

    def test_parse_struct(self, backend):
        """Parse a struct declaration."""
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(struct_decls) == 1
        s = struct_decls[0]
        assert s.name == "Point"
        assert len(s.fields) == 2
        assert s.fields[0].name == "x"
        assert s.fields[1].name == "y"

    def test_parse_enum(self, backend):
        """Parse an enum declaration."""
        code = """
        enum Color {
            RED = 0,
            GREEN = 1,
            BLUE = 2
        };
        """
        header = backend.parse(code, "test.h")
        enum_decls = [d for d in header.declarations if isinstance(d, Enum)]
        assert len(enum_decls) == 1
        e = enum_decls[0]
        assert e.name == "Color"
        assert len(e.values) == 3
        assert e.values[0].name == "RED"
        assert e.values[0].value == 0
        assert e.values[1].name == "GREEN"
        assert e.values[1].value == 1
        assert e.values[2].name == "BLUE"
        assert e.values[2].value == 2

    def test_parse_typedef(self, backend):
        """Parse a typedef declaration."""
        code = """
        typedef unsigned long size_type;
        """
        header = backend.parse(code, "test.h")
        typedef_decls = [d for d in header.declarations if isinstance(d, Typedef)]
        assert len(typedef_decls) == 1
        td = typedef_decls[0]
        assert td.name == "size_type"

    def test_parse_pointer_type(self, backend):
        """Parse a function with pointer parameter and return type."""
        code = "char* strdup(const char* s);"
        header = backend.parse(code, "test.h")
        func_decls = [d for d in header.declarations if isinstance(d, Function)]
        assert len(func_decls) == 1
        func = func_decls[0]
        assert func.name == "strdup"
        assert isinstance(func.return_type, Pointer)
        assert len(func.parameters) == 1
        assert isinstance(func.parameters[0].type, Pointer)

    def test_parse_union(self, backend):
        """Parse a union declaration."""
        code = """
        union Data {
            int i;
            float f;
            char c;
        };
        """
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        union_decls = [s for s in struct_decls if s.is_union]
        assert len(union_decls) == 1
        u = union_decls[0]
        assert u.name == "Data"
        assert u.is_union is True
        assert len(u.fields) == 3

    def test_parse_variadic_function(self, backend):
        """Parse a variadic function declaration."""
        code = "int printf(const char* fmt, ...);"
        header = backend.parse(code, "test.h")
        func_decls = [d for d in header.declarations if isinstance(d, Function)]
        assert len(func_decls) == 1
        func = func_decls[0]
        assert func.name == "printf"
        assert func.is_variadic is True

    def test_parse_forward_declaration(self, backend):
        """Parse a forward struct declaration."""
        code = "struct Opaque;"
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(struct_decls) == 1
        s = struct_decls[0]
        assert s.name == "Opaque"
        assert len(s.fields) == 0  # forward decl has no fields

    def test_forward_decl_vs_empty_struct(self, backend):
        """Forward declaration and empty struct body both produce zero fields."""
        fwd_header = backend.parse("struct Opaque;", "fwd.h")
        fwd_structs = [d for d in fwd_header.declarations if isinstance(d, Struct)]
        assert len(fwd_structs) == 1
        assert fwd_structs[0].name == "Opaque"
        assert len(fwd_structs[0].fields) == 0

        empty_header = backend.parse("struct Empty {};", "empty.h")
        empty_structs = [d for d in empty_header.declarations if isinstance(d, Struct)]
        assert len(empty_structs) == 1
        assert empty_structs[0].name == "Empty"
        assert len(empty_structs[0].fields) == 0

    def test_parse_typedef_struct(self, backend):
        """Parse typedef struct pattern."""
        code = """
        typedef struct Point {
            int x;
            int y;
        } Point;
        """
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(struct_decls) == 1
        s = struct_decls[0]
        assert s.name == "Point"
        assert s.is_typedef is True
        assert len(s.fields) == 2

    def test_forward_decl_replaced_by_definition(self, backend):
        """Forward declaration should be replaced by full definition."""
        code = "struct Opaque;\nstruct Opaque { int x; };\n"
        header = backend.parse(code, "test.h")
        structs = [d for d in header.declarations if isinstance(d, Struct) and d.name == "Opaque"]
        assert len(structs) == 1, "Forward decl should be replaced by definition"
        assert len(structs[0].fields) == 1
        assert structs[0].fields[0].name == "x"

    def test_parse_error_raises_runtime_error(self, backend):
        """Parse error raises RuntimeError."""
        code = "this is not valid C code @#$%;"
        with pytest.raises(RuntimeError, match="Parse error"):
            backend.parse(code, "test.h")

    def test_header_path(self, backend):
        """Parsed header has correct path."""
        header = backend.parse("int x;", "myfile.h")
        assert header.path == "myfile.h"

    def test_parse_produces_correct_ir_types(self, backend):
        """Parse produces correct IR types for a mixed declaration file."""
        code = """
        struct Config {
            int width;
            int height;
            const char* name;
        };

        enum Mode {
            MODE_NORMAL = 0,
            MODE_DEBUG = 1
        };

        int init(struct Config* cfg);
        void shutdown(void);
        """
        header = backend.parse(code, "test.h")

        # Check we got all declaration types
        structs = [d for d in header.declarations if isinstance(d, Struct)]
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        funcs = [d for d in header.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert len(enums) == 1
        assert len(funcs) == 2

        # Verify struct fields
        config = next(s for s in structs if s.name == "Config")
        assert len(config.fields) == 3
        assert config.fields[0].name == "width"
        assert config.fields[2].name == "name"
        assert isinstance(config.fields[2].type, Pointer)

        # Verify enum values
        mode = next(e for e in enums if e.name == "Mode")
        assert len(mode.values) == 2

        # Verify function signatures
        init_fn = next(f for f in funcs if f.name == "init")
        assert isinstance(init_fn.return_type, CType)
        assert init_fn.return_type.name == "int"
        assert len(init_fn.parameters) == 1
        assert isinstance(init_fn.parameters[0].type, Pointer)

        shutdown_fn = next(f for f in funcs if f.name == "shutdown")
        assert isinstance(shutdown_fn.return_type, CType)
        assert shutdown_fn.return_type.name == "void"
        assert len(shutdown_fn.parameters) == 0


@pytest.mark.allow("subprocess")
@libclang
class TestBackendRegistration:
    """Test that the backend registers itself when libclang is available."""

    def test_backend_registered(self):
        """If libclang is available, the backend should be registered."""
        from headerkit.backends import is_backend_available

        assert is_backend_available("libclang")

    def test_get_backend_returns_libclang(self):
        """get_backend('libclang') returns a LibclangBackend instance."""
        from headerkit.backends import get_backend

        backend = get_backend("libclang")
        assert isinstance(backend, LibclangBackend)

    def test_protocol_compliance(self):
        """LibclangBackend satisfies the ParserBackend protocol."""
        from headerkit.ir import ParserBackend

        backend = LibclangBackend()
        assert isinstance(backend, ParserBackend)


class TestGetSystemIncludeDirs:
    """Tests for get_system_include_dirs() system include path detection.

    Adapted from autopxd2 test_libclang_includes.py::_get_system_include_args.
    """

    def setup_method(self):
        """Clear the cached include dirs before each test."""
        self._saved_c = mod._system_include_cache_c
        self._saved_cxx = mod._system_include_cache_cxx
        mod._system_include_cache_c = None
        mod._system_include_cache_cxx = None

    def teardown_method(self):
        """Restore cached include dirs after each test."""
        mod._system_include_cache_c = self._saved_c
        mod._system_include_cache_cxx = self._saved_cxx

    @pytest.mark.allow("subprocess")
    def test_returns_list_of_strings(self):
        """get_system_include_dirs returns a list of strings."""
        result = get_system_include_dirs()
        assert isinstance(result, list)
        if shutil.which("clang") is not None:
            assert len(result) > 0, "clang is available but no include dirs found"
        for item in result:
            assert isinstance(item, str)

    @pytest.mark.allow("subprocess")
    def test_entries_are_isystem_flags(self):
        """Each entry should be an -isystem flag if any paths are found."""
        result = get_system_include_dirs()
        if shutil.which("clang") is not None:
            assert len(result) > 0, "clang is available but no include dirs found"
        for item in result:
            assert item.startswith("-isystem"), f"Expected -isystem prefix, got: {item}"

    @pytest.mark.allow("subprocess")
    def test_result_is_cached(self):
        """Second call returns the same cached list object."""
        first = get_system_include_dirs()
        second = get_system_include_dirs()
        assert first is second
        # On systems where clang is available, verify the cache contains real data
        if shutil.which("clang") is not None:
            assert len(first) > 0, "clang is available but cached result is empty"

    @pytest.mark.allow("subprocess")
    def test_c_and_cxx_cached_separately(self):
        """C and C++ include dirs are cached independently."""
        c_dirs = get_system_include_dirs(cplus=False)
        cxx_dirs = get_system_include_dirs(cplus=True)
        # They might be different (C++ includes libc++ paths)
        # But both should be lists
        assert isinstance(c_dirs, list)
        assert isinstance(cxx_dirs, list)
        # Verify they are separate cache entries
        c_dirs_again = get_system_include_dirs(cplus=False)
        assert c_dirs is c_dirs_again, "C dirs should be cached"
        cxx_dirs_again = get_system_include_dirs(cplus=True)
        assert cxx_dirs is cxx_dirs_again, "C++ dirs should be cached"

    def test_clang_not_found_returns_empty(self):
        """When clang is not on PATH, returns empty list."""
        import sys

        null_file = "NUL" if sys.platform == "win32" else "/dev/null"
        mod._system_include_cache_c = None
        bigfoot.subprocess_mock.mock_run(
            ["clang", "-v", "-x", "c", "-E", null_file],
            raises=FileNotFoundError(),
        )
        with bigfoot:
            result = get_system_include_dirs()
        assert result == []
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["clang", "-v", "-x", "c", "-E", null_file],
            returncode=AnyThing,
            stdout=AnyThing,
            stderr=AnyThing,
        )

    def test_clang_timeout_returns_empty(self):
        """When clang times out, returns empty list."""
        import sys

        null_file = "NUL" if sys.platform == "win32" else "/dev/null"
        mod._system_include_cache_c = None
        bigfoot.subprocess_mock.mock_run(
            ["clang", "-v", "-x", "c", "-E", null_file],
            raises=subprocess.TimeoutExpired(cmd="clang", timeout=10),
        )
        with bigfoot:
            result = get_system_include_dirs()
        assert result == []
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["clang", "-v", "-x", "c", "-E", null_file],
            returncode=AnyThing,
            stdout=AnyThing,
            stderr=AnyThing,
        )

    def test_parses_include_search_paths(self):
        """Parses clang -v output to extract include search paths."""
        import sys

        null_file = "NUL" if sys.platform == "win32" else "/dev/null"
        mod._system_include_cache_c = None
        bigfoot.subprocess_mock.mock_run(
            ["clang", "-v", "-x", "c", "-E", null_file],
            returncode=0,
            stderr=(
                "clang version 18.0.0\n"
                "#include <...> search starts here:\n"
                " /usr/lib/clang/18/include\n"
                " /usr/include\n"
                "End of search list.\n"
            ),
        )
        with bigfoot:
            result = get_system_include_dirs()
        assert result == ["-isystem/usr/lib/clang/18/include", "-isystem/usr/include"]
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["clang", "-v", "-x", "c", "-E", null_file],
            returncode=0,
            stdout=AnyThing,
            stderr=AnyThing,
        )

    def test_skips_framework_directories(self):
        """Framework directories are excluded from the result."""
        import sys

        null_file = "NUL" if sys.platform == "win32" else "/dev/null"
        mod._system_include_cache_c = None
        bigfoot.subprocess_mock.mock_run(
            ["clang", "-v", "-x", "c", "-E", null_file],
            returncode=0,
            stderr=(
                "#include <...> search starts here:\n"
                " /usr/include\n"
                " /System/Library/Frameworks (framework directory)\n"
                "End of search list.\n"
            ),
        )
        with bigfoot:
            result = get_system_include_dirs()
        assert result == ["-isystem/usr/include"]
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["clang", "-v", "-x", "c", "-E", null_file],
            returncode=0,
            stdout=AnyThing,
            stderr=AnyThing,
        )


@pytest.mark.allow("subprocess")
@libclang
class TestHeaderInclusionTracking:
    """Tests for tracking included headers via libclang parsing.

    Adapted from autopxd2 test_libclang_includes.py::TestHeaderInclusionTracking.
    """

    @pytest.fixture
    def backend(self):
        return LibclangBackend()

    def test_includes_stdio(self, backend):
        """Detects stdio.h inclusion when parsing code that includes it."""
        code = "#include <stdio.h>\nvoid test_func(FILE *f);\n"
        header = backend.parse(code, "test.h")
        assert any("stdio.h" in h for h in header.included_headers)

    def test_includes_stdint(self, backend):
        """Detects stdint.h inclusion."""
        code = "#include <stdint.h>\ntypedef uint32_t my_int;\n"
        header = backend.parse(code, "test.h")
        assert any("stdint.h" in h for h in header.included_headers)

    def test_includes_multiple_headers(self, backend):
        """Detects multiple header inclusions."""
        code = "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nvoid test(void);\n"
        header = backend.parse(code, "test.h")
        included_basenames = {os.path.basename(h) for h in header.included_headers}
        assert "stdio.h" in included_basenames
        assert "stdlib.h" in included_basenames
        assert "string.h" in included_basenames

    def test_no_includes_returns_set(self, backend):
        """Header with no #include directives has included_headers as a set."""
        code = "void standalone_func(int x);\n"
        header = backend.parse(code, "test.h")
        assert isinstance(header.included_headers, set)
        assert len(header.included_headers) == 0

    def test_includes_transitive(self, backend):
        """Tracks transitive includes (headers included by other headers)."""
        # stdio.h typically includes other headers transitively
        code = "#include <stdio.h>\nvoid test(void);\n"
        header = backend.parse(code, "test.h")
        # stdio.h must be present.
        assert any("stdio.h" in h for h in header.included_headers)
        # stdio.h on every supported platform (Linux, macOS, Windows) transitively
        # pulls in at least one additional system header (e.g. stddef.h, bits/types.h,
        # _stdio.h, corecrt.h, etc.). The exact set is environment-dependent, so we
        # only assert the count is >=2 rather than enumerating specific transitive files.
        assert len(header.included_headers) >= 2, (
            f"Expected transitive includes beyond stdio.h, got: {header.included_headers}"
        )


@pytest.mark.allow("subprocess")
@libclang
class TestTypeQualifierParsing:
    """Tests for type qualifier parsing through the libclang backend.

    Adapted from autopxd2 test_type_qualifiers.py. These test that the
    libclang backend correctly extracts const, volatile, and handles
    _Atomic / __restrict qualifiers in the IR.
    """

    @pytest.fixture
    def backend(self):
        return LibclangBackend()

    def test_const_qualifier_on_param(self, backend):
        """const qualifier is preserved on function parameter types."""
        code = "void process(const int* ptr);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        # The pointee should be const int
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "const" in pointee.qualifiers

    def test_volatile_qualifier_on_param(self, backend):
        """volatile qualifier is preserved on function parameter types."""
        code = "void modify(volatile int* ptr);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "volatile" in pointee.qualifiers

    def test_const_volatile_combined(self, backend):
        """Both const and volatile qualifiers are preserved together."""
        code = "void observe(const volatile int* ptr);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "const" in pointee.qualifiers
        assert "volatile" in pointee.qualifiers

    def test_const_char_pointer(self, backend):
        """const char* is a common pattern that should parse correctly."""
        code = "void print(const char* msg);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "const" in pointee.qualifiers
        assert pointee.name == "char"

    def test_atomic_typedef_parsed(self, backend):
        """_Atomic typedef is parsed (qualifier may be stripped by libclang)."""
        code = "typedef _Atomic int atomic_int;"
        header = backend.parse(code, "test.h")
        typedefs = [d for d in header.declarations if isinstance(d, Typedef)]
        assert len(typedefs) == 1
        td = typedefs[0]
        assert td.name == "atomic_int"
        # _Atomic may be stripped by libclang's type canonicalization;
        # the key thing is that the typedef is parsed and the base type is int
        assert isinstance(td.underlying_type, CType)
        assert "int" in td.underlying_type.name

    def test_atomic_in_struct_field(self, backend):
        """_Atomic types in struct fields are parsed."""
        code = """
        typedef _Atomic int atomic_int;
        struct counter {
            atomic_int value;
        };
        """
        header = backend.parse(code, "test.h")
        structs = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(structs) == 1
        counter = structs[0]
        assert counter.name == "counter"
        assert len(counter.fields) == 1
        assert counter.fields[0].name == "value"
        assert isinstance(counter.fields[0].type, CType)
        assert "int" in counter.fields[0].type.name

    def test_restrict_in_function_param(self, backend):
        """__restrict qualifier in function parameters is handled."""
        code = "void copy(char* __restrict dst, const char* __restrict src);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        func = funcs[0]
        assert func.name == "copy"
        assert len(func.parameters) == 2
        # __restrict is not a standard qualifier that headerkit preserves in IR,
        # but the function should still parse correctly
        assert func.parameters[0].name == "dst"
        assert func.parameters[1].name == "src"
        # dst should be a pointer type
        dst_param = func.parameters[0]
        assert isinstance(dst_param.type, Pointer), "dst should be a pointer type"
        # src should have const on its pointee
        src_param = func.parameters[1]
        assert isinstance(src_param.type, Pointer)
        assert "const" in src_param.type.pointee.qualifiers

    def test_noreturn_function_parsed(self, backend):
        """_Noreturn functions are parsed (qualifier stripped from IR)."""
        code = "_Noreturn void abort_program(void);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        func = funcs[0]
        assert func.name == "abort_program"
        assert isinstance(func.return_type, CType)
        assert func.return_type.name == "void"


@pytest.mark.allow("subprocess")
@libclang
class TestMacroParsing:
    """Tests for macro parsing via libclang backend."""

    def setup_method(self):
        self.backend = LibclangBackend()

    def test_simple_integer_macro(self):
        code = "#define SIZE 100\nvoid f(void);\n"
        header = self.backend.parse(code, "test.h")
        constants = [d for d in header.declarations if isinstance(d, Constant)]
        size_consts = [c for c in constants if c.name == "SIZE"]
        assert len(size_consts) == 1
        # _analyze_single_token parses "100" via int() and returns int value
        assert size_consts[0].value == 100
        assert isinstance(size_consts[0].value, int)

    def test_hex_macro(self):
        code = "#define MASK 0xFF\nvoid f(void);\n"
        header = self.backend.parse(code, "test.h")
        constants = [d for d in header.declarations if isinstance(d, Constant)]
        mask_consts = [c for c in constants if c.name == "MASK"]
        assert len(mask_consts) == 1
        assert mask_consts[0].value == 255, f"Expected 255 for 0xFF, got {mask_consts[0].value}"

    def test_negative_integer_macro(self):
        code = "#define ERROR_CODE -1\nvoid f(void);\n"
        header = self.backend.parse(code, "test.h")
        constants = [d for d in header.declarations if isinstance(d, Constant)]
        err_consts = [c for c in constants if c.name == "ERROR_CODE"]
        assert len(err_consts) == 1
        # Negative macro is a multi-token expression ("-" and "1");
        # _analyze_expression_tokens returns CType("int") with value=None
        assert err_consts[0].value is None
        assert err_consts[0].type is not None
        assert err_consts[0].type.name == "int"

    def test_string_macro_captured(self):
        """String macros are captured as Constant with quoted value and const char type."""
        code = '#define VERSION "1.0"\nvoid f(void);\n'
        header = self.backend.parse(code, "test.h")
        constants = [d for d in header.declarations if isinstance(d, Constant)]
        ver_consts = [c for c in constants if c.name == "VERSION"]
        # _analyze_single_token detects string literals and returns CType("char", ["const"])
        assert len(ver_consts) == 1
        assert ver_consts[0].value == '"1.0"'
        assert ver_consts[0].type is not None
        assert ver_consts[0].type.name == "char"
        assert "const" in ver_consts[0].type.qualifiers

    def test_function_like_macro_not_captured(self):
        code = "#define MAX(a,b) ((a)>(b)?(a):(b))\nvoid f(void);\n"
        header = self.backend.parse(code, "test.h")
        constants = [d for d in header.declarations if isinstance(d, Constant)]
        max_consts = [c for c in constants if c.name == "MAX"]
        # Function-like macros should not produce Constants
        assert len(max_consts) == 0


class TestLinuxVersionedSearchPaths:
    """Tests that versioned .so names are included in Linux search paths."""

    def test_versioned_so_in_lib64_paths(self):
        """RHEL/Fedora versioned names (libclang.so.18) appear in search paths."""
        with patch("headerkit.backends.libclang.sys.platform", "linux"):
            # Create fake versioned .so files in a temporary /usr/lib64-like dir
            # We patch glob.glob to return controlled results for the /usr/lib64 patterns
            original_glob = glob.glob

            def patched_glob(pattern: str, **kwargs: object) -> list[str]:
                if "/usr/lib64/libclang.so.*" in pattern:
                    return ["/usr/lib64/libclang.so.18", "/usr/lib64/libclang.so.17"]
                if "/usr/lib64/libclang-*.so" in pattern:
                    return ["/usr/lib64/libclang-18.so"]
                if "/usr/lib/libclang.so.*" in pattern:
                    return ["/usr/lib/libclang.so.18"]
                if "/usr/lib/libclang-*.so" in pattern:
                    return ["/usr/lib/libclang-18.so"]
                return original_glob(pattern, **kwargs)

            with patch("headerkit.backends.libclang.glob.glob", side_effect=patched_glob):
                paths = _get_libclang_search_paths()

            # Versioned names should appear before the unversioned ones
            assert "/usr/lib64/libclang.so.18" in paths
            assert "/usr/lib64/libclang.so.17" in paths
            assert "/usr/lib64/libclang-18.so" in paths
            assert "/usr/lib64/libclang.so" in paths
            assert "/usr/lib/libclang.so.18" in paths
            assert "/usr/lib/libclang-18.so" in paths

            # Versioned paths should come before unversioned
            idx_versioned = paths.index("/usr/lib64/libclang.so.18")
            idx_unversioned = paths.index("/usr/lib64/libclang.so")
            assert idx_versioned < idx_unversioned

    def test_generic_lib_versioned_so_paths(self):
        """Generic /usr/lib versioned names appear in search paths."""
        with patch("headerkit.backends.libclang.sys.platform", "linux"):
            paths = _get_libclang_search_paths()
            # The unversioned /usr/lib/libclang.so must always be present
            assert "/usr/lib/libclang.so" in paths
            assert "/usr/local/lib/libclang.so" in paths


class TestPipClangNativeSearchPath:
    """Tests that pip-installed clang/native/ path is included."""

    @pytest.mark.parametrize(
        ("platform", "search_location", "native_result"),
        [
            pytest.param(
                "linux",
                ["/fake/site-packages/clang"],
                ["/fake/site-packages/clang/native/libclang.so.18"],
                id="linux",
            ),
            pytest.param(
                "darwin",
                ["/fake/site-packages/clang"],
                ["/fake/site-packages/clang/native/libclang.dylib"],
                id="darwin",
            ),
            pytest.param(
                "win32",
                ["C:\\fake\\site-packages\\clang"],
                ["C:\\fake\\site-packages\\clang\\native\\libclang.dll"],
                id="win32",
            ),
        ],
    )
    def test_clang_native_dir_included(
        self, platform: str, search_location: list[str], native_result: list[str]
    ) -> None:
        """When clang package is findable, its native dir is searched on each platform."""
        import importlib.util
        import types

        mock_spec = types.SimpleNamespace(submodule_search_locations=search_location)

        env_overrides: dict[str, str] = {}
        if platform == "win32":
            env_overrides = {
                "PROGRAMFILES": r"C:\Program Files",
                "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
            }

        with (
            patch("headerkit.backends.libclang.sys.platform", platform),
            patch.dict(os.environ, env_overrides),
            patch.object(importlib.util, "find_spec", return_value=mock_spec),
            patch("os.path.isdir", return_value=True),
            patch(
                "headerkit.backends.libclang.glob.glob",
                side_effect=lambda pattern, **_kw: native_result if "native" in pattern else [],
            ),
        ):
            paths = _get_libclang_search_paths()
            assert any("native" in p.lower() for p in paths), (
                f"Expected clang/native path in search paths, got: {paths}"
            )

    def test_clang_package_not_found_is_harmless(self):
        """When clang package is not installed, search paths still work."""
        import importlib.util

        with (
            patch("headerkit.backends.libclang.sys.platform", "linux"),
            patch.object(importlib.util, "find_spec", return_value=None),
        ):
            paths = _get_libclang_search_paths()
            # Should still return the standard Linux paths
            assert any("libclang" in p for p in paths)


class TestWindowsAddDllDirectory:
    """Tests that os.add_dll_directory is called on Windows during configure."""

    def test_add_dll_directory_called_on_windows(self):
        """os.add_dll_directory is invoked before loading on Windows."""

        saved_configured = mod._libclang_configured
        saved_cindex = mod._cindex

        try:
            mod._libclang_configured = False

            mock_cindex = type("MockCindex", (), {})()
            mock_config_cls = type(
                "Config",
                (),
                {
                    "loaded": False,
                    "library_file": None,
                    "library_path": None,
                    "compatibility_check": True,
                },
            )

            class MockLibclangError(Exception):
                pass

            mock_cindex.Config = mock_config_cls
            mock_cindex.LibclangError = MockLibclangError
            mock_cindex.CursorKind = type("CK", (), {})()
            mock_cindex.TypeKind = type("TK", (), {})()

            # set_compatibility_check should succeed when not loaded
            mock_config_cls.set_compatibility_check = staticmethod(lambda _v: None)

            def mock_set_library_file(_f: str) -> None:
                pass

            mock_config_cls.set_library_file = staticmethod(mock_set_library_file)

            def mock_get_library(_self: object) -> object:
                raise MockLibclangError("not found")

            mock_config_cls.get_cindex_library = mock_get_library

            # Use a forward-slash path so os.path.dirname works on all platforms.
            # On actual Windows, both separators work; we test the add_dll_directory
            # call site, not Windows path semantics.
            candidate_path = "C:/LLVM/bin/libclang.dll"
            expected_dir = os.path.dirname(candidate_path)

            # Patch _get_cindex to return our mock
            with (
                patch.object(mod, "_cindex", None),
                patch("headerkit.backends.libclang._get_cindex", return_value=mock_cindex),
                patch("headerkit.backends.libclang.sys.platform", "win32"),
                patch(
                    "headerkit.backends.libclang._get_libclang_search_paths",
                    return_value=[candidate_path],
                ),
                patch("os.path.isfile", return_value=True),
                patch("os.add_dll_directory", create=True) as mock_add_dll,
            ):
                # Configure will fail (mock raises), but add_dll_directory should be called
                _configure_libclang()
                mock_add_dll.assert_called_once_with(expected_dir)
        finally:
            mod._libclang_configured = saved_configured
            mod._cindex = saved_cindex


class TestReloadBackendsCindexReset:
    """Tests that reload_backends() resets vendored cindex Config state."""

    def test_reload_resets_cindex_config(self):
        """reload_backends() resets cindex Config.loaded and library_file.

        Uses a ``side_effect`` on ``_ensure_backends_loaded`` to capture
        Config state at the exact moment between the reset and re-discovery
        (on CI where libclang is installed, re-discovery would set them back).
        """
        from unittest.mock import patch

        from headerkit._clang import _cached_cindex

        if _cached_cindex is None:
            pytest.skip("cindex not loaded")

        orig_loaded = _cached_cindex.Config.loaded
        orig_library_file = _cached_cindex.Config.library_file

        captured: dict[str, object] = {}

        def capture_state() -> None:
            captured["loaded"] = _cached_cindex.Config.loaded
            captured["library_file"] = _cached_cindex.Config.library_file

        try:
            _cached_cindex.Config.loaded = True
            _cached_cindex.Config.library_file = "/fake/libclang.so"

            with patch(
                "headerkit.backends._ensure_backends_loaded",
                side_effect=capture_state,
            ):
                reload_backends()

            assert captured["loaded"] is False
            assert captured["library_file"] is None
        finally:
            _cached_cindex.Config.loaded = orig_loaded
            _cached_cindex.Config.library_file = orig_library_file
