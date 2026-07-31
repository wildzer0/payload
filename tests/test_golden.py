from payload.core.golden import check_golden, update_golden


def test_golden_missing_when_not_created(tmp_path):
    out = tmp_path / "table.bin"
    out.write_bytes(b"abc")
    result = check_golden(out, tmp_path / "golden")
    assert result.status == "missing"


def test_golden_match_after_update(tmp_path):
    out = tmp_path / "table.bin"
    out.write_bytes(b"abc")
    update_golden(out, tmp_path / "golden")

    result = check_golden(out, tmp_path / "golden")
    assert result.status == "match"


def test_golden_mismatch_on_changed_output(tmp_path):
    out = tmp_path / "table.bin"
    out.write_bytes(b"abc")
    update_golden(out, tmp_path / "golden")

    out.write_bytes(b"different")
    result = check_golden(out, tmp_path / "golden")
    assert result.status == "mismatch"
    assert result.current == b"different"
    assert result.expected == b"abc"
