"""The gate that makes a skipped test red under CI has to be watched failing.

Every test here plants a skip and asserts on the *exit status of the session
that saw it*, because exit status is the only thing CI reads. Asserting that the
hook recorded something would prove the bookkeeping and nothing about the
verdict -- and a verdict that never moves is exactly the defect this gate exists
to remove.

The nested runs go through ``pytester``, which builds a real pytest session in a
temporary directory with its own plugin manager. The outer session's own gate is
a separate ``NoSilentSkips`` instance and does not see the inner run's skips;
that isolation is asserted directly in ``test_nested_runs_do_not_share_state``,
because a module-global would have made every test in this file poison the suite
that runs it.
"""

from __future__ import annotations

import textwrap

import pytest

from tests.skip_policy import (
    ALLOWED_SKIPS,
    AllowedSkip,
    NoSilentSkips,
    ToolchainUnavailable,
    is_allowed_skip,
    missing_toolchain,
    require_program,
    skip_reason,
    under_ci,
)

#: A non-degenerate stand-in entry for the pytester-planted test. Every field is
#: wide enough to survive ``AllowedSkip.__post_init__``, which is itself under
#: test below.
PLANTED_WHY = (
    "Planted by the suite that proves the allowlist is consulted at all, and shaped to "
    "satisfy every constraint the dataclass enforces on a real entry."
)

pytest_plugins = ["pytester"]

#: Installed into every nested session so the gate under test is armed there.
GATE_CONFTEST = textwrap.dedent("""\
    from tests.skip_policy import NoSilentSkips

    def pytest_configure(config):
        config.pluginmanager.register(NoSilentSkips(), "no-silent-skips")
""")


def _run(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    *,
    ci: str | None,
) -> pytest.RunResult:
    """Run ``body`` as a nested pytest session with the gate armed."""
    pytester.makeconftest(GATE_CONFTEST)
    pytester.makepyfile(test_planted=textwrap.dedent(body))
    if ci is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", ci)
    return pytester.runpytest_inprocess()


class TestUnderCI:
    """``CI`` decides whether a missing toolchain is a skip or a failure."""

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "anything"])
    def test_truthy_spellings(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("CI", value)
        assert under_ci()

    @pytest.mark.parametrize("value", ["", "0", "false", "FALSE", "no", "off", "  "])
    def test_falsy_spellings(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("CI", value)
        assert not under_ci()

    def test_unset_is_not_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        assert not under_ci()


class TestMissingToolchain:
    """The point-of-use half: fail under CI, skip locally, name the tool either way."""

    def test_fails_under_ci_naming_the_tool_and_the_remedy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        with pytest.raises(pytest.fail.Exception, match="nim is not on PATH") as excinfo:
            missing_toolchain("nim is not on PATH", "brew install nim")
        message = str(excinfo.value)
        assert "brew install nim" in message, "the remedy is the only actionable part of the message"

    def test_skips_locally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        with pytest.raises(pytest.skip.Exception, match="nim is not on PATH"):
            missing_toolchain("nim is not on PATH", "brew install nim")

    def test_local_skip_says_how_to_get_the_ci_behaviour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        with pytest.raises(pytest.skip.Exception, match=r"set CI=1"):
            missing_toolchain("nim is not on PATH", "brew install nim")


class TestRequireProgram:
    """``require_program`` resolves a real path, or routes the miss through the policy."""

    def test_returns_the_first_program_found(self) -> None:
        """Windows resolves this to ``python3.EXE``, so the suffix is matched case-insensitively."""
        found = require_program("definitely-not-a-real-binary-xyz", "python3", install="irrelevant")
        assert found.lower().endswith(("python3", "python3.exe"))

    def test_single_missing_program_reads_naturally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        with pytest.raises(pytest.fail.Exception, match=r"^nope-xyz is not on PATH,") as excinfo:
            require_program("nope-xyz", install="brew install nope")
        assert "none of" not in str(excinfo.value)

    def test_several_missing_programs_are_all_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        with pytest.raises(pytest.fail.Exception, match=r"none of nope-a, nope-b, nope-c is on PATH"):
            require_program("nope-a", "nope-b", "nope-c", install="brew install nope")


class TestAllowlist:
    """An entry sanctions one test skipping for one reason, and nothing wider."""

    def test_the_shipped_entry_matches_the_skip_it_was_written_for(self) -> None:
        entry = ALLOWED_SKIPS[0]
        nodeid = entry.nodeid.replace("*", "[c2_anon-libclang]")
        assert is_allowed_skip(nodeid, f"Skipped: {entry.reason}".removeprefix("Skipped: "))

    def test_a_matching_nodeid_with_a_different_reason_is_rejected(self) -> None:
        entry = ALLOWED_SKIPS[0]
        nodeid = entry.nodeid.replace("*", "[c2_anon-libclang]")
        assert not is_allowed_skip(nodeid, "nim is not on PATH")

    def test_a_matching_reason_on_a_different_test_is_rejected(self) -> None:
        assert not is_allowed_skip("tests/test_other.py::test_thing", ALLOWED_SKIPS[0].reason)

    def test_an_unrelated_skip_is_rejected(self) -> None:
        assert not is_allowed_skip("tests/test_other.py::test_thing", "arbitrary")

    def test_every_shipped_entry_carries_a_justification(self) -> None:
        """An entry with no stated reason is an escape hatch wearing a disguise."""
        for entry in ALLOWED_SKIPS:
            assert len(entry.why.split()) >= 15, f"{entry.nodeid} needs a real justification, not a label"


class TestSkipReason:
    """The reason text has to survive the trip out of a report."""

    def test_strips_the_skipped_prefix(self) -> None:
        report = pytest.TestReport(
            nodeid="t::x",
            location=("t.py", 1, "x"),
            keywords={},
            outcome="skipped",
            longrepr=("t.py", 1, "Skipped: nim is not on PATH"),
            when="call",
        )
        assert skip_reason(report) == "nim is not on PATH"

    def test_an_unrecognised_longrepr_still_yields_something(self) -> None:
        """An unexplained skip must reach the gate, not fall out of it."""
        report = pytest.TestReport(
            nodeid="t::x",
            location=("t.py", 1, "x"),
            keywords={},
            outcome="skipped",
            longrepr="something unexpected",
            when="call",
        )
        assert skip_reason(report) == "something unexpected"


class TestSessionGate:
    """The verdict, end to end: what the exit status does when a test does not run."""

    BARE_SKIP = """
        import pytest

        def test_planted_bare_skip():
            pytest.skip("planted skip for the gate's own test")

        def test_that_passes():
            assert True
    """

    MARKER_SKIP = """
        import pytest

        @pytest.mark.skipif(True, reason="arbitrary marker skip")
        def test_planted_marker_skip():
            raise AssertionError("never runs")
    """

    def test_a_bare_skip_turns_the_session_red_under_ci(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(pytester, monkeypatch, self.BARE_SKIP, ci="true")

        assert result.ret == pytest.ExitCode.TESTS_FAILED
        result.stdout.fnmatch_lines(["*SKIPPED UNDER CI*", "*test_planted_bare_skip: planted skip*"])

    def test_a_skipif_marker_is_caught_too(self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """``skipif`` never reaches ``missing_toolchain``, which is why the gate exists."""
        result = _run(pytester, monkeypatch, self.MARKER_SKIP, ci="true")

        assert result.ret == pytest.ExitCode.TESTS_FAILED
        result.stdout.fnmatch_lines(["*test_planted_marker_skip: arbitrary marker skip*"])

    def test_the_same_skip_is_green_off_ci(self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """A contributor without the toolchain is not blocked."""
        result = _run(pytester, monkeypatch, self.BARE_SKIP, ci=None)

        assert result.ret == pytest.ExitCode.OK
        result.stdout.no_fnmatch_line("*SKIPPED UNDER CI*")

    def test_a_run_with_no_skips_is_untouched_under_ci(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(pytester, monkeypatch, "def test_passes():\n    assert True\n", ci="true")

        assert result.ret == pytest.ExitCode.OK
        result.stdout.no_fnmatch_line("*no-silent-skips*")

    def test_an_allowlisted_skip_does_not_fail_the_session(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proved by pointing the allowlist at the planted test, then running it."""
        entry = AllowedSkip(
            nodeid="test_planted.py::test_planted_bare_skip",
            reason="planted skip for the gate's own test",
            why=PLANTED_WHY,
        )
        monkeypatch.setattr("tests.skip_policy.ALLOWED_SKIPS", (entry,))

        result = _run(pytester, monkeypatch, self.BARE_SKIP, ci="true")

        assert result.ret == pytest.ExitCode.OK
        result.stdout.fnmatch_lines(["*no-silent-skips: 1 skip(s), all matched by ALLOWED_SKIPS*"])

    def test_an_allowlist_entry_for_a_different_reason_does_not_shelter_the_skip(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The negative control for the test above: same nodeid, wrong reason, still red."""
        entry = AllowedSkip(
            nodeid="test_planted.py::test_planted_bare_skip",
            reason="a completely different reason entirely",
            why=PLANTED_WHY,
        )
        monkeypatch.setattr("tests.skip_policy.ALLOWED_SKIPS", (entry,))

        result = _run(pytester, monkeypatch, self.BARE_SKIP, ci="true")

        assert result.ret == pytest.ExitCode.TESTS_FAILED

    def test_an_xfail_is_not_a_skip(self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """``xfail`` reports ``skipped`` internally; treating it as one would be wrong."""
        body = """
            import pytest

            @pytest.mark.xfail(reason="a known divergence, recorded on purpose")
            def test_known_divergence():
                raise AssertionError("expected")
        """
        result = _run(pytester, monkeypatch, body, ci="true")

        assert result.ret == pytest.ExitCode.OK

    def test_nested_runs_do_not_share_state(self) -> None:
        """Two gates are two ledgers. A module global would have made this false."""
        first, second = NoSilentSkips(), NoSilentSkips()

        assert first._record("t::x", "arbitrary") is True

        assert first.skips == {"t::x": "arbitrary"}
        assert first.unlisted == {"t::x": "arbitrary"}
        assert second.skips == {}
        assert second.unlisted == {}


class TestDegenerateAllowlistEntries:
    """The allowlist has to cost more than the fix it competes with.

    Every constraint here was written against a construction that worked.
    ``AllowedSkip(nodeid="*", reason="")`` matched every skip in the suite and
    passed every check the class made at the time, so for a developer facing a
    red skip, adding an entry was strictly cheaper than fixing it and nothing
    made the cheap path visibly wrong.
    """

    WHY = (
        "Filler justification long enough to satisfy the word-count constraint so that "
        "the other constraint under test is the one that decides this case."
    )

    def test_a_bare_wildcard_nodeid_is_refused(self) -> None:
        with pytest.raises(ValueError, match="blanket exemption"):
            AllowedSkip(nodeid="*", reason="a plausible looking reason", why=self.WHY)

    def test_a_directory_wide_nodeid_is_refused(self) -> None:
        """``tests/*`` is the same switch wearing a path."""
        with pytest.raises(ValueError, match="literal characters"):
            AllowedSkip(nodeid="tests/*", reason="a plausible looking reason", why=self.WHY)

    def test_an_empty_reason_is_refused(self) -> None:
        """The empty string is a substring of every string, so it matches everything."""
        with pytest.raises(ValueError, match="too lax"):
            AllowedSkip(nodeid="tests/test_something_real.py::test_case", reason="", why=self.WHY)

    def test_a_one_word_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="too lax"):
            AllowedSkip(nodeid="tests/test_something_real.py::test_case", reason="unavailable", why=self.WHY)

    def test_a_label_instead_of_a_justification_is_refused(self) -> None:
        with pytest.raises(ValueError, match="justification, not a label"):
            AllowedSkip(nodeid="tests/test_something_real.py::test_case", reason="a real reason here", why="platform")

    def test_a_well_formed_entry_is_accepted(self) -> None:
        """The negative control: the constraints must not refuse a real entry."""
        entry = AllowedSkip(nodeid="tests/test_something_real.py::test_case", reason="a real reason here", why=self.WHY)

        assert entry.nodeid.endswith("::test_case")

    def test_every_shipped_entry_survives_its_own_constraints(self) -> None:
        for entry in ALLOWED_SKIPS:
            AllowedSkip(nodeid=entry.nodeid, reason=entry.reason, why=entry.why)


class TestXfailCannotShelterAMissingToolchain:
    """pytest turns any exception from a non-strictly-xfailed test into an xfail.

    An xfail is not a skip, so the gate exempts it -- and a missing toolchain
    inside such a test therefore vanished at exit 0, *more quietly than the
    ``pytest.skip`` it replaced*, which the gate did catch. Measured on the real
    gate before the fix: ``pytest.skip`` gave exit 1 with the banner,
    ``missing_toolchain`` gave ``1 xfailed`` and exit 0.
    """

    SHELTERED = """
        import pytest
        from tests.skip_policy import missing_toolchain

        @pytest.mark.xfail(reason="non-strict, exactly as on TestCPython::test_parse")
        def test_toolchain_missing_under_a_non_strict_xfail():
            missing_toolchain("the CPython header could not be downloaded", "check the network")
    """

    GENUINE = """
        import pytest

        @pytest.mark.xfail(reason="a real expected failure")
        def test_genuine_expected_failure():
            raise AssertionError("expected")
    """

    def test_the_toolchain_failure_is_not_sheltered(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(pytester, monkeypatch, self.SHELTERED, ci="true")

        assert result.ret == pytest.ExitCode.TESTS_FAILED
        result.stdout.fnmatch_lines(["*CPython header could not be downloaded*"])
        result.stdout.no_fnmatch_line("*1 xfailed*")

    def test_a_genuine_expected_failure_keeps_its_xfail(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The negative control. Un-sheltering must not break ordinary xfail."""
        result = _run(pytester, monkeypatch, self.GENUINE, ci="true")

        assert result.ret == pytest.ExitCode.OK
        result.stdout.fnmatch_lines(["*1 xfailed*"])

    def test_off_ci_it_is_still_a_skip_and_still_xfails(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(pytester, monkeypatch, self.SHELTERED, ci=None)

        assert result.ret == pytest.ExitCode.OK

    def test_the_marker_type_is_outside_the_exception_hierarchy(self) -> None:
        """Which is *why* ``raises=Exception`` at a call site also excludes it."""
        assert issubclass(ToolchainUnavailable, BaseException)
        assert not issubclass(ToolchainUnavailable, Exception)


class TestCollectionPhaseSkips:
    """A module-scope skip never produces a ``TestReport`` at all.

    It removes a whole file -- potentially a whole backend axis -- and the run
    reports it as one tidy ``s``. The suite carries roughly twenty
    ``pytest.importorskip`` calls that sit inside functions today; one refactor
    hoisting any of them to module scope is all it takes.
    """

    MODULE_SKIP = """
        import pytest
        pytest.importorskip("a_module_that_certainly_does_not_exist_xyz")

        def test_never_runs():
            raise AssertionError("unreachable")
    """

    def test_a_module_scope_skip_fails_the_run(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(pytester, monkeypatch, self.MODULE_SKIP, ci="true")

        assert result.ret == pytest.ExitCode.TESTS_FAILED
        result.stdout.fnmatch_lines(["*SKIPPED UNDER CI*", "*test_planted.py*"])

    def test_it_reaches_the_summary_line_too(self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exit status and the last line must agree; the last line is what gets read."""
        result = _run(pytester, monkeypatch, self.MODULE_SKIP, ci="true")

        result.stdout.fnmatch_lines(["*1 failed*"])

    def test_off_ci_a_module_scope_skip_is_still_a_skip(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NO_TESTS_COLLECTED, not TESTS_FAILED: pytest's own verdict when the one
        module in the run skips, and the gate leaves it alone."""
        result = _run(pytester, monkeypatch, self.MODULE_SKIP, ci=None)

        assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
        result.stdout.fnmatch_lines(["*1 skipped*"])
        result.stdout.no_fnmatch_line("*SKIPPED UNDER CI*")


class TestTheSummaryLineAgreesWithTheVerdict:
    """A red run whose last line reads green is this gate's own defect, inverted."""

    BODY = """
        import pytest

        def test_planted_bare_skip():
            pytest.skip("planted skip for the gate's own test")

        def test_that_passes():
            assert True
    """

    def test_an_unsanctioned_skip_is_counted_as_a_failure(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(pytester, monkeypatch, self.BODY, ci="true")

        assert result.ret == pytest.ExitCode.TESTS_FAILED
        result.stdout.fnmatch_lines(["*1 failed, 1 passed*"])
        result.stdout.no_fnmatch_line("*1 passed, 1 skipped*")


class TestStaleAllowlistEntries:
    """An entry whose skip stops firing shelters the next skip that matches it."""

    def test_an_entry_that_matched_nothing_is_reported(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = AllowedSkip(
            nodeid="tests/test_a_file_that_is_not_in_this_run.py::test_case",
            reason="a reason that never fires",
            why=PLANTED_WHY,
        )
        monkeypatch.setattr("tests.skip_policy.ALLOWED_SKIPS", (entry,))

        result = _run(pytester, monkeypatch, TestTheSummaryLineAgreesWithTheVerdict.BODY, ci="true")

        result.stdout.fnmatch_lines(["*matched nothing this run*", "*test_a_file_that_is_not_in_this_run*"])

    def test_an_entry_that_fired_is_not_reported_as_stale(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = AllowedSkip(
            nodeid="test_planted.py::test_planted_bare_skip",
            reason="planted skip for the gate's own test",
            why=PLANTED_WHY,
        )
        monkeypatch.setattr("tests.skip_policy.ALLOWED_SKIPS", (entry,))

        result = _run(pytester, monkeypatch, TestTheSummaryLineAgreesWithTheVerdict.BODY, ci="true")

        assert result.ret == pytest.ExitCode.OK
        result.stdout.no_fnmatch_line("*matched nothing this run*")
