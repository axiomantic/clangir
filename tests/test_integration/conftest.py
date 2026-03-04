"""Shared fixtures for integration tests that download real-world C/C++ headers."""

from __future__ import annotations

import io
import tarfile
import urllib.error
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from headerkit.backends import get_backend, is_backend_available

# Skip entire module if libclang unavailable
pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)

CACHE_DIR = Path.home() / ".cache" / "headerkit-test-headers"


def _download_file(url: str, dest: Path) -> None:
    """Download a URL to a local file, creating parent dirs."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "headerkit-test/1.0"})
    with urlopen(req, timeout=60) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def _download_and_extract_tar(url: str, dest_dir: Path, members: list[str] | None = None) -> None:
    """Download a tar.gz/tar.xz and extract specific members (or all) to dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "headerkit-test/1.0"})
    with urlopen(req, timeout=120) as resp:  # noqa: S310
        data = resp.read()
    mode = "r:xz" if url.endswith(".tar.xz") else "r:gz"
    with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
        if members:
            for member in tf.getmembers():
                for wanted in members:
                    if member.name.endswith(wanted):
                        member.name = Path(member.name).name
                        tf.extract(member, dest_dir)
                        break
        else:
            tf.extractall(dest_dir)


def _download_and_extract_zip(url: str, dest_dir: Path, members: list[str] | None = None) -> None:
    """Download a zip and extract specific members to dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "headerkit-test/1.0"})
    with urlopen(req, timeout=120) as resp:  # noqa: S310
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if members:
            for name in zf.namelist():
                for wanted in members:
                    if name.endswith(wanted):
                        content = zf.read(name)
                        (dest_dir / Path(name).name).write_bytes(content)
                        break
        else:
            zf.extractall(dest_dir)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def backend():
    """Get a libclang backend instance for the session."""
    return get_backend("libclang")


# -- sqlite3 ---------------------------------------------------------------

SQLITE_VERSION = "3480000"
SQLITE_URL = f"https://www.sqlite.org/2025/sqlite-amalgamation-{SQLITE_VERSION}.zip"


@pytest.fixture(scope="session")
def sqlite3_header() -> Path | None:
    """Download sqlite3.h amalgamation header."""
    cache = CACHE_DIR / f"sqlite3-{SQLITE_VERSION}"
    header = cache / "sqlite3.h"
    if not header.exists():
        try:
            _download_and_extract_zip(SQLITE_URL, cache, ["sqlite3.h"])
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            import warnings

            warnings.warn(f"Failed to download sqlite3: {exc}", stacklevel=2)
            return None
    return header if header.exists() else None


# -- zlib -------------------------------------------------------------------

ZLIB_VERSION = "1.3.1"
ZLIB_URL = f"https://raw.githubusercontent.com/madler/zlib/v{ZLIB_VERSION}/zlib.h"
ZLIB_ZCONF_URL = f"https://raw.githubusercontent.com/madler/zlib/v{ZLIB_VERSION}/zconf.h"


@pytest.fixture(scope="session")
def zlib_header() -> Path | None:
    """Download zlib.h and its required zconf.h companion header."""
    cache = CACHE_DIR / f"zlib-{ZLIB_VERSION}"
    header = cache / "zlib.h"
    zconf = cache / "zconf.h"
    # Download both files if either is missing (zlib.h includes zconf.h).
    if not header.exists() or not zconf.exists():
        try:
            _download_file(ZLIB_URL, header)
            _download_file(ZLIB_ZCONF_URL, zconf)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            import warnings

            warnings.warn(f"Failed to download zlib: {exc}", stacklevel=2)
            return None
    return header if header.exists() else None


# -- lua --------------------------------------------------------------------

LUA_VERSION = "5.4.7"
LUA_URL = f"https://www.lua.org/ftp/lua-{LUA_VERSION}.tar.gz"


@pytest.fixture(scope="session")
def lua_headers() -> Path | None:
    """Download lua header directory (lua.h, lauxlib.h, luaconf.h)."""
    cache = CACHE_DIR / f"lua-{LUA_VERSION}"
    header = cache / "lua.h"
    if not header.exists():
        try:
            _download_and_extract_tar(LUA_URL, cache, ["lua.h", "lauxlib.h", "luaconf.h"])
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            import warnings

            warnings.warn(f"Failed to download lua: {exc}", stacklevel=2)
            return None
    return cache if header.exists() else None


# -- libcurl ----------------------------------------------------------------

CURL_VERSION = "8.11.1"
CURL_TAG = CURL_VERSION.replace(".", "_")
CURL_URL = f"https://github.com/curl/curl/releases/download/curl-{CURL_TAG}/curl-{CURL_VERSION}.tar.gz"


@pytest.fixture(scope="session")
def curl_headers() -> Path | None:
    """Download curl header directory."""
    cache = CACHE_DIR / f"curl-{CURL_VERSION}"
    curl_dir = cache / "curl"
    header = curl_dir / "curl.h"
    if not header.exists():
        try:
            curl_dir.mkdir(parents=True, exist_ok=True)
            req = Request(CURL_URL, headers={"User-Agent": "headerkit-test/1.0"})
            with urlopen(req, timeout=120) as resp:  # noqa: S310
                data = resp.read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                prefix = f"curl-{CURL_VERSION}/include/curl/"
                for member in tf.getmembers():
                    if member.name.startswith(prefix) and member.isfile():
                        member.name = Path(member.name).name
                        tf.extract(member, curl_dir)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            import warnings

            warnings.warn(f"Failed to download curl: {exc}", stacklevel=2)
            return None
    return cache if header.exists() else None


# -- SDL2 -------------------------------------------------------------------

SDL_VERSION = "2.30.10"
SDL_URL = f"https://github.com/libsdl-org/SDL/releases/download/release-{SDL_VERSION}/SDL2-{SDL_VERSION}.tar.gz"


@pytest.fixture(scope="session")
def sdl2_headers(backend) -> Path | None:
    """Download SDL2 header directory.

    Returns None (causing tests to skip) if headers are unavailable or cannot
    be parsed on the current platform.  SDL2 uses ARM NEON intrinsics that
    some libclang builds reject with a hard error; treating that as an
    environmental unavailability keeps the suite green on those platforms.
    """
    cache = CACHE_DIR / f"sdl2-{SDL_VERSION}"
    sdl_dir = cache / "SDL2"
    header = sdl_dir / "SDL.h"
    if not header.exists():
        try:
            sdl_dir.mkdir(parents=True, exist_ok=True)
            req = Request(SDL_URL, headers={"User-Agent": "headerkit-test/1.0"})
            with urlopen(req, timeout=120) as resp:  # noqa: S310
                data = resp.read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                prefix = f"SDL2-{SDL_VERSION}/include/"
                for member in tf.getmembers():
                    if member.name.startswith(prefix) and member.isfile():
                        member.name = Path(member.name).name
                        tf.extract(member, sdl_dir)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            import warnings

            warnings.warn(f"Failed to download SDL2: {exc}", stacklevel=2)
            return None
    if not header.exists():
        return None
    # Probe-parse to detect platform-specific incompatibilities (e.g. ARM NEON
    # intrinsics on macOS that the vendored libclang rejects with a hard error).
    try:
        backend.parse(header.read_text(), header.name, include_dirs=[str(cache), str(sdl_dir)])
    except RuntimeError as exc:
        import warnings

        warnings.warn(f"SDL2 headers cannot be parsed on this platform (skipping): {exc}", stacklevel=2)
        return None
    return cache


# -- CPython ----------------------------------------------------------------

CPYTHON_VERSION = "3.13.2"
CPYTHON_URL = f"https://github.com/python/cpython/archive/refs/tags/v{CPYTHON_VERSION}.tar.gz"


@pytest.fixture(scope="session")
def cpython_headers() -> Path | None:
    """Download CPython header directory."""
    cache = CACHE_DIR / f"cpython-{CPYTHON_VERSION}"
    inc_dir = cache / "Include"
    header = inc_dir / "Python.h"
    if not header.exists():
        try:
            inc_dir.mkdir(parents=True, exist_ok=True)
            req = Request(CPYTHON_URL, headers={"User-Agent": "headerkit-test/1.0"})
            with urlopen(req, timeout=120) as resp:  # noqa: S310
                data = resp.read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                prefix = f"cpython-{CPYTHON_VERSION}/Include/"
                for member in tf.getmembers():
                    if member.name.startswith(prefix) and member.isfile():
                        rel = member.name[len(prefix) :]
                        dest = inc_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        member.name = rel
                        tf.extract(member, inc_dir)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            import warnings

            warnings.warn(f"Failed to download CPython: {exc}", stacklevel=2)
            return None
    return cache if header.exists() else None
