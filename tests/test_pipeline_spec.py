import pytest

from payload.core.errors import InvalidPipelineError
from payload.core.pipeline_spec import ExecStage, PipelineSpec, ReaderStage, WriterStage


def test_implicit_pipeline_has_two_stages():
    spec = PipelineSpec.implicit("raw_text", "bin")
    assert len(spec.stages) == 2
    assert isinstance(spec.stages[0], ReaderStage) and spec.stages[0].name == "raw_text"
    assert isinstance(spec.stages[1], WriterStage) and spec.stages[1].name == "bin"


def test_valid_two_stage_pipeline():
    spec = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "a"},
        {"type": "writer", "name": "b"},
    ])
    assert len(spec.stages) == 2


def test_valid_five_stage_pipeline_from_docs_example():
    spec = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "c_source"},
        {"type": "writer", "name": "bin"},
        {"type": "exec", "command": "sign {input} {output}"},
        {"type": "reader", "name": "raw_text"},
        {"type": "writer", "name": "obj"},
    ])
    assert len(spec.stages) == 5
    assert isinstance(spec.stages[2], ExecStage)


def test_reader_followed_by_reader_rejected():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([
            {"type": "reader", "name": "a"},
            {"type": "reader", "name": "b"},
        ])


def test_pipeline_must_start_with_reader():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([
            {"type": "writer", "name": "a"},
            {"type": "reader", "name": "b"},
            {"type": "writer", "name": "c"},
        ])


def test_pipeline_needs_at_least_two_stages():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([{"type": "reader", "name": "a"}])

    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([])


def test_exec_as_last_stage_requires_output_extension():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([
            {"type": "reader", "name": "a"},
            {"type": "writer", "name": "b"},
            {"type": "exec", "command": "echo test"},
        ])


def test_exec_as_last_stage_with_output_extension_accepted():
    spec = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "a"},
        {"type": "writer", "name": "b"},
        {"type": "exec", "command": "echo test", "output_extension": ".x"},
    ])
    assert spec.stages[-1].output_extension == ".x"


def test_exec_not_last_stage_does_not_require_output_extension():
    spec = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "a"},
        {"type": "writer", "name": "b"},
        {"type": "exec", "command": "echo test"},
        {"type": "reader", "name": "c"},
        {"type": "writer", "name": "d"},
    ])
    assert spec.stages[2].output_extension is None


def test_invalid_on_error_rejected():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([
            {"type": "reader", "name": "a"},
            {"type": "writer", "name": "b"},
            {"type": "exec", "command": "x", "output_extension": ".x", "on_error": "boh"},
        ])


def test_valid_on_error_values_accepted():
    for value in ("fail", "warn"):
        spec = PipelineSpec.from_raw_stages([
            {"type": "reader", "name": "a"},
            {"type": "writer", "name": "b"},
            {"type": "exec", "command": "x", "output_extension": ".x", "on_error": value},
        ])
        assert spec.stages[-1].on_error == value


def test_unknown_stage_type_rejected():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([{"type": "qualcosa_a_caso"}])


def test_reader_stage_missing_name_rejected():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([
            {"type": "reader"},
            {"type": "writer", "name": "b"},
        ])


def test_exec_stage_missing_command_rejected():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages([
            {"type": "reader", "name": "a"},
            {"type": "writer", "name": "b"},
            {"type": "exec", "output_extension": ".x"},
        ])


def test_stage_not_a_dict_rejected():
    with pytest.raises(InvalidPipelineError):
        PipelineSpec.from_raw_stages(["non un dict"])


def test_cache_signature_differs_for_different_pipelines():
    a = PipelineSpec.implicit("raw_text", "bin")
    b = PipelineSpec.implicit("raw_text", "hex")
    assert a.cache_signature() != b.cache_signature()


def test_cache_signature_stable_for_identical_pipelines():
    a = PipelineSpec.implicit("raw_text", "bin")
    b = PipelineSpec.implicit("raw_text", "bin")
    assert a.cache_signature() == b.cache_signature()


def test_cache_signature_includes_exec_command():
    a = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "a"}, {"type": "writer", "name": "b"},
        {"type": "exec", "command": "cmd1", "output_extension": ".x"},
    ])
    b = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "a"}, {"type": "writer", "name": "b"},
        {"type": "exec", "command": "cmd2", "output_extension": ".x"},
    ])
    assert a.cache_signature() != b.cache_signature()


def test_reader_writer_pairs_finds_all_pairs_in_multistage_pipeline():
    spec = PipelineSpec.from_raw_stages([
        {"type": "reader", "name": "r1"},
        {"type": "writer", "name": "w1"},
        {"type": "exec", "command": "x"},
        {"type": "reader", "name": "r2"},
        {"type": "writer", "name": "w2"},
    ])
    pairs = list(spec.reader_writer_pairs())
    assert len(pairs) == 2
    assert pairs[0][0].name == "r1" and pairs[0][1].name == "w1"
    assert pairs[1][0].name == "r2" and pairs[1][1].name == "w2"
