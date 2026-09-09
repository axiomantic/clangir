"""Tests for the shared writer helpers in :mod:`headerkit.writers.base`."""

import ast
import textwrap

import pytest

from headerkit.writers.base import DEDENT_BLOCK, module_level_bindings, render_block_template


class TestModuleLevelBindings:
    """The collision set for a generated module's export block is derived from this.

    A binding form this walk does not recognize is a name the export block will
    happily overwrite, so each form is asserted separately rather than through
    one composite source: a composite passes as soon as *any* clause contributes
    the expected name.
    """

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("x = 1", {"x"}),
            ("x = y = 1", {"x", "y"}),
            ("x: int = 1", {"x"}),
            ("x: int", {"x"}),
            ("def f(): pass", {"f"}),
            ("async def f(): pass", {"f"}),
            ("class C: pass", {"C"}),
            ("import json", {"json"}),
            ("import os.path", {"os"}),
            ("import os.path as p", {"p"}),
            ("from a import b", {"b"}),
            ("from a import b as c", {"c"}),
            ("from a import b, d", {"b", "d"}),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_each_binding_form_is_recognized(self, source: str, expected: set[str]) -> None:
        assert module_level_bindings(source) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "def f():\n    inner = 1",
            "class C:\n    attr = 1",
            "def f():\n    import json",
        ],
        ids=["function_local", "class_attribute", "function_local_import"],
    )
    def test_a_nested_binding_is_not_module_level(self, source: str) -> None:
        """A name bound inside a function or class cannot collide with an export."""
        assert module_level_bindings(source) <= {"f", "C"}

    def test_an_expression_binds_nothing(self) -> None:
        assert module_level_bindings("print(1)\n_lib.thing()") == frozenset()

    def test_unparseable_source_raises(self) -> None:
        """Silently reporting no bindings would under-reserve the collision set."""
        with pytest.raises(SyntaxError):
            module_level_bindings("def (:")


class TestRenderBlockTemplate:
    """``textwrap.dedent`` measures after interpolation; this substitutes after."""

    #: A block whose lines carry their own indentation, as a generated body does.
    BLOCK = "    a = 1\n    b = 2"

    def test_a_multi_line_block_leaves_the_template_dedented(self) -> None:
        template = """\
            def f():
            {block}
        """.replace("{block}", DEDENT_BLOCK)
        rendered = render_block_template(template, self.BLOCK)

        assert rendered.startswith("def f():"), "the template kept its source indentation"
        ast.parse(rendered)

    def test_the_naive_form_is_what_this_avoids(self) -> None:
        """Pin the defect itself, so the helper's reason for existing is executable.

        The emitted text is asserted to be *un-parseable* rather than to carry
        some particular leftover indent: how much survives depends on the block's
        own indentation, but that the file does not parse is the defect.
        """
        naive = textwrap.dedent(f"""\
            def f():
            {self.BLOCK}
        """)

        with pytest.raises(SyntaxError):
            ast.parse(naive)

    def test_an_empty_block_leaves_a_blank_line(self) -> None:
        template = "x = 1\n" + DEDENT_BLOCK + "\n"
        assert render_block_template(template, "") == "x = 1\n\n"

    def test_blocks_substitute_left_to_right(self) -> None:
        template = f"{DEDENT_BLOCK}\n{DEDENT_BLOCK}\n"
        assert render_block_template(template, "first", "second") == "first\nsecond\n"
