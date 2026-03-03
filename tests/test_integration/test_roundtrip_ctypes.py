"""Integration roundtrip tests: parse C headers with libclang -> IR -> ctypes output."""

from __future__ import annotations

import pytest

from headerkit.backends import is_backend_available
from headerkit.writers.ctypes import header_to_ctypes

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


# The `backend` fixture is provided by tests/test_integration/conftest.py.


def parse_and_ctypes(backend, code: str, lib_name: str = "_lib") -> str:
    """Parse C code and convert to ctypes binding string."""
    header = backend.parse(code, "test.h")
    return header_to_ctypes(header, lib_name=lib_name)


_PREAMBLE = '"""ctypes bindings generated from test.h."""\n\nimport ctypes\nimport ctypes.util\nimport sys\n'


class TestCtypesEmpty:
    """Verify the writer does not crash on an empty header."""

    def test_empty_header(self, backend):
        output = parse_and_ctypes(backend, "")
        assert output == _PREAMBLE


class TestCtypesStructRoundtrip:
    """Test parsing and converting struct declarations to ctypes."""

    def test_simple_struct(self, backend):
        output = parse_and_ctypes(backend, "struct Point { int x; int y; };")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Structures and Unions\n"
            + "# ============================================================\n"
            + "\n"
            + "class Point(ctypes.Structure):\n"
            + "    _fields_ = [\n"
            + '        ("x", ctypes.c_int),\n'
            + '        ("y", ctypes.c_int),\n'
            + "    ]\n"
        )

    def test_typedef_struct(self, backend):
        output = parse_and_ctypes(backend, "typedef struct { float r; float g; float b; } Color;")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Structures and Unions\n"
            + "# ============================================================\n"
            + "\n"
            + "class Color(ctypes.Structure):\n"
            + "    _fields_ = [\n"
            + '        ("r", ctypes.c_float),\n'
            + '        ("g", ctypes.c_float),\n'
            + '        ("b", ctypes.c_float),\n'
            + "    ]\n"
        )

    def test_union(self, backend):
        output = parse_and_ctypes(backend, "union Data { int i; float f; char c; };")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Structures and Unions\n"
            + "# ============================================================\n"
            + "\n"
            + "class Data(ctypes.Union):\n"
            + "    _fields_ = [\n"
            + '        ("i", ctypes.c_int),\n'
            + '        ("f", ctypes.c_float),\n'
            + '        ("c", ctypes.c_char),\n'
            + "    ]\n"
        )

    def test_anonymous_typedef_struct(self, backend):
        output = parse_and_ctypes(backend, "typedef struct { int x; } MyPoint;")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Structures and Unions\n"
            + "# ============================================================\n"
            + "\n"
            + "class MyPoint(ctypes.Structure):\n"
            + "    _fields_ = [\n"
            + '        ("x", ctypes.c_int),\n'
            + "    ]\n"
        )

    def test_opaque_struct(self, backend):
        output = parse_and_ctypes(backend, "struct Handle;")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Structures and Unions\n"
            + "# ============================================================\n"
            + "\n"
            + "class Handle(ctypes.Structure):\n"
            + "    pass\n"
        )


class TestCtypesFunctionRoundtrip:
    """Test parsing and converting function declarations to ctypes."""

    def test_void_return_two_params(self, backend):
        output = parse_and_ctypes(backend, "void add(int a, int b);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.add.argtypes = [ctypes.c_int, ctypes.c_int]\n"
            + "_lib.add.restype = None\n"
        )

    def test_int_return_no_params(self, backend):
        output = parse_and_ctypes(backend, "int get(void);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.get.argtypes = []\n"
            + "_lib.get.restype = ctypes.c_int\n"
        )

    def test_pointer_return(self, backend):
        output = parse_and_ctypes(backend, "char *get_name(void);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.get_name.argtypes = []\n"
            + "_lib.get_name.restype = ctypes.c_char_p\n"
        )

    def test_const_char_pointer_param(self, backend):
        output = parse_and_ctypes(backend, "void log_msg(const char *msg);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.log_msg.argtypes = [ctypes.c_char_p]\n"
            + "_lib.log_msg.restype = None\n"
        )

    def test_void_pointer_param(self, backend):
        output = parse_and_ctypes(backend, "void process(void *data);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.process.argtypes = [ctypes.c_void_p]\n"
            + "_lib.process.restype = None\n"
        )

    def test_custom_lib_name(self, backend):
        output = parse_and_ctypes(backend, "void init(void);", lib_name="mylib")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "mylib.init.argtypes = []\n"
            + "mylib.init.restype = None\n"
        )


class TestCtypesMacroRoundtrip:
    """Test parsing and converting macro constants to ctypes."""

    def test_integer_macro(self, backend):
        output = parse_and_ctypes(backend, "#define MAX_SIZE 1024\nvoid func(void);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Constants\n"
            + "# ============================================================\n"
            + "\n"
            + "MAX_SIZE = 1024\n"
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.func.argtypes = []\n"
            + "_lib.func.restype = None\n"
        )

    def test_macro_not_string(self, backend):
        output = parse_and_ctypes(backend, '#define VERSION "1.0"\nvoid func(void);')
        # String macros are emitted as bytes literals by the ctypes writer.
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Constants\n"
            + "# ============================================================\n"
            + "\n"
            + 'VERSION = b"1.0"\n'
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.func.argtypes = []\n"
            + "_lib.func.restype = None\n"
        )


class TestCtypesEnumRoundtrip:
    """Test parsing and converting enum declarations to ctypes."""

    def test_named_enum(self, backend):
        output = parse_and_ctypes(backend, "enum Color { RED=0, GREEN=1, BLUE=2 };")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Enums\n"
            + "# ============================================================\n"
            + "\n"
            + "# enum Color\n"
            + "RED = 0\n"
            + "GREEN = 1\n"
            + "BLUE = 2\n"
        )

    def test_typedef_enum(self, backend):
        output = parse_and_ctypes(backend, "typedef enum { OFF=0, ON=1 } Switch;")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Enums\n"
            + "# ============================================================\n"
            + "\n"
            + "# enum Switch\n"
            + "OFF = 0\n"
            + "ON = 1\n"
            + "\n"
            + "# ============================================================\n"
            + "# Typedefs\n"
            + "# ============================================================\n"
            + "\n"
            + "Switch = Switch\n"
        )


class TestCtypesTypedefRoundtrip:
    """Test parsing and converting typedef declarations to ctypes."""

    def test_function_pointer_typedef(self, backend):
        output = parse_and_ctypes(backend, "typedef void (*callback_fn)(int status);")
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Typedefs\n"
            + "# ============================================================\n"
            + "\n"
            + "callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)\n"
        )


class TestCtypesCompleteHeader:
    """Test parsing a multi-declaration header to ctypes."""

    def test_complete_header(self, backend):
        code = (
            "#define BUF_SIZE 256\n"
            "struct Buffer { char *data; int length; };\n"
            "int buffer_read(struct Buffer *buf, char *out, int n);"
        )
        output = parse_and_ctypes(backend, code)
        assert output == (
            _PREAMBLE
            + "\n"
            + "# ============================================================\n"
            + "# Constants\n"
            + "# ============================================================\n"
            + "\n"
            + "BUF_SIZE = 256\n"
            + "\n"
            + "# ============================================================\n"
            + "# Structures and Unions\n"
            + "# ============================================================\n"
            + "\n"
            + "class Buffer(ctypes.Structure):\n"
            + "    _fields_ = [\n"
            + '        ("data", ctypes.c_char_p),\n'
            + '        ("length", ctypes.c_int),\n'
            + "    ]\n"
            + "\n"
            + "# ============================================================\n"
            + "# Function Prototypes\n"
            + "# ============================================================\n"
            + "\n"
            + "_lib.buffer_read.argtypes = [ctypes.POINTER(struct Buffer), ctypes.c_char_p, ctypes.c_int]\n"
            + "_lib.buffer_read.restype = ctypes.c_int\n"
        )
