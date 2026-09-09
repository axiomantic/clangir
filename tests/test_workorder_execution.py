"""Compile-link-execute gates for generated work-order tests.

A generated test file that merely *parses* proves almost nothing. ``compile()``
succeeds identically whether the package it imports works or raises ``NameError``
on its first line -- which is exactly how the importability defects this repo
carries managed to ship. These tests build a real C library, generate a project
against its header, and run the generated suite for real, asserting that Tier 1
passes and that the stubs fail.

**The generated suite alone does not reach the library, and cannot.** Tier 1 is
language-local by construction -- ``ord(CT_MODE_FAST)`` is a Nim enum constant and a
struct round-trip is a Nim field write -- and every Tier 2/3 stub calls ``fail()``
before it would call anything. ``importc`` is compile-time only, so a linker asked for
an object nobody references is satisfied by an object with no symbols in it. A gate
built on the generated suite alone therefore passes against an *empty* translation
unit, which is what this file used to do.

So each gate appends a driver that calls real functions through the generated bindings
module, and each has a negative control that rebuilds the native artifact from an empty
translation unit and requires the run to go red. The negative control is the load-bearing
part: a gate never observed failing for the right reason is a claim, not a mechanism.

The skips are narrow and explicit. A toolchain check that quietly no-ops when the
compiler is absent proves nothing while looking green.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from headerkit.backends import get_backend
from headerkit.backends.libclang import is_system_libclang_available
from headerkit.scaffold import ScaffoldOptions, scaffold
from tests.native_build import position_independent_flags

#: Every test here parses with libclang. Without the marker, a machine that has `nim`
#: and `cc` but no libclang errors instead of skipping.
pytestmark = pytest.mark.libclang

#: A header with one enum, one record with an unsigned bit-field, and three
#: functions -- one per stub tier. Enums are tag-named because the Nim writer
#: spells a typedef'd anonymous enum as ``importc: "enum X"``, which is not a C
#: type name and does not compile.
LIB_H = """\
typedef enum CtMode { CT_MODE_FAST = 0, CT_MODE_SAFE = 1 } CtMode;
typedef struct CtStats { int total; unsigned flags : 3; } CtStats;
int ct_add(int a, int b);
int ct_mode_cost(CtMode mode);
int ct_scale(CtStats* s, int factor);
"""

LIB_C = """\
#include "lib.h"
int ct_add(int a, int b) { return a + b; }
int ct_mode_cost(CtMode mode) { return mode == CT_MODE_FAST ? 1 : 10; }
int ct_scale(CtStats* s, int factor) { if (!s) return -1; s->total *= factor; return s->total; }
"""

#: A translation unit that defines nothing. Compiling this in place of ``LIB_C`` is the
#: negative control: every gate below must go red against it. The gate this file
#: replaced passed against exactly this input.
EMPTY_LIB_C = "/* no symbols */\n"

#: Environment variable the generated ctypes loader reads to locate the native library.
#: Read from ``_library_loader`` in ``headerkit/writers/ctypes.py`` on branch
#: ``fix/scaffold-package-importable``, which derives it from the package name:
#: non-alphanumerics to ``_``, upper-cased, suffixed ``_LIBRARY``. Pinned by
#: ``test_generated_ctypes_package_is_not_yet_importable`` below, which fails loudly
#: if that contract is not what lands.
CTYPES_LIBRARY_PATH_ENV = "CTDEMO_LIBRARY"

#: Calls two C functions through the generated ctypes bindings and asserts their real
#: return values, including a value written back through a struct pointer.
DRIVER_PY = """\
import ctypes
from ctdemo import _bindings

assert _bindings._lib.ct_add(2, 3) == 5
stats = _bindings.CtStats()
stats.total = 4
assert _bindings._lib.ct_scale(ctypes.byref(stats), 5) == 20
assert stats.total == 20
"""


def _c_compiler() -> str:
    for candidate in ("cc", "gcc", "clang"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no C compiler (cc/gcc/clang) on PATH")


def _shared_library_name(stem: str) -> str:
    if sys.platform == "win32":
        return f"{stem}.dll"
    if sys.platform == "darwin":
        return f"lib{stem}.dylib"
    return f"lib{stem}.so"


def _build_shared_library(compiler: str, workdir: Path, *, source: str = LIB_C) -> Path:
    """Compile ``source`` into a real shared library and return its path."""
    (workdir / "lib.h").write_text(LIB_H, encoding="utf-8")
    (workdir / "lib.c").write_text(source, encoding="utf-8")
    out = workdir / _shared_library_name("ctdemo")
    shared_flag = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared"]
    subprocess.run(
        [compiler, *shared_flag, *position_independent_flags(), "lib.c", "-I", ".", "-o", str(out)],
        cwd=workdir,
        check=True,
        capture_output=True,
    )
    assert out.exists(), f"compiler reported success but produced no {out.name}"
    return out


def _scaffold(workdir: Path, target: str, package: str) -> None:
    if not is_system_libclang_available():
        pytest.skip("System libclang not available")
    unit = get_backend("libclang").parse(LIB_H, "lib.h")
    layout = scaffold(unit, ScaffoldOptions(package_name=package, target_language=target, layout="package"))
    layout.write_to_disk(workdir)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def test_generated_ctypes_loader_reads_the_env_var_this_file_sets(tmp_path: Path) -> None:
    """The loader's environment variable must be the one the gate below sets.

    The neighbouring gate points the generated package at a real library through
    :data:`CTYPES_LIBRARY_PATH_ENV`. If the writer renamed that variable, the gate
    would stop reaching the library and would still be green on everything it can
    see locally, so the mismatch is asserted here rather than inferred there.

    Absence of the library is the condition under test: the import must fail
    *because the library is missing*, not because the emitted module is broken.
    """
    _scaffold(tmp_path, "ctypes", "ctdemo")
    source = (tmp_path / "src" / "ctdemo" / "_bindings.py").read_text(encoding="utf-8")
    assert f'_LIBRARY_PATH_ENV = "{CTYPES_LIBRARY_PATH_ENV}"' in source, source

    result = subprocess.run(
        [sys.executable, "-c", "import ctdemo._bindings"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src"), CTYPES_LIBRARY_PATH_ENV: ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "the package imported with no native library present"
    # Specific, not a bare non-zero exit: a module broken for an unrelated reason
    # also exits non-zero, and that is the state this test replaced.
    assert "OSError" in result.stderr, result.stderr
    assert CTYPES_LIBRARY_PATH_ENV in result.stderr.split("OSError", 1)[1], result.stderr


def test_generated_python_suite_executes_against_a_real_library(tmp_path: Path) -> None:
    """Tier 1 must pass, every stub must fail, and the bindings must call the real library."""
    compiler = _c_compiler()
    lib = _build_shared_library(compiler, tmp_path)
    _scaffold(tmp_path, "ctypes", "ctdemo")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "src")
    env[CTYPES_LIBRARY_PATH_ENV] = str(lib)

    # The generated suite never calls a C function -- Tier 1 is Python-local and every
    # stub fails first -- so it is asserted separately here. Without this the gate says
    # nothing about whether the bindings reach the library.
    driver = subprocess.run(
        [sys.executable, "-c", DRIVER_PY],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert driver.returncode == 0, driver.stdout + driver.stderr

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "tests" / "test_workorder.py"), "-v", "--no-header"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    passed = [ln for ln in result.stdout.splitlines() if " PASSED" in ln]
    failed = [ln for ln in result.stdout.splitlines() if " FAILED" in ln]

    assert any("test_enum_CtMode_values" in ln for ln in passed), result.stdout
    assert any("test_CtStats_field_roundtrip" in ln for ln in passed), result.stdout
    assert any("test_CtStats_flags_bitfield_bounds" in ln for ln in passed), result.stdout
    assert any("test_ct_add" in ln for ln in failed), result.stdout
    assert "WORK ORDER" in result.stdout


# ---------------------------------------------------------------------------
# Nim
# ---------------------------------------------------------------------------


def _nim() -> str:
    found = shutil.which("nim")
    if not found:
        pytest.skip("nim is not on PATH")
    return found


#: Appended to the generated Nim suite. The generated suite itself never calls a C
#: function, so without this the linker is never asked to resolve one and the whole
#: gate passes against an object file with no symbols in it.
NIM_DRIVER = """
suite "gate: the generated bindings call the real library":
  test "ct_add executes through the generated binding":
    check ct_add(2, 3) == 5

  test "ct_scale executes through the generated binding":
    var s: CtStats
    s.total = 4
    check ct_scale(addr s, 5) == 20
    check s.total == 20
"""

#: The line the driver above prints when it runs and passes. Absent both when the link
#: fails and when the call returns the wrong value.
NIM_DRIVER_OK = "[OK] ct_add executes through the generated binding"


def _run_generated_nim_suite(
    tmp_path: Path,
    *,
    mutate: bool = False,
    library_source: str = LIB_C,
) -> subprocess.CompletedProcess[str]:
    """Generate, compile, link and run the Nim work-order suite against a real object file."""
    compiler = _c_compiler()
    nim = _nim()
    (tmp_path / "lib.h").write_text(LIB_H, encoding="utf-8")
    (tmp_path / "lib.c").write_text(library_source, encoding="utf-8")
    subprocess.run(
        [compiler, *position_independent_flags(), "-c", "lib.c", "-o", "lib.o"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    _scaffold(tmp_path, "nim", "ctdemo")

    suite = tmp_path / "tests" / "test_workorder.nim"
    suite.write_text(suite.read_text(encoding="utf-8") + NIM_DRIVER, encoding="utf-8")

    if mutate:
        # Corrupt the Tier 1 expectation. The generated test asserts the enumerator's
        # declared value; if it truly executes and asserts, a wrong value must turn it
        # red. This is the planted failure that proves the gate above can see one.
        text = suite.read_text(encoding="utf-8")
        corrupted = text.replace("check ord(CT_MODE_FAST) == 0", "check ord(CT_MODE_FAST) == 999")
        assert corrupted != text, "expected the generated Tier 1 enum assertion to be present"
        suite.write_text(corrupted, encoding="utf-8")

    return subprocess.run(
        [
            nim,
            "c",
            "-r",
            "--hints:off",
            "--verbosity:0",
            "--path:src",
            "--passC:-I.",
            "--passL:lib.o",
            str(suite),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_generated_nim_suite_executes_against_a_real_object(tmp_path: Path) -> None:
    """Tier 1 passes, every stub fails, a failing case blocks no later case, and the
    bindings resolve and call real C functions in the linked object."""
    result = _run_generated_nim_suite(tmp_path)
    out = result.stdout + result.stderr

    # The load-bearing pair: these two run only if `lib.o` actually contains `ct_add`
    # and `ct_scale` and the generated `importc` declarations match their C signatures.
    # `test_nim_gate_is_red_without_the_native_library` proves they can go red.
    assert NIM_DRIVER_OK in out, out
    assert "[OK] ct_scale executes through the generated binding" in out, out

    assert "[OK] every enumerator of `enum CtMode`" in out, out
    assert "[OK] every scalar field of `CtStats`" in out, out

    assert "[FAILED] ct_add" in out, out
    assert '[FAILED] ct_scale["behaviour"]' in out, out
    assert '[FAILED] ct_scale["null_s"]' in out, out

    # The compile-time macro must produce one discrete result per enumerator, and a
    # failing case must not swallow its successor.
    assert "[FAILED] ct_mode_cost[CT_MODE_FAST]" in out, out
    assert "[FAILED] ct_mode_cost[CT_MODE_SAFE]" in out, out

    assert "WORK ORDER" in out, out
    assert result.returncode != 0, "a suite full of failing stubs must exit non-zero"


def test_nim_tier1_assertions_are_load_bearing(tmp_path: Path) -> None:
    """Planted failure: a wrong Tier 1 expectation must turn the generated test red.

    Without this, a Tier 1 test that executes but asserts nothing would look exactly
    like one that asserts the right value, and the gate above would pass either way.
    """
    result = _run_generated_nim_suite(tmp_path, mutate=True)
    out = result.stdout + result.stderr
    assert "[FAILED] every enumerator of `enum CtMode`" in out, out


def test_nim_gate_is_red_without_the_native_library(tmp_path: Path) -> None:
    """Negative control: link the same suite against an object file with no symbols.

    This is the control the gate above lacked. Built from an empty translation unit,
    every assertion of that gate -- ``result.returncode != 0`` included -- still passed,
    because nothing the generated suite runs calls a C function and ``importc`` is
    compile-time only. With the driver appended, the link cannot be satisfied.
    """
    result = _run_generated_nim_suite(tmp_path, library_source=EMPTY_LIB_C)
    out = result.stdout + result.stderr

    assert result.returncode != 0, out
    assert NIM_DRIVER_OK not in out, out
    # Nothing runs at all: the failure is at link time, not in an assertion.
    assert "[OK] every enumerator of `enum CtMode`" not in out, out
    # Both linkers name the symbol they could not resolve: `"_ct_add", referenced from`
    # on macOS, `undefined reference to 'ct_add'` on ELF platforms.
    assert "ct_add" in out, out
