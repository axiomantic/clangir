# Generate Pipeline

The generation pipeline provides a high-level API for parsing C/C++ headers
and producing writer output with a two-layer cache (IR cache + output cache).
On cache hit, no libclang installation is required.

## Functions

### `generate()`

Parse a header and produce output using a single writer, with caching.

::: headerkit._generate.generate
    options:
      show_source: false

**Example:**

```python
from headerkit import generate

# Parse and generate JSON (default writer)
output = generate("mylib.h")

# Generate CFFI bindings with custom include dirs
output = generate(
    "mylib.h",
    writer_name="cffi",
    include_dirs=["/usr/local/include"],
    defines=["MY_DEFINE=1"],
)

# Write output to a file
generate("mylib.h", writer_name="ctypes", output_path="mylib_bindings.py")

# Parse from a string instead of a file
output = generate("virtual.h", code="int add(int a, int b);")
```

### `generate_all()`

Parse a header once and produce output for multiple writers.

::: headerkit._generate.generate_all
    options:
      show_source: false

**Example:**

```python
from headerkit import generate_all

results = generate_all(
    "mylib.h",
    writers=["cffi", "ctypes", "json"],
    output_dir="generated/",
)
for r in results:
    print(f"{r.writer_name}: cached={r.from_cache}, path={r.output_path}")
```

## Data Classes

### `GenerateResult`

::: headerkit._generate.GenerateResult
    options:
      show_source: false

## JSON Deserialization

### `json_to_header()`

Deserialize a JSON string or dict back into a `Header` IR object.
This is the inverse of the JSON writer's serialization.

::: headerkit._ir_json.json_to_header
    options:
      show_source: false

**Example:**

```python
from headerkit import json_to_header, get_writer

# Round-trip: IR -> JSON -> IR
json_writer = get_writer("json")
json_str = json_writer.write(header)
restored = json_to_header(json_str)
```
