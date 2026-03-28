"""Tests for index.json management."""

from __future__ import annotations

import json
from pathlib import Path

from headerkit._slug import (
    CacheIndex,
    load_index,
    lookup_slug,
    rebuild_index,
    register_slug,
    save_index,
)


class TestLoadIndex:
    """Tests for loading index.json."""

    def test_missing_file(self, tmp_path: Path) -> None:
        idx = load_index(tmp_path / "index.json")
        assert idx == {"version": 1, "entries": {}}

    def test_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "index.json"
        p.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": {"libclang.test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
                }
            )
        )
        idx = load_index(p)
        assert idx == {
            "version": 1,
            "entries": {"libclang.test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
        }

    def test_corrupt_file(self, tmp_path: Path) -> None:
        p = tmp_path / "index.json"
        p.write_text("not json")
        idx = load_index(p)
        assert idx == {"version": 1, "entries": {}}


class TestSaveIndex:
    """Tests for atomic index.json writes."""

    def test_basic_write(self, tmp_path: Path) -> None:
        p = tmp_path / "index.json"
        idx: CacheIndex = {
            "version": 1,
            "entries": {"test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
        }
        save_index(p, idx)
        assert p.exists()
        loaded = json.loads(p.read_text())
        assert loaded == {
            "entries": {"test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
            "version": 1,
        }

    def test_tmp_file_cleaned_up(self, tmp_path: Path) -> None:
        p = tmp_path / "index.json"
        idx: CacheIndex = {"version": 1, "entries": {}}
        save_index(p, idx)
        tmp_file = p.with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "index.json"
        idx1: CacheIndex = {
            "version": 1,
            "entries": {"a": {"cache_key": "1", "created": "2026-01-01T00:00:00Z"}},
        }
        save_index(p, idx1)
        idx2: CacheIndex = {
            "version": 1,
            "entries": {"b": {"cache_key": "2", "created": "2026-01-01T00:00:00Z"}},
        }
        save_index(p, idx2)
        loaded = json.loads(p.read_text())
        assert loaded == {
            "entries": {"b": {"cache_key": "2", "created": "2026-01-01T00:00:00Z"}},
            "version": 1,
        }


class TestLookupSlug:
    """Tests for finding slug by cache key."""

    def test_found(self) -> None:
        idx: CacheIndex = {
            "version": 1,
            "entries": {"test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
        }
        assert lookup_slug(idx, "abc") == "test"

    def test_not_found(self) -> None:
        idx: CacheIndex = {"version": 1, "entries": {}}
        assert lookup_slug(idx, "abc") is None


class TestRegisterSlug:
    """Tests for registering slugs with collision resolution."""

    def test_new_slug(self) -> None:
        idx: CacheIndex = {"version": 1, "entries": {}}
        actual = register_slug(idx, "libclang.test", "abc")
        assert actual == "libclang.test"
        assert idx["entries"]["libclang.test"] == {
            "cache_key": "abc",
            "created": idx["entries"]["libclang.test"]["created"],
        }
        assert idx["entries"]["libclang.test"]["cache_key"] == "abc"

    def test_same_key_same_slug(self) -> None:
        idx: CacheIndex = {
            "version": 1,
            "entries": {"libclang.test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
        }
        actual = register_slug(idx, "libclang.test", "abc")
        assert actual == "libclang.test"
        # Verify entry unchanged
        assert idx["entries"]["libclang.test"] == {
            "cache_key": "abc",
            "created": "2026-01-01T00:00:00Z",
        }

    def test_collision_appends_suffix(self) -> None:
        idx: CacheIndex = {
            "version": 1,
            "entries": {"libclang.test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}},
        }
        actual = register_slug(idx, "libclang.test", "different")
        assert actual == "libclang.test-2"
        assert idx["entries"]["libclang.test-2"]["cache_key"] == "different"

    def test_multiple_collisions(self) -> None:
        idx: CacheIndex = {
            "version": 1,
            "entries": {
                "libclang.test": {"cache_key": "aaa", "created": "2026-01-01T00:00:00Z"},
                "libclang.test-2": {"cache_key": "bbb", "created": "2026-01-01T00:00:00Z"},
            },
        }
        actual = register_slug(idx, "libclang.test", "ccc")
        assert actual == "libclang.test-3"
        assert idx["entries"]["libclang.test-3"]["cache_key"] == "ccc"


class TestRebuildIndex:
    """Tests for rebuilding index from metadata.json files."""

    def test_rebuild_from_metadata(self, tmp_path: Path) -> None:
        # Create a cache entry with metadata
        entry_dir = tmp_path / "libclang.test"
        entry_dir.mkdir()
        metadata = {
            "cache_key": "abc123",
            "created": "2026-01-01T00:00:00Z",
        }
        (entry_dir / "metadata.json").write_text(json.dumps(metadata))

        idx = rebuild_index(tmp_path)
        assert idx == {
            "version": 1,
            "entries": {
                "libclang.test": {"cache_key": "abc123", "created": "2026-01-01T00:00:00Z"},
            },
        }

    def test_rebuild_empty(self, tmp_path: Path) -> None:
        idx = rebuild_index(tmp_path)
        assert idx == {"version": 1, "entries": {}}

    def test_rebuild_skips_corrupt_metadata(self, tmp_path: Path) -> None:
        entry_dir = tmp_path / "libclang.bad"
        entry_dir.mkdir()
        (entry_dir / "metadata.json").write_text("not json")

        idx = rebuild_index(tmp_path)
        assert idx == {"version": 1, "entries": {}}
