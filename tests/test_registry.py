import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from payload.core.errors import AmbiguousReaderError, NoReaderFoundError, PluginLoadError
from payload.core.registry import READER_GROUP, PluginRegistry, load_plugins
from tests.fakes import FakeReader, FakeWriter


class _DoctorCheckStub:
    name = "stub_check"
    api_version = "1.0"

    def run(self, config):
        from payload.core.plugin_base import CheckResult, CheckStatus
        return CheckResult(self.name, CheckStatus.OK, "ok")


class _ReaderExtA:
    name = "reader_ext_a"
    extensions = [".ambig"]
    api_version = "1.0"
    default_writer = None

    def sniff(self, path):
        return False

    def parse(self, path, config):
        raise NotImplementedError


class _ReaderExtB:
    name = "reader_ext_b"
    extensions = [".ambig"]
    api_version = "1.0"
    default_writer = None

    def sniff(self, path):
        return False

    def parse(self, path, config):
        raise NotImplementedError


def test_register_reader_warns_on_name_collision(caplog):
    registry = PluginRegistry()
    with caplog.at_level(logging.WARNING):
        registry.register_reader(FakeReader())
        registry.register_reader(FakeReader())

    assert any("già registrato" in r.message for r in caplog.records)
    assert len(registry.readers) == 1


def test_register_doctor_check_basic_and_collision_warning(caplog):
    registry = PluginRegistry()
    registry.register_doctor_check(_DoctorCheckStub())
    assert "stub_check" in registry.doctor_checks

    with caplog.at_level(logging.WARNING):
        registry.register_doctor_check(_DoctorCheckStub())
    assert any("già registrato" in r.message for r in caplog.records)


def test_find_reader_explicit_unknown_raises(tmp_path):
    registry = PluginRegistry()
    registry.register_reader(FakeReader())
    with pytest.raises(NoReaderFoundError):
        registry.find_reader(tmp_path / "t.fake", explicit="non_esiste")


def test_find_reader_explicit_known_returns_it(tmp_path):
    registry = PluginRegistry()
    registry.register_reader(FakeReader())
    reader = registry.find_reader(tmp_path / "t.whatever", explicit="fake_reader")
    assert reader.name == "fake_reader"


def test_find_reader_no_candidates_raises(tmp_path):
    registry = PluginRegistry()
    registry.register_reader(FakeReader())
    with pytest.raises(NoReaderFoundError):
        registry.find_reader(tmp_path / "t.unknown_ext")


def test_find_reader_ambiguous_when_no_sniff_resolves(tmp_path):
    registry = PluginRegistry()
    registry.register_reader(_ReaderExtA())
    registry.register_reader(_ReaderExtB())
    with pytest.raises(AmbiguousReaderError):
        registry.find_reader(tmp_path / "t.ambig")


def test_find_reader_sniff_resolves_ambiguity(tmp_path):
    class _SniffingReader(_ReaderExtA):
        name = "reader_ext_sniff"

        def sniff(self, path):
            return True

    registry = PluginRegistry()
    registry.register_reader(_SniffingReader())
    registry.register_reader(_ReaderExtB())
    reader = registry.find_reader(tmp_path / "t.ambig")
    assert reader.name == "reader_ext_sniff"


def test_load_plugins_strict_raises_on_broken_entry_point(tmp_path):
    fake_ep = MagicMock()
    fake_ep.name = "broken_reader"
    fake_ep.load.side_effect = ImportError("modulo mancante")

    def fake_entry_points(group):
        return [fake_ep] if group == READER_GROUP else []

    with patch("payload.core.registry.entry_points", side_effect=fake_entry_points):
        with pytest.raises(PluginLoadError):
            load_plugins(project_root=tmp_path, strict=True)


def test_load_plugins_non_strict_tracks_broken_entry_point(tmp_path):
    fake_ep = MagicMock()
    fake_ep.name = "broken_reader"
    fake_ep.load.side_effect = ImportError("modulo mancante")

    def fake_entry_points(group):
        return [fake_ep] if group == READER_GROUP else []

    with patch("payload.core.registry.entry_points", side_effect=fake_entry_points):
        registry = load_plugins(project_root=tmp_path, strict=False)

    assert "broken_reader" not in registry.readers
    assert len(registry.load_failures) == 1
    assert registry.load_failures[0][0] == "broken_reader"
