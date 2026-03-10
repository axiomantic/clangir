# headerkit

[![CI](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml)
[![Docs](https://github.com/axiomantic/headerkit/actions/workflows/docs.yml/badge.svg)](https://axiomantic.github.io/headerkit/)
[![PyPI](https://img.shields.io/pypi/v/headerkit)](https://pypi.org/project/headerkit/)
[![Python](https://img.shields.io/pypi/pyversions/headerkit)](https://pypi.org/project/headerkit/)
[![License](https://img.shields.io/github/license/axiomantic/headerkit)](https://github.com/axiomantic/headerkit/blob/main/LICENSE)

Parse C/C++ headers with libclang and emit output in any format.

Built-in writers produce ctypes, CFFI, Cython, LuaJIT FFI, JSON IR, and LLM-optimized prompt output, and the plugin system lets you add your own.

```mermaid
graph LR
    A[C/C++ headers] --> B[backend]
    B --> C[IR]
    C --> D[writer]
    D --> E[output]
```

## Features

- **One parse, many outputs**: generate multiple bindings in a single pass with `-w ctypes:lib.py -w cython:lib.pxd`
- **Plugin system**: register third-party backends and writers via Python entry points
- **Zero runtime dependencies**: pure Python, nothing to install beyond headerkit itself
- **Config file support**: `.headerkit.toml` or `[tool.headerkit]` in `pyproject.toml`
- **Multi-header merging**: pass multiple `.h` files and they are merged into a single umbrella header
- **API diff reports**: detect breaking changes between header versions with the `diff` writer

## Installation

```bash
pip install headerkit
```

Requires Python 3.10+.

Then install libclang:

```bash
headerkit install-libclang
```

Or install it manually:

| Platform | Command |
|----------|---------|
| macOS | `brew install llvm` or Xcode Command Line Tools |
| Ubuntu | `sudo apt install libclang-dev` |
| Fedora | `sudo dnf install clang-devel` |
| Windows | `winget install LLVM.LLVM` or [LLVM installer](https://github.com/llvm/llvm-project/releases) |

Supports LLVM 18, 19, 20, and 21.

## Quick start

Given a header `mylib.h`:

```c
typedef struct {
    int x;
    int y;
} Point;

Point* create_point(int x, int y);
void free_point(Point* p);
```

Generate CFFI cdef declarations:

```console
$ headerkit mylib.h -w cffi
typedef struct Point {
    int x;
    int y;
} Point;
Point * create_point(int x, int y);
void free_point(Point * p);
```

Generate a Cython `.pxd` file:

```console
$ headerkit mylib.h -w cython
cdef extern from "mylib.h":

    ctypedef struct Point:
        int x
        int y

    Point* create_point(int x, int y)

    void free_point(Point* p)
```

Generate a complete ctypes binding module:

```console
$ headerkit mylib.h -w ctypes
"""ctypes bindings generated from mylib.h."""

import ctypes
import ctypes.util
import sys

# ... library loading omitted for brevity ...

# ============================================================
# Structures and Unions
# ============================================================

class Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
    ]

# ============================================================
# Function Prototypes
# ============================================================

_lib.create_point.argtypes = [ctypes.c_int, ctypes.c_int]
_lib.create_point.restype = ctypes.POINTER(Point)

_lib.free_point.argtypes = [ctypes.POINTER(Point)]
_lib.free_point.restype = None
```

Multiple outputs in one pass:

```bash
headerkit mylib.h -w cython:mylib.pxd -w json:ir.json
```

With include paths and preprocessor defines:

```bash
headerkit mylib.h -I /usr/local/include -D VERSION=2 -w cffi
```

## CLI reference

```
headerkit [options] FILE [FILE ...]
```

### Flags

| Flag | Description |
|------|-------------|
| `-b NAME`, `--backend NAME` | Parser backend (default: `libclang`) |
| `-I DIR` | Add include directory (repeatable) |
| `-D MACRO[=VALUE]` | Define preprocessor macro (repeatable) |
| `--backend-arg ARG` | Pass extra argument to the backend (repeatable) |
| `-w WRITER[:FILE]` | Write output to a file, or omit `:FILE` for stdout (repeatable) |
| `--writer-opt WRITER:KEY=VALUE` | Pass an option to a writer (repeatable) |
| `--config PATH` | Load config from `PATH` instead of searching |
| `--no-config` | Skip all config file loading |
| `--version` | Print version and exit |

At most one `-w` flag may omit the output path. Multiple writers sending to stdout is an error.

### Writers

| Writer | Output | Notes |
|--------|--------|-------|
| `cffi` | CFFI cdef strings | Declarations for `ffibuilder.cdef()` |
| `ctypes` | Python module | Complete ctypes binding module |
| `cython` | .pxd file | Cython declaration file with C++ support |
| `diff` | JSON or Markdown | API compatibility report between two header versions |
| `json` | JSON | Full IR serialization |
| `lua` | LuaJIT FFI bindings | `ffi.cdef()` declarations for LuaJIT |
| `prompt` | Compact text | Token-optimized IR for LLM context windows |

Pass writer options with `--writer-opt`:

```bash
headerkit mylib.h -w cffi --writer-opt cffi:exclude_patterns=^__
headerkit mylib.h -w ctypes:mylib.py --writer-opt ctypes:lib_name=mylib
```

### Config file

headerkit searches from the current directory upward for `.headerkit.toml`, or for a
`[tool.headerkit]` section in `pyproject.toml`. Use `--no-config` to skip this.

```toml
# .headerkit.toml
backend = "libclang"
writers = ["cffi"]
include_dirs = ["/usr/local/include"]
plugins = ["mypkg.headerkit_plugin"]

[writer.cffi]
exclude_patterns = ["^__", "^_internal"]

[writer.ctypes]
lib_name = "mylib"
```

Command-line flags override config file values.

### Plugins

Register third-party backends and writers via Python entry points:

```toml
# In your package's pyproject.toml
[project.entry-points."headerkit.backends"]
mybackend = "mypkg.backend:MyBackend"

[project.entry-points."headerkit.writers"]
mywriter = "mypkg.writer:MyWriter"
```

Or load plugins explicitly from the config file:

```toml
# .headerkit.toml
plugins = ["mypkg.headerkit_plugin"]
```

## Python API

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend("libclang")
header = backend.parse('#include "mylib.h"', "wrapper.h", include_dirs=["/path/to/include"])

writer = get_writer("cffi")
print(writer.write(header))
```

Full documentation, guides, and API reference: [axiomantic.github.io/headerkit](https://axiomantic.github.io/headerkit/)

## Development

```bash
git clone https://github.com/axiomantic/headerkit.git
cd headerkit
pip install -e '.[dev]'
pytest
```

## License

This project is licensed under the [MIT License](LICENSE).

The vendored clang Python bindings in `headerkit/_clang/v*/` are from the
[LLVM Project](https://llvm.org/) and are licensed under the
[Apache License v2.0 with LLVM Exceptions](headerkit/_clang/LICENSE).
