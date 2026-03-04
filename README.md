# headerkit

[![CI](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml)
[![Docs](https://github.com/axiomantic/headerkit/actions/workflows/docs.yml/badge.svg)](https://axiomantic.github.io/headerkit/)
[![PyPI](https://img.shields.io/pypi/v/headerkit)](https://pypi.org/project/headerkit/)
[![Python](https://img.shields.io/pypi/pyversions/headerkit)](https://pypi.org/project/headerkit/)

Parse C/C++ headers with libclang and emit output in any format.

headerkit is the parser engine behind [ctypesgen2](https://github.com/ctypesgen/ctypesgen) (ctypes bindings)
and [autopxd2](https://github.com/elijahr/autopxd2) (Cython .pxd generation).

## Installation

```bash
pip install headerkit
```

Requires Python 3.10+. No Python runtime dependencies.

Then install libclang:

```bash
headerkit install-libclang     # subcommand form
headerkit-install-libclang     # standalone script alias
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

```bash
# CFFI bindings to stdout
headerkit mylib.h -w cffi

# ctypes binding module to a file
headerkit mylib.h -w ctypes:mylib.py

# Multiple outputs in one pass
headerkit mylib.h -w cython:mylib.pxd -w json:ir.json

# With include paths and preprocessor defines
headerkit mylib.h -I /usr/local/include -D VERSION=2 -w cffi
```

## CLI reference

```
headerkit [options] FILE [FILE ...]
```

Multiple input files are merged into a single umbrella header before parsing.

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

---

Full documentation, guides, and API reference: [axiomantic.github.io/headerkit](https://axiomantic.github.io/headerkit/)

## Python API

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend("libclang")
header = backend.parse('#include "mylib.h"', "wrapper.h", include_dirs=["/path/to/include"])

writer = get_writer("cffi")
print(writer.write(header))
```

See the [API reference](https://axiomantic.github.io/headerkit/) for full documentation of
backends, writers, and the IR.

## Architecture

A backend parses headers and produces a language-neutral IR. Writers consume that IR and
produce output. They are independent; any backend feeds any writer.

```
C/C++ headers --> [backend] --> IR --> [writer] --> output
```

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
