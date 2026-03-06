"""Tests for headerkit.install_libclang module."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import bigfoot

from headerkit.install_libclang import (
    DEFAULT_LLVM_VERSION,
    install_linux,
    install_macos,
    install_windows,
    main,
    verify_libclang,
)


class TestInstallLinux:
    def test_install_linux_dnf(self) -> None:
        """When dnf is available, install_linux runs 'dnf install -y clang-devel'."""
        bigfoot.subprocess_mock.mock_which("dnf", returns="/usr/bin/dnf")
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-devel"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="dnf",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-devel"],
        )

    def test_install_linux_apt(self) -> None:
        """When dnf is unavailable but apt-get is, install_linux uses apt-get."""
        bigfoot.subprocess_mock.mock_which("apt-get", returns="/usr/bin/apt-get")
        bigfoot.subprocess_mock.mock_run(["apt-get", "update", "-qq"], returncode=0)
        bigfoot.subprocess_mock.mock_run(["apt-get", "install", "-y", "libclang-dev"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="apt-get",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "update", "-qq"],
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "install", "-y", "libclang-dev"],
        )

    def test_install_linux_apk(self) -> None:
        """When dnf and apt-get are unavailable but apk is, install_linux uses apk."""
        bigfoot.subprocess_mock.mock_which("apk", returns="/sbin/apk")
        bigfoot.subprocess_mock.mock_run(["apk", "add", "clang-dev"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="apk",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apk", "add", "clang-dev"],
        )

    def test_install_linux_no_package_manager(self) -> None:
        """When no package manager is available, install_linux returns False."""
        # Access the proxy to ensure the plugin is created and registered before
        # sandbox entry. Without this, subprocess.run would not be intercepted.
        bigfoot.subprocess_mock.install()  # no mocks; any call raises UnmockedInteractionError

        with bigfoot:
            result = install_linux()

        assert result is False
        # If subprocess.run had fired inside the sandbox, UnmockedInteractionError
        # would have raised immediately.

    def test_install_linux_dnf_fails_falls_through_to_apt(self) -> None:
        """When dnf fails (returncode=1), install_linux falls through to apt-get."""
        bigfoot.subprocess_mock.mock_which("dnf", returns="/usr/bin/dnf")
        bigfoot.subprocess_mock.mock_which("apt-get", returns="/usr/bin/apt-get")
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-devel"], returncode=1)
        bigfoot.subprocess_mock.mock_run(["apt-get", "update", "-qq"], returncode=0)
        bigfoot.subprocess_mock.mock_run(["apt-get", "install", "-y", "libclang-dev"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="dnf",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-devel"],
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="apt-get",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "update", "-qq"],
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "install", "-y", "libclang-dev"],
        )


class TestInstallMacos:
    def test_install_macos_success(self) -> None:
        """When brew is available, install_macos runs 'brew install llvm'."""
        bigfoot.subprocess_mock.mock_which("brew", returns="/opt/homebrew/bin/brew")
        bigfoot.subprocess_mock.mock_run(["brew", "install", "llvm"], returncode=0)

        with bigfoot:
            result = install_macos()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="brew",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["brew", "install", "llvm"],
        )

    def test_install_macos_no_brew(self) -> None:
        """When brew is unavailable, install_macos returns False."""
        bigfoot.subprocess_mock.install()  # no mocks; any call raises UnmockedInteractionError

        with bigfoot:
            result = install_macos()

        assert result is False


class TestInstallWindows:
    def test_install_windows_x64(self) -> None:
        """On x64 Windows with choco, install_windows pins the LLVM version."""
        bigfoot.subprocess_mock.mock_which("choco", returns=r"C:\ProgramData\chocolatey\bin\choco.exe")
        bigfoot.subprocess_mock.mock_run(
            ["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
            returncode=0,
        )

        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False),
            bigfoot,
        ):
            result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="choco",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
        )

    def test_install_windows_x64_no_choco(self) -> None:
        """On x64 Windows without choco, install_windows returns False."""
        bigfoot.subprocess_mock.install()

        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False),
            bigfoot,
        ):
            result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is False

    def test_install_windows_arm64(self) -> None:
        """On ARM64 Windows, install_windows downloads and runs the LLVM installer."""
        version = "18.1.0"
        expected_installer = os.path.join(tempfile.gettempdir(), "llvm-installer.exe")
        curl_url = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/LLVM-{version}-woa64.exe"

        bigfoot.subprocess_mock.mock_run(
            [
                "curl",
                "-sSL",
                "-o",
                expected_installer,
                curl_url,
            ],
            returncode=0,
        )
        bigfoot.subprocess_mock.mock_run(
            [
                "powershell",
                "-Command",
                f"Start-Process '{expected_installer}' -ArgumentList '/S' -Wait",
            ],
            returncode=0,
        )

        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "ARM64"}, clear=False),
            patch("headerkit.install_libclang.os.remove") as mock_remove,
            bigfoot,
        ):
            result = install_windows(version)

        assert result is True
        mock_remove.assert_called_once_with(expected_installer)
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[
                "curl",
                "-sSL",
                "-o",
                expected_installer,
                curl_url,
            ],
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[
                "powershell",
                "-Command",
                f"Start-Process '{expected_installer}' -ArgumentList '/S' -Wait",
            ],
        )


class TestVerifyLibclang:
    def test_verify_libclang_success(self) -> None:
        """When is_system_libclang_available returns True, verify_libclang returns True."""
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
