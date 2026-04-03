"""Tests for output path resolution, collision detection, and CLI output specs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from headerkit._cli import _parse_output_specs
from headerkit._generate import _writer_default_output_pattern
from headerkit._resolve import check_output_collisions, resolve_output_path


class TestResolveOutputPath:
    """Tests for resolve_output_path()."""

    def test_resolve_stem_variable(self, tmp_path: Path) -> None:
        header = tmp_path / "socket.h"
        header.touch()
        result = resolve_output_path("{stem}_cffi.py", header, tmp_path)
        assert result == Path("socket_cffi.py")

    def test_resolve_name_variable(self, tmp_path: Path) -> None:
        header = tmp_path / "socket.h"
        header.touch()
        result = resolve_output_path("{name}.txt", header, tmp_path)
        assert result == Path("socket.h.txt")

    def test_resolve_dir_variable(self, tmp_path: Path) -> None:
        subdir = tmp_path / "include" / "net"
        subdir.mkdir(parents=True)
        header = subdir / "socket.h"
        header.touch()
        result = resolve_output_path("{dir}/{stem}_cffi.py", header, tmp_path)
        assert result == Path("include/net/socket_cffi.py")

    def test_resolve_dir_at_project_root(self, tmp_path: Path) -> None:
        header = tmp_path / "mylib.h"
        header.touch()
        result = resolve_output_path("{dir}/{stem}_cffi.py", header, tmp_path)
        # {dir} is "." when header is at project root
        assert result == Path("./mylib_cffi.py")

    def test_resolve_dir_dot_normalization(self, tmp_path: Path) -> None:
        header = tmp_path / "mylib.h"
        header.touch()
        result = resolve_output_path("{dir}/{stem}_cffi.py", header, tmp_path)
        # Path normalizes "./" so the stem portion is correct
        assert result.name == "mylib_cffi.py"

    def test_resolve_unknown_variable_raises(self, tmp_path: Path) -> None:
        header = tmp_path / "test.h"
        header.touch()
        with pytest.raises(ValueError, match="Unknown template variable"):
            resolve_output_path("{foo}/{stem}.py", header, tmp_path)


class TestCheckOutputCollisions:
    """Tests for check_output_collisions()."""

    def test_collision_detected(self, tmp_path: Path) -> None:
        # Two headers that would resolve to the same output
        resolved: dict[tuple[Path, str], Path] = {
            (tmp_path / "a.h", "cffi"): tmp_path / "out.py",
            (tmp_path / "b.h", "cffi"): tmp_path / "out.py",
        }
        with pytest.raises(ValueError, match="Output collision"):
            check_output_collisions(resolved)

    def test_no_collision_unique_paths(self, tmp_path: Path) -> None:
        resolved: dict[tuple[Path, str], Path] = {
            (tmp_path / "a.h", "cffi"): tmp_path / "a_cffi.py",
            (tmp_path / "b.h", "cffi"): tmp_path / "b_cffi.py",
        }
        # Should not raise
        check_output_collisions(resolved)


class TestWriterDefaultOutputPattern:
    """Tests for _writer_default_output_pattern()."""

    def test_writer_default_output_pattern(self) -> None:
        """Verify returns class attribute when present."""
        writer = MagicMock()
        writer.default_output_pattern = "{dir}/{stem}_cffi.py"
        result = _writer_default_output_pattern(writer, "cffi")
        assert result == "{dir}/{stem}_cffi.py"

    def test_writer_fallback_output_pattern(self) -> None:
        """Writer without default_output_pattern gets extension-based fallback."""
        writer = MagicMock(spec=[])  # No default_output_pattern attribute
        result = _writer_default_output_pattern(writer, "cffi")
        assert result == "{dir}/{stem}.py"

    def test_writer_fallback_unknown_writer(self) -> None:
        """Unknown writer name falls back to .txt extension."""
        writer = MagicMock(spec=[])
        result = _writer_default_output_pattern(writer, "unknown_writer")
        assert result == "{dir}/{stem}.txt"


class TestParseOutputSpecs:
    """Tests for _parse_output_specs()."""

    def test_parse_output_specs_valid(self) -> None:
        result = _parse_output_specs(["cffi:{stem}_cffi.py", "json:{dir}/{stem}.json"])
        assert result == {
            "cffi": "{stem}_cffi.py",
            "json": "{dir}/{stem}.json",
        }

    def test_parse_output_specs_malformed(self) -> None:
        with pytest.raises(ValueError, match="malformed --output"):
            _parse_output_specs(["cffi"])

    def test_parse_output_specs_empty_template(self) -> None:
        with pytest.raises(ValueError, match="malformed --output"):
            _parse_output_specs(["cffi:"])

    def test_parse_output_specs_empty_list(self) -> None:
        result = _parse_output_specs([])
        assert result == {}
