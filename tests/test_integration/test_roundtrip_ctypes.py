"""Integration roundtrip tests: parse C headers with libclang -> IR -> ctypes output."""

from __future__ import annotations

import pytest

from headerkit.backends import is_backend_available
from headerkit.writers.ctypes import header_to_ctypes

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


def parse_and_ctypes(backend, code: str, lib_name: str = "_lib") -> str:
    """Parse C code and convert to ctypes binding string."""
    header = backend.parse(code, "test.h")
    return header_to_ctypes(header, lib_name=lib_name)


class TestCtypesEmpty:
    """Verify the writer does not crash on an empty header."""

    def test_empty_header(self, backend):
        output = parse_and_ctypes(backend, "")
        assert isinstance(output, str)
        assert len(output) > 0
        assert "import ctypes" in output


class TestCtypesStructRoundtrip:
    """Test parsing and converting struct declarations to ctypes."""

    def test_simple_struct(self, backend):
        output = parse_and_ctypes(backend, "struct Point { int x; int y; };")
        assert "class Point(ctypes.Structure):" in output
        assert '("x", ctypes.c_int),' in output
        assert '("y", ctypes.c_int),' in output

    def test_typedef_struct(self, backend):
        output = parse_and_ctypes(backend, "typedef struct { float r; float g; float b; } Color;")
        assert "class Color(ctypes.Structure):" in output
        assert '("r", ctypes.c_float),' in output

    def test_union(self, backend):
        output = parse_and_ctypes(backend, "union Data { int i; float f; char c; };")
        assert "class Data(ctypes.Union):" in output
        assert '("i", ctypes.c_int),' in output
        assert '("f", ctypes.c_float),' in output
        assert '("c", ctypes.c_char),' in output

    def test_anonymous_typedef_struct(self, backend):
        output = parse_and_ctypes(backend, "typedef struct { int x; } MyPoint;")
        assert "class MyPoint(ctypes.Structure):" in output
        assert '("x", ctypes.c_int),' in output
        assert "(anonymous" not in output
        assert "(unnamed" not in output

    def test_opaque_struct(self, backend):
        output = parse_and_ctypes(backend, "struct Handle;")
        assert "class Handle(ctypes.Structure):" in output
        assert "pass" in output


class TestCtypesFunctionRoundtrip:
    """Test parsing and converting function declarations to ctypes."""

    def test_void_return_two_params(self, backend):
        output = parse_and_ctypes(backend, "void add(int a, int b);")
        assert "_lib.add.argtypes = [ctypes.c_int, ctypes.c_int]" in output
        assert "_lib.add.restype = None" in output

    def test_int_return_no_params(self, backend):
        output = parse_and_ctypes(backend, "int get(void);")
        assert "_lib.get.argtypes = []" in output
        assert "_lib.get.restype = ctypes.c_int" in output

    def test_pointer_return(self, backend):
        output = parse_and_ctypes(backend, "char *get_name(void);")
        assert "_lib.get_name.restype = ctypes.c_char_p" in output

    def test_const_char_pointer_param(self, backend):
        output = parse_and_ctypes(backend, "void log_msg(const char *msg);")
        assert "_lib.log_msg.argtypes = [ctypes.c_char_p]" in output

    def test_void_pointer_param(self, backend):
        output = parse_and_ctypes(backend, "void process(void *data);")
        assert "_lib.process.argtypes = [ctypes.c_void_p]" in output

    def test_custom_lib_name(self, backend):
        output = parse_and_ctypes(backend, "void init(void);", lib_name="mylib")
        assert "mylib.init.argtypes = []" in output
        assert "mylib.init.restype = None" in output


class TestCtypesMacroRoundtrip:
    """Test parsing and converting macro constants to ctypes."""

    def test_integer_macro(self, backend):
        output = parse_and_ctypes(backend, "#define MAX_SIZE 1024\nvoid func(void);")
        assert "MAX_SIZE = 1024" in output

    def test_macro_not_string(self, backend):
        output = parse_and_ctypes(backend, '#define VERSION "1.0"\nvoid func(void);')
        # String macros may or may not appear as Constant IR nodes depending on
        # libclang version. If present, the ctypes writer must emit b"..." bytes literal.
        if "VERSION" in output:
            assert 'b"1.0"' in output


class TestCtypesEnumRoundtrip:
    """Test parsing and converting enum declarations to ctypes."""

    def test_named_enum(self, backend):
        output = parse_and_ctypes(backend, "enum Color { RED=0, GREEN=1, BLUE=2 };")
        assert "# enum Color" in output
        assert "RED = 0" in output
        assert "GREEN = 1" in output
        assert "BLUE = 2" in output

    def test_typedef_enum(self, backend):
        output = parse_and_ctypes(backend, "typedef enum { OFF=0, ON=1 } Switch;")
        assert "OFF = 0" in output
        assert "ON = 1" in output


class TestCtypesTypedefRoundtrip:
    """Test parsing and converting typedef declarations to ctypes."""

    def test_function_pointer_typedef(self, backend):
        output = parse_and_ctypes(backend, "typedef void (*callback_fn)(int status);")
        assert "callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)" in output


class TestCtypesCompleteHeader:
    """Test parsing a multi-declaration header to ctypes."""

    def test_complete_header(self, backend):
        code = (
            "#define BUF_SIZE 256\n"
            "struct Buffer { char *data; int length; };\n"
            "int buffer_read(struct Buffer *buf, char *out, int n);"
        )
        output = parse_and_ctypes(backend, code)
        assert "BUF_SIZE = 256" in output
        assert "class Buffer(ctypes.Structure):" in output
        assert "_lib.buffer_read.argtypes" in output
        assert "_lib.buffer_read.restype = ctypes.c_int" in output
