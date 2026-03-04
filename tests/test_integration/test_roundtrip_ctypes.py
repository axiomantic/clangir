"""Integration roundtrip tests: parse C headers with libclang -> IR -> ctypes output."""

from __future__ import annotations

import textwrap

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


_PREAMBLE = textwrap.dedent('''\
    """ctypes bindings generated from test.h."""

    import ctypes
    import ctypes.util
    import sys
''')


class TestCtypesEmpty:
    """Verify the writer does not crash on an empty header."""

    def test_empty_header(self, backend):
        output = parse_and_ctypes(backend, "")
        assert output == _PREAMBLE


class TestCtypesStructRoundtrip:
    """Test parsing and converting struct declarations to ctypes."""

    def test_simple_struct(self, backend):
        output = parse_and_ctypes(backend, "struct Point { int x; int y; };")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Point(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                    ("y", ctypes.c_int),
                ]
        """)

    def test_typedef_struct(self, backend):
        output = parse_and_ctypes(backend, "typedef struct { float r; float g; float b; } Color;")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Color(ctypes.Structure):
                _fields_ = [
                    ("r", ctypes.c_float),
                    ("g", ctypes.c_float),
                    ("b", ctypes.c_float),
                ]
        """)

    def test_union(self, backend):
        output = parse_and_ctypes(backend, "union Data { int i; float f; char c; };")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Data(ctypes.Union):
                _fields_ = [
                    ("i", ctypes.c_int),
                    ("f", ctypes.c_float),
                    ("c", ctypes.c_char),
                ]
        """)

    def test_anonymous_typedef_struct(self, backend):
        output = parse_and_ctypes(backend, "typedef struct { int x; } MyPoint;")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Structures and Unions
            # ============================================================

            class MyPoint(ctypes.Structure):
                _fields_ = [
                    ("x", ctypes.c_int),
                ]
        """)

    def test_opaque_struct(self, backend):
        output = parse_and_ctypes(backend, "struct Handle;")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Handle(ctypes.Structure):
                pass
        """)


class TestCtypesFunctionRoundtrip:
    """Test parsing and converting function declarations to ctypes."""

    def test_void_return_two_params(self, backend):
        output = parse_and_ctypes(backend, "void add(int a, int b);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.add.argtypes = [ctypes.c_int, ctypes.c_int]
            _lib.add.restype = None
        """)

    def test_int_return_no_params(self, backend):
        output = parse_and_ctypes(backend, "int get(void);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.get.argtypes = []
            _lib.get.restype = ctypes.c_int
        """)

    def test_pointer_return(self, backend):
        output = parse_and_ctypes(backend, "char *get_name(void);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.get_name.argtypes = []
            _lib.get_name.restype = ctypes.c_char_p
        """)

    def test_const_char_pointer_param(self, backend):
        output = parse_and_ctypes(backend, "void log_msg(const char *msg);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.log_msg.argtypes = [ctypes.c_char_p]
            _lib.log_msg.restype = None
        """)

    def test_void_pointer_param(self, backend):
        output = parse_and_ctypes(backend, "void process(void *data);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.process.argtypes = [ctypes.c_void_p]
            _lib.process.restype = None
        """)

    def test_custom_lib_name(self, backend):
        output = parse_and_ctypes(backend, "void init(void);", lib_name="mylib")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Function Prototypes
            # ============================================================

            mylib.init.argtypes = []
            mylib.init.restype = None
        """)


class TestCtypesMacroRoundtrip:
    """Test parsing and converting macro constants to ctypes."""

    def test_integer_macro(self, backend):
        output = parse_and_ctypes(backend, "#define MAX_SIZE 1024\nvoid func(void);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Constants
            # ============================================================

            MAX_SIZE = 1024

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.func.argtypes = []
            _lib.func.restype = None
        """)

    def test_macro_not_string(self, backend):
        output = parse_and_ctypes(backend, '#define VERSION "1.0"\nvoid func(void);')
        # String macros are emitted as bytes literals by the ctypes writer.
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Constants
            # ============================================================

            VERSION = b"1.0"

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.func.argtypes = []
            _lib.func.restype = None
        """)


class TestCtypesEnumRoundtrip:
    """Test parsing and converting enum declarations to ctypes."""

    def test_named_enum(self, backend):
        output = parse_and_ctypes(backend, "enum Color { RED=0, GREEN=1, BLUE=2 };")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Enums
            # ============================================================

            # enum Color
            RED = 0
            GREEN = 1
            BLUE = 2
        """)

    def test_typedef_enum(self, backend):
        output = parse_and_ctypes(backend, "typedef enum { OFF=0, ON=1 } Switch;")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Enums
            # ============================================================

            # enum Switch
            OFF = 0
            ON = 1

            # ============================================================
            # Typedefs
            # ============================================================

            Switch = Switch
        """)


class TestCtypesTypedefRoundtrip:
    """Test parsing and converting typedef declarations to ctypes."""

    def test_function_pointer_typedef(self, backend):
        output = parse_and_ctypes(backend, "typedef void (*callback_fn)(int status);")
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Typedefs
            # ============================================================

            callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)
        """)


class TestCtypesCompleteHeader:
    """Test parsing a multi-declaration header to ctypes."""

    def test_complete_header(self, backend):
        code = textwrap.dedent("""\
            #define BUF_SIZE 256
            struct Buffer { char *data; int length; };
            int buffer_read(struct Buffer *buf, char *out, int n);""")
        output = parse_and_ctypes(backend, code)
        assert output == _PREAMBLE + textwrap.dedent("""\

            # ============================================================
            # Constants
            # ============================================================

            BUF_SIZE = 256

            # ============================================================
            # Structures and Unions
            # ============================================================

            class Buffer(ctypes.Structure):
                _fields_ = [
                    ("data", ctypes.c_char_p),
                    ("length", ctypes.c_int),
                ]

            # ============================================================
            # Function Prototypes
            # ============================================================

            _lib.buffer_read.argtypes = [ctypes.POINTER(struct Buffer), ctypes.c_char_p, ctypes.c_int]
            _lib.buffer_read.restype = ctypes.c_int
        """)
