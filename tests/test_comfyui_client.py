import httpx
import pytest
import respx

from agency.comfyui.client import ComfyUIClient, ComfyUIError


@respx.mock
def test_submit_returns_prompt_id():
    respx.post("http://comfy.local/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc123", "number": 1, "node_errors": {}})
    )

    client = ComfyUIClient("http://comfy.local")
    prompt_id = client.submit({"3": {"class_type": "KSampler", "inputs": {}}})

    assert prompt_id == "abc123"


@respx.mock
def test_submit_raises_on_validation_error():
    respx.post("http://comfy.local/prompt").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad node"}, "node_errors": {}})
    )

    client = ComfyUIClient("http://comfy.local")
    with pytest.raises(ComfyUIError):
        client.submit({})


@respx.mock
def test_wait_for_outputs_polls_until_history_has_prompt_id():
    responses = iter(
        [
            httpx.Response(200, json={}),
            httpx.Response(200, json={}),
            httpx.Response(
                200,
                json={"abc123": {"outputs": {"9": {"images": [{"filename": "agency_00001_.png", "subfolder": "", "type": "output"}]}}}},
            ),
        ]
    )
    respx.get("http://comfy.local/history/abc123").mock(side_effect=lambda request: next(responses))

    sleeps = []
    client = ComfyUIClient("http://comfy.local")
    outputs = client.wait_for_outputs("abc123", poll_interval=0.01, sleep_fn=sleeps.append)

    assert outputs == {"9": {"images": [{"filename": "agency_00001_.png", "subfolder": "", "type": "output"}]}}
    assert len(sleeps) == 2


@respx.mock
def test_wait_for_outputs_times_out():
    respx.get("http://comfy.local/history/abc123").mock(return_value=httpx.Response(200, json={}))

    client = ComfyUIClient("http://comfy.local")
    clock = iter([0.0, 0.0, 5.0])  # third check is past the 1-second deadline

    with pytest.raises(ComfyUIError):
        client.wait_for_outputs("abc123", timeout=1.0, sleep_fn=lambda _: None, clock=lambda: next(clock))


@respx.mock
def test_fetch_output_bytes():
    respx.get("http://comfy.local/view", params={"filename": "f.png", "subfolder": "", "type": "output"}).mock(
        return_value=httpx.Response(200, content=b"png-bytes")
    )

    client = ComfyUIClient("http://comfy.local")
    assert client.fetch_output_bytes("f.png") == b"png-bytes"


def test_collect_file_refs_gathers_across_nodes():
    outputs = {
        "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
        "10": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]},
        "11": {"other_key": [{"filename": "c.mp4"}]},
    }

    refs = ComfyUIClient.collect_file_refs(outputs, key="images")

    assert refs == [
        {"filename": "a.png", "subfolder": "", "type": "output"},
        {"filename": "b.png", "subfolder": "", "type": "output"},
    ]
