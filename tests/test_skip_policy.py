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
    is_allowed_skip,
    missing_toolchain,
    require_program,
    skip_reason,
    under_ci,
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
        found = require_program("definitely-not-a-real-binary-xyz", "python3", install="irrelevant")
        assert found.endswith(("python3", "python3.exe"))

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
            pytest.skip("arbitrary")

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
        result.stdout.fnmatch_lines(["*SKIPPED UNDER CI*", "*test_planted_bare_skip: arbitrary*"])

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
            reason="arbitrary",
            why="planted by the suite that proves the allowlist is consulted at all",
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
            reason="a completely different reason",
            why="planted to prove the reason is matched and not merely the nodeid",
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
        report = pytest.TestReport(
            nodeid="t::x",
            location=("t.py", 1, "x"),
            keywords={},
            outcome="skipped",
            longrepr=("t.py", 1, "Skipped: arbitrary"),
            when="call",
        )

        first.pytest_runtest_logreport(report)

        assert first.skips == {"t::x": "arbitrary"}
        assert second.skips == {}
