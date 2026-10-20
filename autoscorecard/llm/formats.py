"""Provider wire formats without the SDKs: our Message list to each API's JSON, the
stop reasons, and the quota wording. Shared by the SDK adapters (local server) and the
proxy adapter (the browser build, where the SDKs are not installed)."""
from __future__ import annotations

import json
from typing import Any

from autoscorecard.llm.types import Message, Text, ToolCall, ToolResult

ANTHROPIC_STOP = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_calls",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        blocks = []
        for b in m.content:
            if isinstance(b, Text):
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolCall):
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.args})
            elif isinstance(b, ToolResult):
                blocks.append({
                    "type": "tool_result", "tool_use_id": b.call_id,
                    "content": b.content, "is_error": b.is_error,
                })
        out.append({"role": m.role, "content": blocks})
    return out


OPENAI_STOP = {
    "stop": "end",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "max_tokens",
    "content_filter": "refusal",
}

# Phrases that mean "you're out of money / daily quota", as opposed to a
# short per-minute limit that clears by waiting.
QUOTA_MARKERS = ("insufficient_quota", "exceeded your current quota", "per day", "perday", "billing")


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        texts = [b.text for b in m.content if isinstance(b, Text)]
        calls = [b for b in m.content if isinstance(b, ToolCall)]
        results = [b for b in m.content if isinstance(b, ToolResult)]
        if m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                    for c in calls
                ]
            out.append(msg)
        else:
            # Tool results become separate role="tool" messages, before any user text.
            for r in results:
                content = f"ERROR: {r.content}" if r.is_error else r.content
                out.append({"role": "tool", "tool_call_id": r.call_id, "content": content})
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return out


def parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {"_unparsed": raw}
