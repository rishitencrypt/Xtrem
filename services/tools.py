"""Validated tool schemas and a controlled dispatcher between the model and Python.

The model can only call the tools registered here. Every argument is validated against an
explicit schema before any handler runs, destructive actions need user confirmation, and file
tools can only touch the configured workspace. There is no shell or arbitrary-code tool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from services.fs_service import WorkspaceError, Workspace
from storage.db import PRIORITIES, Database, StorageError


class ToolArgError(Exception):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    type: str                      # string | integer | boolean | date
    description: str
    required: bool = False
    enum: tuple[str, ...] | None = None
    max_len: int = 500


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: tuple[Param, ...] = ()
    destructive: bool = False


@dataclass
class ToolResult:
    ok: bool
    message: str                   # what the model sees (and the UI shows): factual, verified
    changed: tuple[str, ...] = ()  # which stores changed: files / tasks / notes


BIG = 500_000
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("list_files", "List files and folders in a workspace folder.",
             (Param("path", "string", "Folder relative to the workspace; '.' for the top."),)),
    ToolSpec("read_file", "Read a text file from the workspace.",
             (Param("path", "string", "File path.", True),)),
    ToolSpec("search_files", "Find files by name, or by text inside them.",
             (Param("query", "string", "Text to look for.", True),
              Param("in_content", "boolean", "Search inside file text instead of names."))),
    ToolSpec("create_file", "Create a NEW text file (txt, md, json, csv, ...) with the given content. "
             "Missing folders are created. Never overwrites.",
             (Param("path", "string", "File path, e.g. essays/politics.txt", True),
              Param("content", "string", "The complete text to write.", True, max_len=BIG),
              Param("on_conflict", "string", "If the name exists: rename (default) or error.",
                    enum=("rename", "error")))),
    ToolSpec("write_file", "Replace the entire contents of an EXISTING file (asks the user first).",
             (Param("path", "string", "File path.", True),
              Param("content", "string", "The new full content.", True, max_len=BIG)), destructive=True),
    ToolSpec("append_file", "Add text to the end of an existing file.",
             (Param("path", "string", "File path.", True),
              Param("content", "string", "Text to add.", True, max_len=BIG))),
    ToolSpec("make_folder", "Create a folder (and parents) in the workspace.",
             (Param("path", "string", "Folder path.", True),)),
    ToolSpec("move_file", "Move a file or folder into another workspace folder (created if missing).",
             (Param("source", "string", "Item to move.", True),
              Param("destination_folder", "string", "Target folder.", True))),
    ToolSpec("rename_file", "Rename a file or folder in place.",
             (Param("path", "string", "Item to rename.", True),
              Param("new_name", "string", "New name only, no folders.", True))),
    ToolSpec("delete_file", "Delete a file or folder (asks the user first).",
             (Param("path", "string", "Item to delete.", True),
              Param("recursive", "boolean", "Needed for non-empty folders.")), destructive=True),
    ToolSpec("open_file", "Open a workspace file in its default Mac application.",
             (Param("path", "string", "File path.", True),)),
    ToolSpec("add_task", "Create a task.",
             (Param("title", "string", "What needs doing.", True),
              Param("due", "date", "Due date YYYY-MM-DD."),
              Param("priority", "string", "low, normal or high.", enum=PRIORITIES),
              Param("category", "string", "Category or project name."),
              Param("details", "string", "Extra detail.", max_len=5000))),
    ToolSpec("list_tasks", "List tasks.",
             (Param("status", "string", "pending (default), completed, overdue or all.",
                    enum=("pending", "completed", "overdue", "all")),
              Param("priority", "string", "Filter by priority.", enum=PRIORITIES),
              Param("category", "string", "Filter by category."))),
    ToolSpec("complete_task", "Mark a task complete.", (Param("id", "integer", "Task id.", True),)),
    ToolSpec("update_task", "Edit a task. Use due='none' to clear the due date.",
             (Param("id", "integer", "Task id.", True), Param("title", "string", "New title."),
              Param("due", "string", "YYYY-MM-DD or 'none'."),
              Param("priority", "string", "low, normal or high.", enum=PRIORITIES),
              Param("category", "string", "New category."),
              Param("details", "string", "New details.", max_len=5000),
              Param("status", "string", "pending or completed.", enum=("pending", "completed")))),
    ToolSpec("delete_task", "Delete a task (asks the user first).",
             (Param("id", "integer", "Task id.", True),), destructive=True),
    ToolSpec("create_note", "Create a note.",
             (Param("title", "string", "Note title.", True),
              Param("body", "string", "Note text.", max_len=BIG),
              Param("project", "string", "Project name."),
              Param("task_id", "integer", "Link to a task id."))),
    ToolSpec("search_notes", "Search notes by text or project; empty query lists recent notes.",
             (Param("query", "string", "Text to search."), Param("project", "string", "Project filter."))),
    ToolSpec("read_note", "Read one note in full.", (Param("id", "integer", "Note id.", True),)),
    ToolSpec("update_note", "Edit a note. append_text adds to the end of the body.",
             (Param("id", "integer", "Note id.", True), Param("title", "string", "New title."),
              Param("body", "string", "Replace the body.", max_len=BIG),
              Param("append_text", "string", "Text to append.", max_len=BIG),
              Param("project", "string", "New project."), Param("task_id", "integer", "Link task id."))),
    ToolSpec("delete_note", "Delete a note (asks the user first).",
             (Param("id", "integer", "Note id.", True),), destructive=True),
)
SPEC_BY_NAME = {s.name: s for s in SPECS}


# ---------------------------------------------------------------- validation
def _coerce(p: Param, v):
    if p.type in ("string", "date"):
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise ToolArgError(f"'{p.name}' must be text.")
        v = str(v)
        if len(v) > p.max_len:
            raise ToolArgError(f"'{p.name}' is too long (max {p.max_len} characters).")
        if p.type == "date":
            try:
                v = date.fromisoformat(v.strip()).isoformat()
            except ValueError:
                raise ToolArgError(f"'{p.name}' must be a date like 2026-12-31.") from None
        if p.enum:
            v = v.strip().lower()
            if v not in p.enum:
                raise ToolArgError(f"'{p.name}' must be one of: {', '.join(p.enum)}.")
        return v
    if p.type == "integer":
        if isinstance(v, bool):
            raise ToolArgError(f"'{p.name}' must be a whole number.")
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().lstrip("#").isdigit():
            return int(v.strip().lstrip("#"))
        raise ToolArgError(f"'{p.name}' must be a whole number.")
    if p.type == "boolean":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in ("true", "yes", "1", "false", "no", "0"):
            return v.strip().lower() in ("true", "yes", "1")
        raise ToolArgError(f"'{p.name}' must be true or false.")
    raise ToolArgError(f"Unsupported parameter type for '{p.name}'.")


def validate_args(spec: ToolSpec, raw) -> dict:
    if raw is None:
        raw = {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raise ToolArgError("Arguments were not valid JSON.") from None
    if not isinstance(raw, dict) or "__invalid__" in raw:
        raise ToolArgError("Arguments must be a JSON object.")
    known = {p.name for p in spec.params}
    extra = sorted(set(raw) - known)
    if extra:
        raise ToolArgError(f"Unknown argument(s): {', '.join(extra)}. Allowed: {', '.join(sorted(known)) or 'none'}.")
    out: dict = {}
    for p in spec.params:
        v = raw.get(p.name)
        if v is None or (isinstance(v, str) and v == "" and not p.required):
            if p.required:
                raise ToolArgError(f"Missing required argument '{p.name}'.")
            continue
        if p.required and isinstance(v, str) and not v.strip() and p.name not in ("content",):
            raise ToolArgError(f"Argument '{p.name}' cannot be empty.")
        out[p.name] = _coerce(p, v)
    return out


def ollama_schemas(specs=SPECS) -> list[dict]:
    schemas = []
    for s in specs:
        props = {}
        for p in s.params:
            prop = {"type": "string" if p.type == "date" else p.type, "description": p.description}
            if p.enum:
                prop["enum"] = list(p.enum)
            props[p.name] = prop
        schemas.append({"type": "function", "function": {
            "name": s.name, "description": s.description,
            "parameters": {"type": "object", "properties": props,
                           "required": [p.name for p in s.params if p.required]}}})
    return schemas


# ---------------------------------------------------------------- dispatcher
class ToolRegistry:
    def __init__(self, workspace: Workspace, db: Database,
                 confirm: Callable[[str], bool] | None = None) -> None:
        self.ws = workspace
        self.db = db
        self.confirm: Callable[[str], bool] = confirm or (lambda _prompt: False)

    def schemas(self) -> list[dict]:
        return ollama_schemas()

    def execute(self, name: str, raw_args) -> ToolResult:
        spec = SPEC_BY_NAME.get(name)
        if spec is None:
            return ToolResult(False, f"Unknown tool '{name}'. Available: {', '.join(SPEC_BY_NAME)}.")
        try:
            args = validate_args(spec, raw_args)
            return getattr(self, f"_t_{name}")(args)
        except ToolArgError as exc:
            return ToolResult(False, f"Invalid arguments for {name}: {exc}")
        except (WorkspaceError, StorageError) as exc:
            return ToolResult(False, f"{name} failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let a tool crash the assistant
            return ToolResult(False, f"{name} failed unexpectedly: {exc}")

    def _declined(self) -> ToolResult:
        return ToolResult(False, "The user declined. Nothing was changed.")

    # ---------------- files
    def _t_list_files(self, a: dict) -> ToolResult:
        entries = self.ws.list_dir(a.get("path", "."))
        if not entries:
            return ToolResult(True, f"'{a.get('path', '.')}' is empty. Workspace root: {self.ws.root}")
        lines = [f"{e.name}/" if e.is_dir else f"{e.name} ({e.size} bytes)" for e in entries[:200]]
        more = f"\n... and {len(entries) - 200} more" if len(entries) > 200 else ""
        return ToolResult(True, f"Contents of {self.ws.rel(self.ws.resolve(a.get('path', '.')))}:\n" + "\n".join(lines) + more)

    def _t_read_file(self, a: dict) -> ToolResult:
        text = self.ws.read_text(a["path"])
        note = f"\n[truncated: showing 20000 of {len(text)} characters]" if len(text) > 20000 else ""
        return ToolResult(True, f"Contents of {self.ws.rel(a['path'])}:\n{text[:20000]}{note}")

    def _t_search_files(self, a: dict) -> ToolResult:
        if a.get("in_content"):
            hits = self.ws.search_content(a["query"])
            lines = [f"{self.ws.rel(p)}:{n}: {t}" for p, n, t in hits]
        else:
            lines = [self.ws.rel(p) for p in self.ws.search(a["query"], 50)]
        return ToolResult(True, "\n".join(lines) if lines else "No matches.")

    def _t_create_file(self, a: dict) -> ToolResult:
        rep = self.ws.create_file_at(a["path"], a["content"], a.get("on_conflict", "rename"))
        msg = f"Created file and verified it on disk at: {rep.path} ({rep.size} bytes)."
        if rep.renamed:
            msg += " A file with the requested name already existed, so a new name was used; nothing was overwritten."
        return ToolResult(True, msg, ("files",))

    def _t_write_file(self, a: dict) -> ToolResult:
        p = self.ws.resolve(a["path"])
        if not p.is_file():
            return ToolResult(False, "That file does not exist. Use create_file for new files.")
        if not self.confirm(f"Replace the entire contents of '{self.ws.rel(p)}'?"):
            return self._declined()
        rep = self.ws.write_text(p, a["content"])
        return ToolResult(True, f"Replaced contents and verified on disk: {rep.path} ({rep.size} bytes).", ("files",))

    def _t_append_file(self, a: dict) -> ToolResult:
        rep = self.ws.append_text(a["path"], a["content"])
        return ToolResult(True, f"Appended text and verified on disk: {rep.path} ({rep.size} bytes now).", ("files",))

    def _t_make_folder(self, a: dict) -> ToolResult:
        p = self.ws.ensure_dirs(a["path"])
        if not p.is_dir():
            return ToolResult(False, "Folder could not be created.")
        return ToolResult(True, f"Folder exists: {p}", ("files",))

    def _t_move_file(self, a: dict) -> ToolResult:
        dest = self.ws.ensure_dirs(a["destination_folder"])
        new = self.ws.move(a["source"], dest)
        return ToolResult(True, f"Moved. Verified new location: {new}", ("files",))

    def _t_rename_file(self, a: dict) -> ToolResult:
        new = self.ws.rename(a["path"], a["new_name"])
        return ToolResult(True, f"Renamed. Verified new path: {new}", ("files",))

    def _t_delete_file(self, a: dict) -> ToolResult:
        e = self.ws.entry(a["path"])
        count = self.ws.count_tree(e)
        recursive = bool(a.get("recursive"))
        if e.is_dir() and not e.is_symlink() and count and not recursive:
            return ToolResult(False, f"'{self.ws.rel(e)}' contains {count} item(s). "
                                     "Only retry with recursive=true if the user clearly wants all of it deleted.")
        what = f"the folder '{self.ws.rel(e)}' and its {count} item(s)" if count else f"'{self.ws.rel(e)}'"
        if not self.confirm(f"Permanently delete {what}? This cannot be undone."):
            return self._declined()
        self.ws.delete(e, recursive=recursive)
        return ToolResult(True, f"Deleted and verified it is gone: {e}", ("files",))

    def _t_open_file(self, a: dict) -> ToolResult:
        self.ws.open_external(a["path"])
        return ToolResult(True, f"Asked macOS to open {self.ws.resolve(a['path'])}.")

    # ---------------- tasks
    @staticmethod
    def _task_line(t: dict) -> str:
        bits = [f"#{t['id']} [{'x' if t['done'] else ' '}] {t['title']}"]
        if t.get("due_date"):
            bits.append(f"due {t['due_date']}" + (" OVERDUE" if t.get("overdue") else ""))
        bits.append(t["priority"])
        if t.get("category"):
            bits.append(t["category"])
        return " | ".join(bits)

    def _t_add_task(self, a: dict) -> ToolResult:
        tid = self.db.add_task(a["title"], a.get("due"), a.get("priority", "normal"),
                               a.get("category", ""), a.get("details", ""))
        return ToolResult(True, "Created task: " + self._task_line(self.db.get_task(tid)), ("tasks",))

    def _t_list_tasks(self, a: dict) -> ToolResult:
        rows = self.db.list_tasks(a.get("status", "pending"), a.get("priority"), a.get("category"))
        if not rows:
            return ToolResult(True, "No matching tasks.")
        return ToolResult(True, "\n".join(self._task_line(t) for t in rows[:50]))

    def _t_complete_task(self, a: dict) -> ToolResult:
        self.db.update_task(a["id"], done=True)
        return ToolResult(True, "Marked complete: " + self._task_line(self.db.get_task(a["id"])), ("tasks",))

    def _t_update_task(self, a: dict) -> ToolResult:
        due = a.get("due")
        clear = bool(due) and due.strip().lower() in ("none", "clear", "null")
        done = None if "status" not in a else a["status"] == "completed"
        self.db.update_task(a["id"], title=a.get("title"), done=done, due=None if clear else due,
                            priority=a.get("priority"), category=a.get("category"),
                            details=a.get("details"), clear_due=clear)
        return ToolResult(True, "Updated: " + self._task_line(self.db.get_task(a["id"])), ("tasks",))

    def _t_delete_task(self, a: dict) -> ToolResult:
        t = self.db.get_task(a["id"])
        if t is None:
            return ToolResult(False, f"Task {a['id']} does not exist.")
        if not self.confirm(f"Delete task #{t['id']} '{t['title']}'?"):
            return self._declined()
        self.db.delete_task(a["id"])
        return ToolResult(True, f"Deleted task #{a['id']}.", ("tasks",))

    # ---------------- notes
    def _t_create_note(self, a: dict) -> ToolResult:
        nid = self.db.save_note(None, a["title"], a.get("body", ""), a.get("project", ""), a.get("task_id"))
        return ToolResult(True, f"Created note #{nid} '{self.db.get_note(nid)['title']}'.", ("notes",))

    def _t_search_notes(self, a: dict) -> ToolResult:
        rows = self.db.list_notes(a.get("query", ""), a.get("project"))[:10]
        if not rows:
            return ToolResult(True, "No matching notes.")
        return ToolResult(True, "\n".join(
            f"#{n['id']} {n['title']}" + (f" [{n['project']}]" if n["project"] else "")
            + f": {n['body'][:200].replace(chr(10), ' ')}" for n in rows))

    def _t_read_note(self, a: dict) -> ToolResult:
        n = self.db.get_note(a["id"])
        if n is None:
            return ToolResult(False, f"Note {a['id']} does not exist.")
        return ToolResult(True, f"#{n['id']} {n['title']} [{n['project']}]\n{n['body'][:20000]}")

    def _t_update_note(self, a: dict) -> ToolResult:
        n = self.db.get_note(a["id"])
        if n is None:
            return ToolResult(False, f"Note {a['id']} does not exist.")
        body = a.get("body", n["body"])
        if a.get("append_text"):
            body = (body + "\n" if body and not body.endswith("\n") else body) + a["append_text"]
        self.db.save_note(n["id"], a.get("title", n["title"]), body, a.get("project", n["project"]),
                          a.get("task_id", n["task_id"]))
        return ToolResult(True, f"Updated note #{n['id']}.", ("notes",))

    def _t_delete_note(self, a: dict) -> ToolResult:
        n = self.db.get_note(a["id"])
        if n is None:
            return ToolResult(False, f"Note {a['id']} does not exist.")
        if not self.confirm(f"Delete note #{n['id']} '{n['title']}'?"):
            return self._declined()
        self.db.delete_note(a["id"])
        return ToolResult(True, f"Deleted note #{a['id']}.", ("notes",))
