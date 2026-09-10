"""Under CI, a skipped test is a failure.

A test that cannot run reports the same colour as a test that ran and passed.
That is the defect this suite spends most of its assertions hunting -- absence
reading as fine -- living in the harness itself. Locally the skip is the right
behaviour: a contributor without Nim installed should still be able to run the
suite. In CI it is not, because CI is where "the toolchain is installed" is a
claim somebody is relying on.

Two mechanisms, because either alone leaves a door open.

``require_program`` and ``missing_toolchain`` are the point-of-use half. They
replace a ``pytest.skip`` at the site that knows *which* tool is missing, so the
CI failure names the tool and says how to install it. They only cover the sites
that call them.

The session gate in ``conftest.py`` is the other half, and it is the one that
cannot be bypassed by accident. It watches every skip the session produces --
``pytest.skip`` calls this module has never heard of, ``skipif`` markers,
fixtures that skip on an unavailable backend -- and fails the run under CI on
any skip not named in ``ALLOWED_SKIPS`` below. A new skip site added tomorrow is
covered on the day it is written, without anyone remembering this file exists.

``ALLOWED_SKIPS`` is deliberately not a switch. Each entry names one test and
one reason, and an entry earns its place only if the skip would *still* fire
with every toolchain installed. "It is currently red in CI" is not a reason.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, NoReturn

import pytest

#: How to obtain each toolchain. These strings land verbatim in the CI failure
#: message, which is the only place anyone will read them, so they name the
#: command rather than describing it.
CC_INSTALL = "apt-get install build-essential (Linux), xcode-select --install (macOS)"
CXX_INSTALL = CC_INSTALL
NIM_INSTALL = "https://nim-lang.org/install.html, or the Nim install step in .github/workflows/test.yml"
LIBCLANG_INSTALL = "apt-get install libclang-dev (Linux), brew install llvm (macOS), or `headerkit install-libclang`"
TREESITTER_INSTALL = "pip install 'headerkit[treesitter]'"
CYTHON_INSTALL = "pip install 'headerkit[test]'"

#: Keyed by backend name, for the fixtures that parametrise over both.
BACKEND_INSTALL = {"libclang": LIBCLANG_INSTALL, "tree-sitter": TREESITTER_INSTALL}


def under_ci() -> bool:
    """Whether the suite is running under a CI system.

    GitHub Actions sets ``CI=true``; so do GitLab, CircleCI, Travis and
    Buildkite. The falsy spellings are accepted so a developer can run the CI
    behaviour on and off locally with ``CI=1`` / ``CI=0`` rather than having to
    unset the variable.
    """
    return os.environ.get("CI", "").strip().lower() not in {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class AllowedSkip:
    """One skip that is legitimate even with every toolchain present.

    :param nodeid: ``fnmatch`` pattern against the test's node id. A pattern is
        needed rather than a literal because the skips that qualify are
        overwhelmingly parametrised, and pinning every generated id would go
        stale on the next corpus entry.
    :param reason: substring that must appear in the skip's reason. Matching the
        reason as well as the id is what stops the entry from silently widening:
        if the named test later skips because a *toolchain* is missing, the
        reason no longer matches and the gate fires.
    :param why: why this skip is legitimate. Prose, for the next reader.
    """

    nodeid: str
    reason: str
    why: str

    #: Minimum literal (non-wildcard) characters in ``nodeid``. A pattern built
    #: mostly of wildcards is not an entry for one test, it is a switch: both
    #: ``*`` and ``tests/*`` sail past a bare "is it non-empty" check while
    #: matching the entire suite.
    MIN_NODEID_LITERALS = 20
    #: Minimum words in ``reason``. The reason is half the match, and the empty
    #: string is a substring of every string, so a lax reason widens the entry
    #: to every skip the nodeid pattern reaches.
    MIN_REASON_WORDS = 3
    #: Minimum words in ``why``. Prose short enough to be a label is not a
    #: justification anybody can check.
    MIN_WHY_WORDS = 15

    def __post_init__(self) -> None:
        """Refuse a degenerate entry at import time, where it is loud.

        Every constraint here exists because its absence produced a working
        blanket exemption. ``AllowedSkip(nodeid="*", reason="")`` matched every
        skip in the suite and passed every check the class previously made.
        """
        literals = sum(1 for ch in self.nodeid if ch not in "*?[]")
        if literals < self.MIN_NODEID_LITERALS:
            raise ValueError(
                f"AllowedSkip nodeid {self.nodeid!r} has {literals} literal characters, "
                f"under the {self.MIN_NODEID_LITERALS} required. An entry names one test; "
                f"a pattern this wide is a blanket exemption, which defeats the gate."
            )
        if len(self.reason.split()) < self.MIN_REASON_WORDS:
            raise ValueError(
                f"AllowedSkip reason {self.reason!r} is too lax. The reason is half the "
                f"match and the empty string is a substring of every string, so a vague "
                f"one widens the entry to every skip its nodeid reaches."
            )
        if len(self.why.split()) < self.MIN_WHY_WORDS:
            raise ValueError(
                f"AllowedSkip for {self.nodeid!r} needs a justification, not a label. State "
                f"why this skip would still fire with every toolchain installed -- which is "
                f"the only admissible reason for an entry."
            )


#: Skips permitted under CI. Every entry is measured, not assumed: it fires on a
#: machine with libclang, tree-sitter, Cython, a C compiler, a C++ compiler and
#: Nim all installed.
ALLOWED_SKIPS: tuple[AllowedSkip, ...] = (
    AllowedSkip(
        nodeid="tests/test_regression_backend_parity.py::TestR13PaddingLayoutMatchesC::test_every_member_occupies_the_same_bits_as_in_c*",
        reason="record has no addressable member to locate",
        why=(
            "The padding corpus includes records built entirely from unnamed bit-fields. "
            "This test locates each named member's bits, so for those entries there is "
            "nothing to locate and no assertion to make; their layout is covered by the "
            "sizeof/alignment test in the same class, which does run. The skip is a "
            "property of the corpus entry, not of the machine."
        ),
    ),
    AllowedSkip(
        nodeid="tests/test_scaffold_runs.py::TestScaffoldedCtypesPackageRuns::test_an_implicit_enumerator_past_int_range_is_refused*",
        reason="parser and compiler disagree, so the writer cannot see the overflow",
        why=(
            "The test asserts that the ctypes writer refuses an enumerator past int range. On a "
            "host whose libclang reports that enumerator as in-range while its C compiler gives it "
            "0x80000000, the writer has nothing to refuse and the assertion has no subject -- "
            "observed on the Windows runner, with every toolchain present. The refusal behaviour is "
            "not thereby left unproven: the structural refusal case in the same class keys on the "
            "declaration rather than on any enumerator value, runs on every host including that one, "
            "and a sibling test asserts that it ran."
        ),
    ),
)


def is_allowed_skip(nodeid: str, reason: str) -> bool:
    """Whether ``nodeid`` skipping for ``reason`` is on the allowlist."""
    return any(fnmatch.fnmatch(nodeid, entry.nodeid) and entry.reason in reason for entry in ALLOWED_SKIPS)


class ToolchainUnavailable(pytest.fail.Exception):  # type: ignore[misc,name-defined]
    """Raised by :func:`missing_toolchain` under CI.

    A distinct type, because a bare ``pytest.fail`` cannot be told apart from a
    test's own failure, and the gate has to tell them apart: pytest's ``xfail``
    hook converts *any* exception raised by a non-strictly-xfailed test into an
    ``xfail``, and an ``xfail`` is not a skip. A missing toolchain inside such a
    test therefore vanished at exit 0 -- more quietly than the ``pytest.skip``
    it replaced, which the gate at least caught. ``NoSilentSkips`` recognises
    this type and takes the shelter back off.
    """


def missing_toolchain(problem: str, remedy: str) -> NoReturn:
    """Fail under CI, skip otherwise, because something the test needs is absent.

    :param problem: a complete clause naming what is missing, without trailing
        punctuation -- ``"nim is not on PATH"``, ``"the libclang backend is not
        available"``. It is the whole of the reason a reader will see, in both
        the CI failure and the local skip.
    :param remedy: what to do about it. This lands in the CI failure message,
        which is the only place anyone will read it.
    """
    if under_ci():
        raise ToolchainUnavailable(
            f"{problem}, so this test cannot run. Under CI a test that cannot run is a "
            f"failure, not a skip: a green run must mean the behaviour was checked. "
            f"Remedy: {remedy}",
            pytrace=False,
        )
    pytest.skip(f"{problem} (set CI=1 to make this a failure instead)")


def require_program(*programs: str, install: str) -> str:
    """Return the first of ``programs`` found on PATH.

    Under CI a miss is a failure naming the programs and how to install them.
    Locally it is a skip.
    """
    for candidate in programs:
        found = shutil.which(candidate)
        if found:
            return found
    problem = f"{programs[0]} is not on PATH" if len(programs) == 1 else f"none of {', '.join(programs)} is on PATH"
    missing_toolchain(problem, install)


def skip_reason(report: pytest.TestReport | pytest.CollectReport) -> str:
    """Best-effort reason text for a skipped report.

    ``longrepr`` for a skip is a ``(path, lineno, message)`` triple whose message
    is prefixed ``Skipped: ``. The fallback is deliberate: a reason that cannot be
    recovered must still reach the gate, because an unexplained skip is worse than
    an explained one, not better.
    """
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2]).removeprefix("Skipped: ")
    return str(longrepr)


class NoSilentSkips:
    """Session gate: under CI, an unsanctioned skip turns the run red.

    Registered by ``tests/conftest.py``. State lives on the instance rather than
    in a module global so that a nested pytest run -- which is how this gate is
    itself tested -- cannot leak its skips into the outer session's verdict.

    Three hooks, because a skip has three ways of reaching a run and only one of
    them is the obvious one.

    ``pytest_runtest_makereport``
        Where a per-test skip is converted into a real failed report. Doing it
        here rather than only tallying at session end is what keeps the run's
        LAST LINE honest: a gate that flips the exit status while the summary
        still reads ``97 passed, 1 skipped`` reproduces, one layer up, exactly
        the mismatch it exists to remove. It is also where a missing toolchain
        sheltered by a non-strict ``xfail`` is un-sheltered.
    ``pytest_collectreport``
        A module-scope skip -- ``pytest.importorskip`` at import time, a
        ``pytest.skip(allow_module_level=True)`` -- never produces a
        ``TestReport`` at all. It removes a whole file, and with it potentially a
        whole backend axis, and the run reports it as one tidy ``s``.
    ``pytest_sessionfinish``
        The consolidated inventory, and the verdict for collection-phase skips,
        which have no report to convert.
    """

    def __init__(self) -> None:
        #: ``nodeid -> reason`` for every skip seen this session.
        self.skips: dict[str, str] = {}
        #: ``nodeid -> reason`` for skips no ``ALLOWED_SKIPS`` entry sanctions.
        self.unlisted: dict[str, str] = {}
        #: The subset of those that arrived from COLLECTION. They have no
        #: ``TestReport`` to convert into a failure, so they are the ones that
        #: need a synthetic one to reach the summary line.
        self.unlisted_at_collection: dict[str, str] = {}
        #: Entries that matched at least one skip, by ``nodeid`` pattern.
        self.matched_entries: set[str] = set()

    def _record(self, nodeid: str, reason: str) -> bool:
        """Record one skip. Returns whether it is unsanctioned."""
        self.skips.setdefault(nodeid, reason)
        for entry in ALLOWED_SKIPS:
            if fnmatch.fnmatch(nodeid, entry.nodeid) and entry.reason in reason:
                self.matched_entries.add(entry.nodeid)
                return False
        self.unlisted.setdefault(nodeid, reason)
        return True

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, call: pytest.CallInfo[None]
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        """Un-shelter a missing toolchain, then turn an unsanctioned skip red."""
        report = yield

        # pytest's xfail hook turns ANY exception from a non-strictly-xfailed
        # test into `wasxfail`, which this gate exempts -- so a missing toolchain
        # inside such a test disappeared at exit 0. Only our own exception type
        # is taken back; a genuine expected failure keeps its xfail.
        if isinstance(call.excinfo.value if call.excinfo else None, ToolchainUnavailable) and hasattr(
            report, "wasxfail"
        ):
            del report.wasxfail
            report.outcome = "failed"
            report.longrepr = str(call.excinfo.value)  # type: ignore[union-attr]
            return report

        if not report.skipped or hasattr(report, "wasxfail"):
            return report

        if self._record(report.nodeid, skip_reason(report)) and under_ci():
            report.outcome = "failed"
            report.longrepr = (
                f"{self.skips[report.nodeid]}\n\n"
                f"This test did not run, and under CI a test that did not run has not been "
                f"proven to pass. Install what it needs, or -- only if this skip would still "
                f"fire with every toolchain installed -- add an entry for it to ALLOWED_SKIPS "
                f"in tests/skip_policy.py."
            )
        return report

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """Record a skip that removed a whole module before any test existed."""
        if report.skipped:
            nodeid = report.nodeid or "<session root>"
            reason = skip_reason(report)
            if self._record(nodeid, reason) and under_ci():
                self.unlisted_at_collection[nodeid] = reason

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        """Set the exit status. Every skip this gate rejects makes the run red.

        A per-test skip is already a failed report by this point, so pytest's own
        status covers it. A COLLECTION skip has no report to convert, and would
        otherwise leave the run green, so the status is set explicitly.
        """
        if under_ci() and self.unlisted:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        """Print the inventory, and make the SUMMARY LINE agree with the verdict.

        A gate that flips the exit status while the last line still reads
        ``97 passed, 1 skipped`` reproduces, one layer up, the exact mismatch it
        exists to remove -- and the last line is what someone scanning a log tail
        reads. Per-test skips are already counted, having been turned into real
        failures; collection-phase skips get a synthetic failed report here,
        which is the only way they reach that line.
        """
        if not under_ci() or not self.skips:
            return

        for nodeid, reason in self.unlisted_at_collection.items():
            terminalreporter.stats.setdefault("failed", []).append(
                pytest.TestReport(
                    nodeid=nodeid,
                    location=(nodeid, None, nodeid),
                    keywords={},
                    outcome="failed",
                    longrepr=f"{nodeid} was skipped during collection: {reason}",
                    when="call",
                )
            )

        allowed = len(self.skips) - len(self.unlisted)
        writer = terminalreporter._tw

        if self.unlisted:
            writer.line("")
            writer.sep("=", "SKIPPED UNDER CI -- TREATED AS FAILURES", red=True, bold=True)
            writer.line(
                f"{len(self.unlisted)} test(s) or module(s) did not run. A test that cannot run has "
                "not been proven to pass, so this run is red. Install the missing toolchain, or -- "
                "only if the skip would still fire with every toolchain installed -- add an entry to "
                "ALLOWED_SKIPS in tests/skip_policy.py stating the reason it is legitimate."
            )
            for nodeid, reason in sorted(self.unlisted.items()):
                writer.line(f"  {nodeid}: {reason}")
            if allowed:
                writer.line(f"({allowed} further skip(s) matched ALLOWED_SKIPS and are permitted.)")
        else:
            writer.line(f"\nno-silent-skips: {allowed} skip(s), all matched by ALLOWED_SKIPS.")

        # An entry whose skip has stopped firing is not harmless: it stays,
        # unexamined, and its pattern shelters the next skip that happens to
        # match. Reported rather than failed, because an entry can be legitimately
        # platform-specific and silent on the leg currently running.
        stale = [e for e in ALLOWED_SKIPS if e.nodeid not in self.matched_entries]
        if stale:
            writer.line(f"no-silent-skips: {len(stale)} ALLOWED_SKIPS entry/entries matched nothing this run:")
            for entry in stale:
                writer.line(f"  {entry.nodeid} ({entry.reason})")
            writer.line("  Platform-specific entries are expected to be silent on other legs. An entry")
            writer.line("  silent on EVERY leg no longer describes a real skip and should be deleted.")
