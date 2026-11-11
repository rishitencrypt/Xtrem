"""Small background workers so the GUI thread never waits on the network or the model."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from services.agent import AgentRunner, AgentSettings, RunResult
from services.ollama_client import OllamaClient, OllamaError
from services.tools import ToolRegistry


class StatusWorker(QThread):
    """Lists installed models and the selected model's capabilities."""
    result = Signal(bool, list, list, str)  # ok, installed models, capabilities, error text

    def __init__(self, url: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self.url, self.model = url, model

    def run(self) -> None:
        try:
            client = OllamaClient(self.url, timeout=10)
            models = client.list_models()
            caps: list[str] = []
            if self.model in models or f"{self.model}:latest" in models:
                caps = sorted(client.capabilities(self.model))
            self.result.emit(True, models, caps, "")
        except (OllamaError, ValueError) as exc:
            self.result.emit(False, [], [], str(exc))
        except Exception as exc:  # noqa: BLE001
            self.result.emit(False, [], [], f"Unexpected error: {exc}")


class AgentWorker(QThread):
    status = Signal(str)
    text = Signal(int, str)               # segment index, visible text so far
    tool = Signal(str, str, bool)         # tool name, verified result message, ok
    confirm_requested = Signal(str)
    run_finished = Signal(object)         # RunResult

    def __init__(self, client: OllamaClient, registry: ToolRegistry, settings: AgentSettings,
                 history: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.client, self.registry, self.settings, self.history = client, registry, settings, history
        self._cancel = False
        self._answer = False
        self._event = threading.Event()

    def cancel(self) -> None:
        self._cancel = True
        self._answer = False
        self._event.set()
        self.client.cancel()

    def answer(self, ok: bool) -> None:
        self._answer = ok
        self._event.set()

    def _confirm(self, prompt: str) -> bool:
        if self._cancel:
            return False
        self._answer = False
        self._event.clear()
        self.confirm_requested.emit(prompt)
        while not self._event.wait(0.2):
            if self._cancel:
                return False
        return self._answer and not self._cancel

    def _emit(self, kind: str, payload) -> None:
        if kind == "status":
            self.status.emit(str(payload))
        elif kind == "text":
            self.text.emit(int(payload[0]), str(payload[1]))
        elif kind == "tool":
            name, _args, result = payload
            self.tool.emit(name, result.message, result.ok)

    def run(self) -> None:
        self.registry.confirm = self._confirm
        runner = AgentRunner(self.client, self.registry, self.settings, self._emit, lambda: self._cancel)
        try:
            result = runner.run(self.history)
        except Exception as exc:  # noqa: BLE001 - always report, never crash the GUI
            result = RunResult(False, error=f"Unexpected error: {exc}")
        finally:
            self.registry.confirm = lambda _p: False
        self.run_finished.emit(result)
