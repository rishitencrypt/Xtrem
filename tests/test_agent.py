import tempfile
import unittest
from pathlib import Path

from services.agent import AgentRunner, AgentSettings
from services.fs_service import Workspace
from services.ollama_client import ModelMissing, OllamaTimeout, ThinkingUnsupported
from services.tools import ToolRegistry
from storage.db import Database
from tests.helpers import FakeClient, chunk, reply, tool_call

HELLO = [{"role": "user", "content": "hello!"}]


class AgentTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.ws = Workspace(self.tmp / "ws")
        self.db = Database(self.tmp / "t.db")
        self.confirm_answers: list[bool] = []
        self.registry = ToolRegistry(self.ws, self.db, lambda _p: self.confirm_answers.pop(0) if self.confirm_answers else False)
        self.events: list[tuple] = []

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def run_agent(self, client, history=HELLO, **settings):
        runner = AgentRunner(client, self.registry, AgentSettings(model="qwen3:4b", **settings),
                             emit=lambda k, p: self.events.append((k, p)))
        return runner.run(history)


class ThinkingBugTests(AgentTestBase):
    def test_greeting_returns_only_the_answer_hidden_mode(self):
        client = FakeClient([reply("Hello! ", "How can I help you today?",
                                   thinking="Okay, the user said hello. I need to respond with a short greeting.")])
        res = self.run_agent(client)
        self.assertTrue(res.ok)
        self.assertEqual(res.text, "Hello! How can I help you today?")
        self.assertIs(client.payloads[0]["think"], True)   # Ollama separates reasoning
        shown = " ".join(str(p) for k, p in self.events if k == "text")
        self.assertNotIn("Okay, the user", shown)

    def test_reasoning_leaked_into_content_with_orphan_tag(self):
        leaked = ("Okay, the user said hello. I need to respond with a short greeting.\n</think>\n\n"
                  "Hello! How can I help you today?")
        client = FakeClient([[chunk(leaked[:40]), chunk(leaked[40:]), chunk(done=True)]])
        res = self.run_agent(client, think_mode="off")
        self.assertEqual(res.text, "Hello! How can I help you today?")
        self.assertIs(client.payloads[0]["think"], False)
        self.assertNotIn("Okay, the user", res.text)

    def test_think_tags_in_content(self):
        client = FakeClient([reply("<think>plan the reply</think>", "Hi there!")])
        self.assertEqual(self.run_agent(client).text, "Hi there!")

    def test_think_option_not_sent_to_models_without_thinking(self):
        client = FakeClient([reply("Hello!")], caps=("completion", "tools"))
        self.run_agent(client)
        self.assertNotIn("think", client.payloads[0])

    def test_unsupported_think_option_is_retried_without_it(self):
        client = FakeClient([ThinkingUnsupported("does not support thinking"), reply("Hello!")])
        res = self.run_agent(client)
        self.assertEqual(res.text, "Hello!")
        self.assertIn("think", client.payloads[0])
        self.assertNotIn("think", client.payloads[1])

    def test_thinking_never_replayed_in_history(self):
        client = FakeClient([reply("Fine.")])
        poisoned = [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Okay, the user said hi.</think>Hello!"},
                    {"role": "user", "content": "how are you?"}]
        from services.conversation import sanitize
        self.run_agent(client, history=sanitize(poisoned))
        sent = client.payloads[0]["messages"]
        self.assertTrue(all("</think>" not in m["content"] for m in sent))
        self.assertEqual(sent[0]["role"], "system")

    def test_thinking_budget_exhausted_gives_helpful_error(self):
        client = FakeClient([[chunk(thinking="hmm " * 50), chunk(done=True, reason="length")]])
        res = self.run_agent(client)
        self.assertFalse(res.ok)
        self.assertIn("token limit", res.error)
        self.assertIn("Fast", res.error)

    def test_empty_reply_is_an_error_not_silence(self):
        res = self.run_agent(FakeClient([[chunk(done=True)]]))
        self.assertFalse(res.ok)
        self.assertIn("empty", res.error)

    def test_truncated_stream_is_an_error(self):
        res = self.run_agent(FakeClient([[chunk("Hel")]]))
        self.assertFalse(res.ok)
        self.assertIn("closed", res.error)


class ErrorTests(AgentTestBase):
    def test_model_missing(self):
        client = FakeClient([])
        client.caps = ModelMissing("The model 'qwen3:4b' is not installed. Install it yourself with: ollama pull qwen3:4b")
        res = self.run_agent(client)
        self.assertFalse(res.ok)
        self.assertIn("ollama pull", res.error)

    def test_timeout_during_chat(self):
        res = self.run_agent(FakeClient([OllamaTimeout("No response")]))
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "No response")

    def test_cancel(self):
        client = FakeClient([reply("a", "b", "c")])
        runner = AgentRunner(client, self.registry, AgentSettings(model="m"), cancelled=lambda: True)
        res = runner.run(HELLO)
        self.assertTrue(res.cancelled)
        self.assertTrue(client.cancelled)


class ToolLoopTests(AgentTestBase):
    def test_essay_is_written_to_a_file_and_verified(self):
        essay = "Modern politics is shaped by media, polarisation and technology."
        client = FakeClient([
            tool_call("create_file", path="modern-politics-essay.txt", content=essay),
            reply("I saved your essay.")])
        res = self.run_agent(client, history=[{"role": "user", "content": "Write an essay on modern politics and save it as a text file."}])
        self.assertTrue(res.ok)
        target = self.ws.root / "modern-politics-essay.txt"
        self.assertEqual(target.read_text(), essay)
        name, result = res.tool_log[0]
        self.assertEqual(name, "create_file")
        self.assertIn(str(target), result.message)
        tool_msg = client.payloads[1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("verified", tool_msg["content"])
        self.assertTrue(any(k == "tool" for k, _ in self.events))

    def test_failed_tool_is_returned_to_the_model_which_recovers(self):
        client = FakeClient([
            tool_call("create_file", path="../outside.txt", content="x"),
            tool_call("create_file", path="inside.txt", content="x"),
            reply("Saved it inside the workspace.")])
        res = self.run_agent(client)
        self.assertTrue(res.ok)
        self.assertFalse(res.tool_log[0][1].ok)
        self.assertIn("outside the workspace", client.payloads[1]["messages"][-1]["content"])
        self.assertTrue((self.ws.root / "inside.txt").exists())
        self.assertFalse((self.tmp / "outside.txt").exists())

    def test_declined_delete_leaves_file(self):
        self.ws.create_file(None, "keep.txt", "x")
        client = FakeClient([tool_call("delete_file", path="keep.txt"), reply("Okay, I did not delete it.")])
        res = self.run_agent(client)
        self.assertTrue(res.ok)
        self.assertTrue((self.ws.root / "keep.txt").exists())
        self.assertIn("declined", client.payloads[1]["messages"][-1]["content"])

    def test_infinite_loop_is_stopped(self):
        client = FakeClient([tool_call("list_files")] * 6)
        res = self.run_agent(client)
        self.assertFalse(res.ok)
        self.assertIn("repeating", res.error)

    def test_step_limit(self):
        client = FakeClient([tool_call("make_folder", path=f"f{i}") for i in range(10)])
        res = self.run_agent(client, max_steps=3)
        self.assertFalse(res.ok)
        self.assertIn("too many", res.error)

    def test_tools_unavailable_for_model_without_tool_capability(self):
        client = FakeClient([reply("I can't create files with this model, but here is the text.")], caps=("completion",))
        self.run_agent(client)
        self.assertNotIn("tools", client.payloads[0])
        self.assertIn("cannot create files", client.payloads[0]["messages"][0]["content"])

    def test_tools_can_be_disabled(self):
        client = FakeClient([reply("ok")])
        self.run_agent(client, tools_enabled=False)
        self.assertNotIn("tools", client.payloads[0])

    def test_task_created_through_tool_persists(self):
        client = FakeClient([tool_call("add_task", title="Buy milk", due="2026-11-01", priority="high"),
                             reply("Task added.")])
        self.run_agent(client)
        self.assertEqual(self.db.list_tasks()[0]["title"], "Buy milk")

    def test_tool_only_turn_with_empty_final_text_is_still_ok(self):
        client = FakeClient([tool_call("make_folder", path="notes"), [chunk(done=True)]])
        res = self.run_agent(client)
        self.assertTrue(res.ok)
        self.assertTrue((self.ws.root / "notes").is_dir())


if __name__ == "__main__":
    unittest.main()
