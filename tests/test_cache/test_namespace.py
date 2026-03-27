"""Tests for environment-namespaced cache sidecars."""

from __future__ import annotations

import importlib.metadata
import platform
import re
import sys
import textwrap
from pathlib import Path

from headerkit.cache import (
    compute_hash,
    default_namespace,
    is_up_to_date,
    is_up_to_date_batch,
    save_hash,
)


class TestDefaultNamespace:
    """Tests for default_namespace()."""

    def test_default_namespace_format(self) -> None:
        """default_namespace() returns {impl}-{major}{minor}-{platform}-{machine}."""
        result = default_namespace()
        expected = (
            f"{sys.implementation.name}"
            f"-{sys.version_info.major}{sys.version_info.minor}"
            f"-{sys.platform}"
            f"-{platform.machine()}"
        )
        assert result == expected

    def test_default_namespace_matches_pattern(self) -> None:
        """default_namespace() matches the expected pattern structure."""
        result = default_namespace()
        # Pattern: word-digits-word-word (e.g. cpython-312-darwin-arm64)
        assert re.fullmatch(r"[a-z]+-\d+-[a-z0-9_]+-[a-zA-Z0-9_]+", result) is not None


class TestComputeHashWithNamespace:
    """Tests for namespace parameter in compute_hash()."""

    def test_compute_hash_with_namespace_differs(self, sample_header: Path) -> None:
        """compute_hash() with a namespace produces a different hash than without."""
        hash_no_ns = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
        )
        hash_with_ns = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            namespace="cpython-312-darwin-arm64",
        )
        assert hash_no_ns != hash_with_ns

    def test_compute_hash_same_namespace_same_hash(self, sample_header: Path) -> None:
        """Same namespace produces the same hash on repeated calls."""
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            namespace="cpython-312-darwin-arm64",
        )
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            namespace="cpython-312-darwin-arm64",
        )
        assert h1 == h2

    def test_different_namespaces_different_hashes(self, sample_header: Path) -> None:
        """Different namespace strings produce different hashes."""
        h1 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            namespace="cpython-312-darwin-arm64",
        )
        h2 = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            namespace="cpython-311-linux-x86_64",
        )
        assert h1 != h2


class TestSidecarPathWithNamespace:
    """Tests for namespaced sidecar file paths."""

    def test_sidecar_path_with_namespace(self, sample_header: Path, sample_output: Path) -> None:
        """save_hash() with namespace creates {name}.{namespace}.hkcache file."""
        ns = "cpython-312-darwin-arm64"
        result = save_hash(
            output_path=sample_output,
            header_paths=[sample_header],
            writer_name="cffi",
            namespace=ns,
        )
        expected_sidecar = sample_output.parent / (sample_output.name + f".{ns}.hkcache")
        assert result == expected_sidecar
        assert expected_sidecar.exists()

        # Verify sidecar content matches expected TOML metadata
        expected_hash = compute_hash(
            header_paths=[sample_header],
            writer_name="cffi",
            namespace=ns,
        )
        try:
            version = importlib.metadata.version("headerkit")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        expected_toml = textwrap.dedent(f"""\
            [headerkit-cache]
            hash = "{expected_hash}"
            version = "{version}"
            writer = "cffi"
            namespace = "{ns}"
        """)
        actual_toml = expected_sidecar.read_text(encoding="utf-8")
        assert actual_toml == expected_toml

    def test_sidecar_path_without_namespace(self, sample_header: Path, sample_output: Path) -> None:
        """save_hash() without namespace creates {name}.hkcache (backward compat)."""
        result = save_hash(
            output_path=sample_output,
            header_paths=[sample_header],
            writer_name="cffi",
        )
        expected_sidecar = sample_output.parent / (sample_output.name + ".hkcache")
        assert result == expected_sidecar
        assert expected_sidecar.exists()


class _EmbeddableWriter:
    """Stub writer with hash_comment_format() support for testing."""

    def hash_comment_format(self) -> str:
        return "# {line}"


class TestNamespaceForcesSidecar:
    """Tests that namespace forces sidecar even when writer supports embedding."""

    def test_namespace_with_embeddable_writer_uses_sidecar(self, sample_header: Path, sample_output: Path) -> None:
        """save_hash() with namespace + embeddable writer uses sidecar, not embedded."""
        ns = "cpython-312-darwin-arm64"
        original_content = sample_output.read_text(encoding="utf-8")

        result = save_hash(
            output_path=sample_output,
            header_paths=[sample_header],
            writer_name="cffi",
            writer=_EmbeddableWriter(),  # type: ignore[arg-type]
            namespace=ns,
        )

        # Returned path should be the namespaced sidecar, not the output file
        expected_sidecar = sample_output.parent / (sample_output.name + f".{ns}.hkcache")
        assert result == expected_sidecar
        assert expected_sidecar.exists()

        # Output file should NOT have embedded hash comments prepended
        assert sample_output.read_text(encoding="utf-8") == original_content


class TestNamespaceIsolation:
    """Tests for namespace isolation in is_up_to_date()."""

    def test_namespace_isolation(self, sample_header: Path, sample_output: Path) -> None:
        """is_up_to_date() with namespace A doesn't see sidecar from namespace B."""
        ns_a = "cpython-312-darwin-arm64"
        ns_b = "cpython-311-linux-x86_64"

        # Save hash with namespace A
        save_hash(
            output_path=sample_output,
            header_paths=[sample_header],
            writer_name="cffi",
            namespace=ns_a,
        )

        # Check with namespace A: should be True
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                namespace=ns_a,
            )
            is True
        )

        # Check with namespace B: should be False (no sidecar for B)
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
                namespace=ns_b,
            )
            is False
        )

    def test_no_namespace_does_not_see_namespaced_sidecar(self, sample_header: Path, sample_output: Path) -> None:
        """is_up_to_date() without namespace doesn't see a namespaced sidecar."""
        ns = "cpython-312-darwin-arm64"

        save_hash(
            output_path=sample_output,
            header_paths=[sample_header],
            writer_name="cffi",
            namespace=ns,
        )

        # Check without namespace: should be False
        assert (
            is_up_to_date(
                output_path=sample_output,
                header_paths=[sample_header],
                writer_name="cffi",
            )
            is False
        )


class TestIsUpToDateBatchWithNamespace:
    """Tests for namespace support in is_up_to_date_batch()."""

    def test_is_up_to_date_batch_with_namespace(self, sample_header: Path, tmp_path: Path) -> None:
        """Batch function supports namespace key in check dicts."""
        output = tmp_path / "bindings.py"
        output.write_text("# generated\n", encoding="utf-8")
        ns = "cpython-312-darwin-arm64"

        save_hash(
            output_path=output,
            header_paths=[sample_header],
            writer_name="cffi",
            namespace=ns,
        )

        result = is_up_to_date_batch(
            [
                {
                    "output_path": output,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                    "namespace": ns,
                }
            ]
        )
        assert result == {str(output): True}

    def test_batch_namespace_mismatch_returns_false(self, sample_header: Path, tmp_path: Path) -> None:
        """Batch check with wrong namespace returns False."""
        output = tmp_path / "bindings.py"
        output.write_text("# generated\n", encoding="utf-8")

        save_hash(
            output_path=output,
            header_paths=[sample_header],
            writer_name="cffi",
            namespace="cpython-312-darwin-arm64",
        )

        result = is_up_to_date_batch(
            [
                {
                    "output_path": output,
                    "header_paths": [sample_header],
                    "writer_name": "cffi",
                    "namespace": "cpython-311-linux-x86_64",
                }
            ]
        )
        assert result == {str(output): False}
