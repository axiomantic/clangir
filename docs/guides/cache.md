# Cache Staleness Detection

headerkit includes a hash-based cache system that detects when generated
output files are stale relative to their inputs. This enables a two-phase
workflow: generate bindings on a machine with libclang, then validate
freshness on any machine without libclang.

## Overview

The cache works by computing a SHA-256 hash over all inputs that affect
generation:

- Header file contents (normalized for line endings and BOM)
- Writer name and options
- headerkit version
- Any extra input files

This hash is stored alongside the generated output, either embedded as a
comment block or in a sidecar `.hkcache` file. Later, `is_up_to_date()`
recomputes the hash and compares it to the stored value.

## Python API

### Computing a hash

```python
from headerkit import compute_hash

digest = compute_hash(
    header_paths=["include/mylib.h", "include/mylib_types.h"],
    writer_name="cffi",
    writer_options={"exclude": "__internal_.*"},
)
```

### Saving a hash

```python
from headerkit import save_hash
from headerkit.writers import get_writer

writer = get_writer("cffi")

# Embedded: prepends hash as comment block to the output file
result = save_hash(
    output_path="bindings/mylib_cffi.py",
    header_paths=["include/mylib.h"],
    writer_name="cffi",
    writer=writer,  # enables embedded storage
)

# Sidecar: creates bindings/mylib.json.hkcache alongside the output
result = save_hash(
    output_path="bindings/mylib.json",
    header_paths=["include/mylib.h"],
    writer_name="json",
    writer=get_writer("json"),  # json has no comment format, uses sidecar
)
```

### Checking freshness

```python
from headerkit import is_up_to_date

if is_up_to_date(
    output_path="bindings/mylib_cffi.py",
    header_paths=["include/mylib.h"],
    writer_name="cffi",
):
    print("Output is fresh")
else:
    print("Output is stale, regenerate")
```

### Batch checking

```python
from headerkit.cache import is_up_to_date_batch

results = is_up_to_date_batch([
    {
        "output_path": "bindings/mylib_cffi.py",
        "header_paths": ["include/mylib.h"],
        "writer_name": "cffi",
    },
    {
        "output_path": "bindings/mylib_ctypes.py",
        "header_paths": ["include/mylib.h"],
        "writer_name": "ctypes",
    },
])

for path, fresh in results.items():
    status = "fresh" if fresh else "STALE"
    print(f"  {path}: {status}")
```

## CLI Usage

### Saving a hash

```bash
# Sidecar storage (default when --writer is not specified)
headerkit cache-save bindings/mylib.json \
    --header include/mylib.h \
    --writer-name json

# Embedded storage (pass --writer to enable comment embedding)
headerkit cache-save bindings/mylib_cffi.py \
    --header include/mylib.h \
    --writer-name cffi \
    --writer cffi
```

### Checking freshness

```bash
# Exit code 0 = up-to-date, 1 = stale
headerkit cache-check bindings/mylib_cffi.py \
    --header include/mylib.h \
    --writer-name cffi

# Use in shell scripts
if headerkit cache-check bindings/mylib_cffi.py \
    --header include/mylib.h --writer-name cffi; then
    echo "Fresh"
else
    echo "Stale, regenerating..."
    headerkit -w cffi:bindings/mylib_cffi.py include/mylib.h
    headerkit cache-save bindings/mylib_cffi.py \
        --header include/mylib.h --writer-name cffi --writer cffi
fi
```

### Writer options

Pass writer options to ensure the hash matches the generation configuration:

```bash
headerkit cache-save output.py \
    --header input.h \
    --writer-name cffi \
    --writer-option "exclude=__internal_.*" \
    --writer-option "prefix=mylib_"
```

### Extra inputs

Include additional files (build configs, version files) in the hash:

```bash
headerkit cache-save output.py \
    --header input.h \
    --writer-name cffi \
    --extra-input build.cfg \
    --extra-input VERSION
```

## Storage Modes

### Embedded comments

Writers that support comments (cffi, ctypes, cython, lua) can store the
hash directly in the output file as a comment block at the top:

```python
# [headerkit-cache]
# hash = "a1b2c3..."
# version = "0.9.0"
# writer = "cffi"
# generated = "2026-03-23T14:30:00+00:00"

# ... generated bindings below ...
```

This keeps the hash and output in a single file.

### Sidecar `.hkcache` files

Writers without comment support (json, prompt, diff) use a sidecar file.
For `bindings.json`, the sidecar is `bindings.json.hkcache`:

```toml
[headerkit-cache]
hash = "a1b2c3..."
version = "0.9.0"
writer = "json"
generated = "2026-03-23T14:30:00+00:00"
```

When `writer=None` is passed to `save_hash()`, sidecar storage is always
used regardless of the writer name.

## Version Control

Commit `.hkcache` sidecar files alongside your generated output. They are
small (under 200 bytes) and enable any checkout to verify freshness without
regenerating.

For embedded hashes, the hash block is part of the output file itself, so
committing the output is sufficient.

## CI Integration

A typical CI pipeline validates that committed bindings are fresh:

```yaml
# .github/workflows/check-bindings.yml
name: Check bindings freshness
on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install headerkit
      - run: |
          headerkit cache-check bindings/mylib_cffi.py \
            --header include/mylib.h \
            --writer-name cffi
```

This job requires no libclang installation. It only reads the stored hash
and recomputes from the header files present in the checkout.
