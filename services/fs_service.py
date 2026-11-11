"""Safe, workspace-confined file operations. No Qt imports: fully unit-testable."""
from __future__ import annotations

import heapq
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 1_000_000
MAX_WRITE_CHARS = 500_000
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log", ".yaml", ".yml", ".xml",
    ".html", ".css", ".js", ".py", ".ini", ".toml", ".rtf",
}


class WorkspaceError(Exception):
    """A file operation was refused or failed."""


@dataclass(frozen=True)
class Entry:
    name: str
    path: Path
    is_dir: bool
    size: int


@dataclass(frozen=True)
class WriteReport:
    path: Path
    size: int
    renamed: bool = False  # True if a name conflict made us pick a different filename


def check_name(name: str) -> str:
    name = name.strip()
    if (not name or name in {".", ".."} or len(name) > 255
            or any(c in name for c in ("/", "\\", "\0"))):
        raise WorkspaceError(f"Invalid name: {name!r}")
    return name


def check_extension(path: Path | str) -> None:
    ext = Path(path).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise WorkspaceError(f"Files of type '{ext or '(none)'}' are not supported. Allowed: {allowed}")


def check_content(path: Path | str, content: str) -> None:
    if len(content) > MAX_WRITE_CHARS:
        raise WorkspaceError("Content is too large (limit 500,000 characters).")
    if Path(path).suffix.lower() == ".json":
        try:
            json.loads(content)
        except ValueError as exc:
            raise WorkspaceError(f"Content is not valid JSON: {exc}") from exc


class Workspace:
    """All file access goes through here; nothing outside `root` is ever touched."""

    def __init__(self, root: Path | str) -> None:
        try:
            self.root = Path(root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"Cannot use workspace {root}: {exc}") from exc

    # ---- path safety ----
    def _inside(self, p: Path) -> bool:
        return p == self.root or self.root in p.parents

    def _abs(self, path: Path | str | None) -> Path:
        p = self.root if path in (None, "") else Path(path).expanduser()
        if not p.is_absolute():
            p = self.root / p
        return Path(os.path.normpath(p))

    def resolve(self, path: Path | str | None = None) -> Path:
        """Fully resolve (following symlinks) and require the result inside the workspace."""
        r = self._abs(path).resolve()
        if not self._inside(r):
            raise WorkspaceError("That path is outside the workspace.")
        return r

    def entry(self, path: Path | str) -> Path:
        """Like resolve, but does not follow a final symlink (a link itself can be renamed
        or deleted without touching its target)."""
        p = self._abs(path)
        parent = p.parent.resolve()
        if not self._inside(parent):
            raise WorkspaceError("That path is outside the workspace.")
        e = parent / p.name
        if e == self.root:
            raise WorkspaceError("The workspace folder itself cannot be changed.")
        if not (e.exists() or e.is_symlink()):
            raise WorkspaceError("That item does not exist.")
        return e

    def rel(self, path: Path | str) -> str:
        p = Path(path)
        for candidate in (p, p.resolve()):
            try:
                return str(candidate.relative_to(self.root)) or "."
            except ValueError:
                continue
        return str(path)

    # ---- reading ----
    def list_dir(self, path: Path | str | None = None) -> list[Entry]:
        d = self.resolve(path)
        if not d.is_dir():
            raise WorkspaceError("Not a folder.")
        out: list[Entry] = []
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        out.append(Entry(e.name, Path(e.path), e.is_dir(follow_symlinks=False), st.st_size))
                    except OSError:
                        continue
        except OSError as exc:
            raise WorkspaceError(f"Cannot list folder: {exc}") from exc
        out.sort(key=lambda x: (not x.is_dir, x.name.lower()))
        return out

    def read_text(self, path: Path | str) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise WorkspaceError("Not a file.")
        try:
            if p.stat().st_size > MAX_READ_BYTES:
                raise WorkspaceError("File is larger than 1 MB; it is not opened as text.")
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError("Not a UTF-8 text file.") from None
        except OSError as exc:
            raise WorkspaceError(f"Could not read file: {exc}") from exc

    # ---- creating ----
    def make_folder(self, parent: Path | str | None, name: str) -> Path:
        d = self.resolve(parent)
        if not d.is_dir():
            raise WorkspaceError("Parent is not a folder.")
        target = d / check_name(name)
        try:
            target.mkdir()
        except FileExistsError:
            raise WorkspaceError(f"{target.name} already exists.") from None
        except OSError as exc:
            raise WorkspaceError(f"Could not create folder: {exc}") from exc
        if not target.is_dir():
            raise WorkspaceError("Folder was not created.")
        return target

    def ensure_dirs(self, rel_dir: Path | str | None) -> Path:
        """Create a (possibly nested) folder inside the workspace if missing; returns it."""
        target = self._abs(rel_dir)
        current = self.root
        try:
            parts = target.relative_to(self.root).parts
        except ValueError:
            raise WorkspaceError("That path is outside the workspace.") from None
        for part in parts:
            current = current / check_name(part)
            if current.is_symlink() or current.exists():
                current = self.resolve(current)
                if not current.is_dir():
                    raise WorkspaceError(f"{part} exists and is not a folder.")
            else:
                try:
                    current.mkdir()
                except OSError as exc:
                    raise WorkspaceError(f"Could not create folder: {exc}") from exc
        return self.resolve(current)

    @staticmethod
    def unique_path(target: Path) -> Path:
        if not (target.exists() or target.is_symlink()):
            return target
        n = 2
        while True:
            candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
            if not (candidate.exists() or candidate.is_symlink()):
                return candidate
            n += 1

    def create_file(self, parent: Path | str | None, name: str, content: str = "",
                    on_conflict: str = "error") -> WriteReport:
        """Create a NEW file. Never overwrites. on_conflict: 'error' or 'rename'."""
        d = self.ensure_dirs(parent)
        target = d / check_name(name)
        check_extension(target)
        check_content(target, content)
        renamed = False
        if target.exists() or target.is_symlink():
            if on_conflict != "rename":
                raise WorkspaceError(f"{self.rel(target)} already exists.")
            target = self.unique_path(target)
            renamed = True
        try:
            with open(target, "x", encoding="utf-8", newline="") as fh:  # "x": never overwrite
                fh.write(content)
        except FileExistsError:
            raise WorkspaceError(f"{target.name} already exists.") from None
        except OSError as exc:
            raise WorkspaceError(f"Could not create file: {exc}") from exc
        return self._verify(target, content, renamed)

    def create_file_at(self, rel_path: Path | str, content: str, on_conflict: str = "error") -> WriteReport:
        p = self._abs(rel_path)
        if p == self.root or not str(rel_path).strip():
            raise WorkspaceError("A file name is required.")
        return self.create_file(p.parent, p.name, content, on_conflict)

    def _verify(self, path: Path, content: str, renamed: bool = False) -> WriteReport:
        """Re-read the file from disk and confirm it holds exactly what we meant to write."""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"File could not be verified after writing: {exc}") from exc
        if data != content.encode("utf-8"):
            raise WorkspaceError("File on disk does not match what was written.")
        return WriteReport(path, len(data), renamed)

    def write_text(self, path: Path | str, content: str) -> WriteReport:
        """Atomically replace an existing file's contents."""
        p = self.resolve(path)
        if not p.is_file():
            raise WorkspaceError("Not a file.")
        check_extension(p)
        check_content(p, content)
        data = content.encode("utf-8")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".xtrem-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            shutil.copymode(p, tmp)
            os.replace(tmp, p)
            tmp = None
        except OSError as exc:
            raise WorkspaceError(f"Could not save file: {exc}") from exc
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        return self._verify(p, content)

    def append_text(self, path: Path | str, addition: str) -> WriteReport:
        existing = self.read_text(path)
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        return self.write_text(path, existing + sep + addition)

    # ---- reorganising ----
    def rename(self, path: Path | str, new_name: str) -> Path:
        e = self.entry(path)
        target = e.parent / check_name(new_name)
        if (target.exists() or target.is_symlink()) and not (
                target.exists() and os.path.samefile(e, target)):
            raise WorkspaceError(f"{target.name} already exists.")
        try:
            e.rename(target)
        except OSError as exc:
            raise WorkspaceError(f"Rename failed: {exc}") from exc
        if not (target.exists() or target.is_symlink()):
            raise WorkspaceError("Rename could not be verified.")
        return target

    def move(self, path: Path | str, dest_dir: Path | str) -> Path:
        e = self.entry(path)
        d = self.resolve(dest_dir)
        if not d.is_dir():
            raise WorkspaceError("Destination is not a folder.")
        if e.is_dir() and not e.is_symlink():
            real = e.resolve()
            if d == real or real in d.parents:
                raise WorkspaceError("A folder cannot be moved into itself.")
        target = d / e.name
        if target.exists() or target.is_symlink():
            raise WorkspaceError(f"{target.name} already exists in the destination.")
        try:
            shutil.move(str(e), str(target))
        except (OSError, shutil.Error) as exc:
            raise WorkspaceError(f"Move failed: {exc}") from exc
        if not (target.exists() or target.is_symlink()) or e.exists():
            raise WorkspaceError("Move could not be verified.")
        return target

    def count_tree(self, path: Path | str) -> int:
        e = self.entry(path)
        if not e.is_dir() or e.is_symlink():
            return 0
        return sum(len(dirs) + len(files) for _, dirs, files in os.walk(e))

    def delete(self, path: Path | str, recursive: bool = False) -> None:
        """Callers must obtain user confirmation first. Non-empty folders need recursive=True."""
        e = self.entry(path)
        try:
            if e.is_dir() and not e.is_symlink():
                if recursive:
                    shutil.rmtree(e)
                else:
                    e.rmdir()
            else:
                e.unlink()
        except OSError as exc:
            if e.is_dir() and not recursive:
                raise WorkspaceError("Folder is not empty.") from exc
            raise WorkspaceError(f"Delete failed: {exc}") from exc
        if e.exists() or e.is_symlink():
            raise WorkspaceError("Delete could not be verified.")

    # ---- searching ----
    def search(self, query: str, limit: int = 200) -> list[Path]:
        q = query.strip().lower()
        if not q:
            return []
        hits: list[Path] = []
        for dirpath, dirs, files in os.walk(self.root, followlinks=False):
            for name in dirs + files:
                if q in name.lower():
                    hits.append(Path(dirpath) / name)
                    if len(hits) >= limit:
                        return hits
        return hits

    def search_content(self, query: str, limit: int = 30) -> list[tuple[Path, int, str]]:
        """Case-insensitive text search in supported files (<1 MB). Returns (path, line, text)."""
        q = query.strip().lower()
        if not q:
            return []
        out: list[tuple[Path, int, str]] = []
        for dirpath, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                p = Path(dirpath) / name
                if p.suffix.lower() not in ALLOWED_EXTENSIONS or name.startswith("."):
                    continue
                try:
                    if p.stat().st_size > MAX_READ_BYTES:
                        continue
                    with open(p, encoding="utf-8") as fh:
                        for n, line in enumerate(fh, 1):
                            if q in line.lower():
                                out.append((p, n, line.strip()[:160]))
                                break  # one hit per file keeps results compact
                except (OSError, UnicodeDecodeError):
                    continue
                if len(out) >= limit:
                    return out
        return out

    def recent_files(self, limit: int = 5) -> list[Path]:
        found: list[tuple[float, Path]] = []
        seen = 0
        for dirpath, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if name.startswith("."):
                    continue
                seen += 1
                if seen > 5000:
                    break
                p = Path(dirpath) / name
                try:
                    found.append((p.stat().st_mtime, p))
                except OSError:
                    continue
        return [p for _, p in heapq.nlargest(limit, found, key=lambda t: t[0])]

    def open_external(self, path: Path | str) -> None:
        p = self.resolve(path)
        try:
            subprocess.run(["open", str(p)], check=True, timeout=10, capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceError(f"Could not open the file: {exc}") from exc
