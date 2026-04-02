"""Tests for the public API re-exports from headerkit package."""


def test_all_matches_module_exports():
    """__all__ should list every public name exported from headerkit."""
    import headerkit

    for name in headerkit.__all__:
        assert hasattr(headerkit, name), f"headerkit.__all__ lists {name!r} but it is not an attribute"

    # Reverse check: every public non-module attribute should be listed in __all__
    import types

    public_attrs = {
        name
        for name in dir(headerkit)
        if not name.startswith("_") and not isinstance(getattr(headerkit, name), types.ModuleType)
    }
    all_set = set(headerkit.__all__)
    missing = public_attrs - all_set
    assert missing == set(), f"Public attributes missing from __all__: {missing}"


def test_type_aliases_are_unions():
    """TypeExpr and Declaration should be Union type aliases."""
    import typing

    import headerkit
    from headerkit import (
        Array,
        Constant,
        CType,
        Enum,
        Function,
        FunctionPointer,
        Pointer,
        Struct,
        Typedef,
        Variable,
    )

    # These are Union types, so get_args should return their members
    type_expr_members = set(typing.get_args(headerkit.TypeExpr))
    assert type_expr_members == {CType, Pointer, Array, FunctionPointer}

    decl_members = set(typing.get_args(headerkit.Declaration))
    assert decl_members == {Enum, Struct, Function, Typedef, Variable, Constant}


def test_ir_types_match_direct_import():
    """Types imported from headerkit should be the same objects as from headerkit.ir."""
    import headerkit
    from headerkit import ir

    assert headerkit.CType is ir.CType
    assert headerkit.Pointer is ir.Pointer
    assert headerkit.Array is ir.Array
    assert headerkit.Parameter is ir.Parameter
    assert headerkit.FunctionPointer is ir.FunctionPointer
    assert headerkit.Field is ir.Field
    assert headerkit.EnumValue is ir.EnumValue
    assert headerkit.Enum is ir.Enum
    assert headerkit.Struct is ir.Struct
    assert headerkit.Function is ir.Function
    assert headerkit.Typedef is ir.Typedef
    assert headerkit.Variable is ir.Variable
    assert headerkit.Constant is ir.Constant
    assert headerkit.Header is ir.Header
    assert headerkit.SourceLocation is ir.SourceLocation
    assert headerkit.ParserBackend is ir.ParserBackend


def test_backend_functions_match_direct_import():
    """Backend functions imported from headerkit should match headerkit.backends."""
    import headerkit
    from headerkit import backends

    assert headerkit.get_backend is backends.get_backend
    assert headerkit.list_backends is backends.list_backends
    assert headerkit.is_backend_available is backends.is_backend_available


def test_writer_symbols_in_all():
    """All writer symbols should be listed in headerkit.__all__."""
    import headerkit

    expected_writer_symbols = {
        "WriterBackend",
        "get_default_writer",
        "get_writer",
        "get_writer_info",
        "is_writer_available",
        "list_writers",
        "register_writer",
    }
    all_set = set(headerkit.__all__)
    # Subset check: test_all_matches_module_exports enforces that __all__ contains
    # no extra symbols beyond what the module exports, so together the two tests
    # enforce full exact coverage.
    assert expected_writer_symbols <= all_set, f"Missing from headerkit.__all__: {expected_writer_symbols - all_set}"


def test_writer_functions_match_direct_import():
    """Writer functions imported from headerkit should match headerkit.writers."""
    import headerkit
    from headerkit import writers

    assert headerkit.WriterBackend is writers.WriterBackend
    assert headerkit.get_default_writer is writers.get_default_writer
    assert headerkit.get_writer is writers.get_writer
    assert headerkit.get_writer_info is writers.get_writer_info
    assert headerkit.is_writer_available is writers.is_writer_available
    assert headerkit.list_writers is writers.list_writers
    assert headerkit.register_writer is writers.register_writer


def test_generate_exported():
    """generate function should be importable from headerkit and match _generate module."""
    import headerkit
    from headerkit._generate import generate

    assert headerkit.generate is generate


def test_generate_all_exported():
    """generate_all function should be importable from headerkit and match _generate module."""
    import headerkit
    from headerkit._generate import generate_all

    assert headerkit.generate_all is generate_all


def test_json_to_header_exported():
    """json_to_header function should be importable from headerkit and match _ir_json module."""
    import headerkit
    from headerkit._ir_json import json_to_header

    assert headerkit.json_to_header is json_to_header


def test_generate_result_exported():
    """GenerateResult class should be importable from headerkit and match _generate module."""
    import headerkit
    from headerkit._generate import GenerateResult

    assert headerkit.GenerateResult is GenerateResult


def test_generate_symbols_in_all():
    """Generate and IR JSON symbols should be listed in headerkit.__all__."""
    import headerkit

    expected_symbols = {
        "generate",
        "generate_all",
        "json_to_header",
        "GenerateResult",
    }
    all_set = set(headerkit.__all__)
    assert expected_symbols <= all_set, f"Missing from headerkit.__all__: {expected_symbols - all_set}"


def test_batch_generate_exported():
    """batch_generate function should be importable from headerkit and match _generate module."""
    import headerkit
    from headerkit._generate import batch_generate

    assert headerkit.batch_generate is batch_generate


def test_batch_result_exported():
    """BatchResult class should be importable from headerkit and match _generate module."""
    import headerkit
    from headerkit._generate import BatchResult

    assert headerkit.BatchResult is BatchResult


def test_resolve_headers_exported():
    """resolve_headers function should be importable from headerkit and match _resolve module."""
    import headerkit
    from headerkit._resolve import resolve_headers

    assert headerkit.resolve_headers is resolve_headers


def test_resolve_output_path_exported():
    """resolve_output_path function should be importable from headerkit and match _resolve module."""
    import headerkit
    from headerkit._resolve import resolve_output_path

    assert headerkit.resolve_output_path is resolve_output_path


def test_check_output_collisions_exported():
    """check_output_collisions function should be importable from headerkit and match _resolve module."""
    import headerkit
    from headerkit._resolve import check_output_collisions

    assert headerkit.check_output_collisions is check_output_collisions


def test_batch_and_resolve_symbols_in_all():
    """Batch and resolve symbols should be listed in headerkit.__all__."""
    import headerkit

    expected_symbols = {
        "batch_generate",
        "BatchResult",
        "resolve_headers",
        "resolve_output_path",
        "check_output_collisions",
    }
    all_set = set(headerkit.__all__)
    assert expected_symbols <= all_set, f"Missing from headerkit.__all__: {expected_symbols - all_set}"


def test_is_up_to_date_batch_not_in_all():
    """is_up_to_date_batch is intentionally NOT exported in __all__."""
    import headerkit

    assert "is_up_to_date_batch" not in headerkit.__all__
