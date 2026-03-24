"""Tests for headerkit.cache.is_up_to_date."""

from __future__ import annotations

import textwrap
from pathlib import Path

from headerkit.cache import _sidecar_path, compute_hash, is_up_to_date


class TestIsUpToDateEmbedded:
    """Tests for is_up_to_date with embedded hash."""

    def test_returns_true_when_hash_matches(self, sample_header: Path, sample_output: Path) -> None:
        """Returns True when stored hash matches recomputed hash."""
        digest = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        original = sample_output.read_text(encoding="utf-8")
        sample_output.write_text(
            textwrap.dedent(f"""\
                # [headerkit-cache]
                # hash = "{digest}"
                # version = "0.8.4"
                # writer = "cffi"
                # generated = "2026-03-23T14:30:00Z"

            """)
            + original,
            encoding="utf-8",
        )
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is True
        )

    def test_returns_false_when_header_changed(self, sample_header: Path, sample_output: Path) -> None:
        """Returns False when header content changed since hash was saved."""
        digest = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        original = sample_output.read_text(encoding="utf-8")
        sample_output.write_text(
            textwrap.dedent(f"""\
                # [headerkit-cache]
                # hash = "{digest}"
                # version = "0.8.4"
                # writer = "cffi"
                # generated = "2026-03-23T14:30:00Z"

            """)
            + original,
            encoding="utf-8",
        )
        # Modify the header after saving the hash
        sample_header.write_text("int different(void);\n", encoding="utf-8")
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )


class TestIsUpToDateSidecar:
    """Tests for is_up_to_date with sidecar hash."""

    def test_returns_true_when_sidecar_matches(self, sample_header: Path, sample_output: Path) -> None:
        """Returns True when sidecar hash matches."""
        digest = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        sidecar = _sidecar_path(sample_output)
        sidecar.write_text(
            textwrap.dedent(f"""\
                [headerkit-cache]
                hash = "{digest}"
                version = "0.8.4"
                writer = "cffi"
                generated = "2026-03-23T14:30:00Z"
            """),
            encoding="utf-8",
        )
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is True
        )

    def test_returns_false_when_sidecar_stale(self, sample_header: Path, sample_output: Path) -> None:
        """Returns False when sidecar hash is stale."""
        digest = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        sidecar = _sidecar_path(sample_output)
        sidecar.write_text(
            textwrap.dedent(f"""\
                [headerkit-cache]
                hash = "{digest}"
                version = "0.8.4"
                writer = "cffi"
                generated = "2026-03-23T14:30:00Z"
            """),
            encoding="utf-8",
        )
        sample_header.write_text("int changed(void);\n", encoding="utf-8")
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )


class TestIsUpToDateEdgeCases:
    """Edge cases for is_up_to_date."""

    def test_returns_false_for_missing_output(self, sample_header: Path, tmp_path: Path) -> None:
        """Returns False when output file does not exist."""
        missing = tmp_path / "nonexistent.py"
        assert (
            is_up_to_date(
                output_path=missing,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )

    def test_returns_false_for_no_stored_hash(self, sample_header: Path, sample_output: Path) -> None:
        """Returns False when output exists but has no hash."""
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )

    def test_returns_false_for_corrupted_embedded_toml(self, sample_header: Path, tmp_path: Path) -> None:
        """Returns False for corrupted embedded TOML, logs warning."""
        output = tmp_path / "bindings.py"
        output.write_text(
            textwrap.dedent("""\
                # [headerkit-cache]
                # hash = not valid toml!!!
                # ???

                # actual code
            """),
            encoding="utf-8",
        )
        assert (
            is_up_to_date(
                output_path=output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )

    def test_returns_false_for_missing_hash_key(self, sample_header: Path, tmp_path: Path) -> None:
        """Returns False when TOML exists but hash key is missing."""
        output = tmp_path / "bindings.py"
        output.write_text(
            textwrap.dedent("""\
                # [headerkit-cache]
                # version = "0.8.4"
                # writer = "cffi"

                # actual code
            """),
            encoding="utf-8",
        )
        assert (
            is_up_to_date(
                output_path=output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )

    def test_embedded_takes_priority_over_sidecar(self, sample_header: Path, sample_output: Path) -> None:
        """When both embedded and sidecar exist, embedded hash is used."""
        # Write embedded with correct hash
        digest = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        original = sample_output.read_text(encoding="utf-8")
        sample_output.write_text(
            textwrap.dedent(f"""\
                # [headerkit-cache]
                # hash = "{digest}"
                # version = "0.8.4"
                # writer = "cffi"
                # generated = "2026-03-23T14:30:00Z"

            """)
            + original,
            encoding="utf-8",
        )
        # Also write a sidecar with a WRONG hash
        sidecar = _sidecar_path(sample_output)
        sidecar.write_text(
            textwrap.dedent("""\
                [headerkit-cache]
                hash = "0000000000000000000000000000000000000000000000000000000000000000"
                version = "0.8.4"
                writer = "cffi"
                generated = "2026-01-01T00:00:00"
            """),
            encoding="utf-8",
        )
        # Should still be True because embedded wins
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is True
        )

    def test_returns_false_for_corrupted_sidecar(self, sample_header: Path, sample_output: Path) -> None:
        """Returns False when sidecar file has corrupted TOML."""
        sidecar = _sidecar_path(sample_output)
        sidecar.write_text("this is not valid TOML at all!!!", encoding="utf-8")
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )

    def test_returns_false_for_sidecar_missing_hash_key(self, sample_header: Path, sample_output: Path) -> None:
        """Returns False when sidecar TOML lacks the hash key."""
        sidecar = _sidecar_path(sample_output)
        sidecar.write_text(
            textwrap.dedent("""\
                [headerkit-cache]
                version = "0.8.4"
                writer = "cffi"
            """),
            encoding="utf-8",
        )
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )

    def test_writer_options_affect_staleness(self, sample_header: Path, sample_output: Path) -> None:
        """Hash saved with options does not match check without options."""
        digest_with_opts = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={"exclude": "foo"},
        )
        sidecar = _sidecar_path(sample_output)
        sidecar.write_text(
            textwrap.dedent(f"""\
                [headerkit-cache]
                hash = "{digest_with_opts}"
                version = "0.8.4"
                writer = "cffi"
                generated = "2026-03-23T14:30:00Z"
            """),
            encoding="utf-8",
        )
        # Check WITHOUT the writer_options -> different hash -> stale
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )
        # Check WITH matching writer_options -> same hash -> up to date
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                writer_options={"exclude": "foo"},
            )
            is True
        )
