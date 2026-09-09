# Test work orders

When headerkit scaffolds a project it also generates that project's tests. Generated
tests have a hard limit: a generator knows what an API *is*, but not what it is *for*.
Writing tests mechanically past that limit produces assertions that cannot fail.

So headerkit splits generated tests into three tiers and is explicit about which is
which.

## Tier 1 -- real, passing tests

Where the IR determines both the call and the expected result, headerkit writes the
whole test and it passes as generated:

- **Struct field round-trip** -- set every scalar integer field, read it back. The
  value driven into each field is bounded by its declared width, so a struct of 200
  `uint8_t` fields does not generate a test that fails as generated.
- **Unsigned bit-field bounds** -- a `:3` field holds 7 and truncates 8. Derived from
  the declared width, not guessed.
- **Enum value coverage** -- every enumerator is importable and holds its declared
  value.

These produce no work-order entry. There is nothing to do.

**What a round-trip proves depends on the target.** The ctypes round-trip goes through
a real `Structure` whose layout the writer derived from the header. The Nim round-trip
writes and reads a field of a Nim object without calling into C, so it proves the field
is declared and holds what was written, and nothing about the ABI. The generated Nim
test says so in its own title rather than borrowing the stronger wording.

These exclusions are deliberate, because in each case the IR does *not* in fact
determine the answer:

- **Signed bit-fields** are skipped: sign extension makes the truncated value
  ABI-dependent.
- **Non-integer fields** (char, float, nested structs) are skipped from round-trip:
  they cannot be driven with a plain integer.
- **Enums whose values a backend left unevaluated** are skipped rather than guessed.
- **Bit-field bounds in Nim** are skipped: Nim bindings carry no bit-field width. A
  header whose Tier 1 result is *only* bit-field bounds therefore leaves a Nim project
  with no work-order files at all, rather than a `suite` with an empty body -- which is
  not valid Nim, and which regeneration would never repair, since the file is written
  only if absent.
- **Records and enums a target language cannot spell** -- a qualified name such as
  `ns::E` -- are skipped: the test would have to name the type directly, and there is
  no reference to emit.

A C++ symbol whose name is not an identifier -- `operator==`, `operator*` -- still gets
its stub. The generated test function is named after a sanitised form (`test_operator_eq_eq`)
while the failure message and `WORK_ORDER.md` keep the spelling the header uses.
Punctuation maps to distinct words, so two operators never collapse onto one test name.

## Tier 2 -- the cases are known, the expectation is not

headerkit knows how many cases there are, but not what each should do. It emits one
failing case per value, using `@pytest.mark.parametrize` in Python and the
`parametrizedTest` macro in Nim:

- a function taking an enum -- one case per enumerator;
- a C++ overload set -- one case per overload;
- a function taking a pointer -- a NULL case, *alongside* a case for what the function
  is actually for. The NULL question never displaces the more valuable one.

## Tier 3 -- pure semantics

What does `parse_config()` do? No IR knows. One failing stub per remaining function.

## Where the instructions live

The instruction is attached to the failing assertion, not collected in a banner at the
top of the file, because a note at the top of a file gets skimmed and a message on the
failure you are looking at gets read:

```
FAILED tests/test_workorder.py::test_ct_scale[null_s] - Failed: WORK ORDER: for the
`behaviour` case, describe what `ct_scale` is for and assert it. For the `null_s` case,
decide what it does when `s` is NULL -- an error, or undefined and therefore
untestable? Done means: call it and assert on the result. Asserting that it does not
raise is insufficient.
```

Each stub's docstring additionally carries the signature verbatim, so the reader does
not have to open the header.

## Generated files

| File | Purpose |
|---|---|
| `tests/test_workorder.py` / `.nim` | Tier 1 tests and Tier 2/3 stubs |
| `tests/workorder_dsl.nim` | Nim only: the `parametrizedTest` macro |
| `WORK_ORDER.md` | The same outstanding list in prose |
| `SUGGESTIONS.md` | Static, generic wrapper design ideas |
| `AGENTS.md` | Points the next session at the work order |

## Progress

The test run *is* the progress meter. A stub that has been written turns from red to
green. There is no manifest, no completion tracker and no ID scheme to keep in sync:
a second source of truth would only drift from the first.

Delete a line from `WORK_ORDER.md` as you finish it. When it is empty, delete the file.

## Regeneration safety

Every file in the table above is written **only if it does not already exist**.
Re-running headerkit over a project regenerates the bindings module but never touches
a test you have written, the work order you have edited, or the project's `AGENTS.md`.

The trade-off: a symbol added to the header after the first scaffold does not gain a
stub in the existing file. Delete the file and regenerate if you want it rebuilt.

## The Nim DSL

`std/unittest` cannot express this with a loop. A runtime `for` inside one `test`
block collapses every iteration into a single reported result, and iterating a holey
enum -- which C headers produce almost universally -- does not compile at all. So
headerkit emits a ~25-line macro, `std/unittest` and `std/macros` only, that expands
at compile time to one discrete `test` per enumerator.

`require` is never emitted: it sets `abortOnError` and kills the whole run, so later
suites would never execute. `checkpoint` always precedes `fail()`, or the message is
lost.
