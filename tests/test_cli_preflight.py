import httpx
import pytest
import respx

from agency.cli import _run_preflight
from agency.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        postiz_base_url="http://postiz.local",
        postiz_api_key="k",
        ollama_base_url="http://ollama.local",
        ollama_model="m",
        openrouter_base_url="http://openrouter.local",
        openrouter_api_key="",
        openrouter_model="m",
        knowledge_root="k",
        state_root="s",
        org_dir="org",
        checkpoint_db_path="c",
        run_interval_seconds=1,
        comfyui_base_url="http://comfy.local",
        workflows_dir="workflows/image",
        video_workflows_dir="workflows/video",
        audio_workflows_dir="workflows/audio",
        assets_root="assets",
        content_root="content",
        telegram_bot_token="",
        telegram_chat_id="",
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from_addr="",
        smtp_to_addr="",
        smtp_use_tls=True,
        paperless_base_url="http://paperless.local",
        paperless_api_token="",
        embedding_model="",
        rerank_candidates=10,
    )
    base.update(overrides)
    return Settings(**base)


def _mock_everything_else_down():
    respx.get("http://postiz.local/").mock(return_value=httpx.Response(200))
    respx.route(host="ollama.local").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="comfy.local").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="paperless.local").mock(side_effect=httpx.ConnectError("down"))


@respx.mock
def test_preflight_reports_contract_ok_for_well_formed_integrations(tmp_path, capsys):
    respx.get("http://postiz.local/public/v1/integrations").mock(
        return_value=httpx.Response(200, json=[{"id": "int_1", "name": "Twitter", "disabled": False}])
    )
    _mock_everything_else_down()

    with pytest.raises(SystemExit):
        _run_preflight(_settings(state_root=str(tmp_path)))

    out = capsys.readouterr().out
    assert "GET /public/v1/integrations: OK (1 integration(s), contract matches)" in out


@respx.mock
def test_preflight_flags_contract_drift(tmp_path, capsys):
    respx.get("http://postiz.local/public/v1/integrations").mock(
        return_value=httpx.Response(200, json=[{"id": "int_1", "disabled": False}])  # no "name"
    )
    _mock_everything_else_down()

    with pytest.raises(SystemExit):
        _run_preflight(_settings(state_root=str(tmp_path)))

    out = capsys.readouterr().out
    assert "integration int_1" in out
    assert "name" in out


@respx.mock
def test_preflight_reports_non_json_response_without_crashing(tmp_path, capsys):
    respx.get("http://postiz.local/public/v1/integrations").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    _mock_everything_else_down()

    with pytest.raises(SystemExit):
        _run_preflight(_settings(state_root=str(tmp_path)))  # must not raise anything else

    out = capsys.readouterr().out
    assert "GET /public/v1/integrations: request failed" in out


@respx.mock
def test_preflight_skips_contract_check_when_postiz_api_key_missing(tmp_path, capsys):
    respx.get("http://postiz.local/").mock(return_value=httpx.Response(200))
    respx.route(host="ollama.local").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="comfy.local").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="paperless.local").mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(SystemExit):
        _run_preflight(_settings(state_root=str(tmp_path), postiz_api_key=""))

    out = capsys.readouterr().out
    assert "skipped (Postiz not reachable or POSTIZ_API_KEY not set)" in out


@respx.mock
def test_preflight_skips_contract_check_when_postiz_unreachable(tmp_path, capsys):
    respx.route(host="postiz.local").mock(side_effect=httpx.ConnectError("refused"))
    respx.route(host="ollama.local").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="comfy.local").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="paperless.local").mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(SystemExit):
        _run_preflight(_settings(state_root=str(tmp_path)))

    out = capsys.readouterr().out
    assert "skipped (Postiz not reachable or POSTIZ_API_KEY not set)" in out
