"""End-to-end tests with real-world C/C++ library headers.

Downloads actual library headers and tests the full parse -> IR -> write
pipeline for all writers: CFFI, JSON, ctypes, Cython, Lua, prompt, and diff.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from headerkit.backends import is_backend_available
from headerkit.ir import Function, Header, Struct
from headerkit.writers.cffi import header_to_cffi
from headerkit.writers.ctypes import header_to_ctypes
from headerkit.writers.cython import write_pxd
from headerkit.writers.diff import DiffWriter
from headerkit.writers.json import header_to_json, header_to_json_dict
from headerkit.writers.lua import header_to_lua
from headerkit.writers.prompt import PromptWriter
from tests.skip_policy import missing_toolchain

pytestmark = [
    pytest.mark.skipif(
        not is_backend_available("libclang"),
        reason="libclang backend not available",
    ),
    pytest.mark.download,
    pytest.mark.allow("subprocess"),
]


def _parse_header(
    backend,
    header_path: Path,
    include_dirs: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> Header:
    """Parse a header file, failing the test on parse failure."""
    code = header_path.read_text()
    try:
        return backend.parse(code, header_path.name, include_dirs=include_dirs, extra_args=extra_args)
    except RuntimeError as exc:
        pytest.fail(f"Parse failed (should not happen on known-good headers): {exc}")


def _skip_if_unavailable(fixture_value: Path | None, name: str) -> None:
    """Under CI a failed download is a failure; locally it is a skip.

    A download that quietly turns the test green proves nothing about the real
    header it was supposed to parse, and network flakiness is exactly the kind of
    absence that reads as fine.
    """
    if fixture_value is None:
        missing_toolchain(
            f"the {name} header could not be downloaded",
            "check network access from the runner, and the test-header cache step in .github/workflows/test.yml",
        )


def _check_ctypes_write(header: Header, symbol: str) -> None:
    """Assert the ctypes writer produces a non-empty binding with argtypes for symbol."""
    output = header_to_ctypes(header)
    assert isinstance(output, str) and len(output) > 0
    assert f"_lib.{symbol}.argtypes = " in output


def _check_cython_write(header: Header, symbol: str) -> None:
    """Assert the cython writer produces a .pxd with cdef extern from and the given symbol."""
    output = write_pxd(header)
    assert isinstance(output, str) and len(output) > 0
    assert "cdef extern from" in output
    assert symbol in output


def _check_lua_write(header: Header, symbol: str) -> None:
    """Assert the lua writer produces a LuaJIT FFI binding with ffi.cdef[[ and the given symbol."""
    output = header_to_lua(header)
    assert isinstance(output, str) and len(output) > 0
    assert "ffi.cdef[[" in output
    assert symbol in output


def _check_prompt_write(header: Header, symbol: str) -> None:
    """Assert the prompt writer (compact) produces a non-empty string with FUNC {symbol}."""
    output = PromptWriter(verbosity="compact").write(header)
    assert isinstance(output, str) and len(output) > 0
    assert f"FUNC {symbol}" in output


def _check_diff_write(header: Header) -> None:
    """Assert an identity diff via DiffWriter produces total==0 and entries==[]."""
    result = json.loads(DiffWriter(baseline=header).write(header))
    assert result["summary"]["total"] == 0
    assert result["entries"] == []


def _check_cffi_write(header: Header, *symbols: str) -> None:
    """Assert the cffi writer produces complete C declarations for each symbol."""
    cffi_output = header_to_cffi(header)
    cffi_lines = cffi_output.splitlines()
    for symbol in symbols:
        matching = [line for line in cffi_lines if re.search(rf"\b{symbol}\b", line)]
        assert len(matching) >= 1, f"Expected CFFI declaration for {symbol}"
        assert all(line.rstrip().endswith(";") for line in matching), (
            f"CFFI line for {symbol} does not end with ';': {matching}"
        )


def _check_json_write(header: Header, *symbols: str) -> None:
    """Assert the json writer produces a valid declarations dict containing each symbol."""
    json_output = header_to_json(header)
    parsed = json.loads(json_output)
    assert isinstance(parsed, dict)
    assert "declarations" in parsed
    names = {d["name"] for d in parsed["declarations"] if "name" in d}
    for symbol in symbols:
        assert symbol in names
    json_dict = header_to_json_dict(header)
    assert "declarations" in json_dict
    assert len(json_dict["declarations"]) == len(header.declarations)


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
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration
        # (not just as a substring within an unrelated identifier).
        # Full signature equality is omitted because parameter types/names
        # vary slightly across sqlite3 versions and OS-bundled headers.
        # Use word-boundary match to avoid false positives from longer names
        # (e.g. sqlite3_open_v2 would match a bare "sqlite3_open" substring search).
        _check_cffi_write(header, "sqlite3_open", "sqlite3_close", "sqlite3_exec")

    def test_json_write(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        # Verify specific known symbols appear in the JSON output.
        _check_json_write(header, "sqlite3_open", "sqlite3_close")

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

    def test_ctypes_write(self, backend, sqlite3_header):
        # ESCAPE: test_ctypes_write (TestSqlite3)
        # CLAIM: sqlite3.h parsed through ctypes writer produces a non-empty Python binding
        #        string containing the ctypes argtypes assignment for sqlite3_open.
        # PATH:  backend.parse(sqlite3.h) -> Header -> header_to_ctypes -> str
        # CHECK: isinstance+len>0 proves it is a non-empty string; argtypes line proves
        #        the writer emits function bindings for at least one known sqlite3 symbol.
        # MUTATION: A writer that returns "" fails len>0. A writer that omits argtypes
        #           lines fails the argtypes assertion.
        # ESCAPE: A writer that returns "x" passes len>0 but fails the argtypes assertion.
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        _check_ctypes_write(header, "sqlite3_open")

    def test_cython_write(self, backend, sqlite3_header):
        # ESCAPE: test_cython_write (TestSqlite3)
        # CLAIM: sqlite3.h parsed through the cython writer produces a .pxd string
        #        containing 'cdef extern from' and the sqlite3_open function signature.
        # PATH:  backend.parse(sqlite3.h) -> Header -> write_pxd -> str
        # CHECK: isinstance+len>0 proves non-empty; 'cdef extern from' is the mandatory
        #        .pxd header; 'sqlite3_open' proves at least one known function is emitted.
        # MUTATION: A writer that returns "" fails len>0. A writer that drops the extern
        #           block header fails 'cdef extern from'. A writer that silently drops
        #           sqlite3_open fails the name assertion.
        # ESCAPE: A writer returning only the extern header (no declarations) passes the
        #         extern check but fails the sqlite3_open name check.
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        _check_cython_write(header, "sqlite3_open")

    def test_lua_write(self, backend, sqlite3_header):
        # ESCAPE: test_lua_write (TestSqlite3)
        # CLAIM: sqlite3.h parsed through the lua writer produces a LuaJIT FFI binding
        #        string with 'ffi.cdef[[' block and sqlite3_open function declaration.
        # PATH:  backend.parse(sqlite3.h) -> Header -> header_to_lua -> str
        # CHECK: isinstance+len>0 proves non-empty; 'ffi.cdef[[' is the mandatory FFI
        #        block opener; 'sqlite3_open' proves at least one known function is emitted.
        # MUTATION: A writer that omits the ffi.cdef[[ block fails that assertion.
        #           A writer that drops sqlite3_open fails the name assertion.
        # ESCAPE: A writer returning only the ffi.cdef[[ header without content passes
        #         the ffi.cdef[[ check but fails the sqlite3_open check.
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        _check_lua_write(header, "sqlite3_open")

    def test_prompt_write(self, backend, sqlite3_header):
        # ESCAPE: test_prompt_write (TestSqlite3)
        # CLAIM: sqlite3.h parsed through the prompt writer (compact) produces a string
        #        containing a FUNC line for sqlite3_open.
        # PATH:  backend.parse(sqlite3.h) -> Header -> PromptWriter(compact).write -> str
        # CHECK: isinstance+len>0 proves non-empty; 'FUNC sqlite3_open' is the compact
        #        writer's prefix for function declarations with a known symbol name.
        # MUTATION: A writer that returns "" fails len>0. A writer that emits 'fn sqlite3_open'
        #           instead of 'FUNC sqlite3_open' fails the prefix assertion.
        # ESCAPE: A writer that returns 'FUNC sqlite3_open' with no other content passes;
        #         this is acceptable -- the structural prefix plus name is sufficient signal.
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        _check_prompt_write(header, "sqlite3_open")

    def test_diff_write(self, backend, sqlite3_header):
        # ESCAPE: test_diff_write (TestSqlite3)
        # CLAIM: Diffing sqlite3.h against itself via DiffWriter produces a JSON document
        #        with summary.total==0 and an empty entries list.
        # PATH:  backend.parse(sqlite3.h) -> Header -> DiffWriter(baseline=header).write(header)
        #        -> json.loads -> dict with summary and entries keys
        # CHECK: summary.total==0 proves no spurious diffs are generated for an identity diff.
        #        entries==[] proves no individual diff entries are emitted.
        # MUTATION: A writer that generates spurious entries for identical headers would fail
        #           total==0. A writer that uses wrong comparison key would fail entries==[].
        # ESCAPE: A writer that returns '{"summary": {"total": 0}, "entries": null}' would
        #         fail entries==[] (null != []).
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        _check_diff_write(header)


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
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because zlib function signatures
        # vary across header versions (e.g., z_const, uLong vs uInt).
        _check_cffi_write(header, "deflate", "inflate", "compress")

    def test_json_write(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        # Verify specific known symbols appear in the JSON output.
        _check_json_write(header, "deflate", "inflate")

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

    def test_ctypes_write(self, backend, zlib_header):
        # ESCAPE: test_ctypes_write (TestZlib)
        # CLAIM: zlib.h parsed through ctypes writer produces a non-empty Python binding
        #        string containing the ctypes argtypes assignment for compress.
        # PATH:  backend.parse(zlib.h) -> Header -> header_to_ctypes -> str
        # CHECK: isinstance+len>0 proves non-empty; argtypes line proves the writer emits
        #        function bindings for at least one known zlib symbol.
        # MUTATION: A writer that returns "" fails len>0. A writer that omits compress
        #           argtypes fails the argtypes assertion.
        # ESCAPE: A writer that returns non-empty but omits all argtypes lines fails the check.
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        _check_ctypes_write(header, "compress")

    def test_cython_write(self, backend, zlib_header):
        # ESCAPE: test_cython_write (TestZlib)
        # CLAIM: zlib.h parsed through the cython writer produces a .pxd string
        #        containing 'cdef extern from' and the compress function signature.
        # PATH:  backend.parse(zlib.h) -> Header -> write_pxd -> str
        # CHECK: isinstance+len>0; 'cdef extern from' is the mandatory .pxd block;
        #        'compress' proves the known zlib function is emitted.
        # MUTATION: Dropping the extern block header fails 'cdef extern from'. Dropping
        #           compress silently fails the name assertion.
        # ESCAPE: An extern-only header (no declarations) passes extern check but fails compress.
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        _check_cython_write(header, "compress")

    def test_lua_write(self, backend, zlib_header):
        # ESCAPE: test_lua_write (TestZlib)
        # CLAIM: zlib.h parsed through the lua writer produces a LuaJIT FFI binding
        #        string with 'ffi.cdef[[' block and compress function declaration.
        # PATH:  backend.parse(zlib.h) -> Header -> header_to_lua -> str
        # CHECK: isinstance+len>0; 'ffi.cdef[[' is the mandatory FFI block opener;
        #        'compress' proves the known zlib function is emitted.
        # MUTATION: Omitting ffi.cdef[[ fails that assertion. Dropping compress fails the name check.
        # ESCAPE: An ffi.cdef[[ block without declarations passes the opener but fails the name check.
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        _check_lua_write(header, "compress")

    def test_prompt_write(self, backend, zlib_header):
        # ESCAPE: test_prompt_write (TestZlib)
        # CLAIM: zlib.h parsed through the prompt writer (compact) produces a string
        #        containing a FUNC line for compress.
        # PATH:  backend.parse(zlib.h) -> Header -> PromptWriter(compact).write -> str
        # CHECK: isinstance+len>0; 'FUNC compress' is the compact writer's prefix for the
        #        known compress function.
        # MUTATION: A writer emitting 'fn compress' instead of 'FUNC compress' fails.
        # ESCAPE: A string with only 'FUNC compress' passes; structural prefix + name is sufficient.
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        _check_prompt_write(header, "compress")

    def test_diff_write(self, backend, zlib_header):
        # ESCAPE: test_diff_write (TestZlib)
        # CLAIM: Diffing zlib.h against itself produces JSON with summary.total==0 and entries==[].
        # PATH:  backend.parse(zlib.h) -> Header -> DiffWriter(baseline=header).write(header)
        #        -> json.loads -> dict
        # CHECK: total==0 proves no spurious diffs for identity. entries==[] proves no entries emitted.
        # MUTATION: Spurious entries generation fails total==0. entries=None fails entries==[].
        # ESCAPE: '{"summary": {"total": 0}, "entries": null}' fails entries==[].
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header, include_dirs=[str(zlib_header.parent)])
        _check_diff_write(header)


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
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because Lua function signatures
        # include lua_State* which may be qualified differently per version.
        _check_cffi_write(header, "lua_pushstring", "lua_close")

    def test_json_write(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        # Verify specific known symbols appear in the JSON output.
        _check_json_write(header, "lua_pushstring", "lua_close")

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

    def test_ctypes_write(self, backend, lua_headers):
        # ESCAPE: test_ctypes_write (TestLua)
        # CLAIM: lua.h parsed through ctypes writer produces a non-empty Python binding
        #        string containing the ctypes argtypes assignment for lua_close.
        # PATH:  backend.parse(lua.h) -> Header -> header_to_ctypes -> str
        # CHECK: isinstance+len>0 proves non-empty; argtypes line proves the writer emits
        #        function bindings for at least one known lua symbol.
        # MUTATION: A writer omitting lua_close argtypes fails the argtypes assertion.
        # ESCAPE: A non-empty writer that omits all argtypes lines fails the check.
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        _check_ctypes_write(header, "lua_close")

    def test_cython_write(self, backend, lua_headers):
        # ESCAPE: test_cython_write (TestLua)
        # CLAIM: lua.h parsed through the cython writer produces a .pxd string
        #        containing 'cdef extern from' and the lua_close function signature.
        # PATH:  backend.parse(lua.h) -> Header -> write_pxd -> str
        # CHECK: isinstance+len>0; 'cdef extern from' is mandatory; 'lua_close' proves
        #        at least one known lua function is emitted.
        # MUTATION: Dropping the extern block header fails 'cdef extern from'.
        # ESCAPE: An extern-only header (no declarations) passes extern check but fails lua_close.
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        _check_cython_write(header, "lua_close")

    def test_lua_write(self, backend, lua_headers):
        # ESCAPE: test_lua_write (TestLua)
        # CLAIM: lua.h parsed through the lua writer produces a LuaJIT FFI binding
        #        string with 'ffi.cdef[[' block and lua_close function declaration.
        # PATH:  backend.parse(lua.h) -> Header -> header_to_lua -> str
        # CHECK: isinstance+len>0; 'ffi.cdef[[' is the mandatory FFI block opener;
        #        'lua_close' proves the known lua function is emitted.
        # MUTATION: Omitting ffi.cdef[[ fails that assertion. Dropping lua_close fails the name check.
        # ESCAPE: An ffi.cdef[[ block without declarations passes the opener but fails lua_close.
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        _check_lua_write(header, "lua_close")

    def test_prompt_write(self, backend, lua_headers):
        # ESCAPE: test_prompt_write (TestLua)
        # CLAIM: lua.h parsed through the prompt writer (compact) produces a string
        #        containing a FUNC line for lua_close.
        # PATH:  backend.parse(lua.h) -> Header -> PromptWriter(compact).write -> str
        # CHECK: isinstance+len>0; 'FUNC lua_close' is the compact writer's prefix for
        #        the known lua_close function.
        # MUTATION: A writer emitting 'fn lua_close' instead of 'FUNC lua_close' fails.
        # ESCAPE: A string with only 'FUNC lua_close' passes; structural prefix + name is sufficient.
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        _check_prompt_write(header, "lua_close")

    def test_diff_write(self, backend, lua_headers):
        # ESCAPE: test_diff_write (TestLua)
        # CLAIM: Diffing lua.h against itself produces JSON with summary.total==0 and entries==[].
        # PATH:  backend.parse(lua.h) -> Header -> DiffWriter(baseline=header).write(header)
        #        -> json.loads -> dict
        # CHECK: total==0 proves no spurious diffs for identity. entries==[] proves no entries emitted.
        # MUTATION: Spurious entries generation fails total==0. entries=None fails entries==[].
        # ESCAPE: '{"summary": {"total": 0}, "entries": null}' fails entries==[].
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        _check_diff_write(header)


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
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because curl parameter types
        # vary across libcurl versions (e.g., curl_off_t, CURL* typedef names).
        _check_cffi_write(header, "curl_global_init", "curl_version")

    def test_json_write(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        # Verify specific known symbols appear in the JSON output.
        _check_json_write(header, "curl_global_init", "curl_version")

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

    def test_ctypes_write(self, backend, curl_headers):
        # ESCAPE: test_ctypes_write (TestCurl)
        # CLAIM: curl.h parsed through ctypes writer produces a non-empty Python binding
        #        string containing the ctypes argtypes assignment for curl_global_init.
        # PATH:  backend.parse(curl.h) -> Header -> header_to_ctypes -> str
        # CHECK: isinstance+len>0 proves non-empty; argtypes line proves the writer emits
        #        function bindings for at least one known curl symbol.
        # MUTATION: A writer omitting curl_global_init argtypes fails the argtypes assertion.
        # ESCAPE: A non-empty writer that omits all argtypes lines fails the check.
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        _check_ctypes_write(header, "curl_global_init")

    def test_cython_write(self, backend, curl_headers):
        # ESCAPE: test_cython_write (TestCurl)
        # CLAIM: curl.h parsed through the cython writer produces a .pxd string
        #        containing 'cdef extern from' and the curl_global_init function signature.
        # PATH:  backend.parse(curl.h) -> Header -> write_pxd -> str
        # CHECK: isinstance+len>0; 'cdef extern from' is mandatory; 'curl_global_init' proves
        #        at least one known curl function is emitted.
        # MUTATION: Dropping the extern block header fails 'cdef extern from'.
        # ESCAPE: An extern-only header passes extern check but fails curl_global_init.
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        _check_cython_write(header, "curl_global_init")

    def test_lua_write(self, backend, curl_headers):
        # ESCAPE: test_lua_write (TestCurl)
        # CLAIM: curl.h parsed through the lua writer produces a LuaJIT FFI binding
        #        string with 'ffi.cdef[[' block and curl_global_init function declaration.
        # PATH:  backend.parse(curl.h) -> Header -> header_to_lua -> str
        # CHECK: isinstance+len>0; 'ffi.cdef[[' is the mandatory FFI block opener;
        #        'curl_global_init' proves the known curl function is emitted.
        # MUTATION: Omitting ffi.cdef[[ fails that assertion. Dropping curl_global_init fails name check.
        # ESCAPE: An ffi.cdef[[ block without declarations passes the opener but fails the name check.
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        _check_lua_write(header, "curl_global_init")

    def test_prompt_write(self, backend, curl_headers):
        # ESCAPE: test_prompt_write (TestCurl)
        # CLAIM: curl.h parsed through the prompt writer (compact) produces a string
        #        containing a FUNC line for curl_global_init.
        # PATH:  backend.parse(curl.h) -> Header -> PromptWriter(compact).write -> str
        # CHECK: isinstance+len>0; 'FUNC curl_global_init' is the compact writer's prefix for
        #        the known curl_global_init function.
        # MUTATION: A writer emitting 'fn curl_global_init' instead of 'FUNC curl_global_init' fails.
        # ESCAPE: A string with only 'FUNC curl_global_init' passes; structural prefix + name sufficient.
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        _check_prompt_write(header, "curl_global_init")

    def test_diff_write(self, backend, curl_headers):
        # ESCAPE: test_diff_write (TestCurl)
        # CLAIM: Diffing curl.h against itself produces JSON with summary.total==0 and entries==[].
        # PATH:  backend.parse(curl.h) -> Header -> DiffWriter(baseline=header).write(header)
        #        -> json.loads -> dict
        # CHECK: total==0 proves no spurious diffs for identity. entries==[] proves no entries emitted.
        # MUTATION: Spurious entries generation fails total==0. entries=None fails entries==[].
        # ESCAPE: '{"summary": {"total": 0}, "entries": null}' fails entries==[].
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        _check_diff_write(header)


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
        # Parse CFFI output into individual declaration lines so we can
        # assert that each target symbol appears as a complete declaration.
        # Full signature equality is omitted because SDL_Init/SDL_Quit
        # parameter types vary slightly across SDL2 versions.
        _check_cffi_write(header, "SDL_Init", "SDL_Quit")

    def test_json_write(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        # Verify specific known symbols appear in the JSON output.
        _check_json_write(header, "SDL_Init", "SDL_Quit")

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

    def test_ctypes_write(self, backend, sdl2_headers):
        # ESCAPE: test_ctypes_write (TestSDL2)
        # CLAIM: SDL.h parsed through ctypes writer produces a non-empty Python binding
        #        string containing the ctypes argtypes assignment for SDL_Init.
        # PATH:  backend.parse(SDL.h) -> Header -> header_to_ctypes -> str
        # CHECK: isinstance+len>0 proves non-empty; argtypes line proves the writer emits
        #        function bindings for at least one known SDL2 symbol.
        # MUTATION: A writer omitting SDL_Init argtypes fails the argtypes assertion.
        # ESCAPE: A non-empty writer that omits all argtypes lines fails the check.
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        _check_ctypes_write(header, "SDL_Init")

    def test_cython_write(self, backend, sdl2_headers):
        # ESCAPE: test_cython_write (TestSDL2)
        # CLAIM: SDL.h parsed through the cython writer produces a .pxd string
        #        containing 'cdef extern from' and the SDL_Init function signature.
        # PATH:  backend.parse(SDL.h) -> Header -> write_pxd -> str
        # CHECK: isinstance+len>0; 'cdef extern from' is mandatory; 'SDL_Init' proves
        #        at least one known SDL2 function is emitted.
        # MUTATION: Dropping the extern block header fails 'cdef extern from'.
        # ESCAPE: An extern-only header passes extern check but fails SDL_Init.
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        _check_cython_write(header, "SDL_Init")

    def test_lua_write(self, backend, sdl2_headers):
        # ESCAPE: test_lua_write (TestSDL2)
        # CLAIM: SDL.h parsed through the lua writer produces a LuaJIT FFI binding
        #        string with 'ffi.cdef[[' block and SDL_Init function declaration.
        # PATH:  backend.parse(SDL.h) -> Header -> header_to_lua -> str
        # CHECK: isinstance+len>0; 'ffi.cdef[[' is the mandatory FFI block opener;
        #        'SDL_Init' proves the known SDL2 function is emitted.
        # MUTATION: Omitting ffi.cdef[[ fails that assertion. Dropping SDL_Init fails the name check.
        # ESCAPE: An ffi.cdef[[ block without declarations passes the opener but fails the name check.
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        _check_lua_write(header, "SDL_Init")

    def test_prompt_write(self, backend, sdl2_headers):
        # ESCAPE: test_prompt_write (TestSDL2)
        # CLAIM: SDL.h parsed through the prompt writer (compact) produces a string
        #        containing a FUNC line for SDL_Init.
        # PATH:  backend.parse(SDL.h) -> Header -> PromptWriter(compact).write -> str
        # CHECK: isinstance+len>0; 'FUNC SDL_Init' is the compact writer's prefix for
        #        the known SDL_Init function.
        # MUTATION: A writer emitting 'fn SDL_Init' instead of 'FUNC SDL_Init' fails.
        # ESCAPE: A string with only 'FUNC SDL_Init' passes; structural prefix + name is sufficient.
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        _check_prompt_write(header, "SDL_Init")

    def test_diff_write(self, backend, sdl2_headers):
        # ESCAPE: test_diff_write (TestSDL2)
        # CLAIM: Diffing SDL.h against itself produces JSON with summary.total==0 and entries==[].
        # PATH:  backend.parse(SDL.h) -> Header -> DiffWriter(baseline=header).write(header)
        #        -> json.loads -> dict
        # CHECK: total==0 proves no spurious diffs for identity. entries==[] proves no entries emitted.
        # MUTATION: Spurious entries generation fails total==0. entries=None fails entries==[].
        # ESCAPE: '{"summary": {"total": 0}, "entries": null}' fails entries==[].
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        _check_diff_write(header)


# =============================================================================
# CPython
# =============================================================================


@pytest.mark.timeout(120)
class TestCPython:
    """Test CPython headers through the full pipeline.

    Python.h requires platform-specific pyconfig.h which is generated at
    build time, so parsing is expected to fail on most systems.
    """

    # `raises` is not decoration. Without it a non-strict xfail swallows EVERY
    # exception the body can raise -- a missing header download included, which
    # is how a failed download became an invisible xfail at exit 0, quieter than
    # the skip it replaced. `pytest.fail.Exception` derives from BaseException,
    # so naming `Exception` here excludes it and lets it through as a failure.
    @pytest.mark.xfail(
        raises=Exception,
        reason="Python.h requires platform-specific pyconfig.h",
    )
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


# =============================================================================
# Dear ImGui (C++)
# =============================================================================


@pytest.mark.timeout(120)
class TestDearImGui:
    """Test Dear ImGui C++ header through the full pipeline with C++ class semantics."""

    def test_parse(self, backend, imgui_header):
        _skip_if_unavailable(imgui_header, "Dear ImGui")
        header = _parse_header(
            backend,
            imgui_header,
            include_dirs=[str(imgui_header.parent)],
            extra_args=["-x", "c++", "-std=c++17"],
        )
        assert len(header.declarations) >= 50, (
            f"Expected >=50 declarations from imgui.h, got {len(header.declarations)}"
        )
        structs = {d.name: d for d in header.declarations if isinstance(d, Struct)}
        assert "ImVec2" in structs
        assert "ImVec4" in structs
        assert "ImColor" in structs

        # Verify C++ class semantics on ImVec2 / ImColor
        imvec2 = structs["ImVec2"]
        assert len(imvec2.constructors) >= 1
        assert any(c.name == "ImVec2" for c in imvec2.constructors)
        assert any(f.name == "x" and f.access == "public" for f in imvec2.fields)
        assert any(f.name == "y" and f.access == "public" for f in imvec2.fields)

        imcolor = structs["ImColor"]
        assert len(imcolor.constructors) >= 1
        assert len(imcolor.conversions) >= 1
        assert any(
            "ImVec4" in c.name or (hasattr(c.return_type, "name") and "ImVec4" in c.return_type.name)
            for c in imcolor.conversions
        )

    def test_json_write(self, backend, imgui_header):
        _skip_if_unavailable(imgui_header, "Dear ImGui")
        header = _parse_header(
            backend,
            imgui_header,
            include_dirs=[str(imgui_header.parent)],
            extra_args=["-x", "c++", "-std=c++17"],
        )
        _check_json_write(header, "ImVec2", "ImVec4", "ImColor")
        json_output = header_to_json(header)
        parsed = json.loads(json_output)
        imvec2 = next(d for d in parsed["declarations"] if d.get("name") == "ImVec2")
        assert "constructors" in imvec2
        assert len(imvec2["constructors"]) >= 1

    def test_cython_write(self, backend, imgui_header):
        _skip_if_unavailable(imgui_header, "Dear ImGui")
        header = _parse_header(
            backend,
            imgui_header,
            include_dirs=[str(imgui_header.parent)],
            extra_args=["-x", "c++", "-std=c++17"],
        )
        _check_cython_write(header, "ImVec2")

    def test_prompt_write(self, backend, imgui_header):
        _skip_if_unavailable(imgui_header, "Dear ImGui")
        header = _parse_header(
            backend,
            imgui_header,
            include_dirs=[str(imgui_header.parent)],
            extra_args=["-x", "c++", "-std=c++17"],
        )
        output = PromptWriter(verbosity="compact").write(header)
        assert isinstance(output, str) and len(output) > 0
        assert "STRUCT ImVec2" in output

    def test_diff_write(self, backend, imgui_header):
        _skip_if_unavailable(imgui_header, "Dear ImGui")
        header = _parse_header(
            backend,
            imgui_header,
            include_dirs=[str(imgui_header.parent)],
            extra_args=["-x", "c++", "-std=c++17"],
        )
        _check_diff_write(header)
