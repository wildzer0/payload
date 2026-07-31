from pathlib import Path

from payload.core.cache import BuildCache, compute_cache_key


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

    cache = BuildCache(cache_dir)  # non deve sollevare
    assert cache.is_fresh("anything", "anything") is False
