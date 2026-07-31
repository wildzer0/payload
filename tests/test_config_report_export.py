import zipfile
from pathlib import Path

from payload.core.config import resolve_config_with_provenance
from payload.core.discovery import discover_table_sources


def test_provenance_default_when_no_config(tmp_path):
    config, provenance = resolve_config_with_provenance(tmp_path)
    assert provenance["defaults.writer"] == "default"
    assert provenance["toolchain.compiler"] == "default"


def test_provenance_global_when_set_in_global_toml(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "hex"\n')
    config, provenance = resolve_config_with_provenance(tmp_path)
    assert config.defaults.writer == "hex"
    assert "globale" in provenance["defaults.writer"]


def test_provenance_sidecar_overrides_global(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "bin"\n')
    src = tmp_path / "t.raw"
    src.write_text("x")
    (tmp_path / "t.config.toml").write_text('[defaults]\nwriter = "hex"\n')

    config, provenance = resolve_config_with_provenance(tmp_path, source_path=src)
    assert config.defaults.writer == "hex"
    assert "sidecar" in provenance["defaults.writer"]
    # un campo non toccato dal sidecar resta "globale", non diventa "sidecar" per errore
    # (qui non c'è altro nel globale, quindi resta default — verifichiamo comunque
    # che il merge non abbia sovrascritto la provenance di campi non dichiarati)
    assert provenance["toolchain.compiler"] == "default"


def test_provenance_without_table_argument_ignores_sidecar(tmp_path):
    (tmp_path / "table-tool.toml").write_text('[defaults]\nwriter = "bin"\n')
    src = tmp_path / "t.raw"
    src.write_text("x")
    (tmp_path / "t.config.toml").write_text('[defaults]\nwriter = "hex"\n')

    # senza source_path, il sidecar non deve essere applicato
    config, provenance = resolve_config_with_provenance(tmp_path)
    assert config.defaults.writer == "bin"


def test_export_zip_contains_sources_and_config(tmp_path):
    (tmp_path / "table-tool.toml").write_text("[defaults]\n")
    sensors = tmp_path / "sensors"
    sensors.mkdir()
    src = sensors / "temp.raw"
    src.write_text("x")

    sources = discover_table_sources(tmp_path, {".raw"}, tmp_path / "build")
    files_to_zip = list(sources) + [tmp_path / "table-tool.toml"]

    out_zip = tmp_path / "out.zip"
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files_to_zip:
            zf.write(f, arcname=f.relative_to(tmp_path))

    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
    assert names == {"sensors/temp.raw", "table-tool.toml"}


def test_export_zip_roundtrip_preserves_content(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("contenuto originale")

    out_zip = tmp_path / "out.zip"
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(src, arcname=src.relative_to(tmp_path))

    extract_dir = tmp_path / "extracted"
    with zipfile.ZipFile(out_zip) as zf:
        zf.extractall(extract_dir)

    assert (extract_dir / "t.raw").read_text() == "contenuto originale"
