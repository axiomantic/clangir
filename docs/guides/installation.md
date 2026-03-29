# Installation

## Install headerkit

Install from PyPI:

```bash
pip install headerkit
```

## System Dependency: libclang

headerkit requires the **libclang** shared library to be installed on your system. This is the C API for LLVM's Clang compiler, used by the built-in `LibclangBackend` to parse C and C++ headers.

!!! tip "Automated installation"
    For CI/CD, Docker, or quick setup, use the built-in installer:

    ```bash
    headerkit install-libclang
    ```

    This detects your platform and runs the appropriate install command. See the [Installing libclang](install-libclang.md) guide for details.

!!! note "Vendored Python bindings"
    headerkit includes its own vendored copy of the Clang Python bindings that are automatically version-matched to your system's LLVM installation. You do **not** need to install `libclang` from PyPI.

=== "macOS"

    Install LLVM via Homebrew:

    ```bash
    brew install llvm
    ```

    headerkit automatically searches common Homebrew paths (`/opt/homebrew/opt/llvm/lib/` on Apple Silicon, `/usr/local/opt/llvm/lib/` on Intel). Xcode Command Line Tools also include libclang.

=== "Ubuntu / Debian"

    Install the libclang development package:

    ```bash
    sudo apt install libclang-dev
    ```

    For a specific LLVM version:

    ```bash
    sudo apt install libclang-18-dev
    ```

=== "Fedora / RHEL"

    ```bash
    sudo dnf install clang-devel
    ```

=== "Windows"

    Install LLVM from the [official releases](https://github.com/llvm/llvm-project/releases) or via winget:

    ```bash
    winget install LLVM.LLVM
    ```

    headerkit searches `Program Files\LLVM\bin\` and common package manager locations (Scoop, MSYS2).

    !!! tip "GitHub Actions"
        GitHub Actions Windows runners come with LLVM pre-installed, so no additional setup is needed in CI.

## Development Install

To contribute to headerkit or run its test suite:

```bash
git clone https://github.com/axiomantic/headerkit.git
cd headerkit
pip install -e '.[dev]'
```

## Verify Installation

Confirm everything is working:

```bash
python -c "from headerkit import get_backend; b = get_backend(); print(f'Backend: {b.name}')"
```

Expected output:

```
Backend: libclang
```

If you see a warning about missing backends, your libclang shared library was not found. Double-check the installation steps for your platform above.
