"""Tests for cache CLI subcommands (status, clear, rebuild-index)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


class TestCliDispatch:
    """Tests for early dispatch in _cli.main."""

    def test_cache_status_dispatches(self) -> None:
        """'headerkit cache status' dispatches to cache_status_main."""
        with patch("headerkit._cache_cli.cache_status_main", return_value=0) as mock_status:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "status", "--store-dir", "/tmp/cache"],
            ):
                from headerkit._cli import main

                main()
            mock_status.assert_called_once_with(["--store-dir", "/tmp/cache"])

    def test_cache_clear_dispatches(self) -> None:
        """'headerkit cache clear' dispatches to cache_clear_main."""
        with patch("headerkit._cache_cli.cache_clear_main", return_value=0) as mock_clear:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "clear", "--store-dir", "/tmp/cache"],
            ):
                from headerkit._cli import main

                main()
            mock_clear.assert_called_once_with(["--store-dir", "/tmp/cache"])

    def test_cache_rebuild_index_dispatches(self) -> None:
        """'headerkit cache rebuild-index' dispatches to cache_rebuild_index_main."""
        with patch("headerkit._cache_cli.cache_rebuild_index_main", return_value=0) as mock_rebuild:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "rebuild-index", "--store-dir", "/tmp/cache"],
            ):
                from headerkit._cli import main

                main()
            mock_rebuild.assert_called_once_with(["--store-dir", "/tmp/cache"])


class TestCacheStatusSubcommand:
    """Tests for cache_status_main."""

    def test_empty_cache(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty cache directory shows zero counts."""
        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / ".headerkit"
        (cache_dir / "ir").mkdir(parents=True)
        (cache_dir / "output").mkdir(parents=True)
        exit_code = cache_status_main(["--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Cache directory: {cache_dir}
            IR entries: 0
            Output entries: 0
        """)

    def test_with_ir_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Cache with IR entries shows correct count."""
        import json

        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / ".headerkit"
        ir_dir = cache_dir / "ir"
        for name in ["libclang.test_a", "libclang.test_b"]:
            entry = ir_dir / name
            entry.mkdir(parents=True)
            (entry / "metadata.json").write_text(
                json.dumps({"cache_key": f"key_{name}", "created": "2026-01-01T00:00:00Z"}),
                encoding="utf-8",
            )
        (cache_dir / "output").mkdir(parents=True)
        exit_code = cache_status_main(["--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Cache directory: {cache_dir}
            IR entries: 2
            Output entries: 0
        """)

    def test_with_output_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Cache with output entries shows per-writer counts."""
        import json

        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / ".headerkit"
        (cache_dir / "ir").mkdir(parents=True)

        # Create cffi writer entries
        for name in ["entry1", "entry2"]:
            entry = cache_dir / "output" / "cffi" / name
            entry.mkdir(parents=True)
            (entry / "metadata.json").write_text(
                json.dumps({"cache_key": f"key_{name}", "created": "2026-01-01T00:00:00Z"}),
                encoding="utf-8",
            )

        # Create ctypes writer entry
        entry = cache_dir / "output" / "ctypes" / "entry1"
        entry.mkdir(parents=True)
        (entry / "metadata.json").write_text(
            json.dumps({"cache_key": "key_ct1", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        exit_code = cache_status_main(["--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Cache directory: {cache_dir}
            IR entries: 0
            Output entries: 3
              cffi: 2
              ctypes: 1
        """)

    def test_nonexistent_cache_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nonexistent cache directory reports error."""
        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / "nonexistent"
        exit_code = cache_status_main(["--store-dir", str(cache_dir)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache status: cache directory not found: {cache_dir}\n"


class TestCacheClearSubcommand:
    """Tests for cache_clear_main."""

    def test_clear_all(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Clear all removes both IR and output entries."""
        import json

        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / ".headerkit"

        # Create IR entry
        ir_entry = cache_dir / "ir" / "libclang.test"
        ir_entry.mkdir(parents=True)
        (ir_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        (ir_entry / "ir.json").write_text("{}", encoding="utf-8")
        (cache_dir / "ir" / "index.json").write_text(
            json.dumps(
                {"version": 1, "entries": {"libclang.test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}}}
            ),
            encoding="utf-8",
        )

        # Create output entry
        out_entry = cache_dir / "output" / "cffi" / "entry1"
        out_entry.mkdir(parents=True)
        (out_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        (out_entry / "output.py").write_text("# output", encoding="utf-8")
        (cache_dir / "output" / "cffi" / "index.json").write_text(
            json.dumps({"version": 1, "entries": {"entry1": {"cache_key": "def", "created": "2026-01-01T00:00:00Z"}}}),
            encoding="utf-8",
        )

        exit_code = cache_clear_main(["--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == "Cleared all cache entries.\n"

        # IR dir should exist but be empty (no subdirs)
        assert not any(p for p in (cache_dir / "ir").iterdir() if p.is_dir())
        # Output dir should exist but have no writer subdirs with entries
        assert not any(p for p in (cache_dir / "output").iterdir() if p.is_dir())

    def test_clear_ir_only(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--ir flag clears only IR entries, preserving output."""
        import json

        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / ".headerkit"

        # Create IR entry
        ir_entry = cache_dir / "ir" / "libclang.test"
        ir_entry.mkdir(parents=True)
        (ir_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        # Create output entry
        out_entry = cache_dir / "output" / "cffi" / "entry1"
        out_entry.mkdir(parents=True)
        (out_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        exit_code = cache_clear_main(["--ir", "--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == "Cleared IR cache entries.\n"

        # IR should be empty
        assert not any(p for p in (cache_dir / "ir").iterdir() if p.is_dir())
        # Output should still have entry
        assert (out_entry / "metadata.json").exists()

    def test_clear_output_only(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--output flag clears only output entries, preserving IR."""
        import json

        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / ".headerkit"

        # Create IR entry
        ir_entry = cache_dir / "ir" / "libclang.test"
        ir_entry.mkdir(parents=True)
        (ir_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        # Create output entry
        out_entry = cache_dir / "output" / "cffi" / "entry1"
        out_entry.mkdir(parents=True)
        (out_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        exit_code = cache_clear_main(["--output", "--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == "Cleared output cache entries.\n"

        # IR should still have entry
        assert (ir_entry / "metadata.json").exists()
        # Output should be empty
        assert not any(p for p in (cache_dir / "output").iterdir() if p.is_dir())

    def test_clear_nonexistent_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nonexistent cache directory reports error."""
        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / "nonexistent"
        exit_code = cache_clear_main(["--store-dir", str(cache_dir)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache clear: cache directory not found: {cache_dir}\n"


class TestCacheRebuildIndexSubcommand:
    """Tests for cache_rebuild_index_main."""

    def test_rebuild_ir_index(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Rebuilds IR index.json from metadata files."""
        import json

        from headerkit._cache_cli import cache_rebuild_index_main

        cache_dir = tmp_path / ".headerkit"
        ir_dir = cache_dir / "ir"
        entry = ir_dir / "libclang.test"
        entry.mkdir(parents=True)
        (entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc123", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        exit_code = cache_rebuild_index_main(["--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Rebuilt index: {ir_dir / "index.json"} (1 entries)
        """)

        index_data = json.loads((ir_dir / "index.json").read_text(encoding="utf-8"))
        assert index_data == {
            "entries": {
                "libclang.test": {
                    "cache_key": "abc123",
                    "created": "2026-01-01T00:00:00Z",
                },
            },
            "version": 1,
        }

    def test_rebuild_output_index(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Rebuilds output writer index.json from metadata files."""
        import json

        from headerkit._cache_cli import cache_rebuild_index_main

        cache_dir = tmp_path / ".headerkit"
        (cache_dir / "ir").mkdir(parents=True)
        writer_dir = cache_dir / "output" / "cffi"
        entry = writer_dir / "entry1"
        entry.mkdir(parents=True)
        (entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def456", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        exit_code = cache_rebuild_index_main(["--store-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        # IR index should also be rebuilt (0 entries), then output index
        assert captured.out == textwrap.dedent(f"""\
            Rebuilt index: {cache_dir / "ir" / "index.json"} (0 entries)
            Rebuilt index: {writer_dir / "index.json"} (1 entries)
        """)

        index_data = json.loads((writer_dir / "index.json").read_text(encoding="utf-8"))
        assert index_data == {
            "entries": {
                "entry1": {
                    "cache_key": "def456",
                    "created": "2026-01-01T00:00:00Z",
                },
            },
            "version": 1,
        }

    def test_rebuild_nonexistent_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nonexistent cache directory reports error."""
        from headerkit._cache_cli import cache_rebuild_index_main

        cache_dir = tmp_path / "nonexistent"
        exit_code = cache_rebuild_index_main(["--store-dir", str(cache_dir)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache rebuild-index: cache directory not found: {cache_dir}\n"
