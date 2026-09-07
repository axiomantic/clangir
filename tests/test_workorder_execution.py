"""Compile-link-execute gates for generated work-order tests.

A generated test file that merely *parses* proves almost nothing. ``compile()``
succeeds identically whether the package it imports works or raises ``NameError``
on its first line -- which is exactly how the importability defects this repo
carries managed to ship. These tests build a real C library, generate a project
against its header, and run the generated suite for real, asserting that Tier 1
passes and that the stubs fail.

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
from headerkit.scaffold import ScaffoldOptions, scaffold
from tests.native_build import position_independent_flags

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


def _build_shared_library(compiler: str, workdir: Path) -> Path:
    """Compile LIB_C into a real shared library and return its path."""
    (workdir / "lib.h").write_text(LIB_H, encoding="utf-8")
    (workdir / "lib.c").write_text(LIB_C, encoding="utf-8")
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
    unit = get_backend("libclang").parse(LIB_H, "lib.h")
    layout = scaffold(unit, ScaffoldOptions(package_name=package, target_language=target, layout="package"))
    layout.write_to_disk(workdir)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The ctypes writer emits a generated package that cannot be imported: it never "
        "defines `_lib`, and it emits `CtMode = CtMode` for a typedef'd enum, which raises "
        "NameError. Both are pre-existing writer defects fixed by PR #78, not defects in the "
        "work-order tiering this file gates. strict=True means this turns into a FAILURE the "
        "moment those fixes land, forcing the marker off rather than letting it rot in place."
    ),
)
def test_generated_python_suite_executes_against_a_real_library(tmp_path: Path) -> None:
    """Tier 1 must pass and every stub must fail, against a real compiled library."""
    compiler = _c_compiler()
    lib = _build_shared_library(compiler, tmp_path)
    _scaffold(tmp_path, "ctypes", "ctdemo")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "src")
    env["CTDEMO_LIB"] = str(lib)

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


def _run_generated_nim_suite(tmp_path: Path, *, mutate: bool = False) -> subprocess.CompletedProcess[str]:
    """Generate, compile, link and run the Nim work-order suite against a real object file."""
    compiler = _c_compiler()
    nim = _nim()
    (tmp_path / "lib.h").write_text(LIB_H, encoding="utf-8")
    (tmp_path / "lib.c").write_text(LIB_C, encoding="utf-8")
    subprocess.run(
        [compiler, *position_independent_flags(), "-c", "lib.c", "-o", "lib.o"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    _scaffold(tmp_path, "nim", "ctdemo")

    suite = tmp_path / "tests" / "test_workorder.nim"
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
    """Tier 1 passes, every stub fails, and a failing case blocks no later case."""
    result = _run_generated_nim_suite(tmp_path)
    out = result.stdout + result.stderr

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
