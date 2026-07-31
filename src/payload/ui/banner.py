"""Piccoli tocchi estetici per rendere il tool più piacevole da usare,
non solo funzionale. Usato da 'pld init' e opzionalmente da 'pld' senza
sottocomandi."""
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
    "usa 'pld watch' per ricompilare automaticamente ad ogni salvataggio",
    "'pld doctor' prima di un batch grosso ti evita brutte sorprese a metà",
    "'--dry-run' ti mostra cosa succederebbe senza toccare nulla",
    "'pld golden update' congela un riferimento SOLO quando il cambio è voluto",
    "'pld plugin new' genera lo scaffold di un plugin già pronto da installare",
    "'-vv' mostra anche il timing di parse/emit, utile per trovare tabelle lente",
]


def print_banner(console: Console) -> None:
    console.print(Text(BANNER, style="cyan"), justify="center")


def random_tip() -> str:
    return random.choice(TIPS)
