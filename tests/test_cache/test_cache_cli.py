"""Integration tests for cache-check and cache-save CLI subcommands."""

from __future__ import annotations

import datetime
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from headerkit._cache_cli import cache_check_main, cache_save_main
from headerkit.cache import compute_hash

_FAKE_NOW = datetime.datetime(2026, 3, 23, 14, 30, 0, tzinfo=datetime.timezone.utc)
_FAKE_VERSION = "0.8.4"


class TestParseWriterOptions:
    """Tests for _parse_writer_options internal helper."""

    def test_parses_single_key_value(self) -> None:
        """Single KEY=VALUE pair is parsed correctly."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["exclude=__.*"])
        assert result == {"exclude": "__.*"}

    def test_parses_multiple_key_values(self) -> None:
        """Multiple KEY=VALUE pairs are parsed correctly."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["exclude=__.*", "prefix=my_"])
        assert result == {"exclude": "__.*", "prefix": "my_"}

    def test_splits_on_first_equals(self) -> None:
        """VALUE containing = is preserved (split on first = only)."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["regex=a=b=c"])
        assert result == {"regex": "a=b=c"}

    def test_none_input_returns_none(self) -> None:
        """None input returns None."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(None)
        assert result is None

    def test_empty_list_returns_none(self) -> None:
        """Empty list returns None."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options([])
        assert result is None

    def test_malformed_option_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Malformed option (no =) is skipped with stderr warning."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["noequalssign"])
        assert result is None
        captured = capsys.readouterr()
        assert captured.err == "headerkit cache: malformed --writer-option: 'noequalssign'; expected KEY=VALUE\n"


class TestCacheSaveCli:
    """Tests for the cache-save subcommand."""

    def test_saves_hash_sidecar_exits_0(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save without --writer creates sidecar and exits 0."""
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output}.hkcache (sidecar)\n"

    def test_saves_hash_embedded_with_writer_flag(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with --writer cffi uses embedded storage."""
        with (
            patch("headerkit.cache.datetime") as mock_dt,
            patch("headerkit.cache.importlib.metadata.version", return_value=_FAKE_VERSION),
        ):
            mock_dt.datetime.now.return_value = _FAKE_NOW
            mock_dt.timezone = datetime.timezone
            exit_code = cache_save_main(
                [
                    str(sample_output),
                    "--header",
                    str(sample_header),
                    "--writer-name",
                    "cffi",
                    "--writer",
                    "cffi",
                ]
            )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output} (embedded)\n"

        # Verify exact embedded content
        with patch("headerkit.cache.importlib.metadata.version", return_value=_FAKE_VERSION):
            expected_hash = compute_hash(
                header_paths=[sample_header],
                writer_name="cffi",
            )
        content = sample_output.read_text(encoding="utf-8")
        expected = textwrap.dedent(f"""\
            # [headerkit-cache]
            # hash = "{expected_hash}"
            # version = "{_FAKE_VERSION}"
            # writer = "cffi"
            # generated = "2026-03-23T14:30:00+00:00"

            # generated bindings
        """)
        assert content == expected

    def test_save_missing_output_exits_1(
        self, sample_header: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with nonexistent output file exits 1."""
        missing = tmp_path / "missing.py"
        exit_code = cache_save_main(
            [
                str(missing),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache-save: Output not found: {missing}\n"

    def test_save_with_writer_options(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with --writer-option passes options to hash computation."""
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output}.hkcache (sidecar)\n"

    def test_save_with_extra_input(
        self,
        sample_header: Path,
        sample_output: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cache-save with --extra-input includes extra file in hash."""
        extra = tmp_path / "config.ini"
        extra.write_text("key=value\n", encoding="utf-8")
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--extra-input",
                str(extra),
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output}.hkcache (sidecar)\n"

    def test_save_unknown_writer_exits_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with --writer pointing to unknown writer exits 1."""
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer",
                "nonexistent_writer",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == (
            "headerkit cache-save: Unknown writer: 'nonexistent_writer'."
            " Available: cffi, ctypes, cython, diff, json, lua, prompt\n"
        )


class TestCacheCheckCli:
    """Tests for the cache-check subcommand."""

    def test_check_after_save_returns_0(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save followed by cache-check returns exit 0 with up-to-date message."""
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        capsys.readouterr()  # Clear save output

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"up-to-date: {sample_output}\n"

    def test_check_stale_returns_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-check on file with no hash returns exit 1 with stale message."""
        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {sample_output} (reason: no stored hash)\n"

    def test_check_missing_output_returns_1(
        self, sample_header: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-check on missing output returns exit 1 with missing reason."""
        missing = tmp_path / "missing.py"
        exit_code = cache_check_main(
            [
                str(missing),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {missing} (reason: missing output)\n"

    def test_check_hash_mismatch_returns_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-check returns exit 1 when hash mismatches due to changed header."""
        # Save hash first
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        capsys.readouterr()

        # Modify header content
        sample_header.write_text("int different_function(void);\n", encoding="utf-8")

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {sample_output} (reason: hash mismatch)\n"

    def test_writer_option_round_trip(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--writer-option round-trips: save then check with same options returns 0."""
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        capsys.readouterr()

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"up-to-date: {sample_output}\n"

    def test_writer_option_mismatch_returns_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Differing --writer-option between save and check produces stale."""
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        capsys.readouterr()

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=foo",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {sample_output} (reason: hash mismatch)\n"


class TestCliDispatch:
    """Tests for early dispatch in _cli.main."""

    def test_cache_check_dispatches(self) -> None:
        """'headerkit cache-check' dispatches to cache_check_main."""
        with patch("headerkit._cache_cli.cache_check_main", return_value=0) as mock_check:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache-check", "dummy.py", "--header", "dummy.h", "--writer-name", "cffi"],
            ):
                from headerkit._cli import main

                main()
            mock_check.assert_called_once_with(["dummy.py", "--header", "dummy.h", "--writer-name", "cffi"])

    def test_cache_save_dispatches(self) -> None:
        """'headerkit cache-save' dispatches to cache_save_main."""
        with patch("headerkit._cache_cli.cache_save_main", return_value=0) as mock_save:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache-save", "dummy.py", "--header", "dummy.h", "--writer-name", "cffi"],
            ):
                from headerkit._cli import main

                main()
            mock_save.assert_called_once_with(["dummy.py", "--header", "dummy.h", "--writer-name", "cffi"])
