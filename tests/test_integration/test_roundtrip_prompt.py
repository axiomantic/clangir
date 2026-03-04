"""Integration roundtrip tests: parse C headers with libclang -> IR -> prompt/LLM context output."""

from __future__ import annotations

import json
import textwrap

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.writers.prompt import PromptWriter

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


@pytest.fixture(scope="session")
def backend():
    return get_backend("libclang")


def parse_and_prompt(backend, code: str, verbosity: str = "compact") -> str:
    header = backend.parse(code, "test.h")
    return PromptWriter(verbosity=verbosity).write(header)


class TestPromptCompact:
    """Integration tests for the prompt writer in compact verbosity mode."""

    def test_header_line(self, backend):
        """First line of compact output is always the path comment."""
        output = parse_and_prompt(backend, "int x;", verbosity="compact")
        first_line = output.split("\n")[0]
        assert first_line == "// test.h (headerkit compact)"

    def test_struct(self, backend):
        """Struct with two int fields renders in compact one-liner form."""
        output = parse_and_prompt(backend, "struct Point { int x; int y; };", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
            STRUCT Point {x:int, y:int}
        """)

    def test_function(self, backend):
        """Function declaration with two int parameters renders as FUNC line."""
        output = parse_and_prompt(backend, "int add(int a, int b);", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
            FUNC add(a:int, b:int) -> int
        """)

    def test_void_function(self, backend):
        """Void function with no parameters renders with empty param list."""
        output = parse_and_prompt(backend, "void init(void);", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
            FUNC init() -> void
        """)

    def test_enum(self, backend):
        """Enum with two named values renders as ENUM line."""
        output = parse_and_prompt(backend, "enum Color { RED = 0, GREEN = 1 };", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
            ENUM Color: RED=0, GREEN=1
        """)

    def test_integer_macro(self, backend):
        """Integer macro renders as CONST line before the function."""
        output = parse_and_prompt(backend, "#define MAX_SIZE 1024\nvoid func(void);", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
            CONST MAX_SIZE=1024
            FUNC func() -> void
        """)

    def test_function_pointer_typedef(self, backend):
        """Function pointer typedef renders as CALLBACK line, not TYPEDEF.

        libclang does not preserve parameter names in function pointer typedef
        parameters, so the param appears as bare type (int) without a name.
        """
        output = parse_and_prompt(backend, "typedef void (*Callback)(int status);", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
            CALLBACK Callback(int) -> void
        """)

    def test_empty_header(self, backend):
        """Empty source produces only the header comment line."""
        output = parse_and_prompt(backend, "", verbosity="compact")
        assert output == textwrap.dedent("""\
            // test.h (headerkit compact)
        """)


class TestPromptStandard:
    """Integration tests for the prompt writer in standard verbosity mode."""

    def test_header_line(self, backend):
        """First line of standard output is the path comment."""
        output = parse_and_prompt(backend, "int x;", verbosity="standard")
        first_line = output.split("\n")[0]
        assert first_line == "# test.h (headerkit standard)"

    def test_struct(self, backend):
        """Struct with two int fields renders in standard YAML-like form."""
        output = parse_and_prompt(backend, "struct Point { int x; int y; };", verbosity="standard")
        assert output == textwrap.dedent("""\
            # test.h (headerkit standard)

            structs:
              Point:
                fields:
                  x: int
                  y: int
        """)

    def test_function(self, backend):
        """Function declaration with two int params renders under functions section."""
        output = parse_and_prompt(backend, "int add(int a, int b);", verbosity="standard")
        assert output == textwrap.dedent("""\
            # test.h (headerkit standard)

            functions:
              add: (a: int, b: int) -> int
        """)

    def test_callback(self, backend):
        """Function pointer typedef renders under callbacks section.

        libclang does not preserve parameter names in function pointer typedef
        parameters, so the param appears as bare type (int) without a name.
        """
        output = parse_and_prompt(backend, "typedef void (*on_event_fn)(int code);", verbosity="standard")
        assert output == textwrap.dedent("""\
            # test.h (headerkit standard)

            callbacks:
              on_event_fn: (int) -> void
        """)

    def test_empty_header(self, backend):
        """Empty source produces only the header comment line."""
        output = parse_and_prompt(backend, "", verbosity="standard")
        assert output == textwrap.dedent("""\
            # test.h (headerkit standard)
        """)


class TestPromptVerbose:
    """Integration tests for the prompt writer in verbose (JSON) verbosity mode."""

    def test_returns_valid_json(self, backend):
        """Verbose output is valid JSON that can be parsed without error."""
        output = parse_and_prompt(backend, "int x;", verbosity="verbose")
        # json.loads raises if invalid; that is the failure signal
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_json_structure(self, backend):
        """Top-level JSON object has 'path' and 'declarations' keys."""
        output = parse_and_prompt(backend, "struct Point { int x; };", verbosity="verbose")
        parsed = json.loads(output)
        assert set(parsed.keys()) == {"path", "declarations"}

    def test_path_in_json(self, backend):
        """The 'path' key in verbose JSON equals the header path passed to parse."""
        output = parse_and_prompt(backend, "int x;", verbosity="verbose")
        parsed = json.loads(output)
        assert parsed["path"] == "test.h"

    def test_cross_refs_present(self, backend):
        """Structs referenced by function parameters receive a 'used_in' list.

        The cross-reference fix in Task 0 ensures that when init() takes a
        struct Config * parameter, the Config declaration has used_in: ['init'].
        """
        code = "struct Config { int flags; };\nvoid init(struct Config *cfg);"
        output = parse_and_prompt(backend, code, verbosity="verbose")
        parsed = json.loads(output)
        decls = parsed["declarations"]
        config_decls = [d for d in decls if d.get("name") == "Config"]
        assert len(config_decls) == 1
        config = config_decls[0]
        assert config["used_in"] == ["init"]

    def test_empty_header(self, backend):
        """Empty source produces valid JSON with an empty declarations list."""
        output = parse_and_prompt(backend, "", verbosity="verbose")
        parsed = json.loads(output)
        assert parsed == {"path": "test.h", "declarations": []}


class TestPromptInvalidVerbosity:
    """Tests for PromptWriter input validation."""

    def test_invalid_verbosity_raises(self):
        """Constructing PromptWriter with an unknown verbosity raises ValueError."""
        with pytest.raises(ValueError, match="Unknown verbosity"):
            PromptWriter(verbosity="invalid")
