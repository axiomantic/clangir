"""Tests for PEP 517 build backend and auto-install variant."""

from __future__ import annotations

import os
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


class TestBuildBackendAuto:
    """Test headerkit.build_backend_auto sets env var and delegates."""

    def test_build_wheel_sets_env_and_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto.build_wheel sets HEADERKIT_AUTO_INSTALL_LIBCLANG=1."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)

        captured_env: dict[str, str] = {}

        def mock_build_wheel(
            _wheel_directory: str,
            _config_settings: dict[str, Any] | None = None,
            _metadata_directory: str | None = None,
        ) -> str:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return "pkg-1.0-py3-none-any.whl"

        monkeypatch.setattr("headerkit.build_backend.build_wheel", mock_build_wheel)

        from headerkit.build_backend_auto import build_wheel

        result = build_wheel("/tmp/dist", None, None)
        assert result == "pkg-1.0-py3-none-any.whl"
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "1"

    def test_build_sdist_sets_env_and_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto.build_sdist sets HEADERKIT_AUTO_INSTALL_LIBCLANG=1."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)

        captured_env: dict[str, str] = {}

        def mock_build_sdist(
            _sdist_directory: str,
            _config_settings: dict[str, Any] | None = None,
        ) -> str:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return "pkg-1.0.tar.gz"

        monkeypatch.setattr("headerkit.build_backend.build_sdist", mock_build_sdist)

        from headerkit.build_backend_auto import build_sdist

        result = build_sdist("/tmp/dist", None)
        assert result == "pkg-1.0.tar.gz"
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "1"

    def test_does_not_override_existing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto does not override an existing env var value."""
        monkeypatch.setenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", "0")

        captured_env: dict[str, str] = {}

        def mock_build_wheel(
            _wheel_directory: str,
            _config_settings: dict[str, Any] | None = None,
            _metadata_directory: str | None = None,
        ) -> str:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return "pkg-1.0-py3-none-any.whl"

        monkeypatch.setattr("headerkit.build_backend.build_wheel", mock_build_wheel)

        from headerkit.build_backend_auto import build_wheel

        result = build_wheel("/tmp/dist", None, None)
        assert result == "pkg-1.0-py3-none-any.whl"
        # setdefault should NOT override existing "0"
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "0"

    def test_prepare_metadata_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto.prepare_metadata_for_build_wheel sets env and delegates."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)

        captured_env: dict[str, str] = {}

        def mock_prepare(
            _metadata_directory: str,
            _config_settings: dict[str, Any] | None = None,
        ) -> str:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return "pkg-1.0.dist-info"

        monkeypatch.setattr(
            "headerkit.build_backend.prepare_metadata_for_build_wheel",
            mock_prepare,
        )

        from headerkit.build_backend_auto import prepare_metadata_for_build_wheel

        result = prepare_metadata_for_build_wheel("/tmp/meta", None)
        assert result == "pkg-1.0.dist-info"
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "1"

    def test_build_editable_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto.build_editable sets env and delegates."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)

        captured_env: dict[str, str] = {}

        def mock_build_editable(
            _wheel_directory: str,
            _config_settings: dict[str, Any] | None = None,
            _metadata_directory: str | None = None,
        ) -> str:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return "pkg-1.0-py3-none-any.whl"

        monkeypatch.setattr("headerkit.build_backend.build_editable", mock_build_editable)

        from headerkit.build_backend_auto import build_editable

        result = build_editable("/tmp/dist", None, None)
        assert result == "pkg-1.0-py3-none-any.whl"
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "1"

    def test_get_requires_for_build_wheel_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto.get_requires_for_build_wheel sets env and delegates."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)

        captured_env: dict[str, str] = {}

        def mock_get_requires(
            _config_settings: dict[str, Any] | None = None,
        ) -> list[str]:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return ["hatchling>=1.0"]

        monkeypatch.setattr(
            "headerkit.build_backend.get_requires_for_build_wheel",
            mock_get_requires,
        )

        from headerkit.build_backend_auto import get_requires_for_build_wheel

        result = get_requires_for_build_wheel(None)
        assert result == ["hatchling>=1.0"]
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "1"

    def test_get_requires_for_build_sdist_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_backend_auto.get_requires_for_build_sdist sets env and delegates."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)

        captured_env: dict[str, str] = {}

        def mock_get_requires(
            _config_settings: dict[str, Any] | None = None,
        ) -> list[str]:
            captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] = os.environ.get("HEADERKIT_AUTO_INSTALL_LIBCLANG", "")
            return ["setuptools"]

        monkeypatch.setattr(
            "headerkit.build_backend.get_requires_for_build_sdist",
            mock_get_requires,
        )

        from headerkit.build_backend_auto import get_requires_for_build_sdist

        result = get_requires_for_build_sdist(None)
        assert result == ["setuptools"]
        assert captured_env["HEADERKIT_AUTO_INSTALL_LIBCLANG"] == "1"
