"""Tests for headerkit.cache.compute_hash and internal _compute_hash_digest."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from headerkit.cache import compute_hash


class TestComputeHashDeterminism:
    """Hash must be deterministic for identical inputs."""

    def test_same_inputs_same_hash(self, sample_header: Path) -> None:
        """Calling compute_hash twice with identical inputs returns the same digest."""
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        assert h1 == h2

    def test_hash_is_64_char_hex(self, sample_header: Path) -> None:
        """SHA-256 hex digest is exactly 64 characters of hex."""
        h = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestComputeHashSensitivity:
    """Hash must change when any input changes."""

    def test_changes_when_header_content_changes(self, tmp_path: Path) -> None:
        """Modifying header content produces a different hash."""
        h = tmp_path / "test.h"
        h.write_text("int add(int a, int b);\n", encoding="utf-8")
        hash1 = compute_hash(header_paths=[h], writer_name="cffi")

        h.write_text("int sub(int a, int b);\n", encoding="utf-8")
        hash2 = compute_hash(header_paths=[h], writer_name="cffi")

        assert hash1 != hash2

    def test_changes_when_writer_name_changes(self, sample_header: Path) -> None:
        """Different writer name produces a different hash."""
        h1 = compute_hash(header_paths=[sample_header], writer_name="cffi")
        h2 = compute_hash(header_paths=[sample_header], writer_name="ctypes")
        assert h1 != h2

    def test_changes_when_writer_options_change(self, sample_header: Path) -> None:
        """Different writer options produce a different hash."""
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={"exclude": "foo"},
        )
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={"exclude": "bar"},
        )
        assert h1 != h2

    def test_changes_when_version_changes(self, sample_header: Path) -> None:
        """Different headerkit version produces a different hash."""
        with patch("headerkit.cache.importlib.metadata.version", return_value="0.8.4"):
            h1 = compute_hash(header_paths=[sample_header], writer_name="cffi")
        with patch("headerkit.cache.importlib.metadata.version", return_value="0.9.0"):
            h2 = compute_hash(header_paths=[sample_header], writer_name="cffi")
        assert h1 != h2

    def test_changes_when_extra_inputs_change(self, sample_header: Path, tmp_path: Path) -> None:
        """Different extra input content produces a different hash."""
        extra = tmp_path / "build.cfg"
        extra.write_text("flags=release\n", encoding="utf-8")
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            extra_inputs=[extra],
        )

        extra.write_text("flags=debug\n", encoding="utf-8")
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            extra_inputs=[extra],
        )
        assert h1 != h2


class TestComputeHashNormalization:
    """Hash must be stable across line-ending and BOM differences."""

    def test_crlf_and_lf_produce_same_hash(self, tmp_path: Path) -> None:
        """Files with CRLF and LF line endings produce the same hash."""
        h = tmp_path / "test.h"
        h.write_bytes(b"int add(int a, int b);\nint sub(int a, int b);\n")
        h_lf = compute_hash(header_paths=[h], writer_name="cffi")

        h.write_bytes(b"int add(int a, int b);\r\nint sub(int a, int b);\r\n")
        h_crlf = compute_hash(header_paths=[h], writer_name="cffi")
        assert h_lf == h_crlf

    def test_cr_only_normalized(self, tmp_path: Path) -> None:
        """Files with CR-only line endings are normalized to LF."""
        h = tmp_path / "test.h"
        h.write_bytes(b"int x;\nint y;\n")
        h_lf = compute_hash(header_paths=[h], writer_name="cffi")

        h.write_bytes(b"int x;\rint y;\r")
        h_cr = compute_hash(header_paths=[h], writer_name="cffi")
        assert h_lf == h_cr

    def test_bom_stripped(self, tmp_path: Path) -> None:
        """UTF-8 BOM is stripped before hashing."""
        h = tmp_path / "test.h"
        h.write_bytes(b"int x;\n")
        h_no = compute_hash(header_paths=[h], writer_name="cffi")

        h.write_bytes(b"\xef\xbb\xbfint x;\n")
        h_bom = compute_hash(header_paths=[h], writer_name="cffi")
        assert h_no == h_bom


class TestComputeHashPathOrdering:
    """Path sorting must produce a stable hash regardless of input order."""

    def test_path_order_independent(self, sample_header: Path, second_header: Path) -> None:
        """Hash is the same regardless of header_paths order."""
        h1 = compute_hash(
            header_paths=[sample_header, second_header],
            writer_name="cffi",
        )
        h2 = compute_hash(
            header_paths=[second_header, sample_header],
            writer_name="cffi",
        )
        assert h1 == h2


class TestComputeHashErrors:
    """Error cases must raise clear exceptions."""

    def test_empty_header_paths_raises(self) -> None:
        """Empty header_paths raises ValueError."""
        with pytest.raises(ValueError, match="header_paths must not be empty"):
            compute_hash(header_paths=[], writer_name="cffi")

    def test_missing_header_raises(self, tmp_path: Path) -> None:
        """Non-existent header file raises FileNotFoundError."""
        missing = tmp_path / "nonexistent.h"
        with pytest.raises(FileNotFoundError, match="Header not found"):
            compute_hash(header_paths=[missing], writer_name="cffi")

    def test_missing_extra_input_raises(self, sample_header: Path, tmp_path: Path) -> None:
        """Non-existent extra input raises FileNotFoundError."""
        missing = tmp_path / "missing.cfg"
        with pytest.raises(FileNotFoundError, match="Extra input not found"):
            compute_hash(
                header_paths=[sample_header],
                writer_name="cffi",
                extra_inputs=[missing],
            )


class TestComputeHashWriterOptions:
    """Writer options handling edge cases."""

    def test_none_options_same_as_empty(self, sample_header: Path) -> None:
        """writer_options=None produces the same hash as writer_options={}."""
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options=None,
        )
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={},
        )
        assert h1 == h2

    def test_options_sorted_by_key(self, sample_header: Path) -> None:
        """Options are sorted by key, so insertion order does not matter."""
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={"b": "2", "a": "1"},
        )
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={"a": "1", "b": "2"},
        )
        assert h1 == h2
