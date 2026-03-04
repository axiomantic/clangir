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
