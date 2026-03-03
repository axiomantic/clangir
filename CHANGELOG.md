# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.4] - 2026-03-03

### Fixed

- Prompt writer incorrectly classified `typedef void (*fn)(int);` as a plain typedef in compact and standard modes when libclang represents the underlying type as `Pointer(FunctionPointer(...))`. Compact mode now emits `CALLBACK fn(...) -> void` and standard mode places it in the `callbacks:` section.
- Prompt writer cross-reference map built keys with `struct`/`union`/`enum` prefixes (e.g. `struct Config`) while declaration dicts use bare names (`Config`), so `used_in` was never populated. Keys are now normalized to bare names.

### Added

- Integration roundtrip tests for ctypes, Cython, Lua, prompt, and diff writers: full `libclang → IR → writer output` pipeline coverage for each writer, exercising structs, enums, functions, typedefs, constants, anonymous types, and empty headers.
- Integration smoke tests for all seven writers against real-world library headers (sqlite3, zlib, lua, curl, SDL2) in `test_real_headers.py`.

## [0.7.3] - 2026-03-01

### Fixed

- `get_backend_info()` always reported backends as `available: True` due to tautological check; now attempts instantiation to determine real availability
- Integration test fixtures silently swallowed all download exceptions, causing the entire integration suite to report green with zero assertions; fixtures now emit warnings on failure
- `_parse_header` test helper caught overly broad `Exception`, masking parser regressions as skipped tests; narrowed to `RuntimeError`
- Clang loader fallback tests did not verify which version module was loaded, allowing wrong-version regressions to survive
- Windows clang detection tests did not verify constructed file paths, allowing path construction bugs to survive
- `test_anonymous_struct_skipped` in ctypes writer contained a tautological assertion that could never fail
- `test_output_is_valid_json` serialized 6 declaration types but never verified any content
- `test_mixed_declarations` in prompt writer ran 3 modes x 7 declarations but only checked output was non-empty

### Added

- Macro parsing test coverage: integer, hex, negative, string, and function-like macro tests for the libclang backend (~190 lines of previously untested production code)
- Forward-declaration-to-definition replacement test for libclang backend
- `_ensure_backends_loaded` error handling and lazy loading tests
- Complex pattern roundtrip tests: bitfield structs, array-in-struct fields, nested structs
- Minimum declaration count assertions for real-world header integration tests (sqlite3, zlib, lua, curl, SDL2)
- Type-aware symbol verification in integration tests (checks declaration kind, not just name)
- JSON roundtrip count consistency checks (writer output count must match parse result)
- Invalid `CIR_CLANG_VERSION` env var fallthrough test
- Union member verification for `TypeExpr` and `Declaration` public API type aliases
- Registry cardinality and content checks for Cython type registries
- Anonymous declaration skip test for diff writer
- Variable integration test for Lua writer
- `--skip-verify` flag test and package manager fallthrough test for install_libclang
- PROVENANCE file hash verification in vendor tests

## [0.7.2] - 2026-03-01

### Fixed

- macOS cross-architecture: `ValueError: Unknown backend: 'libclang'` when an x86_64 process (e.g. cibuildwheel x86_64 test phase on Apple Silicon) finds an arm64-only Homebrew libclang first; `_configure_libclang` now iterates through all candidate paths instead of giving up after the first architecture-incompatible dylib fails to load

## [0.7.1] - 2026-02-28

### Fixed

- Windows x64: `LibclangError: function 'clang_getFullyQualifiedName' not found` when system LLVM is older than the vendored v21 bindings; disable cindex compatibility check so unused functions are silently skipped
- Windows x64: `install_libclang` now pins the Chocolatey LLVM version to match the vendored bindings instead of installing whatever default Chocolatey provides
- macOS CI: `ValueError: Unknown backend: 'libclang'` in test environments where libclang is bundled inside a versioned Xcode app bundle (e.g. `Xcode_16.2.app`); added xcrun-based discovery and glob for versioned Xcode paths
- Missing `concurrency` groups on six GitHub Actions workflows (auto-tag, check-llvm, check-python, docs, pre-commit-autoupdate, release)

## [0.7.0] - 2026-02-28

### Added

- `.pyi` type stubs for all vendored clang bindings (v18-v21), enabling mypy to type-check code that uses vendored clang modules
- CI stubtest gate: `mypy.stubtest` validates that `.pyi` stubs match the runtime API of each vendored version, blocking merges on mismatch
- Pre-commit autoupdate workflow (`.github/workflows/pre-commit-autoupdate.yml`): weekly automated PRs to update pre-commit hook versions
- Auto-vendor workflow: `check-llvm.yml` now opens PRs with vendored code and copied stubs when new LLVM versions are detected (falls back to issues on failure)
- Vendoring script (`scripts/vendor_clang.py`): downloads cindex.py, writes PROVENANCE, copies nearest version's stubs, updates `VENDORED_VERSIONS`
- Unit tests for the vendoring script (`tests/test_vendor_clang.py`)

### Changed

- Removed mypy exclude for vendored clang directories; mypy now uses `.pyi` stubs instead of ignoring vendored code entirely

## [0.6.1] - 2026-02-28

### Fixed

- README incorrectly claimed "zero runtime dependencies" when libclang is a required system dependency; clarified to "zero Python package dependencies"
- `Function.__str__` now places calling convention after return type (`int __stdcall__ foo()` not `__stdcall__int foo()`)
- `is_typedef` in JSON writer now only included when `True`, consistent with other boolean flags

### Added

- Auto-tag GitHub Action: automatically creates version tags when `pyproject.toml` version changes on main, triggering the release pipeline

### Changed

- Extract duplicated clang.exe version detection into `_get_version_from_clang_exe()` helper
- Use `normalize_path()` in Windows search path tests instead of manual string replacement
- Strengthen test assertions for const qualifiers on pointer types and cimport line detection

## [0.6.0] - 2026-02-28

### Added

- `stub_cimport_prefix` parameter for CythonWriter/PxdWriter: configurable stub cimport generation (e.g., `from autopxd.stubs.stdarg cimport va_list`)
- Comprehensive Cython type registry tests (17 tests)
- Additional Cython writer tests: full-text output assertions, pointer/array formatting, stub cimport integration (30 tests)

## [0.5.0] - 2026-02-28

### Added

- `Field.bit_width` IR field for C bitfield support
- `Field.anonymous_struct` IR field for anonymous nested struct/union members
- `Struct.is_packed` IR field for `__attribute__((packed))` structs
- `Function.calling_convention` and `FunctionPointer.calling_convention` IR fields
- CtypesWriter: generates complete Python ctypes binding modules
- CythonWriter: generates Cython .pxd declaration files with C++ support (ported from autopxd2)
- DiffWriter: generates API compatibility reports in JSON or Markdown format
- PromptWriter: generates token-optimized IR output for LLM context (compact/standard/verbose)
- LuaWriter: generates LuaJIT FFI binding files

## [0.4.0] - 2026-02-28

### Added

- PyPy support: compatibility shim for `c_interop_string` that avoids `c_char_p` subclassing
- End-to-end integration tests for JSON writer pipeline (18 new roundtrip tests)
- Real-world library header tests: sqlite3, zlib, lua, libcurl, SDL2, CPython (21 tests)
- CI caching for downloaded test headers
- `download` pytest marker for tests requiring network access
- Unit tests for PyPy compatibility monkey-patch (20 tests)

### Changed

- Renamed package from `clangir` to `headerkit` (`pip install headerkit`)
- Console script renamed from `clangir-install-libclang` to `headerkit-install-libclang`

## [0.3.3] - 2026-02-28

### Added

- CI workflow to test `headerkit-install-libclang` across Linux, macOS, and Windows

## [0.3.2] - 2026-02-28

### Added

- `headerkit-install-libclang` CLI tool for automated platform-specific libclang installation
- Console script entry point (`headerkit-install-libclang`) in pyproject.toml
- Documentation guide and API reference for the install tool
- Support for Linux (dnf, apt-get, apk), macOS (Homebrew), Windows x64 (Chocolatey), and Windows ARM64 (direct LLVM download)

### Fixed

- `install_libclang` verification result was ignored, now returns exit code 1 on verification failure
- Narrowed broad `except Exception` to `(ImportError, OSError, RuntimeError)` in verification

## [0.3.1] - 2026-02-28

### Added

- Mermaid diagrams in documentation: pipeline flowcharts and IR class hierarchies

### Fixed

- JSON export tutorial incorrectly listed `is_union` as a JSON output field
- Quickstart guide showed wrong pointer spacing (`char *` vs `char*`)
- `header_to_cffi` docstring converted from Google-style to Sphinx-style for mkdocstrings

## [0.3.0] - 2026-02-27

### Added

- Pluggable writer protocol (`WriterBackend`) mirroring the existing backend registry pattern
- Writer registry with `register_writer()`, `get_writer()`, `list_writers()`, `is_writer_available()`, `get_default_writer()`, `get_writer_info()`
- `CffiWriter` class wrapping `header_to_cffi()` with self-registration as default writer
- `JsonWriter` with `header_to_json()` and `header_to_json_dict()` for full IR serialization
- Public API re-exports for all writer protocol symbols in `headerkit.__init__`
- MkDocs documentation site with Material theme and mkdocstrings autodoc
- 6 API reference pages auto-generated from docstrings
- 6 guide pages: installation, quickstart, architecture, CFFI usage, custom backends, custom writers
- 4 tutorial pages: PXD writer, ctypes writer, JSON export, C header cleanup
- Versioned documentation via mike with version selector dropdown
- GitHub Pages deployment workflow triggered on tagged releases
- `docs` optional dependency group in pyproject.toml

## [0.2.0] - 2026-02-27

### Added

- Windows platform support: LLVM version detection via registry and Program Files scan
- Windows system header detection (`_get_windows_system_headers()`)
- Windows DLL search paths for libclang loading
- Python 3.14 support
- Weekly `check-python.yml` workflow for Python pre-release compatibility
- Full Windows CI in test matrix (ubuntu, macos, windows x Python 3.10-3.14)

### Fixed

- Three test failures on Windows CI (path separators, platform-specific mocks)

## [0.1.0] - 2026-02-26

### Added

- IR data model: `Header`, `Function`, `Struct`, `Enum`, `Typedef`, `Variable`, `Constant`, and type expressions (`CType`, `Pointer`, `Array`, `FunctionPointer`)
- Pluggable backend registry with `ParserBackend` protocol, `register_backend()`, `get_backend()`, `list_backends()`
- Libclang backend extracted from autopxd2 with LLVM 18-21 support
- CFFI cdef writer (`header_to_cffi()`) extracted from pynng
- Vendored clang Python bindings (`cindex.py`) for LLVM 18, 19, 20, 21
- LLVM version auto-detection: env var, llvm-config, pkg-config, clang preprocessor, `/usr/lib/llvm-N/`, Homebrew
- Public API re-exports in `headerkit.__init__`
- CI/CD: GitHub Actions test matrix, lint (ruff + mypy), release workflow with PyPI trusted publishing
- Pre-commit hooks for ruff, mypy, and standard checks
- LLVM license compliance for vendored bindings

[0.7.3]: https://github.com/axiomantic/headerkit/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/axiomantic/headerkit/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/axiomantic/headerkit/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/axiomantic/headerkit/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/axiomantic/headerkit/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/axiomantic/headerkit/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/axiomantic/headerkit/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/axiomantic/headerkit/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/axiomantic/headerkit/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/axiomantic/headerkit/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/axiomantic/headerkit/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/axiomantic/headerkit/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/axiomantic/headerkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/axiomantic/headerkit/releases/tag/v0.1.0
