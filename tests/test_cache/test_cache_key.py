"""Tests for cache key computation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from headerkit._cache_key import (
    _IR_SCHEMA_VERSION,
    ParsedArgs,
    _relative_header_path,
    compute_ir_cache_key,
    compute_output_cache_key,
    parse_extra_args,
)
from headerkit._cache_store import read_ir_entry, write_ir_entry
from headerkit.ir import CType, Function, Header, Parameter


class TestParseExtraArgs:
    """Tests for extra_args parsing."""

    def test_empty(self) -> None:
        result = parse_extra_args(None)
        assert result == ParsedArgs(defines=[], includes=[], other_args=[])

    def test_defines(self) -> None:
        result = parse_extra_args(["-DFOO", "-DBAR=1"])
        assert result.defines == ["BAR=1", "FOO"]  # sorted

    def test_includes(self, tmp_path: Path) -> None:
        d = tmp_path / "inc"
        d.mkdir()
        result = parse_extra_args([f"-I{d}"])
        assert len(result.includes) == 1
        assert result.includes[0] == str(d.resolve())

    def test_include_with_space(self, tmp_path: Path) -> None:
        d = tmp_path / "inc"
        d.mkdir()
        result = parse_extra_args(["-I", str(d)])
        assert len(result.includes) == 1

    def test_other_args(self) -> None:
        result = parse_extra_args(["-std=c11", "-fno-exceptions"])
        assert result.other_args == ["-fno-exceptions", "-std=c11"]  # sorted

    def test_mixed(self, tmp_path: Path) -> None:
        d = tmp_path / "inc"
        d.mkdir()
        result = parse_extra_args(
            ["-DFOO", f"-I{d}", "-std=c11", "-DBAR"],
        )
        assert result.defines == ["BAR", "FOO"]
        assert len(result.includes) == 1
        assert result.other_args == ["-std=c11"]

    def test_explicit_defines_merged(self) -> None:
        result = parse_extra_args(["-DFOO"], defines=["BAR"])
        assert result.defines == ["BAR", "FOO"]  # sorted, merged

    def test_explicit_includes_merged(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d1.mkdir()
        d2 = tmp_path / "b"
        d2.mkdir()
        result = parse_extra_args([f"-I{d1}"], include_dirs=[str(d2)])
        assert len(result.includes) == 2

    def test_deduplication(self) -> None:
        result = parse_extra_args(["-DFOO", "-DFOO"])
        assert result.defines == ["FOO"]


class TestComputeIrCacheKey:
    """Tests for IR cache key computation."""

    def test_deterministic(self, tmp_path: Path) -> None:
        h = tmp_path / "test.h"
        h.write_text("int x;")
        pa = ParsedArgs(defines=[], includes=[], other_args=[])
        k1 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        k2 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        assert k1 == k2

    def test_different_content_different_key(self, tmp_path: Path) -> None:
        h1 = tmp_path / "a.h"
        h1.write_text("int x;")
        h2 = tmp_path / "b.h"
        h2.write_text("int y;")
        pa = ParsedArgs(defines=[], includes=[], other_args=[])
        k1 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h1, project_root=tmp_path, parsed_args=pa
        )
        k2 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h2, project_root=tmp_path, parsed_args=pa
        )
        assert k1 != k2

    def test_different_defines_different_key(self, tmp_path: Path) -> None:
        h = tmp_path / "test.h"
        h.write_text("int x;")
        pa1 = ParsedArgs(defines=["FOO"], includes=[], other_args=[])
        pa2 = ParsedArgs(defines=["BAR"], includes=[], other_args=[])
        k1 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa1
        )
        k2 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa2
        )
        assert k1 != k2

    def test_different_backend_different_key(self, tmp_path: Path) -> None:
        h = tmp_path / "test.h"
        h.write_text("int x;")
        pa = ParsedArgs(defines=[], includes=[], other_args=[])
        k1 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        k2 = compute_ir_cache_key(
            backend_name="other", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        assert k1 != k2

    def test_different_target_different_key(self, tmp_path: Path) -> None:
        """Different target triples produce different cache keys."""
        h = tmp_path / "test.h"
        h.write_text("int x;")
        pa = ParsedArgs(defines=[], includes=[], other_args=[])
        k1 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        k2 = compute_ir_cache_key(
            backend_name="libclang", target="aarch64-apple-darwin", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        assert k1 != k2

    def test_order_independent(self, tmp_path: Path) -> None:
        h = tmp_path / "test.h"
        h.write_text("int x;")
        pa1 = ParsedArgs(defines=["A", "B"], includes=[], other_args=[])
        pa2 = ParsedArgs(defines=["B", "A"], includes=[], other_args=[])
        # Both should be sorted internally
        k1 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa1
        )
        k2 = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa2
        )
        assert k1 == k2

    def test_returns_hex_string(self, tmp_path: Path) -> None:
        h = tmp_path / "test.h"
        h.write_text("int x;")
        pa = ParsedArgs(defines=[], includes=[], other_args=[])
        k = compute_ir_cache_key(
            backend_name="libclang", target="x86_64-pc-linux-gnu", header_path=h, project_root=tmp_path, parsed_args=pa
        )
        assert isinstance(k, str)
        assert len(k) == 64  # SHA-256 hex
        int(k, 16)  # must be valid hex

    def test_relative_path_used(self, tmp_path: Path) -> None:
        """Header path is hashed relative to project root, not absolute."""
        sub = tmp_path / "include"
        sub.mkdir()
        h = sub / "test.h"
        h.write_text("int x;")
        rel = _relative_header_path(h, tmp_path)
        assert rel == "include/test.h"


class TestComputeOutputCacheKey:
    """Tests for output cache key computation."""

    def test_different_writer_different_key(self) -> None:
        k1 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="cffi",
        )
        k2 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="ctypes",
        )
        assert k1 != k2

    def test_different_ir_key_different_key(self) -> None:
        k1 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="cffi",
        )
        k2 = compute_output_cache_key(
            ir_cache_key="def456",
            writer_name="cffi",
        )
        assert k1 != k2

    def test_writer_options_affect_key(self) -> None:
        k1 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="json",
            writer_options={"indent": 2},
        )
        k2 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="json",
            writer_options={"indent": 4},
        )
        assert k1 != k2

    def test_writer_version_affects_key(self) -> None:
        k1 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="cffi",
            writer_cache_version="1",
        )
        k2 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="cffi",
            writer_cache_version="2",
        )
        assert k1 != k2

    def test_deterministic(self) -> None:
        k1 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="cffi",
        )
        k2 = compute_output_cache_key(
            ir_cache_key="abc123",
            writer_name="cffi",
        )
        assert k1 == k2


class TestIrSchemaVersion:
    """Tests for IR schema version changes."""

    def test_ir_schema_version_is_3(self) -> None:
        """IR schema version should be '3' after the v0.14.0 bump."""
        assert _IR_SCHEMA_VERSION == "3"

    def test_old_schema_version_2_cache_miss(self, tmp_path: Path, caplog: logging.LogCaptureFixture) -> None:
        """A metadata.json with ir_schema_version '2' results in a cache miss and warning."""
        cache_dir = tmp_path / ".headerkit"
        entry_dir = cache_dir / "ir" / "libclang.old_entry"
        entry_dir.mkdir(parents=True)

        # Write a valid header IR file
        header = Header("test.h", [Function("foo", CType("void"), [Parameter("x", CType("int"))])])
        write_ir_entry(
            cache_dir=cache_dir,
            slug="libclang.old_entry",
            cache_key="abc123",
            header=header,
            backend_name="libclang",
            header_path="/path/to/test.h",
            target="x86_64-pc-linux-gnu",
            defines=[],
            includes=[],
            other_args=[],
        )

        # Overwrite metadata with old schema version
        meta_path = entry_dir / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["ir_schema_version"] = "2"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            result = read_ir_entry(cache_dir=cache_dir, slug="libclang.old_entry")

        assert result is None
        assert any("schema version mismatch" in record.message for record in caplog.records)
