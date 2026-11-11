#!/usr/bin/env bash
# Xtrem setup. Safe to re-run. Never deletes your data; asks before removing anything.
set -u
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$1"; }

say "Checking Python"
if [ ! -d .venv ]; then
  PY="$(command -v python3.12 || command -v python3)"
  echo "Creating .venv with $PY"
  "$PY" -m venv .venv || { echo "Could not create .venv"; exit 1; }
else
  echo "Using existing .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python --version

say "Installing dependencies"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt || { echo "Dependency install failed"; exit 1; }

# Test file from the previous Xtrem version that tests APIs which no longer exist.
OLD="tests/test_context_trim.py"
if [ -f "$OLD" ]; then
  say "Obsolete file found: $OLD"
  read -r -p "It belongs to the old version and would fail. Remove it? (your backup keeps a copy) [y/N] " ans
  case "$ans" in [yY]*) rm -f "$OLD"; echo "Removed $OLD";; *) echo "Kept $OLD";; esac
fi

say "Checking Ollama"
if command -v ollama >/dev/null 2>&1; then
  if curl -fsS --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama is running."
    ollama list || true
    if ! ollama list 2>/dev/null | grep -q '^qwen3:4b'; then
      echo "Model qwen3:4b is not installed. Install it yourself (about 2.5 GB):  ollama pull qwen3:4b"
    fi
  else
    echo "Ollama is installed but not running. Open the Ollama app or run:  ollama serve"
  fi
else
  echo "Ollama not found. Install it from https://ollama.com"
fi

say "Running tests"
python -m unittest discover -s tests -t . -v 2>&1 | tail -n 15

say "Done. Start Xtrem with:  source .venv/bin/activate && python main.py"
