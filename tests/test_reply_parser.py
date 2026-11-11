import unittest

from services.reply_parser import ReplyAccumulator, clean_reply, split_reasoning, visible_text
from tests.helpers import chunk


class SplitReasoningTests(unittest.TestCase):
    def test_plain_answer_untouched(self):
        self.assertEqual(clean_reply("Hello! How can I help you today?"), "Hello! How can I help you today?")

    def test_think_tags_removed(self):
        vis, why = split_reasoning("<think>The user greeted me.</think>\n\nHello!")
        self.assertEqual(vis, "Hello!")
        self.assertIn("greeted", why)

    def test_orphan_closing_tag_regression(self):
        """The reported bug: reasoning with only a closing tag leaked into the chat."""
        raw = ("Okay, the user said hello. I need to respond with a short greeting. Let me think "
               "about what to say.\n</think>\n\nHello! How can I help you today?")
        self.assertEqual(clean_reply(raw), "Hello! How can I help you today?")

    def test_old_regex_would_have_failed(self):
        import re
        old = re.sub(r"<think>.*?(</think>|$)", "", "Okay, the user said hello...</think>Hello!", flags=re.DOTALL)
        self.assertIn("Okay, the user", old)  # documents why the previous fix did not work
        self.assertEqual(clean_reply("Okay, the user said hello...</think>Hello!"), "Hello!")

    def test_orphan_close_ignored_for_non_thinking_models(self):
        self.assertIn("</think>", clean_reply("Use the </think> tag like so", orphan_close=False))

    def test_unclosed_think_hides_reasoning(self):
        self.assertEqual(clean_reply("Sure.<think>still reasoning about"), "Sure.")
        self.assertEqual(clean_reply("<think>only reasoning so far"), "")

    def test_no_phrase_filtering(self):
        text = "Okay, the user story format is: As a <role>, I want <goal>. Let me think of an example."
        self.assertEqual(clean_reply(text), text)  # valid answers that sound like reasoning survive

    def test_multiple_blocks(self):
        self.assertEqual(clean_reply("<think>a</think>One <think>b</think>two"), "One two")


class StreamingTests(unittest.TestCase):
    def test_partial_tag_not_shown(self):
        self.assertEqual(visible_text("Hello <thi"), "Hello")
        self.assertEqual(visible_text("Hello </thi"), "Hello")

    def test_thinking_field_is_separated_and_never_visible(self):
        acc = ReplyAccumulator()
        acc.feed(chunk(thinking="Okay, the user said hello."))
        self.assertTrue(acc.is_thinking)
        self.assertEqual(acc.visible, "")
        acc.feed(chunk("Hello! How can I help you today?"))
        acc.feed(chunk(done=True))
        self.assertEqual(acc.final_visible, "Hello! How can I help you today?")
        self.assertIn("said hello", acc.thinking)
        self.assertNotIn("said hello", acc.final_visible)
        self.assertEqual(acc.done_reason, "stop")

    def test_tool_calls_with_json_string_arguments(self):
        acc = ReplyAccumulator()
        acc.feed({"message": {"tool_calls": [{"function": {"name": "add_task", "arguments": '{"title": "x"}'}}]}})
        self.assertEqual(acc.tool_calls, [{"name": "add_task", "arguments": {"title": "x"}}])

    def test_garbled_tool_arguments_are_flagged(self):
        acc = ReplyAccumulator()
        acc.feed({"message": {"tool_calls": [{"function": {"name": "add_task", "arguments": "not json"}}]}})
        self.assertIn("__invalid__", acc.tool_calls[0]["arguments"])


if __name__ == "__main__":
    unittest.main()
