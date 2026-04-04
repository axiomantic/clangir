"""Tests for headerkit store merge functionality."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_index(path: Path, entries: dict[str, dict[str, str]]) -> None:
    """Write an index.json file with given entries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "entries": entries}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_ir_entry(
    store: Path,
    slug: str,
    cache_key: str,
    created: str = "2026-01-01T00:00:00Z",
) -> None:
    """Create a minimal IR entry in a store directory."""
    entry_dir = store / "ir" / slug
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "ir.json").write_text('{"declarations": []}', encoding="utf-8")
    (entry_dir / "metadata.json").write_text(
        json.dumps({"cache_key": cache_key, "created": created}),
        encoding="utf-8",
    )
    # Update index
    index_path = store / "ir" / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"version": 1, "entries": {}}
    index["entries"][slug] = {"cache_key": cache_key, "created": created}
    _write_index(index_path, index["entries"])


def _write_output_entry(
    store: Path,
    writer: str,
    slug: str,
    cache_key: str,
    output_text: str = "# generated",
    created: str = "2026-01-01T00:00:00Z",
) -> None:
    """Create a minimal output entry in a store directory."""
    entry_dir = store / "output" / writer / slug
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "output.py").write_text(output_text, encoding="utf-8")
    (entry_dir / "metadata.json").write_text(
        json.dumps({"cache_key": cache_key, "created": created}),
        encoding="utf-8",
    )
    # Update index
    index_path = store / "output" / writer / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"version": 1, "entries": {}}
    index["entries"][slug] = {"cache_key": cache_key, "created": created}
    _write_index(index_path, index["entries"])


def _read_index(path: Path) -> dict[str, dict[str, str]]:
    """Read and return the entries dict from an index.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["entries"]


class TestStoreMergeTwoStoresWithDifferentPlatforms:
    """Merge two stores that have entries for different platforms."""

    def test_merge_different_ir_entries(self, tmp_path: Path) -> None:
        """IR entries from different platforms are combined."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-linux"
        store_b = tmp_path / "store-macos"
        target = tmp_path / "merged"

        _write_ir_entry(store_a, "libclang.test.x86_64-linux-gnu", "key-linux")
        _write_ir_entry(store_b, "libclang.test.aarch64-darwin", "key-macos")

        result = store_merge(sources=[store_a, store_b], target=target)

        assert result.new_entries == 2
        assert result.skipped_entries == 0
        assert result.overwritten_entries == 0
        assert result.errors == []

        # Both entries should exist
        assert (target / "ir" / "libclang.test.x86_64-linux-gnu" / "ir.json").exists()
        assert (target / "ir" / "libclang.test.aarch64-darwin" / "ir.json").exists()

        # Index should contain both entries
        entries = _read_index(target / "ir" / "index.json")
        assert "libclang.test.x86_64-linux-gnu" in entries
        assert "libclang.test.aarch64-darwin" in entries
        assert entries["libclang.test.x86_64-linux-gnu"]["cache_key"] == "key-linux"
        assert entries["libclang.test.aarch64-darwin"]["cache_key"] == "key-macos"

    def test_merge_different_output_entries(self, tmp_path: Path) -> None:
        """Output entries from different platforms are combined."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-linux"
        store_b = tmp_path / "store-macos"
        target = tmp_path / "merged"

        _write_output_entry(store_a, "cffi", "cffi.test.x86_64-linux-gnu", "out-linux", "# linux")
        _write_output_entry(store_b, "cffi", "cffi.test.aarch64-darwin", "out-macos", "# macos")

        result = store_merge(sources=[store_a, store_b], target=target)

        assert result.new_entries == 2
        assert result.skipped_entries == 0

        # Both output entries should exist
        assert (target / "output" / "cffi" / "cffi.test.x86_64-linux-gnu" / "output.py").exists()
        assert (target / "output" / "cffi" / "cffi.test.aarch64-darwin" / "output.py").exists()

        # Index should contain both
        entries = _read_index(target / "output" / "cffi" / "index.json")
        assert len(entries) == 2


class TestStoreMergeOverlappingEntries:
    """Merge stores with overlapping slugs."""

    def test_same_slug_same_cache_key_skips(self, tmp_path: Path) -> None:
        """Same slug and same cache_key is skipped (duplicate)."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        store_b = tmp_path / "store-b"
        target = tmp_path / "merged"

        _write_ir_entry(store_a, "libclang.test.x86_64-linux-gnu", "same-key")
        _write_ir_entry(store_b, "libclang.test.x86_64-linux-gnu", "same-key")

        # First merge
        result1 = store_merge(sources=[store_a], target=target)
        assert result1.new_entries == 1

        # Second merge with same content should skip
        result2 = store_merge(sources=[store_b], target=target)
        assert result2.new_entries == 0
        assert result2.skipped_entries == 1

    def test_same_slug_different_cache_key_overwrites(self, tmp_path: Path) -> None:
        """Same slug but different cache_key overwrites the entry."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        store_b = tmp_path / "store-b"
        target = tmp_path / "merged"

        _write_ir_entry(store_a, "libclang.test.x86_64-linux-gnu", "key-v1")
        _write_ir_entry(store_b, "libclang.test.x86_64-linux-gnu", "key-v2")

        result1 = store_merge(sources=[store_a], target=target)
        assert result1.new_entries == 1

        result2 = store_merge(sources=[store_b], target=target)
        assert result2.overwritten_entries == 1
        assert result2.new_entries == 0

        # Index should reflect the new cache_key
        entries = _read_index(target / "ir" / "index.json")
        assert entries["libclang.test.x86_64-linux-gnu"]["cache_key"] == "key-v2"

    def test_later_source_wins_on_conflict(self, tmp_path: Path) -> None:
        """When merging multiple sources at once, later sources win."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        store_b = tmp_path / "store-b"
        target = tmp_path / "merged"

        _write_ir_entry(store_a, "libclang.test", "key-a", created="2026-01-01T00:00:00Z")
        _write_ir_entry(store_b, "libclang.test", "key-b", created="2026-01-02T00:00:00Z")

        result = store_merge(sources=[store_a, store_b], target=target)

        # First source adds it, second overwrites
        assert result.new_entries == 1
        assert result.overwritten_entries == 1

        entries = _read_index(target / "ir" / "index.json")
        assert entries["libclang.test"]["cache_key"] == "key-b"


class TestStoreMergeIntoEmptyTarget:
    """Merge into a target that does not exist yet."""

    def test_merge_into_nonexistent_target(self, tmp_path: Path) -> None:
        """Target directory is created automatically."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        target = tmp_path / "nonexistent" / "target"

        _write_ir_entry(store_a, "libclang.test", "key-a")
        _write_output_entry(store_a, "cffi", "cffi.test", "out-key-a")

        result = store_merge(sources=[store_a], target=target)

        assert result.new_entries == 2
        assert (target / "ir" / "libclang.test" / "ir.json").exists()
        assert (target / "output" / "cffi" / "cffi.test" / "output.py").exists()

    def test_merge_into_empty_target(self, tmp_path: Path) -> None:
        """Empty target directory gets entries and indexes."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        target = tmp_path / "target"
        target.mkdir()

        _write_ir_entry(store_a, "slug1", "key1")

        result = store_merge(sources=[store_a], target=target)

        assert result.new_entries == 1
        entries = _read_index(target / "ir" / "index.json")
        assert "slug1" in entries


class TestStoreMergeMultipleWriters:
    """Merge stores with output entries for multiple writers."""

    def test_merge_multiple_writers(self, tmp_path: Path) -> None:
        """Entries for different writers are merged independently."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        target = tmp_path / "merged"

        _write_output_entry(store_a, "cffi", "cffi.test.linux", "cffi-linux-key", "# cffi linux")
        _write_output_entry(store_a, "ctypes", "ctypes.test.linux", "ctypes-linux-key", "# ctypes linux")
        _write_output_entry(store_a, "json", "json.test.linux", "json-linux-key", "# json linux")

        result = store_merge(sources=[store_a], target=target)

        assert result.new_entries == 3

        # Each writer should have its own index
        cffi_entries = _read_index(target / "output" / "cffi" / "index.json")
        assert "cffi.test.linux" in cffi_entries

        ctypes_entries = _read_index(target / "output" / "ctypes" / "index.json")
        assert "ctypes.test.linux" in ctypes_entries

        json_entries = _read_index(target / "output" / "json" / "index.json")
        assert "json.test.linux" in json_entries

    def test_merge_from_multiple_sources_multiple_writers(self, tmp_path: Path) -> None:
        """Multiple sources with different writers are all merged."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-linux"
        store_b = tmp_path / "store-macos"
        target = tmp_path / "merged"

        _write_output_entry(store_a, "cffi", "cffi.test.linux", "cffi-linux-key")
        _write_output_entry(store_b, "cffi", "cffi.test.macos", "cffi-macos-key")
        _write_output_entry(store_a, "ctypes", "ctypes.test.linux", "ctypes-linux-key")
        _write_output_entry(store_b, "ctypes", "ctypes.test.macos", "ctypes-macos-key")

        result = store_merge(sources=[store_a, store_b], target=target)

        assert result.new_entries == 4
        cffi_entries = _read_index(target / "output" / "cffi" / "index.json")
        assert len(cffi_entries) == 2
        ctypes_entries = _read_index(target / "output" / "ctypes" / "index.json")
        assert len(ctypes_entries) == 2


class TestStoreMergeErrorHandling:
    """Error handling in store merge."""

    def test_nonexistent_source_raises(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised for nonexistent source directory."""
        from headerkit._store_merge import store_merge

        target = tmp_path / "target"
        with pytest.raises(FileNotFoundError, match="Source store directory not found"):
            store_merge(sources=[tmp_path / "nonexistent"], target=target)

    def test_source_without_ir_or_output(self, tmp_path: Path) -> None:
        """Source with no ir/ or output/ directories is a no-op."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        store_a.mkdir()
        target = tmp_path / "target"

        result = store_merge(sources=[store_a], target=target)
        assert result.new_entries == 0
        assert result.skipped_entries == 0


class TestStoreMergeMixedIrAndOutput:
    """Merge stores that have both IR and output entries."""

    def test_merge_ir_and_output_together(self, tmp_path: Path) -> None:
        """Both IR and output entries are merged in one operation."""
        from headerkit._store_merge import store_merge

        store_a = tmp_path / "store-a"
        target = tmp_path / "merged"

        _write_ir_entry(store_a, "libclang.test.linux", "ir-key")
        _write_output_entry(store_a, "cffi", "cffi.test.linux", "out-key")

        result = store_merge(sources=[store_a], target=target)

        assert result.new_entries == 2
        assert (target / "ir" / "libclang.test.linux" / "ir.json").exists()
        assert (target / "output" / "cffi" / "cffi.test.linux" / "output.py").exists()

        ir_entries = _read_index(target / "ir" / "index.json")
        assert "libclang.test.linux" in ir_entries
        cffi_entries = _read_index(target / "output" / "cffi" / "index.json")
        assert "cffi.test.linux" in cffi_entries


class TestStoreMergeCli:
    """Tests for headerkit store merge CLI."""

    def test_cli_dispatch(self) -> None:
        """'headerkit store merge' dispatches to store_merge_main."""
        with patch("headerkit._cache_cli.store_merge_main", return_value=0) as mock_merge:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "store", "merge", "src/", "-o", "dst/"],
            ):
                from headerkit._cli import main

                main()
            mock_merge.assert_called_once_with(["src/", "-o", "dst/"])

    def test_cli_unknown_store_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Unknown store subcommand prints error."""
        with patch.object(sys, "argv", ["headerkit", "store", "unknown"]):
            from headerkit._cli import main

            exit_code = main()
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "unknown subcommand" in captured.err
        assert "merge" in captured.err

    def test_cli_merge_functional(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Full CLI merge writes output to stdout."""
        from headerkit._cache_cli import store_merge_main

        store_a = tmp_path / "store-a"
        target = tmp_path / "target"

        _write_ir_entry(store_a, "libclang.test", "key-a")

        exit_code = store_merge_main([str(store_a), "-o", str(target)])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Merged into {target}:
              New entries: 1
              Skipped (duplicate): 0
              Overwritten (conflict): 0
        """)

    def test_cli_merge_nonexistent_source(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """CLI reports error for nonexistent source."""
        from headerkit._cache_cli import store_merge_main

        exit_code = store_merge_main([str(tmp_path / "nope"), "-o", str(tmp_path / "target")])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Source store directory not found" in captured.err

    def test_cli_merge_multiple_sources(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """CLI accepts multiple source directories."""
        from headerkit._cache_cli import store_merge_main

        store_a = tmp_path / "store-a"
        store_b = tmp_path / "store-b"
        target = tmp_path / "target"

        _write_ir_entry(store_a, "slug-a", "key-a")
        _write_ir_entry(store_b, "slug-b", "key-b")

        exit_code = store_merge_main([str(store_a), str(store_b), "-o", str(target)])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert "New entries: 2" in captured.out


class TestStoreMergePublicApi:
    """Tests for the public API surface."""

    def test_store_merge_in_public_api(self) -> None:
        """store_merge is importable from headerkit."""
        from headerkit import MergeResult, store_merge

        assert callable(store_merge)
        assert MergeResult is not None

    def test_merge_result_defaults(self) -> None:
        """MergeResult has correct defaults."""
        from headerkit._store_merge import MergeResult

        r = MergeResult()
        assert r.new_entries == 0
        assert r.skipped_entries == 0
        assert r.overwritten_entries == 0
        assert r.errors == []
