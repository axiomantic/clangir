"""End-to-end tests with real-world C/C++ library headers.

Downloads actual library headers and tests the full parse -> IR -> write
pipeline for both CFFI and JSON writers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from headerkit.backends import is_backend_available
from headerkit.ir import Function, Header
from headerkit.writers.cffi import header_to_cffi
from headerkit.writers.json import header_to_json, header_to_json_dict

pytestmark = [
    pytest.mark.skipif(
        not is_backend_available("libclang"),
        reason="libclang backend not available",
    ),
    pytest.mark.download,
]


def _parse_header(
    backend,
    header_path: Path,
    include_dirs: list[str] | None = None,
) -> Header:
    """Parse a header file, failing the test on parse failure."""
    code = header_path.read_text()
    try:
        return backend.parse(code, header_path.name, include_dirs=include_dirs)
    except RuntimeError as exc:
        pytest.fail(f"Parse failed (should not happen on known-good headers): {exc}")


def _skip_if_unavailable(fixture_value: Path | None, name: str) -> None:
    """Skip the test if the header fixture returned None (download failure)."""
    if fixture_value is None:
        pytest.skip(f"{name} header not available (download failed)")


# =============================================================================
# sqlite3
# =============================================================================


@pytest.mark.timeout(120)
class TestSqlite3:
    """Test the sqlite3.h amalgamation header through the full pipeline."""

    def test_parse(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        # sqlite3.h amalgamation defines hundreds of functions; 100 is a
        # conservative lower bound that holds across all known versions.
        assert len(header.declarations) >= 100, (
            f"Expected >=100 declarations from sqlite3, got {len(header.declarations)}"
        )
        # Verify specific well-known symbols are present regardless of version.
        names = {d.name for d in header.declarations}
        assert "sqlite3_open" in names
        assert "sqlite3_close" in names
        assert "sqlite3_exec" in names

    def test_cffi_write(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        cffi_output = header_to_cffi(header)
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration
        # (not just as a substring within an unrelated identifier).
        # Full signature equality is omitted because parameter types/names
        # vary slightly across sqlite3 versions and OS-bundled headers.
        cffi_lines = cffi_output.splitlines()
        for symbol in ("sqlite3_open", "sqlite3_close", "sqlite3_exec"):
            # Use word-boundary match to avoid false positives from longer names
            # (e.g. sqlite3_open_v2 would match a bare "sqlite3_open" substring search).
            matching = [line for line in cffi_lines if re.search(rf"\b{symbol}\b", line)]
            assert len(matching) >= 1, f"Expected CFFI declaration for {symbol}"
            # Each match must look like a complete C declaration ending in ';'
            assert all(line.rstrip().endswith(";") for line in matching), (
                f"CFFI line for {symbol} does not end with ';': {matching}"
            )

    def test_json_write(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        json_output = header_to_json(header)
        parsed = json.loads(json_output)
        assert isinstance(parsed, dict)
        assert "declarations" in parsed
        # Verify specific known symbols appear in the JSON output.
        names = {d["name"] for d in parsed["declarations"] if "name" in d}
        assert "sqlite3_open" in names
        assert "sqlite3_close" in names
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) == len(header.declarations)

    def test_known_symbols(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        names = {d.name for d in header.declarations}
        assert "sqlite3_open" in names
        assert "sqlite3_close" in names
        assert "sqlite3_exec" in names
        functions = {d.name for d in header.declarations if isinstance(d, Function)}
        assert "sqlite3_open" in functions
        assert "sqlite3_close" in functions
        assert "sqlite3_exec" in functions


# =============================================================================
# zlib
# =============================================================================


@pytest.mark.timeout(120)
class TestZlib:
    """Test the zlib.h header through the full pipeline."""

    def test_parse(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        # zlib.h defines at least ~20 functions/types across all known versions.
        assert len(header.declarations) >= 20, f"Expected >=20 declarations from zlib, got {len(header.declarations)}"
        # Verify specific well-known symbols are present regardless of version.
        names = {d.name for d in header.declarations}
        assert "deflate" in names
        assert "inflate" in names
        assert "compress" in names

    def test_cffi_write(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        cffi_output = header_to_cffi(header)
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because zlib function signatures
        # vary across header versions (e.g., z_const, uLong vs uInt).
        cffi_lines = cffi_output.splitlines()
        for symbol in ("deflate", "inflate", "compress"):
            matching = [line for line in cffi_lines if re.search(rf"\b{symbol}\b", line)]
            assert len(matching) >= 1, f"Expected CFFI declaration for {symbol}"
            assert all(line.rstrip().endswith(";") for line in matching), (
                f"CFFI line for {symbol} does not end with ';': {matching}"
            )

    def test_json_write(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        json_output = header_to_json(header)
        parsed = json.loads(json_output)
        assert isinstance(parsed, dict)
        assert "declarations" in parsed
        # Verify specific known symbols appear in the JSON output.
        names = {d["name"] for d in parsed["declarations"] if "name" in d}
        assert "deflate" in names
        assert "inflate" in names
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) == len(header.declarations)

    def test_known_symbols(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        names = {d.name for d in header.declarations}
        assert "deflate" in names
        assert "inflate" in names
        assert "compress" in names
        assert "uncompress" in names
        assert "z_stream" in names
        functions = {d.name for d in header.declarations if isinstance(d, Function)}
        assert "deflate" in functions
        assert "inflate" in functions
        assert "compress" in functions


# =============================================================================
# lua
# =============================================================================


@pytest.mark.timeout(120)
class TestLua:
    """Test lua headers through the full pipeline."""

    def test_parse(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        # lua.h defines at least ~20 functions across all known 5.x versions.
        assert len(header.declarations) >= 20, f"Expected >=20 declarations from lua, got {len(header.declarations)}"
        # Verify specific well-known symbols are present regardless of version.
        names = {d.name for d in header.declarations}
        assert "lua_pushstring" in names
        assert "lua_close" in names

    def test_cffi_write(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        cffi_output = header_to_cffi(header)
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because Lua function signatures
        # include lua_State* which may be qualified differently per version.
        cffi_lines = cffi_output.splitlines()
        for symbol in ("lua_pushstring", "lua_close"):
            matching = [line for line in cffi_lines if re.search(rf"\b{symbol}\b", line)]
            assert len(matching) >= 1, f"Expected CFFI declaration for {symbol}"
            assert all(line.rstrip().endswith(";") for line in matching), (
                f"CFFI line for {symbol} does not end with ';': {matching}"
            )

    def test_json_write(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        json_output = header_to_json(header)
        parsed = json.loads(json_output)
        assert isinstance(parsed, dict)
        assert "declarations" in parsed
        # Verify specific known symbols appear in the JSON output.
        names = {d["name"] for d in parsed["declarations"] if "name" in d}
        assert "lua_pushstring" in names
        assert "lua_close" in names
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) == len(header.declarations)

    def test_known_symbols(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        names = {d.name for d in header.declarations}
        assert "lua_pushstring" in names
        # lua_tonumber is a macro wrapping lua_tonumberx in Lua 5.4+
        assert "lua_tonumberx" in names
        # lua_pcall is a macro wrapping lua_pcallk in Lua 5.4+
        assert "lua_pcallk" in names
        assert "lua_close" in names
        functions = {d.name for d in header.declarations if isinstance(d, Function)}
        assert "lua_pushstring" in functions
        assert "lua_close" in functions


# =============================================================================
# libcurl
# =============================================================================


@pytest.mark.timeout(120)
class TestCurl:
    """Test curl headers through the full pipeline."""

    def test_parse(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        # curl.h defines at least ~50 symbols across all known versions.
        assert len(header.declarations) >= 50, f"Expected >=50 declarations from curl, got {len(header.declarations)}"
        # Verify specific well-known symbols are present regardless of version.
        names = {d.name for d in header.declarations}
        assert "curl_global_init" in names
        assert "curl_version" in names

    def test_cffi_write(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        cffi_output = header_to_cffi(header)
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because curl parameter types
        # vary across libcurl versions (e.g., curl_off_t, CURL* typedef names).
        cffi_lines = cffi_output.splitlines()
        for symbol in ("curl_global_init", "curl_version"):
            matching = [line for line in cffi_lines if re.search(rf"\b{symbol}\b", line)]
            assert len(matching) >= 1, f"Expected CFFI declaration for {symbol}"
            assert all(line.rstrip().endswith(";") for line in matching), (
                f"CFFI line for {symbol} does not end with ';': {matching}"
            )

    def test_json_write(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        json_output = header_to_json(header)
        parsed = json.loads(json_output)
        assert isinstance(parsed, dict)
        assert "declarations" in parsed
        # Verify specific known symbols appear in the JSON output.
        names = {d["name"] for d in parsed["declarations"] if "name" in d}
        assert "curl_global_init" in names
        assert "curl_version" in names
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) == len(header.declarations)

    def test_known_symbols(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        names = {d.name for d in header.declarations}
        # curl_easy_init/perform/cleanup are in easy.h (included header)
        # and may not appear depending on recursive include behavior.
        # These symbols are defined directly in curl.h:
        assert "curl_global_init" in names
        assert "curl_global_cleanup" in names
        assert "curl_version" in names
        assert "curl_slist_append" in names
        functions = {d.name for d in header.declarations if isinstance(d, Function)}
        assert "curl_global_init" in functions
        assert "curl_version" in functions


# =============================================================================
# SDL2
# =============================================================================


@pytest.mark.timeout(120)
class TestSDL2:
    """Test SDL2 headers through the full pipeline."""

    def test_parse(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        # SDL.h is an umbrella header whose own declarations are mostly
        # SDL_Init* functions/macros. The libclang backend filters to the
        # main file, so sub-header declarations are not counted here.
        assert len(header.declarations) >= 10, f"Expected >=10 declarations from SDL2, got {len(header.declarations)}"
        # Verify specific well-known symbols are present regardless of version.
        names = {d.name for d in header.declarations}
        assert "SDL_Init" in names
        assert "SDL_Quit" in names

    def test_cffi_write(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        cffi_output = header_to_cffi(header)
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because SDL_Init/SDL_Quit
        # parameter types vary slightly across SDL2 versions.
        cffi_lines = cffi_output.splitlines()
        for symbol in ("SDL_Init", "SDL_Quit"):
            matching = [line for line in cffi_lines if re.search(rf"\b{symbol}\b", line)]
            assert len(matching) >= 1, f"Expected CFFI declaration for {symbol}"
            assert all(line.rstrip().endswith(";") for line in matching), (
                f"CFFI line for {symbol} does not end with ';': {matching}"
            )

    def test_json_write(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        json_output = header_to_json(header)
        parsed = json.loads(json_output)
        assert isinstance(parsed, dict)
        assert "declarations" in parsed
        # Verify specific known symbols appear in the JSON output.
        names = {d["name"] for d in parsed["declarations"] if "name" in d}
        assert "SDL_Init" in names
        assert "SDL_Quit" in names
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) == len(header.declarations)

    def test_known_symbols(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        names = {d.name for d in header.declarations}
        # SDL.h defines init/quit functions directly
        assert "SDL_Init" in names
        assert "SDL_Quit" in names
        assert "SDL_WasInit" in names
        functions = {d.name for d in header.declarations if isinstance(d, Function)}
        assert "SDL_Init" in functions
        assert "SDL_Quit" in functions


# =============================================================================
# CPython
# =============================================================================


@pytest.mark.timeout(120)
class TestCPython:
    """Test CPython headers through the full pipeline.

    Python.h requires platform-specific pyconfig.h which is generated at
    build time, so parsing is expected to fail on most systems.
    """

    @pytest.mark.xfail(reason="Python.h requires platform-specific pyconfig.h")
    def test_parse(self, backend, cpython_headers):
        _skip_if_unavailable(cpython_headers, "CPython")
        inc_dir = cpython_headers / "Include"
        code = (inc_dir / "Python.h").read_text()
        # Let the error propagate so xfail can catch it
        header = backend.parse(code, "Python.h", include_dirs=[str(inc_dir)])
        # Python.h must define at least Py_Initialize and Py_Finalize.
        names = {d.name for d in header.declarations}
        assert "Py_Initialize" in names
        assert "Py_Finalize" in names
