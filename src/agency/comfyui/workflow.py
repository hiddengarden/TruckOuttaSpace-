import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class WorkflowSpec:
    """A ComfyUI API-format workflow graph plus a map from logical field names
    (e.g. "positive_prompt") to "<node_id>.<input_name>" in that graph.

    ComfyUI graphs are host-specific (they name installed checkpoints/custom
    nodes), so there's no universal workflow to ship. Export your own via
    ComfyUI's "Workflow -> Export (API)" and hand-write the mapping once."""

    template: dict[str, Any]
    node_mapping: dict[str, str]


def load_workflow_spec(workflows_dir: str | Path, name: str) -> WorkflowSpec:
    directory = Path(workflows_dir)
    template = json.loads((directory / f"{name}.json").read_text())
    node_mapping = json.loads((directory / f"{name}.mapping.json").read_text())
    return WorkflowSpec(template=template, node_mapping=node_mapping)


def validate_workflow_mapping(workflows_dir: str | Path, name: str) -> list[str]:
    """Checks a mapping's `node_id.field` entries actually resolve against
    the template, catching a typo'd node id or renamed input at preflight
    time instead of the first time a real generation tries to patch it and
    hits a KeyError mid-run."""
    problems: list[str] = []
    try:
        spec = load_workflow_spec(workflows_dir, name)
    except FileNotFoundError as exc:
        return [f"{name}: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"{name}: invalid JSON ({exc})"]

    for field, target in spec.node_mapping.items():
        if "." not in target:
            problems.append(f"{name}.{field}: mapping value {target!r} is not 'node_id.input_name'")
            continue
        node_id, input_name = target.split(".", 1)
        node = spec.template.get(node_id)
        if node is None:
            problems.append(f"{name}.{field}: node id {node_id!r} not found in template")
            continue
        if input_name not in node.get("inputs", {}):
            problems.append(f"{name}.{field}: input {input_name!r} not found on node {node_id!r}")
    return problems


def patch_workflow(template: dict[str, Any], node_mapping: dict[str, str], values: dict[str, Any]) -> dict[str, Any]:
    """values keys are logical field names from node_mapping (e.g. "positive_prompt");
    unmapped/unset fields are left untouched at whatever the template already has."""
    workflow = copy.deepcopy(template)
    for field, value in values.items():
        if field not in node_mapping:
            continue
        node_id, input_name = node_mapping[field].split(".", 1)
        workflow[node_id]["inputs"][input_name] = value
    return workflow
