"""Integration tests for the headerkit CLI.

These tests exercise the full CLI pipeline: real libclang parsing, real writers,
and real file I/O. Each test requires a system libclang installation and is
skipped automatically when libclang is unavailable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from headerkit._cli import main
from headerkit.backends import is_backend_available

# Skip entire module if libclang is not available
pytestmark = [
    pytest.mark.skipif(
        not is_backend_available("libclang"),
        reason="libclang backend not available",
    ),
    pytest.mark.allow("subprocess"),
]

# ---------------------------------------------------------------------------
# Shared header sources
# ---------------------------------------------------------------------------

_SIMPLE_HEADER = """\
typedef unsigned int uint32_t;

struct Point {
    int x;
    int y;
};

enum Color {
    RED = 0,
    GREEN = 1,
    BLUE = 2
};

int add(int a, int b);
void greet(const char *name);
"""

_FUNCTION_ONLY = "int multiply(int a, int b);\n"
_CONDITIONAL = "#ifdef ENABLE_FEATURE\nint feature_func(void);\n#endif\n"


# ===========================================================================
# Smoke tests – verify the pipeline completes cleanly
# ===========================================================================


class TestCliSmokeTests:
    """Verify the full pipeline runs without error for each writer."""

    def test_json_to_stdout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """json writer emits valid JSON to stdout."""
        header = tmp_path / "test.h"
        header.write_text(_SIMPLE_HEADER)
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "json", str(header)])

        result = main()

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        assert "declarations" in data
        assert isinstance(data["declarations"], list)
        assert len(data["declarations"]) > 0

    def test_json_to_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """json writer creates output file with valid JSON."""
        # Create .git marker so tmp_path is recognized as a project root
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        header = tmp_path / "test.h"
        header.write_text(_SIMPLE_HEADER)
        output = tmp_path / "out.json"
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", "--no-config", "-w", "json", "-o", f"json:{output}", str(header)],
        )

        result = main()

        assert result == 0
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert "declarations" in data

    def test_cffi_to_stdout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cffi writer emits non-empty output to stdout."""
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "cffi", str(header)])

        result = main()

        assert result == 0
        assert capsys.readouterr().out.strip() != ""

    def test_lua_to_stdout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """lua writer emits LuaJIT FFI output to stdout."""
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "lua", str(header)])

        result = main()

        assert result == 0
        out = capsys.readouterr().out
        assert "ffi" in out.lower() or "multiply" in out

    def test_prompt_to_stdout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """prompt writer emits non-empty text to stdout."""
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "prompt", str(header)])

        result = main()

        assert result == 0
        assert capsys.readouterr().out.strip() != ""


# ===========================================================================
# Declaration content – verify IR is populated correctly
# ===========================================================================


class TestCliDeclarationContent:
    """Verify specific declarations appear in writer output."""

    def test_function_name_in_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Parsed function name appears in JSON declarations list."""
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "json", str(header)])

        result = main()

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        names = [d["name"] for d in data["declarations"]]
        assert "multiply" in names

    def test_struct_name_in_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Parsed struct name appears in JSON declarations list."""
        header = tmp_path / "test.h"
        header.write_text("struct Rect { int width; int height; };\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "json", str(header)])

        result = main()

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        names = [d["name"] for d in data["declarations"]]
        assert "Rect" in names

    def test_enum_name_in_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Parsed enum name appears in JSON declarations list."""
        header = tmp_path / "test.h"
        header.write_text("enum Status { OK = 0, ERR = 1 };\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "json", str(header)])

        result = main()

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        names = [d["name"] for d in data["declarations"]]
        assert "Status" in names

    def test_function_name_in_cffi(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Parsed function name appears in CFFI output."""
        header = tmp_path / "test.h"
        header.write_text("int compute(int x, int y);\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "cffi", str(header)])

        result = main()

        assert result == 0
        assert "compute" in capsys.readouterr().out

    def test_function_name_in_lua(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Parsed function name appears in Lua FFI output."""
        header = tmp_path / "test.h"
        header.write_text("int compute(int x, int y);\n")
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "lua", str(header)])

        result = main()

        assert result == 0
        assert "compute" in capsys.readouterr().out


# ===========================================================================
# Flags – verify CLI flags affect backend/writer behaviour
# ===========================================================================


class TestCliFlags:
    """Verify CLI flags correctly influence parsing and output."""

    def test_define_flag_enables_conditional_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """-D flag passes a preprocessor define; guarded declarations are included."""
        header = tmp_path / "test.h"
        header.write_text(_CONDITIONAL)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "headerkit",
                "--no-config",
                "-D",
                "ENABLE_FEATURE",
                "-w",
                "cffi",
                str(header),
            ],
        )

        result = main()

        assert result == 0
        assert "feature_func" in capsys.readouterr().out

    def test_define_absent_excludes_conditional_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without -D the guarded declaration is absent from output."""
        header = tmp_path / "test.h"
        header.write_text(_CONDITIONAL)
        monkeypatch.setattr(sys, "argv", ["headerkit", "--no-config", "-w", "cffi", str(header)])

        result = main()

        assert result == 0
        assert "feature_func" not in capsys.readouterr().out

    def test_writer_opt_cffi_exclude_patterns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Multiple --writer-opt flags for same key form a list; patterns filter output.

        Two --writer-opt flags are required so the CLI produces a list[str] value
        (single-value opts are scalar-unwrapped; see WriterSpec design).
        """
        header = tmp_path / "test.h"
        header.write_text("int public_func(void);\nint __private_func(void);\n")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "headerkit",
                "--no-config",
                "-w",
                "cffi",
                "--writer-opt",
                "cffi:exclude_patterns=^__",
                "--writer-opt",
                "cffi:exclude_patterns=^_internal",  # second flag forces list path
                str(header),
            ],
        )

        result = main()

        assert result == 0
        out = capsys.readouterr().out
        assert "public_func" in out
        assert "__private_func" not in out


# ===========================================================================
# Multiple writers – verify output routing
# ===========================================================================


class TestCliMultipleWriters:
    """Verify multiple -w flags produce independent outputs."""

    def test_two_writers_both_to_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two writers routing to files each produce a file with correct content."""
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        json_out = tmp_path / "out.json"
        cffi_out = tmp_path / "out.h"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "headerkit",
                "--no-config",
                "-w",
                "json",
                "-w",
                "cffi",
                "-o",
                f"json:{json_out}",
                "-o",
                f"cffi:{cffi_out}",
                str(header),
            ],
        )

        result = main()

        assert result == 0
        assert json_out.exists()
        assert cffi_out.exists()
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert "declarations" in data
        assert "multiply" in cffi_out.read_text(encoding="utf-8")

    def test_one_writer_explicit_one_writer_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One writer with explicit -o path; the other uses its default output pattern."""
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        json_out = tmp_path / "out.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "headerkit",
                "--no-config",
                "-w",
                "json",
                "-w",
                "cffi",
                "-o",
                f"json:{json_out}",
                str(header),
            ],
        )

        result = main()

        assert result == 0
        assert json_out.exists()
        # cffi writer uses default pattern {dir}/{stem}_cffi.py
        cffi_default = tmp_path / "test_cffi.py"
        assert cffi_default.exists()
        assert "multiply" in cffi_default.read_text(encoding="utf-8")


# ===========================================================================
# Multiple input files – verify umbrella header merging
# ===========================================================================


class TestCliMultipleInputFiles:
    """Verify multiple input files are merged into a single IR."""

    def test_two_files_declarations_both_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Declarations from both input files appear in the output files."""
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        header_a = tmp_path / "a.h"
        header_b = tmp_path / "b.h"
        header_a.write_text("int func_a(void);\n")
        header_b.write_text("int func_b(void);\n")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "headerkit",
                "--no-config",
                "-w",
                "cffi",
                str(header_a),
                str(header_b),
            ],
        )

        result = main()

        assert result == 0
        # Batch mode writes to default output pattern {dir}/{stem}_cffi.py
        cffi_a = tmp_path / "a_cffi.py"
        cffi_b = tmp_path / "b_cffi.py"
        assert cffi_a.exists()
        assert cffi_b.exists()
        assert "func_a" in cffi_a.read_text(encoding="utf-8")
        assert "func_b" in cffi_b.read_text(encoding="utf-8")

    def test_two_files_json_declaration_count(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """JSON output declaration count reflects all merged input files."""
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        header_a = tmp_path / "a.h"
        header_b = tmp_path / "b.h"
        header_a.write_text("int alpha(void);\n")
        header_b.write_text("int beta(void);\n")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "headerkit",
                "--no-config",
                "-w",
                "json",
                str(header_a),
                str(header_b),
            ],
        )

        result = main()

        assert result == 0
        # Batch mode writes each header to default pattern {dir}/{stem}.json
        json_a = tmp_path / "a.json"
        json_b = tmp_path / "b.json"
        assert json_a.exists()
        assert json_b.exists()
        data_a = json.loads(json_a.read_text(encoding="utf-8"))
        data_b = json.loads(json_b.read_text(encoding="utf-8"))
        names_a = {d["name"] for d in data_a["declarations"]}
        names_b = {d["name"] for d in data_b["declarations"]}
        assert "alpha" in names_a
        assert "beta" in names_b


# ===========================================================================
# Config file – verify config integration
# ===========================================================================


class TestCliConfigFile:
    """Verify config file values are applied to the pipeline."""

    def test_config_sets_writer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """writers = [...] in config applies when no -w flag is given."""
        header = tmp_path / "test.h"
        header.write_text(_FUNCTION_ONLY)
        config = tmp_path / ".headerkit.toml"
        config.write_text('writers = ["json"]\n')
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", f"--config={config}", str(header)],
        )

        result = main()

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        assert "declarations" in data

    def test_config_writer_option_applied(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """[writer.cffi] options in config are applied to the writer.

        Two patterns are used so _instantiate_writer receives a list (single-element
        lists are scalar-unwrapped; multi-element lists are passed as-is).
        """
        header = tmp_path / "test.h"
        header.write_text("int pub(void);\nint __priv(void);\nint _internal(void);\n")
        config = tmp_path / ".headerkit.toml"
        config.write_text('[writer.cffi]\nexclude_patterns = ["^__", "^_internal"]\n')
        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", f"--config={config}", "-w", "cffi", str(header)],
        )

        result = main()

        assert result == 0
        out = capsys.readouterr().out
        assert "pub" in out
        assert "__priv" not in out
        assert "_internal" not in out

    def test_config_include_dirs_applied(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """include_dirs from config are passed to the backend."""
        inc_dir = tmp_path / "include"
        inc_dir.mkdir()
        dep_header = inc_dir / "dep.h"
        dep_header.write_text("int dep_func(void);\n")

        header = tmp_path / "test.h"
        header.write_text('#include "dep.h"\n')

        config = tmp_path / ".headerkit.toml"
        # Use as_posix() to produce forward slashes; Windows backslashes are
        # not valid in TOML basic strings (they are interpreted as escapes).
        config.write_text(f'include_dirs = ["{inc_dir.as_posix()}"]\n')

        monkeypatch.setattr(
            sys,
            "argv",
            ["headerkit", f"--config={config}", "-w", "cffi", str(header)],
        )

        result = main()

        assert result == 0
        assert "dep_func" in capsys.readouterr().out
