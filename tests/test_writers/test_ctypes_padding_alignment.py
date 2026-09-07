"""A record holding both a real member and unnamed-bitfield padding.

ctypes can reserve bits only with a *named* bitfield, and a named bitfield of
``unsigned int`` aligns its record to four bytes. C does that only under
AAPCS64. Under x86-64 System V and Darwin an unnamed bitfield contributes no
alignment at all, so the obvious carrier over-aligns the record on the two most
common platforms -- and, where a following member sits behind a nested record,
moves that member as well.

The corpus in ``test_regression_backend_parity`` cannot see this: every member
in it is ``unsigned int``, which pins the record to 4/4 whatever the padding
does. Every record here therefore carries at least one ``unsigned char`` or
``unsigned short``, so the padding's contribution is observable.

Every expected figure was measured from a compiled C probe on three
platform/toolchain combinations -- Darwin arm64 (Apple clang 21), Linux x86-64
(gcc 14.2, clang 19.1) and Linux aarch64 (gcc 14.2, clang 19.1). gcc and clang
agreed on every entry within each architecture, and Darwin arm64 agreed with
Linux x86-64 on every entry, so two columns describe all three.
"""

from __future__ import annotations

import ctypes
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.ir import CType, Field, Header, Struct
from headerkit.writers import get_writer

#: name, source, (sizeof, alignof) where the ABI does not impose the padding's
#: alignment, the same where it does, and per-member (lowest, highest) set bit
#: in each of those two ABIs.
#:
#: Only ``unsigned char``/``short``/``int`` carriers appear: the tree-sitter
#: backend drops an unnamed bitfield whose declared type is spelled with more
#: than two words, so a ``unsigned long long`` case would measure that defect
#: rather than this one.
_CASES: tuple[
    tuple[str, str, tuple[int, int], tuple[int, int], dict[str, tuple[tuple[int, int], tuple[int, int]]]], ...
] = (
    (
        "d1_char_pad_last",
        "struct d1_char_pad_last { unsigned char a; unsigned int : 8; };",
        (2, 1),
        (4, 4),
        {"a": ((0, 7), (0, 7))},
    ),
    (
        "d2_cbit_pad_last",
        "struct d2_cbit_pad_last { unsigned char a : 3; unsigned int : 8; };",
        (2, 1),
        (4, 4),
        {"a": ((0, 2), (0, 2))},
    ),
    (
        "d3_between_short",
        "struct d3_between_short { unsigned char a; unsigned short : 4; unsigned char b; };",
        (3, 1),
        (4, 2),
        {"a": ((0, 7), (0, 7)), "b": ((16, 23), (16, 23))},
    ),
    (
        "d4_pad_first",
        "struct d4_pad_first { unsigned int : 4; unsigned char a; };",
        (2, 1),
        (4, 4),
        {"a": ((8, 15), (8, 15))},
    ),
    (
        "d5_pad_first_short",
        "struct d5_pad_first_short { unsigned short : 12; unsigned char a; };",
        (3, 1),
        (4, 2),
        {"a": ((16, 23), (16, 23))},
    ),
    (
        "d6_consec",
        "struct d6_consec { unsigned char a : 2; unsigned char : 3; unsigned int : 5; unsigned char b : 4; };",
        (2, 1),
        (4, 4),
        {"a": ((0, 1), (0, 1)), "b": ((10, 13), (10, 13))},
    ),
    (
        "d7_wide_pad",
        "struct d7_wide_pad { unsigned char a; unsigned int : 30; };",
        (8, 1),
        (8, 4),
        {"a": ((0, 7), (0, 7))},
    ),
    (
        "d8_wide_pad_mid",
        "struct d8_wide_pad_mid { unsigned char a; unsigned int : 30; unsigned char b; };",
        (9, 1),
        (12, 4),
        {"a": ((0, 7), (0, 7)), "b": ((64, 71), (64, 71))},
    ),
    (
        "d9_zw_char",
        "struct d9_zw_char { unsigned char a : 3; unsigned int : 0; unsigned char b : 5; };",
        (5, 1),
        (8, 4),
        {"a": ((0, 2), (0, 2)), "b": ((32, 36), (32, 36))},
    ),
    (
        "d10_zw_short",
        "struct d10_zw_short { unsigned short a : 3; unsigned short : 0; unsigned char b : 5; };",
        (4, 2),
        (4, 2),
        {"a": ((0, 2), (0, 2)), "b": ((16, 20), (16, 20))},
    ),
    (
        "d11_anon",
        "struct d11_anon { unsigned char top : 2; "
        "struct { unsigned char x : 3; unsigned int : 4; unsigned char y : 5; }; };",
        (3, 1),
        (8, 4),
        {"top": ((0, 1), (0, 1)), "x": ((8, 10), (32, 34)), "y": ((16, 20), (40, 44))},
    ),
    (
        "d12_anon_first",
        "struct d12_anon_first { struct { unsigned char x : 3; unsigned int : 6; }; unsigned char b; };",
        (3, 1),
        (8, 4),
        {"x": ((0, 2), (0, 2)), "b": ((16, 23), (32, 39))},
    ),
    (
        "d13_mixed",
        "struct d13_mixed { unsigned char a; unsigned short b : 5; unsigned int : 7; unsigned char c; };",
        (4, 2),
        (4, 4),
        {"a": ((0, 7), (0, 7)), "b": ((8, 12), (8, 12)), "c": ((24, 31), (24, 31))},
    ),
    (
        "d14_short_only",
        "struct d14_short_only { unsigned short a; unsigned int : 8; };",
        (4, 2),
        (4, 4),
        {"a": ((0, 15), (0, 15))},
    ),
    (
        "d15_narrow_pad",
        "struct d15_narrow_pad { unsigned int a; unsigned char : 3; };",
        (8, 4),
        (8, 4),
        {"a": ((0, 31), (0, 31))},
    ),
    (
        "d16_narrow_pad_mid",
        "struct d16_narrow_pad_mid { unsigned int a; unsigned char : 3; unsigned char b; };",
        (8, 4),
        (8, 4),
        {"a": ((0, 31), (0, 31)), "b": ((40, 47), (40, 47))},
    ),
    (
        "d19_straddle",
        "struct d19_straddle { unsigned char a : 6; unsigned short : 12; unsigned char b : 4; };",
        (4, 1),
        (4, 2),
        {"a": ((0, 5), (0, 5)), "b": ((28, 31), (28, 31))},
    ),
    (
        "d20_pad_then_plain",
        "struct d20_pad_then_plain { unsigned char a : 1; unsigned int : 9; unsigned short c; };",
        (4, 2),
        (4, 4),
        {"a": ((0, 0), (0, 0)), "c": ((16, 31), (16, 31))},
    ),
)

_SOURCE = "\n".join(source for _, source, _, _, _ in _CASES)

#: Records whose padding cannot decide the alignment, so the two ABIs cannot
#: disagree and no branch belongs in the output. Either the carrier is already
#: byte-aligned (``d15``, ``d16``: ``unsigned char`` padding) or a real member
#: already imposes at least as much (``d10``: an ``unsigned short`` member
#: alongside ``unsigned short`` padding).
#:
#: These are the negative control. Without them a writer that branched on every
#: record would satisfy the two layout tests above while making every generated
#: module import ``platform`` to consult a flag that changes nothing.
_BYTE_CARRIER_PADDING = frozenset({"d10_zw_short", "d15_narrow_pad", "d16_narrow_pad_mid"})


def _bit_extent(buffer: bytes) -> tuple[int, int]:
    """Index of the lowest and highest set bit in ``buffer``, or (-1, -1)."""
    bits = [i for i in range(len(buffer) * 8) if buffer[i // 8] >> (i % 8) & 1]
    return (bits[0], bits[-1]) if bits else (-1, -1)


def _exec_as(code: str, *, system: str, machine: str) -> dict[str, object]:
    """Execute generated source as though it were imported on the named host."""
    namespace: dict[str, object] = {}
    original_platform, original_machine = sys.platform, platform.machine
    sys.platform = system  # type: ignore[assignment]
    platform.machine = lambda: machine  # type: ignore[assignment]
    try:
        exec(compile(code, "generated_ctypes.py", "exec"), namespace)  # noqa: S102
    finally:
        sys.platform = original_platform  # type: ignore[assignment]
        platform.machine = original_machine  # type: ignore[assignment]
    return namespace


def _generate(backend_name: str, source: str) -> str:
    if not is_backend_available(backend_name):
        pytest.skip(f"{backend_name} backend unavailable")
    return get_writer("ctypes").write(get_backend(backend_name).parse(source, "layout.h"))


def _measure(record: type, members: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {"__record__": (ctypes.sizeof(record), ctypes.alignment(record))}
    for member in members:
        instance = record()
        setattr(instance, member, 0xFFFFFFFF)
        out[member] = _bit_extent(bytes(instance))
    return out


# ---------------------------------------------------------------------------
# Both ABI branches, forced on any host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
@pytest.mark.parametrize(
    ("name", "source", "unaligned", "aligned", "members"),
    _CASES,
    ids=[c[0] for c in _CASES],
)
class TestPaddingAlignmentUnderBothABIs:
    """One generated module must satisfy both ABIs, chosen when it is imported.

    ctypes lays a record out for the host running Python, not for the host that
    generated the module, so neither answer may be baked in at write time.
    Forcing both branches here covers the rule on any host, which no
    single-platform CI run could do.
    """

    def test_layout_matches_c_on_a_host_that_does_not_align(
        self,
        backend_name: str,
        name: str,
        source: str,
        unaligned: tuple[int, int],
        aligned: tuple[int, int],
        members: dict[str, tuple[tuple[int, int], tuple[int, int]]],
    ) -> None:
        """The branch that regressed: a wide padding carrier over-aligned here."""
        namespace = _exec_as(_generate(backend_name, source), system="linux", machine="x86_64")

        assert name in namespace, f"writer emitted no class for {name}"
        measured = _measure(namespace[name], members)  # type: ignore[arg-type]
        assert measured["__record__"] == unaligned
        assert {m: measured[m] for m in members} == {m: e[0] for m, e in members.items()}

    def test_layout_matches_c_on_a_host_that_does_align(
        self,
        backend_name: str,
        name: str,
        source: str,
        unaligned: tuple[int, int],
        aligned: tuple[int, int],
        members: dict[str, tuple[tuple[int, int], tuple[int, int]]],
    ) -> None:
        """Correcting the other ABI must not be done by breaking this one."""
        namespace = _exec_as(_generate(backend_name, source), system="linux", machine="aarch64")

        assert name in namespace, f"writer emitted no class for {name}"
        measured = _measure(namespace[name], members)  # type: ignore[arg-type]
        assert measured["__record__"] == aligned
        assert {m: measured[m] for m in members} == {m: e[1] for m, e in members.items()}

    def test_a_branch_is_emitted_exactly_when_the_abis_disagree(
        self,
        backend_name: str,
        name: str,
        source: str,
        unaligned: tuple[int, int],
        aligned: tuple[int, int],
        members: dict[str, tuple[tuple[int, int], tuple[int, int]]],
    ) -> None:
        """A branch belongs only where a padding carrier could raise alignment.

        Emitting one for every record would pass the two tests above while
        making layouts depend on a flag that cannot affect them, and would put
        an unnecessary ``platform`` import in every generated module.
        """
        code = _generate(backend_name, source)

        expected = name not in _BYTE_CARRIER_PADDING
        assert ("_HK_UNNAMED_BITFIELD_ALIGNS" in code) is expected


# ---------------------------------------------------------------------------
# Ground truth: the C compiler on whatever host is running the tests
# ---------------------------------------------------------------------------


def _c_compiler() -> str:
    for candidate in ("cc", "gcc", "clang"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no C compiler available")


@pytest.fixture(scope="module")
def c_layout() -> dict[str, tuple[int, int]]:
    """Compile and run the corpus in C, and read the layout back out.

    Writing a member as all-ones and scanning the byte image locates it
    exactly, which ``offsetof`` cannot do for a bitfield.
    """
    compiler = _c_compiler()
    probes = []
    for name, _, _, _, members in _CASES:
        probes.append(f'    printf("{name} %zu %zu\\n", sizeof(struct {name}), _Alignof(struct {name}));')
        for member in members:
            probes.append(
                f"    {{ struct {name} v; memset(&v, 0, sizeof v); v.{member} = ~0u; "
                f'report("{name}.{member}", (unsigned char *)&v, sizeof v); }}'
            )
    program = textwrap.dedent("""\
        #include <stdio.h>
        #include <string.h>
        {source}
        static void report(const char *label, unsigned char *p, size_t n) {{
            int lo = -1, hi = -1;
            for (size_t i = 0; i < n * 8; i++)
                if (p[i / 8] >> (i % 8) & 1) {{ if (lo < 0) lo = (int)i; hi = (int)i; }}
            printf("%s %d %d\\n", label, lo, hi);
        }}
        int main(void) {{
        {probes}
            return 0;
        }}
        """).format(source=_SOURCE, probes="\n".join(probes))

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / "probe.c").write_text(program)
        build = subprocess.run(
            [compiler, "-w", "probe.c", "-o", "probe"], cwd=workdir, capture_output=True, text=True, check=False
        )
        assert build.returncode == 0, f"layout probe failed to build:\n{build.stderr}"
        run = subprocess.run([str(workdir / "probe")], capture_output=True, text=True, check=False)
        assert run.returncode == 0, f"layout probe failed to run:\n{run.stderr}"

    measured = {}
    for line in run.stdout.splitlines():
        label, first, second = line.split()
        measured[label] = (int(first), int(second))
    expected_keys = {name for name, _, _, _, _ in _CASES} | {
        f"{name}.{member}" for name, _, _, _, members in _CASES for member in members
    }
    assert set(measured) == expected_keys, "probe did not report every corpus entry"
    return measured


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
@pytest.mark.parametrize(
    ("name", "source", "members"),
    [(name, source, tuple(members)) for name, source, _, _, members in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_generated_layout_matches_the_host_c_compiler(
    backend_name: str,
    name: str,
    source: str,
    members: tuple[str, ...],
    c_layout: dict[str, tuple[int, int]],
) -> None:
    """Backend parity proves nothing here -- both backends were wrong together.

    The simulated-ABI tests above pin literals that a future edit could pin to
    the wrong value. This one asks the C compiler on the running host instead,
    so at least one of the two ABI branches is checked against ground truth on
    every machine the suite runs on.
    """
    namespace: dict[str, object] = {}
    exec(compile(_generate(backend_name, source), "generated_ctypes.py", "exec"), namespace)  # noqa: S102

    assert name in namespace, f"writer emitted no class for {name}"
    record = namespace[name]
    assert (ctypes.sizeof(record), ctypes.alignment(record)) == c_layout[name]  # type: ignore[arg-type]
    for member in members:
        instance = record()  # type: ignore[operator]
        setattr(instance, member, 0xFFFFFFFF)
        assert _bit_extent(bytes(instance)) == c_layout[f"{name}.{member}"], (
            f"{name}.{member} is not where the C compiler puts it"
        )


# ---------------------------------------------------------------------------
# Composition with ``_pack_``, and the one shape that cannot be represented
# ---------------------------------------------------------------------------


def _packed_header() -> Header:
    """Hand-built so the writer is exercised on its own. Both backends now set
    ``is_packed``; ``test_packed_records.py`` covers the parsed path."""
    return Header(
        path="p.h",
        declarations=[
            Struct(
                name="packed_pad",
                fields=[
                    Field("a", CType("unsigned char")),
                    Field("", CType("unsigned int"), bit_width=8, is_padding=True),
                    Field("b", CType("unsigned char")),
                ],
                is_union=False,
                is_packed=True,
            )
        ],
    )


def test_a_packed_record_keeps_one_field_list() -> None:
    """``_pack_ = 1`` already pins the record to byte alignment, so the padding
    carrier cannot raise it and there is nothing for the host to choose. A
    branch here would also have to be reconciled with ``_pack_`` itself."""
    code = get_writer("ctypes").write(_packed_header())

    assert "_pack_ = 1" in code
    assert "_HK_UNNAMED_BITFIELD_ALIGNS" not in code
    assert code.count("_fields_ = [") == 1


def test_a_packed_record_still_reserves_the_padding_bits() -> None:
    """A negative control for the test above: dropping the branch must not be
    achieved by dropping the padding.

    The carrier is a byte, not the declared ``unsigned int``. Packing removes
    the storage unit, so C reserves exactly the 8 bits and puts ``b`` straight
    after them; a ``c_uint`` carrier reserves a full 32-bit unit instead. A
    compiled C probe measures this record at 3 bytes with ``b`` at bit 16,
    and the ``c_uint`` spelling produced 6 bytes with ``b`` at bit 40.
    """
    code = get_writer("ctypes").write(_packed_header())

    assert '("_pad0", ctypes.c_ubyte, 8)' in code
    assert '("_pad0", ctypes.c_uint, 8)' not in code


_UNTRACKABLE = "struct e1 { struct { unsigned char x : 3; }; unsigned int : 8; unsigned char b; };"


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
def test_an_untrackable_offset_is_named_in_the_output(backend_name: str) -> None:
    """Padding behind a nested record leaves the writer without a bit offset to
    respell from, so it cannot build the alignment-neutral carrier.

    The record is still emitted, because the aligning spelling is right on
    AAPCS64 and reserves the correct bits everywhere. What must not happen is
    that it is emitted looking exactly like a record the writer got right.
    """
    code = _generate(backend_name, _UNTRACKABLE)

    assert "# HEADERKIT: e1 may be over-aligned where C does not" in code
    assert '("_pad0", ctypes.c_uint, 8)' in code


def test_a_representable_record_carries_no_diagnostic() -> None:
    """Without this, a writer that commented every record would pass above."""
    code = _generate("libclang", "struct d1_char_pad_last { unsigned char a; unsigned int : 8; };")

    assert "# HEADERKIT:" not in code
