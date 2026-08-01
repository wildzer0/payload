"""Implementation of 'pld init': creates the minimal scaffold of a project."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

import tomli_w

from payload.core.config import GLOBAL_CONFIG_FILENAME
from payload.core.local_plugins import LOCAL_PLUGINS_DIRNAME

LOCAL_PLUGINS_README = '''# Local plugins

.py files placed here are discovered automatically by payload, no
`pip install` needed — see src/payload/docs/PLUGINS.md, section
"Local plugins without pip install", for the full guide.

Minimal convention:

    class MyWriter:
        name = "my_writer"
        extension = ".mio"
        api_version = "1.0"

        def emit(self, ir, out_path, config):
            out_path.write_bytes(ir.data)
            return out_path

    WRITER = MyWriter

If the plugin needs third-party libraries not already installed,
declare them with a module-level REQUIRES:

    REQUIRES = ["numpy>=1.20"]

`pld plugin install-deps <file>` installs them with pip.
'''

# Sentinel to distinguish "the caller specified nothing"
# (init_project() with no arguments -> uses the static template, with
# an explicit 'writer = "bin"', meant to give a working default right
# away) from "the caller EXPLICITLY chose no preference" (e.g. the
# wizard, with the user leaving the prompt empty -> writer=None is
# intentional, must not silently fall back to 'bin'). None alone isn't
# enough to distinguish the two cases, hence a sentinel distinct from None.
_UNSET = object()


def is_nonempty_existing_dir(path: Path) -> bool:
    return path.exists() and path.is_dir() and any(path.iterdir())


def _render_project_section(name: str, description: str | None) -> str:
    """A project ALWAYS has a name — if the user doesn't pass one
    explicitly to 'pld init', the caller has already resolved the
    default (the folder name) before getting here. tomli_w takes care
    of correct TOML escaping for name/description (they can contain
    quotes, unicode, etc.)."""
    project: dict = {"name": name}
    if description:
        project["description"] = description
    return tomli_w.dumps({"project": project})


def _render_toml(writer: str | None, byte_order: str) -> str:
    lines = ["[defaults]"]
    if writer:
        lines.append(f'writer = "{writer}"')
    lines.append(f'byte_order = "{byte_order}"')
    lines.append("")
    lines.append("[toolchain]")
    lines.append('compiler = "gcc"')
    lines.append("compiler_flags = []")
    lines.append('objcopy = "objcopy"')
    lines.append("# Only required if you use the 'obj' writer (compiles .c -> linkable .o).")
    lines.append('# objcopy_target = "elf32-littlearm"')
    lines.append('# objcopy_arch = "arm"')
    return "\n".join(lines) + "\n"


def init_project(
    target_dir: Path,
    force: bool = False,
    include_local_plugins: bool = True,
    include_example: bool = True,
    writer=_UNSET,
    byte_order: str = "little",
    project_name: str | None = None,
    project_description: str | None = None,
) -> list[Path]:
    """Creates target_dir if it doesn't exist, then table-tool.toml,
    build/, (optionally) local_plugins/, and a sample table inside it.
    Returns the list of created files/dirs. Doesn't overwrite anything
    unless force=True.

    writer: if omitted (default), uses the static template with an
    explicit 'writer = "bin"' — historical behavior, meant to make
    'pld build example_table.raw' work right away with no flags. Pass
    None explicitly (e.g. from the wizard, when the user expresses no
    preference) to NOT have that default and leave resolution to the
    reader.default_writer/--to mechanism.

    project_name: if omitted, the project takes the basename of
    target_dir as its name — a project ALWAYS has a name (shown in the
    Dashboard), but that's never a reason to refuse creating the
    project: the folder name is already a reasonable choice made by
    the user (same convention as git/npm/cargo).

    SECURITY NOTE: this function NEVER decides whether it's safe to
    write to target_dir (e.g. if it's the cwd and already has other
    stuff in it) — that decision is up to the caller (cli.py), which
    asks for explicit confirmation before calling this function. Here
    it's trusted that permission has already been given."""
    created: list[Path] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    templates = importlib.resources.files("payload.templates.init")

    config_dest = target_dir / GLOBAL_CONFIG_FILENAME
    if not config_dest.exists() or force:
        if writer is _UNSET:
            config_content = (templates / "table-tool.toml").read_text()
        else:
            config_content = _render_toml(writer, byte_order)
        resolved_project_name = project_name or target_dir.resolve().name
        config_content = _render_project_section(resolved_project_name, project_description) + "\n" + config_content
        # newline="\n": without it, Path.write_text() translates '\n' to
        # os.linesep on write (CRLF on Windows), while everything that reads
        # it back (Path.read_text(), the web source editor's raw_bytes.decode())
        # doesn't agree on whether that translation happened — LF is forced
        # here so the file is byte-identical across platforms, matching the
        # templates in the repo (.gitattributes already forces eol=lf there).
        config_dest.write_text(config_content, newline="\n")
        created.append(config_dest)

    build_dir = target_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    created.append(build_dir)

    if include_local_plugins:
        local_plugins_dir = target_dir / LOCAL_PLUGINS_DIRNAME
        local_plugins_dir.mkdir(parents=True, exist_ok=True)
        created.append(local_plugins_dir)
        readme_dest = local_plugins_dir / "README.md"
        if not readme_dest.exists() or force:
            readme_dest.write_text(LOCAL_PLUGINS_README, newline="\n")
            created.append(readme_dest)

    if include_example:
        example_dest = target_dir / "example_table.raw"
        if not example_dest.exists() or force:
            example_dest.write_text((templates / "example_table.raw").read_text(), newline="\n")
            created.append(example_dest)

    return created
