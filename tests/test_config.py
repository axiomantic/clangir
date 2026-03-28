"""Tests for headerkit._config — TOML config loading and merging."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import headerkit._config as config_module
from headerkit._config import (
    HeaderkitConfig,
    WriterConfig,
    find_config_file,
    load_config,
    merge_config,
)


class TestFindConfigFile:
    """Tests for find_config_file()."""

    def test_find_config_returns_none_if_absent(self, tmp_path: Path) -> None:
        """Returns None when no config file exists; .git stops the walk."""
        (tmp_path / ".git").mkdir()
        result = find_config_file(start=tmp_path)
        assert result is None

    def test_find_config_finds_headerkit_toml(self, tmp_path: Path) -> None:
        """Finds .headerkit.toml in the start directory."""
        (tmp_path / ".git").mkdir()
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text("[backend]\n")
        result = find_config_file(start=tmp_path)
        assert result == config_file

    def test_find_config_finds_pyproject_with_headerkit_section(self, tmp_path: Path) -> None:
        """Finds pyproject.toml when it contains [tool.headerkit]."""
        (tmp_path / ".git").mkdir()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.headerkit]\nbackend = "libclang"\n')
        result = find_config_file(start=tmp_path)
        assert result == pyproject

    def test_find_config_prefers_headerkit_toml(self, tmp_path: Path) -> None:
        """Prefers .headerkit.toml over pyproject.toml in same directory."""
        (tmp_path / ".git").mkdir()
        headerkit_toml = tmp_path / ".headerkit.toml"
        headerkit_toml.write_text('backend = "libclang"\n')
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.headerkit]\nbackend = "libclang"\n')
        result = find_config_file(start=tmp_path)
        assert result == headerkit_toml

    def test_find_config_stops_at_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Does not walk past the home directory boundary."""
        # Set up: fake_home/above_home/start_dir
        # No config anywhere; stop at fake_home
        fake_home = tmp_path / "fake_home"
        above_home = tmp_path / "above_home"
        start_dir = fake_home / "projects" / "myproject"
        start_dir.mkdir(parents=True)
        above_home.mkdir()

        # Place config above fake_home — should NOT be found
        above_config = above_home / ".headerkit.toml"
        above_config.write_text('backend = "libclang"\n')

        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        result = find_config_file(start=start_dir)
        assert result is None

    def test_find_config_walks_up(self, tmp_path: Path) -> None:
        """Walks up from start directory to find config in parent."""
        (tmp_path / ".git").mkdir()
        child = tmp_path / "src" / "lib"
        child.mkdir(parents=True)
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('backend = "libclang"\n')
        result = find_config_file(start=child)
        assert result == config_file


class TestLoadConfig:
    """Tests for load_config()."""

    def test_load_config_minimal(self, tmp_path: Path) -> None:
        """Loads a minimal config with just backend."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('backend = "libclang"\n')
        cfg = load_config(config_file)
        assert cfg.backend == "libclang"
        assert cfg.writers is None
        assert cfg.include_dirs == []
        assert cfg.defines == []
        assert cfg.backend_args == []
        assert cfg.plugins == []
        assert cfg.writer_options == {}
        assert cfg.source_path == config_file

    def test_load_config_full_fields(self, tmp_path: Path) -> None:
        """Loads a config with all top-level fields set."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text(
            'backend = "libclang"\n'
            'writers = ["cffi", "json"]\n'
            'include_dirs = ["/usr/include", "/opt/include"]\n'
            'defines = ["NDEBUG", "VERSION=2"]\n'
            'backend_args = ["-std=c11"]\n'
            'plugins = ["mypkg.backend"]\n'
        )
        cfg = load_config(config_file)
        assert cfg.backend == "libclang"
        assert cfg.writers == ["cffi", "json"]
        assert cfg.include_dirs == ["/usr/include", "/opt/include"]
        assert cfg.defines == ["NDEBUG", "VERSION=2"]
        assert cfg.backend_args == ["-std=c11"]
        assert cfg.plugins == ["mypkg.backend"]
        assert cfg.source_path == config_file

    def test_load_config_writer_options(self, tmp_path: Path) -> None:
        """Parses [writer.cffi] section into writer_options."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('backend = "libclang"\n\n[writer.cffi]\nexclude_patterns = ["^__", "^_internal"]\n')
        cfg = load_config(config_file)
        assert cfg.backend == "libclang"
        assert "cffi" in cfg.writer_options
        assert cfg.writer_options["cffi"].options == {"exclude_patterns": ["^__", "^_internal"]}

    def test_load_config_invalid_toml(self, tmp_path: Path) -> None:
        """Raises ValueError on malformed TOML."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text("this is not valid toml ===\n")
        with pytest.raises(ValueError, match="config parse error"):
            load_config(config_file)

    def test_load_config_from_pyproject(self, tmp_path: Path) -> None:
        """Loads [tool.headerkit] section from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.headerkit]\nbackend = "libclang"\nwriters = ["cffi"]\n')
        cfg = load_config(pyproject)
        assert cfg.backend == "libclang"
        assert cfg.writers == ["cffi"]
        assert cfg.source_path == pyproject

    def test_load_config_pyproject_missing_section(self, tmp_path: Path) -> None:
        """Returns empty config when pyproject.toml has no [tool.headerkit]."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.other]\nfoo = 1\n")
        cfg = load_config(pyproject)
        assert cfg.backend is None
        assert cfg.writers is None
        assert cfg.include_dirs == []

    def test_load_config_backend_wrong_type(self, tmp_path: Path) -> None:
        """Raises ValueError when backend is not a string."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text("backend = 42\n")
        with pytest.raises(ValueError, match="backend must be str"):
            load_config(config_file)

    def test_load_config_writers_wrong_type(self, tmp_path: Path) -> None:
        """Raises ValueError when writers is not a list of strings."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('writers = "cffi"\n')
        with pytest.raises(ValueError, match="writers must be list"):
            load_config(config_file)

    def test_load_config_writers_with_path_syntax(self, tmp_path: Path) -> None:
        """Raises ValueError when writers contain :path syntax."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('writers = ["cffi:output.h"]\n')
        with pytest.raises(ValueError, match="no :path syntax"):
            load_config(config_file)

    def test_load_config_no_tomllib(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises ValueError with install hint when tomllib is unavailable."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text('backend = "libclang"\n')
        monkeypatch.setattr(config_module, "tomllib", None)
        with pytest.raises(ValueError, match="pip install tomli"):
            load_config(config_file)


class TestMergeConfig:
    """Tests for merge_config()."""

    def _make_args(self, **kwargs: object) -> argparse.Namespace:
        """Build a minimal Namespace simulating post-parse_args() state."""
        defaults = {
            "backend": None,
            "include_dirs": [],
            "defines": [],
            "backend_args": [],
            "writers": [],
            "plugins": [],
            "writer_opts": {},
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_merge_config_applies_defaults(self) -> None:
        """Config values are applied when CLI fields are unset/empty."""
        cfg = HeaderkitConfig(
            backend="libclang",
            include_dirs=["/opt/include"],
            defines=["NDEBUG"],
            backend_args=["-std=c11"],
            writers=["cffi"],
            plugins=["mypkg.plugin"],
        )
        args = self._make_args()
        merge_config(cfg, args)
        assert args.backend == "libclang"
        assert args.include_dirs == ["/opt/include"]
        assert args.defines == ["NDEBUG"]
        assert args.backend_args == ["-std=c11"]
        assert args.writers == ["cffi"]
        assert args.plugins == ["mypkg.plugin"]

    def test_merge_config_cli_overrides_config(self) -> None:
        """CLI values are preserved; config backend does not override set CLI backend."""
        cfg = HeaderkitConfig(backend="libclang")
        args = self._make_args(backend="custom-backend")
        merge_config(cfg, args)
        assert args.backend == "custom-backend"

    def test_merge_config_plugins_deduplicated(self) -> None:
        """Plugins present in both CLI and config are not duplicated."""
        cfg = HeaderkitConfig(plugins=["mypkg.plugin", "other.plugin"])
        args = self._make_args(plugins=["mypkg.plugin"])
        merge_config(cfg, args)
        # mypkg.plugin from CLI stays; other.plugin added; no duplicate mypkg.plugin
        assert args.plugins == ["mypkg.plugin", "other.plugin"]

    def test_merge_no_config_returns_unchanged(self) -> None:
        """When config is None, args namespace is returned unchanged."""
        args = self._make_args(backend="existing-backend")
        result = merge_config(None, args)
        assert result.backend == "existing-backend"
        assert result is args

    def test_merge_config_include_dirs_prepended(self) -> None:
        """Config include_dirs are prepended before CLI -I dirs."""
        cfg = HeaderkitConfig(include_dirs=["/config/include"])
        args = self._make_args(include_dirs=["/cli/include"])
        merge_config(cfg, args)
        assert args.include_dirs == ["/config/include", "/cli/include"]

    def test_merge_config_defines_prepended(self) -> None:
        """Config defines are prepended before CLI -D defines."""
        cfg = HeaderkitConfig(defines=["CONFIG_DEFINE"])
        args = self._make_args(defines=["CLI_DEFINE"])
        merge_config(cfg, args)
        assert args.defines == ["CONFIG_DEFINE", "CLI_DEFINE"]

    def test_merge_config_writers_from_config(self) -> None:
        """Config writers are used when no CLI -w was given."""
        cfg = HeaderkitConfig(writers=["json"])
        args = self._make_args(writers=[])
        merge_config(cfg, args)
        assert args.writers == ["json"]

    def test_merge_config_cli_writers_override(self) -> None:
        """CLI -w writers take precedence over config writers."""
        cfg = HeaderkitConfig(writers=["json"])
        args = self._make_args(writers=["cffi"])
        merge_config(cfg, args)
        assert args.writers == ["cffi"]

    def test_merge_config_writer_opts_applied(self) -> None:
        """Config writer options are merged into args.writer_opts."""
        cfg = HeaderkitConfig(
            writer_options={
                "cffi": WriterConfig(options={"exclude_patterns": ["^__", "^_internal"]}),
            }
        )
        args = self._make_args()
        merge_config(cfg, args)
        assert args.writer_opts == {
            "cffi": {"exclude_patterns": ["^__", "^_internal"]},
        }

    def test_merge_config_cli_writer_opts_win(self) -> None:
        """Per-key CLI writer opts take precedence over config writer opts."""
        cfg = HeaderkitConfig(
            writer_options={
                "cffi": WriterConfig(options={"exclude_patterns": ["^config_pattern"]}),
            }
        )
        args = self._make_args(writer_opts={"cffi": {"exclude_patterns": ["^cli_pattern"]}})
        merge_config(cfg, args)
        # CLI value wins for the key that exists in CLI
        assert args.writer_opts == {
            "cffi": {"exclude_patterns": ["^cli_pattern"]},
        }


# =============================================================================
# TestCacheConfig
# =============================================================================


class TestCacheConfig:
    """Tests for cache-related fields in HeaderkitConfig."""

    def test_cache_config_defaults(self) -> None:
        """HeaderkitConfig cache fields default to False/None."""
        cfg = HeaderkitConfig()
        assert cfg.cache_dir is None
        assert cfg.no_cache is False
        assert cfg.no_ir_cache is False
        assert cfg.no_output_cache is False

    def test_load_cache_section_from_headerkit_toml(self, tmp_path: Path) -> None:
        """Parses [cache] section from .headerkit.toml."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text(
            'backend = "libclang"\n'
            "\n"
            "[cache]\n"
            'cache_dir = "/tmp/hkcache"\n'
            "no_cache = true\n"
            "no_ir_cache = true\n"
            "no_output_cache = true\n"
        )
        cfg = load_config(config_file)
        assert cfg.backend == "libclang"
        assert cfg.cache_dir == "/tmp/hkcache"
        assert cfg.no_cache is True
        assert cfg.no_ir_cache is True
        assert cfg.no_output_cache is True

    def test_load_cache_section_from_pyproject(self, tmp_path: Path) -> None:
        """Parses [tool.headerkit.cache] section from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit]\n"
            'backend = "libclang"\n'
            "\n"
            "[tool.headerkit.cache]\n"
            'cache_dir = "/custom/cache"\n'
            "no_cache = false\n"
            "no_ir_cache = true\n"
            "no_output_cache = false\n"
        )
        cfg = load_config(pyproject)
        assert cfg.backend == "libclang"
        assert cfg.cache_dir == "/custom/cache"
        assert cfg.no_cache is False
        assert cfg.no_ir_cache is True
        assert cfg.no_output_cache is False

    def test_load_cache_section_partial(self, tmp_path: Path) -> None:
        """Partial [cache] section leaves unset fields at defaults."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text("[cache]\nno_cache = true\n")
        cfg = load_config(config_file)
        assert cfg.no_cache is True
        assert cfg.no_ir_cache is False
        assert cfg.no_output_cache is False
        assert cfg.cache_dir is None

    def test_load_cache_section_wrong_type(self, tmp_path: Path) -> None:
        """Raises ValueError when cache_dir is not a string."""
        config_file = tmp_path / ".headerkit.toml"
        config_file.write_text("[cache]\ncache_dir = 42\n")
        with pytest.raises(ValueError, match="cache.cache_dir must be str"):
            load_config(config_file)
