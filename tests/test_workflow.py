import json

from agency.comfyui.workflow import load_workflow_spec, patch_workflow, validate_workflow_mapping


def test_load_workflow_spec_reads_shipped_default():
    spec = load_workflow_spec("workflows/image", "default")

    assert spec.template["4"]["class_type"] == "CheckpointLoaderSimple"
    assert spec.node_mapping["positive_prompt"] == "6.text"


def test_patch_workflow_sets_mapped_fields_only():
    spec = load_workflow_spec("workflows/image", "default")

    patched = patch_workflow(
        spec.template, spec.node_mapping, {"positive_prompt": "a red bicycle", "seed": 42, "unmapped_field": "x"}
    )

    assert patched["6"]["inputs"]["text"] == "a red bicycle"
    assert patched["3"]["inputs"]["seed"] == 42
    assert patched["7"]["inputs"]["text"] == ""  # negative_prompt untouched


def test_patch_workflow_does_not_mutate_template():
    spec = load_workflow_spec("workflows/image", "default")
    original_text = spec.template["6"]["inputs"]["text"]

    patch_workflow(spec.template, spec.node_mapping, {"positive_prompt": "changed"})

    assert spec.template["6"]["inputs"]["text"] == original_text


def test_validate_workflow_mapping_passes_for_the_shipped_default():
    assert validate_workflow_mapping("workflows/image", "default") == []


def test_validate_workflow_mapping_reports_missing_node_id(tmp_path):
    (tmp_path / "broken.json").write_text(json.dumps({"6": {"class_type": "X", "inputs": {"text": ""}}}))
    (tmp_path / "broken.mapping.json").write_text(json.dumps({"positive_prompt": "99.text"}))

    problems = validate_workflow_mapping(tmp_path, "broken")

    assert len(problems) == 1
    assert "99" in problems[0]


def test_validate_workflow_mapping_reports_missing_input_name(tmp_path):
    (tmp_path / "broken.json").write_text(json.dumps({"6": {"class_type": "X", "inputs": {"text": ""}}}))
    (tmp_path / "broken.mapping.json").write_text(json.dumps({"positive_prompt": "6.prompt_text"}))

    problems = validate_workflow_mapping(tmp_path, "broken")

    assert len(problems) == 1
    assert "prompt_text" in problems[0]


def test_validate_workflow_mapping_reports_missing_files():
    problems = validate_workflow_mapping("workflows/image", "does-not-exist")

    assert len(problems) == 1
