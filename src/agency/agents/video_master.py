from pathlib import Path

from agency.comfyui.client import ComfyUIClient
from agency.comfyui.workflow import patch_workflow, load_workflow_spec
from agency.inference.provider import LLMProvider
from agency.org import BrandContext

_SYSTEM_TEMPLATE = (
    "You write video-generation prompts for {name}.\n"
    "Visual style should match this brand voice: {voice}\n"
    "Output ONLY the video prompt as comma-separated descriptive tags/phrases "
    "(subject, motion, camera, style), nothing else -- no commentary, no quotes."
)


class VideoMaster:
    """Writes video-generation prompts and renders them via ComfyUI, for the
    Studio Worker to assemble into shorts/reels/longer edits.

    Same submit/poll/fetch pattern as Artist. `output_key` defaults to
    "images" -- ComfyUI's native SaveVideo/SaveWEBM nodes report their
    output under that same key (with an extra "animated" flag), per
    comfy_api/latest/_ui.py's PreviewVideo/SavedImages helpers. Pass
    output_key="gifs" instead if your workflow uses the third-party
    VideoHelperSuite combine node, which uses its own key.
    """

    def __init__(self, provider: LLMProvider, comfyui_client: ComfyUIClient, workflows_dir: str | Path):
        self._provider = provider
        self._comfyui = comfyui_client
        self._workflows_dir = Path(workflows_dir)

    def build_prompt(self, brand: BrandContext, brief: str) -> str:
        system = _SYSTEM_TEMPLATE.format(name=brand.name, voice=brand.voice)
        return self._provider.complete(system, f"Create a video prompt for: {brief}").strip()

    def generate(
        self,
        brand: BrandContext,
        brief: str,
        assets_dir: str | Path,
        style: str = "default",
        negative_prompt: str = "",
        checkpoint: str | None = None,
        seed: int | None = None,
        output_key: str = "images",
    ) -> list[Path]:
        spec = load_workflow_spec(self._workflows_dir, style)
        video_prompt = self.build_prompt(brand, brief)

        values = {"positive_prompt": video_prompt, "negative_prompt": negative_prompt}
        if checkpoint is not None:
            values["checkpoint"] = checkpoint
        if seed is not None:
            values["seed"] = seed
        workflow = patch_workflow(spec.template, spec.node_mapping, values)

        prompt_id = self._comfyui.submit(workflow)
        outputs = self._comfyui.wait_for_outputs(prompt_id, timeout=1800.0)
        refs = self._comfyui.collect_file_refs(outputs, key=output_key)

        out_dir = Path(assets_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, ref in enumerate(refs):
            data = self._comfyui.fetch_output_bytes(ref["filename"], ref.get("subfolder", ""), ref.get("type", "output"))
            path = out_dir / f"{prompt_id}-{i}{Path(ref['filename']).suffix or '.mp4'}"
            path.write_bytes(data)
            saved.append(path)
        return saved
