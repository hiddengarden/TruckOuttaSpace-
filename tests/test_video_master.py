import json

from agency.agents.video_master import VideoMaster
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="fast-paced and energetic", audience="everyone", guidelines=[], banned_topics=[])


class RecordingProvider:
    def __init__(self, reply: str = "a bicycle racing down a hill, drone shot"):
        self.reply = reply
        self.last_system = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


class FakeComfyUIClient:
    def __init__(self, output_key="images"):
        self.submitted_workflow = None
        self._output_key = output_key

    def submit(self, workflow, client_id=None):
        self.submitted_workflow = workflow
        return "prompt-1"

    def wait_for_outputs(self, prompt_id, **kwargs):
        return {"9": {self._output_key: [{"filename": "agency_00001.mp4", "subfolder": "", "type": "output"}]}}

    def collect_file_refs(self, outputs, key):
        return outputs["9"].get(key, [])

    def fetch_output_bytes(self, filename, subfolder="", output_type="output"):
        return b"fake-mp4-bytes"


def _write_synthetic_workflow(tmp_path, name="default"):
    template = {
        "3": {"class_type": "KSampler", "inputs": {"seed": 0}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "placeholder.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }
    mapping = {
        "positive_prompt": "6.text",
        "negative_prompt": "7.text",
        "seed": "3.seed",
        "checkpoint": "4.ckpt_name",
    }
    (tmp_path / f"{name}.json").write_text(json.dumps(template))
    (tmp_path / f"{name}.mapping.json").write_text(json.dumps(mapping))


def test_build_prompt_uses_brand_voice():
    provider = RecordingProvider()
    VideoMaster(provider, FakeComfyUIClient(), "does-not-matter").build_prompt(_BRAND, "a race")

    assert "fast-paced and energetic" in provider.last_system


def test_generate_patches_workflow_and_saves_native_video_output(tmp_path):
    _write_synthetic_workflow(tmp_path)
    comfyui = FakeComfyUIClient(output_key="images")
    video_master = VideoMaster(RecordingProvider(), comfyui, tmp_path)

    saved = video_master.generate(_BRAND, "a race", assets_dir=tmp_path / "out", seed=7)

    assert comfyui.submitted_workflow["6"]["inputs"]["text"] == "a bicycle racing down a hill, drone shot"
    assert comfyui.submitted_workflow["3"]["inputs"]["seed"] == 7
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"fake-mp4-bytes"


def test_generate_supports_vhs_style_gifs_output_key(tmp_path):
    _write_synthetic_workflow(tmp_path)
    comfyui = FakeComfyUIClient(output_key="gifs")
    video_master = VideoMaster(RecordingProvider(), comfyui, tmp_path)

    saved = video_master.generate(_BRAND, "a race", assets_dir=tmp_path / "out", output_key="gifs")

    assert len(saved) == 1
