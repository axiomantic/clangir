# Build Backend Guide

headerkit ships a PEP 517 build backend that generates bindings
automatically during `pip install` or `python -m build`. When the
`.hkcache/` directory is committed to version control, the build
works without libclang installed on the target machine.

## Overview

The build backend wraps an inner backend (hatchling by default). Before
the inner backend packages your project, headerkit reads
`[tool.headerkit]` from `pyproject.toml`, runs `generate_all()` for
every header listed in `[tool.headerkit.headers]`, and writes the
output files. The inner backend then includes those files in the wheel
or sdist as usual.

```mermaid
graph LR
    A[pip install / python -m build] --> B[headerkit build backend]
    B --> C[generate_all from cache]
    C --> D[inner backend builds wheel/sdist]
```

## Quick start

### 1. Add headerkit to your build requirements

```toml
[build-system]
requires = ["headerkit", "hatchling"]
build-backend = "headerkit.build_backend"
```

### 2. Configure headers and writers

```toml
[tool.headerkit]
backend = "libclang"
writers = ["cffi"]

[tool.headerkit.headers."include/mylib.h"]
defines = ["VERSION=2"]
include_dirs = ["/usr/local/include"]
```

### 3. Populate the cache

Run headerkit locally on a machine with libclang installed:

```bash
headerkit include/mylib.h -w cffi:bindings/mylib_cffi.py
```

This writes cache entries to `.hkcache/`.

### 4. Commit the cache

```bash
git add .hkcache/
git commit -m "cache: add headerkit cache"
```

### 5. Consumers install without libclang

Anyone who clones your repo (or installs from PyPI) gets bindings
generated from cache:

```bash
pip install .          # reads from .hkcache/, no libclang needed
python -m build        # same for sdist/wheel builds
```

## Full consumer pyproject.toml example

```toml
[build-system]
requires = ["headerkit", "hatchling"]
build-backend = "headerkit.build_backend"

[project]
name = "mylib-bindings"
version = "1.0.0"
requires-python = ">=3.10"

[tool.headerkit]
backend = "libclang"
writers = ["cffi", "ctypes"]

[tool.headerkit.headers."include/mylib.h"]
defines = ["VERSION=2"]
include_dirs = ["/usr/local/include"]

[tool.headerkit.headers."include/mylib_utils.h"]
defines = ["VERSION=2", "UTILS_ONLY"]

[tool.headerkit.cache]
cache_dir = ".hkcache"
```

## How it works

When pip or build invokes `build_wheel()` or `build_sdist()`:

1. headerkit imports `build_backend` as the PEP 517 backend.
2. `_run_generation()` reads `[tool.headerkit]` from `pyproject.toml`.
3. For each entry in `[tool.headerkit.headers]`, it calls `generate_all()`
   with the configured backend, writers, defines, and include dirs.
4. `generate_all()` checks `.hkcache/` first. On a cache hit, it
   deserializes the stored IR and output without libclang. On a cache
   miss, it falls back to parsing with libclang.
5. After generation completes, the inner backend (hatchling by default)
   runs its normal `build_wheel()` or `build_sdist()`, packaging the
   generated files into the distribution.

## Configuration reference

### Build system table

| Key | Description |
|-----|-------------|
| `build-backend` | Set to `"headerkit.build_backend"` |
| `requires` | Must include `"headerkit"` and the inner backend (e.g., `"hatchling"`) |

### `[tool.headerkit]` keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `backend` | string | `"libclang"` | Parser backend name |
| `writers` | list of strings | all registered | Writers to run for each header |
| `include_dirs` | list of strings | `[]` | Global include directories applied to all headers |
| `defines` | list of strings | `[]` | Global preprocessor defines applied to all headers |

### `[tool.headerkit.headers."path/to/header.h"]` keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `defines` | list of strings | `[]` | Per-header defines (merged with global defines) |
| `include_dirs` | list of strings | `[]` | Per-header include dirs (merged with global include dirs) |

### `[tool.headerkit.cache]` keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `cache_dir` | string | `".hkcache"` | Directory for cache storage |
| `no_cache` | bool | `false` | Disable all caching |
| `no_ir_cache` | bool | `false` | Disable IR cache only |
| `no_output_cache` | bool | `false` | Disable output cache only |

### config_settings keys

Pass these via `pip install --config-settings` or `python -m build -C`:

| Key | Description |
|-----|-------------|
| `inner-backend` | Override the inner backend module (default: `hatchling.build`) |
| `no-cache` | Set to `"true"` to disable all caching for this build |
| `no-ir-cache` | Set to `"true"` to disable IR cache for this build |
| `no-output-cache` | Set to `"true"` to disable output cache for this build |

Example:

```bash
pip install . --config-settings="no-cache=true"
pip install . --config-settings="inner-backend=flit_core.buildapi"
```

### Overriding the inner backend

By default headerkit delegates to `hatchling.build`. To use a different
inner backend:

1. Add the inner backend to `requires` in `[build-system]`.
2. Pass `inner-backend` via config_settings:

```bash
python -m build -C inner-backend=flit_core.buildapi
```

Or set it permanently by adding both to your build requires:

```toml
[build-system]
requires = ["headerkit", "flit_core"]
build-backend = "headerkit.build_backend"
```

## Cache miss behavior

When a header's cache entry is missing and libclang is not installed:

- **Wheel builds** (`build_wheel`, `build_editable`): the build fails
  with an `ImportError` or backend-specific error. Install libclang and
  re-run `headerkit` to populate the cache, then commit `.hkcache/`.
- **Sdist builds** (`build_sdist`): generation failures are logged as
  warnings and the build continues. This allows sdist creation on
  machines without libclang, as long as the sdist consumer has libclang
  or a populated cache.

To fix a cache miss:

```bash
# Install libclang
headerkit install-libclang

# Re-generate and populate cache
headerkit include/mylib.h -w cffi:bindings/mylib_cffi.py

# Commit updated cache
git add .hkcache/
git commit -m "cache: update headerkit cache"
```

## Multiple headers

Configure multiple headers with per-header defines and include dirs:

```toml
[tool.headerkit]
backend = "libclang"
writers = ["cffi", "ctypes"]
defines = ["SHARED_DEFINE"]
include_dirs = ["include/common"]

[tool.headerkit.headers."include/core.h"]
defines = ["CORE_API"]
include_dirs = ["include/core"]

[tool.headerkit.headers."include/utils.h"]
defines = ["UTILS_API", "DEBUG"]
include_dirs = ["include/utils"]

[tool.headerkit.headers."include/platform.h"]
# Uses only global defines and include_dirs
```

Each header's defines and include dirs are merged with the global values.
In this example, `include/core.h` is parsed with defines
`["SHARED_DEFINE", "CORE_API"]` and include dirs
`["include/common", "include/core"]`.

## Troubleshooting

### Stale cache

If bindings are outdated after modifying a header:

```bash
# Clear the cache and regenerate
headerkit cache clear --cache-dir .hkcache
headerkit include/mylib.h -w cffi:bindings/mylib_cffi.py
git add .hkcache/
git commit -m "cache: regenerate after header changes"
```

The cache key includes header content, defines, and include dirs. If any
of these change, the old entry becomes a miss and a new entry is created.
Old entries remain until explicitly cleared.

### libclang not found

```
ImportError: libclang not found
```

Install libclang with `headerkit install-libclang` or see the
[installation guide](installation.md) for platform-specific instructions.

### Wrong inner backend

```
ModuleNotFoundError: No module named 'hatchling'
```

Add the inner backend to `requires` in `[build-system]`:

```toml
[build-system]
requires = ["headerkit", "hatchling"]
```

### Build fails but cache exists

Verify the cache is committed and present in the build environment:

```bash
ls .hkcache/ir/
ls .hkcache/output/
```

If files are missing, the `.hkcache/` directory may not be included in
the sdist. Check your inner backend's include/exclude configuration to
ensure `.hkcache/` is packaged.

### CI validation

Verify the committed cache matches current sources:

```bash
headerkit include/mylib.h -w cffi:bindings/mylib_cffi.py
git diff --exit-code .hkcache/ bindings/
```

A non-empty diff means the cache is stale and must be regenerated.
