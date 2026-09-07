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

import pytest

from headerkit.backends import get_backend
from headerkit.ir import Struct
from headerkit.writers import get_writer

BACKENDS = ["libclang", "tree-sitter"]


def _parse(backend_name: str, code: str) -> list[Struct]:
    backend = get_backend(backend_name)
    if not backend.is_available():
        pytest.skip(f"{backend_name} backend unavailable")
    unit = backend.parse(code, "packed.h")
    return [d for d in unit.declarations if isinstance(d, Struct)]


def _only(backend_name: str, code: str, name: str) -> Struct:
    records = {r.name: r for r in _parse(backend_name, code)}
    assert name in records, f"{backend_name} did not emit {name}: {sorted(records)}"
    return records[name]


# ---------------------------------------------------------------------------
# Backends populate is_packed
# ---------------------------------------------------------------------------

# (label, source, record name, expected is_packed)
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
        # An anonymous bit-field does not contribute its type's alignment, so
        # this record is already byte-aligned without being packed. Reading
        # alignment alone would report it packed.
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


def test_libclang_records_an_inexpressible_intermediate_pragma_pack() -> None:
    """``#pragma pack(2)`` squeezes a record without flattening it.

    ``is_packed`` is a boolean, and re-emitting ``__attribute__((packed))``
    would understate the offsets: a C probe puts this record at size 8 with
    ``b`` at byte 2, where a fully packed record would put ``b`` at byte 1.
    The fact is kept in ``notes`` rather than reported wrongly or dropped.
    """
    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    record = _only(
        "libclang",
        "#pragma pack(2)\nstruct S { unsigned char a; unsigned int b; unsigned char c; };\n#pragma pack()\n",
        "S",
    )
    assert record.is_packed is False
    assert any("pack(2)" in note for note in record.notes)


# ---------------------------------------------------------------------------
# The generated ctypes classes reproduce the C layout
# ---------------------------------------------------------------------------


def _build(source: str) -> dict[str, type[ctypes.Structure]]:
    """Generate ctypes bindings from hand-built-equivalent parsed IR and load them."""
    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    code = get_writer("ctypes").write(backend.parse(source, "packed.h"))
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)  # noqa: S102
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
    (
        "unpacked-mixed-plain-named-and-padding",
        "struct S { unsigned char a; unsigned int b : 5; unsigned int : 3;"
        " unsigned short c : 9; unsigned int d; unsigned char e; };",
        12,
        4,
        {"a": (0, 7, 8), "b": (8, 12, 5), "c": (16, 24, 9), "d": (32, 63, 32), "e": (64, 71, 8)},
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
    ids=[c[0] for c in _LAYOUT_CASES],
)
def test_generated_ctypes_matches_c_layout(
    label: str,
    source: str,
    c_sizeof: int,
    c_alignof: int,
    c_fields: dict[str, tuple[int, int, int]],
) -> None:
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
            "packed.h",
        )
    )
    assert '("b", ctypes.c_ubyte, 8)' in code
    assert '("b", ctypes.c_uint, 8)' not in code


def test_a_packed_bitfield_ctypes_cannot_place_is_flagged_not_emitted_silently() -> None:
    """A bit-field wider than a byte that starts mid-byte has no ctypes spelling.

    C continues ``b`` across the storage-unit boundary, putting it at bit 4 (a
    compiled probe measures the record at 6 bytes). ctypes moves it to the next
    unit instead, and no carrier choice avoids that: 30 bits do not fit a byte.
    The generated module says so rather than looking correct.
    """
    code = get_writer("ctypes").write(
        get_backend("libclang").parse(
            "struct __attribute__((packed)) S { unsigned int a : 4; unsigned int b : 30; unsigned char c; };",
            "packed.h",
        )
    )
    assert "# HEADERKIT: packed bit-field(s) b" in code


def test_an_expressible_packed_record_carries_no_diagnostic() -> None:
    """Negative control: the diagnostic must not fire on records ctypes can place."""
    code = get_writer("ctypes").write(
        get_backend("libclang").parse(
            "struct __attribute__((packed)) S { unsigned char a; unsigned int b : 8; unsigned char c; };",
            "packed.h",
        )
    )
    assert "# HEADERKIT: packed bit-field" not in code


# ---------------------------------------------------------------------------
# Other writers now see is_packed set for the first time
# ---------------------------------------------------------------------------

_PACKED_SOURCE = "struct __attribute__((packed)) S { unsigned char a; unsigned int b; unsigned char c; };"


def _write(writer_name: str, source: str = _PACKED_SOURCE) -> str:
    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    return get_writer(writer_name).write(backend.parse(source, "packed.h"))


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


def test_nim_writer_emits_the_packed_pragma() -> None:
    assert "{.packed" in _write("nim") or "packed" in _write("nim")


def test_prompt_writer_marks_the_record_packed() -> None:
    assert "__packed" in _write("prompt")


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_unpacked_record_is_not_marked_packed_by_any_writer(backend_name: str) -> None:
    """Negative control across the writers that read ``is_packed``."""
    backend = get_backend(backend_name)
    if not backend.is_available():
        pytest.skip(f"{backend_name} backend unavailable")
    header = backend.parse("struct S { unsigned char a; unsigned int b; unsigned char c; };", "packed.h")
    assert "__attribute__((packed))" not in get_writer("cffi").write(header)
    assert "packed struct S" not in get_writer("cython").write(header)
    assert "_pack_ = 1" not in get_writer("ctypes").write(header)


def test_diff_writer_reports_a_record_becoming_packed() -> None:
    """``is_packed`` moving is an ABI break, so the diff must name it. This
    comparison was unreachable from parsed headers until both backends set the
    flag: two real headers always compared equal on packing."""
    from headerkit.writers.diff import diff_headers

    backend = get_backend("libclang")
    if not backend.is_available():
        pytest.skip("libclang backend unavailable")
    baseline = backend.parse("struct S { unsigned char a; unsigned int b; unsigned char c; };", "packed.h")
    target = backend.parse(_PACKED_SOURCE, "packed.h")
    report = diff_headers(baseline, target)
    assert "packed" in str(report).lower()


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
    unit = backend.parse("union U { unsigned char a; unsigned int b; } ;", "packed.h")
    del unit
    record = _only("tree-sitter", "union __attribute__((packed)) U { unsigned char a; unsigned int b; };", "U")
    assert record.is_packed is True


def test_packed_struct_str_shows_the_attribute() -> None:
    """``Struct.__str__`` already spelled the attribute; parsing now reaches it."""
    record = _only("libclang", _PACKED_SOURCE, "S")
    assert "__attribute__((packed))" in str(record)
