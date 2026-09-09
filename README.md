# headerkit

[![CI](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml)
[![Docs](https://github.com/axiomantic/headerkit/actions/workflows/docs.yml/badge.svg)](https://axiomantic.github.io/headerkit/)
[![PyPI](https://img.shields.io/pypi/v/headerkit)](https://pypi.org/project/headerkit/)
[![Python](https://img.shields.io/pypi/pyversions/headerkit)](https://pypi.org/project/headerkit/)
[![License](https://img.shields.io/github/license/axiomantic/headerkit)](https://github.com/axiomantic/headerkit/blob/main/LICENSE)

> **Need sturdy, upgradeable bindings? We got you.**
> The universal interop & bindings toolkit: C/C++ in → Python, Mojo, Nim, LuaJIT, & C shims out.
> Scaffolds packages, not just files. Easily extended for any input language or output project type. For humans and LLMs.

HeaderKit parses native C and C++ headers into a normalized intermediate representation (IR). From that single IR, it generates foreign language bindings, scaffolds turnkey packages with verification tests, tracks breaking API diffs, and compresses headers for LLM prompt windows.

---

## What are you looking to do?

| Goal | Quick Command / Action | Section |
| :--- | :--- | :--- |
| **Generate Python bindings** (ctypes, CFFI, Cython) | `headerkit mylib.h -w ctypes -o ctypes:bindings.py` | [Python Bindings](#1-generate-python-bindings-ctypes-cffi-cython) |
| **Generate Mojo, Nim, or LuaJIT bindings** | `headerkit mylib.h -w mojo -o mojo:mylib.mojo` | [Systems & Scripting](#2-generate-systems--scripting-bindings-mojo-nim-luajit) |
| **Wrap C++ classes in a C-ABI shim** (`extern "C"`) | `headerkit mylib.hpp -w cshim -o cshim:mylib_cshim.cpp` | [C Shim Wrappers](#3-wrap-c-classes-in-a-c-abi-shim) |
| **Parse C/C++ without LLVM/libclang** | `headerkit mylib.h -b tree-sitter -w ctypes` | [Zero-Dependency Parsing](#4-zero-dependency-parsing-with-tree-sitter) |
| **Scaffold a turnkey package with tests** | `headerkit mylib.h -w nim --layout package --package-name mypkg` | [Package Scaffolding](#5-scaffold-turnkey-packages-with-tests) |
| **Detect breaking API changes between versions** | `DiffWriter(baseline=v1, format="markdown").write(v2)` | [API Diffing](#6-detect-breaking-api-changes) |
| **Compress headers for LLM prompt windows** | `headerkit mylib.h -w prompt` | [LLM Context](#7-compress-headers-for-llm-prompts) |
| **Ship wheels without requiring libclang on user machines** | `headerkit cache populate mylib.h -w cffi` | [Build Backend & Cache](#8-ship-wheels-without-requiring-libclang) |
| **Inspect or transform IR programmatically** | `headerkit mylib.h -w json -o json:ast.json` | [Programmatic IR](#9-inspect-ir-programmatically) |

---

## Why HeaderKit?

Traditional binding generators (SWIG, bindgen, ctypesgen) dump raw, isolated binding code into the void. Integrating those files into a project requires handwritten Makefiles, setup scripts, package manifests, and linking glue. Keeping bindings updated as upstream libraries evolve is an error-prone chore.

HeaderKit solves this with three core design pillars:

1. **Turnkey Packages vs. Raw Code Dumps**: HeaderKit's `--layout package` doesn't just emit binding syntax; it scaffolds complete, idiomatic packages (e.g. Nimble packages with `--mm:orc`, Mojo packages with dynamic library handles, Python packages with build backends) along with automated tests that immediately verify foreign symbol resolution.
2. **Easily Extended to Any Source or Target Language**: Built on a unified hook engine (`headerkit.hooks`). Write a custom backend or target writer in ~50 lines of Python using typed [`SourceUnit`][headerkit.ir.SourceUnit] IR, register it via standard Python entry points or config files, or customize layout scaffolding templates without touching core code.
3. **Automated Upstream Updates & Zero-Dependency Downstream**: Keep bindings in lockstep with upstream releases. HeaderKit's PEP 517 build backend and committed cache store (`.headerkit/`) let downstream consumers `pip install` wheels without needing `libclang` or system compilers installed.

---

## Quick Solutions

Every example below assumes this sample C header:

```c
// mylib.h
typedef struct { int x, y; } Point;
int distance(const Point *a, const Point *b);
```

### 1. Generate Python Bindings (ctypes, CFFI, Cython)

Generate zero-build ctypes modules, CFFI cdef declarations, or compiled Cython `.pxd` headers:

```bash
# Drop-in Python ctypes module (zero build step)
headerkit mylib.h -w ctypes -o ctypes:bindings.py

# CFFI declarations for ffibuilder.cdef()
headerkit mylib.h -w cffi -o cffi:_defs.cdef.txt

# Cython .pxd declaration file for compiled C/C++ interop
headerkit mylib.h -w cython -o cython:mylib.pxd
```

*See the [ctypes Reference](https://axiomantic.github.io/headerkit/reference/ctypes/), [CFFI Guide](https://axiomantic.github.io/headerkit/guides/cffi/), and [Cython Reference](https://axiomantic.github.io/headerkit/reference/cython/).*

### 2. Generate Systems & Scripting Bindings (Mojo, Nim, LuaJIT)

Bridge C and C++ libraries into modern systems and scripting runtimes:

```bash
# Mojo module with DLHandle dynamic symbol loading
headerkit mylib.h -w mojo -o mojo:mylib.mojo

# Native Nim module with {.importc.} pragma bindings
headerkit mylib.h -w nim -o nim:mylib.nim

# LuaJIT FFI bindings with ffi.cdef[[ ... ]]
headerkit mylib.h -w lua -o lua:mylib_ffi.lua
```

*See the [Mojo Reference](https://axiomantic.github.io/headerkit/reference/mojo/), [Nim Reference](https://axiomantic.github.io/headerkit/reference/nim/), and [LuaJIT Reference](https://axiomantic.github.io/headerkit/reference/lua/).*

### 3. Wrap C++ Classes in a C-ABI Shim

Directly binding complex C++ classes across foreign function interfaces is fragile due to mangled symbols and exception boundaries. The `cshim` writer automatically generates an `extern "C"` wrapper library with opaque handles and exception guards:

```bash
headerkit mylib.hpp -w cshim -o cshim:mylib_cshim.cpp
```

```cpp
// generated mylib_cshim.cpp
#include "mylib.hpp"

extern "C" {
typedef void* PointHandle;
PointHandle Point_create(int x, int y) { return new Point(x, y); }
void Point_destroy(PointHandle self) { delete static_cast<Point*>(self); }
}
```

*See the [CShim Reference](https://axiomantic.github.io/headerkit/reference/cshim/).*

### 4. Zero-Dependency Parsing with Tree-Sitter

When system LLVM / `libclang` is not installed or in lightweight CI environments, HeaderKit can parse C and C++ headers directly using precompiled Tree-sitter grammars (`tree-sitter-c` and `tree-sitter-cpp`):

```bash
# Parse C header using tree-sitter backend
headerkit mylib.h -b tree-sitter -w ctypes -o ctypes:bindings.py

# Parse C++ header using tree-sitter backend
headerkit mylib.hpp -b tree-sitter -w cython -o cython:mylib.pxd
```

*See the [Tree-sitter Backend Guide](https://axiomantic.github.io/headerkit/reference/treesitter/).*

### 5. Scaffold Turnkey Packages with Tests

Generate complete, buildable multi-file packages containing package manifests, compiler configs, and automated test stubs that verify foreign symbol resolution:

```bash
# Scaffold a full Nimble package with tests
headerkit mylib.h -w nim --layout package --package-name nim_vector -o nim:./nim_vector

# Scaffold with specific test stub styles (tripwire, unit, or both)
headerkit mylib.h -w nim --layout package --package-name nim_vector --test-type tripwire
```

Generated project structure:
```text
nim_vector/
├── nim_vector.nimble          # Package manifest with test tasks
├── nim.cfg                    # Compiler configuration (--mm:orc, --threads:on)
├── src/
│   ├── nim_vector.nim         # Public API module
│   └── nim_vector/
│       └── bindings.nim       # Generated foreign function interface
└── tests/
    ├── test_tripwire.nim      # Symbol resolution verification tests
    └── test_nim_vector.nim    # High-level unit test skeleton
```

*See the [Scaffolding Guide](https://axiomantic.github.io/headerkit/guides/scaffolding/).*

### 6. Detect Breaking API Changes

Compare two versions of a C/C++ header to detect signature mutations, missing struct fields, altered enum values, and type changes:

```python
from headerkit.backends import get_backend
from headerkit.writers.diff import DiffWriter

backend = get_backend("libclang")
old_api = backend.parse('#include "mylib_v1.h"', "v1.h")
new_api = backend.parse('#include "mylib_v2.h"', "v2.h")

print(DiffWriter(baseline=old_api, format="markdown").write(new_api))
```

*See the [Diff Writer Reference](https://axiomantic.github.io/headerkit/reference/diff/).*

### 7. Compress Headers for LLM Prompts

Large C/C++ headers waste tokens and clutter context windows with preprocessor noise and implementation details. The `prompt` writer produces a dense, token-optimized summary designed for LLM prompts:

```bash
headerkit mylib.h -w prompt
```

```text
// mylib.h (headerkit compact)
STRUCT Point {x:int, y:int}
FUNC distance(a:const Point*, b:const Point*) -> int
```

*See the [Prompt Writer Reference](https://axiomantic.github.io/headerkit/reference/prompt/).*

### 8. Ship Wheels Without Requiring libclang

HeaderKit includes a two-layer cache (`.headerkit/`) storing parsed IR and generated bindings. Commit the cache to version control and downstream users can install wheels without having `libclang` or LLVM installed:

```bash
# Populate cache across multiple architecture targets
headerkit cache populate mylib.h -w cffi --platform linux/amd64 --platform linux/arm64
git add .headerkit/ && git commit -m "cache: populate bindings"
```

In your project's `pyproject.toml`, declare the PEP 517 build backend to automatically regenerate bindings during `pip install`:

```toml
[build-system]
requires = ["headerkit", "hatchling"]
build-backend = "headerkit.build_backend"
```

*See the [Cache Guide](https://axiomantic.github.io/headerkit/guides/cache/) and [Build Backend Guide](https://axiomantic.github.io/headerkit/guides/build-backend/).*

### 9. Inspect IR Programmatically

Parse headers directly into a strongly typed Python AST or serialize them to JSON for downstream code generators, linters, or analysis tools:

```python
from headerkit import generate
from headerkit.backends import get_backend

# Serialized JSON IR
json_ir = generate("mylib.h", "json")

# Typed Python AST
backend = get_backend("libclang")
unit = backend.parse('#include "mylib.h"', "mylib.h")
for decl in unit.declarations:
    print(decl.name, type(decl))
```

*See the [IR Reference](https://axiomantic.github.io/headerkit/reference/ir/) and [JSON Reference](https://axiomantic.github.io/headerkit/reference/json/).*

---

## Installation

```bash
pip install headerkit
```

Requires Python 3.10+.

To install the optional `libclang` parser backend (if not already present on your system):

```bash
headerkit install-libclang
```

Or install it via your system package manager:

| Platform | Command |
| :--- | :--- |
| **macOS** | `brew install llvm` or Xcode Command Line Tools |
| **Ubuntu / Debian** | `sudo apt install libclang-dev` |
| **Fedora / RHEL** | `sudo dnf install clang-devel` |
| **Windows** | `winget install LLVM.LLVM` or [LLVM releases](https://github.com/llvm/llvm-project/releases) |

*HeaderKit vendors LLVM bindings supporting libclang 18, 19, 20, 21, 22, and 23.*

---

## CLI Reference

```text
headerkit [options] HEADER_OR_GLOB [HEADER_OR_GLOB ...]
```

### Options

| Flag | Description |
| :--- | :--- |
| `-b NAME`, `--backend NAME` | Parser backend (default: `libclang`, or `tree-sitter`) |
| `-w WRITER`, `--writer WRITER` | Output writer to invoke (repeatable) |
| `-o WRITER:PATH`, `--output` | Output destination template (repeatable, e.g. `ctypes:bindings.py`) |
| `--layout {file,package,project}` | Output layout mode (`file` or `package`) |
| `--package-name NAME` | Package name when scaffolding package layouts |
| `--test-type {both,tripwire,unit,none}` | Test stub style to scaffold (default: `both`) |
| `-I DIR`, `--include-dir DIR` | Add include directory (repeatable) |
| `-D MACRO[=VALUE]` | Define preprocessor macro (repeatable) |
| `--backend-arg ARG` | Pass extra argument to the parser backend |
| `--writer-opt WRITER:KEY=VALUE` | Pass writer-specific options (repeatable) |
| `--store-dir DIR` | Cache store directory (default: `.headerkit/`) |
| `--target TRIPLE` | Target triple for cross-compilation (e.g. `aarch64-apple-darwin`) |
| `--no-cache` | Disable all cache lookups |
| `--config PATH` | Path to explicit `.headerkit.toml` config file |
| `--no-config` | Skip loading configuration files |
| `--version` | Display version and exit |

---

## Configuration File

HeaderKit automatically reads configuration from `.headerkit.toml` or the `[tool.headerkit]` table in `pyproject.toml`:

```toml
# .headerkit.toml
backend = "libclang"
writers = ["ctypes", "cffi"]
include_dirs = ["/usr/local/include"]

[writer.ctypes]
lib_name = "mylib"

[writer.cffi]
exclude_patterns = ["^__", "^_internal"]
```

Values support `${ENV_VAR}` expansion for build-time paths injected by CMake or CI systems.

---

## Test Work Orders

Scaffolding a project also generates its tests. A generator knows what an API *is*,
but not what it is *for*, so HeaderKit splits generated tests into three tiers and is
explicit about which is which.

| Tier | What HeaderKit knows | What it emits |
|---|---|---|
| 1 | The call and the expected result | A **real, passing** test -- struct field round-trip, unsigned bit-field bounds, enum value coverage |
| 2 | The cases, but not the expectation | One **failing** case per enumerator, per overload, or a NULL case |
| 3 | Only that the function exists | One **failing** stub |

Stubs fail loudly and carry their instruction on the failing assertion, together with
the signature verbatim and a definition of done:

```
FAILED tests/test_workorder.py::test_ct_scale[null_s] - Failed: WORK ORDER: for the
`behaviour` case, describe what `ct_scale` is for and assert it. For the `null_s`
case, decide what it does when `s` is NULL -- an error, or undefined and therefore
untestable? Done means: call it and assert on the result. Asserting that it does not
raise is insufficient.
```

The test run is the progress meter -- a stub turns from red to green when it is
written. Scaffolded projects also get `WORK_ORDER.md` (the same list in prose),
`SUGGESTIONS.md` (generic wrapper design ideas), and an `AGENTS.md` telling the next
AI session to **ask you** before working through any of it.

Re-running the scaffolder never overwrites a test you have written.

See [Test work orders](docs/guides/work-orders.md) for the full tier rules, the
deliberate exclusions, and the Nim macro that makes per-case reporting work.

## Extensibility & Plugins

HeaderKit's hook architecture makes it straightforward to add new source languages, target writers, or custom package layouts without modifying the core repository.

Register third-party plugins in your own package via `pyproject.toml` entry points:

```toml
[project.entry-points."headerkit.backends"]
mybackend = "mypkg.backend:MyBackend"

[project.entry-points."headerkit.writers"]
mywriter = "mypkg.writer:MyWriter"
```

Or write a custom writer in Python:

```python
from headerkit.ir import SourceUnit
from headerkit.writers import BaseWriter, register_writer

class RubyFfiWriter(BaseWriter):
    @property
    def name(self) -> str:
        return "ruby"

    def write(self, unit: SourceUnit) -> str:
        lines = ["require 'ffi'", "module MyLib", "  extend FFI::Library"]
        # Iterate over unit.declarations...
        return "\n".join(lines)

register_writer("ruby", RubyFfiWriter)
```

*See the [Architecture Guide](https://axiomantic.github.io/headerkit/guides/architecture/) and [Custom Writers Guide](https://axiomantic.github.io/headerkit/guides/custom-writers/).*

---

## Roadmap

- **Origin Library Versioning & ABI Multi-Version Metadata**: Track signatures across multiple upstream release versions to generate version-guarded symbols and automated migration shims.
- **Bi-directional Bridges**: Generate C/C++ header interfaces and C export shims from high-level Python and Mojo source definitions.
- **Grammar-Based Polyglot Source Extraction**: Extract C-ABI interfaces from Rust, Zig, and Nim source code into normalized IR using formal Tree-sitter grammars (`tree-sitter-rust`, `tree-sitter-zig`, `tree-sitter-nim`).
- **Expanded Target Writers**: Additional turnkey writers for Rust (`bindgen`-free FFI), Go (`cgo`), and Swift.

---

## Development

```bash
git clone https://github.com/axiomantic/headerkit.git
cd headerkit
pip install -e '.[dev]'
pytest
```

---

## License

HeaderKit is open source licensed under the [MIT License](LICENSE).

Vendored LLVM clang Python bindings in `headerkit/_clang/` are licensed under the [Apache License v2.0 with LLVM Exceptions](headerkit/_clang/LICENSE).
