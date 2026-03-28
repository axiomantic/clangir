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
        assert slug == "libclang.foo.d.BAR.i.a_b.args.-std=c11"

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
        assert ".d." not in slug
        assert ".i." not in slug
        assert ".args." not in slug


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
        assert slug.startswith("libclang.")

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
        assert "FOO_BAR" in slug

    def test_consecutive_dashes_collapsed(self) -> None:
        slug = build_slug(
            backend_name="libclang",
            header_path="a--b..c.h",
            defines=[],
            includes=[],
            other_args=[],
        )
        # After sanitization: a-b-c (dots->dash, consecutive collapsed)
        assert "a-b-c" in slug
        assert "--" not in slug

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
        assert len(slug) <= 120
        # Should have hashed defines group (8 hex chars)
        assert ".d." in slug
        parts = slug.split(".")
        d_idx = parts.index("d")
        hash_part = parts[d_idx + 1]
        assert len(hash_part) == 8

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
