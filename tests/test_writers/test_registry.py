"""Tests for the writer registry."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest

from headerkit.ir import CType, Function, Header
from headerkit.writers import (
    WriterBackend,
    get_default_writer,
    get_writer,
    get_writer_info,
    is_writer_available,
    list_writers,
    register_writer,
)


class MockWriter:
    """A mock writer for testing the registry."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def write(self, header: Header) -> str:
        return ""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def format_description(self) -> str:
        return "Mock writer for testing"


class MockWriterWithDocstring:
    """Extract this first line as description.

    This second line should be ignored.
    """

    def __init__(self) -> None:
        pass

    def write(self, header: Header) -> str:
        return ""

    @property
    def name(self) -> str:
        return "with-doc"

    @property
    def format_description(self) -> str:
        return ""


@pytest.fixture()
def reset_writer_registry() -> Generator[None, None, None]:
    """Save and restore global writer registry state around each test."""
    import headerkit.writers as w

    saved_registry = dict(w._WRITER_REGISTRY)
    saved_descriptions = dict(w._WRITER_DESCRIPTIONS)
    saved_default = w._DEFAULT_WRITER
    saved_loaded = w._WRITERS_LOADED

    # Clear state for isolated tests
    w._WRITER_REGISTRY.clear()
    w._WRITER_DESCRIPTIONS.clear()
    w._DEFAULT_WRITER = None
    w._WRITERS_LOADED = False

    yield

    # Restore original state
    w._WRITER_REGISTRY.clear()
    w._WRITER_REGISTRY.update(saved_registry)
    w._WRITER_DESCRIPTIONS.clear()
    w._WRITER_DESCRIPTIONS.update(saved_descriptions)
    w._DEFAULT_WRITER = saved_default
    w._WRITERS_LOADED = saved_loaded


class TestWriterRegistry:
    """Tests for the writer registry mechanism using mock writers."""

    @pytest.fixture(autouse=True)
    def _isolate_registry(self, reset_writer_registry: None) -> None:
        """Use the reset fixture for every mock-based test."""

    def test_register_and_get_writer(self) -> None:
        """Register a mock writer class, get_writer() returns instance."""
        import headerkit.writers as w

        register_writer("mock", MockWriter, is_default=True)
        w._WRITERS_LOADED = True

        writer = get_writer("mock")
        assert isinstance(writer, MockWriter)
        # Verify the writer actually works: write() returns a string
        header = Header(
            path="test.h",
            declarations=[
                Function(name="test_fn", return_type=CType(name="void"), parameters=[]),
            ],
        )
        result = writer.write(header)
        assert isinstance(result, str)

    def test_register_default(self) -> None:
        """Register with is_default=True, get_writer() without name returns it."""
        import headerkit.writers as w

        register_writer("first", MockWriter)
        register_writer("second", MockWriter, is_default=True)
        w._WRITERS_LOADED = True

        writer = get_writer()
        assert isinstance(writer, MockWriter)
        assert get_default_writer() == "second"

    def test_list_writers(self) -> None:
        """list_writers() returns names of all registered writers."""
        import headerkit.writers as w

        register_writer("alpha", MockWriter)
        register_writer("beta", MockWriter)
        w._WRITERS_LOADED = True

        names = list_writers()
        # list_writers() returns insertion-ordered results; assert exact order.
        assert names == ["alpha", "beta"]

    def test_duplicate_registration_raises(self) -> None:
        """Registering the same name twice raises ValueError."""
        register_writer("mock", MockWriter)

        with pytest.raises(ValueError, match="Writer already registered"):
            register_writer("mock", MockWriter)

    def test_get_nonexistent_writer(self) -> None:
        """get_writer('nonexistent') raises ValueError."""
        import headerkit.writers as w

        w._WRITERS_LOADED = True

        with pytest.raises(ValueError, match="Unknown writer"):
            get_writer("nonexistent")

    def test_is_writer_available(self) -> None:
        """Returns True for registered, False for unregistered."""
        import headerkit.writers as w

        register_writer("mock", MockWriter)
        w._WRITERS_LOADED = True

        assert is_writer_available("mock") is True
        assert is_writer_available("nonexistent") is False

    def test_get_writer_info(self) -> None:
        """Returns name + description dict for all registered writers."""
        import headerkit.writers as w

        register_writer("mock", MockWriter, description="A test writer")
        w._WRITERS_LOADED = True

        info = get_writer_info()
        assert info == [{"name": "mock", "description": "A test writer", "is_default": True}]

    def test_description_from_docstring(self) -> None:
        """If no description passed, register_writer() extracts from class docstring."""
        import headerkit.writers as w

        register_writer("with-doc", MockWriterWithDocstring)
        w._WRITERS_LOADED = True

        info = get_writer_info()
        assert info == [
            {
                "name": "with-doc",
                "description": "Extract this first line as description.",
                "is_default": True,
            }
        ]

    def test_get_default_writer(self) -> None:
        """Returns the default writer name."""
        import headerkit.writers as w

        register_writer("mock", MockWriter, is_default=True)
        w._WRITERS_LOADED = True

        assert get_default_writer() == "mock"

    def test_get_writer_passes_kwargs(self) -> None:
        """get_writer('x', foo=1) passes foo=1 to constructor."""
        import headerkit.writers as w

        register_writer("mock", MockWriter, is_default=True)
        w._WRITERS_LOADED = True

        writer = get_writer("mock", foo=1, bar="hello")
        assert isinstance(writer, MockWriter)
        assert writer.kwargs == {"foo": 1, "bar": "hello"}

    def test_get_writer_no_writers_raises(self) -> None:
        """get_writer() with no writers registered and no default raises ValueError."""
        import headerkit.writers as w

        w._WRITERS_LOADED = True

        with pytest.raises(ValueError, match="No writers available"):
            get_writer()


class TestWriterRegistryIntegration:
    """Tests using real registered writers (CffiWriter, JsonWriter).

    These tests use the real registry state populated by
    _ensure_writers_loaded(). They do not clear/reset the registry.
    """

    def test_cffi_writer_registered(self) -> None:
        """'cffi' appears in list_writers()."""
        assert "cffi" in list_writers()

    def test_json_writer_registered(self) -> None:
        """'json' appears in list_writers()."""
        assert "json" in list_writers()

    def test_cffi_is_default(self) -> None:
        """get_default_writer() returns 'cffi'."""
        assert get_default_writer() == "cffi"

    def test_get_cffi_writer_returns_instance(self) -> None:
        """get_writer('cffi') returns a WriterBackend instance."""
        writer = get_writer("cffi")
        assert isinstance(writer, WriterBackend)

    def test_get_json_writer_returns_instance(self) -> None:
        """get_writer('json') returns a WriterBackend instance."""
        writer = get_writer("json")
        assert isinstance(writer, WriterBackend)

    def test_roundtrip_cffi(self) -> None:
        """Parse a simple header, write with cffi writer, verify output."""
        header = Header(
            path="test.h",
            declarations=[
                Function(
                    name="test_func",
                    return_type=CType(name="int"),
                    parameters=[],
                ),
            ],
        )

        writer = get_writer("cffi")
        output = writer.write(header)

        assert output == "int test_func(void);"

    def test_roundtrip_json(self) -> None:
        """Parse a simple header, write with json writer, verify JSON is valid."""
        header = Header(
            path="test.h",
            declarations=[
                Function(
                    name="test_func",
                    return_type=CType(name="int"),
                    parameters=[],
                ),
            ],
        )

        writer = get_writer("json")
        output = writer.write(header)

        parsed = json.loads(output)
        assert parsed == {
            "path": "test.h",
            "declarations": [
                {
                    "kind": "function",
                    "name": "test_func",
                    "return_type": {"kind": "ctype", "name": "int"},
                    "parameters": [],
                    "is_variadic": False,
                }
            ],
        }
