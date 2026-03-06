"""Tests for LLVM version detection logic."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import bigfoot
import pytest

from headerkit._clang._version import detect_llvm_version


@pytest.fixture(autouse=True)
def _clear_cir_clang_version():
    """Ensure CIR_CLANG_VERSION is absent before each test."""
    os.environ.pop("CIR_CLANG_VERSION", None)
    yield
    os.environ.pop("CIR_CLANG_VERSION", None)


# Patch glob.glob to disable llvm-dir detection in tests that don't need it.
_no_llvm_dir = patch("headerkit._clang._version.glob.glob", return_value=[])

# Patch sys.platform to "linux" so _try_clang_preprocessor uses "/dev/null" (not
# "NUL") regardless of the OS the test suite is running on.  Tests that exercise
# Windows-specific strategies (TestWindowsDetectionOrder) override this with their
# own platform patch.
_linux_platform = patch("headerkit._clang._version.sys.platform", "linux")


class TestEnvVarOverride:
    def test_cir_clang_version_env_var(self):
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            assert detect_llvm_version() == "18"

    def test_cir_clang_version_env_var_takes_precedence(self):
        """Env var should take precedence over llvm-config."""
        bigfoot.subprocess_mock.install()
        with (
            patch.dict(os.environ, {"CIR_CLANG_VERSION": "20"}),
            bigfoot,
        ):
            # Even though llvm-config would return 19, the env var should win.
            # No subprocess.run or shutil.which calls should be made at all.
            assert detect_llvm_version() == "20"

    def test_env_var_with_whitespace_is_stripped(self):
        """Env var value with leading/trailing whitespace is stripped."""
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "  19  "}):
            assert detect_llvm_version() == "19"

    def test_cir_clang_version_invalid_falls_through(self):
        """Non-numeric CIR_CLANG_VERSION is ignored, falls through to other strategies."""
        with (
            patch.dict(os.environ, {"CIR_CLANG_VERSION": "abc"}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.glob.glob", return_value=[]),
            patch("headerkit._clang._version._try_windows_registry", return_value=None),
            patch("headerkit._clang._version._try_windows_program_files", return_value=None),
        ):
            result = detect_llvm_version()
            assert result is None


class TestLlvmConfig:
    def test_llvm_config_full_version(self):
        # Interaction sequence:
        # 1. which("llvm-config") -> "/usr/bin/llvm-config"
        # 2. run(["/usr/bin/llvm-config", "--version"]) -> rc=0, stdout="18.1.0\n"
        bigfoot.subprocess_mock.mock_which("llvm-config", returns="/usr/bin/llvm-config")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/llvm-config", "--version"],
            returncode=0,
            stdout="18.1.0\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            bigfoot,
        ):
            assert detect_llvm_version() == "18"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="llvm-config")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/llvm-config", "--version"],
        )

    def test_llvm_config_versioned_binary(self):
        """When only llvm-config-18 exists (common on Debian/Ubuntu)."""
        # Interaction sequence:
        # which("llvm-config") -> None (unregistered, semi-permissive)
        # which("llvm-config-30") through which("llvm-config-19") -> None (unregistered)
        # which("llvm-config-18") -> "/usr/bin/llvm-config-18" (registered)
        # run(["/usr/bin/llvm-config-18", "--version"]) -> rc=0, stdout="18.1.8\n"
        bigfoot.subprocess_mock.mock_which("llvm-config-18", returns="/usr/bin/llvm-config-18")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/llvm-config-18", "--version"],
            returncode=0,
            stdout="18.1.8\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            bigfoot,
        ):
            assert detect_llvm_version() == "18"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="llvm-config-18")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/llvm-config-18", "--version"],
        )

    def test_llvm_config_not_found(self):
        """When llvm-config is not found, fall through to clang detection."""
        # Interaction sequence:
        # which("llvm-config") through which("llvm-config-14") -> None (unregistered)
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 19\n#define __clang_minor__ 0\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "19"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_llvm_config_returns_non_zero_exit(self):
        """When llvm-config returns non-zero, fall through to clang."""
        # Interaction sequence:
        # which("llvm-config") -> "/usr/bin/llvm-config" (registered)
        # run(["/usr/bin/llvm-config", "--version"]) -> rc=1 (fail)
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("llvm-config", returns="/usr/bin/llvm-config")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/llvm-config", "--version"],
            returncode=1,
            stdout="",
        )
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 21\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "21"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="llvm-config")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/llvm-config", "--version"],
        )
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_llvm_config_returns_garbage(self):
        """When llvm-config returns non-numeric output, fall through to clang."""
        # Interaction sequence:
        # which("llvm-config") -> "/usr/bin/llvm-config" (registered)
        # run(["/usr/bin/llvm-config", "--version"]) -> rc=0, stdout="not-a-version\n"
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("llvm-config", returns="/usr/bin/llvm-config")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/llvm-config", "--version"],
            returncode=0,
            stdout="not-a-version\n",
        )
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 20\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "20"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="llvm-config")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/llvm-config", "--version"],
        )
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_llvm_config_subprocess_timeout(self):
        """When llvm-config times out, fall through to clang."""
        # Interaction sequence:
        # which("llvm-config") -> "/usr/bin/llvm-config" (registered)
        # run(["/usr/bin/llvm-config", "--version"]) -> raises TimeoutExpired
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("llvm-config", returns="/usr/bin/llvm-config")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/llvm-config", "--version"],
            raises=subprocess.TimeoutExpired(cmd="llvm-config", timeout=5),
        )
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 19\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "19"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="llvm-config")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/llvm-config", "--version"],
        )
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )


class TestClangPreprocessor:
    def test_clang_major_define(self):
        # Interaction sequence:
        # All llvm-config variants -> None (unregistered)
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 20\n#define __clang_minor__ 1\n#define __clang_patchlevel__ 0\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "20"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_clang_versioned_binary(self):
        """When only clang-19 exists (common on Debian/Ubuntu)."""
        # Interaction sequence:
        # All llvm-config variants -> None (unregistered)
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> None (unregistered)
        # which("clang-30") through which("clang-20") -> None (unregistered)
        # which("clang-19") -> "/usr/bin/clang-19" (registered)
        # run(["/usr/bin/clang-19", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("clang-19", returns="/usr/bin/clang-19")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang-19", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 19\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "19"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang-19")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang-19", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_apple_clang_version_detected(self):
        """Apple clang reports its own __clang_major__ which differs from LLVM version.

        Apple Clang version numbers do not correspond to upstream LLVM versions
        (e.g., Apple Clang 16.x is based on LLVM ~18). Our detector returns the
        raw __clang_major__ value without attempting to map to upstream LLVM
        versions. This is approximate but consistent: the caller (get_cindex)
        handles the fallback when the version falls outside vendored range.
        """
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout=(
                "#define __APPLE_CC__ 6000\n"
                "#define __apple_build_version__ 16000026\n"
                "#define __clang__ 1\n"
                "#define __clang_major__ 16\n"
                "#define __clang_minor__ 0\n"
                "#define __clang_patchlevel__ 0\n"
            ),
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "16"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_clang_preprocessor_no_clang_major(self):
        """When clang output has no __clang_major__, falls through to soname."""
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __STDC__ 1\n#define __STDC_VERSION__ 201710L\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() is None

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_clang_subprocess_oserror(self):
        """When clang subprocess raises OSError, falls through to soname."""
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            raises=OSError("Permission denied"),
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() is None

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )


class TestPkgConfig:
    def test_pkg_config_clang_module(self):
        """Detect version via pkg-config --modversion clang."""
        # Interaction sequence:
        # All llvm-config variants -> None (unregistered)
        # which("pkg-config") -> "/usr/bin/pkg-config" (registered)
        # run(["/usr/bin/pkg-config", "--modversion", "clang"]) -> rc=0, stdout="18.1.8\n"
        bigfoot.subprocess_mock.mock_which("pkg-config", returns="/usr/bin/pkg-config")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/pkg-config", "--modversion", "clang"],
            returncode=0,
            stdout="18.1.8\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            bigfoot,
        ):
            assert detect_llvm_version() == "18"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="pkg-config")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/pkg-config", "--modversion", "clang"],
        )

    def test_pkg_config_not_installed(self):
        """When pkg-config is not installed, fall through."""
        # Interaction sequence:
        # All llvm-config variants -> None (unregistered)
        # which("pkg-config") -> None (unregistered)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 20\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "20"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )

    def test_pkg_config_module_not_found(self):
        """When pkg-config can't find clang module, fall through."""
        # Interaction sequence:
        # All llvm-config variants -> None (unregistered)
        # which("pkg-config") -> "/usr/bin/pkg-config" (registered)
        # run(["/usr/bin/pkg-config", "--modversion", "clang"]) -> rc=1 (fail)
        # run(["/usr/bin/pkg-config", "--modversion", "libclang"]) -> rc=1 (fail)
        # which("clang") -> "/usr/bin/clang" (registered)
        # run(["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"]) -> rc=0
        bigfoot.subprocess_mock.mock_which("pkg-config", returns="/usr/bin/pkg-config")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/pkg-config", "--modversion", "clang"],
            returncode=1,
            stdout="",
        )
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/pkg-config", "--modversion", "libclang"],
            returncode=1,
            stdout="",
        )
        bigfoot.subprocess_mock.mock_which("clang", returns="/usr/bin/clang")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
            returncode=0,
            stdout="#define __clang_major__ 19\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            _linux_platform,
            bigfoot,
        ):
            assert detect_llvm_version() == "19"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="pkg-config")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/pkg-config", "--modversion", "clang"],
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/pkg-config", "--modversion", "libclang"],
        )
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="clang")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/bin/clang", "-dM", "-E", "-x", "c", "/dev/null"],
        )


class TestLlvmDir:
    def test_llvm_dir_detection(self):
        """Detect version from /usr/lib/llvm-{N}/ directory."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.sys.platform", "linux"),
            patch("headerkit._clang._version.glob.glob", return_value=["/usr/lib/llvm-18/"]),
        ):
            assert detect_llvm_version() == "18"

    def test_multiple_llvm_dirs_picks_newest(self):
        """When multiple llvm dirs exist, pick the newest (sorted descending)."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.sys.platform", "linux"),
            patch(
                "headerkit._clang._version.glob.glob",
                return_value=["/usr/lib/llvm-18/", "/usr/lib/llvm-20/"],
            ),
        ):
            assert detect_llvm_version() == "20"

    def test_skipped_on_non_linux(self):
        """Soname detection is Linux-only."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.sys.platform", "darwin"),
        ):
            assert detect_llvm_version() is None


class TestHomebrewLlvm:
    def test_brew_llvm_prefix(self):
        """Detect version from Homebrew's llvm installation."""
        # Interaction sequence:
        # All llvm-config variants -> None (unregistered)
        # which("pkg-config") -> None (unregistered)
        # All clang variants -> None (unregistered)
        # which("brew") -> "/usr/local/bin/brew" (registered)
        # run(["/usr/local/bin/brew", "--prefix", "llvm"]) -> rc=0, stdout="/opt/homebrew/opt/llvm\n"
        # os.path.isfile("/opt/homebrew/opt/llvm/bin/llvm-config") -> True (patched)
        # run(["/opt/homebrew/opt/llvm/bin/llvm-config", "--version"]) -> rc=0, stdout="20.1.0\n"
        #
        # Use os.path.join so the expected command matches what the production code
        # builds on the current OS (backslashes on Windows, forward slashes elsewhere).
        _brew_prefix = "/opt/homebrew/opt/llvm"
        _llvm_config = os.path.join(_brew_prefix, "bin", "llvm-config")
        bigfoot.subprocess_mock.mock_which("brew", returns="/usr/local/bin/brew")
        bigfoot.subprocess_mock.mock_run(
            ["/usr/local/bin/brew", "--prefix", "llvm"],
            returncode=0,
            stdout=f"{_brew_prefix}\n",
        )
        bigfoot.subprocess_mock.mock_run(
            [_llvm_config, "--version"],
            returncode=0,
            stdout="20.1.0\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.sys.platform", "darwin"),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
            _no_llvm_dir,
            bigfoot,
        ):
            assert detect_llvm_version() == "20"

        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="brew")
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["/usr/local/bin/brew", "--prefix", "llvm"],
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[_llvm_config, "--version"],
        )

    def test_brew_skipped_on_linux(self):
        """Homebrew detection is macOS-only."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.sys.platform", "linux"),
            _no_llvm_dir,
        ):
            assert detect_llvm_version() is None


class TestWindowsDetectionOrder:
    """Verify Windows-specific strategies are called in correct order.

    Limitation: these tests verify that strategies eventually produce the correct
    result under win32 conditions, but do not assert the exact call ordering
    (e.g., via mock.call_args_list). The detection function's linear strategy
    chain is verified implicitly: earlier strategies are blocked (shutil.which
    returns None), so the result must come from a later strategy.
    """

    def test_registry_called_after_clang_preprocessor(self):
        """On win32, registry detection is used when earlier strategies fail."""
        mock_winreg = MagicMock()
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = (r"C:\Program Files\LLVM", 1)
        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.KEY_READ = 0x20019
        mock_winreg.KEY_WOW64_64KEY = 0x0100

        # Interaction sequence:
        # All which() calls for llvm-config/pkg-config/clang -> None (unregistered)
        # Windows registry finds clang.exe -> run([clang_exe_path, ...])
        # The source uses os.path.join on the host OS, so we compute the same path.
        _install_dir = r"C:\Program Files\LLVM"
        _clang_exe = os.path.join(_install_dir, "bin", "clang.exe")
        bigfoot.subprocess_mock.mock_run(
            [_clang_exe, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 18\n",
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict("sys.modules", {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=True),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
            bigfoot,
        ):
            assert detect_llvm_version() == "18"

        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[_clang_exe, "-dM", "-E", "-x", "c", "NUL"],
        )

    def test_program_files_called_after_registry(self):
        """On win32, Program Files detection is used when registry fails."""
        mock_winreg = MagicMock()
        mock_winreg.OpenKey.side_effect = OSError("No registry key")
        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.KEY_READ = 0x20019
        mock_winreg.KEY_WOW64_64KEY = 0x0100

        def isfile_side_effect(path):
            return "program files" in path.lower() and "clang.exe" in path.lower()

        # Interaction sequence:
        # All which() calls -> None (unregistered, semi-permissive)
        # Registry fails (OpenKey raises OSError)
        # Program Files finds clang.exe -> run([clang_exe_path, ...])
        # The source uses os.path.join on the host OS, so we compute the same path.
        _program_files = r"C:\Program Files"
        _clang_exe = os.path.join(_program_files, "LLVM", "bin", "clang.exe")
        bigfoot.subprocess_mock.mock_run(
            [_clang_exe, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 20\n",
        )
        with (
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": _program_files,
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
                clear=False,
            ),
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict("sys.modules", {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=False),
            patch("headerkit._clang._version.os.path.isfile", side_effect=isfile_side_effect),
            bigfoot,
        ):
            assert detect_llvm_version() == "20"

        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[_clang_exe, "-dM", "-E", "-x", "c", "NUL"],
        )


class TestFallback:
    def test_all_methods_fail_returns_none(self):
        bigfoot.subprocess_mock.install()
        with (
            patch.dict(os.environ, {}, clear=False),
            _no_llvm_dir,
            patch("headerkit._clang._version._try_windows_registry", return_value=None),
            patch("headerkit._clang._version._try_windows_program_files", return_value=None),
            bigfoot,
        ):
            assert detect_llvm_version() is None
