# GitHub Action

headerkit ships a composite GitHub Action that populates the
`.headerkit/` cache in CI. Use it to keep cache entries up to date
whenever headers change, so downstream builds never need libclang.

## What it does

The action runs three steps:

1. Sets up Python (via `actions/setup-python`).
2. Installs headerkit from PyPI.
3. Runs `headerkit cache populate` with the arguments you provide.

Optionally, it commits the updated `.headerkit/` directory back to the
branch.

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `headerkit-version` | no | latest | Pin a specific headerkit version (e.g., `0.15.0`) |
| `python-version` | no | `3.12` | Python version for the runner |
| `args` | no | `""` | Arguments passed to `headerkit cache populate` |
| `commit` | no | `false` | Commit populated cache files after generation |

## Basic usage

```yaml
name: Populate headerkit cache
on:
  push:
    paths:
      - "include/**/*.h"

jobs:
  populate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: axiomantic/headerkit@v0
        with:
          args: "include/mylib.h -w cffi --platform linux/amd64"
          commit: "true"
```

This installs the latest headerkit, runs `cache populate` for the
specified header and platform, and commits the result.

## Pin a headerkit version

Lock the version to avoid surprises from new releases:

```yaml
- uses: axiomantic/headerkit@v0
  with:
    headerkit-version: "0.15.0"
    args: "include/mylib.h -w cffi --cibuildwheel"
```

## Usage with cibuildwheel

If your project uses cibuildwheel, pass `--cibuildwheel` to auto-detect
target platforms and Python versions from `[tool.cibuildwheel]` in
`pyproject.toml`:

```yaml
- uses: axiomantic/headerkit@v0
  with:
    args: "--cibuildwheel"
    commit: "true"
```

This reads the `build` and `skip` selectors to determine which CPython
versions and Linux platforms to target.

## Usage with multiple platforms

Generate cache entries for specific platforms using `--platform`:

```yaml
- uses: axiomantic/headerkit@v0
  with:
    args: >-
      include/mylib.h -w cffi
      --platform linux/amd64
      --platform linux/arm64
    commit: "true"
```

For macOS and Windows targets, run the action on the matching runner
OS instead of relying on Docker emulation:

```yaml
jobs:
  populate-linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: axiomantic/headerkit@v0
        with:
          args: "include/mylib.h -w cffi --platform linux/amd64 --platform linux/arm64"
          commit: "true"

  populate-macos:
    runs-on: macos-latest
    needs: populate-linux
    steps:
      - uses: actions/checkout@v4
      - uses: axiomantic/headerkit@v0
        with:
          args: "include/mylib.h -w cffi"
          commit: "true"
```

Note that when `commit: "true"` is used across multiple jobs, each job
must pull the latest commit from the previous job to avoid conflicts.
Use `needs:` to sequence them.

## How it works

The action is a [composite action](https://docs.github.com/en/actions/sharing-automations/creating-actions/creating-a-composite-action)
defined in `action.yml` at the repository root. It:

1. Calls `actions/setup-python` with the requested Python version.
2. Installs headerkit via pip. If `headerkit-version` is set, it pins
   that exact version; otherwise it installs the latest release.
3. Runs `headerkit cache populate` with your `args` value. The args
   are passed through to the command as-is, so any flag that
   `cache populate` accepts works here.
4. If `commit` is `"true"`, it configures a bot git identity, stages
   `.headerkit/`, and commits if there are changes. If the cache is
   already up to date, the commit step is a no-op.

All input values are passed via environment variables (not shell
interpolation) to prevent injection attacks.

## Triggering on header changes

Use `paths` filters to run the action only when headers change:

```yaml
on:
  push:
    paths:
      - "include/**/*.h"
      - "vendor/**/*.h"
```

This avoids unnecessary cache populate runs on unrelated commits.

## See also

- [Cache Strategy Guide](cache.md) for cache layout, bypass flags, and
  multi-platform population details.
- [Build Backend Guide](build-backend.md) for using headerkit as a
  PEP 517 build backend with committed cache.
