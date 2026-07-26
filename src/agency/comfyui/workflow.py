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
