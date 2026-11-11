"""The assistant loop: model <-> validated tools. Pure Python (no Qt), so it is unit-testable.

How the 'model shows its thinking' bug is handled
-------------------------------------------------
* Ollama reports each model's capabilities (``/api/show``). The ``think`` option is only sent to
  models that can think; sending it to others is an error on current Ollama versions.
* think_mode "hidden" (default): ask for ``think: true``. Ollama's own parser then returns the
  reasoning in a separate ``message.thinking`` field, which we discard and never store or replay.
  This is the reliable setting: ``think: false`` is ignored by models whose template always
  thinks, and then the reasoning lands in ``message.content`` unseparated.
* think_mode "off": ``think: false`` (faster). Reasoning tags that still leak into content, including
  an orphan ``</think>``, are removed by services.reply_parser.
* A model without thinking support (for example an *-instruct* variant) needs neither.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from services.conversation import system_prompt
from services.ollama_client import (OllamaClient, OllamaError, ThinkingUnsupported,
                                    ToolsUnsupported)
from services.reply_parser import ReplyAccumulator
from services.tools import ToolRegistry, ToolResult


@dataclass
class AgentSettings:
    model: str
    think_mode: str = "hidden"       # hidden | off
    num_ctx: int = 8192
    tools_enabled: bool = True
    max_steps: int = 6
    max_calls_per_step: int = 4


@dataclass
class RunResult:
    ok: bool
    text: str = ""
    error: str = ""
    cancelled: bool = False
    tool_log: list[tuple[str, ToolResult]] = field(default_factory=list)
    segment: int = 0   # index of the final assistant message segment (for the UI)


Emit = Callable[[str, object], None]


class AgentRunner:
    def __init__(self, client: OllamaClient, registry: ToolRegistry, settings: AgentSettings,
                 emit: Emit | None = None, cancelled: Callable[[], bool] | None = None) -> None:
        self.client = client
        self.registry = registry
        self.s = settings
        self.emit: Emit = emit or (lambda *_: None)
        self.cancelled = cancelled or (lambda: False)

    # ---- request construction ----
    def build_payload(self, messages: list[dict], caps: set[str], use_think: bool, use_tools: bool) -> dict:
        thinks = "thinking" in caps
        hidden = thinks and self.s.think_mode == "hidden"
        payload: dict = {
            "model": self.s.model, "messages": messages, "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": self.s.num_ctx, "num_predict": 4096 if hidden else 2048,
                        "temperature": 0.6},
        }
        if thinks and use_think:
            payload["think"] = hidden  # True: Ollama separates reasoning; False: no reasoning
        if use_tools:
            payload["tools"] = self.registry.schemas()
        return payload

    # ---- main loop ----
    def run(self, history: list[dict]) -> RunResult:
        try:
            caps = self.client.capabilities(self.s.model)
        except OllamaError as exc:
            return RunResult(False, error=str(exc))
        orphan_close = "thinking" in caps or not caps
        use_think = True
        use_tools = self.s.tools_enabled and ("tools" in caps or not caps)
        tools_ran: list[tuple[str, ToolResult]] = []
        seen_calls: dict[str, int] = {}
        convo = list(history)

        for step in range(self.s.max_steps):
            retried = False
            while True:
                messages = [{"role": "system", "content": system_prompt(use_tools)}] + convo
                payload = self.build_payload(messages, caps, use_think, use_tools)
                acc = ReplyAccumulator(orphan_close=orphan_close)
                try:
                    self._stream(payload, acc, step)
                    break
                except ThinkingUnsupported:
                    if retried or not use_think:
                        return RunResult(False, error="This model does not support the thinking option.")
                    use_think, retried = False, True
                except ToolsUnsupported:
                    if retried or not use_tools:
                        return RunResult(False, error="This model does not support tools.")
                    use_tools, retried = False, True
                except OllamaError as exc:
                    if self.cancelled():
                        return RunResult(False, cancelled=True, tool_log=tools_ran)
                    return RunResult(False, error=str(exc), tool_log=tools_ran)
            if self.cancelled():
                return RunResult(False, text=acc.final_visible, cancelled=True, tool_log=tools_ran)
            if not acc.done_reason:
                return RunResult(False, error="The connection closed before the reply finished.",
                                 tool_log=tools_ran)

            if acc.tool_calls and use_tools:
                convo.append({"role": "assistant", "content": acc.final_visible, "tool_calls": [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}} for c in acc.tool_calls]})
                for call in acc.tool_calls[: self.s.max_calls_per_step]:
                    key = f"{call['name']}:{json.dumps(call['arguments'], sort_keys=True, default=str)}"
                    seen_calls[key] = seen_calls.get(key, 0) + 1
                    if seen_calls[key] > 2:
                        return RunResult(False, error="The assistant kept repeating the same action, so it was stopped.",
                                         tool_log=tools_ran)
                    self.emit("status", f"Running {call['name']}…")
                    result = self.registry.execute(call["name"], call["arguments"])
                    tools_ran.append((call["name"], result))
                    self.emit("tool", (call["name"], call["arguments"], result))
                    convo.append({"role": "tool", "tool_name": call["name"], "content": result.message})
                    if self.cancelled():
                        return RunResult(False, cancelled=True, tool_log=tools_ran)
                continue

            final = acc.final_visible
            if not final and not tools_ran:
                if acc.done_reason == "length":
                    return RunResult(False, error=(
                        "The model used its whole token limit before giving an answer (most likely while "
                        "thinking). In Settings, set Reasoning to 'Fast', or choose a non-thinking model."))
                return RunResult(False, error="The model returned an empty reply.")
            return RunResult(True, text=final, tool_log=tools_ran, segment=step)

        return RunResult(False, error="Stopped after too many tool steps.", tool_log=tools_ran)

    def _stream(self, payload: dict, acc: ReplyAccumulator, segment: int) -> None:
        last_visible = None
        announced = False
        for chunk in self.client.chat_stream(payload):
            if self.cancelled():
                self.client.cancel()
                return
            acc.feed(chunk)
            if acc.is_thinking:
                if not announced:
                    announced = True
                    self.emit("status", "Thinking…")
                continue
            visible = acc.visible
            if visible != last_visible:
                last_visible = visible
                self.emit("text", (segment, visible))
