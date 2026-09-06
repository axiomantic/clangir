"""The tree-sitter backend warns rather than silently ignoring an include filter.

This backend parses exactly the string it is given and follows no ``#include``
directives, so an ``allowlist`` or ``denylist`` naming another file describes
declarations it was never going to emit.  Honoring such a list and ignoring it
produce byte-identical output, which is the silent-failure shape: the caller
gets a plausible result and no signal that the filter did nothing.  The warning
is the only thing that distinguishes the two, so it is what these tests pin.
"""

from __future__ import annotations

import textwrap
import warnings

import pytest

from headerkit.backends import get_backend

treesitter = pytest.mark.treesitter

SOURCE = textwrap.dedent("""\
    int only_declaration(int a);
""")


@treesitter
class TestTreeSitterFilterWarnings:
    @pytest.mark.parametrize("key", ["allowlist", "denylist"])
    def test_an_entry_naming_another_file_warns(self, key: str) -> None:
        """The warning names the offending list, so the caller can tell which one."""
        with pytest.warns(UserWarning, match=rf"{key} \['other\.h'\]"):
            header = get_backend("tree-sitter").parse(SOURCE, "main.h", **{key: ["other.h"]})
        assert [d.name for d in header.declarations] == ["only_declaration"]

    @pytest.mark.parametrize("key", ["allowlist", "denylist"])
    def test_an_entry_naming_the_parsed_file_warns_nothing(self, key: str) -> None:
        """A list satisfied by the parsed file itself asks for nothing unavailable.

        For the allowlist the parsed file is already admitted; for the denylist the
        parsed file is never denied by any backend.  Either way the caller's
        request is honored exactly, so there is nothing to warn about.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            header = get_backend("tree-sitter").parse(SOURCE, "main.h", **{key: ["main.h"]})
        assert [d.name for d in header.declarations] == ["only_declaration"]

    def test_both_lists_warn_independently(self) -> None:
        """Two unhonorable lists produce two warnings, not one.

        A loop that stopped at the first offending list would leave the second
        silently ignored -- the exact defect the warning exists to prevent.
        """
        with pytest.warns(UserWarning) as record:
            get_backend("tree-sitter").parse(SOURCE, "main.h", allowlist=["a.h"], denylist=["b.h"])
        messages = [str(w.message) for w in record]
        assert sum("allowlist" in m for m in messages) == 1
        assert sum("denylist" in m for m in messages) == 1
