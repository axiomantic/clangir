"""Tests for generate() and generate_all() API."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from headerkit._generate import GenerateResult, generate, generate_all
from headerkit.ir import CType, Function, Header, Parameter


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Set up a project directory with .git and .hkcache."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture()
def header_file(project_dir: Path) -> Path:
    """Create a simple header file."""
    h = project_dir / "test.h"
    h.write_text("int add(int a, int b);")
    return h


class TestGenerateIRCacheMiss:
    """Test generate() when IR cache is empty (requires mock backend)."""

    def test_populates_ir_cache(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mock get_backend to avoid needing libclang
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        # Verify JSON output contains expected function declaration
        parsed = json.loads(result)
        assert len(parsed["declarations"]) == 1
        decl = parsed["declarations"][0]
        assert decl["kind"] == "function"
        assert decl["name"] == "add"
        assert len(decl["parameters"]) == 2
        assert decl["parameters"][0]["name"] == "a"
        assert decl["parameters"][1]["name"] == "b"

        # IR cache should now exist
        ir_dir = project_dir / ".hkcache" / "ir"
        assert ir_dir.exists()
        entries = [d for d in ir_dir.iterdir() if d.is_dir()]
        assert len(entries) == 1


class TestGenerateIRCacheHit:
    """Test generate() when IR cache already has the entry."""

    def test_uses_cached_ir(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        # First call: populate cache
        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        # Reset mock to track second call
        mock_backend.parse.reset_mock()

        # Second call: should use cache
        result2 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        # Verify cached result matches expected output
        parsed = json.loads(result2)
        assert len(parsed["declarations"]) == 1
        assert parsed["declarations"][0]["name"] == "add"
        assert len(parsed["declarations"][0]["parameters"]) == 1
        mock_backend.parse.assert_not_called()  # should not parse again


class TestGenerateNoCache:
    """Test cache bypass flags."""

    def test_no_cache_skips_all(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(str(header_file), [])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
            no_cache=True,
        )

        # Cache dir should not have any IR entries
        ir_dir = project_dir / ".hkcache" / "ir"
        assert not ir_dir.exists() or not any(d.is_dir() for d in ir_dir.iterdir())


class TestGenerateAll:
    """Test generate_all() with multiple writers."""

    def test_multiple_writers(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(str(header_file), [Function("f", CType("void"))])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        results = generate_all(
            header_path=header_file,
            writers=["json"],
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        assert len(results) == 1
        assert isinstance(results[0], GenerateResult)
        assert results[0].writer_name == "json"
        parsed = json.loads(results[0].output)
        assert parsed["declarations"][0]["name"] == "f"

    def test_parses_only_once(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(str(header_file), [Function("f", CType("void"))])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        generate_all(
            header_path=header_file,
            writers=["json"],
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        assert mock_backend.parse.call_count == 1


class TestGenerateOutputPath:
    """Test generate() with output_path parameter."""

    def test_writes_file_and_returns_output(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_header = Header(str(header_file), [Function("f", CType("void"))])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        out_file = project_dir / "output" / "bindings.json"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
            output_path=out_file,
        )

        # Returns the output string with expected content
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "f"
        assert parsed["declarations"][0]["kind"] == "function"
        # AND writes the file
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == result


def _make_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_backend to raise ValueError, simulating missing libclang."""

    def raise_no_backend(name: str) -> None:
        raise ValueError(f"Unknown backend: {name!r}. Available: (none)")

    monkeypatch.setattr("headerkit._generate.get_backend", raise_no_backend)


class TestGenerateOutputCacheFallback:
    """Test that generate() falls back to output cache when backend is unavailable."""

    def test_falls_back_to_output_cache(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate() uses output cache when backend raises ValueError."""
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        cache_path = project_dir / ".hkcache"

        # First call: populate cache (both IR and output)
        output1 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=cache_path,
        )

        # Delete IR cache but keep output cache -- simulates a committed
        # .hkcache/ that only ships output entries (no IR).
        ir_dir = cache_path / "ir"
        assert ir_dir.exists()
        shutil.rmtree(ir_dir)
        assert not ir_dir.exists()
        # Output cache must still be present
        assert (cache_path / "output").exists()

        # Now make get_backend raise ValueError (simulating missing libclang)
        _make_backend_unavailable(monkeypatch)

        # Second call: IR cache misses, backend unavailable -- should
        # fall back to output cache instead of raising.
        output2 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=cache_path,
        )

        assert output1 == output2

    def test_raises_when_no_cache_and_no_backend(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate() raises ValueError when backend unavailable AND no cache."""
        _make_backend_unavailable(monkeypatch)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
            )

    def test_raises_when_cache_disabled_and_no_backend(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate() raises when no_cache=True and backend unavailable."""
        _make_backend_unavailable(monkeypatch)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
                no_cache=True,
            )


class TestGenerateFileNotFound:
    """Test error handling for missing header files."""

    def test_missing_header(self, project_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate(
                header_path=project_dir / "nonexistent.h",
                writer_name="json",
                cache_dir=project_dir / ".hkcache",
            )


def _invalidate_all_caches(project_dir: Path) -> None:
    """Ensure both IR and output caches are empty."""
    cache_path = project_dir / ".hkcache"
    if cache_path.exists():
        shutil.rmtree(cache_path)
    cache_path.mkdir(parents=True, exist_ok=True)


class TestGenerateAutoInstall:
    """Test that generate() auto-installs libclang when opted in and backend unavailable."""

    def _make_backend_unavailable_then_available(self, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """Patch get_backend to fail first, then succeed after auto_install."""
        call_count = 0

        def mock_get_backend(name: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError(f"Unknown backend: {name!r}. Available: (none)")
            mock_header = Header(
                str(header_file),
                [Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])],
            )
            backend = MagicMock()
            backend.parse.return_value = mock_header
            return backend

        monkeypatch.setattr("headerkit._generate.get_backend", mock_get_backend)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)
        return mock_auto_install

    def test_auto_installs_via_kwarg(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate(auto_install_libclang=True) enables auto-install."""
        _invalidate_all_caches(project_dir)
        mock_auto_install = self._make_backend_unavailable_then_available(header_file, monkeypatch)

        cache_path = project_dir / ".hkcache"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=cache_path,
            auto_install_libclang=True,
        )

        mock_auto_install.assert_called_once_with()
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "add"
        assert parsed["declarations"][0]["kind"] == "function"

    def test_auto_installs_via_env_var(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HEADERKIT_AUTO_INSTALL_LIBCLANG=1 enables auto-install."""
        _invalidate_all_caches(project_dir)
        monkeypatch.setenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", "1")
        mock_auto_install = self._make_backend_unavailable_then_available(header_file, monkeypatch)

        cache_path = project_dir / ".hkcache"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=cache_path,
        )

        mock_auto_install.assert_called_once_with()
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "add"

    def test_auto_installs_via_config(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auto_install_libclang=true in pyproject.toml enables auto-install."""
        _invalidate_all_caches(project_dir)
        pyproject = project_dir / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit]\nauto_install_libclang = true\n",
            encoding="utf-8",
        )
        mock_auto_install = self._make_backend_unavailable_then_available(header_file, monkeypatch)

        cache_path = project_dir / ".hkcache"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=cache_path,
        )

        mock_auto_install.assert_called_once_with()
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "add"

    def test_default_does_not_auto_install(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default behavior (no kwarg, no env, no config) does NOT auto-install."""
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)
        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
            )

        mock_auto_install.assert_not_called()

    def test_kwarg_false_overrides_env_var(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate(auto_install_libclang=False) disables even when env var is set."""
        monkeypatch.setenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", "1")
        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
                auto_install_libclang=False,
            )

        mock_auto_install.assert_not_called()

    def test_env_var_overrides_config(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HEADERKIT_AUTO_INSTALL_LIBCLANG=0 disables even when config says true."""
        pyproject = project_dir / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit]\nauto_install_libclang = true\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", "0")
        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
            )

        mock_auto_install.assert_not_called()

    def test_no_cache_no_backend_raises_when_auto_install_fails(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate() raises ValueError when auto_install is enabled but fails."""
        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=False)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
                auto_install_libclang=True,
            )

        mock_auto_install.assert_called_once_with()

    def test_does_not_auto_install_for_non_libclang_backend(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate() does NOT auto-install when the failing backend is not 'libclang'."""

        def raise_no_backend(name: str) -> None:
            raise ValueError(f"Unknown backend: {name!r}. Available: (none)")

        monkeypatch.setattr("headerkit._generate.get_backend", raise_no_backend)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="custom_backend",
                cache_dir=project_dir / ".hkcache",
                auto_install_libclang=True,
            )

        mock_auto_install.assert_not_called()

    def test_config_opt_out_disables_auto_install(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auto_install_libclang=false in config disables auto-install."""
        pyproject = project_dir / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit]\nauto_install_libclang = false\n",
            encoding="utf-8",
        )

        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(ValueError, match="Unknown backend"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                cache_dir=project_dir / ".hkcache",
            )

        mock_auto_install.assert_not_called()
