from pathlib import Path

from payload.core.cache import BuildCache, compute_cache_key, compute_pipeline_cache_key_multi


def test_cache_key_changes_with_reader_or_writer():
    k1 = compute_cache_key(b"data", "reader_a", "writer_a", {})
    k2 = compute_cache_key(b"data", "reader_b", "writer_a", {})
    k3 = compute_cache_key(b"data", "reader_a", "writer_b", {})
    assert len({k1, k2, k3}) == 3


def test_cache_key_changes_with_config():
    k1 = compute_cache_key(b"data", "r", "w", {"flag": 1})
    k2 = compute_cache_key(b"data", "r", "w", {"flag": 2})
    assert k1 != k2


def test_cache_persists_across_instances(tmp_path):
    cache_dir = tmp_path / "cache"
    output = tmp_path / "out.bin"
    output.write_bytes(b"x")

    cache1 = BuildCache(cache_dir)
    cache1.update("table1", "abc123", output)
    cache1.save()

    cache2 = BuildCache(cache_dir)
    assert cache2.is_fresh("table1", "abc123") is True
    assert cache2.is_fresh("table1", "different_key") is False


def test_cache_stale_if_output_deleted(tmp_path):
    cache_dir = tmp_path / "cache"
    output = tmp_path / "out.bin"
    output.write_bytes(b"x")

    cache = BuildCache(cache_dir)
    cache.update("table1", "abc123", output)
    output.unlink()

    assert cache.is_fresh("table1", "abc123") is False


def test_corrupted_cache_file_does_not_crash(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".payload_cache.json").write_text("{not valid json")

    cache = BuildCache(cache_dir)  # must not raise
    assert cache.is_fresh("anything", "anything") is False


# --- compute_pipeline_cache_key_multi (batch tables) ----------------------


def test_multi_cache_key_changes_with_content():
    k1 = compute_pipeline_cache_key_multi([("a.txt", b"1"), ("b.txt", b"2")], "sig", {})
    k2 = compute_pipeline_cache_key_multi([("a.txt", b"1"), ("b.txt", b"X")], "sig", {})
    assert k1 != k2


def test_multi_cache_key_changes_with_file_order():
    k1 = compute_pipeline_cache_key_multi([("a.txt", b"1"), ("b.txt", b"2")], "sig", {})
    k2 = compute_pipeline_cache_key_multi([("b.txt", b"2"), ("a.txt", b"1")], "sig", {})
    assert k1 != k2


def test_multi_cache_key_no_boundary_collision():
    """['AB','CD'] and ['A','BCD'] must not produce the same key just
    because the raw byte concatenation would match."""
    k1 = compute_pipeline_cache_key_multi([("a", b"AB"), ("b", b"CD")], "sig", {})
    k2 = compute_pipeline_cache_key_multi([("a", b"A"), ("b", b"BCD")], "sig", {})
    assert k1 != k2


def test_multi_cache_key_changes_with_stage_signature_and_config():
    named = [("a.txt", b"1")]
    k1 = compute_pipeline_cache_key_multi(named, "sig1", {})
    k2 = compute_pipeline_cache_key_multi(named, "sig2", {})
    k3 = compute_pipeline_cache_key_multi(named, "sig1", {"flag": 1})
    assert len({k1, k2, k3}) == 3
