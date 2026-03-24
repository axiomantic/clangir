"""Tests for TOML metadata building and parsing internals."""

from __future__ import annotations

import textwrap
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from headerkit.cache import (
    _build_metadata_toml,
    _parse_embedded_toml,
    _sidecar_path,
)


class TestBuildMetadataToml:
    """Tests for _build_metadata_toml."""

    def test_produces_valid_toml_with_correct_structure(self) -> None:
        """Output parses as valid TOML with exact keys and values."""
        toml_str = _build_metadata_toml(
            hash_digest="abcd1234" * 8,
            writer_name="cffi",
            headerkit_version="0.8.4",
        )
        expected = textwrap.dedent("""\
            [headerkit-cache]
            hash = "abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234"
            version = "0.8.4"
            writer = "cffi"
        """)
        assert toml_str == expected

    def test_parses_as_valid_toml(self) -> None:
        """Output round-trips through tomllib.loads without error."""
        toml_str = _build_metadata_toml(
            hash_digest="a" * 64,
            writer_name="lua",
            headerkit_version="1.0.0",
        )
        parsed = tomllib.loads(toml_str)
        assert parsed == {
            "headerkit-cache": {
                "hash": "a" * 64,
                "version": "1.0.0",
                "writer": "lua",
            }
        }


class TestParseEmbeddedToml:
    """Tests for _parse_embedded_toml."""

    def test_parses_python_comment_style(self) -> None:
        """Extracts TOML from # comment lines with exact parsed dict."""
        content = textwrap.dedent("""\
            # [headerkit-cache]
            # hash = "abcd1234"
            # version = "0.8.4"
            # writer = "cffi"
            # generated = "2026-03-23T14:30:00Z"

            # generated bindings
            import ctypes
        """)
        result = _parse_embedded_toml(content)
        assert result == {
            "headerkit-cache": {
                "hash": "abcd1234",
                "version": "0.8.4",
                "writer": "cffi",
                "generated": "2026-03-23T14:30:00Z",
            }
        }

    def test_parses_lua_comment_style(self) -> None:
        """Extracts TOML from -- comment lines with exact parsed dict."""
        content = textwrap.dedent("""\
            -- [headerkit-cache]
            -- hash = "abcd1234"
            -- version = "0.8.4"
            -- writer = "lua"
            -- generated = "2026-03-23T14:30:00Z"

            local ffi = require("ffi")
        """)
        result = _parse_embedded_toml(content)
        assert result == {
            "headerkit-cache": {
                "hash": "abcd1234",
                "version": "0.8.4",
                "writer": "lua",
                "generated": "2026-03-23T14:30:00Z",
            }
        }

    def test_returns_none_for_no_marker(self) -> None:
        """Returns None when file has no [headerkit-cache] marker."""
        content = "# just a regular python file\nimport os\n"
        result = _parse_embedded_toml(content)
        assert result is None

    def test_returns_none_for_corrupted_toml(self) -> None:
        """Returns None for unparseable TOML (does not raise)."""
        content = textwrap.dedent("""\
            # [headerkit-cache]
            # hash = not a valid toml value !!!
            # ???
        """)
        result = _parse_embedded_toml(content)
        assert result is None

    def test_stops_at_blank_line(self) -> None:
        """Parser stops reading TOML at the first blank/non-comment line."""
        content = textwrap.dedent("""\
            # [headerkit-cache]
            # hash = "abc123"
            # version = "0.8.4"

            # This is NOT part of the TOML
            # writer = "should_not_appear"
        """)
        result = _parse_embedded_toml(content)
        assert result == {
            "headerkit-cache": {
                "hash": "abc123",
                "version": "0.8.4",
            }
        }

    def test_stops_at_non_comment_line(self) -> None:
        """Parser stops reading TOML at a non-comment line."""
        content = textwrap.dedent("""\
            # [headerkit-cache]
            # hash = "def456"
            import ctypes
            # writer = "should_not_appear"
        """)
        result = _parse_embedded_toml(content)
        assert result == {
            "headerkit-cache": {
                "hash": "def456",
            }
        }

    def test_empty_string_returns_none(self) -> None:
        """Empty string input returns None."""
        result = _parse_embedded_toml("")
        assert result is None

    def test_marker_not_at_line_start_still_detected(self) -> None:
        """Marker with leading whitespace is still detected."""
        content = textwrap.dedent("""\
              # [headerkit-cache]
              # hash = "indented"
              # writer = "cffi"

            code here
        """)
        result = _parse_embedded_toml(content)
        assert result == {
            "headerkit-cache": {
                "hash": "indented",
                "writer": "cffi",
            }
        }


class TestSidecarPath:
    """Tests for _sidecar_path."""

    def test_appends_hkcache_extension(self) -> None:
        """Sidecar path is the output path with .hkcache appended."""
        assert _sidecar_path(Path("/foo/bar/bindings.json")) == Path("/foo/bar/bindings.json.hkcache")

    def test_preserves_original_extension(self) -> None:
        """The .hkcache is appended, not replacing the original extension."""
        result = _sidecar_path(Path("output.py"))
        assert result == Path("output.py.hkcache")

    def test_works_with_no_extension(self) -> None:
        """Works correctly when the output has no extension."""
        result = _sidecar_path(Path("/tmp/bindings"))
        assert result == Path("/tmp/bindings.hkcache")

    def test_works_with_multiple_extensions(self) -> None:
        """Works correctly when the output has multiple dot-separated parts."""
        result = _sidecar_path(Path("/tmp/output.tar.gz"))
        assert result == Path("/tmp/output.tar.gz.hkcache")
