"""Tests for target triple detection and resolution."""

from __future__ import annotations

import pytest

from headerkit._target import (
    _construct_triple_from_python,
    detect_host_triple,
    normalize_triple,
    resolve_target,
    short_target,
)


class TestDetectHostTriple:
    """Tests for detect_host_triple()."""

    def test_returns_string(self) -> None:
        """detect_host_triple() returns a non-empty string."""
        result = detect_host_triple()
        assert isinstance(result, str)
        assert len(result) > 0


class TestConstructTripleFromPython:
    """Tests for _construct_triple_from_python()."""

    def test_returns_string(self) -> None:
        """_construct_triple_from_python() returns a non-empty string."""
        result = _construct_triple_from_python()
        assert isinstance(result, str)
        assert len(result) > 0
        # Should have at least 3 hyphen-separated components
        parts = result.split("-")
        assert len(parts) >= 3


class TestNormalizeTriple:
    """Tests for normalize_triple()."""

    def test_lowercases(self) -> None:
        """Normalizes to lowercase."""
        assert normalize_triple("X86_64-PC-LINUX-GNU") == "x86_64-pc-linux-gnu"

    def test_normalizes_arm64_to_aarch64(self) -> None:
        """arm64 arch alias is normalized to aarch64."""
        assert normalize_triple("arm64-apple-darwin") == "aarch64-apple-darwin"

    def test_normalizes_amd64_to_x86_64(self) -> None:
        """AMD64 arch alias is normalized to x86_64."""
        assert normalize_triple("AMD64-pc-windows-msvc") == "x86_64-pc-windows-msvc"

    def test_rejects_single_component(self) -> None:
        """Single component raises ValueError."""
        with pytest.raises(ValueError, match="at least 3"):
            normalize_triple("x86_64")

    def test_rejects_two_components(self) -> None:
        """Two components raises ValueError."""
        with pytest.raises(ValueError, match="at least 3"):
            normalize_triple("x86_64-linux")

    def test_accepts_three_components(self) -> None:
        """Three components are valid."""
        result = normalize_triple("x86_64-apple-darwin")
        assert result == "x86_64-apple-darwin"

    def test_accepts_four_components(self) -> None:
        """Four components are valid."""
        result = normalize_triple("x86_64-unknown-linux-gnu")
        assert result == "x86_64-unknown-linux-gnu"


class TestResolveTarget:
    """Tests for resolve_target()."""

    def test_kwarg_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit target kwarg overrides env var and default."""
        monkeypatch.setenv("HEADERKIT_TARGET", "aarch64-apple-darwin")
        result = resolve_target(target="x86_64-pc-linux-gnu")
        assert result == "x86_64-pc-linux-gnu"

    def test_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HEADERKIT_TARGET env var is used when no kwarg."""
        monkeypatch.setenv("HEADERKIT_TARGET", "aarch64-apple-darwin")
        result = resolve_target()
        assert result == "aarch64-apple-darwin"

    def test_env_var_not_set_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no kwarg and no env var, falls back to detection."""
        monkeypatch.delenv("HEADERKIT_TARGET", raising=False)
        result = resolve_target()
        # Should return something from detect_host_triple
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

    def test_short_triple(self) -> None:
        """Triples with fewer than 3 components pass through."""
        assert short_target("x86_64-linux") == "x86_64-linux"
