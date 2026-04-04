# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.16.1] - 2026-04-04

### Fixed

- `define_patterns` now resolves `#include` directives when `code=` is provided, scanning included files for matching macros (umbrella header pattern)

## [0.16.0] - 2026-04-04

### Added

- Environment variable expansion in config: `${VAR}` syntax in string values, with error on unset variables
- `define_patterns` CFFI writer option: regex-based macro extraction from raw header text, emitting `#define NAME ...` for CFFI compile-time resolution
- `extra_cdef` CFFI writer option: append literal cdef lines (e.g., `extern "Python"` callbacks) to generated output
- CFFI Build Integration documentation guide

## [0.15.1] - 2026-04-03

### Removed

- `action.yml` composite GitHub Action (use standard workflow with `peter-evans/create-pull-request` instead; see CI Store Population guide)

### Changed

- Renamed "GitHub Action" docs guide to "CI Store Population" with workflow examples using standard GitHub Actions building blocks

## [0.15.0] - 2026-04-03

### Added

- Glob-based header selection: CLI positional args accept quoted glob patterns (e.g., `headerkit 'include/**/*.h'`)
- `[[tool.headerkit.headers]]` array-of-tables config for header selection with per-pattern overrides
- `--exclude` CLI flag for glob-based header exclusion
- `[tool.headerkit.output]` config section for per-writer output path templates
- Output path template variables: `{stem}`, `{name}`, `{dir}`
- `-o WRITER:TEMPLATE` CLI flag for per-writer output path templates
- `batch_generate()` public API for multi-header generation with fail-fast semantics
- `BatchResult` dataclass for batch generation results
- `resolve_headers()`, `resolve_output_path()`, `check_output_collisions()` public API functions
- `default_output_pattern` class attribute on all built-in writers
- GitHub Action (`action.yml`) for CI cache population

### Changed

- **Breaking:** Store directory renamed from `.hkcache/` to `.headerkit/`
- **Breaking:** IR schema version bumped from "2" to "3" (all existing cache entries invalidated)
- **Breaking:** `cache_dir` parameter renamed to `store_dir` in `generate()`, `generate_all()`, and `HeaderkitConfig`
- **Breaking:** `--cache-dir` CLI flag renamed to `--store-dir`
- **Breaking:** Config key `[tool.headerkit.cache].cache_dir` moved to `[tool.headerkit].store_dir`
- **Breaking:** `-w WRITER[:PATH]` syntax removed; use `-w WRITER` and `-o WRITER:TEMPLATE`
- **Breaking:** CLI positional args accept globs; quote patterns to prevent shell expansion
- **Breaking:** `WriterSpec.output_path` renamed to `WriterSpec.output_template`
- `find_cache_dir()` simplified to single-pass project-root-only lookup (no walk-up for existing directory)

## [0.14.0] - 2026-04-02

### Added

- `detect_process_triple()` replaces `detect_host_triple()` with process-aware detection using `HOST_GNU_TYPE` (POSIX) or `sysconfig.get_platform()` (Windows)
- musl libc detection: correctly produces `linux-musl` triples on Alpine and other musl-based systems (via `os.confstr` sniff for pre-3.13 Python where `HOST_GNU_TYPE` may report `gnu` on musl)

### Changed

- **Breaking:** `detect_host_triple()` removed; use `detect_process_triple()`
- Simplified target detection: one signal per platform instead of 5-step fallback chain. `HOST_GNU_TYPE` on POSIX, `get_platform()` on Windows. For cross-compilation, set `--target` explicitly.

## [0.13.0] - 2026-04-01

### Added

- Target triple support for cross-compilation: `generate(target="aarch64-apple-darwin")`
- `detect_host_triple()` and `resolve_target()` public API functions
- `--target` CLI flag for specifying target triple
- `HEADERKIT_TARGET` environment variable for target triple configuration
- `[tool.headerkit] target` config key in pyproject.toml
- Target triple included in cache directory slugs for readability
- `normalize_triple()` inserts `unknown` vendor for 3-component triples (e.g., `x86_64-linux-gnu` -> `x86_64-unknown-linux-gnu`)

### Changed

- **Breaking:** Cache keys now use LLVM target triple instead of `sys.platform` + `platform.machine()` + Python version. Existing `.hkcache/` entries will be regenerated on first use (IR schema version bumped to 2).
- **Breaking:** `compute_ir_cache_key()` requires `target` parameter instead of reading platform/arch at runtime
- **Breaking:** `PopulateTarget` uses `target_triple` field instead of `sys_platform`, `machine`, `py_impl`
- **Breaking:** `PLATFORM_MAP` values are target triple strings instead of `(platform, machine)` tuples
- **Breaking:** Removed `py_impl_for_version()` from `_populate` module
- Python version removed from IR cache key (IR represents parsed C declarations, not Python-specific output)
- `-target` flag automatically passed to libclang for correct cross-platform parsing
- Bump `bigfoot` test dependency minimum to 0.19

### Fixed

- Add positional-only parameter markers to vendored clang binding stubs (v18-v21) to fix stubtest failures

## [0.12.5] - 2026-03-29

### Fixed

- Document `LibclangUnavailableError` in backends reference page
- Update `is_backend_available()` description in architecture guide to reflect real load test behavior
- Update build backend guide to reference `LibclangUnavailableError` instead of generic error

## [0.12.4] - 2026-03-29

### Added

- `LibclangUnavailableError` exception for clear error reporting when libclang cannot be found after all recovery attempts (auto-install, cache fallback)
- Deploy dev docs on every push to main (docs fixes go live immediately)
- CI warning when a PR is missing a version bump or changelog entry

### Fixed

- Auto-install now triggers correctly when libclang library is not found (was broken in 0.12.3 due to exception type mismatch between `RuntimeError` from `parse()` and the `ValueError` catch in `generate()`)
- Fix incorrect CLI command in install-libclang reference (`headerkit-install-libclang` -> `headerkit install-libclang`)
- Fix wrong default writer comment in generate reference (default is cffi, not json)
- Fix references to non-existent `Writer` base class in cache guide examples
- Fix broken relative doc links in README (use full docs site URLs)
- Fix LLVM version example in installation guide (17 -> 18, matching supported range)
- Update `site_description` in mkdocs.yml to mention all writers

### Changed

- `generate()` uses explicit `is_backend_available()` check instead of exception catching to detect missing libclang and trigger the output-cache fallback / auto-install flow
- `is_backend_available("libclang")` now performs a real library load test via `is_system_libclang_available()` instead of only checking whether the backend class is registered
- CI and install-libclang workflows now skip on docs-only changes via `paths-ignore`

## [0.12.3] - 2026-03-29

### Changed

- Removed `reload_backends()` from the public API. The libclang backend class is now always registered, and `_configure_libclang()` re-searches on every call (short-circuiting only when the library is already loaded). After `auto_install()` puts libclang on disk, the next `get_backend().parse()` call naturally finds it without any manual reload step.

### Fixed

- Linux: libclang search now includes versioned .so names (`libclang.so.18`, `libclang-18.so`) in RHEL/Fedora `/usr/lib64` and generic `/usr/lib` paths, not just the unversioned `libclang.so` from clang-devel
- Cross-platform: libclang search now checks the `clang/native/` directory from the PyPI `libclang` package (`pip install libclang`) as a fallback on all platforms
- Windows: `_configure_libclang()` now calls `os.add_dll_directory()` for the candidate DLL's directory before loading, so dependent DLLs (e.g. zlib, ncurses) can be found

## [0.12.2] - 2026-03-29

### Fixed

- Corrected changelog: added missing release sections for v0.10.1, v0.11.0, and v0.12.0 that were previously lumped into [Unreleased]

## [0.12.1] - 2026-03-28

### Fixed

- Linux: `install_linux()` now tries the lighter `clang-libs` package before falling back to `clang-devel` on dnf-based distros (RHEL/AlmaLinux/manylinux_2_28)
- Windows x64: `_install_windows_x64()` now detects pre-installed LLVM at the default location before attempting Chocolatey, and configures PATH/`os.add_dll_directory()` so ctypes can find libclang.dll
- `auto_install()` now falls back to `pip install libclang` when platform-specific installation fails or the library is not loadable after install
- Backend registry caching bug: after `auto_install()` installs libclang at runtime, `get_backend("libclang")` now correctly discovers the newly available backend instead of returning the stale "no backends available" result
- `_find_project_root()` regression from cache populate PR: restored use of `absolute()` instead of `resolve()` to prevent Windows 8.3 short-name expansion
- Wired `load_populate_config()` defaults into `populate()` and CLI for platforms, python_versions, and timeout

## [0.12.0] - 2026-03-28

### Added

- `headerkit cache populate` CLI subcommand for generating cache entries across multiple target platforms using Docker containers
- `populate()` Python API with `PopulateResult` and `PopulateTarget` data types
- cibuildwheel config parsing (`--cibuildwheel`) for automatic target detection
- Per-platform Docker image configuration via `[tool.headerkit.cache.populate.images]`
- Dry-run mode (`--dry-run`) for previewing planned cache population targets

### Fixed

- `parse_cibuildwheel_config()` no longer emits spurious macOS/Windows warnings when those platforms are not in the build matrix (e.g., `build = "cp312-manylinux*"`)

## [0.11.0] - 2026-03-28

### Added

- Opt-in auto-install of libclang when `generate()` needs to parse but the backend is unavailable, with layered configuration (highest precedence first):
  1. `generate(auto_install_libclang=True)` kwarg
  2. `HEADERKIT_AUTO_INSTALL_LIBCLANG=1` environment variable
  3. `auto_install_libclang = true` in `[tool.headerkit]` of pyproject.toml
  4. Default: disabled (opt-in)
- `auto_install()` function in `install_libclang` module for quiet, non-interactive libclang installation
- `headerkit.build_backend_auto` PEP 517 build backend that wraps `headerkit.build_backend` with auto-install enabled (sets `HEADERKIT_AUTO_INSTALL_LIBCLANG=1`)

### Changed

- Auto-install is now opt-in (default disabled) instead of opt-out. Projects that relied on the previous default-enabled behavior should set `HEADERKIT_AUTO_INSTALL_LIBCLANG=1` or use `headerkit.build_backend_auto` as their build backend.
- Replaced `HEADERKIT_NO_AUTO_INSTALL` env var with `HEADERKIT_AUTO_INSTALL_LIBCLANG` (set to `1` to enable)
- CI test matrix reduced to full Python range on Ubuntu only, with latest Python on macOS and Windows

### Fixed

- `_find_project_root()` no longer uses `Path.resolve()`, which on Windows can expand 8.3 short names and cause the `.git` marker walk to escape the intended project boundary, potentially triggering unwanted auto-install

## [0.10.1] - 2026-03-28

### Fixed

- `generate()` now falls back to the output cache when the backend (libclang) is unavailable, enabling the documented libclang-free build workflow

## [0.10.0] - 2026-03-28

### Added

- Two-layer content-addressable cache store (`.hkcache/` directory)
- `generate()` and `generate_all()` public API for cache-aware header generation
- `json_to_header()` JSON IR deserializer (inverse of `header_to_json()`)
- `GenerateResult` dataclass for multi-writer generation results
- PEP 517 build backend (`headerkit.build_backend`) for consumer projects
- CLI flags: `--no-cache`, `--no-ir-cache`, `--no-output-cache`, `--cache-dir`
- Environment variables: `HEADERKIT_NO_CACHE`, `HEADERKIT_NO_IR_CACHE`, `HEADERKIT_NO_OUTPUT_CACHE`
- Cache subcommands: `headerkit cache status`, `headerkit cache clear`, `headerkit cache rebuild-index`
- `[tool.headerkit.cache]` configuration section in pyproject.toml
- Writer `cache_output` attribute for opt-out of output caching (diff, prompt writers)

### Changed

- CLI `main()` now delegates to `generate()` for cache-integrated pipeline

## [0.8.4] - 2026-03-11

### Changed

- Deduplicated README and removed premature autopxd2 mention

## [0.8.3] - 2026-03-10

### Changed

- Updated README: fixed project description, added Mermaid architecture diagram, listed supported output formats and plugin system
- Added CHANGELOG checklist item to PR template
- Bumped `actions/checkout` to v6, `actions/cache` to v5, `peter-evans/create-pull-request` to v8

### Fixed

- Test assertion for Homebrew detection was unconditional but the code only probes `brew` on macOS

## [0.8.2] - 2026-03-05

### Changed

- Updated bigfoot dependency to >=0.4.1 and adopted `with bigfoot:` context manager syntax
- Test suite now uses [bigfoot](https://github.com/axiomantic/bigfoot) for subprocess interception. `subprocess.run` and `shutil.which` mocks in `test_install_libclang.py`, `test_version_detect.py`, `test_libclang.py`, and `test_windows_detection.py` are replaced with `bigfoot.subprocess_mock`, which enforces strict FIFO ordering and fails fast on unexpected calls.
- Integration test writer assertions extracted into shared helpers (`_check_ctypes_write`, `_check_cython_write`, etc.) in `test_real_headers.py`, eliminating repeated assertion logic across the five library test classes.

## [0.8.1] - 2026-03-04

### Removed

- `headerkit-install-libclang` standalone console script. Use `headerkit install-libclang` instead.

## [0.8.0] - 2026-03-03

### Fixed

- Prompt writer incorrectly classified `typedef void (*fn)(int);` as a plain typedef in compact and standard modes when libclang represents the underlying type as `Pointer(FunctionPointer(...))`. Compact mode now emits `CALLBACK fn(...) -> void` and standard mode places it in the `callbacks:` section.
- Prompt writer cross-reference map built keys with `struct`/`union`/`enum` prefixes (e.g. `struct Config`) while declaration dicts use bare names (`Config`), so `used_in` was never populated. Keys are now normalized to bare names.
- Tautological writer tests: all 4 writer test files asserted `writer.write(h) == writer_function(h)`, which is always true since write() delegates to the function; replaced with specific output content assertions
- Tautological protocol checks: `isinstance(writer, WriterBackend)` only checks attribute names exist on runtime-checkable Protocol; replaced with behavioral verification in all writer test files
- Integration writer tests used `len(output) > 0` as sole assertion; replaced with known-symbol checks in all 10 cffi/json writer integration tests
- `test_version_detect.py` patched `shutil.which` in the wrong namespace (global instead of `headerkit._clang._version`), making the mock ineffective; test passed by coincidence
- `test_ensure_backends_loaded_handles_import_error` had a broken patch target and asserted a flag that was set unconditionally before the import; fixed with `sys.modules` sentinel and registry-empty assertion
- `test_verify_libclang_success/failure` had a dead `@patch` decorator with `create=True` on a nonexistent attribute and never verified the target function was called; removed dead patch, added `assert_called_once()`
- `test_dict_is_json_serializable` asserted `json.loads(json.dumps(x)) == x`, which is always true for JSON-native dicts; replaced with structural verification
- `test_loader.py` used `hasattr` as sole assertion for module attributes; replaced with `inspect.isclass` checks
- `test_pypy_compat.py` `test_value_property` was an exact duplicate of `test_init_with_string`; deleted
- Macro tests used permissive `or` (accepting int or str) and conditional `if` guards that passed silently when features were absent; resolved type ambiguity and pinned behavior assertions
- `test_negative_integer_macro` comment stated `value is None` but never asserted it; added the assertion
- `test_install_linux_apt` truncated command assertions to first 2-3 tokens, missing package names; now asserts full commands
- `test_install_windows_arm64` had self-referential path assertion comparing `call_args` to itself; replaced with computed expected path
- `test_ir.py` `test_pointer_with_qualifiers` docstring incorrectly described output as `int * const` when actual output is `const int*`; fixed docstring
- `test_ir.py` used substring checks (`"packed" in str(s)`, `"stdcall" in str(f)`) instead of exact equality; replaced with exact string assertions
- `test_public_api.py` `test_type_aliases_are_unions` checked union membership but not completeness; replaced with exact set equality assertions
- `test_diff.py` `test_format_description_property` used disjunctive assertion unlike every other writer test; replaced with exact string match
- `test_ctypes.py` `test_type_map_completeness` used magic number `len(MAP) == 28`; replaced with full key set assertion
- `test_ctypes.py` `test_section_headers_present` checked ordering of 4 of 5 sections, missing "Typedefs"; added
- `test_cython.py` `test_basic_cppclass` accepted both spaces and tabs via `or`; pinned to 4-space indentation matching source
- Integration `conftest.py` caught bare `except Exception` in all 6 download fixtures; narrowed to `urllib.error.URLError`, `socket.timeout`, `OSError`
- Integration `_parse_header` used `pytest.skip` for parse failures on known-good headers; changed to `pytest.fail`
- `test_roundtrip.py` conditional assertions (`if version_constants:`, `if switch_td:`) replaced with pinned behavior assertions
- Five duplicate `skip_if_no_libclang` autouse fixtures across test_libclang.py classes consolidated into one module-level fixture
- Redundant `CIR_CLANG_VERSION` env cleanup across 21 test methods replaced with module-level autouse fixture in test_version_detect.py
- Repeated `mock_winreg` constant setup across 6 test methods extracted into shared fixture in test_windows_detection.py

### Added

- `headerkit` CLI command: parse C headers and emit output via configurable writers (`headerkit input.h`, `headerkit -w cffi:out.h -w json:out.json input.h`)
- `headerkit install-libclang` subcommand: installs libclang system packages (delegates to `headerkit-install-libclang`)
- `--backend` flag to select parser backend (default: `libclang`)
- `-I` / `--include-dir`, `-D` / `--define`, `--backend-arg` flags for backend configuration
- `-w WRITER[:OUTPUT]` flag for writer selection and output routing; multiple writers supported; omitting output path sends to stdout
- `--writer-opt WRITER:KEY=VALUE` flag for per-writer constructor options; multiple flags accumulate list values
- `--config PATH` and `--no-config` flags for config file control
- Config file support: `.headerkit.toml` (preferred) and `[tool.headerkit]` section in `pyproject.toml`, discovered by walking up from the current directory
- Entry-point plugin discovery: install third-party backends/writers and register them under `headerkit.backends` or `headerkit.writers` entry-point groups
- `plugins` config key for explicit plugin module imports
- Multi-input file support via synthetic umbrella header with automatic prefix filtering
- `toml` optional dependency group (`pip install headerkit[toml]`) for TOML config support on Python 3.10
- Integration roundtrip tests for ctypes, Cython, Lua, prompt, and diff writers: full `libclang → IR → writer output` pipeline coverage for each writer, exercising structs, enums, functions, typedefs, constants, anonymous types, and empty headers.
- Integration smoke tests for all seven writers against real-world library headers (sqlite3, zlib, lua, curl, SDL2) in `test_real_headers.py`.
- `test_unknown_declaration_kind` for JSON writer's `"unknown"` fallback path (previously untested code path)
- `test_identical_functions/structs/enums_produce_no_diff` verifying unchanged declarations produce zero diff entries
- `test_field_added_in_middle_is_breaking` for struct diff edge case (middle insertion vs end append)
- `test_is_umbrella_header_system_headers_excluded` verifying system header filtering in umbrella detection
- `test_mixed_declarations` split into per-verbosity tests for better failure diagnosis in prompt writer

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

[Unreleased]: https://github.com/axiomantic/headerkit/compare/v0.16.0...HEAD
[0.16.0]: https://github.com/axiomantic/headerkit/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/axiomantic/headerkit/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/axiomantic/headerkit/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/axiomantic/headerkit/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/axiomantic/headerkit/compare/v0.12.4...v0.13.0
[0.12.4]: https://github.com/axiomantic/headerkit/compare/v0.12.3...v0.12.4
[0.12.3]: https://github.com/axiomantic/headerkit/compare/v0.12.2...v0.12.3
[0.12.2]: https://github.com/axiomantic/headerkit/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/axiomantic/headerkit/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/axiomantic/headerkit/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/axiomantic/headerkit/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/axiomantic/headerkit/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/axiomantic/headerkit/compare/v0.8.4...v0.10.0
[0.8.4]: https://github.com/axiomantic/headerkit/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/axiomantic/headerkit/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/axiomantic/headerkit/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/axiomantic/headerkit/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/axiomantic/headerkit/compare/v0.7.3...v0.8.0
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
