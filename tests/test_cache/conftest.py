"""Shared fixtures for cache tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def sample_header(tmp_path: Path) -> Path:
    """Create a minimal C header file."""
    h = tmp_path / "test.h"
    h.write_text("int add(int a, int b);\n", encoding="utf-8")
    return h


@pytest.fixture()
def sample_output(tmp_path: Path) -> Path:
    """Create a minimal generated output file."""
    out = tmp_path / "bindings.py"
    out.write_text("# generated bindings\n", encoding="utf-8")
    return out


@pytest.fixture()
def second_header(tmp_path: Path) -> Path:
    """Create a second C header file for multi-header tests."""
    h = tmp_path / "other.h"
    h.write_text("void cleanup(void);\n", encoding="utf-8")
    return h
