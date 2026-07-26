import json

import pytest

from agency.agents.music_agent import MusicAgent
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="upbeat and playful", audience="everyone", guidelines=[], banned_topics=[])


class RecordingProvider:
    def __init__(self, reply: str = "upbeat synth pop, 120bpm"):
        self.reply = reply
        self.last_system = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


class FakeComfyUIClient:
    def __init__(self):
        self.submitted_workflow = None

    def submit(self, workflow, client_id=None):
        self.submitted_workflow = workflow
        return "prompt-1"

    def wait_for_outputs(self, prompt_id, **kwargs):
        return {"9": {"audio": [{"filename": "agency_00001_.flac", "subfolder": "", "type": "output"}]}}

    def collect_file_refs(self, outputs, key):
        return outputs["9"].get(key, [])

    def fetch_output_bytes(self, filename, subfolder="", output_type="output"):
        return b"fake-flac-bytes"


class FakeApiFallback:
    def __init__(self):
        self.calls = []

    def generate(self, brief, assets_dir):
        self.calls.append(brief)
        path = assets_dir / "fallback.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fallback-bytes")
        return [path]


def _write_synthetic_workflow(tmp_path, name="default"):
    template = {
        "3": {"class_type": "KSampler", "inputs": {"seed": 0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }
    mapping = {"positive_prompt": "6.text", "seed": "3.seed"}
    (tmp_path / f"{name}.json").write_text(json.dumps(template))
    (tmp_path / f"{name}.mapping.json").write_text(json.dumps(mapping))


def test_build_prompt_uses_brand_voice():
    provider = RecordingProvider()
    MusicAgent(provider, FakeComfyUIClient(), "does-not-matter").build_prompt(_BRAND, "a jingle")

    assert "upbeat and playful" in provider.last_system


def test_generate_uses_local_comfyui_workflow_when_available(tmp_path):
    _write_synthetic_workflow(tmp_path)
    comfyui = FakeComfyUIClient()
    agent = MusicAgent(RecordingProvider(), comfyui, tmp_path)

    saved = agent.generate(_BRAND, "a jingle", assets_dir=tmp_path / "out", seed=3)

    assert comfyui.submitted_workflow["6"]["inputs"]["text"] == "upbeat synth pop, 120bpm"
    assert comfyui.submitted_workflow["3"]["inputs"]["seed"] == 3
    assert saved[0].read_bytes() == b"fake-flac-bytes"


def test_generate_falls_back_to_api_when_no_local_workflow(tmp_path):
    fallback = FakeApiFallback()
    agent = MusicAgent(RecordingProvider(), FakeComfyUIClient(), tmp_path, api_fallback=fallback)

    saved = agent.generate(_BRAND, "a jingle", assets_dir=tmp_path / "out", style="nonexistent")

    assert fallback.calls == ["a jingle"]
    assert saved[0].read_bytes() == b"fallback-bytes"


def test_generate_raises_when_no_local_workflow_and_no_fallback(tmp_path):
    agent = MusicAgent(RecordingProvider(), FakeComfyUIClient(), tmp_path)

    with pytest.raises(FileNotFoundError):
        agent.generate(_BRAND, "a jingle", assets_dir=tmp_path / "out", style="nonexistent")
