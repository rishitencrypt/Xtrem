"""Conversation history: what is stored, and what is sent to the model.

Only user messages and *cleaned, user-facing* assistant answers are kept. Reasoning is never
stored, so the model can't imitate its own earlier thinking. Old tool chatter from previous
turns is not replayed either: the assistant's final answer already summarises it.
"""
from __future__ import annotations

from datetime import date

from services.reply_parser import clean_reply

SYSTEM_PROMPT = """You are Xtrem, a private assistant running locally on the user's Mac.
Reply directly with the final answer. Be concise and useful. Use Markdown; put code in fenced blocks.
Today's date is {today}.

You can act through tools: files in the user's workspace folder, tasks and notes.
- When asked to create, save, move, rename, read or search files, tasks or notes, CALL THE TOOL. Never say
  you did something unless a tool result confirms it. Report the real path/ID from the tool result.
- To write an essay, report or any document to a file: write the full text yourself, then call create_file
  with that text as `content`. Choose a sensible filename (e.g. modern-politics-essay.txt) unless told one.
- If a tool returns an error, read it, fix the arguments and retry, or explain the problem plainly.
- Deleting and overwriting require the user's confirmation, which the app asks for itself.
- If you cannot do something with your tools, say so clearly. Do not pretend."""

NO_TOOLS_PROMPT = """You are Xtrem, a private assistant running locally on the user's Mac.
Reply directly with the final answer. Be concise and useful. Use Markdown; put code in fenced blocks.
Today's date is {today}.
Tools are not available with the current model, so you cannot create files, tasks or notes yourself.
If asked to, say so briefly and offer the text so the user can save it."""


def system_prompt(tools: bool, today: date | None = None) -> str:
    tpl = SYSTEM_PROMPT if tools else NO_TOOLS_PROMPT
    return tpl.format(today=(today or date.today()).isoformat())


def sanitize(messages: list[dict], orphan_close: bool = True) -> list[dict]:
    """Strip reasoning from assistant messages, drop anything empty or non-chat."""
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "assistant":
            content = clean_reply(content, orphan_close)
        if role in ("user", "assistant") and content.strip():
            out.append({"role": role, "content": content})
    return out


def trim(messages: list[dict], max_chars: int) -> list[dict]:
    """Newest messages that fit the character budget (the latest is always kept)."""
    kept: list[dict] = []
    total = 0
    for m in reversed(messages):
        total += len(m["content"])
        if kept and total > max_chars:
            break
        kept.append(m)
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


class Conversation:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str, orphan_close: bool = True) -> None:
        cleaned = clean_reply(text, orphan_close)
        if cleaned:
            self.messages.append({"role": "assistant", "content": cleaned})

    def clear(self) -> None:
        self.messages.clear()

    def history_for_model(self, max_chars: int, orphan_close: bool = True) -> list[dict]:
        return trim(sanitize(self.messages, orphan_close), max_chars)
