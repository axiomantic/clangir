"""Tests for headerkit._resolve — header path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from headerkit._resolve import resolve_headers


def _make_tree(tmp_path: Path, files: list[str]) -> None:
    """Create a directory tree with .h files and a .git marker."""
    (tmp_path / ".git").mkdir()
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"/* {f} */\n")


class TestResolveHeaders:
    """Tests for resolve_headers()."""

    def test_resolve_explicit_paths(self, tmp_path: Path) -> None:
        """Explicit file paths resolve to absolute paths under project_root."""
        _make_tree(tmp_path, ["foo.h", "bar.h"])
        paths, mapping = resolve_headers(
            patterns=["foo.h", "bar.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        assert paths == sorted([tmp_path / "foo.h", tmp_path / "bar.h"])
        assert mapping[tmp_path / "foo.h"] == ["foo.h"]
        assert mapping[tmp_path / "bar.h"] == ["bar.h"]

    def test_resolve_glob_star(self, tmp_path: Path) -> None:
        """The '*.h' pattern matches .h files in the root directory."""
        _make_tree(tmp_path, ["alpha.h", "beta.h", "sub/gamma.h"])
        paths, mapping = resolve_headers(
            patterns=["*.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        assert tmp_path / "alpha.h" in paths
        assert tmp_path / "beta.h" in paths
        # *.h should not match files in subdirectories
        assert tmp_path / "sub" / "gamma.h" not in paths

    def test_resolve_glob_recursive(self, tmp_path: Path) -> None:
        """The '**/*.h' pattern matches .h files recursively."""
        _make_tree(tmp_path, ["alpha.h", "include/beta.h", "include/net/gamma.h"])
        paths, mapping = resolve_headers(
            patterns=["**/*.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        assert tmp_path / "alpha.h" in paths
        assert tmp_path / "include" / "beta.h" in paths
        assert tmp_path / "include" / "net" / "gamma.h" in paths

    def test_resolve_exclude_patterns(self, tmp_path: Path) -> None:
        """Exclude patterns remove matching files from results."""
        _make_tree(tmp_path, ["public.h", "internal/private.h", "internal/secret.h"])
        paths, mapping = resolve_headers(
            patterns=["**/*.h"],
            exclude_patterns=["internal/*.h"],
            project_root=tmp_path,
        )
        assert tmp_path / "public.h" in paths
        assert tmp_path / "internal" / "private.h" not in paths
        assert tmp_path / "internal" / "secret.h" not in paths
        # Excluded paths should not appear in the mapping
        assert tmp_path / "internal" / "private.h" not in mapping

    def test_resolve_dedup_multiple_patterns(self, tmp_path: Path) -> None:
        """A file matched by two patterns appears once in sorted output."""
        _make_tree(tmp_path, ["include/foo.h"])
        paths, mapping = resolve_headers(
            patterns=["include/*.h", "**/*.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        assert paths.count(tmp_path / "include" / "foo.h") == 1

    def test_resolve_sorted_deterministic(self, tmp_path: Path) -> None:
        """Output paths are sorted for deterministic ordering."""
        _make_tree(tmp_path, ["z.h", "a.h", "m.h"])
        paths, _ = resolve_headers(
            patterns=["*.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        assert paths == sorted(paths)

    def test_resolve_empty_raises_valueerror(self, tmp_path: Path) -> None:
        """Patterns matching nothing raise ValueError."""
        _make_tree(tmp_path, [])
        with pytest.raises(ValueError, match="No headers matched"):
            resolve_headers(
                patterns=["nonexistent/*.h"],
                exclude_patterns=[],
                project_root=tmp_path,
            )

    def test_resolve_literal_path_no_glob(self, tmp_path: Path) -> None:
        """A pattern without glob metacharacters is treated as a literal path."""
        _make_tree(tmp_path, ["src/mylib.h"])
        paths, mapping = resolve_headers(
            patterns=["src/mylib.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        assert paths == [tmp_path / "src" / "mylib.h"]
        assert mapping[tmp_path / "src" / "mylib.h"] == ["src/mylib.h"]

    def test_resolve_literal_outside_project_root_raises(self, tmp_path: Path) -> None:
        """A literal path escaping project_root raises ValueError."""
        _make_tree(tmp_path, ["foo.h"])
        with pytest.raises(ValueError, match="outside project root"):
            resolve_headers(
                patterns=["../../etc/passwd"],
                exclude_patterns=[],
                project_root=tmp_path,
            )

    def test_resolve_pattern_mapping_all_patterns(self, tmp_path: Path) -> None:
        """The mapping dict maps each path to ALL matching patterns, not just the last."""
        _make_tree(tmp_path, ["include/foo.h"])
        paths, mapping = resolve_headers(
            patterns=["*.h", "include/*.h", "**/*.h"],
            exclude_patterns=[],
            project_root=tmp_path,
        )
        target = tmp_path / "include" / "foo.h"
        assert target in mapping
        # Should have both "include/*.h" and "**/*.h" (not "*.h" since that
        # only matches root-level files, not files in subdirectories)
        assert "include/*.h" in mapping[target]
        assert "**/*.h" in mapping[target]
