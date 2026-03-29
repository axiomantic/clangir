# Backends

Parser backends convert C/C++ source code into the headerkit
[IR](ir.md). The [`ParserBackend`][headerkit.ir.ParserBackend] protocol defines
the interface that all backends implement.

Backends are accessed through a registry. Use [`get_backend()`][headerkit.backends.get_backend]
to obtain an instance and [`list_backends()`][headerkit.backends.list_backends] to discover
what is available.

## Available Backends

| Backend | Description | C++ Support | Macro Extraction |
|---------|-------------|:-----------:|:----------------:|
| `libclang` | LLVM clang-based parser | Yes | Yes |

## Protocol

See [`ParserBackend`][headerkit.ir.ParserBackend] on the IR Types page for the
full protocol definition including `parse()`, `name`, `supports_macros`, and
`supports_cpp`.

## Registry Functions

::: headerkit.backends.get_backend
    options:
      show_source: false

::: headerkit.backends.get_default_backend
    options:
      show_source: false

::: headerkit.backends.list_backends
    options:
      show_source: false

::: headerkit.backends.is_backend_available
    options:
      show_source: false

::: headerkit.backends.register_backend
    options:
      show_source: false

::: headerkit.backends.reload_backends
    options:
      show_source: false

!!! note "When to call"
    Call `reload_backends()` after `auto_install()` within the same Python
    process to pick up the newly installed backend. It is not needed between
    separate process invocations.

::: headerkit.backends.get_backend_info
    options:
      show_source: false
