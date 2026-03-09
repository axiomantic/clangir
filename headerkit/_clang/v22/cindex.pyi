from __future__ import annotations

import ctypes
from ctypes import Array, CDLL, Structure, c_char_p, c_int, c_uint, c_void_p
from enum import Enum
from typing import Any, Callable, ClassVar, Generic, Iterator, Sequence, TypeVar

import os

# Type aliases used internally
TInstance = TypeVar("TInstance")
TResult = TypeVar("TResult")

class c_interop_string(c_char_p):
    def __init__(self, p: str | bytes | None = None) -> None: ...
    def __str__(self) -> str: ...
    @property
    def value(self) -> str | None: ...  # type: ignore[override]
    @classmethod
    def from_param(cls, param: str | bytes | None) -> c_interop_string: ...
    @staticmethod
    def to_python_string(x: c_interop_string) -> str | None: ...

def b(x: str | bytes) -> bytes: ...

c_object_p: type[ctypes._Pointer[Any]]

class TranslationUnitLoadError(Exception): ...

class TranslationUnitSaveError(Exception):
    ERROR_UNKNOWN: ClassVar[int]
    ERROR_TRANSLATION_ERRORS: ClassVar[int]
    ERROR_INVALID_TU: ClassVar[int]
    save_error: int
    def __init__(self, enumeration: int, message: str) -> None: ...

class CachedProperty(Generic[TInstance, TResult]):
    wrapped: Callable[[TInstance], TResult]
    def __init__(self, wrapped: Callable[[TInstance], TResult]) -> None: ...
    def __get__(self, instance: TInstance, instance_type: Any = None) -> TResult: ...

class _CXString(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    spelling: Any
    free: Any
    def __del__(self) -> None: ...
    @staticmethod
    def from_result(res: _CXString) -> str: ...

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
    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def __lt__(self, other: SourceLocation) -> bool: ...
    def __le__(self, other: SourceLocation) -> bool: ...
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
    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def __contains__(self, other: object) -> bool: ...
    def __repr__(self) -> str: ...

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

class TokenKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> TokenKind: ...
    @classmethod
    def from_value(cls, value: int) -> TokenKind: ...
    PUNCTUATION = 0
    KEYWORD = 1
    IDENTIFIER = 2
    LITERAL = 3
    COMMENT = 4

class CursorKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> CursorKind: ...
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
    UNEXPOSED_DECL = 1
    STRUCT_DECL = 2
    UNION_DECL = 3
    CLASS_DECL = 4
    ENUM_DECL = 5
    FIELD_DECL = 6
    ENUM_CONSTANT_DECL = 7
    FUNCTION_DECL = 8
    VAR_DECL = 9
    PARM_DECL = 10
    OBJC_INTERFACE_DECL = 11
    OBJC_CATEGORY_DECL = 12
    OBJC_PROTOCOL_DECL = 13
    OBJC_PROPERTY_DECL = 14
    OBJC_IVAR_DECL = 15
    OBJC_INSTANCE_METHOD_DECL = 16
    OBJC_CLASS_METHOD_DECL = 17
    OBJC_IMPLEMENTATION_DECL = 18
    OBJC_CATEGORY_IMPL_DECL = 19
    TYPEDEF_DECL = 20
    CXX_METHOD = 21
    NAMESPACE = 22
    LINKAGE_SPEC = 23
    CONSTRUCTOR = 24
    DESTRUCTOR = 25
    CONVERSION_FUNCTION = 26
    TEMPLATE_TYPE_PARAMETER = 27
    TEMPLATE_NON_TYPE_PARAMETER = 28
    TEMPLATE_TEMPLATE_PARAMETER = 29
    FUNCTION_TEMPLATE = 30
    CLASS_TEMPLATE = 31
    CLASS_TEMPLATE_PARTIAL_SPECIALIZATION = 32
    NAMESPACE_ALIAS = 33
    USING_DIRECTIVE = 34
    USING_DECLARATION = 35
    TYPE_ALIAS_DECL = 36
    OBJC_SYNTHESIZE_DECL = 37
    OBJC_DYNAMIC_DECL = 38
    CXX_ACCESS_SPEC_DECL = 39
    OBJC_SUPER_CLASS_REF = 40
    OBJC_PROTOCOL_REF = 41
    OBJC_CLASS_REF = 42
    TYPE_REF = 43
    CXX_BASE_SPECIFIER = 44
    TEMPLATE_REF = 45
    NAMESPACE_REF = 46
    MEMBER_REF = 47
    LABEL_REF = 48
    OVERLOADED_DECL_REF = 49
    VARIABLE_REF = 50
    INVALID_FILE = 70
    NO_DECL_FOUND = 71
    NOT_IMPLEMENTED = 72
    INVALID_CODE = 73
    UNEXPOSED_EXPR = 100
    DECL_REF_EXPR = 101
    MEMBER_REF_EXPR = 102
    CALL_EXPR = 103
    OBJC_MESSAGE_EXPR = 104
    BLOCK_EXPR = 105
    INTEGER_LITERAL = 106
    FLOATING_LITERAL = 107
    IMAGINARY_LITERAL = 108
    STRING_LITERAL = 109
    CHARACTER_LITERAL = 110
    PAREN_EXPR = 111
    UNARY_OPERATOR = 112
    ARRAY_SUBSCRIPT_EXPR = 113
    BINARY_OPERATOR = 114
    COMPOUND_ASSIGNMENT_OPERATOR = 115
    CONDITIONAL_OPERATOR = 116
    CSTYLE_CAST_EXPR = 117
    COMPOUND_LITERAL_EXPR = 118
    INIT_LIST_EXPR = 119
    ADDR_LABEL_EXPR = 120
    StmtExpr = 121
    GENERIC_SELECTION_EXPR = 122
    GNU_NULL_EXPR = 123
    CXX_STATIC_CAST_EXPR = 124
    CXX_DYNAMIC_CAST_EXPR = 125
    CXX_REINTERPRET_CAST_EXPR = 126
    CXX_CONST_CAST_EXPR = 127
    CXX_FUNCTIONAL_CAST_EXPR = 128
    CXX_TYPEID_EXPR = 129
    CXX_BOOL_LITERAL_EXPR = 130
    CXX_NULL_PTR_LITERAL_EXPR = 131
    CXX_THIS_EXPR = 132
    CXX_THROW_EXPR = 133
    CXX_NEW_EXPR = 134
    CXX_DELETE_EXPR = 135
    CXX_UNARY_EXPR = 136
    OBJC_STRING_LITERAL = 137
    OBJC_ENCODE_EXPR = 138
    OBJC_SELECTOR_EXPR = 139
    OBJC_PROTOCOL_EXPR = 140
    OBJC_BRIDGE_CAST_EXPR = 141
    PACK_EXPANSION_EXPR = 142
    SIZE_OF_PACK_EXPR = 143
    LAMBDA_EXPR = 144
    OBJ_BOOL_LITERAL_EXPR = 145
    OBJ_SELF_EXPR = 146
    OMP_ARRAY_SECTION_EXPR = 147
    OBJC_AVAILABILITY_CHECK_EXPR = 148
    FIXED_POINT_LITERAL = 149
    OMP_ARRAY_SHAPING_EXPR = 150
    OMP_ITERATOR_EXPR = 151
    CXX_ADDRSPACE_CAST_EXPR = 152
    CONCEPT_SPECIALIZATION_EXPR = 153
    REQUIRES_EXPR = 154
    CXX_PAREN_LIST_INIT_EXPR = 155
    PACK_INDEXING_EXPR = 156
    UNEXPOSED_STMT = 200
    LABEL_STMT = 201
    COMPOUND_STMT = 202
    CASE_STMT = 203
    DEFAULT_STMT = 204
    IF_STMT = 205
    SWITCH_STMT = 206
    WHILE_STMT = 207
    DO_STMT = 208
    FOR_STMT = 209
    GOTO_STMT = 210
    INDIRECT_GOTO_STMT = 211
    CONTINUE_STMT = 212
    BREAK_STMT = 213
    RETURN_STMT = 214
    ASM_STMT = 215
    OBJC_AT_TRY_STMT = 216
    OBJC_AT_CATCH_STMT = 217
    OBJC_AT_FINALLY_STMT = 218
    OBJC_AT_THROW_STMT = 219
    OBJC_AT_SYNCHRONIZED_STMT = 220
    OBJC_AUTORELEASE_POOL_STMT = 221
    OBJC_FOR_COLLECTION_STMT = 222
    CXX_CATCH_STMT = 223
    CXX_TRY_STMT = 224
    CXX_FOR_RANGE_STMT = 225
    SEH_TRY_STMT = 226
    SEH_EXCEPT_STMT = 227
    SEH_FINALLY_STMT = 228
    MS_ASM_STMT = 229
    NULL_STMT = 230
    DECL_STMT = 231
    OMP_PARALLEL_DIRECTIVE = 232
    OMP_SIMD_DIRECTIVE = 233
    OMP_FOR_DIRECTIVE = 234
    OMP_SECTIONS_DIRECTIVE = 235
    OMP_SECTION_DIRECTIVE = 236
    OMP_SINGLE_DIRECTIVE = 237
    OMP_PARALLEL_FOR_DIRECTIVE = 238
    OMP_PARALLEL_SECTIONS_DIRECTIVE = 239
    OMP_TASK_DIRECTIVE = 240
    OMP_MASTER_DIRECTIVE = 241
    OMP_CRITICAL_DIRECTIVE = 242
    OMP_TASKYIELD_DIRECTIVE = 243
    OMP_BARRIER_DIRECTIVE = 244
    OMP_TASKWAIT_DIRECTIVE = 245
    OMP_FLUSH_DIRECTIVE = 246
    SEH_LEAVE_STMT = 247
    OMP_ORDERED_DIRECTIVE = 248
    OMP_ATOMIC_DIRECTIVE = 249
    OMP_FOR_SIMD_DIRECTIVE = 250
    OMP_PARALLELFORSIMD_DIRECTIVE = 251
    OMP_TARGET_DIRECTIVE = 252
    OMP_TEAMS_DIRECTIVE = 253
    OMP_TASKGROUP_DIRECTIVE = 254
    OMP_CANCELLATION_POINT_DIRECTIVE = 255
    OMP_CANCEL_DIRECTIVE = 256
    OMP_TARGET_DATA_DIRECTIVE = 257
    OMP_TASK_LOOP_DIRECTIVE = 258
    OMP_TASK_LOOP_SIMD_DIRECTIVE = 259
    OMP_DISTRIBUTE_DIRECTIVE = 260
    OMP_TARGET_ENTER_DATA_DIRECTIVE = 261
    OMP_TARGET_EXIT_DATA_DIRECTIVE = 262
    OMP_TARGET_PARALLEL_DIRECTIVE = 263
    OMP_TARGET_PARALLELFOR_DIRECTIVE = 264
    OMP_TARGET_UPDATE_DIRECTIVE = 265
    OMP_DISTRIBUTE_PARALLELFOR_DIRECTIVE = 266
    OMP_DISTRIBUTE_PARALLEL_FOR_SIMD_DIRECTIVE = 267
    OMP_DISTRIBUTE_SIMD_DIRECTIVE = 268
    OMP_TARGET_PARALLEL_FOR_SIMD_DIRECTIVE = 269
    OMP_TARGET_SIMD_DIRECTIVE = 270
    OMP_TEAMS_DISTRIBUTE_DIRECTIVE = 271
    OMP_TEAMS_DISTRIBUTE_SIMD_DIRECTIVE = 272
    OMP_TEAMS_DISTRIBUTE_PARALLEL_FOR_SIMD_DIRECTIVE = 273
    OMP_TEAMS_DISTRIBUTE_PARALLEL_FOR_DIRECTIVE = 274
    OMP_TARGET_TEAMS_DIRECTIVE = 275
    OMP_TARGET_TEAMS_DISTRIBUTE_DIRECTIVE = 276
    OMP_TARGET_TEAMS_DISTRIBUTE_PARALLEL_FOR_DIRECTIVE = 277
    OMP_TARGET_TEAMS_DISTRIBUTE_PARALLEL_FOR_SIMD_DIRECTIVE = 278
    OMP_TARGET_TEAMS_DISTRIBUTE_SIMD_DIRECTIVE = 279
    BUILTIN_BIT_CAST_EXPR = 280
    OMP_MASTER_TASK_LOOP_DIRECTIVE = 281
    OMP_PARALLEL_MASTER_TASK_LOOP_DIRECTIVE = 282
    OMP_MASTER_TASK_LOOP_SIMD_DIRECTIVE = 283
    OMP_PARALLEL_MASTER_TASK_LOOP_SIMD_DIRECTIVE = 284
    OMP_PARALLEL_MASTER_DIRECTIVE = 285
    OMP_DEPOBJ_DIRECTIVE = 286
    OMP_SCAN_DIRECTIVE = 287
    OMP_TILE_DIRECTIVE = 288
    OMP_CANONICAL_LOOP = 289
    OMP_INTEROP_DIRECTIVE = 290
    OMP_DISPATCH_DIRECTIVE = 291
    OMP_MASKED_DIRECTIVE = 292
    OMP_UNROLL_DIRECTIVE = 293
    OMP_META_DIRECTIVE = 294
    OMP_GENERIC_LOOP_DIRECTIVE = 295
    OMP_TEAMS_GENERIC_LOOP_DIRECTIVE = 296
    OMP_TARGET_TEAMS_GENERIC_LOOP_DIRECTIVE = 297
    OMP_PARALLEL_GENERIC_LOOP_DIRECTIVE = 298
    OMP_TARGET_PARALLEL_GENERIC_LOOP_DIRECTIVE = 299
    OMP_PARALLEL_MASKED_DIRECTIVE = 300
    OMP_MASKED_TASK_LOOP_DIRECTIVE = 301
    OMP_MASKED_TASK_LOOP_SIMD_DIRECTIVE = 302
    OMP_PARALLEL_MASKED_TASK_LOOP_DIRECTIVE = 303
    OMP_PARALLEL_MASKED_TASK_LOOP_SIMD_DIRECTIVE = 304
    OMP_ERROR_DIRECTIVE = 305
    OMP_SCOPE_DIRECTIVE = 306
    OMP_REVERSE_DIRECTIVE = 307
    OMP_INTERCHANGE_DIRECTIVE = 308
    OMP_ASSUME_DIRECTIVE = 309
    OMP_STRIPE_DIRECTIVE = 310
    OMP_FUSE_DIRECTIVE = 311
    OPEN_ACC_COMPUTE_DIRECTIVE = 320
    OPEN_ACC_LOOP_CONSTRUCT = 321
    OPEN_ACC_COMBINED_CONSTRUCT = 322
    OPEN_ACC_DATA_CONSTRUCT = 323
    OPEN_ACC_ENTER_DATA_CONSTRUCT = 324
    OPEN_ACC_EXIT_DATA_CONSTRUCT = 325
    OPEN_ACC_HOST_DATA_CONSTRUCT = 326
    OPEN_ACC_WAIT_CONSTRUCT = 327
    OPEN_ACC_INIT_CONSTRUCT = 328
    OPEN_ACC_SHUTDOWN_CONSTRUCT = 329
    OPEN_ACC_SET_CONSTRUCT = 330
    OPEN_ACC_UPDATE_CONSTRUCT = 331
    OPEN_ACC_ATOMIC_CONSTRUCT = 332
    OPEN_ACC_CACHE_CONSTRUCT = 333
    TRANSLATION_UNIT = 350
    UNEXPOSED_ATTR = 400
    IB_ACTION_ATTR = 401
    IB_OUTLET_ATTR = 402
    IB_OUTLET_COLLECTION_ATTR = 403
    CXX_FINAL_ATTR = 404
    CXX_OVERRIDE_ATTR = 405
    ANNOTATE_ATTR = 406
    ASM_LABEL_ATTR = 407
    PACKED_ATTR = 408
    PURE_ATTR = 409
    CONST_ATTR = 410
    NODUPLICATE_ATTR = 411
    CUDACONSTANT_ATTR = 412
    CUDADEVICE_ATTR = 413
    CUDAGLOBAL_ATTR = 414
    CUDAHOST_ATTR = 415
    CUDASHARED_ATTR = 416
    VISIBILITY_ATTR = 417
    DLLEXPORT_ATTR = 418
    DLLIMPORT_ATTR = 419
    NS_RETURNS_RETAINED = 420
    NS_RETURNS_NOT_RETAINED = 421
    NS_RETURNS_AUTORELEASED = 422
    NS_CONSUMES_SELF = 423
    NS_CONSUMED = 424
    OBJC_EXCEPTION = 425
    OBJC_NSOBJECT = 426
    OBJC_INDEPENDENT_CLASS = 427
    OBJC_PRECISE_LIFETIME = 428
    OBJC_RETURNS_INNER_POINTER = 429
    OBJC_REQUIRES_SUPER = 430
    OBJC_ROOT_CLASS = 431
    OBJC_SUBCLASSING_RESTRICTED = 432
    OBJC_EXPLICIT_PROTOCOL_IMPL = 433
    OBJC_DESIGNATED_INITIALIZER = 434
    OBJC_RUNTIME_VISIBLE = 435
    OBJC_BOXABLE = 436
    FLAG_ENUM = 437
    CONVERGENT_ATTR = 438
    WARN_UNUSED_ATTR = 439
    WARN_UNUSED_RESULT_ATTR = 440
    ALIGNED_ATTR = 441
    PREPROCESSING_DIRECTIVE = 500
    MACRO_DEFINITION = 501
    MACRO_INSTANTIATION = 502
    INCLUSION_DIRECTIVE = 503
    MODULE_IMPORT_DECL = 600
    TYPE_ALIAS_TEMPLATE_DECL = 601
    STATIC_ASSERT = 602
    FRIEND_DECL = 603
    CONCEPT_DECL = 604
    OVERLOAD_CANDIDATE = 700

class TypeKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> TypeKind: ...
    @property
    def spelling(self) -> str: ...
    INVALID = 0
    UNEXPOSED = 1
    VOID = 2
    BOOL = 3
    CHAR_U = 4
    UCHAR = 5
    CHAR16 = 6
    CHAR32 = 7
    USHORT = 8
    UINT = 9
    ULONG = 10
    ULONGLONG = 11
    UINT128 = 12
    CHAR_S = 13
    SCHAR = 14
    WCHAR = 15
    SHORT = 16
    INT = 17
    LONG = 18
    LONGLONG = 19
    INT128 = 20
    FLOAT = 21
    DOUBLE = 22
    LONGDOUBLE = 23
    NULLPTR = 24
    OVERLOAD = 25
    DEPENDENT = 26
    OBJCID = 27
    OBJCCLASS = 28
    OBJCSEL = 29
    FLOAT128 = 30
    HALF = 31
    FLOAT16 = 32
    SHORTACCUM = 33
    ACCUM = 34
    LONGACCUM = 35
    USHORTACCUM = 36
    UACCUM = 37
    ULONGACCUM = 38
    BFLOAT16 = 39
    IBM128 = 40
    COMPLEX = 100
    POINTER = 101
    BLOCKPOINTER = 102
    LVALUEREFERENCE = 103
    RVALUEREFERENCE = 104
    RECORD = 105
    ENUM = 106
    TYPEDEF = 107
    OBJCINTERFACE = 108
    OBJCOBJECTPOINTER = 109
    FUNCTIONNOPROTO = 110
    FUNCTIONPROTO = 111
    CONSTANTARRAY = 112
    VECTOR = 113
    INCOMPLETEARRAY = 114
    VARIABLEARRAY = 115
    DEPENDENTSIZEDARRAY = 116
    MEMBERPOINTER = 117
    AUTO = 118
    ELABORATED = 119
    PIPE = 120
    OCLIMAGE1DRO = 121
    OCLIMAGE1DARRAYRO = 122
    OCLIMAGE1DBUFFERRO = 123
    OCLIMAGE2DRO = 124
    OCLIMAGE2DARRAYRO = 125
    OCLIMAGE2DDEPTHRO = 126
    OCLIMAGE2DARRAYDEPTHRO = 127
    OCLIMAGE2DMSAARO = 128
    OCLIMAGE2DARRAYMSAARO = 129
    OCLIMAGE2DMSAADEPTHRO = 130
    OCLIMAGE2DARRAYMSAADEPTHRO = 131
    OCLIMAGE3DRO = 132
    OCLIMAGE1DWO = 133
    OCLIMAGE1DARRAYWO = 134
    OCLIMAGE1DBUFFERWO = 135
    OCLIMAGE2DWO = 136
    OCLIMAGE2DARRAYWO = 137
    OCLIMAGE2DDEPTHWO = 138
    OCLIMAGE2DARRAYDEPTHWO = 139
    OCLIMAGE2DMSAAWO = 140
    OCLIMAGE2DARRAYMSAAWO = 141
    OCLIMAGE2DMSAADEPTHWO = 142
    OCLIMAGE2DARRAYMSAADEPTHWO = 143
    OCLIMAGE3DWO = 144
    OCLIMAGE1DRW = 145
    OCLIMAGE1DARRAYRW = 146
    OCLIMAGE1DBUFFERRW = 147
    OCLIMAGE2DRW = 148
    OCLIMAGE2DARRAYRW = 149
    OCLIMAGE2DDEPTHRW = 150
    OCLIMAGE2DARRAYDEPTHRW = 151
    OCLIMAGE2DMSAARW = 152
    OCLIMAGE2DARRAYMSAARW = 153
    OCLIMAGE2DMSAADEPTHRW = 154
    OCLIMAGE2DARRAYMSAADEPTHRW = 155
    OCLIMAGE3DRW = 156
    OCLSAMPLER = 157
    OCLEVENT = 158
    OCLQUEUE = 159
    OCLRESERVEID = 160
    OBJCOBJECT = 161
    OBJCTYPEPARAM = 162
    ATTRIBUTED = 163
    OCLINTELSUBGROUPAVCMCEPAYLOAD = 164
    OCLINTELSUBGROUPAVCIMEPAYLOAD = 165
    OCLINTELSUBGROUPAVCREFPAYLOAD = 166
    OCLINTELSUBGROUPAVCSICPAYLOAD = 167
    OCLINTELSUBGROUPAVCMCERESULT = 168
    OCLINTELSUBGROUPAVCIMERESULT = 169
    OCLINTELSUBGROUPAVCREFRESULT = 170
    OCLINTELSUBGROUPAVCSICRESULT = 171
    OCLINTELSUBGROUPAVCIMERESULTSINGLEREFERENCESTREAMOUT = 172
    OCLINTELSUBGROUPAVCIMERESULTSDUALREFERENCESTREAMOUT = 173
    OCLINTELSUBGROUPAVCIMERESULTSSINGLEREFERENCESTREAMIN = 174
    OCLINTELSUBGROUPAVCIMEDUALREFERENCESTREAMIN = 175
    EXTVECTOR = 176
    ATOMIC = 177
    BTFTAGATTRIBUTED = 178
    HLSLRESOURCE = 179
    HLSLATTRIBUTEDRESOURCE = 180
    HLSLINLINESPIRV = 181
    FIRSTBUILTIN = 2
    LASTBUILTIN = 40

class AvailabilityKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> AvailabilityKind: ...
    AVAILABLE = 0
    DEPRECATED = 1
    NOT_AVAILABLE = 2
    NOT_ACCESSIBLE = 3

class AccessSpecifier(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> AccessSpecifier: ...
    INVALID = 0
    PUBLIC = 1
    PROTECTED = 2
    PRIVATE = 3

class LanguageKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> LanguageKind: ...
    INVALID = 0
    C = 1
    OBJ_C = 2
    C_PLUS_PLUS = 3

class LinkageKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> LinkageKind: ...
    INVALID = 0
    NO_LINKAGE = 1
    INTERNAL = 2
    UNIQUE_EXTERNAL = 3
    EXTERNAL = 4

class TLSKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> TLSKind: ...
    NONE = 0
    DYNAMIC = 1
    STATIC = 2

class StorageClass(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> StorageClass: ...
    INVALID = 0
    NONE = 1
    EXTERN = 2
    STATIC = 3
    PRIVATEEXTERN = 4
    OPENCLWORKGROUPLOCAL = 5
    AUTO = 6
    REGISTER = 7

class ExceptionSpecificationKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> ExceptionSpecificationKind: ...
    NONE = 0
    DYNAMIC_NONE = 1
    DYNAMIC = 2
    MS_ANY = 3
    BASIC_NOEXCEPT = 4
    COMPUTED_NOEXCEPT = 5
    UNEVALUATED = 6
    UNINSTANTIATED = 7
    UNPARSED = 8
    NOTHROW = 9

class TemplateArgumentKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> TemplateArgumentKind: ...
    NULL = 0
    TYPE = 1
    DECLARATION = 2
    NULLPTR = 3
    INTEGRAL = 4
    TEMPLATE = 5
    TEMPLATE_EXPANSION = 6
    EXPRESSION = 7
    PACK = 8
    INVALID = 9

class BinaryOperator(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> BinaryOperator: ...
    @property
    def is_assignment(self) -> bool: ...
    Invalid = 0
    PtrMemD = 1
    PtrMemI = 2
    Mul = 3
    Div = 4
    Rem = 5
    Add = 6
    Sub = 7
    Shl = 8
    Shr = 9
    Cmp = 10
    LT = 11
    GT = 12
    LE = 13
    GE = 14
    EQ = 15
    NE = 16
    And = 17
    Xor = 18
    Or = 19
    LAnd = 20
    LOr = 21
    Assign = 22
    MulAssign = 23
    DivAssign = 24
    RemAssign = 25
    AddAssign = 26
    SubAssign = 27
    ShlAssign = 28
    ShrAssign = 29
    AndAssign = 30
    XorAssign = 31
    OrAssign = 32
    Comma = 33

class RefQualifierKind(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> RefQualifierKind: ...
    NONE = 0
    LVALUE = 1
    RVALUE = 2

class PrintingPolicyProperty(Enum):
    def from_param(self) -> int: ...
    @classmethod
    def from_id(cls, id: int) -> PrintingPolicyProperty: ...
    Indentation = 0
    SuppressSpecifiers = 1
    SuppressTagKeyword = 2
    IncludeTagDefinition = 3
    SuppressScope = 4
    SuppressUnwrittenScope = 5
    SuppressInitializers = 6
    ConstantArraySizeAsWritten = 7
    AnonymousTagLocations = 8
    SuppressStrongLifetime = 9
    SuppressLifetimeQualifiers = 10
    SuppressTemplateArgsInCXXConstructors = 11
    Bool = 12
    Restrict = 13
    Alignof = 14
    UnderscoreAlignof = 15
    UseVoidForZeroParams = 16
    TerseOutput = 17
    PolishForDeclaration = 18
    Half = 19
    MSWChar = 20
    IncludeNewlines = 21
    MSVCFormatting = 22
    ConstantsAsWritten = 23
    SuppressImplicitBase = 24
    FullyQualifiedName = 25

class Cursor(Structure):
    _fields_: ClassVar[list[tuple[str, type]]]
    _tu: TranslationUnit
    data: Any
    xdata: Any
    @staticmethod
    def from_location(tu: TranslationUnit, location: SourceLocation) -> Cursor | None: ...
    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def __hash__(self, *args: Any, **kwargs: Any) -> int: ...
    def is_null(self) -> bool: ...
    def is_definition(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_const_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_converting_constructor(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_copy_constructor(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_default_constructor(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_move_constructor(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_default_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_deleted_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_copy_assignment_operator_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_move_assignment_operator_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_explicit_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_mutable_field(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_pure_virtual_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_static_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_virtual_method(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_abstract_record(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_scoped_enum(self, *args: Any, **kwargs: Any) -> bool: ...
    def get_definition(self, *args: Any, **kwargs: Any) -> Cursor | None: ...
    def get_usr(self, *args: Any, **kwargs: Any) -> str: ...
    def get_included_file(self, *args: Any, **kwargs: Any) -> File: ...
    @property
    def kind(self) -> CursorKind: ...
    @property
    def spelling(self) -> str: ...
    def pretty_printed(self, *args: Any, **kwargs: Any) -> str: ...
    @property
    def displayname(self) -> str: ...
    @property
    def mangled_name(self) -> str: ...
    @property
    def location(self) -> SourceLocation: ...
    @property
    def linkage(self) -> LinkageKind: ...
    @property
    def language(self) -> LanguageKind: ...
    @property
    def tls_kind(self) -> TLSKind: ...
    @property
    def extent(self) -> SourceRange: ...
    @property
    def storage_class(self) -> StorageClass: ...
    @property
    def availability(self) -> AvailabilityKind: ...
    @property
    def binary_operator(self) -> BinaryOperator: ...
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
    def specialized_template(self) -> Cursor | None: ...
    @property
    def translation_unit(self) -> TranslationUnit: ...
    @property
    def referenced(self) -> Cursor | None: ...
    @property
    def brief_comment(self) -> str: ...
    @property
    def raw_comment(self) -> str: ...
    def get_arguments(self, *args: Any, **kwargs: Any) -> Iterator[Cursor | None]: ...
    def get_num_template_arguments(self, *args: Any, **kwargs: Any) -> int: ...
    def get_template_argument_kind(self, *args: Any, **kwargs: Any) -> TemplateArgumentKind: ...
    def get_template_argument_type(self, *args: Any, **kwargs: Any) -> Type: ...
    def get_template_argument_value(self, *args: Any, **kwargs: Any) -> int: ...
    def get_template_argument_unsigned_value(self, *args: Any, **kwargs: Any) -> int: ...
    def get_children(self, *args: Any, **kwargs: Any) -> Iterator[Cursor]: ...
    def walk_preorder(self, *args: Any, **kwargs: Any) -> Iterator[Cursor]: ...
    def get_tokens(self, *args: Any, **kwargs: Any) -> Iterator[Token]: ...
    def get_field_offsetof(self, *args: Any, **kwargs: Any) -> int: ...
    def get_base_offsetof(self, *args: Any, **kwargs: Any) -> int: ...
    def is_virtual_base(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_anonymous(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_anonymous_record_decl(self, *args: Any, **kwargs: Any) -> bool: ...
    def is_bitfield(self, *args: Any, **kwargs: Any) -> bool: ...
    def get_bitfield_width(self, *args: Any, **kwargs: Any) -> int: ...
    def is_function_inlined(self, *args: Any, **kwargs: Any) -> bool: ...
    def has_attrs(self, *args: Any, **kwargs: Any) -> bool: ...
    @staticmethod
    def from_result(res: Cursor, arg: Cursor | TranslationUnit | Type) -> Cursor | None: ...
    @staticmethod
    def from_cursor_result(res: Cursor, arg: Cursor) -> Cursor | None: ...
    @staticmethod
    def from_non_null_cursor_result(res: Cursor, arg: Cursor | Type) -> Cursor: ...

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
    def from_result(res: Type, arg: Cursor | Type) -> Type: ...
    def get_num_template_arguments(self) -> int: ...
    def get_template_argument_type(self, num: int) -> Type: ...
    def get_canonical(self) -> Type: ...
    def get_fully_qualified_name(self, policy: PrintingPolicy, with_global_ns_prefix: bool = False) -> str: ...
    def is_const_qualified(self) -> bool: ...
    def is_volatile_qualified(self) -> bool: ...
    def is_restrict_qualified(self) -> bool: ...
    def is_function_variadic(self) -> bool: ...
    def get_address_space(self) -> int: ...
    def get_typedef_name(self) -> str: ...
    def is_pod(self) -> bool: ...
    def get_pointee(self) -> Type: ...
    def get_declaration(self) -> Cursor: ...
    def get_result(self) -> Type: ...
    def get_array_element_type(self) -> Type: ...
    def get_array_size(self) -> int: ...
    def get_class_type(self) -> Type: ...
    def get_named_type(self) -> Type: ...
    def get_align(self) -> int: ...
    def get_size(self) -> int: ...
    def get_offset(self, fieldname: str) -> int: ...
    def get_ref_qualifier(self) -> RefQualifierKind: ...
    def get_fields(self) -> Iterator[Cursor]: ...
    def get_bases(self) -> Iterator[Cursor]: ...
    def get_methods(self) -> Iterator[Cursor]: ...
    def get_exception_specification_kind(self) -> ExceptionSpecificationKind: ...
    @property
    def spelling(self) -> str: ...
    def pretty_printed(self, policy: PrintingPolicy) -> str: ...
    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...

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
    def from_result(res: Any) -> CompilationDatabase: ...
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
    def from_result(res: Any) -> CompileCommands | None: ...

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
    def __eq__(self, other: Any) -> bool: ...
    def __ne__(self, other: Any) -> bool: ...
    @staticmethod
    def from_result(res: Any, arg: Any) -> File: ...

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
    @staticmethod
    def process_unsaved_files(unsaved_files: Any) -> Array[Any] | None: ...
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
    def __len__(self) -> int: ...
    def __getitem__(self, key: int) -> Any: ...
    @property
    def results(self) -> Any: ...
    @property
    def diagnostics(self) -> Sequence[Diagnostic]: ...

class PrintingPolicy(ClangObject):
    def __init__(self, ptr: Any) -> None: ...
    @staticmethod
    def create(cursor: Any) -> PrintingPolicy: ...
    def __del__(self) -> None: ...
    def get_property(self, property: Any) -> Any: ...
    def set_property(self, property: Any, value: Any) -> None: ...

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
    @property
    def lib(self) -> Any: ...

conf: Config

__all__ = [
    "AccessSpecifier",
    "AvailabilityKind",
    "BinaryOperator",
    "Config",
    "CodeCompletionResults",
    "CompilationDatabase",
    "CompileCommands",
    "CompileCommand",
    "CursorKind",
    "Cursor",
    "Diagnostic",
    "ExceptionSpecificationKind",
    "File",
    "FixIt",
    "Index",
    "LinkageKind",
    "PrintingPolicy",
    "PrintingPolicyProperty",
    "RefQualifierKind",
    "SourceLocation",
    "SourceRange",
    "StorageClass",
    "TemplateArgumentKind",
    "TLSKind",
    "TokenKind",
    "Token",
    "TranslationUnitLoadError",
    "TranslationUnit",
    "TypeKind",
    "Type",
]
