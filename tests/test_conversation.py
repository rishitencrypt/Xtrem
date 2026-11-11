import unittest
from datetime import date

from services.conversation import Conversation, sanitize, system_prompt, trim


class ConversationTests(unittest.TestCase):
    def test_reasoning_never_stored(self):
        c = Conversation()
        c.add_user("hello!")
        c.add_assistant("Okay, the user said hello...</think>\n\nHello! How can I help?")
        self.assertEqual(c.messages[-1]["content"], "Hello! How can I help?")

    def test_empty_assistant_reply_not_stored(self):
        c = Conversation()
        c.add_user("hi")
        c.add_assistant("<think>only thoughts</think>")
        self.assertEqual(len(c.messages), 1)

    def test_sanitize_cleans_old_poisoned_history(self):
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "I should greet.</think>Hello!"},
                {"role": "tool", "content": "x"}, {"role": "assistant", "content": ""}]
        self.assertEqual(sanitize(msgs), [{"role": "user", "content": "hi"},
                                          {"role": "assistant", "content": "Hello!"}])

    def test_trim_keeps_latest_and_starts_with_user(self):
        msgs = [{"role": "user", "content": "a" * 50}, {"role": "assistant", "content": "b" * 50},
                {"role": "user", "content": "c" * 50}]
        self.assertEqual(trim(msgs, 60), [msgs[2]])
        self.assertEqual(trim(msgs, 10_000), msgs)
        self.assertEqual(trim([msgs[0]], 1), [msgs[0]])

    def test_history_for_model(self):
        c = Conversation()
        c.add_user("one"); c.add_assistant("<think>x</think>two"); c.add_user("three")
        self.assertEqual([m["content"] for m in c.history_for_model(1000)], ["one", "two", "three"])

    def test_system_prompt_has_date_and_tool_rules(self):
        p = system_prompt(True, date(2026, 10, 20))
        self.assertIn("2026-10-20", p)
        self.assertIn("CALL THE TOOL", p)
        self.assertIn("cannot create files", system_prompt(False))


if __name__ == "__main__":
    unittest.main()
