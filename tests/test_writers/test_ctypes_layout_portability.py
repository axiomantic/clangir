"""The generated layout must not depend on which CPython imports the module.

ctypes has had two different bit-field algorithms. Before 3.14 it opened a
fresh storage unit whenever a bit-field's declared type differed in size from
the unit it would otherwise land in, and derived the record's alignment only
from bit-fields that opened a unit. 3.14 replaced that with the platform C
compiler's rule. A generated module is a file: it may be written under one
interpreter and imported under another, so a spelling that is right on only
one of them is wrong.

The remedy has two halves, and each test below pins one of them:

* every bit-field in the portable spelling uses a one-byte carrier, so no
  declared type ever differs from the unit in play and all three engines --
  pre-3.14, 3.14, and the MSVC algorithm ctypes uses on Windows -- agree;
* the alignment those one-byte carriers give up is put back by a zero-length
  array, which occupies no bytes and imposes its element type's alignment
  identically on every CPython measured.

Measured for ``struct { unsigned char : 4; unsigned int : 4; }``, whose
declared types are mixed, on macOS arm64:

======================================  ======  ======  ======
spelling                                3.10    3.13    3.14
======================================  ======  ======  ======
``(c_ubyte, 4), (c_uint, 4)``           4 / 1   4 / 1   4 / 4
``(c_uint, 8)`` (one carrier)           4 / 4   4 / 4   4 / 4
======================================  ======  ======  ======

C under AAPCS64 says 4 / 4. Only the second spelling is right everywhere.
"""

from __future__ import annotations

import ctypes
import platform
import sys

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.writers import get_writer
from tests.skip_policy import BACKEND_INSTALL, missing_toolchain

#: Records whose padding and members do not share one declared type. A record
#: spelled in a single type cannot show the disagreement, so nothing here is.
_MIXED_CARRIER_SOURCES: tuple[tuple[str, str], ...] = (
    ("d1_char_pad_last", "struct d1_char_pad_last { unsigned char a; unsigned int : 8; };"),
    ("d3_between_short", "struct d3_between_short { unsigned char a; unsigned short : 4; unsigned char b; };"),
    (
        "d6_consec",
        "struct d6_consec { unsigned char a : 2; unsigned char : 3; unsigned int : 5; unsigned char b : 4; };",
    ),
    (
        "d13_mixed",
        "struct d13_mixed { unsigned char a; unsigned short b : 5; unsigned int : 7; unsigned char c; };",
    ),
)

#: An all-padding record takes the other half of the remedy -- a single wide
#: carrier rather than byte carriers plus a zero-length array -- and has its own
#: test below.


def _generate(backend_name: str, source: str) -> str:
    if not is_backend_available(backend_name):
        missing_toolchain(f"the {backend_name} backend is not available", BACKEND_INSTALL[backend_name])
    return get_writer("ctypes").write(get_backend(backend_name).parse(source, "layout.h"))


def _exec_as(code: str, *, system: str, machine: str) -> dict[str, object]:
    """Execute generated source as though imported on the named host."""
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


def _bitfield_carriers(record: type) -> list[type]:
    """The declared type of every bit-field entry in ``record``."""
    return [entry[1] for entry in record._fields_ if len(entry) == 3]  # type: ignore[attr-defined]


def _zero_length_arrays(record: type) -> list[type]:
    """Every field of ``record`` that occupies no bytes."""
    return [entry[1] for entry in record._fields_ if len(entry) == 2 and ctypes.sizeof(entry[1]) == 0]  # type: ignore[attr-defined]


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
@pytest.mark.parametrize(("struct_name", "source"), _MIXED_CARRIER_SOURCES, ids=[c[0] for c in _MIXED_CARRIER_SOURCES])
class TestSpellingIsIndependentOfTheImportingInterpreter:
    def test_portable_spelling_uses_only_one_byte_bitfield_carriers(
        self, backend_name: str, struct_name: str, source: str
    ) -> None:
        """A carrier wider than a byte is exactly what the engines disagree about.

        ``d13_mixed`` is the case that proves this is not cosmetic: its
        ``unsigned short b : 5`` sits at bit 8 in C and on 3.14, and at bit 16
        on 3.10 through 3.13, purely because its declared type is two bytes.
        """
        record = _exec_as(_generate(backend_name, source), system="linux", machine="x86_64")[struct_name]

        carriers = _bitfield_carriers(record)  # type: ignore[arg-type]
        assert carriers, f"{struct_name} has no bit-field to check"
        assert all(ctypes.sizeof(c) == 1 for c in carriers), (
            f"{struct_name} keeps a multi-byte bit-field carrier {[c.__name__ for c in carriers]}, "
            "which lays out differently before CPython 3.14"
        )

    def test_alignment_is_carried_by_a_field_that_occupies_no_bytes(
        self, backend_name: str, struct_name: str, source: str
    ) -> None:
        """One-byte carriers cannot raise alignment, so something else must.

        Before 3.14 a wide bit-field raises nothing unless it opens a storage
        unit, so alignment cannot be a side effect of the field spellings; it
        has to come from a field chosen for that job alone.
        """
        record = _exec_as(_generate(backend_name, source), system="linux", machine="aarch64")[struct_name]

        empty = _zero_length_arrays(record)  # type: ignore[arg-type]
        assert empty, f"{struct_name} has no zero-length array to carry its alignment"
        assert ctypes.alignment(record) == max(ctypes.alignment(e) for e in empty), (  # type: ignore[arg-type]
            f"{struct_name} does not take its alignment from its zero-length array"
        )


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
def test_all_padding_record_is_respelled_in_a_single_carrier(backend_name: str) -> None:
    """An all-padding record has no member, so only size and alignment show.

    That freedom is what lets the whole span move into one carrier -- and it
    must, because a chain that opens with a narrower type leaves the wider one
    mid-unit, where pre-3.14 ctypes reads no alignment from it at all.
    """
    source = "struct a7_all_padding { unsigned char : 4; unsigned int : 4; };"
    record = _exec_as(_generate(backend_name, source), system="linux", machine="aarch64")["a7_all_padding"]

    carriers = _bitfield_carriers(record)  # type: ignore[arg-type]
    assert carriers, "the all-padding record reserved no bits"
    assert len(set(carriers)) == 1, f"all-padding record mixes carriers {[c.__name__ for c in carriers]}"
    assert (ctypes.sizeof(record), ctypes.alignment(record)) == (4, 4)  # type: ignore[arg-type]


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
def test_narrowing_preserves_a_signed_bitfield_s_signedness(backend_name: str) -> None:
    """Narrowing may change a carrier's width, never how it reads back.

    ``c_ubyte`` would report ``-1`` stored in a three-bit signed field as 7.
    """
    source = "struct s { signed short a : 3; unsigned int : 5; unsigned char b; };"
    record = _exec_as(_generate(backend_name, source), system="linux", machine="x86_64")["s"]

    instance = record()  # type: ignore[operator]
    instance.a = -1

    assert instance.a == -1


@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
@pytest.mark.parametrize(("struct_name", "source"), _MIXED_CARRIER_SOURCES, ids=[c[0] for c in _MIXED_CARRIER_SOURCES])
def test_windows_keeps_the_declared_types(backend_name: str, struct_name: str, source: str) -> None:
    """Windows is a third layout rule, and ctypes already implements it there.

    MSVC opens a fresh storage unit whenever a bit-field's declared type
    differs from the unit in play, so ``struct { unsigned char a; unsigned int
    : 8; }`` is 8 bytes on Windows against 4 under AAPCS64 and 2 under System
    V -- measured on the CI runner with MinGW ``cc``, ``gcc``, ``clang`` and
    ``clang`` targeting MSVC, which all agreed. ctypes on Windows uses that
    same algorithm, so the declared types reproduce the C compiler there on
    their own; respelling them for another ABI's rule is what would break it.

    Only the spelling is asserted, not the resulting numbers: reproducing them
    needs the MSVC engine, which exists only on a Windows host.
    """
    record = _exec_as(_generate(backend_name, source), system="win32", machine="AMD64")[struct_name]

    names = [entry[0] for entry in record._fields_]  # type: ignore[attr-defined]
    assert not [n for n in names if n.startswith("_hk_align") or n.startswith("_hk_pad_align")], (
        f"{struct_name} carries an alignment field on Windows, where the declared types already align it"
    )
    carriers = _bitfield_carriers(record)  # type: ignore[arg-type]
    assert any(ctypes.sizeof(c) > 1 for c in carriers), (
        f"{struct_name} narrowed a carrier on Windows, which changes where MSVC puts the field"
    )
