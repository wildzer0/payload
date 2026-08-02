import os
from pathlib import Path

import pytest

from payload.core.config import PayloadConfig
from payload.core.registry import PluginRegistry
from tests.fakes import BrokenReader, FakeReader, FakeWriter

_EXAMPLES_PLUGINS_DIR = str(Path(__file__).resolve().parent.parent / "examples" / "plugins")


@pytest.fixture(autouse=True, scope="session")
def _example_plugins_on_plugin_path():
    """payload ships no reader/writer of its own (see pyproject.toml) —
    CLI/web integration tests that build a real project end-to-end
    (not the unit tests using the 'registry'/FakeReader fixture below)
    need SOME reader/writer to actually be discoverable, the same way
    a real user would get one: by pointing PAYLOAD_PLUGIN_PATH at a
    folder of plugins (see core/local_plugins.py). Using
    examples/plugins/ here is exactly that, for the whole test
    session, so individual test projects don't each need their own
    populated plugins/ folder just to build 'example_table.raw'."""
    previous = os.environ.get("PAYLOAD_PLUGIN_PATH")
    os.environ["PAYLOAD_PLUGIN_PATH"] = _EXAMPLES_PLUGINS_DIR
    yield
    if previous is None:
        os.environ.pop("PAYLOAD_PLUGIN_PATH", None)
    else:
        os.environ["PAYLOAD_PLUGIN_PATH"] = previous


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
