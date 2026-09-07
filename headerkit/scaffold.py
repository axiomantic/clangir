"""Polyglot project scaffolding engine with unified layout and BYOScaffolder architecture."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from headerkit.hooks import HookDispatcher, Priority, hook
from headerkit.ir import Declaration, Function, Header, SourceUnit


@dataclass(frozen=True)
class OutputFile:
    """A single file to be written in a project layout."""

    path: str
    content: str
    is_executable: bool = False
    #: Never clobber this file if it already exists on disk. Set on artifacts a human
    #: is expected to edit -- work-order test stubs, the work order itself -- because a
    #: generator that eats the work it asked for is worse than no generator.
    preserve_existing: bool = False


@dataclass
class ProjectLayout:
    """A collection of output files comprising a project or package layout."""

    files: list[OutputFile] = field(default_factory=list)

    def get_file(self, path: str) -> OutputFile | None:
        """Find an output file by its relative project path."""
        for f in self.files:
            if f.path == path:
                return f
        return None

    def write_to_disk(self, target_dir: Path | str, *, overwrite: bool = True) -> list[Path]:
        """Write all files in this layout to the destination directory."""
        written: list[Path] = []
        base = Path(target_dir).resolve()
        for f in self.files:
            p = (base / f.path).resolve()
            if not p.is_relative_to(base):
                raise ValueError(f"Path traversal detected: {f.path}")
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and (f.preserve_existing or not overwrite):
                continue
            p.write_text(f.content, encoding="utf-8")
            if f.is_executable:
                p.chmod(p.stat().st_mode | 0o111)
            written.append(p)
        return written


def extract_function_names(unit: SourceUnit | Header) -> list[str]:
    """Extract top-level function names for test stubs and tripwires."""
    decls: list[Declaration] = getattr(unit, "declarations", [])
    return [d.name for d in decls if isinstance(d, Function) and d.name]


@dataclass
class ScaffoldOptions:
    """Configuration options for project scaffolding."""

    package_name: str = "bindings"
    target_language: str = "nim"
    layout: str = "file"  # Configurable layout (e.g. "file", "package", "project")
    options: dict[str, Any] = field(default_factory=dict)
    interactive: bool = False
    extra_context: dict[str, Any] = field(default_factory=dict)
    test_type: str = "both"  # Backwards compatibility alias for options["test_type"]
    test_runner: str = "tripwire"  # Backwards compatibility alias

    def __post_init__(self) -> None:
        if "test_type" not in self.options and self.test_type != "both":
            self.options["test_type"] = self.test_type
        elif "test_type" in self.options:
            self.test_type = str(self.options["test_type"])

    def get_option(self, name: str, default: Any = None) -> Any:
        """Get a writer-specific option value."""
        if name in self.options:
            return self.options[name]
        if hasattr(self, name):
            return getattr(self, name)
        return default


class BYOScaffolder:
    """Protocol and base class for Bring-Your-Own-Scaffolder plugins."""

    def scaffold(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        """Generate a ProjectLayout for the given SourceUnit and options."""
        raise NotImplementedError


class StdlibScaffolder(BYOScaffolder):
    """Zero-dependency standard library project scaffolder."""

    _EXT_MAP: dict[str, str] = {
        "nim": ".nim",
        "mojo": ".mojo",
        "ctypes": ".py",
        "cffi": ".py",
        "cython": ".pyx",
        "cshim": ".cpp",
        "json": ".json",
        "lua": ".lua",
        "luajit": ".lua",
        "python": ".py",
    }

    def _resolve_target(self, target_language: str) -> str:
        target = target_language.lower()
        if target == "python":
            return "ctypes"
        if target == "luajit":
            return "lua"
        return target

    def scaffold(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        """Generate project layout using built-in templates."""
        from headerkit.writers import get_writer

        writer_target = self._resolve_target(options.target_language)
        writer = get_writer(writer_target)
        if hasattr(writer, "write_layout"):
            return writer.write_layout(unit, options)

        return self._scaffold_single_file(unit, options, writer_target)

    def _scaffold_single_file(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
        target: str,
    ) -> ProjectLayout:
        from headerkit.writers import get_writer

        writer_target = self._resolve_target(target)
        writer = get_writer(writer_target)
        rendered = writer.write(unit)
        ext = self._EXT_MAP.get(target.lower(), ".txt")
        filename = f"{options.package_name}{ext}"
        return ProjectLayout(files=[OutputFile(path=filename, content=rendered)])


@hook("scaffold_project", priority=Priority.STANDARD)
def _default_scaffold(
    unit: SourceUnit | Header,
    options: ScaffoldOptions,
    _context: Any = None,
    **_kwargs: Any,
) -> ProjectLayout | None:
    """Default standard library project scaffolder hook."""
    return StdlibScaffolder().scaffold(unit, options)


def scaffold(
    unit: SourceUnit | Header,
    options: ScaffoldOptions,
    context: Any = None,
) -> ProjectLayout:
    """Scaffold a project layout for a source unit, dispatching to registered hooks."""
    from headerkit.hooks import PipelineContext

    if context is None:
        ctx = PipelineContext(
            writer=options.target_language,
            target=options.target_language,
            layout=options.layout,
            options=options.options,
        )
    else:
        if getattr(context, "layout", None) is None:
            ctx = PipelineContext(
                backend=getattr(context, "backend", None),
                writer=getattr(context, "writer", None) or options.target_language,
                target=getattr(context, "target", None) or options.target_language,
                layout=options.layout,
                language=getattr(context, "language", None),
                classification=getattr(context, "classification", None),
                runtime=getattr(context, "runtime", None),
                options=getattr(context, "options", None) or options.options,
            )
        else:
            ctx = context

    dispatcher = HookDispatcher()
    unit = dispatcher.waterfall("transform_unit", unit, context=ctx)
    result = dispatcher.first_result("scaffold_project", unit, options, context=ctx)
    if isinstance(result, ProjectLayout):
        return result
    return StdlibScaffolder().scaffold(unit, options)


def prompt_scaffold_options(
    defaults: ScaffoldOptions | None = None,
    *,
    is_tty: bool | None = None,
) -> ScaffoldOptions:
    """TTY-aware interactive prompt wizard for scaffolding options."""
    opts = defaults if defaults is not None else ScaffoldOptions()
    tty_active = is_tty if is_tty is not None else (sys.stdin.isatty() and sys.stdout.isatty())

    if not tty_active:
        return opts

    try:
        raw_pkg = input(f"Package name [{opts.package_name}]: ").strip()
        pkg_name = raw_pkg or opts.package_name

        raw_lang = input(f"Target language (nim, mojo, ctypes, cffi) [{opts.target_language}]: ").strip()
        target_lang = raw_lang or opts.target_language

        raw_layout = input(f"Layout (file, package) [{opts.layout}]: ").strip()
        layout = raw_layout or opts.layout

        raw_test = input(f"Test generation (both, tripwire, unit, none) [{opts.test_type}]: ").strip()
        test_type = raw_test or opts.test_type

        return ScaffoldOptions(
            package_name=pkg_name,
            target_language=target_lang,
            layout=layout,
            options=dict(opts.options),
            test_type=test_type,
            test_runner=opts.test_runner,
            interactive=True,
            extra_context=opts.extra_context,
        )
    except (EOFError, KeyboardInterrupt):
        return opts
