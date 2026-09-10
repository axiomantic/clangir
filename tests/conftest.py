# tripwire.pytest_plugin is registered automatically via the pytest11 entry point.
from __future__ import annotations

import pytest

from headerkit.backends import is_backend_available
from tests.skip_policy import (
    LIBCLANG_INSTALL,
    TREESITTER_INSTALL,
    NoSilentSkips,
    missing_toolchain,
)


def pytest_configure(config: pytest.Config) -> None:
    """Arm the session gate that turns an unsanctioned skip into a red run."""
    config.pluginmanager.register(NoSilentSkips(), "no-silent-skips")


@pytest.fixture(autouse=True)
def _skip_if_missing_backend(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("libclang"):
        from headerkit.backends.libclang import is_system_libclang_available

        if not is_system_libclang_available():
            missing_toolchain("the system libclang is not available", LIBCLANG_INSTALL)
    if request.node.get_closest_marker("treesitter"):
        if not is_backend_available("tree-sitter"):
            missing_toolchain("the tree-sitter backend is not available", TREESITTER_INSTALL)
