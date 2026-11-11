"""Minimal local Ollama HTTP client (stdlib only, no Qt). All traffic stays on this machine."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Callable, Iterator
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OllamaError(Exception):
    """Base class; str(exc) is safe to show to the user."""


class OllamaUnavailable(OllamaError):
    pass


class ModelMissing(OllamaError):
    pass


class OllamaTimeout(OllamaError):
    pass


class OllamaProtocolError(OllamaError):
    pass


class ThinkingUnsupported(OllamaError):
    pass


class ToolsUnsupported(OllamaError):
    pass


def is_local_url(url: str) -> bool:
    try:
        u = urlparse(url)
        _ = u.port
        return u.scheme == "http" and (u.hostname or "") in LOCAL_HOSTS
    except ValueError:
        return False


def validate_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not is_local_url(url):
        raise ValueError("Ollama URL must be http://localhost or http://127.0.0.1 "
                         "(Xtrem only talks to local models).")
    return url


def _error_text(body: bytes | str | None) -> str:
    if not body:
        return ""
    try:
        data = json.loads(body)
        return str(data.get("error", "")) if isinstance(data, dict) else ""
    except ValueError:
        return ""


def classify_error(message: str, model: str, status: int | None = None) -> OllamaError:
    low = message.lower()
    if "does not support thinking" in low or "think value" in low and "not supported" in low:
        return ThinkingUnsupported(message)
    if "does not support tools" in low:
        return ToolsUnsupported(message)
    if status == 404 or "not found" in low:
        return ModelMissing(f"The model '{model}' is not installed. "
                            f"Install it yourself with: ollama pull {model}")
    return OllamaError(f"Ollama error{f' ({status})' if status else ''}: {message or 'unknown error'}")


Opener = Callable[..., object]


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 120.0, opener: Opener | None = None) -> None:
        self.base_url = validate_url(base_url)
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self._caps: dict[str, set[str]] = {}
        self._active = None

    # ---- plumbing ----
    def _request(self, path: str, payload: dict | None, timeout: float):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET")
        return self._open(req, timeout=timeout)

    def _translate(self, exc: Exception, model: str) -> OllamaError:
        if isinstance(exc, OllamaError):
            return exc
        if isinstance(exc, urllib.error.HTTPError):
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                body = b""
            return classify_error(_error_text(body) or str(exc.reason), model, exc.code)
        if isinstance(exc, urllib.error.URLError):
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                return OllamaTimeout("Ollama did not answer in time.")
            return OllamaUnavailable(
                f"Ollama is not reachable at {self.base_url} ({exc.reason}). "
                "Open the Ollama app or run `ollama serve`, then try again.")
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return OllamaTimeout(f"No response from Ollama for {int(self.timeout)} s. "
                                 "The model may still be loading; try again.")
        if isinstance(exc, (ConnectionError, OSError)):
            return OllamaUnavailable(f"Connection to Ollama failed: {exc}")
        if isinstance(exc, ValueError):
            return OllamaProtocolError("Ollama sent a response that could not be read.")
        return OllamaError(f"Unexpected error: {exc}")

    # ---- simple endpoints ----
    def list_models(self) -> list[str]:
        try:
            with self._request("/api/tags", None, 5) as resp:
                data = json.loads(resp.read())
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, "") from exc

    def show(self, model: str) -> dict:
        try:
            with self._request("/api/show", {"model": model}, 10) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, model) from exc

    def capabilities(self, model: str, refresh: bool = False) -> set[str]:
        """Capabilities reported by Ollama (e.g. {'completion','tools','thinking'}).
        Returns an empty set if the server is too old to report them."""
        if refresh or model not in self._caps:
            info = self.show(model)  # ModelMissing / OllamaUnavailable propagate
            caps = info.get("capabilities")
            self._caps[model] = set(caps) if isinstance(caps, list) else set()
        return self._caps[model]

    # ---- chat ----
    def cancel(self) -> None:
        """Best effort: wake a blocked read so cancellation is prompt."""
        resp = self._active
        if resp is not None:
            try:
                resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass

    def chat_stream(self, payload: dict) -> Iterator[dict]:
        """Yield decoded NDJSON chunks. Raises OllamaError subclasses with readable messages."""
        model = payload.get("model", "")
        try:
            with self._request("/api/chat", payload, self.timeout) as resp:
                self._active = resp
                for raw in resp:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        raise OllamaProtocolError("Ollama sent a malformed response line.") from None
                    if not isinstance(obj, dict):
                        raise OllamaProtocolError("Ollama sent an unexpected response.")
                    if obj.get("error"):
                        raise classify_error(str(obj["error"]), model)
                    yield obj
                    if obj.get("done"):
                        return
        except GeneratorExit:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, model) from exc
        finally:
            self._active = None
