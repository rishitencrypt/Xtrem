"""Shared test doubles (no Ollama server needed)."""
from __future__ import annotations

import io
import json
import urllib.error


class FakeResponse:
    def __init__(self, lines: list[dict | str] | None = None, body: dict | None = None):
        self._lines = [(json.dumps(l) if isinstance(l, dict) else l).encode() + b"\n" for l in (lines or [])]
        self._body = json.dumps(body or {}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._body


def http_error(code: int, message: str):
    return urllib.error.HTTPError("http://localhost:11434/api/x", code, "err", {},
                                  io.BytesIO(json.dumps({"error": message}).encode()))


def chunk(content: str = "", thinking: str = "", done: bool = False, reason: str = "stop", tool_calls=None) -> dict:
    msg = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    if tool_calls:
        msg["tool_calls"] = tool_calls
    out = {"message": msg, "done": done}
    if done:
        out["done_reason"] = reason
    return out


def reply(*parts: str, thinking: str = "") -> list[dict]:
    """A normal streamed reply: optional thinking chunk, content split into parts, done chunk."""
    chunks = [chunk(thinking=thinking)] if thinking else []
    chunks += [chunk(p) for p in parts]
    chunks.append(chunk(done=True))
    return chunks


def tool_call(name: str, **args) -> list[dict]:
    return [chunk(tool_calls=[{"function": {"name": name, "arguments": args}}]), chunk(done=True)]


class FakeClient:
    """Stands in for OllamaClient: scripted replies, records every payload sent."""

    def __init__(self, scripts: list, caps=("completion", "tools", "thinking")):
        self.scripts = list(scripts)
        self.caps = set(caps)
        self.payloads: list[dict] = []
        self.cancelled = False

    def capabilities(self, model, refresh=False):
        if isinstance(self.caps, Exception):
            raise self.caps
        return self.caps

    def cancel(self):
        self.cancelled = True

    def chat_stream(self, payload):
        self.payloads.append(payload)
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        yield from script
