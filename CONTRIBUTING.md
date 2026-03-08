# Contributing to headerkit

Thanks for your interest in contributing to headerkit! This guide covers everything
you need to get started.

## Development setup

```bash
git clone https://github.com/axiomantic/headerkit.git
cd headerkit
pip install -e '.[dev]'
pytest
```

## Quality gates

All code must pass before submitting a PR:

```bash
ruff check .
ruff format --check .
mypy --strict headerkit/
pytest
```

Pre-commit hooks enforce lint and format checks automatically on each commit.

## PR process

1. Fork the repo and create a branch from `main`.
2. Make your changes.
3. Add or update tests as needed.
4. Run all quality gates listed above.
5. Update `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.
6. Open a pull request against `main`.

## Important notes

### Vendored clang bindings

`headerkit/_clang/` contains vendored upstream clang Python bindings. **Do not modify,
refactor, or lint these files.** They are maintained upstream and excluded from ruff
and mypy.

### Zero runtime dependencies

headerkit has no runtime dependencies and must stay that way. If a feature needs an
external package, make it an optional dependency with graceful degradation when absent.

### Registry pattern

Backends and writers use a self-registration pattern. When adding a new backend or
writer, follow the existing pattern in `headerkit/backends/` and `headerkit/writers/`.

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). Please
read it before participating.
