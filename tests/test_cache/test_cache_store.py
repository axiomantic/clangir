"""Tests for cache store operations."""

from __future__ import annotations

import json
from pathlib import Path

from headerkit._cache_key import _IR_SCHEMA_VERSION
from headerkit._cache_store import (
    find_cache_dir,
    read_ir_entry,
    read_output_entry,
    write_ir_entry,
    write_output_entry,
)
from headerkit.ir import CType, Function, Header, Parameter


class TestFindCacheDir:
    """Tests for cache directory resolution."""

    def test_creates_headerkit_at_git_root(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = find_cache_dir(tmp_path)
        assert result == tmp_path / ".headerkit"

    def test_walks_upward(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "lib"
        subdir.mkdir(parents=True)
        result = find_cache_dir(subdir)
        assert result == tmp_path / ".headerkit"

    def test_find_cache_dir_creates_headerkit_at_git_root(self, tmp_path: Path) -> None:
        """Verify .headerkit/ is created at the .git root, not at intermediate directories."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        result = find_cache_dir(subdir)
        assert result == tmp_path / ".headerkit"
        assert (tmp_path / ".headerkit").is_dir()
        # Should NOT create .headerkit/ at intermediate directories
        assert not (subdir / ".headerkit").exists()
        assert not (tmp_path / "src" / ".headerkit").exists()

    def test_find_cache_dir_no_project_root_returns_none(self, tmp_path: Path) -> None:
        """Verify returns None when no .git found."""
        # No .git directory anywhere in the hierarchy
        subdir = tmp_path / "isolated" / "deep"
        subdir.mkdir(parents=True)
        result = find_cache_dir(subdir)
        assert result is None

    def test_find_cache_dir_no_walkup_for_existing(self, tmp_path: Path) -> None:
        """Verify that an existing .headerkit/ at an intermediate directory is NOT found."""
        # Create .git at top level
        (tmp_path / ".git").mkdir()
        # Create .headerkit/ at an intermediate directory (not at git root)
        intermediate = tmp_path / "sub"
        intermediate.mkdir()
        (intermediate / ".headerkit").mkdir()
        # Start from a deeper directory
        deep = intermediate / "deep"
        deep.mkdir()
        result = find_cache_dir(deep)
        # Should find .headerkit at git root, not at intermediate
        assert result == tmp_path / ".headerkit"


class TestWriteReadIrEntry:
    """Tests for IR cache entry write/read."""

    def test_round_trip(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".headerkit"
        cache_dir.mkdir()
        (cache_dir / "ir").mkdir()

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [Parameter("x", CType("int"))])],
        )

        write_ir_entry(
            cache_dir=cache_dir,
            slug="libclang.test",
            cache_key="abc123",
            header=header,
            backend_name="libclang",
            header_path="/path/to/test.h",
            target="x86_64-pc-linux-gnu",
            defines=["FOO"],
            includes=["/usr/include"],
            other_args=["-std=c11"],
        )

        result = read_ir_entry(cache_dir=cache_dir, slug="libclang.test")
        assert result is not None
        assert result == header

    def test_missing_entry_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".headerkit"
        cache_dir.mkdir()
        (cache_dir / "ir").mkdir()

        result = read_ir_entry(cache_dir=cache_dir, slug="nonexistent")
        assert result is None

    def test_writes_metadata(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".headerkit"
        cache_dir.mkdir()
        (cache_dir / "ir").mkdir()

        header = Header("test.h", [])
        write_ir_entry(
            cache_dir=cache_dir,
            slug="libclang.test",
            cache_key="abc123",
            header=header,
            backend_name="libclang",
            header_path="/path/to/test.h",
            target="x86_64-pc-linux-gnu",
            defines=[],
            includes=[],
            other_args=[],
        )

        metadata_path = cache_dir / "ir" / "libclang.test" / "metadata.json"
        assert metadata_path.exists()
        meta = json.loads(metadata_path.read_text())
        from headerkit._cache_key import _IR_SCHEMA_VERSION

        assert meta["cache_key"] == "abc123"
        assert meta["backend_name"] == "libclang"
        assert meta["ir_schema_version"] == _IR_SCHEMA_VERSION
        assert isinstance(meta["headerkit_version"], str)
        assert isinstance(meta["created"], str)

    def test_corrupt_ir_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".headerkit"
        (cache_dir / "ir" / "libclang.bad").mkdir(parents=True)
        (cache_dir / "ir" / "libclang.bad" / "ir.json").write_text("not json")
        (cache_dir / "ir" / "libclang.bad" / "metadata.json").write_text(
            json.dumps({"ir_schema_version": _IR_SCHEMA_VERSION, "cache_key": "x", "created": "t"})
        )

        result = read_ir_entry(cache_dir=cache_dir, slug="libclang.bad")
        assert result is None


class TestWriteReadOutputEntry:
    """Tests for output cache entry write/read."""

    def test_round_trip(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".headerkit"
        cache_dir.mkdir()
        (cache_dir / "output").mkdir()

        output_text = "# generated cffi output\nffi.cdef('int foo(int x);')\n"

        write_output_entry(
            cache_dir=cache_dir,
            writer_name="cffi",
            slug="libclang.test",
            cache_key="out_abc",
            ir_cache_key="ir_abc",
            output=output_text,
            writer_options={},
            writer_cache_version=None,
            output_extension=".py",
        )

        result = read_output_entry(
            cache_dir=cache_dir,
            writer_name="cffi",
            slug="libclang.test",
            output_extension=".py",
        )
        assert result == output_text

    def test_missing_entry_returns_none(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".headerkit"
        cache_dir.mkdir()
        (cache_dir / "output").mkdir()

        result = read_output_entry(
            cache_dir=cache_dir,
            writer_name="cffi",
            slug="nonexistent",
            output_extension=".py",
        )
        assert result is None
