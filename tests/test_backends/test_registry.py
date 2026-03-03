"""Tests for the backend registry."""

import pytest

from headerkit.backends import (
    get_backend,
    get_backend_info,
    get_default_backend,
    is_backend_available,
    list_backends,
    register_backend,
)
from headerkit.ir import Header


class MockBackend:
    """A minimal mock backend for testing the registry."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def parse(self, code: str, filename: str, **kwargs) -> Header:
        return Header(path=filename, declarations=[])


class TestBackendRegistry:
    def setup_method(self):
        """Reset registry state before each test."""
        import headerkit.backends as b

        self._saved_registry = dict(b._BACKEND_REGISTRY)
        self._saved_default = b._DEFAULT_BACKEND
        self._saved_loaded = b._BACKENDS_LOADED
        b._BACKEND_REGISTRY.clear()
        b._DEFAULT_BACKEND = None
        b._BACKENDS_LOADED = False

    def teardown_method(self):
        """Restore registry state after each test."""
        import headerkit.backends as b

        b._BACKEND_REGISTRY.clear()
        b._BACKEND_REGISTRY.update(self._saved_registry)
        b._DEFAULT_BACKEND = self._saved_default
        b._BACKENDS_LOADED = self._saved_loaded

    def test_register_and_get_backend(self):
        register_backend("mock", MockBackend, is_default=True)
        backend = get_backend("mock")
        assert isinstance(backend, MockBackend)
        assert backend.name == "mock"

    def test_get_default_backend(self):
        register_backend("mock", MockBackend, is_default=True)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True  # Prevent lazy loading
        name = get_default_backend()
        assert name == "mock"

    def test_list_backends(self):
        register_backend("mock", MockBackend)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        names = list_backends()
        assert names == ["mock"]

    def test_is_backend_available(self):
        register_backend("mock", MockBackend)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        assert is_backend_available("mock")
        assert not is_backend_available("nonexistent")

    def test_get_nonexistent_backend_raises(self):
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        with pytest.raises(ValueError, match=r"Unknown backend: 'nonexistent'"):
            get_backend("nonexistent")

    def test_get_backend_none_returns_default(self):
        register_backend("mock", MockBackend, is_default=True)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        backend = get_backend(None)
        assert isinstance(backend, MockBackend)

    def test_get_backend_no_default_raises(self):
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        with pytest.raises(ValueError, match="No backends available"):
            get_backend(None)

    def test_get_backend_info(self):
        register_backend("mock", MockBackend)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        info = get_backend_info()
        assert info == [
            {
                "name": "mock",
                "available": True,
                "default": True,
                "description": "",
            }
        ]

    def test_first_registered_becomes_default(self):
        register_backend("first", MockBackend)
        register_backend("second", MockBackend)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        assert get_default_backend() == "first"

    def test_is_default_overrides(self):
        register_backend("first", MockBackend)
        register_backend("second", MockBackend, is_default=True)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        assert get_default_backend() == "second"

    def test_duplicate_registration_second_class_wins(self):
        """Registering the same name twice replaces the first class.

        The registry key ("mock") is independent of the backend's .name property
        ("mock2"). The registry uses the key passed to register_backend(), not
        the instance's .name. This is intentional: it allows re-registration
        under the same key with a different class.
        """

        class MockBackend2:
            @property
            def name(self) -> str:
                return "mock2"

            @property
            def supports_macros(self) -> bool:
                return True

            @property
            def supports_cpp(self) -> bool:
                return True

            def parse(self, code: str, filename: str, **kwargs) -> Header:
                return Header(path=filename, declarations=[])

        register_backend("mock", MockBackend)
        register_backend("mock", MockBackend2)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        backend = get_backend("mock")
        assert isinstance(backend, MockBackend2)
        # Registry key is "mock" but the instance's .name is "mock2";
        # the registry stores classes by registration key, not by .name
        assert backend.name == "mock2"
        assert backend.supports_macros is True
        assert backend.supports_cpp is True

    def test_get_backend_creates_new_instances(self):
        """Each call to get_backend should return a new instance."""
        register_backend("mock", MockBackend, is_default=True)
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        first = get_backend("mock")
        second = get_backend("mock")
        assert first is not second

    def test_get_default_backend_raises_when_empty(self):
        import headerkit.backends as b

        b._BACKENDS_LOADED = True
        # Registry is already cleared in setup_method
        with pytest.raises(ValueError, match="No backends available"):
            get_default_backend()

    def test_ensure_backends_loaded_handles_import_error(self):
        """Test that _ensure_backends_loaded catches ImportError gracefully.

        When the libclang module fails to import, _ensure_backends_loaded should:
        1. Set _BACKENDS_LOADED = True (preventing retry)
        2. Leave the registry empty (no backends registered)
        3. Emit a warning about missing backends (not raise an exception)
        """
        import sys
        import warnings

        import headerkit.backends as b

        b._BACKEND_REGISTRY.clear()
        b._DEFAULT_BACKEND = None
        b._BACKENDS_LOADED = False

        # Remove the libclang module from sys.modules so import is re-attempted,
        # then make the import raise ImportError via None sentinel
        saved_module = sys.modules.get("headerkit.backends.libclang")
        sys.modules["headerkit.backends.libclang"] = None  # type: ignore[assignment]  # forces ImportError
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                b._ensure_backends_loaded()
        finally:
            if saved_module is not None:
                sys.modules["headerkit.backends.libclang"] = saved_module
            else:
                sys.modules.pop("headerkit.backends.libclang", None)

        # Flag is set to prevent re-trying the failed import
        assert b._BACKENDS_LOADED is True
        # Registry should be empty since the import failed and no backend registered
        assert len(b._BACKEND_REGISTRY) == 0
