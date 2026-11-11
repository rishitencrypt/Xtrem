from __future__ import annotations

from services.fs_service import Workspace, WorkspaceError
from services.ollama_client import OllamaClient
from services.tools import ToolRegistry
from storage.db import DEFAULTS, Database


class AppContext:
    """Shared application state: database, settings, workspace and the tool registry."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.cfg: dict[str, str] = {}
        self.workspace: Workspace
        self.registry: ToolRegistry
        self.reload()

    def reload(self) -> None:
        self.cfg = self.db.all_settings()
        try:
            self.workspace = Workspace(self.cfg["workspace"])
        except WorkspaceError:
            self.cfg["workspace"] = DEFAULTS["workspace"]
            self.workspace = Workspace(DEFAULTS["workspace"])
        self.registry = ToolRegistry(self.workspace, self.db)

    def make_client(self) -> OllamaClient:
        return OllamaClient(self.cfg["ollama_url"])
