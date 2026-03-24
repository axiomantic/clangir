"""headerkit - C/C++ header parsing toolkit."""

from headerkit.backends import get_backend, is_backend_available, list_backends
from headerkit.cache import compute_hash, is_up_to_date, save_hash
from headerkit.ir import (
    Array,
    Constant,
    # Type expressions
    CType,
    Declaration,
    Enum,
    EnumValue,
    # Declarations
    Field,
    Function,
    FunctionPointer,
    # Container
    Header,
    Parameter,
    # Protocol
    ParserBackend,
    Pointer,
    SourceLocation,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.writers import (
    WriterBackend,
    get_default_writer,
    get_writer,
    get_writer_info,
    is_writer_available,
    list_writers,
    register_writer,
)

__all__ = [
    # Types
    "CType",
    "Pointer",
    "Array",
    "Parameter",
    "FunctionPointer",
    "TypeExpr",
    # Declarations
    "Field",
    "EnumValue",
    "Enum",
    "Struct",
    "Function",
    "Typedef",
    "Variable",
    "Constant",
    "Declaration",
    # Container
    "Header",
    "SourceLocation",
    # Parser Protocol
    "ParserBackend",
    # Backend API
    "get_backend",
    "list_backends",
    "is_backend_available",
    # Writer Protocol
    "WriterBackend",
    # Writer API
    "get_default_writer",
    "get_writer",
    "get_writer_info",
    "is_writer_available",
    "list_writers",
    "register_writer",
    # Cache API
    "compute_hash",
    "is_up_to_date",
    "save_hash",
]
