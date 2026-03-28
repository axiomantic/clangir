# Cache Strategy Guide

headerkit includes a two-layer cache that stores parsed IR and generated
output in `.hkcache/`. This enables libclang-free builds by committing the
cache to your repository.

## Overview

Parsing C/C++ headers with libclang is slow and requires libclang to be
installed. The cache eliminates both problems:

- **IR cache**: Stores the parsed intermediate representation (IR) as JSON.
  Subsequent runs skip libclang entirely.
- **Output cache**: Stores each writer's generated output. Subsequent runs
  skip both parsing and writing.

When the cache is committed to version control, `pip install` and CI builds
work without libclang installed.

## How it works

The cache uses content-addressed storage with human-readable directory names.

1. **Parse phase**: headerkit computes a SHA-256 cache key from the backend
   name, header content, include dirs, defines, and other args. If the IR
   cache has a matching entry, it deserializes the cached JSON IR. Otherwise
   it parses with the backend and writes the result to the IR cache.

2. **Write phase**: headerkit computes a second cache key from the IR cache
   key plus the writer name, writer options, and writer cache version. If the
   output cache has a matching entry, it returns the cached output. Otherwise
   it runs the writer and caches the result.

## Directory layout

```
.hkcache/
  ir/
    index.json                          # slug -> cache_key mapping
    libclang.mylib/                     # one dir per unique parse
      ir.json                           # serialized Header IR
      metadata.json                     # cache key, backend, args, timestamp
    libclang.mylib.d.DEBUG/             # different defines = different entry
      ir.json
      metadata.json
  output/
    cffi/
      index.json
      libclang.mylib/
        output.py                       # generated cffi output
        metadata.json
    ctypes/
      index.json
      libclang.mylib/
        output.py
        metadata.json
    json/
      index.json
      libclang.mylib/
        output.json
        metadata.json
```

Slug names are derived from the backend name and header filename. Defines
and include dirs are encoded into the slug (e.g., `.d.DEBUG`, `.i.include`).
When a slug collides, a numeric suffix is appended (e.g., `libclang.mylib-2`).

## Using the cache

### Python API

```python
from headerkit import generate, generate_all

# Single writer: parse + generate cffi output
output = generate("include/mylib.h", "cffi")

# Second call with same inputs: loaded entirely from cache
output = generate("include/mylib.h", "cffi")

# Multiple writers: parses once, generates each writer
results = generate_all(
    "include/mylib.h",
    writers=["cffi", "ctypes", "json"],
    output_paths={
        "cffi": "bindings/mylib_cffi.py",
        "ctypes": "bindings/mylib_ctypes.py",
        "json": "bindings/mylib_ir.json",
    },
)

# With backend options
output = generate(
    "include/mylib.h",
    "cffi",
    include_dirs=["/usr/local/include"],
    defines=["VERSION=2", "DEBUG"],
    writer_options={"exclude_patterns": "^__"},
)
```

### CLI

```bash
# Generate with caching (default behavior)
headerkit include/mylib.h -w cffi:bindings/mylib_cffi.py

# Second run uses cache automatically
headerkit include/mylib.h -w cffi:bindings/mylib_cffi.py

# Multiple writers in one pass
headerkit include/mylib.h -w cffi:bindings/cffi.py -w ctypes:bindings/ctypes.py

# Custom cache directory
headerkit include/mylib.h -w cffi --cache-dir /tmp/hkcache
```

### PEP 517 build backend

Consumer projects can use headerkit as their build backend. When `pip install`
or `python -m build` runs, headerkit generates bindings from cached IR before
delegating to the inner backend (hatchling by default).

```toml
# Consumer project's pyproject.toml
[build-system]
requires = ["headerkit", "hatchling"]
build-backend = "headerkit.build_backend"

[tool.headerkit]
backend = "libclang"
writers = ["cffi"]

[tool.headerkit.headers."include/mylib.h"]
defines = ["VERSION=2"]
include_dirs = ["/usr/local/include"]
```

With a committed `.hkcache/`, the build works without libclang. The build
backend reads from the cache and only falls back to libclang on cache miss.

## Cache bypass

Disable caching at three levels:

| Scope | CLI flag | Environment variable | Config key |
|-------|----------|---------------------|------------|
| All caching | `--no-cache` | `HEADERKIT_NO_CACHE=1` | `no_cache = true` |
| IR cache only | `--no-ir-cache` | `HEADERKIT_NO_IR_CACHE=1` | `no_ir_cache = true` |
| Output cache only | `--no-output-cache` | `HEADERKIT_NO_OUTPUT_CACHE=1` | `no_output_cache = true` |

Priority order: CLI flags > environment variables > config file.

```bash
# Skip all caching
headerkit include/mylib.h -w cffi --no-cache

# Re-parse with libclang but use output cache
headerkit include/mylib.h -w cffi --no-ir-cache

# Re-run writer but use IR cache
headerkit include/mylib.h -w cffi --no-output-cache
```

In Python:

```python
output = generate("include/mylib.h", "cffi", no_cache=True)
output = generate("include/mylib.h", "cffi", no_ir_cache=True)
output = generate("include/mylib.h", "cffi", no_output_cache=True)
```

## Cache management

headerkit provides subcommands for inspecting and managing the cache.

```bash
# Show cache statistics
headerkit cache status --cache-dir .hkcache

# Clear all cache entries
headerkit cache clear --cache-dir .hkcache

# Clear only IR entries (keeps output cache)
headerkit cache clear --cache-dir .hkcache --ir

# Clear only output entries (keeps IR cache)
headerkit cache clear --cache-dir .hkcache --output

# Rebuild index.json files from metadata
headerkit cache rebuild-index --cache-dir .hkcache
```

`rebuild-index` is useful after manually editing or moving cache entries. It
scans all `metadata.json` files and regenerates the `index.json` mappings.

## Configuration

Cache settings live in the `[cache]` section of `.headerkit.toml` or
`[tool.headerkit.cache]` in `pyproject.toml`.

```toml
# .headerkit.toml
[cache]
cache_dir = ".hkcache"
no_cache = false
no_ir_cache = false
no_output_cache = false
```

```toml
# pyproject.toml
[tool.headerkit.cache]
cache_dir = ".hkcache"
no_cache = false
no_ir_cache = false
no_output_cache = false
```

## Committing the cache

Commit `.hkcache/` to your repository so that downstream consumers and CI
can build without libclang:

```bash
git add .hkcache/
git commit -m "cache: update headerkit cache"
```

A typical workflow:

1. Developer with libclang runs `headerkit` to generate bindings.
2. Cache entries are written to `.hkcache/`.
3. Developer commits `.hkcache/` alongside the generated output.
4. CI and downstream `pip install` use the cache, no libclang required.

### CI validation

To verify the committed cache is up-to-date in CI:

```bash
# Generate with current sources
headerkit generate mylib.h --writer cffi --output-path bindings.py

# Check for uncommitted changes
git diff --exit-code .hkcache/ bindings.py
```

If the diff is non-empty, the cache is stale and needs to be regenerated.

## Writer opt-out

Writers can opt out of output caching by setting `cache_output = False` as
a class attribute. The IR cache still runs, but the writer's output is
regenerated on every call.

```python
from headerkit.writers import Writer, register_writer

class MyWriter(Writer):
    cache_output = False  # always regenerate output

    def write(self, header):
        ...

register_writer("mywriter", MyWriter)
```

The built-in `diff` and `prompt` writers use this because their output
depends on runtime context (baseline header, verbosity) that is not fully
captured by the cache key.

Writers can also declare a `cache_version` attribute. Changing this value
invalidates all cached output for that writer, which is useful when the
output format changes between versions:

```python
class MyWriter(Writer):
    cache_version = "2"  # bump to invalidate old cache entries
    ...
```
