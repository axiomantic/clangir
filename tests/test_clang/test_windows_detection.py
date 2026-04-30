"""Tests for Windows-specific LLVM version detection."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
import tripwire
from dirty_equals import AnyThing

from headerkit._clang._version import (
    _try_windows_program_files,
    _try_windows_registry,
)


@pytest.fixture()
def mock_winreg_constants() -> MagicMock:
    """Create a MagicMock winreg module with standard registry constants pre-set."""
    mock = MagicMock()
    mock.HKEY_LOCAL_MACHINE = 0x80000002
    mock.KEY_READ = 0x20019
    mock.KEY_WOW64_64KEY = 0x0100
    return mock


class TestWindowsRegistry:
    """Tests for _try_windows_registry()."""

    def test_finds_version_from_registry(self, mock_winreg_constants):
        """Registry returns install dir, clang.exe returns version."""
        mock_winreg = mock_winreg_constants
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = (r"C:\Program Files\LLVM", 1)

        expected_path = os.path.join(r"C:\Program Files\LLVM", "bin", "clang.exe")
        tripwire.subprocess.mock_run(
            [expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 18\n#define __clang_minor__ 1\n",
        )
        with (
            tripwire,
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=True),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
        ):
            result = _try_windows_registry()
        assert result == "18"
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 18\n#define __clang_minor__ 1\n",
            stderr="",
        )

    def test_registry_key_not_found(self, mock_winreg_constants):
        """Registry key does not exist, returns None."""
        mock_winreg = mock_winreg_constants
        mock_winreg.OpenKey.side_effect = OSError("Key not found")

        with (
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
        ):
            result = _try_windows_registry()
            assert result is None

    def test_install_dir_does_not_exist(self, mock_winreg_constants):
        """Registry returns dir that does not exist on disk."""
        mock_winreg = mock_winreg_constants
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = (r"C:\Nonexistent\LLVM", 1)

        with (
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=False),
        ):
            result = _try_windows_registry()
            assert result is None

    def test_clang_exe_not_found_in_install_dir(self, mock_winreg_constants):
        """Registry install dir exists but clang.exe is missing."""
        mock_winreg = mock_winreg_constants
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = (r"C:\Program Files\LLVM", 1)

        with (
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=True),
            patch("headerkit._clang._version.os.path.isfile", return_value=False),
        ):
            result = _try_windows_registry()
            assert result is None

    def test_clang_exe_returns_no_version(self, mock_winreg_constants):
        """clang.exe runs but output lacks __clang_major__."""
        mock_winreg = mock_winreg_constants
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = (r"C:\Program Files\LLVM", 1)

        expected_path = os.path.join(r"C:\Program Files\LLVM", "bin", "clang.exe")
        tripwire.subprocess.mock_run(
            [expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __STDC__ 1\n",
        )
        with (
            tripwire,
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=True),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
        ):
            result = _try_windows_registry()
        assert result is None
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __STDC__ 1\n",
            stderr="",
        )

    def test_skipped_on_non_windows(self):
        """Returns None immediately on non-Windows platforms."""
        with patch("headerkit._clang._version.sys.platform", "linux"):
            result = _try_windows_registry()
            assert result is None

    def test_subprocess_timeout(self, mock_winreg_constants):
        """clang.exe subprocess times out, returns None."""
        mock_winreg = mock_winreg_constants
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = (r"C:\Program Files\LLVM", 1)

        expected_path = os.path.join(r"C:\Program Files\LLVM", "bin", "clang.exe")
        tripwire.subprocess.mock_run(
            [expected_path, "-dM", "-E", "-x", "c", "NUL"],
            raises=subprocess.TimeoutExpired(cmd="clang.exe", timeout=5),
        )
        with (
            tripwire,
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": mock_winreg}),
            patch("headerkit._clang._version.os.path.isdir", return_value=True),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
        ):
            result = _try_windows_registry()
        assert result is None
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=AnyThing,
            stdout=AnyThing,
            stderr=AnyThing,
        )

    def test_winreg_import_error(self):
        """When winreg module is not importable, returns None."""
        with (
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(sys.modules, {"winreg": None}),
        ):
            result = _try_windows_registry()
            assert result is None


class TestWindowsProgramFiles:
    """Tests for _try_windows_program_files()."""

    def test_finds_version_from_programfiles(self):
        """Finds LLVM in PROGRAMFILES directory."""
        expected_path = os.path.join(r"C:\Program Files", "LLVM", "bin", "clang.exe")
        tripwire.subprocess.mock_run(
            [expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 20\n",
        )
        with (
            tripwire,
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
        ):
            result = _try_windows_program_files()
        assert result == "20"
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[expected_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 20\n",
            stderr="",
        )

    def test_finds_version_from_programfiles_x86(self):
        """Finds LLVM in PROGRAMFILES(X86) when PROGRAMFILES path has no clang."""
        x86_path = os.path.join(r"C:\Program Files (x86)", "LLVM", "bin", "clang.exe")
        tripwire.subprocess.mock_run(
            [x86_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 19\n",
        )

        def isfile_side_effect(path):
            # Only the x86 path has clang.exe
            expected = os.path.join(r"C:\Program Files (x86)", "LLVM", "bin", "clang.exe")
            return os.path.normpath(path) == os.path.normpath(expected)

        with (
            tripwire,
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
            patch("headerkit._clang._version.os.path.isfile", side_effect=isfile_side_effect),
        ):
            result = _try_windows_program_files()
        assert result == "19"
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[x86_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 19\n",
            stderr="",
        )

    def test_no_programfiles_env_var(self):
        """Both PROGRAMFILES env vars are absent."""
        env = os.environ.copy()
        env.pop("PROGRAMFILES", None)
        env.pop("PROGRAMFILES(X86)", None)

        with (
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(os.environ, env, clear=True),
        ):
            result = _try_windows_program_files()
            assert result is None

    def test_clang_exe_not_found(self):
        """PROGRAMFILES set but no clang.exe at the expected path."""
        with (
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                },
            ),
            patch("headerkit._clang._version.os.path.isfile", return_value=False),
        ):
            result = _try_windows_program_files()
            assert result is None

    def test_skipped_on_non_windows(self):
        """Returns None on non-Windows platforms."""
        with patch("headerkit._clang._version.sys.platform", "darwin"):
            result = _try_windows_program_files()
            assert result is None

    def test_subprocess_error_continues_to_next_candidate(self):
        """If clang.exe fails for PROGRAMFILES, tries PROGRAMFILES(X86)."""
        first_path = os.path.join(r"C:\Program Files", "LLVM", "bin", "clang.exe")
        x86_path = os.path.join(r"C:\Program Files (x86)", "LLVM", "bin", "clang.exe")
        tripwire.subprocess.mock_run(
            [first_path, "-dM", "-E", "-x", "c", "NUL"],
            raises=subprocess.SubprocessError("clang.exe crashed"),
        )
        tripwire.subprocess.mock_run(
            [x86_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 21\n",
        )
        with (
            tripwire,
            patch("headerkit._clang._version.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
            patch("headerkit._clang._version.os.path.isfile", return_value=True),
        ):
            result = _try_windows_program_files()
        assert result == "21"
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[first_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=AnyThing,
            stdout=AnyThing,
            stderr=AnyThing,
        )
        tripwire.assert_interaction(
            tripwire.subprocess.run,
            command=[x86_path, "-dM", "-E", "-x", "c", "NUL"],
            returncode=0,
            stdout="#define __clang_major__ 21\n",
            stderr="",
        )
