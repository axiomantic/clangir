# API Reference

The headerkit API is organized into three layers:

**[IR Types](ir.md)** -- The core data model. Dataclasses representing C/C++ declarations
(structs, functions, enums, typedefs, etc.) and type expressions (pointers, arrays,
function pointers). All backends produce these types; all writers consume them.

**[Backends](backends.md)** -- Parsers that read C/C++ source code and produce IR.
The [`ParserBackend`][headerkit.ir.ParserBackend] protocol defines the interface;
the registry functions let you discover and instantiate backends.

**[Writers](writers.md)** -- Output generators that consume IR and produce target formats.
The [`WriterBackend`][headerkit.writers.WriterBackend] protocol defines the interface;
the registry functions mirror the backend API.

## Writer Implementations

| Writer | Module | Description |
|--------|--------|-------------|
| [CFFI](cffi.md) | `headerkit.writers.cffi` | Generates `ffibuilder.cdef()` strings |
| [ctypes](ctypes.md) | `headerkit.writers.ctypes` | Generates Python ctypes binding modules |
| [Cython](cython.md) | `headerkit.writers.cython` | Generates Cython `.pxd` declarations with C++ support |
| [Diff](diff.md) | `headerkit.writers.diff` | Produces API compatibility diff reports (JSON/Markdown) |
| [JSON](json.md) | `headerkit.writers.json` | Serializes IR to JSON for inspection and tooling |
| [Lua](lua.md) | `headerkit.writers.lua` | Generates LuaJIT FFI binding files |
| [Prompt](prompt.md) | `headerkit.writers.prompt` | Token-optimized output for LLM context |

## Generation Pipeline

| Symbol | Module | Description |
|--------|--------|-------------|
| [generate](generate.md) | `headerkit._generate` | Cache-aware single-writer generation |
| [generate_all](generate.md) | `headerkit._generate` | Cache-aware multi-writer generation |
| [GenerateResult](generate.md) | `headerkit._generate` | Result dataclass for generation |
| [json_to_header](generate.md) | `headerkit._ir_json` | Deserialize JSON back to IR |

## CLI Tools

| Tool | Module | Description |
|------|--------|-------------|
| [Install Libclang](install-libclang.md) | `headerkit.install_libclang` | Installs the libclang system dependency for the current platform |

## Quick Example

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

# Parse a C header
backend = get_backend()
header = backend.parse("int add(int a, int b);", "math.h")

# Write CFFI output
writer = get_writer("cffi")
print(writer.write(header))
# => int add(int a, int b);

# Write JSON output
json_writer = get_writer("json")
print(json_writer.write(header))
```
