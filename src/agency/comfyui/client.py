import time
import uuid
from typing import Any, Callable

import httpx


class ComfyUIError(Exception):
    pass


class ComfyUIClient:
    """Thin wrapper over ComfyUI's queue API: POST /prompt, GET /history/{id}, GET /view.

    Verified against comfyanonymous/ComfyUI source (server.py route handlers)
    and its own script_examples/basic_api_example.py: submit a workflow
    graph, poll history until the prompt_id key appears (its "outputs" dict
    is populated by each SaveImage/SaveAudio/etc. node), then fetch each
    output file by filename/subfolder/type via /view.
    """

    def __init__(self, base_url: str, timeout: float = 300.0, client: httpx.Client | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def submit(self, workflow: dict[str, Any], client_id: str | None = None) -> str:
        response = self._client.post(
            f"{self._base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id or str(uuid.uuid4())},
        )
        if response.status_code >= 400:
            body = response.json()
            raise ComfyUIError(f"ComfyUI rejected the workflow: {body.get('error') or body}")
        return response.json()["prompt_id"]

    def wait_for_outputs(
        self,
        prompt_id: str,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict[str, Any]:
        deadline = clock() + timeout
        while True:
            response = self._client.get(f"{self._base_url}/history/{prompt_id}")
            response.raise_for_status()
            history = response.json()
            if prompt_id in history:
                return history[prompt_id]["outputs"]
            if clock() >= deadline:
                raise ComfyUIError(f"Timed out waiting for ComfyUI prompt {prompt_id}")
            sleep_fn(poll_interval)

    def fetch_output_bytes(self, filename: str, subfolder: str = "", output_type: str = "output") -> bytes:
        response = self._client.get(
            f"{self._base_url}/view", params={"filename": filename, "subfolder": subfolder, "type": output_type}
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def collect_file_refs(outputs: dict[str, Any], key: str) -> list[dict[str, str]]:
        """Pulls e.g. every SaveImage node's {"filename","subfolder","type"} entries out of
        a /history response's "outputs" dict. `key` is "images" for SaveImage, "gifs" for
        video-combine nodes, etc. -- whatever the specific workflow's output node uses."""
        refs: list[dict[str, str]] = []
        for node_output in outputs.values():
            refs.extend(node_output.get(key, []))
        return refs

    def close(self) -> None:
        self._client.close()
