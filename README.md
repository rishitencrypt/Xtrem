# Xtrem

A private desktop assistant for macOS. PySide6 GUI, local models through Ollama, SQLite storage.
Nothing leaves your Mac: no cloud APIs, and the Ollama URL must be `localhost` / `127.0.0.1`.

## Install over your existing project (keeps `.venv`, data and workspace)

Replace `~/Downloads/Xtrem.zip` if the file is elsewhere. Close Xtrem first.

```bash
# 1. Back up the existing project (a full copy, including .venv and workspace)
cp -R ~/Projects/Xtrem ~/Projects/Xtrem-backup-$(date +%Y%m%d-%H%M)

# 2. Extract the new version next to it (nothing is overwritten yet)
rm -rf /tmp/xtrem-new && mkdir /tmp/xtrem-new && unzip -q ~/Downloads/Xtrem.zip -d /tmp/xtrem-new

# 3. Copy the code over the old files. .venv and your workspace/ files are not touched,
#    and nothing is deleted (no --delete flag).
rsync -a --exclude '.venv' --exclude 'workspace/' --exclude '__pycache__' /tmp/xtrem-new/Xtrem/ ~/Projects/Xtrem/

# 4. Set up: reuses your .venv, installs PySide6, checks Ollama, runs the tests
cd ~/Projects/Xtrem && ./setup.sh
```

`setup.sh` asks before removing `tests/test_context_trim.py` (a test from the old version that no longer applies).
Your notes, tasks and settings live in `~/Library/Application Support/Xtrem/xtrem.db`. They are never overwritten:
the first launch upgrades the database in place and first saves a copy next to it as `xtrem.db.bak-v1`.

## Run

```bash
cd ~/Projects/Xtrem
source .venv/bin/activate
python main.py
```

## Ollama and the model

```bash
ollama list                 # is qwen3:4b installed?
ollama pull qwen3:4b        # only if missing (about 2.5 GB). Xtrem never downloads models by itself.
curl http://localhost:11434/api/tags   # is the server up? If not: open the Ollama app, or `ollama serve`
```

Overview shows whether Ollama is reachable, whether the model is installed, and what it supports.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -t . -v
```

No running model is needed (Ollama is mocked). `tests/test_ui_smoke.py` runs the GUI headless and is skipped when
PySide6 is not installed.

## The "model shows its thinking" bug

**What was going wrong.** Qwen3 is a "thinking" model. Depending on the Ollama version and model build, its reasoning
arrives in one of three shapes:

1. in a separate `message.thinking` field (when `think` is enabled and Ollama parses it out),
2. as `<think>...</think>` inside `message.content`,
3. as reasoning followed by only a closing `</think>`, because the template already supplied the opening tag.

The previous code stripped `<think>...</think>` with a regex that needs the opening tag, so shape 3 passed straight
through. And `"think": false` is ignored by builds that always think; then the reasoning is not separated and lands in
`content`. Stored chat history also kept those replies, so the model imitated them on later turns.

**What Xtrem does now.**

- Asks Ollama (`/api/show`) what the model can do, and sends `think` only to models that can think.
- Default **Hidden thinking** mode sends `think: true`, so Ollama separates the reasoning into `message.thinking`.
  Xtrem discards it: it is never shown, stored or sent back to the model.
- **Fast** mode sends `think: false`. If reasoning still leaks into the content, `services/reply_parser.py` removes
  complete `<think>` blocks, an unclosed `<think>`, and an orphan `</think>` (only structural tags, no phrase guessing).
- History is cleaned before every request, and old saved replies are cleaned when loaded.
- If the model runs out of tokens while thinking, you get a clear error instead of an empty answer.
- Not hiding it would be the wrong fix for a model that cannot behave: in Settings you can pick a non-thinking
  (instruct) model from your installed list. Install one yourself, e.g. look for an `instruct` tag of `qwen3:4b`
  in the Ollama library and `ollama pull` it.

Trade-off: Hidden mode is the reliable setting, but the model still thinks internally, so replies start later
(you see "Thinking…"). On an 8 GB Mac, choose Fast or a non-thinking model if speed matters.

## What the assistant can do (tools)

With a tool-capable model the assistant calls validated Python functions. It has no shell and no arbitrary file access.

- **Files** (inside the workspace only): list, read, search by name or text, create (never overwrites; name conflicts get
  `name (2).ext` and you are told), append, replace (asks you), make folders, move, rename, delete (asks you), open in the
  default Mac app. Allowed types: txt, md, json (validated), csv, tsv, log, yaml, xml, html, css, js, py, ini, toml, rtf.
- **Tasks:** add with due date, priority and category; list pending / completed / overdue; complete; edit; delete (asks you).
- **Notes:** create, search, read, edit, link to a project or task; delete (asks you).

Every file write is re-read from disk and compared before success is reported; the chat shows an "ACTION" card with the
verified real path. Tool errors go back to the model so it can retry. Repeated identical calls and runaway loops are stopped.

## Layout

```
main.py            entry point
services/          ollama_client, reply_parser, conversation, tools, agent, fs_service (no Qt: fully testable)
storage/db.py      SQLite (settings, notes, tasks, optional history), safe migrations
ui/                neumorphic widgets, theme, chat, overview, files, notes, tasks, settings
tests/             unit tests (Ollama mocked)
workspace/         default workspace for files
```

Keyboard: Cmd+1…6 switch pages, Enter sends, Shift+Enter adds a line.

## Known limits

- Inference speed depends on the model, prompt length and your 8 GB of RAM; no response times are promised.
- Tool use needs a model that reports the `tools` capability; otherwise Xtrem is chat only and says so.
- Small models sometimes call tools badly; failures are shown, never reported as successes.
- "Stop" frees the UI immediately; Ollama may finish its current token in the background.
