from agency.agents.artist import Artist
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="bold and minimal", audience="everyone", guidelines=[], banned_topics=[])


class RecordingProvider:
    def __init__(self, reply: str = "a red bicycle, studio lighting"):
        self.reply = reply
        self.last_system = None
        self.last_user = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self.reply


class FakeComfyUIClient:
    def __init__(self):
        self.submitted_workflow = None

    def submit(self, workflow, client_id=None):
        self.submitted_workflow = workflow
        return "prompt-1"

    def wait_for_outputs(self, prompt_id, **kwargs):
        return {"9": {"images": [{"filename": "agency_00001_.png", "subfolder": "", "type": "output"}]}}

    def collect_file_refs(self, outputs, key):
        return outputs["9"][key]

    def fetch_output_bytes(self, filename, subfolder="", output_type="output"):
        return b"fake-png-bytes"


def test_build_prompt_uses_brand_voice():
    provider = RecordingProvider()
    Artist(provider, FakeComfyUIClient(), "workflows/image").build_prompt(_BRAND, "a product launch")

    assert "bold and minimal" in provider.last_system


def test_generate_patches_workflow_and_saves_output(tmp_path):
    comfyui = FakeComfyUIClient()
    artist = Artist(RecordingProvider(), comfyui, "workflows/image")

    saved = artist.generate(_BRAND, "a product launch", assets_dir=tmp_path, seed=42)

    assert comfyui.submitted_workflow["6"]["inputs"]["text"] == "a red bicycle, studio lighting"
    assert comfyui.submitted_workflow["3"]["inputs"]["seed"] == 42
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"fake-png-bytes"
    assert saved[0].parent == tmp_path


def test_generate_can_override_checkpoint(tmp_path):
    comfyui = FakeComfyUIClient()
    artist = Artist(RecordingProvider(), comfyui, "workflows/image")

    artist.generate(_BRAND, "a product launch", assets_dir=tmp_path, checkpoint="sdxl_base.safetensors")

    assert comfyui.submitted_workflow["4"]["inputs"]["ckpt_name"] == "sdxl_base.safetensors"
