"""Unnamed-bitfield padding must reach the ctypes writer and no other writer.

The ctypes writer reconstructs record layout itself, so it needs the reserved
bits. Every other writer emits source that a C compiler lays out from the
original declaration; a padding entry in that output would be a spurious member.

The rule lives in ``BaseWriter._prepare``, which each entry point must route
through. A writer that overrides ``write`` and forgets is the failure this
module exists to make loud -- otherwise the omission shows up as a nameless
member in generated source, which no other test would attribute to padding.
"""

from __future__ import annotations

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.ir import CType, Field, Header, Struct
from headerkit.writers import get_writer, list_writers
from tests.skip_policy import BACKEND_INSTALL, missing_toolchain

SOURCE = "struct pad_probe { unsigned int a : 3; unsigned int : 4; unsigned int b : 5; };"

#: The writers that must never see padding: everything except ctypes.
_C_SOURCE_WRITERS = tuple(name for name in list_writers() if name != "ctypes")


def _padding_bearing_header() -> Header:
    """Hand-built IR, so this holds even where no parser backend is installed."""
    return Header(
        path="pad.h",
        declarations=[
            Struct(
                name="pad_probe",
                fields=[
                    Field("a", CType("unsigned int"), bit_width=3),
                    Field("", CType("unsigned int"), bit_width=4, is_padding=True),
                    Field("b", CType("unsigned int"), bit_width=5),
                ],
                is_union=False,
            )
        ],
    )


@pytest.mark.parametrize("writer_name", _C_SOURCE_WRITERS)
def test_padding_does_not_reach_a_c_source_writer(writer_name: str) -> None:
    """Neither entry point may hand padding to a writer that defers layout to C."""
    seen: list[list[Field]] = []
    writer = get_writer(writer_name)

    original = type(writer)._prepare

    def spy(self, unit):  # type: ignore[no-untyped-def]
        prepared = original(self, unit)
        for decl in prepared.declarations:
            if isinstance(decl, Struct):
                seen.append([f for f in decl.fields if f.is_padding])
        return prepared

    type(writer)._prepare = spy  # type: ignore[method-assign]
    try:
        writer.write(_padding_bearing_header())
        writer.write_layout(_padding_bearing_header())
    finally:
        type(writer)._prepare = original  # type: ignore[method-assign]

    assert seen, f"{writer_name} bypassed _prepare entirely"
    assert all(not padding for padding in seen), f"{writer_name} received padding fields"


def test_the_ctypes_writer_is_the_one_writer_that_keeps_padding() -> None:
    """A negative control for the test above: proving absence everywhere is vacuous.

    If ``_prepare`` stripped padding unconditionally the parametrized test would
    still pass while the ctypes layout silently reverted to the defective one.
    """
    writer = get_writer("ctypes")

    prepared = writer._prepare(_padding_bearing_header())

    struct = prepared.declarations[0]
    assert isinstance(struct, Struct)
    assert [f.bit_width for f in struct.fields if f.is_padding] == [4]


@pytest.mark.parametrize("writer_name", _C_SOURCE_WRITERS)
@pytest.mark.parametrize("backend_name", ["libclang", "tree-sitter"])
def test_generated_source_gains_no_nameless_member(writer_name: str, backend_name: str) -> None:
    """End of the pipeline, not the middle: the emitted text must be unchanged.

    ``_prepare`` could be routed correctly and a writer still render padding from
    a nested record it reaches by another path, so the output itself is checked.
    """
    if not is_backend_available(backend_name):
        missing_toolchain(f"the {backend_name} backend is not available", BACKEND_INSTALL[backend_name])
    unit = get_backend(backend_name).parse(SOURCE, "pad.h")

    output = get_writer(writer_name).write(unit)

    assert "_pad" not in output
    for marker in ('""', "''"):
        assert f"({marker}," not in output
