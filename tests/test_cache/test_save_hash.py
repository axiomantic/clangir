"""Tests for headerkit.cache.save_hash."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from headerkit.cache import _sidecar_path, compute_hash, save_hash
from headerkit.writers import get_writer

_FAKE_VERSION = "0.8.4"


def _patch_version():  # noqa: ANN202
    """Patch importlib.metadata.version in headerkit.cache to return _FAKE_VERSION."""
    return patch("headerkit.cache.importlib.metadata.version", return_value=_FAKE_VERSION)


def _compute_hash_with_fake_version(
    *,
    header_paths: list[Path],
    writer_name: str,
    writer_options: dict[str, str] | None = None,
) -> str:
    """Compute hash under the fake version mock (version affects the hash)."""
    with _patch_version():
        return compute_hash(
            header_paths=header_paths,
            writer_name=writer_name,
            writer_options=writer_options,
        )


class TestSaveHashEmbedded:
    """Tests for embedded hash storage (writers with hash_comment_format)."""

    def test_cffi_embeds_hash_in_output(self, sample_header: Path, sample_output: Path) -> None:
        """save_hash with cffi writer prepends hash comment to output file."""
        writer = get_writer("cffi")
        with _patch_version():
            result_path = save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer=writer,
            )
        assert result_path == sample_output

        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        content = sample_output.read_text(encoding="utf-8")
        expected = textwrap.dedent(f"""\
            # [headerkit-cache]
            # hash = "{expected_hash}"
            # version = "{_FAKE_VERSION}"
            # writer = "cffi"

            # generated bindings
        """)
        assert content == expected

    def test_lua_embeds_hash_with_lua_comments(self, sample_header: Path, tmp_path: Path) -> None:
        """save_hash with lua writer uses -- comment prefix."""
        output = tmp_path / "bindings.lua"
        output.write_text("local ffi = require('ffi')\n", encoding="utf-8")

        writer = get_writer("lua")
        with _patch_version():
            result_path = save_hash(
                output_path=output,
                header_paths=[sample_header],
                writer_name="lua",
                writer=writer,
            )
        assert result_path == output

        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="lua",
        )
        content = output.read_text(encoding="utf-8")
        expected = textwrap.dedent(f"""\
            -- [headerkit-cache]
            -- hash = "{expected_hash}"
            -- version = "{_FAKE_VERSION}"
            -- writer = "lua"

            local ffi = require('ffi')
        """)
        assert content == expected

    def test_embedded_preserves_original_content_exactly(self, sample_header: Path, sample_output: Path) -> None:
        """Original file content is fully preserved after the hash block."""
        original = sample_output.read_text(encoding="utf-8")
        writer = get_writer("cffi")
        save_hash(
            output_path=sample_output,
            header_paths=[sample_header],
            writer_name="cffi",
            writer=writer,
        )
        content = sample_output.read_text(encoding="utf-8")
        # Content after the blank separator line should be exactly the original
        parts = content.split("\n\n", 1)
        assert len(parts) == 2
        assert parts[1] == original

    def test_embedded_toml_is_parseable(self, sample_header: Path, sample_output: Path) -> None:
        """The embedded TOML block can be parsed back to valid TOML."""
        writer = get_writer("cffi")
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        with _patch_version():
            save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer=writer,
            )
        content = sample_output.read_text(encoding="utf-8")
        # Extract the comment block (everything before the blank line)
        comment_block = content.split("\n\n", 1)[0]
        # Strip "# " prefix from each line to get raw TOML
        toml_lines = []
        for line in comment_block.splitlines():
            toml_lines.append(line.removeprefix("# "))
        raw_toml = "\n".join(toml_lines) + "\n"
        parsed = tomllib.loads(raw_toml)
        assert parsed == {
            "headerkit-cache": {
                "hash": expected_hash,
                "version": _FAKE_VERSION,
                "writer": "cffi",
            }
        }

    def test_embedded_hash_matches_compute_hash(self, sample_header: Path, sample_output: Path) -> None:
        """The embedded hash matches what compute_hash would return."""
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        writer = get_writer("cffi")
        with _patch_version():
            save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer=writer,
            )
        content = sample_output.read_text(encoding="utf-8")
        lines = content.splitlines()
        hash_line = lines[1]
        stored_hash = hash_line.removeprefix('# hash = "').removesuffix('"')
        assert stored_hash == expected_hash

    def test_embedded_with_writer_options(self, sample_header: Path, sample_output: Path) -> None:
        """save_hash includes writer_options in the hash computation."""
        options = {"exclude": "__.*"}
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options=options,
        )
        writer = get_writer("cffi")
        with _patch_version():
            save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer_options=options,
                writer=writer,
            )
        content = sample_output.read_text(encoding="utf-8")
        lines = content.splitlines()
        stored_hash = lines[1].removeprefix('# hash = "').removesuffix('"')
        assert stored_hash == expected_hash


class TestSaveHashSidecar:
    """Tests for sidecar .hkcache storage."""

    def test_json_writer_uses_sidecar(self, sample_header: Path, tmp_path: Path) -> None:
        """save_hash with json writer creates .hkcache sidecar file."""
        output = tmp_path / "bindings.json"
        output.write_text('{"declarations": []}\n', encoding="utf-8")

        writer = get_writer("json")
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="json",
        )
        with _patch_version():
            result_path = save_hash(
                output_path=output,
                header_paths=[sample_header],
                writer_name="json",
                writer=writer,
            )

        sidecar = _sidecar_path(output)
        assert result_path == sidecar
        assert sidecar.exists() is True

        sidecar_content = sidecar.read_text(encoding="utf-8")
        parsed = tomllib.loads(sidecar_content)
        assert parsed == {
            "headerkit-cache": {
                "hash": expected_hash,
                "version": _FAKE_VERSION,
                "writer": "json",
            }
        }

        # Original output file should NOT be modified
        assert output.read_text(encoding="utf-8") == '{"declarations": []}\n'

    def test_no_writer_falls_back_to_sidecar(self, sample_header: Path, sample_output: Path) -> None:
        """save_hash with writer=None always uses sidecar storage."""
        original_content = sample_output.read_text(encoding="utf-8")
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        with _patch_version():
            result_path = save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer=None,
            )
        sidecar = _sidecar_path(sample_output)
        assert result_path == sidecar
        assert sidecar.exists() is True

        # Output file should NOT be modified (even though cffi supports embedding)
        assert sample_output.read_text(encoding="utf-8") == original_content

        # Sidecar should contain valid TOML with exact values
        parsed = tomllib.loads(sidecar.read_text(encoding="utf-8"))
        assert parsed == {
            "headerkit-cache": {
                "hash": expected_hash,
                "version": _FAKE_VERSION,
                "writer": "cffi",
            }
        }

    def test_writer_without_hash_comment_format_uses_sidecar(self, sample_header: Path, tmp_path: Path) -> None:
        """Writer lacking hash_comment_format falls back to sidecar."""
        output = tmp_path / "prompt.txt"
        output.write_text("some prompt output\n", encoding="utf-8")

        writer = get_writer("prompt")
        result_path = save_hash(
            output_path=output,
            header_paths=[sample_header],
            writer_name="prompt",
            writer=writer,
        )
        sidecar = _sidecar_path(output)
        assert result_path == sidecar
        assert sidecar.exists() is True

        # Output file should NOT be modified
        assert output.read_text(encoding="utf-8") == "some prompt output\n"

    def test_sidecar_hash_matches_compute_hash(self, sample_header: Path, sample_output: Path) -> None:
        """The sidecar hash matches what compute_hash would return."""
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        with _patch_version():
            result_path = save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer=None,
            )
        parsed = tomllib.loads(result_path.read_text(encoding="utf-8"))
        assert parsed["headerkit-cache"]["hash"] == expected_hash

    def test_sidecar_toml_structure(self, sample_header: Path, sample_output: Path) -> None:
        """Sidecar file contains properly structured TOML."""
        expected_hash = _compute_hash_with_fake_version(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        with _patch_version():
            result_path = save_hash(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer=None,
            )
        raw = result_path.read_text(encoding="utf-8")
        expected = textwrap.dedent(f"""\
            [headerkit-cache]
            hash = "{expected_hash}"
            version = "{_FAKE_VERSION}"
            writer = "cffi"
        """)
        assert raw == expected


class TestSaveHashErrors:
    """Error cases for save_hash."""

    def test_missing_output_raises(self, sample_header: Path, tmp_path: Path) -> None:
        """save_hash raises FileNotFoundError if output file is missing."""
        missing = tmp_path / "nonexistent.py"
        with pytest.raises(FileNotFoundError, match="Output not found"):
            save_hash(
                output_path=missing,
                header_paths=[sample_header],
                writer_name="cffi",
            )

    def test_empty_headers_raises(self, sample_output: Path) -> None:
        """save_hash raises ValueError for empty header_paths."""
        with pytest.raises(ValueError, match="header_paths must not be empty"):
            save_hash(
                output_path=sample_output,
                header_paths=[],
                writer_name="cffi",
            )

    def test_missing_header_raises(self, sample_output: Path, tmp_path: Path) -> None:
        """save_hash raises FileNotFoundError for missing header file."""
        missing_header = tmp_path / "nonexistent.h"
        with pytest.raises(FileNotFoundError, match="Header not found"):
            save_hash(
                output_path=sample_output,
                header_paths=[missing_header],
                writer_name="cffi",
            )
