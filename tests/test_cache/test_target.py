"""Tests for target triple detection and resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from headerkit._target import (
    _is_musl_linux,
    detect_process_triple,
    normalize_triple,
    resolve_target,
    short_target,
)


class TestDetectProcessTriple:
    """Tests for detect_process_triple()."""

    def test_returns_string(self) -> None:
        result = detect_process_triple()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_has_at_least_3_components(self) -> None:
        result = detect_process_triple()
        assert len(result.split("-")) >= 3

    def test_host_gnu_type_used_on_posix(self) -> None:
        """HOST_GNU_TYPE is the primary signal on POSIX."""
        with patch(
            "headerkit._target.sysconfig.get_config_var",
            return_value="x86_64-pc-linux-gnu",
        ):
            result = detect_process_triple()
            assert result == "x86_64-pc-linux-gnu"

    def test_host_gnu_type_lowercased(self) -> None:
        with patch(
            "headerkit._target.sysconfig.get_config_var",
            return_value="X86_64-PC-LINUX-GNU",
        ):
            result = detect_process_triple()
            assert result == "x86_64-pc-linux-gnu"

    def test_host_gnu_type_darwin(self) -> None:
        with patch(
            "headerkit._target.sysconfig.get_config_var",
            return_value="aarch64-apple-darwin",
        ):
            result = detect_process_triple()
            assert result == "aarch64-apple-darwin"

    def test_host_gnu_type_freebsd_with_version(self) -> None:
        with patch(
            "headerkit._target.sysconfig.get_config_var",
            return_value="x86_64-unknown-freebsd14.0",
        ):
            result = detect_process_triple()
            assert result == "x86_64-unknown-freebsd14.0"

    def test_windows_win_amd64(self) -> None:
        with (
            patch("headerkit._target.sysconfig.get_config_var", return_value=None),
            patch("headerkit._target.sysconfig.get_platform", return_value="win-amd64"),
        ):
            result = detect_process_triple()
            assert result == "x86_64-pc-windows-msvc"

    def test_windows_win32(self) -> None:
        with (
            patch("headerkit._target.sysconfig.get_config_var", return_value=None),
            patch("headerkit._target.sysconfig.get_platform", return_value="win32"),
        ):
            result = detect_process_triple()
            assert result == "i686-pc-windows-msvc"

    def test_windows_win_arm64(self) -> None:
        with (
            patch("headerkit._target.sysconfig.get_config_var", return_value=None),
            patch("headerkit._target.sysconfig.get_platform", return_value="win-arm64"),
        ):
            result = detect_process_triple()
            assert result == "aarch64-pc-windows-msvc"

    def test_fallback_when_no_host_gnu_type_and_not_windows(self) -> None:
        """Falls back to platform.machine() when HOST_GNU_TYPE is None."""
        with (
            patch("headerkit._target.sysconfig.get_config_var", return_value=None),
            patch("headerkit._target.sysconfig.get_platform", return_value="unknown"),
            patch("headerkit._target.platform_mod.machine", return_value="x86_64"),
            patch("headerkit._target.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            result = detect_process_triple()
            assert result == "x86_64-unknown-linux"


class TestMuslDetection:
    """Tests for _is_musl_linux() and musl correction in detect_process_triple."""

    def test_glibc_returns_false(self) -> None:
        """glibc responds to CS_GNU_LIBC_VERSION, so _is_musl_linux is False."""
        with (
            patch("headerkit._target.sys") as mock_sys,
            patch("headerkit._target.os.confstr", create=True, return_value="glibc 2.35"),
        ):
            mock_sys.platform = "linux"
            assert _is_musl_linux() is False

    def test_musl_returns_true(self) -> None:
        """musl raises ValueError for CS_GNU_LIBC_VERSION."""
        with (
            patch("headerkit._target.sys") as mock_sys,
            patch("headerkit._target.os.confstr", create=True, side_effect=ValueError),
        ):
            mock_sys.platform = "linux"
            assert _is_musl_linux() is True

    def test_musl_oserror_returns_true(self) -> None:
        """Some musl builds raise OSError instead of ValueError."""
        with (
            patch("headerkit._target.sys") as mock_sys,
            patch("headerkit._target.os.confstr", create=True, side_effect=OSError),
        ):
            mock_sys.platform = "linux"
            assert _is_musl_linux() is True

    def test_non_linux_returns_false(self) -> None:
        with patch("headerkit._target.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert _is_musl_linux() is False

    def test_no_confstr_returns_false(self) -> None:
        """If os.confstr is not available (Windows), assume not musl."""
        with (
            patch("headerkit._target.sys") as mock_sys,
            patch("headerkit._target.os.confstr", create=True, side_effect=AttributeError),
        ):
            mock_sys.platform = "linux"
            assert _is_musl_linux() is False

    def test_detect_corrects_gnu_to_musl(self) -> None:
        """detect_process_triple corrects linux-gnu to linux-musl on musl systems."""
        with (
            patch(
                "headerkit._target.sysconfig.get_config_var",
                return_value="x86_64-pc-linux-gnu",
            ),
            patch("headerkit._target._is_musl_linux", return_value=True),
        ):
            result = detect_process_triple()
            assert result == "x86_64-pc-linux-musl"

    def test_detect_keeps_gnu_on_glibc(self) -> None:
        """detect_process_triple keeps linux-gnu on glibc systems."""
        with (
            patch(
                "headerkit._target.sysconfig.get_config_var",
                return_value="x86_64-pc-linux-gnu",
            ),
            patch("headerkit._target._is_musl_linux", return_value=False),
        ):
            result = detect_process_triple()
            assert result == "x86_64-pc-linux-gnu"

    def test_detect_already_musl_not_double_corrected(self) -> None:
        """If HOST_GNU_TYPE already says musl (3.13+), don't double-correct."""
        with patch(
            "headerkit._target.sysconfig.get_config_var",
            return_value="x86_64-pc-linux-musl",
        ):
            result = detect_process_triple()
            assert result == "x86_64-pc-linux-musl"


class TestNormalizeTriple:
    """Tests for normalize_triple()."""

    def test_lowercases(self) -> None:
        assert normalize_triple("X86_64-PC-LINUX-GNU") == "x86_64-pc-linux-gnu"

    def test_normalizes_arm64_to_aarch64(self) -> None:
        assert normalize_triple("arm64-apple-darwin") == "aarch64-apple-darwin"

    def test_normalizes_amd64_to_x86_64(self) -> None:
        assert normalize_triple("AMD64-pc-windows-msvc") == "x86_64-pc-windows-msvc"

    def test_inserts_unknown_vendor(self) -> None:
        assert normalize_triple("x86_64-linux-gnu") == "x86_64-unknown-linux-gnu"

    def test_keeps_known_vendor(self) -> None:
        assert normalize_triple("x86_64-pc-linux-gnu") == "x86_64-pc-linux-gnu"

    def test_rejects_single_component(self) -> None:
        with pytest.raises(ValueError, match="at least 3"):
            normalize_triple("x86_64")

    def test_rejects_two_components(self) -> None:
        with pytest.raises(ValueError, match="at least 3"):
            normalize_triple("x86_64-linux")

    def test_accepts_three_components(self) -> None:
        assert normalize_triple("x86_64-apple-darwin") == "x86_64-apple-darwin"

    def test_accepts_four_components(self) -> None:
        assert normalize_triple("x86_64-unknown-linux-gnu") == "x86_64-unknown-linux-gnu"


class TestResolveTarget:
    """Tests for resolve_target()."""

    def test_kwarg_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HEADERKIT_TARGET", "aarch64-apple-darwin")
        result = resolve_target(target="x86_64-pc-linux-gnu")
        assert result == "x86_64-pc-linux-gnu"

    def test_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HEADERKIT_TARGET", "aarch64-apple-darwin")
        result = resolve_target()
        assert result == "aarch64-apple-darwin"

    def test_env_var_not_set_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HEADERKIT_TARGET", raising=False)
        result = resolve_target()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_kwarg_normalized(self) -> None:
        """User-provided triples are normalized."""
        result = resolve_target(target="arm64-apple-darwin")
        assert result == "aarch64-apple-darwin"

    def test_env_var_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HEADERKIT_TARGET", "AMD64-pc-linux-gnu")
        result = resolve_target()
        assert result == "x86_64-pc-linux-gnu"


class TestShortTarget:
    """Tests for short_target()."""

    def test_linux(self) -> None:
        assert short_target("x86_64-pc-linux-gnu") == "x86_64-linux"

    def test_darwin(self) -> None:
        assert short_target("aarch64-apple-darwin") == "aarch64-darwin"

    def test_windows(self) -> None:
        assert short_target("x86_64-pc-windows-msvc") == "x86_64-windows"

    def test_freebsd(self) -> None:
        assert short_target("x86_64-unknown-freebsd") == "x86_64-freebsd"

    def test_four_component(self) -> None:
        assert short_target("armv7-unknown-linux-gnueabihf") == "armv7-linux"

    def test_darwin_versioned(self) -> None:
        assert short_target("aarch64-apple-darwin25.3.0") == "aarch64-darwin"

    def test_musl(self) -> None:
        assert short_target("x86_64-pc-linux-musl") == "x86_64-linux"

    def test_freebsd_versioned(self) -> None:
        assert short_target("x86_64-unknown-freebsd14.0") == "x86_64-freebsd"
