# Target Triples in headerkit

## What is a target triple?

A target triple is a string that identifies the compilation target: the combination
of processor architecture, vendor, operating system, and optionally the runtime
environment. The format is:

```
<arch>-<vendor>-<os>[-<env>]
```

Examples:

| Triple | Meaning |
|--------|---------|
| `x86_64-pc-linux-gnu` | 64-bit x86, PC vendor, Linux, GNU libc |
| `aarch64-apple-darwin25.3.0` | ARM64, Apple, macOS 25.3 |
| `x86_64-pc-windows-msvc` | 64-bit x86, PC, Windows, MSVC ABI |
| `i686-pc-windows-msvc` | 32-bit x86, PC, Windows, MSVC ABI |
| `aarch64-unknown-linux-gnu` | ARM64, unknown vendor, Linux, GNU libc |
| `armv7-unknown-linux-gnueabihf` | ARMv7, Linux, GNU hard-float ABI |
| `x86_64-unknown-linux-musl` | 64-bit x86, Linux, musl libc |

The name "triple" is historical - it originally had exactly three parts. Modern
triples can have 3 to 5 components. There is no formal standard or standards
body that assigns these names. LLVM is the de facto authority.

## Why headerkit uses target triples

headerkit parses C/C++ headers and generates Python bindings. The parsed IR
(intermediate representation) is the result of C preprocessing, which is
target-sensitive:

- `#ifdef _WIN64` expands differently for 32-bit vs 64-bit Windows targets
- `sizeof(void*)` is 4 on 32-bit, 8 on 64-bit
- `sizeof(long)` is 4 on Windows, 8 on Linux 64-bit
- System headers differ between OS versions (new functions, changed structs)
- ABI matters: `gnu` vs `musl` can affect struct layouts

The cache key for parsed IR must capture all of these distinctions. A target
triple encodes exactly this information in a single canonical string.

### What the cache key represents

The cache key answers: "if I parse this header for this target, will I get the
same IR?" Two cache entries should match if and only if their preprocessing
output would be identical. The target triple is the right granularity for this
because it's what the C preprocessor uses to make its decisions.

## Detecting the target triple

### The problem with host detection

The naive approach is to ask the system what platform it is:

```python
import sys, platform
sys.platform      # "darwin", "linux", "win32"
platform.machine()  # "x86_64", "arm64", "AMD64"
```

This gives the **host** platform, not the **target**. They differ when:

- 32-bit Python runs on a 64-bit OS (common on Windows via cibuildwheel)
- Cross-compiling for a different architecture entirely
- Running in an emulated environment (QEMU, Rosetta 2)

Common approaches like `platform.machine()`, `cc -dumpmachine`, and
`struct.calcsize("P")` each have limitations: they report the host machine,
the compiler's default target, or the pointer width respectively -- none
directly give the process's build target. headerkit sidesteps all of these
by using `HOST_GNU_TYPE`, which is the actual triple from autoconf baked
into the Python build at compile time.

## headerkit's detection algorithm

headerkit resolves the target triple via config precedence:

1. **Explicit `target` kwarg** to `generate()` (highest priority)
2. **`HEADERKIT_TARGET` environment variable**
3. **`[tool.headerkit] target`** in pyproject.toml
4. **Auto-detection** via `detect_process_triple()`

The auto-detection uses one signal per platform:

- **POSIX** (Linux, macOS, BSDs): `sysconfig.get_config_var('HOST_GNU_TYPE')`
  -- the `--host` value from autoconf, baked into the Python build at compile
  time. This is inherently process-aware: a 32-bit Python build has a 32-bit
  `HOST_GNU_TYPE`, so no pointer-width correction is needed.
- **Windows**: `sysconfig.get_platform()` -- returns `win-amd64`, `win32`, or
  `win-arm64`. Mapped to LLVM-style triples (e.g., `x86_64-pc-windows-msvc`).

For cross-compilation, set `--target`, `HEADERKIT_TARGET`, or
`[tool.headerkit] target` explicitly rather than relying on auto-detection.

### Why HOST_GNU_TYPE?

`HOST_GNU_TYPE` is the most direct signal for "what target was this Python
built for." Unlike `sysconfig.get_platform()` (which returns a lossy platform
tag like `linux-x86_64` with no libc flavor) or `cc -dumpmachine` (which
reports the compiler's default target, not the process), `HOST_GNU_TYPE` is
the actual triple from autoconf and includes vendor, OS, and libc flavor
(e.g., `x86_64-pc-linux-gnu` vs `x86_64-pc-linux-musl` on Python 3.13+).

### musl libc detection {#musl}

On Linux, the libc flavor (glibc vs musl) matters for C preprocessing:
different system headers, potentially different struct layouts. `HOST_GNU_TYPE`
correctly reports `linux-musl` on Python 3.13+ (fixed in CPython issue #95855).
On pre-3.13 Python, `HOST_GNU_TYPE` may report `linux-gnu` even when the
interpreter is linked against musl (CPython issue #87278).

headerkit corrects this with a runtime libc sniff using
`os.confstr('CS_GNU_LIBC_VERSION')`. On glibc, this returns a version string
(e.g., `'glibc 2.35'`). On musl, it raises `ValueError` or `OSError`. This
is process-aware: it checks what THIS interpreter links against, not what
libraries are installed on the system.

### cibuildwheel integration

[cibuildwheel](https://cibuildwheel.readthedocs.io/) invokes PEP 517 build
backends once per architecture per wheel. On Linux, it uses QEMU emulation,
so the running Python IS the target architecture and `HOST_GNU_TYPE` is
correct. On macOS, it downloads the matching Python for each arch. On
Windows, it uses native builds per arch. In all cases, headerkit's
auto-detection works without special hooks or configuration.

### Normalization (user input only)

`normalize_triple()` canonicalizes user-provided triples (`--target`,
`HEADERKIT_TARGET`, config file). Auto-detected triples from
`detect_process_triple()` are already canonical and bypass normalization.

- Lowercases all components
- Inserts `unknown` vendor for 3-component triples missing it:
  `x86_64-linux-gnu` -> `x86_64-unknown-linux-gnu`

Architecture names are used as-is. If you specify `--target arm64-apple-darwin`
and auto-detect would produce `aarch64-apple-darwin`, those are different
cache keys. This is intentional: `--target` means "use this exact triple."

### What flows where

The resolved triple is used in two places:

- **Cache key**: the full triple (including OS version) goes into the SHA-256
  hash for the IR cache key. This ensures cache entries are correctly
  invalidated when the target changes.
- **`-target` flag**: the full triple is passed to libclang via `-target` so
  that preprocessing reflects the correct target platform.

The **slug** (human-readable cache directory name) uses a shortened form
(`arch-os` with version stripped) for readability:
`x86_64-pc-linux-gnu` -> `x86_64-linux` in the directory name.

## Cross-compilation workflow

With target triple support, cross-compilation is straightforward:

```python
# Generate bindings for ARM64 Linux while running on x86_64 macOS
from headerkit import generate

output = generate(
    "mylib.h",
    target="aarch64-unknown-linux-gnu",
    writer_name="cffi",
)
```

Or via CLI:

```bash
headerkit mylib.h --target aarch64-unknown-linux-gnu -w cffi
```

Or via environment variable (useful in CI):

```bash
export HEADERKIT_TARGET=aarch64-unknown-linux-gnu
headerkit mylib.h -w cffi
```

The cache will store entries keyed by target triple, so the same machine can
build for multiple targets and each gets its own cache entry.

## Limitations and future work

### System headers in cross-compilation

When cross-compiling, libclang needs access to the target platform's system
headers (sysroot). headerkit passes `-target` to libclang but does not
automatically locate or configure a sysroot. Users must provide include paths
via `-I` flags or `include_dirs` for target-specific headers.

### Python cross-compilation ecosystem

[PEP 720](https://peps.python.org/pep-0720/) documents the challenges of
cross-compiling Python packages. The Python packaging ecosystem lacks
standardized cross-compilation infrastructure. headerkit uses
`HOST_GNU_TYPE` (which is already baked into every autoconf-built Python)
for native detection and relies on explicit `--target` for
cross-compilation. If a future PEP standardizes a cross-compilation
signaling mechanism, headerkit can adopt it.

### No formal triple standard

As noted in ["What the Hell Is a Target Triple?"](https://mcyoung.xyz/2025/04/14/target-triples/),
there is no formal standard for triple format. LLVM and GCC triples are
similar but not identical. headerkit follows LLVM conventions since it uses
libclang as its parsing backend.

## References

- [LLVM Triple class reference](https://llvm.org/doxygen/classllvm_1_1Triple.html)
- [Cross-compilation using Clang](https://clang.llvm.org/docs/CrossCompilation.html)
- [What the Hell Is a Target Triple?](https://mcyoung.xyz/2025/04/14/target-triples/)
- [What's an LLVM target triple?](https://www.flother.is/til/llvm-target-triple/)
- [PEP 720: Cross-compiling Python packages](https://peps.python.org/pep-0720/)
- [Rust target-lexicon crate](https://crates.io/crates/target-lexicon)
- [LLVM Triple.cpp source](https://llvm.org/doxygen/Triple_8cpp_source.html)
- [cibuildwheel documentation](https://cibuildwheel.readthedocs.io/)
- [crossenv: cross-compiling virtualenvs](https://github.com/benfogle/crossenv)
