"""Tests for PEP 517 build backend."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


class TestGetInnerBackend:
    """Test _get_inner_backend imports the correct module."""

    def test_default_inner_backend(self) -> None:
        """Default inner backend is hatchling.build."""
        from headerkit.build_backend import _get_inner_backend

        mock_module = MagicMock()
        with patch(
            "headerkit.build_backend.importlib.import_module",
            return_value=mock_module,
        ) as mock_import:
            result = _get_inner_backend(None)
            mock_import.assert_called_once_with("hatchling.build")
            assert result is mock_module

    def test_custom_inner_backend_via_config_settings(self) -> None:
        """config_settings['inner-backend'] overrides the default."""
        from headerkit.build_backend import _get_inner_backend

        mock_module = MagicMock()
        with patch(
            "headerkit.build_backend.importlib.import_module",
            return_value=mock_module,
        ) as mock_import:
            result = _get_inner_backend({"inner-backend": "flit_core.buildapi"})
            mock_import.assert_called_once_with("flit_core.buildapi")
            assert result is mock_module

    def test_empty_config_settings_uses_default(self) -> None:
        """Empty config_settings dict still uses default."""
        from headerkit.build_backend import _get_inner_backend

        mock_module = MagicMock()
        with patch(
            "headerkit.build_backend.importlib.import_module",
            return_value=mock_module,
        ) as mock_import:
            result = _get_inner_backend({})
            mock_import.assert_called_once_with("hatchling.build")
            assert result is mock_module


class TestLoadHeaderkitConfig:
    """Test _load_headerkit_config reads pyproject.toml correctly."""

    def test_reads_tool_headerkit_section(self, tmp_path: Path) -> None:
        """Extracts [tool.headerkit] from a valid pyproject.toml."""
        from headerkit.build_backend import _load_headerkit_config

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
            [tool.headerkit]
            backend = "libclang"
            writers = ["ctypes"]

            [tool.headerkit.headers."mylib.h"]
            defines = ["FOO=1"]
        """)
        )
        result = _load_headerkit_config(pyproject)
        assert result == {
            "backend": "libclang",
            "writers": ["ctypes"],
            "headers": {
                "mylib.h": {
                    "defines": ["FOO=1"],
                },
            },
        }

    def test_missing_tool_headerkit_returns_empty(self, tmp_path: Path) -> None:
        """Returns {} when [tool.headerkit] section is absent."""
        from headerkit.build_backend import _load_headerkit_config

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
            [project]
            name = "mypackage"
        """)
        )
        result = _load_headerkit_config(pyproject)
        assert result == {}

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Returns {} when pyproject.toml does not exist."""
        from headerkit.build_backend import _load_headerkit_config

        result = _load_headerkit_config(tmp_path / "nonexistent.toml")
        assert result == {}

    def test_invalid_toml_returns_empty(self, tmp_path: Path) -> None:
        """Returns {} and logs warning on invalid TOML."""
        from headerkit.build_backend import _load_headerkit_config

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[[[[invalid toml")
        result = _load_headerkit_config(pyproject)
        assert result == {}


class TestRunGeneration:
    """Test _run_generation dispatches to generate_all correctly."""

    def test_no_headers_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No headers in config means no generate_all calls."""
        from headerkit.build_backend import _run_generation

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
            [tool.headerkit]
            backend = "libclang"
        """)
        )
        monkeypatch.chdir(tmp_path)
        with patch("headerkit._generate.generate_all") as mock_gen:
            _run_generation(None)
            mock_gen.assert_not_called()

    def test_calls_generate_all_per_header(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calls generate_all once per header entry with merged config."""
        from headerkit.build_backend import _run_generation

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
            [tool.headerkit]
            backend = "libclang"
            writers = ["ctypes"]
            include_dirs = ["/usr/include"]
            defines = ["GLOBAL=1"]

            [tool.headerkit.headers."alpha.h"]
            defines = ["ALPHA=1"]
            include_dirs = ["/alpha/include"]

            [tool.headerkit.headers."beta.h"]
        """)
        )
        monkeypatch.chdir(tmp_path)
        with patch("headerkit._generate.generate_all") as mock_gen:
            _run_generation(None)
            assert mock_gen.call_count == 2
            mock_gen.assert_has_calls(
                [
                    call(
                        header_path="alpha.h",
                        writers=["ctypes"],
                        backend_name="libclang",
                        include_dirs=["/usr/include", "/alpha/include"],
                        defines=["GLOBAL=1", "ALPHA=1"],
                        no_cache=False,
                        no_ir_cache=False,
                        no_output_cache=False,
                    ),
                    call(
                        header_path="beta.h",
                        writers=["ctypes"],
                        backend_name="libclang",
                        include_dirs=["/usr/include"],
                        defines=["GLOBAL=1"],
                        no_cache=False,
                        no_ir_cache=False,
                        no_output_cache=False,
                    ),
                ],
                any_order=True,
            )

    def test_config_settings_cache_flags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """config_settings no-cache/no-ir-cache/no-output-cache are parsed."""
        from headerkit.build_backend import _run_generation

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
            [tool.headerkit]

            [tool.headerkit.headers."test.h"]
        """)
        )
        monkeypatch.chdir(tmp_path)
        with patch("headerkit._generate.generate_all") as mock_gen:
            _run_generation({"no-cache": "true", "no-ir-cache": "1", "no-output-cache": "True"})
            mock_gen.assert_called_once_with(
                header_path="test.h",
                writers=None,
                backend_name="libclang",
                include_dirs=None,
                defines=None,
                no_cache=True,
                no_ir_cache=True,
                no_output_cache=True,
            )

    def test_missing_pyproject_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing pyproject.toml means no generation runs."""
        from headerkit.build_backend import _run_generation

        monkeypatch.chdir(tmp_path)
        with patch("headerkit._generate.generate_all") as mock_gen:
            _run_generation(None)
            mock_gen.assert_not_called()


class TestBuildWheel:
    """Test build_wheel runs generation then delegates."""

    def test_build_wheel_generates_then_delegates(self) -> None:
        """build_wheel calls _run_generation then inner build_wheel."""
        from headerkit.build_backend import build_wheel

        mock_inner = MagicMock()
        mock_inner.build_wheel.return_value = "my_package-1.0-py3-none-any.whl"
        call_order: list[str] = []

        def track_generation(_cs: Any = None) -> None:
            call_order.append("generation")

        def track_build_wheel(*_args: Any, **_kwargs: Any) -> str:
            call_order.append("build_wheel")
            return "my_package-1.0-py3-none-any.whl"

        mock_inner.build_wheel = track_build_wheel

        with (
            patch(
                "headerkit.build_backend._run_generation",
                side_effect=track_generation,
            ),
            patch(
                "headerkit.build_backend._get_inner_backend",
                return_value=mock_inner,
            ),
        ):
            result = build_wheel("/tmp/dist", {"inner-backend": "test"}, None)
            assert result == "my_package-1.0-py3-none-any.whl"
            assert call_order == ["generation", "build_wheel"]


class TestBuildSdist:
    """Test build_sdist handles generation failure gracefully."""

    def test_build_sdist_generates_then_delegates(self) -> None:
        """build_sdist calls _run_generation then inner build_sdist."""
        from headerkit.build_backend import build_sdist

        mock_inner = MagicMock()
        mock_inner.build_sdist.return_value = "my_package-1.0.tar.gz"

        with (
            patch("headerkit.build_backend._run_generation") as mock_gen,
            patch(
                "headerkit.build_backend._get_inner_backend",
                return_value=mock_inner,
            ),
        ):
            result = build_sdist("/tmp/dist", None)
            mock_gen.assert_called_once_with(None)
            mock_inner.build_sdist.assert_called_once_with("/tmp/dist", None)
            assert result == "my_package-1.0.tar.gz"

    def test_build_sdist_swallows_generation_error(self) -> None:
        """build_sdist still builds if generation fails."""
        from headerkit.build_backend import build_sdist

        mock_inner = MagicMock()
        mock_inner.build_sdist.return_value = "my_package-1.0.tar.gz"

        with (
            patch(
                "headerkit.build_backend._run_generation",
                side_effect=RuntimeError("libclang not found"),
            ),
            patch(
                "headerkit.build_backend._get_inner_backend",
                return_value=mock_inner,
            ),
        ):
            result = build_sdist("/tmp/dist", None)
            mock_inner.build_sdist.assert_called_once_with("/tmp/dist", None)
            assert result == "my_package-1.0.tar.gz"


class TestPrepareMetadata:
    """Test prepare_metadata_for_build_wheel delegates without generation."""

    def test_delegates_without_generation(self) -> None:
        """prepare_metadata_for_build_wheel does not run generation."""
        from headerkit.build_backend import prepare_metadata_for_build_wheel

        mock_inner = MagicMock()
        mock_inner.prepare_metadata_for_build_wheel.return_value = "my_package-1.0.dist-info"

        with (
            patch("headerkit.build_backend._run_generation") as mock_gen,
            patch(
                "headerkit.build_backend._get_inner_backend",
                return_value=mock_inner,
            ),
        ):
            result = prepare_metadata_for_build_wheel("/tmp/meta", None)
            mock_gen.assert_not_called()
            mock_inner.prepare_metadata_for_build_wheel.assert_called_once_with("/tmp/meta", None)
            assert result == "my_package-1.0.dist-info"


class TestBuildEditable:
    """Test build_editable runs generation then delegates."""

    def test_build_editable_generates_then_delegates(self) -> None:
        """build_editable calls _run_generation then inner build_editable."""
        from headerkit.build_backend import build_editable

        mock_inner = MagicMock()
        mock_inner.build_editable.return_value = "my_package-1.0-py3-none-any.whl"

        with (
            patch("headerkit.build_backend._run_generation") as mock_gen,
            patch(
                "headerkit.build_backend._get_inner_backend",
                return_value=mock_inner,
            ),
        ):
            result = build_editable("/tmp/dist", {"no-cache": "true"}, None)
            mock_gen.assert_called_once_with({"no-cache": "true"})
            mock_inner.build_editable.assert_called_once_with("/tmp/dist", {"no-cache": "true"}, None)
            assert result == "my_package-1.0-py3-none-any.whl"


class TestGetRequires:
    """Test get_requires_for_build_wheel/sdist proxy to inner backend."""

    def test_get_requires_for_build_wheel_proxies(self) -> None:
        """get_requires_for_build_wheel returns inner backend's result."""
        from headerkit.build_backend import get_requires_for_build_wheel

        mock_inner = MagicMock()
        mock_inner.get_requires_for_build_wheel.return_value = ["hatchling>=1.0"]

        with patch(
            "headerkit.build_backend._get_inner_backend",
            return_value=mock_inner,
        ):
            result = get_requires_for_build_wheel(None)
            mock_inner.get_requires_for_build_wheel.assert_called_once_with(None)
            assert result == ["hatchling>=1.0"]

    def test_get_requires_for_build_wheel_missing_hook(self) -> None:
        """Returns [] when inner backend lacks the hook."""
        from headerkit.build_backend import get_requires_for_build_wheel

        mock_inner = MagicMock(spec=[])  # no attributes

        with patch(
            "headerkit.build_backend._get_inner_backend",
            return_value=mock_inner,
        ):
            result = get_requires_for_build_wheel(None)
            assert result == []

    def test_get_requires_for_build_sdist_proxies(self) -> None:
        """get_requires_for_build_sdist returns inner backend's result."""
        from headerkit.build_backend import get_requires_for_build_sdist

        mock_inner = MagicMock()
        mock_inner.get_requires_for_build_sdist.return_value = ["setuptools"]

        with patch(
            "headerkit.build_backend._get_inner_backend",
            return_value=mock_inner,
        ):
            result = get_requires_for_build_sdist(None)
            mock_inner.get_requires_for_build_sdist.assert_called_once_with(None)
            assert result == ["setuptools"]

    def test_get_requires_for_build_sdist_missing_hook(self) -> None:
        """Returns [] when inner backend lacks the hook."""
        from headerkit.build_backend import get_requires_for_build_sdist

        mock_inner = MagicMock(spec=[])

        with patch(
            "headerkit.build_backend._get_inner_backend",
            return_value=mock_inner,
        ):
            result = get_requires_for_build_sdist(None)
            assert result == []
