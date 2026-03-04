"""Tests for the vendored cindex module loader."""

import inspect
import os
import warnings
from unittest.mock import patch

from headerkit._clang import LATEST_VENDORED, OLDEST_VENDORED, VENDORED_VERSIONS, get_cindex


class TestGetCindex:
    def setup_method(self):
        """Reset the cached module before each test."""
        import headerkit._clang

        headerkit._clang._cached_cindex = None

    def test_returns_module_with_config_class(self):
        """get_cindex should return a module containing a Config class."""
        cindex = get_cindex()
        assert inspect.isclass(cindex.Config)

    def test_returns_module_with_index_class(self):
        """get_cindex should return a module containing an Index class."""
        cindex = get_cindex()
        assert inspect.isclass(cindex.Index)

    def test_returns_module_with_cursor_kind_class(self):
        """get_cindex should return a module containing a CursorKind class."""
        cindex = get_cindex()
        assert inspect.isclass(cindex.CursorKind)

    def test_caching(self):
        """Second call should return the exact same module object."""
        first = get_cindex()
        second = get_cindex()
        assert first is second

    def test_env_var_override(self):
        """CIR_CLANG_VERSION env var should select the version."""
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            cindex = get_cindex()
            assert inspect.isclass(cindex.Config)
            assert cindex.__name__ == "headerkit._clang.v18.cindex"

    def test_vendored_versions_tuple(self):
        assert isinstance(VENDORED_VERSIONS, tuple)
        assert VENDORED_VERSIONS == ("18", "19", "20", "21")

    def test_latest_is_newer_than_oldest(self):
        """LATEST_VENDORED and OLDEST_VENDORED should be consistent with VENDORED_VERSIONS."""
        assert int(LATEST_VENDORED) > int(OLDEST_VENDORED)
        assert OLDEST_VENDORED in VENDORED_VERSIONS
        assert LATEST_VENDORED in VENDORED_VERSIONS

    def test_fallback_to_latest_when_detection_fails(self):
        """When version detection returns None, emit exactly one warning and fall back to latest."""
        with (
            patch("headerkit._clang._version.detect_llvm_version", return_value=None),
            warnings.catch_warnings(record=True) as w,
        ):
            warnings.simplefilter("always")
            cindex = get_cindex()
            assert cindex.__name__ == f"headerkit._clang.v{LATEST_VENDORED}.cindex"
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert len(user_warnings) == 1
            assert str(user_warnings[0].message) == (
                f"Could not detect LLVM version. Using vendored cindex for LLVM {LATEST_VENDORED}. "
                f"Set CIR_CLANG_VERSION to override."
            )

    def test_fallback_to_oldest_for_old_version(self):
        """When detected version is below oldest, emit exactly one warning and fall back to oldest."""
        with (
            patch("headerkit._clang._version.detect_llvm_version", return_value="15"),
            warnings.catch_warnings(record=True) as w,
        ):
            warnings.simplefilter("always")
            cindex = get_cindex()
            assert cindex.__name__ == f"headerkit._clang.v{OLDEST_VENDORED}.cindex"
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert len(user_warnings) == 1
            assert str(user_warnings[0].message) == (
                f"LLVM 15 is older than oldest vendored version ({OLDEST_VENDORED}). "
                f"Using {OLDEST_VENDORED}. Set CIR_CLANG_VERSION to override."
            )

    def test_fallback_to_latest_for_new_version(self):
        """When detected version is above latest, emit exactly one warning and fall back to latest."""
        with (
            patch("headerkit._clang._version.detect_llvm_version", return_value="25"),
            warnings.catch_warnings(record=True) as w,
        ):
            warnings.simplefilter("always")
            cindex = get_cindex()
            assert cindex.__name__ == f"headerkit._clang.v{LATEST_VENDORED}.cindex"
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert len(user_warnings) == 1
            assert str(user_warnings[0].message) == (
                f"LLVM 25 is newer than latest vendored version ({LATEST_VENDORED}). "
                f"Using {LATEST_VENDORED}. Set CIR_CLANG_VERSION to override."
            )

    def test_each_vendored_version_loads(self):
        """Every vendored version can be imported and has required classes."""
        import importlib

        for version in VENDORED_VERSIONS:
            module = importlib.import_module(f"headerkit._clang.v{version}.cindex")
            assert inspect.isclass(module.Config), f"v{version} Config is not a class"
            assert inspect.isclass(module.Index), f"v{version} Index is not a class"
            assert inspect.isclass(module.CursorKind), f"v{version} CursorKind is not a class"
            assert inspect.isclass(module.TypeKind), f"v{version} TypeKind is not a class"
            assert inspect.isclass(module.TranslationUnit), f"v{version} TranslationUnit is not a class"

    def test_exact_match_no_warning(self):
        """When detected version exactly matches a vendored version, no warning is emitted."""
        with (
            patch("headerkit._clang._version.detect_llvm_version", return_value="20"),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error")  # Turn warnings into errors
            cindex = get_cindex()
            assert cindex.__name__ == "headerkit._clang.v20.cindex"

    def test_cache_cleared_causes_fresh_load(self):
        """After clearing cache, next call re-runs detection and re-populates the cache."""
        import headerkit._clang

        # Load once to populate the cache.
        get_cindex()
        assert headerkit._clang._cached_cindex is not None, "Cache should be populated after first load"

        # Clear the cache.
        headerkit._clang._cached_cindex = None
        assert headerkit._clang._cached_cindex is None, "Cache should be empty after clearing"

        # Load again with an explicit version override: get_cindex() must go through
        # the detection path again (not return the old cached value) and return v18.
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            second = get_cindex()
            assert second.__name__ == "headerkit._clang.v18.cindex"
            assert headerkit._clang._cached_cindex is second, "Cache should be repopulated after fresh load"
