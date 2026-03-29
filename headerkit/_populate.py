"""Multi-platform cache population via Docker.

Provides the ``populate()`` function and supporting data types for
generating cache entries across Docker-emulated target platforms.
Each (platform, python_version, writer) combination runs headerkit
inside a Docker container that matches the target's sys.platform,
platform.machine(), and Python implementation.
"""

from __future__ import annotations

import fnmatch
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from headerkit._config import _find_project_root, _parse_toml

# ---------------------------------------------------------------------------
# Platform constants
# ---------------------------------------------------------------------------

DEFAULT_IMAGES: dict[str, str] = {
    "linux/amd64": "quay.io/pypa/manylinux_2_28_x86_64",
    "linux/arm64": "quay.io/pypa/manylinux_2_28_aarch64",
    "linux/386": "quay.io/pypa/manylinux_2_28_i686",
}

PLATFORM_MAP: dict[str, tuple[str, str]] = {
    "linux/amd64": ("linux", "x86_64"),
    "linux/arm64": ("linux", "aarch64"),
    "linux/386": ("linux", "i686"),
    "linux/arm/v7": ("linux", "armv7l"),
}

DEFAULT_PYTHON_VERSIONS: list[str] = ["3.10", "3.11", "3.12", "3.13", "3.14"]


def _parse_version(version: str) -> tuple[str, str]:
    """Split and validate a MAJOR.MINOR version string.

    :param version: Version string like "3.12".
    :returns: (major, minor) tuple, e.g. ("3", "12").
    :raises ValueError: If version is not in MAJOR.MINOR format.
    """
    parts = version.split(".")
    if len(parts) != 2:
        raise ValueError(f"Expected MAJOR.MINOR version, got: {version!r}")
    return parts[0], parts[1]


def python_path_for_version(version: str) -> str:
    """Return the manylinux Python interpreter path for a version string.

    :param version: Version string like "3.12".
    :returns: Path like "/opt/python/cp312-cp312/bin/python".
    :raises ValueError: If version is not in MAJOR.MINOR format.
    """
    major, minor = _parse_version(version)
    tag = f"cp{major}{minor}"
    return f"/opt/python/{tag}-{tag}/bin/python"


def py_impl_for_version(version: str) -> str:
    """Return the cache-key-compatible Python implementation string.

    :param version: Version string like "3.12".
    :returns: String like "cpython312".
    :raises ValueError: If version is not in MAJOR.MINOR format.
    """
    major, minor = _parse_version(version)
    return f"cpython{major}{minor}"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PopulateTarget:
    """A single (platform, python_version) target for cache population."""

    docker_platform: str
    python_version: str
    docker_image: str
    python_path: str
    sys_platform: str = ""
    machine: str = ""
    py_impl: str = ""


@dataclass
class PopulateEntryResult:
    """Result for a single (platform, python_version, writer) combination."""

    target: PopulateTarget
    writer_name: str
    success: bool
    # ir_cache_key and output_cache_key are intentionally empty strings in v1.
    # The container writes cache entries directly; we don't parse keys back out.
    ir_cache_key: str = ""
    ir_slug: str = ""
    output_cache_key: str = ""
    output_slug: str = ""
    error: str = ""
    skipped: bool = False
    elapsed_seconds: float = 0.0


@dataclass
class PopulateResult:
    """Aggregate result of a populate() call."""

    entries: list[PopulateEntryResult] = field(default_factory=list)
    planned: list[PopulateTarget] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        """Count of successful entries."""
        return sum(1 for e in self.entries if e.success)

    @property
    def failed(self) -> int:
        """Count of failed (non-skipped) entries."""
        return sum(1 for e in self.entries if not e.success and not e.skipped)

    @property
    def skipped_count(self) -> int:
        """Count of skipped entries."""
        return sum(1 for e in self.entries if e.skipped)

    @property
    def total(self) -> int:
        """Total number of entries."""
        return len(self.entries)


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def build_targets(
    *,
    platforms: list[str] | None = None,
    python_versions: list[str] | None = None,
    docker_image: str | None = None,
    config_images: dict[str, str] | None = None,
) -> tuple[list[PopulateTarget], list[str]]:
    """Resolve the full list of PopulateTargets from user inputs.

    :param platforms: Docker platform strings (e.g., ["linux/amd64"]).
    :param python_versions: Python version strings (e.g., ["3.12"]).
        Defaults to DEFAULT_PYTHON_VERSIONS.
    :param docker_image: Override Docker image for all platforms.
    :param config_images: Per-platform image overrides from config.
    :returns: (targets, warnings) tuple.
    :raises ValueError: If a platform has no default image and no override,
        or if a PyPy version is requested.
    """
    warnings: list[str] = []
    effective_platforms = platforms or []
    effective_versions = python_versions or list(DEFAULT_PYTHON_VERSIONS)

    # Validate python versions: reject PyPy
    for v in effective_versions:
        if v.startswith("pp"):
            raise ValueError(
                f"PyPy targets are not supported by cache populate. Use CI to generate PyPy cache entries. Got: {v}"
            )

    targets: list[PopulateTarget] = []
    for plat in effective_platforms:
        # Skip non-Docker platforms
        if plat.startswith(("macos", "darwin", "windows", "win")):
            warnings.append(
                f"Platform '{plat}' cannot be emulated via Docker. "
                f"Run `headerkit cache populate` natively on that platform, "
                f"or use CI."
            )
            continue

        # Resolve platform mapping
        if plat not in PLATFORM_MAP:
            warnings.append(f"Unknown platform '{plat}'; attempting to use it with Docker --platform.")
            sys_platform = "linux"
            machine = plat.split("/")[-1]
        else:
            sys_platform, machine = PLATFORM_MAP[plat]

        # Resolve Docker image
        image: str | None = None
        if docker_image is not None:
            image = docker_image
        elif config_images and plat in config_images:
            image = config_images[plat]
        elif plat in DEFAULT_IMAGES:
            image = DEFAULT_IMAGES[plat]
        else:
            raise ValueError(
                f"No default Docker image for platform '{plat}'. "
                f"Provide --docker-image or configure "
                f"[tool.headerkit.cache.populate.images]."
            )

        for ver in effective_versions:
            targets.append(
                PopulateTarget(
                    docker_platform=plat,
                    python_version=ver,
                    docker_image=image,
                    python_path=python_path_for_version(ver),
                    sys_platform=sys_platform,
                    machine=machine,
                    py_impl=py_impl_for_version(ver),
                )
            )

    return targets, warnings


# ---------------------------------------------------------------------------
# cibuildwheel config parsing
# ---------------------------------------------------------------------------

# Map cibuildwheel platform tags to Docker platforms.
# Only manylinux variants are mapped since the default Docker images are
# manylinux-based. musllinux would need separate image handling.
_CIBW_PLATFORM_TO_DOCKER: dict[str, str] = {
    "manylinux_x86_64": "linux/amd64",
    "manylinux_aarch64": "linux/arm64",
    "manylinux_i686": "linux/386",
}

# cibuildwheel platform tags that are non-Docker
_CIBW_NON_DOCKER_PLATFORMS: set[str] = {
    "macosx_x86_64",
    "macosx_arm64",
    "macosx_universal2",
    "win_amd64",
    "win32",
    "win_arm64",
}


def _parse_cibw_selectors(selector_str: str) -> list[str]:
    """Split a cibuildwheel build/skip string into individual selectors.

    :param selector_str: Space-separated selector string.
    :returns: List of individual selectors.
    """
    return selector_str.split()


def _is_cibw_target_active(
    version: str,
    cibw_platform: str,
    build_selectors: list[str],
    skip_selectors: list[str],
) -> bool:
    """Check if a (version, cibw_platform) combination is active.

    :param version: Python version string like "3.12".
    :param cibw_platform: cibuildwheel platform tag (e.g. "manylinux_x86_64").
    :param build_selectors: Build selector patterns from cibuildwheel config.
    :param skip_selectors: Skip selector patterns from cibuildwheel config.
    :returns: True if the combination matches a build selector and is not skipped.
    """
    major, minor = version.split(".")
    full_tag = f"cp{major}{minor}-{cibw_platform}"
    matched_build = any(fnmatch.fnmatch(full_tag, sel) for sel in build_selectors if not sel.startswith("pp"))
    if not matched_build:
        return False
    matched_skip = any(fnmatch.fnmatch(full_tag, sel) for sel in skip_selectors)
    return not matched_skip


def parse_cibuildwheel_config(
    pyproject_path: Path,
) -> tuple[list[str], list[str], list[str]]:
    """Parse cibuildwheel config to determine platforms and Python versions.

    :param pyproject_path: Path to pyproject.toml.
    :returns: (platforms, python_versions, warnings).
    :raises ValueError: If no [tool.cibuildwheel] section found.
    """
    raw = _parse_toml(pyproject_path.read_bytes())
    tool = raw.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError("No [tool.cibuildwheel] section in pyproject.toml. Cannot auto-detect targets.")
    cibw = tool.get("cibuildwheel")
    if not isinstance(cibw, dict):
        raise ValueError("No [tool.cibuildwheel] section in pyproject.toml. Cannot auto-detect targets.")

    warnings: list[str] = []

    # Check for overrides
    if "overrides" in cibw:
        warnings.append(
            "cibuildwheel overrides detected but not supported by "
            "cache populate v1. Override targets may be missing from "
            "the populate set."
        )

    build_str = str(cibw.get("build", "cp3*"))
    skip_str = str(cibw.get("skip", ""))
    build_selectors = _parse_cibw_selectors(build_str)
    skip_selectors = _parse_cibw_selectors(skip_str)

    # Check for PyPy in build selectors
    has_pypy = any(sel.startswith("pp") for sel in build_selectors)
    if has_pypy:
        warnings.append(
            "PyPy targets detected in cibuildwheel config but not supported "
            "by cache populate v1. PyPy cache entries must be generated "
            "natively or via CI."
        )

    # Determine CPython versions
    python_versions: list[str] = []
    for ver in DEFAULT_PYTHON_VERSIONS:
        major, minor = ver.split(".")
        cp_tag = f"cp{major}{minor}"
        build_tag = f"{cp_tag}-*"

        # Check if matched by any build selector
        matched_build = any(
            fnmatch.fnmatch(build_tag, sel) or fnmatch.fnmatch(cp_tag, sel.split("-")[0])
            for sel in build_selectors
            if not sel.startswith("pp")
        )
        if not matched_build:
            continue

        # Check if excluded by any skip selector
        matched_skip = any(fnmatch.fnmatch(build_tag, sel) for sel in skip_selectors)
        if matched_skip:
            continue

        python_versions.append(ver)

    # Determine platforms
    # Build reverse map: docker_platform -> list of cibw tags
    docker_to_cibw: dict[str, list[str]] = {}
    for cibw_plat, docker_plat in _CIBW_PLATFORM_TO_DOCKER.items():
        docker_to_cibw.setdefault(docker_plat, []).append(cibw_plat)

    docker_platforms: set[str] = set()
    has_macos = False
    has_windows = False

    # Check non-Docker platforms for warnings.
    # Only warn about a platform if at least one (version, cibw_platform)
    # combination matches a build selector AND is not skipped.
    for cibw_plat in _CIBW_NON_DOCKER_PLATFORMS:
        plat_in_matrix = any(
            _is_cibw_target_active(ver, cibw_plat, build_selectors, skip_selectors) for ver in python_versions
        )

        if not plat_in_matrix:
            continue

        if "macos" in cibw_plat:
            has_macos = True
        elif "win" in cibw_plat:
            has_windows = True

    # Check Docker platforms: a docker platform is included only if at
    # least one (version, cibw_tag) combination matches a build selector
    # AND is not excluded by a skip selector.
    for docker_plat, cibw_tags in docker_to_cibw.items():
        plat_has_match = any(
            _is_cibw_target_active(ver, cibw_plat, build_selectors, skip_selectors)
            for cibw_plat in cibw_tags
            for ver in python_versions
        )

        if plat_has_match:
            docker_platforms.add(docker_plat)

    if has_macos or has_windows:
        parts: list[str] = []
        if has_macos:
            parts.append("macOS")
        if has_windows:
            parts.append("Windows")
        warnings.append(
            f"{'/'.join(parts)} targets detected in cibuildwheel config but "
            f"cannot be emulated via Docker. Run `headerkit cache populate` "
            f"natively on those platforms, or use CI."
        )

    platforms = sorted(docker_platforms)
    return platforms, python_versions, warnings


# ---------------------------------------------------------------------------
# Docker interaction helpers
# ---------------------------------------------------------------------------


def check_docker_available() -> None:
    """Verify Docker is installed and the daemon is running.

    :raises RuntimeError: If Docker is not installed or daemon not running.
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Docker is not installed or not in PATH. Install from https://docs.docker.com/get-docker/"
        ) from None

    if result.returncode != 0:
        raise RuntimeError("Docker daemon is not running. Start Docker and try again.")


def _find_headerkit_source() -> Path:
    """Find the headerkit source directory for mounting into Docker.

    :returns: Path to the directory containing pyproject.toml.
    :raises RuntimeError: If source directory cannot be found.
    """
    import headerkit as _hk

    pkg_dir = Path(_hk.__file__).parent
    src_root = pkg_dir.parent
    if (src_root / "pyproject.toml").exists():
        return src_root
    raise RuntimeError(
        "Cannot find headerkit source directory for Docker mount. "
        "Install headerkit from source (editable or sdist) to use "
        "cache populate, or specify --headerkit-version."
    )


def build_docker_command(
    *,
    target: PopulateTarget,
    project_root: Path,
    header_paths: list[str],
    writers: list[str],
    cache_dir: Path,
    headerkit_source: Path | None = None,
    headerkit_version: str | None = None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
    backend_args: list[str] | None = None,
    backend_name: str = "libclang",
    writer_options: dict[str, dict[str, object]] | None = None,
) -> list[str]:
    """Build the full docker run command for a single target.

    :param target: The PopulateTarget to run.
    :param project_root: Absolute path to the project root.
    :param header_paths: Absolute paths to header files.
    :param writers: Writer names.
    :param cache_dir: Absolute path to cache directory.
    :param headerkit_source: Path to headerkit source (for mount).
    :param headerkit_version: Version string (mutually exclusive with source).
    :param include_dirs: Include directories.
    :param defines: Preprocessor defines.
    :param backend_args: Extra backend arguments.
    :param backend_name: Backend name.
    :param writer_options: Per-writer options.
    :returns: Command list suitable for subprocess.run().
    """
    # Use POSIX paths for the container side of volume mounts and for
    # all paths inside the bash -c script (which runs on Linux).
    # The host side uses native str() so Docker Desktop can resolve it.
    project_posix = project_root.as_posix()
    cache_posix = cache_dir.as_posix()
    python_path = target.python_path

    cmd = [
        "docker",
        "run",
        "--rm",
        "--platform",
        target.docker_platform,
        "-v",
        f"{project_root!s}:{project_posix}:rw",
    ]

    # Headerkit source mount
    if headerkit_version is None and headerkit_source is not None:
        cmd.extend(["-v", f"{headerkit_source!s}:/headerkit-src:ro"])

    # External include path mounts
    if include_dirs:
        for inc_dir in include_dirs:
            inc_path = Path(inc_dir)
            # Only mount if outside project root
            try:
                inc_path.relative_to(project_root)
            except ValueError:
                inc_posix = inc_path.as_posix()
                cmd.extend(["-v", f"{inc_path!s}:{inc_posix}:ro"])

    cmd.append(target.docker_image)

    # Build the bash -c script.  Every user-controlled value is quoted
    # via shlex.quote() to prevent shell injection.
    script_parts: list[str] = []

    # Install headerkit
    if headerkit_version is not None:
        quoted_ver = shlex.quote(headerkit_version)
        script_parts.append(f"{python_path} -m pip install --quiet headerkit=={quoted_ver}")
    else:
        script_parts.append(f"{python_path} -m pip install --quiet /headerkit-src")

    # Install libclang
    script_parts.append(f"{python_path} -m headerkit install-libclang")

    # Build headerkit command
    hk_cmd_parts = [python_path, "-m", "headerkit"]
    for hp in header_paths:
        hk_cmd_parts.append(shlex.quote(Path(hp).as_posix()))
    for w in writers:
        hk_cmd_parts.extend(["-w", shlex.quote(w)])
    if include_dirs:
        for inc_dir in include_dirs:
            hk_cmd_parts.extend(["-I", shlex.quote(Path(inc_dir).as_posix())])
    if defines:
        for d in defines:
            hk_cmd_parts.extend(["-D", shlex.quote(d)])
    if backend_args:
        for arg in backend_args:
            hk_cmd_parts.extend(["--backend-arg", shlex.quote(arg)])
    if writer_options:
        for w_name, opts in writer_options.items():
            for key, value in opts.items():
                if isinstance(value, list):
                    for v in value:
                        hk_cmd_parts.extend(["--writer-opt", shlex.quote(f"{w_name}:{key}={v}")])
                else:
                    hk_cmd_parts.extend(["--writer-opt", shlex.quote(f"{w_name}:{key}={value}")])
    hk_cmd_parts.extend(["--backend", shlex.quote(backend_name)])
    hk_cmd_parts.extend(["--cache-dir", shlex.quote(cache_posix)])
    script_parts.append(" ".join(hk_cmd_parts))

    script = " && ".join(script_parts)
    cmd.extend(["bash", "-c", script])

    return cmd


# ---------------------------------------------------------------------------
# Main populate() function
# ---------------------------------------------------------------------------


def populate(
    header_paths: str | Path | list[str | Path],
    *,
    writers: list[str] | None = None,
    platforms: list[str] | None = None,
    python_versions: list[str] | None = None,
    from_cibuildwheel: bool = False,
    docker_image: str | None = None,
    headerkit_version: str | None = None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
    backend_args: list[str] | None = None,
    backend_name: str | None = None,
    writer_options: dict[str, dict[str, object]] | None = None,
    cache_dir: str | Path | None = None,
    dry_run: bool = False,
    timeout: int = 300,
) -> PopulateResult:
    """Populate cache entries for target platforms using Docker.

    :param header_paths: Header file path(s).
    :param writers: Writer names (default: ["json"]).
    :param platforms: Docker platform strings (e.g., ["linux/amd64"]).
    :param python_versions: Python versions (e.g., ["3.12", "3.13"]).
    :param from_cibuildwheel: Auto-detect from pyproject.toml.
    :param docker_image: Override Docker image for all platforms.
    :param headerkit_version: Install this version instead of mounting source.
    :param include_dirs: Include directories.
    :param defines: Preprocessor defines.
    :param backend_args: Extra backend arguments.
    :param backend_name: Backend name (default: "libclang").
    :param writer_options: Per-writer options.
    :param cache_dir: Cache directory (default: auto-detected .hkcache/).
    :param dry_run: Plan without executing.
    :param timeout: Per-container timeout in seconds.
    :returns: PopulateResult with per-entry results.
    :raises RuntimeError: If Docker is not available.
    :raises FileNotFoundError: If header file(s) not found.
    :raises ValueError: If no platforms specified and from_cibuildwheel not set.
    """
    writers = writers or ["json"]
    backend_name = backend_name or "libclang"

    # Normalize header_paths to list of Path
    if isinstance(header_paths, str | Path):
        header_paths_list: list[str | Path] = [header_paths]
    else:
        header_paths_list = list(header_paths)

    resolved_headers: list[Path] = []
    for hp in header_paths_list:
        hp_str = str(hp)
        if hp_str == "-":
            raise ValueError("cache populate requires file paths, not stdin. Write content to a file first.")
        p = Path(hp_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Header file not found: {hp}")
        resolved_headers.append(p)

    # Determine project root
    project_root = _find_project_root(resolved_headers[0].parent)

    # Resolve cache dir
    resolved_cache_dir: Path
    if cache_dir is not None:
        resolved_cache_dir = Path(cache_dir).resolve()
    else:
        resolved_cache_dir = project_root / ".hkcache"

    # Load populate config from pyproject.toml for defaults
    config_images: dict[str, str] | None = None
    cfg = _empty_populate_config()
    pyproject_for_config = project_root / "pyproject.toml"
    if pyproject_for_config.exists():
        cfg = load_populate_config(pyproject_for_config)
        if cfg.images:
            config_images = cfg.images

    # Apply config defaults for platforms, python_versions, and timeout
    # when the caller did not provide explicit values.
    if platforms is None and not from_cibuildwheel and cfg.platforms:
        platforms = cfg.platforms
    if python_versions is None and cfg.python_versions:
        python_versions = cfg.python_versions
    if timeout == 300 and cfg.timeout is not None:
        timeout = cfg.timeout

    # Resolve targets
    all_warnings: list[str] = []

    effective_platforms: list[str] | None
    effective_versions: list[str] | None
    if from_cibuildwheel:
        pyproject_path = project_root / "pyproject.toml"
        cibw_platforms, cibw_versions, cibw_warnings = parse_cibuildwheel_config(pyproject_path)
        all_warnings.extend(cibw_warnings)
        # cibuildwheel values are defaults; CLI overrides
        effective_platforms = platforms if platforms else cibw_platforms
        effective_versions = python_versions if python_versions else cibw_versions
    else:
        effective_platforms = platforms
        effective_versions = python_versions

    if not effective_platforms:
        raise ValueError("Specify at least one --platform or use --cibuildwheel.")

    targets, build_warnings = build_targets(
        platforms=effective_platforms,
        python_versions=effective_versions,
        docker_image=docker_image,
        config_images=config_images,
    )
    all_warnings.extend(build_warnings)

    result = PopulateResult(warnings=all_warnings)

    # Dry run: return planned targets without executing
    if dry_run:
        result.planned = targets
        return result

    # Real execution: check Docker, resolve headerkit source
    check_docker_available()

    hk_source: Path | None = None
    if headerkit_version is None:
        hk_source = _find_headerkit_source()

    header_path_strs = [str(h) for h in resolved_headers]

    for target in targets:
        start_time = time.monotonic()

        cmd = build_docker_command(
            target=target,
            project_root=project_root,
            headerkit_source=hk_source,
            headerkit_version=headerkit_version,
            header_paths=header_path_strs,
            writers=writers,
            cache_dir=resolved_cache_dir,
            include_dirs=include_dirs,
            defines=defines,
            backend_args=backend_args,
            backend_name=backend_name,
            writer_options=writer_options,
        )

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start_time

            if proc.returncode == 0:
                # One entry per writer for this target
                for w in writers:
                    result.entries.append(
                        PopulateEntryResult(
                            target=target,
                            writer_name=w,
                            success=True,
                            elapsed_seconds=elapsed,
                        )
                    )
            else:
                for w in writers:
                    result.entries.append(
                        PopulateEntryResult(
                            target=target,
                            writer_name=w,
                            success=False,
                            error=proc.stderr.strip(),
                            elapsed_seconds=elapsed,
                        )
                    )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start_time
            for w in writers:
                result.entries.append(
                    PopulateEntryResult(
                        target=target,
                        writer_name=w,
                        success=False,
                        error=(f"Container timed out after {timeout}s. Increase with --timeout."),
                        elapsed_seconds=elapsed,
                    )
                )

    return result


# ---------------------------------------------------------------------------
# Config file support
# ---------------------------------------------------------------------------


class PopulateConfig:
    """Populate-specific config from pyproject.toml or .headerkit.toml."""

    __slots__ = ("platforms", "python_versions", "timeout", "images")

    def __init__(
        self,
        *,
        platforms: list[str],
        python_versions: list[str],
        timeout: int | None,
        images: dict[str, str],
    ) -> None:
        self.platforms = platforms
        self.python_versions = python_versions
        self.timeout = timeout
        self.images = images

    def __getitem__(self, key: str) -> object:
        return getattr(self, key)


def _empty_populate_config() -> PopulateConfig:
    """Return an empty PopulateConfig."""
    return PopulateConfig(
        platforms=[],
        python_versions=[],
        timeout=None,
        images={},
    )


def load_populate_config(config_path: Path) -> PopulateConfig:
    """Load populate-specific config from a config file.

    :param config_path: Path to pyproject.toml or .headerkit.toml.
    :returns: PopulateConfig with parsed values.
    """
    raw = _parse_toml(config_path.read_bytes())

    # Extract the right section
    if config_path.name == "pyproject.toml":
        tool = raw.get("tool", {})
        if not isinstance(tool, dict):
            return _empty_populate_config()
        hk = tool.get("headerkit", {})
        if not isinstance(hk, dict):
            return _empty_populate_config()
        cache = hk.get("cache", {})
    else:
        cache = raw.get("cache", {})

    if not isinstance(cache, dict):
        return _empty_populate_config()
    populate_section = cache.get("populate", {})
    if not isinstance(populate_section, dict):
        return _empty_populate_config()

    platforms_raw = populate_section.get("platforms", [])
    python_versions_raw = populate_section.get("python_versions", [])
    timeout_val = populate_section.get("timeout")
    images_section = populate_section.get("images", {})

    return PopulateConfig(
        platforms=(list(platforms_raw) if isinstance(platforms_raw, list) else []),
        python_versions=(list(python_versions_raw) if isinstance(python_versions_raw, list) else []),
        timeout=int(timeout_val) if timeout_val is not None else None,
        images=(dict(images_section) if isinstance(images_section, dict) else {}),
    )
