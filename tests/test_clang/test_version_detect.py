"""Tests for LLVM version detection logic."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from headerkit._clang._version import detect_llvm_version


@pytest.fixture(autouse=True)
def _clear_cir_clang_version():
    """Ensure CIR_CLANG_VERSION is absent before each test."""
    os.environ.pop("CIR_CLANG_VERSION", None)
    yield
    os.environ.pop("CIR_CLANG_VERSION", None)


def _which_only(*names: str) -> object:
    """Return a shutil.which mock that only finds the given binary names."""
    name_set = set(names)

    def side_effect(cmd: str) -> str | None:
        if cmd in name_set:
            return f"/usr/bin/{cmd}"
        return None

    return side_effect


# Patch glob.glob to disable llvm-dir detection in tests that don't need it.
_no_llvm_dir = patch("headerkit._clang._version.glob.glob", return_value=[])


class TestEnvVarOverride:
    def test_cir_clang_version_env_var(self):
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            assert detect_llvm_version() == "18"

    def test_cir_clang_version_env_var_takes_precedence(self):
        """Env var should take precedence over llvm-config."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "19.0.0\n"
        with (
            patch.dict(os.environ, {"CIR_CLANG_VERSION": "20"}),
            patch("headerkit._clang._version.shutil.which", return_value="/usr/bin/llvm-config"),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            # Even though llvm-config would return 19, the env var should win
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
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "18.1.0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("llvm-config")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "18"

    def test_llvm_config_versioned_binary(self):
        """When only llvm-config-18 exists (common on Debian/Ubuntu)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "18.1.8\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("llvm-config-18")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "18"

    def test_llvm_config_not_found(self):
        """When llvm-config is not found, fall through to clang detection."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 19\n#define __clang_minor__ 0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "19"

    def test_llvm_config_returns_non_zero_exit(self):
        """When llvm-config returns non-zero, fall through to clang."""
        llvm_result = MagicMock()
        llvm_result.returncode = 1
        llvm_result.stdout = ""
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 21\n"

        def run_side_effect(cmd, **kwargs):  # noqa: ARG001
            if "llvm-config" in cmd[0]:
                return llvm_result
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "headerkit._clang._version.shutil.which",
                side_effect=_which_only("llvm-config", "clang"),
            ),
            patch("headerkit._clang._version.subprocess.run", side_effect=run_side_effect),
        ):
            assert detect_llvm_version() == "21"

    def test_llvm_config_returns_garbage(self):
        """When llvm-config returns non-numeric output, fall through to clang."""
        llvm_result = MagicMock()
        llvm_result.returncode = 0
        llvm_result.stdout = "not-a-version\n"
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 20\n"

        def run_side_effect(cmd, **kwargs):  # noqa: ARG001
            if "llvm-config" in cmd[0]:
                return llvm_result
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "headerkit._clang._version.shutil.which",
                side_effect=_which_only("llvm-config", "clang"),
            ),
            patch("headerkit._clang._version.subprocess.run", side_effect=run_side_effect),
        ):
            assert detect_llvm_version() == "20"

    def test_llvm_config_subprocess_timeout(self):
        """When llvm-config times out, fall through to clang."""
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 19\n"

        def run_side_effect(cmd, **kwargs):  # noqa: ARG001
            if "llvm-config" in cmd[0]:
                raise subprocess.TimeoutExpired(cmd="llvm-config", timeout=5)
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "headerkit._clang._version.shutil.which",
                side_effect=_which_only("llvm-config", "clang"),
            ),
            patch("headerkit._clang._version.subprocess.run", side_effect=run_side_effect),
        ):
            assert detect_llvm_version() == "19"


class TestClangPreprocessor:
    def test_clang_major_define(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 20\n#define __clang_minor__ 1\n#define __clang_patchlevel__ 0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "20"

    def test_clang_versioned_binary(self):
        """When only clang-19 exists (common on Debian/Ubuntu)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 19\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang-19")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "19"

    def test_apple_clang_version_detected(self):
        """Apple clang reports its own __clang_major__ which differs from LLVM version.

        Apple Clang version numbers do not correspond to upstream LLVM versions
        (e.g., Apple Clang 16.x is based on LLVM ~18). Our detector returns the
        raw __clang_major__ value without attempting to map to upstream LLVM
        versions. This is approximate but consistent: the caller (get_cindex)
        handles the fallback when the version falls outside vendored range.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "#define __APPLE_CC__ 6000\n"
            "#define __apple_build_version__ 16000026\n"
            "#define __clang__ 1\n"
            "#define __clang_major__ 16\n"
            "#define __clang_minor__ 0\n"
            "#define __clang_patchlevel__ 0\n"
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "16"

    def test_clang_preprocessor_no_clang_major(self):
        """When clang output has no __clang_major__, falls through to soname."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __STDC__ 1\n#define __STDC_VERSION__ 201710L\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
            _no_llvm_dir,
        ):
            assert detect_llvm_version() is None

    def test_clang_subprocess_oserror(self):
        """When clang subprocess raises OSError, falls through to soname."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang")),
            patch("headerkit._clang._version.subprocess.run", side_effect=OSError("Permission denied")),
            _no_llvm_dir,
        ):
            assert detect_llvm_version() is None


class TestPkgConfig:
    def test_pkg_config_clang_module(self):
        """Detect version via pkg-config --modversion clang."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "18.1.8\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("pkg-config")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "18"

    def test_pkg_config_not_installed(self):
        """When pkg-config is not installed, fall through."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 20\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("clang")),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "20"

    def test_pkg_config_module_not_found(self):
        """When pkg-config can't find clang module, fall through."""
        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 19\n"

        def run_side_effect(cmd, **kwargs):  # noqa: ARG001
            if "pkg-config" in cmd[0]:
                return fail_result
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("pkg-config", "clang")),
            patch("headerkit._clang._version.subprocess.run", side_effect=run_side_effect),
        ):
            assert detect_llvm_version() == "19"


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
        prefix_result = MagicMock()
        prefix_result.returncode = 0
        prefix_result.stdout = "/opt/homebrew/opt/llvm\n"
        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "20.1.0\n"

        def run_side_effect(cmd, **kwargs):  # noqa: ARG001
            if cmd[0].endswith("brew"):
                return prefix_result
            return version_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", side_effect=_which_only("brew")),
            patch("headerkit._clang._version.subprocess.run", side_effect=run_side_effect),
            patch("headerkit._clang._version.sys.platform", "darwin"),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
            _no_llvm_dir,
        ):
            assert detect_llvm_version() == "20"

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

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 18\n"

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict("sys.modules", {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=True),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "18"

    def test_program_files_called_after_registry(self):
        """On win32, Program Files detection is used when registry fails."""
        mock_winreg = MagicMock()
        mock_winreg.OpenKey.side_effect = OSError("No registry key")
        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.KEY_READ = 0x20019
        mock_winreg.KEY_WOW64_64KEY = 0x0100

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 20\n"

        def isfile_side_effect(path):
            return "program files" in path.lower() and "clang.exe" in path.lower()

        with (
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
                clear=False,
            ),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict("sys.modules", {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=False),
            patch("headerkit._clang._version.os.path.isfile", side_effect=isfile_side_effect),
            patch("headerkit._clang._version.subprocess.run", return_value=mock_result),
        ):
            assert detect_llvm_version() == "20"


class TestFallback:
    def test_all_methods_fail_returns_none(self):
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("headerkit._clang._version.shutil.which", return_value=None),
            _no_llvm_dir,
            patch("headerkit._clang._version._try_windows_registry", return_value=None),
            patch("headerkit._clang._version._try_windows_program_files", return_value=None),
        ):
            assert detect_llvm_version() is None
