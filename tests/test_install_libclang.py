"""Tests for headerkit.install_libclang module."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import bigfoot

from headerkit.install_libclang import (
    _WINDOWS_LIBCLANG_DLL,
    _WINDOWS_LLVM_BIN,
    DEFAULT_LLVM_VERSION,
    _try_pip_install_libclang,
    auto_install,
    install_linux,
    install_macos,
    install_windows,
    main,
    verify_libclang,
)


class TestInstallLinux:
    def test_install_linux_dnf_clang_libs(self) -> None:
        """When dnf is available, install_linux tries clang-libs first."""
        bigfoot.subprocess_mock.mock_which("dnf", returns="/usr/bin/dnf")
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-libs"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="dnf",
            returns="/usr/bin/dnf",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-libs"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_install_linux_dnf_clang_libs_fails_falls_back_to_clang_devel(self) -> None:
        """When clang-libs fails, install_linux falls back to clang-devel."""
        bigfoot.subprocess_mock.mock_which("dnf", returns="/usr/bin/dnf")
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-libs"], returncode=1)
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-devel"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="dnf",
            returns="/usr/bin/dnf",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-libs"],
            returncode=1,
            stdout="",
            stderr="",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-devel"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_install_linux_apt(self) -> None:
        """When dnf is unavailable but apt-get is, install_linux uses apt-get."""
        bigfoot.subprocess_mock.mock_which("apt-get", returns="/usr/bin/apt-get")
        bigfoot.subprocess_mock.mock_run(["apt-get", "update", "-qq"], returncode=0)
        bigfoot.subprocess_mock.mock_run(["apt-get", "install", "-y", "libclang-dev"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="dnf", returns=None)
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="apt-get",
            returns="/usr/bin/apt-get",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "update", "-qq"],
            returncode=0,
            stdout="",
            stderr="",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "install", "-y", "libclang-dev"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_install_linux_apk(self) -> None:
        """When dnf and apt-get are unavailable but apk is, install_linux uses apk."""
        bigfoot.subprocess_mock.mock_which("apk", returns="/sbin/apk")
        bigfoot.subprocess_mock.mock_run(["apk", "add", "clang-dev"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="dnf", returns=None)
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="apt-get", returns=None)
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="apk",
            returns="/sbin/apk",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apk", "add", "clang-dev"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_install_linux_no_package_manager(self) -> None:
        """When no package manager is available, install_linux returns False."""
        # Access the proxy to ensure the plugin is created and registered before
        # sandbox entry. Without this, subprocess.run would not be intercepted.
        bigfoot.subprocess_mock.install()  # no mocks; any call raises UnmockedInteractionError

        with bigfoot:
            result = install_linux()

        assert result is False
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="dnf", returns=None)
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="apt-get", returns=None)
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="apk", returns=None)

    def test_install_linux_dnf_fails_falls_through_to_apt(self) -> None:
        """When both dnf packages fail, install_linux falls through to apt-get."""
        bigfoot.subprocess_mock.mock_which("dnf", returns="/usr/bin/dnf")
        bigfoot.subprocess_mock.mock_which("apt-get", returns="/usr/bin/apt-get")
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-libs"], returncode=1)
        bigfoot.subprocess_mock.mock_run(["dnf", "install", "-y", "clang-devel"], returncode=1)
        bigfoot.subprocess_mock.mock_run(["apt-get", "update", "-qq"], returncode=0)
        bigfoot.subprocess_mock.mock_run(["apt-get", "install", "-y", "libclang-dev"], returncode=0)

        with bigfoot:
            result = install_linux()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="dnf",
            returns="/usr/bin/dnf",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-libs"],
            returncode=1,
            stdout="",
            stderr="",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["dnf", "install", "-y", "clang-devel"],
            returncode=1,
            stdout="",
            stderr="",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="apt-get",
            returns="/usr/bin/apt-get",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "update", "-qq"],
            returncode=0,
            stdout="",
            stderr="",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["apt-get", "install", "-y", "libclang-dev"],
            returncode=0,
            stdout="",
            stderr="",
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
            returns="/opt/homebrew/bin/brew",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["brew", "install", "llvm"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_install_macos_no_brew(self) -> None:
        """When brew is unavailable, install_macos returns False."""
        bigfoot.subprocess_mock.install()  # no mocks; any call raises UnmockedInteractionError

        with bigfoot:
            result = install_macos()

        assert result is False
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="brew", returns=None)


class TestInstallWindows:
    def test_install_windows_x64(self) -> None:
        """On x64 Windows with choco (no pre-installed LLVM), install_windows pins the LLVM version."""
        bigfoot.subprocess_mock.mock_which("choco", returns=r"C:\ProgramData\chocolatey\bin\choco.exe")
        bigfoot.subprocess_mock.mock_run(
            ["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
            returncode=0,
        )

        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False),
            patch("headerkit.install_libclang.os.path.isfile", return_value=False),
            patch("headerkit.install_libclang.os.path.isdir", return_value=True),
            patch("headerkit.install_libclang._configure_windows_dll_path"),
            bigfoot,
        ):
            result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="choco",
            returns=r"C:\ProgramData\chocolatey\bin\choco.exe",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_install_windows_x64_no_choco(self) -> None:
        """On x64 Windows without choco (no pre-installed LLVM), install_windows returns False."""
        bigfoot.subprocess_mock.install()

        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False),
            patch("headerkit.install_libclang.os.path.isfile", return_value=False),
            bigfoot,
        ):
            result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is False
        bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="choco", returns=None)

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
            returncode=0,
            stdout="",
            stderr="",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[
                "powershell",
                "-Command",
                f"Start-Process '{expected_installer}' -ArgumentList '/S' -Wait",
            ],
            returncode=0,
            stdout="",
            stderr="",
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


class TestAutoInstall:
    """Tests for auto_install() - quiet, non-interactive libclang installer."""

    @patch("headerkit.install_libclang.verify_libclang", return_value=True)
    def test_already_available_is_noop(self, mock_verify: MagicMock) -> None:
        """auto_install() returns True without installing if libclang already works."""
        result = auto_install()

        assert result is True
        mock_verify.assert_called_once_with(quiet=True)

    @patch("headerkit.install_libclang.verify_libclang", side_effect=[False, True])
    @patch("headerkit.install_libclang.install_linux", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_installs_on_linux_and_verifies(
        self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock
    ) -> None:
        """auto_install() installs on Linux, then verifies. Returns True on success."""
        mock_sys.platform = "linux"
        result = auto_install()

        assert result is True
        mock_install.assert_called_once_with(quiet=True)
        assert mock_verify.call_count == 2

    @patch("headerkit.install_libclang.verify_libclang", side_effect=[False, True])
    @patch("headerkit.install_libclang.install_macos", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_installs_on_macos_and_verifies(
        self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock
    ) -> None:
        """auto_install() installs on macOS, then verifies. Returns True on success."""
        mock_sys.platform = "darwin"
        result = auto_install()

        assert result is True
        mock_install.assert_called_once_with(quiet=True)
        assert mock_verify.call_count == 2

    @patch("headerkit.install_libclang.verify_libclang", side_effect=[False, True])
    @patch("headerkit.install_libclang.install_windows", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_installs_on_windows_and_verifies(
        self, mock_sys: MagicMock, mock_install: MagicMock, mock_verify: MagicMock
    ) -> None:
        """auto_install() installs on Windows, then verifies. Returns True on success."""
        mock_sys.platform = "win32"
        result = auto_install()

        assert result is True
        mock_install.assert_called_once_with(DEFAULT_LLVM_VERSION, quiet=True)
        assert mock_verify.call_count == 2

    @patch("headerkit.install_libclang._try_pip_install_libclang", return_value=False)
    @patch("headerkit.install_libclang.verify_libclang", return_value=False)
    @patch("headerkit.install_libclang.install_linux", return_value=False)
    @patch("headerkit.install_libclang.sys")
    def test_returns_false_when_install_fails(
        self,
        mock_sys: MagicMock,
        mock_install: MagicMock,
        mock_verify: MagicMock,
        mock_pip: MagicMock,
    ) -> None:
        """auto_install() returns False when platform installer and pip fallback both fail."""
        mock_sys.platform = "linux"
        result = auto_install()

        assert result is False
        mock_install.assert_called_once_with(quiet=True)
        mock_pip.assert_called_once_with(quiet=True)

    @patch("headerkit.install_libclang._try_pip_install_libclang", return_value=False)
    @patch("headerkit.install_libclang.verify_libclang", side_effect=[False, False])
    @patch("headerkit.install_libclang.install_linux", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_returns_false_when_verify_fails_after_install(
        self,
        mock_sys: MagicMock,
        mock_install: MagicMock,
        mock_verify: MagicMock,
        mock_pip: MagicMock,
    ) -> None:
        """auto_install() returns False when install succeeds but verify fails, and pip fails."""
        mock_sys.platform = "linux"
        result = auto_install()

        assert result is False
        mock_install.assert_called_once_with(quiet=True)
        mock_pip.assert_called_once_with(quiet=True)

    @patch("headerkit.install_libclang._try_pip_install_libclang", return_value=False)
    @patch("headerkit.install_libclang.install_windows")
    @patch("headerkit.install_libclang.install_macos")
    @patch("headerkit.install_libclang.install_linux")
    @patch("headerkit.install_libclang.verify_libclang", return_value=False)
    @patch("headerkit.install_libclang.sys")
    def test_returns_false_on_unsupported_platform(
        self,
        mock_sys: MagicMock,
        mock_verify: MagicMock,
        mock_install_linux: MagicMock,
        mock_install_macos: MagicMock,
        mock_install_windows: MagicMock,
        mock_pip: MagicMock,
    ) -> None:
        """auto_install() returns False on an unsupported platform."""
        mock_sys.platform = "freebsd"
        result = auto_install()

        assert result is False
        mock_install_linux.assert_not_called()
        mock_install_macos.assert_not_called()
        mock_install_windows.assert_not_called()
        mock_pip.assert_not_called()

    @patch("headerkit.install_libclang.verify_libclang", side_effect=[False, True])
    @patch("headerkit.install_libclang._try_pip_install_libclang", return_value=True)
    @patch("headerkit.install_libclang.install_linux", return_value=False)
    @patch("headerkit.install_libclang.sys")
    def test_pip_fallback_when_platform_install_fails(
        self,
        mock_sys: MagicMock,
        mock_install: MagicMock,
        mock_pip: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        """auto_install() tries pip when platform installer fails, and succeeds.

        When ok=False, the `ok and verify()` short-circuits so verify is
        only called twice: initial check (False) and post-pip check (True).
        """
        mock_sys.platform = "linux"
        result = auto_install()

        assert result is True
        mock_install.assert_called_once_with(quiet=True)
        mock_pip.assert_called_once_with(quiet=True)
        assert mock_verify.call_count == 2

    @patch("headerkit.install_libclang.verify_libclang", side_effect=[False, False, True])
    @patch("headerkit.install_libclang._try_pip_install_libclang", return_value=True)
    @patch("headerkit.install_libclang.install_linux", return_value=True)
    @patch("headerkit.install_libclang.sys")
    def test_pip_fallback_when_verify_fails_after_platform_install(
        self,
        mock_sys: MagicMock,
        mock_install: MagicMock,
        mock_pip: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        """auto_install() tries pip when platform install succeeds but verify fails."""
        mock_sys.platform = "linux"
        result = auto_install()

        assert result is True
        mock_install.assert_called_once_with(quiet=True)
        mock_pip.assert_called_once_with(quiet=True)


class TestTryPipInstallLibclang:
    """Tests for _try_pip_install_libclang() - pip fallback installer."""

    def test_pip_install_success(self) -> None:
        """_try_pip_install_libclang() returns True when pip succeeds."""
        bigfoot.subprocess_mock.mock_run(
            [sys.executable, "-m", "pip", "install", "libclang"],
            returncode=0,
        )

        with bigfoot:
            result = _try_pip_install_libclang()

        assert result is True
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[sys.executable, "-m", "pip", "install", "libclang"],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_pip_install_failure(self) -> None:
        """_try_pip_install_libclang() returns False when pip fails."""
        bigfoot.subprocess_mock.mock_run(
            [sys.executable, "-m", "pip", "install", "libclang"],
            returncode=1,
        )

        with bigfoot:
            result = _try_pip_install_libclang()

        assert result is False
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=[sys.executable, "-m", "pip", "install", "libclang"],
            returncode=1,
            stdout="",
            stderr="",
        )


class TestInstallWindowsPreInstalled:
    """Tests for Windows pre-installed LLVM detection."""

    def test_detects_pre_installed_llvm(self) -> None:
        """install_windows returns True when libclang.dll already exists."""
        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False),
            patch("headerkit.install_libclang.os.path.isfile", return_value=True) as mock_isfile,
            patch("headerkit.install_libclang._configure_windows_dll_path") as mock_configure,
        ):
            result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is True
        mock_isfile.assert_called_once_with(_WINDOWS_LIBCLANG_DLL)
        mock_configure.assert_called_once_with(_WINDOWS_LLVM_BIN, quiet=False)

    def test_falls_through_to_choco_when_not_pre_installed(self) -> None:
        """install_windows tries Chocolatey when pre-installed LLVM is absent."""
        bigfoot.subprocess_mock.mock_which("choco", returns=r"C:\ProgramData\chocolatey\bin\choco.exe")
        bigfoot.subprocess_mock.mock_run(
            ["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
            returncode=0,
        )

        with (
            patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}, clear=False),
            patch("headerkit.install_libclang.os.path.isfile", return_value=False),
            patch("headerkit.install_libclang.os.path.isdir", return_value=True),
            patch("headerkit.install_libclang._configure_windows_dll_path") as mock_configure,
            bigfoot,
        ):
            result = install_windows(DEFAULT_LLVM_VERSION)

        assert result is True
        mock_configure.assert_called_once_with(_WINDOWS_LLVM_BIN, quiet=False)
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.which,
            name="choco",
            returns=r"C:\ProgramData\chocolatey\bin\choco.exe",
        )
        bigfoot.assert_interaction(
            bigfoot.subprocess_mock.run,
            command=["choco", "install", "llvm", f"--version={DEFAULT_LLVM_VERSION}", "-y"],
            returncode=0,
            stdout="",
            stderr="",
        )
