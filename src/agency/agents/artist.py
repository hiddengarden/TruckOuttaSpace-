from pathlib import Path

from agency.comfyui.client import ComfyUIClient
from agency.comfyui.workflow import WorkflowSpec, load_workflow_spec, patch_workflow
from agency.inference.provider import LLMProvider
from agency.org import BrandContext

_SYSTEM_TEMPLATE = (
    "You write image-generation prompts for {name}.\n"
    "Visual style should match this brand voice: {voice}\n"
    "Output ONLY the image prompt as comma-separated descriptive tags/phrases, "
    "nothing else -- no commentary, no quotes."
)


class Artist:
    """Writes image-generation prompts and renders them via a local ComfyUI instance.

    Style/model selection: pass a different `style` (a workflow name under
    workflows_dir) to use a structurally different graph, or a `checkpoint`
    override to swap the model within the same graph -- whichever the
    workflow's mapping supports. Callers (a future Designer, or content
    agents directly) decide both; Artist itself has no opinion on which.
    """

    def __init__(self, provider: LLMProvider, comfyui_client: ComfyUIClient, workflows_dir: str | Path):
        self._provider = provider
        self._comfyui = comfyui_client
        self._workflows_dir = Path(workflows_dir)

    def build_prompt(self, brand: BrandContext, brief: str) -> str:
        system = _SYSTEM_TEMPLATE.format(name=brand.name, voice=brand.voice)
        return self._provider.complete(system, f"Create an image prompt for: {brief}").strip()

    def generate(
        self,
        brand: BrandContext,
        brief: str,
        assets_dir: str | Path,
        style: str = "default",
        negative_prompt: str = "",
        checkpoint: str | None = None,
        seed: int | None = None,
    ) -> list[Path]:
        spec = self._load_spec(style)
        image_prompt = self.build_prompt(brand, brief)

        values = {"positive_prompt": image_prompt, "negative_prompt": negative_prompt}
        if checkpoint is not None:
            values["checkpoint"] = checkpoint
        if seed is not None:
            values["seed"] = seed
        workflow = patch_workflow(spec.template, spec.node_mapping, values)

        prompt_id = self._comfyui.submit(workflow)
        outputs = self._comfyui.wait_for_outputs(prompt_id)
        refs = self._comfyui.collect_file_refs(outputs, key="images")

        out_dir = Path(assets_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, ref in enumerate(refs):
            data = self._comfyui.fetch_output_bytes(ref["filename"], ref.get("subfolder", ""), ref.get("type", "output"))
            path = out_dir / f"{prompt_id}-{i}{Path(ref['filename']).suffix or '.png'}"
            path.write_bytes(data)
            saved.append(path)
        return saved

    def _load_spec(self, style: str) -> WorkflowSpec:
        return load_workflow_spec(self._workflows_dir, style)
