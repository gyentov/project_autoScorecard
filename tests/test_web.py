"""The browser build's Python side: LLM calls through the Worker, the in-process bridge to
the FastAPI app, and the browser config."""
import asyncio
import json

import pytest
import yaml

from autoscorecard.config import ProviderConfig, ROOT
from autoscorecard.llm import LLMRequest, Message, ToolSpec
from autoscorecard.llm.base import AuthFailed, BadRequest, OutOfCredit, RateLimited, Transient
from autoscorecard.llm.proxy import ProxyProvider
from autoscorecard.llm.router import build_provider

ANTH = ProviderConfig(type="anthropic", model="claude-haiku-4-5", key_env="ANTHROPIC_API_KEY", max_tokens=500)
OAI = ProviderConfig(type="openai_compat", model="gemini-flash-latest", key_env="GEMINI_API_KEY",
                     base_url="https://example/v1")
REQ = LLMRequest("sys", [Message.user("halo")], [ToolSpec("t", "d", {"type": "object", "properties": {}})],
                 agent="partner", project_id="p1")


def fake(status=200, body=None, headers=None, sent=None):
    def post(url, payload):
        if sent is not None:
            sent.append((url, payload))
        return status, headers or {}, json.dumps(body or {})
    return post


def test_proxy_sends_the_providers_own_request_without_a_key():
    sent = []
    p = ProxyProvider("claude_haiku", ANTH, "https://site/llm/", fake(body={
        "content": [{"type": "text", "text": "hai"}, {"type": "tool_use", "id": "c1", "name": "t", "input": {"a": 1}}],
        "stop_reason": "tool_use", "model": "claude-haiku-4-5",
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7}}, sent=sent))
    r = p.complete(REQ)
    url, payload = sent[0]
    assert url == "https://site/llm/chat" and payload["provider"] == "claude_haiku"
    assert payload["body"]["model"] == "claude-haiku-4-5" and payload["body"]["max_tokens"] == 500
    assert set(payload["body"]) == {"model", "max_tokens", "system", "messages", "cache_control", "tools"}
    assert "key" not in json.dumps(payload).lower()                 # keys live in the Worker only
    assert r.text == "hai" and r.tool_calls[0].args == {"a": 1} and r.stop == "tool_calls"
    assert r.usage.cache_read_tokens == 7


def test_proxy_openai_shape_and_parsing():
    sent = []
    p = ProxyProvider("gemini_free", OAI, "/llm", fake(body={
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x", "function": {"name": "t", "arguments": "{\"k\": 2}"}}]}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 10}}},
        sent=sent))
    r = p.complete(REQ)
    assert sent[0][1]["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert r.stop == "tool_calls" and r.tool_calls[0].args == {"k": 2}
    assert (r.usage.input_tokens, r.usage.cache_read_tokens) == (20, 10)


@pytest.mark.parametrize("status,body,headers,err", [
    (401, {"error": {"message": "ANTHROPIC_API_KEY is not set on the Worker"}}, {}, AuthFailed),
    (402, {"error": {"message": "budget reached"}}, {}, OutOfCredit),
    (400, {"error": {"message": "Your credit balance is too low"}}, {}, OutOfCredit),
    (429, {"error": {"message": "slow down"}}, {"retry-after": "3"}, RateLimited),
    (429, {"error": {"message": "You exceeded your current quota"}}, {}, OutOfCredit),
    (529, {"error": {"message": "overloaded"}}, {}, Transient),
    (400, {"error": {"message": "field not allowed: temperature"}}, {}, BadRequest),
])
def test_proxy_errors_map_to_the_router_categories(status, body, headers, err):
    with pytest.raises(err) as e:
        ProxyProvider("claude_haiku", ANTH, "/llm", fake(status, body, headers)).complete(REQ)
    if err is RateLimited:
        assert e.value.retry_after == 3.0


def test_proxy_unreachable_is_transient():
    def down(url, payload):
        raise OSError("network error")
    with pytest.raises(Transient):
        ProxyProvider("claude_haiku", ANTH, "/llm", down).complete(REQ)


def test_router_uses_the_proxy_only_when_configured():
    assert isinstance(build_provider("x", ANTH, "/llm"), ProxyProvider)
    assert not isinstance(build_provider("x", ANTH, None), ProxyProvider)
    assert type(build_provider("o", ProviderConfig(type="offline"), "/llm")).__name__ == "OfflineProvider"


def test_web_config_points_at_browser_storage_and_the_worker():
    from autoscorecard.web.boot import web_config
    cfg = web_config(ROOT / "config" / "config.yaml", "https://site/llm")
    assert cfg["app"]["projects_dir"] == "/data/projects" and cfg["app"]["inline_jobs"] is True
    assert cfg["app"]["llm_proxy_url"] == "https://site/llm"
    from autoscorecard.config import Settings
    Settings.model_validate(yaml.safe_load(yaml.safe_dump(cfg)))   # still a valid config


def test_bridge_answers_like_the_server():
    from autoscorecard.server.app import app
    from autoscorecard.web.bridge import handle
    r = asyncio.run(handle(app, "GET", "http://site/api/health", [("accept", "application/json")]))
    assert r["status"] == 200 and json.loads(r["body"])["ok"] is True
    r = asyncio.run(handle(app, "POST", "http://site/api/projects", [("content-type", "application/json")],
                           json.dumps({"name": "x", "model_type": "fraud"}).encode()))
    assert r["status"] == 400 and "scorecard" in json.loads(r["body"])["detail"]
    r = asyncio.run(handle(app, "GET", "http://site/api/projects/none/files/x?y=1", []))
    assert r["status"] == 404
