import json
from pathlib import Path
from unittest.mock import patch

import pytest

from payload.core.discovery import discover_table_sources
from payload.core.errors import SnapshotNotFoundError
from payload.core.history import HistoryStore


def test_read_blob_missing_raises(tmp_path):
    history = HistoryStore(tmp_path)
    with pytest.raises(SnapshotNotFoundError):
        history.read_blob("hash_che_non_esiste")


def test_never_committed_table_is_dirty(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    assert history.is_dirty("t", [src]) is True
    assert history.last_snapshot("t") is None


def test_commit_then_not_dirty(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "primo")
    assert history.is_dirty("t", [src]) is False


def test_modify_after_commit_is_dirty_again(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "primo")
    src.write_text("y")
    assert history.is_dirty("t", [src]) is True


def test_dirty_when_output_changed_but_source_did_not(tmp_path):
    """Regressione: cambiare writer (o comunque solo l'output, es. un
    writer diverso -> nome/estensione diversi) senza toccare il
    sorgente veniva visto come 'invariata' perché is_dirty() guardava
    solo il sorgente — il commit non aveva modo di accorgersi che
    c'era un output nuovo da salvare."""
    src = tmp_path / "t.raw"
    src.write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out_bin = out_dir / "t.bin"
    out_bin.write_bytes(b"output bin")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out_bin], "v1 con writer bin")

    assert history.is_dirty("t", [src], [out_bin]) is False

    # stesso sorgente, writer diverso: file di output diverso (nome ed
    # estensione compresi), 't.bin' precedente resta o meno non conta,
    # quello che conta è cosa c'è ORA in output_paths
    out_hex = out_dir / "t.hex"
    out_hex.write_bytes(b"output hex, diverso")

    assert history.is_dirty("t", [src], [out_hex]) is True


def test_dirty_when_output_content_changed_same_filename(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"prima")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out], "v1")
    assert history.is_dirty("t", [src], [out]) is False

    out.write_bytes(b"dopo, contenuto diverso")
    assert history.is_dirty("t", [src], [out]) is True


def test_commit_ids_increment(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    s1 = history.commit("t", [src], [], "v1")
    src.write_text("y")
    s2 = history.commit("t", [src], [], "v2")
    assert s1.id == 1
    assert s2.id == 2


# --- golden pointer ---


def test_golden_snapshot_id_none_by_default(tmp_path):
    history = HistoryStore(tmp_path)
    assert history.golden_snapshot_id("t") is None


def test_set_and_get_golden(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 3)
    assert history.golden_snapshot_id("t") == 3


def test_set_golden_overwrites_previous(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 3)
    history.set_golden("t", 7)
    assert history.golden_snapshot_id("t") == 7


def test_clear_golden(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 3)

    assert history.clear_golden("t") is True
    assert history.golden_snapshot_id("t") is None
    assert history.clear_golden("t") is False  # idempotente


def test_all_golden(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t1", 1)
    history.set_golden("t2", 4)
    assert history.all_golden() == {"t1": 1, "t2": 4}


def test_golden_map_survives_reload(tmp_path):
    HistoryStore(tmp_path).set_golden("t", 5)
    assert HistoryStore(tmp_path).golden_snapshot_id("t") == 5


def test_golden_map_corrupted_recreated(tmp_path):
    history = HistoryStore(tmp_path)
    history.set_golden("t", 1)
    history._golden_path.write_text("{not json")

    assert history.golden_snapshot_id("t") is None
    history.set_golden("t2", 2)
    assert HistoryStore(tmp_path).all_golden() == {"t2": 2}


def test_head_map_corrupted_falls_back_to_tip(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")
    history._head_path.write_text("{not json")

    assert history.head_snapshot_id("t") == 1
    assert history.last_snapshot("t").message == "v1"


def test_commit_after_restore_clears_head_override(tmp_path):
    """Un commit successivo a un restore-a-uno-snapshot-precedente
    diventa la nuova punta E il nuovo 'attuale': l'override lasciato
    dal restore non ha più senso una volta che c'è un commit fresco
    sopra, altrimenti il nuovo commit resterebbe invisibile a
    last_snapshot()/is_dirty()."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")

    src.write_text("v2")
    history.commit("t", [src], [], "v2")

    history.restore("t", 1, [src], tmp_path / "build")
    assert history.head_snapshot_id("t") == 1

    src.write_text("v3")
    snap = history.commit("t", [src], [], "v3")

    assert snap.id == 3
    assert history.head_snapshot_id("t") == 3
    assert history.tip_snapshot_id("t") == 3
    assert history.last_snapshot("t").message == "v3"


def test_restore_does_not_create_new_snapshot(tmp_path):
    """Il redesign 'solo puntatore': restore non deve mai aggiungere
    una entry alla cronologia, a differenza del vecchio comportamento
    stile 'git revert'."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")
    src.write_text("v2")
    history.commit("t", [src], [], "v2")

    history.restore("t", 1, [src], tmp_path / "build")
    history.restore("t", 2, [src], tmp_path / "build")

    assert len(history.log("t")) == 2
    assert history.head_snapshot_id("t") == 2


def test_commit_records_reader_and_writers(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("v1")
    history = HistoryStore(tmp_path)
    snap = history.commit("t", [src], [], "v1", reader="raw_text", writers=["bin", "header"])

    assert snap.reader == "raw_text"
    assert snap.writers == ["bin", "header"]
    reloaded = HistoryStore(tmp_path).get_snapshot("t", 1)
    assert reloaded.reader == "raw_text"
    assert reloaded.writers == ["bin", "header"]


def test_old_manifest_without_reader_writer_fields_still_loads(tmp_path):
    """Retrocompatibilità: un manifest scritto prima dell'aggiunta dei
    campi reader/writers deve continuare a caricarsi, con quei campi
    ai valori di default."""
    history = HistoryStore(tmp_path)
    history._ensure_dirs()
    manifest_path = history._manifest_path("t")
    manifest_path.write_text(json.dumps([
        {"id": 1, "timestamp": "2020-01-01T00:00:00", "message": "vecchio", "source_blob": "abc", "output_blobs": {}}
    ]))

    snap = history.get_snapshot("t", 1)
    assert snap.reader is None
    assert snap.writers == []


def test_old_manifest_is_dirty_still_works_by_value_not_filename(tmp_path):
    """Regressione: uno snapshot scritto prima delle tabelle batch non
    conosce il filename reale (solo l'hash, sotto una chiave
    placeholder) — is_dirty non deve confrontare le CHIAVI in questo
    caso (fallirebbe sempre, 'placeholder' != 't.raw'), solo il valore."""
    src = tmp_path / "t.raw"
    src.write_bytes(b"contenuto invariato")
    history = HistoryStore(tmp_path)
    history._ensure_dirs()
    history._manifest_path("t").write_text(json.dumps([{
        "id": 1, "timestamp": "2020-01-01T00:00:00", "message": "vecchio",
        "source_blob": history._write_blob(b"contenuto invariato"), "output_blobs": {},
    }]))

    assert history.is_dirty("t", [src]) is False

    src.write_bytes(b"contenuto cambiato")
    assert history.is_dirty("t", [src]) is True


def test_old_manifest_restore_still_writes_the_source_by_value(tmp_path):
    src = tmp_path / "t.raw"
    history = HistoryStore(tmp_path)
    history._ensure_dirs()
    blob_hash = history._write_blob(b"contenuto originale")
    history._manifest_path("t").write_text(json.dumps([{
        "id": 1, "timestamp": "2020-01-01T00:00:00", "message": "vecchio",
        "source_blob": blob_hash, "output_blobs": {},
    }]))

    src.write_bytes(b"modificato")
    result = history.restore("t", 1, [src], tmp_path / "build")

    assert src.read_bytes() == b"contenuto originale"
    assert result.written == [src]


def test_log_returns_snapshots_in_order(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")
    src.write_text("y")
    history.commit("t", [src], [], "v2")

    log = history.log("t")
    assert [s.message for s in log] == ["v1", "v2"]


def test_restore_brings_back_source_and_output(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("originale")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"output originale")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out], "v1")

    src.write_text("modificato")
    out.write_bytes(b"output modificato")

    result = history.restore("t", 1, [src], out_dir)

    assert src.read_text() == "originale"
    assert out.read_bytes() == b"output originale"
    assert len(result.written) == 2
    assert result.removed == []


def test_restore_leaves_table_clean_not_dirty(tmp_path):
    """Regressione: is_dirty() deve confrontare lo stato appena
    ripristinato con lo snapshot 'attuale' (head), non con l'ultimo
    committato in assoluto (la punta), altrimenti la tabella
    risulterebbe 'modificata' subito dopo un restore riuscito."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out = out_dir / "t.bin"
    out.write_bytes(b"out-v1")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out], "v1")

    src.write_text("v2")
    out.write_bytes(b"out-v2")
    history.commit("t", [src], [out], "v2")

    history.restore("t", 1, [src], out_dir)

    assert history.is_dirty("t", [src]) is False
    log = history.log("t")
    # il restore NON crea un nuovo snapshot: sposta solo l'attuale
    # indietro, la cronologia resta additiva e invariata.
    assert len(log) == 2
    assert history.head_snapshot_id("t") == 1
    assert history.tip_snapshot_id("t") == 2
    assert history.last_snapshot("t").message == "v1"
    assert src.read_text() == "v1"


def test_restore_removes_orphaned_output_from_a_different_writer(tmp_path):
    """Regressione trovata dall'utente: se tra due snapshot cambia il
    writer (es. bin -> header), l'output del writer successivo resta
    fisicamente su disco anche dopo un restore allo snapshot precedente
    — a differenza di git, che ai checkout rimuove i file non presenti
    nel commit di destinazione. Senza pulizia, la tabella risulterebbe
    'modificata' di nuovo subito dopo il restore (l'output orfano non
    fa parte del nuovo snapshot appena creato dal restore stesso)."""
    src = tmp_path / "t.raw"
    src.write_text("v1")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    out_bin = out_dir / "t.bin"
    out_bin.write_bytes(b"out-bin")

    history = HistoryStore(tmp_path)
    history.commit("t", [src], [out_bin], "v1 con writer bin")

    src.write_text("v2")
    out_header = out_dir / "t.h"
    out_header.write_bytes(b"out-header")
    history.commit("t", [src], [out_header], "v2 con writer header")

    assert out_bin.exists() and out_header.exists()  # entrambi presenti prima del restore

    result = history.restore("t", 1, [src], out_dir)

    assert src.read_text() == "v1"
    assert out_bin.exists()
    assert not out_header.exists()  # orfano, non faceva parte dello snapshot #1
    assert result.removed == [out_header]

    current_outputs = list(out_dir.glob("t.*"))
    assert history.is_dirty("t", [src], current_outputs) is False


def test_restore_skips_filename_absent_from_the_snapshot(tmp_path):
    """Una tabella batch a cui è stato AGGIUNTO un file membro dopo un
    commit: quel file non ha un blob in quello snapshot (non esisteva
    ancora) — restore lo salta con un warning invece di sollevare,
    ripristinando comunque normalmente gli altri file del batch."""
    row1 = tmp_path / "ROW1.txt"
    row3 = tmp_path / "ROW3.txt"
    row1.write_text("uno")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1], [], "v1, solo ROW1")

    row1.write_text("modificato")
    row3.write_text("nuovo file, non nello snapshot #1")
    result = history.restore("rows", 1, [row1, row3], tmp_path / "build")

    assert row1.read_text() == "uno"
    assert row3.read_text() == "nuovo file, non nello snapshot #1"  # non toccato
    assert result.written == [row1]


def test_restore_unknown_snapshot_raises(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("x")
    history = HistoryStore(tmp_path)
    history.commit("t", [src], [], "v1")

    with pytest.raises(SnapshotNotFoundError):
        history.restore("t", 999, [src], tmp_path / "build")


def test_identical_content_deduplicates_blobs(tmp_path):
    src = tmp_path / "t.raw"
    src.write_text("stesso contenuto")
    history = HistoryStore(tmp_path)

    s1 = history.commit("t", [src], [], "v1")
    s2 = history.commit("t", [src], [], "v2 nessuna modifica reale")

    assert s1.source_blobs == s2.source_blobs
    objects_dir = tmp_path / ".payload_history" / "objects"
    blob_files = [p for p in objects_dir.rglob("*") if p.is_file()]
    assert len(blob_files) == 1  # un solo blob su disco, non due


# --- tabelle batch (source_paths con N > 1 elementi) ------------------------


def test_batch_commit_stores_one_blob_per_source_filename(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row1.write_text("uno")
    row2.write_text("due")
    history = HistoryStore(tmp_path)

    snap = history.commit("rows", [row1, row2], [], "v1")

    assert snap.source_blobs.keys() == {"ROW1.txt", "ROW2.txt"}


def test_batch_not_dirty_after_commit_dirty_after_any_member_changes(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row1.write_text("uno")
    row2.write_text("due")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1, row2], [], "v1")

    assert history.is_dirty("rows", [row1, row2]) is False

    row2.write_text("due-modificato")
    assert history.is_dirty("rows", [row1, row2]) is True


def test_batch_dirty_when_a_member_file_is_added_or_removed(tmp_path):
    """Bonus emergente del confronto dict-a-dict: un file aggiunto o
    rimosso dal batch tra due commit è 'dirty' anche se il contenuto
    degli altri file non è cambiato, perché le CHIAVI del dict differiscono."""
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row3 = tmp_path / "ROW3.txt"
    row1.write_text("uno")
    row2.write_text("due")
    row3.write_text("tre")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1, row2], [], "v1")

    assert history.is_dirty("rows", [row1, row2, row3]) is True
    assert history.is_dirty("rows", [row1]) is True


def test_batch_restore_writes_back_every_member_file(tmp_path):
    row1 = tmp_path / "ROW1.txt"
    row2 = tmp_path / "ROW2.txt"
    row1.write_text("uno")
    row2.write_text("due")
    history = HistoryStore(tmp_path)
    history.commit("rows", [row1, row2], [], "v1")

    row1.write_text("cambiato")
    row2.write_text("anche questo")

    result = history.restore("rows", 1, [row1, row2], tmp_path / "build")

    assert row1.read_text() == "uno"
    assert row2.read_text() == "due"
    assert set(result.written) == {row1, row2}


def test_all_tracked_tables_lists_committed_tables(tmp_path):
    history = HistoryStore(tmp_path)
    assert history.all_tracked_tables() == []

    src_a = tmp_path / "a.raw"
    src_a.write_text("a")
    src_b = tmp_path / "b.raw"
    src_b.write_text("b")
    history.commit("a", [src_a], [], "v1")
    history.commit("b", [src_b], [], "v1")

    assert history.all_tracked_tables() == ["a", "b"]


# --- discovery -------------------------------------------------------------

def test_discover_table_sources_excludes_output_dir(tmp_path):
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "t1.bin").write_bytes(b"x")  # non deve comparire tra i sorgenti

    sources = discover_table_sources(tmp_path, {".raw"}, out_dir)
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_excludes_matching_extension_inside_output_dir(tmp_path):
    """Un file DENTRO output_dir con un'estensione nota (non solo
    un'estensione diversa, come nel test sopra) deve comunque essere
    escluso — altrimenti una build che rigenera un .raw dentro build/
    (caso limite ma possibile) verrebbe ripresa come sorgente."""
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    out_dir.mkdir()
    (out_dir / "rigenerato.raw").write_text("x")

    sources = discover_table_sources(tmp_path, {".raw"}, out_dir)
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_respects_filter_glob(tmp_path):
    (tmp_path / "sensors").mkdir()
    (tmp_path / "sensors" / "t1.raw").write_text("x")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "t2.raw").write_text("x")

    sources = discover_table_sources(tmp_path, {".raw"}, tmp_path / "build", filter_glob="sensors/**")
    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_tolerates_unresolvable_output_dir(tmp_path):
    """Se output_dir.resolve() fallisce (es. permessi, filesystem
    particolari), la discovery non deve crashare — degrada a usare il
    path non risolto invece di alzare."""
    (tmp_path / "t1.raw").write_text("x")
    out_dir = tmp_path / "build"
    real_resolve = Path.resolve

    def fake_resolve(self, *a, **kw):
        if self == out_dir:
            raise OSError("simulato")
        return real_resolve(self, *a, **kw)

    with patch.object(Path, "resolve", fake_resolve):
        sources = discover_table_sources(tmp_path, {".raw"}, out_dir)

    assert [s.name for s in sources] == ["t1.raw"]


def test_discover_table_sources_tolerates_unresolvable_source(tmp_path):
    """Se il resolve() di un candidato fallisce, va comunque incluso tra
    i sorgenti invece di essere perso silenziosamente (fail-safe: meglio
    un falso positivo che una tabella scomparsa dalla discovery)."""
    src = tmp_path / "t1.raw"
    src.write_text("x")
    real_resolve = Path.resolve

    def fake_resolve(self, *a, **kw):
        if self == src:
            raise OSError("simulato")
        return real_resolve(self, *a, **kw)

    with patch.object(Path, "resolve", fake_resolve):
        sources = discover_table_sources(tmp_path, {".raw"}, tmp_path / "build")

    assert [s.name for s in sources] == ["t1.raw"]
