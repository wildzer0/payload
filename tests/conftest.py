from pathlib import Path

import pytest

from payload.core.config import PayloadConfig
from payload.core.registry import PluginRegistry
from tests.fakes import BrokenReader, FakeReader, FakeWriter


@pytest.fixture
def registry() -> PluginRegistry:
    r = PluginRegistry()
    r.register_reader(FakeReader())
    r.register_reader(BrokenReader())
    r.register_writer(FakeWriter())
    return r


@pytest.fixture
def config() -> PayloadConfig:
    return PayloadConfig()


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    p = tmp_path / "example.fake"
    p.write_text("hello table")
    return p
