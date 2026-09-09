"""Execution gates for scaffolded packages: import them, compile them, call through them.

Every other scaffolding test in this suite asserts on the *text* of a generated
file. That is what let three un-importable packages ship: a test that greps
``_bindings.py`` for ``_lib.thing_add.argtypes`` passes identically whether the
module works or raises ``NameError`` on the line it just matched.

The gates here consume the output instead. A real C library is compiled from a
fixture header, the scaffolder is run against that same header, and the result
has to survive being used:

* the ctypes package must **import** and the call must reach the C function;
* the Nim package must **compile, link and execute** against the same library;
* the generated tests must themselves **run**, both of them;
* and with the native library absent both the package and its tripwire must
  **fail**, which is the tripwire invariant in ``AGENTS.md`` §1 -- a tripwire
  that passes without the binary present is a green mirage.

**These gates degrade to a silent no-op in two ways, and both are CI-only
properties rather than local ones.** ``_require`` skips the whole test when no
compiler is on PATH, so a machine without a toolchain reports green having
executed nothing; and ``tree-sitter`` is an optional extra, so on a plain
``pip install -e .[test]`` only the ``libclang`` parameter ever runs and the
``[tree-sitter]`` id is silently absent rather than failing. "Parameterized over
both backends" is therefore true of CI, where both are installed, and not
necessarily of a local run -- check the test ids, not the exit status, when
using these gates to judge a change. Making the skips loud would mean deciding
what a contributor without a C++ toolchain should see, which is a change to
every gate in this file and not one this file's newest additions should make
alone.

Every gate runs against each installed parser backend. The ``importc`` spelling
the Nim writer emits is chosen from ``is_typedef``, which the two backends
derive by different routes, so a gate bound to one backend would leave the other
one's output unproven.

The fixture header is the smallest one reaching every discrimination these gates
were written for. Each declaration earns its place:

``typedef enum { ... } Flags;``
    Tag-less. Declares no ``enum Flags``, so ``importc: "enum Flags"`` names an
    incomplete type, and ``Flags = Flags`` in ctypes raises ``NameError``. Its
    enumerators are written as plain integers rather than as ``1 << 3``: the
    tree-sitter backend passes such an initialiser through verbatim, and Nim
    spells that operator ``shl``, which is a separate defect this file is not
    the gate for.
``typedef enum { ... } Mode;``
    A second tag-less enum, so the first cannot pass by coincidence.
``typedef enum Mode2 { ... } Mode2;``
    Tagged, alias repeating the tag. ``enum Mode2`` really is declared here.
``enum Bare { ... };``
    A tag with no typedef at all: the bare name is *not* a type spelling.
``typedef struct { ... } Rec;``
    Tag-less, and carries a bit-field so the record's layout is exercised.
``typedef struct Foo { ... } FooAlias;``
    Tagged, alias differing from the tag -- the case where the tag must survive
    into the emitted C rather than being replaced by the alias.
``thing_add`` / ``rec_a`` / ``foo_a``
    Something to call. Two or more functions also make the generated test files
    multi-line, which is the shape that broke their indentation.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.ir import Enum, SourceUnit
from headerkit.scaffold import ScaffoldOptions, scaffold
from tests.native_build import IS_WINDOWS, shared_library_command, shared_library_filename

#: Every parser backend the writers can be driven from. Both are exercised
#: because the Nim ``importc`` spelling depends on IR each one fills in itself.
BACKENDS = ("libclang", "tree-sitter")

FIXTURE_HEADER = textwrap.dedent("""\
    #ifndef PROBE_H
    #define PROBE_H

    typedef enum { FLAG_A = 1, FLAG_D = 8 } Flags;
    typedef enum { MODE_X, MODE_Y } Mode;
    typedef enum Mode2 { MODE2_X, MODE2_Y } Mode2;
    enum Bare { BARE_A, BARE_B };
    typedef struct { int a; unsigned b : 3; } Rec;
    typedef struct Foo { int f; } FooAlias;

    int thing_add(int x, int y);
    int rec_a(Rec r);
    int foo_a(FooAlias v);

    #endif
""")

FIXTURE_SOURCE = textwrap.dedent("""\
    #include "probe.h"

    int thing_add(int x, int y) { return x + y; }
    int rec_a(Rec r) { return r.a; }
    int foo_a(FooAlias v) { return v.f; }
""")

#: C keeps tags and ordinary identifiers in separate namespaces, so a struct tag
#: and a function may share a spelling. Both reach Python as ``Dup``.
#:
#: ``_lib`` is the second collision axis and the more destructive one: it names
#: nothing in the header's own namespace, but the generated module binds it to
#: the loaded library, and every exported symbol is read from it. Re-exporting
#: a C function of that name replaces the handle with a function pointer, and
#: the next export line dies on it.
COLLISION_HEADER = textwrap.dedent("""\
    #ifndef DUP_H
    #define DUP_H

    struct Dup { int a; };
    int Dup(void);
    int _lib(void);
    int thing_add(int a, int b);

    #endif
""")

COLLISION_SOURCE = textwrap.dedent("""\
    #include "dup.h"

    int Dup(void) { return 11; }
    int _lib(void) { return 13; }
    int thing_add(int a, int b) { return a + b; }
""")

#: Every shape in which an enum can reach a record member or a function
#: signature. An enum binds no ctypes class, so each of these used to reach the
#: generated module as the C spelling: ``("m", enum Colour)`` under libclang,
#: which is a ``SyntaxError``, and ``("m", Colour)`` under tree-sitter, which is
#: a ``NameError`` -- the alias is emitted, but into the typedefs section, which
#: comes *after* the records that use it.
#:
#: ``Tagged``       the ``enum E`` tag spelling, with no typedef on the enum
#: ``Aliased``      an enum typedef whose name differs from the enum's own
#:                  tag, so only the ``Typedef`` node carries that name -- and
#:                  it renders into the typedefs section, which is emitted
#:                  after the record that uses it
#: ``Anon``         a tag-less enum typedef, whose alias the ``Enum`` node
#:                  carries itself, into the enums section
#: ``Choice``       the same member in a union rather than a struct
#: ``Row``          an array of enum, where the spelling is composed into
#:                  ``Colour * 4`` and a bad element type is still a bad name
#: ``colour_next``  an enum as a parameter and as a return type, which is the
#:                  same defect outside a record entirely
#: ``Shadowed``     the negative control. ``enum Tone`` and ``typedef struct
#:                  { ... } Tone`` are both legal in one unit -- a tag and an
#:                  ordinary identifier are separate namespaces in C -- and an
#:                  unprefixed ``Tone`` is the *record*. Resolving every bare
#:                  enum tag to an integer would retype this member from eight
#:                  bytes to four, so this case fails if the fix over-reaches
#:
#: Every *record* here is a tag-less typedef, so no record is ever spelled
#: ``struct X``. That is deliberate: a record named by its tag in a function
#: signature reaches the generated module as ``argtypes = [struct Tagged]``,
#: which is a separate unfixed defect in the same writer. Leaving it in the
#: fixture would make this gate red for a reason it is not about.
#:
#: Each ``*_size`` function reports what the C compiler actually laid out, so the
#: gate compares against the real ABI rather than against a second guess.
ENUM_HEADER = textwrap.dedent("""\
    #ifndef ENUMS_H
    #define ENUMS_H

    enum Colour { COLOUR_RED = 0, COLOUR_BLUE = 1 };
    typedef enum Level { LEVEL_LOW = 0, LEVEL_HIGH = 1 } LevelAlias;
    typedef enum { STATE_OFF = 0, STATE_ON = 1 } State;

    typedef struct { enum Colour m; } Tagged;
    typedef struct { LevelAlias m; } Aliased;
    typedef struct { State m; } Anon;
    typedef union { enum Colour m; int i; } Choice;
    typedef struct { enum Colour arr[4]; } Row;

    enum Tone { TONE_A = 0, TONE_B = 1 };
    typedef struct { int lo; int hi; } Tone;
    typedef struct { Tone t; } Shadowed;

    int tagged_m(Tagged r);
    int aliased_m(Aliased r);
    int anon_m(Anon r);
    int choice_m(Choice u);
    int row_at(Row r, int i);
    int shadowed_packed(Shadowed s);
    enum Colour colour_next(enum Colour c);

    int tagged_size(void);
    int aliased_size(void);
    int anon_size(void);
    int choice_size(void);
    int row_size(void);
    int shadowed_size(void);
    int colour_size(void);

    #endif
""")

ENUM_SOURCE = textwrap.dedent("""\
    #include "enums.h"

    int tagged_m(Tagged r) { return (int)r.m; }
    int aliased_m(Aliased r) { return (int)r.m; }
    int anon_m(Anon r) { return (int)r.m; }
    int choice_m(Choice u) { return (int)u.m; }
    int row_at(Row r, int i) { return (int)r.arr[i]; }
    int shadowed_packed(Shadowed s) { return s.t.lo * 1000 + s.t.hi; }
    enum Colour colour_next(enum Colour c) {
        return c == COLOUR_RED ? COLOUR_BLUE : COLOUR_RED;
    }

    int tagged_size(void) { return (int)sizeof(Tagged); }
    int aliased_size(void) { return (int)sizeof(Aliased); }
    int anon_size(void) { return (int)sizeof(Anon); }
    int choice_size(void) { return (int)sizeof(Choice); }
    int row_size(void) { return (int)sizeof(Row); }
    int shadowed_size(void) { return (int)sizeof(Shadowed); }
    int colour_size(void) { return (int)sizeof(enum Colour); }
""")

#: C++ enums, in the two shapes that decide *whether the width is knowable*.
#:
#: ``namespace n { enum Plain ... }`` is an ordinary unscoped enum that happens
#: to live in a namespace. Its width is provable from its enumerators, so it
#: must resolve -- and the member is spelled ``n::Plain``, a third spelling
#: beside the tag and bare forms, which is the part that used to emit
#: ``("m", n::Plain)`` and fail to parse under tree-sitter.
#:
#: The accessors are reached through a plain ``ctypes.CDLL`` rather than through
#: the generated module: libclang drops an ``extern "C"`` function from the IR
#: altogether, so binding this gate to the generated prototypes would prove
#: nothing under one of the two backends. The record layout is what is under
#: test, and it comes from the generated module either way.
NS_HEADER = textwrap.dedent("""\
    #ifndef NSENUM_H
    #define NSENUM_H

    namespace n { enum Plain { N_LOW = 0, N_HIGH = 1 }; }
    struct NsRec { n::Plain m; };

    extern "C" int ns_m(NsRec r);
    extern "C" int ns_size(void);

    #endif
""")

NS_SOURCE = textwrap.dedent("""\
    #include "nsenum.h"

    extern "C" int ns_m(NsRec r) { return static_cast<int>(r.m); }
    extern "C" int ns_size(void) { return static_cast<int>(sizeof(NsRec)); }
""")

#: A C++ ``enum class`` carrying a **fixed underlying type**. ``Enum`` records no
#: underlying type, so this one byte of width is exactly the information the IR
#: throws away -- and resolving it to ``ctypes.c_int`` anyway produces a record
#: of 8 bytes where the C++ compiler lays out 2, which *imports cleanly*. This
#: enum must therefore be refused rather than resolved, and the refusal is what
#: the gate asserts. Sized deliberately so a wrong answer cannot coincide with
#: the right one.
SCOPED_HEADER = textwrap.dedent("""\
    #ifndef SCOPED_H
    #define SCOPED_H

    enum class Small : unsigned char { S_LOW = 0, S_HIGH = 1 };
    struct Pair { Small a; Small b; };

    extern "C" int pair_size(void);

    #endif
""")

SCOPED_SOURCE = textwrap.dedent("""\
    #include "scoped.h"

    extern "C" int pair_size(void) { return static_cast<int>(sizeof(Pair)); }
""")

#: A C enum with an enumerator too wide for an ``int``. The C compiler widens the
#: whole enum, so ``ctypes.c_int`` is the wrong width in the other direction --
#: 4 bytes where C lays out 8 -- and it too imports cleanly. Refused, not
#: resolved. No integer suffix is written on the literal: tree-sitter passes an
#: initialiser through verbatim, and ``LL`` is not valid Python, which would make
#: this gate red for an unrelated reason.
BIG_HEADER = textwrap.dedent("""\
    #ifndef BIGENUM_H
    #define BIGENUM_H

    enum Big { BIG_LOW = 0, BIG_HIGH = 0x7FFFFFFFFF };
    typedef struct { enum Big m; } BigHolder;

    int big_size(void);

    #endif
""")

BIG_SOURCE = textwrap.dedent("""\
    #include "bigenum.h"

    int big_size(void) { return (int)sizeof(BigHolder); }
""")

#: A **tag-less typedef** of an unsizable enum. This is the shape where refusing
#: to resolve the member is not by itself enough: the alias such an enum carries
#: is written into the ``enums`` section, which precedes ``structs``, so a member
#: left spelled ``BigT`` would *resolve* against that alias and the package would
#: import with a four-byte member where C laid out eight. The alias has to be
#: withheld too, and this fixture is what proves it.
BIGT_HEADER = textwrap.dedent("""\
    #ifndef BIGTENUM_H
    #define BIGTENUM_H

    typedef enum { BIGT_LOW = 0, BIGT_HIGH = 0x7FFFFFFFFF } BigT;
    typedef struct { BigT m; } BigTHolder;

    int bigt_size(void);

    #endif
""")

BIGT_SOURCE = textwrap.dedent("""\
    #include "bigtenum.h"

    int bigt_size(void) { return (int)sizeof(BigTHolder); }
""")

#: An enum whose *implicit* value overflows. C defines an enumerator with no
#: initialiser as one more than the previous one, so ``ROLL_NEXT`` is
#: 0x80000000 -- out of signed 32-bit range -- but only a reader that tracks the
#: running counter can see it, since the IR records ``None`` for that enumerator.
#:
#: Size cannot discriminate this one and the gate does not pretend it can: the
#: successor of ``INT32_MAX`` is at most 0x80000000, which ``unsigned int``
#: holds in the same four bytes. What goes wrong is *signedness* --
#: ``ctypes.c_int`` reads 0x80000000 as -2147483648 -- so the value is what this
#: fixture measures.
IMPLICIT_HEADER = textwrap.dedent("""\
    #ifndef IMPLENUM_H
    #define IMPLENUM_H

    enum Roll { ROLL_MAX = 0x7FFFFFFF, ROLL_NEXT };
    typedef struct { enum Roll m; } RollHolder;

    int roll_size(void);
    long long roll_next(void);

    #endif
""")

IMPLICIT_SOURCE = textwrap.dedent("""\
    #include "implenum.h"

    int roll_size(void) { return (int)sizeof(RollHolder); }
    long long roll_next(void) { return (long long)ROLL_NEXT; }
""")

#: Records named by their **tag**, in every position a type can occupy. This is
#: the same defect as the enum one and in the same function: ``type_to_ctypes``
#: returned the C spelling, and ``struct Inner`` is two words where Python needs
#: one. libclang preserves C's elaborated form, so every line below reached the
#: module unparseable; tree-sitter drops the keyword and so only the C++
#: namespace case fails there.
#:
#: A record differs from an enum in having a real answer rather than a default:
#: the class this writer emits. So there is no width question here, and no
#: fallback -- a record the header does not declare keeps its C spelling.
#:
#: ``struct Gauge`` beside ``typedef unsigned char Gauge`` is the mirror of the
#: ``Tone`` control: a tag and an ordinary identifier are separate namespaces in
#: C, so both are legal in one unit and mean different types. The tag-spelled
#: member must still resolve to the *record* while that collision exists. Sized
#: 8 against 1 so a confusion between them cannot go unnoticed.
RECORD_HEADER = textwrap.dedent("""\
    #ifndef RECS_H
    #define RECS_H

    struct Inner { int a; int b; };
    union Choice2 { int i; float f; };

    typedef struct { struct Inner n; } ByTag;
    typedef struct { union Choice2 u; } UnionMember;
    typedef struct { struct Inner arr[4]; } RecArray;
    typedef struct { struct Inner *p; } RecPointer;

    struct Gauge { int lo; int hi; };
    typedef unsigned char Gauge;
    typedef struct { struct Gauge g; } Shadowed2;

    int inner_sum(struct Inner v);
    struct Inner inner_make(int a, int b);

    int bytag_size(void);
    int unionmember_size(void);
    int recarray_size(void);
    int recpointer_size(void);
    int shadowed2_size(void);

    int bytag_n_b(ByTag v);
    int unionmember_i(UnionMember v);
    int recarray_at(RecArray v, int i);
    int recpointer_b(RecPointer v);
    int shadowed2_hi(Shadowed2 v);

    #endif
""")

RECORD_SOURCE = textwrap.dedent("""\
    #include "recs.h"

    int inner_sum(struct Inner v) { return v.a + v.b; }
    struct Inner inner_make(int a, int b) { struct Inner r; r.a = a; r.b = b; return r; }

    int bytag_size(void) { return (int)sizeof(ByTag); }
    int unionmember_size(void) { return (int)sizeof(UnionMember); }
    int recarray_size(void) { return (int)sizeof(RecArray); }
    int recpointer_size(void) { return (int)sizeof(RecPointer); }
    int shadowed2_size(void) { return (int)sizeof(Shadowed2); }

    int bytag_n_b(ByTag v) { return v.n.b; }
    int unionmember_i(UnionMember v) { return v.u.i; }
    int recarray_at(RecArray v, int i) { return v.arr[i].b; }
    int recpointer_b(RecPointer v) { return v.p->b; }
    int shadowed2_hi(Shadowed2 v) { return v.g.hi; }
""")

#: A tag-named record inside a **packed** record. This is the consumer the
#: widening exists for: the packed-record branch computes layouts for named
#: nested members and nobody can observe them while the module will not import.
#:
#: Only importability and the member's identity are asserted, deliberately. This
#: writer emits no ``_pack_``, so ``ctypes`` lays a packed record out at natural
#: alignment and both its ``sizeof`` and any by-value call would disagree with C
#: for a reason that has nothing to do with tag spelling. When the packed branch
#: lands, the size and round-trip assertions belong here.
PACKED_RECORD_HEADER = textwrap.dedent("""\
    #ifndef PACKREC_H
    #define PACKREC_H

    struct Tiny { int a; };
    struct __attribute__((packed)) Squeezed { char c; struct Tiny n; };

    int squeezed_size(void);

    #endif
""")

PACKED_RECORD_SOURCE = textwrap.dedent("""\
    #include "packrec.h"

    int squeezed_size(void) { return (int)sizeof(struct Squeezed); }
""")

#: An **incomplete** record: ``struct Unknown`` is never defined, only pointed
#: at. No class is emitted for it, so there is nothing to resolve to, and the
#: table simply does not carry it. The point of the gate is that no fallback is
#: invented -- a stripped ``Unknown`` would name a class that does not exist.
OPAQUE_HEADER = textwrap.dedent("""\
    #ifndef OPAQUE_H
    #define OPAQUE_H

    typedef struct { struct Unknown *p; int a; } Opaque;

    int opaque_a(Opaque v);

    #endif
""")

OPAQUE_SOURCE = textwrap.dedent("""\
    #include "opaque.h"

    int opaque_a(Opaque v) { return v.a; }
""")

#: A C++ record in a namespace, spelled ``n::Inner`` at the use site -- the
#: record twin of the namespaced-enum case, and the one shape tree-sitter also
#: got wrong, since it emits the qualified name verbatim.
NS_RECORD_HEADER = textwrap.dedent("""\
    #ifndef NSREC_H
    #define NSREC_H

    namespace n { struct Inner { int a; int b; }; }
    struct NsRecHolder { n::Inner m; };

    extern "C" int nsrec_b(NsRecHolder h);
    extern "C" int nsrec_size(void);

    #endif
""")

NS_RECORD_SOURCE = textwrap.dedent("""\
    #include "nsrec.h"

    extern "C" int nsrec_b(NsRecHolder h) { return h.m.b; }
    extern "C" int nsrec_size(void) { return static_cast<int>(sizeof(NsRecHolder)); }
""")

#: An enum with a *computed* enumerator, which the two backends disagree about:
#: libclang evaluates ``1 << 3`` to the integer 8, while tree-sitter passes the
#: initialiser through verbatim as the string ``"1 << 3"``. A string has no
#: width, so under tree-sitter this enum is not sizable and is refused -- the
#: conservative half of the same rule that refuses a scoped or over-wide enum.
EXPR_HEADER = textwrap.dedent("""\
    #ifndef EXPRENUM_H
    #define EXPRENUM_H

    enum Expr { EXPR_A = 1, EXPR_B = 1 << 3 };
    typedef struct { enum Expr m; } ExprHolder;

    int expr_m(ExprHolder h);
    int expr_size(void);

    #endif
""")

EXPR_SOURCE = textwrap.dedent("""\
    #include "exprenum.h"

    int expr_m(ExprHolder h) { return (int)h.m; }
    int expr_size(void) { return (int)sizeof(ExprHolder); }
""")

#: A *record* declared in an included header. The spelling half of this is fixed
#: -- ``struct Swatch`` resolves to the class ``Swatch`` under libclang -- but the
#: module still does not import, for a reason that is not about spelling at all:
#: declarations are emitted in the order the backend reports them, and libclang
#: reports the including file's declarations before the included file's, so
#: ``class Swatch`` is written *after* the record whose ``_fields_`` names it.
#: That is a declaration-ordering defect, it is not what this change is about,
#: and it is loud on ``main`` too. The gate pins that it stays loud.
INCLUDED_RECORD_HEADER = textwrap.dedent("""\
    #ifndef SWATCH_H
    #define SWATCH_H

    struct Swatch { int lo; int hi; };

    #endif
""")

INCLUDING_RECORD_HEADER = textwrap.dedent("""\
    #ifndef SWUSER_H
    #define SWUSER_H

    #include "swatch.h"

    typedef struct { struct Swatch s; } SwatchHolder;

    int swatch_hi(SwatchHolder h);

    #endif
""")

INCLUDING_RECORD_SOURCE = textwrap.dedent("""\
    #include "swuser.h"

    int swatch_hi(SwatchHolder h) { return h.s.hi; }
""")

#: An enum declared in a *different* header and reached through ``#include``.
#: This is the most ordinary real-world shape and the two backends genuinely
#: differ on it: libclang follows the include and sees ``Enum Colour``, so it can
#: both recognise and size it; tree-sitter does not process includes at all, so
#: it sees neither the declaration nor -- because it reduces every enum reference
#: to its bare name -- the ``enum`` keyword the source actually wrote. There is
#: no header-local answer for tree-sitter, so the contract the gate pins is
#: asymmetric on purpose: resolved under libclang, and under tree-sitter a loud
#: failure rather than a silently mis-sized member.
INCLUDED_ENUM_HEADER = textwrap.dedent("""\
    #ifndef PALETTE_H
    #define PALETTE_H

    enum Shade { SHADE_DARK = 0, SHADE_LIGHT = 1 };
    struct Swatch { int lo; int hi; };

    #endif
""")

INCLUDING_HEADER = textwrap.dedent("""\
    #ifndef INCLUSER_H
    #define INCLUSER_H

    #include "palette.h"

    typedef struct { enum Shade m; } ShadeHolder;

    int shade_m(ShadeHolder h);
    int shade_size(void);

    #endif
""")

INCLUDING_SOURCE = textwrap.dedent("""\
    #include "incluser.h"

    int shade_m(ShadeHolder h) { return (int)h.m; }
    int shade_size(void) { return (int)sizeof(ShadeHolder); }
""")


def _require(*programs: str) -> str:
    """Return the first of ``programs`` on PATH, or skip.

    The skip is narrow on purpose: a gate that quietly no-ops when its toolchain
    is missing proves nothing while reporting green.
    """
    for candidate in programs:
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip(f"none of {', '.join(programs)} on PATH")


@pytest.fixture(params=BACKENDS)
def backend_name(request: pytest.FixtureRequest) -> str:
    """Run the gate once per installed parser backend."""
    name = str(request.param)
    if not is_backend_available(name):
        pytest.skip(f"{name} backend not available")
    return name


def _parse(backend_name: str, source: str, filename: str) -> SourceUnit:
    return get_backend(backend_name).parse(source, filename)


def _build_c_library(
    workdir: Path,
    stem: str,
    *,
    header: str = FIXTURE_HEADER,
    source: str = FIXTURE_SOURCE,
    basename: str = "probe",
    extra_headers: dict[str, str] | None = None,
) -> Path:
    """Compile ``source`` into a real shared library and return its path.

    :param extra_headers: further ``name -> text`` headers written beside the
        main one, for a fixture whose enum lives in an ``#include``.
    """
    compiler = _require("cc", "gcc", "clang")
    for extra_name, extra_text in (extra_headers or {}).items():
        (workdir / extra_name).write_text(extra_text, encoding="utf-8")
    (workdir / f"{basename}.h").write_text(header, encoding="utf-8")
    (workdir / f"{basename}.c").write_text(source, encoding="utf-8")
    out = workdir / shared_library_filename(stem)
    argv = shared_library_command(compiler, [f"{basename}.c"], str(out), includes=["."])
    result = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"fixture library failed to build:\n{result.stderr}"
    assert out.is_file()
    return out


def _build_cpp_library(workdir: Path, stem: str, *, header: str, source: str, basename: str) -> Path:
    """Compile a C++ shared library and return its path.

    The two ``-static-*`` flags are the mitigation ``native_build`` documents for
    the Windows runner: its ``c++`` is MinGW, so a shared object it links names
    ``libstdc++-6.dll`` and ``libgcc_s_seh-1.dll`` as load-time dependencies that
    are not on the loader's search path for the interpreter which then opens the
    library with ``ctypes.CDLL``. Without them the failure is "The specified
    module could not be found", naming neither the DLL nor MinGW.
    """
    compiler = _require("c++", "g++", "clang++")
    (workdir / f"{basename}.h").write_text(header, encoding="utf-8")
    (workdir / f"{basename}.cpp").write_text(source, encoding="utf-8")
    out = workdir / shared_library_filename(stem)
    extra = ["-static", "-static-libstdc++", "-static-libgcc"] if IS_WINDOWS else []
    argv = shared_library_command(compiler, [f"{basename}.cpp"], str(out), includes=["."], extra=extra)
    result = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"fixture C++ library failed to build:\n{result.stderr}"
    assert out.is_file()
    return out


def _scaffold_to(
    target: str,
    workdir: Path,
    pkg: str,
    *,
    backend_name: str,
    header: str = FIXTURE_HEADER,
    filename: str = "probe.h",
    options: dict[str, object] | None = None,
) -> Path:
    """Scaffold a package for ``target`` into ``workdir`` and return the root."""
    root = workdir / f"{target}-{pkg}"
    layout = scaffold(
        _parse(backend_name, header, filename),
        ScaffoldOptions(
            package_name=pkg,
            target_language=target,
            layout="package",
            options=dict(options or {}),
        ),
    )
    layout.write_to_disk(root)
    return root


def _widest_enumerator(backend_name: str, header: str, filename: str) -> int:
    """The largest absolute enumerator value this backend reports for ``header``.

    The refusal gates rest on a premise that is not true everywhere: that the
    parser and the C compiler agree an enum is too wide for an ``int``. On a host
    where libclang targets a different ABI than the ``cc`` compiling the fixture
    -- Windows, where the runner's ``cc`` is MinGW -- libclang reports an
    enumerator already truncated into ``int`` range while the compiler widens the
    enum to 8 bytes. The writer sees only the IR, so it cannot know about a
    widening its own parser never reported, and the gate would be demanding
    something no writer code could deliver. Reading the value back is what lets
    that host be named and passed over instead of failing for the wrong reason.
    """
    unit = _parse(backend_name, header, filename)
    headers = unit.headers if hasattr(unit, "headers") else [unit]
    values = [
        v.value
        for h in headers
        for d in h.declarations
        if isinstance(d, Enum)
        for v in d.values
        if isinstance(v.value, int)
    ]
    return max((abs(v) for v in values), default=0)


def _run_python(script: str, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter so an import failure is observable."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _run_pytest(target: str, *extra_args: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the generated test suite at ``target`` inside the scaffolded package."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", target, "-p", "no:cacheprovider", "-q", *extra_args],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src"), **env},
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# ctypes
# ---------------------------------------------------------------------------


class TestScaffoldedCtypesPackageRuns:
    def test_package_imports_and_calls_into_the_c_library(self, tmp_path: Path, backend_name: str) -> None:
        """The generated package must import and the call must reach the C function.

        ``thing_add(2, 3) == 5`` is the whole point: the value can only be 5 if
        the module imported, the library loaded, the symbol resolved and the
        argument types were right. No part of that is provable from the text of
        the generated file.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        script = textwrap.dedent("""\
            from probe import _bindings

            assert _bindings._lib.thing_add(2, 3) == 5, "the call did not reach the C function"
            assert _bindings.thing_add(2, 3) == 5, "the re-exported name is not the configured callable"
            assert _bindings.FLAG_D == 8
            assert _bindings.MODE_Y == 1
            assert _bindings.MODE2_Y == 1
            assert _bindings.BARE_B == 1
            r = _bindings.Rec(a=7, b=5)
            assert _bindings._lib.rec_a(r) == 7, "the record did not survive the ABI boundary"
            assert _bindings._lib.foo_a(_bindings.FooAlias(f=9)) == 9, "the tagged typedef record is unusable"
            print("CALLED")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"generated ctypes package is not usable:\n{result.stderr}"
        assert "CALLED" in result.stdout

    def test_generated_typedef_names_are_bound(self, tmp_path: Path, backend_name: str) -> None:
        """A ``typedef enum`` must leave a usable name behind, not a self-reference.

        ``Flags = Flags`` raises ``NameError`` at import, so this cannot be
        checked by looking for the name in the file: the name is *there*.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        script = textwrap.dedent("""\
            import ctypes
            from probe import _bindings

            for name in ("Flags", "Mode", "Mode2"):
                alias = getattr(_bindings, name, None)
                assert alias is not None, name + " is not bound in the generated module"
                assert issubclass(alias, ctypes._SimpleCData), name + " is not a ctypes type"
                assert ctypes.sizeof(alias) == ctypes.sizeof(ctypes.c_int)
            print("BOUND")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"typedef'd enums are not usable:\n{result.stderr}"
        assert "BOUND" in result.stdout

    def test_a_function_does_not_clobber_a_same_named_struct(self, tmp_path: Path, backend_name: str) -> None:
        """A function must not clobber a same-named struct or the library handle.

        Both ``struct Dup`` and ``int Dup(void)`` land on the module-level name
        ``Dup``, and the exported-symbol block is emitted last, so re-exporting
        the function unconditionally replaces the struct class with a function
        pointer. Nothing raises: the module imports, and ``Dup(a=1)`` fails much
        later somewhere else.

        ``int _lib(void)`` is the same collision against a name no declaration
        binds -- the loader preamble binds it -- and it is fatal rather than
        silent: it overwrites the library handle that every later export reads
        from, so ``thing_add`` dies with ``AttributeError`` on a ``_FuncPtr``.
        """
        library = _build_c_library(
            tmp_path,
            "dup",
            header=COLLISION_HEADER,
            source=COLLISION_SOURCE,
            basename="dup",
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "dup",
            backend_name=backend_name,
            header=COLLISION_HEADER,
            filename="dup.h",
        )

        script = textwrap.dedent("""\
            import ctypes
            from dup import _bindings

            assert issubclass(_bindings.Dup, ctypes.Structure), "the struct class was replaced"
            assert ctypes.sizeof(_bindings.Dup) == ctypes.sizeof(ctypes.c_int)
            assert _bindings.Dup(a=3).a == 3
            assert _bindings._lib.Dup() == 11, "the C function is unreachable through the library object"

            assert isinstance(_bindings._lib, ctypes.CDLL), "the library handle was replaced"
            assert _bindings._lib._lib() == 13, "the C function is unreachable through the library object"

            # A function following the collision in the export block still binds.
            # This is what fails first when the handle is destroyed.
            assert _bindings.thing_add(2, 3) == 5, "a later export did not survive the collision"
            print("INTACT")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "DUP_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"the struct class did not survive the export block:\n{result.stderr}"
        assert "INTACT" in result.stdout

    def test_enum_typed_members_and_signatures_match_the_c_abi(self, tmp_path: Path, backend_name: str) -> None:
        """An enum-typed member must be a usable ctypes type of the C enum's width.

        An enum binds no ctypes class, so a member typed by one has to resolve
        to the underlying integer at the point of use. Emitting the C spelling
        instead is fatal in both of the shapes a backend can produce it:
        ``("m", enum Colour)`` does not parse at all, and ``("m", Colour)``
        parses and then raises ``NameError``, because the alias is emitted into
        a section that comes after the records.

        Neither the round-trip nor the width alone is enough. A member of the
        wrong width still round-trips a small value, so the C compiler's own
        ``sizeof`` is the second assertion; and a matching size proves nothing
        about which member the value landed in, so the value is passed through
        a real by-value call to C and read back from there.
        """
        library = _build_c_library(
            tmp_path,
            "enums",
            header=ENUM_HEADER,
            source=ENUM_SOURCE,
            basename="enums",
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "enums",
            backend_name=backend_name,
            header=ENUM_HEADER,
            filename="enums.h",
        )

        script = textwrap.dedent("""\
            import ctypes
            from enums import _bindings as b

            lib = b._lib

            # Width first: a member narrower or wider than the C enum still
            # round-trips COLOUR_BLUE, so the value checks below cannot see it.
            colour = lib.colour_size()
            assert colour == ctypes.sizeof(ctypes.c_int), "fixture assumption: C enum is not int-sized"
            for name, size_fn in (
                ("Tagged", lib.tagged_size),
                ("Aliased", lib.aliased_size),
                ("Anon", lib.anon_size),
                ("Choice", lib.choice_size),
                ("Row", lib.row_size),
                ("Shadowed", lib.shadowed_size),
            ):
                cls = getattr(b, name)
                assert ctypes.sizeof(cls) == size_fn(), (
                    f"{name}: ctypes lays out {ctypes.sizeof(cls)} bytes, "
                    f"the C compiler lays out {size_fn()}"
                )

            # Then the member itself, across the real ABI boundary.
            assert lib.tagged_m(b.Tagged(m=b.COLOUR_BLUE)) == 1, "the tag-spelled member did not survive the call"
            assert lib.aliased_m(b.Aliased(m=b.LEVEL_HIGH)) == 1, "the typedef'd member did not survive the call"
            assert lib.anon_m(b.Anon(m=b.STATE_ON)) == 1, "the tag-less typedef member did not survive the call"
            assert lib.choice_m(b.Choice(m=b.COLOUR_BLUE)) == 1, "the union member did not survive the call"

            row = b.Row(arr=(ctypes.c_int * 4)(0, 1, 1, 0))
            assert [lib.row_at(row, i) for i in range(4)] == [0, 1, 1, 0], "the enum array did not survive the call"

            # Negative control: ``Tone`` names the record, not ``enum Tone``.
            # A fix that resolved every bare enum tag would make this member a
            # four-byte integer, and the size loop above would already be red.
            assert lib.shadowed_packed(b.Shadowed(t=b.Tone(lo=4, hi=9))) == 4009, (
                "the typedef shadowing an enum tag was resolved to the enum"
            )

            # An enum as a parameter and as a return type, outside any record.
            assert lib.colour_next(b.COLOUR_RED) == b.COLOUR_BLUE
            assert lib.colour_next(b.COLOUR_BLUE) == b.COLOUR_RED
            print("ABI-MATCH")
        """)
        result = _run_python(
            script,
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "ENUMS_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"enum-typed members are not usable:\n{result.stderr}"
        assert "ABI-MATCH" in result.stdout

    def test_a_namespaced_cpp_enum_member_matches_the_cpp_abi(self, tmp_path: Path, backend_name: str) -> None:
        """``n::Plain`` is a third spelling, and it must resolve like the others.

        A C++ enum inside a namespace is an ordinary unscoped enum whose width is
        provable from its enumerators, so it is resolvable -- but the member is
        spelled with the qualified name, which matches neither the ``enum E`` tag
        form nor the bare ``E``. Under tree-sitter that used to reach the module
        as ``("m", n::Plain)``, which does not parse.
        """
        library = _build_cpp_library(tmp_path, "nsenum", header=NS_HEADER, source=NS_SOURCE, basename="nsenum")
        root = _scaffold_to(
            "ctypes", tmp_path, "nsenum", backend_name=backend_name, header=NS_HEADER, filename="nsenum.hpp"
        )

        script = textwrap.dedent("""\
            import ctypes
            import os
            from nsenum import _bindings as b

            lib = ctypes.CDLL(os.environ["NSENUM_LIBRARY"])
            lib.ns_size.restype = ctypes.c_int
            assert ctypes.sizeof(b.NsRec) == lib.ns_size(), (
                f"ctypes lays out {ctypes.sizeof(b.NsRec)} bytes, "
                f"the C++ compiler lays out {lib.ns_size()}"
            )

            lib.ns_m.argtypes = [b.NsRec]
            lib.ns_m.restype = ctypes.c_int
            assert lib.ns_m(b.NsRec(m=1)) == 1, "the namespaced enum member did not survive the call"
            print("NS-MATCH")
        """)
        result = _run_python(
            script, cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "NSENUM_LIBRARY": str(library)}
        )
        assert result.returncode == 0, f"the namespaced enum member is not usable:\n{result.stderr}"
        assert "NS-MATCH" in result.stdout

    def test_an_enum_whose_width_is_unprovable_is_refused_not_mis_sized(
        self, tmp_path: Path, backend_name: str
    ) -> None:
        """An enum this writer cannot size must fail loudly, never resolve wrongly.

        ``ENUM_CTYPE`` is ``ctypes.c_int``, and ``Enum`` carries no underlying
        type, so resolving every enum to it is only right for the enums that are
        int-width. The two here are not, in opposite directions:

        * ``enum class Small : unsigned char`` -- the fixed underlying type is
          exactly what the IR drops, and a ``Pair`` of them is 2 bytes in C++
          against 8 if both members become ``c_int``;
        * ``enum Big`` with an enumerator past ``INT32_MAX`` -- the C compiler
          widens the enum, so C lays out 8 bytes against 4.

        Both mis-sized modules **import cleanly**, which is what makes this worth
        a gate: there is no exception to catch and no line to read, only wrong
        answers across the ABI. The assertion is therefore twofold -- the C/C++
        compiler really does disagree with ``c_int`` for this type (so a silent
        resolution would really have been wrong), and the generated package fails
        to import rather than producing it.
        """
        cases = (
            ("scoped", _build_cpp_library, SCOPED_HEADER, SCOPED_SOURCE, "scoped", "scoped.hpp", "pair_size"),
            ("bigenum", _build_c_library, BIG_HEADER, BIG_SOURCE, "bigenum", "bigenum.h", "big_size"),
            ("bigtenum", _build_c_library, BIGT_HEADER, BIGT_SOURCE, "bigtenum", "bigtenum.h", "bigt_size"),
        )
        exercised: list[str] = []
        skipped: list[str] = []
        for pkg, build, header, source, basename, filename, size_fn in cases:
            work = tmp_path / pkg
            work.mkdir()
            if build is _build_cpp_library:
                library = build(work, pkg, header=header, source=source, basename=basename)
            else:
                library = build(work, pkg, header=header, source=source, basename=basename)

            # The compiler's own answer, so "would have been wrong" is measured
            # rather than assumed.
            probe = textwrap.dedent(f"""\
                import ctypes, os
                lib = ctypes.CDLL(os.environ["PROBE_LIB"])
                lib.{size_fn}.restype = ctypes.c_int
                print(lib.{size_fn}())
            """)
            measured = _run_python(probe, cwd=work, env={"PROBE_LIB": str(library)})
            assert measured.returncode == 0, f"{pkg}: could not read the compiled size:\n{measured.stderr}"
            real = int(measured.stdout.strip())
            naive = ctypes.sizeof(ctypes.c_int) * (2 if pkg == "scoped" else 1)
            assert real != naive, (
                f"{pkg}: fixture no longer discriminates -- C lays out {real} bytes, "
                f"which is what resolving to c_int would also give"
            )

            # A value-based case is only meaningful where the parser agrees with
            # the compiler that the enum is over-wide; see ``_widest_enumerator``.
            if pkg != "scoped" and _widest_enumerator(backend_name, header, filename) <= 2**31 - 1:
                skipped.append(
                    f"{pkg}: {backend_name} reports every enumerator inside int range while this host's "
                    f"C compiler lays out {real} bytes"
                )
                continue
            exercised.append(pkg)

            root = _scaffold_to("ctypes", work, pkg, backend_name=backend_name, header=header, filename=filename)
            result = _run_python(
                f"import {pkg}",
                cwd=work,
                env={"PYTHONPATH": str(root / "src"), f"{pkg.upper()}_LIBRARY": str(library)},
            )
            assert result.returncode != 0, (
                f"{pkg}: the package imported, so the unsizable enum was resolved anyway -- "
                f"C lays out {real} bytes and this module would report {naive}"
            )
            assert "SyntaxError" in result.stderr or "NameError" in result.stderr, (
                f"{pkg}: the import failed for some reason other than the refused enum:\n{result.stderr}"
            )

        # A gate that passed over every case would report green having asserted
        # nothing. The scoped case is structural rather than value-based, so it
        # is never passed over and this cannot be vacuous on any host.
        assert "scoped" in exercised, f"no refusal case ran; passed over: {skipped}"

    def test_an_implicit_enumerator_past_int_range_is_refused(self, tmp_path: Path, backend_name: str) -> None:
        """An enumerator with no initialiser still has a value, and it can overflow.

        ``enum Roll { ROLL_MAX = 0x7FFFFFFF, ROLL_NEXT };`` gives ``ROLL_NEXT``
        the value 0x80000000, which no ``ctypes.c_int`` can represent. The IR
        records ``None`` for it, so only a reader that reconstructs C's running
        counter sees the overflow; a reader that treats an implicit value as
        unremarkable resolves the enum and emits a signed member that reads the
        constant back as -2147483648.

        Size is not the discriminator here and the assertion does not use it --
        the compiler holds 0x80000000 in an unsigned int, the same four bytes.
        The C compiler's own value is read instead, and the package must refuse
        to import rather than bind a member that cannot hold it.
        """
        library = _build_c_library(
            tmp_path, "implenum", header=IMPLICIT_HEADER, source=IMPLICIT_SOURCE, basename="implenum"
        )
        probe = textwrap.dedent("""\
            import ctypes, os
            lib = ctypes.CDLL(os.environ["PROBE_LIB"])
            lib.roll_next.restype = ctypes.c_longlong
            print(lib.roll_next())
        """)
        measured = _run_python(probe, cwd=tmp_path, env={"PROBE_LIB": str(library)})
        assert measured.returncode == 0, f"could not read the compiled enumerator:\n{measured.stderr}"
        value = int(measured.stdout.strip())
        assert value > 2**31 - 1, (
            f"fixture no longer discriminates: the C compiler gave ROLL_NEXT {value}, "
            f"which a signed 32-bit member could hold after all"
        )

        if _widest_enumerator(backend_name, IMPLICIT_HEADER, "implenum.h") <= 2**31 - 1:
            pytest.skip(
                f"this host's {backend_name} reports ROLL_NEXT inside int range while its C compiler gives "
                f"it {value} -- parser and compiler disagree, so the writer cannot see the overflow"
            )

        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "implenum",
            backend_name=backend_name,
            header=IMPLICIT_HEADER,
            filename="implenum.h",
        )
        result = _run_python(
            "import implenum",
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "IMPLENUM_LIBRARY": str(library)},
        )
        assert result.returncode != 0, (
            f"the package imported, so an enum holding {value} was bound as a signed 32-bit member"
        )
        assert "SyntaxError" in result.stderr or "NameError" in result.stderr, (
            f"the import failed for some reason other than the refused enum:\n{result.stderr}"
        )

    def test_an_unevaluated_enumerator_is_refused_where_the_backend_left_it(
        self, tmp_path: Path, backend_name: str
    ) -> None:
        """An enumerator a backend did not evaluate has no width, so it is refused.

        libclang evaluates ``1 << 3`` and stores the integer 8, which is sizable;
        tree-sitter stores the string ``"1 << 3"``, which is not. Nothing here
        evaluates C text to find out -- guessing is what produces a member of the
        wrong width -- so under tree-sitter the enum keeps its C spelling and the
        import fails loudly. The asymmetry is the honest contract: this gate
        exists so that the conservative branch is exercised at all, since every
        other fixture in this file uses plain integer enumerators.
        """
        library = _build_c_library(tmp_path, "exprenum", header=EXPR_HEADER, source=EXPR_SOURCE, basename="exprenum")
        root = _scaffold_to(
            "ctypes", tmp_path, "exprenum", backend_name=backend_name, header=EXPR_HEADER, filename="exprenum.h"
        )
        script = textwrap.dedent("""\
            import ctypes
            from exprenum import _bindings as b

            assert b.EXPR_B == 8
            assert ctypes.sizeof(b.ExprHolder) == b._lib.expr_size(), (
                f"ctypes lays out {ctypes.sizeof(b.ExprHolder)} bytes, "
                f"the C compiler lays out {b._lib.expr_size()}"
            )
            assert b._lib.expr_m(b.ExprHolder(m=8)) == 8
            print("EXPR-MATCH")
        """)
        result = _run_python(
            script, cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "EXPRENUM_LIBRARY": str(library)}
        )
        if backend_name == "libclang":
            assert result.returncode == 0, f"an evaluated enumerator should be sizable:\n{result.stderr}"
            assert "EXPR-MATCH" in result.stdout
        else:
            assert result.returncode != 0, (
                "tree-sitter sized an enum whose enumerator it never evaluated; if the backend now "
                "evaluates initialisers, this gate should assert the success branch instead"
            )
            assert "NameError" in result.stderr, (
                f"the tree-sitter failure is not the expected unbound name:\n{result.stderr}"
            )

    def test_a_record_reached_through_an_include_fails_loudly(self, tmp_path: Path, backend_name: str) -> None:
        """An included record resolves by name and still cannot import -- loudly.

        This is the record half of the ``#include`` question and it does not have
        the enum's answer. Under libclang the spelling is now resolved correctly:
        ``struct Swatch`` becomes the class ``Swatch``. The module still fails,
        because declarations are emitted in the order the backend reports them
        and libclang reports the *including* file first, so ``class Swatch`` is
        written after the record whose ``_fields_`` names it. Under tree-sitter
        the declaration is never seen at all.

        Both are ``NameError`` at import, which is the property worth pinning: a
        member silently typed as something else would be the defect this change
        exists to avoid. Fixing the ordering means emitting records in dependency
        order, which is a different defect in a different part of the writer, and
        it is loud on ``main`` today as well.
        """
        library = _build_c_library(
            tmp_path,
            "swuser",
            header=INCLUDING_RECORD_HEADER,
            source=INCLUDING_RECORD_SOURCE,
            basename="swuser",
            extra_headers={"swatch.h": INCLUDED_RECORD_HEADER},
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "swuser",
            backend_name=backend_name,
            header=INCLUDING_RECORD_HEADER,
            filename=str(tmp_path / "swuser.h"),
        )
        result = _run_python(
            "import swuser",
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "SWUSER_LIBRARY": str(library)},
        )
        assert result.returncode != 0, (
            "the package imported; if records are now emitted in dependency order, "
            "this gate should assert the size and round-trip instead"
        )
        assert "NameError" in result.stderr, f"the failure is not the expected unbound name:\n{result.stderr}"

    def test_tag_named_records_match_the_c_abi_in_every_position(self, tmp_path: Path, backend_name: str) -> None:
        """A record named by its tag must resolve to its class, wherever it appears.

        ``struct Inner`` is two words; Python needs one. It is the same defect as
        ``enum E`` and in the same function, and it reached the module in every
        position a type can occupy -- a member, a union member, an array element,
        a pointee, and both halves of a function signature.

        A record has a real answer rather than a default, so unlike the enum case
        the assertion can be exact: the class this writer emits. Sizes come from
        the C compiler's own ``sizeof`` and every member crosses the ABI by value,
        because a member resolved to the wrong class still imports.

        ``struct Gauge`` beside ``typedef unsigned char Gauge`` is the control: the
        tag must keep meaning the eight-byte record while an ordinary identifier
        of the same spelling means one byte.
        """
        library = _build_c_library(tmp_path, "recs", header=RECORD_HEADER, source=RECORD_SOURCE, basename="recs")
        root = _scaffold_to(
            "ctypes", tmp_path, "recs", backend_name=backend_name, header=RECORD_HEADER, filename="recs.h"
        )
        script = textwrap.dedent("""\
            import ctypes
            from recs import _bindings as b

            lib = b._lib
            for name, size_fn in (
                ("ByTag", lib.bytag_size),
                ("UnionMember", lib.unionmember_size),
                ("RecArray", lib.recarray_size),
                ("RecPointer", lib.recpointer_size),
                ("Shadowed2", lib.shadowed2_size),
            ):
                cls = getattr(b, name)
                assert ctypes.sizeof(cls) == size_fn(), (
                    f"{name}: ctypes lays out {ctypes.sizeof(cls)} bytes, "
                    f"the C compiler lays out {size_fn()}"
                )

            # The tag-spelled member is the class, not some other reading of it.
            assert b.ByTag._fields_[0][1] is b.Inner, "the tag-named member is not the emitted class"
            assert lib.bytag_n_b(b.ByTag(n=b.Inner(a=1, b=7))) == 7, "the struct member did not survive the call"
            assert lib.unionmember_i(b.UnionMember(u=b.Choice2(i=5))) == 5, "the union member did not survive"

            arr = (b.Inner * 4)(b.Inner(a=0, b=10), b.Inner(a=0, b=11), b.Inner(a=0, b=12), b.Inner(a=0, b=13))
            got = [lib.recarray_at(b.RecArray(arr=arr), i) for i in range(4)]
            assert got == [10, 11, 12, 13], f"the array of records did not survive the call: {got}"

            inner = b.Inner(a=0, b=21)
            assert lib.recpointer_b(b.RecPointer(p=ctypes.pointer(inner))) == 21, "the pointer member is wrong"

            # Both halves of a signature, tag-spelled on each side.
            lib.inner_sum.argtypes = [b.Inner]
            lib.inner_sum.restype = ctypes.c_int
            assert lib.inner_sum(b.Inner(a=2, b=3)) == 5, "the tag-named parameter did not survive"
            lib.inner_make.restype = b.Inner
            assert lib.inner_make(4, 9).b == 9, "the tag-named return type did not survive"

            # Control: the tag still means the record, not the ordinary identifier.
            assert ctypes.sizeof(b.Gauge) == 8, f"the tag lost to the typedef: {ctypes.sizeof(b.Gauge)}"
            assert lib.shadowed2_hi(b.Shadowed2(g=b.Gauge(lo=1, hi=6))) == 6, "the shadowed tag member is wrong"
            print("RECORDS-MATCH")
        """)
        result = _run_python(script, cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "RECS_LIBRARY": str(library)})
        assert result.returncode == 0, f"tag-named records are not usable:\n{result.stderr}"
        assert "RECORDS-MATCH" in result.stdout

    def test_a_tag_named_record_inside_a_packed_record_imports(self, tmp_path: Path, backend_name: str) -> None:
        """The packed-record branch's consumer: the module has to import at all.

        Size and round-trip are deliberately not asserted. This writer emits no
        ``_pack_``, so a packed record is laid out at natural alignment and both
        would disagree with C for a reason unrelated to tag spelling. What the
        waiting branch needs from here is that a packed record with a tag-named
        nested member produces an importable module and a real nested class.
        """
        library = _build_c_library(
            tmp_path,
            "packrec",
            header=PACKED_RECORD_HEADER,
            source=PACKED_RECORD_SOURCE,
            basename="packrec",
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "packrec",
            backend_name=backend_name,
            header=PACKED_RECORD_HEADER,
            filename="packrec.h",
        )
        script = textwrap.dedent("""\
            import ctypes
            from packrec import _bindings as b

            assert issubclass(b.Squeezed, ctypes.Structure)
            names = dict((n, t) for n, t, *_ in b.Squeezed._fields_)
            assert names["n"] is b.Tiny, f"the nested tag-named member is {names['n']}, not the emitted class"
            assert b.Squeezed(n=b.Tiny(a=5)).n.a == 5, "the nested record does not round-trip in Python"
            print("PACKED-IMPORTS")
        """)
        result = _run_python(
            script, cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "PACKREC_LIBRARY": str(library)}
        )
        assert result.returncode == 0, f"a packed record with a tag-named member is unusable:\n{result.stderr}"
        assert "PACKED-IMPORTS" in result.stdout

    def test_an_incomplete_record_is_not_resolved_to_a_class_that_does_not_exist(
        self, tmp_path: Path, backend_name: str
    ) -> None:
        """A record with no definition has no class, and none is invented for it.

        ``struct Unknown`` is only ever pointed at, so this writer emits no class
        for it and the table carries no entry. The resolution is a plain lookup
        with no fallback, which is what keeps a member from being bound to a name
        nothing defines. The import must fail rather than produce a module whose
        first use of the member dies somewhere else.
        """
        library = _build_c_library(tmp_path, "opaque", header=OPAQUE_HEADER, source=OPAQUE_SOURCE, basename="opaque")
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "opaque",
            backend_name=backend_name,
            header=OPAQUE_HEADER,
            filename="opaque.h",
        )
        result = _run_python(
            "import opaque",
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "OPAQUE_LIBRARY": str(library)},
        )
        assert result.returncode != 0, "the package imported; an incomplete record was bound to something"
        assert "SyntaxError" in result.stderr or "NameError" in result.stderr, (
            f"the import failed for some reason other than the incomplete record:\n{result.stderr}"
        )

    def test_a_namespaced_cpp_record_member_matches_the_cpp_abi(self, tmp_path: Path, backend_name: str) -> None:
        """``n::Inner`` is the record twin of the namespaced enum, and broke the same way."""
        library = _build_cpp_library(
            tmp_path, "nsrec", header=NS_RECORD_HEADER, source=NS_RECORD_SOURCE, basename="nsrec"
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "nsrec",
            backend_name=backend_name,
            header=NS_RECORD_HEADER,
            filename="nsrec.hpp",
        )
        script = textwrap.dedent("""\
            import ctypes
            import os
            from nsrec import _bindings as b

            lib = ctypes.CDLL(os.environ["NSREC_LIBRARY"])
            lib.nsrec_size.restype = ctypes.c_int
            assert ctypes.sizeof(b.NsRecHolder) == lib.nsrec_size(), (
                f"ctypes lays out {ctypes.sizeof(b.NsRecHolder)} bytes, "
                f"the C++ compiler lays out {lib.nsrec_size()}"
            )
            lib.nsrec_b.argtypes = [b.NsRecHolder]
            lib.nsrec_b.restype = ctypes.c_int
            assert lib.nsrec_b(b.NsRecHolder(m=b.Inner(a=1, b=4))) == 4, "the namespaced record member is wrong"
            print("NSREC-MATCH")
        """)
        result = _run_python(script, cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "NSREC_LIBRARY": str(library)})
        assert result.returncode == 0, f"the namespaced record member is not usable:\n{result.stderr}"
        assert "NSREC-MATCH" in result.stdout

    def test_an_enum_reached_through_an_include(self, tmp_path: Path, backend_name: str) -> None:
        """An enum declared in an included header, which the backends see differently.

        libclang follows the ``#include`` and sees the ``Enum`` declaration, so it
        can recognise the member's type *and* prove its width: the member resolves
        and the record matches what C laid out. The record half of this question
        has a different answer and lives in its own gate below.

        tree-sitter does not process includes, and it reduces every enum reference
        to its bare name, so it sees neither the declaration nor the ``enum``
        keyword the source actually wrote. Nothing header-local can resolve that,
        and this gate does not pretend otherwise -- what it pins is that the
        failure stays **loud**. A member silently typed ``c_int`` on a guess would
        be the defect this whole change exists to avoid, so the asymmetry is the
        contract, not a gap in the test.
        """
        library = _build_c_library(
            tmp_path,
            "incluser",
            header=INCLUDING_HEADER,
            source=INCLUDING_SOURCE,
            basename="incluser",
            extra_headers={"palette.h": INCLUDED_ENUM_HEADER},
        )
        root = _scaffold_to(
            "ctypes",
            tmp_path,
            "incluser",
            backend_name=backend_name,
            header=INCLUDING_HEADER,
            filename=str(tmp_path / "incluser.h"),
        )

        script = textwrap.dedent("""\
            import ctypes
            from incluser import _bindings as b

            assert ctypes.sizeof(b.ShadeHolder) == b._lib.shade_size(), (
                f"ctypes lays out {ctypes.sizeof(b.ShadeHolder)} bytes, "
                f"the C compiler lays out {b._lib.shade_size()}"
            )
            assert b._lib.shade_m(b.ShadeHolder(m=1)) == 1, "the included enum member did not survive the call"
            print("INCLUDE-MATCH")
        """)
        result = _run_python(
            script, cwd=tmp_path, env={"PYTHONPATH": str(root / "src"), "INCLUSER_LIBRARY": str(library)}
        )

        if backend_name == "libclang":
            assert result.returncode == 0, f"the included enum is not usable under libclang:\n{result.stderr}"
            assert "INCLUDE-MATCH" in result.stdout
        else:
            assert result.returncode != 0, (
                "tree-sitter resolved an enum it never saw declared; if the backend now follows "
                "includes, this gate should assert the success branch instead"
            )
            assert "NameError" in result.stderr, (
                f"the tree-sitter failure is not the expected unbound name:\n{result.stderr}"
            )

    def test_the_library_name_is_separable_from_the_package_name(self, tmp_path: Path, backend_name: str) -> None:
        """Scaffolding ``probe_bindings`` around ``libprobe`` must produce a usable package.

        The two names coincide only by accident, and a gate whose fixture makes
        them equal cannot see the difference at all. The default is asserted in
        the same test: without the option the package looks for a library named
        after itself and fails, which is what makes the option load-bearing
        rather than decorative.
        """
        library = _build_c_library(tmp_path, "probe")

        named = _scaffold_to(
            "ctypes",
            tmp_path,
            "probe_bindings",
            backend_name=backend_name,
            options={"library": "probe"},
        )
        result = _run_python(
            "import probe_bindings; assert probe_bindings.thing_add(2, 3) == 5; print('CALLED')",
            cwd=tmp_path,
            env={"PYTHONPATH": str(named / "src"), "PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"the package could not load the library it was told to:\n{result.stderr}"
        assert "CALLED" in result.stdout

        # Negative control: the package name is the fallback, and it is wrong here.
        defaulted = _scaffold_to("ctypes", tmp_path, "probe_bindings", backend_name=backend_name)
        fallback = _run_python(
            "import probe_bindings",
            cwd=tmp_path,
            env={"PYTHONPATH": str(defaulted / "src"), "PROBE_BINDINGS_LIBRARY": ""},
        )
        assert fallback.returncode != 0, "the package found a library that is not installed under that name"
        # ``[1]``, not ``[-1]``: on a one-element split ``[-1]`` is the whole
        # stderr, which contains the package name in every traceback path, so
        # the control would pass for a module broken in some unrelated way.
        # The OSError check is what makes the index safe and the failure
        # specific to the library lookup.
        assert "OSError" in fallback.stderr, f"the fallback did not fail on the missing library:\n{fallback.stderr}"
        assert "probe_bindings" in fallback.stderr.split("OSError", 1)[1], (
            f"the fallback did not look for the package's own name:\n{fallback.stderr}"
        )

    def test_import_fails_when_the_native_library_is_absent(self, tmp_path: Path, backend_name: str) -> None:
        """AGENTS.md §1: the tripwire must fail when the binary is missing.

        The generated tripwire imports ``_bindings``, so the import is where the
        absence has to be detected. A module that imports cleanly without its
        library and defers the failure to the first call is the green mirage
        that invariant forbids.
        """
        root = _scaffold_to("ctypes", tmp_path, "nosuchlib_probe", backend_name=backend_name)

        result = _run_python(
            "import nosuchlib_probe",
            cwd=tmp_path,
            env={"PYTHONPATH": str(root / "src"), "NOSUCHLIB_PROBE_LIBRARY": ""},
        )
        assert result.returncode != 0, "the package imported with no native library present"
        # A bare non-zero exit would be satisfied by the module being broken for
        # some unrelated reason -- which is how this file's first run passed
        # while the package could not import at all. The failure has to be
        # *about* the missing library.
        assert "OSError" in result.stderr, f"the import did not fail on the missing library:\n{result.stderr}"
        assert "nosuchlib_probe" in result.stderr.split("OSError", 1)[1], (
            f"the failure does not name the library it could not find:\n{result.stderr}"
        )

    def test_generated_tests_pass_against_the_real_library(self, tmp_path: Path, backend_name: str) -> None:
        """Both generated self-checking test files must themselves run and pass.

        Both are named, not just ``test_tripwire.py``: the tripwire and the unit
        test are emitted from separate templates, and naming one leaves the
        other's syntax unexecuted by anything in the repository.

        ``tests/`` as a whole is *not* the target, because the scaffolder also
        emits ``test_workorder.py``, whose Tier 2 and Tier 3 cases fail on
        purpose until a human writes the assertions. A directory-wide run would
        make this gate red for the one reason that means the generator is
        working. The exclusion is not a blind spot: the work-order suite is
        asserted below to collect and to be red, so a work-order file that
        vanished, or that went quietly green, still fails here.
        """
        library = _build_c_library(tmp_path, "probe")
        root = _scaffold_to("ctypes", tmp_path, "probe", backend_name=backend_name)

        result = _run_pytest(
            "tests/test_tripwire.py",
            "tests/test_bindings.py",
            root=root,
            env={"PROBE_LIBRARY": str(library)},
        )
        assert result.returncode == 0, f"generated tests do not pass:\n{result.stdout}\n{result.stderr}"

        # Every file has to have been collected -- a run that found only the
        # tripwire would also exit 0. Asserting the *filenames* rather than a
        # test count says what is actually meant: two tests inside one file
        # would satisfy a count and leave the other file's syntax unexecuted.
        collected = _run_pytest("tests/", "--collect-only", root=root, env={"PROBE_LIBRARY": str(library)})
        assert collected.returncode == 0, f"the generated suite did not collect:\n{collected.stdout}"
        for name in ("test_tripwire.py", "test_bindings.py", "test_workorder.py"):
            assert name in collected.stdout, f"the generated suite did not collect {name}:\n{collected.stdout}"

        # The outstanding work is outstanding. A work-order suite that exits 0
        # has stopped asking for the assertions it exists to ask for.
        work_order = _run_pytest("tests/test_workorder.py", root=root, env={"PROBE_LIBRARY": str(library)})
        assert work_order.returncode != 0, (
            f"the generated work-order suite passes; its stubs no longer fail:\n{work_order.stdout}"
        )

    def test_generated_tripwire_fails_when_the_native_library_is_absent(
        self, tmp_path: Path, backend_name: str
    ) -> None:
        """The §1 invariant, run rather than inferred.

        The two neighbouring gates prove the tripwire passes *with* the library
        and that ``import <pkg>`` fails *without* it. Neither runs the emitted
        tripwire in the failure direction the invariant is actually about, so
        this does. The failure arrives as a collection error, not a test
        failure, because the import sits at module scope -- pinned here so a
        template that moved the import into the test body, and thereby turned a
        hard failure into a reported one, is visible as a change.
        """
        root = _scaffold_to("ctypes", tmp_path, "nosuchlib_probe", backend_name=backend_name)

        result = _run_pytest("tests/test_tripwire.py", root=root, env={"NOSUCHLIB_PROBE_LIBRARY": ""})
        assert result.returncode != 0, "the generated tripwire passed with no native library present"
        assert result.returncode == 2, (
            f"expected pytest's collection-error exit status, got {result.returncode}:\n{result.stdout}"
        )
        assert "error during collection" in result.stdout, f"the tripwire was collected and then ran:\n{result.stdout}"
        assert "OSError" in result.stdout, f"the tripwire did not fail on the missing library:\n{result.stdout}"


# ---------------------------------------------------------------------------
# Nim
# ---------------------------------------------------------------------------


class TestScaffoldedNimPackageCompiles:
    def test_bindings_compile_link_and_execute(self, tmp_path: Path, backend_name: str) -> None:
        """The generated Nim bindings must reach a running binary.

        Nim only emits an ``importc`` type into its generated C when something
        *uses* it, so the program below declares a variable of every generated
        type. Without that the C never mentions ``enum Flags`` and a bad
        ``importc`` compiles cleanly -- which is exactly how this defect stayed
        invisible.
        """
        nim = _require("nim")
        compiler = _require("cc", "gcc", "clang")
        root = _scaffold_to("nim", tmp_path, "probe", backend_name=backend_name)

        work = tmp_path / "nimrun"
        work.mkdir()
        (work / "probe.h").write_text(FIXTURE_HEADER, encoding="utf-8")
        (work / "probe.c").write_text(FIXTURE_SOURCE, encoding="utf-8")
        shutil.copy(root / "src" / "probe" / "bindings.nim", work / "bindings.nim")
        (work / "main.nim").write_text(
            textwrap.dedent("""\
                import bindings

                var f: Flags = FLAG_D
                var m: Mode = MODE_Y
                var m2: Mode2 = MODE2_Y
                var b: Bare = BARE_B
                var r: Rec
                var v: FooAlias
                r.a = 7
                v.f = 9
                doAssert thing_add(2, 3) == 5
                doAssert rec_a(r) == 7
                doAssert foo_a(v) == 9
                doAssert ord(f) == 8
                doAssert ord(m) == 1
                doAssert ord(m2) == 1
                doAssert ord(b) == 1
                echo "RAN"
            """),
            encoding="utf-8",
        )

        obj = subprocess.run(  # noqa: S603
            [compiler, "-c", "probe.c", "-I.", "-o", "probe.o"],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert obj.returncode == 0, f"fixture object failed to build:\n{obj.stderr}"

        build = subprocess.run(  # noqa: S603
            [nim, "c", "--hints:off", "--nimcache:nimcache", "--passC:-I.", "--passL:probe.o", "-o:main", "main.nim"],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, f"generated Nim bindings do not compile:\n{build.stdout}\n{build.stderr}"

        run = subprocess.run(  # noqa: S603
            [str(work / ("main.exe" if sys.platform == "win32" else "main"))],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, f"generated Nim binary failed:\n{run.stdout}\n{run.stderr}"
        assert "RAN" in run.stdout

    @pytest.mark.parametrize("generated", ["tests/test_tripwire.nim", "tests/test_probe.nim"])
    def test_generated_nim_tests_compile(self, tmp_path: Path, backend_name: str, generated: str) -> None:
        """Both emitted Nim test files must be syntactically valid Nim.

        They are produced by separate templates, and the indentation defect that
        made them unparseable shows up only once the header declares more than
        one function -- so nothing short of handing them to the compiler
        detects it. Compilation stops at ``--compileOnly``: the tripwire wants
        the shared library at *runtime*, which the ctypes gates already cover.
        """
        nim = _require("nim")
        root = _scaffold_to("nim", tmp_path, "probe", backend_name=backend_name)
        (root / "probe.h").write_text(FIXTURE_HEADER, encoding="utf-8")

        build = subprocess.run(  # noqa: S603
            [
                nim,
                "c",
                "--hints:off",
                "--compileOnly",
                "--nimcache:nimcache",
                "--path:src",
                "--passC:-I.",
                generated,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, f"generated {generated} does not compile:\n{build.stdout}\n{build.stderr}"
