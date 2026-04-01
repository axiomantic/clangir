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

### `platform.machine()` lies

`platform.machine()` returns the host CPU architecture, not the process
architecture. A 32-bit Python running on 64-bit Windows reports `AMD64`, not
`i686`. This was identified as a real problem in
[PyO3 PR #830](https://github.com/PyO3/pyo3/pull/830).

### `struct.calcsize("P")` tells the truth

`struct.calcsize("P")` returns the size of a C `void*` pointer in the current
Python process. This is 4 bytes on 32-bit and 8 bytes on 64-bit, regardless
of the host OS bitness. It is the most reliable way to detect the process's
pointer width in Python.

```python
import struct
pointer_bits = struct.calcsize("P") * 8  # 32 or 64
```

Other methods and their pitfalls:

| Method | Reliable? | Notes |
|--------|-----------|-------|
| `struct.calcsize("P") * 8` | Yes | Measures actual pointer size |
| `sys.maxsize > 2**32` | Mostly | Checks address space |
| `platform.architecture()` | No | Unreliable on macOS (universal binaries) |
| `platform.machine()` | No | Reports host, not process |

### `cc -dumpmachine`

Running `cc -dumpmachine` (or `gcc -dumpmachine`, `clang -dumpmachine`) returns
the compiler's default target triple. This is useful because:

- It includes OS version information (e.g., `darwin25.3.0`)
- It reflects the actual compiler configuration
- It's the same format libclang expects for `-target`

However, it reports the **compiler's** default target, not the Python process's
target. A 64-bit compiler on a system running 32-bit Python will report
`x86_64-pc-windows-msvc`, but the correct target for headerkit is
`i686-pc-windows-msvc`.

### LLVM's own distinction

LLVM itself distinguishes between two functions
([docs](https://llvmlite.readthedocs.io/en/latest/user-guide/binding/target-information.html)):

- `get_default_triple()`: the triple LLVM was configured to target (host compiler)
- `get_process_triple()`: the triple suitable for the current process

headerkit needs the equivalent of `get_process_triple()`.

## headerkit's detection algorithm

headerkit resolves the target triple via config precedence:

1. **Explicit `target` kwarg** to `generate()` (highest priority)
2. **`HEADERKIT_TARGET` environment variable**
3. **`[tool.headerkit] target`** in pyproject.toml
4. **Auto-detection** via `detect_process_triple()`

The auto-detection algorithm (5 steps, in order):

1. **`sysconfig.get_platform()`** -- respects `_PYTHON_HOST_PLATFORM` and
   crossenv monkeypatching. Converts the platform tag (e.g., `linux-aarch64`,
   `macosx-14.0-arm64`, `win-amd64`) to an LLVM triple. Returns `None` for
   `universal2` (ambiguous fat binary tag; see [universal2 note](#universal2)).
2. **`ARCHFLAGS` environment variable** (macOS) -- extracts a single `-arch`
   value. Returns `None` for universal2 or multiple architectures.
3. **`VSCMD_ARG_TGT_ARCH` environment variable** (Windows Visual Studio) --
   maps `x64`/`x86`/`arm64`/`arm` to LLVM architecture names.
4. **`cc -dumpmachine`** with pointer-width correction via
   `struct.calcsize("P")`.
5. **Construct from `sys.platform` + `platform.machine()`** with pointer-width
   correction.

Each step either returns a definitive triple or falls through to the next.
Step 1 covers the vast majority of cases (native builds, cibuildwheel,
crossenv). Steps 2-3 handle platform-specific cross-compilation signals.
Steps 4-5 are fallbacks for environments where `sysconfig.get_platform()`
does not reflect the actual target.

### Cross-compilation signals

Python has no standardized mechanism for communicating the cross-compilation
target to build-time code. Instead, several de facto signals have emerged:

**`_PYTHON_HOST_PLATFORM`**: Set by CPython's own cross-build infrastructure,
[crossenv](https://github.com/benfogle/crossenv), and cibuildwheel. When set,
it overrides the return value of `sysconfig.get_platform()`. This is the most
widely supported signal because `sysconfig.get_platform()` is already part of
the standard library and build backends already call it.

**`ARCHFLAGS`**: Set by macOS build tools (Xcode, setuptools on macOS) and
cibuildwheel. Format: `-arch <name>` (e.g., `-arch arm64`). Can contain
multiple `-arch` flags for universal builds; headerkit ignores multi-arch
values since it produces text output, not binary.

**`VSCMD_ARG_TGT_ARCH`**: Set by the Visual Studio Developer Command Prompt
and `vcvarsall.bat`. Values: `x86`, `x64`, `arm`, `arm64`. Communicates
which toolchain variant was selected.

**`struct.calcsize("P")`**: Not an environment variable, but a runtime signal.
Returns the pointer width of the running Python process (4 bytes for 32-bit,
8 bytes for 64-bit). This detects 32-bit Python on a 64-bit host, where
`platform.machine()` would incorrectly report the host's 64-bit architecture.

`sysconfig.get_platform()` is the primary signal because it integrates
`_PYTHON_HOST_PLATFORM` and crossenv's monkeypatching, making it the closest
thing to a standard cross-compilation signal in Python. headerkit checks it
first and only falls through to the other signals when it yields an ambiguous
or unparseable result.

### universal2 {#universal2}

`universal2` is a macOS platform tag representing a fat binary that contains
both `x86_64` and `arm64` slices. Since headerkit produces text output (Python
bindings), not binary code, `universal2` is not a meaningful target. When
`sysconfig.get_platform()` returns a tag ending in `universal2`, detection
returns `None` for that step and falls through to the native architecture
detection methods (ARCHFLAGS, cc -dumpmachine, or platform.machine()), which
resolve to a single concrete architecture.

### cibuildwheel integration

[cibuildwheel](https://cibuildwheel.readthedocs.io/) invokes PEP 517 build
backends once per architecture per wheel. For each invocation, it sets
`_PYTHON_HOST_PLATFORM` and/or `ARCHFLAGS` to communicate the target
architecture. This means headerkit's auto-detection "just works" in
cibuildwheel environments without special hooks or configuration. A project
that builds wheels for `x86_64` and `aarch64` on the same CI runner will
get correct, architecture-specific bindings for each wheel.

### Normalization

`normalize_triple()` canonicalizes user-provided triples:

- Lowercases all components
- Normalizes arch aliases: `arm64` -> `aarch64`, `AMD64` -> `x86_64`
- Inserts `unknown` vendor for 3-component triples missing it:
  `x86_64-linux-gnu` -> `x86_64-unknown-linux-gnu`

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
standardized cross-compilation infrastructure. While PEP 720 identifies
the problems, no standardized solution has emerged yet. headerkit works
with the existing de facto signals (`_PYTHON_HOST_PLATFORM`, `ARCHFLAGS`,
`VSCMD_ARG_TGT_ARCH`, `sysconfig.get_platform()`) rather than waiting for
a formal standard. If a future PEP standardizes a cross-compilation
signaling mechanism, headerkit can adopt it as an additional detection step.

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
- [llvmlite target information](https://llvmlite.readthedocs.io/en/latest/user-guide/binding/target-information.html)
- [PyO3 PR #830: struct.calcsize("P") for arch detection](https://github.com/PyO3/pyo3/pull/830)
- [PEP 720: Cross-compiling Python packages](https://peps.python.org/pep-0720/)
- [Rust target-lexicon crate](https://crates.io/crates/target-lexicon)
- [LLVM Triple.cpp source](https://llvm.org/doxygen/Triple_8cpp_source.html)
- [cibuildwheel documentation](https://cibuildwheel.readthedocs.io/)
- [crossenv: cross-compiling virtualenvs](https://github.com/benfogle/crossenv)
