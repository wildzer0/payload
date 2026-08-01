from pathlib import Path

import pytest

from payload.core.cache import BuildCache
from payload.core.config import load_config
from payload.core.errors import InvalidConfigError
from payload.core.ir import TableIR
from payload.core.pipeline import build
from payload.core.registry import PluginRegistry


class _DemoReader:
    name = "demo"
    extensions = [".demo"]
    api_version = "1.0"
    default_writer = "demo_writer"

    def sniff(self, path):
        return False

    def parse(self, path, config):
        persistent = config.get("plugin", {}).get("demo", {}).get("delimiter", ",")
        override = config.get("cli_opts", {}).get("delimiter")
        used = override or persistent
        return TableIR(name=path.stem, data=used.encode(), source_path=path, source_format=self.name)


class _DemoWriter:
    name = "demo_writer"
    extension = ".bin"
    api_version = "1.0"
    compatible_readers = None

    def emit(self, ir, out_path, config):
        out_path.write_bytes(ir.data)
        return out_path


@pytest.fixture
def registry():
    r = PluginRegistry()
    r.register_reader(_DemoReader())
    r.register_writer(_DemoWriter())
    return r


def test_plugin_section_reaches_reader(tmp_path, registry):
    (tmp_path / "table-tool.toml").write_text('[plugin.demo]\ndelimiter = ";"\n')
    src = tmp_path / "t.demo"
    src.write_text("x")

    config = load_config(tmp_path, source_path=src)
    assert config.plugin == {"demo": {"delimiter": ";"}}

    out_paths, _ = build([src], registry, config, tmp_path / "out")
    assert out_paths[0].read_bytes() == b";"


def test_sidecar_overrides_plugin_section(tmp_path, registry):
    (tmp_path / "table-tool.toml").write_text('[plugin.demo]\ndelimiter = ";"\n')
    src = tmp_path / "t.demo"
    src.write_text("x")
    (tmp_path / "t.config.toml").write_text('[plugin.demo]\ndelimiter = "|"\n')

    config = load_config(tmp_path, source_path=src)
    assert config.plugin == {"demo": {"delimiter": "|"}}


def test_cli_opts_override_persistent_plugin_config(tmp_path, registry):
    (tmp_path / "table-tool.toml").write_text('[plugin.demo]\ndelimiter = ";"\n')
    src = tmp_path / "t.demo"
    src.write_text("x")

    config = load_config(tmp_path, source_path=src)
    out_paths, _ = build([src], registry, config, tmp_path / "out", cli_opts={"delimiter": "|"})

    assert out_paths[0].read_bytes() == b"|"


def test_cli_opts_invalidate_cache(tmp_path, registry):
    src = tmp_path / "t.demo"
    src.write_text("x")
    config = load_config(tmp_path, source_path=src)
    cache = BuildCache(tmp_path / "cache")

    out1, built1 = build([src], registry, config, tmp_path / "out", cache=cache)
    out2, built2 = build([src], registry, config, tmp_path / "out", cache=cache, cli_opts={"delimiter": "|"})

    assert built1 is True
    assert built2 is True  # must NOT be served from cache: different cli_opts
    assert out2[0].read_bytes() == b"|"


def test_unknown_top_level_section_still_rejected(tmp_path):
    (tmp_path / "table-tool.toml").write_text("[some_random_thing]\nx = 1\n")
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_plugin_section_must_be_a_table(tmp_path):
    (tmp_path / "table-tool.toml").write_text('plugin = "not a table"\n')
    with pytest.raises(InvalidConfigError):
        load_config(tmp_path)


def test_no_plugin_section_defaults_to_empty_dict(tmp_path):
    config = load_config(tmp_path)
    assert config.plugin == {}
