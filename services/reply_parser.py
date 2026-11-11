"""Separates a model's private reasoning from its user-facing answer.

Reasoning can reach us in three different shapes, and all three are handled here:

1. A separate ``message.thinking`` field (Ollama's own parser split it off).
2. ``<think> ... </think>`` written into ``message.content``.
3. An ORPHAN closing tag: ``...reasoning...</think>answer``. Qwen-style templates can
   pre-fill the opening ``<think>`` in the prompt, so the model's output only ever contains
   the closing tag. A regex that requires ``<think>`` never matches this, and the whole
   reasoning leaks into the chat. That was the failure mode of the previous Xtrem version.

No keyword or phrase guessing is used: only the structural tags are interpreted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

OPEN, CLOSE = "<think>", "</think>"
_PAIR = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def split_reasoning(raw: str, orphan_close: bool = True) -> tuple[str, str]:
    """Return (visible_answer, reasoning) for raw model content.

    orphan_close: treat text before a lone ``</think>`` as reasoning. Enabled for models that
    can think; for other models a literal ``</think>`` in the answer is left untouched.
    """
    reasoning: list[str] = []
    text = raw
    if orphan_close and CLOSE in text:
        i_close, i_open = text.find(CLOSE), text.find(OPEN)
        if i_open == -1 or i_close < i_open:
            head, text = text.split(CLOSE, 1)
            reasoning.append(head)
    text = _PAIR.sub(lambda m: (reasoning.append(m.group(1)), "")[1], text)
    if OPEN in text:  # opened but never closed (yet): everything after it is reasoning
        text, tail = text.split(OPEN, 1)
        reasoning.append(tail)
    return text.strip(), "\n".join(r.strip() for r in reasoning if r.strip())


def clean_reply(raw: str, orphan_close: bool = True) -> str:
    return split_reasoning(raw, orphan_close)[0]


def _partial_tag_len(text: str) -> int:
    """Length of a trailing fragment that could be the start of <think> or </think>."""
    for tag in (OPEN, CLOSE):
        for n in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:n]):
                return n
    return 0


def visible_text(raw: str, orphan_close: bool = True) -> str:
    """Streaming-safe visible text (does not flash half-written tags)."""
    n = _partial_tag_len(raw)
    if n:
        raw = raw[:-n]
    return split_reasoning(raw, orphan_close)[0]


def _coerce_args(args) -> dict:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {"__invalid__": args}
    return args if isinstance(args, dict) else {"__invalid__": args}


@dataclass
class ReplyAccumulator:
    """Collects streamed Ollama chunks: content, thinking and tool calls."""
    orphan_close: bool = True
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    done_reason: str = ""

    def feed(self, chunk: dict) -> None:
        msg = chunk.get("message") or {}
        self.content += msg.get("content") or ""
        self.thinking += msg.get("thinking") or ""
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name"):
                self.tool_calls.append({"name": fn["name"], "arguments": _coerce_args(fn.get("arguments", {}))})
        if chunk.get("done"):
            self.done_reason = chunk.get("done_reason") or "stop"

    @property
    def visible(self) -> str:
        return visible_text(self.content, self.orphan_close)

    @property
    def final_visible(self) -> str:
        return clean_reply(self.content, self.orphan_close)

    @property
    def is_thinking(self) -> bool:
        """True while reasoning has arrived but nothing user-facing has."""
        return (bool(self.thinking) or OPEN in self.content) and not self.visible
