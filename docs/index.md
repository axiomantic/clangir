# headerkit

**A C/C++ header parsing toolkit with pluggable backends and writers.**

headerkit parses C and C++ header files into a language-agnostic Intermediate Representation (IR), then transforms that IR into various output formats. Use it to generate FFI bindings, serialize header metadata to JSON, or build your own custom code generators.

## Key Features

- **Pluggable parser backends** -- swap between parsing implementations (libclang ships built-in) without changing your code
- **Dataclass-based IR** -- a clean, inspectable Python representation of C/C++ declarations: structs, functions, enums, typedefs, and more
- **Pluggable output writers** -- generate CFFI bindings, ctypes modules, Cython `.pxd` files, LuaJIT FFI bindings, JSON, API diffs, LLM-optimized prompts, or write your own writer for any target format
- **Registry pattern** -- backends and writers self-register, making the system extensible without modifying core code

## Quick Example

```python
from headerkit import get_backend, get_writer

# Parse a C header
backend = get_backend()
header = backend.parse(
    open("mylib.h").read(),
    "mylib.h",
)

# Generate CFFI bindings (default writer)
writer = get_writer("cffi")
cdef_source = writer.write(header)

# Or pick another built-in writer
ctypes_writer = get_writer("ctypes")      # Python ctypes bindings
cython_writer = get_writer("cython")      # Cython .pxd declarations
lua_writer = get_writer("lua")            # LuaJIT FFI bindings
json_writer = get_writer("json", indent=2)  # JSON for inspection
prompt_writer = get_writer("prompt")      # Token-optimized LLM context

print(json_writer.write(header))
```

## How It Works

```mermaid
graph LR
    A["C/C++ Header"] --> B["Backend"]
    B --> C["IR"]
    C --> D["Writer"]
    D --> E["Output"]
    B -.- B1["ParserBackend<br>Protocol"]
    D -.- D1["WriterBackend<br>Protocol"]
```

1. A **backend** (e.g., `LibclangBackend`) parses C/C++ source code and produces an IR `Header` object containing `Declaration` nodes.
2. A **writer** (e.g., `CffiWriter`, `CtypesWriter`, `CythonWriter`, `LuaWriter`, `JsonWriter`, `DiffWriter`, `PromptWriter`) consumes the IR and produces output in a target format.

Both backends and writers follow simple protocols and are registered in a global registry, so you can add your own without modifying headerkit itself.

## Next Steps

<div class="grid cards" markdown>

-   **Installation**

    ---

    Install headerkit and its system dependency (libclang).

    [:octicons-arrow-right-24: Installation](guides/installation.md)

-   **Quick Start**

    ---

    Parse your first header and generate bindings in under five minutes.

    [:octicons-arrow-right-24: Quick Start](guides/quickstart.md)

-   **Architecture**

    ---

    Understand the three-layer backend/IR/writer architecture.

    [:octicons-arrow-right-24: Architecture Overview](guides/architecture.md)

-   **Cache System**

    ---

    Two-layer cache for libclang-free builds.

    [:octicons-arrow-right-24: Cache Guide](guides/cache.md)

-   **Build Backend**

    ---

    PEP 517 build backend for libclang-free installs via pip.

    [:octicons-arrow-right-24: Build Backend Guide](guides/build-backend.md)

-   **API Reference**

    ---

    Full reference for all IR types, backends, and writers.

    [:octicons-arrow-right-24: API Reference](reference/index.md)

</div>
