"""
Test di conformita per {{plugin_class}}.

Usa payload.testing per verificare che il plugin rispetti davvero il
contratto Reader/Writer (non solo che 'esistano dei test'):
- attributi richiesti presenti e corretti
- parse()/emit() ritornano il tipo giusto
- gli errori sono sollevati come ReaderParseError/WriterEmitError, non
  come Exception generiche

TODO: completa i sample qui sotto in base al formato del tuo plugin.
Puoi anche validare il plugin gia installato a runtime con:
    pld plugin validate {{plugin_slug}} --sample <file>
"""
from pathlib import Path

from {{plugin_name}}.plugin import {{plugin_class}}

# --- se e' un Reader: rimuovi il blocco Writer sotto e viceversa ---

# from payload.testing import assert_reader_conforms
#
# def test_reader_conforms(tmp_path):
#     sample = tmp_path / "sample{{plugin_slug}}"
#     sample.write_text("...")  # TODO: contenuto valido per il tuo formato
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
