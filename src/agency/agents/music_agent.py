from pathlib import Path
from typing import Protocol

from agency.comfyui.client import ComfyUIClient
from agency.comfyui.workflow import load_workflow_spec, patch_workflow
from agency.inference.provider import LLMProvider
from agency.org import BrandContext

_SYSTEM_TEMPLATE = (
    "You write music/sound-effect generation prompts for {name}.\n"
    "Mood/style should match this brand voice: {voice}\n"
    "Output ONLY the audio prompt as comma-separated descriptive tags/phrases "
    "(genre, mood, instrumentation, tempo), nothing else -- no commentary, no quotes."
)


class MusicApiProvider(Protocol):
    """Bring-your-own fallback for when no local ComfyUI audio workflow is configured.

    There's no standard analogous to OpenAI-compatible chat completions for
    audio generation across vendors, so unlike LLM inference this repo ships
    no concrete implementation -- plug in whichever API (ElevenLabs, Suno,
    etc.) you actually have a key for.
    """

    def generate(self, brief: str, assets_dir: Path) -> list[Path]: ...


class MusicAgent:
    """Writes music/SFX prompts and renders them via ComfyUI's native audio
    nodes (Stable Audio, MusicGen, etc.) -- output collected under the
    "audio" key, per SaveAudio's AudioSaveHelper.get_save_audio_ui() ->
    SavedAudios.as_dict() in comfy_api/latest/_ui.py. Falls back to a
    provided MusicApiProvider if no local workflow is configured for the
    requested style.
    """

    def __init__(
        self,
        provider: LLMProvider,
        comfyui_client: ComfyUIClient,
        workflows_dir: str | Path,
        api_fallback: MusicApiProvider | None = None,
    ):
        self._provider = provider
        self._comfyui = comfyui_client
        self._workflows_dir = Path(workflows_dir)
        self._api_fallback = api_fallback

    def build_prompt(self, brand: BrandContext, brief: str) -> str:
        system = _SYSTEM_TEMPLATE.format(name=brand.name, voice=brand.voice)
        return self._provider.complete(system, f"Create an audio prompt for: {brief}").strip()

    def generate(
        self,
        brand: BrandContext,
        brief: str,
        assets_dir: str | Path,
        style: str = "default",
        seed: int | None = None,
        output_key: str = "audio",
    ) -> list[Path]:
        try:
            spec = load_workflow_spec(self._workflows_dir, style)
        except FileNotFoundError:
            if self._api_fallback is None:
                raise
            return self._api_fallback.generate(brief, Path(assets_dir))

        audio_prompt = self.build_prompt(brand, brief)
        values = {"positive_prompt": audio_prompt}
        if seed is not None:
            values["seed"] = seed
        workflow = patch_workflow(spec.template, spec.node_mapping, values)

        prompt_id = self._comfyui.submit(workflow)
        outputs = self._comfyui.wait_for_outputs(prompt_id, timeout=600.0)
        refs = self._comfyui.collect_file_refs(outputs, key=output_key)

        out_dir = Path(assets_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, ref in enumerate(refs):
            data = self._comfyui.fetch_output_bytes(ref["filename"], ref.get("subfolder", ""), ref.get("type", "output"))
            path = out_dir / f"{prompt_id}-{i}{Path(ref['filename']).suffix or '.flac'}"
            path.write_bytes(data)
            saved.append(path)
        return saved
