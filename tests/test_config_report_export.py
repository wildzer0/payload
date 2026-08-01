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


def test_export_project_contains_sources_and_config(tmp_path):
    from payload.export import export_project

    (tmp_path / "table-tool.toml").write_text("[defaults]\n")
    sensors = tmp_path / "sensors"
    sensors.mkdir()
    src = sensors / "temp.raw"
    src.write_text("x")

    sources = discover_table_sources(tmp_path, {".raw"}, tmp_path / "build")
    out_zip = tmp_path / "out.zip"
    export_project(tmp_path, sources, out_zip)

    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
    assert names == {"sensors/temp.raw", "table-tool.toml"}


def test_export_project_only_includes_sidecars_of_exported_sources(tmp_path):
    """Il punto chiave di export_project rispetto a un rglob generico su
    tutti i *.config.toml: un sidecar di una tabella NON esportata non
    deve finire nello zip per sbaglio."""
    from payload.export import export_project

    (tmp_path / "table-tool.toml").write_text("[defaults]\n")
    sensors = tmp_path / "sensors"
    sensors.mkdir()
    exported_src = sensors / "temp.raw"
    exported_src.write_text("x")
    (sensors / "temp.config.toml").write_text('[defaults]\nwriter = "hex"\n')

    # tabella NON esportata, con sidecar orfano nello stesso progetto
    other_src = tmp_path / "other.raw"
    other_src.write_text("y")
    (tmp_path / "other.config.toml").write_text('[defaults]\nwriter = "bin"\n')

    out_zip = tmp_path / "out.zip"
    export_project(tmp_path, [exported_src], out_zip)  # solo temp.raw

    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())

    assert "sensors/temp.config.toml" in names
    assert "other.config.toml" not in names
    assert "other.raw" not in names


def test_export_project_include_history(tmp_path):
    from payload.core.history import HistoryStore
    from payload.export import export_project

    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")

    out_zip = tmp_path / "out.zip"
    export_project(tmp_path, [src], out_zip, include_history=True)

    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
    assert any(n.startswith(".payload_history/") for n in names)


def test_export_project_roundtrip_preserves_content(tmp_path):
    from payload.export import export_project

    src = tmp_path / "t.raw"
    src.write_text("contenuto originale")

    out_zip = tmp_path / "out.zip"
    export_project(tmp_path, [src], out_zip)

    extract_dir = tmp_path / "extracted"
    with zipfile.ZipFile(out_zip) as zf:
        zf.extractall(extract_dir)

    assert (extract_dir / "t.raw").read_text() == "contenuto originale"
