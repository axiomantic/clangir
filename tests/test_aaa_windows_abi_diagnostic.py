"""TEMPORARY diagnostic: measure Windows unnamed-bitfield layout on CI.

Removed once the win32 row of ``_ABI_HOSTS`` is settled. It exists to answer
one question that cannot be answered from this machine: whether MinGW ``cc``
(what the R13 ground-truth probe compiles with) and MSVC (what ``ctypes``
follows) agree about ``struct { unsigned int : 8; }``.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

_PROBE_STRUCTS: tuple[tuple[str, str], ...] = (
    ("s1", "struct s1 { unsigned int : 8; };"),
    ("s2", "struct s2 { unsigned int : 1; };"),
    ("s3", "struct s3 { unsigned int : 9; };"),
    ("s4", "struct s4 { unsigned int : 32; };"),
    ("s5", "struct s5 { unsigned char : 4; unsigned int : 4; };"),
    ("s6", "struct s6 { unsigned char a; unsigned int : 8; };"),
    ("s7", "struct s7 { unsigned char a; unsigned short b : 5; unsigned int : 7; unsigned char c; };"),
)

_CTYPES_EQUIVALENTS: dict[str, list[tuple[str, type, int]]] = {
    "s1": [("_pad0", ctypes.c_uint, 8)],
    "s2": [("_pad0", ctypes.c_uint, 1)],
    "s3": [("_pad0", ctypes.c_uint, 9)],
    "s4": [("_pad0", ctypes.c_uint, 32)],
    "s5": [("_pad0", ctypes.c_uint, 8)],
}


def _compile_and_run(compiler: list[str], workdir: Path, msvc_driver: bool) -> str:
    source = "\n".join(src for _, src in _PROBE_STRUCTS)
    probes = "\n".join(
        f'    printf("  {name} sizeof=%d alignof=%d\\n", (int)sizeof(struct {name}), (int)_Alignof(struct {name}));'
        for name, _ in _PROBE_STRUCTS
    )
    program = textwrap.dedent("""\
        #include <stdio.h>
        {source}
        int main(void) {{
        {probes}
            return 0;
        }}
        """).format(source=source, probes=probes)
    (workdir / "probe.c").write_text(program)
    exe = workdir / ("probe.exe" if sys.platform == "win32" else "probe")
    cmd = [*compiler, "probe.c", "/Fe:probe.exe"] if msvc_driver else [*compiler, "probe.c", "-o", str(exe)]
    build = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, check=False)
    if build.returncode != 0:
        return f"  BUILD FAILED: {build.stdout}\n{build.stderr}"
    run = subprocess.run([str(exe)], cwd=workdir, capture_output=True, text=True, check=False)
    if run.returncode != 0:
        return f"  RUN FAILED: {run.stdout}\n{run.stderr}"
    return run.stdout.rstrip()


def test_windows_unnamed_bitfield_abi_diagnostic() -> None:
    """Report every toolchain's answer, then fail so CI prints the report."""
    if sys.platform != "win32":
        pytest.skip("diagnostic targets the Windows runner only")

    report = [f"platform={sys.platform} python={sys.version}"]
    candidates: tuple[tuple[str, list[str], bool], ...] = (
        ("cc", ["cc"], False),
        ("gcc", ["gcc"], False),
        ("clang", ["clang"], False),
        ("clang --target=x86_64-pc-windows-msvc", ["clang", "--target=x86_64-pc-windows-msvc"], False),
        ("clang-cl", ["clang-cl"], True),
        ("cl", ["cl"], True),
    )
    with tempfile.TemporaryDirectory() as tmp:
        for label, cmd, msvc_driver in candidates:
            if shutil.which(cmd[0]) is None:
                report.append(f"{label}: NOT ON PATH")
                continue
            workdir = Path(tmp) / label.replace(" ", "_").replace("=", "").replace("-", "")
            workdir.mkdir(parents=True, exist_ok=True)
            report.append(f"{label}:")
            report.append(_compile_and_run(cmd, workdir, msvc_driver))

    report.append("ctypes:")
    for name, fields in _CTYPES_EQUIVALENTS.items():
        cls = type(name, (ctypes.Structure,), {"_fields_": fields})
        report.append(f"  {name} sizeof={ctypes.sizeof(cls)} alignof={ctypes.alignment(cls)}")

    pytest.fail("WINDOWS ABI DIAGNOSTIC\n" + "\n".join(report))
