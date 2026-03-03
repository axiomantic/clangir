"""Tests for headerkit.install_libclang module."""

from __future__ import annotations

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, call, patch

from headerkit.install_libclang import (
    DEFAULT_LLVM_VERSION,
    install_linux,
    install_macos,
    install_windows,
    main,
    verify_libclang,
)


def _completed(returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


class TestInstallLinux:
    @patch("headerkit.install_libclang.subprocess.run", return_value=_completed(0))
    @patch("headerkit.install_libclang.shutil.which")
    def test_install_linux_dnf(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """When dnf is available, install_linux runs 'dnf install -y clang-devel'."""
        mock_which.side_effect = lambda name: "/usr/bin/dnf" if name == "dnf" else None

        result = install_linux()

        assert result is True
        mock_run.assert_called_once_with(["dnf", "install", "-y", "clang-devel"], check=False, text=True)

    @patch("headerkit.install_libclang.subprocess.run", return_value=_completed(0))
    @patch("headerkit.install_libclang.shutil.which")
    def test_install_linux_apt(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """When dnf is unavailable but apt-get is, install_linux uses apt-get."""
        mock_which.side_effect = lambda name: "/usr/bin/apt-get" if name == "apt-get" else None

        result = install_linux()

        assert result is True
        assert mock_run.call_count == 2
        calls = mock_run.call_args_list
        assert calls[0] == call(["apt-get", "update", "-qq"], check=False, text=True)
        assert calls[1] == call(["apt-get", "install", "-y", "libclang-dev"], check=False, text=True)

    @patch("headerkit.install_libclang.subprocess.run", return_value=_completed(0))
    @patch("headerkit.install_libclang.shutil.which")
    def test_install_linux_apk(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """When dnf and apt-get are unavailable but apk is, install_linux uses apk."""
        mock_which.side_effect = lambda name: "/sbin/apk" if name == "apk" else None

        result = install_linux()

        assert result is True
        mock_run.assert_called_once_with(["apk", "add", "clang-dev"], check=False, text=True)

    @patch("headerkit.install_libclang.subprocess.run")
    @patch("headerkit.install_libclang.shutil.which", return_value=None)
    def test_install_linux_no_package_manager(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """When no package manager is available, install_linux returns False."""
        result = install_linux()

        assert result is False
        mock_run.assert_not_called()

    def test_install_linux_dnf_fails_falls_through_to_apt(self) -> None:
        def which_side_effect(cmd: str) -> str | None:
            if cmd in ("dnf", "apt-get"):
                return f"/usr/bin/{cmd}"
            return None

        def run_side_effect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            cmd = args[0] if args else kwargs.get("args", [])
            if "dnf" in cmd[0]:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("headerkit.install_libclang.shutil.which", side_effect=which_side_effect),
            patch("headerkit.install_libclang.subprocess.run", side_effect=run_side_effect) as mock_run,
        ):
            result = install_linux()
            assert result is True
            calls = mock_run.call_args_list
            apt_update = [c for c in calls if c.args[0] == ["apt-get", "update", "-qq"]]
            apt_install = [c for c in calls if c.args[0] == ["apt-get", "install", "-y", "libclang-dev"]]
            assert len(apt_update) == 1, "Expected one apt-get update call"
            assert len(apt_install) == 1, "Expected one apt-get install call"


class TestInstallMacos:
    @patch("headerkit.install_libclang.subprocess.run", return_value=_completed(0))
    @patch("headerkit.install_libclang.shutil.which", return_value="/opt/homebrew/bin/brew")
    def test_install_macos_success(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """When brew is available, install_macos runs 'brew install llvm'."""
        result = install_macos()

        assert result is True
        mock_run.assert_called_once_with(["brew", "install", "llvm"], check=False, text=True)

    @patch("headerkit.install_libclang.subprocess.run")
    @patch("headerkit.install_libclang.shutil.which", return_value=None)
    def test_install_macos_no_brew(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """When brew is unavailable, install_macos returns False."""
        result = install_macos()

        assert result is False
        mock_run.assert_not_called()


class TestInstallWindows:
    @patch("headerkit.install_libclang.subprocess.run", return_value=_completed(0))
    @patch("headerkit.install_libclang.shutil.which", return_value=r"C:\ProgramData\chocolatey\bin\choco.exe")
    @patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False)
    def test_install_windows_x64(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """On x64 Windows with choco, install_windows pins the LLVM version."""
        result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is True
        mock_run.assert_called_once_with(
            ["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
            check=False,
            text=True,
        )

    @patch("headerkit.install_libclang.subprocess.run")
    @patch("headerkit.install_libclang.shutil.which", return_value=None)
    @patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False)
    def test_install_windows_x64_no_choco(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """On x64 Windows without choco, install_windows returns False."""
        result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is False
        mock_run.assert_not_called()

    @patch("headerkit.install_libclang.os.remove")
    @patch("headerkit.install_libclang.subprocess.run", return_value=_completed(0))
    @patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "ARM64"}, clear=False)
    def test_install_windows_arm64(self, mock_run: MagicMock, mock_remove: MagicMock) -> None:
        """On ARM64 Windows, install_windows downloads and runs the LLVM installer."""
        version = "18.1.0"
        result = install_windows(version)

        assert result is True
        expected_installer = os.path.join(tempfile.gettempdir(), "llvm-installer.exe")
        # First call: curl download
        curl_call = mock_run.call_args_list[0]
        assert curl_call == call(
            [
                "curl",
                "-sSL",
                "-o",
                expected_installer,
                f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-woa64.exe",
            ],
            check=False,
            text=True,
        )
        # Second call: powershell install
        powershell_call = mock_run.call_args_list[1]
        assert powershell_call == call(
            [
                "powershell",
                "-Command",
                f"Start-Process '{expected_installer}' -ArgumentList '/S' -Wait",
            ],
            check=False,
            text=True,
        )
        mock_remove.assert_called_once_with(expected_installer)


class TestVerifyLibclang:
    def test_verify_libclang_success(self) -> None:
        """When is_system_libclang_available returns True, verify_libclang returns True."""
        # Patch the import inside verify_libclang
        mock_module = MagicMock()
        mock_module.is_system_libclang_available.return_value = True
        with patch.dict("sys.modules", {"headerkit.backends.libclang": mock_module}):
            result = verify_libclang()

        assert result is True
        mock_module.is_system_libclang_available.assert_called_once_with()

    def test_verify_libclang_failure(self) -> None:
        """When is_system_libclang_available returns False, verify_libclang returns False."""
        mock_module = MagicMock()
        mock_module.is_system_libclang_available.return_value = False
        with patch.dict("sys.modules", {"headerkit.backends.libclang": mock_module}):
            result = verify_libclang()

        assert result is False
        mock_module.is_system_libclang_available.assert_called_once_with()


class TestMain:
    @patch("headerkit.install_libclang.verify_libclang", return_value=True)
    @patch("headerkit.install_libclang.install_linux", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_main_linux(self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock) -> None:
        """On linux, main() calls install_linux."""
        mock_sys.platform = "linux"
        # argparse uses sys.argv if argv is None, but we pass [] explicitly
        result = main([])

        mock_install.assert_called_once_with()
        mock_verify.assert_called_once_with()
        assert result == 0

    @patch("headerkit.install_libclang.verify_libclang", return_value=True)
    @patch("headerkit.install_libclang.install_macos", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_main_macos(self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock) -> None:
        """On darwin, main() calls install_macos."""
        mock_sys.platform = "darwin"
        result = main([])

        mock_install.assert_called_once_with()
        mock_verify.assert_called_once_with()
        assert result == 0

    @patch("headerkit.install_libclang.sys")
    def test_main_unsupported_platform(self, mock_sys: MagicMock) -> None:
        """On an unsupported platform, main() returns 1."""
        mock_sys.platform = "freebsd"
        result = main([])

        assert result == 1

    @patch("headerkit.install_libclang.verify_libclang", return_value=False)
    @patch("headerkit.install_libclang.install_linux", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_main_verification_failure_returns_1(
        self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock
    ) -> None:
        """When install succeeds but verify fails, main() returns 1."""
        mock_sys.platform = "linux"
        result = main([])

        mock_install.assert_called_once_with()
        mock_verify.assert_called_once_with()
        assert result == 1

    @patch("headerkit.install_libclang.verify_libclang")
    @patch("headerkit.install_libclang.install_linux", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_main_skip_verify(self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock) -> None:
        """With --skip-verify, main() skips verification."""
        mock_sys.platform = "linux"
        result = main(["--skip-verify"])

        mock_install.assert_called_once_with()
        mock_verify.assert_not_called()
        assert result == 0
