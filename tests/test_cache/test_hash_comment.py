"""Tests for writer hash_comment_format() methods."""

from __future__ import annotations

from headerkit.writers import get_writer


class TestHashCommentFormat:
    """Each writer that supports comments must return a valid format string."""

    def test_cffi_writer_returns_python_comment_format(self) -> None:
        """CffiWriter.hash_comment_format() returns '# {line}'."""
        writer = get_writer("cffi")
        result = writer.hash_comment_format()
        assert result == "# {line}"

    def test_ctypes_writer_returns_python_comment_format(self) -> None:
        """CtypesWriter.hash_comment_format() returns '# {line}'."""
        writer = get_writer("ctypes")
        result = writer.hash_comment_format()
        assert result == "# {line}"

    def test_cython_writer_returns_python_comment_format(self) -> None:
        """CythonWriter.hash_comment_format() returns '# {line}'."""
        writer = get_writer("cython")
        result = writer.hash_comment_format()
        assert result == "# {line}"

    def test_lua_writer_returns_lua_comment_format(self) -> None:
        """LuaWriter.hash_comment_format() returns '-- {line}'."""
        writer = get_writer("lua")
        result = writer.hash_comment_format()
        assert result == "-- {line}"

    def test_json_writer_lacks_hash_comment_format(self) -> None:
        """JsonWriter has no hash_comment_format (uses sidecar)."""
        writer = get_writer("json")
        assert getattr(writer, "hash_comment_format", None) is None

    def test_prompt_writer_lacks_hash_comment_format(self) -> None:
        """PromptWriter has no hash_comment_format (uses sidecar)."""
        writer = get_writer("prompt")
        assert getattr(writer, "hash_comment_format", None) is None

    def test_diff_writer_lacks_hash_comment_format(self) -> None:
        """DiffWriter has no hash_comment_format (uses sidecar)."""
        writer = get_writer("diff")
        assert getattr(writer, "hash_comment_format", None) is None

    def test_format_string_placeholder_is_usable(self) -> None:
        """Format strings can be used with str.format(line=...) for all writers."""
        for name, expected in (
            ("cffi", "# [headerkit-cache]"),
            ("ctypes", "# [headerkit-cache]"),
            ("cython", "# [headerkit-cache]"),
            ("lua", "-- [headerkit-cache]"),
        ):
            writer = get_writer(name)
            fmt = writer.hash_comment_format()
            result = fmt.format(line="[headerkit-cache]")
            assert result == expected, (
                f"{name} writer: format(line='[headerkit-cache]') returned {result!r}, expected {expected!r}"
            )
