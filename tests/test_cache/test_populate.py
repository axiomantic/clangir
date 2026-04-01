"""Tests for cache populate module."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPopulateTarget:
    """Tests for PopulateTarget dataclass."""

    def test_create_target(self) -> None:
        """PopulateTarget holds platform, python, image, and path fields."""
        from headerkit._populate import PopulateTarget

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
        )
        assert target.docker_platform == "linux/amd64"
        assert target.python_version == "3.12"
        assert target.docker_image == "quay.io/pypa/manylinux_2_28_x86_64"
        assert target.python_path == "/opt/python/cp312-cp312/bin/python"
        assert target.target_triple == ""

    def test_target_with_target_triple(self) -> None:
        """PopulateTarget target_triple field can be set."""
        from headerkit._populate import PopulateTarget

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
            target_triple="x86_64-unknown-linux-gnu",
        )
        assert target.target_triple == "x86_64-unknown-linux-gnu"


class TestPopulateEntryResult:
    """Tests for PopulateEntryResult dataclass."""

    def test_create_success_result(self) -> None:
        """PopulateEntryResult records success with cache keys."""
        from headerkit._populate import PopulateEntryResult, PopulateTarget

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
        )
        result = PopulateEntryResult(
            target=target,
            writer_name="cffi",
            success=True,
            # ir_cache_key and output_cache_key are intentionally empty strings
            # in v1 -- the container writes cache entries directly and we don't
            # parse them back out.
            ir_cache_key="abc123",
            ir_slug="libclang.test",
            output_cache_key="def456",
            output_slug="libclang.test",
        )
        assert result.success is True
        assert result.error == ""
        assert result.skipped is False

    def test_create_failure_result(self) -> None:
        """PopulateEntryResult records failure with error message."""
        from headerkit._populate import PopulateEntryResult, PopulateTarget

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
        )
        result = PopulateEntryResult(
            target=target,
            writer_name="cffi",
            success=False,
            error="Docker timed out",
        )
        assert result.success is False
        assert result.error == "Docker timed out"


class TestPopulateResult:
    """Tests for PopulateResult aggregate dataclass."""

    def test_empty_result(self) -> None:
        """Empty PopulateResult has zero counts."""
        from headerkit._populate import PopulateResult

        result = PopulateResult()
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.skipped_count == 0
        assert result.total == 0

    def test_mixed_results(self) -> None:
        """PopulateResult correctly counts success/fail/skip."""
        from headerkit._populate import (
            PopulateEntryResult,
            PopulateResult,
            PopulateTarget,
        )

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
        )
        result = PopulateResult(
            entries=[
                PopulateEntryResult(
                    target=target,
                    writer_name="cffi",
                    success=True,
                ),
                PopulateEntryResult(
                    target=target,
                    writer_name="json",
                    success=False,
                    error="fail",
                ),
                PopulateEntryResult(
                    target=target,
                    writer_name="ctypes",
                    success=False,
                    skipped=True,
                ),
            ],
        )
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.skipped_count == 1
        assert result.total == 3


class TestPlatformConstants:
    """Tests for platform mapping constants."""

    def test_default_images(self) -> None:
        """DEFAULT_IMAGES maps docker platforms to manylinux images."""
        from headerkit._populate import DEFAULT_IMAGES

        assert DEFAULT_IMAGES == {
            "linux/amd64": "quay.io/pypa/manylinux_2_28_x86_64",
            "linux/arm64": "quay.io/pypa/manylinux_2_28_aarch64",
            "linux/386": "quay.io/pypa/manylinux_2_28_i686",
        }

    def test_platform_mapping(self) -> None:
        """PLATFORM_MAP maps docker platforms to target triples."""
        from headerkit._populate import PLATFORM_MAP

        assert PLATFORM_MAP == {
            "linux/amd64": "x86_64-unknown-linux-gnu",
            "linux/arm64": "aarch64-unknown-linux-gnu",
            "linux/386": "i686-unknown-linux-gnu",
            "linux/arm/v7": "armv7-unknown-linux-gnueabihf",
        }

    def test_default_python_versions(self) -> None:
        """DEFAULT_PYTHON_VERSIONS lists supported CPython versions."""
        from headerkit._populate import DEFAULT_PYTHON_VERSIONS

        assert DEFAULT_PYTHON_VERSIONS == ["3.10", "3.11", "3.12", "3.13", "3.14"]

    def test_python_path_for_version(self) -> None:
        """python_path_for_version returns correct manylinux path."""
        from headerkit._populate import python_path_for_version

        assert python_path_for_version("3.12") == "/opt/python/cp312-cp312/bin/python"
        assert python_path_for_version("3.10") == "/opt/python/cp310-cp310/bin/python"
        assert python_path_for_version("3.14") == "/opt/python/cp314-cp314/bin/python"


class TestBuildTargets:
    """Tests for build_targets() target resolution."""

    def test_explicit_platforms_and_versions(self) -> None:
        """Explicit platforms and python versions produce correct targets."""
        from headerkit._populate import build_targets

        targets, warnings = build_targets(
            platforms=["linux/amd64", "linux/arm64"],
            python_versions=["3.12", "3.13"],
        )
        combos = sorted((t.docker_platform, t.python_version) for t in targets)
        assert combos == [
            ("linux/amd64", "3.12"),
            ("linux/amd64", "3.13"),
            ("linux/arm64", "3.12"),
            ("linux/arm64", "3.13"),
        ]

    def test_default_python_versions(self) -> None:
        """When python_versions is None, all defaults are used."""
        from headerkit._populate import DEFAULT_PYTHON_VERSIONS, build_targets

        targets, _ = build_targets(platforms=["linux/amd64"])
        versions = [t.python_version for t in targets]
        assert versions == DEFAULT_PYTHON_VERSIONS

    def test_custom_docker_image_overrides_default(self) -> None:
        """docker_image param overrides the default image for all platforms."""
        from headerkit._populate import build_targets

        targets, _ = build_targets(
            platforms=["linux/amd64", "linux/arm64"],
            python_versions=["3.12"],
            docker_image="custom/image:latest",
        )
        assert [t.docker_image for t in targets] == [
            "custom/image:latest",
            "custom/image:latest",
        ]

    def test_config_images_override_defaults(self) -> None:
        """Per-platform config images override defaults."""
        from headerkit._populate import build_targets

        targets, _ = build_targets(
            platforms=["linux/amd64"],
            python_versions=["3.12"],
            config_images={"linux/amd64": "my-registry/manylinux:custom"},
        )
        assert targets[0].docker_image == "my-registry/manylinux:custom"

    def test_docker_image_overrides_config_images(self) -> None:
        """CLI docker_image takes precedence over config_images."""
        from headerkit._populate import build_targets

        targets, _ = build_targets(
            platforms=["linux/amd64"],
            python_versions=["3.12"],
            docker_image="cli-override:latest",
            config_images={"linux/amd64": "config-image:v1"},
        )
        assert targets[0].docker_image == "cli-override:latest"

    def test_target_computed_fields_populated(self) -> None:
        """Targets have target_triple populated."""
        from headerkit._populate import build_targets

        targets, _ = build_targets(
            platforms=["linux/amd64"],
            python_versions=["3.12"],
        )
        t = targets[0]
        assert t.target_triple == "x86_64-unknown-linux-gnu"
        assert t.python_path == "/opt/python/cp312-cp312/bin/python"

    def test_macos_platform_warns_and_skips(self) -> None:
        """macOS platforms produce a warning and are skipped."""
        from headerkit._populate import build_targets

        targets, warnings = build_targets(
            platforms=["linux/amd64", "macos/arm64"],
            python_versions=["3.12"],
        )
        assert len(targets) == 1
        assert targets[0].docker_platform == "linux/amd64"
        assert any("macos/arm64" in w for w in warnings)

    def test_windows_platform_warns_and_skips(self) -> None:
        """Windows platforms produce a warning and are skipped."""
        from headerkit._populate import build_targets

        targets, warnings = build_targets(
            platforms=["windows/amd64"],
            python_versions=["3.12"],
        )
        assert len(targets) == 0
        assert any("windows/amd64" in w for w in warnings)

    def test_unknown_linux_platform_no_default_image_errors(self) -> None:
        """linux/arm/v7 with no docker_image or config_images raises ValueError."""
        from headerkit._populate import build_targets

        with pytest.raises(ValueError, match="No default Docker image"):
            build_targets(
                platforms=["linux/arm/v7"],
                python_versions=["3.12"],
            )

    def test_unknown_linux_platform_with_docker_image(self) -> None:
        """linux/arm/v7 works when docker_image is provided."""
        from headerkit._populate import build_targets

        targets, _ = build_targets(
            platforms=["linux/arm/v7"],
            python_versions=["3.12"],
            docker_image="custom/armv7:latest",
        )
        assert len(targets) == 1
        assert targets[0].docker_platform == "linux/arm/v7"
        assert targets[0].target_triple == "armv7-unknown-linux-gnueabihf"

    def test_pypy_version_rejected(self) -> None:
        """PyPy version strings are rejected with an error."""
        from headerkit._populate import build_targets

        with pytest.raises(ValueError, match="PyPy.*not supported"):
            build_targets(
                platforms=["linux/amd64"],
                python_versions=["pp310"],
            )


class TestParseCibuildwheelConfig:
    """Tests for parse_cibuildwheel_config()."""

    def test_default_build_all_cpython(self, tmp_path: Path) -> None:
        """Default cibuildwheel config targets all CPython versions."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.cibuildwheel]\n",
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        platforms, python_versions, warnings = parse_cibuildwheel_config(
            pyproject,
        )
        assert sorted(platforms) == ["linux/386", "linux/amd64", "linux/arm64"]
        assert python_versions == ["3.10", "3.11", "3.12", "3.13", "3.14"]

    def test_build_restricts_versions(self, tmp_path: Path) -> None:
        """build = "cp312-* cp313-*" restricts to 3.12 and 3.13."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-* cp313-*"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        _, python_versions, _ = parse_cibuildwheel_config(pyproject)
        assert python_versions == ["3.12", "3.13"]

    def test_skip_excludes_versions(self, tmp_path: Path) -> None:
        """skip = "cp310-*" excludes 3.10."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp3*"\nskip = "cp310-*"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        _, python_versions, _ = parse_cibuildwheel_config(pyproject)
        assert python_versions == ["3.11", "3.12", "3.13", "3.14"]

    def test_skip_excludes_platform(self, tmp_path: Path) -> None:
        """skip = "*-manylinux_x86_64" excludes linux/amd64."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-*"\nskip = "*-manylinux_x86_64"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        platforms, _, _ = parse_cibuildwheel_config(pyproject)
        assert sorted(platforms) == ["linux/386", "linux/arm64"]

    def test_pypy_in_build_emits_warning(self, tmp_path: Path) -> None:
        """PyPy in build field emits warning and is excluded."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-* pp310-*"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        _, python_versions, warnings = parse_cibuildwheel_config(pyproject)
        assert python_versions == ["3.12"]
        assert any("PyPy" in w for w in warnings)

    def test_overrides_emits_warning(self, tmp_path: Path) -> None:
        """Presence of overrides section emits warning."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\n[[tool.cibuildwheel.overrides]]\nselect = "cp312-*"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        _, _, warnings = parse_cibuildwheel_config(pyproject)
        assert any("overrides" in w.lower() for w in warnings)

    def test_macos_windows_in_config_warns(self, tmp_path: Path) -> None:
        """macOS/Windows targets in config emit warnings."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-*"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        _, _, warnings = parse_cibuildwheel_config(pyproject)
        assert any("macOS" in w or "Windows" in w for w in warnings)

    def test_no_cibuildwheel_section_raises(self, tmp_path: Path) -> None:
        """Missing cibuildwheel section raises ValueError."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\n',
            encoding="utf-8",
        )
        from headerkit._populate import parse_cibuildwheel_config

        with pytest.raises(ValueError, match="cibuildwheel"):
            parse_cibuildwheel_config(pyproject)


class TestDockerHelpers:
    """Tests for Docker interaction functions."""

    def test_check_docker_available_success(self) -> None:
        """check_docker_available passes when docker info succeeds."""
        from headerkit._populate import check_docker_available

        with patch("headerkit._populate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Should not raise
            check_docker_available()
            mock_run.assert_called_once_with(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_check_docker_not_installed(self) -> None:
        """check_docker_available raises when docker not found."""
        from headerkit._populate import check_docker_available

        with (
            patch(
                "headerkit._populate.subprocess.run",
                side_effect=FileNotFoundError,
            ),
            pytest.raises(RuntimeError, match="Docker is not installed"),
        ):
            check_docker_available()

    def test_check_docker_daemon_not_running(self) -> None:
        """check_docker_available raises when daemon not running."""
        from headerkit._populate import check_docker_available

        with patch("headerkit._populate.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="daemon not running")
            with pytest.raises(RuntimeError, match="Docker daemon is not running"):
                check_docker_available()

    def test_find_headerkit_source_from_editable(self, tmp_path: Path) -> None:
        """find_headerkit_source finds source root from editable install."""
        from headerkit._populate import _find_headerkit_source

        # Create a fake headerkit package structure
        pkg_dir = tmp_path / "headerkit"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "headerkit"\n',
            encoding="utf-8",
        )

        import headerkit as _hk

        with patch.object(_hk, "__file__", str(pkg_dir / "__init__.py")):
            result = _find_headerkit_source()
            assert result == tmp_path

    def test_build_docker_command_basic(self) -> None:
        """build_docker_command produces correct docker run command."""
        from headerkit._populate import PopulateTarget, build_docker_command

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
            target_triple="x86_64-unknown-linux-gnu",
        )
        cmd = build_docker_command(
            target=target,
            project_root=Path("/home/user/project"),
            headerkit_source=Path("/home/user/headerkit"),
            header_paths=["/home/user/project/include/mylib.h"],
            writers=["cffi"],
            cache_dir=Path("/home/user/project/.hkcache"),
        )
        # Verify command list structure
        assert cmd[:3] == ["docker", "run", "--rm"]
        idx = cmd.index("--platform")
        assert cmd[idx + 1] == "linux/amd64"

        # Verify volume mounts as list items
        assert "-v" in cmd
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        volume_args = [cmd[i + 1] for i in v_indices]
        # Host side uses native str(), container side uses POSIX
        project_host = str(Path("/home/user/project"))
        assert f"{project_host}:/home/user/project:rw" in volume_args
        hk_host = str(Path("/home/user/headerkit"))
        assert f"{hk_host}:/headerkit-src:ro" in volume_args

        # Verify bash -c script contains pip install before headerkit command
        bash_idx = cmd.index("bash")
        assert cmd[bash_idx + 1] == "-c"
        script = cmd[bash_idx + 2]
        pip_pos = script.index("pip install")
        headerkit_pos = script.index("-m headerkit", pip_pos)
        assert pip_pos < headerkit_pos

    def test_build_docker_command_with_headerkit_version(self) -> None:
        """build_docker_command uses pip install when headerkit_version set."""
        from headerkit._populate import PopulateTarget, build_docker_command

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
            target_triple="x86_64-unknown-linux-gnu",
        )
        cmd = build_docker_command(
            target=target,
            project_root=Path("/home/user/project"),
            header_paths=["/home/user/project/include/mylib.h"],
            writers=["cffi"],
            cache_dir=Path("/home/user/project/.hkcache"),
            headerkit_version="0.10.1",
        )
        # Should NOT have headerkit source mount
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        volume_args = [cmd[i + 1] for i in v_indices]
        assert all("/headerkit-src:ro" not in v for v in volume_args)

        # Should have pip install with version in the bash script
        bash_idx = cmd.index("bash")
        script = cmd[bash_idx + 2]
        assert "headerkit==0.10.1" in script

    def test_build_docker_command_with_extra_mounts(self) -> None:
        """build_docker_command adds volume mounts for external include paths."""
        from headerkit._populate import PopulateTarget, build_docker_command

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
            target_triple="x86_64-unknown-linux-gnu",
        )
        cmd = build_docker_command(
            target=target,
            project_root=Path("/home/user/project"),
            headerkit_source=Path("/home/user/headerkit"),
            header_paths=["/home/user/project/include/mylib.h"],
            writers=["cffi"],
            cache_dir=Path("/home/user/project/.hkcache"),
            include_dirs=["/usr/local/include/libfoo"],
        )
        # Verify volume mount as list item
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        volume_args = [cmd[i + 1] for i in v_indices]
        # Host side uses native str(), container side uses POSIX
        inc_host = str(Path("/usr/local/include/libfoo"))
        assert f"{inc_host}:/usr/local/include/libfoo:ro" in volume_args

        # Verify -I in bash script
        bash_idx = cmd.index("bash")
        script = cmd[bash_idx + 2]
        assert "-I" in script
        assert "/usr/local/include/libfoo" in script

    def test_build_docker_command_shlex_quoting(self) -> None:
        """build_docker_command quotes user inputs to prevent shell injection."""
        from headerkit._populate import PopulateTarget, build_docker_command

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
            target_triple="x86_64-unknown-linux-gnu",
        )
        malicious_path = '/tmp/evil"; rm -rf ~'
        cmd = build_docker_command(
            target=target,
            project_root=Path("/home/user/project"),
            headerkit_source=Path("/home/user/headerkit"),
            header_paths=[malicious_path],
            writers=["cffi"],
            cache_dir=Path("/home/user/project/.hkcache"),
            defines=['FOO=bar"; echo pwned'],
            backend_args=['--arg="; whoami'],
            writer_options={"cffi": {"key": 'val"; cat /etc/passwd'}},
        )
        bash_idx = cmd.index("bash")
        script = cmd[bash_idx + 2]
        # All user-controlled values should be shell-quoted via shlex.quote(),
        # which wraps them in single quotes to neutralize special characters.
        # Path goes through Path.as_posix() then shlex.quote()
        assert shlex.quote(Path(malicious_path).as_posix()) in script
        # Defines should be quoted
        assert shlex.quote('FOO=bar"; echo pwned') in script
        # Backend args should be quoted
        assert shlex.quote('--arg="; whoami') in script
        # Writer options should be quoted
        assert shlex.quote('cffi:key=val"; cat /etc/passwd') in script
        # Cache dir should be quoted
        # Bash script paths are always POSIX (runs inside Linux container)
        assert shlex.quote("/home/user/project/.hkcache") in script

    def test_build_docker_command_list_writer_options(self) -> None:
        """build_docker_command emits separate --writer-opt flags for list values."""
        from headerkit._populate import PopulateTarget, build_docker_command

        target = PopulateTarget(
            docker_platform="linux/amd64",
            python_version="3.12",
            docker_image="quay.io/pypa/manylinux_2_28_x86_64",
            python_path="/opt/python/cp312-cp312/bin/python",
            target_triple="x86_64-unknown-linux-gnu",
        )
        cmd = build_docker_command(
            target=target,
            project_root=Path("/home/user/project"),
            headerkit_source=Path("/home/user/headerkit"),
            header_paths=["/home/user/project/include/mylib.h"],
            writers=["cffi"],
            cache_dir=Path("/home/user/project/.hkcache"),
            writer_options={"cffi": {"exclude": ["^_", "^test_"]}},
        )
        bash_idx = cmd.index("bash")
        script = cmd[bash_idx + 2]
        # Should contain two separate --writer-opt flags
        assert shlex.quote("cffi:exclude=^_") in script
        assert shlex.quote("cffi:exclude=^test_") in script
        # Should NOT contain a stringified list
        assert "['^_'" not in script
        assert "['^_'" not in script


class TestPopulateFunction:
    """Tests for the populate() top-level function."""

    def test_dry_run_returns_planned_targets(self, tmp_path: Path) -> None:
        """dry_run=True returns planned targets without running Docker."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        result = populate(
            header_paths=header,
            writers=["cffi"],
            platforms=["linux/amd64", "linux/arm64"],
            python_versions=["3.12", "3.13"],
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        assert len(result.planned) == 4
        combos = sorted((t.docker_platform, t.python_version) for t in result.planned)
        assert combos == [
            ("linux/amd64", "3.12"),
            ("linux/amd64", "3.13"),
            ("linux/arm64", "3.12"),
            ("linux/arm64", "3.13"),
        ]
        assert len(result.entries) == 0

    def test_dry_run_does_not_create_cache_dir(self, tmp_path: Path) -> None:
        """dry_run=True does not create .hkcache/."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        cache_dir = tmp_path / ".hkcache"

        populate(
            header_paths=header,
            writers=["cffi"],
            platforms=["linux/amd64"],
            python_versions=["3.12"],
            cache_dir=cache_dir,
            dry_run=True,
        )
        assert not cache_dir.exists()

    def test_no_platforms_or_cibuildwheel_raises(self, tmp_path: Path) -> None:
        """Missing platforms and from_cibuildwheel raises ValueError."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Specify at least one --platform"):
            populate(
                header_paths=header,
                writers=["cffi"],
                cache_dir=tmp_path / ".hkcache",
            )

    def test_header_not_found_raises(self, tmp_path: Path) -> None:
        """Missing header file raises FileNotFoundError."""
        from headerkit._populate import populate

        with pytest.raises(FileNotFoundError, match="Header file not found"):
            populate(
                header_paths=tmp_path / "nonexistent.h",
                writers=["cffi"],
                platforms=["linux/amd64"],
                cache_dir=tmp_path / ".hkcache",
                dry_run=True,
            )

    def test_stdin_rejected(self, tmp_path: Path) -> None:
        """Stdin placeholder '-' is rejected."""
        from headerkit._populate import populate

        with pytest.raises(ValueError, match="requires file paths, not stdin"):
            populate(
                header_paths="-",
                writers=["cffi"],
                platforms=["linux/amd64"],
                cache_dir=tmp_path / ".hkcache",
                dry_run=True,
            )

    def test_string_header_path_accepted(self, tmp_path: Path) -> None:
        """Single string header_path is accepted."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        result = populate(
            header_paths=str(header),
            writers=["cffi"],
            platforms=["linux/amd64"],
            python_versions=["3.12"],
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        assert len(result.planned) == 1

    def test_list_header_paths_accepted(self, tmp_path: Path) -> None:
        """List of header paths is accepted."""
        from headerkit._populate import populate

        h1 = tmp_path / "a.h"
        h2 = tmp_path / "b.h"
        h1.write_text("int a(void);\n", encoding="utf-8")
        h2.write_text("int b(void);\n", encoding="utf-8")

        result = populate(
            header_paths=[h1, h2],
            writers=["cffi"],
            platforms=["linux/amd64"],
            python_versions=["3.12"],
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        assert len(result.planned) == 1

    def test_populate_runs_docker(self, tmp_path: Path) -> None:
        """populate() calls subprocess.run with docker command per target."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        with (
            patch("headerkit._populate.check_docker_available"),
            patch(
                "headerkit._populate._find_headerkit_source",
                return_value=tmp_path,
            ),
            patch("headerkit._populate.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = populate(
                header_paths=header,
                writers=["cffi"],
                platforms=["linux/amd64"],
                python_versions=["3.12"],
                cache_dir=tmp_path / ".hkcache",
                timeout=60,
            )
        # One target = one subprocess call
        assert mock_run.call_count == 1
        assert result.total == 1
        assert result.succeeded == 1

        # Verify the actual command list structure
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["docker", "run", "--rm"]
        assert "--platform" in cmd
        plat_idx = cmd.index("--platform")
        assert cmd[plat_idx + 1] == "linux/amd64"

    def test_populate_docker_failure(self, tmp_path: Path) -> None:
        """Docker container failure is recorded as failed entry."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        with (
            patch("headerkit._populate.check_docker_available"),
            patch(
                "headerkit._populate._find_headerkit_source",
                return_value=tmp_path,
            ),
            patch("headerkit._populate.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="exec format error",
            )
            result = populate(
                header_paths=header,
                writers=["cffi"],
                platforms=["linux/amd64"],
                python_versions=["3.12"],
                cache_dir=tmp_path / ".hkcache",
            )
        assert result.total == 1
        assert result.failed == 1
        assert "exec format error" in result.entries[0].error

    def test_populate_timeout(self, tmp_path: Path) -> None:
        """Container timeout is recorded as failed entry."""
        from headerkit._populate import populate

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        with (
            patch("headerkit._populate.check_docker_available"),
            patch(
                "headerkit._populate._find_headerkit_source",
                return_value=tmp_path,
            ),
            patch("headerkit._populate.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["docker", "run"],
                timeout=60,
            )
            result = populate(
                header_paths=header,
                writers=["cffi"],
                platforms=["linux/amd64"],
                python_versions=["3.12"],
                cache_dir=tmp_path / ".hkcache",
                timeout=60,
            )
        assert result.total == 1
        assert result.failed == 1
        assert "timed out" in result.entries[0].error.lower()

    def test_populate_from_cibuildwheel(self, tmp_path: Path) -> None:
        """from_cibuildwheel reads pyproject.toml for targets."""
        from headerkit._populate import populate

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\n[tool.cibuildwheel]\nbuild = "cp312-*"\n',
            encoding="utf-8",
        )
        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()  # So project root detection works

        result = populate(
            header_paths=header,
            writers=["cffi"],
            from_cibuildwheel=True,
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        # Should find linux/amd64 and linux/arm64 from cibuildwheel defaults
        docker_platforms = [t.docker_platform for t in result.planned]
        assert "linux/amd64" in docker_platforms
        assert all(t.python_version == "3.12" for t in result.planned)

    def test_populate_uses_config_platforms(self, tmp_path: Path) -> None:
        """populate() falls back to config platforms when param is None."""
        from headerkit._populate import populate

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.headerkit.cache.populate]\nplatforms = ["linux/amd64"]\npython_versions = ["3.12"]\n',
            encoding="utf-8",
        )
        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()

        result = populate(
            header_paths=header,
            writers=["json"],
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        assert len(result.planned) == 1
        assert result.planned[0].docker_platform == "linux/amd64"
        assert result.planned[0].python_version == "3.12"

    def test_populate_uses_config_python_versions(self, tmp_path: Path) -> None:
        """populate() falls back to config python_versions when param is None."""
        from headerkit._populate import populate

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.headerkit.cache.populate]\npython_versions = ["3.13"]\n',
            encoding="utf-8",
        )
        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()

        result = populate(
            header_paths=header,
            writers=["json"],
            platforms=["linux/amd64"],
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        assert len(result.planned) == 1
        assert result.planned[0].python_version == "3.13"

    def test_populate_uses_config_timeout(self, tmp_path: Path) -> None:
        """populate() falls back to config timeout when param is the default."""
        from headerkit._populate import populate

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit.cache.populate]\ntimeout = 600\n",
            encoding="utf-8",
        )
        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()

        with (
            patch("headerkit._populate.check_docker_available"),
            patch(
                "headerkit._populate._find_headerkit_source",
                return_value=tmp_path,
            ),
            patch("headerkit._populate.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            populate(
                header_paths=header,
                writers=["json"],
                platforms=["linux/amd64"],
                python_versions=["3.12"],
                cache_dir=tmp_path / ".hkcache",
            )
            # Verify the timeout passed to subprocess.run is 600 (from config)
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 600

    def test_populate_explicit_params_override_config(self, tmp_path: Path) -> None:
        """Explicit params override config file values."""
        from headerkit._populate import populate

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.headerkit.cache.populate]\nplatforms = ["linux/arm64"]\npython_versions = ["3.11"]\ntimeout = 600\n',
            encoding="utf-8",
        )
        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()

        result = populate(
            header_paths=header,
            writers=["json"],
            platforms=["linux/amd64"],
            python_versions=["3.12"],
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
            timeout=120,
        )
        # Explicit values should win
        assert len(result.planned) == 1
        assert result.planned[0].docker_platform == "linux/amd64"
        assert result.planned[0].python_version == "3.12"

    def test_populate_cibuildwheel_ignores_config_platforms(self, tmp_path: Path) -> None:
        """from_cibuildwheel=True ignores config platforms."""
        from headerkit._populate import populate

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\n'
            "[tool.headerkit.cache.populate]\n"
            'platforms = ["linux/arm64"]\n'
            '[tool.cibuildwheel]\nbuild = "cp312-manylinux_x86_64"\n',
            encoding="utf-8",
        )
        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()

        result = populate(
            header_paths=header,
            writers=["json"],
            from_cibuildwheel=True,
            cache_dir=tmp_path / ".hkcache",
            dry_run=True,
        )
        docker_platforms = [t.docker_platform for t in result.planned]
        # Should use cibuildwheel platforms (linux/amd64), not config (linux/arm64)
        assert "linux/amd64" in docker_platforms
        assert "linux/arm64" not in docker_platforms


class TestFindProjectRoot:
    """Tests for _find_project_root()."""

    def test_finds_git_root(self, tmp_path: Path) -> None:
        """Finds project root by walking up from a subdirectory."""
        from headerkit._config import _find_project_root

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        nested = tmp_path / "src" / "pkg" / "sub"
        nested.mkdir(parents=True)

        result = _find_project_root(nested)
        assert result == tmp_path

    def test_falls_back_to_start(self, tmp_path: Path) -> None:
        """Falls back to start directory when no .git found."""
        from headerkit._config import _find_project_root

        nested = tmp_path / "no_git" / "deep"
        nested.mkdir(parents=True)

        result = _find_project_root(nested)
        assert result == nested

    def test_uses_absolute_not_resolve(self) -> None:
        """_find_project_root uses absolute() to avoid 8.3 short-name expansion."""
        import inspect

        from headerkit._config import _find_project_root

        source = inspect.getsource(_find_project_root)
        # Strip the docstring: everything after the closing triple-quotes
        # is executable code.
        body_start = source.find('"""', source.find('"""') + 3) + 3
        code_body = source[body_start:]
        # The code body must use .absolute() for the traversal
        assert ".absolute()" in code_body
        # The code body must NOT use .resolve() which causes 8.3 short-name issues
        assert ".resolve()" not in code_body

    def test_importable_from_populate(self) -> None:
        """_find_project_root is importable from _populate for backwards compat."""
        from headerkit._populate import _find_project_root  # noqa: F401

    def test_importable_from_generate(self) -> None:
        """_find_project_root is importable from _generate for backwards compat."""
        from headerkit._generate import _find_project_root  # noqa: F401


class TestVersionValidation:
    """Tests for version string validation."""

    def test_python_path_rejects_full_version(self) -> None:
        """python_path_for_version rejects '3.12.1'."""
        from headerkit._populate import python_path_for_version

        with pytest.raises(ValueError, match="Expected MAJOR.MINOR"):
            python_path_for_version("3.12.1")

    def test_python_path_rejects_single_component(self) -> None:
        """python_path_for_version rejects '312'."""
        from headerkit._populate import python_path_for_version

        with pytest.raises(ValueError, match="Expected MAJOR.MINOR"):
            python_path_for_version("312")


class TestBuildSelectorPlatformFiltering:
    """Tests for cibuildwheel build selector platform filtering."""

    def test_build_x86_64_only_excludes_arm64(self, tmp_path: Path) -> None:
        """build = 'cp312-manylinux_x86_64' should not include linux/arm64."""
        from headerkit._populate import parse_cibuildwheel_config

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-manylinux_x86_64"\n',
            encoding="utf-8",
        )

        platforms, python_versions, _ = parse_cibuildwheel_config(pyproject)
        assert platforms == ["linux/amd64"]
        assert python_versions == ["3.12"]

    def test_linux_only_build_no_macos_windows_warning(self, tmp_path: Path) -> None:
        """build = 'cp312-manylinux*' should not warn about macOS/Windows."""
        from headerkit._populate import parse_cibuildwheel_config

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-manylinux*"\n',
            encoding="utf-8",
        )

        platforms, python_versions, warnings = parse_cibuildwheel_config(pyproject)
        assert python_versions == ["3.12"]
        # Should have linux platforms only
        assert all(p.startswith("linux/") for p in platforms)
        # No macOS or Windows warnings
        assert not any("macOS" in w or "Windows" in w for w in warnings)

    def test_build_aarch64_only_excludes_amd64(self, tmp_path: Path) -> None:
        """build = 'cp312-manylinux_aarch64' should not include linux/amd64."""
        from headerkit._populate import parse_cibuildwheel_config

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.cibuildwheel]\nbuild = "cp312-manylinux_aarch64"\n',
            encoding="utf-8",
        )

        platforms, python_versions, _ = parse_cibuildwheel_config(pyproject)
        assert platforms == ["linux/arm64"]
        assert python_versions == ["3.12"]


class TestCachePopulateCli:
    """Tests for the cache populate CLI entry point."""

    def test_dry_run_output(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--dry-run prints planned targets and exits 0."""
        from headerkit._cache_cli import cache_populate_main

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        exit_code = cache_populate_main(
            [
                str(header),
                "-w",
                "cffi",
                "--platform",
                "linux/amd64",
                "--python",
                "3.12",
                "--cache-dir",
                str(tmp_path / ".hkcache"),
                "--dry-run",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Planned cache population" in captured.out
        assert "linux/amd64" in captured.out
        assert "x86_64-unknown-linux-gnu" in captured.out

    def test_no_platform_or_cibuildwheel_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing --platform and --cibuildwheel prints error."""
        from headerkit._cache_cli import cache_populate_main

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        exit_code = cache_populate_main(
            [
                str(header),
                "-w",
                "cffi",
                "--cache-dir",
                str(tmp_path / ".hkcache"),
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Specify at least one --platform" in captured.err

    def test_header_not_found_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing header prints error."""
        from headerkit._cache_cli import cache_populate_main

        exit_code = cache_populate_main(
            [
                str(tmp_path / "nonexistent.h"),
                "-w",
                "cffi",
                "--platform",
                "linux/amd64",
                "--cache-dir",
                str(tmp_path / ".hkcache"),
                "--dry-run",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()

    def test_duplicate_writer_opt_aggregates_into_list(
        self,
        tmp_path: Path,
    ) -> None:
        """Duplicate --writer-opt keys for the same writer are aggregated into lists."""
        from headerkit._cache_cli import cache_populate_main

        header = tmp_path / "test.h"
        header.write_text("int foo(void);\n", encoding="utf-8")

        with patch("headerkit._populate.populate") as mock_populate:
            mock_populate.return_value = MagicMock(
                warnings=[],
                failed=0,
                succeeded=1,
                skipped_count=0,
                total=1,
                entries=[],
                planned=[],
            )
            cache_populate_main(
                [
                    str(header),
                    "-w",
                    "cffi",
                    "--platform",
                    "linux/amd64",
                    "--python",
                    "3.12",
                    "--cache-dir",
                    str(tmp_path / ".hkcache"),
                    "--writer-opt",
                    "cffi:exclude=^_",
                    "--writer-opt",
                    "cffi:exclude=^test_",
                    "--dry-run",
                ]
            )
            call_kwargs = mock_populate.call_args[1]
            assert call_kwargs["writer_options"] == {
                "cffi": {"exclude": ["^_", "^test_"]},
            }

    def test_cli_dispatch_from_main(self) -> None:
        """'headerkit cache populate' dispatches to cache_populate_main."""
        with patch(
            "headerkit._cache_cli.cache_populate_main",
            return_value=0,
        ) as mock_pop:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "populate", "--dry-run"],
            ):
                from headerkit._cli import main

                result = main()
                assert result == 0
            mock_pop.assert_called_once_with(["--dry-run"])


class TestLoadPopulateConfig:
    """Tests for loading populate settings from config files."""

    def test_load_from_pyproject(self, tmp_path: Path) -> None:
        """Load populate config from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.headerkit.cache.populate]\n"
            'platforms = ["linux/amd64", "linux/arm64"]\n'
            'python_versions = ["3.12", "3.13"]\n'
            "timeout = 600\n"
            "\n"
            "[tool.headerkit.cache.populate.images]\n"
            '"linux/amd64" = "custom/manylinux:v1"\n',
            encoding="utf-8",
        )
        from headerkit._populate import load_populate_config

        cfg = load_populate_config(pyproject)
        assert cfg["platforms"] == ["linux/amd64", "linux/arm64"]
        assert cfg["python_versions"] == ["3.12", "3.13"]
        assert cfg["timeout"] == 600
        assert cfg["images"] == {"linux/amd64": "custom/manylinux:v1"}

    def test_load_empty_section(self, tmp_path: Path) -> None:
        """Empty populate section returns empty config."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\n',
            encoding="utf-8",
        )
        from headerkit._populate import load_populate_config

        cfg = load_populate_config(pyproject)
        assert cfg["platforms"] == []
        assert cfg["python_versions"] == []
        assert cfg["timeout"] is None
        assert cfg["images"] == {}

    def test_load_from_headerkit_toml(self, tmp_path: Path) -> None:
        """Load populate config from .headerkit.toml."""
        config = tmp_path / ".headerkit.toml"
        config.write_text(
            '[cache.populate]\nplatforms = ["linux/amd64"]\ntimeout = 120\n',
            encoding="utf-8",
        )
        from headerkit._populate import load_populate_config

        cfg = load_populate_config(config)
        assert cfg["platforms"] == ["linux/amd64"]
        assert cfg["timeout"] == 120


class TestPublicAPI:
    """Tests for public API re-exports."""

    def test_populate_importable_from_headerkit(self) -> None:
        """populate() is importable from the top-level package."""
        from headerkit import populate

        assert callable(populate)

    def test_result_types_importable_from_headerkit(self) -> None:
        """PopulateResult and PopulateTarget are importable from top-level."""
        from headerkit import PopulateResult, PopulateTarget

        assert PopulateResult is not None
        assert PopulateTarget is not None

    def test_populate_in_all(self) -> None:
        """populate, PopulateResult, PopulateTarget are in __all__."""
        import headerkit

        assert "populate" in headerkit.__all__
        assert "PopulateResult" in headerkit.__all__
        assert "PopulateTarget" in headerkit.__all__
