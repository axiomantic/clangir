# Agents

Instructions for AI coding agents working on this project.

## Changelog

Keep `CHANGELOG.md` up to date using [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Every user-facing change should have a changelog entry.

## Versioning

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Bump the version in `pyproject.toml` whenever creating a branch:

- **Major** (X.0.0): Breaking changes to public API
- **Minor** (0.X.0): New features, new public API surface
- **Patch** (0.0.X): Bug fixes, documentation-only changes, internal refactors

## Vendored clang bindings

`headerkit/_clang/` contains vendored upstream clang Python bindings for LLVM 18-23. These are excluded from ruff and mypy. Do not modify, refactor, or lint them.

## Registry pattern & unified hooks

Backends and writers use the unified hook engine (`headerkit.hooks`) as their underlying registry, preserving the managed circular import pattern for self-registration. Both `headerkit/backends/__init__.py` and `headerkit/writers/__init__.py` lazily import their concrete modules via `_ensure_*_loaded()`, and each concrete module self-registers via hooks and `register_backend`/`register_writer` at the bottom of the file. Do not restructure these imports.

When adding a new backend or writer, follow the pattern: define the class, register hooks on `parse_unit`/`get_backend` or `write_output`/`get_writer`, and call `register_*()` at the bottom of the module file.

## Strict prohibition against regex-based AST extraction (CANNOT REGEX FOR CONTEXT-FREE GRAMMARS)

**NEVER use regular expressions to parse, tokenize, or extract Abstract Syntax Tree (AST) structures from source code in context-free or structured programming languages (C, C++, Rust, Zig, Nim, etc.).**

Context-free grammars cannot be parsed by regular languages. Attempting to extract function declarations, type definitions, structs, interfaces, or language scopes with regex is fundamentally flawed and strictly forbidden:
- It fails on nested braces, generic type parameters, closures, macro invocations, block comments, multiline attributes, and string literals.
- It produces fragile green mirages—tests that pass on trivial happy-path snippets while failing on any non-trivial real-world syntax.

All parser backends and AST extractors in HeaderKit **MUST** use formal parser grammars or compiler toolchains:
- **Tree-sitter grammars** (e.g. `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-rust`, `tree-sitter-zig`) traversing concrete syntax trees.
- **Compiler frontend ASTs** (e.g. LLVM `libclang`).
- **Formal compiler / AST bindings** provided by the language ecosystem.

Any code introducing regex-based AST extraction, source scanning, or signature scraping will be rejected immediately.

## Anti-completion bias & anti-green-mirage discipline

Completion bias is the failure mode where an agent rushes to check off roadmap items or satisfy the test runner by introducing superficial happy-path implementations, hollow file skeletons, or vacuous assertions that cannot fail. All code and tests must uphold the following non-negotiable invariants:

### 1. Zero tautological or vacuous assertions in generated code
Generated test suites (tripwires, unit tests) emitted by scaffolders, writers, or templates **MUST NEVER** emit tautologies:
- **Strictly prohibited**: `assert True`, `assert_true(True)`, `check true`, `assert True == True`.
- **Strictly prohibited**: `echo "Verifying symbol..."` or `print(...)` without assertions or error handling.
- **Strictly prohibited**: Superficial module existence checks (e.g. `assert mod is not None` on an imported module object) as the sole assertion.
- **Tripwire invariant**: A tripwire's purpose is to fail immediately if the native dynamic library binary is missing or if foreign C ABI entry points fail to link/resolve. A tripwire that passes when the native library is absent is a green mirage and is strictly forbidden.
- **Unit test invariant**: Generated unit test stubs must exercise the generated API: construct wrapper types, invoke wrapper functions, or assert that function signatures match expected parameters and return types.

**One narrow carve-out applies to both this section and section 2**, for the
deliberately failing stubs emitted by `headerkit/workorder.py`. It is stated in
full after section 2; read it before "fixing" a generated stub.

### 2. No hollow scaffolding (complete interface artifacts)
Scaffolding engines and layout writers must never emit placeholder or hollow files:
- **Headers must declare interfaces**: Never emit empty headers with comments like `// See implementation for details`. Header files (`.h`, `.hpp`, `.pxd`) must contain full function prototypes, struct definitions, enum definitions, and opaque handles.
- **Test harnesses must test**: Test harnesses (such as C test runners or CMake test targets) must include the generated header, link against the generated shared library, and execute at least one entry point. Never emit dummy stubs like `int main(void) { return 0; }`.
- **Packaging manifests must configure builds**: Manifests (`CMakeLists.txt`, `pyproject.toml`, `*.nimble`, `*.rockspec`) must specify real compiler flags, include directories, and link libraries necessary to build the target.

### Carve-out: work-order stubs (sections 1 and 2)

**The exemption is attached to the failing construct, not to a file path.** Sections 1
and 2 do not apply to a stub generated by `headerkit/workorder.py` **for as long as it
fails**. A stub edited to pass is an ordinary test from that moment and is fully subject
to sections 1 and 2. Scoping the exemption to `tests/test_workorder.py` and
`tests/test_workorder.nim` as *files* would license replacing `pytest.fail(...)` with
`assert True` -- exactly the artifact section 1 forbids, in a path section 1 no longer
reached.

A generated stub calls `pytest.fail(...)` in Python, and `checkpoint(...)` followed by
`fail()` in Nim, and those are sanctioned.

**The distinguishing property is that they FAIL LOUDLY AND SAY WHY.** What section 2
forbids is scaffolding that *looks finished and is not* -- a hollow file that reports
success while testing nothing. A work-order stub is the exact opposite: it is red from
the moment it is generated, it names the specific work outstanding in its failure
message, and it carries the signature under test plus a definition of done. It cannot
produce a green mirage, because it cannot be green until a human or an LLM writes the
assertion it asks for. Red-to-green is the intended progress meter.

The carve-out does not license:
- a stub that passes, or that asserts a tautology, in place of failing;
- a stub emitted for behaviour Tier 1 already tests completely (Tier 1 is defined in
  [`docs/guides/work-orders.md`](docs/guides/work-orders.md));
- deleting a stub, or making it pass with a vacuous assertion, to get a green run.

The Tier 1 tests emitted alongside them are ordinary generated tests and remain fully
subject to sections 1 and 2: they must assert real values derived from the IR.

**A future agent reading section 2 literally will be tempted to "fix" these stubs by
deleting them or making them pass. Do not. That destroys the work order.**

### 3. Consumption validates (testing the generators)
Tests that verify code generators, writers, and scaffolders must follow the "Consumption Validates" rule:
- **No path-presence-only testing**: Asserting `assert "filename" in paths` or `assert len(layout.files) > 0` is strictly insufficient. Tests must inspect the contents of every generated file to verify that declarations, symbols, types, and compiler flags are present.
- **Negative controls**: Tests must verify that invalid inputs, missing options, or unsupported layouts fail with descriptive exceptions rather than silently succeeding.
- **Compilation verification where available**: Whenever the target toolchain is installed in the local environment (Python, Clang/GCC, Cython, Nim), integration tests must compile or execute the generated output to prove validity.

### 4. Language grammar semantics (no mutually exclusive branch merging)
Parsers and AST extractors must respect language grammar semantics:
- **Preprocessor mutual exclusion**: Never blindly walk all branches of conditional preprocessor directives (`#if`, `#ifdef`, `#elif`, `#else`). Extracting declarations from mutually exclusive branches creates conflicting or duplicate IR representations.
- **Scope integrity**: Declarations inside classes, namespaces, or local scopes must reflect their enclosing scope in the IR; never flatten inner symbols into the global namespace without proper qualification.

### 5. Zero dead prototype residue
Before declaring any task complete:
- Clean up all prototype imports (`import re`, unused variables, debug prints, commented-out experiments).
- Ensure linters and typecheckers run without unreferenced imports.

## Architectural coherence & anti-islanding (Phase 0 scope gate)

Before writing code or tests for any task, perform an explicit Phase 0 scope audit:

- **Zero-Dual-System Rule**: When introducing a new engine, pipeline, or abstraction, existing built-in components must immediately adopt it in the foundational PR. Never build a new subsystem as an isolated island alongside the legacy mechanism it was designed to replace.
- **Foundational Noun Priority**: When core IR containers, domain models, or input classifications evolve (e.g. `Header` $\rightarrow$ `SourceUnit`, `InputSpec`), establish the new nouns in the base PR so new features are never built on deprecated models.
- **Consumer Trace**: Verify that standard callers (`get_backend()`, `get_writer()`, CLI) transparently flow through the new architecture rather than requiring special-case entry points.

## Public API

`headerkit/__init__.py` defines `__all__` with the full public surface. Backends and writers are accessed via registry functions (`get_backend()`, `get_writer()`), not by importing concrete classes directly. When adding new public symbols, add them to both the imports and `__all__` in `__init__.py`.

## Quality gates

All code must pass before committing:

- `ruff check .` and `ruff format --check .`
- `mypy --strict` on `headerkit/`
- `pytest` across Python 3.10-3.14 on Linux, macOS, and Windows

Pre-commit hooks enforce these automatically.

## Testing

Registry tests (`test_backends/test_registry.py`, `test_writers/test_registry.py`) must save and restore global registry state via fixtures. Each test should see a clean registry to prevent test pollution.

Tests requiring a system libclang installation use the `@pytest.mark.libclang` marker.

## Multi-line string literals

Use `textwrap.dedent` with a triple-quoted string instead of concatenating fragments with `+` and `"\n"`. Use an `f`-string prefix for interpolation. The backslash after the opening `"""` suppresses the leading newline.

```python
# Preferred
import textwrap

assert output == textwrap.dedent(f"""\
    {_PREAMBLE}
    # ========================
    # Typedefs
    # ========================

    callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)
""")

# Avoid
assert output == (
    _PREAMBLE
    + "\n"
    + "# ========================\n"
    + "# Typedefs\n"
    + "# ========================\n"
    + "\n"
    + "callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)\n"
)
```

This applies anywhere a multi-line string literal appears: assertions, expected-value variables, template strings, error messages.

## Runtime dependencies

The project has one conditional runtime dependency: `tomli` for Python 3.10 (before `tomllib` was added to stdlib). All other functionality is zero-dependency. If a feature needs an external package, make it an optional dependency with graceful degradation when absent.
