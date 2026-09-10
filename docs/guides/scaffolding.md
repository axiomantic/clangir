# Project & Extension Scaffolding

HeaderKit can generate both standalone binding files and complete, multi-file packages using the `--layout` option:

- `--layout file` (default): Emits a single binding file.
- `--layout package`: Scaffolds a complete package directory with build manifests, package entrypoints, compiler settings, and verification tests.

---

## What Scaffolding Generates

Generating raw binding code is only half the job of creating a package. When targeting `--layout package`, HeaderKit generates:

1. **Package Manifests**: Idiomatic build files (`.nimble`, `pyproject.toml`, `mojoproject.toml`).
2. **Package Structure**: Separation between low-level generated foreign bindings (`_bindings.*`) and clean user-facing package entrypoints (`__init__.*`).
3. **Compiler & Linker Flags**: Memory management flags (`--mm:orc`, threading options) and dynamic library loading configurations.
4. **Symbol & Interface Tests**: Automated test stubs that verify foreign dynamic libraries can be linked and all exported symbols resolve.

---

## Quick Start (CLI)

### 1. Generating a Single File (Default)

When targeting an output file or stdout, HeaderKit produces a standalone binding module:

```bash
headerkit include/vector.h -w nim -o vector_bindings.nim
```

### 2. Scaffolding a Full Package

To generate a full turnkey package with build configuration and tests, specify `--layout package`:

```bash
headerkit include/vector.h \
  -w nim \
  --layout package \
  --package-name nim_vector \
  -o nim:./nim_vector \
  --no-input
```

This generates the following structure:

```text
nim_vector/
├── nim_vector.nimble          # Nimble package spec with test tasks
├── nim.cfg                    # Compiler and link flags (see 'Nim build configuration')
├── src/
│   ├── nim_vector.nim         # Public API re-export
│   └── nim_vector/
│       └── bindings.nim       # Generated foreign function interface
└── tests/
    ├── test_tripwire.nim      # Symbol resolution (C) or compile-and-link (C++) verification
    └── test_nim_vector.nim    # High-level unit test skeleton
```

### Nim build configuration

`nim.cfg` carries the flags the generated package needs in order to build. Every flag
is invariant, derived from the parsed IR, or supplied by you. Nothing is guessed.

| Flag | Where it comes from |
|------|--------------------|
| `--mm:orc`, `--threads:on`, `--styleCheck:hint` | Invariant. |
| `--path:"$config/src"` | Invariant. Lets the package build with a bare `nim c`, not only under `nimble`. |
| `--backend:cpp` | Emitted when the parsed unit needs the C++ backend. |
| `--passC:"-I\"...\""` | The header's own directory, plus every `-I` passed to the parse. The inner quotes survive Nim's word-splitting of config values, so a path containing a space reaches the compiler whole. They are **double** quotes, escaped: a config file is read by Nim's own lexer, where `'` opens a character literal and a single-quoted path is a syntax error rather than a flag. Paths are emitted with forward slashes, which every compiler in the matrix accepts on Windows and which keeps a `\U` out of a Nim string literal. |
| `--passC:"-D..."` | Every `-D` passed to the parse. |
| `--passL:"-l..."`, `--passL:"-L\"...\""` | The `library` and `library_dirs` writer options. |

#### The C++ backend

Nim's default C backend cannot build `importcpp` bindings: it hands a C++ header to
the C compiler, which rejects `class` outright. HeaderKit decides from the IR, not
from the file extension -- a `.h` declaring a class is C++, and a `.hpp` declaring
only C functions is not. The test is structural rather than a list of known shapes:
any C++ reference, any template-id, any qualified name, any namespace, any scoped
enumeration. It is deliberately broader than the `importc`/`importcpp` choice, since
a record can keep an `importc` pragma while a field of it renders as `CppString`.

One case cannot be decided from the IR's type names at all: libclang reports a
`std::string` field and a C `typedef struct { ... } string;` as the identical
`CType(name="string")`. HeaderKit settles it with the language the *parser* chose for
the translation unit, which both backends record on the IR. A C header naming a record
`vector` therefore stays on the C backend, where its functions link by their unmangled
names.

Not every C++ shape can be *compile-gated*. A shape earns a place in the compile gate
only when the C backend genuinely rejects it; a scoped enumeration, for instance, needs
the C++ backend the moment it is used but builds under either when only its size is
taken, so gating it would be a check that cannot fail. The decision matrix therefore
covers many more shapes than the compile gate does, and that is deliberate. The flag is set in `nim.cfg` rather than in the `.nimble`
test task because a backend selected in the config also overrides a plain `nim c`.

#### Naming the native library

HeaderKit cannot know which library your header's declarations live in, so it does
not guess one. Name it, and the link flags appear:

```bash
headerkit include/counter.hpp \
  -w nim --layout package --package-name counter -o nim:./counter \
  --writer-opt nim:library=counter \
  --writer-opt nim:library_dirs=/opt/counter/lib
```

Both options are repeatable. Without them, `nim.cfg` says in a comment that no
library is linked rather than carrying an `-l` that resolves to the wrong one.

#### What the C++ tripwire establishes

For a C target, `tests/test_tripwire.nim` loads the shared library and resolves each
exported symbol. That check cannot serve a C++ target: an `importcpp` binding has no
unmangled name to look up, and a header-only or statically linked library has no
shared object at all -- such a tripwire fails for a reason unrelated to the bindings.

A unit that binds nothing linkable -- one declaring only templates, which emit no
symbol until instantiated -- gets a tripwire that reports *skipped* and echoes the
reason. **Such a tripwire exits 0**, because Nim's `std/unittest` counts failures and
a skip is not one, so a CI step reading its exit status alone learns nothing about
linkage. The generated file says so in a comment. Do not read it as link
verification; instantiate the generics you use in a test of your own instead. It cannot report success, because there is nothing there whose linkage it
could have established. A private or protected member is not probed either: the
probe would fail to compile rather than fail to link, taking the whole package with
it.

For a C++ target the tripwire's assertion is its own build. It compiles, which proves
the bindings are valid C++ against the real header; it links, which proves every
non-generic entry point resolves against the real library; and it checks `sizeof` of
each bound class, which the C++ compiler can only answer from a complete definition.
The link probes it contains are never executed -- they exist so the compiler must
emit a reference to each entry point. It does not establish that a shared library is
findable at run time, because a statically linked package has none to find.

---

### 3. Interactive Wizard

When executed in a terminal without explicit arguments, HeaderKit launches an interactive questionnaire:

```bash
$ headerkit include/vector.h -w mojo -o ./mojo_vector
Package name [mojo_vector]:
Target language (nim, mojo, ctypes, cffi) [mojo]:
Layout (file, package) [package]:
Test generation (both, tripwire, unit, none) [both]:
```

To bypass prompts in CI or automated scripts, pass `--no-input`.

---

## Test Generation Options

When scaffolding a package, HeaderKit generates test suites tailored to the target language via `--test-type`:

| Value | Description |
|---|---|
| `both` (Default) | Emits both symbol linking tests and unit test skeletons. |
| `tripwire` | Generates symbol verification tests confirming each C export symbol resolves in the dynamic library. |
| `unit` | Generates standard assertion skeletons for verifying high-level functions. |
| `none` | Omits the `tests/` directory entirely. |

### Tripwire Verification in Python (`pytest-tripwire`)
```python
import pytest
from mypkg import _bindings

@pytest.mark.tripwire
def test_tripwire_exported_symbols():
    """Tripwire verification: asserts foreign C symbols are present in runtime bindings."""
    assert hasattr(_bindings, "vector_add"), "Missing export entrypoint vector_add"
    assert hasattr(_bindings, "vector_norm"), "Missing export entrypoint vector_norm"
```

### Tripwire Verification in Nim
```nim
import std/unittest
import mypkg

suite "Tripwire Symbol & ABI Verification":
  test "verify foreign library entrypoints exist and link":
    echo "Verifying tripwire symbol: vector_add"
    echo "Verifying tripwire symbol: vector_norm"
    checkpoint "Tripwire symbol link verification active"
```

---

## Bring-Your-Own-Scaffolder (BYOScaffolder)

HeaderKit core is 100% zero-dependency, shipping with `StdlibScaffolder` built purely on Python's standard library (`string.Template` and `pathlib`).

For corporate environments or advanced repositories needing external template engines (like [Copier](https://copier.readthedocs.io/) or [Cookiecutter](https://cookiecutter.readthedocs.io/)), HeaderKit provides the pluggable `BYOScaffolder` protocol integrated with the unified hook engine.

### Example: Copier BYOScaffolder Plugin

```python
from pathlib import Path
import tempfile
import copier
from headerkit.hooks import Priority, hook
from headerkit.ir import Header
from headerkit.scaffold import BYOScaffolder, OutputFile, ProjectLayout, ScaffoldOptions, scaffold
from headerkit.writers import get_writer

class CopierScaffolder(BYOScaffolder):
    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir

    def scaffold(self, unit: Header, options: ScaffoldOptions) -> ProjectLayout:
        writer = get_writer(options.target_language)
        bindings = writer.write(unit)

        with tempfile.TemporaryDirectory() as tmp_dir:
            copier.run_copy(
                str(self.template_dir),
                tmp_dir,
                data={
                    "package_name": options.package_name,
                    "target_language": options.target_language,
                    "bindings_code": bindings,
                },
                defaults=True,
            )
            files = [
                OutputFile(path=str(p.relative_to(tmp_dir)), content=p.read_text(encoding="utf-8"))
                for p in Path(tmp_dir).rglob("*") if p.is_file()
            ]
            return ProjectLayout(files=files)

# Register via HeaderKit hook engine
copier_plugin = CopierScaffolder(Path("./templates/custom_template"))

@hook("scaffold_project", priority=Priority.OVERRIDE)
def custom_scaffold_hook(unit: Header, options: ScaffoldOptions, **_kwargs) -> ProjectLayout:
    return copier_plugin.scaffold(unit, options)
```

See the executable example in `examples/scaffolding/copier_scaffolder.py`.

## Test work orders

Scaffolded projects also receive tiered tests: real passing tests where the IR
determines the answer, and deliberately failing stubs naming the work that needs
human or LLM judgment. See [Test work orders](work-orders.md).
