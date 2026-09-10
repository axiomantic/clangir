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
from dataclasses import dataclass
from typing import NoReturn

import pytest

#: How to obtain each toolchain. These strings land verbatim in the CI failure
#: message, which is the only place anyone will read them, so they name the
#: command rather than describing it.
CC_INSTALL = "apt-get install build-essential (Linux), xcode-select --install (macOS)"
CXX_INSTALL = CC_INSTALL
NIM_INSTALL = "https://nim-lang.org/install.html, or the setup-nim step in .github/workflows/test.yml"
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
)


def is_allowed_skip(nodeid: str, reason: str) -> bool:
    """Whether ``nodeid`` skipping for ``reason`` is on the allowlist."""
    return any(fnmatch.fnmatch(nodeid, entry.nodeid) and entry.reason in reason for entry in ALLOWED_SKIPS)


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
        pytest.fail(
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


def skip_reason(report: pytest.TestReport) -> str:
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
    """

    def __init__(self) -> None:
        #: ``nodeid -> reason`` for every skip seen this session.
        self.skips: dict[str, str] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Record every skip. ``xfail`` is a different outcome and is not one."""
        if report.skipped and not hasattr(report, "wasxfail"):
            self.skips.setdefault(report.nodeid, skip_reason(report))

    def unlisted(self) -> dict[str, str]:
        """The recorded skips that ``ALLOWED_SKIPS`` does not sanction."""
        return {nodeid: reason for nodeid, reason in self.skips.items() if not is_allowed_skip(nodeid, reason)}

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        """Fail the session under CI if anything skipped for an unlisted reason.

        This is the half of the policy that covers skip sites the helpers above do
        not own: a bare ``pytest.skip``, a ``skipif`` marker, a fixture that bails
        on an unavailable backend. A skip is invisible in a default pytest run and
        identical in colour to a pass, so nothing else here would notice.
        """
        if not under_ci() or not self.skips:
            return

        unlisted = self.unlisted()
        allowed = len(self.skips) - len(unlisted)
        writer = session.config.get_terminal_writer()

        if not unlisted:
            writer.line(f"\nno-silent-skips: {allowed} skip(s), all matched by ALLOWED_SKIPS.")
            return

        writer.line("")
        writer.sep("=", "SKIPPED UNDER CI -- TREATED AS FAILURES", red=True, bold=True)
        writer.line(
            f"{len(unlisted)} test(s) did not run. A test that cannot run has not been proven to "
            "pass, so this run is red. Install the missing toolchain, or -- only if the skip "
            "would still fire with every toolchain installed -- add an entry to ALLOWED_SKIPS "
            "in tests/skip_policy.py stating the reason it is legitimate."
        )
        for nodeid, reason in sorted(unlisted.items()):
            writer.line(f"  {nodeid}: {reason}")
        if allowed:
            writer.line(f"({allowed} further skip(s) matched ALLOWED_SKIPS and are permitted.)")
        writer.line(f"ALLOWED_SKIPS currently holds {len(ALLOWED_SKIPS)} entry/entries.")

        session.exitstatus = pytest.ExitCode.TESTS_FAILED
