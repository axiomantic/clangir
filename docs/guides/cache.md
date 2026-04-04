# Cache Strategy Guide

headerkit includes a two-layer cache that stores parsed IR and generated
output in `.headerkit/`. This enables libclang-free builds by committing the
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
.headerkit/
  ir/
    index.json                          # slug -> cache_key mapping
    libclang.mylib.x86_64-linux/        # one dir per unique parse (includes target)
      ir.json                           # serialized Header IR
      metadata.json                     # cache key, backend, args, timestamp
    libclang.mylib.x86_64-linux.d.DEBUG/ # different defines = different entry
      ir.json
      metadata.json
    libclang.mylib.aarch64-linux/       # different target = different entry
      ir.json
      metadata.json
  output/
    cffi/
      index.json
      libclang.mylib.x86_64-linux/
        output.py                       # generated cffi output
        metadata.json
    ctypes/
      index.json
      libclang.mylib.x86_64-linux/
        output.py
        metadata.json
    json/
      index.json
      libclang.mylib.x86_64-linux/
        output.json
        metadata.json
```

Slug names are derived from the backend name, header filename, and target
triple (in short `arch-os` form). Defines and include dirs are also encoded
into the slug (e.g., `.d.DEBUG`, `.i.include`). When a slug collides, a
numeric suffix is appended (e.g., `libclang.mylib.x86_64-linux-2`).

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

# Cross-compile: generate ARM64 Linux bindings on any host
output = generate(
    "include/mylib.h",
    "cffi",
    target="aarch64-unknown-linux-gnu",
)
```

### CLI

```bash
# Generate with caching (default behavior)
headerkit include/mylib.h -w cffi -o cffi:bindings/mylib_cffi.py

# Second run uses cache automatically
headerkit include/mylib.h -w cffi -o cffi:bindings/mylib_cffi.py

# Multiple writers in one pass
headerkit include/mylib.h -w cffi -o cffi:bindings/cffi.py -w ctypes -o ctypes:bindings/ctypes.py

# Custom store directory
headerkit include/mylib.h -w cffi --store-dir /tmp/headerkit-store
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

With a committed `.headerkit/`, the build works without libclang. The build
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
headerkit cache status --store-dir .headerkit

# Clear all cache entries
headerkit cache clear --store-dir .headerkit

# Clear only IR entries (keeps output cache)
headerkit cache clear --store-dir .headerkit --ir

# Clear only output entries (keeps IR cache)
headerkit cache clear --store-dir .headerkit --output

# Rebuild index.json files from metadata
headerkit cache rebuild-index --store-dir .headerkit
```

`rebuild-index` is useful after manually editing or moving cache entries. It
scans all `metadata.json` files and regenerates the `index.json` mappings.

## Configuration

The store directory can be configured at the top level of `[tool.headerkit]`
(or `.headerkit.toml`). Cache bypass settings live in the `[cache]` section.

```toml
# .headerkit.toml
store_dir = ".headerkit"

[cache]
no_cache = false
no_ir_cache = false
no_output_cache = false
```

```toml
# pyproject.toml
[tool.headerkit]
store_dir = ".headerkit"

[tool.headerkit.cache]
no_cache = false
no_ir_cache = false
no_output_cache = false
```

## Committing the cache

Commit `.headerkit/` to your repository so that downstream consumers and CI
can build without libclang:

```bash
git add .headerkit/
git commit -m "cache: update headerkit cache"
```

When libclang is unavailable and the IR cache misses, `generate()` will check
the output cache before raising an error. If a cached output exists for the
requested writer and inputs, it is returned directly. This makes `pip install`
from committed `.headerkit/` work without libclang.

A typical workflow:

1. Developer with libclang runs `headerkit` to generate bindings.
2. Cache entries are written to `.headerkit/`.
3. Developer commits `.headerkit/` alongside the generated output.
4. CI and downstream `pip install` use the cache, no libclang required.

### CI validation

To verify the committed cache is up-to-date in CI:

```bash
# Generate with current sources
headerkit mylib.h -w cffi -o cffi:bindings.py

# Check for uncommitted changes
git diff --exit-code .headerkit/ bindings.py
```

If the diff is non-empty, the cache is stale and needs to be regenerated.

## Multi-platform cache population

Cache keys include the LLVM target triple (e.g., `x86_64-pc-linux-gnu`,
`aarch64-apple-darwin`) because libclang preprocessor output can differ
across platforms. For example, a header with `#ifdef __linux__` branches
produces different IR on Linux vs macOS. Python version is not part of the
IR cache key because C preprocessing is Python-version-independent.

headerkit auto-detects the target triple from the running Python process
via `detect_process_triple()`, which uses `HOST_GNU_TYPE` on POSIX
(the triple baked into the Python build) or `sysconfig.get_platform()`
on Windows. On musl-based Linux systems, a runtime libc sniff ensures
the triple correctly says `linux-musl` instead of `linux-gnu`. You can
also set the target explicitly using `--target TRIPLE` (CLI flag), the
`target` key in `[tool.headerkit]` config, or the `HEADERKIT_TARGET`
environment variable. This allows cross-compilation without Docker.

Projects using cibuildwheel or building for multiple platforms need cache
entries for every target. The `cache populate` command solves this by running
headerkit inside Docker containers that match each target platform.

### Basic usage

```bash
# Populate for specific Linux targets
headerkit cache populate mylib.h -w cffi \
    --platform linux/amd64 --platform linux/arm64

# Populate for specific Python versions (selects which Python
# runs headerkit inside the Docker container)
headerkit cache populate mylib.h -w cffi \
    --platform linux/amd64 \
    --python 3.12 --python 3.13

# Preview what would be generated (no Docker required)
headerkit cache populate mylib.h -w cffi \
    --platform linux/amd64 --platform linux/arm64 --dry-run

# Use --target for direct target triple specification (no Docker)
headerkit mylib.h -w cffi --target x86_64-unknown-linux-gnu
```

The `--platform` flag uses Docker platform format, which headerkit maps to
LLVM target triples internally (e.g., `linux/amd64` maps to
`x86_64-unknown-linux-gnu`, `linux/arm64` maps to
`aarch64-unknown-linux-gnu`). For direct control without Docker, use
`--target` with a full LLVM target triple instead.

### Auto-detect from cibuildwheel

If your project uses cibuildwheel, pass `--cibuildwheel` to auto-detect
target platforms and Python versions from `[tool.cibuildwheel]` in
`pyproject.toml`:

```bash
headerkit cache populate mylib.h -w cffi --cibuildwheel
```

This reads the `build` and `skip` selectors to determine which CPython
versions and Linux platforms to target. macOS and Windows targets emit
warnings because they cannot be emulated via Docker.

### Configuration file

For projects that always target the same platforms, configure defaults in
`pyproject.toml` or `.headerkit.toml`:

```toml
# pyproject.toml
[tool.headerkit.cache.populate]
platforms = ["linux/amd64", "linux/arm64"]
python_versions = ["3.12", "3.13"]
timeout = 600

[tool.headerkit.cache.populate.images]
"linux/amd64" = "quay.io/pypa/manylinux_2_28_x86_64"
"linux/arm64" = "quay.io/pypa/manylinux_2_28_aarch64"
```

### Docker and QEMU setup

Cache populate requires Docker. For cross-architecture targets (e.g.,
building arm64 entries on an amd64 host), Docker uses QEMU emulation.
Set up QEMU with:

```bash
docker run --privileged multiarch/qemu-user-static --reset -p yes
```

This is a one-time setup. After this, Docker can run containers for any
architecture that QEMU supports.

### Default Docker images

| Platform | Default image |
|----------|--------------|
| linux/amd64 | `quay.io/pypa/manylinux_2_28_x86_64` |
| linux/arm64 | `quay.io/pypa/manylinux_2_28_aarch64` |
| linux/386 | `quay.io/pypa/manylinux_2_28_i686` |

Override images with `--docker-image` (applies to all platforms) or
per-platform via the config file.

### Limitations

- **PyPy**: Not supported by cache populate. Generate PyPy cache entries
  natively or via CI.
- **macOS and Windows**: Cannot be emulated via Docker. Run
  `headerkit cache populate` natively on those platforms, or use CI jobs
  to generate platform-specific entries.

## Merging stores from multiple platforms

When collecting store entries from multiple CI platforms (e.g., via
cibuildwheel matrix builds), each platform produces its own `.headerkit/`
directory. Naive file copy does not work because `index.json` files would
be overwritten instead of merged.

The `store merge` command combines multiple store directories into one,
copying entry subdirectories and merging `index.json` files:

```bash
# Merge platform-specific stores into the project store
headerkit store merge store-linux/ store-macos/ store-windows/ -o .headerkit/

# Merge a single CI artifact into the existing store
headerkit store merge /tmp/ci-headerkit-store -o .headerkit/
```

Merge behavior:

- **New entries** (slug not in target): copied to target.
- **Duplicate entries** (same slug and cache_key): skipped.
- **Conflicting entries** (same slug, different cache_key): overwritten
  by the source entry (later sources win when multiple sources are given).

The merge operates on both `ir/` and `output/<writer>/` layers, updating
each layer's `index.json` independently.

### Python API

```python
from headerkit import store_merge

result = store_merge(
    sources=["/tmp/store-linux", "/tmp/store-macos"],
    target=".headerkit/",
)
print(f"New: {result.new_entries}, Skipped: {result.skipped_entries}")
```

### CI workflow example

A typical multi-platform CI workflow:

1. Each platform job runs `headerkit cache populate` and uploads
   `.headerkit/` as an artifact.
2. A merge job downloads all platform artifacts and runs
   `headerkit store merge` to combine them.
3. The merged store is committed or used for subsequent build steps.

```yaml
merge-stores:
  needs: [build-linux, build-macos]
  steps:
    - uses: actions/download-artifact@v4
      with:
        name: headerkit-store-linux
        path: store-linux/
    - uses: actions/download-artifact@v4
      with:
        name: headerkit-store-macos
        path: store-macos/
    - run: headerkit store merge store-linux/ store-macos/ -o .headerkit/
```

## Glob-based header selection

Instead of listing each header file explicitly, you can use glob patterns
to select headers:

```bash
# Process all .h files under include/
headerkit 'include/**/*.h' -w cffi -o cffi:{dir}/{stem}_cffi.py

# Exclude internal headers
headerkit 'include/**/*.h' --exclude 'include/internal/**' -w cffi
```

Quote glob patterns to prevent shell expansion. headerkit expands them
relative to the project root.

### Output path templates

Use `-o WRITER:TEMPLATE` to control where generated files are written.
Templates support these variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `{stem}` | Filename without extension | `mylib` |
| `{name}` | Filename with extension | `mylib.h` |
| `{dir}` | Relative directory from project root | `include/net` |

```bash
# Each header gets its own output file
headerkit 'include/**/*.h' -w cffi -o cffi:{dir}/{stem}_cffi.py

# Multiple writers with different templates
headerkit 'include/**/*.h' \
    -w cffi -o cffi:{dir}/{stem}_cffi.py \
    -w json -o json:{dir}/{stem}.json
```

### Configuration file

Configure header selection and output templates in `pyproject.toml`:

```toml
[tool.headerkit]
exclude = ["include/internal/**"]

[[tool.headerkit.headers]]
pattern = "include/**/*.h"

[[tool.headerkit.headers]]
pattern = "vendor/special.h"
defines = ["VENDOR_MODE"]

[tool.headerkit.output]
cffi = "{dir}/{stem}_cffi.py"
json = "{dir}/{stem}.json"
```

Per-pattern overrides (like `defines` above) apply only to headers
matching that specific pattern.

## Writer opt-out

Writers can opt out of output caching by setting `cache_output = False` as
a class attribute. The IR cache still runs, but the writer's output is
regenerated on every call.

```python
from headerkit.writers import register_writer


class MyWriter:
    cache_output = False  # always regenerate output

    @property
    def name(self) -> str:
        return "mywriter"

    @property
    def format_description(self) -> str:
        return "My custom output format"

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
class MyWriter:
    cache_version = "2"  # bump to invalidate old cache entries

    # ... name, format_description, write() as above
```
