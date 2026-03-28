"""Tests for slug construction and sanitization."""

from __future__ import annotations

from headerkit._slug import build_slug


class TestBuildSlugBasic:
    """Test slug construction from design doc examples."""

    def test_backend_and_header_only(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="mylib.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.mylib"

    def test_with_define(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="openssl/ssl.h",
            defines=["OPENSSL_NO_DEPRECATED"],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.ssl.d.OPENSSL_NO_DEPRECATED"

    def test_with_all_groups(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="foo.h",
            defines=["BAR"],
            includes=["/a", "/b"],
            other_args=["-std=c11"],
        )
        assert slug == "libclang.foo.d.BAR.i.a_b.args.std=c11"

    def test_multiple_defines_sorted(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="test.h",
            defines=["FOO=1", "BAR"],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.test.d.BAR_FOO=1"

    def test_multiple_includes_basenames_sorted(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="test.h",
            defines=[],
            includes=["/usr/include", "./src"],
            other_args=[],
        )
        assert slug == "libclang.test.i.include_src"

    def test_empty_groups_omitted(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="simple.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.simple"


class TestSlugSanitization:
    """Test component sanitization rules."""

    def test_dots_in_header_replaced(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="my.header.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.my-header"

    def test_slashes_in_path(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="vendor/include/lib.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        # Only basename is used
        assert slug == "libclang.lib"

    def test_backend_lowercased(self) -> None:
        slug = build_slug(
            backend_name="LibClang",
            header_path="test.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.test"

    def test_header_stem_lowercased(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="MyLib.H",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.mylib"

    def test_define_case_preserved(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="t.h",
            defines=["FOO_BAR"],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.t.d.FOO_BAR"

    def test_colon_in_path_sanitized(self) -> None:
        """Windows drive letters must be sanitized."""
        slug = build_slug(
            backend_name="libclang",
            header_path="C:\\Users\\test\\mylib.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert ":" not in slug

    def test_consecutive_dashes_collapsed(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="a--b..c.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.a-b-c"

    def test_leading_trailing_dashes_stripped(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="-test-.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        # After sanitization, stem should not start/end with dash
        parts = slug.split(".")
        header_part = parts[1]
        assert not header_part.startswith("-")
        assert not header_part.endswith("-")


class TestSlugLengthLimit:
    """Test the 120-char limit and hash fallback."""

    def test_long_defines_hashed(self) -> None:
        # 30 long defines should exceed 120 chars
        long_defines = [f"VERY_LONG_DEFINE_NAME_{i}=value_{i}" for i in range(30)]
        slug = build_slug(
            backend_name="libclang",
            header_path="x.h",
            defines=long_defines,
            includes=[],
            other_args=[],
        )
        assert slug == "libclang.x.d.fd9d42ea"

    def test_within_limit_not_hashed(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="foo.h",
            defines=["A"],
            includes=[],
            other_args=[],
        )
        assert len(slug) <= 120
        assert slug == "libclang.foo.d.A"

    def test_collision_budget_reserved(self) -> None:
        """Effective budget is 116 chars (4 reserved for collision suffix)."""
        long_defines = [f"DEF_{i}" for i in range(50)]
        slug = build_slug(
            backend_name="libclang",
            header_path="x.h",
            defines=long_defines,
            includes=[],
            other_args=[],
        )
        # Must leave room for "-999" suffix (4 chars)
        assert len(slug) <= 116

    def test_multi_group_overflow(self) -> None:
        """Multiple groups that individually fit can overflow combined."""
        slug = build_slug(
            backend_name="libclang",
            header_path="x.h",
            defines=[f"DEF_{i}" for i in range(20)],
            includes=[f"/path/to/inc_{i}" for i in range(20)],
            other_args=[f"-flag{i}" for i in range(5)],
        )
        assert len(slug) <= 116


class TestSlugCrossPlatform:
    """Slugs must be valid directory names on Linux, macOS, and Windows."""

    def test_windows_invalid_chars(self) -> None:
        """Windows-invalid characters: < > : " | ? *"""
        slug = build_slug(
            backend_name="libclang",
            header_path="test.h",
            defines=["FOO=<bar>", 'BAZ="qux"'],
            includes=[],
            other_args=[],
        )
        for ch in '<>:"|?*':
            assert ch not in slug

    def test_control_characters(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="test.h",
            defines=["FOO=\x01bar\x1f"],
            includes=[],
            other_args=[],
        )
        for i in range(0x20):
            assert chr(i) not in slug

    def test_backslashes(self) -> None:
        """Windows path separators."""
        slug = build_slug(
            backend_name="libclang",
            header_path="C:\\Users\\dev\\test.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert "\\" not in slug

    def test_colons_drive_letter(self) -> None:
        """Windows drive letter colons."""
        slug = build_slug(
            backend_name="libclang",
            header_path="D:\\projects\\lib.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        assert ":" not in slug

    def test_unicode_characters(self) -> None:
        """Non-ASCII chars should be sanitized."""
        slug = build_slug(
            backend_name="libclang",
            header_path="\u65e5\u672c\u8a9e.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        # Should only contain safe chars
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_=." for c in slug)

    def test_spaces_and_tabs(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="my lib.h",
            defines=["FOO BAR"],
            includes=[],
            other_args=[],
        )
        assert " " not in slug
        assert "\t" not in slug

    def test_null_bytes(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="test.h",
            defines=["FOO\x00BAR"],
            includes=[],
            other_args=[],
        )
        assert "\x00" not in slug

    def test_only_safe_chars_in_output(self) -> None:
        """Comprehensive: the slug should only contain safe characters."""
        import string

        safe = set(string.ascii_letters + string.digits + "-_=.")
        slug = build_slug(
            backend_name="libclang",
            header_path="weird<>|file?.h",
            defines=["A=1", "B:C", "D*E"],
            includes=["/path/with spaces/inc"],
            other_args=["-std=c++17", '--flag="val"'],
        )
        assert all(c in safe for c in slug), f"Unsafe chars in slug: {slug}"

    def test_windows_reserved_names_not_bare(self) -> None:
        """Windows reserved names (CON, PRN, NUL, etc.) as header stem."""
        slug = build_slug(
            backend_name="libclang",
            header_path="CON.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        # The slug includes the backend prefix, so "libclang.con" is fine
        # (not a bare reserved name). Just verify no crash.
        assert slug == "libclang.con"
