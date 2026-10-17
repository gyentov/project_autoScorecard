from types import SimpleNamespace

import pytest

from autoscorecard.config import Price, ProviderConfig, Settings
from autoscorecard.llm import LLMRequest, LLMRouter, Message, ToolCall, ToolResult, ToolSpec, Usage
from autoscorecard.llm.anthropic_provider import AnthropicProvider, to_anthropic_messages
from autoscorecard.llm.base import OutOfCredit, Provider, RateLimited, Transient
from autoscorecard.llm.cost import Ledger, cost_usd
from autoscorecard.llm.openai_compat import OpenAICompatProvider, to_openai_messages
from autoscorecard.llm.ratelimit import RpmLimiter
from autoscorecard.llm.types import LLMResponse


# ---------- helpers ---------------------------------------------------------
class FakeProvider(Provider):
    def __init__(self, name, cfg, script):
        super().__init__(name, cfg)
        self.script = list(script)   # exceptions to raise, or text to return
        self.calls = 0

    def complete(self, req):
        self.calls += 1
        item = self.script.pop(0) if self.script else "ok"
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, tool_calls=[], stop="end",
                           usage=Usage(input_tokens=1000, output_tokens=100),
                           provider=self.name, model=self.cfg.model)


def make_settings(tmp_path, budget=4.5):
    paid = ProviderConfig(type="anthropic", model="claude-haiku-4-5",
                          price=Price(input=1, output=5))
    free = ProviderConfig(type="openai_compat", model="gemini", price=Price())
    off = ProviderConfig(type="offline")
    s = Settings(
        providers={"paid": paid, "free": free, "offline": off},
        agents={"partner": "paid"},
        fallback_chain=["paid", "free", "offline"],
        budget={"total_usd": budget, "per_project_usd": 1.0},
        app={"ledger_path": tmp_path / "ledger.jsonl", "projects_dir": tmp_path / "p"},
    )
    return s


def make_router(tmp_path, scripts, budget=4.5):
    s = make_settings(tmp_path, budget)
    provs = {n: FakeProvider(n, s.providers[n], scripts.get(n, [])) for n in s.providers}
    events = []
    r = LLMRouter(s, providers=provs, on_event=events.append, sleep=lambda _: None)
    return r, provs, events


REQ = LLMRequest(system="sys", messages=[Message.user("hi")], agent="partner", project_id="p1")


# ---------- cost & ledger ---------------------------------------------------
def test_cost_formula():
    p = Price(input=1, output=5, cache_write=1.25, cache_read=0.1)
    u = Usage(input_tokens=1_000_000, output_tokens=200_000,
              cache_write_tokens=100_000, cache_read_tokens=1_000_000)
    assert cost_usd(u, p) == pytest.approx(1 + 1 + 0.125 + 0.1)


def test_ledger_persists(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.record(provider="paid", model="m", agent="a", project_id="p1",
               usage=Usage(), cost=0.25)
    again = Ledger(tmp_path / "l.jsonl")
    assert again.total_usd == pytest.approx(0.25)
    assert again.project_usd("p1") == pytest.approx(0.25)


# ---------- router ----------------------------------------------------------
def test_router_uses_agent_provider_and_records_cost(tmp_path, monkeypatch):
    r, provs, _ = make_router(tmp_path, {})
    resp = r.complete(REQ)
    assert resp.provider == "paid"
    assert resp.cost_usd == pytest.approx((1000 * 1 + 100 * 5) / 1e6)
    assert r.ledger.total_usd == pytest.approx(resp.cost_usd)


def test_out_of_credit_falls_back_and_disables(tmp_path):
    r, provs, events = make_router(tmp_path, {"paid": [OutOfCredit("paid", "credit balance too low")]})
    assert r.complete(REQ).provider == "free"
    assert r.complete(REQ).provider == "free"      # paid stays disabled
    assert provs["paid"].calls == 1
    assert any(e["type"] == "provider_disabled" for e in events)


def test_rate_limit_retries_then_succeeds(tmp_path):
    r, provs, events = make_router(tmp_path, {"paid": [RateLimited("paid", "429", 1.0), "hello"]})
    resp = r.complete(REQ)
    assert resp.provider == "paid" and resp.text == "hello"
    assert any(e["type"] == "waiting" for e in events)


def test_transient_exhausts_attempts_then_falls_back(tmp_path):
    errs = [Transient("paid", "503")] * 3
    r, provs, _ = make_router(tmp_path, {"paid": errs})
    assert r.complete(REQ).provider == "free"
    assert provs["paid"].calls == 3


def test_budget_blocks_paid_but_not_free(tmp_path):
    r, provs, _ = make_router(tmp_path, {}, budget=0.0)
    assert r.complete(REQ).provider == "free"
    assert provs["paid"].calls == 0


def test_project_budget(tmp_path):
    r, provs, _ = make_router(tmp_path, {})
    r.ledger.record(provider="paid", model="m", agent="a", project_id="p1", usage=Usage(), cost=1.0)
    assert r.complete(REQ).provider == "free"


def test_missing_key_is_skipped(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    s.providers["paid"].key_env = "DEFINITELY_UNSET_KEY_XYZ"
    monkeypatch.delenv("DEFINITELY_UNSET_KEY_XYZ", raising=False)
    r = LLMRouter(s, providers={"free": FakeProvider("free", s.providers["free"], [])},
                  sleep=lambda _: None)
    assert r.complete(REQ).provider == "free"


# ---------- rate limiter ----------------------------------------------------
def test_rpm_limiter_waits_when_full():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    lim = RpmLimiter(2, clock=lambda: t[0], sleep=sleep)
    lim.acquire(); lim.acquire()
    t[0] = 10
    lim.acquire()
    assert slept == [pytest.approx(50)]


# ---------- message translation ---------------------------------------------
CONVO = [
    Message.user("buat scorecard"),
    Message("assistant", [ToolCall("c1", "profile", {"table": "loans"})]),
    Message("user", [ToolResult("c1", '{"rows": 10}')]),
]


def test_to_anthropic_messages():
    out = to_anthropic_messages(CONVO)
    assert out[1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "profile",
                                    "input": {"table": "loans"}}
    assert out[2]["content"][0]["type"] == "tool_result"


def test_to_openai_messages():
    out = to_openai_messages("sys", CONVO)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2]["tool_calls"][0]["function"]["arguments"] == '{"table": "loans"}'
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"rows": 10}'}


# ---------- adapters with fake SDK clients ----------------------------------
def test_anthropic_adapter_parses_response():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Siap Boss."),
                     SimpleNamespace(type="tool_use", id="t1", name="profile", input={"a": 1})],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=7),
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    cfg = ProviderConfig(type="anthropic", model="claude-haiku-4-5")
    p = AnthropicProvider("c", cfg, client=client)
    tool = ToolSpec("profile", "Profile a table", {"type": "object", "properties": {}})
    r = p.complete(LLMRequest(system="s", messages=[Message.user("x")], tools=[tool]))
    assert r.stop == "tool_calls" and r.tool_calls[0].args == {"a": 1}
    assert r.usage.cache_read_tokens == 7
    assert captured["cache_control"] == {"type": "ephemeral"}
    assert captured["tools"][0]["input_schema"] == tool.parameters


def test_openai_adapter_parses_response():
    def create(**kw):
        tc = SimpleNamespace(id="t1", function=SimpleNamespace(name="profile", arguments='{"a": 1}'))
        msg = SimpleNamespace(content=None, tool_calls=[tc])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=4, prompt_tokens_details=None),
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    cfg = ProviderConfig(type="openai_compat", model="gemini")
    r = OpenAICompatProvider("g", cfg, client=client).complete(
        LLMRequest(system="s", messages=[Message.user("x")]))
    assert r.stop == "tool_calls"          # normalised even though finish_reason="stop"
    assert r.tool_calls[0].args == {"a": 1}
    assert r.usage.input_tokens == 20
