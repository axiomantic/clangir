"""Tests for headerkit._cli — argument parsing and main() entry point."""

from __future__ import annotations

import re
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from headerkit._cli import _build_umbrella, _parse_defines, _parse_writer_specs
from headerkit.ir import Header

# =============================================================================
# Helpers / shared mock classes
# =============================================================================


class MockBackend:
    """Minimal mock backend for CLI tests."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def parse(self, code: str, filename: str, **kwargs: Any) -> Header:
        return Header(path=filename, declarations=[])


class MockWriter:
    """Minimal mock writer for CLI tests."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def write(self, header: Header) -> str:
        return "mock-output"

    @property
    def name(self) -> str:
        return "mock"

    @property
    def format_description(self) -> str:
        return "Mock writer"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def reset_registries() -> Generator[None, None, None]:
    """Save and restore both backend and writer registry state."""
    import headerkit.backends as b
    import headerkit.writers as w

    saved_b_registry = dict(b._BACKEND_REGISTRY)
    saved_b_default = b._DEFAULT_BACKEND
    saved_b_loaded = b._BACKENDS_LOADED

    saved_w_registry = dict(w._WRITER_REGISTRY)
    saved_w_descriptions = dict(w._WRITER_DESCRIPTIONS)
    saved_w_default = w._DEFAULT_WRITER
    saved_w_loaded = w._WRITERS_LOADED

    yield

    b._BACKEND_REGISTRY.clear()
    b._BACKEND_REGISTRY.update(saved_b_registry)
    b._DEFAULT_BACKEND = saved_b_default
    b._BACKENDS_LOADED = saved_b_loaded

    w._WRITER_REGISTRY.clear()
    w._WRITER_REGISTRY.update(saved_w_registry)
    w._WRITER_DESCRIPTIONS.clear()
    w._WRITER_DESCRIPTIONS.update(saved_w_descriptions)
    w._DEFAULT_WRITER = saved_w_default
    w._WRITERS_LOADED = saved_w_loaded


@pytest.fixture()
def setup_mocks(reset_registries: None) -> Generator[tuple[MagicMock, MagicMock], None, None]:  # noqa: ARG001
    """Patch get_backend and get_writer in _cli module, suppress plugin loading."""
    mock_backend_instance = MockBackend()
    mock_writer_instance = MockWriter()

    with (
        patch("headerkit._cli.get_backend", return_value=mock_backend_instance) as mock_get_backend,
        patch("headerkit._cli.get_writer", return_value=mock_writer_instance) as mock_get_writer,
        patch("headerkit._cli._load_backend_plugins"),
        patch("headerkit._cli._load_writer_plugins"),
    ):
        yield mock_get_backend, mock_get_writer


# =============================================================================
# TestParseWriterSpecs
# =============================================================================


class TestParseWriterSpecs:
    def test_parse_single_writer_stdout(self) -> None:
        """'-w cffi' produces WriterSpec with output_path=None."""
        specs = _parse_writer_specs(["cffi"], [])
        assert len(specs) == 1
        assert specs[0].name == "cffi"
        assert specs[0].output_path is None
        assert specs[0].options == {}

    def test_parse_single_writer_file(self) -> None:
        """'-w cffi:out.h' produces WriterSpec with output_path='out.h'."""
        specs = _parse_writer_specs(["cffi:out.h"], [])
        assert len(specs) == 1
        assert specs[0].name == "cffi"
        assert specs[0].output_path == "out.h"
        assert specs[0].options == {}

    def test_parse_multiple_writers(self) -> None:
        """'-w cffi:a.h -w json:b.json' produces two specs."""
        specs = _parse_writer_specs(["cffi:a.h", "json:b.json"], [])
        assert len(specs) == 2
        assert specs[0].name == "cffi"
        assert specs[0].output_path == "a.h"
        assert specs[1].name == "json"
        assert specs[1].output_path == "b.json"

    def test_parse_writer_opt_scoped(self) -> None:
        """'--writer-opt cffi:exclude_patterns=^__' accumulates correctly."""
        specs = _parse_writer_specs(["cffi"], ["cffi:exclude_patterns=^__"])
        assert specs[0].options == {"exclude_patterns": ["^__"]}

    def test_parse_writer_opt_multiple_same_key(self) -> None:
        """Two --writer-opt flags for same key produce list[str]."""
        specs = _parse_writer_specs(
            ["cffi"],
            ["cffi:exclude_patterns=^__", "cffi:exclude_patterns=^_internal"],
        )
        assert specs[0].options == {"exclude_patterns": ["^__", "^_internal"]}

    def test_parse_writer_opt_unscoped_raises(self) -> None:
        """Unscoped --writer-opt (no colon) raises ValueError."""
        with pytest.raises(ValueError, match="unscoped"):
            _parse_writer_specs(["cffi"], ["key=value"])

    def test_parse_orphaned_opt_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--writer-opt for a writer not in the writer list warns to stderr and is skipped."""
        specs = _parse_writer_specs(["cffi"], ["json:key=value"])
        assert len(specs) == 1
        assert specs[0].name == "cffi"
        assert specs[0].options == {}
        captured = capsys.readouterr()
        assert captured.err != "", "Expected a warning on stderr for orphaned --writer-opt"


# =============================================================================
# TestBuildUmbrella
# =============================================================================


class TestBuildUmbrella:
    def test_single_file_umbrella(self, tmp_path: Path) -> None:
        """Single file produces correct #include and project_prefixes=(parent_dir,)."""
        header = tmp_path / "foo.h"
        header.write_text("// header\n")
        code, filename, project_prefixes = _build_umbrella([str(header)])
        assert filename == "foo.h"
        assert f'#include "{header.resolve()}"' in code
        assert project_prefixes == (str(tmp_path.resolve()),)

    def test_multi_file_umbrella(self, tmp_path: Path) -> None:
        """Multiple files produce umbrella includes, filename='<multiple>', and parent dirs."""
        sub_a = tmp_path / "a"
        sub_b = tmp_path / "b"
        sub_a.mkdir()
        sub_b.mkdir()
        header_a = sub_a / "a.h"
        header_b = sub_b / "b.h"
        header_a.write_text("// a\n")
        header_b.write_text("// b\n")
        code, filename, project_prefixes = _build_umbrella([str(header_a), str(header_b)])
        assert filename == "<multiple>"
        assert f'#include "{header_a.resolve()}"' in code
        assert f'#include "{header_b.resolve()}"' in code
        assert str(sub_a.resolve()) in project_prefixes
        assert str(sub_b.resolve()) in project_prefixes

    def test_project_prefixes_are_parents(self, tmp_path: Path) -> None:
        """project_prefixes are parent directories, not file paths."""
        header = tmp_path / "foo.h"
        header.write_text("// header\n")
        _code, _filename, project_prefixes = _build_umbrella([str(header)])
        # Prefixes must be directories, not file paths
        for prefix in project_prefixes:
            assert not prefix.endswith(".h"), f"Prefix should be a directory, got: {prefix}"
            assert prefix == str(tmp_path.resolve())


# =============================================================================
# TestParseDefines
# =============================================================================


class TestParseDefines:
    def test_define_with_value(self) -> None:
        """-D FOO=BAR -> '-DFOO=BAR'."""
        result = _parse_defines(["FOO=BAR"])
        assert result == ["-DFOO=BAR"]

    def test_define_without_value(self) -> None:
        """-D FOO -> '-DFOO'."""
        result = _parse_defines(["FOO"])
        assert result == ["-DFOO"]

    def test_multiple_defines(self) -> None:
        """Multiple defines produce multiple -D items."""
        result = _parse_defines(["FOO", "BAR=1"])
        assert result == ["-DFOO", "-DBAR=1"]


# =============================================================================
# TestMain
# =============================================================================


class TestMain:
    """Tests for the main() entry point."""

    @pytest.fixture(autouse=True)
    def _setup(self, setup_mocks: tuple[MagicMock, MagicMock]) -> None:
        self.mock_get_backend, self.mock_get_writer = setup_mocks

    def test_main_version(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """--version prints version string with semver and exits 0."""
        monkeypatch.setattr(sys, "argv", ["headerkit", "--version"])
        with pytest.raises(SystemExit) as exc_info:
            from headerkit._cli import main

            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        version_output = captured.out or captured.err
        assert "headerkit" in version_output
        assert re.search(r"\d+\.\d+\.\d+", version_output), (
            f"Expected semver pattern in version output, got: {version_output!r}"
        )

    def test_main_no_input_exits(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """No input files produces nonzero exit (argparse error)."""
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config"])
        with pytest.raises(SystemExit) as exc_info:
            from headerkit._cli import main

            main()
        assert exc_info.value.code != 0

    def test_main_single_writer_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Single writer with no output path writes to stdout."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "mock", str(header_file)])
        from headerkit._cli import main

        result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert captured.out == "mock-output"
        self.mock_get_backend.assert_called_once()
        self.mock_get_writer.assert_called_once_with("mock")

    def test_main_single_writer_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single writer with output path writes to file."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        output_file = tmp_path / "out.h"
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", "--no-config", "-w", f"mock:{output_file}", str(header_file)],
        )
        from headerkit._cli import main

        result = main()
        assert result == 0
        assert output_file.read_text(encoding="utf-8") == "mock-output"
        self.mock_get_backend.assert_called_once()
        self.mock_get_writer.assert_called_once_with("mock")

    def test_main_multiple_stdout_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two writers without output paths produce exit code 1."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", "--no-config", "-w", "mock", "-w", "other", str(header_file)],
        )
        from headerkit._cli import main

        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "stdout" in captured.err

    def test_main_diff_writer_no_baseline_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """diff writer without baseline emits warning to stderr and exits 0."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "diff", str(header_file)])
        from headerkit._cli import main

        result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "Warning" in captured.err or "warning" in captured.err

    def test_main_input_file_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nonexistent input file produces exit code 1."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", "--no-config", str(tmp_path / "nonexistent.h")],
        )
        from headerkit._cli import main

        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_main_config_and_no_config_exclusive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--config and --no-config together produce exit code 1 with error on stderr."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('backend = "libclang"\n')
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", "--config", str(config_file), "--no-config", str(header_file)],
        )
        from headerkit._cli import main

        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.err

    def test_main_no_config_skips_config_loading(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--no-config prevents find_config_file and load_config from being called."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "mock", str(header_file)])

        with (
            patch("headerkit._cli.find_config_file") as mock_find,
            patch("headerkit._cli.load_config") as mock_load,
        ):
            from headerkit._cli import main

            result = main()

        assert result == 0
        mock_find.assert_not_called()
        mock_load.assert_not_called()

    def test_main_plugin_loaders_called(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """main() calls _load_backend_plugins() and _load_writer_plugins() exactly once."""
        header_file = tmp_path / "test.h"
        header_file.write_text("// test\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "mock", str(header_file)])

        with (
            patch("headerkit._cli._load_backend_plugins") as mock_load_backends,
            patch("headerkit._cli._load_writer_plugins") as mock_load_writers,
        ):
            from headerkit._cli import main

            result = main()

        assert result == 0
        mock_load_backends.assert_called_once()
        mock_load_writers.assert_called_once()
