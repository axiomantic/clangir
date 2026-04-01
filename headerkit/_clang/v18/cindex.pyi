from __future__ import annotations

import ctypes
from ctypes import Array, CDLL, Structure, c_char_p, c_int, c_uint, c_void_p
from typing import Any, Callable, ClassVar, Generic, Iterator, Sequence, TypeVar

import os

_TInstance = TypeVar('_TInstance')
_TResult = TypeVar('_TResult')

class c_interop_string(c_char_p):
    def __init__(self, p: str | bytes | None = None) -> None: ...
    def __str__(self) -> str: ...
    @property
    def value(self) -> str | None: ...  # type: ignore[override]
    @classmethod
    def from_param(cls, param: str | bytes | None) -> c_interop_string: ...
    @staticmethod
    def to_python_string(x: c_interop_string, *args: Any) -> str | None: ...

def b(x: str | bytes) -> bytes: ...

callbacks: dict[str, Any]

c_object_p: type[ctypes._Pointer[Any]]

class TranslationUnitLoadError(Exception): ...

class TranslationUnitSaveError(Exception):
    ERROR_UNKNOWN: ClassVar[int]
    ERROR_TRANSLATION_ERRORS: ClassVar[int]
    ERROR_INVALID_TU: ClassVar[int]
    save_error: int
    def __init__(self, enumeration: int, message: str) -> None: ...

class CachedProperty(Generic[_TInstance, _TResult]):
    wrapped: Callable[[_TInstance], _TResult]
    def __init__(self, wrapped: Callable[[_TInstance], _TResult]) -> None: ...
    def __get__(self, instance: _TInstance, instance_type: Any = None) -> _TResult: ...

class _CXString(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    spelling: Any
    free: Any
    def __del__(self) -> None: ...
    @staticmethod
    def from_result(res: _CXString, fn: Any = None, args: Any = None) -> str: ...

class SourceLocation(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    ptr_data: Any
    int_data: Any
    _data: tuple[File | None, int, int, int] | None
    def _get_instantiation(self) -> tuple[File | None, int, int, int]: ...
    @staticmethod
    def from_position(tu: TranslationUnit, file: File, line: int, column: int) -> SourceLocation: ...
    @staticmethod
    def from_offset(tu: TranslationUnit, file: File, offset: int) -> SourceLocation: ...
    @property
    def file(self) -> File | None: ...
    @property
    def line(self) -> int: ...
    @property
    def column(self) -> int: ...
    @property
    def offset(self) -> int: ...
    @property
    def is_in_system_header(self) -> bool: ...
    def __eq__(self, other: object, /) -> bool: ...
    def __ne__(self, other: object, /) -> bool: ...
    def __lt__(self, other: SourceLocation, /) -> bool: ...
    def __le__(self, other: SourceLocation, /) -> bool: ...
    def __repr__(self) -> str: ...

class SourceRange(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    ptr_data: Any
    begin_int_data: Any
    end_int_data: Any
    @staticmethod
    def from_locations(start: SourceLocation, end: SourceLocation) -> SourceRange: ...
    @property
    def start(self) -> SourceLocation: ...
    @property
    def end(self) -> SourceLocation: ...
    def __eq__(self, other: object, /) -> bool: ...
    def __ne__(self, other: object, /) -> bool: ...
    def __contains__(self, other: object) -> bool: ...
    def __repr__(self) -> str: ...

class BaseEnumeration:
    value: int
    _kinds: ClassVar[list[Any]]
    _name_map: ClassVar[dict[int, str] | None]
    def __init__(self, value: int) -> None: ...
    def __repr__(self) -> str: ...
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> BaseEnumeration: ...
    @property
    def name(self) -> str: ...

class TokenKind:
    value: int
    name: str
    _value_map: ClassVar[dict[int, TokenKind]]
    def __init__(self, value: int, name: str) -> None: ...
    def __repr__(self) -> str: ...
    @staticmethod
    def from_value(value: int) -> TokenKind: ...
    @staticmethod
    def register(value: int, name: str) -> TokenKind: ...
    PUNCTUATION: ClassVar[TokenKind]
    KEYWORD: ClassVar[TokenKind]
    IDENTIFIER: ClassVar[TokenKind]
    LITERAL: ClassVar[TokenKind]
    COMMENT: ClassVar[TokenKind]

class CursorKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    @staticmethod
    def get_all_kinds() -> list[CursorKind]: ...
    def is_declaration(self) -> bool: ...
    def is_reference(self) -> bool: ...
    def is_expression(self) -> bool: ...
    def is_statement(self) -> bool: ...
    def is_attribute(self) -> bool: ...
    def is_invalid(self) -> bool: ...
    def is_translation_unit(self) -> bool: ...
    def is_preprocessing(self) -> bool: ...
    def is_unexposed(self) -> bool: ...
    UNEXPOSED_DECL: ClassVar[CursorKind]
    STRUCT_DECL: ClassVar[CursorKind]
    UNION_DECL: ClassVar[CursorKind]
    CLASS_DECL: ClassVar[CursorKind]
    ENUM_DECL: ClassVar[CursorKind]
    FIELD_DECL: ClassVar[CursorKind]
    ENUM_CONSTANT_DECL: ClassVar[CursorKind]
    FUNCTION_DECL: ClassVar[CursorKind]
    VAR_DECL: ClassVar[CursorKind]
    PARM_DECL: ClassVar[CursorKind]
    OBJC_INTERFACE_DECL: ClassVar[CursorKind]
    OBJC_CATEGORY_DECL: ClassVar[CursorKind]
    OBJC_PROTOCOL_DECL: ClassVar[CursorKind]
    OBJC_PROPERTY_DECL: ClassVar[CursorKind]
    OBJC_IVAR_DECL: ClassVar[CursorKind]
    OBJC_INSTANCE_METHOD_DECL: ClassVar[CursorKind]
    OBJC_CLASS_METHOD_DECL: ClassVar[CursorKind]
    OBJC_IMPLEMENTATION_DECL: ClassVar[CursorKind]
    OBJC_CATEGORY_IMPL_DECL: ClassVar[CursorKind]
    TYPEDEF_DECL: ClassVar[CursorKind]
    CXX_METHOD: ClassVar[CursorKind]
    NAMESPACE: ClassVar[CursorKind]
    LINKAGE_SPEC: ClassVar[CursorKind]
    CONSTRUCTOR: ClassVar[CursorKind]
    DESTRUCTOR: ClassVar[CursorKind]
    CONVERSION_FUNCTION: ClassVar[CursorKind]
    TEMPLATE_TYPE_PARAMETER: ClassVar[CursorKind]
    TEMPLATE_NON_TYPE_PARAMETER: ClassVar[CursorKind]
    TEMPLATE_TEMPLATE_PARAMETER: ClassVar[CursorKind]
    FUNCTION_TEMPLATE: ClassVar[CursorKind]
    CLASS_TEMPLATE: ClassVar[CursorKind]
    CLASS_TEMPLATE_PARTIAL_SPECIALIZATION: ClassVar[CursorKind]
    NAMESPACE_ALIAS: ClassVar[CursorKind]
    USING_DIRECTIVE: ClassVar[CursorKind]
    USING_DECLARATION: ClassVar[CursorKind]
    TYPE_ALIAS_DECL: ClassVar[CursorKind]
    OBJC_SYNTHESIZE_DECL: ClassVar[CursorKind]
    OBJC_DYNAMIC_DECL: ClassVar[CursorKind]
    CXX_ACCESS_SPEC_DECL: ClassVar[CursorKind]
    OBJC_SUPER_CLASS_REF: ClassVar[CursorKind]
    OBJC_PROTOCOL_REF: ClassVar[CursorKind]
    OBJC_CLASS_REF: ClassVar[CursorKind]
    TYPE_REF: ClassVar[CursorKind]
    CXX_BASE_SPECIFIER: ClassVar[CursorKind]
    TEMPLATE_REF: ClassVar[CursorKind]
    NAMESPACE_REF: ClassVar[CursorKind]
    MEMBER_REF: ClassVar[CursorKind]
    LABEL_REF: ClassVar[CursorKind]
    OVERLOADED_DECL_REF: ClassVar[CursorKind]
    VARIABLE_REF: ClassVar[CursorKind]
    INVALID_FILE: ClassVar[CursorKind]
    NO_DECL_FOUND: ClassVar[CursorKind]
    NOT_IMPLEMENTED: ClassVar[CursorKind]
    INVALID_CODE: ClassVar[CursorKind]
    UNEXPOSED_EXPR: ClassVar[CursorKind]
    DECL_REF_EXPR: ClassVar[CursorKind]
    MEMBER_REF_EXPR: ClassVar[CursorKind]
    CALL_EXPR: ClassVar[CursorKind]
    OBJC_MESSAGE_EXPR: ClassVar[CursorKind]
    BLOCK_EXPR: ClassVar[CursorKind]
    INTEGER_LITERAL: ClassVar[CursorKind]
    FLOATING_LITERAL: ClassVar[CursorKind]
    IMAGINARY_LITERAL: ClassVar[CursorKind]
    STRING_LITERAL: ClassVar[CursorKind]
    CHARACTER_LITERAL: ClassVar[CursorKind]
    PAREN_EXPR: ClassVar[CursorKind]
    UNARY_OPERATOR: ClassVar[CursorKind]
    ARRAY_SUBSCRIPT_EXPR: ClassVar[CursorKind]
    BINARY_OPERATOR: ClassVar[CursorKind]
    COMPOUND_ASSIGNMENT_OPERATOR: ClassVar[CursorKind]
    CONDITIONAL_OPERATOR: ClassVar[CursorKind]
    CSTYLE_CAST_EXPR: ClassVar[CursorKind]
    COMPOUND_LITERAL_EXPR: ClassVar[CursorKind]
    INIT_LIST_EXPR: ClassVar[CursorKind]
    ADDR_LABEL_EXPR: ClassVar[CursorKind]
    StmtExpr: ClassVar[CursorKind]
    GENERIC_SELECTION_EXPR: ClassVar[CursorKind]
    GNU_NULL_EXPR: ClassVar[CursorKind]
    CXX_STATIC_CAST_EXPR: ClassVar[CursorKind]
    CXX_DYNAMIC_CAST_EXPR: ClassVar[CursorKind]
    CXX_REINTERPRET_CAST_EXPR: ClassVar[CursorKind]
    CXX_CONST_CAST_EXPR: ClassVar[CursorKind]
    CXX_FUNCTIONAL_CAST_EXPR: ClassVar[CursorKind]
    CXX_TYPEID_EXPR: ClassVar[CursorKind]
    CXX_BOOL_LITERAL_EXPR: ClassVar[CursorKind]
    CXX_NULL_PTR_LITERAL_EXPR: ClassVar[CursorKind]
    CXX_THIS_EXPR: ClassVar[CursorKind]
    CXX_THROW_EXPR: ClassVar[CursorKind]
    CXX_NEW_EXPR: ClassVar[CursorKind]
    CXX_DELETE_EXPR: ClassVar[CursorKind]
    CXX_UNARY_EXPR: ClassVar[CursorKind]
    OBJC_STRING_LITERAL: ClassVar[CursorKind]
    OBJC_ENCODE_EXPR: ClassVar[CursorKind]
    OBJC_SELECTOR_EXPR: ClassVar[CursorKind]
    OBJC_PROTOCOL_EXPR: ClassVar[CursorKind]
    OBJC_BRIDGE_CAST_EXPR: ClassVar[CursorKind]
    PACK_EXPANSION_EXPR: ClassVar[CursorKind]
    SIZE_OF_PACK_EXPR: ClassVar[CursorKind]
    LAMBDA_EXPR: ClassVar[CursorKind]
    OBJ_BOOL_LITERAL_EXPR: ClassVar[CursorKind]
    OBJ_SELF_EXPR: ClassVar[CursorKind]
    OMP_ARRAY_SECTION_EXPR: ClassVar[CursorKind]
    OBJC_AVAILABILITY_CHECK_EXPR: ClassVar[CursorKind]
    UNEXPOSED_STMT: ClassVar[CursorKind]
    LABEL_STMT: ClassVar[CursorKind]
    COMPOUND_STMT: ClassVar[CursorKind]
    CASE_STMT: ClassVar[CursorKind]
    DEFAULT_STMT: ClassVar[CursorKind]
    IF_STMT: ClassVar[CursorKind]
    SWITCH_STMT: ClassVar[CursorKind]
    WHILE_STMT: ClassVar[CursorKind]
    DO_STMT: ClassVar[CursorKind]
    FOR_STMT: ClassVar[CursorKind]
    GOTO_STMT: ClassVar[CursorKind]
    INDIRECT_GOTO_STMT: ClassVar[CursorKind]
    CONTINUE_STMT: ClassVar[CursorKind]
    BREAK_STMT: ClassVar[CursorKind]
    RETURN_STMT: ClassVar[CursorKind]
    ASM_STMT: ClassVar[CursorKind]
    OBJC_AT_TRY_STMT: ClassVar[CursorKind]
    OBJC_AT_CATCH_STMT: ClassVar[CursorKind]
    OBJC_AT_FINALLY_STMT: ClassVar[CursorKind]
    OBJC_AT_THROW_STMT: ClassVar[CursorKind]
    OBJC_AT_SYNCHRONIZED_STMT: ClassVar[CursorKind]
    OBJC_AUTORELEASE_POOL_STMT: ClassVar[CursorKind]
    OBJC_FOR_COLLECTION_STMT: ClassVar[CursorKind]
    CXX_CATCH_STMT: ClassVar[CursorKind]
    CXX_TRY_STMT: ClassVar[CursorKind]
    CXX_FOR_RANGE_STMT: ClassVar[CursorKind]
    SEH_TRY_STMT: ClassVar[CursorKind]
    SEH_EXCEPT_STMT: ClassVar[CursorKind]
    SEH_FINALLY_STMT: ClassVar[CursorKind]
    MS_ASM_STMT: ClassVar[CursorKind]
    NULL_STMT: ClassVar[CursorKind]
    DECL_STMT: ClassVar[CursorKind]
    OMP_PARALLEL_DIRECTIVE: ClassVar[CursorKind]
    OMP_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_FOR_DIRECTIVE: ClassVar[CursorKind]
    OMP_SECTIONS_DIRECTIVE: ClassVar[CursorKind]
    OMP_SECTION_DIRECTIVE: ClassVar[CursorKind]
    OMP_SINGLE_DIRECTIVE: ClassVar[CursorKind]
    OMP_PARALLEL_FOR_DIRECTIVE: ClassVar[CursorKind]
    OMP_PARALLEL_SECTIONS_DIRECTIVE: ClassVar[CursorKind]
    OMP_TASK_DIRECTIVE: ClassVar[CursorKind]
    OMP_MASTER_DIRECTIVE: ClassVar[CursorKind]
    OMP_CRITICAL_DIRECTIVE: ClassVar[CursorKind]
    OMP_TASKYIELD_DIRECTIVE: ClassVar[CursorKind]
    OMP_BARRIER_DIRECTIVE: ClassVar[CursorKind]
    OMP_TASKWAIT_DIRECTIVE: ClassVar[CursorKind]
    OMP_FLUSH_DIRECTIVE: ClassVar[CursorKind]
    SEH_LEAVE_STMT: ClassVar[CursorKind]
    OMP_ORDERED_DIRECTIVE: ClassVar[CursorKind]
    OMP_ATOMIC_DIRECTIVE: ClassVar[CursorKind]
    OMP_FOR_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_PARALLELFORSIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_DIRECTIVE: ClassVar[CursorKind]
    OMP_TEAMS_DIRECTIVE: ClassVar[CursorKind]
    OMP_TASKGROUP_DIRECTIVE: ClassVar[CursorKind]
    OMP_CANCELLATION_POINT_DIRECTIVE: ClassVar[CursorKind]
    OMP_CANCEL_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_DATA_DIRECTIVE: ClassVar[CursorKind]
    OMP_TASK_LOOP_DIRECTIVE: ClassVar[CursorKind]
    OMP_TASK_LOOP_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_DISTRIBUTE_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_ENTER_DATA_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_EXIT_DATA_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_PARALLEL_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_PARALLELFOR_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_UPDATE_DIRECTIVE: ClassVar[CursorKind]
    OMP_DISTRIBUTE_PARALLELFOR_DIRECTIVE: ClassVar[CursorKind]
    OMP_DISTRIBUTE_PARALLEL_FOR_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_DISTRIBUTE_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_PARALLEL_FOR_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_TARGET_SIMD_DIRECTIVE: ClassVar[CursorKind]
    OMP_TEAMS_DISTRIBUTE_DIRECTIVE: ClassVar[CursorKind]
    TRANSLATION_UNIT: ClassVar[CursorKind]
    UNEXPOSED_ATTR: ClassVar[CursorKind]
    IB_ACTION_ATTR: ClassVar[CursorKind]
    IB_OUTLET_ATTR: ClassVar[CursorKind]
    IB_OUTLET_COLLECTION_ATTR: ClassVar[CursorKind]
    CXX_FINAL_ATTR: ClassVar[CursorKind]
    CXX_OVERRIDE_ATTR: ClassVar[CursorKind]
    ANNOTATE_ATTR: ClassVar[CursorKind]
    ASM_LABEL_ATTR: ClassVar[CursorKind]
    PACKED_ATTR: ClassVar[CursorKind]
    PURE_ATTR: ClassVar[CursorKind]
    CONST_ATTR: ClassVar[CursorKind]
    NODUPLICATE_ATTR: ClassVar[CursorKind]
    CUDACONSTANT_ATTR: ClassVar[CursorKind]
    CUDADEVICE_ATTR: ClassVar[CursorKind]
    CUDAGLOBAL_ATTR: ClassVar[CursorKind]
    CUDAHOST_ATTR: ClassVar[CursorKind]
    CUDASHARED_ATTR: ClassVar[CursorKind]
    VISIBILITY_ATTR: ClassVar[CursorKind]
    DLLEXPORT_ATTR: ClassVar[CursorKind]
    DLLIMPORT_ATTR: ClassVar[CursorKind]
    CONVERGENT_ATTR: ClassVar[CursorKind]
    WARN_UNUSED_ATTR: ClassVar[CursorKind]
    WARN_UNUSED_RESULT_ATTR: ClassVar[CursorKind]
    ALIGNED_ATTR: ClassVar[CursorKind]
    PREPROCESSING_DIRECTIVE: ClassVar[CursorKind]
    MACRO_DEFINITION: ClassVar[CursorKind]
    MACRO_INSTANTIATION: ClassVar[CursorKind]
    INCLUSION_DIRECTIVE: ClassVar[CursorKind]
    MODULE_IMPORT_DECL: ClassVar[CursorKind]
    TYPE_ALIAS_TEMPLATE_DECL: ClassVar[CursorKind]
    STATIC_ASSERT: ClassVar[CursorKind]
    FRIEND_DECL: ClassVar[CursorKind]
    CONCEPT_DECL: ClassVar[CursorKind]
    OVERLOAD_CANDIDATE: ClassVar[CursorKind]

class TypeKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    @property
    def spelling(self) -> str: ...
    INVALID: ClassVar[TypeKind]
    UNEXPOSED: ClassVar[TypeKind]
    VOID: ClassVar[TypeKind]
    BOOL: ClassVar[TypeKind]
    CHAR_U: ClassVar[TypeKind]
    UCHAR: ClassVar[TypeKind]
    CHAR16: ClassVar[TypeKind]
    CHAR32: ClassVar[TypeKind]
    USHORT: ClassVar[TypeKind]
    UINT: ClassVar[TypeKind]
    ULONG: ClassVar[TypeKind]
    ULONGLONG: ClassVar[TypeKind]
    UINT128: ClassVar[TypeKind]
    CHAR_S: ClassVar[TypeKind]
    SCHAR: ClassVar[TypeKind]
    WCHAR: ClassVar[TypeKind]
    SHORT: ClassVar[TypeKind]
    INT: ClassVar[TypeKind]
    LONG: ClassVar[TypeKind]
    LONGLONG: ClassVar[TypeKind]
    INT128: ClassVar[TypeKind]
    FLOAT: ClassVar[TypeKind]
    DOUBLE: ClassVar[TypeKind]
    LONGDOUBLE: ClassVar[TypeKind]
    NULLPTR: ClassVar[TypeKind]
    OVERLOAD: ClassVar[TypeKind]
    DEPENDENT: ClassVar[TypeKind]
    OBJCID: ClassVar[TypeKind]
    OBJCCLASS: ClassVar[TypeKind]
    OBJCSEL: ClassVar[TypeKind]
    FLOAT128: ClassVar[TypeKind]
    HALF: ClassVar[TypeKind]
    IBM128: ClassVar[TypeKind]
    COMPLEX: ClassVar[TypeKind]
    POINTER: ClassVar[TypeKind]
    BLOCKPOINTER: ClassVar[TypeKind]
    LVALUEREFERENCE: ClassVar[TypeKind]
    RVALUEREFERENCE: ClassVar[TypeKind]
    RECORD: ClassVar[TypeKind]
    ENUM: ClassVar[TypeKind]
    TYPEDEF: ClassVar[TypeKind]
    OBJCINTERFACE: ClassVar[TypeKind]
    OBJCOBJECTPOINTER: ClassVar[TypeKind]
    FUNCTIONNOPROTO: ClassVar[TypeKind]
    FUNCTIONPROTO: ClassVar[TypeKind]
    CONSTANTARRAY: ClassVar[TypeKind]
    VECTOR: ClassVar[TypeKind]
    INCOMPLETEARRAY: ClassVar[TypeKind]
    VARIABLEARRAY: ClassVar[TypeKind]
    DEPENDENTSIZEDARRAY: ClassVar[TypeKind]
    MEMBERPOINTER: ClassVar[TypeKind]
    AUTO: ClassVar[TypeKind]
    ELABORATED: ClassVar[TypeKind]
    PIPE: ClassVar[TypeKind]
    OCLIMAGE1DRO: ClassVar[TypeKind]
    OCLIMAGE1DARRAYRO: ClassVar[TypeKind]
    OCLIMAGE1DBUFFERRO: ClassVar[TypeKind]
    OCLIMAGE2DRO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYRO: ClassVar[TypeKind]
    OCLIMAGE2DDEPTHRO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYDEPTHRO: ClassVar[TypeKind]
    OCLIMAGE2DMSAARO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYMSAARO: ClassVar[TypeKind]
    OCLIMAGE2DMSAADEPTHRO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYMSAADEPTHRO: ClassVar[TypeKind]
    OCLIMAGE3DRO: ClassVar[TypeKind]
    OCLIMAGE1DWO: ClassVar[TypeKind]
    OCLIMAGE1DARRAYWO: ClassVar[TypeKind]
    OCLIMAGE1DBUFFERWO: ClassVar[TypeKind]
    OCLIMAGE2DWO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYWO: ClassVar[TypeKind]
    OCLIMAGE2DDEPTHWO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYDEPTHWO: ClassVar[TypeKind]
    OCLIMAGE2DMSAAWO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYMSAAWO: ClassVar[TypeKind]
    OCLIMAGE2DMSAADEPTHWO: ClassVar[TypeKind]
    OCLIMAGE2DARRAYMSAADEPTHWO: ClassVar[TypeKind]
    OCLIMAGE3DWO: ClassVar[TypeKind]
    OCLIMAGE1DRW: ClassVar[TypeKind]
    OCLIMAGE1DARRAYRW: ClassVar[TypeKind]
    OCLIMAGE1DBUFFERRW: ClassVar[TypeKind]
    OCLIMAGE2DRW: ClassVar[TypeKind]
    OCLIMAGE2DARRAYRW: ClassVar[TypeKind]
    OCLIMAGE2DDEPTHRW: ClassVar[TypeKind]
    OCLIMAGE2DARRAYDEPTHRW: ClassVar[TypeKind]
    OCLIMAGE2DMSAARW: ClassVar[TypeKind]
    OCLIMAGE2DARRAYMSAARW: ClassVar[TypeKind]
    OCLIMAGE2DMSAADEPTHRW: ClassVar[TypeKind]
    OCLIMAGE2DARRAYMSAADEPTHRW: ClassVar[TypeKind]
    OCLIMAGE3DRW: ClassVar[TypeKind]
    OCLSAMPLER: ClassVar[TypeKind]
    OCLEVENT: ClassVar[TypeKind]
    OCLQUEUE: ClassVar[TypeKind]
    OCLRESERVEID: ClassVar[TypeKind]
    EXTVECTOR: ClassVar[TypeKind]
    ATOMIC: ClassVar[TypeKind]

class AvailabilityKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    AVAILABLE: ClassVar[AvailabilityKind]
    DEPRECATED: ClassVar[AvailabilityKind]
    NOT_AVAILABLE: ClassVar[AvailabilityKind]
    NOT_ACCESSIBLE: ClassVar[AvailabilityKind]

class AccessSpecifier(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    INVALID: ClassVar[AccessSpecifier]
    PUBLIC: ClassVar[AccessSpecifier]
    PROTECTED: ClassVar[AccessSpecifier]
    PRIVATE: ClassVar[AccessSpecifier]
    NONE: ClassVar[AccessSpecifier]

class LinkageKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    INVALID: ClassVar[LinkageKind]
    NO_LINKAGE: ClassVar[LinkageKind]
    INTERNAL: ClassVar[LinkageKind]
    UNIQUE_EXTERNAL: ClassVar[LinkageKind]
    EXTERNAL: ClassVar[LinkageKind]

class TLSKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    NONE: ClassVar[TLSKind]
    DYNAMIC: ClassVar[TLSKind]
    STATIC: ClassVar[TLSKind]

class StorageClass(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    @staticmethod
    def from_id(id: int) -> StorageClass: ...
    INVALID: ClassVar[StorageClass]
    NONE: ClassVar[StorageClass]
    EXTERN: ClassVar[StorageClass]
    STATIC: ClassVar[StorageClass]
    PRIVATEEXTERN: ClassVar[StorageClass]
    OPENCLWORKGROUPLOCAL: ClassVar[StorageClass]
    AUTO: ClassVar[StorageClass]
    REGISTER: ClassVar[StorageClass]

class ExceptionSpecificationKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    NONE: ClassVar[ExceptionSpecificationKind]
    DYNAMIC_NONE: ClassVar[ExceptionSpecificationKind]
    DYNAMIC: ClassVar[ExceptionSpecificationKind]
    MS_ANY: ClassVar[ExceptionSpecificationKind]
    BASIC_NOEXCEPT: ClassVar[ExceptionSpecificationKind]
    COMPUTED_NOEXCEPT: ClassVar[ExceptionSpecificationKind]
    UNEVALUATED: ClassVar[ExceptionSpecificationKind]
    UNINSTANTIATED: ClassVar[ExceptionSpecificationKind]
    UNPARSED: ClassVar[ExceptionSpecificationKind]

class TemplateArgumentKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    NULL: ClassVar[TemplateArgumentKind]
    TYPE: ClassVar[TemplateArgumentKind]
    DECLARATION: ClassVar[TemplateArgumentKind]
    NULLPTR: ClassVar[TemplateArgumentKind]
    INTEGRAL: ClassVar[TemplateArgumentKind]
    TEMPLATE: ClassVar[TemplateArgumentKind]
    TEMPLATE_EXPANSION: ClassVar[TemplateArgumentKind]
    EXPRESSION: ClassVar[TemplateArgumentKind]
    PACK: ClassVar[TemplateArgumentKind]
    INVALID: ClassVar[TemplateArgumentKind]

class RefQualifierKind(BaseEnumeration):
    _kinds: ClassVar[list[Any]]
    NONE: ClassVar[RefQualifierKind]
    LVALUE: ClassVar[RefQualifierKind]
    RVALUE: ClassVar[RefQualifierKind]

class Diagnostic:
    Ignored: ClassVar[int]
    Note: ClassVar[int]
    Warning: ClassVar[int]
    Error: ClassVar[int]
    Fatal: ClassVar[int]
    DisplaySourceLocation: ClassVar[int]
    DisplayColumn: ClassVar[int]
    DisplaySourceRanges: ClassVar[int]
    DisplayOption: ClassVar[int]
    DisplayCategoryId: ClassVar[int]
    DisplayCategoryName: ClassVar[int]
    _FormatOptionsMask: ClassVar[int]
    ptr: Any
    def __init__(self, ptr: Any) -> None: ...
    def __del__(self) -> None: ...
    @property
    def severity(self) -> int: ...
    @property
    def location(self) -> SourceLocation: ...
    @property
    def spelling(self) -> str: ...
    @property
    def ranges(self) -> Sequence[SourceRange]: ...
    @property
    def fixits(self) -> Sequence[FixIt]: ...
    @property
    def children(self) -> Sequence[Diagnostic]: ...
    @property
    def category_number(self) -> int: ...
    @property
    def category_name(self) -> str: ...
    @property
    def option(self) -> str: ...
    @property
    def disable_option(self) -> str: ...
    def format(self, options: int | None = None) -> str: ...
    def __repr__(self) -> str: ...
    def __str__(self) -> str: ...
    def from_param(self) -> Any: ...

class FixIt:
    range: SourceRange
    value: str
    def __init__(self, range: Any, value: Any) -> None: ...
    def __repr__(self) -> str: ...

class TokenGroup:
    def __init__(self, tu: Any, memory: Any, count: Any) -> None: ...
    def __del__(self) -> None: ...
    @staticmethod
    def get_tokens(tu: Any, extent: Any) -> Iterator[Token]: ...

class Cursor(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    _tu: TranslationUnit
    data: Any
    xdata: Any
    @staticmethod
    def from_location(tu: TranslationUnit, location: SourceLocation) -> Cursor | None: ...
    def __eq__(self, other: object, /) -> bool: ...
    def __ne__(self, other: object, /) -> bool: ...
    __hash__: ClassVar[None]  # type: ignore[assignment]
    def is_definition(self) -> bool: ...
    def is_const_method(self) -> bool: ...
    def is_converting_constructor(self) -> bool: ...
    def is_copy_constructor(self) -> bool: ...
    def is_default_constructor(self) -> bool: ...
    def is_move_constructor(self) -> bool: ...
    def is_default_method(self) -> bool: ...
    def is_deleted_method(self) -> bool: ...
    def is_copy_assignment_operator_method(self) -> bool: ...
    def is_move_assignment_operator_method(self) -> bool: ...
    def is_explicit_method(self) -> bool: ...
    def is_mutable_field(self) -> bool: ...
    def is_pure_virtual_method(self) -> bool: ...
    def is_static_method(self) -> bool: ...
    def is_virtual_method(self) -> bool: ...
    def is_abstract_record(self) -> bool: ...
    def is_scoped_enum(self) -> bool: ...
    def get_definition(self) -> Cursor | None: ...
    def get_usr(self) -> str: ...
    def get_included_file(self) -> File: ...
    @property
    def kind(self) -> CursorKind: ...
    @property
    def spelling(self) -> str: ...
    @property
    def displayname(self) -> str: ...
    @property
    def mangled_name(self) -> str: ...
    @property
    def location(self) -> SourceLocation: ...
    @property
    def linkage(self) -> LinkageKind: ...
    @property
    def tls_kind(self) -> TLSKind: ...
    @property
    def extent(self) -> SourceRange: ...
    @property
    def storage_class(self) -> StorageClass: ...
    @property
    def availability(self) -> AvailabilityKind: ...
    @property
    def access_specifier(self) -> AccessSpecifier: ...
    @property
    def type(self) -> Type: ...
    @property
    def canonical(self) -> Cursor: ...
    @property
    def result_type(self) -> Type: ...
    @property
    def exception_specification_kind(self) -> ExceptionSpecificationKind: ...
    @property
    def underlying_typedef_type(self) -> Type: ...
    @property
    def enum_type(self) -> Type: ...
    @property
    def enum_value(self) -> int: ...
    @property
    def objc_type_encoding(self) -> str: ...
    @property
    def hash(self) -> int: ...
    @property
    def semantic_parent(self) -> Cursor | None: ...
    @property
    def lexical_parent(self) -> Cursor | None: ...
    @property
    def translation_unit(self) -> TranslationUnit: ...
    @property
    def referenced(self) -> Cursor | None: ...
    @property
    def brief_comment(self) -> str: ...
    @property
    def raw_comment(self) -> str: ...
    def get_arguments(self) -> Iterator[Cursor | None]: ...
    def get_num_template_arguments(self) -> int: ...
    def get_template_argument_kind(self, num: int) -> TemplateArgumentKind: ...
    def get_template_argument_type(self, num: int) -> Type: ...
    def get_template_argument_value(self, num: int) -> int: ...
    def get_template_argument_unsigned_value(self, num: int) -> int: ...
    def get_children(self) -> Iterator[Cursor]: ...
    def walk_preorder(self) -> Iterator[Cursor]: ...
    def get_tokens(self) -> Iterator[Token]: ...
    def get_field_offsetof(self) -> int: ...
    def is_anonymous(self) -> bool: ...
    def is_bitfield(self) -> bool: ...
    def get_bitfield_width(self) -> int: ...
    @staticmethod
    def from_result(res: Cursor, fn: Any, args: Any) -> Cursor | None: ...
    @staticmethod
    def from_cursor_result(res: Cursor, fn: Any, args: Any) -> Cursor | None: ...

class Type(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    _tu: TranslationUnit
    data: Any
    @property
    def kind(self) -> TypeKind: ...
    def argument_types(self) -> Sequence[Type]: ...
    @property
    def element_type(self) -> Type: ...
    @property
    def element_count(self) -> int: ...
    @property
    def translation_unit(self) -> TranslationUnit: ...
    @staticmethod
    def from_result(res: Type, fn: Any, args: Any) -> Type: ...
    def get_num_template_arguments(self) -> int: ...
    def get_template_argument_type(self, num: int) -> Type: ...
    def get_canonical(self) -> Type: ...
    def is_const_qualified(self) -> bool: ...
    def is_volatile_qualified(self) -> bool: ...
    def is_restrict_qualified(self) -> bool: ...
    def is_function_variadic(self) -> bool: ...
    def get_address_space(self) -> int: ...
    def get_typedef_name(self) -> str: ...
    def is_pod(self) -> bool: ...
    def get_pointee(self) -> Type: ...
    def get_result(self) -> Type: ...
    def get_array_element_type(self) -> Type: ...
    def get_class_type(self) -> Type: ...
    def get_named_type(self) -> Type: ...
    def get_declaration(self) -> Cursor: ...
    def get_array_size(self) -> int: ...
    def get_align(self) -> int: ...
    def get_size(self) -> int: ...
    def get_offset(self, fieldname: str) -> int: ...
    def get_ref_qualifier(self) -> RefQualifierKind: ...
    def get_fields(self) -> Iterator[Cursor]: ...
    def get_exception_specification_kind(self) -> ExceptionSpecificationKind: ...
    @property
    def spelling(self) -> str: ...
    def __eq__(self, other: object, /) -> bool: ...
    def __ne__(self, other: object, /) -> bool: ...

class FileInclusion:
    source: File | None
    include: File
    location: SourceLocation
    depth: int
    def __init__(self, src: Any, tgt: Any, loc: Any, depth: Any) -> None: ...
    @property
    def is_input_file(self) -> bool: ...

class ClangObject:
    obj: Any
    _as_parameter_: Any
    def __init__(self, obj: Any) -> None: ...
    def from_param(self) -> Any: ...

class CompilationDatabase(ClangObject):
    def __del__(self) -> None: ...
    @staticmethod
    def from_result(res: Any, fn: Any, args: Any) -> CompilationDatabase: ...
    @staticmethod
    def fromDirectory(buildDir: Any) -> CompilationDatabase: ...
    def getCompileCommands(self, filename: Any) -> CompileCommands | None: ...
    def getAllCompileCommands(self) -> CompileCommands | None: ...

class CompileCommands:
    ccmds: Any
    def __init__(self, ccmds: Any) -> None: ...
    def __del__(self) -> None: ...
    def __len__(self) -> int: ...
    def __getitem__(self, i: Any) -> CompileCommand: ...
    @staticmethod
    def from_result(res: Any, fn: Any, args: Any) -> CompileCommands | None: ...

class CompileCommand:
    cmd: Any
    ccmds: Any
    def __init__(self, cmd: Any, ccmds: Any) -> None: ...
    @property
    def directory(self) -> str: ...
    @property
    def filename(self) -> str: ...
    @property
    def arguments(self) -> Iterator[str]: ...

class Token(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    int_data: Any
    ptr_data: Any
    @property
    def spelling(self) -> str: ...
    @property
    def kind(self) -> TokenKind: ...
    @property
    def location(self) -> SourceLocation: ...
    @property
    def extent(self) -> SourceRange: ...
    @property
    def cursor(self) -> Cursor: ...

class File(ClangObject):
    @staticmethod
    def from_name(translation_unit: Any, file_name: Any) -> File: ...
    @property
    def name(self) -> str: ...
    @property
    def time(self) -> int: ...
    def __str__(self) -> str: ...
    def __repr__(self) -> str: ...
    def __eq__(self, other: Any, /) -> bool: ...
    def __ne__(self, other: Any, /) -> bool: ...
    @staticmethod
    def from_result(res: Any, fn: Any, args: Any) -> File: ...

class Index(ClangObject):
    @staticmethod
    def create(excludeDecls: bool = False) -> Index: ...
    def __del__(self) -> None: ...
    def read(self, path: Any) -> TranslationUnit: ...
    def parse(self, path: Any, args: Any = None, unsaved_files: Any = None, options: int = 0) -> TranslationUnit: ...

class TranslationUnit(ClangObject):
    PARSE_NONE: ClassVar[int]
    PARSE_DETAILED_PROCESSING_RECORD: ClassVar[int]
    PARSE_INCOMPLETE: ClassVar[int]
    PARSE_PRECOMPILED_PREAMBLE: ClassVar[int]
    PARSE_CACHE_COMPLETION_RESULTS: ClassVar[int]
    PARSE_SKIP_FUNCTION_BODIES: ClassVar[int]
    PARSE_INCLUDE_BRIEF_COMMENTS_IN_CODE_COMPLETION: ClassVar[int]
    index: Index
    def __init__(self, ptr: Any, index: Any) -> None: ...
    def __del__(self) -> None: ...
    @classmethod
    def from_source(cls, filename: Any, args: Any = None, unsaved_files: Any = None, options: int = 0, index: Any = None) -> TranslationUnit: ...
    @classmethod
    def from_ast_file(cls, filename: Any, index: Any = None) -> TranslationUnit: ...
    @property
    def cursor(self) -> Cursor: ...
    @property
    def spelling(self) -> str: ...
    def get_includes(self) -> Iterator[FileInclusion]: ...
    def get_file(self, filename: Any) -> File: ...
    def get_location(self, filename: Any, position: Any) -> SourceLocation: ...
    def get_extent(self, filename: Any, locations: Any) -> SourceRange: ...
    @property
    def diagnostics(self) -> Sequence[Diagnostic]: ...
    def reparse(self, unsaved_files: Any = None, options: int = 0) -> None: ...
    def save(self, filename: Any) -> None: ...
    def codeComplete(self, path: Any, line: int, column: int, unsaved_files: Any = None, include_macros: bool = False, include_code_patterns: bool = False, include_brief_comments: bool = False) -> CodeCompletionResults | None: ...
    def get_tokens(self, locations: Any = None, extent: Any = None) -> Iterator[Token] | None: ...

class CodeCompletionResults(ClangObject):
    ptr: Any
    _as_parameter_: Any
    def __init__(self, ptr: Any) -> None: ...
    def from_param(self) -> Any: ...
    def __del__(self) -> None: ...
    @property
    def results(self) -> Any: ...
    @property
    def diagnostics(self) -> Sequence[Diagnostic]: ...

class Config:
    compatibility_check: ClassVar[bool]
    loaded: ClassVar[bool]
    library_path: ClassVar[str | None]
    library_file: ClassVar[str | None]
    @staticmethod
    def set_library_path(path: str | os.PathLike[str]) -> None: ...
    @staticmethod
    def set_library_file(filename: str | os.PathLike[str]) -> None: ...
    @staticmethod
    def set_compatibility_check(check_status: bool) -> None: ...
    def get_filename(self) -> str: ...
    def get_cindex_library(self) -> CDLL: ...
    def function_exists(self, name: str) -> bool: ...
    @property
    def lib(self) -> Any: ...

conf: Config

__all__ = [
    "AvailabilityKind",
    "CodeCompletionResults",
    "CompilationDatabase",
    "CompileCommand",
    "CompileCommands",
    "Config",
    "Cursor",
    "CursorKind",
    "Diagnostic",
    "File",
    "FixIt",
    "Index",
    "LinkageKind",
    "SourceLocation",
    "SourceRange",
    "TLSKind",
    "Token",
    "TokenKind",
    "TranslationUnit",
    "TranslationUnitLoadError",
    "Type",
    "TypeKind",
]
