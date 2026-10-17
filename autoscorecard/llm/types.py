"""Provider-neutral message types.

Agents only ever build these. Each provider adapter translates them to its own
wire format, so switching Claude <-> Gemini <-> OpenAI needs no agent changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


@dataclass
class Text:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


Block = Union[Text, ToolCall, ToolResult]


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: list[Block]

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls("user", [Text(text)])

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls("assistant", [Text(text)])


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema object


@dataclass
class Usage:
    input_tokens: int = 0         # uncached input
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


StopReason = Literal["end", "tool_calls", "max_tokens", "refusal", "other"]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop: StopReason
    usage: Usage
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    raw_stop: str | None = None

    def as_message(self) -> Message:
        blocks: list[Block] = []
        if self.text:
            blocks.append(Text(self.text))
        blocks.extend(self.tool_calls)
        return Message("assistant", blocks)


@dataclass
class LLMRequest:
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    max_tokens: int | None = None   # None -> provider default
    agent: str = ""                 # for logging / cost attribution
    project_id: str | None = None
