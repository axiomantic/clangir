"""Tests for the PyPy compatibility monkey-patch in headerkit._clang."""

from __future__ import annotations

import platform

import pytest

from headerkit._clang import _compat_c_interop_string


class TestCompatCInteropStringInit:
    """Tests for _compat_c_interop_string construction and .value property."""

    def test_init_with_string(self) -> None:
        obj = _compat_c_interop_string("hello")
        assert obj.value == "hello"

    def test_init_with_bytes(self) -> None:
        obj = _compat_c_interop_string(b"hello")
        assert obj.value == "hello"

    def test_init_with_none(self) -> None:
        # None is coerced to empty bytes, so value is "" and a buffer is allocated
        obj = _compat_c_interop_string(None)
        assert obj.value == ""
        assert obj._buffer is not None, "Buffer must be allocated even for None input"

    def test_init_default(self) -> None:
        obj = _compat_c_interop_string()
        assert obj.value == ""


class TestCompatCInteropStringStr:
    """Tests for _compat_c_interop_string __str__ behavior."""

    def test_str(self) -> None:
        obj = _compat_c_interop_string("world")
        assert str(obj) == "world"

    def test_str_empty(self) -> None:
        obj = _compat_c_interop_string()
        assert str(obj) == ""


class TestCompatCInteropStringFromParam:
    """Tests for _compat_c_interop_string.from_param class method."""

    def test_from_param_string(self) -> None:
        result = _compat_c_interop_string.from_param("hello")
        assert isinstance(result, _compat_c_interop_string)
        assert result.value == "hello"

    def test_from_param_bytes(self) -> None:
        result = _compat_c_interop_string.from_param(b"hello")
        assert isinstance(result, _compat_c_interop_string)
        assert result.value == "hello"

    def test_from_param_none(self) -> None:
        result = _compat_c_interop_string.from_param(None)
        assert isinstance(result, _compat_c_interop_string)
        assert result.value == ""

    def test_from_param_invalid(self) -> None:
        with pytest.raises(TypeError, match="Cannot convert 'int'"):
            _compat_c_interop_string.from_param(123)  # type: ignore[arg-type]


class TestCompatCInteropStringToPython:
    """Tests for _compat_c_interop_string.to_python_string static method."""

    def test_to_python_string(self) -> None:
        # Documents the public API contract: to_python_string is a static method
        # that extracts the string value from a _compat_c_interop_string instance.
        obj = _compat_c_interop_string("result")
        assert _compat_c_interop_string.to_python_string(obj) == "result"

    def test_to_python_string_empty(self) -> None:
        obj = _compat_c_interop_string()
        assert _compat_c_interop_string.to_python_string(obj) == ""


class TestCompatCInteropStringUnicode:
    """Tests for UTF-8 roundtrip with non-ASCII characters."""

    def test_utf8_roundtrip_accented(self) -> None:
        text = "caf\u00e9"
        obj = _compat_c_interop_string(text)
        assert obj.value == text

    def test_utf8_roundtrip_combining(self) -> None:
        # e followed by combining acute accent
        text = "cafe\u0301"
        obj = _compat_c_interop_string(text)
        assert obj.value == text

    def test_utf8_roundtrip_emoji(self) -> None:
        text = "hello \U0001f600"
        obj = _compat_c_interop_string(text)
        assert obj.value == text

    def test_utf8_roundtrip_cjk(self) -> None:
        text = "\u4f60\u597d\u4e16\u754c"
        obj = _compat_c_interop_string(text)
        assert obj.value == text


class TestMonkeyPatchMechanism:
    """Tests for the _NEEDS_INTEROP_STRING_PATCH flag and monkey-patch logic."""

    def test_patch_flag_exists(self) -> None:
        import headerkit._clang

        assert hasattr(headerkit._clang, "_NEEDS_INTEROP_STRING_PATCH")
        expected = platform.python_implementation() != "CPython"
        assert expected == headerkit._clang._NEEDS_INTEROP_STRING_PATCH

    def test_patch_not_applied_on_cpython(self) -> None:
        """On CPython, c_interop_string should remain the original class."""
        if platform.python_implementation() != "CPython":
            pytest.skip("Only relevant on CPython")

        import headerkit._clang
        from headerkit._clang import get_cindex

        saved = headerkit._clang._cached_cindex
        try:
            headerkit._clang._cached_cindex = None
            cindex = get_cindex()
            assert cindex.c_interop_string is not _compat_c_interop_string
            assert cindex.c_interop_string.__name__ == "c_interop_string"
        finally:
            headerkit._clang._cached_cindex = saved

    def test_patch_applied_when_forced(self) -> None:
        """When _NEEDS_INTEROP_STRING_PATCH is forced True, the compat class is used."""
        import headerkit._clang
        from headerkit._clang import get_cindex

        saved_cindex = headerkit._clang._cached_cindex
        saved_flag = headerkit._clang._NEEDS_INTEROP_STRING_PATCH
        try:
            headerkit._clang._cached_cindex = None
            headerkit._clang._NEEDS_INTEROP_STRING_PATCH = True
            cindex = get_cindex()
            assert cindex.c_interop_string is _compat_c_interop_string
        finally:
            headerkit._clang._NEEDS_INTEROP_STRING_PATCH = saved_flag
            headerkit._clang._cached_cindex = saved_cindex
