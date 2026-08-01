"""Small aesthetic touches to make the tool nicer to use, not just
functional. Used by 'pld init' and optionally by 'pld' with no
subcommands."""
from __future__ import annotations

import random

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

BANNER = r"""
        .  *  .    ___    .  *  .
    *  .   _  .-'"   '-.       *
       .  ( )'   PAYLOAD  '.  .
    .   *  '-.___________.-'  *   .
"""

TIPS = [
    "use 'pld watch' to rebuild automatically on every save",
    "'pld doctor' before a big batch saves you from nasty surprises halfway through",
    "'--dry-run' shows you what would happen without touching anything",
    "'pld golden update' freezes a reference only when the change is intentional",
    "'pld plugin new' generates the scaffold of a plugin ready to install",
    "'-vv' also shows parse/emit timing, useful to find slow tables",
]


def print_banner(console: Console) -> None:
    console.print(Text(BANNER, style="cyan"), justify="center")


def random_tip() -> str:
    return random.choice(TIPS)
