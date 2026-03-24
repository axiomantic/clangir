"""Tests for headerkit.cache.is_up_to_date_batch."""

from __future__ import annotations

from pathlib import Path

from headerkit.cache import is_up_to_date_batch, save_hash
from headerkit.writers import get_writer


class TestIsUpToDateBatch:
    """Tests for batch staleness checking."""

    def test_single_stale_entry(self, sample_header: Path, sample_output: Path) -> None:
        """A single check with no stored hash returns {path: False}."""
        result = is_up_to_date_batch(
            [
                {
                    "output_path": sample_output,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                }
            ]
        )
        assert result == {str(sample_output): False}

    def test_mix_of_up_to_date_and_stale(self, sample_header: Path, tmp_path: Path) -> None:
        """Correctly identifies mix of up-to-date and stale outputs."""
        fresh = tmp_path / "fresh.py"
        fresh.write_text("# fresh\n", encoding="utf-8")
        stale = tmp_path / "stale.py"
        stale.write_text("# stale\n", encoding="utf-8")

        writer = get_writer("cffi")
        save_hash(
            output_path=fresh,
            header_paths=[sample_header],
            writer_name="cffi",
            writer=writer,
        )
        # stale has no hash

        result = is_up_to_date_batch(
            [
                {
                    "output_path": fresh,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                },
                {
                    "output_path": stale,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                },
            ]
        )
        assert result == {str(fresh): True, str(stale): False}

    def test_exception_in_single_check_returns_false(self, sample_header: Path, tmp_path: Path) -> None:
        """A check that raises an exception returns False without stopping batch.

        Uses two different output paths to verify error isolation:
        good_path gets a sidecar hash and should be True,
        bad_path has empty header_paths (raises ValueError) and should be False.
        """
        good_path = tmp_path / "good.py"
        good_path.write_text("# good\n", encoding="utf-8")
        save_hash(
            output_path=good_path,
            header_paths=[sample_header],
            writer_name="cffi",
            writer=None,
        )

        bad_path = tmp_path / "bad.py"
        bad_path.write_text("# bad\n", encoding="utf-8")

        result = is_up_to_date_batch(
            [
                {
                    "output_path": good_path,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                },
                {
                    # This will fail: empty header_paths raises ValueError
                    "output_path": bad_path,
                    "header_paths": [],
                    "writer_name": "cffi",
                },
            ]
        )
        assert result == {str(good_path): True, str(bad_path): False}

    def test_empty_batch_returns_empty_dict(self) -> None:
        """Empty batch input returns empty dict."""
        result = is_up_to_date_batch([])
        assert result == {}

    def test_writer_options_forwarded(self, sample_header: Path, tmp_path: Path) -> None:
        """Writer options are correctly forwarded to is_up_to_date."""
        output = tmp_path / "opts.py"
        output.write_text("# opts\n", encoding="utf-8")
        # Save hash WITH writer_options
        save_hash(
            output_path=output,
            header_paths=[sample_header],
            writer_name="cffi",
            writer_options={"exclude": "internal_.*"},
            writer=None,
        )

        # Check with same options: should be True
        result_match = is_up_to_date_batch(
            [
                {
                    "output_path": output,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                    "writer_options": {"exclude": "internal_.*"},
                }
            ]
        )
        assert result_match == {str(output): True}

        # Check with different options: should be False
        result_mismatch = is_up_to_date_batch(
            [
                {
                    "output_path": output,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                    "writer_options": {"exclude": "other_.*"},
                }
            ]
        )
        assert result_mismatch == {str(output): False}

    def test_extra_inputs_forwarded(self, sample_header: Path, tmp_path: Path) -> None:
        """Extra inputs are correctly forwarded to is_up_to_date."""
        output = tmp_path / "extra.py"
        output.write_text("# extra\n", encoding="utf-8")
        extra = tmp_path / "config.cfg"
        extra.write_text("mode=release\n", encoding="utf-8")

        save_hash(
            output_path=output,
            header_paths=[sample_header],
            writer_name="cffi",
            extra_inputs=[extra],
            writer=None,
        )

        result = is_up_to_date_batch(
            [
                {
                    "output_path": output,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                    "extra_inputs": [extra],
                }
            ]
        )
        assert result == {str(output): True}
