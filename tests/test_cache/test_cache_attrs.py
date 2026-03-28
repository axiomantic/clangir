"""Tests for optional writer cache attributes."""

from __future__ import annotations

from headerkit.writers import get_writer


class TestWriterCacheAttributes:
    """Test that writers expose correct cache_output defaults."""

    def test_diff_writer_no_cache(self) -> None:
        writer = get_writer("diff")
        assert getattr(writer, "cache_output", True) is False

    def test_prompt_writer_no_cache(self) -> None:
        writer = get_writer("prompt")
        assert getattr(writer, "cache_output", True) is False

    def test_cffi_writer_defaults_cacheable(self) -> None:
        writer = get_writer("cffi")
        assert not hasattr(writer, "cache_output")  # relies on default True in _should_cache_output

    def test_json_writer_defaults_cacheable(self) -> None:
        writer = get_writer("json")
        assert not hasattr(writer, "cache_output")  # relies on default True in _should_cache_output

    def test_ctypes_writer_defaults_cacheable(self) -> None:
        writer = get_writer("ctypes")
        assert not hasattr(writer, "cache_output")  # relies on default True in _should_cache_output
