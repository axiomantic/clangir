"""Tests for batch_generate() orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from headerkit._generate import BatchResult, batch_generate


def _make_header(path: Path, content: str = "int x;\n") -> Path:
    """Create a minimal .h file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project with .git marker."""
    (tmp_path / ".git").mkdir()
    return tmp_path


class TestBatchMultipleHeaders:
    """test_batch_multiple_headers: Multiple headers processed sequentially."""

    def test_batch_multiple_headers(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        _make_header(project / "a.h")
        _make_header(project / "b.h")

        mock_generate = MagicMock(return_value="mock-output")
        with patch("headerkit._generate.generate", mock_generate):
            result = batch_generate(
                patterns=["a.h", "b.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
            )

        assert isinstance(result, BatchResult)
        assert result.headers_processed == 2
        assert len(result.results) == 2
        assert mock_generate.call_count == 2
        # All results should have output_path set (batch always writes files)
        for r in result.results:
            assert r.output_path is not None


class TestBatchPerPatternOverrides:
    """test_batch_per_pattern_overrides: Later pattern overrides earlier pattern's defines."""

    def test_batch_per_pattern_overrides(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        _make_header(project / "a.h")

        mock_generate = MagicMock(return_value="mock-output")
        with patch("headerkit._generate.generate", mock_generate):
            batch_generate(
                patterns=["a.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
                header_overrides={
                    "a.h": {"defines": ["FOO=1", "BAR=2"]},
                },
            )

        call_kwargs = mock_generate.call_args.kwargs
        assert call_kwargs["defines"] == ["FOO=1", "BAR=2"]


class TestBatchMultiPatternMerge:
    """test_batch_multi_pattern_merge: Header matching two patterns gets merged overrides."""

    def test_batch_multi_pattern_merge(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        _make_header(project / "lib.h")

        mock_generate = MagicMock(return_value="mock-output")
        with patch("headerkit._generate.generate", mock_generate):
            # "*.h" matches first, then "lib.h" matches second
            # Second pattern's target should override first's
            batch_generate(
                patterns=["*.h", "lib.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
                header_overrides={
                    "*.h": {"target": "x86_64-linux-gnu", "defines": ["COMMON=1"]},
                    "lib.h": {"target": "aarch64-linux-gnu"},
                },
            )

        call_kwargs = mock_generate.call_args.kwargs
        # "lib.h" override should win for target
        assert call_kwargs["target"] == "aarch64-linux-gnu"
        # "*.h" defines should still be inherited (lib.h has no defines override)
        assert call_kwargs["defines"] == ["COMMON=1"]


class TestBatchFailFast:
    """test_batch_fail_fast: First header that fails raises immediately."""

    def test_batch_fail_fast(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        _make_header(project / "a.h")
        _make_header(project / "b.h")

        call_count = 0

        def mock_generate_side_effect(**kwargs: Any) -> str:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Parse failed")
            return "mock-output"

        with (
            patch("headerkit._generate.generate", side_effect=mock_generate_side_effect),
            pytest.raises(RuntimeError, match="Parse failed"),
        ):
            batch_generate(
                patterns=["a.h", "b.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
            )

        # Should have stopped after first failure
        assert call_count == 1


class TestBatchOutputCollisionRaises:
    """test_batch_output_collision_raises: Two headers with same output path raises."""

    def test_batch_output_collision_raises(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        _make_header(project / "a.h")
        _make_header(project / "b.h")

        # Both headers use a template that produces the same output
        with pytest.raises(ValueError, match="Output collision"):
            batch_generate(
                patterns=["a.h", "b.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
                output_templates={"json": "same_output.json"},
            )


class TestBatchExcludePatterns:
    """test_batch_exclude_patterns: Excluded headers not processed."""

    def test_batch_exclude_patterns(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        _make_header(project / "a.h")
        _make_header(project / "b.h")
        _make_header(project / "internal.h")

        mock_generate = MagicMock(return_value="mock-output")
        with patch("headerkit._generate.generate", mock_generate):
            result = batch_generate(
                patterns=["*.h"],
                exclude_patterns=["internal.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
            )

        assert result.headers_processed == 2
        # internal.h should not be in any output paths
        processed_headers = {call.kwargs["header_path"] for call in mock_generate.call_args_list}
        assert project / "internal.h" not in processed_headers


class TestBatchNoOutputPathRaises:
    """test_batch_no_output_path_raises: When no output template resolvable, uses default."""

    def test_batch_uses_default_output_pattern(self, tmp_path: Path) -> None:
        """When no explicit template, batch_generate uses writer default pattern."""
        project = _make_project(tmp_path)
        _make_header(project / "a.h")

        mock_generate = MagicMock(return_value="mock-output")
        with patch("headerkit._generate.generate", mock_generate):
            result = batch_generate(
                patterns=["a.h"],
                writers=["json"],
                project_root=project,
                no_cache=True,
                # No output_templates provided -- should use writer default
            )

        assert result.headers_processed == 1
        # Should have resolved to the writer's default pattern
        call_kwargs = mock_generate.call_args.kwargs
        output_path = call_kwargs["output_path"]
        assert output_path is not None
        # json writer default is {dir}/{stem}.json -> a.json at project root
        assert str(output_path).endswith("a.json")
