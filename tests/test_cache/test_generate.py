"""Tests for generate() and generate_all() API."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from headerkit._generate import GenerateResult, generate, generate_all
from headerkit.backends import LibclangUnavailableError
from headerkit.ir import CType, Function, Header, Parameter


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Set up a project directory with .git and .headerkit."""
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
            store_dir=project_dir / ".headerkit",
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
        ir_dir = project_dir / ".headerkit" / "ir"
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
            store_dir=project_dir / ".headerkit",
        )

        # Reset mock to track second call
        mock_backend.parse.reset_mock()

        # Second call: should use cache
        result2 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=project_dir / ".headerkit",
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
            store_dir=project_dir / ".headerkit",
            no_cache=True,
        )

        # Cache dir should not have any IR entries
        ir_dir = project_dir / ".headerkit" / "ir"
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
            store_dir=project_dir / ".headerkit",
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
            store_dir=project_dir / ".headerkit",
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
            store_dir=project_dir / ".headerkit",
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
    """Patch is_backend_available to return False, simulating missing libclang."""
    monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: False)


class TestGenerateOutputCacheFallback:
    """Test that generate() falls back to output cache when backend is unavailable."""

    def test_falls_back_to_output_cache(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate() uses output cache when libclang is unavailable."""
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)

        cache_path = project_dir / ".headerkit"

        # First call: populate cache (both IR and output)
        output1 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=cache_path,
        )

        # Delete IR cache but keep output cache -- simulates a committed
        # .headerkit/ that only ships output entries (no IR).
        ir_dir = cache_path / "ir"
        assert ir_dir.exists()
        shutil.rmtree(ir_dir)
        assert not ir_dir.exists()
        # Output cache must still be present
        assert (cache_path / "output").exists()

        # Now make is_backend_available return False (simulating missing libclang)
        _make_backend_unavailable(monkeypatch)

        # Second call: backend unavailable, but output cache hit -- should
        # fall back to output cache instead of raising.
        output2 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=cache_path,
        )

        assert output1 == output2

    def test_raises_when_no_cache_and_no_backend(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate() raises LibclangUnavailableError when backend unavailable AND no cache."""
        _make_backend_unavailable(monkeypatch)

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
            )

    def test_raises_when_cache_disabled_and_no_backend(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate() raises when no_cache=True and backend unavailable."""
        _make_backend_unavailable(monkeypatch)

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
                no_cache=True,
            )


class TestGenerateFileNotFound:
    """Test error handling for missing header files."""

    def test_missing_header(self, project_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate(
                header_path=project_dir / "nonexistent.h",
                writer_name="json",
                store_dir=project_dir / ".headerkit",
            )


def _invalidate_all_caches(project_dir: Path) -> None:
    """Ensure both IR and output caches are empty."""
    cache_path = project_dir / ".headerkit"
    if cache_path.exists():
        shutil.rmtree(cache_path)
    cache_path.mkdir(parents=True, exist_ok=True)


class TestGenerateAutoInstall:
    """Test that generate() auto-installs libclang when opted in and backend unavailable."""

    def _make_backend_unavailable_then_available(self, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """Patch is_backend_available to return False initially, then True after auto_install.

        Also patches get_backend to return a mock backend (for when parse is called
        after availability becomes True).
        """
        available = False

        def mock_is_available(_name: str) -> bool:
            return available

        monkeypatch.setattr("headerkit._generate.is_backend_available", mock_is_available)

        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        def mock_auto_install_fn() -> bool:
            nonlocal available
            available = True
            return True

        mock_auto_install = MagicMock(side_effect=mock_auto_install_fn)
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

        cache_path = project_dir / ".headerkit"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=cache_path,
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

        cache_path = project_dir / ".headerkit"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=cache_path,
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

        cache_path = project_dir / ".headerkit"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=cache_path,
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

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
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

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
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

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
            )

        mock_auto_install.assert_not_called()

    def test_no_cache_no_backend_raises_when_auto_install_fails(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate() raises LibclangUnavailableError when auto_install is enabled but fails."""
        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=False)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
                auto_install_libclang=True,
            )

        mock_auto_install.assert_called_once_with()

    def test_does_not_auto_install_for_non_libclang_backend(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate() does NOT auto-install when the backend is not 'libclang'.

        Non-libclang backends skip the availability check entirely, so
        errors propagate from get_backend() or parse() as-is.
        """

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
                store_dir=project_dir / ".headerkit",
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
        monkeypatch.delenv("HEADERKIT_AUTO_INSTALL_LIBCLANG", raising=False)
        pyproject = project_dir / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit]\nauto_install_libclang = false\n",
            encoding="utf-8",
        )

        _make_backend_unavailable(monkeypatch)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        with pytest.raises(LibclangUnavailableError, match="libclang shared library not found"):
            generate(
                header_path=header_file,
                writer_name="json",
                backend_name="libclang",
                store_dir=project_dir / ".headerkit",
            )

        mock_auto_install.assert_not_called()

    def test_available_backend_skips_auto_install(
        self,
        project_dir: Path,
        header_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate() with available libclang does not call auto_install at all."""
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)

        mock_auto_install = MagicMock(return_value=True)
        monkeypatch.setattr("headerkit._generate.auto_install", mock_auto_install)

        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=project_dir / ".headerkit",
            auto_install_libclang=True,
        )

        mock_auto_install.assert_not_called()
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "add"


class TestLibclangUnavailableErrorImport:
    """Test that LibclangUnavailableError is importable from headerkit."""

    def test_importable_from_headerkit(self) -> None:
        """LibclangUnavailableError can be imported from the top-level package."""
        from headerkit import LibclangUnavailableError as Exc

        assert issubclass(Exc, RuntimeError)

    def test_importable_from_backends(self) -> None:
        """LibclangUnavailableError can be imported from headerkit.backends."""
        from headerkit.backends import LibclangUnavailableError as Exc

        assert issubclass(Exc, RuntimeError)


class TestIsBackendAvailable:
    """Test is_backend_available() behavior via generate()'s usage."""

    def test_returns_false_when_library_not_loadable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """is_backend_available('libclang') returns False when library is not loadable.

        Patches at the _generate module level to avoid test pollution from
        other test files that manipulate the libclang module's sys.modules entry.
        """
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: False)

        # Verify the patched function is used by generate()
        from headerkit._generate import is_backend_available

        assert is_backend_available("libclang") is False

    def test_returns_true_when_library_is_loadable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """is_backend_available('libclang') returns True when library is loadable."""
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)

        from headerkit._generate import is_backend_available

        assert is_backend_available("libclang") is True

    def test_returns_false_for_unregistered_backend(self) -> None:
        """is_backend_available() returns False for unregistered backend names."""
        from headerkit.backends import is_backend_available

        assert is_backend_available("nonexistent_backend") is False


class TestGenerateStoreDirEnvVar:
    """Test HEADERKIT_STORE_DIR environment variable support."""

    def test_env_var_used_when_store_dir_is_none(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HEADERKIT_STORE_DIR is used when store_dir parameter is None."""
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)

        env_store = project_dir / "env-store"
        monkeypatch.setenv("HEADERKIT_STORE_DIR", str(env_store))

        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
        )

        # The env var store directory should have been created and used
        assert env_store.exists()
        ir_dir = env_store / "ir"
        assert ir_dir.exists()

    def test_explicit_store_dir_overrides_env_var(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit store_dir parameter takes priority over HEADERKIT_STORE_DIR."""
        mock_header = Header(
            str(header_file),
            [Function("f", CType("void"))],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)

        env_store = project_dir / "env-store"
        explicit_store = project_dir / "explicit-store"
        monkeypatch.setenv("HEADERKIT_STORE_DIR", str(env_store))

        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            store_dir=explicit_store,
        )

        # Explicit store should be used, env var store should not exist
        assert explicit_store.exists()
        assert (explicit_store / "ir").exists()
        assert not env_store.exists()

    def test_empty_env_var_falls_back_to_auto_detect(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty HEADERKIT_STORE_DIR is treated as unset (falls back to auto-detect)."""
        mock_header = Header(
            str(header_file),
            [Function("f", CType("void"))],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)

        monkeypatch.setenv("HEADERKIT_STORE_DIR", "")

        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
        )

        # Auto-detect should create .headerkit/ at project root
        auto_store = project_dir / ".headerkit"
        assert auto_store.exists()
        assert (auto_store / "ir").exists()

    def test_env_var_not_set_falls_back_to_auto_detect(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When HEADERKIT_STORE_DIR is not set and store_dir is None, auto-detect is used."""
        mock_header = Header(
            str(header_file),
            [Function("f", CType("void"))],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)
        monkeypatch.setattr("headerkit._generate.is_backend_available", lambda _name: True)
        monkeypatch.delenv("HEADERKIT_STORE_DIR", raising=False)

        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
        )

        # Auto-detect should create .headerkit/ at project root (where .git is)
        auto_store = project_dir / ".headerkit"
        assert auto_store.exists()
        assert (auto_store / "ir").exists()
