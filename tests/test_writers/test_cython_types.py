"""Tests for Cython type registries."""

from headerkit.writers._cython_types import (
    CYTHON_STDLIB_HEADERS,
    CYTHON_STDLIB_TYPES,
    HEADERKIT_STUB_TYPES,
    LIBCPP_HEADERS,
    LIBCPP_TYPES,
    get_cython_module_for_type,
    get_libcpp_module_for_type,
    get_stub_module_for_type,
)


class TestCythonStdlibRegistry:
    """Tests for Cython standard library mappings."""

    def test_stdlib_headers_contain_expected_keys(self) -> None:
        expected_headers = {
            "stddef.h",
            "stdint.h",
            "stdio.h",
            "stdlib.h",
            "string.h",
            "math.h",
            "time.h",
            "signal.h",
            "errno.h",
            "setjmp.h",
            "locale.h",
            "unistd.h",
            "fcntl.h",
            "sys/stat.h",
            "sys/types.h",
            "dlfcn.h",
            "Python.h",
        }
        assert set(CYTHON_STDLIB_HEADERS.keys()) == expected_headers

    def test_stdint_header_mapping(self) -> None:
        """stdint.h maps to libc.stdint with all expected types."""
        assert "stdint.h" in CYTHON_STDLIB_HEADERS
        module, types = CYTHON_STDLIB_HEADERS["stdint.h"]
        assert module == "libc.stdint"
        expected_stdint_types = {
            "int8_t",
            "int16_t",
            "int32_t",
            "int64_t",
            "uint8_t",
            "uint16_t",
            "uint32_t",
            "uint64_t",
            "intptr_t",
            "uintptr_t",
            "intmax_t",
            "uintmax_t",
        }
        assert types == expected_stdint_types

    def test_stddef_header_mapping(self) -> None:
        """stddef.h maps to libc.stddef with expected types."""
        assert "stddef.h" in CYTHON_STDLIB_HEADERS
        module, types = CYTHON_STDLIB_HEADERS["stddef.h"]
        assert module == "libc.stddef"
        assert "ptrdiff_t" in types
        assert "wchar_t" in types

    def test_stdio_header_mapping(self) -> None:
        """stdio.h maps to libc.stdio with FILE type."""
        assert "stdio.h" in CYTHON_STDLIB_HEADERS
        module, types = CYTHON_STDLIB_HEADERS["stdio.h"]
        assert module == "libc.stdio"
        assert "FILE" in types

    def test_time_header_mapping(self) -> None:
        """time.h maps to libc.time with time types."""
        assert "time.h" in CYTHON_STDLIB_HEADERS
        module, types = CYTHON_STDLIB_HEADERS["time.h"]
        assert module == "libc.time"
        assert "time_t" in types
        assert "tm" in types

    def test_posix_unistd_mapping(self) -> None:
        """unistd.h maps to posix.unistd."""
        assert "unistd.h" in CYTHON_STDLIB_HEADERS
        module, types = CYTHON_STDLIB_HEADERS["unistd.h"]
        assert module == "posix.unistd"
        assert "pid_t" in types

    def test_python_header_mapping(self) -> None:
        """Python.h maps to cpython module."""
        assert "Python.h" in CYTHON_STDLIB_HEADERS
        module, types = CYTHON_STDLIB_HEADERS["Python.h"]
        assert module == "cpython"
        assert "PyObject" in types

    def test_reverse_lookup_populated(self) -> None:
        """CYTHON_STDLIB_TYPES reverse lookup is populated."""
        assert "uint32_t" in CYTHON_STDLIB_TYPES
        assert CYTHON_STDLIB_TYPES["uint32_t"] == ("libc.stdint", "stdint.h")
        assert "FILE" in CYTHON_STDLIB_TYPES
        assert CYTHON_STDLIB_TYPES["FILE"] == ("libc.stdio", "stdio.h")

    def test_get_cython_module_for_type(self) -> None:
        """get_cython_module_for_type returns correct module."""
        assert get_cython_module_for_type("uint32_t") == "libc.stdint"
        assert get_cython_module_for_type("FILE") == "libc.stdio"
        assert get_cython_module_for_type("unknown_type") is None


class TestStubTypeRegistry:
    """Tests for headerkit stub type mappings."""

    def test_va_list_mapping(self) -> None:
        """va_list maps to stdarg stub module."""
        assert "va_list" in HEADERKIT_STUB_TYPES
        assert HEADERKIT_STUB_TYPES["va_list"] == "stdarg"

    def test_socket_type_mapping(self) -> None:
        """sockaddr maps to sys_socket stub module."""
        assert "sockaddr" in HEADERKIT_STUB_TYPES
        assert HEADERKIT_STUB_TYPES["sockaddr"] == "sys_socket"

    def test_netinet_type_mapping(self) -> None:
        """sockaddr_in maps to netinet_in stub module."""
        assert "sockaddr_in" in HEADERKIT_STUB_TYPES
        assert HEADERKIT_STUB_TYPES["sockaddr_in"] == "netinet_in"

    def test_get_stub_module_for_type(self) -> None:
        """get_stub_module_for_type returns correct module."""
        assert get_stub_module_for_type("va_list") == "stdarg"
        assert get_stub_module_for_type("sockaddr") == "sys_socket"
        assert get_stub_module_for_type("unknown_type") is None


class TestLibcppRegistry:
    """Tests for C++ STL mappings."""

    def test_vector_header_mapping(self) -> None:
        """vector header maps to libcpp.vector."""
        assert "vector" in LIBCPP_HEADERS
        module, types = LIBCPP_HEADERS["vector"]
        assert module == "libcpp.vector"
        assert "vector" in types

    def test_string_header_mapping(self) -> None:
        """string header maps to libcpp.string."""
        assert "string" in LIBCPP_HEADERS
        module, types = LIBCPP_HEADERS["string"]
        assert module == "libcpp.string"
        assert "string" in types

    def test_memory_header_mapping(self) -> None:
        """memory header maps to libcpp.memory with smart pointers."""
        assert "memory" in LIBCPP_HEADERS
        module, types = LIBCPP_HEADERS["memory"]
        assert module == "libcpp.memory"
        assert "shared_ptr" in types
        assert "unique_ptr" in types

    def test_reverse_lookup_populated(self) -> None:
        """LIBCPP_TYPES reverse lookup is populated."""
        assert "vector" in LIBCPP_TYPES
        assert LIBCPP_TYPES["vector"] == ("libcpp.vector", "vector")
        assert "shared_ptr" in LIBCPP_TYPES
        assert LIBCPP_TYPES["shared_ptr"] == ("libcpp.memory", "memory")

    def test_get_libcpp_module_for_type(self) -> None:
        """get_libcpp_module_for_type returns correct module."""
        assert get_libcpp_module_for_type("vector") == "libcpp.vector"
        assert get_libcpp_module_for_type("shared_ptr") == "libcpp.memory"
        assert get_libcpp_module_for_type("unknown_type") is None
