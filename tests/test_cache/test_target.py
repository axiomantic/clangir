"""Tests for target triple detection and resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from headerkit._target import (
    _construct_triple_from_python,
    _correct_arch_for_pointer_width,
    _parse_archflags,
    _parse_vscmd_tgt_arch,
    _triple_from_platform_tag,
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


class TestConstructTripleFromPython:
    """Tests for _construct_triple_from_python()."""

    def test_returns_string(self) -> None:
        result = _construct_triple_from_python()
        assert isinstance(result, str)
        parts = result.split("-")
        assert len(parts) >= 3

    def test_32bit_python_gets_32bit_arch(self) -> None:
        """32-bit Python should produce a 32-bit arch even on 64-bit host."""
        with (
            patch("headerkit._target.platform_mod.machine", return_value="x86_64"),
            patch("headerkit._target._process_pointer_bits", return_value=32),
            patch("headerkit._target.sys") as mock_sys,
        ):
            mock_sys.platform = "win32"
            result = _construct_triple_from_python()
            assert result.startswith("i686-")

    def test_64bit_python_keeps_64bit_arch(self) -> None:
        with (
            patch("headerkit._target.platform_mod.machine", return_value="x86_64"),
            patch("headerkit._target._process_pointer_bits", return_value=64),
            patch("headerkit._target.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            result = _construct_triple_from_python()
            assert result.startswith("x86_64-")


class TestCorrectArchForPointerWidth:
    """Tests for _correct_arch_for_pointer_width()."""

    def test_32bit_downgrades_x86_64(self) -> None:
        with patch("headerkit._target._process_pointer_bits", return_value=32):
            assert _correct_arch_for_pointer_width("x86_64") == "i686"

    def test_32bit_downgrades_aarch64(self) -> None:
        with patch("headerkit._target._process_pointer_bits", return_value=32):
            assert _correct_arch_for_pointer_width("aarch64") == "armv7"

    def test_64bit_keeps_x86_64(self) -> None:
        with patch("headerkit._target._process_pointer_bits", return_value=64):
            assert _correct_arch_for_pointer_width("x86_64") == "x86_64"

    def test_32bit_keeps_i686(self) -> None:
        """Already 32-bit arch is not downgraded further."""
        with patch("headerkit._target._process_pointer_bits", return_value=32):
            assert _correct_arch_for_pointer_width("i686") == "i686"


class TestParseArchflags:
    """Tests for _parse_archflags()."""

    def test_single_arch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARCHFLAGS", "-arch arm64")
        assert _parse_archflags() == "aarch64"

    def test_single_x86(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARCHFLAGS", "-arch x86_64")
        assert _parse_archflags() == "x86_64"

    def test_universal2_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple arches (universal2) returns None (ambiguous)."""
        monkeypatch.setenv("ARCHFLAGS", "-arch x86_64 -arch arm64")
        assert _parse_archflags() is None

    def test_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARCHFLAGS", raising=False)
        assert _parse_archflags() is None

    def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARCHFLAGS", "")
        assert _parse_archflags() is None


class TestParseVscmdTgtArch:
    """Tests for _parse_vscmd_tgt_arch()."""

    def test_x64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VSCMD_ARG_TGT_ARCH", "x64")
        assert _parse_vscmd_tgt_arch() == "x86_64"

    def test_x86(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VSCMD_ARG_TGT_ARCH", "x86")
        assert _parse_vscmd_tgt_arch() == "i686"

    def test_arm64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VSCMD_ARG_TGT_ARCH", "arm64")
        assert _parse_vscmd_tgt_arch() == "aarch64"

    def test_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VSCMD_ARG_TGT_ARCH", raising=False)
        assert _parse_vscmd_tgt_arch() is None


class TestTripleFromPlatformTag:
    """Tests for _triple_from_platform_tag()."""

    def test_linux_x86_64(self) -> None:
        assert _triple_from_platform_tag("linux-x86_64") == "x86_64-unknown-linux-gnu"

    def test_linux_aarch64(self) -> None:
        assert _triple_from_platform_tag("linux-aarch64") == "aarch64-unknown-linux-gnu"

    def test_macosx_arm64(self) -> None:
        assert _triple_from_platform_tag("macosx-14.0-arm64") == "aarch64-apple-darwin"

    def test_macosx_x86_64(self) -> None:
        assert _triple_from_platform_tag("macosx-10.15-x86_64") == "x86_64-apple-darwin"

    def test_win_amd64(self) -> None:
        assert _triple_from_platform_tag("win-amd64") == "x86_64-pc-windows-msvc"

    def test_win_arm64(self) -> None:
        assert _triple_from_platform_tag("win-arm64") == "aarch64-pc-windows-msvc"

    def test_freebsd(self) -> None:
        result = _triple_from_platform_tag("freebsd-14.1-release-amd64")
        assert result == "x86_64-unknown-freebsd"

    def test_win32(self) -> None:
        assert _triple_from_platform_tag("win32") == "i686-pc-windows-msvc"

    def test_universal2_returns_none(self) -> None:
        """universal2 is ambiguous (fat binary), not a real arch."""
        assert _triple_from_platform_tag("macosx-10.9-universal2") is None

    def test_single_component_returns_none(self) -> None:
        assert _triple_from_platform_tag("linux") is None

    def test_unknown_os_returns_none(self) -> None:
        assert _triple_from_platform_tag("haiku-x86_64") is None


class TestDetectProcessTripleCrossCompile:
    """Tests that detect_process_triple respects cross-compilation signals."""

    def test_python_host_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_PYTHON_HOST_PLATFORM is respected via sysconfig.get_platform()."""
        monkeypatch.setattr("headerkit._target.sysconfig.get_platform", lambda: "linux-aarch64")
        result = detect_process_triple()
        assert "aarch64" in result
        assert "linux" in result

    def test_archflags_macos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ARCHFLAGS is used when sysconfig doesn't indicate cross-compile."""
        # Patch sysconfig to return something that won't parse (force fallthrough)
        monkeypatch.setattr("headerkit._target.sysconfig.get_platform", lambda: "unknown-platform")
        monkeypatch.setenv("ARCHFLAGS", "-arch arm64")
        monkeypatch.setattr("headerkit._target.sys.platform", "darwin")
        monkeypatch.delenv("VSCMD_ARG_TGT_ARCH", raising=False)
        result = detect_process_triple()
        assert result.startswith("aarch64-")
        assert "darwin" in result


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
