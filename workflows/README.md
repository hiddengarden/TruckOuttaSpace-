# ComfyUI workflow templates

`image/default.json` is ComfyUI's own official example graph, taken verbatim
(structure and node IDs) from `script_examples/basic_api_example.py` in
comfyanonymous/ComfyUI -- the exact API-format JSON their docs use to
demonstrate `POST /prompt`. It will not actually render anything on your
instance as-is: `ckpt_name` names a specific SD1.5 checkpoint
(`v1-5-pruned-emaonly.safetensors`) that almost certainly isn't what you
have installed, and this repo has no way to know what checkpoints, samplers,
or custom nodes your ComfyUI setup has (that's entirely a property of your
own instance, AMD ROCm/Vulkan build included -- this client is just an HTTP
caller and has no GPU-specific code at all).

To use your own workflow:
1. Build the graph you want in ComfyUI's UI (whatever checkpoint/sampler you
   actually have).
2. `Workflow -> Export (API)` and save it as `image/<name>.json`.
3. Write `image/<name>.mapping.json` pointing at the node IDs you want the
   `Artist` agent to patch per generation, e.g.:
   ```json
   {
       "positive_prompt": "6.text",
       "negative_prompt": "7.text",
       "seed": "3.seed",
       "checkpoint": "4.ckpt_name"
   }
   ```
   Only include the fields your workflow actually has nodes for -- `Artist`
   silently skips any mapping key it doesn't need to set.
4. Pass `style="<name>"` when calling the agent to select it.

`default.json`/`default.mapping.json` exist mainly so `patch_workflow()` has
a real, sourced fixture to test against -- not as something meant to run
unmodified against your instance.

## Video (`video/`)

Same idea, under `video/<name>.json` + `video/<name>.mapping.json` (see
`VIDEO_WORKFLOWS_DIR`, default `./workflows/video`). No default is shipped
here -- unlike the image case, there's no equivalent official ComfyUI
example graph to source one from truthfully, and image-generation workflows
aren't drop-in compatible with video ones. Build your own image-to-video or
text-to-video graph in the UI, export it, and write the mapping the same
way. `VideoMaster.generate()`'s `output_key` defaults to `"images"` --
ComfyUI's native `SaveVideo`/`SaveWEBM` nodes report output under that same
key (per `comfy_api/latest/_ui.py`) -- pass `output_key="gifs"` instead if
your graph uses the third-party VideoHelperSuite combine node.

## Audio (`audio/`)

Same idea again, under `audio/<name>.json` + `audio/<name>.mapping.json`
(see `AUDIO_WORKFLOWS_DIR`, default `./workflows/audio`) -- a Stable
Audio/MusicGen-style graph, no default shipped for the same reason as
video. `MusicAgent` collects output under the `"audio"` key, per
`SaveAudio`'s `AudioSaveHelper.get_save_audio_ui()` ->
`SavedAudios.as_dict()` in `comfy_api/latest/_ui.py` -- a different key
from images/video. If you have no local audio workflow at all,
`MusicAgent` accepts a `MusicApiProvider` (bring your own -- there's no
cross-vendor standard for audio generation the way OpenAI-compatible chat
completions covers LLMs) as a fallback.
