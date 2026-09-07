"""Base class and common helpers for Headerkit writers."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, ClassVar

from headerkit.ir import Header, SourceUnit, strip_padding_fields
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions

#: One-line stand-in for a generated multi-line block inside a dedented
#: template. See :func:`render_block_template` for why the substitution cannot
#: be an ordinary f-string interpolation.
DEDENT_BLOCK = "_HK_BLOCK_"


def render_block_template(template: str, *blocks: str) -> str:
    """Dedent ``template``, then substitute ``blocks`` for its placeholders.

    ``textwrap.dedent`` measures the string *after* interpolation, so dropping a
    multi-line block straight into an indented template makes the block's own
    indentation the common prefix and strips the template down to it -- the
    emitted file then begins with the template's leftover indent and does not
    parse. It only misbehaves once the block has a second line, which is why the
    single-item case looks correct and the defect survives review. Dedenting
    against a one-line token and substituting afterwards keeps the template's
    indentation and the block's independent of each other.

    :param template: An f-string carrying one :data:`DEDENT_BLOCK` token per
        block, each written at the template's own indentation.
    :param blocks: Replacement text, substituted into the tokens left to right.
        A block may be empty, which leaves the token's line blank.
    """
    rendered = textwrap.dedent(template)
    for block in blocks:
        rendered = rendered.replace(DEDENT_BLOCK, block, 1)
    return rendered


@dataclass(frozen=True)
class WriterOption:
    """Specification of a writer-configurable option or parameter."""

    name: str
    description: str
    default: Any = None
    choices: tuple[str, ...] | None = None
    type: type = str

    def coerce(self, val: Any) -> Any:
        """Coerce a raw value (e.g. from CLI string) to this option's expected type."""
        if val is None:
            return None
        if self.type is bool:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                lower = val.strip().lower()
                if lower in ("true", "1", "yes", "on"):
                    return True
                elif lower in ("false", "0", "no", "off"):
                    return False
                raise ValueError(f"Cannot coerce {val!r} to bool for option {self.name!r} (expected 'true'/'false')")
            return bool(val)
        if self.type is int:
            if isinstance(val, int) and not isinstance(val, bool):
                return val
            return int(val)
        if self.type is float:
            if isinstance(val, int | float) and not isinstance(val, bool):
                return float(val)
            return float(val)
        if self.type is str:
            return str(val) if not isinstance(val, str) else val
        if self.type is list:
            if isinstance(val, list):
                return val
            if isinstance(val, tuple | set):
                return list(val)
            if isinstance(val, str):
                return [val]
            return [val]
        return self.type(val)


def coerce_writer_options(
    options: dict[str, Any],
    supported_options: tuple[WriterOption, ...] | list[WriterOption],
) -> dict[str, Any]:
    """Coerce option values using the given supported WriterOption specifications."""
    if not supported_options:
        return dict(options)
    specs = {opt.name: opt for opt in supported_options}
    result: dict[str, Any] = {}
    for k, v in options.items():
        if k in specs:
            opt = specs[k]
            if opt.type is list:
                result[k] = opt.coerce(v)
            elif isinstance(v, list):
                result[k] = [opt.coerce(elem) for elem in v]
            else:
                result[k] = opt.coerce(v)
        else:
            result[k] = v
    return result


class BaseWriter:
    """Base class providing unified layout generation for all output writers."""

    name: str = ""
    format_description: str = ""
    default_output_pattern: str = "{dir}/{stem}.txt"
    default_extension: str = ".txt"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = ()

    #: Whether this writer reconstructs record layout itself and therefore needs
    #: unnamed-bitfield padding. Only the ctypes writer does; a writer that emits
    #: C source hands layout back to the C compiler and must not gain a spurious
    #: nameless member. The safe default is to strip padding before rendering.
    consumes_padding_fields: ClassVar[bool] = False

    def write_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> ProjectLayout:
        """Convert parsed unit IR into a complete ProjectLayout."""
        unit = self._prepare(unit)

        opts = options or ScaffoldOptions(target_language=self.name, layout="file")
        if opts.layout not in self.supported_layouts:
            raise ValueError(
                f"Writer '{self.name}' does not support layout '{opts.layout}'. "
                f"Supported layouts: {list(self.supported_layouts)}"
            )

        if self.supported_options and opts.options:
            opts.options = coerce_writer_options(opts.options, self.supported_options)

        if opts.layout == "file":
            return self._write_single_file_layout(unit, opts)
        elif opts.layout in ("package", "project"):
            return self._write_package_layout(unit, opts)
        return self._write_custom_layout(unit, opts)

    def _write_single_file_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Default single file layout: one file containing the rendered output."""
        content = self._render(unit)
        filename = f"{options.package_name}{self.default_extension}"
        return ProjectLayout(files=[OutputFile(path=filename, content=content)])

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Generate full package layout. Subclasses should override this."""
        return self._write_single_file_layout(unit, options)

    def _write_custom_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Generate custom layout defined by subclass."""
        return self._write_package_layout(unit, options)

    def _prepare(self, unit: SourceUnit | Header) -> SourceUnit | Header:
        """Normalize IR before rendering.

        Every entry point must route through this, including a ``write``
        override, or unnamed-bitfield padding reaches a writer that cannot
        represent it. ``test_no_padding_leaks_into_writers`` enforces that.
        """
        if not self.consumes_padding_fields:
            return strip_padding_fields(unit)
        return unit

    def _render(self, unit: SourceUnit | Header) -> str:
        """Render unit to string representation. Subclasses implement this."""
        raise NotImplementedError

    def write(self, header: Header | SourceUnit) -> str:
        """Convert parsed header IR to the target output format.

        Delegates to write_layout(layout='file') to satisfy the Zero-Dual-System Rule.
        """
        layout = self.write_layout(header, ScaffoldOptions(package_name="output", layout="file"))
        return layout.files[0].content
