"""
Conformance test for {{plugin_class}}.

Uses payload.testing to verify that the plugin really honors the
Reader/Writer contract (not just that 'tests exist'):
- required attributes present and correct
- parse()/emit() return the right type
- errors are raised as ReaderParseError/WriterEmitError, not as
  generic Exceptions

TODO: fill in the samples below based on your plugin's format.
You can also validate the already-installed plugin at runtime with:
    pld plugin validate {{plugin_slug}} --sample <file>
"""
from pathlib import Path

from {{plugin_name}}.plugin import {{plugin_class}}

# --- if it's a Reader: remove the Writer block below and vice versa ---

# from payload.testing import assert_reader_conforms
#
# def test_reader_conforms(tmp_path):
#     sample = tmp_path / "sample{{plugin_slug}}"
#     sample.write_text("...")  # TODO: valid content for your format
#     assert_reader_conforms({{plugin_class}}(), sample)


# from payload.testing import assert_writer_conforms
# from payload.core.ir import TableIR
#
# def test_writer_conforms(tmp_path):
#     sample_ir = TableIR(
#         name="sample", data=b"\x00\x01\x02",
#         source_path=Path("sample"), source_format="testing",
#     )
#     assert_writer_conforms({{plugin_class}}(), sample_ir, tmp_path)


def test_plugin_has_required_attributes():
    plugin = {{plugin_class}}()
    assert plugin.name == "{{plugin_slug}}"
    assert plugin.api_version
