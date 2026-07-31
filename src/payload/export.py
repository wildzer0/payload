"""'pld export': pacchetto portabile di sorgenti + config (+ opzionalmente
history) in un unico zip. Utile per condividere un sotto-progetto o farne
backup fuori da git, senza dover spiegare a mano quali cartelle servono."""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from payload.core.config import GLOBAL_CONFIG_FILENAME, SIDECAR_SUFFIX
from payload.core.history import HISTORY_DIRNAME

logger = logging.getLogger(__name__)


def export_project(
    root: Path,
    sources: list[Path],
    output_zip: Path,
    include_history: bool = False,
) -> Path:
    """Crea output_zip con: table-tool.toml (se esiste), ogni sorgente in
    `sources` con il relativo sidecar (se esiste), e opzionalmente tutto
    .payload_history/. I percorsi nello zip sono relativi a root."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        global_config = root / GLOBAL_CONFIG_FILENAME
        if global_config.exists():
            zf.write(global_config, arcname=global_config.relative_to(root))

        for src in sources:
            zf.write(src, arcname=src.relative_to(root))
            sidecar = src.parent / f"{src.stem}{SIDECAR_SUFFIX}"
            if sidecar.exists():
                zf.write(sidecar, arcname=sidecar.relative_to(root))

        if include_history:
            history_dir = root / HISTORY_DIRNAME
            if history_dir.exists():
                for f in history_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, arcname=f.relative_to(root))

    logger.info("Export creato: %s (%d sorgenti, history=%s)", output_zip, len(sources), include_history)
    return output_zip
