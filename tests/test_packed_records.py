"""Packed-record support, measured against a compiled C probe.

Every layout figure asserted here was produced by compiling the corresponding
record with the host C compiler and reading the bits back out of a live object:
each field is set to all ones in an otherwise zeroed record and the set bits are
scanned. The expected values are literals so that a change in the writer or a
backend moves a test rather than silently moving the expectation with it.

Ground truth was taken on macOS arm64 (Apple clang, LP64). The records were
chosen so that every asserted figure is fixed by the Itanium C++ ABI's rules for
packing and bit-field allocation rather than by anything host-specific: all
member types are ``unsigned char``/``unsigned short``/``unsigned int``, whose
sizes are the same on every platform the project supports.
"""

import ctypes
import re
import sys
import warnings

import pytest

from headerkit.backends import get_backend
from headerkit.ir import Struct
from headerkit.writers import get_writer

#: CPython before 3.14 opens a fresh storage unit whenever a bit-field's
#: declared type differs in size from the unit it would otherwise land in,
#: where C keeps packing while the field still fits. The writer's ABI branch
#: covers the shapes it can, but a record mixing a narrow bit-field, an
#: anonymous padding bit-field and a wider bit-field is not one of them.
_BITFIELD_UNIT_RULE_MATCHES_C = sys.version_info >= (3, 14)


def _unnamed_bitfields_align_here() -> bool:
    """Whether the *host's C ABI* gives an unnamed bit-field its type's alignment.

    Asked of clang rather than guessed from the platform triple. The figures in
    the layout corpus below were measured under the Itanium C++ ABI, where it
    does not; on a target where it does, C itself lays those records out
    differently and the corpus would be measuring a different language.

    The backend already answers this by compiling a probe, which is the whole
    point of the change these tests cover -- restating it here as a triple
    heuristic would leave the repository asserting an ABI rule in one place and
    measuring it in another. False if the probe cannot be run at all, which
    matches what the backend then assumes.
    """
    backend = get_backend("libclang")
    if not backend.is_available():
        return False
    backend.parse("struct _hk_configure { int a; };", "rec.h")
    from headerkit.backends.libclang import _unnamed_bitfields_impose_alignment

    return _unnamed_bitfields_impose_alignment((), False) is True


BACKENDS = ["libclang", "tree-sitter"]


def _parse(backend_name: str, code: str, extra_args: list[str] | None = None) -> list[Struct]:
    backend = get_backend(backend_name)
    if not backend.is_available():
        pytest.skip(f"{backend_name} backend unavailable")
    unit = backend.parse(code, "rec.h", extra_args=extra_args)
    return [d for d in unit.declarations if isinstance(d, Struct)]


def _only(backend_name: str, code: str, name: str, extra_args: list[str] | None = None) -> Struct:
    records = {r.name: r for r in _parse(backend_name, code, extra_args)}
    assert name in records, f"{backend_name} did not emit {name}: {sorted(records)}"
    return records[name]


# ---------------------------------------------------------------------------
# Backends populate is_packed
# ---------------------------------------------------------------------------

# (label, source, expected is_packed) -- the record is always named ``S``.
_PACKED_CASES = [
    (
        "prefix-attribute",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int b; unsigned char c; };",
        True,
    ),
    (
        "suffix-attribute",
        "struct S { unsigned char a; unsigned int b; unsigned char c; } __attribute__((packed));",
        True,
    ),
    (
        "no-attribute",
        "struct S { unsigned char a; unsigned int b; unsigned char c; };",
        False,
    ),
    (
        "pragma-pack-1",
        "#pragma pack(1)\nstruct S { unsigned char a; unsigned int b; unsigned char c; };\n#pragma pack()\n",
        True,
    ),
    (
        "pragma-pack-push-pop",
        "#pragma pack(push, 1)\nstruct S { unsigned char a; unsigned int b; unsigned char c; };\n#pragma pack(pop)\n",
        True,
    ),
    (
        "after-pragma-pack-reset",
        "#pragma pack(1)\nstruct T { unsigned char x; };\n#pragma pack()\n"
        "struct S { unsigned char a; unsigned int b; unsigned char c; };\n",
        False,
    ),
    (
        "after-pragma-pack-pop",
        "#pragma pack(push, 1)\nstruct T { unsigned char x; };\n#pragma pack(pop)\n"
        "struct S { unsigned char a; unsigned int b; unsigned char c; };\n",
        False,
    ),
    (
        "aligned-attribute-is-not-packing",
        "struct __attribute__((aligned(16))) S { unsigned char a; unsigned int b; unsigned char c; };",
        False,
    ),
    (
        "packed-and-aligned-together",
        "struct __attribute__((packed, aligned(16))) S { unsigned char a; unsigned int b; unsigned char c; };",
        True,
    ),
    (
        "packed-with-named-bitfield",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 8; unsigned char c; };",
        True,
    ),
    (
        "packed-with-padding-bitfield",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int : 8; unsigned char b; };",
        True,
    ),
    (
        "unpacked-with-padding-bitfield",
        # Under the Itanium C++ ABI an anonymous bit-field does not contribute
        # its type's alignment, so this record is already byte-aligned without
        # being packed and reading alignment alone would report it packed.
        # AAPCS64 does impose it, and there the record aligns to 4 -- either
        # way it is unpacked; see the target-parametrized test below.
        "struct S { unsigned char a; unsigned int : 8; unsigned char b; };",
        False,
    ),
    (
        "suffix-attribute-on-union",
        "union S { unsigned char a; unsigned int b; } __attribute__((packed));",
        True,
    ),
    (
        "pragma-pack-on-union",
        "#pragma pack(1)\nunion S { unsigned char a; unsigned int b; };\n#pragma pack()\n",
        True,
    ),
]


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    ("source", "expected"),
    [(case[1], case[2]) for case in _PACKED_CASES],
    ids=[case[0] for case in _PACKED_CASES],
)
def test_backend_sets_is_packed(backend_name: str, source: str, expected: bool) -> None:
    assert _only(backend_name, source, "S").is_packed is expected


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_aligned_attribute_is_recorded_separately_from_packing(backend_name: str) -> None:
    """``aligned(N)`` raises alignment; it does not remove padding.

    A compiled C probe puts this record at size 16 alignment 16 with ``b`` still
    at byte 4 -- byte for byte the unpacked layout, only over-aligned. Reporting
    it as packed would claim ``b`` had moved to byte 1.
    """
    record = _only(
        backend_name,
        "struct __attribute__((aligned(16))) S { unsigned char a; unsigned int b; unsigned char c; };",
        "S",
    )
    assert record.is_packed is False


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_an_inexpressible_intermediate_pragma_pack_is_noted(backend_name: str) -> None:
    """``#pragma pack(2)`` squeezes a record without flattening it.

    ``is_packed`` is a boolean, and re-emitting ``__attribute__((packed))``
    would understate the offsets: a C probe puts this record at size 8 with
    ``b`` at byte 2, where a fully packed record would put ``b`` at byte 1.
    The fact is kept in ``notes`` rather than reported wrongly or dropped --
    and by both backends, so the same header does not yield two different IRs.
    """
    record = _only(
        backend_name,
        "#pragma pack(2)\nstruct S { unsigned char a; unsigned int b; unsigned char c; };\n#pragma pack()\n",
        "S",
    )
    assert record.is_packed is False
    assert any("pack(2)" in note for note in record.notes)


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_a_pack_pragma_with_an_msvc_label_still_packs(backend_name: str) -> None:
    """``#pragma pack(push, <label>, N)`` is the MSVC spelling.

    GCC and Clang accept it too, and Windows-targeting headers use it heavily.
    Reading the alignment as the word straight after ``push`` misses it and
    yields an unpacked record from a packed header, silently. Both backends
    must agree: a disagreement here is a defect whichever one is right.
    """
    source = (
        "#pragma pack(push, mylabel, 1)\n"
        "struct S { unsigned char a; unsigned int b; unsigned char c; };\n"
        "#pragma pack(pop, mylabel)\n"
        "struct T { unsigned char a; unsigned int b; unsigned char c; };\n"
    )
    records = {r.name: r for r in _parse(backend_name, source)}
    assert records["S"].is_packed is True
    # The label on ``pop`` must not stop the scope from ending, either.
    assert records["T"].is_packed is False


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_a_pack_pragma_inside_a_conditional_branch_does_not_pack(backend_name: str) -> None:
    """Branches of ``#ifdef`` are mutually exclusive, so none may be applied.

    Walking every branch leaves whichever one was visited last in force, which
    is an alignment no translation of the header ever has. Here the branch is
    not taken at all, so the record is unpacked; the tree-sitter backend cannot
    evaluate the condition and says so in ``notes`` instead of guessing.
    """
    record = _only(
        backend_name,
        "#ifdef HEADERKIT_UNDEFINED_MACRO\n#pragma pack(1)\n#endif\n"
        "struct S { unsigned char a; unsigned int b; unsigned char c; };\n",
        "S",
    )
    assert record.is_packed is False


def test_treesitter_notes_a_pack_pragma_it_could_not_evaluate() -> None:
    """The tree-sitter backend has no preprocessor, so the fact must be visible.

    libclang evaluates the condition and needs no note; this backend skips the
    branch and records that the answer is unverified rather than silently
    reporting the record unpacked.
    """
    record = _only(
        "tree-sitter",
        "#ifdef _MSC_VER\n#pragma pack(push, 1)\n#else\n#pragma pack(4)\n#endif\n"
        "struct S { unsigned char a; unsigned int b; unsigned char c; };\n",
        "S",
    )
    assert any("conditional preprocessor branch" in note for note in record.notes)


def test_a_record_aligned_only_by_a_packed_aggregate_member_is_packed() -> None:
    """Wrapping a family of records in one ``#pragma pack(1)`` is the idiom.

    The inner record is packed first, so it reports an alignment of 1, and a
    container whose widest member is that record has nothing left to detect on
    it. A compiled C probe measures ``S1`` at size 6 alignment 1 where the
    unpacked equivalent is 12 alignment 4, so it is packed and the natural
    figure has to come from the leaf scalars.
    """
    records = {
        r.name: r
        for r in _parse(
            "libclang",
            "#pragma pack(1)\n"
            "struct N1 { unsigned char x; unsigned int y; };\n"
            "struct S1 { unsigned char a; struct N1 n; };\n"
            "#pragma pack()\n",
        )
    }
    assert records["N1"].is_packed is True
    assert records["S1"].is_packed is True


def test_an_unpacked_record_with_an_aggregate_member_is_not_packed() -> None:
    """Negative control for the recursion: no pragma, no packing."""
    records = {
        r.name: r
        for r in _parse(
            "libclang",
            "struct N2 { unsigned char x; unsigned int y; };\nstruct S2 { unsigned char a; struct N2 n; };\n",
        )
    }
    assert records["N2"].is_packed is False
    assert records["S2"].is_packed is False


# (target triple, whether an unnamed bit-field imposes its type's alignment).
# Measured by compiling ``struct { char a; unsigned int : 8; char b; }`` with
# the host clang for each triple: 3/1 under the Itanium C++ ABI, 4/4 under
# AAPCS. The split does not follow the architecture -- aarch64-apple-darwin
# behaves like Itanium and aarch64-linux-gnu does not.
_ANON_BITFIELD_ABI_TARGETS = [
    ("x86_64-linux-gnu", False),
    ("aarch64-apple-darwin", False),
    ("riscv64-linux-gnu", False),
    ("aarch64-linux-gnu", True),
    ("arm-none-eabi", True),
]


@pytest.mark.parametrize(
    ("target", "imposes"), _ANON_BITFIELD_ABI_TARGETS, ids=[t[0] for t in _ANON_BITFIELD_ABI_TARGETS]
)
def test_pragma_pack_with_an_anonymous_bitfield_follows_the_target_abi(target: str, imposes: bool) -> None:
    """Whether the anonymous bit-field's alignment counts is a target property.

    Under AAPCS the packed record measures 3 where the unpacked one measures 4,
    so it really is packed and reporting it unpacked emits members at the wrong
    offsets. Under the Itanium C++ ABI both measure 3 and there is nothing to
    report. Excluding the bit-field unconditionally is right on one and a false
    negative on the other, and both are supported CI platforms.
    """
    args = ["-target", target]
    packed = _only(
        "libclang",
        "#pragma pack(1)\nstruct S { char a; unsigned int : 8; char b; };\n#pragma pack()\n",
        "S",
        extra_args=args,
    )
    unpacked = _only("libclang", "struct S { char a; unsigned int : 8; char b; };", "S", extra_args=args)
    assert packed.is_packed is imposes
    # The unpacked record must never be reported packed on either ABI: that is
    # the false positive excluding the bit-field was introduced to prevent.
    assert unpacked.is_packed is False


def test_the_abi_probe_reports_when_it_could_not_be_measured() -> None:
    """A failed probe must be distinguishable from a measured "no".

    Both were False before, and the result is cached for the life of the
    process, so one clang that would not accept the arguments pinned the
    Itanium answer on an AAPCS target for every later parse.
    """
    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    backend.parse("struct X { int a; };", "rec.h")  # configures libclang
    from headerkit.backends.libclang import _unnamed_bitfields_impose_alignment

    assert _unnamed_bitfields_impose_alignment(("-target", "aarch64-linux-gnu"), False) is True
    assert _unnamed_bitfields_impose_alignment(("-target", "x86_64-linux-gnu"), False) is False
    # Arguments clang rejects outright: not measured, and reported as such.
    assert _unnamed_bitfields_impose_alignment(("-std=c++99",), False) is None
    # And a translation unit that parses but does not compile -- a forced
    # include that is not there is the ordinary way a caller produces one.
    assert _unnamed_bitfields_impose_alignment(("-include", "/nonexistent/hk_missing.h"), False) is None


def test_a_record_whose_abi_question_went_unmeasured_says_so() -> None:
    """The note fires only where the unmeasured answer could have changed the result.

    The probe is broken here by defining away its own tag name, which leaves
    the caller's header parsing perfectly well -- so this exercises the real
    path rather than a patched function, and cannot be defeated by another test
    warming the probe's cache first.
    """
    records = {
        r.name: r
        for r in _parse(
            "libclang",
            "struct WithAnon { char a; unsigned int : 8; char b; };\nstruct Plain { char a; unsigned int b; };\n",
            ["-D_hk_abi_probe=0"],
        )
    }
    assert any("could not be asked" in note for note in records["WithAnon"].notes)
    # A record with no unnamed bit-field is unaffected by the question, so a
    # note there would be noise rather than information.
    assert not records["Plain"].notes


def test_the_conditional_pack_note_stops_at_the_next_unconditional_pragma() -> None:
    """The doubt covers the records it can affect, not the rest of the file.

    ``#ifdef _MSC_VER / #pragma pack(push, 1) / #endif`` guards are ordinary,
    and running the note to end of file puts it on every later record in such a
    header -- which trains the reader to ignore it. The next pragma that states
    the pack state outright ends the uncertainty, because from there the
    alignment is the same whichever branch the preprocessor took.
    """
    source = (
        "#ifdef _MSC_VER\n#pragma pack(push, 1)\n#endif\n"
        "struct Inside { unsigned char a; unsigned int b; };\n"
        "#pragma pack(pop)\n"
        "struct After { unsigned char a; unsigned int b; };\n"
    )
    records = {r.name: r for r in _parse("tree-sitter", source)}
    assert any("conditional preprocessor branch" in note for note in records["Inside"].notes)
    assert not any("conditional preprocessor branch" in note for note in records["After"].notes)


def _emitted_fields(source: str, class_name: str = "S") -> list[tuple[str, str, int | None]]:
    """The ``_fields_`` tuples the writer emits for one class.

    Read back from the generated module rather than from its text: ``_fields_``
    is a real list of tuples once the class body has run, so this is the
    spelling itself and not a re-parse of it.
    """
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    cls = namespace[class_name]
    return [(f[0], f[1].__name__, f[2] if len(f) > 2 else None) for f in cls._fields_]  # type: ignore[union-attr]


def test_packed_padding_is_respelled_from_its_c_offset_not_the_unpacked_one() -> None:
    """A packed unnamed bit-field reserves exactly the bits it declares.

    ``unsigned int : 8`` reserves 8 bits in a packed record, whatever storage
    unit its declared type would have opened. It lands at bit 20 here, because
    ``b`` ends there under packing -- measured with a compiled C probe, which
    puts ``c`` at bit 32 and the record at 5 bytes. Reserving the span the
    *unpacked* offset implies would take 12 bits instead of 8, and starting the
    span at the unpacked offset would put it at bit 24 instead of 20.

    Both errors are invisible in the resulting field offsets, because the next
    plain member rounds to the same byte either way. What they do change is the
    byte-granular chunks the padding is spelled in: a span of 8 bits from bit
    20 is ``[4, 4]``, from bit 24 it is ``[8]``, and 12 bits from bit 20 is
    ``[4, 8]``. So the chunk widths are the assertion.
    """
    source = (
        "struct __attribute__((packed)) S { unsigned short a : 12; unsigned short b : 8;"
        " unsigned int : 8; unsigned char c; };"
    )
    emitted = _emitted_fields(source)
    pad_widths = [width for name, _expr, width in emitted if name.startswith("_pad")]
    assert pad_widths == [4, 4]
    assert all(expr == "c_ubyte" for name, expr, _w in emitted if name.startswith("_pad"))


def test_the_emitted_spelling_transcribes_the_c_packed_offsets() -> None:
    """Walking the emitted tuples under C's packed rules must reproduce C.

    This is the writer's *intent*, independent of what ctypes then does with
    the spelling: a bit-field starts at the very next bit, a plain member
    rounds to a byte. Every row of both corpora is checked, so a width or a
    padding span that stops matching C is caught even on a record ctypes
    cannot place anyway.
    """
    rows = [(case[1], case[4]) for case in _UNFAITHFUL_PACKED_CASES]
    rows += [
        (case[1], case[4]) for case in _LAYOUT_CASES if not hasattr(case, "values") and case[0].startswith("packed")
    ]
    assert len(rows) == len(_UNFAITHFUL_PACKED_CASES) + 4, "corpus rows changed; update the expected count"
    for source, c_fields in rows:
        offsets: dict[str, int] = {}
        position = 0
        for name, expr, width in _emitted_fields(source):
            if width is None:
                position = ((position + 7) // 8) * 8
                offsets[name] = position
                position += ctypes.sizeof(getattr(ctypes, expr)) * 8
            else:
                offsets[name] = position
                position += width
        for name, (first_bit, _last, _count) in c_fields.items():
            assert offsets[name] == first_bit, f"{source}: {name} at {offsets[name]}, C says {first_bit}"


def test_a_bitfield_is_narrowed_only_when_it_fits_the_byte_its_c_offset_lies_in() -> None:
    """Narrowing is what makes ctypes follow C; it is unsound anywhere else.

    A one-byte carrier removes the disagreement between C and ctypes only while
    the field fits inside the byte C put it in. Past that boundary C itself
    would have moved the field on, and a byte carrier cannot follow, so the
    declared type has to stand. Reading the *unpacked* running offset to make
    this decision narrows fields C placed mid-byte -- ``unsigned short c : 8``
    at bit 20 in the third row below, which no byte can hold.

    Checked across every packed corpus row, against the C offsets those rows
    were measured at.
    """
    from headerkit.writers.ctypes import type_to_ctypes

    rows = [(case[1], case[4]) for case in _UNFAITHFUL_PACKED_CASES]
    rows += [
        (case[1], case[4]) for case in _LAYOUT_CASES if not hasattr(case, "values") and case[0].startswith("packed")
    ]
    narrowed_seen = 0
    for source, c_fields in rows:
        emitted = {name: expr for name, expr, width in _emitted_fields(source) if width is not None}
        for field in _only("libclang", source, "S").fields:
            if field.bit_width is None or not field.name or field.name not in emitted:
                continue
            declared = type_to_ctypes(field.type).removeprefix("ctypes.")
            # Narrower, not merely spelled differently. ``unsigned int`` renders
            # as ``c_ulong`` on Windows and ``c_uint`` elsewhere; both are four
            # bytes, and treating that as narrowing reads a rename as a
            # decision the writer never made.
            if ctypes.sizeof(getattr(ctypes, emitted[field.name])) >= ctypes.sizeof(getattr(ctypes, declared)):
                continue
            narrowed_seen += 1
            first_bit = c_fields[field.name][0]
            assert first_bit % 8 + field.bit_width <= 8, (
                f"{source}: {field.name} narrowed to {emitted[field.name]} but C puts it at bit {first_bit}"
            )
    assert narrowed_seen, "no field was narrowed; the invariant above was never exercised"


def test_treesitter_notes_nothing_when_no_pack_pragma_is_conditional() -> None:
    """Negative control: the note must not fire on an unconditional header."""
    record = _only(
        "tree-sitter",
        "#pragma pack(1)\nstruct S { unsigned char a; unsigned int b; unsigned char c; };\n#pragma pack()\n",
        "S",
    )
    assert record.is_packed is True
    assert not any("conditional preprocessor branch" in note for note in record.notes)


# ---------------------------------------------------------------------------
# The generated ctypes classes reproduce the C layout
# ---------------------------------------------------------------------------


def _build(source: str) -> dict[str, type[ctypes.Structure]]:
    """Parse ``source``, generate ctypes bindings from the IR, and load them."""
    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    code = get_writer("ctypes").write(backend.parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    return {
        k: v for k, v in namespace.items() if isinstance(v, type) and issubclass(v, ctypes.Structure | ctypes.Union)
    }


def _field_bits(cls: type, size: int, name: str) -> tuple[int, int, int]:
    """Return (first set bit, last set bit, number of set bits) for one field.

    This is the same measurement the C probe takes: set the field to all ones in
    an otherwise zeroed record and scan. Comparing bit positions rather than
    ``offsetof`` is what makes the check meaningful for bit-fields, which have
    no byte offset of their own.
    """
    obj = cls()
    ctypes.memset(ctypes.byref(obj), 0, size)
    setattr(obj, name, (1 << 64) - 1)
    buf = (ctypes.c_ubyte * size).from_buffer(obj)
    first = last = -1
    count = 0
    for index in range(size):
        for bit in range(8):
            if buf[index] & (1 << bit):
                position = index * 8 + bit
                if first < 0:
                    first = position
                last = position
                count += 1
    del buf
    return first, last, count


# Each row: label, source, record name, C sizeof, C alignof,
# {field: (first bit, last bit, width)} -- all measured with a compiled C probe.
_LAYOUT_CASES = [
    (
        "packed-plain-members",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int b; unsigned char c; };",
        6,
        1,
        {"a": (0, 7, 8), "b": (8, 39, 32), "c": (40, 47, 8)},
    ),
    (
        "unpacked-plain-members",
        "struct S { unsigned char a; unsigned int b; unsigned char c; };",
        12,
        4,
        {"a": (0, 7, 8), "b": (32, 63, 32), "c": (64, 71, 8)},
    ),
    (
        "packed-named-bitfield",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 8; unsigned char c; };",
        3,
        1,
        {"a": (0, 7, 8), "b": (8, 15, 8), "c": (16, 23, 8)},
    ),
    (
        "packed-padding-bitfield",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int : 8; unsigned char b; };",
        3,
        1,
        {"a": (0, 7, 8), "b": (16, 23, 8)},
    ),
    (
        "packed-mixed-plain-named-and-padding",
        "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 5; unsigned int : 3;"
        " unsigned short c : 9; unsigned int d; unsigned char e; };",
        9,
        1,
        {"a": (0, 7, 8), "b": (8, 12, 5), "c": (16, 24, 9), "d": (32, 63, 32), "e": (64, 71, 8)},
    ),
    pytest.param(
        "unpacked-mixed-plain-named-and-padding",
        "struct S { unsigned char a; unsigned int b : 5; unsigned int : 3;"
        " unsigned short c : 9; unsigned int d; unsigned char e; };",
        12,
        4,
        {"a": (0, 7, 8), "b": (8, 12, 5), "c": (16, 24, 9), "d": (32, 63, 32), "e": (64, 71, 8)},
        marks=pytest.mark.xfail(
            not _BITFIELD_UNIT_RULE_MATCHES_C or _unnamed_bitfields_align_here(),
            reason=(
                "Pre-existing and unrelated to packing: this record is not packed. "
                "On CPython 3.10 the generated class measures 16 bytes where C measures "
                "12, because ctypes gives the unsigned short bit-field a fresh storage "
                "unit. Verified against the writer as it stood before packed-record "
                "support was added, which produces the same 16 on 3.10. "
                "The row is also inapplicable wherever an unnamed bit-field contributes "
                "its type's alignment -- Windows and AAPCS64 -- because there C lays "
                "this record out differently from the 12 bytes measured for it under "
                "the Itanium C++ ABI, so the expectation is not the host's C at all. "
                "Every other row of this corpus is fixed across those ABIs; this is "
                "the only one carrying an unnamed bit-field in an unpacked record, "
                "which is exactly the case they disagree on."
            ),
            strict=True,
        ),
    ),
    (
        "pragma-packed-named-bitfield",
        "#pragma pack(1)\nstruct S { unsigned char a; unsigned int b : 8; unsigned char c; };\n#pragma pack()\n",
        3,
        1,
        {"a": (0, 7, 8), "b": (8, 15, 8), "c": (16, 23, 8)},
    ),
]


@pytest.mark.parametrize(
    ("label", "source", "c_sizeof", "c_alignof", "c_fields"),
    _LAYOUT_CASES,
    ids=[case.values[0] if hasattr(case, "values") else case[0] for case in _LAYOUT_CASES],
)
def test_generated_ctypes_matches_c_layout(
    label: str,
    source: str,
    c_sizeof: int,
    c_alignof: int,
    c_fields: dict[str, tuple[int, int, int]],
) -> None:
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    assert "HEADERKIT: packed record" not in code, f"{label} is reproducible but was flagged"

    cls = _build(source)["S"]
    assert ctypes.sizeof(cls) == c_sizeof
    assert ctypes.alignment(cls) == c_alignof
    for name, expected in c_fields.items():
        assert _field_bits(cls, c_sizeof, name) == expected, f"{label}.{name}"


def test_packed_bitfield_uses_a_byte_carrier_not_the_declared_type() -> None:
    """The mechanism behind ``test_generated_ctypes_matches_c_layout``.

    ``_pack_ = 1`` pins the record's alignment but leaves ctypes allocating a
    full storage unit for a bit-field declared ``unsigned int``. Only a carrier
    no wider than the bits in play makes ctypes follow C.
    """
    code = get_writer("ctypes").write(
        get_backend("libclang").parse(
            "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 8; unsigned char c; };",
            "rec.h",
        )
    )
    assert '("b", ctypes.c_ubyte, 8)' in code
    assert '("b", ctypes.c_uint, 8)' not in code


# Packed shapes ctypes cannot reproduce at all. Each row: label, source, C
# sizeof, C alignof, {field: (first bit, last bit, width)} -- all measured with
# a compiled C probe, on the same terms as ``_LAYOUT_CASES``. Which field the
# diagnostic names is not a column: the writer measures the interpreter it runs
# on, and the three ctypes engines misplace different members of one record.
#
# The generated size is deliberately absent. It is not a property of the
# header: measured on macOS arm64, ``short12-char4-int20`` generates 6 bytes on
# CPython 3.10 and 3.13 and 7 on 3.14. What every version agrees on is that the
# generated class does not reproduce C, which is what the row asserts.
_UNFAITHFUL_PACKED_CASES = [
    (
        "wide-bitfield-starting-mid-byte",
        "struct __attribute__((packed)) S { unsigned int a : 4; unsigned int b : 30; unsigned char c; };",
        6,
        1,
        {"a": (0, 3, 4), "b": (4, 33, 30), "c": (40, 47, 8)},
    ),
    (
        "narrow-carrier-after-a-bitfield-that-crossed-its-unit",
        "struct __attribute__((packed)) S { unsigned short a : 12; unsigned char b : 4; unsigned int c : 20; };",
        5,
        1,
        {"a": (0, 11, 12), "b": (12, 15, 4), "c": (16, 35, 20)},
    ),
    (
        "narrow-carrier-then-plain-member",
        "struct __attribute__((packed)) S { unsigned short a : 12; unsigned char b : 4; unsigned char c; };",
        3,
        1,
        {"a": (0, 11, 12), "b": (12, 15, 4), "c": (16, 23, 8)},
    ),
    (
        "byte-carriers-straddling-a-byte",
        "struct __attribute__((packed)) S { unsigned char a : 3; unsigned char b : 6; unsigned char c; };",
        3,
        1,
        {"a": (0, 2, 3), "b": (3, 8, 6), "c": (16, 23, 8)},
    ),
    (
        # The three rows below are the ones where C's packed offset and the
        # unpacked running offset actually part company: ``b`` crosses the
        # storage unit its declared type would have opened, so the unpacked
        # offset rounds and C's does not. A shape where the two agree cannot
        # tell a packed-offset defect from a correct one however it is
        # asserted.
        "bitfield-crossing-its-declared-unit",
        "struct __attribute__((packed)) S { unsigned short a : 12; unsigned short b : 8; unsigned short c : 8; };",
        4,
        1,
        {"a": (0, 11, 12), "b": (12, 19, 8), "c": (20, 27, 8)},
    ),
    (
        "bitfield-crossing-its-declared-unit-then-padding",
        "struct __attribute__((packed)) S { unsigned short a : 12; unsigned short b : 8;"
        " unsigned int : 8; unsigned char c; };",
        5,
        1,
        {"a": (0, 11, 12), "b": (12, 19, 8), "c": (32, 39, 8)},
    ),
    (
        "bitfield-crossing-a-32-bit-unit",
        "struct __attribute__((packed)) S { unsigned int a : 30; unsigned int b : 8; unsigned int c : 8; };",
        6,
        1,
        {"a": (0, 29, 30), "b": (30, 37, 8), "c": (38, 45, 8)},
    ),
    (
        "plain-member-after-a-part-filled-storage-unit",
        "struct __attribute__((packed)) S { unsigned int a : 12; unsigned int b : 12; unsigned char c; };",
        4,
        1,
        {"a": (0, 11, 12), "b": (12, 23, 12), "c": (24, 31, 8)},
    ),
]


@pytest.mark.parametrize(
    ("label", "source", "c_sizeof", "c_alignof", "c_fields"),
    _UNFAITHFUL_PACKED_CASES,
    ids=[case[0] for case in _UNFAITHFUL_PACKED_CASES],
)
def test_an_unreproducible_packed_record_is_flagged_not_emitted_silently(
    label: str,
    source: str,
    c_sizeof: int,
    c_alignof: int,
    c_fields: dict[str, tuple[int, int, int]],
) -> None:
    """A packed record ctypes cannot place must say so, and say something true.

    Three assertions, and the last two keep the first honest. The record has to
    be named in ``HEADERKIT_UNVERIFIED_RECORDS``; the generated class has to
    genuinely disagree with C, or a detector that flagged everything would
    pass; and the field the diagnostic names has to be one whose placement
    really is wrong, or the flag could be right by accident while pointing at
    an innocent member.

    Which field is named is deliberately not pinned. The writer measures the
    interpreter it runs on, and the three supported ctypes engines misplace
    different members of the same record, so a fixed name would be asserting
    the interpreter rather than the writer.
    """
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    assert "S" in namespace.get("HEADERKIT_UNVERIFIED_RECORDS", ()), label

    cls = _build(source)["S"]
    generated = (ctypes.sizeof(cls), ctypes.alignment(cls)) + tuple(
        _field_bits(cls, ctypes.sizeof(cls), name) for name in c_fields
    )
    measured = (c_sizeof, c_alignof) + tuple(c_fields[name] for name in c_fields)
    assert generated != measured, f"{label} is reproducible after all; it does not belong in this corpus"

    named = re.search(r"places '(\w+)' somewhere\n\s*# other than bit (\d+)", code)
    assert named is not None, f"{label}: no field named in the diagnostic:\n{code}"
    field, claimed_bit = named.group(1), int(named.group(2))
    if field in c_fields:
        assert claimed_bit == c_fields[field][0], f"{label}: diagnostic misquotes C's offset for {field}"
        assert _field_bits(cls, ctypes.sizeof(cls), field) != c_fields[field], (
            f"{label}: diagnostic names {field}, but that field is placed correctly"
        )


# Packed unions. Each row: label, source, C sizeof, C alignof -- measured with
# a compiled C probe, which puts every member of a union at bit 0 and makes the
# union as wide as its widest member.
_UNION_CASES = [
    ("two-byte-bitfields", "union __attribute__((packed)) S { unsigned char f0 : 3; unsigned char f1 : 3; };", 1, 1),
    ("byte-then-int-bitfield", "union __attribute__((packed)) S { unsigned char f0 : 3; unsigned int f1 : 4; };", 1, 1),
    (
        "short-then-int-bitfield",
        "union __attribute__((packed)) S { unsigned short f0 : 9; unsigned int f1 : 20; };",
        3,
        1,
    ),
]


@pytest.mark.parametrize(
    ("label", "source", "c_sizeof", "c_alignof"),
    _UNION_CASES,
    ids=[case[0] for case in _UNION_CASES],
)
def test_a_packed_union_is_reproduced_or_flagged_never_silently_wrong(
    label: str, source: str, c_sizeof: int, c_alignof: int
) -> None:
    """The invariant for a record the writer may not be able to place.

    Not "the union is correct": ctypes places a union's second bit-field
    correctly only from CPython 3.14, so on 3.10 through 3.13 no spelling of
    these is right. What must hold on every interpreter is that a union is
    either laid out as C lays it out or named in
    ``HEADERKIT_UNVERIFIED_RECORDS``. Silently wrong is the one outcome ruled
    out, and it is what this branch made reachable by setting ``is_packed`` on
    a union for the first time.

    Field positions are read off the class rather than by writing to it.
    Assigning to a misplaced union bit-field writes outside the object on 3.10
    and 3.13 and takes the interpreter down at the next collection.
    """
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    cls = namespace["S"]
    flagged = "S" in namespace.get("HEADERKIT_UNVERIFIED_RECORDS", ())

    starts = {name: getattr(cls, name).offset * 8 + (getattr(cls, name).size & 0xFFFF) for name, *_ in cls._fields_}
    correct = (
        ctypes.sizeof(cls) == c_sizeof and ctypes.alignment(cls) == c_alignof and all(b == 0 for b in starts.values())
    )
    assert correct or flagged, f"{label}: laid out as {ctypes.sizeof(cls)}B {starts}, C says {c_sizeof}B all at bit 0"
    if not correct:
        # The reader of the module gets the reason beside the class, not only
        # a name in a tuple at the bottom of the file.
        assert "# HEADERKIT: packed record S" in code, label


def test_a_union_ctypes_places_correctly_is_not_flagged() -> None:
    """Negative control for the union path: a single-member union is expressible."""
    code = get_writer("ctypes").write(
        get_backend("libclang").parse("union __attribute__((packed)) S { unsigned char f0 : 3; };", "rec.h")
    )
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    assert "S" not in namespace.get("HEADERKIT_UNVERIFIED_RECORDS", ())


def test_padding_after_an_untrackable_member_is_reserved_not_dropped() -> None:
    """Losing a padding member silently moves every field after it.

    A function-pointer member has no size the writer can read, so the running
    bit offset stops there. That is a reason not to know how to *spell* the
    following padding, not a reason to reserve nothing: dropping the entry
    deletes the bits from the record and moves every member after them. A
    compiled C probe puts this record at 10 bytes with ``b`` at byte 9.

    The writer's reason for not knowing the offset travels with the record too,
    rather than being assigned to a field the packed branch never renders.
    """
    source = "struct __attribute__((packed)) S { void (*fn)(int); unsigned int : 8; unsigned char b; };"
    emitted = _emitted_fields(source)
    pad_widths = [width for name, _expr, width in emitted if name.startswith("_pad")]
    assert sum(pad_widths) == 8, f"padding not reserved: {emitted}"

    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    cls = namespace["S"]
    assert ctypes.sizeof(cls) == 10
    assert cls.b.offset == 9
    assert "The writer also reports:" in code


def test_a_record_whose_layout_cannot_be_judged_is_reported_not_assumed_good() -> None:
    """Unjudgeable is not the same as reproduced.

    The nested class's size is not readable from the ctypes scalar table, so
    the writer cannot check this record against C at all. Saying nothing would
    put it in the same category as a record that was checked and passed.
    """
    # A function-pointer member: valid Python, but no size the writer can
    # obtain, so the record genuinely cannot be checked. An array or a nested
    # record is *not* such a case -- both are sized and verified.
    source = "struct __attribute__((packed)) S { unsigned char a; void (*fn)(int); unsigned char b; };"
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    assert "S" in namespace.get("HEADERKIT_UNVERIFIED_RECORDS", ())
    assert "checked against C at all" in code
    # The writer's own reason for not knowing the offset travels with the
    # record, rather than being assigned to a field the packed branch never
    # renders.


# Packed records whose members are not plain scalars. Each row: label, source,
# C sizeof, C alignof, {field: byte offset} -- measured with a compiled C probe.
# These are the member shapes real packed headers are made of: network and
# file-format structs are mostly byte arrays and nested records.
_AGGREGATE_MEMBER_CASES = [
    (
        "byte-array",
        "struct __attribute__((packed)) S { unsigned char a; unsigned char data[16]; unsigned int b; };",
        21,
        1,
        {"a": 0, "data": 1, "b": 17},
    ),
    (
        "two-dimensional-array",
        "struct __attribute__((packed)) S { unsigned char a; unsigned char m[4][4]; unsigned int b; };",
        21,
        1,
        {"a": 0, "m": 1, "b": 17},
    ),
    (
        "nested-record-with-bitfield",
        "struct __attribute__((packed)) S { struct { unsigned char x : 3; }; unsigned int : 8; unsigned char b; };",
        3,
        1,
        {"b": 2},
    ),
    (
        "nested-record-with-plain-members",
        "struct __attribute__((packed)) S { unsigned char a; struct { unsigned char x; unsigned int y; };"
        " unsigned char b; };",
        10,
        1,
        {"a": 0, "x": 1, "y": 5, "b": 9},
    ),
]


@pytest.mark.parametrize(
    ("label", "source", "c_sizeof", "c_alignof", "c_offsets"),
    _AGGREGATE_MEMBER_CASES,
    ids=[case[0] for case in _AGGREGATE_MEMBER_CASES],
)
def test_a_packed_record_with_aggregate_members_is_reproduced_and_not_flagged(
    label: str, source: str, c_sizeof: int, c_alignof: int, c_offsets: dict[str, int]
) -> None:
    """An array or a nested record is sized, not treated as unmeasurable.

    ``ctypes.sizeof(ctypes.c_ubyte * 16)`` is exactly knowable, and a nested
    record can be built and sized like any other member, so neither is a reason
    to report a record unverified. Reporting them would cover most packed
    records in real headers -- network and file formats are largely byte arrays
    -- and spend the signal a genuine report depends on.

    Both halves matter: the record has to match C *and* carry no report.
    """
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    cls = namespace["S"]

    assert "S" not in namespace.get("HEADERKIT_UNVERIFIED_RECORDS", ()), f"{label} reproduces C but was reported"
    assert ctypes.sizeof(cls) == c_sizeof, label
    assert ctypes.alignment(cls) == c_alignof, label
    for name, offset in c_offsets.items():
        assert getattr(cls, name).offset == offset, f"{label}.{name}"


def test_a_record_nested_deeper_than_the_alignment_walk_says_so() -> None:
    """Hitting the recursion bound is not the same as finding no alignment.

    Past the bound the walk used to return zero, which left the natural figure
    at 1, the packed test false, and a packed record emitted at the unpacked
    layout with nothing said. A compiled C probe puts the twelve-deep record at
    size 17 alignment 1, against 56 alignment 4 for the same nest without the
    pragma, so it is packed.
    """

    def nest(depth: int) -> str:
        source = "#pragma pack(1)\nstruct L0 { unsigned char x; unsigned int y; };\n"
        for level in range(1, depth + 1):
            source += f"struct L{level} {{ unsigned char a; struct L{level - 1} n; }};\n"
        return source + "#pragma pack()\n"

    within = {r.name: r for r in _parse("libclang", nest(4))}["L4"]
    assert within.is_packed is True
    assert not any("nests aggregates" in note for note in within.notes)

    beyond = {r.name: r for r in _parse("libclang", nest(12))}["L12"]
    assert any("nests aggregates" in note for note in beyond.notes), (
        "a record too deep to resolve must say so rather than be reported unpacked"
    )


def test_unreproducible_records_are_named_in_a_module_level_tuple() -> None:
    """The comment is invisible to importing code; the tuple is not.

    A caller that generated bindings in a build step cannot see a ``#``
    comment. Naming the records in a module attribute puts the fact where a
    consumer's own assertion or startup check can reach it.
    """
    source = (
        "struct __attribute__((packed)) S { unsigned short a : 12; unsigned char b : 4; unsigned char c; };\n"
        "struct __attribute__((packed)) T { unsigned char a; unsigned int b : 8; unsigned char c; };\n"
    )
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    assert namespace["HEADERKIT_UNVERIFIED_RECORDS"] == ("S",)


def test_the_layout_check_runs_on_import_not_at_generation() -> None:
    """The verdict has to be the importing interpreter's, not the writer's.

    ctypes lays packed bit-fields out differently across versions, so a verdict
    computed when the module was written describes the wrong interpreter as
    soon as another one imports it. The module therefore carries C's answer and
    re-derives the verdict itself.

    Asserted by rebuilding the classes against a layout this interpreter does
    *not* produce: the check must notice, whatever the writer concluded.
    """
    source = "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 8; unsigned char c; };"
    code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))

    clean: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), clean)
    assert clean["HEADERKIT_UNVERIFIED_RECORDS"] == ()

    # Same module, but C's recorded answer moved one field along. The check is
    # what must react -- nothing about the class definitions changed.
    moved = code.replace('"c": 16', '"c": 24')
    assert moved != code, "the expected-layout table was not found in the module"
    tampered: dict[str, object] = {}
    exec(compile(moved, "<generated>", "exec"), tampered)
    assert tampered["HEADERKIT_UNVERIFIED_RECORDS"] == ("S",)


def test_a_module_with_no_packed_record_defines_no_tuple() -> None:
    """Negative control: an unpacked module is untouched by the mechanism."""
    code = get_writer("ctypes").write(get_backend("libclang").parse("struct T { unsigned char x; };", "rec.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    assert "HEADERKIT_UNVERIFIED_RECORDS" not in namespace
    assert "_HK_PACKED_EXPECTED" not in namespace


def test_a_packed_record_pins_the_msvc_layout_explicitly() -> None:
    """``_pack_`` alone selects that layout implicitly, which 3.19 will reject.

    From CPython 3.14 ``_pack_`` implies the MSVC memory layout and warns once
    per class that the implicit default is deprecated. Saying ``_layout_``
    outright silences the warning and keeps these records working past 3.19.
    """
    code = get_writer("ctypes").write(get_backend("libclang").parse(_PACKED_SOURCE, "rec.h"))
    assert '_layout_ = "ms"' in code

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        namespace: dict[str, object] = {}
        exec(compile(code, "<generated>", "exec"), namespace)
    assert "S" in namespace


def test_pinning_the_layout_changes_no_layout() -> None:
    """The pin is a statement of the status quo, not a change to it.

    Asserted by laying every corpus record out both ways on this interpreter
    and comparing field for field, rather than by trusting the claim. Below
    3.14 ``_layout_`` is ignored outright, so this is a no-op there; on 3.14 it
    names what ``_pack_`` was already selecting.
    """
    sources = [case[1] for case in _UNFAITHFUL_PACKED_CASES]
    sources += [case[1] for case in _LAYOUT_CASES if not hasattr(case, "values") and case[0].startswith("packed")]
    sources += [case[1] for case in _UNION_CASES]
    assert sources, "no packed corpus rows to compare"

    for source in sources:
        code = get_writer("ctypes").write(get_backend("libclang").parse(source, "rec.h"))
        assert '_layout_ = "ms"' in code, source
        unpinned = code.replace('    _layout_ = "ms"\n', "")
        assert unpinned != code

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pinned_ns: dict[str, object] = {}
            exec(compile(code, "<generated>", "exec"), pinned_ns)
            unpinned_ns: dict[str, object] = {}
            exec(compile(unpinned, "<generated>", "exec"), unpinned_ns)

        pinned, plain = pinned_ns["S"], unpinned_ns["S"]
        assert ctypes.sizeof(pinned) == ctypes.sizeof(plain), source
        assert ctypes.alignment(pinned) == ctypes.alignment(plain), source
        for field in pinned._fields_:
            name = field[0]
            assert getattr(pinned, name).offset == getattr(plain, name).offset, f"{source}: {name} offset"
            assert getattr(pinned, name).size == getattr(plain, name).size, f"{source}: {name} size"


def test_an_expressible_packed_record_carries_no_diagnostic() -> None:
    """Negative control: the diagnostic must not fire on records ctypes can place."""
    code = get_writer("ctypes").write(
        get_backend("libclang").parse(
            "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 8; unsigned char c; };",
            "rec.h",
        )
    )
    assert "HEADERKIT: packed record" not in code


# ---------------------------------------------------------------------------
# Other writers now see is_packed set for the first time
# ---------------------------------------------------------------------------

_PACKED_SOURCE = "struct __attribute__((packed)) S { unsigned char a; unsigned int b; unsigned char c; };"


def _write(writer_name: str, source: str = _PACKED_SOURCE) -> str:
    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    return get_writer(writer_name).write(backend.parse(source, "rec.h"))


def test_cython_writer_marks_a_parsed_packed_record() -> None:
    """Cython's ``cdef extern`` blocks cannot carry the attribute, so the writer
    records the fact in a comment. Reaching this at all is new: before both
    backends set ``is_packed`` the branch was dead for parsed headers."""
    assert "packed struct" in _write("cython")


def test_cffi_writer_tells_the_reader_how_to_get_the_packed_layout() -> None:
    """cffi's cdef parser rejects ``__attribute__((packed))`` in either
    position, so the record cannot be emitted packed. Measured with cffi, the
    unmarked declaration comes back at 12 bytes where C measures 6, and nothing
    reports it -- so the comment must name ``packed=True``."""
    out = _write("cffi")
    assert "HEADERKIT: packed record" in out
    assert "packed=True" in out


def test_lua_writer_emits_the_packed_attribute() -> None:
    """LuaJIT's ffi.cdef *does* accept ``__attribute__((packed))``, unlike
    cffi's, so the attribute belongs in the emitted C text."""
    assert "__attribute__((packed))" in _write("lua")


def test_json_writer_round_trips_is_packed() -> None:
    import json as json_module

    payload = json_module.loads(_write("json"))
    record = next(d for d in payload["declarations"] if d.get("name") == "S")
    assert record["is_packed"] is True


def _nim_pragmas(output: str) -> list[str]:
    """The items of the first Nim ``{. .}`` pragma block in ``output``.

    Reading the block rather than the whole module is what makes the assertion
    about the record: the module also carries a ``header:`` entry naming the
    parsed file, so a substring search for "packed" over the text would pass on
    a header called ``packed.h`` whatever the writer did.
    """
    start = output.index("{.") + 2
    return [item.strip() for item in output[start : output.index(".}", start)].split(",")]


def test_nim_writer_emits_the_packed_pragma() -> None:
    assert "packed" in _nim_pragmas(_write("nim"))


def test_prompt_writer_marks_the_record_packed() -> None:
    assert "__packed" in _write("prompt")


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_unpacked_record_is_not_marked_packed_by_any_writer(backend_name: str) -> None:
    """Negative control across the writers that read ``is_packed``."""
    backend = get_backend(backend_name)
    if not backend.is_available():
        pytest.skip(f"{backend_name} backend unavailable")
    header = backend.parse("struct S { unsigned char a; unsigned int b; unsigned char c; };", "rec.h")
    assert "__attribute__((packed))" not in get_writer("cffi").write(header)
    assert "packed struct S" not in get_writer("cython").write(header)
    assert "_pack_ = 1" not in get_writer("ctypes").write(header)
    assert "packed" not in _nim_pragmas(get_writer("nim").write(header))


def test_diff_writer_reports_a_record_becoming_packed() -> None:
    """``is_packed`` moving is an ABI break, so the diff must name it. This
    comparison was unreachable from parsed headers until both backends set the
    flag: two real headers always compared equal on packing."""
    from headerkit.writers.diff import diff_headers

    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    baseline = backend.parse("struct S { unsigned char a; unsigned int b; unsigned char c; };", "rec.h")
    target = backend.parse(_PACKED_SOURCE, "rec.h")
    report = diff_headers(baseline, target)
    # The entry itself, not ``str(report)``: the report's repr carries the
    # header path, so a substring check against it passes on the filename.
    assert any("packed attribute changed from False to True" in entry.detail for entry in report.entries)


@pytest.mark.xfail(
    reason=(
        "tree-sitter-c admits an attribute between 'struct' and the tag name but not "
        "between 'union' and the tag name: it misparses the whole declaration into a "
        "function_definition containing an ERROR node, so the union never reaches the "
        "record converter. Pre-existing and not specific to packing; the suffix form "
        "and #pragma pack both work for unions."
    ),
    strict=True,
)
def test_treesitter_handles_a_prefix_attribute_on_a_union() -> None:
    backend = get_backend("tree-sitter")
    if not backend.is_available():
        pytest.skip("tree-sitter backend unavailable")
    record = _only("tree-sitter", "union __attribute__((packed)) U { unsigned char a; unsigned int b; };", "U")
    assert record.is_packed is True


def test_packed_struct_str_shows_the_attribute() -> None:
    """``Struct.__str__`` already spelled the attribute; parsing now reaches it."""
    record = _only("libclang", _PACKED_SOURCE, "S")
    assert "__attribute__((packed))" in str(record)
