# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- CI: `ci.yml`, `check-release-prep.yml` and `test-install-libclang.yml` run on every pull request, not only on pull requests into `main`. Each carried a `branches: [main]` filter on its `pull_request` trigger, so a stacked pull request -- one based on another feature branch -- matched none of them and **no workflow ran on it at all**. The failure was silent rather than loud: an untested pull request shows an empty checks list and `gh pr checks` reports "no checks", so "CI passed" and "CI never ran" were nearly indistinguishable on the page. The `push` triggers stay scoped to `main`; they guard the trunk after a merge and deliberately skip feature branches, which the `pull_request` trigger now covers. `release.yml` and `auto-tag.yml` have no `pull_request` trigger and are unchanged, and `momus.yml` was already unfiltered.
- CI: `check-release-prep.yml` runs only when the pull request's base is the default branch. A stack of pull requests is one release -- the parent carries the version bump and the changelog entry, and a child stacked on it correctly carries neither -- so comparing a child against its parent produced two confident and wrong warnings, and following them yields a doubled version and a duplicate entry. False warnings on a warn-only check train people to ignore it on exactly the pull requests into `main` where it is load-bearing. Stacked pull requests still gain `ci.yml` coverage. The base ref is still read from `github.event.pull_request.base.ref` rather than hardcoded: the gate is written against `github.event.repository.default_branch`, so a literal would silently disagree with it if the default branch were renamed.
- CI: `ci.yml`, `check-release-prep.yml` and `test-install-libclang.yml` declare `permissions: contents: read`. They were the only pull-request-triggered workflows inheriting the default `GITHUB_TOKEN` scope, and `test-install-libclang.yml` runs pull-request-authored code under `sudo`. `check-release-prep.yml` also gains the `concurrency` block the other two already had.

### Fixed

- CI: `check-release-prep.yml` fails loudly when it cannot read the base ref, instead of reporting a reassuring result it did not measure. Neither step declared a `shell:`, so GitHub ran them under `bash -e {0}` with no `pipefail`; in `git show "origin/$BASE_REF:pyproject.toml" | grep ... | sed ...` the exit status comes from `sed`, so a `fatal: invalid object name` was discarded and the step printed `Version bumped:  -> 0.9.9` and exited 0. The changelog step degraded identically, reporting "CHANGELOG.md has not been modified" off an empty diff. Both steps now declare `shell: bash`, set `-euo pipefail`, and verify the base ref resolves before reading it; a missing version line likewise now emits a diagnostic rather than exiting 1 with no output. A parent branch auto-deleted after merge is the ordinary end of a stacked pull request's life, so this path is reached in normal use rather than only pathologically.
- CI: the changelog check matches with a `case` statement rather than a pipe into `grep -q`. `grep -q` exits on its first match and closes the pipe; because `CHANGELOG.md` sorts near the top of `git diff --name-only`, on a diff exceeding the 64K pipe buffer the writer was still writing, took SIGPIPE, and `pipefail` reported that as the pipeline's status -- so the check warned that the changelog was untouched when it had in fact been modified. Measured at 24012 and 48012 bytes (correct) against 72012 and 240012 bytes (wrong), with the same input passing under the pre-`pipefail` code, placing the flip at roughly 1300 changed paths. The `case` form has no pipe by construction and is correct at every size tested, with warn-only semantics and exit 0 preserved on a genuine miss.

## [0.38.1] - 2026-09-07

### Fixed

- `LibclangBackend`: an object-like macro whose replacement list is *declaration specifiers* rather than a constant expression is no longer emitted as an `int` constant. `#define PyMODINIT_FUNC __declspec(dllexport) PyObject *` produced `int PyMODINIT_FUNC`, so a consumer generating Cython emitted `__Pyx_PyLong_From_int(PyMODINIT_FUNC)` and the C compiler rejected the expansion (`use of undeclared identifier 'dllexport'`). The token-validation loop accepted every identifier as an operand and never checked that the tokens *composed* into an expression. Two checks now run before a multi-token replacement list is accepted. First, a token clang classifies as a **keyword** disqualifies it unless that keyword can occur inside a constant expression -- so `static`, `extern`, `inline`, `__cdecl`, `__stdcall`, `__fastcall` and the C++ literals `true` / `false` / `nullptr` disqualify, while the type specifiers, `const` / `volatile`, the `struct` / `union` / `enum` tags and `sizeof` / `alignof` do not: a cast and a `sizeof` are ordinary values, and a gate that rejected every keyword deleted `#define X ((int)0x1F)`, `#define X ((unsigned long)-1)` and `#define X sizeof(int)` along with the declaration specifiers. Second, the remaining tokens must form a **well-formed constant expression**, which is where a type name outside a cast is rejected -- `const char *`, `unsigned int`, `struct PyObject` -- alongside two adjacent operands (`__declspec ( dllexport ) PyObject`), a trailing binary operator (`PyObject *`, `PyObject &`), call syntax (`__declspec ( dllimport )`) and unbalanced parentheses. A parenthesised type name in operand position with something after it is read as a cast, which consumes no operand. A lone identifier counts as a type name, because `size_t`, `uint32_t` and every project typedef arrive as one identifier and are commoner in real headers than the keyword spellings, so `#define X ((size_t)1)`, `#define X ((uint32_t)0x1F)` and `#define X ((mystruct_t *)0)` keep their classification. That reading is safe in this position for two independent reasons: a run is only treated as a cast when a token follows the `)`, so `#define B (A)` is still grouping and keeps its value; and where a token does follow, grouping would leave two adjacent operands, which C has no rule for, so a cast is the only reading that parses. The type name must also name a type, and differential fuzzing of the predicate against the C compiler found five shapes that are built from admissible tokens and are not types or not values -- `(const)X` is implicit int, which C99 removed; `(void)X` discards its operand rather than yielding one; `int char` names no type; C has no cast to a struct, union or enum type, only to a pointer to one; and a qualifier cannot sit between a tag keyword and its name. All five are rejected. `sizeof(void)` is admitted, because a cast to `void` yielding no value says nothing about `sizeof`, which the compiler accepts and evaluates to 1. Both rules read clang's own token classification and the C expression grammar, not a denylist of extension spellings. Genuine constant macros are unaffected: `#define SIZE 100`, `#define PI 3.14`, `#define VERSION "1.0"`, `#define NEG -1`, `#define HEX 0x1F`, `#define SHIFTED (1 << 4)`, `#define B (A + 1)`, `#define PICK (1 ? 2 : 3)`, `#define X ((int)0x1F)`, `#define X sizeof(int)` and `#define X ((const char *)0)` all keep their previous type and value, which is asserted by cythonizing the generated binding, compiling it, importing it and reading the values back. Previously only the GNU spelling `__attribute__((visibility("default"))) PyObject *` was rejected, and incidentally so -- by the string-literal guard reacting to `"default"`. The MSVC spelling carries no string literal, so nothing caught it; the defect was never platform-specific.

## [0.38.0] - 2026-09-05

### Changed

- `LibclangBackend`: whether `#include` directives are followed is now decided solely by the caller's `recursive_includes` flag. An `_is_umbrella_header` heuristic previously vetoed recursion unless the header had at least three non-system includes *and* fewer than three declarations of its own, overruling an explicit caller instruction based on the header's shape. A single magic number served two unrelated quantities -- a minimum include count and a maximum declaration count -- and its failure was silent: a forwarding header with one or two project includes produced an empty result with no error, and adding a third `#include` changed the outcome though the caller's intent had not. Scope remains bounded by the mechanisms that state it directly: `_is_system_header` with `project_prefixes`, `allowlist`/`denylist`, `max_depth`, and the visited set. Headers with one or two project includes now expand where they previously did not. The private `_is_umbrella_header` function is removed.
- `ParserBackend.parse` / `LibclangBackend.parse`: the include filter now narrows the result in **both** traversal modes. It previously ran on only one of the two routes a declaration can take: `_parse_recursively` merged each included header wholesale as its own translation unit, while the filter governed only the separate admission of preprocessor-inlined text into the main translation unit. The filter was therefore a silent no-op whenever `recursive_includes` was on, which is the default -- `allowlist=["no_such_file.h"]` and `allowlist=["other.h"]` and no allowlist at all produced byte-identical output. The filter now also prunes the descent, so an included header the caller did not allow, or explicitly denied, is neither descended into nor admitted. `recursive_includes=True` with `allowlist=["other.h"]` now yields the main file's declarations plus `other.h`'s alone, where it previously yielded every non-system include; `recursive_includes=True` with an allowlist matching nothing now yields the main file alone, where it previously yielded everything. The unfiltered and non-recursive rows are unchanged. Matching remains on whole absolute, symlink-resolved paths, never substrings. The full `recursive_includes` x `allowlist` matrix is pinned by tests, including the three recursive rows whose previous assertions recorded the no-op.

### Added

- `ParserBackend.parse` and `LibclangBackend.parse` accept keyword-only `allowlist` and `denylist` parameters naming the included files whose declarations are kept or dropped. Both default to `None`, so existing backends remain conformant. Absolute entries are used as-is; relative entries (including a bare basename) resolve against the directory of the parsed file, then `include_dirs`, then the current working directory. An entry containing `*`, `?` or `[` is an `fnmatch` pattern whose directory part resolves by the same rule. Comparison is between whole absolute, symlink-resolved paths, so `er.h` does not match `other.h`. An entry matching nothing is not an error. **Deny wins over allow**: a file named by both lists is excluded. A `denylist` with no `allowlist` means "everything except these". The parsed file itself cannot be denied -- honoring that would return an empty header for the file the caller explicitly asked to parse, with nothing in the result to say why. `TreeSitterBackend` accepts both parameters and warns for either, per the entry below.

- Regression guard pinning the reproducibility of generated Cython output. `PxdWriter._early_reference_forwards` derives its result from a `set` of type names, so before it sorted them the forward-declaration block inherited Python's per-process string hash order and the same header rendered to a different `.pxd` in every process. The guard asserts the forward block is emitted in sorted order while the definitions keep source order, and renders a header in five subprocesses under differing `PYTHONHASHSEED` values and requires one distinct output. Generating twice within a single process cannot detect this, because the seed is fixed for the life of the process.

### Fixed

- `PxdWriter`: a record that is forward-declared and defined in the same block no longer emits the redundant forward declaration, so generated output no longer varies with the LLVM version. `typedef struct X X_t;` names `struct X` through an elaborated type specifier, and libclang up to LLVM 21 -- including Apple clang 21 -- surfaces that as a body-less `StructDecl` cursor of its own, while LLVM 22 does not. The writer emitted whatever it was handed, so the same header rendered with a stray `cdef struct X` on one toolchain and without it on another; the divergence reached both the acyclic path and the cycle-breaking Phase 3, where the escaped form (`cdef struct class_ "class"`) appeared twice. The line was redundant in every case -- Phase 1 already forwards every record with a body, and the definition follows -- so it is now dropped when the same block defines the record, before the declarations are sorted, which also makes cycle detection see identical input on either toolchain. Measured by rendering the same headers against libclang 19 and libclang 22 in containers: the generated `.pxd` is now byte-identical, where it previously differed by that line. A forward declaration with no definition in the block -- the undeclared-record case that carries the keyword-escape cname -- is untouched.
- `TreeSitterBackend`: an unnamed bitfield whose type ends in a size keyword -- `unsigned short`, `unsigned long`, `unsigned long long`, `long long` -- is no longer dropped. `struct s { unsigned long long : 8; };` parsed to an empty struct while `unsigned int : 8` parsed correctly, so the defect was invisible on the spelling most bitfield code uses. tree-sitter-c's `field_declaration` requires a declarator and recovers `unsigned int : 8` by inserting a MISSING `field_identifier`, which left the `bitfield_clause` intact and was the shape the padding path detected. After a size keyword the parser can still accept a further `primitive_type`, so it cannot commit to that insertion and emits an `ERROR` node wrapping the `:` and the width instead; with no declarator and no `bitfield_clause`, the member matched nothing and was discarded silently. The `ERROR` shape is now recognized by its node structure -- `:` followed by exactly one expression -- and yields the same padding `Field` the MISSING path does, so an unnamed bitfield is carried for every declared type spelling, matching `LibclangBackend`. Because an unnamed bitfield is padding rather than a member, a dropped one produced a wrong layout rather than a missing declaration: the ctypes writer placed the following member in the padding's bits, and for `struct { unsigned long long a : 3; unsigned long long : 8; unsigned long long b : 5; }` the generated record kept the correct `sizeof` and still read `b` back as the value written, so only the byte image against a compiled C probe distinguished it -- `0d00000000000000` where C gives `0508000000000000`. An `ERROR` node that is not shaped like a bitfield clause, which invalid C also produces, is ignored rather than mined for a width. Named bitfields take the declarator path and were never affected. tree-sitter's source-spelling policy is unchanged, so `short int` stays `short int` where libclang canonicalizes it to `short`.
- `LibclangBackend`: system headers are classified by asking clang rather than by matching path fragments alone, so libc internals no longer leak into every binding generated on Linux. `_is_system_header` matched the literal fragment `clang/include`, but clang's resource directory is versioned -- `/usr/lib/llvm-19/lib/clang/19/include` on Debian and Ubuntu, `/usr/lib/gcc/x86_64-linux-gnu/13/include` for gcc -- so the marker and the `include` component are never adjacent there and nothing matched. macOS masked it completely: its resource directory sits under `/Library/Developer/CommandLineTools`, which a prefix already covered, so the same code classified correctly on one platform and not on the other. With the `_is_umbrella_header` veto removed earlier in this release, this classification is the only thing bounding recursion, and the measured consequence on Linux was that `#include <stdio.h>` beside a project header contributed `NULL`, `size_t`, `ptrdiff_t`, `wchar_t` and `max_align_t` to the output. Each included file's classification is now read from `clang_Location_isInSystemHeader`, which reports how clang itself treated the search path the file was found on -- its resource directory, its `-isystem` entries, and the platform defaults -- and is therefore correct on toolchain layouts no heuristic anticipates. The path heuristic remains as the fallback for paths clang has not classified, and it too now recognizes a versioned resource directory by looking for a `clang`, `gcc`, `g++` or `c++` directory component within three components of an `include` or `include-fixed` one, rather than requiring the two to be adjacent. `project_prefixes` still overrides every system classification, including clang's, so an umbrella header installed under a system location is still descended into. `TreeSitterBackend` does not follow includes and is unaffected. Verified by running the suite in a Linux container, not only on macOS, and the Linux path shapes are pinned by tests that run on every host.
- `LibclangBackend`: an enum tag that is declared but never defined is no longer discarded. `_process_enum` returned on every cursor that was not a definition, so `enum E;` with no matching definition anywhere in the translation unit vanished -- the type did not merely lose its values, it lost its only declaration. A neighbouring `void use(enum E *p);` then rendered as `void use(E* p)` against an undeclared `E`, which Cython rejects outright with "'E' is not a type identifier", so the loss was a hard failure rather than a cosmetic one. The opaque *record* path never had this defect: `struct S;` already survived as `cdef struct S`, which made enums the inconsistent case, and `TreeSitterBackend` already kept the declaration. The forward declaration is now skipped only when `cursor.get_definition()` reports a definition in the same unit, so the pre-existing collapse is unchanged in both source orders -- `enum E; enum E { A, B };` and `enum E { A, B }; enum E;` each still yield one declaration carrying the values, and a repeated `enum E; enum E;` still yields one. The valueless enum reaches the Cython writer's existing empty-suite path and is emitted as `cdef enum E: pass`, which is the opaque-handle idiom: an incomplete enum is usable only through a pointer, and a generated binding that obtains one from C and reads it back cythonizes, compiles, links, imports and returns the value the C definition specifies. C++ requires a fixed underlying type to forward-declare a tag (`enum E : int;`), and that spelling is covered too.
- `PxdWriter`: C++ `bool` is emitted with `from libcpp cimport bool` instead of as a bare `bool`. Cython has no builtin of that name, so a bare `bool` in a `.pxd` resolves to the *Python* bool object and the generated C++ stored a C++ bool straight into a `PyObject*` (`__pyx_t_4 = __pyx_v_a.eq(__pyx_v_a);`). Nothing in the chain reported it: the binding cythonized, compiled and linked cleanly, then segfaulted (rc=-11) on the first call. `libcpp/__init__.pxd` declares `ctypedef bint bool` inside a `cdef extern from *` block, which keeps the C spelling `bool` while giving it integer semantics, and the lowering becomes the correct `__Pyx_PyBool_FromLong(...)`. The spelling is faithful where `bint` would not be: `bint` lowers to C `int`, so a `bool (*)(bool)` function-pointer typedef spelled `bint (*)(bint)` is a different C++ type and cannot take the address of a real `bool(bool)` function. C is a separate case and is unchanged: `<stdbool.h>`'s `bool` is a macro for `_Bool`, which libclang canonicalizes and the writer already maps to `bint`, so the C path emits no bare `bool` and acquires no cimport. The cimport header is now rendered after the body rather than before it, so it states what the declarations actually emitted rather than what the IR mentioned; return types, parameters, fields, typedef targets, template arguments and function-pointer signatures are therefore all covered by one rule.
- `PxdWriter`: a C++ conversion operator is no longer dropped without a trace. `LibclangBackend` files `operator bool()`, `operator int()` and the rest under `Struct.conversions`, which this writer never read, so the declaration vanished and a record whose only members were conversions came out as a body-less `cdef cppclass B` -- itself a syntax error. Conversions now render through the same path as ordinary methods, so `operator bool` -- the one conversion Cython declares -- is emitted, and every other conversion is replaced by an `UNSUPPORTED` comment naming the symbol and quoting Cython's own message, on the existing diagnostic channel that dependent names, rvalue returns and unsupported operators already use. The classification comes from `_unsupported_operator_reason`, which already handled conversions correctly, rather than from a second rule.
- `PxdWriter`: `operator,` is emitted instead of being dropped by a hard-coded exclusion set. Cython 3.3 parses the declaration -- verified by compiling it, not by reading the grammar -- so the set contradicted the empirical table the surrounding operator handling is built from. Python syntax offers no way to invoke it from a `.pyx`, but the binding describes the header and dropping it lost API silently. The set is removed; `operator,` is now governed by the same `_unsupported_operator_reason` rule as every other operator. Superseded within this release by the aliasing entry below, which makes the emitted declaration callable rather than merely present.
- `PxdWriter`: `operator,` is emitted through the `operator_aliases` channel as `comma "operator,"`, so a `.pyx` can call it. Emitting it unaliased was not enough: the declaration cythonizes, compiles, links, imports and runs, which is what made it look correct, but nothing can reach it. `a.operator,(b)` is rejected by Cython with "Object of type 'Foo' has no attribute 'operator'", and `a, b` builds a tuple -- measured on the generated C++, that spelling produces **zero** call sites, and the member's body never executes (a member the operator increments by 100 stayed at its initial value through a full compile-link-import-run). Counting `operator,` in the generated C++ does not settle it, because Cython echoes the `.pyx` source into comments; the call site is the statement `__pyx_v_a.operator,(__pyx_v_b);`, not a comment. Aliased, that statement is generated and the C++ body executes: `Conv(3), Conv(7)` returns 307 from `v * 100 + o.v`, a value no other member of the record produces. This is the mechanism `operator->` -> `deref` and `operator()` -> `call` already use for spellings Cython parses but cannot invoke, so `operator,` stops being the one exception. The quoted C name keeps `operator,` in the output, so the binding still describes the header.
- `PxdWriter`: cross-namespace name collisions are disambiguated for enums, typedefs and variables, not only for records. The existing mechanism moved a renamed duplicate into a block carrying no `namespace` of its own and gave it a fully qualified cname, but only `_write_struct` applied it, and `Variable` was not even scanned for collisions. Measured consequences: a `.pyx` reading `v` where `a::v` is 111 and `b::v` is 222.5 cythonized, compiled, linked, imported and *ran*, returning `222.5` -- the generated C++ contained only `PyFloat_FromDouble(b::v)` and `a::v` was unreachable, with Cython emitting a `'v' redeclared` warning at most. Enums were worse: both tags reached the output bare, so the generated C++ failed with `use of undeclared identifier 'X'`. All four kinds now share one code path: `a_v "a::v"`, `cdef enum a_E "a::E"`, `ctypedef int a_alias "a::alias"`. An enum's *enumerators* are qualified too, because an enumerator is a namespace-scope name in C++ rather than a member of the tag, so qualifying the tag alone still emits a bare `X`. The declaration being written now also puts its own namespace in scope, so a function in `namespace a` returning `alias` resolves to `a`'s typedef instead of naming nothing. Unchanged, as before: a declaration in the global namespace keeps its bare name, and a name unique to one namespace is not renamed, so no existing output moves.

- `PxdWriter`: a colliding *enumerator* is now disambiguated even when the enum tags do not collide. The collision scan read tag names only, so `namespace a { enum E { X }; }` beside `namespace b { enum F { X }; }` renamed nothing -- `E` and `F` differ -- while both enumerators reached the flat `.pxd` module as a bare `X`. An enumerator of an unscoped enum is a namespace-scope name in C++, not a member of its tag, so it collides on its own. Measured before the fix, `namespace a { enum E { v = 11 }; }` beside `namespace b { inline int v = 5; }` cythonized, compiled, linked, imported and *ran*, returning `5`: the generated C++ read `b::v` and `a::E::v` was unreachable, with no diagnostic from any tool in the chain. Enumerator names now feed the same `_collect_collisions` map the tags use, and an enum with a colliding enumerator is moved out of its `namespace` block and self-qualifies, exactly as a renamed tag already did. The tag itself is left alone when only its enumerators collide: `cdef enum E "a::E"` keeps the header's spelling and gains only the cname the block no longer supplies. A colliding enumerator becomes `a_X "a::X"`; an enumerator unique to its namespace keeps its own spelling and takes the cname alone (`UNIQ "a::UNIQ"`), which also stops the pre-existing tag-collision path from renaming enumerators that never collided. An enum with no collision at all keeps its `namespace` block, and a global-namespace enumerator keeps its bare name. Scoped enums are handled by the entry below, which supersedes the strict `xfail` that pinned them here.

- `Enum` gains `is_scoped` and `cpp_name`, both populated by `LibclangBackend` and `TreeSitterBackend`, and `PxdWriter` spells a scoped enumerator through its tag. A C++ scoped enumerator is a member of the tag (`a::E::X`) and is not introduced into the enclosing namespace, but the IR modelled no scoping, so `enum class E { X }` was indistinguishable from `enum E { X }` and the writer emitted the `a::X` that no C++ scope declares -- clang rejected it with "no member named 'X' in namespace 'a'". `is_scoped` comes from `cursor.is_scoped_enum()` on libclang and, on tree-sitter, from the `class`/`struct` child token the C++ grammar gives `enum_specifier`, never from the source text. `cpp_name` carries the record-qualified spelling a hoisted member enum otherwise loses: a member enum is lifted to the top level and `namespace` cannot hold a record, so `class C { enum M; }` records `C::M` and renders `cdef enum M "C::M"` with `X "C::M::X"`. A scoped enum leaves its `namespace` block and self-qualifies, because an explicit cname is not qualified by one. Unscoped enums are unchanged in every position, including the `namespace` block they already used: an unscoped enumerator really is a namespace-scope name, so `a::X` is its correct spelling.

- `PxdWriter`: an enumerator's duplicate-scan key is its enclosing *scope* rather than its namespace, and a scoped enumerator's scope is its tag. Keyed on the namespace, every bare enumerator at global scope shared one key, so `enum class G { X }` beside `enum U { X }` was not seen as a duplicate and both reached the flat module as `X`; Cython reported `'X' redeclared` and bound every use to one of them. Measured directly: two enumerators sharing a Cython name but carrying distinct cnames compile, link, import and run, returning the value of whichever came last, with the other's qualified spelling absent from the generated C++ entirely. Enumerators of scoped enums are now named after their tag (`a_E_X "a::E::X"`), which is also the name a caller `cimport`s.

- `LibclangBackend` and `TreeSitterBackend`: two classes declaring the same enum tag no longer collapse to the first. The dedup key was the hoisted bare name, which is `E` for both `C1::E` and `C2::E`, so the second class's enumerators were discarded with no diagnostic. The key is now the record-qualified `cpp_name`, and `PxdWriter`'s duplicate scan reads the same enclosing scope, so the pair renders as `cdef enum C1_E "C1::E"` and `cdef enum C2_E "C2::E"` and both sets of enumerators survive. Both are read back from a compiled extension and checked against the values their header declares.

- `LibclangBackend`: C++ mode is derived from the file extension as well as from the flags, matching clang's own driver. `is_cplus` read only `extra_args`, so a `.hpp` with no `-std=c++` flag left clang parsing C++ -- it infers the language from the suffix -- while the backend simultaneously believed it held C. Every C++-only guard therefore stayed shut on a translation unit clang had already read as C++: `_enum_is_typedef_only` emitted `ctypedef enum Name` for a `.hpp` whose records were rendering as `cdef cppclass`. `.hpp`, `.hh`, `.hxx`, `.h++`, `.H`, `.tcc` and `.tpp` now select C++. `.h` is ambiguous and stays C unless a flag says otherwise, and a flag still wins over the extension in both directions -- an explicit `-x c` forces C on a `.hpp`.

- `PxdWriter`: the bitfield comment reads `# bitfield: 1 bit` rather than `# bitfield: 1 bits`. It is the only count in this writer's generated output that was not pluralized against its value.

- `LibclangBackend`: bitfield widths now reach `Field.bit_width` instead of being discarded. The backend called `cursor.is_bitfield()` only to recognize unnamed padding and never read `clang_getFieldDeclBitWidth`, so `unsigned lo : 4` arrived as an ordinary `unsigned int` and every bitfield in every header parsed by this backend lost its width. The IR has modelled `bit_width` all along and `TreeSitterBackend` populated it, so the two backends disagreed on identical input. Six writers move as a result: `ctypes` emits the `("lo", c_uint, 4)` 3-tuple rather than a 2-tuple, `cffi` and `lua` regain the `: 4` suffix, `json` regains the `bit_width` key, `prompt` regains the `:4b` suffix, and `cython` regains its `# bitfield: 4 bits` comment. The ctypes change is behavioural, not cosmetic: without the width the generated structure laid `lo` and `hi` out as separate 4-byte members, so `sizeof` and every member offset after the first bitfield disagreed with the C compiler, and a value written to `lo` read back unmasked. Cython itself cannot express a width -- `unsigned int lo : 4` in a `cdef struct` is rejected with "C function definition not allowed here" -- but a `cdef extern` block re-uses the real header, so the comment is the whole of what that writer can carry. Unnamed padding (`unsigned : 0`, `unsigned : 3`) is carried on the IR as `Field.is_padding` rather than emitted as a nameless field, and reaches only the writers that need it -- see the padding entry below. Widths inside a C11 anonymous member and inside a class template are covered by their own conversion paths.
- `Field` gains `is_padding`, populated by `LibclangBackend` and `TreeSitterBackend`, and the `ctypes` writer reproduces the layout it describes. An unnamed bitfield is not an accessible member, so both backends dropped it -- correctly for every writer that emits C source, and wrongly for the one writer that rebuilds the layout itself. `PxdWriter` was immune and hid the defect: `cdef extern` defers to the real C header, so every Cython-side check passed while the generated ctypes class had a different `sizeof` and different member offsets than the C compiler, with nothing raising. Measured against a compiled C probe, `struct { unsigned a : 3; unsigned : 0; unsigned b : 5; }` is 8 bytes with `b` at bit 32; the generated class was 4 bytes with `b` at bit 3, and writing `a=5, b=1` produced `0d 00 00 00` where C produces `05 00 00 00 01 00 00 00`. Reading `instance.b` back still answered `1`, so the wrong layout was invisible from Python -- only the byte image showed it. The two backends agreed with each other throughout, so cross-backend parity could not have caught this and is not what pins it now: every expectation is taken from a compiled C probe. `: 0` has no ctypes spelling (a zero width raises for any name, `None` included) and is reproduced by reserving the remainder of the current storage unit, which restores both the size and the bit position. A record whose every entry is padding is carried by a `c_ubyte` array rather than a bitfield carrier, which reproduces C on the ABIs measured at the time; the entry below corrects that to an ABI the measurement had not covered. Padding reaches no other writer: `BaseWriter._prepare` strips it unless a writer opts in with `consumes_padding_fields`, so cffi, cshim, Cython, diff, json, lua, mojo, nim and prompt output is byte-identical and gains no nameless member. Zero-width reset, anonymous padding, padding at the start, at the end, consecutive padding, padding inside a C11 anonymous member, and a record of only padding are each pinned against the C probe on both backends.
- `CtypesWriter`: a C11 anonymous member is emitted as a nested class plus `_anonymous_` instead of `("", None)`, which is not merely mislaid out -- it raises `TypeError: this type has no size` at class creation, so any binding containing one could not be imported. The writer had no anonymous-member handling at all, and `type_to_ctypes(CType("void"))` yielded `None` for the placeholder the backends both produce. The inner record takes its own alignment and so is not flattenable: measured against C, `struct { unsigned top : 2; struct { unsigned x : 3; unsigned : 4; unsigned y : 5; }; }` is 8 bytes with `x` at bit 32, not a continuation of the first storage unit. `_anonymous_` keeps the members reachable as `instance.x`, matching C's transparent-member semantics.
- `LibclangBackend`: an unnamed padding bitfield no longer produces the note "Field '' skipped: unable to represent type 'unsigned int'". Padding is skipped by design and its type is perfectly representable, so the note named a cause that was not the real one -- and the Cython writer rendered it verbatim into generated output as a comment on the enclosing struct.

- `TreeSitterBackend`: an enum declared inside a C++ namespace now records it on `Enum.namespace`, matching `LibclangBackend`. The field was threaded through every conversion path and then dropped at the point the `Enum` was constructed, so `namespace a { enum E {...}; }` and `namespace b { enum E {...}; }` both reported `None`. `_deduplicate_declarations` keys on `(type, name, namespace)`, so the two were one identity and whichever arrived second was discardable -- the tree-sitter output happened to survive only because that backend does not run the pass. The namespace comes from the same `_convert_namespace` context the backend already uses for records, not a second mechanism, so a nested `namespace a { namespace b { ... } }` yields `a::b` and an enum at global scope keeps `None`. An anonymous enum records its namespace too. A class scope is not a namespace and is not recorded as one: a class member enum carries the class's *enclosing* namespace, matching libclang.
- `TreeSitterBackend`: a typedef spelled twice in one translation unit is emitted once. C11 6.7p3 and C++ both permit `typedef int T;` to be repeated with the same underlying type, and the backend emitted one `Typedef` per spelling, so the writer produced two `ctypedef int T` lines and Cython reported "'T' redeclared". Duplicates now collapse on the `(kind, namespace, name)` identity `LibclangBackend` uses, so a `T` in each of two namespaces still yields two declarations.
- `TreeSitterBackend`: an opaque `enum E : int;` followed by its definition is emitted as one declaration instead of a valueless enum plus the definition, which rendered as two `cdef enum E` blocks and drew the same Cython redeclaration report. The definition fills in the forward declaration already emitted rather than appending a second, so either order collapses. Dedup is qualified by namespace, so a forward-plus-definition pair in each of two namespaces yields two enums. A lone opaque enum with no definition in the unit keeps its valueless form: it is the only declaration of that tag, and dropping it would leave the type unnamed. `LibclangBackend` dropped it until the entry below; the two backends now agree.
- `PxdWriter`: a C++ record declaring a callable member is emitted as a `cppclass` instead of a `cdef struct`. Cython parses a function declaration only inside a `cppclass` suite, so `struct SubcaseSignature { bool operator==(const SubcaseSignature&) const; }` -- ordinary C++, not a quirk of one header -- rendered as `cdef struct` and failed with "Syntax error in C variable declaration", taking the whole `.pxd` with it. The promotion now also fires on methods, constructors, a destructor and conversion operators, alongside the existing `is_cppclass`, base-list and used-as-a-base conditions. The boundary is deliberate: only a C++ record can carry a callable member, so a plain C struct of data fields alone is never promoted and stays a `cdef struct`, `cppclass` being meaningless in a C context; unions are excluded for the same reason. In doctest this moves 19 records and removes every "Syntax error in C variable declaration" from the output.
- `PxdWriter`: an operator overload Cython cannot declare is replaced by an `UNSUPPORTED` comment naming the symbol and the reason, instead of being emitted as invalid syntax. Which spellings are accepted was settled by compiling one declaration per spelling against Cython 3.3.0, not by reading its grammar: `+ - * / %`, `== != < > <= >=`, `[]`, `()`, `++ --`, `=`, `!`, `~`, `<< >>`, `& | ^`, `,` and the `bool` conversion parse; every compound assignment (`+= -= *= /= %= &= |= ^= <<= >>=`), `&&`, `||`, `new`, `delete` and every conversion other than `bool` are rejected with "Overloading operator '<op>' not yet supported.", and `<=>` with "Syntax error in C variable declaration". The restriction applies identically to a namespace-level operator, so free functions are guarded on the same rule. `++`, `--` and `,` are emitted although Python syntax offers no way to invoke them from a `.pyx`, because the declaration parses and dropping it would lose API. Renaming an unsupported operator to an ordinary method was rejected as the alternative: it compiles, but invents an API the header does not declare. In doctest this guards 25 declarations and removes every "Overloading operator" error.

- `TreeSitterBackend`: a bare signedness specifier no longer renders as `unsigned unsigned`. `unsigned size;` produced the field type `unsigned unsigned` and `signed flag;` produced `signed signed`: the type parser collects `unsigned` and `signed` as qualifiers, and with no other token left it fell back to reusing the whole source spelling as the base type, so the specifier appeared twice. C11 6.7.2p2 lists both standing alone among the multisets designating `unsigned int` and `int`, so the implicit `int` is supplied instead and the spellings render as `unsigned int` and `signed int`. `const unsigned` renders as `const unsigned int`. Every other integer spelling is unchanged, including the compound forms (`unsigned long long`, `long double`) and the ones tree-sitter deliberately preserves rather than canonicalizing as libclang does (`short int`, `signed int`). The defect was in the shared type parser, so variables, return types and parameters were affected alongside fields.
- `TreeSitterBackend`: anonymous struct and union members are no longer dropped from the IR entirely. An anonymous record specifier carries no `name`, so it was skipped by the nested-type path; a transparent member carries no declarator either, so the field loop had nothing to iterate and `union { int a; float b; };` vanished from its parent with no diagnostic. Both forms now match the settled `LibclangBackend` behaviour: a declarator-less member is carried on `Field.anonymous_struct` with `is_anonymous_transparent` set, for the Cython writer to flatten into the enclosing record per C11 6.7.2.1p13; a named member of an anonymous type (`union { int a; float b; } u;`) keeps its member and its type is lifted to the top level under a synthesized tag qualified by the enclosing record (`_s_u_u`), emitted ahead of the record that names it. A tagless `typedef struct { ... } T;` supplies the typedef alias as the qualifier, so two typedefs each holding a member of the same name do not collide. Anonymous structs, anonymous unions, and both nested inside either a struct or a union are covered. Bit widths inside an anonymous member are kept, as they already were outside one; libclang dropped them everywhere, which is fixed separately in the entry above.
- `LibclangBackend`: an enum, typedef or variable declared in more than one C++ namespace is no longer silently dropped. `self._seen` keyed these three on the bare name, so `namespace b { enum E { Y }; }` beside `namespace a { enum E { X }; }` never reached the IR at all -- not renamed, not marked, absent -- and the writer had nothing to disambiguate. This is the same defect already fixed for records on this branch, and the three keys now use the same `_record_key` helper, which qualifies by namespace and mirrors the `(type, name, namespace)` identity `_deduplicate_declarations` uses. Genuine duplicates still collapse: one header reached through two include paths, an opaque enum declaration and its definition, and the `typedef struct {...} Foo;` pattern each remain a single entry, and a declaration in the global namespace keeps its bare name. Widening the `_seen` key alone was not enough for enums, because `_deduplicate_declarations` re-collapsed them on the include-merge path; `Enum` therefore gains a `namespace` field, which `LibclangBackend` populates, matching `Struct`, `Function`, `Typedef` and `Variable`. The field defaults to `None`, so a backend that does not set it is unaffected.
- `PxdWriter`: an rvalue reference is emitted only where Cython accepts one. Measured against Cython 3.3.0, `&&` parses in parameter position -- free function, method, move constructor, move assignment, function-pointer parameter, template method, and with `const` -- and nowhere else: a return type, field, `ctypedef` or variable spelled `T&&` is "Syntax error in C variable declaration", and `vector[T&&]` is "Expected ']'". The doctest `String&& toString(String&& in_)` therefore made the whole `.pxd` unparseable at the return type, though its parameter was fine. Parameters now keep `&&`; the unrepresentable positions are skipped and replaced by an `UNSUPPORTED` comment naming the symbol, the spelling and the reason. Downgrading to `T&` was rejected as the alternative: it compiles, so nothing in the chain reports it, while widening the signature to bind lvalues that C++ refuses. Cython warns "Rvalue-reference as function argument not supported" on an accepted parameter and still lowers it to a real `cython_std::move`, so the warning does not indicate a lost move -- an executed end-to-end case observes the moved-from source.
- `LibclangBackend` / `TreeSitterBackend` / `PxdWriter`: a record defined inside a C++ class body is now declared instead of being silently dropped. The doctest `String::view` shape reached the output only as a field type (`view data`) with no declaration anywhere, so the `.pxd` failed with "'view' is not a type identifier". The backends previously excluded nested C++ classes from the path that lifts a nested C record to the top level, correctly -- lifting one would flatten an inner symbol into the global namespace -- but nothing took their place. Nested records are now carried on `Struct.nested_records` and emitted inside the parent's suite without the `cdef` keyword, which is the form `libcpp/vector.pxd` uses for `vector[T].iterator`; the C++ qualification is implicit, so no synthesized name is needed and none can collide. A nested record is also excluded from the translation-unit deduplication set, which is keyed by tag alone: a global `struct view` beside a class member `struct view` collided there, and whichever was seen second was dropped.

- `PxdWriter`: a type name declared in more than one namespace no longer loses every declaration but one. `namespace a { struct dup; }` beside `namespace b { struct dup; }` is idiomatic C++ -- a `detail::Options` next to a public `Options` -- but a `.pxd` module namespace is flat, and emitting both as `dup` in two `namespace`-qualified `cdef extern` blocks is caught by nothing in the chain: Cython accepts the duplicate, silently binds every use to whichever block came first, and generates C++ naming only `a::dup`. It cythonizes and it compiles, so a cythonize-only check passes while one declaration is gone. Colliding names are now disambiguated to `<ns>_<name>` with an explicit fully qualified cname (`cdef struct a_dup "a::dup"`), and an unqualified use resolves to the declaration in the namespace of the record being rendered. A renamed declaration is moved to a block with no `namespace` of its own, because a `namespace "a"` block does not qualify an explicit cname -- it emits a bare, incomplete `struct dup`. A declaration in the global namespace keeps its bare name, so a public `Options` is untouched and only `detail::Options` is renamed, and a name unique to one namespace is not renamed at all, so existing output is unchanged. The mapping is built from sorted input and so does not depend on declaration order or set iteration order.
- `PxdWriter`: a field, parameter or return type no longer carries the C elaborated-type-specifier keyword. `struct view data` inside a `cppclass` body fails with "Syntax error in C++ class definition"; the same spelling in a `cdef struct` body fails with "Syntax error in C variable declaration", and in a parameter list with "Expected ')'". There is no type-use position in which Cython accepts the tag -- it takes `struct`/`union`/`enum` on the declaration itself and wants the bare name everywhere else. The tag was previously stripped only when the record was in a `known_*` or `undeclared_*` set, which a record nested inside a class never is, so the doctest `String::view` shape emitted invalid Cython. The tag is now dropped from every type use regardless of whether the record is declared in the translation unit. A remainder that is not an identifier (libclang spells an anonymous record `struct (anonymous at f.h:1)`) keeps its tag, so the existing unrepresentable-name diagnostics still fire.

- `PxdWriter`: C++ base-class lists are emitted in a spelling Cython can resolve. Base names bypassed `_format_ctype`, so the namespace stripping every other type position receives never reached them and `struct deferred_false : types::false_type` rendered as `cdef cppclass deferred_false(types::false_type)`; Cython has no `::` in a type expression. Bases now go through a dedicated `_format_base_name`, which applies only the three transformations a class name can need -- namespace stripping, `<>` to `[]`, and keyword escaping -- rather than the full type-expression pipeline, whose remaining steps (C builtin mapping, `struct`/`enum` prefix stripping, resolution through the *derived* class's inner typedefs) are inapplicable or wrong for a base. `types::conjunction<A, B>` becomes `conjunction[A, B]`.
- `PxdWriter`: a record with base classes is emitted as a `cppclass` even when the backend did not mark it `is_cppclass`, and so is any record named as a base. Only a `cppclass` may carry an inheritance list, so `struct derived : base { ... }` previously emitted `cdef struct derived` with its bases dropped and every inherited member unreachable; and a `cppclass` inheriting from a `cdef struct` makes Cython crash outright with `'CStructOrUnionType' object has no attribute 'base_classes'`.
- `PxdWriter`: `cdef extern` blocks are ordered so a base class's namespace precedes the namespaces that inherit from it. Cython resolves inherited members where the derived class is parsed and reports no error when the base is not yet known -- the subclass simply gets none -- so a base in an alphabetically later namespace produced a silently empty binding. The default order (unnamespaced first, then alphabetical) remains the tiebreak, so headers without cross-namespace inheritance are unaffected. Dropping the namespace qualifier is correct even across namespaces: an inheritance list is never re-emitted into the generated C++, only the derived class's own name is.
- `PxdWriter`: a base class that cannot be named -- absent from this translation unit with no known cimport, or unreachable because two namespaces inherit from each other -- is replaced by an `UNSUPPORTED` comment naming the base and the derived class, instead of a bare name Cython would resolve to nothing. The derived class itself is still emitted with its own members.
- `PxdWriter`: base-class names are included in cimport collection, so a base provided by `libcpp` or a stub module now yields the `cimport` it needs.

- `LibclangBackend`: a free function's dependent member alias no longer decays to a bare member name. libclang reports the declaration cursor of a nested `typedef`/`using` with its unqualified spelling, so `types::remove_reference<int>::type` reached the IR as `type` -- an identifier naming nothing at the output's top level -- and rendered as `type dep_result(int x)`. Class-template *methods* were unaffected because they arrive as a different `TypeKind` carrying the full spelling, which is why the defect survived the earlier dependent-name work. For a concrete instantiation the canonical type is exact and already substituted by clang, so it is emitted instead: the example now renders as `int dep_result(int x)`. The qualified spelling is not a usable alternative, having no valid form in a Cython declaration. Aliases whose canonical type is still dependent, and aliases that are not class members, keep the previous bare-name behaviour.
- `LibclangBackend`: C++11 `using type = T;` aliases inside a class are captured in `Struct.inner_typedefs` alongside classic `typedef` members. Clang reports them as `TYPE_ALIAS_DECL` rather than `TYPEDEF_DECL`, and matching only the latter dropped every `using` alias silently, so a class template declaring its nested member with `using` emitted an empty body and its dependent names had no member to resolve against.
- `PxdWriter`: C++ dependent names are emitted in a spelling Cython accepts. `typename types::remove_reference<T>::type` rendered as `typename remove_reference[T]::type`, which Cython rejects with "Expected an identifier or literal": it has no `typename` keyword and spells nested access with `.`, not `::`. The emission is now `remove_reference[T].type`, with the namespace carried on the enclosing `cdef extern ... namespace` block -- qualifying the type itself fails with "'types' is not declared". This is sound because an `extern` block is never re-emitted into the generated C++; Cython consults it only at use sites, where every template argument is already concrete.
- `PxdWriter`: a C++ class's nested typedefs are emitted as `ctypedef` members. Without them a dependent name indexed into a class template referenced a member the generated `cppclass` never declared.
- `PxdWriter`: a dependent name Cython cannot express is replaced by an `UNSUPPORTED` comment naming the symbol and the type, instead of emitting invalid syntax or dropping the declaration silently. Two cases are unrepresentable: a nested name on a bare template parameter (`typename T::foo`), which Cython rejects outright, and a nested name on a qualifier that does not declare that member. Where a bare-parameter nested name has a conventional concrete spelling, `size_type` and `difference_type` are substituted with `size_t` and `ptrdiff_t` and the deviation is recorded in a comment, as Cython's own `libcpp` headers do.
- `PxdWriter`: the per-class formatting context is cleared when a C++ class is emitted as a forward declaration. It previously leaked, so a later free function resolved an unrelated type name through the previous class's inner typedefs.
- `PxdWriter`: a class whose every member was replaced by a diagnostic comment now emits `pass`. Comments are not a body, so the block otherwise failed with "Expected an increase in indentation level".
- `LibclangBackend`: anonymous struct, union and enum declarations no longer leak clang's internal `struct (unnamed at file:line:col)` spelling into the IR. An anonymous tag is now named after the declarator that uses it (`struct { int a; } v;` becomes `_v_s`), falling back to a per-translation-unit counter slug (`_anon_struct_1`) when nothing references it, so output stays reproducible.
- `PxdWriter`: function pointer variables emitted an abstract declarator with the name appended (`void (*)(int, char) my_func`), which is not valid Cython. They are now emitted as a named `ctypedef` plus an alias declaration (`ctypedef void (*_my_func_ft)(int a, char b)` / `_my_func_ft my_func`).
- `LibclangBackend`: a named member whose type is an anonymous struct or union (`struct { int v; int g; } css;`) is no longer flattened into its parent as if it were a C11 anonymous member. Such a member has a declarator, so C11 6.7.2.1p13 does not apply; flattening dropped the member entirely, leaving `outer.css` unreachable, and emitted a bodyless `cdef struct` for its type. The anonymous type now gets its own bodied declaration named for the enclosing record (`_outer_css_s`), so two records may each hold a `css`. Genuine C11 anonymous members (`struct { int b; };` with no declarator) still flatten.
- `PxdWriter`: a function pointer variable's generated `ctypedef` and its alias declaration are now separated by a blank line, matching how the writer separates every other pair of declarations.
- `LibclangBackend`: pointers to unprototyped functions (`TypeKind.FUNCTIONNOPROTO`, e.g. `int (*f)()`) were converted to opaque types carrying raw clang spelling instead of function pointers.
- `LibclangBackend`: function pointer parameter names are recovered from the declaring cursor's `PARM_DECL` children for variables, struct fields and function parameters. Clang's function *type* carries no argument names, so these previously rendered as `(int, char)`.
- `LibclangBackend` / `PxdWriter`: C11 anonymous nested struct and union members are now captured on `Field.anonymous_struct` and flattened into the enclosing record by the Cython writer, instead of being dropped.
- `LibclangBackend`: a tagged struct or union *defined* inside another record body (`struct my_s { union my_nested_u { char c; int i; } n; };`) is now emitted as a top-level declaration ahead of the record that uses it. The nested definition was dropped entirely, so the writer saw only an undeclared tag and emitted a bare forward declaration; the containing struct then used that incomplete type by value, which Cython rejects with "Variable type 'my_nested_u' is incomplete". Nesting recurses innermost-first, so each level precedes the level that uses it. A tag introduced without a body (`struct node { struct peer *p; };`) still yields a forward declaration only, and C++ nested classes are unaffected.
- `PxdWriter`: a named pointer-to-function-pointer parameter (`void reg(void (**pxFunc)(int, char));`) kept its name outside the declarator (`void (**)(int, char) pxFunc`), which Cython rejects with "Expected ')'". Only single-level function-pointer parameters were routed through the declarator formatter; the star count now follows the pointer depth, so any depth renders as `void (**pxFunc)(int, char)`. Reduced from sqlite3's `xFindFunction`.
- `LibclangBackend`: enums declared inside a struct or union body are now emitted as top-level declarations, so fields referring to them no longer name an undeclared type. A named field whose type is an anonymous enum (`enum { X } e;`) is no longer misclassified as a transparent anonymous member.
- `PxdWriter`: removed the guard that silently discarded any enum whose name contained `(unnamed at`, which deleted top-level anonymous enums entirely.
- `PxdWriter`: a struct whose every member is filtered out now emits `pass` rather than a suite header with no body, which Cython rejects.
- `LibclangBackend`: tag-less typedef'd enums (`typedef enum { ... } Name;`) now set `Enum.is_typedef`, so the Cython writer emits `ctypedef enum Name` instead of `cdef enum Name`. The old output made Cython generate `enum Name x;`, which fails to compile against the header with "tentative definition has type 'enum Name' that is never completed". Tagged enums (`typedef enum Tag { ... } Tag;`) keep `cdef enum`, and C++ enums are unaffected.
- `CffiWriter`: enum/typedef pairs are combined into the tag-less `typedef enum { ... } Name;` form only when the enum really has no tag. A tagged `typedef enum Tag { ... } Tag;` now keeps its `enum Tag` tag instead of having it silently dropped.
- `LibclangBackend`: `project_prefixes` are matched against absolute, symlink-resolved paths instead of as substrings of clang's raw location spelling. Clang reports included files by their relative include path (e.g. `./foo.h`), so passing an absolute prefix previously never matched.
- `LibclangBackend`: the redundant self-referential `Typedef` produced alongside a `typedef enum` is now dropped during declaration deduplication, matching the existing `typedef struct` handling.
- `LibclangBackend`: `const` and `volatile` qualifiers are no longer dropped from pointers, elaborated types, records, enums and typedefs (`char* const`, `const my_struct*`, `const my_union* const`). The deliberate stripping of `_Atomic`, `__restrict` and `_Noreturn` is unchanged.
- `TreeSitterBackend`: pointee `const` is preserved in every declaration position -- struct and union fields, function parameters, return types, typedefs, global variables and type aliases. `const char*` previously parsed as `char*`, silently dropping the qualifier from generated bindings.
- `TreeSitterBackend`: pointer-level `const` on a function parameter (`char* const p`) is preserved instead of being discarded.
- `TreeSitterBackend`: an `allowlist` or `denylist` naming a file other than the one being parsed now raises `UserWarning` instead of being discarded silently, because this backend does not follow `#include` directives and cannot honor either. Each list warns independently. Entries that all resolve to the parsed file itself are already satisfied and warn nothing; resolution follows the same rule as the libclang backend.
- `PxdWriter`: synthesized forward declarations now carry the keyword-escape cname (`cdef struct class_ "class"`). Without it the generated C referenced a nonexistent `struct class_`.
- `PxdWriter`: a record referenced before its definition is emitted now gets a forward declaration, matching the reference output.
- `PxdWriter`: a keyword-named typedef no longer emits a bogus self-referential `ctypedef with_ with_ "with"`; the circular-typedef check now compares the bare escaped name rather than the cname-annotated spelling.
- `LibclangBackend`: a named function-pointer typedef (`typedef void (*cb_t)(int a, char *b, int c[3][4]);`) now recovers its parameter names from the declaring cursor's `PARM_DECL` children, as the struct-field and variable paths already did. The typedef path never reached that recovery, so the names were lost -- and with them the array extents, because the writer only spells an array parameter inside a named declarator. `int c[3][4]` therefore degraded to `int`, a weaker type than C's adjusted `int (*)[4]`.
- `LibclangBackend`: the generated tag for an anonymous enum declared inside a record is now qualified by the enclosing record's name (`_outer_x_e`), as anonymous struct and union tags already were. Two records each holding an anonymous-enum member named `x` both bound the tag `_x_e`, so the second record's member took the first's type and its own enumerators were emitted nowhere.
- `Pointer.__str__`: qualifiers on the pointer itself are now rendered after the `*` (`int* const`) instead of before the base type (`const int*`), which spelled a const pointer as a pointer to const. Nested pointers render each level's qualifiers at that level, so `Pointer(Pointer(CType("char", ["const"]), ["const"]), ["const"])` is `const char* const* const` rather than `const const const char**`. Pointee qualifiers are unaffected.
- `PxdWriter`: namespace stripping in `_format_ctype` no longer hangs on a C++ dependent name. The loop ran `re.sub(r"\b\w+::", "", name)` until no `::` remained, but a name such as `types::remove_reference<T>::type` retains a `>::` that the pattern cannot match, so the condition never became false. Writing a `.pxd` for doctest's 9,148-line header spun forever (>300 s against 1.5 s of parsing); it now completes in 0.002 s. The loop iterates to a fixpoint instead. Output for headers that already terminated is byte-for-byte unchanged.
- `LibclangBackend`: a record name declared in more than one namespace no longer loses every declaration but one before the writer sees it. `self._seen` keyed a record as `"{kind}:{name}"` with no namespace component, so `namespace b { struct dup { double y; }; }` following `namespace a { struct dup { int x; }; }` was skipped at insertion and vanished from the IR with no diagnostic, leaving the writer's cross-namespace disambiguation nothing to disambiguate. Record identity now carries the enclosing namespace, matching the `(type, name, namespace)` scheme `_deduplicate_declarations` already used in the same module and the namespace-qualified key the function path already used -- two disagreeing dedup keys in one file were the defect. Genuine duplicates still collapse: a record reached twice through different include paths yields one entry, and a forward declaration followed by its definition still collapses to the definition. `_remove_forward_declaration` is qualified by namespace for the same reason; scanning for a bare name let a definition of `b::dup` evict the already-emitted forward declaration of `a::dup` and leave `b::dup` present twice. The libclang and tree-sitter backends now render this header identically.
- `PxdWriter`: the C elaborated-type-specifier keyword is stripped from a type use before cross-namespace collision resolution rather than after. libclang spells a field's type `struct dup` where tree-sitter spells it `dup`, and the tag-bearing spelling matched no collision key, so a field whose record had been renamed to `a_dup` was still emitted as `dup` -- a name no longer declared anywhere in the `.pxd`. A qualified use (`struct a::dup`) failed to resolve for the same reason.
- `TreeSitterBackend`: an enum defined inside a C++ class or struct body is now emitted as a top-level declaration instead of vanishing. The record IR has no slot for a member enum, so the class-body loop produced nothing at all for `class C { public: enum E { A }; };` -- the tag and every enumerator were absent from the output, and the constants were unreachable from generated bindings. The enum is now hoisted to the top level exactly as `LibclangBackend` reports it, carrying the class's *enclosing* namespace rather than the class: a class scope is not a namespace, so `namespace X { class C { public: enum E { A }; }; }` yields `namespace="X"`, not `"X::C"`. An anonymous member enum, a member enum with an explicit underlying type, an `enum class` member and a member enum that also declares a field (`enum G { X } m;`, which keeps `m`) are all covered. The hoisted enum is emitted ahead of the class it came from, so nothing references a tag before it is declared. An enum inside a *nested* class is not hoisted, because the nested record itself is not: its only real spelling is `Outer::Inner::E`, and libclang emits nothing there either. Two classes in one namespace declaring the same tag collapse to the first, on the existing namespace-qualified enum identity -- the parent-qualified synthesized tag used for anonymous records is not applicable, since `E` is the name the header wrote and renaming it would name a type no compiler can find; `LibclangBackend` resolves the clash the same way. Enums of the same name hoisted out of classes in *different* namespaces both survive. Rendering the hoisted enum with its C++ qualification (`cdef enum Mode "Cfg::Mode"`) remains a separate writer gap that affects both backends identically; the `.pxd` the two now produce for a member enum is byte-identical.
- `TreeSitterBackend`: a forward-declared record and its definition are emitted as one declaration instead of two. `struct S; struct S { int a; };` produced a valueless record plus the definition, which the writer rendered as a second `cdef struct S` and Cython reported as `'S' redeclared`. The definition now fills the forward declaration already emitted rather than appending a second, so either declaration order collapses -- the same upgrade-in-place treatment, on the same namespace-qualified identity, that opaque enums already use, rather than a second mechanism. Unions collapse on the same rule. A forward declaration that is *never* defined keeps its valueless form: that is the whole of an opaque handle type (`typedef struct Handle Handle;`), and dropping it would leave the typedef pointing at nothing. A same-named forward declaration in each of two namespaces still yields two records, and a pair in each of two namespaces collapses independently. A `typedef struct S { ... } S;` following a `struct S;` stamps the alias onto the record already emitted instead of adding a stray typedef.
- `TreeSitterBackend`: an unnamed bitfield is dropped instead of being emitted as a nameless `Field`, matching `LibclangBackend`. C11 6.7.2.1p12 gives `unsigned : 0` and `unsigned : 3` no member name -- the first forces the next field onto a fresh storage unit, the second reserves anonymous bits -- so neither is an accessible member. tree-sitter's grammar requires a declarator and therefore inserts a *missing* `field_identifier` node for these, which the backend unwrapped to `""` and let through because the bitfield width was present. The missing node is now what distinguishes padding from a declared member. The consequence was not cosmetic: the `ctypes` writer renders a bitfield as a 3-tuple, so the padding entry became `("", ctypes.c_uint, 0)` and the generated module raised `ValueError: number of bits invalid for bit field ''` at class creation -- bindings that could not be imported at all. Named bitfields are untouched and keep their widths, and padding inside a C11 anonymous member is dropped by the same rule. Neither backend carries padding into the IR, which has no field for it; that is a known shared limitation for writers that *reconstruct* a layout rather than defer to the header. `PxdWriter` is unaffected because a `cdef extern` block re-uses the real header, so the emitted `.pxd` reproduces the C compiler's `sizeof` and bit positions exactly.

- `CtypesWriter`: an all-padding record picks its carrier from the ABI of the host that *imports* the generated module, instead of baking in the ABI of the host that generated it. Whether a C unnamed bit-field contributes its declared type's alignment to the enclosing record is an ABI choice, and it was read off one platform and written down as universal -- the code said "a record whose every entry is padding has no member to impose an alignment, so C sizes it to just the bits declared and aligns it to 1", which is true on some ABIs and false on others. Measured with compiled C probes rather than inferred: AAPCS64 (Linux aarch64, gcc 14.2 and clang 19.1) imposes the alignment; x86-64 System V (Linux x86_64, gcc 14.2 and clang 19.1) and Darwin arm64 (Apple clang 21) do not. The split is therefore not macOS versus Linux -- x86-64 Linux and macOS agree, and Linux on aarch64 is the odd one -- so a rule stated as "Apple versus SysV" would have been wrong in the same way, for a different reason. `struct { unsigned int : 8; }` is 4 bytes aligned to 4 under AAPCS64 and 1 aligned to 1 under the other two; the emitted `c_ubyte` array measured 1/1 everywhere, so the generated class disagreed with the C compiler on Linux aarch64 for every all-padding record, not only that one -- 15 of the 17 all-padding shapes probed were wrong there. ctypes has no unnamed bit-field, which is why a carrier is synthesized at all, and its layout engine is ABI-blind: every construct probed measured identically on all three platforms, so no single spelling is correct everywhere and nothing can be detected from ctypes itself at run time. Both carriers are therefore emitted under an `if`, and the module resolves it on import from `sys.platform` and `platform.machine()`: a named bit-field of the same storage type where the ABI imposes alignment, and the `c_ubyte` array where it does not. This matters because ctypes lays a record out for the host running Python, which need not be the host that generated the file -- a module generated on macOS and imported on Linux aarch64 now lays out correctly, which was verified by doing exactly that rather than by regenerating on each platform. ABIs beyond the three measured are assumed to follow System V and are documented as such in the generated output. Records that have a real member are unchanged; they already carry the padding as a named bit-field, and their layout does not go through this path. Verified by comparing a compiled C probe's `sizeof`, `_Alignof` and per-member bit positions against the generated class for 28 records on macOS arm64, Linux aarch64 and Linux x86-64, under both gcc and clang.

- `CtypesWriter`: a record holding **both** a real member and unnamed bit-field padding no longer takes its alignment from the padding on ABIs where C does not. The all-padding fix above ends by noting that such records "are unchanged; they already carry the padding as a named bit-field" -- that carrier was the defect. ctypes can reserve bits only with a *named* bit-field, and a named bit-field of `unsigned int` aligns its record to 4, which C does only under AAPCS64. Measured with compiled C probes on the same three combinations as above, `struct { unsigned char a; unsigned int : 8; }` is 2/1 on Darwin arm64 and Linux x86-64 and 4/4 on Linux aarch64, while the writer emitted 4/4 everywhere; so did `struct { unsigned char a : 3; unsigned int : 8; }`, and `struct { unsigned char a; unsigned short : 4; unsigned char b; }` was 4/2 against C's 3/1. The existing 32-case corpus could not see any of this because every member in it is `unsigned int`, which pins the record to 4/4 whatever the padding does; the corpus is extended here with `unsigned char`, `unsigned short` and mixed-width members. Where the padding decides the alignment the writer now emits both field lists under the same import-time `if` the all-padding case introduced, rather than a second mechanism. The alignment-neutral spelling is a chain of `c_ubyte` bit-fields covering the identical bit range -- including any storage unit the wide carrier skipped past -- split so that no entry crosses a byte, since ctypes moves a bit-field to the next unit whenever it would straddle one and a single wide entry would therefore land in the wrong place. `_pack_ = 1` was considered and rejected: it does lower the alignment, but it also changes bit-field straddling, so `struct { unsigned char a; unsigned int : 8; unsigned char b; }` measures 6 bytes with `b` at bit 40 under `_pack_` against C's 3 bytes with `b` at bit 16, and it would collide with records that are genuinely `__attribute__((packed))`. The byte-split carrier needs no `_pack_` and reproduces C exactly. The branch is emitted only where it can matter -- not when every carrier is already byte-aligned, and not when a real member already imposes at least as much alignment, so `struct { unsigned a : 3; unsigned : 0; unsigned b : 5; }` keeps one field list. A packed record keeps one list too, because `_pack_ = 1` already pins it to byte alignment. One shape cannot be respelled: padding that follows a nested anonymous record leaves the writer without a bit offset to work from, and that record is now emitted with a `# HEADERKIT:` comment naming it and stating that it may be over-aligned, rather than looking like a record the writer got right. Verified by comparing a compiled C probe's `sizeof`, `_Alignof` and per-member bit positions against the generated class for 18 records on macOS arm64, Linux aarch64 and Linux x86-64 under both gcc and clang; 21 of 55 measured figures disagreed with C before the change and none do after.

- `CtypesWriter`: a generated record's layout no longer depends on which CPython imports the module. ctypes has had two bit-field algorithms. Before 3.14 it opened a fresh storage unit whenever a bit-field's declared type differed in size from the unit it would otherwise land in, and derived the record's alignment only from bit-fields that *opened* a unit; 3.14 replaced that with the platform C compiler's rule. A generated module is a file, so it may be written under one interpreter and imported under another, and a spelling correct on only one of them is wrong by construction -- the same defect the ABI branch above exists to prevent, in a second dimension nothing was watching. Measured, not read off the changelog: the emitted carrier for `struct { unsigned char : 4; unsigned int : 4; }` measured 4/1 on 3.10 and 3.13 and 4/4 on 3.14, against C's 4/4 under AAPCS64 on all three; and `struct { unsigned char a; unsigned short b : 5; unsigned int : 7; unsigned char c; }` put `b` at bit 16 on 3.10 through 3.13 where C and 3.14 put it at bit 8. One spelling is now correct on every supported Python, so no `sys.version_info` branch is emitted. It has two halves. An all-padding record has no addressable member, so only its `sizeof` and alignment are observable; the whole span is therefore respelled as a single chain in the widest padding carrier, which makes that carrier open the first unit on every engine. A record that *does* have a member keeps byte-granular carriers for its padding and narrows a named bit-field to a one-byte carrier wherever the field already fits in the byte the running offset is in -- the case where C would not move it either -- so no declared type ever differs from the unit in play and pre-3.14 ctypes, 3.14 ctypes and the MSVC algorithm ctypes uses on Windows all agree. Narrowing preserves signedness, so a `signed short a : 3` still reads back `-1` rather than `7`. The alignment that byte carriers give up is put back by a zero-length array of the original declared type, which occupies no bytes and imposes its element type's alignment identically on 3.10 through 3.14; alignment is thereby chosen independently of position, which pre-3.14 ctypes offers no other way to do -- `_align_` did not exist before 3.13. A field that does not fit its current byte keeps its declared type, which is the faithful spelling on 3.14 and under MSVC, so nothing that worked before regresses. Verified by running the full suite under 3.10, 3.13 and 3.14: 28 layout assertions failed on 3.10 before the change and none do after.
- `CtypesWriter`: Windows is no longer classified as an ABI that ignores an unnamed bit-field's alignment. `_HK_UNNAMED_BITFIELD_ALIGNS` resolved to false on `win32` because every ABI that had not been measured was assumed to follow System V, and `struct { unsigned int : 8; }` was therefore emitted as a 1-byte, 1-aligned record where the C compiler makes it 4/4. The suspicion that the ground-truth probe rather than the predicate was at fault -- the probe compiles with MinGW `cc` while ctypes follows MSVC, so the comparison could have been between two different ABIs -- was settled by measurement rather than argument: a probe run on the Windows runner compiled the same records with MinGW `cc`, `gcc`, `clang`, and `clang --target=x86_64-pc-windows-msvc`, and all four agreed on every entry (`: 8` -> 4/4, `: 1` -> 4/4, `: 9` -> 4/4, `: 32` -> 4/4). There is no MinGW-versus-MSVC split here, so the probe was right and the predicate was wrong. The flag is now true on `win32`, and the documented ABI table in the generated output records the Windows row as measured rather than assumed.

- Windows compile-link-execute tests link the MinGW C++ and GCC runtimes into the extension module. The runner's `c++` is MinGW, so a linked `.pyd` named `libstdc++-6.dll`, `libgcc_s_seh-1.dll` and `libwinpthread-1.dll` as load-time dependencies; those sit in the MinGW `bin` directory, which is not on the loader's search path for the interpreter that imports the module. Cythonizing, compiling and linking all succeeded and `import use` then failed with "DLL load failed while importing use: The specified module could not be found", a message naming neither the missing DLL nor MinGW. Only the C++ tests reached it, because a C extension names no `libstdc++`. `-static-libstdc++ -static-libgcc` removes the dependency rather than relying on the runner's PATH. Linux and macOS link lines are unchanged.

- `CtypesWriter`: Windows gets its own layout rule rather than being sorted into one of the two others. The writer modelled the choice as a boolean -- an ABI either imposes an unnamed bit-field's alignment or it does not -- and Windows fits neither branch. MSVC opens a fresh storage unit whenever a bit-field's declared type differs from the unit in play, so `struct { unsigned char a; unsigned int : 8; }` is 8 bytes there against 4 under AAPCS64 and 2 under System V; measured on the runner with MinGW `cc`, `gcc`, `clang` and `clang --target=x86_64-pc-windows-msvc`, which all agreed. Merely flipping the boolean produced 4 where C says 8. The generated module now resolves three cases on import instead of two, and the Windows one needs no synthesized spelling at all: ctypes implements that same MSVC algorithm on Windows, so the declared types are written out as they stand and ctypes reproduces the platform C compiler on its own. The position-chosen spellings, which exist to paper over a ctypes engine that changed in 3.14, are confined to the hosts that need them.

- `CtypesWriter`: a zero-width bit-field is expressed as a zero-length array rather than as a fill bit-field where ctypes reproduces the host C compiler natively. `unsigned int : 0` reserves no bits in C; it ends the current storage unit and moves the next member to a fresh one. ctypes rejects a zero-width entry outright, so the boundary was reached by reserving the rest of the unit with a named bit-field -- faithful under the GCC rule, where that fill sits in the very unit being closed, and wrong under the MSVC rule, which gives the fill a unit of its own and then gives the next member yet another. Measured on Windows, `struct { unsigned char a : 3; unsigned int : 0; unsigned char b : 5; }` is 8 bytes and the fill spelling produced 12. A zero-length array closes the unit and imposes the boundary's alignment while occupying nothing, which is what `: 0` means; it also measures 8 bytes with the members at bits 0-2 and 32-36 on 3.10 and 3.14 alike, where the fill spelling measured 5 bytes on 3.10 and 8 on 3.14. The fill spelling is kept on the hosts whose rule it matches.

## [0.37.0] - 2026-09-05

### Added

- Enhanced CShim writer (`CShimWriter`) with C++ inheritance flattening: emits upcast helpers (`{Derived}_as_{Base}`) using `static_cast` for proper pointer offset adjustment under multiple/virtual inheritance, and flattens public base class methods directly onto derived opaque handle APIs.
- Standard library container mappings in `CShimWriter`: `std::string` and `std::string_view` mapped to `const char*` with thread-safe C-string conversion; `std::vector<T>` parameters mapped to flat `(const T* data, size_t count)` C-ABI arrays and reconstructed into C++ vectors.
- Expanded operator mappings in `CShimWriter`: added conversion/cast operators (`operator bool`, `operator int`, etc. mapped to `to_bool`, `to_int`) and unary operator disambiguation (`operator*` to `deref`, `operator-` to `neg`, `operator+` to `pos`).

### Fixed

- Pipeline and registry wiring: added backend alias normalization (`treesitter` and `tree_sitter` to `tree-sitter`) across `get_backend()` and `is_backend_available()`; wired custom override `@hook("write_output")` execution into `generate()`; executed `transform_unit` hooks during `scaffold()`; forwarded CLI `--writer-opt` values to `ScaffoldOptions`; and completed `supported_options` declarations across Mojo, Ctypes, Cffi, Cython, and Nim writers.
- `CShimWriter`: rejected non-const `std::string&` and `std::string_view&` references from automatic C-string flattening, avoiding invalid C++ compilation when binding temporary rvalues to mutable references, and preserving opaque handle type safety.
- `CShimWriter`: fixed base class resolution in multiple inheritance flattening and upcast generation by tracking classes by fully-qualified name and scoped namespace lookup, preventing collisions between identical short class names across namespaces.
- Writer options type coercion: implemented `WriterOption.coerce` and `coerce_writer_options` in `BaseWriter`, `get_writer()`, and CLI `--writer-opt` parsing, properly coercing string inputs into declared types (e.g. `bool`, `int`, `float`).
- `CShimWriter`: replaced tautological `assert((void*)fn != NULL)` in generated C test harnesses with cross-platform runtime dynamic symbol resolution (`dlopen`/`dlsym` on POSIX, `GetProcAddress` on Windows) linked against `${CMAKE_DL_LIBS}`, eliminating green-mirage compiler-optimized assertions.
- `MojoWriter`: generated tripwire tests now compute exact Mojo FFI parameter and return type signatures for each foreign function symbol rather than hardcoding `fn() -> None`.
- Layout delegation: removed redundant `write()` method overrides in `MojoWriter`, `CShimWriter`, and `NimWriter`, properly inheriting `BaseWriter.write()` delegation to `write_layout(layout="file")` to satisfy the Zero-Dual-System Rule.
- `TreeSitterBackend`: fixed extraction of C variadic function declarations (`variadic_parameter` node type in tree-sitter C grammar), top-level variable declarations (pointers, multi-dimensional arrays, sized primitives, and multiple declarators per statement), struct callback function pointer fields, bitfield widths, and function pointer typedefs.
- Memory safety in `CShimWriter`: prevented dangling pointer / use-after-free when returning `std::string` by value by managing lifetime via thread-local C-string storage, and fixed `std::string_view` returns to avoid invalid `.c_str()` member invocations.
- Missing standard headers in `CShimWriter`: added `#include <stddef.h>` to generated C headers when `size_t` is present, and added `#include <vector>`, `<string>`, and `<cstddef>` to generated C++ implementation shims.
- Constrained `std::vector<T>` parameter flattening in `CShimWriter` to by-value and const references, preventing illegal binding of temporary rvalues to non-const references.
- Disambiguated overloaded C++ methods and free functions in `CShimWriter` with numeric suffixes rather than silently skipping subsequent overloads.
- Expanded cross-compilation compiler binary pattern matching in `headerkit._target` to support version-suffixed binaries (`gcc-12`, `gcc-14.2.0`), Windows `.exe`, and dotted target components (`darwin21.4`).
- Upgraded generated unit test stubs in `headerkit.packaging.nim` to assert function parameter counts and signatures via `inspect.signature` instead of shallow callable checks.
- Nim scikit-build wheel packaging template and build helpers (`headerkit.packaging.nim`, `generate_nim_cmake`, `generate_nim_pyproject`, `generate_nim_python_wrapper`, `generate_nim_source`, `generate_nim_wheel_layout`): end-to-end scaffolding for compiling Nim modules into distributable binary Python wheels via `scikit-build-core` with deterministic `--mm:orc` ARC runtime memory management, multi-platform library naming across Linux/macOS/Windows, idempotent `NimMain()` initialization, ctypes function wrappers, and non-tautological tripwires.
- Added `wheel` and `scikit-build` layout modes to `NimWriter` (`headerkit header.h -w nim --layout wheel`) with CLI support for compiling and packaging Nim-based native extensions into binary wheels.
- Structured `TargetTriple` dataclass and parsing (`headerkit.TargetTriple`, `parse_triple`): parses 3- and 4-component triples as well as 2-component shorthands (`x86_64-linux`, `aarch64-darwin`, `win64`, `wasm32-wasi`, `arm-none-eabi`) into structured target models with platform predicates (`is_windows`, `is_darwin`, `is_linux`, `is_musl`, `is_wasm`, `is_embedded`) and architecture-aware `pointer_width` (8 for 64-bit, 4 for 32-bit, 2 for 16-bit).
- Cross-compilation toolchain auto-detection (`detect_cross_compiler_target`): automatically detects active cross-compilation targets from `CARGO_BUILD_TARGET`, `LLVM_TARGET_TRIPLE`, `CROSS_COMPILE`, and `CC`/`CXX` cross-compiler binary prefixes (e.g. `aarch64-linux-gnu-gcc`) in `resolve_target()`.
- C++ Tree-Sitter parser backend: enhanced `TreeSitterBackend` with `tree-sitter-cpp` support, enabling zero-system-dependency parsing of C++ headers (.hpp, .hh, .hxx, .cpptest) and `-x c++` inputs.
- C++ AST extraction in Tree-Sitter: support for extracting C++ classes (`cppclass`), member access specifiers (`public`, `protected`, `private`), constructors, virtual/pure-virtual methods (`= 0`), static methods, const-qualified methods, destructors, base inheritance (`BaseSpecifier`), nested namespaces, class/function templates, using-declaration type aliases, reference types (`&` and `&&`), and operator overloads.
- Added `tree-sitter-cpp>=0.23` to `treesitter` optional dependency extra.

- Pipeline context layout filtering: added `layout` attribute to `PipelineContext` enabling hooks to match and filter on requested layout strategies (e.g. `@hook("scaffold_project", layout="package")`), with automatic fallback when a layout is unsupported by a specific writer.
- End-to-end roundtrip integration test coverage for all 10 writers: added `test_roundtrip_cshim.py`, `test_roundtrip_mojo.py`, and `test_roundtrip_nim.py` completing full `libclang` C/C++ AST-to-binding roundtrip verification across the entire writer surface.
- Writer-defined layouts and options introspection: added `WriterOption` dataclass and `supported_layouts` / `supported_options` attributes on `BaseWriter` and all 10 concrete writers, enabling writers to declare layouts (e.g. `file`, `package`, `project`, `cmake`) and configurable arguments (`test_type`, `catch_exceptions`, `indent`, `verbosity`). Added public discovery functions `list_writer_layouts(name)` and `list_writer_options(name)` with layout validation in `BaseWriter.write_layout()`.
- Unified output writer scaffolding architecture: all 10 writers (`ctypes`, `cffi`, `cython`, `luajit/lua`, `nim`, `mojo`, `cshim`, `json`, `diff`, `prompt`) inherit from `BaseWriter` and implement `write_layout(unit, options) -> ProjectLayout`, unifying single-file generation and multi-file package scaffolding under a single layout engine.
- Automatic scaffolder hook registration: `register_writer()` registers the `@hook("scaffold_project", writer=name, target=name)` hook, allowing all writers to transparently serve as scaffolding engines.
- Extended package scaffolding templates for Cython (`.pxd`, `.pyx`, `pyproject.toml`, tripwire), CFFI (`build_ffi.py`, `_bindings.py`, `pyproject.toml`, tripwire), CShim (`CMakeLists.txt`, headers, bridge source, test harness), and LuaJIT (rockspec, Lua source, tripwire).
- Legacy `writer.write(header)` backward-compatibility facade natively delegating to `write_layout(layout="file")`.
- Polyglot project and extension scaffolding engine (`headerkit.scaffold`): unified layout architecture where single-file bindings and full packages are driven by a single output model (`OutputFile`, `ProjectLayout`, `ScaffoldOptions`, `scaffold()`).
- Built-in zero-dependency standard library scaffolder (`StdlibScaffolder`) generating complete turnkey packages with build metadata and test suites for Nim (`.nimble`, `nim.cfg`), Mojo (`mojoproject.toml`), and Python (`pyproject.toml`).
- Pluggable `BYOScaffolder` protocol and `scaffold_project` hook integration for third-party template engines (e.g. Copier, Cookiecutter) with custom precedence.
- Dual test stub generation: automated side-by-side failing tripwires (`pytest-tripwire`, `nim-tripwire`) for C ABI symbol/library verification and unit test skeletons.
- TTY-aware dynamic wizard (`prompt_scaffold_options`) auto-detecting terminal status to guide users through package setup interactively, with graceful `--no-input` fallback.
- CLI options: `--layout` (`file`, `package`, `project`), `--package-name` / `--pkg`, `--test-type` (`both`, `tripwire`, `unit`, `none`), and `--no-input`.
- Executable Copier BYOScaffolder showcase example (`examples/scaffolding/copier_scaffolder.py`).
- Comprehensive scaffolding guide (`docs/guides/scaffolding.md`) and API reference (`docs/reference/scaffold.md`).
- Mojo FFI binding writer (`headerkit.writers.mojo`): generates idiomatic Modular Mojo bindings using `sys.ffi.DLHandle` and C-ABI flat shims, mapping C types, structs, enums, typedefs, constants, and high-level C++ class wrapper structs.
- Mojo writer reference documentation (`docs/reference/mojo.md`).
- C source definition parsing: enhanced `TreeSitterBackend` to extract non-static function definitions and declarations from `.c` source files.

### Removed

- Removed regex-based `RustBackend`, `ZigBackend`, and `NimBackend` source extractors. Regular-expression parsing of context-free languages is strictly prohibited; proper grammar-based AST extractors (via Tree-sitter grammars) are scheduled on the roadmap.

### Fixed

- Eliminated completion bias, hollow scaffolding, and tautological test generation across all writer packages:
  - `CShimWriter`: emit complete C-ABI function prototypes and opaque struct handles into `include/{pkg}_cshim.h` instead of hollow placeholders; updated C test harness to `#include` the generated header and assert non-null symbol pointers.
  - `CythonWriter`: replaced tautological `assert pkg is not None` with module inspection and package structure assertions.
  - `CffiWriter`: replaced vacuous assertions with FFI instance type and configuration checks.
  - `CtypesWriter`: replaced vacuous assertions with exported symbol `hasattr` checks and module verification.
  - `NimWriter`: replaced `check true` and echo tripwires with real `dynlib.loadLib` and `symAddr` symbol resolution and `check declared(...)` assertions.
  - `MojoWriter`: replaced `assert_true(True)` with `DLHandle.get_function` symbol verification and struct type assertions.
  - `LuaWriter`: replaced print stubs with `ffi.load` and symbol table verification.
  - `TreeSitterBackend`: fixed preprocessor conditional traversal to skip mutually exclusive `#elif`/`#else` branches when visiting `#if`/`#ifdef`, preventing duplicate or conflicting symbol extraction. Fixed `-x c` and `-std=c*` CLI override handling.
- `headerkit.backends.treesitter`: lightweight, zero-system-dependency parser backend using `tree-sitter-c` for parsing C headers without requiring system LLVM or `libclang`. Added support for nested preprocessor blocks (`#ifndef`, `#ifdef __cplusplus`) and pointer-return function prototypes.
- Comprehensive guide for Nim to Python packaging and deterministic memory management (`docs/guides/nim-python-packaging.md`) using `--mm:orc` and `scikit-build-core`.
- Real-world working example (`examples/nim_bridge/`) demonstrating compiled Nim library, Headerkit ctypes bindings, and binary wheel distribution.
- `treesitter` optional dependency extra in `pyproject.toml` (`pip install "headerkit[treesitter]"`).
- `headerkit.hooks` module implementing a unified hook architecture with priority tiers (`FALLBACK`, `STANDARD`, `PROJECT`, `OVERRIDE`), glob pattern matching, and `first_result` / `waterfall` dispatch modes.
- Exported hook symbols (`Priority`, `PipelineContext`, `HookImpl`, `HookRegistry`, `hook`, `HookDispatcher`, `HookCaller`, `execute_pipeline`) in top-level `headerkit` namespace.
- Core IR evolution: renamed `Header` to `SourceUnit` with `Header = SourceUnit` backward-compatibility alias, and added `InputSpec` for polyglot input classification.
- Unified backend and writer registry: migrated all 9 built-in writers and parser backends into `HookRegistry`, with `get_backend()` and `get_writer()` delegating to `HookDispatcher`.
- Enhanced `TreeSitterBackend` with recursive preprocessor block extraction, pointer return parsing, and void parameter handling.
- `execute_pipeline`: automated 3-stage execution pipeline (`parse_unit` -> `transform_unit` -> `write_output`) with context threading.
- Polyglot generator trunk migration: `generate()`, `generate_all()`, and `batch_generate()` accept `InputSpec` directly and route parsing through `parse_unit` and AST transformations through `transform_unit`.
- Added `runtime`, `language`, and `classification` parameters to `generate()`, `generate_all()`, `batch_generate()`, and `PipelineContext`.
- CLI `--runtime`, `--language`, and `--classification` options and corresponding `HEADERKIT_RUNTIME`, `HEADERKIT_LANGUAGE`, `HEADERKIT_CLASSIFICATION` environment variable overrides.
- `_load_hook_plugins()` for discovery and dynamic loading of third-party hook plugins via `headerkit.hooks` entry points.
- Cheap static capability discovery: `supported_languages` and `supported_classifications` declared on `ParserBackend` protocol, `TreeSitterBackend`, and `LibclangBackend`.
- Project `ROADMAP.md` defining strategic pillars and horizons (Now, Next, Later) for language integrations (Nim, Mojo), packaging templates, a unified priority/glob hook pipeline, polyglot input classification, `SourceUnit` IR evolution, and documentation sweeps.

### Changed

- Bumped `actions/checkout` to v7, `actions/cache` to v6, and `actions/setup-python` to v7 in GitHub Actions CI workflows.

## [0.29.0] - 2026-09-03

### Added

- Vendored official LLVM `cindex.py` Python bindings and `.pyi` type stubs for LLVM 22 (`llvmorg-22.1.8`) and LLVM 23 (`llvmorg-23.1.0`).
- Expanded supported LLVM version detection range to LLVM 18 through 23.

## [0.28.0] - 2026-09-03

### Added

- `NimWriter`: automated C++ smart pointer safety suite (`UniquePtr` with deleted `=copy` hook and `move`, `get`, `reset` procs; `SharedPtr` with `get`, `reset`, `useCount`).
- `CShimWriter`: `catch_exceptions` parameter to wrap C++ constructors, methods, and functions in `try ... catch (...)` blocks for exception safety across C-ABI boundaries.
- `headerkit.writers.cshim`: added `write_cshim` top-level convenience function.
- Real-world binding examples and idiomatic Nim wrappers in `examples/nim/` for RtMidi, RtAudio, NNG, and CLAP.
- `examples/generate_all.py` automation script to regenerate all binding examples.
- Reference documentation for `NimWriter` (`docs/reference/nim.md`) and `CShimWriter` (`docs/reference/cshim.md`).
- Bundled Cython `.pxd` stub declarations under `headerkit.stubs` (e.g. `pthread`, `stdatomic`, `stdarg`, `sys_socket`, `netinet_in`, `sys_statvfs`, `sys_un`, `termios`, `cpparray`, `cppchrono`, `cppvariant`, etc.).
- `CythonWriter`, `PxdWriter`, and `write_pxd` default `stub_cimport_prefix` to `"headerkit.stubs"`.
- Extended `HEADERKIT_STUB_TYPES` registry to automatically emit stub cimports for bundled stub types.

### Fixed

- `NimWriter`: emit `struct`, `union`, and `enum` tag specifiers in `{.importc.}` pragmas for C declarations.
- `NimWriter`: map `unsigned char` to `uint8` instead of deprecated `cuchar`.
- `NimWriter`: emit anonymous enum values as `const` instead of invalid named type declarations.
- `NimWriter`: deduplicate self-referential typedefs (e.g. `typedef struct foo foo`).
- `NimWriter`: disambiguate enum names that collide with function names (e.g. `foo_enum` with `importc: "foo"`).
- `NimWriter`: ensure enumerated parameter names for anonymous/unnamed function pointer and proc parameters.
- `NimWriter`: sanitize identifiers with leading/trailing underscores and namespace scope resolution (`::`).
- `NimWriter`: support C++ base class inheritance with `object of RootObj` and standard exception mapping.

## [0.27.0] - 2026-09-02

### Added

- Added `cshim` writer (`CShimWriter`) generating pure C-ABI wrappers (`extern "C"`) around C++ classes, constructors, destructors, methods, and namespaced free functions with opaque pointer handles.
- Registered `cshim` in `headerkit.writers` registry with default output pattern `{dir}/{stem}_cshim.cpp`.

## [0.26.0] - 2026-09-02

### Added

- Extended `Struct` IR node with `vtable_entries: list[Function]` to explicitly model virtual method tables and abstract interfaces.
- Extended `Typedef` and `Variable` IR nodes with `namespace: str | None` context tracking.
- Updated `libclang` backend to populate vtable layout / virtual entries on classes/structs, and record namespaces across typedefs and variables.
- Updated JSON serializer and deserializer to round-trip `vtable_entries` on structs, and `namespace` on `Typedef` and `Variable`.

## [0.25.0] - 2026-09-02

### Added

- Extended `Constant` IR node with `evaluated_value` (evaluated numeric / string value) and `raw_expression` (un-evaluated expression string).
- Added constant expression evaluator for safe arithmetic and bitwise macro expressions (e.g. `(1 << 3 | 0x02)`, `100 + 20 * 2`) in `libclang` backend.
- Extended `Function` IR node with `is_inline` boolean flag and `body` string preserving function definition implementations.
- Updated JSON serializer and deserializer to round-trip `evaluated_value`, `raw_expression`, `is_inline`, and `body`.

## [0.24.0] - 2026-09-02

### Added

- Added `nim` writer (`NimWriter`) generating idiomatic Nim bindings with full C (`{.importc.}`) and C++ (`{.importcpp.}`) interop.
- Added support in `NimWriter` for generic structs/classes (`type Foo[T] = object`), generic procedures (`proc bar[T](x: T)`), constructors, member methods, inheritance, references, default arguments, and identifier escaping.
- Added C++ operator overloading maps (`operator[]`, `operator==`, etc.), move semantics mapping (`sink T` / `var T`), smart pointers (`SharedPtr[T]`, `UniquePtr[T]`, `WeakPtr[T]`), and container iteration (`iterator items*`).
- Registered `nim` in `headerkit.writers` registry with default output pattern `{dir}/{stem}.nim`.

## [0.23.0] - 2026-09-02

### Added

- Extended `Declaration` types (`Struct`, `Function`, `Typedef`, `Variable`) with `attributes` list and `is_deprecated` boolean flag.
- Added `alignment` attribute to `Struct` and `Variable` IR nodes for explicit data alignment (`__attribute__((aligned(N)))`, `alignas`).
- Added `is_anonymous_transparent` attribute to `Field` IR node for transparent anonymous struct/union member detection.
- Updated libclang backend to extract declaration attributes, deprecation status, alignment, and anonymous transparent fields.
- Updated JSON serializer and deserializer to round-trip attributes, deprecation flags, alignment, and transparent field markers.

## [0.22.0] - 2026-09-02

### Added

- Added `Reference` IR node (`target`, `is_rvalue`, `qualifiers`) to distinguish C++ lvalue (`&`) and rvalue (`&&`) reference types from raw pointers.
- Extended `Parameter` IR node with `default_value` attribute to preserve default argument expressions.
- Extended `Function` IR node with `is_noexcept` attribute for C++ exception specifications (`noexcept`, `throw()`).
- Updated libclang backend to extract `Reference` types, parameter `default_value`, and `is_noexcept`.
- Updated JSON serializer/deserializer and Cython writer to support references, default values, and noexcept specifications.

## [0.21.0] - 2026-09-02

### Added

- Added `template_params` support to `Function` IR node.
- Updated libclang backend to extract template parameters for free function templates (`CursorKind.FUNCTION_TEMPLATE`) and member method templates.
- Updated JSON writer and deserializer to roundtrip `Function.template_params`.
- Updated Cython writer to render template type parameters for functions and methods.

## [0.20.0] - 2026-09-02

### Added

- Added `BaseSpecifier` IR node (`name`, `access`, `is_virtual`) to model C++ class base specifiers and exported it in `headerkit` top-level package.
- Extended `Struct` IR node with C++ class semantics: `bases`, `is_abstract`, `constructors`, `destructor`, and `conversions`.
- Extended `Function` IR node with C++ member function semantics: `is_static`, `is_const`, `is_virtual`, `is_pure_virtual`, `is_explicit`, `access`, `is_deleted`, and `is_defaulted`.
- Extended `Field` IR node with `access` and `is_static` attributes for C++ class member representation.
- Updated libclang backend to extract C++ base classes, constructors, destructors, conversion operators, static members, and member access/virtual/const qualifiers.
- Updated JSON writer and deserializer to support full round-trip serialization of C++ class semantics.

## [0.19.2] - 2026-05-06

### Changed

- Corrected test subprocess interception dependency distribution name from `python-tripwire` to `pytest-tripwire`. The package was renamed again upstream after 0.19.1 shipped; the import name (`tripwire`) and plugin proxy attribute (`tripwire.subprocess`) are unchanged. Test minimum is now `pytest-tripwire>=0.20,<1`.

## [0.19.1] - 2026-04-30

### Changed

- Migrated test subprocess interception dependency from `bigfoot` to `python-tripwire`. The package was renamed upstream in 0.20.0 (2026-04-26); the import name is now `tripwire` (was `bigfoot`) and the plugin proxy attribute is `tripwire.subprocess` (was `bigfoot.subprocess_mock`). Test minimum is `python-tripwire>=0.20,<1`.

## [0.19.0] - 2026-04-04

### Changed

- CFFI writer default output extension changed from `.py` to `.cdef.txt` (output is C declarations, not Python)
- CFFI writer `hash_comment_format` now uses C89-compatible `/* */` comments instead of `#`

## [0.18.0] - 2026-04-04

### Added

- `HEADERKIT_STORE_DIR` environment variable for configuring the store directory, enabling cibuildwheel integration where the store must reside on a host-visible mounted volume. Resolution order: explicit `store_dir` parameter > `HEADERKIT_STORE_DIR` env var > config file `store_dir` > auto-detect from project root.
- cibuildwheel integration guide: Docker volume mount pattern for persisting `.headerkit/` store on Linux builds

## [0.17.0] - 2026-04-04

### Added

- `headerkit store merge` CLI subcommand for combining platform-specific store directories into a single `.headerkit/` directory
- `store_merge()` Python API with `MergeResult` dataclass for programmatic store merging
- Merge logic handles IR entries (`ir/`), output entries (`output/<writer>/`), and `index.json` files
- Duplicate detection: entries with the same slug and cache_key are skipped; entries with the same slug but different cache_key are overwritten (later sources win)

## [0.16.1] - 2026-04-04

### Fixed

- `define_patterns` now resolves `#include` directives when `code=` is provided, scanning included files for matching macros (umbrella header pattern)

## [0.16.0] - 2026-04-04

### Added

- Environment variable expansion in config: `${VAR}` syntax in string values, with error on unset variables
- `define_patterns` CFFI writer option: regex-based macro extraction from raw header text, emitting `#define NAME ...` for CFFI compile-time resolution
- `extra_cdef` CFFI writer option: append literal cdef lines (e.g., `extern "Python"` callbacks) to generated output
- CFFI Build Integration documentation guide

## [0.15.1] - 2026-04-03

### Removed

- `action.yml` composite GitHub Action (use standard workflow with `peter-evans/create-pull-request` instead; see CI Store Population guide)

### Changed

- Renamed "GitHub Action" docs guide to "CI Store Population" with workflow examples using standard GitHub Actions building blocks

## [0.15.0] - 2026-04-03

### Added

- Glob-based header selection: CLI positional args accept quoted glob patterns (e.g., `headerkit 'include/**/*.h'`)
- `[[tool.headerkit.headers]]` array-of-tables config for header selection with per-pattern overrides
- `--exclude` CLI flag for glob-based header exclusion
- `[tool.headerkit.output]` config section for per-writer output path templates
- Output path template variables: `{stem}`, `{name}`, `{dir}`
- `-o WRITER:TEMPLATE` CLI flag for per-writer output path templates
- `batch_generate()` public API for multi-header generation with fail-fast semantics
- `BatchResult` dataclass for batch generation results
- `resolve_headers()`, `resolve_output_path()`, `check_output_collisions()` public API functions
- `default_output_pattern` class attribute on all built-in writers
- GitHub Action (`action.yml`) for CI cache population

### Changed

- **Breaking:** Store directory renamed from `.hkcache/` to `.headerkit/`
- **Breaking:** IR schema version bumped from "2" to "3" (all existing cache entries invalidated)
- **Breaking:** `cache_dir` parameter renamed to `store_dir` in `generate()`, `generate_all()`, and `HeaderkitConfig`
- **Breaking:** `--cache-dir` CLI flag renamed to `--store-dir`
- **Breaking:** Config key `[tool.headerkit.cache].cache_dir` moved to `[tool.headerkit].store_dir`
- **Breaking:** `-w WRITER[:PATH]` syntax removed; use `-w WRITER` and `-o WRITER:TEMPLATE`
- **Breaking:** CLI positional args accept globs; quote patterns to prevent shell expansion
- **Breaking:** `WriterSpec.output_path` renamed to `WriterSpec.output_template`
- `find_cache_dir()` simplified to single-pass project-root-only lookup (no walk-up for existing directory)

## [0.14.0] - 2026-04-02

### Added

- `detect_process_triple()` replaces `detect_host_triple()` with process-aware detection using `HOST_GNU_TYPE` (POSIX) or `sysconfig.get_platform()` (Windows)
- musl libc detection: correctly produces `linux-musl` triples on Alpine and other musl-based systems (via `os.confstr` sniff for pre-3.13 Python where `HOST_GNU_TYPE` may report `gnu` on musl)

### Changed

- **Breaking:** `detect_host_triple()` removed; use `detect_process_triple()`
- Simplified target detection: one signal per platform instead of 5-step fallback chain. `HOST_GNU_TYPE` on POSIX, `get_platform()` on Windows. For cross-compilation, set `--target` explicitly.

## [0.13.0] - 2026-04-01

### Added

- Target triple support for cross-compilation: `generate(target="aarch64-apple-darwin")`
- `detect_host_triple()` and `resolve_target()` public API functions
- `--target` CLI flag for specifying target triple
- `HEADERKIT_TARGET` environment variable for target triple configuration
- `[tool.headerkit] target` config key in pyproject.toml
- Target triple included in cache directory slugs for readability
- `normalize_triple()` inserts `unknown` vendor for 3-component triples (e.g., `x86_64-linux-gnu` -> `x86_64-unknown-linux-gnu`)

### Changed

- **Breaking:** Cache keys now use LLVM target triple instead of `sys.platform` + `platform.machine()` + Python version. Existing `.hkcache/` entries will be regenerated on first use (IR schema version bumped to 2).
- **Breaking:** `compute_ir_cache_key()` requires `target` parameter instead of reading platform/arch at runtime
- **Breaking:** `PopulateTarget` uses `target_triple` field instead of `sys_platform`, `machine`, `py_impl`
- **Breaking:** `PLATFORM_MAP` values are target triple strings instead of `(platform, machine)` tuples
- **Breaking:** Removed `py_impl_for_version()` from `_populate` module
- Python version removed from IR cache key (IR represents parsed C declarations, not Python-specific output)
- `-target` flag automatically passed to libclang for correct cross-platform parsing
- Bump `bigfoot` test dependency minimum to 0.19

### Fixed

- Add positional-only parameter markers to vendored clang binding stubs (v18-v21) to fix stubtest failures

## [0.12.5] - 2026-03-29

### Fixed

- Document `LibclangUnavailableError` in backends reference page
- Update `is_backend_available()` description in architecture guide to reflect real load test behavior
- Update build backend guide to reference `LibclangUnavailableError` instead of generic error

## [0.12.4] - 2026-03-29

### Added

- `LibclangUnavailableError` exception for clear error reporting when libclang cannot be found after all recovery attempts (auto-install, cache fallback)
- Deploy dev docs on every push to main (docs fixes go live immediately)
- CI warning when a PR is missing a version bump or changelog entry

### Fixed

- Auto-install now triggers correctly when libclang library is not found (was broken in 0.12.3 due to exception type mismatch between `RuntimeError` from `parse()` and the `ValueError` catch in `generate()`)
- Fix incorrect CLI command in install-libclang reference (`headerkit-install-libclang` -> `headerkit install-libclang`)
- Fix wrong default writer comment in generate reference (default is cffi, not json)
- Fix references to non-existent `Writer` base class in cache guide examples
- Fix broken relative doc links in README (use full docs site URLs)
- Fix LLVM version example in installation guide (17 -> 18, matching supported range)
- Update `site_description` in mkdocs.yml to mention all writers

### Changed

- `generate()` uses explicit `is_backend_available()` check instead of exception catching to detect missing libclang and trigger the output-cache fallback / auto-install flow
- `is_backend_available("libclang")` now performs a real library load test via `is_system_libclang_available()` instead of only checking whether the backend class is registered
- CI and install-libclang workflows now skip on docs-only changes via `paths-ignore`

## [0.12.3] - 2026-03-29

### Changed

- Removed `reload_backends()` from the public API. The libclang backend class is now always registered, and `_configure_libclang()` re-searches on every call (short-circuiting only when the library is already loaded). After `auto_install()` puts libclang on disk, the next `get_backend().parse()` call naturally finds it without any manual reload step.

### Fixed

- Linux: libclang search now includes versioned .so names (`libclang.so.18`, `libclang-18.so`) in RHEL/Fedora `/usr/lib64` and generic `/usr/lib` paths, not just the unversioned `libclang.so` from clang-devel
- Cross-platform: libclang search now checks the `clang/native/` directory from the PyPI `libclang` package (`pip install libclang`) as a fallback on all platforms
- Windows: `_configure_libclang()` now calls `os.add_dll_directory()` for the candidate DLL's directory before loading, so dependent DLLs (e.g. zlib, ncurses) can be found

## [0.12.2] - 2026-03-29

### Fixed

- Corrected changelog: added missing release sections for v0.10.1, v0.11.0, and v0.12.0 that were previously lumped into [Unreleased]

## [0.12.1] - 2026-03-28

### Fixed

- Linux: `install_linux()` now tries the lighter `clang-libs` package before falling back to `clang-devel` on dnf-based distros (RHEL/AlmaLinux/manylinux_2_28)
- Windows x64: `_install_windows_x64()` now detects pre-installed LLVM at the default location before attempting Chocolatey, and configures PATH/`os.add_dll_directory()` so ctypes can find libclang.dll
- `auto_install()` now falls back to `pip install libclang` when platform-specific installation fails or the library is not loadable after install
- Backend registry caching bug: after `auto_install()` installs libclang at runtime, `get_backend("libclang")` now correctly discovers the newly available backend instead of returning the stale "no backends available" result
- `_find_project_root()` regression from cache populate PR: restored use of `absolute()` instead of `resolve()` to prevent Windows 8.3 short-name expansion
- Wired `load_populate_config()` defaults into `populate()` and CLI for platforms, python_versions, and timeout

## [0.12.0] - 2026-03-28

### Added

- `headerkit cache populate` CLI subcommand for generating cache entries across multiple target platforms using Docker containers
- `populate()` Python API with `PopulateResult` and `PopulateTarget` data types
- cibuildwheel config parsing (`--cibuildwheel`) for automatic target detection
- Per-platform Docker image configuration via `[tool.headerkit.cache.populate.images]`
- Dry-run mode (`--dry-run`) for previewing planned cache population targets

### Fixed

- `parse_cibuildwheel_config()` no longer emits spurious macOS/Windows warnings when those platforms are not in the build matrix (e.g., `build = "cp312-manylinux*"`)

## [0.11.0] - 2026-03-28

### Added

- Opt-in auto-install of libclang when `generate()` needs to parse but the backend is unavailable, with layered configuration (highest precedence first):
  1. `generate(auto_install_libclang=True)` kwarg
  2. `HEADERKIT_AUTO_INSTALL_LIBCLANG=1` environment variable
  3. `auto_install_libclang = true` in `[tool.headerkit]` of pyproject.toml
  4. Default: disabled (opt-in)
- `auto_install()` function in `install_libclang` module for quiet, non-interactive libclang installation
- `headerkit.build_backend_auto` PEP 517 build backend that wraps `headerkit.build_backend` with auto-install enabled (sets `HEADERKIT_AUTO_INSTALL_LIBCLANG=1`)

### Changed

- Auto-install is now opt-in (default disabled) instead of opt-out. Projects that relied on the previous default-enabled behavior should set `HEADERKIT_AUTO_INSTALL_LIBCLANG=1` or use `headerkit.build_backend_auto` as their build backend.
- Replaced `HEADERKIT_NO_AUTO_INSTALL` env var with `HEADERKIT_AUTO_INSTALL_LIBCLANG` (set to `1` to enable)
- CI test matrix reduced to full Python range on Ubuntu only, with latest Python on macOS and Windows

### Fixed

- `_find_project_root()` no longer uses `Path.resolve()`, which on Windows can expand 8.3 short names and cause the `.git` marker walk to escape the intended project boundary, potentially triggering unwanted auto-install

## [0.10.1] - 2026-03-28

### Fixed

- `generate()` now falls back to the output cache when the backend (libclang) is unavailable, enabling the documented libclang-free build workflow

## [0.10.0] - 2026-03-28

### Added

- Two-layer content-addressable cache store (`.hkcache/` directory)
- `generate()` and `generate_all()` public API for cache-aware header generation
- `json_to_header()` JSON IR deserializer (inverse of `header_to_json()`)
- `GenerateResult` dataclass for multi-writer generation results
- PEP 517 build backend (`headerkit.build_backend`) for consumer projects
- CLI flags: `--no-cache`, `--no-ir-cache`, `--no-output-cache`, `--cache-dir`
- Environment variables: `HEADERKIT_NO_CACHE`, `HEADERKIT_NO_IR_CACHE`, `HEADERKIT_NO_OUTPUT_CACHE`
- Cache subcommands: `headerkit cache status`, `headerkit cache clear`, `headerkit cache rebuild-index`
- `[tool.headerkit.cache]` configuration section in pyproject.toml
- Writer `cache_output` attribute for opt-out of output caching (diff, prompt writers)

### Changed

- CLI `main()` now delegates to `generate()` for cache-integrated pipeline

## [0.8.4] - 2026-03-11

### Changed

- Deduplicated README and removed premature autopxd2 mention

## [0.8.3] - 2026-03-10

### Changed

- Updated README: fixed project description, added Mermaid architecture diagram, listed supported output formats and plugin system
- Added CHANGELOG checklist item to PR template
- Bumped `actions/checkout` to v6, `actions/cache` to v5, `peter-evans/create-pull-request` to v8

### Fixed

- Test assertion for Homebrew detection was unconditional but the code only probes `brew` on macOS

## [0.8.2] - 2026-03-05

### Changed

- Updated bigfoot dependency to >=0.4.1 and adopted `with bigfoot:` context manager syntax
- Test suite now uses [bigfoot](https://github.com/axiomantic/bigfoot) for subprocess interception. `subprocess.run` and `shutil.which` mocks in `test_install_libclang.py`, `test_version_detect.py`, `test_libclang.py`, and `test_windows_detection.py` are replaced with `bigfoot.subprocess_mock`, which enforces strict FIFO ordering and fails fast on unexpected calls.
- Integration test writer assertions extracted into shared helpers (`_check_ctypes_write`, `_check_cython_write`, etc.) in `test_real_headers.py`, eliminating repeated assertion logic across the five library test classes.

## [0.8.1] - 2026-03-04

### Removed

- `headerkit-install-libclang` standalone console script. Use `headerkit install-libclang` instead.

## [0.8.0] - 2026-03-03

### Fixed

- Prompt writer incorrectly classified `typedef void (*fn)(int);` as a plain typedef in compact and standard modes when libclang represents the underlying type as `Pointer(FunctionPointer(...))`. Compact mode now emits `CALLBACK fn(...) -> void` and standard mode places it in the `callbacks:` section.
- Prompt writer cross-reference map built keys with `struct`/`union`/`enum` prefixes (e.g. `struct Config`) while declaration dicts use bare names (`Config`), so `used_in` was never populated. Keys are now normalized to bare names.
- Tautological writer tests: all 4 writer test files asserted `writer.write(h) == writer_function(h)`, which is always true since write() delegates to the function; replaced with specific output content assertions
- Tautological protocol checks: `isinstance(writer, WriterBackend)` only checks attribute names exist on runtime-checkable Protocol; replaced with behavioral verification in all writer test files
- Integration writer tests used `len(output) > 0` as sole assertion; replaced with known-symbol checks in all 10 cffi/json writer integration tests
- `test_version_detect.py` patched `shutil.which` in the wrong namespace (global instead of `headerkit._clang._version`), making the mock ineffective; test passed by coincidence
- `test_ensure_backends_loaded_handles_import_error` had a broken patch target and asserted a flag that was set unconditionally before the import; fixed with `sys.modules` sentinel and registry-empty assertion
- `test_verify_libclang_success/failure` had a dead `@patch` decorator with `create=True` on a nonexistent attribute and never verified the target function was called; removed dead patch, added `assert_called_once()`
- `test_dict_is_json_serializable` asserted `json.loads(json.dumps(x)) == x`, which is always true for JSON-native dicts; replaced with structural verification
- `test_loader.py` used `hasattr` as sole assertion for module attributes; replaced with `inspect.isclass` checks
- `test_pypy_compat.py` `test_value_property` was an exact duplicate of `test_init_with_string`; deleted
- Macro tests used permissive `or` (accepting int or str) and conditional `if` guards that passed silently when features were absent; resolved type ambiguity and pinned behavior assertions
- `test_negative_integer_macro` comment stated `value is None` but never asserted it; added the assertion
- `test_install_linux_apt` truncated command assertions to first 2-3 tokens, missing package names; now asserts full commands
- `test_install_windows_arm64` had self-referential path assertion comparing `call_args` to itself; replaced with computed expected path
- `test_ir.py` `test_pointer_with_qualifiers` docstring incorrectly described output as `int * const` when actual output is `const int*`; fixed docstring
- `test_ir.py` used substring checks (`"packed" in str(s)`, `"stdcall" in str(f)`) instead of exact equality; replaced with exact string assertions
- `test_public_api.py` `test_type_aliases_are_unions` checked union membership but not completeness; replaced with exact set equality assertions
- `test_diff.py` `test_format_description_property` used disjunctive assertion unlike every other writer test; replaced with exact string match
- `test_ctypes.py` `test_type_map_completeness` used magic number `len(MAP) == 28`; replaced with full key set assertion
- `test_ctypes.py` `test_section_headers_present` checked ordering of 4 of 5 sections, missing "Typedefs"; added
- `test_cython.py` `test_basic_cppclass` accepted both spaces and tabs via `or`; pinned to 4-space indentation matching source
- Integration `conftest.py` caught bare `except Exception` in all 6 download fixtures; narrowed to `urllib.error.URLError`, `socket.timeout`, `OSError`
- Integration `_parse_header` used `pytest.skip` for parse failures on known-good headers; changed to `pytest.fail`
- `test_roundtrip.py` conditional assertions (`if version_constants:`, `if switch_td:`) replaced with pinned behavior assertions
- Five duplicate `skip_if_no_libclang` autouse fixtures across test_libclang.py classes consolidated into one module-level fixture
- Redundant `CIR_CLANG_VERSION` env cleanup across 21 test methods replaced with module-level autouse fixture in test_version_detect.py
- Repeated `mock_winreg` constant setup across 6 test methods extracted into shared fixture in test_windows_detection.py

### Added

- `headerkit` CLI command: parse C headers and emit output via configurable writers (`headerkit input.h`, `headerkit -w cffi:out.h -w json:out.json input.h`)
- `headerkit install-libclang` subcommand: installs libclang system packages (delegates to `headerkit-install-libclang`)
- `--backend` flag to select parser backend (default: `libclang`)
- `-I` / `--include-dir`, `-D` / `--define`, `--backend-arg` flags for backend configuration
- `-w WRITER[:OUTPUT]` flag for writer selection and output routing; multiple writers supported; omitting output path sends to stdout
- `--writer-opt WRITER:KEY=VALUE` flag for per-writer constructor options; multiple flags accumulate list values
- `--config PATH` and `--no-config` flags for config file control
- Config file support: `.headerkit.toml` (preferred) and `[tool.headerkit]` section in `pyproject.toml`, discovered by walking up from the current directory
- Entry-point plugin discovery: install third-party backends/writers and register them under `headerkit.backends` or `headerkit.writers` entry-point groups
- `plugins` config key for explicit plugin module imports
- Multi-input file support via synthetic umbrella header with automatic prefix filtering
- `toml` optional dependency group (`pip install headerkit[toml]`) for TOML config support on Python 3.10
- Integration roundtrip tests for ctypes, Cython, Lua, prompt, and diff writers: full `libclang → IR → writer output` pipeline coverage for each writer, exercising structs, enums, functions, typedefs, constants, anonymous types, and empty headers.
- Integration smoke tests for all seven writers against real-world library headers (sqlite3, zlib, lua, curl, SDL2) in `test_real_headers.py`.
- `test_unknown_declaration_kind` for JSON writer's `"unknown"` fallback path (previously untested code path)
- `test_identical_functions/structs/enums_produce_no_diff` verifying unchanged declarations produce zero diff entries
- `test_field_added_in_middle_is_breaking` for struct diff edge case (middle insertion vs end append)
- `test_is_umbrella_header_system_headers_excluded` verifying system header filtering in umbrella detection
- `test_mixed_declarations` split into per-verbosity tests for better failure diagnosis in prompt writer

## [0.7.3] - 2026-03-01

### Fixed

- `get_backend_info()` always reported backends as `available: True` due to tautological check; now attempts instantiation to determine real availability
- Integration test fixtures silently swallowed all download exceptions, causing the entire integration suite to report green with zero assertions; fixtures now emit warnings on failure
- `_parse_header` test helper caught overly broad `Exception`, masking parser regressions as skipped tests; narrowed to `RuntimeError`
- Clang loader fallback tests did not verify which version module was loaded, allowing wrong-version regressions to survive
- Windows clang detection tests did not verify constructed file paths, allowing path construction bugs to survive
- `test_anonymous_struct_skipped` in ctypes writer contained a tautological assertion that could never fail
- `test_output_is_valid_json` serialized 6 declaration types but never verified any content
- `test_mixed_declarations` in prompt writer ran 3 modes x 7 declarations but only checked output was non-empty

### Added

- Macro parsing test coverage: integer, hex, negative, string, and function-like macro tests for the libclang backend (~190 lines of previously untested production code)
- Forward-declaration-to-definition replacement test for libclang backend
- `_ensure_backends_loaded` error handling and lazy loading tests
- Complex pattern roundtrip tests: bitfield structs, array-in-struct fields, nested structs
- Minimum declaration count assertions for real-world header integration tests (sqlite3, zlib, lua, curl, SDL2)
- Type-aware symbol verification in integration tests (checks declaration kind, not just name)
- JSON roundtrip count consistency checks (writer output count must match parse result)
- Invalid `CIR_CLANG_VERSION` env var fallthrough test
- Union member verification for `TypeExpr` and `Declaration` public API type aliases
- Registry cardinality and content checks for Cython type registries
- Anonymous declaration skip test for diff writer
- Variable integration test for Lua writer
- `--skip-verify` flag test and package manager fallthrough test for install_libclang
- PROVENANCE file hash verification in vendor tests

## [0.7.2] - 2026-03-01

### Fixed

- macOS cross-architecture: `ValueError: Unknown backend: 'libclang'` when an x86_64 process (e.g. cibuildwheel x86_64 test phase on Apple Silicon) finds an arm64-only Homebrew libclang first; `_configure_libclang` now iterates through all candidate paths instead of giving up after the first architecture-incompatible dylib fails to load

## [0.7.1] - 2026-02-28

### Fixed

- Windows x64: `LibclangError: function 'clang_getFullyQualifiedName' not found` when system LLVM is older than the vendored v21 bindings; disable cindex compatibility check so unused functions are silently skipped
- Windows x64: `install_libclang` now pins the Chocolatey LLVM version to match the vendored bindings instead of installing whatever default Chocolatey provides
- macOS CI: `ValueError: Unknown backend: 'libclang'` in test environments where libclang is bundled inside a versioned Xcode app bundle (e.g. `Xcode_16.2.app`); added xcrun-based discovery and glob for versioned Xcode paths
- Missing `concurrency` groups on six GitHub Actions workflows (auto-tag, check-llvm, check-python, docs, pre-commit-autoupdate, release)

## [0.7.0] - 2026-02-28

### Added

- `.pyi` type stubs for all vendored clang bindings (v18-v21), enabling mypy to type-check code that uses vendored clang modules
- CI stubtest gate: `mypy.stubtest` validates that `.pyi` stubs match the runtime API of each vendored version, blocking merges on mismatch
- Pre-commit autoupdate workflow (`.github/workflows/pre-commit-autoupdate.yml`): weekly automated PRs to update pre-commit hook versions
- Auto-vendor workflow: `check-llvm.yml` now opens PRs with vendored code and copied stubs when new LLVM versions are detected (falls back to issues on failure)
- Vendoring script (`scripts/vendor_clang.py`): downloads cindex.py, writes PROVENANCE, copies nearest version's stubs, updates `VENDORED_VERSIONS`
- Unit tests for the vendoring script (`tests/test_vendor_clang.py`)

### Changed

- Removed mypy exclude for vendored clang directories; mypy now uses `.pyi` stubs instead of ignoring vendored code entirely

## [0.6.1] - 2026-02-28

### Fixed

- README incorrectly claimed "zero runtime dependencies" when libclang is a required system dependency; clarified to "zero Python package dependencies"
- `Function.__str__` now places calling convention after return type (`int __stdcall__ foo()` not `__stdcall__int foo()`)
- `is_typedef` in JSON writer now only included when `True`, consistent with other boolean flags

### Added

- Auto-tag GitHub Action: automatically creates version tags when `pyproject.toml` version changes on main, triggering the release pipeline

### Changed

- Extract duplicated clang.exe version detection into `_get_version_from_clang_exe()` helper
- Use `normalize_path()` in Windows search path tests instead of manual string replacement
- Strengthen test assertions for const qualifiers on pointer types and cimport line detection

## [0.6.0] - 2026-02-28

### Added

- `stub_cimport_prefix` parameter for CythonWriter/PxdWriter: configurable stub cimport generation (e.g., `from autopxd.stubs.stdarg cimport va_list`)
- Comprehensive Cython type registry tests (17 tests)
- Additional Cython writer tests: full-text output assertions, pointer/array formatting, stub cimport integration (30 tests)

## [0.5.0] - 2026-02-28

### Added

- `Field.bit_width` IR field for C bitfield support
- `Field.anonymous_struct` IR field for anonymous nested struct/union members
- `Struct.is_packed` IR field for `__attribute__((packed))` structs
- `Function.calling_convention` and `FunctionPointer.calling_convention` IR fields
- CtypesWriter: generates complete Python ctypes binding modules
- CythonWriter: generates Cython .pxd declaration files with C++ support (ported from autopxd2)
- DiffWriter: generates API compatibility reports in JSON or Markdown format
- PromptWriter: generates token-optimized IR output for LLM context (compact/standard/verbose)
- LuaWriter: generates LuaJIT FFI binding files

## [0.4.0] - 2026-02-28

### Added

- PyPy support: compatibility shim for `c_interop_string` that avoids `c_char_p` subclassing
- End-to-end integration tests for JSON writer pipeline (18 new roundtrip tests)
- Real-world library header tests: sqlite3, zlib, lua, libcurl, SDL2, CPython (21 tests)
- CI caching for downloaded test headers
- `download` pytest marker for tests requiring network access
- Unit tests for PyPy compatibility monkey-patch (20 tests)

### Changed

- Renamed package from `clangir` to `headerkit` (`pip install headerkit`)
- Console script renamed from `clangir-install-libclang` to `headerkit-install-libclang`

## [0.3.3] - 2026-02-28

### Added

- CI workflow to test `headerkit-install-libclang` across Linux, macOS, and Windows

## [0.3.2] - 2026-02-28

### Added

- `headerkit-install-libclang` CLI tool for automated platform-specific libclang installation
- Console script entry point (`headerkit-install-libclang`) in pyproject.toml
- Documentation guide and API reference for the install tool
- Support for Linux (dnf, apt-get, apk), macOS (Homebrew), Windows x64 (Chocolatey), and Windows ARM64 (direct LLVM download)

### Fixed

- `install_libclang` verification result was ignored, now returns exit code 1 on verification failure
- Narrowed broad `except Exception` to `(ImportError, OSError, RuntimeError)` in verification

## [0.3.1] - 2026-02-28

### Added

- Mermaid diagrams in documentation: pipeline flowcharts and IR class hierarchies

### Fixed

- JSON export tutorial incorrectly listed `is_union` as a JSON output field
- Quickstart guide showed wrong pointer spacing (`char *` vs `char*`)
- `header_to_cffi` docstring converted from Google-style to Sphinx-style for mkdocstrings

## [0.3.0] - 2026-02-27

### Added

- Pluggable writer protocol (`WriterBackend`) mirroring the existing backend registry pattern
- Writer registry with `register_writer()`, `get_writer()`, `list_writers()`, `is_writer_available()`, `get_default_writer()`, `get_writer_info()`
- `CffiWriter` class wrapping `header_to_cffi()` with self-registration as default writer
- `JsonWriter` with `header_to_json()` and `header_to_json_dict()` for full IR serialization
- Public API re-exports for all writer protocol symbols in `headerkit.__init__`
- MkDocs documentation site with Material theme and mkdocstrings autodoc
- 6 API reference pages auto-generated from docstrings
- 6 guide pages: installation, quickstart, architecture, CFFI usage, custom backends, custom writers
- 4 tutorial pages: PXD writer, ctypes writer, JSON export, C header cleanup
- Versioned documentation via mike with version selector dropdown
- GitHub Pages deployment workflow triggered on tagged releases
- `docs` optional dependency group in pyproject.toml

## [0.2.0] - 2026-02-27

### Added

- Windows platform support: LLVM version detection via registry and Program Files scan
- Windows system header detection (`_get_windows_system_headers()`)
- Windows DLL search paths for libclang loading
- Python 3.14 support
- Weekly `check-python.yml` workflow for Python pre-release compatibility
- Full Windows CI in test matrix (ubuntu, macos, windows x Python 3.10-3.14)

### Fixed

- Three test failures on Windows CI (path separators, platform-specific mocks)

## [0.1.0] - 2026-02-26

### Added

- IR data model: `Header`, `Function`, `Struct`, `Enum`, `Typedef`, `Variable`, `Constant`, and type expressions (`CType`, `Pointer`, `Array`, `FunctionPointer`)
- Pluggable backend registry with `ParserBackend` protocol, `register_backend()`, `get_backend()`, `list_backends()`
- Libclang backend extracted from autopxd2 with LLVM 18-21 support
- CFFI cdef writer (`header_to_cffi()`) extracted from pynng
- Vendored clang Python bindings (`cindex.py`) for LLVM 18, 19, 20, 21
- LLVM version auto-detection: env var, llvm-config, pkg-config, clang preprocessor, `/usr/lib/llvm-N/`, Homebrew
- Public API re-exports in `headerkit.__init__`
- CI/CD: GitHub Actions test matrix, lint (ruff + mypy), release workflow with PyPI trusted publishing
- Pre-commit hooks for ruff, mypy, and standard checks
- LLVM license compliance for vendored bindings

[Unreleased]: https://github.com/axiomantic/headerkit/compare/v0.38.1...HEAD
[0.38.1]: https://github.com/axiomantic/headerkit/compare/v0.38.0...v0.38.1
[0.38.0]: https://github.com/axiomantic/headerkit/compare/v0.37.0...v0.38.0
[0.37.0]: https://github.com/axiomantic/headerkit/compare/v0.29.0...v0.37.0
[0.29.0]: https://github.com/axiomantic/headerkit/compare/v0.28.0...v0.29.0
[0.28.0]: https://github.com/axiomantic/headerkit/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/axiomantic/headerkit/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/axiomantic/headerkit/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/axiomantic/headerkit/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/axiomantic/headerkit/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/axiomantic/headerkit/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/axiomantic/headerkit/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/axiomantic/headerkit/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/axiomantic/headerkit/compare/v0.19.2...v0.20.0
[0.19.2]: https://github.com/axiomantic/headerkit/compare/v0.19.1...v0.19.2
[0.19.1]: https://github.com/axiomantic/headerkit/compare/v0.19.0...v0.19.1
[0.19.0]: https://github.com/axiomantic/headerkit/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/axiomantic/headerkit/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/axiomantic/headerkit/compare/v0.16.1...v0.17.0
[0.16.1]: https://github.com/axiomantic/headerkit/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/axiomantic/headerkit/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/axiomantic/headerkit/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/axiomantic/headerkit/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/axiomantic/headerkit/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/axiomantic/headerkit/compare/v0.12.4...v0.13.0
[0.12.4]: https://github.com/axiomantic/headerkit/compare/v0.12.3...v0.12.4
[0.12.3]: https://github.com/axiomantic/headerkit/compare/v0.12.2...v0.12.3
[0.12.2]: https://github.com/axiomantic/headerkit/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/axiomantic/headerkit/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/axiomantic/headerkit/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/axiomantic/headerkit/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/axiomantic/headerkit/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/axiomantic/headerkit/compare/v0.8.4...v0.10.0
[0.8.4]: https://github.com/axiomantic/headerkit/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/axiomantic/headerkit/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/axiomantic/headerkit/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/axiomantic/headerkit/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/axiomantic/headerkit/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/axiomantic/headerkit/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/axiomantic/headerkit/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/axiomantic/headerkit/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/axiomantic/headerkit/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/axiomantic/headerkit/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/axiomantic/headerkit/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/axiomantic/headerkit/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/axiomantic/headerkit/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/axiomantic/headerkit/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/axiomantic/headerkit/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/axiomantic/headerkit/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/axiomantic/headerkit/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/axiomantic/headerkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/axiomantic/headerkit/releases/tag/v0.1.0
