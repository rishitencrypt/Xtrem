import socket
import unittest
import urllib.error

from services.ollama_client import (ModelMissing, OllamaClient, OllamaProtocolError, OllamaUnavailable,
                                    OllamaTimeout, ThinkingUnsupported, ToolsUnsupported, validate_url)
from tests.helpers import FakeResponse, chunk, http_error


def client_with(opener):
    return OllamaClient("http://localhost:11434", opener=opener)


class ClientTests(unittest.TestCase):
    def test_only_local_urls_allowed(self):
        for bad in ("https://api.example.com", "http://192.168.1.5:11434", "ftp://localhost"):
            with self.assertRaises(ValueError):
                validate_url(bad)
        self.assertEqual(validate_url("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")

    def test_list_models_and_capabilities(self):
        def opener(req, timeout):
            if req.full_url.endswith("/api/tags"):
                return FakeResponse(body={"models": [{"name": "qwen3:4b"}, {"name": "gemma3:4b"}]})
            return FakeResponse(body={"capabilities": ["completion", "tools", "thinking"]})
        c = client_with(opener)
        self.assertEqual(c.list_models(), ["qwen3:4b", "gemma3:4b"])
        self.assertEqual(c.capabilities("qwen3:4b"), {"completion", "tools", "thinking"})

    def test_old_server_without_capabilities(self):
        c = client_with(lambda req, timeout: FakeResponse(body={"details": {}}))
        self.assertEqual(c.capabilities("m"), set())

    def test_chat_stream_parses_thinking_and_content(self):
        lines = [chunk(thinking="hmm"), chunk("Hello"), chunk(done=True)]
        c = client_with(lambda req, timeout: FakeResponse(lines))
        out = list(c.chat_stream({"model": "m", "messages": []}))
        self.assertEqual(out[0]["message"]["thinking"], "hmm")
        self.assertTrue(out[-1]["done"])

    def test_malformed_line(self):
        c = client_with(lambda req, timeout: FakeResponse(["{not json"]))
        with self.assertRaises(OllamaProtocolError):
            list(c.chat_stream({"model": "m"}))

    def test_error_line_thinking_unsupported(self):
        c = client_with(lambda req, timeout: FakeResponse([{"error": '"m" does not support thinking'}]))
        with self.assertRaises(ThinkingUnsupported):
            list(c.chat_stream({"model": "m"}))

    def test_http_400_tools_unsupported(self):
        def opener(req, timeout):
            raise http_error(400, "registry.ollama.ai/library/m does not support tools")
        with self.assertRaises(ToolsUnsupported):
            list(client_with(opener).chat_stream({"model": "m"}))

    def test_missing_model(self):
        def opener(req, timeout):
            raise http_error(404, "model 'nope' not found")
        with self.assertRaises(ModelMissing) as ctx:
            list(client_with(opener).chat_stream({"model": "nope"}))
        self.assertIn("ollama pull nope", str(ctx.exception))

    def test_server_stopped(self):
        def opener(req, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        with self.assertRaises(OllamaUnavailable):
            client_with(opener).list_models()

    def test_timeout(self):
        def opener(req, timeout):
            raise socket.timeout("timed out")
        with self.assertRaises(OllamaTimeout):
            list(client_with(opener).chat_stream({"model": "m"}))

    def test_capabilities_cached(self):
        calls = []
        def opener(req, timeout):
            calls.append(1)
            return FakeResponse(body={"capabilities": ["completion"]})
        c = client_with(opener)
        c.capabilities("m"); c.capabilities("m")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
