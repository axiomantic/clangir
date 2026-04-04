# CFFI Build Integration

headerkit generates `.cdef.txt` files for CFFI projects. Combined with
[cffi_buildtool](https://github.com/niccokunzmann/cffi_buildtool)'s
`read-sources` mode, this eliminates custom build scripts entirely:
headerkit handles the cdef generation, cffi_buildtool handles the
compilation, and your build system ties them together.

## Environment variable expansion

Config values in `[tool.headerkit]` support `${VAR}` syntax for
referencing environment variables set at build time. This is useful when
a dependency's include path is determined by CMake or another build
system.

```toml
[tool.headerkit]
backend = "libclang"
writers = ["cffi"]

[[tool.headerkit.headers]]
pattern = "include/nng.h"
include_dirs = ["${NNG_INCLUDE_DIR}"]
```

When headerkit loads this config, it replaces `${NNG_INCLUDE_DIR}` with
the value of the `NNG_INCLUDE_DIR` environment variable. If the variable
is not set, headerkit raises an error immediately rather than silently
producing wrong output.

Expansion works in any string value, including lists and nested tables.
The syntax is strictly `${VAR}` (not `$VAR`).

## Macro extraction with define_patterns

libclang cannot capture `#define` values because the preprocessor
evaluates them before the AST is built. The `define_patterns` option
fills this gap by scanning the raw header text for `#define` names that
match one or more regex patterns.

```toml
[tool.headerkit.writer.cffi]
define_patterns = ["NNG_FLAG_\\w+", "NNG_\\w+_VERSION", "NNG_MAXADDRLEN"]
```

Matching names are emitted as:

```c
#define NNG_FLAG_ALLOC ...
#define NNG_FLAG_NONBLOCK ...
#define NNG_MAJOR_VERSION ...
#define NNG_MAXADDRLEN ...
```

The `...` is CFFI's "figure it out at compile time" syntax. During
`ffi.verify()` or `ffi.set_source()` compilation, CFFI resolves each
value from the actual C headers. This gives your Python code access to
the constants without hardcoding their values.

Patterns use Python's `re.search()`, so they match anywhere in the
`#define` name. Use anchors for precision: `"^NNG_FLAG_\\w+$"` matches
names starting with `NNG_FLAG_`, while `"NNG_FLAG"` also matches
`SOME_NNG_FLAG_EXTRA`.

### Umbrella headers

When using the `code=` parameter with an umbrella header (a string that
`#include`s other headers), `define_patterns` automatically resolves the
included files against your `include_dirs` and scans them too. This
means you can use an umbrella header like:

```python
generate(
    "umbrella.h", "cffi",
    code='#include <nng/nng.h>\n',
    include_dirs=["/path/to/nng/include"],
    writer_options={"define_patterns": [r"NNG_FLAG_\w+"]},
)
```

headerkit will find and scan `nng/nng.h` for matching `#define` names,
even though the umbrella string itself contains only `#include`
directives.

## Extra cdef lines with extra_cdef

CFFI's `extern "Python"` syntax lets you define Python callbacks that C
code can call. Since these declarations do not come from a C header,
headerkit cannot generate them automatically. The `extra_cdef` option
appends literal cdef lines to the generated output.

```toml
[tool.headerkit.writer.cffi]
extra_cdef = ['extern "Python" void _async_complete(void *);']
```

Multiple lines are supported:

```toml
[tool.headerkit.writer.cffi]
extra_cdef = [
    'extern "Python" void _async_complete(void *);',
    'extern "Python" int _dial_callback(void *, int);',
]
```

These lines are appended verbatim after all generated declarations.

## Full example with cffi_buildtool

This example shows a complete integration for a project that wraps
[nng](https://nng.nanomsg.org/) using CMake's FetchContent, headerkit
for cdef generation, and cffi_buildtool for compilation.

### CMakeLists.txt

```cmake
include(FetchContent)
FetchContent_Declare(nng
    GIT_REPOSITORY https://github.com/nanomsg/nng.git
    GIT_TAG v1.9.0
)
FetchContent_MakeAvailable(nng)

# After nng is built, generate CFFI bindings
add_custom_command(
    OUTPUT ${CMAKE_CURRENT_SOURCE_DIR}/nng/_nng.cdef.txt
    COMMAND ${Python_EXECUTABLE} -m headerkit
        ${nng_SOURCE_DIR}/include/nng/nng.h
        -w cffi
        -o cffi:nng/_nng.cdef.txt
        -I ${nng_SOURCE_DIR}/include
    DEPENDS nng
    COMMENT "Generating CFFI cdef with headerkit"
)

# cffi_buildtool compiles the extension module
add_custom_command(
    OUTPUT ${CMAKE_CURRENT_SOURCE_DIR}/nng/_nng_cffi.c
    COMMAND ${Python_EXECUTABLE} -m cffi_buildtool read-sources
        --cdef nng/_nng.cdef.txt
        --csrc nng/_nng.csrc.c
        --output nng/_nng_cffi.c
        --module-name nng._nng_cffi
    DEPENDS nng/_nng.cdef.txt nng/_nng.csrc.c
    COMMENT "Building CFFI extension with cffi_buildtool"
)
```

### pyproject.toml

```toml
[build-system]
requires = ["headerkit", "hatchling"]
build-backend = "headerkit.build_backend"

[tool.headerkit]
backend = "libclang"
writers = ["cffi"]

[[tool.headerkit.headers]]
pattern = "include/nng/nng.h"
include_dirs = ["${NNG_INCLUDE_DIR}"]

[tool.headerkit.writer.cffi]
define_patterns = ["NNG_FLAG_\\w+", "NNG_\\w+_VERSION", "NNG_MAXADDRLEN"]
extra_cdef = ['extern "Python" void _async_complete(void *);']

[tool.headerkit.output]
cffi = "nng/_nng.cdef.txt"
```

### The csrc file

The `_nng.csrc.c` file is a small hand-written C source that
cffi_buildtool passes to `ffi.set_source()`:

```c
#include <nng/nng.h>
```

### What happens at build time

1. CMake fetches and builds nng, setting `NNG_INCLUDE_DIR`.
2. headerkit parses `nng.h` with libclang, scanning for `#define` names
   matching `define_patterns`, and writes `_nng.cdef.txt`.
3. cffi_buildtool reads `_nng.cdef.txt` and `_nng.csrc.c`, then
   generates and compiles the CFFI extension module.
4. The resulting `_nng_cffi` module is importable from Python.

## CI workflow

For multi-platform projects, the `.headerkit/` cache directory ensures
builds work without libclang installed. See the
[CI Store Population](github-action.md) guide for a workflow that keeps
`.headerkit/` up to date across Linux, macOS, and Windows.
