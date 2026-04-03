# Agents

Instructions for AI coding agents working on this project.

## Changelog

Keep `CHANGELOG.md` up to date using [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Every user-facing change should have a changelog entry.

## Versioning

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Bump the version in `pyproject.toml` whenever creating a branch:

- **Major** (X.0.0): Breaking changes to public API
- **Minor** (0.X.0): New features, new public API surface
- **Patch** (0.0.X): Bug fixes, documentation-only changes, internal refactors

## Vendored clang bindings

`headerkit/_clang/` contains vendored upstream clang Python bindings for LLVM 18-21. These are excluded from ruff and mypy. Do not modify, refactor, or lint them.

## Registry pattern

Backends and writers use a managed circular import pattern for self-registration. Both `headerkit/backends/__init__.py` and `headerkit/writers/__init__.py` lazily import their concrete modules via `_ensure_*_loaded()`, and each concrete module imports `register_backend`/`register_writer` at the bottom of the file to self-register. Do not restructure these imports.

When adding a new backend or writer, follow the existing pattern: define the class, then call `register_*()` at the bottom of the module file.

## Public API

`headerkit/__init__.py` defines `__all__` with the full public surface. Backends and writers are accessed via registry functions (`get_backend()`, `get_writer()`), not by importing concrete classes directly. When adding new public symbols, add them to both the imports and `__all__` in `__init__.py`.

## Quality gates

All code must pass before committing:

- `ruff check .` and `ruff format --check .`
- `mypy --strict` on `headerkit/`
- `pytest` across Python 3.10-3.14 on Linux, macOS, and Windows

Pre-commit hooks enforce these automatically.

## Testing

Registry tests (`test_backends/test_registry.py`, `test_writers/test_registry.py`) must save and restore global registry state via fixtures. Each test should see a clean registry to prevent test pollution.

Tests requiring a system libclang installation use the `@pytest.mark.libclang` marker.

## Multi-line string literals

Use `textwrap.dedent` with a triple-quoted string instead of concatenating fragments with `+` and `"\n"`. Use an `f`-string prefix for interpolation. The backslash after the opening `"""` suppresses the leading newline.

```python
# Preferred
import textwrap

assert output == textwrap.dedent(f"""\
    {_PREAMBLE}
    # ========================
    # Typedefs
    # ========================

    callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)
""")

# Avoid
assert output == (
    _PREAMBLE
    + "\n"
    + "# ========================\n"
    + "# Typedefs\n"
    + "# ========================\n"
    + "\n"
    + "callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)\n"
)
```

This applies anywhere a multi-line string literal appears: assertions, expected-value variables, template strings, error messages.

## Runtime dependencies

The project has one conditional runtime dependency: `tomli` for Python 3.10 (before `tomllib` was added to stdlib). All other functionality is zero-dependency. If a feature needs an external package, make it an optional dependency with graceful degradation when absent.
