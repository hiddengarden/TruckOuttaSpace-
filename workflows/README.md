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
