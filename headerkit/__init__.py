"""headerkit - C/C++ header parsing toolkit."""

from headerkit._generate import GenerateResult, generate, generate_all
from headerkit._ir_json import json_to_header
from headerkit._populate import PopulateResult, PopulateTarget, populate
from headerkit._target import detect_process_triple, resolve_target
from headerkit.backends import (
    LibclangUnavailableError,
    get_backend,
    is_backend_available,
    list_backends,
)
from headerkit.install_libclang import auto_install
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
    "LibclangUnavailableError",
    # Writer Protocol
    "WriterBackend",
    # Writer API
    "get_default_writer",
    "get_writer",
    "get_writer_info",
    "is_writer_available",
    "list_writers",
    "register_writer",
    # Generate API
    "generate",
    "generate_all",
    "GenerateResult",
    # IR JSON API
    "json_to_header",
    # Populate API
    "populate",
    "PopulateResult",
    "PopulateTarget",
    # Install API
    "auto_install",
    # Target detection API
    "detect_process_triple",
    "resolve_target",
]
